"""
Geno Language Server (LSP)
==========================

Provides IDE features via the Language Server Protocol:
- Diagnostics (on open / change / save)
- Hover (type information)
- Go-to-definition
- Completion (builtins, keywords, user-defined names)
- Signature help (parameter info on function calls)

Requires ``pygls`` (optional dependency: ``pip install geno-lang[lsp]``).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterable,
    Mapping,
    MutableMapping,
    Union,
    cast,
)
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from geno.symbol_table import SymbolDef, SymbolTable

try:
    from lsprotocol import types
    from pygls.server import LanguageServer

    HAS_PYGLS = True
except ImportError:
    HAS_PYGLS = False

import geno
from geno.lsp_cache import (
    BoundedDict as _BoundedDict,
)
from geno.lsp_cache import (
    project_view_cache_key as _build_project_view_cache_key,
)
from geno.lsp_cache import (
    project_view_keys_for_path as _project_view_keys_for_path,
)
from geno.lsp_cache import (
    symbol_table_keys_for_path as _symbol_table_keys_for_path,
)
from geno.lsp_completions import (
    CompletionSymbol as _CompletionSymbol,
)
from geno.lsp_completions import (
    extract_completion_symbols as _extract_completion_symbols,
)
from geno.lsp_completions import (
    extract_names as _extract_names,
)
from geno.lsp_completions import (
    extract_type_defs as _extract_type_defs,
)
from geno.lsp_completions import (
    get_builtin_names as _get_builtin_names,
)
from geno.lsp_diagnostics import (
    error_diagnostic as _error_diagnostic,
)
from geno.lsp_diagnostics import (
    to_lsp_diagnostic as _to_lsp_diagnostic,
)
from geno.tokens import KEYWORDS, SourceLocation

_logger = logging.getLogger(__name__)
_IS_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class _ParameterInformationFallback:
    label: str


def _parameter_information(label: str) -> Any:
    if HAS_PYGLS:
        return types.ParameterInformation(label=label)
    return _ParameterInformationFallback(label=label)


def _diagnostics_by_uri_from_exception(
    exc: Exception,
    default_uri: str,
) -> dict[str, list[Any]]:
    """Convert a single or aggregate Geno exception into LSP diagnostics."""
    nested_errors = getattr(exc, "errors", None)
    errors: Iterable[Any]
    if isinstance(nested_errors, list):
        errors = nested_errors
    else:
        errors = [exc]

    grouped: dict[str, list[Any]] = {}
    for error in errors:
        err_line, err_col, err_uri = 0, 0, default_uri
        loc = getattr(error, "location", None)
        if loc is not None:
            err_line = max(loc.line - 1, 0)
            err_col = max(loc.column - 1, 0)
            if hasattr(loc, "filename") and loc.filename != "<unknown>":
                err_path = Path(loc.filename)
                if err_path.exists():
                    err_uri = err_path.as_uri()

        diag = _error_diagnostic(
            getattr(error, "message", str(error)),
            line=err_line,
            character=err_col,
        )
        grouped.setdefault(err_uri, []).append(diag)
    return grouped


# ---------------------------------------------------------------------------
# Keyword and builtin lists for completion
# ---------------------------------------------------------------------------

_KEYWORDS = list(KEYWORDS.keys())


def _symbol_kind_for_lsp(kind: str) -> types.SymbolKind:
    """Map Geno symbol kinds to LSP symbol kinds."""
    mapping = {
        "function": types.SymbolKind.Function,
        "type": types.SymbolKind.Class,
        "trait": types.SymbolKind.Interface,
        "constructor": types.SymbolKind.EnumMember,
        "module": types.SymbolKind.Module,
    }
    return mapping.get(kind, types.SymbolKind.Variable)


def _completion_kind_for_symbol(kind: str) -> types.CompletionItemKind:
    """Map Geno symbol kinds to LSP completion kinds."""
    mapping = {
        "function": types.CompletionItemKind.Function,
        "type": types.CompletionItemKind.Class,
        "trait": types.CompletionItemKind.Interface,
        "constructor": types.CompletionItemKind.EnumMember,
        "module": types.CompletionItemKind.Module,
    }
    return mapping.get(kind, types.CompletionItemKind.Variable)


def _top_level_symbol_defs(table: SymbolTable, filename: str) -> list[SymbolDef]:
    """Return top-level, document-owned symbol definitions for LSP symbol surfaces."""
    seen: set[tuple[str, int, int, str]] = set()
    defs: list[SymbolDef] = []
    for defn in table.definitions:
        if defn.location.filename != filename:
            continue
        if defn.kind not in {"function", "type", "constructor", "trait"}:
            continue
        key = (defn.name, defn.location.line, defn.location.column, defn.kind)
        if key in seen:
            continue
        seen.add(key)
        defs.append(defn)
    return defs


def _symbol_range_from_source(
    source: str,
    name: str,
    line_1_based: int,
    fallback_column_1_based: int,
) -> types.Range:
    """Return a best-effort LSP range for a symbol name on one source line."""
    line_index = max(line_1_based - 1, 0)
    lines = source.splitlines()
    if line_index >= len(lines):
        start_char = max(fallback_column_1_based - 1, 0)
        return types.Range(
            start=types.Position(line=line_index, character=start_char),
            end=types.Position(line=line_index, character=start_char + len(name)),
        )

    line_text = lines[line_index]
    fallback_start = max(fallback_column_1_based - 1, 0)
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    match = pattern.search(line_text, pos=min(fallback_start, len(line_text)))
    if match is None:
        match = pattern.search(line_text)
    if match is not None:
        return types.Range(
            start=types.Position(line=line_index, character=match.start()),
            end=types.Position(line=line_index, character=match.end()),
        )

    return types.Range(
        start=types.Position(line=line_index, character=fallback_start),
        end=types.Position(line=line_index, character=fallback_start + len(name)),
    )


def _line_at(source: str, line_1_based: int) -> str | None:
    """Return one source line using 1-based indexing."""
    lines = source.splitlines()
    index = line_1_based - 1
    if 0 <= index < len(lines):
        return lines[index]
    return None


def _extract_user_type_decl(name: str, source: str) -> str | None:
    """Extract a top-level Geno type declaration line."""
    pattern = rf"^\s*(?:export\s+)?type\s+{re.escape(name)}\b.*$"
    match = re.search(pattern, source, re.MULTILINE)
    if match is None:
        return None
    return match.group(0).strip()


def _extract_user_trait_decl(name: str, source: str) -> str | None:
    """Extract a top-level Geno trait declaration line."""
    pattern = rf"^\s*trait\s+{re.escape(name)}\b.*$"
    match = re.search(pattern, source, re.MULTILINE)
    if match is None:
        return None
    return match.group(0).strip()


def _find_constructor_parent_type(name: str, source: str) -> str | None:
    """Find the parent type name for a constructor variant."""
    from geno.ast_nodes import TypeDef
    from geno.lexer import Lexer
    from geno.parser import Parser
    from geno.parser_base import ParseError as _ParseError
    from geno.parser_base import ParseErrors as _ParseErrors

    definitions = None
    try:
        tokens = Lexer(source, "<hover>").tokenize()
        program = Parser(tokens).parse_program()
        definitions = program.definitions
    except (_ParseError, _ParseErrors) as e:
        partial = getattr(e, "partial_program", None)
        if partial is not None and hasattr(partial, "definitions"):
            definitions = partial.definitions
    except Exception:
        _logger.debug("Constructor parent AST lookup failed", exc_info=True)

    if definitions is not None:
        for defn in definitions:
            if isinstance(defn, TypeDef) and any(
                variant.name == name for variant in defn.variants
            ):
                return cast(str, defn.name)

    current_type: str | None = None
    for line in source.splitlines():
        type_match = re.match(
            r"^[ \t]*(?:export[ \t]+)?type[ \t]+(\w+)[ \t]*(?:\[[^\]]*\])?[ \t]*=[ \t]*(.*)$",
            line,
        )
        if type_match is not None:
            current_type = type_match.group(1)
            variants = type_match.group(2)
        elif current_type is not None and re.match(r"^[ \t]*\|", line):
            variants = line
        elif current_type is not None and (
            not line.strip() or line.lstrip().startswith(("//", "/*", "*"))
        ):
            continue
        else:
            current_type = None
            continue

        for variant in variants.split("|"):
            variant_name = variant.strip().split("(", 1)[0].strip()
            if variant_name == name:
                return current_type
    return None


def _extract_parameter_decl(name: str, source: str, line_1_based: int) -> str | None:
    """Extract a parameter declaration snippet from a function signature line."""
    line = _line_at(source, line_1_based)
    if line is None:
        return None
    annotation = _extract_named_type_annotation(name, line)
    if annotation is None:
        return None
    return f"{name}: {annotation}"


def _extract_binding_decl(name: str, source: str, line_1_based: int) -> str | None:
    """Extract a let/var binding declaration snippet from a source line."""
    line = _line_at(source, line_1_based)
    if line is None:
        return None
    match = re.search(
        rf"\b(?:let|var)\s+{re.escape(name)}\s*:\s*[^=]+(?:=\s*.*)?$",
        line.strip(),
    )
    if match is None:
        return None
    return match.group(0).strip()


def _extract_named_type_annotation(name: str, line: str) -> str | None:
    """Extract a type annotation that follows one named binding on a source line."""
    for match in re.finditer(rf"\b{re.escape(name)}\b", line):
        index = match.end()
        while index < len(line) and line[index].isspace():
            index += 1
        if index >= len(line) or line[index] != ":":
            continue

        index += 1
        while index < len(line) and line[index].isspace():
            index += 1

        start = index
        paren_depth = 0
        bracket_depth = 0
        while index < len(line):
            ch = line[index]
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                if paren_depth > 0:
                    paren_depth -= 1
                elif bracket_depth == 0:
                    break
            elif ch == "[":
                bracket_depth += 1
            elif ch == "]":
                if bracket_depth > 0:
                    bracket_depth -= 1
            elif ch in {",", "="} and paren_depth == 0 and bracket_depth == 0:
                break
            index += 1

        annotation = line[start:index].strip()
        if annotation:
            return annotation
    return None


def _split_top_level_csv(text: str) -> list[str]:
    """Split a comma-separated list while preserving nested type syntax."""
    parts: list[str] = []
    start = 0
    paren_depth = 0
    bracket_depth = 0
    for index, ch in enumerate(text):
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            if paren_depth > 0:
                paren_depth -= 1
        elif ch == "[":
            bracket_depth += 1
        elif ch == "]":
            if bracket_depth > 0:
                bracket_depth -= 1
        elif ch == "," and paren_depth == 0 and bracket_depth == 0:
            part = text[start:index].strip()
            if part:
                parts.append(part)
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _signature_from_function_type(name: str, annotation: str) -> tuple | None:
    """Render signature-help data from a function-typed annotation."""
    text = annotation.strip()
    if not text.startswith("("):
        return None

    paren_depth = 0
    end_index = -1
    for index, ch in enumerate(text):
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1
            if paren_depth == 0:
                end_index = index
                break
    if end_index == -1:
        return None

    params_text = text[1:end_index].strip()
    rest = text[end_index + 1 :].lstrip()
    if not rest.startswith("->"):
        return None
    return_type = rest[2:].strip()
    if not return_type:
        return None

    param_types = _split_top_level_csv(params_text)
    param_infos = [_parameter_information(param) for param in param_types]
    label = f"{name}({', '.join(param_types)}) -> {return_type}"
    return label, param_infos


def _format_hover_block(snippet: str, kind: str) -> str:
    """Render a hover snippet as markdown."""
    return f"```\n{snippet}\n```\n({kind})"


def _extract_unknown_symbol_name(message: str) -> str | None:
    """Return the unresolved symbol name from a Geno diagnostic message."""
    patterns = (
        r"Unknown (?:name|identifier)\s+'(\w+)'",
        r"Undefined variable:\s*(\w+)",
        r"Unknown constructor:\s*(\w+)",
        r"Unknown type:\s*'(\w+)'",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if match is not None:
            return match.group(1)
    return None


def _infer_missing_end_text(source: str) -> tuple[str, str] | None:
    """Infer the innermost unclosed block from incomplete Geno source."""
    block_patterns = (
        ("func", r"^\s*(?:export\s+)?(?:@\w+\([^)]*\)\s+)?(?:async\s+)?func\b"),
        ("if", r"^\s*if\b.*\bthen\b"),
        ("match", r"^\s*match\b.*\bwith\b"),
        ("while", r"^\s*while\b.*\bdo\b"),
        ("for", r"^\s*for\b.*\bdo\b"),
        ("try", r"^\s*try\b"),
        ("trait", r"^\s*trait\b"),
        ("impl", r"^\s*impl\b"),
        ("test", r"^\s*test\b"),
    )
    stack: list[tuple[str, str]] = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        end_match = re.match(r"^\s*end\s+(\w+)\b", line)
        if end_match is not None:
            kind = end_match.group(1)
            for idx in range(len(stack) - 1, -1, -1):
                if stack[idx][0] == kind:
                    del stack[idx]
                    break
            continue
        indent_match = re.match(r"(\s*)", line)
        indent = indent_match.group(1) if indent_match else ""
        for kind, pattern in block_patterns:
            if re.match(pattern, line):
                stack.append((kind, indent))
                break
    if not stack:
        return None
    kind, indent = stack[-1]
    return f"end {kind}", indent


def _uri_to_path(uri: str) -> Path:
    """Convert a file:// URI to a local filesystem path."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"LSP URI must use the file scheme: {uri!r}")

    netloc = unquote(parsed.netloc)
    if netloc and netloc.lower() != "localhost":
        raise ValueError(f"LSP file URI must be local: {uri!r}")

    path = unquote(parsed.path)

    if _IS_WINDOWS:
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]

    return Path(path)


def _uri_to_path_or_none(uri: str) -> Path | None:
    """Convert a local file URI to a path, or return None for virtual buffers."""
    try:
        return _uri_to_path(uri)
    except ValueError:
        return None


def _has_lsp_line(lines: list[str], line: int) -> bool:
    """Return whether a zero-based LSP line is inside a split document."""
    return 0 <= line < len(lines)


def _has_manifest_project(root: Path | None) -> bool:
    """Return whether *root* points at a manifest-backed Geno project."""
    return root is not None and (root / "geno.toml").exists()


def _relevant_source_overrides(
    project_root: Path | None,
    file_path: Path,
    source_override: str | None,
    source_overrides: Mapping[Path, str] | None,
) -> Mapping[Path, str] | None:
    """Limit overlays that cannot affect an import-free standalone file."""
    if (
        project_root is None
        and source_override is not None
        and re.search(r"(?m)^[ \t]*import\b", source_override) is None
    ):
        return {file_path.resolve(): source_override}
    return source_overrides


@dataclass(frozen=True)
class _IndexedProjectModule:
    """One module's LSP metadata before per-document visibility filtering."""

    graph_name: str
    display_name: str
    path: str
    all_names: list[str]
    exported_names: list[str]
    all_completion_symbols: list[_CompletionSymbol]
    exported_completion_symbols: list[_CompletionSymbol]
    scope: str | None
    owner_package: str | None


_ROOT_PROJECT_SCOPE = "<root-project>"


@dataclass(frozen=True)
class _ProjectView:
    """Per-document project index state used by LSP features."""

    project_modules: dict[str, tuple[str, list[str], list[str]]]
    completion_modules: dict[
        str,
        tuple[str, list[_CompletionSymbol], list[_CompletionSymbol]],
    ]
    path_to_module: dict[str, str]
    project_paths: frozenset[str]
    indexed_modules: tuple[_IndexedProjectModule, ...] = ()


def _scoped_project_view(view: _ProjectView, current_path: Path) -> _ProjectView:
    """Return the import-visible module aliases for one source file.

    Root project modules are visible to other root files. Dependency entry
    modules and stdlib modules are public from every package. A dependency's
    private sibling modules are visible only while editing that dependency.
    """
    if not view.indexed_modules:
        return view

    current_path_str = str(current_path.resolve())
    current_module = next(
        (module for module in view.indexed_modules if module.path == current_path_str),
        None,
    )
    focus_scope = (
        current_module.owner_package
        if current_module is not None and current_module.owner_package is not None
        else _ROOT_PROJECT_SCOPE
    )

    project_modules: dict[str, tuple[str, list[str], list[str]]] = {}
    completion_modules: dict[
        str,
        tuple[str, list[_CompletionSymbol], list[_CompletionSymbol]],
    ] = {}
    path_to_module: dict[str, str] = {}
    visible_modules = [
        module
        for module in view.indexed_modules
        if module.scope is None or module.scope == focus_scope
    ]
    # Same-directory imports win before package lookup in the resolver. Add
    # global modules first so the matching root/package-local alias replaces
    # any public dependency or stdlib module with the same display name.
    visible_modules.sort(key=lambda module: module.scope is not None)
    for module in visible_modules:
        project_modules[module.display_name] = (
            module.path,
            module.all_names,
            module.exported_names,
        )
        completion_modules[module.display_name] = (
            module.path,
            module.all_completion_symbols,
            module.exported_completion_symbols,
        )
        path_to_module[module.path] = module.display_name

    return _ProjectView(
        project_modules,
        completion_modules,
        path_to_module,
        view.project_paths,
        view.indexed_modules,
    )


def _is_stdlib_path(path: Path) -> bool:
    """Return whether *path* belongs to the bundled stdlib tree."""
    from geno.module_resolver import _STD_DIR

    std_root = str(_STD_DIR.resolve()) + os.sep
    return str(path.resolve()).startswith(std_root)


def _load_project_view(
    file_path: Path,
    source_override: str | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> _ProjectView:
    """Build a per-document project view for completion/definition surfaces."""
    from geno.dependency_graph import DependencyGraph
    from geno.project_graph import ProjectGraph
    from geno.project_resolution import resolve_file_context

    project = ProjectGraph.discover(file_path)
    source_overrides = _relevant_source_overrides(
        project.root,
        file_path,
        source_override,
        source_overrides,
    )
    normalized_overrides = cast(
        Mapping[Union[str, Path], str] | None,
        source_overrides,
    )
    if _has_manifest_project(project.root):
        graph_overrides = cast(Mapping[Path, str] | None, source_overrides)
        if source_override is not None:
            graph_overrides = {
                Path(path).resolve(): source
                for path, source in (source_overrides or {}).items()
            }
            graph_overrides[file_path.resolve()] = source_override
        dependency_graph = DependencyGraph.resolve(
            project,
            source_overrides=graph_overrides,
        )
        return _project_view_from_graph(
            project.files,
            dependency_graph.sorted_modules,
            dependency_graph.file_map,
            dependency_graph.original_sources,
            current_path=file_path,
            current_source=source_override,
        )

    context = resolve_file_context(
        file_path,
        source_override=source_override,
        source_overrides=normalized_overrides,
    )
    return _project_view_from_graph(
        context.project.files,
        context.dependency_graph.sorted_modules,
        context.dependency_graph.file_map,
        context.dependency_graph.original_sources,
        current_path=file_path,
        current_source=context.source,
    )


def _project_view_from_graph(
    project_files: Iterable[Any],
    sorted_modules: Iterable[str],
    file_map: Mapping[str, Any],
    module_sources: Mapping[str, str],
    *,
    current_path: Path,
    current_source: str | None,
) -> _ProjectView:
    """Convert a resolved project/dependency graph into LSP project indexes.

    The returned view is scope-neutral so it can be shared by the project-view
    cache. ``_scoped_project_view`` derives import-visible aliases for the
    current root project or dependency package.
    """
    project_paths = frozenset(
        str(resolved_file.path.resolve())
        for resolved_file in project_files
        if not _is_stdlib_path(resolved_file.path)
    )
    if len(project_paths) <= 1:
        return _ProjectView({}, {}, {}, project_paths)

    indexed_modules: list[_IndexedProjectModule] = []
    current_resolved_path = current_path.resolve()
    for module_name in sorted_modules:
        resolved_file = file_map.get(module_name)
        if resolved_file is None:
            continue
        resolved_path = resolved_file.path.resolve()
        path_str = str(resolved_path)
        if current_source is not None and resolved_path == current_resolved_path:
            module_source = current_source
        else:
            imported_source = module_sources.get(module_name)
            if imported_source is None:
                module_source = resolved_file.path.read_text(encoding="utf-8")
            else:
                module_source = imported_source
        all_names, exported_names = _extract_names(module_source)
        all_completion_symbols, exported_completion_symbols = (
            _extract_completion_symbols(module_source)
        )
        if _is_stdlib_path(resolved_path):
            scope = None
        elif resolved_file.is_dependency:
            scope = (
                resolved_file.package_name
                if resolved_file.graph_name is not None
                else None
            )
        else:
            scope = _ROOT_PROJECT_SCOPE
        indexed_modules.append(
            _IndexedProjectModule(
                graph_name=module_name,
                display_name=resolved_file.module_name,
                path=path_str,
                all_names=all_names,
                exported_names=exported_names,
                all_completion_symbols=all_completion_symbols,
                exported_completion_symbols=exported_completion_symbols,
                scope=scope,
                owner_package=resolved_file.package_name,
            )
        )
    return _ProjectView({}, {}, {}, project_paths, tuple(indexed_modules))


def _load_project_module_index(
    file_path: Path,
    source_override: str | None = None,
    source_overrides: Mapping[Path, str] | None = None,
) -> tuple[dict[str, tuple[str, list[str], list[str]]], dict[str, str]]:
    """Build cross-module completion/definition indexes for a source file."""
    project_view = _load_project_view(
        file_path,
        source_override=source_override,
        source_overrides=source_overrides,
    )
    project_view = _scoped_project_view(project_view, file_path)
    return project_view.project_modules, project_view.path_to_module


def _build_symbol_table_for_document(
    file_path: Path,
    source: str,
    source_overrides: Mapping[Path, str] | None = None,
) -> SymbolTable | None:
    """Build a symbol table for a file plus its imported modules."""
    from geno.lexer import Lexer, LexerError
    from geno.parser import Parser
    from geno.parser_base import ParseError as _ParseError
    from geno.parser_base import ParseErrors as _ParseErrors
    from geno.project_resolution import ProjectResolutionError, resolve_file_context
    from geno.symbol_table import build_symbol_table

    recoverable_errors = (
        LexerError,
        _ParseError,
        _ParseErrors,
        ProjectResolutionError,
        OSError,
        UnicodeError,
    )
    filename = str(file_path)
    try:
        tokens = Lexer(source, filename).tokenize()
        program = Parser(tokens).parse_program()
        context = resolve_file_context(
            file_path,
            source_override=source,
            source_overrides=cast(
                Mapping[Union[str, Path], str] | None,
                source_overrides,
            ),
        )
        return build_symbol_table(program, filename, context.parsed_modules or None)
    except recoverable_errors:
        _logger.debug(
            "Full symbol table build failed, trying single-file fallback", exc_info=True
        )
        try:
            tokens = Lexer(source, filename).tokenize()
            program = Parser(tokens).parse_program()
            return build_symbol_table(program, filename)
        except (LexerError, _ParseError, _ParseErrors) as e:
            # Use partial program from error recovery
            partial = getattr(e, "partial_program", None)
            if partial is not None and hasattr(partial, "definitions"):
                _logger.debug(
                    "Using partial parse for symbol table (%d definitions)",
                    len(partial.definitions),
                )
                try:
                    return build_symbol_table(partial, filename)
                except Exception:
                    _logger.debug(
                        "Symbol table from partial parse failed", exc_info=True
                    )
            return None
        except Exception:
            _logger.debug(
                "Single-file symbol table fallback also failed", exc_info=True
            )
            return None


_DOC_CACHE_MAX = 512
_PROJECT_VIEW_CACHE_MAX = 128
_SYMBOL_TABLE_CACHE_MAX = 256
_DIAG_DEBOUNCE_SEC = 0.3


def _completion_symbols_for_uri(
    uri: str,
    doc_cache: MutableMapping[str, tuple[str, list[_CompletionSymbol]]],
    open_docs: Mapping[str, str],
) -> list[_CompletionSymbol] | None:
    """Return current-document completion symbols, rebuilding on cache miss."""
    cached = doc_cache.get(uri)
    if cached is not None:
        _, symbols = cached
        return cast(list[_CompletionSymbol], symbols)

    source = open_docs.get(uri)
    if source is None:
        return None

    all_symbols, _ = _extract_completion_symbols(source)
    typed_symbols = cast(list[_CompletionSymbol], all_symbols)
    doc_cache[uri] = (source, typed_symbols)
    return typed_symbols


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class GenoLanguageServer:
    """Stateful wrapper around the pygls language server."""

    def __init__(self, *, diag_debounce_sec: float = _DIAG_DEBOUNCE_SEC) -> None:
        if not HAS_PYGLS:
            raise RuntimeError(
                "pygls is required for the LSP server. Install with: pip install geno-lang[lsp]"
            )

        self.server = LanguageServer("geno-lsp", "0.1.0")
        self.server.lsp._text_document_sync_kind = types.TextDocumentSyncKind.Full  # type: ignore[attr-defined]
        self._diag_debounce_sec = diag_debounce_sec
        self._state_lock = threading.RLock()

        # Cache of document source -> top-level completion symbols.
        # Maps URI -> (source text, list of user-defined completion symbols).
        self._doc_cache: _BoundedDict = _BoundedDict(_DOC_CACHE_MAX)
        self._open_docs: dict[str, str] = {}
        self._open_doc_paths: dict[str, Path] = {}
        self._open_doc_versions: dict[str, int] = {}
        self._open_doc_lsp_versions: dict[str, int] = {}

        # Per-document project indexes for cross-module LSP features.
        self._project_views: dict[str, _ProjectView] = {}
        self._project_view_cache: _BoundedDict = _BoundedDict(_PROJECT_VIEW_CACHE_MAX)
        self._symbol_table_cache: _BoundedDict = _BoundedDict(_SYMBOL_TABLE_CACHE_MAX)
        self._debounce_timers: dict[str, threading.Timer] = {}
        self._register_handlers()

    def to_pygls_server(self) -> LanguageServer:
        """Return the configured pygls server instance."""
        return self.server

    def _locked_handler(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        """Return a registered LSP handler serialized on server state."""

        @wraps(handler)
        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            with self._state_lock:
                return handler(*args, **kwargs)

        _wrapped.__self__ = self  # type: ignore[attr-defined]
        return _wrapped

    def _register_handlers(self) -> None:
        """Register LSP feature handlers on the underlying pygls server."""
        feature = self.server.feature
        locked = self._locked_handler
        feature(types.TEXT_DOCUMENT_DID_OPEN)(locked(self.did_open))
        feature(types.TEXT_DOCUMENT_DID_CHANGE)(locked(self.did_change))
        feature(types.TEXT_DOCUMENT_DID_SAVE)(locked(self.did_save))
        feature(types.TEXT_DOCUMENT_DID_CLOSE)(locked(self.did_close))
        feature(types.TEXT_DOCUMENT_HOVER)(locked(self.hover))
        feature(types.TEXT_DOCUMENT_DEFINITION)(locked(self.definition))
        feature(types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT)(locked(self.document_highlight))
        feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)(locked(self.document_symbol))
        feature(types.WORKSPACE_SYMBOL)(locked(self.workspace_symbol))
        feature(types.TEXT_DOCUMENT_COMPLETION)(locked(self.completion))
        feature(
            types.TEXT_DOCUMENT_SIGNATURE_HELP,
            types.SignatureHelpOptions(trigger_characters=["(", ","]),
        )(locked(self.signature_help))
        feature(types.TEXT_DOCUMENT_RENAME)(locked(self.rename))
        feature(types.TEXT_DOCUMENT_REFERENCES)(locked(self.references))
        feature(types.TEXT_DOCUMENT_CODE_ACTION)(locked(self.code_action))

    @staticmethod
    def _document_lsp_version(document: Any) -> int | None:
        """Return a usable LSP document version, if the payload carries one."""
        version = getattr(document, "version", None)
        if isinstance(version, int) and not isinstance(version, bool):
            return version
        return None

    def _is_stale_document_version(self, uri: str, version: int | None) -> bool:
        """Return true when an incoming document version is older than current state."""
        if version is None:
            return False
        current = self._open_doc_lsp_versions.get(uri)
        return current is not None and version < current

    def _source_overrides_from_open_documents(self) -> dict[Path, str]:
        """Return open in-memory document sources keyed by resolved path."""
        return {
            path: self._open_docs[uri]
            for uri, path in self._open_doc_paths.items()
            if uri in self._open_docs
        }

    def _read_project_source(self, path_str: str) -> str | None:
        """Return project module source, preferring open in-memory documents."""
        path = Path(path_str).resolve()
        for uri, source in self._open_docs.items():
            if self._open_doc_paths.get(uri) == path:
                return source
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _open_uri_for_path(self, path_str: str) -> str | None:
        """Return an open document URI for a resolved filesystem path, if any."""
        path = Path(path_str)
        for uri, open_path in self._open_doc_paths.items():
            if open_path == path:
                return uri
        return None

    def _project_uri_for_path(self, path: Path) -> str:
        """Return the stable LSP URI for one filesystem path."""
        resolved = path.resolve()
        open_uri = self._open_uri_for_path(str(resolved))
        return open_uri if open_uri is not None else resolved.as_uri()

    def _module_name_for_path(self, path_str: str) -> str | None:
        """Return a known module name for a resolved project file path."""
        for project_view in self._project_views.values():
            for indexed_module in project_view.indexed_modules:
                if indexed_module.path == path_str:
                    return indexed_module.display_name
        return None

    def _resolved_path_for_symbol_filename(
        self,
        uri: str,
        symbol_filename: str,
    ) -> Path | None:
        """Resolve a symbol-table filename back to a filesystem path."""
        current_path = _uri_to_path(uri)
        if symbol_filename == str(current_path):
            return current_path

        symbol_path = Path(symbol_filename)
        if symbol_path.is_absolute() or symbol_path.exists():
            return symbol_path

        project_view = self._project_view_for_uri(uri)
        for module_name, (
            module_path,
            _all,
            _exported,
        ) in project_view.project_modules.items():
            if module_name == symbol_filename:
                return Path(module_path)
        for indexed_module in project_view.indexed_modules:
            if indexed_module.graph_name == symbol_filename:
                return Path(indexed_module.path)
        return None

    def _workspace_symbol_paths(self) -> list[str]:
        """Return the unique file paths reachable from open documents' projects."""
        paths: set[str] = set()
        for uri in self._open_docs:
            project_paths = self._project_view_for_uri(uri).project_paths
            if project_paths:
                paths.update(project_paths)
                continue
            file_path = _uri_to_path_or_none(uri)
            if file_path is not None:
                paths.add(str(file_path.resolve()))
        return sorted(paths)

    def _read_symbol_source(
        self, uri: str, symbol_filename: str
    ) -> tuple[Path | None, str | None]:
        """Resolve and read source for one symbol-table filename."""
        symbol_path = self._resolved_path_for_symbol_filename(uri, symbol_filename)
        if symbol_path is None:
            return None, None
        if symbol_path == _uri_to_path(uri):
            return symbol_path, self._open_docs.get(uri)
        source = self._read_project_source(str(symbol_path))
        if source is None and symbol_path.exists():
            source = symbol_path.read_text(encoding="utf-8")
        return symbol_path, source

    def _semantic_function_signature(
        self,
        uri: str,
        source: str,
        line: int,
        name: str,
        name_column: int,
    ) -> tuple[bool, tuple | None]:
        """Resolve a call target semantically and extract signature-help data."""
        table = self._build_symbol_table(uri, source)
        if table is None:
            return False, None

        defn = table.symbol_at(
            str(_uri_to_path(uri)),
            line + 1,
            name_column + 1,
            name=name,
        )
        if defn is None:
            return False, None

        _symbol_path, symbol_source = self._read_symbol_source(
            uri, defn.location.filename
        )
        if symbol_source is None:
            return True, None

        if defn.kind == "function":
            return True, _extract_user_func_sig(defn.name, symbol_source)
        if defn.kind in {"variable", "parameter"}:
            line_text = _line_at(symbol_source, defn.location.line)
            if line_text is None:
                return True, None
            annotation = _extract_named_type_annotation(defn.name, line_text)
            if annotation is None:
                return True, None
            return True, _signature_from_function_type(defn.name, annotation)
        return True, None

    def _semantic_hover_info(self, uri: str, word: str, defn) -> str | None:
        """Build hover markdown for one semantic symbol definition."""
        _symbol_path, symbol_source = self._read_symbol_source(
            uri, defn.location.filename
        )
        if symbol_source is None:
            return None

        if defn.kind == "function":
            sig = _extract_user_func_sig(defn.name, symbol_source)
            if sig is not None:
                label, _params = sig
                return _format_hover_block(label, "function")
        elif defn.kind == "type":
            decl = _extract_user_type_decl(defn.name, symbol_source)
            if decl is not None:
                return _format_hover_block(decl, "type")
        elif defn.kind == "trait":
            decl = _extract_user_trait_decl(defn.name, symbol_source)
            if decl is not None:
                return _format_hover_block(decl, "trait")
        elif defn.kind == "constructor":
            parent_type = _find_constructor_parent_type(defn.name, symbol_source)
            if parent_type is not None:
                return _format_hover_block(f"{defn.name}: {parent_type}", "constructor")
        elif defn.kind == "parameter":
            decl = _extract_parameter_decl(
                defn.name,
                symbol_source,
                defn.location.line,
            )
            if decl is not None:
                return _format_hover_block(decl, "parameter")
        elif defn.kind == "variable":
            decl = _extract_binding_decl(
                defn.name,
                symbol_source,
                defn.location.line,
            )
            if decl is not None:
                return _format_hover_block(decl, "variable")

        line = _line_at(symbol_source, defn.location.line)
        if line is not None:
            return _format_hover_block(line.strip(), defn.kind)
        return None

    def _set_open_document_source(
        self,
        uri: str,
        source: str,
        *,
        lsp_version: int | None = None,
    ) -> None:
        """Update in-memory source and revision for an open document."""
        file_path = _uri_to_path_or_none(uri)
        if file_path is not None:
            resolved_path = file_path.resolve()
            path_str = str(resolved_path)
            self._open_doc_paths[uri] = resolved_path
            self._invalidate_project_view_cache_for_path(path_str)
            self._invalidate_symbol_table_cache_for_path(path_str)
        else:
            self._open_doc_paths.pop(uri, None)
            self._project_views.pop(uri, None)
        self._open_docs[uri] = source
        self._open_doc_versions[uri] = self._open_doc_versions.get(uri, 0) + 1
        if lsp_version is not None:
            self._open_doc_lsp_versions[uri] = lsp_version

    def _invalidate_project_view_cache_for_path(self, path_str: str) -> None:
        """Drop cached project views that include the given file path."""
        stale_keys = _project_view_keys_for_path(self._project_view_cache, path_str)
        for cache_key in stale_keys:
            self._project_view_cache.pop(cache_key, None)

    def _invalidate_symbol_table_cache_for_path(self, path_str: str) -> None:
        """Drop cached symbol tables whose project view includes the given file."""
        stale_keys = _symbol_table_keys_for_path(self._symbol_table_cache, path_str)
        for cache_key in stale_keys:
            self._symbol_table_cache.pop(cache_key, None)

    def _project_view_cache_key(
        self,
        project_paths: frozenset[str],
    ) -> tuple[frozenset[str], tuple[tuple[str, int], ...]]:
        """Build a cache key from project file membership and overlay revisions."""
        open_paths: list[str] = []
        versions_by_path: dict[str, int] = {}
        for uri in self._open_docs:
            open_path = self._open_doc_paths.get(uri)
            if open_path is None:
                continue
            path = str(open_path)
            open_paths.append(path)
            versions_by_path[path] = self._open_doc_versions.get(uri, 0)
        return cast(
            tuple[frozenset[str], tuple[tuple[str, int], ...]],
            _build_project_view_cache_key(
                project_paths,
                open_paths,
                versions_by_path,
            ),
        )

    def _index_project(
        self,
        uri: str,
        source: str | None = None,
        project_paths_hint: frozenset[str] | None = None,
    ) -> None:
        """Try to discover and index the project from a file URI."""
        try:
            file_path = self._open_doc_paths.get(uri)
            if file_path is None:
                file_path = _uri_to_path(uri).resolve()
            uri_path = str(file_path)
            if project_paths_hint:
                cache_key = self._project_view_cache_key(project_paths_hint)
                cached_view = self._project_view_cache.get(cache_key)
                if cached_view is not None and uri_path in cached_view.project_paths:
                    self._project_views[uri] = cached_view
                    return

            source_overrides = self._source_overrides_from_open_documents()
            if source is not None:
                source_overrides[file_path] = source
            project_view = _load_project_view(
                file_path,
                source_override=source,
                source_overrides=source_overrides,
            )
            self._project_views[uri] = project_view
            self._project_view_cache[
                self._project_view_cache_key(project_view.project_paths)
            ] = project_view
        except Exception:
            _logger.debug("Project view build failed for %s", uri, exc_info=True)
            self._project_views.pop(uri, None)

    def _project_view_for_uri(self, uri: str) -> _ProjectView:
        """Return the project view for *uri*, rebuilding it when needed."""
        project_view = self._project_views.get(uri)
        if project_view is not None:
            file_path = _uri_to_path_or_none(uri)
            if file_path is None:
                return _ProjectView({}, {}, {}, project_view.project_paths)
            return _scoped_project_view(project_view, file_path)
        source = self._open_docs.get(uri)
        if source is None:
            return _ProjectView({}, {}, {}, frozenset())
        file_path = _uri_to_path_or_none(uri)
        if file_path is None:
            return _ProjectView({}, {}, {}, frozenset())
        self._index_project(uri, source)
        fallback_path = str(file_path.resolve())
        project_view = self._project_views.get(
            uri,
            _ProjectView({}, {}, {}, frozenset({fallback_path})),
        )
        return _scoped_project_view(project_view, file_path)

    def _affected_open_document_uris(
        self,
        focus_uri: str | None,
        previous_project_paths: frozenset[str],
    ) -> list[str]:
        """Return open documents whose project views overlap the changed project."""
        if focus_uri is None:
            return list(self._open_docs)

        focus_paths = set(previous_project_paths)
        if focus_uri in self._open_docs:
            self._index_project(
                focus_uri,
                self._open_docs[focus_uri],
                project_paths_hint=previous_project_paths or None,
            )
            focus_paths.update(self._project_view_for_uri(focus_uri).project_paths)

        if not focus_paths:
            return [focus_uri] if focus_uri in self._open_docs else []

        affected: list[str] = []
        for open_uri in self._open_docs:
            if open_uri == focus_uri:
                affected.append(open_uri)
                continue
            if self._project_view_for_uri(open_uri).project_paths & focus_paths:
                affected.append(open_uri)
        return affected

    def _refresh_document_metadata(self, uri: str) -> None:
        """Refresh cached names and project view for one open document."""
        source = self._open_docs[uri]
        all_symbols, _ = _extract_completion_symbols(source)
        self._doc_cache[uri] = (source, all_symbols)
        previous_paths = self._project_views.get(uri)
        self._index_project(
            uri,
            source,
            project_paths_hint=(
                previous_paths.project_paths if previous_paths is not None else None
            ),
        )

    def _refresh_open_documents(
        self,
        focus_uri: str | None = None,
        previous_project_paths: frozenset[str] = frozenset(),
    ) -> None:
        """Republish diagnostics for the affected open documents only."""
        if not self._open_docs:
            self._project_views.clear()
            self._project_view_cache.clear()
            self._symbol_table_cache.clear()
            return

        affected_uris = self._affected_open_document_uris(
            focus_uri, previous_project_paths
        )
        if not affected_uris:
            return

        for open_uri in affected_uris:
            self._refresh_document_metadata(open_uri)

        groups: dict[frozenset[str], list[str]] = {}
        for open_uri in affected_uris:
            project_paths = self._project_view_for_uri(open_uri).project_paths
            groups.setdefault(project_paths, []).append(open_uri)

        for group_uris in groups.values():
            representative_uri = group_uris[0]
            if focus_uri is not None and focus_uri in group_uris:
                representative_uri = focus_uri
            self._publish_diagnostics(
                representative_uri, self._open_docs[representative_uri]
            )

    def _publish_diagnostics(self, uri: str, source: str) -> None:
        """Run geno.check() and publish diagnostics."""
        # Use ProjectGraph → DependencyGraph → check_project_graph() pipeline
        # to match geno check output exactly. Always attempt discovery —
        # _index_project may have failed (e.g., circular imports) but we
        # still want to surface those errors as LSP diagnostics.
        try:
            from geno.dependency_graph import (
                DependencyGraph,
                DependencyGraphError,
            )
            from geno.lexer import LexerError
            from geno.parser_base import ParseError, ParseErrors
            from geno.project_graph import (
                ProjectGraph,
            )
            from geno.project_graph import (
                ProjectGraphError as _ProjectGraphError,
            )
            from geno.project_resolution import (
                ProjectResolutionError,
                resolve_file_context,
            )
            from geno.types import GenoTypeError
            from geno.types import TypeErrors as GenoTypeErrors

            file_path = _uri_to_path_or_none(uri)
            if file_path is None:
                result = geno.check(source, filename=uri)
                virtual_diags = [_to_lsp_diagnostic(d) for d in result.diagnostics]
                self.server.publish_diagnostics(uri, virtual_diags)
                all_symbols, _ = _extract_completion_symbols(source)
                self._doc_cache[uri] = (source, all_symbols)
                return
            source_overrides = self._source_overrides_from_open_documents()
            project = ProjectGraph.discover(file_path)
            source_overrides = dict(
                _relevant_source_overrides(
                    project.root,
                    file_path,
                    source,
                    source_overrides,
                )
                or {}
            )
            source_overrides[file_path.resolve()] = source
            if len(project.files) > 1 and _has_manifest_project(project.root):
                dg = DependencyGraph.resolve(project, source_overrides=source_overrides)

                # Read manifest targets if available
                from geno.target_profile import (
                    TargetProfile,
                    resolve_manifest_targets,
                )

                # Typecheck via the same pipeline as geno check
                from geno.typechecker import TypeChecker

                try:
                    manifest_target_names: list[str] = resolve_manifest_targets(
                        project.root
                    )
                    manifest_check_targets: list[str | None] = (
                        list(manifest_target_names) if manifest_target_names else [None]
                    )
                    for manifest_target_name in manifest_check_targets:
                        manifest_target_profile = (
                            TargetProfile.load(manifest_target_name)
                            if manifest_target_name is not None
                            else None
                        )
                        checker = TypeChecker(target_profile=manifest_target_profile)
                        checker.check_project_graph(dg)
                except (
                    DependencyGraphError,
                    LexerError,
                    ParseError,
                    ParseErrors,
                    _ProjectGraphError,
                    ProjectResolutionError,
                    GenoTypeError,
                    GenoTypeErrors,
                    TypeError,
                    ValueError,
                ) as e:
                    grouped_diags = _diagnostics_by_uri_from_exception(e, uri)
                    for err_uri, diagnostics in grouped_diags.items():
                        self.server.publish_diagnostics(err_uri, diagnostics)
                    for rf in project.files:
                        mod_uri = rf.path.as_uri()
                        if mod_uri not in grouped_diags:
                            self.server.publish_diagnostics(mod_uri, [])
                    all_symbols, _ = _extract_completion_symbols(source)
                    self._doc_cache[uri] = (source, all_symbols)
                    return

                # No errors — clear diagnostics for all project files
                for rf in project.files:
                    mod_uri = rf.path.as_uri()
                    self.server.publish_diagnostics(mod_uri, [])

                all_symbols, _ = _extract_completion_symbols(source)
                self._doc_cache[uri] = (source, all_symbols)
                return

            context = resolve_file_context(
                file_path,
                source_override=source,
                source_overrides=cast(
                    Mapping[Union[str, Path], str] | None,
                    source_overrides,
                ),
            )
            if len(context.project.files) > 1 and _has_manifest_project(
                context.project.root
            ):
                dg = context.dependency_graph

                # Read manifest targets if available
                from geno.target_profile import (
                    TargetProfile,
                    resolve_manifest_targets,
                )

                # Typecheck via the same pipeline as geno check
                from geno.typechecker import TypeChecker

                try:
                    target_names: list[str] = resolve_manifest_targets(
                        context.project.root
                    )
                    check_targets: list[str | None] = (
                        list(target_names) if target_names else [None]
                    )
                    for target_name in check_targets:
                        target_profile = (
                            TargetProfile.load(target_name)
                            if target_name is not None
                            else None
                        )
                        checker = TypeChecker(target_profile=target_profile)
                        checker.check_project_graph(dg)
                except (
                    DependencyGraphError,
                    LexerError,
                    ParseError,
                    ParseErrors,
                    _ProjectGraphError,
                    ProjectResolutionError,
                    GenoTypeError,
                    GenoTypeErrors,
                    TypeError,
                    ValueError,
                ) as e:
                    grouped_diags = _diagnostics_by_uri_from_exception(e, uri)
                    for err_uri, diagnostics in grouped_diags.items():
                        self.server.publish_diagnostics(err_uri, diagnostics)
                    for rf in context.project.files:
                        mod_uri = rf.path.as_uri()
                        if mod_uri not in grouped_diags:
                            self.server.publish_diagnostics(mod_uri, [])
                    all_symbols, _ = _extract_completion_symbols(source)
                    self._doc_cache[uri] = (source, all_symbols)
                    return

                # No errors — clear diagnostics for all project files
                for rf in context.project.files:
                    mod_uri = rf.path.as_uri()
                    self.server.publish_diagnostics(mod_uri, [])

                all_symbols, _ = _extract_completion_symbols(source)
                self._doc_cache[uri] = (source, all_symbols)
                return
            else:
                from geno.target_profile import resolve_manifest_targets

                lsp_diags: list[types.Diagnostic] = []
                target_names = resolve_manifest_targets(context.project.root)
                single_file_check_targets: list[str | None] = (
                    list(target_names) if target_names else [None]
                )
                for target_name in single_file_check_targets:
                    result = geno.check(
                        source,
                        filename=context.filename,
                        modules=context.merged_module_sources(),
                        target=target_name,
                    )
                    if not result.ok:
                        lsp_diags.extend(
                            _to_lsp_diagnostic(d) for d in result.diagnostics
                        )
                self.server.publish_diagnostics(uri, lsp_diags)
                all_symbols, _ = _extract_completion_symbols(source)
                self._doc_cache[uri] = (source, all_symbols)
                return
        except DependencyGraphError as e:
            diag = _error_diagnostic(str(e))
            self.server.publish_diagnostics(uri, [diag])
            all_symbols, _ = _extract_completion_symbols(source)
            self._doc_cache[uri] = (source, all_symbols)
            return
        except _ProjectGraphError as e:
            diag = _error_diagnostic(str(e))
            self.server.publish_diagnostics(uri, [diag])
            all_symbols, _ = _extract_completion_symbols(source)
            self._doc_cache[uri] = (source, all_symbols)
            return
        except ProjectResolutionError as e:
            diag = _error_diagnostic(str(e))
            self.server.publish_diagnostics(uri, [diag])
            all_symbols, _ = _extract_completion_symbols(source)
            self._doc_cache[uri] = (source, all_symbols)
            return
        except ValueError as e:
            diag = _error_diagnostic(str(e))
            self.server.publish_diagnostics(uri, [diag])
            all_symbols, _ = _extract_completion_symbols(source)
            self._doc_cache[uri] = (source, all_symbols)
            return
        except Exception:
            _logger.debug("Project-level diagnostics failed for %s", uri, exc_info=True)

        # Single-file fallback — also uses geno.check() for consistency
        result = geno.check(source, filename=uri)
        lsp_diags = [_to_lsp_diagnostic(d) for d in result.diagnostics]
        self.server.publish_diagnostics(uri, lsp_diags)

        # Extract user-defined names for completion
        all_symbols, _ = _extract_completion_symbols(source)
        self._doc_cache[uri] = (source, all_symbols)

    # _extract_names is defined at module level for testability

    def did_open(self, params: types.DidOpenTextDocumentParams) -> None:
        uri = params.text_document.uri
        source = params.text_document.text
        version = self._document_lsp_version(params.text_document)
        self._set_open_document_source(uri, source, lsp_version=version)
        self._refresh_document_metadata(uri)
        self._refresh_open_documents(focus_uri=uri)

    def _cancel_debounce(self, uri: str) -> None:
        """Cancel any pending debounce timer for a URI."""
        timer = self._debounce_timers.pop(uri, None)
        if timer is not None:
            timer.cancel()

    def _schedule_debounced_refresh(
        self, uri: str, previous_project_paths: frozenset[str]
    ) -> None:
        """Schedule a debounced diagnostic refresh for a URI."""
        self._cancel_debounce(uri)

        if self._diag_debounce_sec <= 0:
            # No debounce — refresh immediately (useful for tests)
            self._refresh_open_documents(
                focus_uri=uri,
                previous_project_paths=previous_project_paths,
            )
            return

        def _do_refresh() -> None:
            with self._state_lock:
                self._debounce_timers.pop(uri, None)
                self._refresh_open_documents(
                    focus_uri=uri,
                    previous_project_paths=previous_project_paths,
                )

        timer = threading.Timer(self._diag_debounce_sec, _do_refresh)
        timer.daemon = True
        self._debounce_timers[uri] = timer
        timer.start()

    @staticmethod
    def _is_full_document_change(change: Any) -> bool:
        """Return true for full-sync didChange payloads."""
        return getattr(change, "range", None) is None

    def did_change(self, params: types.DidChangeTextDocumentParams) -> None:
        uri = params.text_document.uri
        version = self._document_lsp_version(params.text_document)
        if self._is_stale_document_version(uri, version):
            _logger.debug(
                "Ignoring stale didChange for %s at version %s",
                uri,
                version,
            )
            return

        # Full sync — use the last content change
        if params.content_changes:
            change = params.content_changes[-1]
            if not self._is_full_document_change(change):
                _logger.debug(
                    "Ignoring incremental didChange payload for full-sync document %s",
                    uri,
                )
                return
            source = change.text
            previous_project_paths = self._project_view_for_uri(uri).project_paths
            self._set_open_document_source(uri, source, lsp_version=version)
            self._schedule_debounced_refresh(uri, previous_project_paths)

    def did_save(self, params: types.DidSaveTextDocumentParams) -> None:
        uri = params.text_document.uri
        doc = self.server.workspace.get_text_document(uri)
        previous_project_paths = self._project_view_for_uri(uri).project_paths
        self._set_open_document_source(
            uri,
            doc.source,
            lsp_version=self._document_lsp_version(doc),
        )
        self._refresh_open_documents(
            focus_uri=uri,
            previous_project_paths=previous_project_paths,
        )

    def did_close(self, params: types.DidCloseTextDocumentParams) -> None:
        uri = params.text_document.uri
        self._cancel_debounce(uri)
        previous_project_paths = self._project_view_for_uri(uri).project_paths
        file_path = self._open_doc_paths.get(uri)
        if file_path is not None:
            path_str = str(file_path)
            self._invalidate_project_view_cache_for_path(path_str)
            self._invalidate_symbol_table_cache_for_path(path_str)
        self._open_docs.pop(uri, None)
        self._open_doc_paths.pop(uri, None)
        self._open_doc_versions.pop(uri, None)
        self._open_doc_lsp_versions.pop(uri, None)
        self._doc_cache.pop(uri, None)
        self._project_views.pop(uri, None)
        self.server.publish_diagnostics(uri, [])
        self._refresh_open_documents(previous_project_paths=previous_project_paths)

    def hover(self, params: types.HoverParams) -> types.Hover | None:
        uri = params.text_document.uri
        doc = self.server.workspace.get_text_document(uri)
        line = params.position.line
        char = params.position.character
        project_modules = self._project_view_for_uri(uri).project_modules

        # Get the word under cursor
        lines = doc.source.splitlines()
        if not _has_lsp_line(lines, line):
            return None
        if _position_is_in_comment_or_string(doc.source, line, char):
            return None
        word = _word_at(lines[line], char)
        if not word:
            return None

        result = self._semantic_locations(uri, doc.source, line, char, word=word)
        if result:
            defn, _locs = result
            info = self._semantic_hover_info(uri, word, defn)
            if info:
                return types.Hover(
                    contents=types.MarkupContent(
                        kind=types.MarkupKind.Markdown,
                        value=info,
                    ),
                )

        # Builtin fallback (also covers builtin names not resolved semantically)
        info = _get_type_info(word)
        if info:
            return types.Hover(
                contents=types.MarkupContent(
                    kind=types.MarkupKind.Markdown,
                    value=info,
                ),
            )

        # User-defined function fallback by name when semantic resolution misses.
        sig = _get_func_signature(
            word,
            doc.source,
            project_modules,
            project_source_reader=self._read_project_source,
        )
        if sig:
            label, _params = sig
            return types.Hover(
                contents=types.MarkupContent(
                    kind=types.MarkupKind.Markdown,
                    value=_format_hover_block(label, "function"),
                ),
            )
        return None

    def definition(self, params: types.DefinitionParams) -> types.Location | None:
        uri = params.text_document.uri
        doc = self.server.workspace.get_text_document(uri)
        project_modules = self._project_view_for_uri(uri).project_modules
        line = params.position.line
        char = params.position.character

        lines = doc.source.splitlines()
        if not _has_lsp_line(lines, line):
            return None
        if _position_is_in_comment_or_string(doc.source, line, char):
            return None
        word = _word_at(lines[line], char)
        if not word:
            return None

        result = self._semantic_locations(uri, doc.source, line, char, word=word)
        if result:
            defn, _locs = result
            def_path = self._resolved_path_for_symbol_filename(
                uri, defn.location.filename
            )
            if def_path is None:
                def_path = _uri_to_path(uri)
            def_path = def_path.resolve()
            current_path = _uri_to_path(uri).resolve()
            def_uri = (
                uri
                if def_path == current_path
                else self._project_uri_for_path(def_path)
            )
            def_source = (
                doc.source
                if def_uri == uri
                else self._read_project_source(str(def_path))
            )
            if def_source is not None:
                return types.Location(
                    uri=def_uri,
                    range=_symbol_range_from_source(
                        def_source,
                        defn.name,
                        defn.location.line,
                        defn.location.column,
                    ),
                )

        # Search current document first
        for i, src_line in enumerate(lines):
            m = re.match(rf"(?:func|type)\s+{re.escape(word)}\b", src_line)
            if m:
                return types.Location(
                    uri=uri,
                    range=_symbol_range_from_source(
                        doc.source,
                        word,
                        i + 1,
                        m.start() + 1,
                    ),
                )

        # Search across project modules (only exported symbols)
        for _mod_name, (mod_path, _all, exported) in project_modules.items():
            if word not in exported:
                continue
            try:
                mod_source = self._read_project_source(mod_path)
                if mod_source is None:
                    continue
                mod_lines = mod_source.splitlines()
                for i, src_line in enumerate(mod_lines):
                    m = re.match(
                        rf"(?:export\s+)?(?:func|type)\s+{re.escape(word)}\b", src_line
                    )
                    if m:
                        mod_uri = self._project_uri_for_path(Path(mod_path))
                        return types.Location(
                            uri=mod_uri,
                            range=_symbol_range_from_source(
                                mod_source,
                                word,
                                i + 1,
                                m.start() + 1,
                            ),
                        )
            except Exception:
                _logger.debug("Definition lookup attempt failed", exc_info=True)
                continue
        return None

    def document_highlight(
        self,
        params: types.DocumentHighlightParams,
    ) -> list[types.DocumentHighlight]:
        uri = params.text_document.uri
        doc = self.server.workspace.get_text_document(uri)
        line = params.position.line
        char = params.position.character

        lines = doc.source.splitlines()
        if not _has_lsp_line(lines, line):
            return []
        if _position_is_in_comment_or_string(doc.source, line, char):
            return []
        word = _word_at(lines[line], char)
        if not word:
            return []

        filename = str(_uri_to_path(uri))
        result = self._semantic_locations(uri, doc.source, line, char, word=word)
        if result:
            _defn, locs = result
            return [
                types.DocumentHighlight(
                    range=_symbol_range_from_source(
                        doc.source,
                        word,
                        loc.line,
                        loc.column,
                    ),
                    kind=types.DocumentHighlightKind.Text,
                )
                for loc in locs
                if loc.filename == filename
            ]

        highlights: list[types.DocumentHighlight] = []
        pattern = re.compile(rf"\b{re.escape(word)}\b")
        for line_no, line_text in enumerate(doc.source.splitlines()):
            for match in pattern.finditer(line_text):
                if _is_in_comment_or_string(line_text, match.start()):
                    continue
                highlights.append(
                    types.DocumentHighlight(
                        range=types.Range(
                            start=types.Position(
                                line=line_no,
                                character=match.start(),
                            ),
                            end=types.Position(
                                line=line_no,
                                character=match.end(),
                            ),
                        ),
                        kind=types.DocumentHighlightKind.Text,
                    )
                )
        return highlights

    def document_symbol(
        self,
        params: types.DocumentSymbolParams,
    ) -> list[types.DocumentSymbol]:
        uri = params.text_document.uri
        doc = self.server.workspace.get_text_document(uri)
        table = self._build_symbol_table(uri, doc.source)
        if table is None:
            return []

        filename = str(_uri_to_path(uri))
        symbols: list[types.DocumentSymbol] = []
        for defn in _top_level_symbol_defs(table, filename):
            symbol_range = _symbol_range_from_source(
                doc.source,
                defn.name,
                defn.location.line,
                defn.location.column,
            )
            symbols.append(
                types.DocumentSymbol(
                    name=defn.name,
                    kind=_symbol_kind_for_lsp(defn.kind),
                    range=symbol_range,
                    selection_range=symbol_range,
                )
            )

        return symbols

    def workspace_symbol(
        self,
        params: types.WorkspaceSymbolParams,
    ) -> list[types.SymbolInformation]:
        query = params.query.strip().lower()
        source_overrides = self._source_overrides_from_open_documents()
        symbols: list[types.SymbolInformation] = []

        for path_str in self._workspace_symbol_paths():
            path = Path(path_str)
            source = self._read_project_source(path_str)
            if source is None:
                continue

            open_uri = self._open_uri_for_path(path_str)
            if open_uri is not None:
                table = self._build_symbol_table(open_uri, source)
            else:
                table = _build_symbol_table_for_document(
                    path,
                    source,
                    source_overrides=source_overrides,
                )
            if table is None:
                continue

            module_name = self._module_name_for_path(path_str) or path.stem
            for defn in _top_level_symbol_defs(table, path_str):
                if query and query not in defn.name.lower():
                    continue

                symbol_range = _symbol_range_from_source(
                    source,
                    defn.name,
                    defn.location.line,
                    defn.location.column,
                )
                symbols.append(
                    types.SymbolInformation(
                        name=defn.name,
                        kind=_symbol_kind_for_lsp(defn.kind),
                        location=types.Location(
                            uri=path.as_uri(),
                            range=symbol_range,
                        ),
                        container_name=module_name,
                    )
                )

        return symbols

    def _dot_completions(
        self, uri: str, source: str, line: int, char: int
    ) -> list[types.CompletionItem]:
        """Return completions for dot-access (record fields or module members)."""
        lines = source.splitlines()
        if not _has_lsp_line(lines, line):
            return []
        line_text = lines[line]
        # Scan backwards from cursor to find the dot position
        prefix = line_text[:char]
        dot_pos = -1
        for i in range(len(prefix) - 1, -1, -1):
            ch = prefix[i]
            if ch == ".":
                dot_pos = i
                break
            if not (ch.isalnum() or ch == "_"):
                break
        if dot_pos < 0:
            return []
        end = dot_pos
        start = end - 1
        while start >= 0 and (line_text[start].isalnum() or line_text[start] == "_"):
            start -= 1
        start += 1
        target_name = line_text[start:end]
        if not target_name:
            return []

        items: list[types.CompletionItem] = []

        # Module dot-access: check if target_name is a module alias
        project_view = self._project_view_for_uri(uri)
        for mod_name, (_, _all, exported) in project_view.completion_modules.items():
            if mod_name == target_name or mod_name.split("/")[-1] == target_name:
                for symbol in exported:
                    items.append(
                        types.CompletionItem(
                            label=symbol.name,
                            kind=_completion_kind_for_symbol(symbol.kind),
                            detail=f"(from {mod_name})",
                        )
                    )
                return items

        # Record field dot-access: try to resolve the type of the identifier
        type_name = self._resolve_identifier_type_name(
            uri, source, target_name, line, start
        )
        if type_name is not None:
            field_items = self._fields_for_type_name(uri, source, type_name)
            if field_items:
                return field_items

        return items

    def _resolve_identifier_type_name(
        self, uri: str, source: str, name: str, line: int, col: int
    ) -> str | None:
        """Try to resolve an identifier's type name via the symbol table + source.

        Scopes the search to the enclosing function to avoid returning a type
        from a different function that happens to use the same variable name.
        """
        src_lines = source.splitlines()

        # Find the enclosing function header by scanning backwards from cursor
        func_start = -1
        for i in range(min(line, len(src_lines) - 1), -1, -1):
            if re.match(r"\s*(?:export\s+)?(?:@\w+\([^)]*\)\s+)?func\b", src_lines[i]):
                func_start = i
                break

        # Search from the enclosing function header down to the cursor line
        search_start = max(func_start, 0)
        search_end = min(line + 1, len(src_lines))

        for i in range(search_start, search_end):
            src_line = src_lines[i]
            # Check let/var bindings
            m = re.search(
                rf"\b(?:let|var)\s+{re.escape(name)}\s*:\s*(\w+)",
                src_line,
            )
            if m:
                return m.group(1)
            # Check function parameters (only on the func header line)
            if i == func_start:
                m = re.search(
                    rf"\b{re.escape(name)}\s*:\s*(\w+)",
                    src_line,
                )
                if m and re.match(r".*\bfunc\b", src_line[: m.start()]):
                    return m.group(1)
        return None

    def _type_info_for_name(self, uri: str, source: str, type_name: str):
        """Resolve type metadata from the current document, project modules, or builtins."""
        current_path = _uri_to_path_or_none(uri)
        current_filename = str(current_path) if current_path is not None else uri
        current_info = _extract_type_defs(source, current_filename).get(type_name)
        if current_info is not None:
            return current_info

        if current_path is not None:
            project_view = self._project_view_for_uri(uri)
            try:
                current_path_str = str(current_path.resolve())
                for _mod_name, (
                    path_str,
                    _all,
                    _exported,
                ) in project_view.completion_modules.items():
                    if path_str == current_path_str:
                        continue
                    module_path = Path(path_str)
                    module_uri = module_path.resolve().as_uri()
                    module_source = self._open_docs.get(module_uri)
                    if module_source is None:
                        module_source = module_path.read_text(encoding="utf-8")
                    info = _extract_type_defs(module_source, path_str).get(type_name)
                    if info is not None:
                        return info
            except Exception:
                _logger.debug("Project type lookup failed", exc_info=True)

        try:
            from geno.typechecker import TypeChecker

            return TypeChecker().type_defs.get(type_name)
        except Exception:
            _logger.debug("Builtin type lookup failed", exc_info=True)
            return None

    def _fields_for_type_name(
        self, uri: str, source: str, type_name: str
    ) -> list[types.CompletionItem]:
        """Return field completion items for a type visible from the current document."""
        type_info = self._type_info_for_name(uri, source, type_name)
        if type_info is None:
            return []
        items: list[types.CompletionItem] = []
        seen: set[str] = set()
        for _variant_name, fields in type_info.variants.items():
            for field_name, field_type in fields:
                if field_name not in seen:
                    seen.add(field_name)
                    items.append(
                        types.CompletionItem(
                            label=field_name,
                            kind=types.CompletionItemKind.Field,
                            detail=str(field_type),
                        )
                    )
        return items

    def _match_pattern_completions(
        self, uri: str, source: str, line: int, char: int
    ) -> list[types.CompletionItem]:
        """Return completions for match arms (unmatched variant patterns)."""
        lines = source.splitlines()
        if not _has_lsp_line(lines, line):
            return []

        # Scan backwards to find the enclosing `match <expr> with`
        scrutinee_type_name: str | None = None
        existing_patterns: set[str] = set()
        for i in range(line, -1, -1):
            stripped = lines[i].strip()
            if stripped.startswith("end match"):
                return []  # We're past the match block
            # Collect existing patterns
            arm_match = re.match(r"\|\s*(\w+)", stripped)
            if arm_match:
                existing_patterns.add(arm_match.group(1))
            # Find the match header
            match_header = re.match(r"match\s+(\w+)\s+with", stripped)
            if match_header:
                scrutinee_name = match_header.group(1)
                scrutinee_type_name = self._resolve_identifier_type_name(
                    uri, source, scrutinee_name, i, 0
                )
                break

        if scrutinee_type_name is None:
            return []

        # Get the type's variants and suggest unmatched ones
        try:
            type_info = self._type_info_for_name(uri, source, scrutinee_type_name)
            if type_info is None:
                return []

            items: list[types.CompletionItem] = []
            for variant_name, fields in type_info.variants.items():
                if variant_name in existing_patterns:
                    continue
                if fields:
                    field_str = ", ".join(f"{f[0]}" for f in fields)
                    insert = f"{variant_name}({field_str})"
                else:
                    insert = variant_name
                items.append(
                    types.CompletionItem(
                        label=variant_name,
                        kind=types.CompletionItemKind.EnumMember,
                        detail=f"(variant of {scrutinee_type_name})",
                        insert_text=insert,
                    )
                )
            return items
        except Exception:
            _logger.debug("Match pattern completion failed", exc_info=True)
            return []

    def _effect_completions(self, line_text: str) -> list[types.CompletionItem]:
        """Return effect name completions when cursor is after 'with' in a func signature."""
        from geno.builtin_registry import VALID_EFFECTS

        # Check if the line looks like a function signature with 'with' at the end
        if not re.search(r"\bfunc\b.*\bwith\b\s*\w*$", line_text):
            return []

        # Extract already-listed effects
        with_match = re.search(r"\bwith\s+(.*)", line_text)
        existing: set[str] = set()
        if with_match:
            for part in with_match.group(1).split(","):
                existing.add(part.strip())

        items: list[types.CompletionItem] = []
        for effect in sorted(VALID_EFFECTS):
            if effect not in existing:
                items.append(
                    types.CompletionItem(
                        label=effect,
                        kind=types.CompletionItemKind.Keyword,
                        detail="(effect)",
                    )
                )
        return items

    def completion(self, params: types.CompletionParams) -> types.CompletionList:
        uri = params.text_document.uri
        source = self._open_docs.get(uri, "")
        line = params.position.line
        char = params.position.character
        lines = source.splitlines()
        line_text = lines[line] if _has_lsp_line(lines, line) else ""

        # --- Context-aware completions ---

        # 1. Dot-completion: after "."
        if char > 0 and _has_lsp_line(lines, line):
            prefix_text = line_text[:char]
            if prefix_text.rstrip().endswith(".") or re.search(r"\.\w*$", prefix_text):
                dot_items = self._dot_completions(uri, source, line, char)
                if dot_items:
                    return types.CompletionList(is_incomplete=False, items=dot_items)

        # 2. Effect completion: after "with" in function signature
        if _has_lsp_line(lines, line):
            effect_items = self._effect_completions(line_text[:char])
            if effect_items:
                return types.CompletionList(is_incomplete=False, items=effect_items)

        # 3. Match pattern completion: inside a match block on a "|" line
        if _has_lsp_line(lines, line):
            stripped = line_text.strip()
            if stripped == "|" or stripped.startswith("| "):
                match_items = self._match_pattern_completions(uri, source, line, char)
                if match_items:
                    return types.CompletionList(is_incomplete=False, items=match_items)

        # --- Default flat completions ---
        items: list[types.CompletionItem] = []
        project_view = self._project_view_for_uri(uri)
        completion_modules = project_view.completion_modules

        # Keywords
        for kw in _KEYWORDS:
            items.append(
                types.CompletionItem(
                    label=kw,
                    kind=types.CompletionItemKind.Keyword,
                )
            )

        # Builtins
        for name in _get_builtin_names():
            items.append(
                types.CompletionItem(
                    label=name,
                    kind=types.CompletionItemKind.Function,
                    detail="(builtin)",
                )
            )

        # User-defined names from current document
        symbols = _completion_symbols_for_uri(uri, self._doc_cache, self._open_docs)
        if symbols is not None:
            for symbol in symbols:
                items.append(
                    types.CompletionItem(
                        label=symbol.name,
                        kind=_completion_kind_for_symbol(symbol.kind),
                    )
                )

        # Names from imported modules (only exported symbols)
        seen = {item.label for item in items}
        for mod_name, (_, _all, exported) in completion_modules.items():
            for symbol in exported:
                if symbol.name not in seen:
                    items.append(
                        types.CompletionItem(
                            label=symbol.name,
                            kind=_completion_kind_for_symbol(symbol.kind),
                            detail=f"(from {mod_name})",
                        )
                    )
                    seen.add(symbol.name)

        return types.CompletionList(is_incomplete=False, items=items)

    def signature_help(
        self,
        params: types.SignatureHelpParams,
    ) -> types.SignatureHelp | None:
        uri = params.text_document.uri
        doc = self.server.workspace.get_text_document(uri)
        project_modules = self._project_view_for_uri(uri).project_modules
        line = params.position.line
        char = params.position.character

        lines = doc.source.splitlines()
        if not _has_lsp_line(lines, line):
            return None

        text = lines[line][:char]

        # Walk backwards to find the function name and active parameter
        func_name, active_param, func_name_col = _find_call_context(text)
        if func_name is None:
            return None

        semantic_resolved = False
        sig = None
        if func_name_col is not None:
            semantic_resolved, sig = self._semantic_function_signature(
                uri,
                doc.source,
                line,
                func_name,
                func_name_col,
            )
        if sig is None and not semantic_resolved:
            sig = _get_func_signature(
                func_name,
                doc.source,
                project_modules,
                project_source_reader=self._read_project_source,
            )
        if sig is None:
            return None

        label, param_infos = sig
        return types.SignatureHelp(
            signatures=[
                types.SignatureInformation(
                    label=label,
                    parameters=param_infos,
                )
            ],
            active_signature=0,
            active_parameter=min(active_param, max(len(param_infos) - 1, 0)),
        )

    def _symbol_table_cache_key(
        self,
        uri: str,
    ) -> tuple[str, tuple[frozenset[str], tuple[tuple[str, int], ...]]]:
        """Build a cache key for one document's semantic project state."""
        path_str = str(_uri_to_path(uri).resolve())
        project_paths = self._project_view_for_uri(uri).project_paths or frozenset(
            {path_str}
        )
        return path_str, self._project_view_cache_key(project_paths)

    def _build_symbol_table(self, uri: str, source: str) -> SymbolTable | None:
        """Build a symbol table for the document at uri.

        Parses the source and all project modules, returns the table or
        None on failure.  Results are cached per-URI.
        """
        try:
            cache_key = self._symbol_table_cache_key(uri)
            cached: SymbolTable | None = self._symbol_table_cache.get(cache_key)
            if cached is not None:
                return cached
            table = _build_symbol_table_for_document(
                _uri_to_path(uri),
                source,
                source_overrides=self._source_overrides_from_open_documents(),
            )
            if table is None:
                return None
            self._symbol_table_cache[cache_key] = table
            return table
        except Exception:
            _logger.debug("Symbol table resolution failed", exc_info=True)
            return None

    def _semantic_locations(
        self, uri: str, source: str, line: int, char: int, word: str | None = None
    ) -> tuple | None:
        """Find the symbol at cursor and all its locations via the symbol table."""
        table = self._build_symbol_table(uri, source)
        if not table:
            return None

        file_path = _uri_to_path(uri)
        filename = str(file_path)

        # LSP lines are 0-based, Geno locations are 1-based
        if _position_is_in_comment_or_string(source, line, char):
            return None
        defn = table.symbol_at(filename, line + 1, char + 1, name=word)
        if not defn:
            return None

        return defn, table.all_locations(defn)

    def _semantic_location_target(
        self,
        current_uri: str,
        current_source: str,
        name: str,
        loc: SourceLocation,
    ) -> tuple[str, types.Range] | None:
        """Map one semantic symbol location to an LSP target URI and token range."""
        resolved_path = self._resolved_path_for_symbol_filename(
            current_uri, loc.filename
        )
        if resolved_path is None:
            return None
        resolved_path = resolved_path.resolve()
        current_path = _uri_to_path(current_uri).resolve()

        loc_uri = (
            current_uri
            if resolved_path == current_path
            else self._project_uri_for_path(resolved_path)
        )
        location_source = (
            current_source
            if loc_uri == current_uri
            else self._read_project_source(str(resolved_path))
        )

        if location_source is not None:
            location_range = _symbol_range_from_source(
                location_source,
                name,
                loc.line,
                loc.column,
            )
        else:
            location_range = types.Range(
                start=types.Position(
                    line=loc.line - 1,
                    character=loc.column - 1,
                ),
                end=types.Position(
                    line=loc.line - 1,
                    character=loc.column - 1 + len(name),
                ),
            )
        return loc_uri, location_range

    def rename(self, params: types.RenameParams) -> types.WorkspaceEdit | None:
        uri = params.text_document.uri
        doc = self.server.workspace.get_text_document(uri)
        project_modules = self._project_view_for_uri(uri).project_modules
        line = params.position.line
        char = params.position.character
        new_name = params.new_name

        lines = doc.source.splitlines()
        if not _has_lsp_line(lines, line):
            return None
        if _position_is_in_comment_or_string(doc.source, line, char):
            return None
        old_name = _word_at(lines[line], char)
        if not old_name:
            return None

        # Reject renaming builtins or keywords
        if old_name in _get_builtin_names() or old_name in _KEYWORDS:
            return None

        # Validate new name is a valid identifier
        if not new_name or not re.match(r"^[a-zA-Z_]\w*$", new_name):
            return None

        # Reject if new name is a keyword or builtin
        if new_name in _KEYWORDS or new_name in _get_builtin_names():
            return None

        # Try semantic rename first
        result = self._semantic_locations(uri, doc.source, line, char, word=old_name)
        if result:
            defn, locs = result
            changes: dict[str, list[types.TextEdit]] = {}
            for loc in locs:
                target = self._semantic_location_target(uri, doc.source, defn.name, loc)
                if target is None:
                    continue
                loc_uri, location_range = target
                edit = types.TextEdit(
                    range=location_range,
                    new_text=new_name,
                )
                changes.setdefault(loc_uri, []).append(edit)
            return types.WorkspaceEdit(changes=changes) if changes else None

        # Fallback to text-based rename
        changes = {}
        edits = _find_word_occurrences(doc.source, old_name, new_name)
        if edits:
            changes[uri] = edits

        for _mod_name, (mod_path, _all, _exported) in project_modules.items():
            try:
                mod_file = Path(mod_path)
                mod_source = self._read_project_source(mod_path)
                if mod_source is None:
                    continue
                mod_uri = self._project_uri_for_path(mod_file)
                if mod_uri == uri:
                    continue
                mod_edits = _find_word_occurrences(mod_source, old_name, new_name)
                if mod_edits:
                    changes[mod_uri] = mod_edits
            except Exception:
                _logger.debug("Rename in module failed", exc_info=True)
                continue

        if not changes:
            return None
        return types.WorkspaceEdit(changes=changes)

    def references(
        self,
        params: types.ReferenceParams,
    ) -> list | None:
        uri = params.text_document.uri
        doc = self.server.workspace.get_text_document(uri)
        project_modules = self._project_view_for_uri(uri).project_modules
        line = params.position.line
        char = params.position.character

        lines = doc.source.splitlines()
        if not _has_lsp_line(lines, line):
            return None
        if _position_is_in_comment_or_string(doc.source, line, char):
            return None
        word = _word_at(lines[line], char)
        if not word:
            return None

        # Try semantic references first
        result = self._semantic_locations(uri, doc.source, line, char, word=word)
        if result:
            _defn, locs = result
            semantic_results: list[types.Location] = []
            for loc in locs:
                target = self._semantic_location_target(
                    uri, doc.source, _defn.name, loc
                )
                if target is None:
                    continue
                loc_uri, location_range = target
                semantic_results.append(
                    types.Location(
                        uri=loc_uri,
                        range=location_range,
                    )
                )
            return semantic_results

        # Fallback to text-based references
        results: list[types.Location] = []
        _collect_references(doc.source, word, uri, results)

        for _mod_name, (mod_path, _all, _exported) in project_modules.items():
            try:
                mod_file = Path(mod_path)
                mod_uri = self._project_uri_for_path(mod_file)
                if mod_uri == uri:
                    continue
                mod_source = self._read_project_source(mod_path)
                if mod_source is None:
                    continue
                _collect_references(mod_source, word, mod_uri, results)
            except Exception:
                _logger.debug("Reference search in module failed", exc_info=True)
                continue

        return results if results else None

    def code_action(
        self,
        params: types.CodeActionParams,
    ) -> list[types.CodeAction]:
        uri = params.text_document.uri
        source = self._open_docs.get(uri, "")
        diagnostics = params.context.diagnostics
        actions: list[types.CodeAction] = []

        for diag in diagnostics:
            msg = diag.message

            # Code action: add missing 'end' block
            end_match = re.search(r"[Ee]xpected '(end \w+(?:\s+\w+)?)'", msg)
            inferred_end = (
                _infer_missing_end_text(source) if "Expected 'end'" in msg else None
            )
            if end_match or inferred_end:
                if end_match is not None:
                    end_text = end_match.group(1)
                else:
                    assert inferred_end is not None
                    end_text = inferred_end[0]
                indent = inferred_end[1] if inferred_end else ""
                insert_line = len(source.splitlines())
                actions.append(
                    types.CodeAction(
                        title=f"Add missing '{end_text}'",
                        kind=types.CodeActionKind.QuickFix,
                        diagnostics=[diag],
                        edit=types.WorkspaceEdit(
                            changes={
                                uri: [
                                    types.TextEdit(
                                        range=types.Range(
                                            start=types.Position(
                                                line=insert_line,
                                                character=0,
                                            ),
                                            end=types.Position(
                                                line=insert_line,
                                                character=0,
                                            ),
                                        ),
                                        new_text=f"{indent}{end_text}\n",
                                    )
                                ]
                            }
                        ),
                    )
                )

            # Code action: import module for unknown name
            unknown_name = _extract_unknown_symbol_name(msg)
            if unknown_name is not None:
                project_view = self._project_view_for_uri(uri)
                for mod_name, (
                    _,
                    _all,
                    exported,
                ) in project_view.completion_modules.items():
                    for sym in exported:
                        if sym.name == unknown_name:
                            actions.append(
                                types.CodeAction(
                                    title=f"Import '{mod_name}'",
                                    kind=types.CodeActionKind.QuickFix,
                                    diagnostics=[diag],
                                    edit=types.WorkspaceEdit(
                                        changes={
                                            uri: [
                                                types.TextEdit(
                                                    range=types.Range(
                                                        start=types.Position(
                                                            line=0,
                                                            character=0,
                                                        ),
                                                        end=types.Position(
                                                            line=0,
                                                            character=0,
                                                        ),
                                                    ),
                                                    new_text=f"import {mod_name}\n",
                                                )
                                            ]
                                        }
                                    ),
                                )
                            )
                            break

        return actions


def create_server(*, diag_debounce_sec: float = _DIAG_DEBOUNCE_SEC) -> LanguageServer:
    """Create and configure the Geno language server."""
    wrapper = GenoLanguageServer(diag_debounce_sec=diag_debounce_sec)
    server = wrapper.to_pygls_server()
    server._geno_language_server = wrapper  # type: ignore[attr-defined]
    return server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _word_at(line: str, char: int) -> str | None:
    """Extract the word at the given character position in a line."""
    if char < 0:
        return None
    if char >= len(line):
        char = max(len(line) - 1, 0)
    if not line:
        return None

    # Find word boundaries
    start = char
    while start > 0 and (line[start - 1].isalnum() or line[start - 1] == "_"):
        start -= 1
    end = char
    while end < len(line) and (line[end].isalnum() or line[end] == "_"):
        end += 1

    word = line[start:end]
    return word if word else None


_cached_builtin_types: dict | None = None


def _get_type_info(name: str) -> str | None:
    """Get type information for a builtin or keyword."""
    global _cached_builtin_types
    if _cached_builtin_types is None:
        from geno.typechecker import TypeChecker

        _cached_builtin_types = TypeChecker().builtin_types
    if name in _cached_builtin_types:
        func_type, _params = _cached_builtin_types[name]
        return f"```\n{name}: {func_type}\n```\n(builtin)"
    return None


def _find_call_context(text: str) -> tuple:
    """Find the function being called and active parameter index.

    Walks backwards through *text* (the line up to the cursor) counting
    parentheses and commas to determine the call site.

    Returns (func_name, active_param_index, name_start_column) or
    (None, 0, None).
    """
    depth = 0
    commas = 0
    i = len(text) - 1
    while i >= 0:
        ch = text[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                # Found the opening paren — extract function name
                j = i - 1
                while j >= 0 and text[j] == " ":
                    j -= 1
                end = j + 1
                while j >= 0 and (text[j].isalnum() or text[j] == "_"):
                    j -= 1
                name = text[j + 1 : end]
                if name:
                    return name, commas, j + 1
                return None, 0, None
            depth -= 1
        elif ch == "," and depth == 0:
            commas += 1
        i -= 1
    return None, 0, None


def _get_func_signature(
    name: str,
    source: str,
    project_modules: dict,
    project_source_reader: Callable[[str], str | None] | None = None,
) -> tuple | None:
    """Get signature info for a function (builtin or user-defined).

    Returns (label_string, [ParameterInformation, ...]) or None.
    """
    # Check builtins first
    global _cached_builtin_types
    if _cached_builtin_types is None:
        from geno.typechecker import TypeChecker

        _cached_builtin_types = TypeChecker().builtin_types

    if name in _cached_builtin_types:
        func_type, param_names = _cached_builtin_types[name]
        param_types = func_type.param_types
        ret_type = func_type.return_type
        parts = []
        param_infos = []
        for pname, ptype in zip(param_names, param_types):
            part = f"{pname}: {ptype}"
            parts.append(part)
            param_infos.append(_parameter_information(part))
        label = f"{name}({', '.join(parts)}) -> {ret_type}"
        return label, param_infos

    # Check user-defined functions in current document
    sig = _extract_user_func_sig(name, source)
    if sig:
        return sig

    # Check project modules (only exported symbols)
    for _mod_name, (mod_path, _all, exported) in project_modules.items():
        if name not in exported:
            continue
        try:
            mod_source = (
                project_source_reader(mod_path)
                if project_source_reader is not None
                else Path(mod_path).read_text(encoding="utf-8")
            )
            if mod_source is None:
                continue
            sig = _extract_user_func_sig(name, mod_source)
            if sig:
                return sig
        except Exception:
            _logger.debug("Signature lookup in module failed", exc_info=True)
            continue

    return None


def _extract_user_func_sig(name: str, source: str) -> tuple | None:
    """Extract function signature from Geno source via regex."""
    # Match: [export] [@annotation(...)] [async] func name(params) -> RetType [with effects]
    pattern = (
        rf"^\s*(?:export\s+)?(?:@\w+\([^)]*\)\s+)?(?:async\s+)?func\s+{re.escape(name)}"
        r"\s*\(([^)]*)\)"
        r"\s*(?:->\s*(\S+))?"
        r"[ \t]*(?:with[ \t]+([A-Za-z_]\w*(?:[ \t]*,[ \t]*[A-Za-z_]\w*)*))?"
    )
    m = re.search(pattern, source, re.MULTILINE)
    if not m:
        return None

    params_str = m.group(1).strip()
    ret_type = m.group(2) or "Unit"
    effects_str = m.group(3)

    suffix = f" -> {ret_type}"
    if effects_str:
        effects = ", ".join(e.strip() for e in effects_str.split(",") if e.strip())
        suffix += f" with {effects}"

    if not params_str:
        label = f"{name}(){suffix}"
        return label, []

    param_infos = []
    for param in params_str.split(","):
        param = param.strip()
        if param:
            param_infos.append(_parameter_information(param))

    label = f"{name}({params_str}){suffix}"
    return label, param_infos


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _is_in_comment_or_string(line: str, start: int) -> bool:
    """Check if position *start* in *line* falls inside a comment or string."""
    inside, _state = _scan_line_context(line, start)
    return inside


def _position_is_in_comment_or_string(source: str, line: int, char: int) -> bool:
    """Check if a document position falls inside a comment or string."""
    in_block_comment = False
    in_triple_string = False
    lines = source.splitlines()
    for line_no, line_text in enumerate(lines):
        start = char if line_no == line else len(line_text)
        inside, (in_block_comment, in_triple_string) = _scan_line_context(
            line_text,
            start,
            in_block_comment=in_block_comment,
            in_triple_string=in_triple_string,
        )
        if line_no == line:
            return inside
    return False


def _scan_line_context(
    line: str,
    start: int,
    *,
    in_block_comment: bool = False,
    in_triple_string: bool = False,
) -> tuple[bool, tuple[bool, bool]]:
    """Scan one line and report whether *start* is inside comment/string context.

    Returns ``(inside, (in_block_comment_after, in_triple_string_after))`` so
    callers can carry multi-line lexical state across source lines.
    """
    in_str: str | None = None
    inside_at_start: bool | None = None
    i = 0
    while i < len(line) and i < start:
        if in_block_comment:
            if i + 1 < len(line) and line[i] == "*" and line[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_triple_string:
            if i + 2 < len(line) and line[i : i + 3] == '"""':
                in_triple_string = False
                i += 3
                continue
            i += 1
            continue

        ch = line[i]
        if in_str is not None:
            if ch == "\\" and i + 1 < len(line):
                i += 2  # skip escaped char
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue

        if ch == "/" and i + 1 < len(line):
            next_ch = line[i + 1]
            if next_ch == "/":
                return True, (False, False)
            if next_ch == "*":
                in_block_comment = True
                i += 2
                continue
        if line[i : i + 3] == '"""':
            in_triple_string = True
            i += 3
            continue
        if ch == '"':
            in_str = '"'
        elif ch == "'":
            in_str = "'"
        i += 1

    if inside_at_start is None:
        inside_at_start = in_block_comment or in_triple_string or in_str is not None

    scan_index = i
    while scan_index < len(line):
        if in_block_comment:
            if (
                scan_index + 1 < len(line)
                and line[scan_index] == "*"
                and line[scan_index + 1] == "/"
            ):
                in_block_comment = False
                scan_index += 2
                continue
            scan_index += 1
            continue

        if in_triple_string:
            if line[scan_index : scan_index + 3] == '"""':
                in_triple_string = False
                scan_index += 3
                continue
            scan_index += 1
            continue

        ch = line[scan_index]
        if in_str is not None:
            if ch == "\\" and scan_index + 1 < len(line):
                scan_index += 2
                continue
            if ch == in_str:
                in_str = None
            scan_index += 1
            continue

        if ch == "/" and scan_index + 1 < len(line):
            next_ch = line[scan_index + 1]
            if next_ch == "/":
                break
            if next_ch == "*":
                in_block_comment = True
                scan_index += 2
                continue
        if line[scan_index : scan_index + 3] == '"""':
            in_triple_string = True
            scan_index += 3
            continue
        if ch == '"':
            in_str = '"'
        elif ch == "'":
            in_str = "'"
        scan_index += 1

    return inside_at_start, (in_block_comment, in_triple_string)


def _collect_references(source: str, name: str, uri: str, results: list) -> None:
    """Find all whole-word occurrences of name in source and append Locations.

    Skips matches inside comments and string literals.
    """
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    in_block_comment = False
    in_triple_string = False
    for line_no, line_text in enumerate(source.splitlines()):
        for m in pattern.finditer(line_text):
            in_ignored, _state = _scan_line_context(
                line_text,
                m.start(),
                in_block_comment=in_block_comment,
                in_triple_string=in_triple_string,
            )
            if in_ignored:
                continue
            results.append(
                types.Location(
                    uri=uri,
                    range=types.Range(
                        start=types.Position(line=line_no, character=m.start()),
                        end=types.Position(line=line_no, character=m.end()),
                    ),
                )
            )
        _inside, (in_block_comment, in_triple_string) = _scan_line_context(
            line_text,
            len(line_text),
            in_block_comment=in_block_comment,
            in_triple_string=in_triple_string,
        )


def _find_word_occurrences(source: str, old_name: str, new_name: str) -> list:
    """Find all whole-word occurrences of old_name in source and return TextEdits.

    Skips matches inside comments and string literals.
    """
    edits = []
    pattern = re.compile(rf"\b{re.escape(old_name)}\b")
    in_block_comment = False
    in_triple_string = False
    for line_no, line_text in enumerate(source.splitlines()):
        for m in pattern.finditer(line_text):
            in_ignored, _state = _scan_line_context(
                line_text,
                m.start(),
                in_block_comment=in_block_comment,
                in_triple_string=in_triple_string,
            )
            if in_ignored:
                continue
            edits.append(
                types.TextEdit(
                    range=types.Range(
                        start=types.Position(line=line_no, character=m.start()),
                        end=types.Position(line=line_no, character=m.end()),
                    ),
                    new_text=new_name,
                )
            )
        _inside, (in_block_comment, in_triple_string) = _scan_line_context(
            line_text,
            len(line_text),
            in_block_comment=in_block_comment,
            in_triple_string=in_triple_string,
        )
    return edits


_LSP_LOG_HANDLER_NAME = "geno-lsp-stderr"


def _configure_lsp_logging() -> None:
    """Route the geno logger tree to stderr for the ``geno lsp`` entry point.

    Internal LSP failures are logged (some at DEBUG with tracebacks), but the
    entry point never configured logging, so Python's lastResort handler
    dropped everything below WARNING and there was no way to enable it (M-14).
    Configure a stderr handler — never stdout, which carries the LSP protocol
    over stdio — at a level controlled by GENO_LSP_LOG_LEVEL (default WARNING;
    set to DEBUG to surface the internal-failure tracebacks).
    """
    import sys

    level_name = os.environ.get("GENO_LSP_LOG_LEVEL", "WARNING").strip().upper()
    level = getattr(logging, level_name, logging.WARNING)
    geno_logger = logging.getLogger("geno")
    if not any(h.get_name() == _LSP_LOG_HANDLER_NAME for h in geno_logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.set_name(_LSP_LOG_HANDLER_NAME)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        geno_logger.addHandler(handler)
    geno_logger.setLevel(level)


def start_server(tcp: bool = False, port: int = 2087) -> None:
    """Start the LSP server."""
    _configure_lsp_logging()
    srv = create_server()
    if tcp:
        srv.start_tcp("127.0.0.1", port)
    else:
        srv.start_io()
