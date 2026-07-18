"""
Project resolution helpers shared by the CLI and embedding API.

This module centralizes the "find project -> resolve imports -> identify
entrypoint" flow so command surfaces and API wrappers do not drift apart.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, cast

from .dependency_graph import (
    DependencyGraph,
    _dependency_package_module_maps,
    _parse_module_source,
    _rewrite_dependency_imports,
)
from .module_resolver import (
    CircularImportError,
    ModuleResolutionError,
    resolve_module_sources,
)
from .project_graph import (
    ProjectGraph,
    ResolvedFile,
    dependency_private_graph_name,
)

if TYPE_CHECKING:
    from .ast_nodes import Program
    from .module_resolver import ResolvedModuleSource


class ProjectResolutionError(Exception):
    """Raised when a path cannot be resolved to an executable Geno project."""


@dataclass(frozen=True)
class MergedModuleInputs:
    """Overlay-merged sources plus diagnostic metadata from selective expansion."""

    module_sources: dict[str, str] | None
    overlay_graph_keys: dict[str, str]
    module_paths: dict[str, str]


def describe_project_resolution_error(exc: Exception) -> str:
    """Return a user-facing project resolution error message."""
    if isinstance(exc, FileNotFoundError):
        path = exc.filename or str(exc)
        return f"File not found: {path}"
    return str(exc)


def _missing_path_error(path: str | Path) -> FileNotFoundError:
    """Build a FileNotFoundError that preserves the caller's original spelling."""
    return FileNotFoundError(2, "No such file or directory", str(path))


def _module_display_names(file_map: Mapping[str, ResolvedFile]) -> dict[str, str]:
    """Map internal graph keys to user-facing module names where unambiguous.

    Same-stem private modules from different dependencies share a user-facing
    name; those keep their unique graph keys so the mapping stays invertible.
    """
    name_counts = Counter(
        resolved_file.module_name for resolved_file in file_map.values()
    )
    return {
        graph_key: (
            resolved_file.module_name
            if name_counts[resolved_file.module_name] == 1
            else graph_key
        )
        for graph_key, resolved_file in file_map.items()
    }


def _resolved_file_identity(resolved_file: ResolvedFile) -> tuple[str, str]:
    """Return a stable identity for deduplicating overlay candidates."""
    return resolved_file.graph_key, str(resolved_file.path.resolve())


def _dedupe_resolved_files(
    resolved_files: list[ResolvedFile],
) -> list[ResolvedFile]:
    """Deduplicate resolved files while preserving discovery order."""
    result: list[ResolvedFile] = []
    seen: set[tuple[str, str]] = set()
    for resolved_file in resolved_files:
        identity = _resolved_file_identity(resolved_file)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(resolved_file)
    return result


def _overlay_candidate_files(
    project: ProjectGraph,
    file_map: Mapping[str, ResolvedFile],
) -> list[ResolvedFile]:
    """Return overlay candidates with canonical project/package metadata."""
    candidates = list(file_map.values())
    if project.root is None:
        return _dedupe_resolved_files(candidates)

    manifest_path = project.root / "geno.toml"
    if manifest_path.is_file():
        # File-focused contexts intentionally retain only their import closure.
        # Re-discover metadata (not sources) so on-demand overlays still honor
        # dependency entry aliases, manifest-declared nested files, and private
        # graph keys exactly as the canonical project graph does.
        candidates.extend(ProjectGraph.discover(project.root).files)
        return _dedupe_resolved_files(candidates)

    # Manifestless direct-file projects have no richer metadata source. Their
    # resolver contract is direct siblings only, so scan just that directory.
    try:
        siblings = sorted(project.root.glob("*.geno"))
    except OSError:
        siblings = []
    existing_by_path = {
        resolved_file.path.resolve(): resolved_file for resolved_file in candidates
    }
    for sibling in siblings:
        path = sibling.resolve()
        candidates.append(
            existing_by_path.get(path) or ResolvedFile(module_name=path.stem, path=path)
        )
    return _dedupe_resolved_files(candidates)


def _overlay_candidates(
    name: str,
    candidate_files: list[ResolvedFile],
) -> list[ResolvedFile]:
    """Filter canonical candidate files by graph key, module alias, or stem."""
    return _dedupe_resolved_files(
        [
            resolved_file
            for resolved_file in candidate_files
            if name
            in {
                resolved_file.graph_key,
                resolved_file.module_name,
                resolved_file.path.stem,
            }
        ]
    )


def _overlay_candidate_description(resolved_file: ResolvedFile) -> str:
    """Describe an overlay target with its package and filesystem path."""
    if resolved_file.is_dependency and resolved_file.package_name is not None:
        owner = f"dependency package '{resolved_file.package_name}'"
    else:
        owner = "the project"
    return f"{owner} ({resolved_file.path})"


def _ambiguous_overlay_error(
    name: str, candidates: list[ResolvedFile]
) -> ProjectResolutionError:
    """Build an actionable error for a flat overlay name with multiple targets."""
    descriptions = "; ".join(
        _overlay_candidate_description(candidate)
        for candidate in _dedupe_resolved_files(candidates)
    )
    return ProjectResolutionError(
        f"Overlay module '{name}' is ambiguous: candidates are {descriptions}. "
        "Overlay the package module that imports it as well, so the target can "
        "be resolved in that package's local context."
    )


def _synthetic_package_local_overlay(name: str, importer: ResolvedFile) -> ResolvedFile:
    """Represent an in-memory-only module in its importing package's namespace."""
    graph_name = None
    if importer.is_dependency and importer.package_name is not None:
        graph_name = dependency_private_graph_name(importer.package_name, name)
    return ResolvedFile(
        module_name=name,
        path=(importer.path.parent / f"{name}.geno").resolve(),
        is_dependency=importer.is_dependency,
        package_name=importer.package_name,
        graph_name=graph_name,
    )


def _package_local_overlay_target(
    name: str,
    importer: ResolvedFile,
    candidates: list[ResolvedFile],
    file_map: Mapping[str, ResolvedFile],
) -> ResolvedFile:
    """Resolve an imported overlay using the importing module's lookup scope."""
    if importer.is_dependency and importer.package_name is not None:
        local = [
            candidate
            for candidate in candidates
            if candidate.is_dependency
            and candidate.package_name == importer.package_name
        ]
    else:
        importer_dir = importer.path.parent.resolve()
        local = [
            candidate
            for candidate in candidates
            if not candidate.is_dependency
            and candidate.path.parent.resolve() == importer_dir
        ]

    local = _dedupe_resolved_files(local)
    if len(local) > 1:
        raise _ambiguous_overlay_error(name, local)
    if local:
        return local[0]

    # A package may import another dependency's public entry module. Preserve
    # that public graph binding when no same-package sibling shadows it.
    public_target = file_map.get(name)
    if public_target is not None:
        return public_target

    # The modules API also permits in-memory-only modules. Keep such a module
    # in the importing package's graph namespace rather than binding it to an
    # unrelated dependency-private module with the same stem.
    return _synthetic_package_local_overlay(name, importer)


def _overlay_imports(name: str, source: str) -> list[str]:
    """Parse imports for package-context inference, tolerating bad overlays."""
    from .lexer import LexerError
    from .parser_base import ParseError, ParseErrors

    try:
        _, imports = _parse_module_source(source, Path(f"{name}.geno"))
    except (LexerError, ParseError, ParseErrors):
        return []
    return cast(list[str], imports)


def _resolve_overlay_targets(
    overlay_modules: Mapping[str, str],
    project: ProjectGraph,
    file_map: Mapping[str, ResolvedFile],
    candidate_files: list[ResolvedFile] | None = None,
) -> dict[str, ResolvedFile | None]:
    """Resolve flat overlay names, using overlay importers as package context."""
    candidate_files = candidate_files or _overlay_candidate_files(project, file_map)
    candidates = {
        name: _overlay_candidates(name, candidate_files) for name in overlay_modules
    }
    imports = {
        name: _overlay_imports(name, source) for name, source in overlay_modules.items()
    }
    explicit_graph_keys = {
        name
        for name in overlay_modules
        if name in file_map and file_map[name].graph_name == name
    }
    targets: dict[str, ResolvedFile | None] = {
        name: (
            file_map[name]
            if name in explicit_graph_keys
            else file_map[name]
            if name in file_map
            else matches[0]
            if len(matches) == 1
            else None
        )
        for name, matches in candidates.items()
    }

    # Package context can propagate through a chain of overlaid modules. The
    # global unique-name choice is only provisional: a resolved importer may
    # prove that the overlay belongs to a different package-local sibling.
    max_iterations = max(2, len(overlay_modules) * 4)
    for _ in range(max_iterations):
        hints: dict[str, list[ResolvedFile]] = {name: [] for name in overlay_modules}
        for importer_name, importer in targets.items():
            if importer is None:
                continue
            for imported_name in imports[importer_name]:
                if imported_name not in overlay_modules:
                    continue
                hints[imported_name].append(
                    _package_local_overlay_target(
                        imported_name,
                        importer,
                        candidates[imported_name],
                        file_map,
                    )
                )

        updated = dict(targets)
        for name in overlay_modules:
            if name in explicit_graph_keys:
                continue
            local_hints = _dedupe_resolved_files(hints[name])
            if len(local_hints) == 1:
                updated[name] = local_hints[0]
            elif len(local_hints) > 1:
                updated[name] = None
            elif name in file_map:
                updated[name] = file_map[name]
            elif len(candidates[name]) == 1:
                updated[name] = candidates[name][0]
            else:
                updated[name] = None
        if {
            name: _resolved_file_identity(target) if target is not None else None
            for name, target in updated.items()
        } == {
            name: _resolved_file_identity(target) if target is not None else None
            for name, target in targets.items()
        }:
            targets = updated
            break
        targets = updated

    final_hints: dict[str, list[ResolvedFile]] = {name: [] for name in overlay_modules}
    for importer_name, importer in targets.items():
        if importer is None:
            continue
        for imported_name in imports[importer_name]:
            if imported_name not in overlay_modules:
                continue
            final_hints[imported_name].append(
                _package_local_overlay_target(
                    imported_name,
                    importer,
                    candidates[imported_name],
                    file_map,
                )
            )

    for name in overlay_modules:
        if name in explicit_graph_keys:
            continue
        local_hints = _dedupe_resolved_files(final_hints[name])
        if len(local_hints) > 1:
            raise _ambiguous_overlay_error(name, local_hints)
        if local_hints:
            targets[name] = local_hints[0]
            continue
        if name in file_map:
            targets[name] = file_map[name]
            continue
        if len(candidates[name]) > 1:
            raise _ambiguous_overlay_error(name, candidates[name])
        if candidates[name]:
            targets[name] = candidates[name][0]

    return targets


def _resolved_module_file(resolved: ResolvedModuleSource) -> ResolvedFile:
    """Convert module-resolver metadata into the project-graph representation."""
    return ResolvedFile(
        module_name=resolved.module_name,
        path=resolved.path,
        is_dependency=resolved.is_dependency,
        package_name=resolved.package_name,
        graph_name=resolved.graph_name,
    )


def _discover_overlay_modules(
    overlay_modules: Mapping[str, str],
    targets: Mapping[str, ResolvedFile | None],
    project: ProjectGraph,
    file_map: Mapping[str, ResolvedFile],
    candidate_files: list[ResolvedFile],
) -> tuple[
    dict[str, tuple[ResolvedFile, str]],
    ProjectGraph,
    dict[str, ResolvedFile],
]:
    """Discover only modules reachable through imports introduced by overlays."""
    from .lexer import LexerError
    from .parser_base import ParseError, ParseErrors

    source_overrides = {
        target.path.resolve(): overlay_modules[name]
        for name, target in targets.items()
        if target is not None
    }
    target_sources = {
        target.graph_key: (target, overlay_modules[name])
        for name, target in targets.items()
        if target is not None
    }
    target_graph_keys = set(target_sources)
    baseline_graph_keys = set(file_map)
    canonical_files = _dedupe_resolved_files(
        [
            *candidate_files,
            *(target for target in targets.values() if target is not None),
        ]
    )
    canonical_by_key = {
        resolved_file.graph_key: resolved_file for resolved_file in canonical_files
    }
    canonical_project = ProjectGraph(
        root=project.root,
        entrypoint=project.entrypoint,
        files=canonical_files,
        dependencies=dict(project.dependencies),
    )
    package_modules = _dependency_package_module_maps(canonical_project)
    discovered: dict[str, tuple[ResolvedFile, str]] = {}
    queue = list(target_sources.values())
    processed: set[str] = set()

    def register(resolved_file: ResolvedFile) -> None:
        """Register fallback resolver metadata for later package-local imports."""
        graph_key = resolved_file.graph_key
        if graph_key not in canonical_by_key:
            canonical_files.append(resolved_file)
            canonical_by_key[graph_key] = resolved_file
        if resolved_file.is_dependency and resolved_file.package_name is not None:
            modules = package_modules.setdefault(resolved_file.package_name, {})
            modules[resolved_file.module_name] = graph_key
            modules[resolved_file.path.stem] = graph_key

    def source_for(resolved_file: ResolvedFile) -> str | None:
        override = source_overrides.get(resolved_file.path.resolve())
        if override is not None:
            return override
        try:
            return cast(str, resolved_file.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            return None

    def enqueue(resolved_file: ResolvedFile, source: str) -> None:
        graph_key = resolved_file.graph_key
        if graph_key not in target_graph_keys and graph_key not in baseline_graph_keys:
            discovered[graph_key] = (resolved_file, source)
        if graph_key not in baseline_graph_keys or graph_key in target_graph_keys:
            queue.append((resolved_file, source))

    while queue:
        importer, source = queue.pop()
        graph_key = importer.graph_key
        if graph_key in processed:
            continue
        processed.add(graph_key)
        try:
            _, imports = _parse_module_source(source, importer.path)
        except (LexerError, ParseError, ParseErrors):
            continue

        local_modules = (
            package_modules.get(importer.package_name, {})
            if importer.is_dependency and importer.package_name is not None
            else {}
        )
        for imported_name in cast(list[str], imports):
            imported_key = local_modules.get(imported_name, imported_name)
            imported_file = canonical_by_key.get(imported_key)
            if imported_file is not None:
                imported_source = source_for(imported_file)
                if imported_source is not None:
                    enqueue(imported_file, imported_source)
                continue

            # Canonical project metadata does not include auto-added stdlib or
            # manifestless dependency lookups. Resolve just this import through
            # the filesystem fallback so one unresolved import cannot hide a
            # canonical nested sibling found above.
            try:
                import_program, _ = _parse_module_source(
                    f"import {imported_name}\n", importer.path
                )
                fallback_modules = resolve_module_sources(
                    importer.path,
                    import_program,
                    source_overrides=source_overrides,
                )
            except (
                CircularImportError,
                ModuleResolutionError,
                LexerError,
                ParseError,
                ParseErrors,
                OSError,
                UnicodeError,
            ):
                continue
            for resolved in fallback_modules.values():
                resolved_file = _resolved_module_file(resolved)
                register(resolved_file)
                enqueue(resolved_file, cast(str, resolved.source))

    expanded_project = ProjectGraph(
        root=project.root,
        entrypoint=project.entrypoint,
        files=canonical_files,
        dependencies=dict(project.dependencies),
    )
    return discovered, expanded_project, canonical_by_key


def _overlay_graph_source(
    graph_key: str,
    source: str,
    project: ProjectGraph,
    file_map: Mapping[str, ResolvedFile],
) -> str:
    """Rewrite an overlay's same-package dependency imports to graph keys.

    This mirrors what ``DependencyGraph.resolve`` does to on-disk dependency
    sources, so a name-keyed overlay re-runs exactly like a path-keyed
    override of the same module.
    """
    resolved = file_map.get(graph_key)
    if resolved is None or not resolved.is_dependency or resolved.package_name is None:
        return source
    package_modules = _dependency_package_module_maps(project).get(
        resolved.package_name, {}
    )
    if not package_modules:
        return source

    from .lexer import LexerError
    from .parser_base import ParseError, ParseErrors

    try:
        program, _ = _parse_module_source(source, resolved.path)
    except (LexerError, ParseError, ParseErrors):
        # Downstream surfaces report overlay parse errors with full
        # diagnostics; pass the text through untouched.
        return source
    _, _, rewritten = _rewrite_dependency_imports(program, source, package_modules)
    return cast(str, rewritten)


def _merged_module_inputs(
    graph_module_sources: Mapping[str, str],
    overlay_modules: Mapping[str, str] | None,
    project: ProjectGraph,
    file_map: Mapping[str, ResolvedFile],
) -> MergedModuleInputs:
    """Merge overlays and imports introduced by them with diagnostic metadata."""
    merged = dict(graph_module_sources)
    if not overlay_modules:
        return MergedModuleInputs(merged or None, {}, {})

    candidate_files = _overlay_candidate_files(project, file_map)
    targets = _resolve_overlay_targets(
        overlay_modules,
        project,
        file_map,
        candidate_files,
    )
    discovered, expanded_project, expanded_file_map = _discover_overlay_modules(
        overlay_modules,
        targets,
        project,
        file_map,
        candidate_files,
    )

    for graph_key, (resolved_file, source) in discovered.items():
        if graph_key in merged:
            continue
        merged[graph_key] = _overlay_graph_source(
            resolved_file.graph_key,
            source,
            expanded_project,
            expanded_file_map,
        )

    for name, source in overlay_modules.items():
        target = targets[name]
        graph_key = target.graph_key if target is not None else name
        merged[graph_key] = _overlay_graph_source(
            graph_key,
            source,
            expanded_project,
            expanded_file_map,
        )
    overlay_graph_keys = {
        (target.graph_key if target is not None else name): name
        for name, target in targets.items()
    }
    module_paths = {
        graph_key: str(resolved_file.path)
        for graph_key, (resolved_file, _source) in discovered.items()
    }
    return MergedModuleInputs(merged or None, overlay_graph_keys, module_paths)


def _merged_graph_module_sources(
    graph_module_sources: Mapping[str, str],
    overlay_modules: Mapping[str, str] | None,
    project: ProjectGraph,
    file_map: Mapping[str, ResolvedFile],
) -> dict[str, str] | None:
    """Merge user-keyed overlays into graph-keyed module sources."""
    return _merged_module_inputs(
        graph_module_sources,
        overlay_modules,
        project,
        file_map,
    ).module_sources


def _overlay_graph_keys(
    overlay_modules: Mapping[str, str],
    project: ProjectGraph,
    file_map: Mapping[str, ResolvedFile],
) -> dict[str, str]:
    """Map resolved overlay graph keys back to their user-facing names."""
    targets = _resolve_overlay_targets(overlay_modules, project, file_map)
    return {
        (target.graph_key if target is not None else name): name
        for name, target in targets.items()
    }


@dataclass(frozen=True)
class ResolvedFileContext:
    """Resolved module inputs for file-focused tooling surfaces.

    ``module_sources`` is the user-facing view: keyed by the module names an
    ``import`` statement uses, with the source text as the caller supplied it.
    ``graph_module_sources`` is the re-runnable internal view: keyed by unique
    graph keys with same-package dependency imports rewritten to those keys.
    """

    requested_path: Path
    project: ProjectGraph
    dependency_graph: DependencyGraph
    module_name: str
    module_file: ResolvedFile
    source: str
    module_sources: dict[str, str]
    graph_module_sources: dict[str, str]
    parsed_modules: dict[str, Program]

    @property
    def filename(self) -> str:
        """Filename to use for diagnostics on the resolved file."""
        return str(self.module_file.path)

    @property
    def program(self) -> Program:
        """Parsed AST for the resolved file."""
        return self.dependency_graph.parsed[self.module_name]

    def merged_module_inputs(
        self, overlay_modules: Mapping[str, str] | None = None
    ) -> MergedModuleInputs:
        """Return overlay-merged sources and selective diagnostic metadata.

        The result is keyed by internal graph keys; user-facing overlay names
        are mapped onto graph keys and their same-package dependency imports
        rewritten, so name-keyed overlays re-run exactly like path-keyed
        source overrides.
        """
        if overlay_modules and self.module_name in overlay_modules:
            raise ProjectResolutionError(
                "Explicit module overrides cannot replace the focused "
                f"module '{self.module_name}'"
            )
        return _merged_module_inputs(
            self.graph_module_sources,
            overlay_modules,
            self.project,
            self.dependency_graph.file_map,
        )

    def merged_module_sources(
        self, overlay_modules: Mapping[str, str] | None = None
    ) -> dict[str, str] | None:
        """Return re-runnable module sources with explicit overlays applied."""
        return self.merged_module_inputs(overlay_modules).module_sources

    def overlay_graph_keys(self, overlay_modules: Mapping[str, str]) -> dict[str, str]:
        """Map overlay graph keys back to their user-facing overlay names."""
        return _overlay_graph_keys(
            overlay_modules,
            self.project,
            self.dependency_graph.file_map,
        )


@dataclass(frozen=True)
class ResolvedProjectContext:
    """Resolved project inputs for CLI/API execution surfaces.

    ``module_sources`` is the user-facing view: keyed by the module names an
    ``import`` statement uses, with the source text as the caller supplied it.
    ``graph_module_sources`` is the re-runnable internal view: keyed by unique
    graph keys with same-package dependency imports rewritten to those keys.
    """

    requested_path: Path
    project: ProjectGraph
    dependency_graph: DependencyGraph
    entrypoint: str
    entry_file: ResolvedFile
    source: str
    module_sources: dict[str, str]
    graph_module_sources: dict[str, str]
    parsed_modules: dict[str, Program]

    @property
    def filename(self) -> str:
        """Filename to use for diagnostics on the resolved entrypoint."""
        return str(self.entry_file.path)

    def merged_module_inputs(
        self, overlay_modules: Mapping[str, str] | None = None
    ) -> MergedModuleInputs:
        """Return overlay-merged sources and selective diagnostic metadata.

        Precedence is:
        1. explicit in-memory overlays
        2. local project files
        3. installed dependencies / auto-added stdlib modules

        The result is keyed by internal graph keys; user-facing overlay names
        are mapped onto graph keys and their same-package dependency imports
        rewritten, so name-keyed overlays re-run exactly like path-keyed
        source overrides.
        """
        if overlay_modules and self.entrypoint in overlay_modules:
            raise ProjectResolutionError(
                "Explicit module overrides cannot replace the entrypoint "
                f"module '{self.entrypoint}'"
            )
        return _merged_module_inputs(
            self.graph_module_sources,
            overlay_modules,
            self.project,
            self.dependency_graph.file_map,
        )

    def merged_module_sources(
        self, overlay_modules: Mapping[str, str] | None = None
    ) -> dict[str, str] | None:
        """Return re-runnable module sources with explicit overlays applied."""
        return self.merged_module_inputs(overlay_modules).module_sources

    def overlay_graph_keys(self, overlay_modules: Mapping[str, str]) -> dict[str, str]:
        """Map overlay graph keys back to their user-facing overlay names."""
        return _overlay_graph_keys(
            overlay_modules,
            self.project,
            self.dependency_graph.file_map,
        )


def _single_file_project(
    file_path: Path,
    *,
    module_name: str | None = None,
    root: Path | None = None,
    dependencies: Mapping[str, Path] | None = None,
) -> ProjectGraph:
    """Build a single-file ProjectGraph for a directly requested file."""
    module_name = module_name or file_path.stem
    return ProjectGraph(
        root=root.resolve() if root is not None else None,
        entrypoint=module_name,
        files=[
            ResolvedFile(
                module_name=module_name,
                path=file_path.resolve(),
            )
        ],
        dependencies=dict(dependencies or {}),
    )


def _resolve_dependency_graph(
    project: ProjectGraph,
    source_overrides: Mapping[Path, str] | None = None,
) -> DependencyGraph:
    """Build a dependency graph, optionally overriding module source strings."""
    return DependencyGraph.resolve(project, source_overrides=source_overrides)


def _normalize_source_overrides(
    source_overrides: Mapping[str | Path, str] | None = None,
) -> dict[Path, str]:
    """Normalize in-memory source overrides to resolved filesystem paths."""
    return {
        Path(path).resolve(): source
        for path, source in (source_overrides or {}).items()
    }


def _read_source(path: Path, source_overrides: Mapping[Path, str]) -> str:
    """Return source for *path*, preferring in-memory overrides."""
    return source_overrides.get(path.resolve(), path.read_text(encoding="utf-8"))


def _with_baseline_dependency_modules(
    resolved_imports: dict[str, ResolvedModuleSource],
    focus_file: ResolvedFile,
    focus_program: Program,
    source_overrides: Mapping[Path, str],
) -> dict[str, ResolvedModuleSource]:
    """Keep on-disk dependency modules that a non-focus override redirected away.

    Name-keyed ``modules=`` overlays layer on top of the disk-resolved project
    without shrinking it, so path-keyed overrides must not prune installed
    dependency modules either: walk the imports again with only the focus
    override applied and keep the dependency-package modules the overridden
    walk no longer reaches.
    """
    from .lexer import LexerError
    from .parser_base import ParseError, ParseErrors

    focus_resolved_path = focus_file.path.resolve()
    focus_only = {
        path: source
        for path, source in source_overrides.items()
        if path == focus_resolved_path
    }
    try:
        baseline_imports = resolve_module_sources(
            focus_file.path,
            focus_program,
            source_overrides=focus_only or None,
        )
    except (
        CircularImportError,
        ModuleResolutionError,
        LexerError,
        ParseError,
        ParseErrors,
        OSError,
        UnicodeError,
    ):
        # The on-disk view is only additive context; if it no longer resolves,
        # the overridden view alone remains authoritative.
        return resolved_imports

    override_paths = {resolved.path.resolve() for resolved in resolved_imports.values()}
    merged = dict(resolved_imports)
    for key, resolved in baseline_imports.items():
        if not resolved.is_dependency:
            continue
        if key in merged or resolved.path.resolve() in override_paths:
            continue
        merged[key] = resolved
    return merged


def _maybe_expand_single_file_project(
    requested_path: Path,
    project: ProjectGraph,
    dependency_graph: DependencyGraph,
    focus_module: str,
    *,
    expand_single_file: bool = False,
    source_overrides: Mapping[Path, str] | None = None,
) -> tuple[ProjectGraph, DependencyGraph, str]:
    """Expand a single-file graph with sibling imports resolved from disk."""
    if not (expand_single_file and requested_path.is_file()):
        return project, dependency_graph, focus_module

    focus_file = dependency_graph.file_map.get(focus_module)
    if focus_file is None or focus_module not in dependency_graph.parsed:
        raise ProjectResolutionError(
            f"Module '{focus_module}' not found in resolved project modules"
        )

    try:
        resolved_imports = resolve_module_sources(
            focus_file.path,
            dependency_graph.parsed[focus_module],
            source_overrides=source_overrides,
        )
    except (CircularImportError, ModuleResolutionError) as exc:
        raise ProjectResolutionError(str(exc)) from exc

    focus_resolved_path = focus_file.path.resolve()
    if any(path != focus_resolved_path for path in (source_overrides or {})):
        resolved_imports = _with_baseline_dependency_modules(
            resolved_imports,
            focus_file,
            dependency_graph.parsed[focus_module],
            source_overrides or {},
        )

    if not resolved_imports:
        return project, dependency_graph, focus_module

    existing_files = {rf.path.resolve(): rf for rf in project.files}

    def _existing_file(candidate_path: Path) -> ResolvedFile | None:
        resolved_candidate = candidate_path.resolve()
        matched = existing_files.get(resolved_candidate)
        if matched is not None:
            return matched
        for existing_path, existing_file in existing_files.items():
            try:
                if existing_path.samefile(resolved_candidate):
                    return existing_file
            except OSError:
                continue
        return None

    synthetic_files = [_existing_file(focus_file.path) or focus_file]
    seen_paths = {focus_file.path.resolve()}
    for resolved in resolved_imports.values():
        resolved_path = resolved.path.resolve()
        if resolved_path in seen_paths:
            continue
        synthetic_files.append(
            _existing_file(resolved_path)
            or ResolvedFile(
                module_name=resolved.module_name,
                path=resolved_path,
                is_dependency=resolved.is_dependency,
                package_name=resolved.package_name,
                graph_name=resolved.graph_name,
            )
        )
        seen_paths.add(resolved_path)

    project = ProjectGraph(
        root=project.root
        if project.root is not None
        else requested_path.parent.resolve(),
        entrypoint=project.entrypoint or focus_module,
        files=synthetic_files,
        dependencies=dict(project.dependencies),
    )
    dependency_graph = _resolve_dependency_graph(
        project,
        source_overrides,
    )
    return project, dependency_graph, focus_module


def _read_file_context(
    requested_path: Path,
    project: ProjectGraph,
    dependency_graph: DependencyGraph,
    module_name: str,
    source_overrides: Mapping[Path, str] | None = None,
) -> ResolvedFileContext:
    """Build a ResolvedFileContext for a module in a resolved graph."""
    module_file = dependency_graph.file_map.get(module_name)
    if module_file is None or module_name not in dependency_graph.parsed:
        raise ProjectResolutionError(
            f"Module '{module_name}' not found in resolved project modules"
        )

    overrides = dict(source_overrides or {})
    source = dependency_graph.normalized_sources.get(
        module_name,
        _read_source(module_file.path, overrides),
    )
    graph_module_sources = {
        name: dependency_graph.normalized_sources.get(
            name,
            _read_source(dependency_graph.file_map[name].path, overrides),
        )
        for name in dependency_graph.sorted_modules
        if name != module_name and name in dependency_graph.file_map
    }
    display_names = _module_display_names(dependency_graph.file_map)
    module_sources = {
        display_names.get(name, name): dependency_graph.original_sources.get(
            name, graph_source
        )
        for name, graph_source in graph_module_sources.items()
    }
    parsed_modules = {
        name: dependency_graph.parsed[name]
        for name in dependency_graph.sorted_modules
        if name != module_name and name in dependency_graph.parsed
    }
    return ResolvedFileContext(
        requested_path=requested_path.resolve(),
        project=project,
        dependency_graph=dependency_graph,
        module_name=module_name,
        module_file=module_file,
        source=source,
        module_sources=module_sources,
        graph_module_sources=graph_module_sources,
        parsed_modules=parsed_modules,
    )


def _resolve_requested_file_project(
    requested_path: Path, project: ProjectGraph
) -> tuple[ProjectGraph, str | None, bool]:
    """Choose the effective project/module for an explicitly requested file path."""
    if not requested_path.is_file():
        return project, None, False

    if not project.files:
        return _single_file_project(requested_path), requested_path.stem, True

    requested_resolved = requested_path.resolve()
    matched = next(
        (rf for rf in project.files if rf.path.resolve() == requested_resolved),
        None,
    )
    if matched is None:
        return (
            _single_file_project(
                requested_path,
                root=project.root,
                dependencies=project.dependencies,
            ),
            requested_path.stem,
            True,
        )
    return (
        _single_file_project(
            matched.path,
            module_name=matched.module_name,
            root=project.root,
            dependencies=project.dependencies,
        ),
        matched.module_name,
        True,
    )


def resolve_file_context(
    start_path: str | Path,
    source_override: str | None = None,
    source_overrides: Mapping[str | Path, str] | None = None,
) -> ResolvedFileContext:
    """Resolve a file without observing a partial dependency publication."""
    from .package_manager import _package_transaction_locks

    requested_path = Path(start_path)
    with _package_transaction_locks(requested_path):
        if not requested_path.exists():
            raise _missing_path_error(start_path)
        if not requested_path.is_file():
            raise ProjectResolutionError(f"Expected a file path, got {requested_path}")
        return _resolve_file_context_unlocked(
            requested_path,
            source_override=source_override,
            source_overrides=source_overrides,
        )


def _resolve_file_context_unlocked(
    requested_path: Path,
    source_override: str | None = None,
    source_overrides: Mapping[str | Path, str] | None = None,
) -> ResolvedFileContext:
    """Resolve a file while its package transaction lock is already held."""
    project = ProjectGraph.discover(requested_path)
    project, module_name, expand_single_file = _resolve_requested_file_project(
        requested_path, project
    )
    if module_name is None:
        raise ProjectResolutionError(f"Expected a file path, got {requested_path}")

    normalized_overrides = _normalize_source_overrides(source_overrides)
    if source_override is not None:
        normalized_overrides[requested_path.resolve()] = source_override
    dependency_graph = _resolve_dependency_graph(
        project,
        normalized_overrides or None,
    )
    project, dependency_graph, module_name = _maybe_expand_single_file_project(
        requested_path,
        project,
        dependency_graph,
        module_name,
        expand_single_file=expand_single_file,
        source_overrides=normalized_overrides or None,
    )
    return _read_file_context(
        requested_path,
        project,
        dependency_graph,
        module_name,
        normalized_overrides or None,
    )


def resolve_project_context(
    start_path: str | Path,
    source_override: str | None = None,
    source_overrides: Mapping[str | Path, str] | None = None,
) -> ResolvedProjectContext:
    """Resolve a project without observing a partial dependency publication."""
    from .package_manager import _package_transaction_locks

    requested_path = Path(start_path)
    with _package_transaction_locks(requested_path):
        if not requested_path.exists():
            raise _missing_path_error(start_path)
        return _resolve_project_context_unlocked(
            requested_path,
            source_override=source_override,
            source_overrides=source_overrides,
        )


def _resolve_project_context_unlocked(
    requested_path: Path,
    source_override: str | None = None,
    source_overrides: Mapping[str | Path, str] | None = None,
) -> ResolvedProjectContext:
    """Resolve a project while its package transaction lock is already held."""

    project = ProjectGraph.discover(requested_path)
    requested_module_name: str | None = None
    expand_single_file = False
    if requested_path.is_file():
        project, requested_module_name, expand_single_file = (
            _resolve_requested_file_project(
                requested_path,
                project,
            )
        )

    if not project.files:
        raise ProjectResolutionError(f"No .geno files found at {requested_path}")

    normalized_overrides = _normalize_source_overrides(source_overrides)
    if source_override is not None:
        if not requested_path.is_file():
            raise ProjectResolutionError(
                "source_override is only supported when resolving a file path"
            )
        normalized_overrides[requested_path.resolve()] = source_override

    dependency_graph = _resolve_dependency_graph(
        project,
        normalized_overrides or None,
    )
    if requested_module_name is not None:
        entrypoint = requested_module_name
    elif not project.entrypoint and not dependency_graph.sorted_modules:
        raise ProjectResolutionError("No modules found in project")
    else:
        entrypoint = project.entrypoint or dependency_graph.sorted_modules[-1]
    project, dependency_graph, entrypoint = _maybe_expand_single_file_project(
        requested_path,
        project,
        dependency_graph,
        entrypoint,
        expand_single_file=expand_single_file,
        source_overrides=normalized_overrides or None,
    )
    file_context = _read_file_context(
        requested_path,
        project,
        dependency_graph,
        entrypoint,
        normalized_overrides or None,
    )

    return ResolvedProjectContext(
        requested_path=file_context.requested_path,
        project=file_context.project,
        dependency_graph=file_context.dependency_graph,
        entrypoint=file_context.module_name,
        entry_file=file_context.module_file,
        source=file_context.source,
        module_sources=file_context.module_sources,
        graph_module_sources=file_context.graph_module_sources,
        parsed_modules=file_context.parsed_modules,
    )
