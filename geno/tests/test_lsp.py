"""
Tests for the Geno LSP server
==============================
"""

import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

pygls = pytest.importorskip("pygls")

from lsprotocol import types  # noqa: E402
from pygls.workspace import Workspace  # noqa: E402

import geno.lsp_server as lsp_server  # noqa: E402
from geno.ast_nodes import FunctionDef, Program  # noqa: E402
from geno.diagnostics import Diagnostic, ErrorCode, Severity  # noqa: E402
from geno.lsp_server import (  # noqa: E402
    _KEYWORDS,
    _build_symbol_table_for_document,
    _extract_completion_symbols,
    _extract_names,
    _get_builtin_names,
    _get_type_info,
    _load_project_module_index,
    _to_lsp_diagnostic,
    _uri_to_path,
    _word_at,
    create_server,
)
from geno.symbol_table import SymbolTable, build_symbol_table  # noqa: E402
from geno.tokens import SourceLocation  # noqa: E402


def _location_key(location: types.Location) -> tuple[str, int, int, int, int]:
    """Normalize an LSP location for equality checks across URI spellings."""
    path = str(_uri_to_path(location.uri).resolve())
    return (
        path,
        location.range.start.line,
        location.range.start.character,
        location.range.end.line,
        location.range.end.character,
    )


def _highlight_key(
    uri: str,
    highlight: types.DocumentHighlight,
) -> tuple[str, int, int, int, int]:
    """Normalize a highlight to the same shape used by location comparisons."""
    path = str(_uri_to_path(uri).resolve())
    return (
        path,
        highlight.range.start.line,
        highlight.range.start.character,
        highlight.range.end.line,
        highlight.range.end.character,
    )


class TestToLspDiagnostic:
    def test_error_maps_to_severity_1(self):
        diag = Diagnostic(
            code=ErrorCode.TYPE_MISMATCH,
            message="expected Int, got String",
            severity=Severity.ERROR,
            location=SourceLocation(line=5, column=3, filename="test.geno"),
        )
        lsp_diag = _to_lsp_diagnostic(diag)
        assert lsp_diag.severity.value == 1  # Error
        assert lsp_diag.range.start.line == 4  # 0-indexed
        assert lsp_diag.range.start.character == 2  # 0-indexed
        assert lsp_diag.message == "expected Int, got String"
        assert lsp_diag.source == "geno"

    def test_warning_maps_to_severity_2(self):
        diag = Diagnostic(
            code=ErrorCode.TYPE_MISMATCH,
            message="unused variable",
            severity=Severity.WARNING,
            location=SourceLocation(line=1, column=1),
        )
        lsp_diag = _to_lsp_diagnostic(diag)
        assert lsp_diag.severity.value == 2  # Warning

    def test_info_maps_to_severity_3(self):
        diag = Diagnostic(
            code=ErrorCode.TYPE_MISMATCH,
            message="note",
            severity=Severity.INFO,
            location=SourceLocation(line=1, column=1),
        )
        lsp_diag = _to_lsp_diagnostic(diag)
        assert lsp_diag.severity.value == 3  # Information

    def test_no_location_uses_line_zero(self):
        diag = Diagnostic(
            code=ErrorCode.TYPE_MISMATCH,
            message="global error",
            severity=Severity.ERROR,
        )
        lsp_diag = _to_lsp_diagnostic(diag)
        assert lsp_diag.range.start.line == 0
        assert lsp_diag.range.start.character == 0


class TestWordAt:
    def test_simple_word(self):
        assert _word_at("  let x = 5", 6) == "x"

    def test_function_name(self):
        assert _word_at("func greet(name: String)", 5) == "greet"

    def test_at_start(self):
        assert _word_at("hello world", 0) == "hello"

    def test_at_end(self):
        assert _word_at("hello", 4) == "hello"

    def test_empty_line(self):
        assert _word_at("", 0) is None

    def test_on_space(self):
        # When cursor is on space between words, may pick up adjacent word
        result = _word_at("a b", 1)
        assert result in (None, "", "a", "b")

    def test_underscore_in_word(self):
        assert _word_at("my_func()", 3) == "my_func"

    def test_negative_character_returns_none(self):
        assert _word_at("value", -1) is None


class TestVirtualDocuments:
    def test_untitled_document_handlers_do_not_crash(self):
        source = "func main() -> Int\n  let value: Int = 1\n  return value\nend func\n"
        uri = "untitled:Untitled-1"
        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        completion = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)
        signature_help = server.lsp._get_handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=uri,
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        identifier = types.TextDocumentIdentifier(uri=uri)
        assert (
            hover(
                types.HoverParams(
                    text_document=identifier,
                    position=types.Position(line=1, character=8),
                )
            )
            is None
        )
        assert (
            definition(
                types.DefinitionParams(
                    text_document=identifier,
                    position=types.Position(line=1, character=8),
                )
            )
            is None
        )
        assert (
            signature_help(
                types.SignatureHelpParams(
                    text_document=identifier,
                    position=types.Position(line=99, character=0),
                )
            )
            is None
        )
        completion_result = completion(
            types.CompletionParams(
                text_document=identifier,
                position=types.Position(line=99, character=0),
            )
        )
        assert completion_result.items


class TestGetTypeInfo:
    def test_builtin_function(self):
        info = _get_type_info("length")
        assert info is not None
        assert "length" in info
        assert "builtin" in info

    def test_unknown_name(self):
        info = _get_type_info("nonexistent_function_xyz")
        assert info is None


class TestGetBuiltinNames:
    def test_includes_common_builtins(self):
        names = _get_builtin_names()
        assert "length" in names
        assert "to_string" in names
        assert "csv_parse" in names

    def test_includes_capability_gated(self):
        names = _get_builtin_names()
        assert "exec" in names
        assert "http_fetch" in names


class TestKeywords:
    def test_keywords_list(self):
        assert "func" in _KEYWORDS
        assert "return" in _KEYWORDS
        assert "match" in _KEYWORDS
        assert "async" in _KEYWORDS


class TestServerCreation:
    def test_create_server_returns_language_server(self):
        server = create_server(diag_debounce_sec=0)
        assert server is not None
        assert server.name == "geno-lsp"


class TestProjectIndexing:
    """Tests for project-aware LSP features."""

    def test_extract_names_finds_funcs_and_types(self):
        """_extract_names picks up both func and type definitions."""
        source = (
            "type Color = Red | Green | Blue\n"
            "func greet(name: String) -> String\n"
            '  return "hi"\n'
            "end func\n"
        )
        all_names, exported = _extract_names(source)
        assert "Color" in all_names
        assert "greet" in all_names
        # No export keyword → all names are exported
        assert exported == all_names

    def test_extract_names_picks_up_traits(self):
        """Regression for #663 / F-0023.  ``_extract_names`` used to
        omit ``trait`` definitions from its pre-scan, so LSP
        completion silently dropped user-defined traits."""
        source = (
            "trait Describable\n"
            "    func describe(self: Self) -> String\n"
            "end trait\n"
            "\n"
            "func main() -> Int\n"
            "    return 0\n"
            "end func\n"
        )
        all_names, _exported = _extract_names(source)
        assert "Describable" in all_names
        assert "main" in all_names

        symbols, exported_symbols = _extract_completion_symbols(source)
        assert ("Describable", "trait") in {
            (symbol.name, symbol.kind) for symbol in symbols
        }
        assert ("Describable", "trait") in {
            (symbol.name, symbol.kind) for symbol in exported_symbols
        }

    def test_project_modules_populated(self, tmp_path):
        """Project indexing populates module data for multi-file projects."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        (tmp_path / "Utils.geno").write_text(
            "func helper(x: Int) -> Int\n  example 1 -> 2\n  return x + 1\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Utils\n"
            '@untested("integration")\n'
            "func main() -> Int\n"
            "  return helper(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        assert len(pg.files) == 2

        dg = DependencyGraph.resolve(pg)
        assert "Main" in dg.sorted_modules
        assert "Utils" in dg.sorted_modules

    def test_cross_module_definition_search(self, tmp_path):
        """Go-to-definition regex finds definitions across module files."""
        import re

        (tmp_path / "Utils.geno").write_text(
            "func helper(x: Int) -> Int\n  example 1 -> 2\n  return x + 1\nend func\n"
        )
        source = (tmp_path / "Utils.geno").read_text()
        lines = source.splitlines()

        word = "helper"
        found = False
        for i, src_line in enumerate(lines):
            m = re.match(rf"(?:func|type)\s+{re.escape(word)}\b", src_line)
            if m:
                found = True
                assert i == 0
                break
        assert found

    def test_cross_module_completion_names(self, tmp_path):
        """Completion includes names from imported modules."""
        # Simulate what the LSP server does for project completion
        project_modules: dict[str, tuple[str, list[str], list[str]]] = {}

        (tmp_path / "Utils.geno").write_text(
            "func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
            "type Color = Red | Green\n"
        )
        source = (tmp_path / "Utils.geno").read_text()
        all_names, exported = _extract_names(source)
        project_modules["Utils"] = (str(tmp_path / "Utils.geno"), all_names, exported)

        # Verify names from Utils are found
        visible = []
        for _mod_name, (_, _all, exp) in project_modules.items():
            visible.extend(exp)
        assert "helper" in visible
        assert "Color" in visible

    def test_index_helper_resolves_direct_file_sibling_imports(self, tmp_path):
        """Direct file indexing includes sibling imports without geno.toml."""
        (tmp_path / "Utils.geno").write_text(
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(
            "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        )

        modules, path_to_module = _load_project_module_index(main_file)

        assert set(modules) == {"Main", "Utils"}
        assert str(main_file) in path_to_module
        assert str(tmp_path / "Utils.geno") in path_to_module
        assert "helper" in modules["Utils"][2]

    def test_index_helper_prefers_unsaved_imported_module_source(self, tmp_path):
        """Project indexing uses in-memory source for imported open buffers."""
        utils_file = tmp_path / "Utils.geno"
        utils_file.write_text(
            "export func old_api(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(
            "import Utils\nfunc main() -> Int\n  return new_api(1)\nend func\n"
        )
        unsaved_utils = (
            "export func new_api(x: Int) -> Int\n"
            "  example 1 -> 3\n"
            "  return x + 2\n"
            "end func\n"
        )

        modules, _ = _load_project_module_index(
            main_file,
            source_overrides={utils_file: unsaved_utils},
        )

        assert "new_api" in modules["Utils"][2]
        assert "old_api" not in modules["Utils"][2]

    def test_symbol_table_helper_uses_current_file_not_entrypoint(self, tmp_path):
        """Multi-file symbol tables are built from the requested file."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        utils_file = tmp_path / "Utils.geno"
        utils_source = (
            "func helper(x: Int) -> Int\n  example 1 -> 2\n  return x + 1\nend func\n"
        )
        utils_file.write_text(utils_source)
        (tmp_path / "Main.geno").write_text(
            "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        )

        table = _build_symbol_table_for_document(utils_file, utils_source)

        assert table is not None
        helper_defs = [
            d
            for d in table.definitions
            if d.name == "helper" and d.location.filename == str(utils_file)
        ]
        assert len(helper_defs) == 1


class TestDependencyPrivateModuleVisibility:
    """Dependency siblings are importable only from inside their package."""

    def test_private_modules_are_scoped_for_completion_actions_and_navigation(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App"]\n\n'
            "[dependencies.alpha]\n"
            'git = "https://example.com/alpha.git"\n'
            "[dependencies.alt]\n"
            'git = "https://example.com/alt.git"\n'
        )
        app_file = tmp_path / "App.geno"
        app_source = (
            "import Alpha\nfunc main() -> Int\n  return alpha_value()\nend func\n"
        )
        app_file.write_text(app_source)

        alpha_dir = tmp_path / "geno_modules" / "alpha"
        alpha_dir.mkdir(parents=True)
        alpha_file = alpha_dir / "Alpha.geno"
        alpha_source = (
            "import Leaf\n"
            "export func alpha_value() -> Int\n"
            "  example () -> 1\n"
            "  return leaf_value()\n"
            "end func\n"
        )
        alpha_file.write_text(alpha_source)
        leaf_file = alpha_dir / "Leaf.geno"
        leaf_file.write_text(
            "export func leaf_value() -> Int\n  example () -> 1\n  return 1\nend func\n"
        )
        private_alt_file = alpha_dir / "Alt.geno"
        private_alt_file.write_text(
            "export func alt_value() -> Int\n  example () -> 2\n  return 2\nend func\n"
        )
        public_alt_file = tmp_path / "geno_modules" / "alt" / "Alt.geno"
        public_alt_file.parent.mkdir(parents=True)
        public_alt_file.write_text(
            "export func public_alt_value() -> Int\n"
            "  example () -> 3\n"
            "  return 3\n"
            "end func\n"
        )

        app_modules, _ = _load_project_module_index(app_file)
        alpha_modules, _ = _load_project_module_index(alpha_file)

        assert set(app_modules) == {"App", "Alpha", "Alt"}
        assert "Leaf" not in app_modules
        assert Path(app_modules["Alt"][0]) == public_alt_file
        assert "App" not in alpha_modules
        assert {"Alpha", "Leaf", "Alt"} <= set(alpha_modules)
        assert Path(alpha_modules["Alt"][0]) == private_alt_file

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None, sync_kind=types.TextDocumentSyncKind.Full
        )
        monkeypatch.setattr(server, "publish_diagnostics", lambda _u, _d: None)
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        completion = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        code_action = server.lsp._get_handler(types.TEXT_DOCUMENT_CODE_ACTION)

        for path, source in ((app_file, app_source), (alpha_file, alpha_source)):
            did_open(
                types.DidOpenTextDocumentParams(
                    text_document=types.TextDocumentItem(
                        uri=path.as_uri(),
                        language_id="geno",
                        version=1,
                        text=source,
                    )
                )
            )

        def completion_labels(path: Path) -> set[str]:
            result = completion(
                types.CompletionParams(
                    text_document=types.TextDocumentIdentifier(uri=path.as_uri()),
                    position=types.Position(line=1, character=0),
                )
            )
            return {item.label for item in result.items}

        app_labels = completion_labels(app_file)
        alpha_labels = completion_labels(alpha_file)
        assert "alpha_value" in app_labels
        assert "leaf_value" not in app_labels
        assert "alt_value" not in app_labels
        assert "public_alt_value" in app_labels
        assert {"leaf_value", "alt_value"} <= alpha_labels
        assert "public_alt_value" not in alpha_labels

        leaf_definition = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=alpha_file.as_uri()),
                position=types.Position(line=3, character=11),
            )
        )
        assert leaf_definition is not None
        assert _uri_to_path(leaf_definition.uri).resolve() == leaf_file.resolve()

        unknown_alt = types.Diagnostic(
            range=types.Range(
                start=types.Position(line=1, character=9),
                end=types.Position(line=1, character=18),
            ),
            severity=types.DiagnosticSeverity.Error,
            source="geno",
            message="Undefined variable: alt_value",
        )

        def action_titles(path: Path) -> set[str]:
            actions = code_action(
                types.CodeActionParams(
                    text_document=types.TextDocumentIdentifier(uri=path.as_uri()),
                    range=unknown_alt.range,
                    context=types.CodeActionContext(diagnostics=[unknown_alt]),
                )
            )
            return {action.title for action in actions}

        assert "Import 'Alt'" not in action_titles(app_file)
        assert "Import 'Alt'" in action_titles(alpha_file)


class TestDocumentSymbols:
    """Tests for textDocument/documentSymbol."""

    def test_document_symbols_include_top_level_defs(self, tmp_path):
        source = (
            "trait Describable\n"
            "  func describe(self: Self) -> String\n"
            "end trait\n"
            "type Color = Red | Green\n"
            "func greet(name: String) -> String\n"
            '  example "a" -> "hi"\n'
            '  return "hi"\n'
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        document_symbol = server.lsp._get_handler(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        symbols = document_symbol(
            types.DocumentSymbolParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri())
            )
        )
        symbol_kinds = {(symbol.name, symbol.kind) for symbol in symbols}

        assert ("Color", types.SymbolKind.Class) in symbol_kinds
        assert ("Red", types.SymbolKind.EnumMember) in symbol_kinds
        assert ("Green", types.SymbolKind.EnumMember) in symbol_kinds
        assert ("greet", types.SymbolKind.Function) in symbol_kinds
        assert ("Describable", types.SymbolKind.Interface) in symbol_kinds

    def test_document_symbols_exclude_locals_and_params(self, tmp_path):
        source = (
            "func greet(name: String) -> String\n"
            '  example "a" -> "hi"\n'
            '  let local: String = "hi"\n'
            "  return local\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        document_symbol = server.lsp._get_handler(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        symbols = document_symbol(
            types.DocumentSymbolParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri())
            )
        )
        names = {symbol.name for symbol in symbols}

        assert "greet" in names
        assert "name" not in names
        assert "local" not in names

    def test_document_symbols_include_type_aliases(self, tmp_path):
        source = "type UserId = Int\nfunc main() -> Int\n  return 1\nend func\n"
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        document_symbol = server.lsp._get_handler(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        symbols = document_symbol(
            types.DocumentSymbolParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri())
            )
        )
        symbol_kinds = {(symbol.name, symbol.kind) for symbol in symbols}

        assert ("UserId", types.SymbolKind.Class) in symbol_kinds
        assert ("main", types.SymbolKind.Function) in symbol_kinds


class TestWorkspaceSymbols:
    """Tests for workspace/symbol."""

    def test_workspace_symbols_search_open_project_files(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        utils_source = (
            "trait Describable\n"
            "  func describe(self: Self) -> String\n"
            "end trait\n"
            "type Color = Red | Green\n"
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        workspace_symbol = server.lsp._get_handler(types.WORKSPACE_SYMBOL)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        symbols = workspace_symbol(types.WorkspaceSymbolParams(query="help"))
        matches = {(symbol.name, symbol.container_name) for symbol in symbols}

        assert ("helper", "Utils") in matches
        assert ("main", "Main") not in matches

        trait_symbols = workspace_symbol(
            types.WorkspaceSymbolParams(query="Describable")
        )
        assert ("Describable", types.SymbolKind.Interface) in {
            (symbol.name, symbol.kind) for symbol in trait_symbols
        }

    def test_workspace_symbols_use_unsaved_imported_module_source(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = (
            "import Utils\nfunc main() -> Int\n  return new_api(1)\nend func\n"
        )
        utils_disk = (
            "export func old_api(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        utils_unsaved = (
            "export func new_api(x: Int) -> Int\n"
            "  example 1 -> 3\n"
            "  return x + 2\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_disk)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        workspace_symbol = server.lsp._get_handler(types.WORKSPACE_SYMBOL)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_unsaved,
                )
            )
        )

        new_symbols = workspace_symbol(types.WorkspaceSymbolParams(query="new_api"))
        old_symbols = workspace_symbol(types.WorkspaceSymbolParams(query="old_api"))

        assert {symbol.name for symbol in new_symbols} == {"new_api"}
        assert old_symbols == []

    def test_workspace_symbols_include_unsaved_imported_type_aliases(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> AliasId\n  return 1\nend func\n"
        utils_disk = "export type OldAlias = Int\n"
        utils_unsaved = "export type AliasId = Int\n"
        main_file.write_text(main_source)
        utils_file.write_text(utils_disk)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        workspace_symbol = server.lsp._get_handler(types.WORKSPACE_SYMBOL)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=2,
                    text=utils_unsaved,
                )
            )
        )

        alias_symbols = workspace_symbol(types.WorkspaceSymbolParams(query="Alias"))
        names_and_kinds = {(symbol.name, symbol.kind) for symbol in alias_symbols}

        assert ("AliasId", types.SymbolKind.Class) in names_and_kinds
        assert ("OldAlias", types.SymbolKind.Class) not in names_and_kinds


class TestDocumentHighlights:
    """Tests for textDocument/documentHighlight."""

    def test_document_highlight_marks_definition_and_reference(self, tmp_path):
        source = (
            "func add(x: Int, y: Int) -> Int\n"
            "  example () -> 3\n"
            "  return x + y\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "  example () -> 3\n"
            "  return add(1, 2)\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        document_highlight = server.lsp._get_handler(
            types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT
        )

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        highlights = document_highlight(
            types.DocumentHighlightParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=7, character=10),
            )
        )
        ranges = {
            (h.range.start.line, h.range.start.character, h.range.end.character)
            for h in highlights
        }

        assert (0, 5, 8) in ranges
        assert (7, 9, 12) in ranges

    def test_document_highlight_stays_in_current_document(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = (
            "import Utils\n"
            "func main() -> Int\n"
            "  return helper(1) + helper(2)\n"
            "end func\n"
        )
        utils_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        document_highlight = server.lsp._get_handler(
            types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT
        )

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        highlights = document_highlight(
            types.DocumentHighlightParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
            )
        )

        assert len(highlights) == 2
        assert all(h.range.start.line == 2 for h in highlights)


class TestSemanticDefinition:
    """Tests for semantic go-to-definition."""

    def test_semantic_actions_ignore_comment_and_string_words(self, tmp_path):
        source = (
            "func main() -> Int\n"
            "  let x: Int = 1 // x\n"
            '  let label: String = "x"\n'
            "  return x\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)
        document_highlight = server.lsp._get_handler(
            types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT
        )

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        comment_char = source.splitlines()[1].index("// x") + 3
        string_char = source.splitlines()[2].index('"x"') + 1
        for line, char in [(1, comment_char), (2, string_char)]:
            identifier = types.TextDocumentIdentifier(uri=main_file.as_uri())
            assert (
                definition(
                    types.DefinitionParams(
                        text_document=identifier,
                        position=types.Position(line=line, character=char),
                    )
                )
                is None
            )
            assert (
                references(
                    types.ReferenceParams(
                        text_document=identifier,
                        position=types.Position(line=line, character=char),
                        context=types.ReferenceContext(include_declaration=True),
                    )
                )
                is None
            )
            assert (
                rename(
                    types.RenameParams(
                        text_document=identifier,
                        position=types.Position(line=line, character=char),
                        new_name="renamed",
                    )
                )
                is None
            )
            assert (
                document_highlight(
                    types.DocumentHighlightParams(
                        text_document=identifier,
                        position=types.Position(line=line, character=char),
                    )
                )
                == []
            )

    def test_definition_resolves_local_variable(self, tmp_path):
        source = (
            "func main() -> Int\n"
            "  example () -> 1\n"
            "  let value: Int = 1\n"
            "  return value\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        location = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=3, character=10),
            )
        )

        assert location is not None
        assert location.uri == main_file.as_uri()
        assert location.range.start.line == 2
        assert location.range.start.character == 6
        assert location.range.end.character == 11

    def test_definition_resolves_imported_function_semantically(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        utils_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        location = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
            )
        )

        assert location is not None
        assert location.uri == utils_file.as_uri()
        assert location.range.start.line == 0
        assert location.range.start.character == 12
        assert location.range.end.character == 18


class TestSemanticHover:
    """Tests for semantic hover content."""

    def test_hover_shows_local_variable_declaration(self, tmp_path):
        source = (
            "func main() -> Int\n"
            "  example () -> 1\n"
            "  let value: Int = 1\n"
            "  return value\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=3, character=10),
            )
        )

        assert result is not None
        contents = result.contents
        assert isinstance(contents, types.MarkupContent)
        assert "let value: Int = 1" in contents.value
        assert "(variable)" in contents.value

    def test_hover_shows_imported_function_signature(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        utils_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
            )
        )

        assert result is not None
        contents = result.contents
        assert isinstance(contents, types.MarkupContent)
        assert "helper(x: Int) -> Int" in contents.value
        assert "(function)" in contents.value

    def test_hover_shows_imported_type_declaration(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = "import Utils\nfunc make() -> Maybe\n  return Some(1)\nend func\n"
        utils_source = "export type Maybe = Some(value: Int) | None\n"
        other_source = "export type Maybe = Some(value: String) | Never\n"
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=16),
            )
        )

        assert result is not None
        contents = result.contents
        assert isinstance(contents, types.MarkupContent)
        assert "export type Maybe = Some(value: Int) | None" in contents.value
        assert "(type)" in contents.value

    def test_hover_shows_imported_constructor_context(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\nfunc make() -> OptionInt\n  return Some(1)\nend func\n"
        )
        utils_source = "export type OptionInt = Some(value: Int) | None\n"
        other_source = "export type Alternate = Some(value: String) | Never\n"
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
            )
        )

        assert result is not None
        contents = result.contents
        assert isinstance(contents, types.MarkupContent)
        assert "Some: OptionInt" in contents.value
        assert "(constructor)" in contents.value

    def test_hover_keeps_builtin_fallback(self, tmp_path):
        source = "func main() -> Int\n  return length([1])\nend func\n"
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=10),
            )
        )

        assert result is not None
        contents = result.contents
        assert isinstance(contents, types.MarkupContent)
        assert "length:" in contents.value
        assert "(builtin)" in contents.value

    def test_hover_shows_parameter_when_name_matches_function_name(self, tmp_path):
        source = "func greet(greet: String) -> String\n  return greet\nend func\n"
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=10),
            )
        )

        assert result is not None
        contents = result.contents
        assert isinstance(contents, types.MarkupContent)
        assert "greet: String" in contents.value
        assert "(parameter)" in contents.value


class TestSemanticSignatureHelp:
    """Tests for semantic signature help content."""

    def test_signature_help_uses_callable_local_binding_type(self, tmp_path):
        source = (
            "func add(a: Int, b: Int) -> Int\n"
            "  example (1, 2) -> 3\n"
            "  return a + b\n"
            "end func\n"
            "\n"
            "func length(value: Int, extra: Int) -> Int\n"
            "  example (1, 2) -> 3\n"
            "  return value + extra\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "  let length: (Int, Int) -> Int = add\n"
            "  return length(1, 2)\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        signature_help = server.lsp._get_handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        call_line = source.splitlines()[12]
        result = signature_help(
            types.SignatureHelpParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(
                    line=12,
                    character=call_line.index("2") + 1,
                ),
            )
        )

        assert result is not None
        assert result.active_parameter == 1
        assert result.signatures[0].label == "length(Int, Int) -> Int"

    def test_signature_help_does_not_leak_builtin_for_non_callable_local(
        self, tmp_path
    ):
        source = (
            "func main() -> Int\n  let length: Int = 1\n  return length(1)\nend func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        signature_help = server.lsp._get_handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        call_line = source.splitlines()[2]
        result = signature_help(
            types.SignatureHelpParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(
                    line=2,
                    character=call_line.index("1") + 1,
                ),
            )
        )

        assert result is None

    def test_signature_help_uses_qualified_import_target(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Foo", "Bar"]\n'
        )
        main_file = tmp_path / "Main.geno"
        foo_file = tmp_path / "Foo.geno"
        bar_file = tmp_path / "Bar.geno"
        main_source = (
            "import Foo as F\n"
            "import Bar as B\n"
            "func main() -> String\n"
            '  return B.helper("x")\n'
            "end func\n"
        )
        foo_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        bar_source = (
            "export func helper(text: String) -> String\n"
            '  example "x" -> "x!"\n'
            '  return text + "!"\n'
            "end func\n"
        )
        main_file.write_text(main_source)
        foo_file.write_text(foo_source)
        bar_file.write_text(bar_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        signature_help = server.lsp._get_handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        call_line = main_source.splitlines()[3]
        result = signature_help(
            types.SignatureHelpParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(
                    line=3,
                    character=call_line.index('"') + 2,
                ),
            )
        )

        assert result is not None
        assert result.active_parameter == 0
        assert result.signatures[0].label == "helper(text: String) -> String"

    def test_signature_help_keeps_builtin_fallback(self, tmp_path):
        source = "func main() -> Int\n  return length([1])\nend func\n"
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        signature_help = server.lsp._get_handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        call_line = source.splitlines()[1]
        result = signature_help(
            types.SignatureHelpParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(
                    line=1,
                    character=call_line.index("[1]") + 2,
                ),
            )
        )

        assert result is not None
        assert result.active_parameter == 0
        assert result.signatures[0].label.startswith("length(")

    def test_signature_help_uses_function_typed_parameter(self, tmp_path):
        source = "func apply(f: (Int, Int) -> Int) -> Int\n  return f(1, 2)\nend func\n"
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        signature_help = server.lsp._get_handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        call_line = source.splitlines()[1]
        result = signature_help(
            types.SignatureHelpParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(
                    line=1,
                    character=call_line.index("2") + 1,
                ),
            )
        )

        assert result is not None
        assert result.active_parameter == 1
        assert result.signatures[0].label == "f(Int, Int) -> Int"

    def test_signature_help_handles_parameter_named_like_function(self, tmp_path):
        source = (
            "func apply(apply: (Int, Int) -> Int) -> Int\n"
            "  return apply(1, 2)\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        signature_help = server.lsp._get_handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        call_line = source.splitlines()[1]
        result = signature_help(
            types.SignatureHelpParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(
                    line=1,
                    character=call_line.index("2") + 1,
                ),
            )
        )

        assert result is not None
        assert result.active_parameter == 1
        assert result.signatures[0].label == "apply(Int, Int) -> Int"

    def test_signature_help_uses_unsaved_imported_module_source(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = (
            'import Utils\nfunc main() -> String\n  return helper("x")\nend func\n'
        )
        disk_utils = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        unsaved_utils = (
            "export func helper(text: String) -> String\n"
            '  example "x" -> "x!"\n'
            '  return text + "!"\n'
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(disk_utils)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        signature_help = server.lsp._get_handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=unsaved_utils,
                )
            )
        )

        call_line = main_source.splitlines()[2]
        result = signature_help(
            types.SignatureHelpParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(
                    line=2,
                    character=call_line.index('"x"') + 2,
                ),
            )
        )

        assert result is not None
        assert result.signatures[0].label == "helper(text: String) -> String"

    def test_signature_help_reads_closed_imported_module_from_disk(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        utils_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        signature_help = server.lsp._get_handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        call_line = main_source.splitlines()[2]
        result = signature_help(
            types.SignatureHelpParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(
                    line=2,
                    character=call_line.index("1") + 1,
                ),
            )
        )

        assert result is not None
        assert result.signatures[0].label == "helper(x: Int) -> Int"


class TestSemanticInvariants:
    """Cross-feature regressions that should stay aligned on one symbol."""

    def test_definition_is_included_in_references_for_imported_symbol(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = (
            "import Utils\n"
            "func main() -> Int\n"
            "  return helper(1) + helper(2)\n"
            "end func\n"
        )
        utils_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
                context=types.ReferenceContext(include_declaration=True),
            )
        )

        assert defn is not None
        assert refs is not None
        assert _location_key(defn) in {_location_key(ref) for ref in refs}

    def test_unqualified_imported_symbol_surfaces_ignore_unrelated_same_name_module(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        utils_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        other_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 3\n"
            "  return x + 2\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
                new_name="count",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            0,
            12,
            0,
            18,
        )

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(main_file.resolve()),
            2,
            9,
            2,
            15,
        ) in ref_keys
        assert (
            str(utils_file.resolve()),
            0,
            12,
            0,
            18,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }

    def test_imported_type_surfaces_ignore_unrelated_same_name_module(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = "import Utils\nfunc make() -> Maybe\n  return Some(1)\nend func\n"
        utils_source = "export type Maybe = Some(value: Int) | None\n"
        other_source = "export type Maybe = Some(value: String) | Never\n"
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=16),
            )
        )
        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=16),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=16),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=16),
                new_name="OptionInt",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            0,
            12,
            0,
            17,
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert (
            "export type Maybe = Some(value: Int) | None" in hover_result.contents.value
        )

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(main_file.resolve()),
            1,
            15,
            1,
            20,
        ) in ref_keys
        assert (
            str(utils_file.resolve()),
            0,
            12,
            0,
            17,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }

    def test_imported_constructor_surfaces_ignore_unrelated_same_name_module(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\nfunc make() -> OptionInt\n  return Some(1)\nend func\n"
        )
        utils_source = "export type OptionInt = Some(value: Int) | None\n"
        other_source = "export type Alternate = Some(value: String) | Never\n"
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
            )
        )
        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
                new_name="Just",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            0,
            24,
            0,
            28,
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert "Some: OptionInt" in hover_result.contents.value

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(main_file.resolve()),
            2,
            9,
            2,
            13,
        ) in ref_keys
        assert (
            str(utils_file.resolve()),
            0,
            24,
            0,
            28,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }

    def test_imported_type_in_lambda_annotation_ignores_unrelated_same_name_module(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\n"
            "func main() -> Int\n"
            "  let f: (Int) -> Int = fn(value: Maybe) -> 1\n"
            "  return 1\n"
            "end func\n"
        )
        utils_source = "export type Maybe = Some(value: Int) | None\n"
        other_source = "export type Maybe = Some(value: String) | Never\n"
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        lambda_line = main_source.splitlines()[2]
        maybe_char = lambda_line.index("Maybe") + 1
        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=maybe_char),
            )
        )
        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=maybe_char),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=maybe_char),
                context=types.ReferenceContext(include_declaration=True),
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            0,
            12,
            0,
            17,
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert (
            "export type Maybe = Some(value: Int) | None" in hover_result.contents.value
        )

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(main_file.resolve()),
            2,
            34,
            2,
            39,
        ) in ref_keys
        assert (
            str(utils_file.resolve()),
            0,
            12,
            0,
            17,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

    def test_trait_method_type_surfaces_ignore_unrelated_same_name_module(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\n"
            "trait Describable\n"
            "  func describe(value: AliasId) -> AliasId\n"
            "end trait\n"
        )
        utils_source = "export type AliasId = Int\n"
        other_source = "export type AliasId = String\n"
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=23),
            )
        )
        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=23),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=23),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=23),
                new_name="WrappedId",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            0,
            12,
            0,
            19,
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert "export type AliasId = Int" in hover_result.contents.value

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(main_file.resolve()),
            2,
            23,
            2,
            30,
        ) in ref_keys
        assert (
            str(main_file.resolve()),
            2,
            35,
            2,
            42,
        ) in ref_keys
        assert (
            str(utils_file.resolve()),
            0,
            12,
            0,
            19,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }
        assert len(rename_result.changes[main_file.as_uri()]) == 2
        assert len(rename_result.changes[utils_file.as_uri()]) == 1

    def test_impl_target_type_surfaces_ignore_unrelated_same_name_module(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\n"
            "trait Describable\n"
            "  func describe(value: Self) -> Int\n"
            "end trait\n"
            "impl Describable for AliasId\n"
            "  func describe(value: AliasId) -> Int\n"
            "    example 1 -> 1\n"
            "    return value\n"
            "  end func\n"
            "end impl\n"
        )
        utils_source = "export type AliasId = Int\n"
        other_source = "export type AliasId = String\n"
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=4, character=21),
            )
        )
        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=4, character=21),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=4, character=21),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=4, character=21),
                new_name="WrappedId",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            0,
            12,
            0,
            19,
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert "export type AliasId = Int" in hover_result.contents.value

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(main_file.resolve()),
            4,
            21,
            4,
            28,
        ) in ref_keys
        assert (
            str(main_file.resolve()),
            5,
            23,
            5,
            30,
        ) in ref_keys
        assert (
            str(utils_file.resolve()),
            0,
            12,
            0,
            19,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }
        assert len(rename_result.changes[main_file.as_uri()]) == 2
        assert len(rename_result.changes[utils_file.as_uri()]) == 1

    def test_impl_trait_surfaces_ignore_unrelated_same_name_module(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\n"
            "impl AliasTrait for Int\n"
            "  func convert(value: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    return value\n"
            "  end func\n"
            "end impl\n"
        )
        utils_source = (
            "trait AliasTrait\n  func convert(value: Int) -> Int\nend trait\n"
        )
        other_source = (
            "trait AliasTrait\n  func convert(value: String) -> String\nend trait\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=6),
            )
        )
        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=6),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=6),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=6),
                new_name="Converter",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            0,
            6,
            0,
            16,
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert "trait AliasTrait" in hover_result.contents.value

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(main_file.resolve()),
            1,
            5,
            1,
            15,
        ) in ref_keys
        assert (
            str(utils_file.resolve()),
            0,
            6,
            0,
            16,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }

    def test_imported_type_alias_in_top_level_alias_uses_semantic_resolution(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\n"
            "type Wrapped = AliasId\n"
            "func main() -> Int\n"
            "  return 1\n"
            "end func\n"
        )
        disk_utils = "export type AliasId = Int\n"
        unsaved_utils = "\n\nexport type AliasId = Int\n"
        other_source = "export type AliasId = String\n"
        main_file.write_text(main_source)
        utils_file.write_text(disk_utils)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=2,
                    text=unsaved_utils,
                )
            )
        )

        position = types.Position(line=1, character=16)
        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=position,
            )
        )
        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=position,
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=position,
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=position,
                new_name="Renamed",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            2,
            12,
            2,
            19,
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert "export type AliasId = Int" in hover_result.contents.value

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(main_file.resolve()),
            1,
            15,
            1,
            22,
        ) in ref_keys
        assert (
            str(utils_file.resolve()),
            2,
            12,
            2,
            19,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }
        main_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[main_file.as_uri()]
        }
        assert (1, 15, 1, 22, "Renamed") in main_edits
        utils_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[utils_file.as_uri()]
        }
        assert (2, 12, 2, 19, "Renamed") in utils_edits

    def test_imported_type_alias_in_top_level_type_field_uses_semantic_resolution(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\n"
            "type Wrapper = Wrapper(value: AliasId)\n"
            "func main() -> Int\n"
            "  return 1\n"
            "end func\n"
        )
        disk_utils = "export type AliasId = Int\n"
        unsaved_utils = "\n\nexport type AliasId = Int\n"
        other_source = "export type AliasId = String\n"
        main_file.write_text(main_source)
        utils_file.write_text(disk_utils)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=2,
                    text=unsaved_utils,
                )
            )
        )

        position = types.Position(line=1, character=31)
        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=position,
            )
        )
        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=position,
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=position,
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=position,
                new_name="Renamed",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            2,
            12,
            2,
            19,
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert "export type AliasId = Int" in hover_result.contents.value

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(main_file.resolve()),
            1,
            30,
            1,
            37,
        ) in ref_keys
        assert (
            str(utils_file.resolve()),
            2,
            12,
            2,
            19,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }
        main_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[main_file.as_uri()]
        }
        assert (1, 30, 1, 37, "Renamed") in main_edits
        utils_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[utils_file.as_uri()]
        }
        assert (2, 12, 2, 19, "Renamed") in utils_edits

    def test_rename_imported_type_uses_unsaved_module_locations(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = "import Utils\nfunc make() -> Maybe\n  return Some(1)\nend func\n"
        disk_utils = "export type Maybe = Some(value: Int) | None\n"
        unsaved_utils = "\n\nexport type Maybe = Some(value: Int) | None\n"
        other_source = "export type Maybe = Some(value: String) | Never\n"
        main_file.write_text(main_source)
        utils_file.write_text(disk_utils)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=2,
                    text=unsaved_utils,
                )
            )
        )

        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=16),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=16),
                new_name="OptionInt",
            )
        )

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(utils_file.resolve()),
            2,
            12,
            2,
            17,
        ) in ref_keys
        assert (
            str(main_file.resolve()),
            1,
            15,
            1,
            20,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }
        utils_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[utils_file.as_uri()]
        }
        assert (2, 12, 2, 17, "OptionInt") in utils_edits

    def test_rename_imported_constructor_uses_unsaved_module_locations(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\nfunc make() -> OptionInt\n  return Some(1)\nend func\n"
        )
        disk_utils = "export type OptionInt = Some(value: Int) | None\n"
        unsaved_utils = "\n\nexport type OptionInt = Some(value: Int) | None\n"
        other_source = "export type Alternate = Some(value: String) | SomeElse\n"
        main_file.write_text(main_source)
        utils_file.write_text(disk_utils)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=2,
                    text=unsaved_utils,
                )
            )
        )

        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
                new_name="Just",
            )
        )

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(utils_file.resolve()),
            2,
            24,
            2,
            28,
        ) in ref_keys
        assert (
            str(main_file.resolve()),
            2,
            9,
            2,
            13,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }
        utils_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[utils_file.as_uri()]
        }
        assert (2, 24, 2, 28, "Just") in utils_edits

    def test_rename_imported_type_uses_unsaved_importer_and_module_locations(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_disk = "import Utils\nfunc make() -> Maybe\n  return Some(1)\nend func\n"
        main_unsaved = (
            "import Utils\n"
            "func make() -> Maybe\n"
            "  let x: Maybe = Some(1)\n"
            "  return x\n"
            "end func\n"
        )
        disk_utils = "export type Maybe = Some(value: Int) | None\n"
        unsaved_utils = "\n\nexport type Maybe = Some(value: Int) | None\n"
        other_source = "export type Maybe = Some(value: String) | Never\n"
        main_file.write_text(main_disk)
        utils_file.write_text(disk_utils)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=2,
                    text=main_unsaved,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=3,
                    text=unsaved_utils,
                )
            )
        )

        unsaved_line = main_unsaved.splitlines()[2]
        maybe_char = unsaved_line.index("Maybe") + 1
        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=maybe_char),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=maybe_char),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=maybe_char),
                new_name="Renamed",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            2,
            12,
            2,
            17,
        )

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(utils_file.resolve()),
            2,
            12,
            2,
            17,
        ) in ref_keys
        assert (
            str(main_file.resolve()),
            1,
            15,
            1,
            20,
        ) in ref_keys
        assert (
            str(main_file.resolve()),
            2,
            9,
            2,
            14,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }
        main_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[main_file.as_uri()]
        }
        assert (1, 15, 1, 20, "Renamed") in main_edits
        assert (2, 9, 2, 14, "Renamed") in main_edits
        utils_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[utils_file.as_uri()]
        }
        assert (2, 12, 2, 17, "Renamed") in utils_edits

    def test_rename_imported_constructor_uses_unsaved_importer_and_module_locations(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_disk = (
            "import Utils\nfunc make() -> OptionInt\n  return Some(1)\nend func\n"
        )
        main_unsaved = (
            "import Utils\n"
            "func make() -> OptionInt\n"
            "  let x: OptionInt = Some(1)\n"
            "  return x\n"
            "end func\n"
        )
        disk_utils = "export type OptionInt = Some(value: Int) | None\n"
        unsaved_utils = "\n\nexport type OptionInt = Some(value: Int) | None\n"
        other_source = "export type Alternate = Some(value: String) | SomeElse\n"
        main_file.write_text(main_disk)
        utils_file.write_text(disk_utils)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=2,
                    text=main_unsaved,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=3,
                    text=unsaved_utils,
                )
            )
        )

        unsaved_line = main_unsaved.splitlines()[2]
        ctor_char = unsaved_line.index("Some") + 1
        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=ctor_char),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=ctor_char),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=ctor_char),
                new_name="Renamed",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            2,
            24,
            2,
            28,
        )

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(utils_file.resolve()),
            2,
            24,
            2,
            28,
        ) in ref_keys
        assert (
            str(main_file.resolve()),
            2,
            21,
            2,
            25,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }
        main_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[main_file.as_uri()]
        }
        assert (2, 21, 2, 25, "Renamed") in main_edits
        utils_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[utils_file.as_uri()]
        }
        assert (2, 24, 2, 28, "Renamed") in utils_edits

    def test_rename_imported_pattern_constructor_uses_unsaved_module_locations(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\n"
            "func unwrap(value: Maybe) -> Int\n"
            "  match value with\n"
            "    | Some(x) -> return x\n"
            "    | None -> return 0\n"
            "  end match\n"
            "end func\n"
        )
        disk_utils = "export type Maybe = Some(value: Int) | None\n"
        unsaved_utils = "\n\nexport type Maybe = Some(value: Int) | None\n"
        other_source = "export type Maybe = Some(value: String) | Never\n"
        main_file.write_text(main_source)
        utils_file.write_text(disk_utils)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=2,
                    text=unsaved_utils,
                )
            )
        )

        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=3, character=7),
            )
        )
        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=3, character=7),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=3, character=7),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=3, character=7),
                new_name="Renamed",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            2,
            20,
            2,
            24,
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert "Some: Maybe" in hover_result.contents.value

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(utils_file.resolve()),
            2,
            20,
            2,
            24,
        ) in ref_keys
        assert (
            str(main_file.resolve()),
            3,
            6,
            3,
            10,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }
        main_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[main_file.as_uri()]
        }
        assert (3, 6, 3, 10, "Renamed") in main_edits
        utils_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[utils_file.as_uri()]
        }
        assert (2, 20, 2, 24, "Renamed") in utils_edits

    def test_rename_imported_lambda_return_type_uses_unsaved_module_locations(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        other_file = tmp_path / "Other.geno"
        main_source = (
            "import Utils\n"
            "func main() -> Int\n"
            "  let f: () -> Maybe = fn() do\n"
            "    return Some(1)\n"
            "  end fn\n"
            "  return 1\n"
            "end func\n"
        )
        disk_utils = "export type Maybe = Some(value: Int) | None\n"
        unsaved_utils = "\n\nexport type Maybe = Some(value: Int) | None\n"
        other_source = "export type Maybe = Some(value: String) | Never\n"
        main_file.write_text(main_source)
        utils_file.write_text(disk_utils)
        other_file.write_text(other_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=2,
                    text=unsaved_utils,
                )
            )
        )

        defn = definition(
            types.DefinitionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=17),
            )
        )
        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=17),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=17),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        rename_result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=17),
                new_name="Renamed",
            )
        )

        assert defn is not None
        assert _location_key(defn) == (
            str(utils_file.resolve()),
            2,
            12,
            2,
            17,
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert (
            "export type Maybe = Some(value: Int) | None" in hover_result.contents.value
        )

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(utils_file.resolve()),
            2,
            12,
            2,
            17,
        ) in ref_keys
        assert (
            str(main_file.resolve()),
            2,
            15,
            2,
            20,
        ) in ref_keys
        assert not any(key[0] == str(other_file.resolve()) for key in ref_keys)

        assert rename_result is not None
        assert rename_result.changes is not None
        assert set(rename_result.changes) == {
            main_file.as_uri(),
            utils_file.as_uri(),
        }
        main_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[main_file.as_uri()]
        }
        assert (2, 15, 2, 20, "Renamed") in main_edits
        utils_edits = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
                edit.new_text,
            )
            for edit in rename_result.changes[utils_file.as_uri()]
        }
        assert (2, 12, 2, 17, "Renamed") in utils_edits

    def test_document_highlights_are_subset_of_same_file_references(self, tmp_path):
        source = (
            "func main() -> Int\n"
            "  example () -> 2\n"
            "  let value: Int = 1\n"
            "  return value + value\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        document_highlight = server.lsp._get_handler(
            types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT
        )
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        highlights = document_highlight(
            types.DocumentHighlightParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=3, character=10),
            )
        )
        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=3, character=10),
                context=types.ReferenceContext(include_declaration=True),
            )
        )

        assert refs is not None
        ref_keys = {
            _location_key(ref)
            for ref in refs
            if _uri_to_path(ref.uri).resolve() == main_file.resolve()
        }
        highlight_keys = {
            _highlight_key(main_file.as_uri(), highlight) for highlight in highlights
        }
        assert highlight_keys <= ref_keys

    def test_hover_and_signature_help_agree_on_unsaved_imported_function(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = (
            'import Utils\nfunc main() -> String\n  return helper("x")\nend func\n'
        )
        disk_utils = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        unsaved_utils = (
            "export func helper(text: String) -> String\n"
            '  example "x" -> "x!"\n'
            '  return text + "!"\n'
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(disk_utils)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        hover = server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
        signature_help = server.lsp._get_handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=unsaved_utils,
                )
            )
        )

        hover_result = hover(
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
            )
        )
        call_line = main_source.splitlines()[2]
        sig_result = signature_help(
            types.SignatureHelpParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(
                    line=2,
                    character=call_line.index('"x"') + 2,
                ),
            )
        )

        assert hover_result is not None
        assert isinstance(hover_result.contents, types.MarkupContent)
        assert sig_result is not None
        assert "helper(text: String) -> String" in hover_result.contents.value
        assert sig_result.signatures[0].label == "helper(text: String) -> String"

    def test_rename_uses_symbol_token_range_for_local_declaration(self, tmp_path):
        source = (
            "func main() -> Int\n"
            "  example () -> 2\n"
            "  let value: Int = 1\n"
            "  return value + value\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=3, character=10),
                new_name="count",
            )
        )

        assert result is not None
        edits = result.changes[main_file.as_uri()]
        ranges = {
            (
                edit.range.start.line,
                edit.range.start.character,
                edit.range.end.line,
                edit.range.end.character,
            )
            for edit in edits
        }
        assert (2, 6, 2, 11) in ranges

    def test_references_include_imported_call_before_string_literal(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = (
            'import Utils\nfunc main() -> String\n  return helper("x")\nend func\n'
        )
        utils_source = (
            "export func helper(x: String) -> String\n"
            '  example "x" -> "x!"\n'
            '  return x + "!"\n'
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=10),
                context=types.ReferenceContext(include_declaration=True),
            )
        )

        assert refs is not None
        ref_keys = {_location_key(ref) for ref in refs}
        assert (
            str(main_file.resolve()),
            2,
            9,
            2,
            15,
        ) in ref_keys
        assert (
            str(utils_file.resolve()),
            0,
            12,
            0,
            18,
        ) in ref_keys

    def test_rename_uses_open_document_uris_without_duplicates(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = (
            "import Utils as U\n"
            "func main() -> Int\n"
            "  return U.helper(1) + U.helper(2)\n"
            "end func\n"
        )
        utils_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        rename = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        result = rename(
            types.RenameParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=11),
                new_name="count",
            )
        )

        assert result is not None
        assert result.changes is not None
        assert set(result.changes) == {main_file.as_uri(), utils_file.as_uri()}
        assert len(result.changes[main_file.as_uri()]) == 2
        assert len(result.changes[utils_file.as_uri()]) == 1


class TestDocumentSyncDiagnostics:
    """Notification-driven diagnostics regressions."""

    def test_did_open_uses_unsaved_source_for_manifest_project(
        self, tmp_path, monkeypatch
    ):
        """didOpen typechecks the current buffer, not the on-disk entrypoint."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_file.write_text("func main() -> Int\n  return helper(1)\nend func\n")
        utils_file.write_text(
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        unsaved_source = (
            "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        )

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=unsaved_source,
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[main_file.as_uri()] == []
        assert latest[utils_file.as_uri()] == []

    def test_did_open_honors_manifest_target_for_single_file_project(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main"]\ntargets = ["python-cli"]\n'
        )
        main_file = tmp_path / "Main.geno"
        source = "func main() -> Int\n  return screen_width()\nend func\n"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert any(
            "screen_width" in diag.message for diag in latest[main_file.as_uri()]
        )
        assert any("python-cli" in diag.message for diag in latest[main_file.as_uri()])

    def test_did_open_reports_multiple_project_type_errors(
        self, tmp_path, monkeypatch
    ) -> None:
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = (
            "import Utils\n"
            "func main() -> Int\n"
            '  let a: Int = "wrong"\n'
            "  let b: Bool = 1\n"
            "  return 0\n"
            "end func\n"
        )
        utils_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            "  return x\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        messages = [diag.message for diag in latest[main_file.as_uri()]]
        assert len(messages) >= 2
        assert any("let a" in message for message in messages)
        assert any("let b" in message for message in messages)

    def test_did_change_uses_unsaved_imported_module_for_project_diagnostics(
        self, tmp_path, monkeypatch
    ):
        """Unsaved imported-module edits affect project diagnostics before save."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        good_utils = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        bad_utils = (
            "export func helper(x: Int) -> String\n"
            '  example 1 -> "two"\n'
            '  return "two"\n'
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(good_utils)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_change = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CHANGE)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=good_utils,
                )
            )
        )

        published.clear()
        did_change(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(
                    uri=utils_file.as_uri(),
                    version=2,
                ),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type2(text=bad_utils)
                ],
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[main_file.as_uri()]
        assert latest[utils_file.as_uri()] == []

    def test_did_change_ignores_incremental_payload_for_full_sync(
        self, tmp_path, monkeypatch
    ):
        """Ranged didChange payloads must not replace the full open document."""
        file_path = tmp_path / "Main.geno"
        source = "func main() -> Int\n  return 1\nend func\n"
        file_path.write_text(source)
        uri = file_path.as_uri()

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_change = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CHANGE)
        lsp_wrapper = server._geno_language_server

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=uri,
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        published.clear()
        did_change(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(
                    uri=uri,
                    version=2,
                ),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type1(
                        range=types.Range(
                            start=types.Position(line=1, character=2),
                            end=types.Position(line=1, character=10),
                        ),
                        range_length=8,
                        text="partial fragment",
                    )
                ],
            )
        )

        assert lsp_wrapper._open_docs[uri] == source
        assert published == []

    def test_did_change_ignores_stale_lsp_version(self, tmp_path, monkeypatch):
        """Older versioned full-sync changes must not roll document state back."""
        file_path = tmp_path / "Main.geno"
        source_v1 = "func main() -> Int\n  return 1\nend func\n"
        source_v3 = "func main() -> Int\n  return 3\nend func\n"
        stale_source = "func main() -> Int\n  return 2\nend func\n"
        file_path.write_text(source_v1)
        uri = file_path.as_uri()

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_change = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CHANGE)
        lsp_wrapper = server._geno_language_server

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=uri,
                    language_id="geno",
                    version=1,
                    text=source_v1,
                )
            )
        )
        did_change(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(
                    uri=uri,
                    version=3,
                ),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type2(text=source_v3)
                ],
            )
        )
        assert lsp_wrapper._open_docs[uri] == source_v3
        assert lsp_wrapper._open_doc_lsp_versions[uri] == 3

        published.clear()
        did_change(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(
                    uri=uri,
                    version=2,
                ),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type2(text=stale_source)
                ],
            )
        )

        assert lsp_wrapper._open_docs[uri] == source_v3
        assert lsp_wrapper._open_doc_lsp_versions[uri] == 3
        assert published == []

    def test_did_change_does_not_refresh_unrelated_open_project(
        self, tmp_path, monkeypatch
    ):
        """Changing one project leaves unrelated open projects untouched."""
        project_a = tmp_path / "project_a"
        project_b = tmp_path / "project_b"
        project_a.mkdir()
        project_b.mkdir()

        (project_a / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_a = project_a / "Main.geno"
        utils_a = project_a / "Utils.geno"
        main_a_source = (
            "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        )
        main_a.write_text(main_a_source)
        utils_a.write_text(
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )

        main_b = project_b / "Main.geno"
        other_b = project_b / "Other.geno"
        main_b_source = (
            "import Other\nfunc main() -> Int\n  return other(1)\nend func\n"
        )
        main_b.write_text(main_b_source)
        other_b.write_text(
            "export func other(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )

        bad_utils = (
            "export func helper(x: Int) -> String\n"
            '  example 1 -> "two"\n'
            '  return "two"\n'
            "end func\n"
        )

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_change = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CHANGE)

        for path, source in (
            (main_a, main_a_source),
            (utils_a, utils_a.read_text()),
            (main_b, main_b_source),
        ):
            did_open(
                types.DidOpenTextDocumentParams(
                    text_document=types.TextDocumentItem(
                        uri=path.as_uri(),
                        language_id="geno",
                        version=1,
                        text=source,
                    )
                )
            )

        published.clear()
        did_change(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(
                    uri=utils_a.as_uri(),
                    version=2,
                ),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type2(text=bad_utils)
                ],
            )
        )

        published_uris = [uri for uri, _ in published]
        assert published_uris.count(main_a.as_uri()) == 1
        assert published_uris.count(utils_a.as_uri()) == 1
        assert main_b.as_uri() not in published_uris

    def test_did_close_revalidates_remaining_open_documents_against_disk(
        self, tmp_path, monkeypatch
    ):
        """Closing an unsaved imported buffer refreshes dependents from disk."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        disk_utils = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        unsaved_utils = (
            "export func helper(x: Int) -> String\n"
            '  example 1 -> "two"\n'
            '  return "two"\n'
            "end func\n"
            "export func new_api(x: Int) -> Int\n"
            "  example 1 -> 3\n"
            "  return x + 2\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(disk_utils)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_close = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CLOSE)
        completion = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=unsaved_utils,
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[main_file.as_uri()]

        items = completion(
            types.CompletionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=2),
            )
        ).items
        labels = {item.label for item in items}
        assert "new_api" in labels

        published.clear()
        did_close(
            types.DidCloseTextDocumentParams(
                text_document=types.TextDocumentIdentifier(uri=utils_file.as_uri())
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[utils_file.as_uri()] == []
        assert latest[main_file.as_uri()] == []

        items = completion(
            types.CompletionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=2),
            )
        ).items
        labels = {item.label for item in items}
        assert "new_api" not in labels
        assert "helper" in labels

    def test_did_close_does_not_switch_completion_to_another_open_project(
        self, tmp_path
    ):
        """Closing one project file keeps other open projects out of this file's view."""
        project_a = tmp_path / "project_a"
        project_b = tmp_path / "project_b"
        project_a.mkdir()
        project_b.mkdir()

        (project_a / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_a = project_a / "Main.geno"
        utils_a = project_a / "Utils.geno"
        main_a_source = (
            "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        )
        main_a.write_text(main_a_source)
        utils_a.write_text(
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )

        main_b = project_b / "Main.geno"
        other_b = project_b / "Other.geno"
        main_b_source = (
            "import Other\nfunc main() -> Int\n  return other(1)\nend func\n"
        )
        main_b.write_text(main_b_source)
        other_b.write_text(
            "export func other(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_change = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CHANGE)
        did_close = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CLOSE)
        completion = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)

        for path, source in (
            (main_a, main_a_source),
            (utils_a, utils_a.read_text()),
            (main_b, main_b_source),
        ):
            did_open(
                types.DidOpenTextDocumentParams(
                    text_document=types.TextDocumentItem(
                        uri=path.as_uri(),
                        language_id="geno",
                        version=1,
                        text=source,
                    )
                )
            )

        did_change(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(
                    uri=main_a.as_uri(),
                    version=2,
                ),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type2(text=main_a_source)
                ],
            )
        )

        labels = {
            item.label
            for item in completion(
                types.CompletionParams(
                    text_document=types.TextDocumentIdentifier(uri=main_a.as_uri()),
                    position=types.Position(line=1, character=2),
                )
            ).items
        }
        assert "helper" in labels
        assert "other" not in labels

        did_close(
            types.DidCloseTextDocumentParams(
                text_document=types.TextDocumentIdentifier(uri=utils_a.as_uri())
            )
        )

        labels = {
            item.label
            for item in completion(
                types.CompletionParams(
                    text_document=types.TextDocumentIdentifier(uri=main_a.as_uri()),
                    position=types.Position(line=1, character=2),
                )
            ).items
        }
        assert "helper" in labels
        assert "other" not in labels

    def test_completion_includes_imported_types_and_constructors_from_unsaved_module(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return 1\nend func\n"
        disk_utils = (
            "export type OldMaybe = OldSome(value: Int) | OldNone\n"
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        unsaved_utils = (
            "export type Maybe = Some(value: Int) | None\n"
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(disk_utils)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        completion = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=2,
                    text=unsaved_utils,
                )
            )
        )

        items = completion(
            types.CompletionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=2),
            )
        ).items
        item_map = {item.label: item for item in items}

        assert item_map["Maybe"].kind == types.CompletionItemKind.Class
        assert item_map["Maybe"].detail == "(from Utils)"
        assert item_map["Some"].kind == types.CompletionItemKind.EnumMember
        assert item_map["Some"].detail == "(from Utils)"
        assert item_map["None"].kind == types.CompletionItemKind.EnumMember
        assert item_map["None"].detail == "(from Utils)"
        assert "OldMaybe" not in item_map
        assert "OldSome" not in item_map
        assert "OldNone" not in item_map

    def test_completion_uses_semantic_kinds_for_current_document_type_symbols(
        self, tmp_path
    ):
        source = (
            "type Maybe = Some(value: Int) | None\n"
            "func main() -> Int\n"
            "  return 1\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        completion = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        items = completion(
            types.CompletionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=1, character=2),
            )
        ).items
        item_map = {item.label: item for item in items}

        assert item_map["Maybe"].kind == types.CompletionItemKind.Class
        assert item_map["Some"].kind == types.CompletionItemKind.EnumMember
        assert item_map["None"].kind == types.CompletionItemKind.EnumMember

    @pytest.mark.timeout(360)
    def test_completion_recovers_local_symbols_after_doc_cache_eviction(
        self, tmp_path, monkeypatch
    ):
        """Still-open documents keep local completion after LRU eviction.

        Opens _DOC_CACHE_MAX + 1 (= 513) documents to exercise the eviction
        boundary, so it is legitimately slow — ~90s in isolation, but well over
        the suite-wide 60s pytest-timeout when run inside the full suite on a
        loaded machine. Give it generous headroom rather than weakening the
        eviction-boundary coverage (the doc count is tied to the production
        _DOC_CACHE_MAX constant). The hosted lsp-tests job runs test_lsp.py on
        its own, so it sees the lighter isolated timing.
        """
        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        monkeypatch.setattr(
            lsp_server.geno,
            "check",
            lambda source, **kwargs: SimpleNamespace(diagnostics=[]),
        )
        monkeypatch.setattr(server, "publish_diagnostics", lambda uri, diags: None)

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        completion = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)

        uris: list[str] = []
        for i in range(lsp_server._DOC_CACHE_MAX + 1):
            path = tmp_path / f"Doc{i}.geno"
            source = (
                f"func local_{i}() -> Int\n"
                f"  example () -> {i}\n"
                f"  return {i}\n"
                "end func\n"
            )
            path.write_text(source)
            uri = path.as_uri()
            uris.append(uri)
            did_open(
                types.DidOpenTextDocumentParams(
                    text_document=types.TextDocumentItem(
                        uri=uri,
                        language_id="geno",
                        version=1,
                        text=source,
                    )
                )
            )

        items = completion(
            types.CompletionParams(
                text_document=types.TextDocumentIdentifier(uri=uris[0]),
                position=types.Position(line=0, character=2),
            )
        ).items
        labels = {item.label for item in items}

        assert "local_0" in labels


class TestProjectViewCaching:
    """Performance-oriented regressions for project view reuse."""

    def test_did_change_reuses_one_project_view_for_shared_project(
        self, tmp_path, monkeypatch
    ):
        """Editing one file rebuilds one shared project view, not one per doc."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        good_utils = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        bad_utils = (
            "export func helper(x: Int) -> String\n"
            '  example 1 -> "two"\n'
            '  return "two"\n'
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(good_utils)

        load_calls = 0
        original = lsp_server._load_project_view

        def counting_load(*args, **kwargs):
            nonlocal load_calls
            load_calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(lsp_server, "_load_project_view", counting_load)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_change = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CHANGE)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=good_utils,
                )
            )
        )

        load_calls = 0
        did_change(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(
                    uri=utils_file.as_uri(),
                    version=2,
                ),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type2(text=bad_utils)
                ],
            )
        )

        assert load_calls == 1

    def test_completion_reuses_cached_project_view(self, tmp_path, monkeypatch):
        """Repeated completion on an unchanged doc does not rebuild project views."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        utils_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        load_calls = 0
        original = lsp_server._load_project_view

        def counting_load(*args, **kwargs):
            nonlocal load_calls
            load_calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(lsp_server, "_load_project_view", counting_load)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        completion = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        load_calls = 0
        for _ in range(2):
            completion(
                types.CompletionParams(
                    text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                    position=types.Position(line=1, character=2),
                )
            )

        assert load_calls == 0


class TestSymbolTableCaching:
    """Performance-oriented regressions for semantic symbol-table reuse."""

    def test_references_reuse_cached_symbol_table(self, tmp_path, monkeypatch):
        """Repeated semantic lookups on an unchanged doc reuse one symbol table."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        utils_source = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_source)

        build_calls = 0
        original = lsp_server._build_symbol_table_for_document

        def counting_build(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(
            lsp_server, "_build_symbol_table_for_document", counting_build
        )

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_source,
                )
            )
        )

        build_calls = 0
        for _ in range(2):
            result = references(
                types.ReferenceParams(
                    text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                    position=types.Position(line=2, character=11),
                    context=types.ReferenceContext(include_declaration=True),
                )
            )
            assert result is not None

        assert build_calls == 1

    def test_import_change_invalidates_cached_symbol_table(self, tmp_path, monkeypatch):
        """Changing an imported module forces one fresh semantic rebuild."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Utils"]\n'
        )
        main_file = tmp_path / "Main.geno"
        utils_file = tmp_path / "Utils.geno"
        main_source = "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        utils_v1 = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        utils_v2 = (
            "export func helper(x: Int) -> Int\n"
            "  example 1 -> 3\n"
            "  return x + 2\n"
            "end func\n"
        )
        main_file.write_text(main_source)
        utils_file.write_text(utils_v1)

        build_calls = 0
        original = lsp_server._build_symbol_table_for_document

        def counting_build(*args, **kwargs):
            nonlocal build_calls
            build_calls += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(
            lsp_server, "_build_symbol_table_for_document", counting_build
        )

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_change = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CHANGE)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=utils_v1,
                )
            )
        )

        first = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=11),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        assert first is not None

        build_calls = 0
        did_change(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(
                    uri=utils_file.as_uri(),
                    version=2,
                ),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type2(text=utils_v2)
                ],
            )
        )
        second = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=2, character=11),
                context=types.ReferenceContext(include_declaration=True),
            )
        )
        assert second is not None
        assert build_calls == 1


class TestExportVisibility:
    """Tests that the LSP respects export visibility."""

    def test_no_exports_all_visible(self):
        """Module with no export keywords exposes everything."""
        source = (
            "func public_a(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            "  return x\n"
            "end func\n"
            "func public_b(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            "  return x\n"
            "end func\n"
        )
        all_names, exported = _extract_names(source)
        assert all_names == ["public_a", "public_b"]
        assert exported == ["public_a", "public_b"]

    def test_explicit_exports_filter_private(self):
        """Module with export keywords only exposes exported symbols."""
        source = (
            "export func visible(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            "  return x\n"
            "end func\n"
            "func private_helper(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            "  return x\n"
            "end func\n"
            "export type Color = Red | Green\n"
        )
        all_names, exported = _extract_names(source)
        assert set(all_names) == {"visible", "private_helper", "Color"}
        assert set(exported) == {"visible", "Color"}
        assert "private_helper" not in exported

    def test_exported_type_included(self):
        """Exported types appear in exported_names."""
        source = "export type Shape = Circle | Square\ntype Internal = A | B\n"
        _all_names, exported = _extract_names(source)
        assert "Shape" in exported
        assert "Internal" not in exported

    def test_export_with_untested_annotation(self):
        """export @untested(...) func is recognized as exported."""
        source = (
            'export @untested("reason") func foo(x: Int) -> Int\n'
            "  return x\n"
            "end func\n"
            "func bar(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            "  return x\n"
            "end func\n"
        )
        all_names, exported = _extract_names(source)
        assert set(all_names) == {"foo", "bar"}
        assert exported == ["foo"]

    def test_completion_uses_exported_names(self):
        """Cross-module completion only offers exported symbols."""
        # Module with exports
        lib_source = (
            "export func api_call(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            "  return x\n"
            "end func\n"
            "func internal_helper(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            "  return x\n"
            "end func\n"
        )
        _all_names, exported = _extract_names(lib_source)
        # Simulate the completion loop (uses exported, not all)
        completion_items = list(exported)
        assert "api_call" in completion_items
        assert "internal_helper" not in completion_items


# ---------------------------------------------------------------------------
# Symbol table tests (#214)
# ---------------------------------------------------------------------------


def _build_table(
    source: str,
    filename: str = "test.geno",
    modules: dict[str, str] | None = None,
) -> SymbolTable:
    from geno.lexer import Lexer
    from geno.parser import Parser

    tokens = Lexer(source, filename).tokenize()
    program = Parser(tokens).parse_program()
    parsed_modules = None
    if modules:
        parsed_modules = {}
        for module_name, module_source in modules.items():
            module_filename = f"{module_name}.geno"
            parsed_modules[module_name] = Parser(
                Lexer(module_source, module_filename).tokenize()
            ).parse_program()
    return build_symbol_table(program, filename, parsed_modules)


class TestSymbolTableScoping:
    """Regression tests for shadowing and same-spelled identifiers."""

    def test_shadowed_variable_not_confused(self):
        """x in double and x in main are separate symbols."""
        source = (
            "func double(x: Int) -> Int\n"
            "    example (2) -> 4\n"
            "    return x * 2\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "    example () -> 4\n"
            "    let x: Int = double(2)\n"
            "    return x\n"
            "end func\n"
        )
        table = _build_table(source)

        # Find both x definitions
        x_defs = [d for d in table.definitions if d.name == "x"]
        assert len(x_defs) == 2

        param_x = [d for d in x_defs if d.kind == "parameter"][0]
        var_x = [d for d in x_defs if d.kind == "variable"][0]

        # References to param x (in double's body)
        param_refs = table.refs_for_def(param_x)
        assert len(param_refs) >= 1
        assert all(r.location.line == 3 for r in param_refs)  # return x * 2

        # References to var x (in main's body)
        var_refs = table.refs_for_def(var_x)
        assert len(var_refs) >= 1
        assert all(r.location.line == 9 for r in var_refs)  # return x

    def test_for_loop_variable_scoped(self):
        """for-loop variable does not leak into outer scope."""
        source = (
            "func f(items: List[Int]) -> Int\n"
            "    example [1] -> 1\n"
            "    let x: Int = 0\n"
            "    for x: Int in items do\n"
            "        let y: Int = x + 1\n"
            "    end for\n"
            "    return x\n"
            "end func\n"
        )
        table = _build_table(source)

        x_defs = [d for d in table.definitions if d.name == "x"]
        # Should have two x definitions: let and for-loop variable
        assert len(x_defs) == 2

    def test_match_arm_bindings_scoped(self):
        """Variables bound in match arms don't escape."""
        source = (
            "type Result = Ok(value: Int) | Err(msg: String)\n"
            "\n"
            "func unwrap(r: Result) -> Int\n"
            "    example Ok(42) -> 42\n"
            "    match r with\n"
            "        | Ok(v) -> return v\n"
            "        | Err(e) -> return 0\n"
            "    end match\n"
            "end func\n"
        )
        table = _build_table(source)

        # v and e should be separate definitions
        v_defs = [d for d in table.definitions if d.name == "v"]
        e_defs = [d for d in table.definitions if d.name == "e"]
        assert len(v_defs) == 1
        assert len(e_defs) == 1

    def test_lambda_params_scoped(self):
        """Lambda parameter x does not conflict with outer x."""
        source = (
            "func apply(x: Int) -> Int\n"
            "    example (1) -> 2\n"
            "    let f: (Int) -> Int = fn(x: Int) -> x + 1\n"
            "    return f(x)\n"
            "end func\n"
        )
        table = _build_table(source)

        x_defs = [d for d in table.definitions if d.name == "x"]
        # Should have 2: function param and lambda param
        assert len(x_defs) == 2

    def test_function_refs_resolved(self):
        """References to a function point to its definition."""
        source = (
            "func add(a: Int, b: Int) -> Int\n"
            "    example (1, 2) -> 3\n"
            "    return a + b\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "    example () -> 3\n"
            "    return add(1, 2)\n"
            "end func\n"
        )
        table = _build_table(source)

        add_def = [d for d in table.definitions if d.name == "add"][0]
        add_refs = table.refs_for_def(add_def)
        assert len(add_refs) == 1
        assert add_refs[0].location.line == 8  # add(1, 2)

    def test_type_and_constructor_refs(self):
        """Type names and constructor names are resolved."""
        source = (
            "type Color = Red | Green | Blue\n"
            "\n"
            "func favorite() -> Color\n"
            "    example () -> Red\n"
            "    return Red\n"
            "end func\n"
        )
        table = _build_table(source)

        red_def = [d for d in table.definitions if d.name == "Red"][0]
        red_refs = table.refs_for_def(red_def)
        assert len(red_refs) >= 1  # return Red

    def test_symbol_at_definition(self):
        """symbol_at returns the definition when cursor is on it."""
        source = "func foo() -> Int\n    example () -> 1\n    return 1\nend func\n"
        table = _build_table(source)
        # FunctionDef location is at line start; use name fallback
        defn = table.symbol_at("test.geno", 1, 6, name="foo")
        assert defn is not None
        assert defn.name == "foo"

    def test_symbol_at_reference(self):
        """symbol_at returns the definition when cursor is on a reference."""
        source = (
            "func foo() -> Int\n"
            "    example () -> 1\n"
            "    return 1\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "    example () -> 1\n"
            "    return foo()\n"
            "end func\n"
        )
        table = _build_table(source)
        # foo() call is at line 8 — find exact col
        refs = [r for r in table.references if r.name == "foo"]
        assert len(refs) == 1
        defn = table.symbol_at(
            "test.geno", refs[0].location.line, refs[0].location.column
        )
        assert defn is not None
        assert defn.name == "foo"
        assert defn.kind == "function"

    def test_unqualified_imported_function_refs_resolved(self):
        """Plain imports bind exported members semantically for unqualified use."""
        table = _build_table(
            "import Utils\nfunc main() -> Int\n    return helper(1)\nend func\n",
            filename="Main.geno",
            modules={
                "Utils": "export func helper(x: Int) -> Int\n"
                "    example 1 -> 2\n"
                "    return x + 1\n"
                "end func\n"
            },
        )

        helper_refs = [r for r in table.references if r.name == "helper"]
        assert len(helper_refs) == 1
        assert helper_refs[0].definition.location.filename == "Utils.geno"

        defn = table.symbol_at("Main.geno", 3, 12, name="helper")
        assert defn is not None
        assert defn.name == "helper"
        assert defn.location.filename == "Utils.geno"

    def test_imported_type_annotation_refs_resolved(self):
        """Plain imports resolve exported type names inside annotations."""
        table = _build_table(
            "import Utils\nfunc make() -> Maybe\n    return Some(1)\nend func\n",
            filename="Main.geno",
            modules={"Utils": "export type Maybe = Some(value: Int) | None\n"},
        )

        maybe_def = [
            d
            for d in table.definitions
            if d.name == "Maybe"
            and d.kind == "type"
            and d.location.filename == "Utils.geno"
        ][0]
        maybe_refs = table.refs_for_def(maybe_def)
        assert len(maybe_refs) == 1
        assert maybe_refs[0].location.filename == "Main.geno"

        defn = table.symbol_at(
            "Main.geno",
            maybe_refs[0].location.line,
            maybe_refs[0].location.column,
            name="Maybe",
        )
        assert defn is not None
        assert defn.name == "Maybe"
        assert defn.location.filename == "Utils.geno"

    def test_imported_constructor_call_refs_resolved(self):
        """Plain imports resolve exported constructors in constructor calls."""
        table = _build_table(
            "import Utils\nfunc make() -> Maybe\n    return Some(1)\nend func\n",
            filename="Main.geno",
            modules={"Utils": "export type Maybe = Some(value: Int) | None\n"},
        )

        some_def = [
            d
            for d in table.definitions
            if d.name == "Some"
            and d.kind == "constructor"
            and d.location.filename == "Utils.geno"
        ][0]
        some_refs = table.refs_for_def(some_def)
        assert len(some_refs) == 1
        assert some_refs[0].location.filename == "Main.geno"

        defn = table.symbol_at(
            "Main.geno",
            some_refs[0].location.line,
            some_refs[0].location.column,
            name="Some",
        )
        assert defn is not None
        assert defn.name == "Some"
        assert defn.location.filename == "Utils.geno"

    def test_lambda_annotation_refs_resolved(self):
        """Imported type names in lambda annotations resolve semantically."""
        table = _build_table(
            "import Utils\n"
            "func main() -> Int\n"
            "    let f: (Int) -> Int = fn(value: Maybe) -> 1\n"
            "    return 1\n"
            "end func\n",
            filename="Main.geno",
            modules={"Utils": "export type Maybe = Some(value: Int) | None\n"},
        )

        maybe_def = [
            d
            for d in table.definitions
            if d.name == "Maybe"
            and d.kind == "type"
            and d.location.filename == "Utils.geno"
        ][0]
        maybe_refs = table.refs_for_def(maybe_def)
        assert len(maybe_refs) == 1
        assert maybe_refs[0].location.filename == "Main.geno"

        defn = table.symbol_at(
            "Main.geno",
            maybe_refs[0].location.line,
            maybe_refs[0].location.column,
            name="Maybe",
        )
        assert defn is not None
        assert defn.name == "Maybe"
        assert defn.location.filename == "Utils.geno"

    def test_trait_method_annotation_refs_resolved(self):
        """Imported type names in trait signatures resolve semantically."""
        table = _build_table(
            "import Utils\n"
            "trait Describable\n"
            "    func describe(value: AliasId) -> AliasId\n"
            "end trait\n",
            filename="Main.geno",
            modules={"Utils": "export type AliasId = Int\n"},
        )

        alias_def = [
            d
            for d in table.definitions
            if d.name == "AliasId"
            and d.kind == "type"
            and d.location.filename == "Utils.geno"
        ][0]
        alias_refs = table.refs_for_def(alias_def)
        assert len(alias_refs) == 2
        assert all(ref.location.filename == "Main.geno" for ref in alias_refs)

        defn = table.symbol_at(
            "Main.geno",
            alias_refs[0].location.line,
            alias_refs[0].location.column,
            name="AliasId",
        )
        assert defn is not None
        assert defn.name == "AliasId"
        assert defn.location.filename == "Utils.geno"

    def test_impl_target_type_refs_resolved(self):
        """Impl target types and impl method annotations resolve semantically."""
        table = _build_table(
            "import Utils\n"
            "trait Describable\n"
            "    func describe(value: Self) -> Int\n"
            "end trait\n"
            "impl Describable for AliasId\n"
            "    func describe(value: AliasId) -> Int\n"
            "        example 1 -> 1\n"
            "        return value\n"
            "    end func\n"
            "end impl\n",
            filename="Main.geno",
            modules={"Utils": "export type AliasId = Int\n"},
        )

        alias_def = [
            d
            for d in table.definitions
            if d.name == "AliasId"
            and d.kind == "type"
            and d.location.filename == "Utils.geno"
        ][0]
        alias_refs = table.refs_for_def(alias_def)
        assert len(alias_refs) == 2
        assert all(ref.location.filename == "Main.geno" for ref in alias_refs)

        defn = table.symbol_at(
            "Main.geno",
            5,
            23,
            name="AliasId",
        )
        assert defn is not None
        assert defn.name == "AliasId"
        assert defn.location.filename == "Utils.geno"

    def test_imported_impl_trait_refs_resolved(self):
        """Imported trait names in impl headers resolve semantically."""
        table = _build_table(
            "import Utils\n"
            "impl AliasTrait for Int\n"
            "    func convert(value: Int) -> Int\n"
            "        example 1 -> 1\n"
            "        return value\n"
            "    end func\n"
            "end impl\n",
            filename="Main.geno",
            modules={
                "Utils": "trait AliasTrait\n"
                "    func convert(value: Int) -> Int\n"
                "end trait\n"
            },
        )

        trait_def = [
            d
            for d in table.definitions
            if d.name == "AliasTrait"
            and d.kind == "trait"
            and d.location.filename == "Utils.geno"
        ][0]
        trait_refs = table.refs_for_def(trait_def)
        assert len(trait_refs) == 1
        assert trait_refs[0].location.filename == "Main.geno"

        defn = table.symbol_at(
            "Main.geno",
            trait_refs[0].location.line,
            trait_refs[0].location.column,
            name="AliasTrait",
        )
        assert defn is not None
        assert defn.name == "AliasTrait"
        assert defn.location.filename == "Utils.geno"

    def test_transitive_unqualified_imports_resolved(self):
        """Transitive plain imports keep exported names visible semantically."""
        table = _build_table(
            "import Mid\nfunc main() -> Int\n    return leaf_value()\nend func\n",
            filename="Main.geno",
            modules={
                "Mid": "import Leaf\n"
                "func mid_value() -> Int\n"
                "    example () -> 7\n"
                "    return leaf_value()\n"
                "end func\n",
                "Leaf": "export func leaf_value() -> Int\n"
                "    example () -> 7\n"
                "    return 7\n"
                "end func\n",
            },
        )

        defn = table.symbol_at("Main.geno", 3, 12, name="leaf_value")
        assert defn is not None
        assert defn.name == "leaf_value"
        assert defn.location.filename == "Leaf.geno"

    def test_aliased_module_field_access_resolves_member(self):
        """Aliased module namespaces resolve member refs semantically."""
        table = _build_table(
            "import Math as M\nfunc main() -> Int\n    return M.double(2)\nend func\n",
            filename="Main.geno",
            modules={
                "Math": "export func double(x: Int) -> Int\n"
                "    example 2 -> 4\n"
                "    return x * 2\n"
                "end func\n"
            },
        )

        double_refs = [r for r in table.references if r.name == "double"]
        assert len(double_refs) == 1
        assert double_refs[0].definition.location.filename == "Math.geno"


class TestBoundedDict:
    """Tests for the LRU-evicting _BoundedDict used by LSP caches."""

    def test_evicts_oldest_when_full(self):
        from geno.lsp_server import _BoundedDict

        d = _BoundedDict(maxsize=3)
        d["a"] = 1
        d["b"] = 2
        d["c"] = 3
        d["d"] = 4  # should evict "a"
        assert "a" not in d
        assert list(d.keys()) == ["b", "c", "d"]

    def test_access_refreshes_order(self):
        from geno.lsp_server import _BoundedDict

        d = _BoundedDict(maxsize=3)
        d["a"] = 1
        d["b"] = 2
        d["c"] = 3
        _ = d["a"]  # refresh "a" to most recent
        d["d"] = 4  # should evict "b", not "a"
        assert "a" in d
        assert "b" not in d

    def test_overwrite_refreshes_order(self):
        from geno.lsp_server import _BoundedDict

        d = _BoundedDict(maxsize=2)
        d["a"] = 1
        d["b"] = 2
        d["a"] = 10  # overwrite refreshes "a"
        d["c"] = 3  # evicts "b"
        assert d["a"] == 10
        assert "b" not in d

    def test_many_inserts_stay_bounded(self):
        from geno.lsp_server import _BoundedDict

        d = _BoundedDict(maxsize=50)
        for i in range(500):
            d[f"key-{i}"] = i
        assert len(d) == 50
        # Most recent 50 keys remain
        assert "key-499" in d
        assert "key-449" not in d

    def test_threaded_updates_keep_order_consistent(self):
        from geno.lsp_server import _BoundedDict

        d = _BoundedDict(maxsize=10)
        errors: list[BaseException] = []
        error_lock = threading.Lock()

        def worker(offset: int) -> None:
            try:
                for i in range(200):
                    key = f"{offset}-{i % 20}"
                    d[key] = i
                    assert d.get(key) is not None
                    if i % 5 == 0:
                        d.pop(f"{offset}-{(i - 7) % 20}", None)
            except BaseException as exc:
                with error_lock:
                    errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(offset,)) for offset in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        with d._lock:
            assert len(d) <= 10
            assert len(d._order) == len(d)
            assert set(d._order) == set(d.keys())


class TestIsInCommentOrString:
    """Regression tests for _is_in_comment_or_string (#545)."""

    def test_identifier_inside_line_comment_is_skipped(self):
        from geno.lsp_server import _is_in_comment_or_string

        # "// call foo here" -- 'foo' starts at index 8
        line = "// call foo here"
        assert _is_in_comment_or_string(line, 8) is True

    def test_identifier_inside_doc_comment_is_skipped(self):
        from geno.lsp_server import _is_in_comment_or_string

        # "/// foo is documented" -- 'foo' starts at index 4
        line = "/// foo is documented"
        assert _is_in_comment_or_string(line, 4) is True

    def test_identifier_inside_string_literal_is_skipped(self):
        from geno.lsp_server import _is_in_comment_or_string

        line = 'let x: String = "foo"'
        # 'foo' starts at index 18, inside the string
        assert _is_in_comment_or_string(line, 18) is True

    def test_identifier_in_code_is_not_skipped(self):
        from geno.lsp_server import _is_in_comment_or_string

        line = "let foo: Int = 1"
        # 'foo' starts at index 4
        assert _is_in_comment_or_string(line, 4) is False

    def test_identifier_after_string_in_code_is_not_skipped(self):
        from geno.lsp_server import _is_in_comment_or_string

        line = 'let x: String = "hello" + foo'
        # 'foo' starts at index 26
        assert _is_in_comment_or_string(line, 26) is False

    def test_code_with_slash_but_not_comment(self):
        from geno.lsp_server import _is_in_comment_or_string

        # A single slash should not be treated as a comment
        line = "let x: Int = a / b"
        # 'b' starts at index 17
        assert _is_in_comment_or_string(line, 17) is False

    def test_references_skip_identifiers_in_line_comments(self, tmp_path):
        """Integration: find-references must not match names in // comments."""
        source = (
            "func foo() -> Int\n"
            "  example () -> 1\n"
            "  return 1\n"
            "end func\n"
            "// foo is great\n"
            "func main() -> Int\n"
            "  return foo()\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=0, character=5),
                context=types.ReferenceContext(include_declaration=True),
            )
        )

        assert refs is not None
        ref_lines = {ref.range.start.line for ref in refs}
        # Line 4 is "// foo is great" -- should NOT appear in references
        assert 4 not in ref_lines
        # Lines 0 (definition) and 6 (call) should appear
        assert 0 in ref_lines
        assert 6 in ref_lines

    def test_references_skip_identifiers_in_block_comments(self, tmp_path):
        """Integration: find-references must not match names in /* */ comments."""
        source = (
            "func foo() -> Int\n"
            "  example () -> 1\n"
            "  return 1\n"
            "end func\n"
            "/* foo should be ignored\n"
            "   foo is still ignored */\n"
            "func main() -> Int\n"
            "  return foo()\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        refs = references(
            types.ReferenceParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                position=types.Position(line=0, character=5),
                context=types.ReferenceContext(include_declaration=True),
            )
        )

        assert refs is not None
        ref_lines = {ref.range.start.line for ref in refs}
        assert 4 not in ref_lines
        assert 5 not in ref_lines
        assert 0 in ref_lines
        assert 7 in ref_lines


# ---------------------------------------------------------------------------
# New tests for LSP overhaul
# ---------------------------------------------------------------------------


class TestDebouncedDiagnostics:
    """Verify that did_change debounces diagnostics."""

    def test_did_change_does_not_publish_immediately_with_debounce(
        self, tmp_path, monkeypatch
    ):
        """With a non-zero debounce, did_change should not publish immediately."""
        source = "func foo() -> Int\n  example () -> 1\n  return 1\nend func\n"
        file_path = tmp_path / "test.geno"
        file_path.write_text(source)
        uri = file_path.as_uri()

        server = create_server(diag_debounce_sec=10.0)  # Long debounce
        server.lsp._workspace = Workspace(
            None, sync_kind=types.TextDocumentSyncKind.Full
        )
        published: list[tuple[str, list]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda u, d: published.append((u, list(d))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_change = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CHANGE)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=uri, language_id="geno", version=1, text=source
                )
            )
        )
        published.clear()

        # did_change with debounce should NOT publish immediately
        did_change(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(uri=uri, version=2),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type2(text=source)
                ],
            )
        )
        assert len(published) == 0

    def test_did_close_cancels_pending_debounce_and_clears_version_state(
        self, tmp_path, monkeypatch
    ):
        """Closing an edited document clears pending timer and versioned state."""
        source = "func foo() -> Int\n  example () -> 1\n  return 1\nend func\n"
        changed = "func foo() -> Int\n  example () -> 2\n  return 2\nend func\n"
        file_path = tmp_path / "test.geno"
        file_path.write_text(source)
        uri = file_path.as_uri()

        server = create_server(diag_debounce_sec=10.0)
        server.lsp._workspace = Workspace(
            None, sync_kind=types.TextDocumentSyncKind.Full
        )
        monkeypatch.setattr(server, "publish_diagnostics", lambda _u, _d: None)

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_change = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CHANGE)
        did_close = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_CLOSE)
        lsp_wrapper = server._geno_language_server

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=uri, language_id="geno", version=1, text=source
                )
            )
        )
        did_change(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(uri=uri, version=2),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type2(text=changed)
                ],
            )
        )
        assert uri in lsp_wrapper._debounce_timers

        did_close(
            types.DidCloseTextDocumentParams(
                text_document=types.TextDocumentIdentifier(uri=uri)
            )
        )

        assert uri not in lsp_wrapper._debounce_timers
        assert uri not in lsp_wrapper._open_docs
        assert uri not in lsp_wrapper._open_doc_paths
        assert uri not in lsp_wrapper._open_doc_versions
        assert uri not in lsp_wrapper._open_doc_lsp_versions

    def test_did_save_publishes_immediately(self, tmp_path, monkeypatch):
        """did_save should always publish diagnostics without debounce."""
        source = "func foo() -> Int\n  example () -> 1\n  return 1\nend func\n"
        file_path = tmp_path / "test.geno"
        file_path.write_text(source)
        uri = file_path.as_uri()

        server = create_server(diag_debounce_sec=10.0)  # Long debounce
        server.lsp._workspace = Workspace(
            None, sync_kind=types.TextDocumentSyncKind.Full
        )
        published: list[tuple[str, list]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda u, d: published.append((u, list(d))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_save = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_SAVE)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=uri, language_id="geno", version=1, text=source
                )
            )
        )
        published.clear()

        did_save(
            types.DidSaveTextDocumentParams(
                text_document=types.TextDocumentIdentifier(uri=uri)
            )
        )
        assert len(published) > 0


class TestEffectCompletions:
    """Verify effect name completions after 'with' in function signatures."""

    def test_effect_completion_after_with(self, tmp_path):
        """Typing 'with' in a function signature offers effect names."""
        source = "func foo() -> Int with \n  return 1\nend func\n"
        file_path = tmp_path / "test_effects.geno"
        uri = file_path.as_uri()

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None, sync_kind=types.TextDocumentSyncKind.Full
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        completion_handler = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=uri, language_id="geno", version=1, text=source
                )
            )
        )

        result = completion_handler(
            types.CompletionParams(
                text_document=types.TextDocumentIdentifier(uri=uri),
                position=types.Position(line=0, character=23),
            )
        )

        labels = {item.label for item in result.items}
        assert "io" in labels
        assert "fs" in labels
        assert "http" in labels
        # All should be keyword kind
        for item in result.items:
            assert item.kind == types.CompletionItemKind.Keyword
            assert item.detail == "(effect)"


class TestEffectAnnotationsInSignature:
    """Verify effect annotations in function signatures for hover/sig help."""

    def test_extract_user_func_sig_with_effects(self):
        """_extract_user_func_sig captures 'with' effects."""
        from geno.lsp_server import _extract_user_func_sig

        source = "func read_file(path: String) -> String with io, fs\n  return path\nend func\n"
        result = _extract_user_func_sig("read_file", source)
        assert result is not None
        label, params = result
        assert "with io, fs" in label
        assert "-> String" in label
        assert len(params) == 1

    def test_extract_user_func_sig_async_with_effects(self):
        """_extract_user_func_sig captures async function signatures."""
        from geno.lsp_server import _extract_user_func_sig

        source = (
            "async func fetch(url: String) -> String with http\n"
            "  return url\n"
            "end func\n"
        )
        result = _extract_user_func_sig("fetch", source)
        assert result is not None
        label, params = result
        assert label == "fetch(url: String) -> String with http"
        assert len(params) == 1

    def test_extract_user_func_sig_without_effects(self):
        """_extract_user_func_sig still works without effects."""
        from geno.lsp_server import _extract_user_func_sig

        source = "func add(a: Int, b: Int) -> Int\n  return a + b\nend func\n"
        result = _extract_user_func_sig("add", source)
        assert result is not None
        label, params = result
        assert "with" not in label
        assert "-> Int" in label
        assert len(params) == 2


class TestPartialParseRecovery:
    """Verify LSP features work with incomplete/errored source code."""

    def test_completion_symbols_from_partial_parse(self):
        """Completions work even when file has syntax errors."""
        from geno.lsp_server import _extract_completion_symbols

        # First function is complete, second is broken
        source = (
            "func good(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            "  return x\n"
            "end func\n"
            "\n"
            "func broken(\n"
        )
        all_syms, _exported = _extract_completion_symbols(source)
        names = [s.name for s in all_syms]
        assert "good" in names

    def test_parse_error_has_partial_program(self):
        """ParseError/ParseErrors carry partial_program attribute."""
        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.parser_base import ParseError as _ParseError
        from geno.parser_base import ParseErrors as _ParseErrors

        source = (
            "func complete(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            "  return x\n"
            "end func\n"
            "\n"
            "func broken(\n"
        )
        tokens = Lexer(source, "test.geno").tokenize()
        parser = Parser(tokens)
        try:
            parser.parse_program()
            raise AssertionError("Should have raised a parse error")
        except (_ParseError, _ParseErrors) as e:
            assert e.partial_program is not None
            partial_program = cast(Program, e.partial_program)
            assert len(partial_program.definitions) >= 1
            # The first function should be there
            first_def = cast(FunctionDef, partial_program.definitions[0])
            assert first_def.name == "complete"


class TestCodeActions:
    """Verify code action features."""

    def test_code_action_import_module(self, tmp_path, monkeypatch):
        """Unknown name with matching exported symbol offers import action."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Helpers"]\n'
        )
        helpers_file = tmp_path / "Helpers.geno"
        helpers_file.write_text(
            "export func do_stuff(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_source = "func main() -> Int\n  return do_stuff(1)\nend func\n"
        main_file.write_text(main_source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None, sync_kind=types.TextDocumentSyncKind.Full
        )
        monkeypatch.setattr(server, "publish_diagnostics", lambda u, d: None)

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        code_action_handler = server.lsp._get_handler(types.TEXT_DOCUMENT_CODE_ACTION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=main_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=main_source,
                )
            )
        )

        # Simulate the real Geno diagnostic for an unresolved function name.
        diag = types.Diagnostic(
            range=types.Range(
                start=types.Position(line=1, character=9),
                end=types.Position(line=1, character=17),
            ),
            severity=types.DiagnosticSeverity.Error,
            source="geno",
            message="Undefined variable: do_stuff",
        )

        actions = code_action_handler(
            types.CodeActionParams(
                text_document=types.TextDocumentIdentifier(uri=main_file.as_uri()),
                range=types.Range(
                    start=types.Position(line=1, character=0),
                    end=types.Position(line=1, character=20),
                ),
                context=types.CodeActionContext(diagnostics=[diag]),
            )
        )

        titles = [a.title for a in actions]
        assert any("Import" in t and "Helpers" in t for t in titles)

    def test_code_action_add_missing_end(self, tmp_path, monkeypatch):
        """Real missing-end parser diagnostics offer an add-end quick fix."""
        source = "func foo() -> Int\n  return 1\n"
        file_path = tmp_path / "test_codeaction.geno"
        uri = file_path.as_uri()

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None, sync_kind=types.TextDocumentSyncKind.Full
        )
        monkeypatch.setattr(server, "publish_diagnostics", lambda u, d: None)

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        code_action_handler = server.lsp._get_handler(types.TEXT_DOCUMENT_CODE_ACTION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=uri, language_id="geno", version=1, text=source
                )
            )
        )

        diag = types.Diagnostic(
            range=types.Range(
                start=types.Position(line=2, character=0),
                end=types.Position(line=2, character=0),
            ),
            severity=types.DiagnosticSeverity.Error,
            source="geno",
            message="Expected 'end', got end of file",
        )

        actions = code_action_handler(
            types.CodeActionParams(
                text_document=types.TextDocumentIdentifier(uri=uri),
                range=types.Range(
                    start=types.Position(line=1, character=0),
                    end=types.Position(line=1, character=10),
                ),
                context=types.CodeActionContext(diagnostics=[diag]),
            )
        )

        titles = [a.title for a in actions]
        assert any("end func" in t for t in titles)


class TestContextAwareCompletions:
    """Verify the new dot and match-pattern completion paths."""

    def test_dot_completion_uses_user_defined_type_fields(self, tmp_path, monkeypatch):
        source = (
            "type User = Person(name: String, age: Int)\n"
            "func main() -> String\n"
            '  let user: User = Person(name: "Ada", age: 37)\n'
            "  return user.\n"
            "end func\n"
        )
        file_path = tmp_path / "Main.geno"
        file_path.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None, sync_kind=types.TextDocumentSyncKind.Full
        )
        monkeypatch.setattr(server, "publish_diagnostics", lambda u, d: None)

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        completion_handler = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=file_path.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        result = completion_handler(
            types.CompletionParams(
                text_document=types.TextDocumentIdentifier(uri=file_path.as_uri()),
                position=types.Position(line=3, character=14),
            )
        )

        item_map = {item.label: item for item in result.items}
        assert item_map["name"].kind == types.CompletionItemKind.Field
        assert item_map["age"].kind == types.CompletionItemKind.Field

    def test_match_pattern_completion_uses_user_defined_variants(
        self, tmp_path, monkeypatch
    ):
        source = (
            "type Maybe = Some(value: Int) | None\n"
            "func main() -> Int\n"
            "  let x: Maybe = Some(value: 1)\n"
            "  match x with\n"
            "    | \n"
            "  end match\n"
            "end func\n"
        )
        file_path = tmp_path / "Main.geno"
        file_path.write_text(source)

        server = create_server(diag_debounce_sec=0)
        server.lsp._workspace = Workspace(
            None, sync_kind=types.TextDocumentSyncKind.Full
        )
        monkeypatch.setattr(server, "publish_diagnostics", lambda u, d: None)

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        completion_handler = server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=file_path.as_uri(),
                    language_id="geno",
                    version=1,
                    text=source,
                )
            )
        )

        result = completion_handler(
            types.CompletionParams(
                text_document=types.TextDocumentIdentifier(uri=file_path.as_uri()),
                position=types.Position(line=4, character=6),
            )
        )

        item_map = {item.label: item for item in result.items}
        assert item_map["Some"].kind == types.CompletionItemKind.EnumMember
        assert item_map["None"].kind == types.CompletionItemKind.EnumMember
