"""Completion-oriented helpers for the Geno language server."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from geno.builtin_registry import all_builtin_names

if TYPE_CHECKING:
    from geno.types import TypeDefInfo

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompletionSymbol:
    """One top-level symbol exposed through LSP completion."""

    name: str
    kind: str


def get_builtin_names() -> list[str]:
    """Return sorted list of all builtin function names."""
    return cast(list[str], all_builtin_names())


def extract_names(source: str) -> tuple[list[str], list[str]]:
    """Extract function and type names from source for completion.

    Returns (all_names, exported_names). If no ``export`` keyword appears,
    exported_names == all_names for backward-compatible public visibility.
    """
    all_names: list[str] = []
    exported_names: list[str] = []
    has_exports = False
    # Keep this alternation in lockstep with the top-level-definition keywords
    # accepted by ``_parse_definition``.
    for match in re.finditer(
        r"^(?:(export)\s+)?(?:@\w+\([^)]*\)\s+)?(?:func|type|trait)\s+(\w+)",
        source,
        re.MULTILINE,
    ):
        name = match.group(2)
        all_names.append(name)
        if match.group(1):
            has_exports = True
            exported_names.append(name)
    if not has_exports:
        exported_names = list(all_names)
    return all_names, exported_names


def extract_completion_symbols(
    source: str,
) -> tuple[list[CompletionSymbol], list[CompletionSymbol]]:
    """Extract top-level completion symbols with kinds and export visibility."""
    from geno.ast_nodes import FunctionDef, TraitDef, TypeAlias, TypeDef
    from geno.lexer import Lexer
    from geno.parser import Parser
    from geno.parser_base import ParseError as _ParseError
    from geno.parser_base import ParseErrors as _ParseErrors

    def _append_symbol(
        symbols: list[CompletionSymbol],
        seen: set[tuple[str, str]],
        name: str,
        kind: str,
    ) -> None:
        key = (name, kind)
        if key in seen:
            return
        seen.add(key)
        symbols.append(CompletionSymbol(name=name, kind=kind))

    def _symbols_from_defs(
        defs: list,
    ) -> tuple[list[CompletionSymbol], list[CompletionSymbol]]:
        has_explicit_exports = any(
            isinstance(defn, (FunctionDef, TypeDef, TraitDef, TypeAlias))
            and bool(getattr(defn, "exported", False))
            for defn in defs
        )

        all_symbols: list[CompletionSymbol] = []
        exported_symbols: list[CompletionSymbol] = []
        all_seen: set[tuple[str, str]] = set()
        exported_seen: set[tuple[str, str]] = set()

        for defn in defs:
            exported = bool(getattr(defn, "exported", False))
            if isinstance(defn, FunctionDef):
                _append_symbol(all_symbols, all_seen, defn.name, "function")
                if not has_explicit_exports or exported:
                    _append_symbol(
                        exported_symbols,
                        exported_seen,
                        defn.name,
                        "function",
                    )
            elif isinstance(defn, TypeAlias):
                _append_symbol(all_symbols, all_seen, defn.name, "type")
                if not has_explicit_exports or exported:
                    _append_symbol(exported_symbols, exported_seen, defn.name, "type")
            elif isinstance(defn, TypeDef):
                _append_symbol(all_symbols, all_seen, defn.name, "type")
                for variant in defn.variants:
                    _append_symbol(
                        all_symbols,
                        all_seen,
                        variant.name,
                        "constructor",
                    )
                if not has_explicit_exports or exported:
                    _append_symbol(exported_symbols, exported_seen, defn.name, "type")
                    for variant in defn.variants:
                        _append_symbol(
                            exported_symbols,
                            exported_seen,
                            variant.name,
                            "constructor",
                        )
            elif isinstance(defn, TraitDef):
                _append_symbol(all_symbols, all_seen, defn.name, "trait")
                if not has_explicit_exports or exported:
                    _append_symbol(
                        exported_symbols,
                        exported_seen,
                        defn.name,
                        "trait",
                    )

        return all_symbols, exported_symbols

    try:
        tokens = Lexer(source, "<completion>").tokenize()
        program = Parser(tokens).parse_program()
        return _symbols_from_defs(program.definitions)
    except (_ParseError, _ParseErrors) as e:
        partial = getattr(e, "partial_program", None)
        if partial is not None and hasattr(partial, "definitions"):
            _logger.debug(
                "Using partial parse results for completion (%d definitions recovered)",
                len(partial.definitions),
            )
            return _symbols_from_defs(partial.definitions)
        _logger.debug(
            "Parse failed with no partial program, falling back to regex",
            exc_info=True,
        )
    except Exception:
        _logger.debug(
            "Semantic completion extraction failed, falling back to regex",
            exc_info=True,
        )

    all_names: list[CompletionSymbol] = []
    exported_names: list[CompletionSymbol] = []
    has_exports = False
    for match in re.finditer(
        r"^(?:(export)\s+)?(?:@\w+\([^)]*\)\s+)?(func|type|trait)\s+(\w+)",
        source,
        re.MULTILINE,
    ):
        export_marker, defn_kind, name = match.groups()
        kind = (
            "function"
            if defn_kind == "func"
            else "trait"
            if defn_kind == "trait"
            else "type"
        )
        all_names.append(CompletionSymbol(name=name, kind=kind))
        if export_marker:
            has_exports = True
            exported_names.append(CompletionSymbol(name=name, kind=kind))
    if not has_exports:
        exported_names = list(all_names)
    return all_names, exported_names


def extract_type_defs(
    source: str,
    filename: str = "<completion>",
) -> dict[str, TypeDefInfo]:
    """Extract user-defined type metadata from source, using partial recovery."""
    from geno.ast_nodes import TypeDef
    from geno.lexer import Lexer
    from geno.parser import Parser
    from geno.parser_base import ParseError as _ParseError
    from geno.parser_base import ParseErrors as _ParseErrors
    from geno.types import TypeDefInfo

    definitions = None
    try:
        tokens = Lexer(source, filename).tokenize()
        program = Parser(tokens).parse_program()
        definitions = program.definitions
    except (_ParseError, _ParseErrors) as e:
        partial = getattr(e, "partial_program", None)
        if partial is not None and hasattr(partial, "definitions"):
            definitions = partial.definitions
    except Exception:
        _logger.debug("Type definition extraction failed", exc_info=True)
        return {}

    if definitions is None:
        return {}

    infos: dict[str, TypeDefInfo] = {}
    for defn in definitions:
        if not isinstance(defn, TypeDef):
            continue
        infos[defn.name] = TypeDefInfo(
            name=defn.name,
            type_params=list(defn.type_params),
            variants=cast(
                dict[str, list[tuple[str, Any]]],
                {variant.name: list(variant.fields) for variant in defn.variants},
            ),
        )
    return infos
