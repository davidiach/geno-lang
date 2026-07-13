"""
ProjectGraph — file discovery and manifest parsing
====================================================

Layer 1 of the project resolution pipeline:
1. Find the project root by walking up to ``geno.toml``
2. Parse the manifest to get files, entrypoint, and dependencies
3. Resolve all ``.geno`` file paths relative to the project root
4. Resolve package dependency paths from ``geno_modules/``

Produces a flat list of resolved file paths — no graph analysis yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from .manifest import Manifest

_logger = logging.getLogger(__name__)


class ProjectGraphError(Exception):
    """Error raised during project discovery or resolution."""


def _resolve_contained_path(path: Path, root: Path) -> Path | None:
    """Return the resolved path when it stays within *root*, else None."""
    resolved = path.resolve()
    if resolved.is_relative_to(root.resolve()):
        return resolved
    return None


def _file_entry_to_path(root: Path, file_name: str) -> Path:
    """Resolve a manifest file entry to its candidate ``.geno`` path."""
    if not file_name.endswith(".geno"):
        file_name = file_name + ".geno"
    return root / file_name


@dataclass
class ResolvedFile:
    """A resolved .geno file path with its module name."""

    module_name: str
    path: Path
    is_dependency: bool = False
    package_name: str | None = None  # Set for dependency files
    graph_name: str | None = None  # Internal unique graph key when needed

    @property
    def graph_key(self) -> str:
        """Return the unique module key used by downstream graph resolution."""
        return self.graph_name or self.module_name


def _encode_graph_name_part(value: str) -> str:
    """Return an identifier-safe, collision-free graph-name component."""
    if not value:
        return "Empty"
    return "_".join(f"{ord(ch):02X}" for ch in value)


def dependency_private_graph_name(package_name: str, module_name: str) -> str:
    """Build a unique internal graph key for a dependency-private module."""
    safe_package = _encode_graph_name_part(package_name)
    safe_module = _encode_graph_name_part(module_name)
    return f"GenoDep{safe_package}__{safe_module}"


@dataclass
class ProjectGraph:
    """Result of project file discovery."""

    root: Path | None
    entrypoint: str | None
    files: List[ResolvedFile]
    dependencies: Dict[str, Path]  # dep_name -> dep_dir

    @property
    def file_paths(self) -> List[Path]:
        """All resolved file paths."""
        return [f.path for f in self.files]

    @property
    def module_names(self) -> List[str]:
        """All resolved module names."""
        return [f.module_name for f in self.files]

    @classmethod
    def discover(cls, start_path: Path) -> ProjectGraph:
        """Discover a project starting from a file or directory path.

        If start_path points to a single .geno file without a geno.toml
        in any parent directory, returns a single-file project graph.

        If a geno.toml is found, parses it and resolves all declared files
        and dependencies.
        """
        start_path = start_path.resolve()

        # Find project root
        root = _find_project_root(start_path)

        if root is None:
            # Single-file fallback
            if start_path.is_file() and start_path.suffix == ".geno":
                module_name = start_path.stem
                return cls(
                    root=None,
                    entrypoint=module_name,
                    files=[ResolvedFile(module_name=module_name, path=start_path)],
                    dependencies={},
                )
            if start_path.is_dir():
                # Look for .geno files in the directory
                geno_files = sorted(start_path.glob("*.geno"))
                if geno_files:
                    files = []
                    for geno_file in geno_files:
                        resolved = _resolve_contained_path(geno_file, start_path)
                        if resolved is None:
                            raise ProjectGraphError(
                                f"File '{geno_file.name}' escapes the project root"
                            )
                        files.append(
                            ResolvedFile(module_name=geno_file.stem, path=resolved)
                        )
                    return cls(
                        root=start_path,
                        entrypoint=None,
                        files=files,
                        dependencies={},
                    )
            return cls(
                root=None,
                entrypoint=None,
                files=[],
                dependencies={},
            )

        # Parse manifest
        from .manifest import kebab_to_pascal, parse_manifest

        manifest_path = root / "geno.toml"
        manifest = parse_manifest(manifest_path)

        # Resolve project files
        resolved_files: List[ResolvedFile] = []

        if manifest.files:
            # Explicit file list in manifest
            for file_name in manifest.files:
                file_path = _file_entry_to_path(root, file_name)
                resolved = _resolve_contained_path(file_path, root)
                if resolved is None:
                    raise ProjectGraphError(
                        f"File '{file_name}' escapes the project root"
                    )
                if not file_path.exists():
                    raise ProjectGraphError(
                        f"File '{file_name}' declared in geno.toml not found "
                        f"at {file_path}"
                    )
                module_name = Path(file_name).stem
                resolved_files.append(
                    ResolvedFile(module_name=module_name, path=resolved)
                )
        else:
            # No explicit files: discover all .geno files in root
            for geno_file in sorted(root.glob("*.geno")):
                resolved = _resolve_contained_path(geno_file, root)
                if resolved is None:
                    raise ProjectGraphError(
                        f"File '{geno_file.name}' escapes the project root"
                    )
                resolved_files.append(
                    ResolvedFile(module_name=geno_file.stem, path=resolved)
                )

        # Resolve dependencies from geno_modules/
        resolved_deps: Dict[str, Path] = {}
        geno_modules_dir = root / "geno_modules"
        geno_modules_root = geno_modules_dir.resolve()

        for dep_name, _dep_info in manifest.dependencies.items():
            dep_dir = geno_modules_dir / dep_name
            if not dep_dir.is_dir():
                raise ProjectGraphError(
                    f"Dependency '{dep_name}' not found at {dep_dir}. "
                    f"Run 'geno install' to install dependencies."
                )
            resolved_dep_dir = _resolve_contained_path(dep_dir, geno_modules_root)
            if resolved_dep_dir is None:
                raise ProjectGraphError(f"Dependency '{dep_name}' escapes geno_modules")
            resolved_deps[dep_name] = resolved_dep_dir

            # Resolve the dependency's entry file and sibling modules
            pascal_name = kebab_to_pascal(dep_name)
            dep_manifest = _parse_dependency_manifest(resolved_dep_dir)
            dep_entry = _resolve_dependency_entry(
                resolved_dep_dir, dep_name, pascal_name, dep_manifest=dep_manifest
            )
            registered_stems: set[str] = set()
            registered_paths: set[Path] = set()
            if dep_entry is not None:
                entry_mod_name = (
                    pascal_name if pascal_name != dep_name else dep_entry.stem
                )
                resolved_files.append(
                    ResolvedFile(
                        module_name=entry_mod_name,
                        path=dep_entry,
                        is_dependency=True,
                        package_name=dep_name,
                    )
                )
                registered_stems.add(dep_entry.stem)
                registered_paths.add(dep_entry.resolve())

            if dep_manifest is not None:
                for file_name in dep_manifest.files:
                    file_path = _file_entry_to_path(resolved_dep_dir, file_name)
                    resolved_file = _resolve_contained_path(file_path, resolved_dep_dir)
                    if resolved_file is None:
                        raise ProjectGraphError(
                            f"Dependency '{dep_name}' file '{file_name}' escapes "
                            "the package root"
                        )
                    if not file_path.exists():
                        raise ProjectGraphError(
                            f"File '{file_name}' declared in dependency "
                            f"'{dep_name}' geno.toml not found at {file_path}"
                        )
                    if resolved_file in registered_paths:
                        continue
                    resolved_files.append(
                        ResolvedFile(
                            module_name=file_path.stem,
                            path=resolved_file,
                            is_dependency=True,
                            package_name=dep_name,
                            graph_name=dependency_private_graph_name(
                                dep_name, file_path.stem
                            ),
                        )
                    )
                    registered_stems.add(file_path.stem)
                    registered_paths.add(resolved_file)

            # Pull in sibling .geno files so intra-dependency imports resolve
            for sibling in sorted(resolved_dep_dir.glob("*.geno")):
                if sibling.stem not in registered_stems:
                    resolved_sibling = _resolve_contained_path(
                        sibling, resolved_dep_dir
                    )
                    if resolved_sibling is None:
                        _logger.warning(
                            "Dependency '%s' sibling escapes package root: %s",
                            dep_name,
                            sibling.name,
                        )
                        continue
                    resolved_files.append(
                        ResolvedFile(
                            module_name=sibling.stem,
                            path=resolved_sibling,
                            is_dependency=True,
                            package_name=dep_name,
                            graph_name=dependency_private_graph_name(
                                dep_name, sibling.stem
                            ),
                        )
                    )
                    registered_stems.add(sibling.stem)
                    registered_paths.add(resolved_sibling)

        # Check for module name collisions
        seen_modules: Dict[str, ResolvedFile] = {}
        for rf in resolved_files:
            graph_key = rf.graph_key
            existing = seen_modules.get(graph_key)
            if existing is not None:
                raise ProjectGraphError(
                    f"Module name collision: '{graph_key}' is defined by "
                    f"both {existing.path} and {rf.path}"
                )
            seen_modules[graph_key] = rf

        return cls(
            root=root,
            entrypoint=manifest.entrypoint,
            files=resolved_files,
            dependencies=resolved_deps,
        )


def _find_project_root(start: Path) -> Path | None:
    """Walk up from *start* looking for geno.toml."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for parent in [current, *current.parents]:
        if (parent / "geno.toml").exists():
            return parent
    return None


def _parse_dependency_manifest(dep_dir: Path) -> Manifest | None:
    """Parse a dependency's manifest when present; return None if unusable."""
    dep_manifest = dep_dir / "geno.toml"
    if not dep_manifest.exists():
        return None
    try:
        from .manifest import parse_manifest

        return parse_manifest(dep_manifest)
    except (OSError, ValueError, RuntimeError):
        # A corrupt dependency manifest otherwise surfaces as a misleading
        # 'module not found'; WARNING is visible via Python's default handler
        # even without explicit logging config (M-15).
        _logger.warning(
            "Failed to parse dependency manifest %s", dep_manifest, exc_info=True
        )
        return None


def _resolve_dependency_entry(
    dep_dir: Path,
    dep_name: str,
    pascal_name: str,
    dep_manifest: Manifest | None = None,
) -> Path | None:
    """Find the entry file for a dependency package."""
    dep_root = dep_dir.resolve()

    for label, candidate in (
        ("default entrypoint", dep_dir / f"{pascal_name}.geno"),
        ("package-name entrypoint", dep_dir / f"{dep_name}.geno"),
    ):
        if candidate.exists():
            resolved = _resolve_contained_path(candidate, dep_root)
            if resolved is not None:
                return resolved
            _logger.warning(
                "Dependency '%s' %s escapes package root: %s",
                dep_name,
                label,
                candidate.name,
            )

    if dep_manifest is not None and dep_manifest.entrypoint:
        candidate = _file_entry_to_path(dep_dir, dep_manifest.entrypoint)
        resolved = _resolve_contained_path(candidate, dep_root)
        if resolved is None:
            _logger.warning(
                "Dependency '%s' entrypoint escapes package root: %s",
                dep_name,
                dep_manifest.entrypoint,
            )
            return None
        if candidate.exists():
            return resolved

    return None
