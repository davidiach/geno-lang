"""
Filesystem-based Module Resolver
================================

Resolves import statements to .geno files on disk. Walks the import graph
transitively and returns a modules dict compatible with the existing
RunConfig.modules / typechecker / interpreter interfaces.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, cast

from .ast_nodes import ImportStatement, Program
from .lexer import Lexer
from .parser import Parser
from .project_graph import dependency_private_graph_name

_logger = logging.getLogger(__name__)


class ModuleResolutionError(Exception):
    """Raised when a module cannot be found on the filesystem."""

    def __init__(self, module_name: str, search_path: Path, location=None):
        self.module_name = module_name
        self.search_path = search_path
        self.location = location
        super().__init__(
            f"Module '{module_name}' not found. "
            f"Searched for '{module_name}.geno' in {search_path}"
        )


class AmbiguousModuleError(ModuleResolutionError):
    """Raised when a case-insensitive lookup matches multiple files."""

    def __init__(self, module_name: str, matches: list[Path], location=None):
        self.module_name = module_name
        self.matches = matches
        self.search_path = matches[0].parent if matches else Path(".")
        self.location = location
        names = ", ".join(sorted(p.name for p in matches))
        Exception.__init__(
            self,
            f"Module '{module_name}' is ambiguous: multiple files match "
            f"case-insensitively ({names}). Rename one file so only a "
            f"single case variant exists.",
        )


class CircularImportError(Exception):
    """Raised when a circular import is detected."""

    def __init__(self, module_name: str, search_path: Path, location=None):
        self.module_name = module_name
        self.search_path = search_path
        self.location = location
        super().__init__(
            f"Circular import detected: module '{module_name}' is already being resolved"
        )


@dataclass(frozen=True)
class ResolvedModuleSource:
    """Resolved filesystem module source plus its originating path."""

    module_name: str
    path: Path
    source: str
    is_dependency: bool = False
    package_name: str | None = None
    graph_name: str | None = None

    @property
    def graph_key(self) -> str:
        """Return the unique key used when dependency-private names collide."""
        return self.graph_name or self.module_name


def resolve_modules(
    source_path: Path,
    program: Program,
    source_overrides: Mapping[Path, str] | None = None,
) -> dict[str, str]:
    """
    Walk import statements in a parsed program and load module sources from disk.

    Resolution rule: for ``import Foo``, look for ``Foo.geno`` in the same
    directory as the importing file.

    Returns:
        dict mapping module name to source code string, including transitive
        imports. Compatible with ``RunConfig(modules=...)``.
    """
    return {
        name: resolved.source
        for name, resolved in resolve_module_sources(
            source_path,
            program,
            source_overrides=source_overrides,
        ).items()
    }


def resolve_module_sources(
    source_path: Path,
    program: Program,
    source_overrides: Mapping[Path, str] | None = None,
) -> dict[str, ResolvedModuleSource]:
    """Return resolved module sources without observing partial package updates."""
    from .package_manager import _package_transaction_locks

    base_dir = source_path.parent.resolve()
    with _package_transaction_locks(base_dir):
        return _resolve_module_sources_unlocked(
            base_dir, program, source_overrides=source_overrides
        )


def _resolve_module_sources_unlocked(
    base_dir: Path,
    program: Program,
    source_overrides: Mapping[Path, str] | None = None,
) -> dict[str, ResolvedModuleSource]:
    overrides = {
        Path(path).resolve(): source
        for path, source in (source_overrides or {}).items()
    }
    modules: dict[str, ResolvedModuleSource] = {}
    _resolve_imports(program, base_dir, modules, source_overrides=overrides)
    return modules


def _resolve_imports(
    program,
    base_dir: Path,
    modules: dict[str, ResolvedModuleSource],
    resolving: set[str] | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> None:
    """Recursively resolve imports from a parsed program."""
    if resolving is None:
        resolving = set()

    for defn in program.definitions:
        if isinstance(defn, ImportStatement):
            name = defn.module_name

            # Defense-in-depth: reject path separators in module names
            if "/" in name or "\\" in name or ".." in name:
                raise ModuleResolutionError(name, base_dir, defn.location)

            file_path = base_dir / f"{name}.geno"
            if file_path.exists():
                exact_path = _find_exact_case(name, base_dir)
                if exact_path is None:
                    case_path = _find_case_insensitive(
                        name, base_dir, location=defn.location
                    )
                    if case_path is not None:
                        file_path = case_path
                else:
                    file_path = exact_path
            else:
                # Fallback 1: look in the standard library
                found = _find_in_std(name)
                # Fallback 2: look in geno_modules/<name>/ for the dependency
                if found is None:
                    found = _find_in_geno_modules(name, base_dir)
                # Fallback 3: case-insensitive match in base_dir. Lets adopters
                # with lowercase filesystem conventions (e.g., cli.geno) satisfy
                # a PascalCase import without shipping redirect stubs.
                if found is None:
                    found = _find_case_insensitive(
                        name, base_dir, location=defn.location
                    )
                if found is None:
                    raise ModuleResolutionError(name, base_dir, defn.location)
                file_path = found

            # Resolve the file path and verify it stays within the base directory,
            # within geno_modules/ under the project root, or in the std library
            resolved_file = file_path.resolve()
            project_root = _find_project_root(base_dir)
            allowed_roots = [base_dir.resolve(), _STD_DIR.resolve()]
            if project_root:
                modules_root = project_root / "geno_modules"
                if modules_root.is_symlink():
                    raise ModuleResolutionError(name, base_dir, defn.location)
                allowed_roots.append(modules_root.resolve())
            if not any(resolved_file.is_relative_to(r) for r in allowed_roots):
                raise ModuleResolutionError(name, base_dir, defn.location)

            package_name = _dependency_package_for_path(resolved_file, project_root)
            graph_name = None
            if (
                package_name is not None
                and project_root is not None
                and _is_inside_dependency_package(base_dir, project_root, package_name)
            ):
                graph_name = dependency_private_graph_name(package_name, name)
            storage_key = graph_name or name

            # Check circular imports BEFORE the already-resolved check,
            # so that A->B->A is detected even though A is in modules.
            # Dependency-private modules are keyed by their graph names so
            # different packages can safely contain same-stem private files.
            if storage_key in resolving:
                raise CircularImportError(name, base_dir, defn.location)

            if storage_key in modules:
                continue  # already resolved

            resolving.add(storage_key)
            override_source = None
            if source_overrides is not None:
                override_source = source_overrides.get(resolved_file)
            source = (
                override_source
                if override_source is not None
                else resolved_file.read_text(encoding="utf-8")
            )
            modules[storage_key] = ResolvedModuleSource(
                module_name=name,
                path=resolved_file,
                source=source,
                is_dependency=package_name is not None,
                package_name=package_name,
                graph_name=graph_name,
            )

            # Parse the module to find its transitive imports
            # Use the resolved file's directory as base_dir so dependency-internal
            # imports resolve relative to the dependency, not the caller's project
            tokens = Lexer(source, str(file_path)).tokenize()
            mod_program = Parser(tokens).parse_program()
            mod_base_dir = resolved_file.parent
            _resolve_imports(
                mod_program,
                mod_base_dir,
                modules,
                resolving,
                source_overrides,
            )

            resolving.discard(storage_key)


_STD_DIR = Path(__file__).parent / "std"


def _find_in_std(name: str) -> Path | None:
    """Look for a module in the Geno standard library (geno/std/)."""
    candidate = _STD_DIR / f"{name}.geno"
    if candidate.exists():
        return candidate
    return None


def _find_case_insensitive(name: str, base_dir: Path, location=None) -> Path | None:
    """Case-insensitive filesystem match for ``<name>.geno`` in ``base_dir``.

    Raises ``AmbiguousModuleError`` if two or more files differ only in case.
    """
    if not base_dir.is_dir():
        return None
    target = f"{name}.geno".lower()
    matches = [
        p for p in base_dir.iterdir() if p.is_file() and p.name.lower() == target
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise AmbiguousModuleError(name, matches, location)
    return matches[0]


def _find_exact_case(name: str, base_dir: Path) -> Path | None:
    """Return the directory entry whose name exactly matches ``<name>.geno``."""
    if not base_dir.is_dir():
        return None
    target = f"{name}.geno"
    for path in base_dir.iterdir():
        if path.is_file() and path.name == target:
            return path
    return None


def _find_project_root(start: Path) -> Path | None:
    """Walk up from *start* looking for geno.toml. Returns None if not found."""
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / "geno.toml").exists():
            return parent
    return None


def _dependency_package_for_path(path: Path, project_root: Path | None) -> str | None:
    """Return the dependency package name for a path under geno_modules."""
    if project_root is None:
        return None
    geno_modules_root = (project_root / "geno_modules").resolve()
    try:
        rel = path.resolve().relative_to(geno_modules_root)
    except ValueError:
        return None
    if not rel.parts:
        return None
    return _canonical_geno_modules_package_name(geno_modules_root, rel.parts[0])


def _canonical_geno_modules_package_name(
    geno_modules_root: Path, package_name: str
) -> str:
    """Return the real installed package directory name when available."""
    candidate = geno_modules_root / package_name
    try:
        for child in geno_modules_root.iterdir():
            if child.name == package_name:
                return child.name
            try:
                if child.samefile(candidate):
                    return child.name
            except OSError:
                continue
    except OSError:
        return package_name
    return package_name


def _is_inside_dependency_package(
    base_dir: Path,
    project_root: Path,
    package_name: str,
) -> bool:
    """Return True when imports are being resolved from inside one package."""
    package_root = (project_root / "geno_modules" / package_name).resolve()
    return _is_same_or_descendant_path(base_dir, package_root)


def _is_same_or_descendant_path(path: Path, root: Path) -> bool:
    """Return True when *path* is *root* or below it by filesystem identity."""
    try:
        current = path.resolve()
        resolved_root = root.resolve()
    except OSError:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            return False
        return True

    while True:
        try:
            if current.samefile(resolved_root):
                return True
        except OSError:
            if current == resolved_root:
                return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _find_in_geno_modules(name: str, base_dir: Path) -> Path | None:
    """Look for a module in geno_modules/<name>/ under the project root.

    Searches using both the import name (PascalCase) and its kebab-case
    equivalent, then falls back to the dependency's own entrypoint.
    """
    from .manifest import pascal_to_kebab

    project_root = _find_project_root(base_dir)
    if project_root is None:
        return None

    modules_root = project_root / "geno_modules"
    if modules_root.is_symlink():
        raise ModuleResolutionError(name, base_dir)
    # Try both the direct name and its kebab-case equivalent
    candidates_dirs: list[str] = [name, cast(str, pascal_to_kebab(name))]
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_dirs: list[str] = []
    for d in candidates_dirs:
        if d not in seen:
            seen.add(d)
            unique_dirs.append(d)

    for dir_name in unique_dirs:
        dep_dir = _find_geno_modules_dependency_dir(modules_root, dir_name)
        if dep_dir is None:
            continue

        # Direct match: geno_modules/<dir_name>/<name>.geno
        candidate = dep_dir / f"{name}.geno"
        if candidate.exists():
            return candidate

        # Also try the dir_name as file name
        if dir_name != name:
            candidate = dep_dir / f"{dir_name}.geno"
            if candidate.exists():
                return candidate

        # Fallback: read the dependency's geno.toml for its entrypoint
        dep_manifest = dep_dir / "geno.toml"
        if dep_manifest.exists():
            if dep_manifest.is_symlink():
                try:
                    resolved_manifest = dep_manifest.resolve(strict=True)
                    relative_manifest = resolved_manifest.relative_to(dep_dir.resolve())
                except (OSError, RuntimeError, ValueError) as exc:
                    raise ModuleResolutionError(name, dep_dir) from exc
                if ".git" in relative_manifest.parts or not resolved_manifest.is_file():
                    raise ModuleResolutionError(name, dep_dir)
            try:
                from .manifest import parse_manifest

                m = parse_manifest(dep_manifest, allow_symlink=True)
                if m.entrypoint:
                    entrypoint = cast(str, m.entrypoint)
                    candidate = dep_dir / f"{entrypoint}.geno"
                    if candidate.exists():
                        return candidate
            except (OSError, ValueError, RuntimeError):
                # A corrupt dependency manifest otherwise surfaces as a
                # misleading 'module not found'; WARNING is visible via Python's
                # default handler even without explicit logging config (M-15).
                _logger.warning(
                    "Failed to parse manifest in %s", dep_dir, exc_info=True
                )

    return None


def _find_geno_modules_dependency_dir(
    geno_modules_root: Path, dir_name: str
) -> Path | None:
    """Return the actual directory entry for a dependency package."""
    if not geno_modules_root.is_dir():
        return None

    case_matches: list[Path] = []
    try:
        for child in geno_modules_root.iterdir():
            if not child.is_dir():
                continue
            if child.name == dir_name:
                return child
            if child.name.lower() == dir_name.lower():
                case_matches.append(child)
    except OSError:
        candidate = geno_modules_root / dir_name
        return candidate if candidate.is_dir() else None

    if len(case_matches) == 1:
        return case_matches[0]
    return None
