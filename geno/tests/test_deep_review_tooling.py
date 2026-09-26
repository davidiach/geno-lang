"""End-to-end regressions for project discovery and source-preserving LSP edits."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from geno.api import check, check_path, run_path
from geno.cli.build import bundle_project
from geno.init import create_project
from geno.project_resolution import resolve_file_context


def _project(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "src").mkdir()
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "src/Utils"]\n'
    )
    main = tmp_path / "Main.geno"
    utils = tmp_path / "src" / "Utils.geno"
    main.write_text("import Utils\nfunc main() -> Int\n  return helper(3)\nend func\n")
    utils.write_text(
        "export func helper(x: Int) -> Int\n"
        "  example 1 -> 2\n  return x + 1\nend func\n"
    )
    return main, utils


def test_generated_library_is_consumable_with_documented_alias(tmp_path):
    library = tmp_path / "my-lib"
    create_project(library, "lib")
    assert "import MyLib" in (library / "README.md").read_text()
    consumer = tmp_path / "consumer"
    shutil.copytree(library, consumer / "geno_modules" / "my-lib")
    (consumer / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main"]\n'
        '[dependencies.my-lib]\ngit = "https://example.com/my-lib.git"\n'
    )
    main = consumer / "Main.geno"
    main.write_text(
        "import MyLib\nfunc main() -> Int\n  return MyLib.double(5)\nend func\n"
    )
    assert check_path(str(library)).ok
    for target in (consumer, main):
        assert check_path(str(target)).ok
        result = run_path(str(target))
        assert result.ok, result.error
        assert result.value == 10


def test_explicit_file_keeps_manifest_paths_and_only_parses_its_closure(tmp_path):
    main, utils = _project(tmp_path)
    assert run_path(str(tmp_path)).value == 4
    assert run_path(str(main)).value == 4
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "src/Utils", "Broken"]\n'
    )
    (tmp_path / "Broken.geno").write_text("this is not a Geno program")
    assert not check_path(str(tmp_path)).ok
    assert check_path(str(main)).ok
    context = resolve_file_context(main)
    assert set(context.dependency_graph.parsed) == {"Main", "Utils"}
    assert context.dependency_graph.file_map["Utils"].path == utils
    override = utils.read_text().replace("x + 1", "x + 2")
    context = resolve_file_context(main, source_overrides={utils: override})
    assert context.module_sources["Utils"] == override


@pytest.mark.parametrize("files", ['files = ["Main"]\n', ""])
def test_bundle_uses_canonical_discovery(tmp_path, capsys, files):
    source = "func main() -> Int\n  return 42\nend func\n"
    (tmp_path / "Main.geno").write_text(source)
    config = tmp_path / "custom.toml"
    config.write_text('entrypoint = "Main"\n' + files)
    bundle_project(str(config))
    artifact = json.loads(capsys.readouterr().out)
    assert artifact["entrypoint"] == "Main"
    assert artifact["modules"] == {"Main": source}


def test_bundle_contains_nested_imports_and_rejects_missing_entrypoint(
    tmp_path, capsys
):
    main, _utils = _project(tmp_path)
    config = tmp_path / "geno.toml"
    bundle_project(str(config))
    artifact = json.loads(capsys.readouterr().out)
    assert set(artifact["modules"]) == {"Main", "Utils"}
    assert check(artifact["modules"].pop("Main"), modules=artifact["modules"]).ok
    config.write_text('entrypoint = "Missing"\nfiles = ["Main"]\n')
    with pytest.raises(SystemExit, match="1"):
        bundle_project(str(config))
    assert "Entrypoint module 'Missing'" in capsys.readouterr().err
    assert main.exists()


def _server(documents, encoding="utf-16"):
    pytest.importorskip("pygls")
    from lsprotocol import types
    from pygls.workspace import Workspace

    from geno.lsp_server import create_server

    server = create_server(diag_debounce_sec=0)
    diagnostics = {}
    server.publish_diagnostics = lambda uri, items: diagnostics.update({uri: items})
    server.lsp._workspace = Workspace(
        None, sync_kind=types.TextDocumentSyncKind.Full, position_encoding=encoding
    )
    for path, source in documents.items():
        server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=path.as_uri(), language_id="geno", version=1, text=source
                )
            )
        )
    return server, diagnostics


def _rename(server, path, line, character, new_name="increment"):
    from lsprotocol import types

    return server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME)(
        types.RenameParams(
            text_document=types.TextDocumentIdentifier(uri=path.as_uri()),
            position=types.Position(line=line, character=character),
            new_name=new_name,
        )
    )


def _apply_edits(source, edits, encoding="utf-16-le", unit=2):
    lines = source.splitlines(keepends=True)
    for edit in sorted(
        edits, key=lambda e: (e.range.start.line, e.range.start.character), reverse=True
    ):
        assert edit.range.start.line == edit.range.end.line
        i = edit.range.start.line
        encoded = lines[i].encode(encoding)
        lines[i] = (
            encoded[: edit.range.start.character * unit]
            + edit.new_text.encode(encoding)
            + encoded[edit.range.end.character * unit :]
        ).decode(encoding)
    return "".join(lines)


@pytest.mark.parametrize("from_definition", [True, False])
def test_rename_covers_reverse_importers_and_open_buffers(tmp_path, from_definition):
    main, utils = _project(tmp_path)
    # The authoritative caller is an unsaved buffer, with two references.
    overlay = main.read_text().replace("helper(3)", "helper(3) + helper(4)")
    documents = {main: overlay, utils: utils.read_text()}
    server, _ = _server(documents)
    result = (
        _rename(server, utils, 0, 13)
        if from_definition
        else _rename(server, main, 2, 11)
    )
    assert set(result.changes) == {main.as_uri(), utils.as_uri()}
    assert len(result.changes[main.as_uri()]) == 2
    for path, source in documents.items():
        path.write_text(_apply_edits(source, result.changes[path.as_uri()]))
    assert check_path(str(tmp_path)).ok
    assert run_path(str(main)).value == 9


def test_rename_refuses_partial_project_edits_but_keeps_local_renames(tmp_path):
    main, utils = _project(tmp_path)
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "src/Utils", "Broken"]\n'
    )
    (tmp_path / "Broken.geno").write_text("func broken(\n")
    source = (
        "import Utils\nfunc main() -> Int\n"
        "  let value = 3\n  return helper(value)\nend func\n"
    )
    main.write_text(source)
    server, _ = _server({main: source, utils: utils.read_text()})

    # Neither the declaration nor the caller may return an edit that leaves
    # the other occurrence behind when the semantic project view is partial.
    assert _rename(server, utils, 0, 13) is None
    assert _rename(server, main, 3, 11) is None

    # A lexically resolved local binding has no project-wide references.
    result = _rename(server, main, 2, 7, "count")
    assert set(result.changes) == {main.as_uri()}
    updated = _apply_edits(source, result.changes[main.as_uri()])
    assert "let count = 3" in updated
    assert "return helper(count)" in updated


@pytest.mark.parametrize("error", [OSError("unreadable"), ValueError("bad manifest")])
def test_rename_refuses_expected_project_loading_errors(tmp_path, monkeypatch, error):
    from geno import lsp_server

    main, utils = _project(tmp_path)
    server, _ = _server({main: main.read_text(), utils: utils.read_text()})

    def fail_loading(*args, **kwargs):
        raise error

    monkeypatch.setattr(lsp_server, "_load_validation_project", fail_loading)
    assert _rename(server, utils, 0, 13) is None


def test_rename_completeness_does_not_mask_programming_errors(tmp_path, monkeypatch):
    pytest.importorskip("lsprotocol")
    from lsprotocol import types

    from geno import lsp_server

    main, utils = _project(tmp_path)
    server, _ = _server({main: main.read_text(), utils: utils.read_text()})

    def fail_loading(*args, **kwargs):
        raise AttributeError("unexpected programming error")

    monkeypatch.setattr(lsp_server, "_load_validation_project", fail_loading)
    wrapper = server.lsp._get_handler(types.TEXT_DOCUMENT_RENAME).__self__
    with pytest.raises(AttributeError, match="unexpected programming error"):
        wrapper._rename_project_is_complete(utils.as_uri(), utils.read_text())


@pytest.mark.parametrize(
    ("encoding", "codec", "unit", "reference_column"),
    [
        ("utf-16", "utf-16-le", 2, 16),
        ("utf-8", "utf-8", 1, 18),
        ("utf-32", "utf-32-le", 4, 15),
    ],
)
def test_unicode_rename_round_trips_incoming_and_outgoing_positions(
    tmp_path, encoding, codec, unit, reference_column
):
    main = tmp_path / "Main.geno"
    source = (
        'func main() -> String\n  let value: String = "x"\n'
        '  return "😀" + value\nend func\n'
    )
    main.write_text(source, encoding="utf-8")
    server, _ = _server({main: source}, encoding)
    # Request at the last letter so a missing inbound conversion misses the name.
    result = _rename(server, main, 2, reference_column + 4, "count")
    edits = result.changes[main.as_uri()]
    reference = next(edit for edit in edits if edit.range.start.line == 2)
    assert reference.range.start.character == reference_column
    assert reference.range.end.character == reference_column + 5
    updated = _apply_edits(source, edits, codec, unit)
    assert 'return "😀" + count' in updated
    assert check(updated).ok


def test_unicode_diagnostics_and_navigation_share_protocol_positions(tmp_path):
    pytest.importorskip("lsprotocol")
    from lsprotocol import types

    main = tmp_path / "Main.geno"
    source = (
        'func main() -> String\n  let value: String = "x"\n'
        '  return "😀" + value\nend func\n'
    )
    main.write_text(source, encoding="utf-8")
    server, _ = _server({main: source})
    params = types.DefinitionParams(
        text_document=types.TextDocumentIdentifier(uri=main.as_uri()),
        position=types.Position(line=2, character=20),
    )
    definition = server.lsp._get_handler(types.TEXT_DOCUMENT_DEFINITION)(params)
    assert definition.range.start.line == 1
    references = server.lsp._get_handler(types.TEXT_DOCUMENT_REFERENCES)(
        types.ReferenceParams(
            text_document=params.text_document,
            position=params.position,
            context=types.ReferenceContext(include_declaration=True),
        )
    )
    reference = next(loc for loc in references if loc.range.start.line == 2)
    assert reference.range.start.character == 16
    invalid = source.replace(" + value", " + missing")
    main.write_text(invalid, encoding="utf-8")
    _server_instance, diagnostics = _server({main: invalid})
    error = next(d for d in diagnostics[main.as_uri()] if "missing" in d.message)
    assert error.range.start.character == 16
