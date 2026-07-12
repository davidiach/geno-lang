"""Shared capability parsing and validation.

Deliberately light: this module is imported by the CLI entry point on
every invocation, so it must not pull in the full builtin registry
(which builds the builtin/type tables at import). Capability *names*
come straight from the manifest.
"""

from __future__ import annotations

from collections.abc import Iterable

from .builtin_manifest import manifest_capability_names

KNOWN_CAPABILITIES = manifest_capability_names()

# Capabilities granted to programs that do not ask for any. Re-exported by
# builtin_registry for its existing importers.
DEFAULT_ALLOWED_CAPABILITIES = frozenset({"print", "clock", "random"})


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
