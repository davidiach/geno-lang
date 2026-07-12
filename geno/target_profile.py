"""
TargetProfile — target-aware builtin availability
===================================================

Loads the ``targets.toml`` matrix and answers: "is builtin X available
on target Y?"  Used by the typechecker to reject builtins that the
current target does not support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Set

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


_SOURCE_TARGETS_TOML = Path(__file__).parent.parent / "targets.toml"
_PACKAGE_TARGETS_TOML = Path(__file__).parent / "targets.toml"
TARGETS_TOML = (
    _SOURCE_TARGETS_TOML if _SOURCE_TARGETS_TOML.exists() else _PACKAGE_TARGETS_TOML
)

VALID_TARGETS = {"python-cli", "node-cli", "browser", "python-hosted"}


class ManifestTargetError(ValueError):
    """Raised when ``geno.toml`` declares an invalid target."""


def _valid_targets_text() -> str:
    return ", ".join(sorted(VALID_TARGETS))


@dataclass
class TargetProfile:
    """Availability info for a specific compilation target."""

    target: str
    # Builtins explicitly marked "unavailable" on this target
    unavailable: Set[str] = field(default_factory=set)
    # Builtins that are "capability-gated" on this target
    capability_gated: Dict[str, str] = field(
        default_factory=dict
    )  # builtin -> capability
    # Capabilities granted by this target's runtime environment
    capabilities: Set[str] = field(default_factory=set)
    # Path to targets.toml used for this profile (for alternative suggestions)
    _toml_path: Path = field(default_factory=lambda: TARGETS_TOML)

    def is_available(self, builtin_name: str) -> bool:
        """True if the builtin can be used on this target."""
        return builtin_name not in self.unavailable

    def rejection_message(self, builtin_name: str) -> str:
        """Error message explaining why a builtin is unavailable."""
        alternatives = self._find_supporting_targets(builtin_name)
        if alternatives:
            alt_str = ", ".join(sorted(alternatives))
            return (
                f"'{builtin_name}' is not available on the '{self.target}' target. "
                f"Available on: {alt_str}"
            )
        return f"'{builtin_name}' is not available on the '{self.target}' target."

    def _find_supporting_targets(self, builtin_name: str) -> list[str]:
        """Find targets that support the given builtin."""
        path = self._toml_path
        if not path.exists() or tomllib is None:
            return []
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        info = raw.get("builtins", {}).get(builtin_name, {})
        return [
            t
            for t in sorted(VALID_TARGETS)
            if t != self.target and info.get(t, "available") != "unavailable"
        ]

    @classmethod
    def load(cls, target: str, toml_path: Path | None = None) -> TargetProfile:
        """Load a target profile from targets.toml.

        Args:
            target: One of 'python-cli', 'node-cli', 'browser', 'python-hosted'.
            toml_path: Optional path override (for testing).
        """
        if target not in VALID_TARGETS:
            raise ValueError(
                f"Unknown target '{target}'. "
                f"Valid targets: {', '.join(sorted(VALID_TARGETS))}"
            )

        path = toml_path or TARGETS_TOML
        if not path.exists():
            raise RuntimeError(
                f"Target metadata not found: {path}. "
                "This is a packaging error; geno/targets.toml must be included."
            )

        if tomllib is None:
            raise RuntimeError(
                "TOML parsing not available. Install tomli for Python < 3.11."
            )
        raw = tomllib.loads(path.read_text(encoding="utf-8"))

        unavailable: set[str] = set()
        capability_gated: dict[str, str] = {}
        capabilities = set(
            raw.get("targets", {}).get(target, {}).get("capabilities", [])
        )

        for builtin_name, info in raw.get("builtins", {}).items():
            availability = info.get(target, "available")
            if availability == "unavailable":
                unavailable.add(builtin_name)
            elif availability == "capability-gated":
                cap = info.get("capability", "")
                capability_gated[builtin_name] = cap

        return cls(
            target=target,
            unavailable=unavailable,
            capability_gated=capability_gated,
            capabilities=capabilities,
            _toml_path=path,
        )

    @classmethod
    def permissive(cls) -> TargetProfile:
        """Return a profile that allows all builtins (no target restriction)."""
        return cls(target="any")


def resolve_manifest_targets(project_root: Path | None) -> list[str]:
    """Read target declarations from a project's ``geno.toml`` manifest.

    Returns all declared targets in manifest order. Invalid target names
    are hard errors so a manifest typo cannot silently disable target policy.
    """
    if project_root is None:
        return []
    manifest_path = project_root / "geno.toml"
    if not manifest_path.exists():
        return []

    from .manifest import parse_manifest

    manifest = parse_manifest(manifest_path)
    if not manifest.targets:
        return []

    invalid = [target for target in manifest.targets if target not in VALID_TARGETS]
    if invalid:
        target = invalid[0]
        raise ManifestTargetError(
            f"Unknown target '{target}' in geno.toml. "
            f"Valid targets: {_valid_targets_text()}."
        )

    targets: list[str] = []
    for target in manifest.targets:
        if target not in targets:
            targets.append(target)
    return targets


def resolve_manifest_target(project_root: Path | None) -> str | None:
    """Read the first target from a project's ``geno.toml`` manifest."""
    targets = resolve_manifest_targets(project_root)
    return targets[0] if targets else None
