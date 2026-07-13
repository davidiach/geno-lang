"""
DependencyGraph — import graph resolution and validation
=========================================================

Layer 2 of the project resolution pipeline (builds on ProjectGraph):
1. Parse each discovered file to extract ``import`` statements
2. Build a directed graph of module dependencies
3. Validate: detect circular imports and name collisions
4. Produce a topologically sorted module list for compilation
"""

from __future__ import annotations

import hashlib
import heapq
import logging
import os
import pickle
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Set, Tuple

from . import ast_nodes as _ast_nodes
from . import tokens as _tokens
from . import types as _types
from .ast_nodes import ImportStatement, Program
from .lexer import Lexer
from .module_resolver import _STD_DIR
from .parser import Parser
from .project_graph import ProjectGraph, ResolvedFile

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

_PARSE_CACHE_VERSION = 4  # bumped: cache entries include source content hash
_CACHE_ROOT_ENV = "GENO_CACHE_DIR"
_DISABLE_CACHE_ENV = "GENO_DISABLE_DEP_GRAPH_CACHE"
_CACHE_PICKLE_PROTOCOL = 3


def _module_defined_classes(module: object) -> set[tuple[str, str]]:
    """Return class globals defined directly in ``module``."""
    module_name = getattr(module, "__name__", None)
    if module_name is None:
        return set()
    return {
        (module_name, name)
        for name, obj in vars(module).items()
        if isinstance(obj, type) and getattr(obj, "__module__", None) == module_name
    }


_SAFE_UNPICKLE_GLOBALS = frozenset(
    {
        ("geno.dependency_graph", "_ParsedModuleCacheEntry"),
    }
    | _module_defined_classes(_ast_nodes)
    | _module_defined_classes(_tokens)
    | _module_defined_classes(_types)
)


class _RestrictedUnpickler(pickle.Unpickler):
    """Unpickler that only allows known-safe project data classes."""

    def find_class(self, module: str, name: str):  # type: ignore[override]
        if (module, name) not in _SAFE_UNPICKLE_GLOBALS:
            raise pickle.UnpicklingError(f"Blocked unpickle of {module}.{name}")
        return super().find_class(module, name)


def _safe_loads(data: bytes):
    """Deserialize bytes with class restrictions."""
    import io as _io

    return _RestrictedUnpickler(_io.BytesIO(data)).load()


def _safe_load(handle):
    """Deserialize from a file handle with class restrictions."""
    return _RestrictedUnpickler(handle).load()


def _compiler_fingerprint() -> str:
    """Hash the parser and AST source files to detect compiler changes."""
    h = hashlib.sha256()
    h.update(str(_PARSE_CACHE_VERSION).encode())
    pkg_dir = Path(__file__).resolve().parent
    for name in (
        "ast_nodes.py",
        "dependency_graph.py",
        "lexer.py",
        "parser.py",
        "parser_base.py",
        "parser_expressions.py",
        "parser_patterns.py",
        "parser_statements.py",
        "parser_types.py",
        "tokens.py",
    ):
        src = pkg_dir / name
        if src.exists():
            h.update(src.read_bytes())
    return h.hexdigest()


_COMPILER_FINGERPRINT: str | None = None
_PARSED_MODULE_CACHES: Dict[str, _ParsedModuleCache] = {}


def _get_compiler_fingerprint() -> str:
    """Return the cached compiler fingerprint (computed once per process)."""
    global _COMPILER_FINGERPRINT
    if _COMPILER_FINGERPRINT is None:
        _COMPILER_FINGERPRINT = _compiler_fingerprint()
    return _COMPILER_FINGERPRINT


def _source_digest(data: bytes) -> str:
    """Return a stable digest for source content cache validation."""
    return hashlib.sha256(data).hexdigest()


class DependencyGraphError(Exception):
    """Error raised during dependency graph resolution."""


class CircularDependencyError(DependencyGraphError):
    """Raised when a circular import is detected."""

    def __init__(self, cycle: List[str]):
        self.cycle = cycle
        cycle_str = " -> ".join(cycle)
        super().__init__(f"Circular import detected: {cycle_str}")


class NameCollisionError(DependencyGraphError):
    """Raised when two files define the same module name."""

    def __init__(self, name: str, path_a: Path, path_b: Path):
        self.name = name
        self.path_a = path_a
        self.path_b = path_b
        super().__init__(
            f"Module name collision: '{name}' defined by both {path_a} and {path_b}"
        )


@dataclass
class _ParsedModuleCacheEntry:
    """Serialized parse result for a single Geno source file."""

    cache_version: int
    compiler_fingerprint: str
    source_path: str
    mtime_ns: int
    size: int
    source_hash: str
    program_payload: bytes
    imports: List[str]


class _ParsedModuleCache:
    """Persistent parse cache keyed by file path + metadata."""

    def __init__(self, cache_dir: Path | None):
        self.cache_dir = cache_dir
        self._memory: Dict[str, Tuple[str, int, int, str, bytes, List[str]]] = {}
        # Emit the first on-disk write failure loudly (a read-only/full/wrong-
        # ownership cache dir otherwise silently re-parses every module on every
        # invocation with no signal); throttle the rest to DEBUG.
        self._warned_store_failure = False
        if self.cache_dir is not None:
            try:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning(
                    "Disabling on-disk parse cache: cannot create %s: %s",
                    self.cache_dir,
                    exc,
                )
                self.cache_dir = None

    def load_or_parse(self, path: Path) -> Tuple[Program, List[str]]:
        """Load a cached parse result when valid, otherwise parse from source."""
        resolved = path.resolve()
        stat = resolved.stat()
        source_hash = _source_digest(resolved.read_bytes())
        compiler_fingerprint = _get_compiler_fingerprint()
        key = str(resolved)
        memory_entry = self._memory.get(key)
        if memory_entry is not None:
            (
                cached_fingerprint,
                cached_mtime_ns,
                cached_size,
                cached_source_hash,
                cached_program_payload,
                cached_imports,
            ) = memory_entry
            if (
                cached_fingerprint == compiler_fingerprint
                and cached_mtime_ns == stat.st_mtime_ns
                and cached_size == stat.st_size
                and cached_source_hash == source_hash
            ):
                cached_result = self._deserialize_result(
                    cached_program_payload, cached_imports
                )
                if cached_result is not None:
                    return cached_result
                self._memory.pop(key, None)

        disk_cached = self._load_from_disk(
            resolved, stat.st_mtime_ns, stat.st_size, source_hash
        )
        if disk_cached is not None:
            cached_program_payload, cached_imports = disk_cached
            cached_result = self._deserialize_result(
                cached_program_payload, cached_imports
            )
            if cached_result is not None:
                self._memory[key] = (
                    compiler_fingerprint,
                    stat.st_mtime_ns,
                    stat.st_size,
                    source_hash,
                    cached_program_payload,
                    cached_imports,
                )
                return cached_result

        program, imports = _parse_module(resolved)
        imports_list = list(imports)
        # Serializing the AST can exceed Python's recursion limit for a valid
        # but very deeply-nested program (e.g. a long left-associative operator
        # chain: `1 + 1 + ... + 1`). The parse already succeeded, so degrade to
        # no-cache for this module rather than crashing the whole command with a
        # raw RecursionError (H-08).
        try:
            program_payload = self._serialize_program(program)
        except (RecursionError, pickle.PickleError) as exc:
            logger.debug(
                "Parse cache: not caching %s (AST not serializable): %s",
                resolved,
                exc,
            )
            return program, imports_list
        self._memory[key] = (
            compiler_fingerprint,
            stat.st_mtime_ns,
            stat.st_size,
            source_hash,
            program_payload,
            imports_list,
        )
        self._store_to_disk(
            resolved,
            stat.st_mtime_ns,
            stat.st_size,
            source_hash,
            program_payload,
            imports_list,
        )
        return program, imports_list

    @staticmethod
    def _serialize_program(program: Program) -> bytes:
        """Serialize a parsed program once for cache reuse."""
        return pickle.dumps(program, protocol=_CACHE_PICKLE_PROTOCOL)

    @staticmethod
    def _deserialize_result(
        program_payload: bytes, imports: List[str]
    ) -> Tuple[Program, List[str]] | None:
        """Return a fresh parse result, or None when the serialized payload is invalid."""
        try:
            program = _safe_loads(program_payload)
        except (
            EOFError,
            ImportError,
            pickle.PickleError,
            AttributeError,
            TypeError,
            ValueError,
        ):
            return None
        return program, list(imports)

    def _cache_path_for(self, source_path: Path) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.pickle"

    def _load_from_disk(
        self, source_path: Path, mtime_ns: int, size: int, source_hash: str
    ) -> Tuple[bytes, List[str]] | None:
        cache_path = self._cache_path_for(source_path)
        if cache_path is None or not cache_path.exists():
            return None

        try:
            with cache_path.open("rb") as handle:
                entry = _safe_load(handle)
        except (
            OSError,
            EOFError,
            ImportError,
            pickle.PickleError,
            AttributeError,
            TypeError,
            ValueError,
        ) as exc:
            # A corrupt/unreadable entry is a legitimate cache miss; record it
            # at DEBUG so a persistently broken cache dir is diagnosable.
            logger.debug(
                "Ignoring unreadable parse-cache entry %s: %s", cache_path, exc
            )
            return None

        if not isinstance(entry, _ParsedModuleCacheEntry):
            return None
        if entry.cache_version != _PARSE_CACHE_VERSION:
            return None
        if getattr(entry, "compiler_fingerprint", None) != _get_compiler_fingerprint():
            return None
        if entry.source_path != str(source_path):
            return None
        if entry.mtime_ns != mtime_ns or entry.size != size:
            return None
        if getattr(entry, "source_hash", None) != source_hash:
            return None
        if not isinstance(entry.program_payload, bytes):
            return None

        return entry.program_payload, list(entry.imports)

    def _store_to_disk(
        self,
        source_path: Path,
        mtime_ns: int,
        size: int,
        source_hash: str,
        program_payload: bytes,
        imports: List[str],
    ) -> None:
        cache_path = self._cache_path_for(source_path)
        if cache_path is None:
            return

        entry = _ParsedModuleCacheEntry(
            cache_version=_PARSE_CACHE_VERSION,
            compiler_fingerprint=_get_compiler_fingerprint(),
            source_path=str(source_path),
            mtime_ns=mtime_ns,
            size=size,
            source_hash=source_hash,
            program_payload=program_payload,
            imports=list(imports),
        )
        temp_path = cache_path.with_suffix(".tmp")

        try:
            with temp_path.open("wb") as handle:
                pickle.dump(
                    entry,
                    handle,
                    protocol=_CACHE_PICKLE_PROTOCOL,
                )
            temp_path.replace(cache_path)
        except (OSError, pickle.PickleError, AttributeError, TypeError) as exc:
            # Never let a cache-write failure break the user's command, but do
            # not fail silently either: surface the first failure per process at
            # WARNING (throttle the rest to DEBUG). A pickling TypeError/
            # AttributeError signals a real serialization regression rather than
            # an environmental problem, so log those at ERROR.
            if isinstance(exc, (TypeError, AttributeError)):
                logger.error(
                    "Parse cache: failed to serialize %s (likely a bug): %s",
                    source_path,
                    exc,
                    exc_info=True,
                )
            elif not self._warned_store_failure:
                self._warned_store_failure = True
                logger.warning(
                    "Parse cache: failed to write %s: %s "
                    "(further failures logged at DEBUG)",
                    cache_path,
                    exc,
                )
            else:
                logger.debug("Parse cache: failed to write %s: %s", cache_path, exc)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


@dataclass
class DependencyGraph:
    """Resolved dependency graph with topological ordering."""

    project: ProjectGraph
    # module_name -> list of imported module names
    edges: Dict[str, List[str]] = field(default_factory=dict)
    # module_name -> parsed AST program
    parsed: Dict[str, Program] = field(default_factory=dict)
    # module_name -> ResolvedFile
    file_map: Dict[str, ResolvedFile] = field(default_factory=dict)
    # module_name -> normalized source text used by reparsing surfaces
    normalized_sources: Dict[str, str] = field(default_factory=dict)
    # module_name -> pre-rewrite source text as the caller supplied it
    original_sources: Dict[str, str] = field(default_factory=dict)
    # Topologically sorted module names (dependencies first)
    sorted_modules: List[str] = field(default_factory=list)

    @classmethod
    def resolve(
        cls,
        project: ProjectGraph,
        source_overrides: Mapping[Path, str] | None = None,
    ) -> DependencyGraph:
        """Build and validate the dependency graph from a ProjectGraph.

        Parses all files, extracts imports, detects cycles and name
        collisions, and returns a topologically sorted module order.
        """
        graph = cls(project=project)
        parse_cache = _get_parsed_module_cache()
        overrides = {
            Path(path).resolve(): source
            for path, source in (source_overrides or {}).items()
        }
        dependency_package_modules = _dependency_package_module_maps(project)

        # Step 1: Build file_map and check for name collisions
        for resolved_file in project.files:
            name = resolved_file.graph_key
            if name in graph.file_map:
                raise NameCollisionError(
                    name, graph.file_map[name].path, resolved_file.path
                )
            graph.file_map[name] = resolved_file

        # Step 2: Parse each file and extract imports
        for name, resolved_file in graph.file_map.items():
            resolved_path = resolved_file.path.resolve()
            override_source = overrides.get(resolved_path)
            if override_source is None:
                program, imports = parse_cache.load_or_parse(resolved_file.path)
                source_text = resolved_file.path.read_text(encoding="utf-8")
            else:
                source_text = override_source
                program, imports = _parse_module_source(
                    override_source,
                    resolved_file.path,
                )
            graph.original_sources[name] = source_text
            if resolved_file.is_dependency and resolved_file.package_name is not None:
                program, imports, source_text = _rewrite_dependency_imports(
                    program,
                    source_text,
                    dependency_package_modules.get(resolved_file.package_name, {}),
                )
            else:
                imports = [
                    defn.module_name
                    for defn in program.definitions
                    if isinstance(defn, ImportStatement)
                ]
            graph.parsed[name] = program
            graph.edges[name] = imports
            graph.normalized_sources[name] = source_text

        # Step 2b: Auto-resolve standard library imports
        _resolve_std_imports(graph, parse_cache, overrides)

        # Step 3: Detect circular imports via DFS
        _detect_cycles(graph.edges)

        # Step 4: Topological sort (dependencies first)
        graph.sorted_modules = _topological_sort(graph.edges)

        return graph


def _dependency_package_module_maps(
    project: ProjectGraph,
) -> Dict[str, Dict[str, str]]:
    """Map package-local import names to unique graph keys per dependency."""
    package_modules: Dict[str, Dict[str, str]] = {}
    for resolved_file in project.files:
        if not resolved_file.is_dependency or resolved_file.package_name is None:
            continue
        modules = package_modules.setdefault(resolved_file.package_name, {})
        modules[resolved_file.module_name] = resolved_file.graph_key
        modules[resolved_file.path.stem] = resolved_file.graph_key
    return package_modules


def _rewrite_dependency_import_line(line: str, original: str, rewritten: str) -> str:
    """Rewrite one dependency-local import line to its graph key."""
    updated, count = re.subn(
        rf"(\bimport\s+){re.escape(original)}\b",
        rf"\1{rewritten}",
        line,
        count=1,
    )
    if count:
        return updated
    return line.replace(f"import {original}", f"import {rewritten}", 1)


def _rewrite_dependency_imports(
    program: Program,
    source_text: str,
    package_modules: Mapping[str, str],
) -> Tuple[Program, List[str], str]:
    """Rewrite same-package dependency imports to unique graph keys."""
    lines = source_text.splitlines(keepends=True)
    rewritten_source = False
    imports: List[str] = []

    for defn in program.definitions:
        if not isinstance(defn, ImportStatement):
            continue
        rewritten_name = package_modules.get(defn.module_name, defn.module_name)
        if rewritten_name != defn.module_name:
            line_index = defn.location.line - 1
            if 0 <= line_index < len(lines):
                lines[line_index] = _rewrite_dependency_import_line(
                    lines[line_index],
                    defn.module_name,
                    rewritten_name,
                )
                rewritten_source = True
            defn.module_name = rewritten_name
        imports.append(defn.module_name)

    if rewritten_source:
        return program, imports, "".join(lines)
    return program, imports, source_text


def _resolve_std_imports(
    graph: DependencyGraph,
    parse_cache: _ParsedModuleCache,
    source_overrides: Mapping[Path, str] | None = None,
) -> None:
    """Auto-add standard library modules referenced by imports."""
    overrides = {
        Path(path).resolve(): source
        for path, source in (source_overrides or {}).items()
    }
    std_needed: Set[str] = set()
    for imports in graph.edges.values():
        for imp in imports:
            if imp not in graph.file_map:
                std_path = _STD_DIR / f"{imp}.geno"
                if std_path.exists():
                    std_needed.add(imp)

    for name in std_needed:
        std_path = _STD_DIR / f"{name}.geno"
        resolved = ResolvedFile(module_name=name, path=std_path)
        graph.file_map[name] = resolved

        override_source = overrides.get(std_path.resolve())
        if override_source is None:
            source_text = std_path.read_text(encoding="utf-8")
            program, mod_imports = parse_cache.load_or_parse(std_path)
        else:
            source_text = override_source
            program, mod_imports = _parse_module_source(override_source, std_path)
        graph.parsed[name] = program
        graph.edges[name] = mod_imports
        graph.normalized_sources[name] = source_text
        graph.original_sources[name] = source_text


def _parse_module(path: Path) -> Tuple[Program, List[str]]:
    """Parse a single Geno module and extract its direct imports."""
    source = path.read_text(encoding="utf-8")
    return _parse_module_source(source, path)


def _parse_module_source(source: str, path: Path) -> Tuple[Program, List[str]]:
    """Parse Geno source text and extract its direct imports."""
    tokens = Lexer(source, str(path)).tokenize()
    program = Parser(tokens).parse_program()
    return program, _extract_imports(program)


def _extract_imports(program: Program) -> List[str]:
    """Collect import names from a parsed program."""
    imports: List[str] = []
    for defn in program.definitions:
        if isinstance(defn, ImportStatement):
            imports.append(defn.module_name)
    return imports


def _dependency_cache_dir() -> Path | None:
    """Return the on-disk dependency-graph cache directory, if enabled."""
    if os.environ.get(_DISABLE_CACHE_ENV) == "1":
        return None

    override = os.environ.get(_CACHE_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve() / (
            f"dependency-graph-v{_PARSE_CACHE_VERSION}"
        )

    return _default_cache_root() / f"dependency-graph-v{_PARSE_CACHE_VERSION}"


def _get_parsed_module_cache() -> _ParsedModuleCache:
    """Return the process-local parsed-module cache for the active cache dir."""
    cache_dir = _dependency_cache_dir()
    if cache_dir is None:
        return _ParsedModuleCache(None)

    key = str(cache_dir)
    cache = _PARSED_MODULE_CACHES.get(key)
    if cache is None:
        cache = _ParsedModuleCache(cache_dir)
        _PARSED_MODULE_CACHES[key] = cache
    return cache


def _default_cache_root() -> Path:
    """Choose a platform-appropriate user cache directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "geno"

    local_app_data = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local_app_data:
        return Path(local_app_data) / "geno"

    return Path.home() / ".cache" / "geno"


def _detect_cycles(edges: Dict[str, List[str]]) -> None:
    """Detect circular imports using iterative DFS.

    Only considers edges where both endpoints are known modules
    (external/unresolved imports are ignored for cycle detection).
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = dict.fromkeys(edges, WHITE)
    parent: Dict[str, str | None] = dict.fromkeys(edges)

    for start in edges:
        if color[start] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, idx = stack.pop()
            if idx == 0:
                color[node] = GRAY

            neighbors = [n for n in edges.get(node, []) if n in edges]
            if idx < len(neighbors):
                stack.append((node, idx + 1))
                neighbor = neighbors[idx]
                if color[neighbor] == GRAY:
                    # Reconstruct the cycle
                    cycle = [neighbor]
                    for frame_node, _ in reversed(stack):
                        cycle.append(frame_node)
                        if frame_node == neighbor:
                            break
                    cycle.reverse()
                    raise CircularDependencyError(cycle)
                if color[neighbor] == WHITE:
                    parent[neighbor] = node
                    stack.append((neighbor, 0))
            else:
                color[node] = BLACK


def _topological_sort(edges: Dict[str, List[str]]) -> List[str]:
    """Return modules in topological order (dependencies first).

    If A imports B, B appears before A in the output.
    Ignores edges to modules not in the graph (external imports).
    """
    # Build reverse adjacency: for each module, who depends on it?
    dependents: Dict[str, List[str]] = {name: [] for name in edges}
    # in_degree[X] = how many modules X depends on (within the graph)
    in_degree: Dict[str, int] = dict.fromkeys(edges, 0)
    for name, imports in edges.items():
        for imp in imports:
            if imp in dependents:
                dependents[imp].append(name)
                in_degree[name] += 1

    # Start with modules that have no in-graph dependencies
    queue: list[str] = sorted([name for name, deg in in_degree.items() if deg == 0])
    heapq.heapify(queue)
    result: List[str] = []

    while queue:
        node = heapq.heappop(queue)
        result.append(node)
        for dependent in dependents.get(node, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                heapq.heappush(queue, dependent)

    return result
