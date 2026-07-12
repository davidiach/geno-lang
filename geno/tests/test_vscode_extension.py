"""Regression tests for VS Code extension packaging metadata."""

import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
EXTENSION_ROOT = ROOT / "vscode-geno"
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _extension_path(path: str) -> Path:
    rel = PurePosixPath(path)
    if rel.parts and rel.parts[0] == ".":
        rel = PurePosixPath(*rel.parts[1:])
    return EXTENSION_ROOT.joinpath(*rel.parts)


def test_language_icon_paths_exist_when_declared():
    manifest = json.loads((EXTENSION_ROOT / "package.json").read_text(encoding="utf-8"))
    language = manifest["contributes"]["languages"][0]
    icons = language.get("icon")

    assert icons is None or set(icons) == {"light", "dark"}
    if icons is None:
        return

    for theme in ("light", "dark"):
        icon_path = _extension_path(icons[theme])
        assert icon_path.is_file()
        assert icon_path.read_bytes().startswith(PNG_HEADER)


def test_server_path_setting_is_declared():
    manifest = json.loads((EXTENSION_ROOT / "package.json").read_text(encoding="utf-8"))
    properties = manifest["contributes"]["configuration"]["properties"]

    server_path = properties["geno.serverPath"]
    assert server_path["type"] == "string"
    assert server_path["scope"] == "machine"
    assert server_path["default"] == "geno"
    assert "language server" in server_path["markdownDescription"]
    assert "run-file commands" in server_path["markdownDescription"]


def test_untrusted_workspace_capabilities_are_limited():
    manifest = json.loads((EXTENSION_ROOT / "package.json").read_text(encoding="utf-8"))
    untrusted = manifest["capabilities"]["untrustedWorkspaces"]

    assert untrusted["supported"] == "limited"
    assert "execution" in untrusted["description"]
    assert "geno.serverPath" in untrusted["restrictedConfigurations"]


def test_extension_runtime_gates_execution_on_workspace_trust():
    extension = (EXTENSION_ROOT / "src" / "extension.ts").read_text(encoding="utf-8")

    assert "vscode.workspace.isTrusted" in extension
    assert "onDidGrantWorkspaceTrust" in extension
    assert "ensureTrustedWorkspace" in extension


def test_run_file_uses_configured_geno_path():
    extension = (EXTENSION_ROOT / "src" / "extension.ts").read_text(encoding="utf-8")
    shell_escape = (EXTENSION_ROOT / "src" / "shellEscape.ts").read_text(
        encoding="utf-8"
    )

    assert "const genoPath = getGenoServerPath()" in extension
    assert "buildRunFileInvocation(\n      genoPath,\n      filePath" in extension
    assert "shellPath: genoPath" in shell_escape
    assert "text: `geno run" not in shell_escape
