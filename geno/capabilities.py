"""Shared capability parsing and validation.

Deliberately light: this module is imported by the CLI entry point on
every invocation, so it must not pull in the full builtin registry
(which builds the builtin/type tables at import). Capability *names*
come straight from the manifest.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from .builtin_manifest import manifest_capability_names

KNOWN_CAPABILITIES = manifest_capability_names()

# Capabilities granted to programs that do not ask for any. Re-exported by
# builtin_registry for its existing importers.
DEFAULT_ALLOWED_CAPABILITIES = frozenset({"print", "clock", "random"})

# ---------------------------------------------------------------------------
# Capability grants in the process-isolated ``geno run`` lane (proposal 0013)
#
# One source of truth for the parent's pre-launch checks, the ``--cap`` help
# text, the denial hint, the worker's policy validator and both compilers.

# Capabilities the isolated lane can grant today. Each milestone of proposal
# 0013 adds its capability here once its implementation and gates land; any
# other capability is refused before the worker starts.
ISOLATED_RUN_CAPABILITIES = frozenset(
    {"clock", "env", "fs", "print", "random", "regex"}
)

# The lane default of both isolated write limits when ``fs`` is granted: the
# per-file limit (``--max-file-size-bytes``) and the run's total
# (``--max-total-write-bytes``). Both default to 0 without ``fs``.
ISOLATED_RUN_FS_WRITE_LIMIT_BYTES = 64 * 1024 * 1024

# Never granted in the isolated lane: they need ``--unsafe``.
ISOLATED_RUN_REFUSED_CAPABILITIES = frozenset({"process", "serve"})

# Refused before launch on Windows until their platform gates have evidence
# there (stdin handle inheritance and cleanup, HTTP client behaviour).
ISOLATED_RUN_WINDOWS_UNAVAILABLE = frozenset({"stdin", "http"})

# The runtime prelude's private host channel in the isolated worker. Generated
# program code can neither define nor reference these names: both compilers
# reserve them and the worker's validator rejects them in the program tail.
ISOLATED_HOST_PRIVATE_NAMES = frozenset(
    {"_GENO_HOST", "_GenoHostUnavailable", "_geno_host_module", "_geno_environ"}
)

_ISOLATED_RUN_REFUSAL_REASONS = {
    "process": "programs it starts would run outside the sandbox",
    "serve": "a listener would outlive the run's wall-clock limit and buffered output",
}


def isolated_env_name_error(name: object) -> str | None:
    """Return why *name* cannot be an ``--allow-env`` entry, or ``None``.

    Names are exact (no wildcard or prefix grants). They must be non-empty
    and free of ``=`` and NUL, which no environment variable name can hold.
    """
    if not isinstance(name, str) or not name:
        return "must be a non-empty environment variable name"
    if "=" in name:
        return "must not contain '='"
    if "\x00" in name:
        return "must not contain a NUL character"
    return None


def isolated_run_capabilities(os_name: str | None = None) -> frozenset[str]:
    """Return the capabilities the isolated ``geno run`` lane grants on *os_name*.

    *os_name* defaults to this host's ``os.name``; tests pass another value to
    pin the platform matrix.
    """
    name = os.name if os_name is None else os_name
    if name == "nt":
        return ISOLATED_RUN_CAPABILITIES - ISOLATED_RUN_WINDOWS_UNAVAILABLE
    return ISOLATED_RUN_CAPABILITIES


def isolated_run_refusal(capability: str, os_name: str | None = None) -> str | None:
    """Return why the isolated lane refuses *capability* before launch, or None.

    The message has no ``Error:`` prefix; the CLI adds it.
    """
    if capability in ISOLATED_RUN_REFUSED_CAPABILITIES:
        reason = _ISOLATED_RUN_REFUSAL_REASONS[capability]
        return (
            f"--cap {capability} is not available in the process-isolated run "
            f"mode: {reason}. Use --unsafe --cap ... for trusted code."
        )
    if capability in isolated_run_capabilities(os_name):
        return None
    name = os.name if os_name is None else os_name
    where = (
        " on Windows"
        if name == "nt" and capability in ISOLATED_RUN_CAPABILITIES
        else ""
    )
    return (
        f"--cap {capability} is not yet available in the process-isolated run "
        f"mode{where}. Use --unsafe --cap ... for trusted code, or --json "
        "--cap ... for embedding API execution."
    )


def isolated_run_refusals(
    capabilities: Iterable[str], os_name: str | None = None
) -> list[str]:
    """Return one refusal message per refused capability, sorted by name."""
    messages: list[str] = []
    for capability in sorted(set(capabilities)):
        message = isolated_run_refusal(capability, os_name)
        if message is not None:
            messages.append(message)
    return messages


def isolated_run_cap_help() -> str:
    """Return the ``geno run --cap`` help text, derived from the lane rules."""
    supported = ", ".join(sorted(ISOLATED_RUN_CAPABILITIES))
    others = ", ".join(sorted(KNOWN_CAPABILITIES - ISOLATED_RUN_CAPABILITIES))
    return (
        "Grant a capability (e.g. --cap print or --cap print,regex). "
        "Repeatable and comma-separable; the grant is exactly this list. "
        f"The default process-isolated mode grants {supported}; "
        f"{others} need --unsafe (trusted code only). --json also accepts --cap."
        " With env, environment variables stay hidden unless named with"
        " --allow-env."
    )


def capability_denied_message(builtin_name: str, capability: str | None) -> str:
    """Return the runtime message for a call to a builtin whose cap is missing.

    Every execution lane uses this wording so the denial always names the
    missing capability. The compiled runtimes (``_runtime_support.py`` and
    ``_js_runtime_support.js``) cannot import this module, so they spell the
    same text out; a parity test keeps them aligned.
    """
    if capability is None:
        return (
            f"Capability denied: '{builtin_name}' requires a capability that "
            "was not granted"
        )
    return f"Capability denied: '{builtin_name}' requires the '{capability}' capability"


class CapabilityParseError(ValueError):
    """Raised when a capability list contains an unknown or malformed name."""


def _valid_capabilities_text() -> str:
    return ", ".join(sorted(KNOWN_CAPABILITIES))


def _unknown_capability_message(name: str) -> str:
    from difflib import get_close_matches  # error path only; difflib is slow

    message = f"Unknown capability '{name}'. Valid capabilities: {_valid_capabilities_text()}."
    suggestions = get_close_matches(name, sorted(KNOWN_CAPABILITIES), n=1, cutoff=0.6)
    if suggestions:
        message += f" Did you mean '{suggestions[0]}'?"
    return message


def normalize_capability_values(
    values: Iterable[str],
    *,
    allow_comma: bool = False,
) -> set[str]:
    """Normalize and validate capability names from CLI/API inputs."""
    capabilities: set[str] = set()
    for raw_value in values:
        raw_parts = raw_value.split(",") if allow_comma else [raw_value]
        for raw_part in raw_parts:
            capability = raw_part.strip()
            if not capability:
                raise CapabilityParseError("Capability names must not be empty.")
            if capability not in KNOWN_CAPABILITIES:
                raise CapabilityParseError(_unknown_capability_message(capability))
            capabilities.add(capability)
    return capabilities
