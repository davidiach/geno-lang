"""Trusted host side of capability grants in the isolated ``geno run`` lane.

Proposal 0013 lets ``geno run --cap ...`` grant capabilities without leaving
the process-isolated lane. The parent (``geno/cli/run.py``) turns the user's
grant into an :class:`IsolatedRunPolicy` and sends it to the sandbox worker in
the parent-set configuration, never in the request the worker parses later.
The worker validates it with :meth:`IsolatedRunPolicy.from_config` before it
reads any untrusted input, so a bad grant or policy is a startup error and
nothing runs.

Trusted worker setup then builds the private host table (:func:`build_host_table`)
that the compiled program's runtime prelude reaches only through
``_geno_host_module``. The table holds a binding only for a granted capability
that needs host access, so a missing binding is a second gate, independent of
the per-builtin capability check.

The parent imports this module as well as the worker, so module scope imports
only the standard library, :mod:`geno.capabilities` and :mod:`geno._serve`
(whose validators the parent already loads for ``--unsafe``). The interpreter
and the runtime values are imported inside the functions that need them.

Everything here runs with real builtins, in the parent and in the worker
alike: the worker imported it through the real import system during trusted
setup. The runtime prelude's worker branches only call the narrow methods of
these bindings, so all host access stays in this trusted, typed code.
"""

from __future__ import annotations

import codecs
import dataclasses
import math
import os
import sys
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import IO, TYPE_CHECKING, Any

from ._serve import (
    HTTP_REQUEST_TIMEOUT_SECONDS,
    _http_request_data,
    _resolve_fs_roots,
    _resolve_scoped_lstat_path,
    _resolve_scoped_path,
    _send_http_request,
    _validate_http_target,
    file_kind_name,
)
from .capabilities import (
    DEFAULT_ALLOWED_CAPABILITIES,
    ISOLATED_RUN_FS_WRITE_LIMIT_BYTES,
    KNOWN_CAPABILITIES,
    isolated_env_name_error,
    isolated_run_capabilities,
)

if TYPE_CHECKING:
    from .interpreter import Interpreter
    from .sandbox import SandboxConfig


# The longest variable name an environment-policy tag carries to the parent.
# The name is program-chosen; the parent offers a hint only for a short,
# plain identifier anyway.
ENV_NAME_TAG_LIMIT = 256


class IsolatedRunPolicyError(ValueError):
    """The parent-set isolated run policy is malformed or not allowed."""


# The exact key set of ``IsolatedRunPolicy.to_config()``. The worker rejects a
# policy with any key missing or extra.
_POLICY_KEYS = frozenset(
    {
        "capabilities",
        "env_allowed_names",
        "env_case_insensitive",
        "fs_read_only",
        "fs_configured_roots",
        "fs_allow_absolute",
        "max_total_write_bytes",
        "http_allow_private",
        "stdin_fd",
        "deadline_seconds",
    }
)


def _policy_bool(raw: Mapping[str, Any], key: str) -> bool:
    value = raw[key]
    if not isinstance(value, bool):
        raise IsolatedRunPolicyError(f"{key} must be a boolean")
    return value


def _policy_text(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise IsolatedRunPolicyError(f"{key} entries must be non-empty strings")
    return value


def _policy_names(raw: Mapping[str, Any], key: str) -> frozenset[str]:
    value = raw[key]
    if not isinstance(value, list):
        raise IsolatedRunPolicyError(f"{key} must be a list")
    names = [_policy_text(item, key) for item in value]
    if len(set(names)) != len(names):
        raise IsolatedRunPolicyError(f"{key} must not repeat a name")
    return frozenset(names)


def _policy_roots(raw: Mapping[str, Any]) -> tuple[str, ...] | None:
    value = raw["fs_configured_roots"]
    if value is None:
        return None
    if not isinstance(value, list):
        raise IsolatedRunPolicyError("fs_configured_roots must be a list or null")
    return tuple(_policy_text(item, "fs_configured_roots") for item in value)


def _policy_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class IsolatedRunPolicy:
    """Trusted grant and host policy for one isolated ``geno run``.

    Every capability-specific field keeps its neutral value (empty, false,
    ``None`` or 0) unless its capability is granted, so a policy for one grant
    can never carry settings for another. :meth:`validate` enforces that.
    """

    capabilities: frozenset[str]
    env_allowed_names: frozenset[str] = frozenset()
    env_case_insensitive: bool = False
    fs_read_only: bool = False
    fs_configured_roots: tuple[str, ...] | None = None
    fs_allow_absolute: bool = False
    max_total_write_bytes: int = 0
    http_allow_private: bool = False
    stdin_fd: int | None = None
    deadline_seconds: float | None = None

    @classmethod
    def default(cls) -> IsolatedRunPolicy:
        """The policy of a run without ``--cap``: the default grant, no host."""
        return cls(capabilities=DEFAULT_ALLOWED_CAPABILITIES)

    def validate(self, os_name: str | None = None) -> None:
        """Raise :class:`IsolatedRunPolicyError` unless the lane allows this policy.

        *os_name* defaults to this host's ``os.name``.
        """
        supported = isolated_run_capabilities(os_name)
        for capability in sorted(self.capabilities):
            if capability not in supported:
                raise IsolatedRunPolicyError(
                    f"capability {capability!r} is not available in the "
                    "process-isolated run lane"
                )
        granted = self.capabilities
        if "env" not in granted and (
            self.env_allowed_names or self.env_case_insensitive
        ):
            raise IsolatedRunPolicyError(
                "environment policy settings require the 'env' capability"
            )
        for name in self.env_allowed_names:
            problem = isolated_env_name_error(name)
            if problem is not None:
                raise IsolatedRunPolicyError(f"env_allowed_names entries {problem}")
        if self.env_case_insensitive:
            # Only Windows folds names, as its os.environ does; the parent
            # sends the allowlist already folded.
            if not isolated_env_case_insensitive(os_name):
                raise IsolatedRunPolicyError(
                    "env_case_insensitive applies only on Windows"
                )
            if any(name != name.upper() for name in self.env_allowed_names):
                raise IsolatedRunPolicyError(
                    "a case-insensitive env_allowed_names must be upper-case"
                )
        if "fs" not in granted and (
            self.fs_read_only
            or self.fs_configured_roots is not None
            or self.fs_allow_absolute
            or self.max_total_write_bytes != 0
        ):
            raise IsolatedRunPolicyError(
                "filesystem policy settings require the 'fs' capability"
            )
        if self.fs_configured_roots is not None and not self.fs_configured_roots:
            raise IsolatedRunPolicyError(
                "fs_configured_roots must name at least one filesystem root"
            )
        if not _policy_int(self.max_total_write_bytes):
            raise IsolatedRunPolicyError("max_total_write_bytes must be an integer")
        if self.max_total_write_bytes < 0:
            raise IsolatedRunPolicyError("max_total_write_bytes must not be negative")
        if "http" not in granted and self.http_allow_private:
            raise IsolatedRunPolicyError(
                "http_allow_private requires the 'http' capability"
            )
        if self.stdin_fd is not None:
            if "stdin" not in granted:
                raise IsolatedRunPolicyError("stdin_fd requires the 'stdin' capability")
            if self.stdin_fd < 3:
                raise IsolatedRunPolicyError("stdin_fd must be at least 3")
        if self.deadline_seconds is not None and not (
            math.isfinite(self.deadline_seconds) and self.deadline_seconds > 0
        ):
            raise IsolatedRunPolicyError(
                "deadline_seconds must be a positive finite number"
            )

    def to_config(self) -> dict[str, Any]:
        """Serialize for the worker configuration (JSON-compatible)."""
        return {
            "capabilities": sorted(self.capabilities),
            "env_allowed_names": sorted(self.env_allowed_names),
            "env_case_insensitive": self.env_case_insensitive,
            "fs_read_only": self.fs_read_only,
            "fs_configured_roots": (
                None
                if self.fs_configured_roots is None
                else list(self.fs_configured_roots)
            ),
            "fs_allow_absolute": self.fs_allow_absolute,
            "max_total_write_bytes": self.max_total_write_bytes,
            "http_allow_private": self.http_allow_private,
            "stdin_fd": self.stdin_fd,
            "deadline_seconds": self.deadline_seconds,
        }

    @classmethod
    def from_config(cls, raw: object, os_name: str | None = None) -> IsolatedRunPolicy:
        """Parse and validate a policy from the parent-set worker configuration.

        Strict: the exact key set of :meth:`to_config`, exact JSON types and
        every rule of :meth:`validate`. Anything else raises
        :class:`IsolatedRunPolicyError`.
        """
        if raw is None:
            raise IsolatedRunPolicyError("the policy is missing")
        if not isinstance(raw, dict):
            raise IsolatedRunPolicyError("the policy must be an object")
        keys = set(raw)
        if keys != _POLICY_KEYS:
            missing = sorted(_POLICY_KEYS - keys)
            extra = sorted(str(key) for key in keys - _POLICY_KEYS)
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if extra:
                details.append("unexpected " + ", ".join(extra))
            raise IsolatedRunPolicyError("invalid fields: " + "; ".join(details))

        capabilities = _policy_names(raw, "capabilities")
        unknown = sorted(capabilities - KNOWN_CAPABILITIES)
        if unknown:
            raise IsolatedRunPolicyError(f"unknown capability {unknown[0]!r}")

        max_total_write_bytes = raw["max_total_write_bytes"]
        if not _policy_int(max_total_write_bytes):
            raise IsolatedRunPolicyError("max_total_write_bytes must be an integer")
        stdin_fd = raw["stdin_fd"]
        if stdin_fd is not None and not _policy_int(stdin_fd):
            raise IsolatedRunPolicyError("stdin_fd must be an integer or null")
        deadline_seconds = raw["deadline_seconds"]
        if deadline_seconds is not None:
            if isinstance(deadline_seconds, bool) or not isinstance(
                deadline_seconds, (int, float)
            ):
                raise IsolatedRunPolicyError(
                    "deadline_seconds must be a number or null"
                )
            deadline_seconds = float(deadline_seconds)

        policy = cls(
            capabilities=capabilities,
            env_allowed_names=_policy_names(raw, "env_allowed_names"),
            env_case_insensitive=_policy_bool(raw, "env_case_insensitive"),
            fs_read_only=_policy_bool(raw, "fs_read_only"),
            fs_configured_roots=_policy_roots(raw),
            fs_allow_absolute=_policy_bool(raw, "fs_allow_absolute"),
            max_total_write_bytes=max_total_write_bytes,
            http_allow_private=_policy_bool(raw, "http_allow_private"),
            stdin_fd=stdin_fd,
            deadline_seconds=deadline_seconds,
        )
        policy.validate(os_name)
        return policy


def isolated_env_case_insensitive(os_name: str | None = None) -> bool:
    """Whether environment variable names are case-insensitive on *os_name*.

    Windows folds them (its ``os.environ`` upper-cases every key); POSIX
    names are exact. *os_name* defaults to this host's ``os.name``.
    """
    return (os.name if os_name is None else os_name) == "nt"


def env_name_key(name: str, case_insensitive: bool) -> str:
    """The key *name* is allowed, snapshotted and looked up under."""
    return name.upper() if case_insensitive else name


def _env_truthy(value: str | None) -> bool:
    """The host-policy truthy rule of ``_serve._env_truthy``, for one value."""
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def isolated_fs_host_policy(
    environ: Mapping[str, str] | None = None,
) -> tuple[bool, tuple[str, ...] | None, bool]:
    """Resolve the caller's filesystem policy variables for an ``fs`` grant.

    Returns ``(read_only, configured_roots, allow_absolute)`` from
    ``GENO_FS_READ_ONLY``, ``GENO_FS_ROOTS`` (split on ``os.pathsep``;
    ``None`` when unset) and ``GENO_FS_ALLOW_ABSOLUTE``, read from *environ*
    (default ``os.environ``) exactly as ``geno run --unsafe --cap fs`` reads
    them: absolute paths inside the roots are allowed unless either roots
    variable is set, and then only when ``GENO_FS_ALLOW_ABSOLUTE`` is truthy.
    A set but empty ``GENO_FS_ROOTS`` raises :class:`IsolatedRunPolicyError`.
    The parent calls this only when ``fs`` is granted.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    read_only = _env_truthy(source.get("GENO_FS_READ_ONLY"))
    raw_roots = source.get("GENO_FS_ROOTS")
    roots: tuple[str, ...] | None = None
    if raw_roots is not None:
        roots = tuple(root for root in raw_roots.split(os.pathsep) if root)
        if not roots:
            raise IsolatedRunPolicyError(
                "GENO_FS_ROOTS must contain at least one filesystem root"
            )
    if raw_roots is None and "GENO_FS_ALLOW_ABSOLUTE" not in source:
        allow_absolute = True
    else:
        allow_absolute = _env_truthy(source.get("GENO_FS_ALLOW_ABSOLUTE"))
    return read_only, roots, allow_absolute


def isolated_run_policy_for(
    grant: Collection[str] | None,
    *,
    timeout: float | None,
    allow_env: Collection[str] = (),
    max_total_write_bytes: int | None = None,
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> IsolatedRunPolicy:
    """Build and validate the policy the parent sends for one isolated run.

    *grant* is the ``--cap`` set, or ``None`` for the default grant, and
    *allow_env* the ``--allow-env`` names (exact; folded on Windows). Host
    policy is resolved only for the capability it configures: a grant without
    ``fs`` or ``http`` never reads those capabilities' settings from
    *environ* (default ``os.environ``). With ``fs``,
    *max_total_write_bytes* is the ``--max-total-write-bytes`` value, or
    ``None`` for the lane default; without ``fs`` it is ignored and the
    policy keeps 0. With ``http``, ``GENO_HTTP_ALLOW_PRIVATE`` decides
    private-network access, whatever the program's own environment
    allowlist holds. The program input handle of a ``stdin`` grant is added
    just before launch, by :func:`with_program_stdin`.
    """
    capabilities = frozenset(DEFAULT_ALLOWED_CAPABILITIES if grant is None else grant)
    deadline: float | None = None
    if timeout is not None and math.isfinite(timeout) and timeout > 0:
        deadline = float(timeout)
    case_insensitive = "env" in capabilities and isolated_env_case_insensitive(os_name)
    fs_fields: dict[str, Any] = {}
    if "fs" in capabilities:
        read_only, roots, allow_absolute = isolated_fs_host_policy(environ)
        fs_fields = {
            "fs_read_only": read_only,
            "fs_configured_roots": roots,
            "fs_allow_absolute": allow_absolute,
            "max_total_write_bytes": (
                ISOLATED_RUN_FS_WRITE_LIMIT_BYTES
                if max_total_write_bytes is None
                else max_total_write_bytes
            ),
        }
    http_fields: dict[str, Any] = {}
    if "http" in capabilities:
        source: Mapping[str, str] = os.environ if environ is None else environ
        http_fields = {
            "http_allow_private": _env_truthy(source.get("GENO_HTTP_ALLOW_PRIVATE"))
        }
    policy = IsolatedRunPolicy(
        capabilities=capabilities,
        env_allowed_names=frozenset(
            env_name_key(name, case_insensitive) for name in allow_env
        ),
        env_case_insensitive=case_insensitive,
        deadline_seconds=deadline,
        **fs_fields,
        **http_fields,
    )
    policy.validate(os_name)
    return policy


def isolated_env_snapshot(
    allow_env: Collection[str],
    *,
    environ: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> dict[str, str]:
    """Snapshot the caller's values of the allowed variables that are set.

    The parent calls this before it sets its own ``GENO_CLI_ARGS``, and only
    when ``env`` is granted; nothing else from its environment crosses to the
    worker. On Windows names are case-insensitive, as ``os.environ`` is
    there, and the snapshot is keyed by the upper-cased name.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    names = sorted(set(allow_env))
    if not isolated_env_case_insensitive(os_name):
        return {name: source[name] for name in names if name in source}
    by_key = {env_name_key(key, True): value for key, value in source.items()}
    snapshot: dict[str, str] = {}
    for name in names:
        key = env_name_key(name, True)
        if key in by_key:
            snapshot[key] = by_key[key]
    return snapshot


def duplicate_program_stdin(stream: Any = None) -> int | None:
    """Duplicate the caller's standard input for one isolated run.

    Returns a new descriptor numbered 3 or above, so it can never be confused
    with the worker's own control pipes, or ``None`` when there is no usable
    input: *stream* (default ``sys.stdin``) is missing, has no real file
    descriptor, or cannot be duplicated, or the platform is Windows, whose
    isolated lane refuses ``stdin`` before launch. Nothing is read. The
    duplicate is not inheritable; the worker receives it through ``pass_fds``
    alone, and :meth:`geno.sandbox.ProcessSandbox.execute_geno_request` closes
    this process's copy when the run ends.
    """
    if os.name == "nt":
        return None
    source = sys.stdin if stream is None else stream
    if source is None:
        return None
    try:
        fd = source.fileno()
    except (AttributeError, OSError, ValueError):
        return None
    if not isinstance(fd, int) or isinstance(fd, bool) or fd < 0:
        return None
    low: list[int] = []
    try:
        duplicate = os.dup(fd)
        # A caller that closed its stdout or stderr gets 1 or 2 back first.
        while duplicate < 3:
            low.append(duplicate)
            duplicate = os.dup(fd)
    except OSError:
        return None
    finally:
        for unused in low:
            os.close(unused)
    return duplicate


def with_program_stdin(
    policy: IsolatedRunPolicy, stream: Any = None
) -> IsolatedRunPolicy:
    """Return *policy* with the program's input handle, when ``stdin`` is granted.

    Without the grant the policy is returned unchanged and nothing is
    duplicated, so the worker inherits no program-input handle. With it, the
    handle is :func:`duplicate_program_stdin` of *stream*, or ``None`` (reads
    then fail with ``Err``) when there is no usable input. The caller hands
    the result straight to ``execute_geno_request``, which owns the handle.
    """
    if "stdin" not in policy.capabilities:
        return policy
    return dataclasses.replace(policy, stdin_fd=duplicate_program_stdin(stream))


class IsolatedRunClock:
    """The run's one cooperative deadline, shared by every phase.

    Started at the first statement of the worker's ``main()``; the parent's
    hard kill, measured from before the worker launched, stays authoritative.
    Nothing resets it between frontend work, examples and ``main``.
    """

    def __init__(
        self, deadline_seconds: float | None, started_at: float | None = None
    ) -> None:
        self._deadline_seconds = deadline_seconds
        self._started_at = time.monotonic() if started_at is None else started_at

    def remaining(self) -> float | None:
        """Seconds left before the run's deadline, or ``None`` without one."""
        if self._deadline_seconds is None:
            return None
        return self._deadline_seconds - (time.monotonic() - self._started_at)


class IsolatedHostBinding:
    """Marker base for the trusted objects in the worker's private host table.

    A binding exposes only the narrow operations its capability's builtins
    need, and returns plain Python values or trusted handles to the runtime
    prelude, never to Geno code.
    """

    __slots__ = ()


def env_policy_denied_message(builtin_name: str, name: str) -> str:
    """The denial for a variable outside the allowlist (the embedding API's)."""
    return (
        f"{builtin_name}: environment variable {name!r} is not allowed by the "
        "host env policy"
    )


class IsolatedEnv(IsolatedHostBinding):
    """The ``env`` grant's binding: allowlist, snapshot and program arguments.

    ``cli_args`` reads :attr:`program_args`, which needs no allowed name.
    ``env_get`` and ``env_get_or`` go through :meth:`lookup`: a name outside
    the allowlist is an environment-policy denial even when the caller does
    not have the variable, and an allowed name reads only the parent's
    snapshot, never the worker's own environment (its temporary ``HOME``, its
    sandbox configuration). With *case_insensitive* (Windows) every name is
    folded with ``str.upper``, as ``os.environ`` folds keys there.
    """

    __slots__ = ("_allowed", "_case_insensitive", "environ", "program_args")

    def __init__(
        self,
        allowed: Collection[str],
        snapshot: Mapping[str, str],
        program_args: Sequence[str],
        *,
        case_insensitive: bool = False,
    ) -> None:
        self._case_insensitive = case_insensitive
        self._allowed = frozenset(
            env_name_key(name, case_insensitive) for name in allowed
        )
        values: dict[str, str] = {}
        for name, value in snapshot.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise TypeError("the environment snapshot must map str to str")
            key = env_name_key(name, case_insensitive)
            if key not in self._allowed:
                raise ValueError("the environment snapshot holds an unlisted name")
            values[key] = value
        if any(not isinstance(arg, str) for arg in program_args):
            raise TypeError("program arguments must be strings")
        # Read-only views: the prelude reads them, Geno code never sees them.
        self.environ: Mapping[str, str] = MappingProxyType(values)
        self.program_args: tuple[str, ...] = tuple(program_args)

    def allows(self, name: str) -> bool:
        """Whether the allowlist permits *name* (folded on Windows)."""
        return env_name_key(name, self._case_insensitive) in self._allowed

    def value(self, name: str) -> str | None:
        """The snapshot value of an allowed *name*, or ``None`` when unset."""
        return self.environ.get(env_name_key(name, self._case_insensitive))

    def lookup(self, builtin_name: str, name: str) -> str | None:
        """Return an allowed variable's value, or ``None`` when it is unset.

        Raises the tagged environment-policy denial for any other name. The
        compiled runtime calls this; the worker reads the tag (never the
        message) to offer the ``--allow-env`` hint.
        """
        if not isinstance(name, str):
            raise TypeError(f"{builtin_name}: name must be a string")
        if not self.allows(name):
            error: Any = RuntimeError(env_policy_denied_message(builtin_name, name))
            error._geno_env_policy_denied = name
            raise error
        return self.value(name)


# How many bytes one lazy read of the program's input takes at most.
STDIN_CHUNK_BYTES = 8192


class IsolatedStdinDecodeError(UnicodeError):
    """The program's standard input is not valid UTF-8.

    The message is CPython's own ``UnicodeDecodeError`` text for decoding the
    whole input at once, with positions counted from the start of the
    stream, so the ``Err`` a program sees matches standalone compiled output.
    """


def _stream_decode_error_message(error: UnicodeDecodeError, offset: int) -> str:
    """CPython's text for *error*, with its positions moved by *offset* bytes."""
    start = offset + error.start
    end = offset + error.end
    if end - start == 1:
        byte = error.object[error.start]
        return (
            f"'{error.encoding}' codec can't decode byte 0x{byte:02x} in "
            f"position {start}: {error.reason}"
        )
    return (
        f"'{error.encoding}' codec can't decode bytes in position "
        f"{start}-{end - 1}: {error.reason}"
    )


class IsolatedStdin(IsolatedHostBinding):
    """The ``stdin`` grant's binding: a lazy, bounded reader of the program's input.

    *fd* is the duplicate of the caller's standard input that the worker
    inherited (``pass_fds``), or ``None`` when the caller had no usable input;
    every read then fails with ``OSError``, which the builtins return as
    ``Err``. Nothing is read until ``stdin_read_all`` asks. Examples and
    ``main`` share one instance, so input an example consumed is gone for
    ``main``: there is no rewind, cache or replay, and once the end of input
    has been seen every later read returns nothing. A read blocks until data
    or end of input arrives; the parent's hard kill at the run's deadline is
    what bounds it, and the kernel releases the handle with the worker.
    """

    __slots__ = ("_eof", "_fd")

    def __init__(self, fd: int | None) -> None:
        if fd is not None and (
            not isinstance(fd, int) or isinstance(fd, bool) or fd < 3
        ):
            raise ValueError("the program input handle must be a descriptor >= 3")
        self._fd = fd
        self._eof = False

    def read_chunk(self, size: int = STDIN_CHUNK_BYTES) -> bytes:
        """Read at most *size* bytes; ``b""`` at, and after, the end of input."""
        if self._fd is None:
            raise OSError("stdin is not available")
        if self._eof:
            return b""
        data = os.read(self._fd, size)
        if not data:
            self._eof = True
        return data

    def read_text(self, check_size: Callable[[int], object]) -> str:
        """Read to the end of input and decode it as strict UTF-8, chunk by chunk.

        *check_size* receives the number of characters decoded so far after
        each chunk and raises once that exceeds the caller's collection limit,
        so at most one chunk (:data:`STDIN_CHUNK_BYTES`) past the limit is
        ever decoded: the size it reports lies between the limit and the limit
        plus one chunk. Only an incomplete character (at most three bytes) is
        carried from one chunk to the next. Invalid UTF-8 raises
        :class:`IsolatedStdinDecodeError`; ``OSError`` from the handle
        propagates. After the end of input this returns ``""``.
        """
        parts: list[str] = []
        decoded = 0
        pending = b""
        offset = 0  # the stream position of pending[0]
        while True:
            chunk = self.read_chunk()
            final = not chunk
            data = pending + chunk
            try:
                text, consumed = codecs.utf_8_decode(data, "strict", final)
            except UnicodeDecodeError as error:
                raise IsolatedStdinDecodeError(
                    _stream_decode_error_message(error, offset)
                ) from None
            if text:
                parts.append(text)
                decoded += len(text)
                check_size(decoded)
            offset += consumed
            pending = data[consumed:]
            if final:
                return "".join(parts)


class IsolatedQuotaError(RuntimeError):
    """``fs_write_text`` hit an isolated write limit, or one set to 0.

    The runtime prelude and the example interpreter return it as ``Err``: a
    program can handle the rejection and carry on, and a later, smaller write
    may still fit. It is raised before the destination is opened, so the file
    is unchanged.
    """


def _limit_value(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


class IsolatedFs(IsolatedHostBinding):
    """The ``fs`` grant's binding: a narrow facade over the host filesystem.

    Every operation first resolves its path with the validators
    ``geno run --unsafe --cap fs`` uses (``_serve._resolve_scoped_path`` and
    ``_serve._resolve_scoped_lstat_path``): the same roots, symlink and
    absolute-path rules, and the same error texts. Results are plain values (a
    text reader for ``fs_read_text``, names, booleans, a classified
    ``(kind, size, mtime_ns)`` triple, a path); no stat result, raw ``os``
    access or write handle ever reaches the runtime prelude.

    Writes pass the trusted read-only policy and two portable byte limits:
    *max_file_size* for one write's whole resulting file and
    *max_total_write_bytes* for every write of the run, examples and ``main``
    together (they share this instance). A write is counted in the UTF-8 bytes
    it emits after newline translation to *linesep* (``os.linesep``: text-mode
    ``fs_write_text`` writes CRLF for each LF on Windows), reserved
    from the total before the destination is opened, and never refunded, even
    when the write then fails or rewrites the same path. A limit of 0 disables
    every write, including empty ones and truncation.
    """

    __slots__ = (
        "_allow_absolute",
        "_linesep",
        "_max_file_size",
        "_max_total",
        "_remaining",
        "_roots",
        "_writes_allowed",
    )

    def __init__(
        self,
        roots: Sequence[str],
        *,
        allow_absolute: bool,
        writes_allowed: bool,
        max_file_size: int,
        max_total_write_bytes: int,
        linesep: str = os.linesep,
    ) -> None:
        if isinstance(roots, str) or not roots:
            raise ValueError("the filesystem facade needs at least one root")
        if any(not isinstance(root, str) or not root for root in roots):
            raise ValueError("filesystem roots must be non-empty strings")
        if linesep not in ("\n", "\r\n"):
            raise ValueError("linesep must be '\\n' or '\\r\\n'")
        self._roots = _resolve_fs_roots(list(roots))
        self._allow_absolute = bool(allow_absolute)
        self._writes_allowed = bool(writes_allowed)
        self._max_file_size = _limit_value(max_file_size, "max_file_size")
        self._max_total = _limit_value(max_total_write_bytes, "max_total_write_bytes")
        self._remaining = self._max_total
        self._linesep = linesep

    @property
    def remaining_write_bytes(self) -> int:
        """Bytes the run may still write (examples and ``main`` together)."""
        return self._remaining

    def _resolve(self, path: Any, fn_name: str) -> str:
        return _resolve_scoped_path(
            path, fn_name, self._roots, allow_absolute_paths=self._allow_absolute
        )

    def open_text_reader(self, path: Any, fn_name: str) -> IO[str]:
        """Open a file inside the roots for reading as UTF-8 text.

        The caller reads it with its own bounded reader and closes it.
        """
        return open(self._resolve(path, fn_name), encoding="utf-8")

    def list_dir(self, path: Any, fn_name: str) -> list[str]:
        """The sorted entry names of a directory inside the roots."""
        return sorted(os.listdir(self._resolve(path, fn_name)))

    def exists(self, path: Any, fn_name: str) -> bool:
        """Whether a path inside the roots exists (an escape raises)."""
        return os.path.exists(self._resolve(path, fn_name))

    def metadata(
        self, path: Any, fn_name: str, *, follow: bool
    ) -> tuple[str, int, int]:
        """Return ``(kind, size, mtime_ns)`` for a path inside the roots.

        *kind* is ``"file"``, ``"directory"``, ``"symlink"`` or ``"other"``,
        classified here. With *follow* the final symbolic link is followed
        (``fs_metadata``); without it the link itself is described
        (``fs_symlink_metadata``), after its parent components are resolved
        inside the roots.
        """
        if follow:
            result = os.stat(self._resolve(path, fn_name))
        else:
            result = os.lstat(
                _resolve_scoped_lstat_path(
                    path,
                    fn_name,
                    self._roots,
                    allow_absolute_paths=self._allow_absolute,
                )
            )
        return file_kind_name(result.st_mode), result.st_size, result.st_mtime_ns

    def canonicalize(self, path: Any, fn_name: str) -> str:
        """The resolved path of an existing path inside the roots."""
        resolved = self._resolve(path, fn_name)
        if not os.path.exists(resolved):
            raise FileNotFoundError(f"{fn_name}: path does not exist: {path}")
        return resolved

    def require_writable(self, fn_name: str) -> None:
        """Raise unless the trusted policy allows writes (``GENO_FS_READ_ONLY``).

        Callers check this before :meth:`write_text`, outside their ``Err``
        conversion: a read-only run's write is an error, as in every lane.
        """
        if not self._writes_allowed:
            raise RuntimeError(f"{fn_name}: filesystem writes are not allowed")

    def _reserve(self, size: int, fn_name: str) -> None:
        if self._max_file_size == 0:
            raise IsolatedQuotaError(
                f"{fn_name}: file writes are disabled in this run "
                "(--max-file-size-bytes is 0)"
            )
        if self._max_total == 0:
            raise IsolatedQuotaError(
                f"{fn_name}: file writes are disabled in this run "
                "(--max-total-write-bytes is 0)"
            )
        if size > self._max_file_size:
            raise IsolatedQuotaError(
                f"{fn_name}: writing {size} bytes exceeds the per-file limit of "
                f"{self._max_file_size} bytes (--max-file-size-bytes)"
            )
        if size > self._remaining:
            raise IsolatedQuotaError(
                f"{fn_name}: writing {size} bytes exceeds the remaining total write "
                f"budget ({self._remaining} of {self._max_total} bytes left; "
                "--max-total-write-bytes)"
            )
        self._remaining -= size

    def write_text(self, path: Any, content: Any, fn_name: str) -> None:
        """Replace a file inside the roots with *content*, within both limits.

        In order: the read-only policy, the path, the encoding (UTF-8 after
        newline translation), the disabled limits, the per-file limit, the
        remaining total, the reservation, and only then the open, which
        truncates. Every rejection before the open leaves the file unchanged;
        quota rejections raise :class:`IsolatedQuotaError`, path rejections
        ``RuntimeError`` and encoding errors ``ValueError``, all of which the
        callers return as ``Err``. An ``OSError`` from the open or the write
        does not refund the reservation.
        """
        self.require_writable(fn_name)
        resolved = self._resolve(path, fn_name)
        if not isinstance(content, str):
            raise TypeError(f"{fn_name}: content must be String")
        data = content.replace("\n", self._linesep).encode("utf-8")
        self._reserve(len(data), fn_name)
        with open(resolved, "wb") as handle:
            handle.write(data)


# The shortest timeout an HTTP connection attempt gets once the run's
# deadline has (nearly) passed: it fails at once instead of blocking, and the
# parent's hard kill ends the run anyway.
HTTP_MIN_TIMEOUT_SECONDS = 0.001

# The client modules ``_serve``'s validated HTTP client imports lazily, and
# the hostname codec it needs: preloaded with an ``http`` grant only, before
# the worker freezes its Darwin address-space baseline.
ISOLATED_HTTP_SUPPORT_MODULES = (
    "socket",
    "ssl",
    "http.client",
    "urllib.error",
    "urllib.parse",
    "urllib.request",
    "urllib.response",
    "encodings.idna",
)


def preload_isolated_capability_support(
    capabilities: Collection[str],
) -> tuple[str, ...]:
    """Import the trusted support modules a grant needs; return their names.

    The isolated worker calls this during trusted setup, before it freezes
    the Darwin address-space baseline and before it reads any untrusted
    input, so those modules never count against the program's memory budget.
    Only ``http`` needs any: a run without it loads no client module.
    """
    import importlib

    modules = ISOLATED_HTTP_SUPPORT_MODULES if "http" in capabilities else ()
    for module_name in modules:
        importlib.import_module(module_name)
    return modules


class IsolatedHttp(IsolatedHostBinding):
    """The ``http`` grant's binding: the validated client, bounded by the deadline.

    Requests go through the client ``geno run --unsafe --cap http`` uses
    (``_serve._validate_http_target`` and ``_serve._send_http_request``): only
    ``http`` and ``https``, destinations resolved, validated and pinned for
    every connection and redirect hop, at most ten redirects with caller
    headers dropped across origins, ``ProxyHandler({})`` so that no proxy
    variable reroutes a request, and TLS certificate and hostname checks.
    Non-public destinations are refused unless *allow_private* (the parent's
    ``GENO_HTTP_ALLOW_PRIVATE``, never a variable the program can name).

    Each connection attempt waits at most :data:`HTTP_REQUEST_TIMEOUT_SECONDS`
    and at most the time *remaining* (the run clock) reports, read afresh for
    every hop, so no hop or phase resets the run's deadline; DNS and blocked
    reads past it are ended by the parent's hard kill. The response handle
    goes back to the runtime prelude, which reads it with its own bounded
    reader and closes it; it never reaches Geno code.
    """

    __slots__ = ("_allow_private", "_remaining")

    def __init__(
        self, *, allow_private: bool, remaining: Callable[[], float | None]
    ) -> None:
        if not isinstance(allow_private, bool):
            raise TypeError("allow_private must be a boolean")
        if not callable(remaining):
            raise TypeError("remaining must be callable")
        self._allow_private = allow_private
        self._remaining = remaining

    @property
    def allow_private(self) -> bool:
        """Whether the trusted policy allows non-public destinations."""
        return self._allow_private

    def timeout_seconds(self) -> float:
        """The longest the next connection attempt may take."""
        remaining = self._remaining()
        if remaining is None:
            return HTTP_REQUEST_TIMEOUT_SECONDS
        return min(
            HTTP_REQUEST_TIMEOUT_SECONDS, max(remaining, HTTP_MIN_TIMEOUT_SECONDS)
        )

    def _send(
        self, method: str, url: str, headers: Any, data: bytes | None, fn_name: str
    ) -> Any:
        return _send_http_request(
            method,
            url,
            headers,
            data,
            fn_name,
            allow_private_networks=self._allow_private,
            timeout=self.timeout_seconds,
        )

    def open_body(self, method: str, url: str, data: bytes | None, fn_name: str) -> Any:
        """Send ``http_fetch``'s or ``http_post``'s request; return the response.

        *data* is the UTF-8 request body, sent as ``application/json``, or
        ``None``. Scheme, destination, redirect and transport failures raise
        ``RuntimeError``, ``OSError`` or ``ValueError`` with the ``--unsafe``
        messages; a response with an error status is still returned.
        """
        if data is not None and not isinstance(data, bytes):
            raise TypeError(f"{fn_name}: the request body must be bytes")
        _validate_http_target(url, fn_name, allow_private_networks=self._allow_private)
        headers = [] if data is None else [("Content-Type", "application/json")]
        return self._send(method, url, headers, data, fn_name)

    def open_request(
        self, method: str, url: str, headers: Any, body: str | None, fn_name: str
    ) -> Any:
        """Send ``http_request``'s request; return the response.

        As ``--unsafe``: the method is upper-cased, an empty ``GET`` or
        ``HEAD`` body sends none, and *headers* are ``(name, value)`` pairs.
        """
        _validate_http_target(url, fn_name, allow_private_networks=self._allow_private)
        normalized_method, data = _http_request_data(method, body)
        return self._send(normalized_method, url, headers, data, fn_name)


# The binding class each capability's host table entry must be.
_BINDING_TYPES: dict[str, type[IsolatedHostBinding]] = {
    "env": IsolatedEnv,
    "fs": IsolatedFs,
    "http": IsolatedHttp,
    "stdin": IsolatedStdin,
}


def build_host_table(
    policy: IsolatedRunPolicy,
    *,
    program_args: Sequence[str] = (),
    env_snapshot: Mapping[str, str] | None = None,
    fs_roots: Sequence[str] | None = None,
    max_file_size_bytes: int = 0,
    clock: IsolatedRunClock | None = None,
) -> dict[str, IsolatedHostBinding]:
    """Return the private host table for a validated policy.

    The table holds a binding only for a granted capability that needs host
    access: ``clock``, ``print``, ``random`` and ``regex`` need none. Trusted
    setup always receives the program arguments, but only the ``env``
    binding exposes them (to ``cli_args``). *env_snapshot* must hold only
    allowed names, and only with ``env``. The ``stdin`` binding reads the
    policy's inherited input handle; it reads nothing until asked.

    With ``fs``, *fs_roots* are the run's filesystem roots (trusted setup
    computes them as ``--unsafe`` does) and *max_file_size_bytes* the
    per-file write limit from the parent-set worker configuration; the
    policy supplies the read-only and absolute-path rules and the run's total
    write budget. Roots without ``fs`` are refused.

    With ``http``, *clock* is the run's one deadline (a clock started now
    when omitted), which bounds every connection attempt, and the policy
    decides private-network access.
    """
    policy.validate()
    table: dict[str, IsolatedHostBinding] = {}
    if "env" in policy.capabilities:
        table["env"] = IsolatedEnv(
            policy.env_allowed_names,
            env_snapshot or {},
            program_args,
            case_insensitive=policy.env_case_insensitive,
        )
    elif env_snapshot is not None:
        raise ValueError("an environment snapshot requires the 'env' capability")
    if "stdin" in policy.capabilities:
        table["stdin"] = IsolatedStdin(policy.stdin_fd)
    if "fs" in policy.capabilities:
        if fs_roots is None:
            raise ValueError("the 'fs' capability needs its filesystem roots")
        table["fs"] = IsolatedFs(
            fs_roots,
            allow_absolute=policy.fs_allow_absolute,
            writes_allowed=not policy.fs_read_only,
            max_file_size=max_file_size_bytes,
            max_total_write_bytes=policy.max_total_write_bytes,
        )
    elif fs_roots is not None:
        raise ValueError("filesystem roots require the 'fs' capability")
    if "http" in policy.capabilities:
        run_clock = clock or IsolatedRunClock(policy.deadline_seconds)
        table["http"] = IsolatedHttp(
            allow_private=policy.http_allow_private, remaining=run_clock.remaining
        )
    return table


def checked_prepared_host_state(
    prepared: Mapping[str, Any], policy: IsolatedRunPolicy
) -> tuple[dict[str, IsolatedHostBinding], int]:
    """Validate the host state trusted setup prepared for the program.

    Returns the host table and the output the examples already used. Raises
    ``ValueError`` when the table names an ungranted capability or holds
    anything but a host binding, or when the runtime grant differs from the
    policy.
    """
    host_table = prepared.get("host_table")
    if not isinstance(host_table, dict):
        raise ValueError("host table must be a dict")
    for capability, binding in host_table.items():
        if not isinstance(capability, str) or capability not in policy.capabilities:
            raise ValueError("host table names a capability that was not granted")
        if not isinstance(binding, IsolatedHostBinding):
            raise ValueError("host table holds an object that is not a host binding")
        expected = _BINDING_TYPES.get(capability)
        if expected is None or type(binding) is not expected:
            raise ValueError(f"host table holds the wrong binding for {capability!r}")
    example_output_length = prepared.get("example_output_length")
    if (
        not isinstance(example_output_length, int)
        or isinstance(example_output_length, bool)
        or example_output_length < 0
    ):
        raise ValueError("example output length must be a non-negative integer")
    runtime_capabilities = prepared.get("runtime_capabilities")
    if not isinstance(runtime_capabilities, list) or set(runtime_capabilities) != set(
        policy.capabilities
    ):
        raise ValueError("runtime capabilities differ from the run policy")
    return dict(host_table), example_output_length


def _install_example_env_callbacks(interpreter: Interpreter, env: IsolatedEnv) -> None:
    """Back the example interpreter's env builtins with *env*.

    The same binding instance serves ``main``, so both phases share one
    allowlist, snapshot and argument list. Messages and limits match the
    interpreter's own env builtins; a name outside the allowlist is an E412
    environment-policy denial carrying the same tag as the compiled one.
    """
    from . import builtins as _builtins
    from .diagnostics import ErrorCode
    from .values import BuiltinFunction, ConstructorValue, GenoRuntimeError

    cap = _builtins._InterpreterCap(interpreter.sandbox_config.max_collection_size)

    def _allowed_value(builtin_name: str, name: Any) -> str | None:
        if not isinstance(name, str):
            raise GenoRuntimeError(f"{builtin_name}: name must be a string")
        if not env.allows(name):
            error: Any = GenoRuntimeError(
                env_policy_denied_message(builtin_name, name),
                error_code=ErrorCode.RUNTIME_CAPABILITY_DENIED,
            )
            error._geno_env_policy_denied = name
            raise error
        return env.value(name)

    def _env_get(name: Any) -> Any:
        value = _allowed_value("env_get", name)
        if value is None:
            return ConstructorValue("None", {})
        _builtins._check_result_string_size("env_get", len(value), cap)
        return ConstructorValue("Some", {"value": value})

    def _env_get_or(name: Any, default: Any) -> str:
        value = _allowed_value("env_get_or", name)
        if not isinstance(default, str):
            raise GenoRuntimeError("env_get_or: default must be a string")
        result = default if value is None else value
        _builtins._check_result_string_size("env_get_or", len(result), cap)
        return result

    def _cli_args() -> list[str]:
        limit = _builtins._effective_max_collection_size(cap)
        args = list(env.program_args)
        _builtins._check_collection_size("List", len(args), limit)
        for arg in args:
            _builtins._check_collection_size("String", len(arg), limit)
        return args

    bind = interpreter.global_env.bind
    bind("env_get", BuiltinFunction("env_get", _env_get, 1, ["name"]))
    bind(
        "env_get_or",
        BuiltinFunction("env_get_or", _env_get_or, 2, ["name", "default"]),
    )
    bind("cli_args", BuiltinFunction("cli_args", _cli_args, 0, []))


def _install_example_stdin_callbacks(
    interpreter: Interpreter, stdin: IsolatedStdin
) -> None:
    """Back the example interpreter's ``stdin_read_all`` with *stdin*.

    ``main`` reads through the same binding instance, so both phases consume
    one stream and get the same ``Err`` texts. The size check is the
    interpreter's collection limit, applied chunk by chunk.
    """
    from .values import BuiltinFunction, ConstructorValue

    def _check_size(size: int) -> None:
        interpreter._check_collection_size("String", size, None)

    def _stdin_read_all() -> Any:
        try:
            text = stdin.read_text(_check_size)
        except IsolatedStdinDecodeError as error:
            return ConstructorValue(
                "Err", {"error": f"stdin is not valid UTF-8: {error}"}
            )
        except OSError as error:
            return ConstructorValue("Err", {"error": str(error)})
        return ConstructorValue("Ok", {"value": text})

    interpreter.global_env.bind(
        "stdin_read_all", BuiltinFunction("stdin_read_all", _stdin_read_all, 0, [])
    )


def _install_example_fs_callbacks(interpreter: Interpreter, fs: IsolatedFs) -> None:
    """Back the example interpreter's filesystem builtins with *fs*.

    ``main`` uses the same binding instance, so both phases share one set of
    roots and one write budget, and get the same results: quota rejections
    are ``Err`` with the same text in both. Everything else mirrors
    ``_serve.install_fs_callbacks`` (``--unsafe``): the interpreter's
    collection limits, its ``FileMetadata`` values and its error categories.
    """
    from ._serve import (
        _checked_callback_result,
        _metadata_value_from_parts,
        _read_limited_text_stream,
    )
    from .values import BuiltinFunction, ConstructorValue

    def _err(error: BaseException) -> Any:
        return ConstructorValue("Err", {"error": str(error)})

    def _ok(value: Any) -> Any:
        return _checked_callback_result(
            interpreter, ConstructorValue("Ok", {"value": value})
        )

    def _fs_read_text(path: Any) -> Any:
        try:
            with fs.open_text_reader(path, "fs_read_text") as reader:
                text = _read_limited_text_stream(interpreter, reader, "fs_read_text")
        except (OSError, RuntimeError, ValueError) as error:
            return _err(error)
        return _ok(text)

    def _fs_write_text(path: Any, content: Any) -> Any:
        fs.require_writable("fs_write_text")
        try:
            fs.write_text(path, content, "fs_write_text")
        except (OSError, RuntimeError, ValueError) as error:
            return _err(error)
        return ConstructorValue("Ok", {"value": None})

    def _fs_list_dir(path: Any) -> Any:
        try:
            entries = fs.list_dir(path, "fs_list_dir")
        except (OSError, RuntimeError) as error:
            return _err(error)
        return _ok(entries)

    def _fs_exists(path: Any) -> bool:
        return fs.exists(path, "fs_exists")

    def _metadata(path: Any, fn_name: str, follow: bool) -> Any:
        try:
            kind, size, mtime_ns = fs.metadata(path, fn_name, follow=follow)
        except (OSError, RuntimeError, ValueError) as error:
            return _err(error)
        return _ok(_metadata_value_from_parts(kind, size, mtime_ns, fn_name))

    def _fs_metadata(path: Any) -> Any:
        return _metadata(path, "fs_metadata", True)

    def _fs_symlink_metadata(path: Any) -> Any:
        return _metadata(path, "fs_symlink_metadata", False)

    def _fs_canonicalize(path: Any) -> Any:
        try:
            resolved = fs.canonicalize(path, "fs_canonicalize")
        except (OSError, RuntimeError, ValueError) as error:
            return _err(error)
        return _ok(resolved.replace("\\", "/"))

    bind = interpreter.global_env.bind
    for name, function, params in (
        ("fs_read_text", _fs_read_text, ["path"]),
        ("fs_write_text", _fs_write_text, ["path", "content"]),
        ("fs_list_dir", _fs_list_dir, ["path"]),
        ("fs_exists", _fs_exists, ["path"]),
        ("fs_metadata", _fs_metadata, ["path"]),
        ("fs_symlink_metadata", _fs_symlink_metadata, ["path"]),
        ("fs_canonicalize", _fs_canonicalize, ["path"]),
    ):
        bind(name, BuiltinFunction(name, function, len(params), params))


def _install_example_http_callbacks(
    interpreter: Interpreter, http: IsolatedHttp
) -> None:
    """Back the example interpreter's HTTP builtins with *http*'s policy.

    They are ``_serve.install_http_callbacks`` (``--unsafe``'s), over the same
    validated client ``main`` uses through *http*, with the trusted
    private-network policy and a timeout bounded by both the run's clock and
    the interpreter's own deadline, so the example phase gets no fresh
    allowance either.
    """
    from ._serve import install_http_callbacks

    def _timeout() -> float:
        bound = http.timeout_seconds()
        deadline = interpreter._deadline
        if deadline is not None:
            bound = min(bound, deadline - time.perf_counter())
        return max(bound, HTTP_MIN_TIMEOUT_SECONDS)

    install_http_callbacks(
        interpreter, allow_private_networks=http.allow_private, timeout=_timeout
    )


def build_example_interpreter(
    policy: IsolatedRunPolicy,
    sandbox_config: SandboxConfig,
    host_table: Mapping[str, IsolatedHostBinding] | None = None,
) -> Interpreter:
    """Build the interpreter that checks examples before ``main`` runs.

    The run's exact grant goes to the constructor: it is the only place a
    gated builtin keeps its real implementation, because a later
    ``apply_capabilities`` only narrows. Omitted defaults are never restored.
    Host access comes from the same *host_table* bindings ``main`` uses; a
    granted capability without its binding fails closed rather than reach the
    worker's own host.
    """
    from .interpreter import Interpreter

    table = host_table or {}
    interpreter = Interpreter(
        check_examples=True,
        sandbox_config=sandbox_config,
        capabilities=set(policy.capabilities),
    )
    if "clock" in policy.capabilities:
        # As in --unsafe: `sleep_ms` is real and bounded by the run deadline.
        from ._serve import install_clock_callbacks

        install_clock_callbacks(interpreter)
    if "env" in policy.capabilities:
        env = table.get("env")
        if not isinstance(env, IsolatedEnv):
            raise ValueError("the env grant has no host binding")
        _install_example_env_callbacks(interpreter, env)
    if "stdin" in policy.capabilities:
        stdin = table.get("stdin")
        if not isinstance(stdin, IsolatedStdin):
            raise ValueError("the stdin grant has no host binding")
        _install_example_stdin_callbacks(interpreter, stdin)
    if "fs" in policy.capabilities:
        fs = table.get("fs")
        if not isinstance(fs, IsolatedFs):
            raise ValueError("the fs grant has no host binding")
        _install_example_fs_callbacks(interpreter, fs)
    if "http" in policy.capabilities:
        http = table.get("http")
        if not isinstance(http, IsolatedHttp):
            raise ValueError("the http grant has no host binding")
        _install_example_http_callbacks(interpreter, http)
    return interpreter


def frontend_error_tags(error: BaseException) -> dict[str, str]:
    """Structured denial tags for a failure in the worker's frontend phase.

    Only an interpreter runtime error carrying a tag trusted code set
    qualifies (the denial stub's capability, or the env binding's variable
    name), so a program that panics with the same text gets no tag. The
    parent bases its rerun hint on these tags, never on error text.
    """
    from .values import GenoRuntimeError

    if type(error) is not GenoRuntimeError:
        return {}
    capability = getattr(error, "_geno_capability_denied", None)
    if isinstance(capability, str) and capability in KNOWN_CAPABILITIES:
        return {"capability": capability}
    env_name = getattr(error, "_geno_env_policy_denied", None)
    if isinstance(env_name, str):
        return {"env_name": env_name[:ENV_NAME_TAG_LIMIT]}
    return {}
