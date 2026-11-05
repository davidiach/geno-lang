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
only the standard library and :mod:`geno.capabilities`. The interpreter and
the runtime values are imported inside the functions that need them.
"""

from __future__ import annotations

import math
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .capabilities import (
    DEFAULT_ALLOWED_CAPABILITIES,
    KNOWN_CAPABILITIES,
    isolated_run_capabilities,
)

if TYPE_CHECKING:
    from .interpreter import Interpreter
    from .sandbox import SandboxConfig


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
            if "=" in name:
                raise IsolatedRunPolicyError(
                    "env_allowed_names entries must not contain '='"
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


def isolated_run_policy_for(
    grant: Collection[str] | None,
    *,
    timeout: float | None,
    os_name: str | None = None,
) -> IsolatedRunPolicy:
    """Build and validate the policy the parent sends for one isolated run.

    *grant* is the ``--cap`` set, or ``None`` for the default grant. Host
    policy is resolved only for the capability it configures: a grant without
    ``fs`` or ``http`` never reads those capabilities' settings.
    """
    capabilities = frozenset(DEFAULT_ALLOWED_CAPABILITIES if grant is None else grant)
    deadline: float | None = None
    if timeout is not None and math.isfinite(timeout) and timeout > 0:
        deadline = float(timeout)
    policy = IsolatedRunPolicy(capabilities=capabilities, deadline_seconds=deadline)
    policy.validate(os_name)
    return policy


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


def build_host_table(policy: IsolatedRunPolicy) -> dict[str, IsolatedHostBinding]:
    """Return the private host table for a validated policy.

    The table holds a binding only for a granted capability that needs host
    access. ``clock``, ``print``, ``random`` and ``regex`` need none, so the
    table is empty for every grant the lane supports so far.
    """
    policy.validate()
    return {}


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


def build_example_interpreter(
    policy: IsolatedRunPolicy, sandbox_config: SandboxConfig
) -> Interpreter:
    """Build the interpreter that checks examples before ``main`` runs.

    The run's exact grant goes to the constructor: it is the only place a
    gated builtin keeps its real implementation, because a later
    ``apply_capabilities`` only narrows. Omitted defaults are never restored.
    """
    from .interpreter import Interpreter

    interpreter = Interpreter(
        check_examples=True,
        sandbox_config=sandbox_config,
        capabilities=set(policy.capabilities),
    )
    if "clock" in policy.capabilities:
        # As in --unsafe: `sleep_ms` is real and bounded by the run deadline.
        from ._serve import install_clock_callbacks

        install_clock_callbacks(interpreter)
    return interpreter


def frontend_error_tags(error: BaseException) -> dict[str, str]:
    """Structured denial tags for a failure in the worker's frontend phase.

    Only an interpreter runtime error carrying the tag its denial stub sets
    qualifies, so a program that panics with the same text gets no tag. The
    parent bases its rerun hint on these tags, never on error text.
    """
    from .values import GenoRuntimeError

    if type(error) is not GenoRuntimeError:
        return {}
    capability = getattr(error, "_geno_capability_denied", None)
    if isinstance(capability, str) and capability in KNOWN_CAPABILITIES:
        return {"capability": capability}
    return {}
