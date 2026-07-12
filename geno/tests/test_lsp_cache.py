"""Dependency-free tests for LSP cache helpers."""

import threading
from types import SimpleNamespace

import pytest

import geno.lsp_server as lsp_server
from geno.lsp_cache import (
    project_view_cache_key,
    project_view_keys_for_path,
    symbol_table_keys_for_path,
)
from geno.lsp_completions import extract_completion_symbols


class TestCompletionSymbolCache:
    def test_completion_module_extracts_traits_and_exported_symbols(self):
        source = "trait Show\nend trait\n\ntype Maybe = Some(value: Int) | None\n"

        all_symbols, exported_symbols = extract_completion_symbols(source)

        assert [(symbol.name, symbol.kind) for symbol in all_symbols] == [
            ("Show", "trait"),
            ("Maybe", "type"),
            ("Some", "constructor"),
            ("None", "constructor"),
        ]
        assert exported_symbols == all_symbols

        export_source = (
            "export func shown() -> Int\n  return 1\nend func\n\n"
            "func hidden() -> Int\n  return 2\nend func\n"
        )
        _all_symbols, exported_symbols = extract_completion_symbols(export_source)
        assert [(symbol.name, symbol.kind) for symbol in exported_symbols] == [
            ("shown", "function")
        ]

    def test_completion_symbols_rebuild_from_open_document_on_cache_miss(self):
        uri = "file:///tmp/Doc0.geno"
        source = "func local_0() -> Int\n  example () -> 0\n  return 0\nend func\n"
        doc_cache = lsp_server._BoundedDict(maxsize=1)
        doc_cache["file:///tmp/Other.geno"] = ("func other() -> Int\nend func\n", [])

        symbols = lsp_server._completion_symbols_for_uri(uri, doc_cache, {uri: source})

        assert symbols is not None
        assert [symbol.name for symbol in symbols] == ["local_0"]
        assert uri in doc_cache
        assert "file:///tmp/Other.geno" not in doc_cache


class TestDocumentVersionState:
    def test_document_lsp_version_accepts_real_integer_versions(self):
        assert (
            lsp_server.GenoLanguageServer._document_lsp_version(
                SimpleNamespace(version=7)
            )
            == 7
        )
        assert (
            lsp_server.GenoLanguageServer._document_lsp_version(
                SimpleNamespace(version=True)
            )
            is None
        )
        assert (
            lsp_server.GenoLanguageServer._document_lsp_version(SimpleNamespace())
            is None
        )

    def test_stale_document_version_detects_older_version(self):
        uri = "file:///tmp/Main.geno"
        server = object.__new__(lsp_server.GenoLanguageServer)
        server._open_doc_lsp_versions = {uri: 3}

        assert server._is_stale_document_version(uri, 2)
        assert not server._is_stale_document_version(uri, 3)
        assert not server._is_stale_document_version(uri, 4)
        assert not server._is_stale_document_version(uri, None)


class TestBoundedDictThreadSafety:
    def test_threaded_updates_keep_order_consistent(self):
        d = lsp_server._BoundedDict(maxsize=10)
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


class TestProjectCacheHelpers:
    def test_project_view_cache_key_uses_only_project_member_revisions(self):
        key = project_view_cache_key(
            frozenset({"/repo/Main.geno", "/repo/Util.geno"}),
            ["/repo/Util.geno", "/outside/Other.geno", "/repo/Main.geno"],
            {
                "/repo/Main.geno": 2,
                "/repo/Util.geno": 5,
                "/outside/Other.geno": 99,
            },
        )

        assert key == (
            frozenset({"/repo/Main.geno", "/repo/Util.geno"}),
            (("/repo/Main.geno", 2), ("/repo/Util.geno", 5)),
        )

    def test_invalidation_helpers_match_project_membership(self):
        project_key = (frozenset({"/repo/Main.geno", "/repo/Util.geno"}), ())
        unrelated_project_key = (frozenset({"/repo/Other.geno"}), ())
        symbol_key = ("/repo/Main.geno", project_key)
        unrelated_symbol_key = ("/repo/Other.geno", unrelated_project_key)

        assert project_view_keys_for_path(
            [project_key, unrelated_project_key],
            "/repo/Util.geno",
        ) == [project_key]
        assert symbol_table_keys_for_path(
            [symbol_key, unrelated_symbol_key],
            "/repo/Util.geno",
        ) == [symbol_key]


class TestDiagnosticHelpers:
    def test_diagnostic_module_exports_conversion_helpers(self):
        pytest.importorskip("lsprotocol")

        from geno.diagnostics import Diagnostic, ErrorCode, Severity
        from geno.lsp_diagnostics import error_diagnostic, to_lsp_diagnostic
        from geno.tokens import SourceLocation

        diag = Diagnostic(
            code=ErrorCode.TYPE_MISMATCH,
            message="expected Int",
            severity=Severity.ERROR,
            location=SourceLocation(line=2, column=4),
        )

        lsp_diag = to_lsp_diagnostic(diag)
        fallback = error_diagnostic("fallback", line=3, character=5)

        assert lsp_diag.range.start.line == 1
        assert lsp_diag.range.start.character == 3
        assert fallback.message == "fallback"
        assert fallback.range.start.line == 3
        assert fallback.range.start.character == 5


class TestUriToPath:
    def test_decodes_percent_encoded_file_uri(self, tmp_path):
        path = tmp_path / "My Project" / "main.geno"
        path.parent.mkdir()

        decoded = lsp_server._uri_to_path(path.as_uri())

        assert decoded == path

    def test_decodes_windows_drive_file_uri(self, monkeypatch):
        monkeypatch.setattr(lsp_server, "_IS_WINDOWS", True)

        decoded = lsp_server._uri_to_path(
            "file:///C:/Users/User/Desktop/My%20Project/main.geno"
        )

        assert str(decoded).replace("\\", "/") == (
            "C:/Users/User/Desktop/My Project/main.geno"
        )

    def test_rejects_non_file_uri_scheme(self):
        with pytest.raises(ValueError, match="file scheme"):
            lsp_server._uri_to_path("https://attacker.example/main.geno")

    def test_rejects_remote_file_uri_netloc(self):
        with pytest.raises(ValueError, match="must be local"):
            lsp_server._uri_to_path("file://attacker.example/share/main.geno")

    def test_rejects_windows_remote_file_uri_netloc(self, monkeypatch):
        monkeypatch.setattr(lsp_server, "_IS_WINDOWS", True)

        with pytest.raises(ValueError, match="must be local"):
            lsp_server._uri_to_path("file://attacker.example/share/main.geno")

    def test_accepts_localhost_file_uri_netloc(self):
        decoded = lsp_server._uri_to_path("file://localhost/tmp/main.geno")

        assert str(decoded).replace("\\", "/") == "/tmp/main.geno"


class TestLexicalScanContext:
    def test_block_comment_state_carries_across_lines(self):
        inside, state = lsp_server._scan_line_context(
            "/* foo starts here",
            len("/* foo starts here"),
        )

        assert inside is True
        assert state == (True, False)

        inside, state = lsp_server._scan_line_context(
            "still foo here",
            len("still foo here"),
            in_block_comment=state[0],
            in_triple_string=state[1],
        )

        assert inside is True
        assert state == (True, False)

        inside, state = lsp_server._scan_line_context(
            "comment closes */ let foo: Int = 1",
            len("comment closes */ let foo: Int = 1"),
            in_block_comment=state[0],
            in_triple_string=state[1],
        )

        assert inside is False
        assert state == (False, False)


class TestTypeDefExtraction:
    def test_extract_type_defs_recovers_user_defined_variants_from_partial_parse(self):
        source = "type Maybe = Some(value: Int) | None\n\nfunc broken(\n"

        type_defs = lsp_server._extract_type_defs(source, "test.geno")

        assert "Maybe" in type_defs
        assert type_defs["Maybe"].variants["Some"][0][0] == "value"
        assert str(type_defs["Maybe"].variants["Some"][0][1]) == "Int"


class TestConstructorParentLookup:
    def test_constructor_parent_lookup_handles_later_multiline_variants(self):
        source = (
            "type Maybe = Some(value: Int)\n// no payload\n| None\n\nfunc broken(\n"
        )

        assert lsp_server._find_constructor_parent_type("None", source) == "Maybe"


class TestLspQuickFixHelpers:
    def test_infer_missing_end_text_for_unclosed_function(self):
        source = "func foo() -> Int\n  return 1\n"

        assert lsp_server._infer_missing_end_text(source) == ("end func", "")

    def test_infer_missing_end_text_prefers_innermost_block(self):
        source = "func foo() -> Int\n    if true then\n        return 1\n"

        assert lsp_server._infer_missing_end_text(source) == ("end if", "    ")

    def test_extract_unknown_symbol_name_matches_real_diagnostics(self):
        assert (
            lsp_server._extract_unknown_symbol_name("Undefined variable: do_stuff")
            == "do_stuff"
        )
        assert (
            lsp_server._extract_unknown_symbol_name("Unknown constructor: SomeElse")
            == "SomeElse"
        )
        assert (
            lsp_server._extract_unknown_symbol_name("Unknown type: 'Widget'")
            == "Widget"
        )

    def test_triple_string_state_carries_across_lines(self):
        inside, state = lsp_server._scan_line_context(
            'let text: String = """foo',
            len('let text: String = """foo'),
        )

        assert inside is True
        assert state == (False, True)

        inside, state = lsp_server._scan_line_context(
            "still foo here",
            len("still foo here"),
            in_block_comment=state[0],
            in_triple_string=state[1],
        )

        assert inside is True
        assert state == (False, True)

        inside, state = lsp_server._scan_line_context(
            'foo closes here""" + foo',
            len('foo closes here""" + foo'),
            in_block_comment=state[0],
            in_triple_string=state[1],
        )

        assert inside is False
        assert state == (False, False)
