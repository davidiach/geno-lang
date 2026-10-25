"""Tests for VS Code extension package manifest file references."""

from __future__ import annotations

import json
from pathlib import Path

from packaging.version import Version


def _extension_root() -> Path:
    return Path(__file__).resolve().parents[2] / "vscode-geno"


def _assert_manifest_file_exists(extension_root: Path, manifest_path: str) -> None:
    path = extension_root / manifest_path.removeprefix("./")
    assert path.is_file(), (
        f"vscode-geno/package.json references missing file: {manifest_path}"
    )


def test_vscode_manifest_references_existing_files():
    extension_root = _extension_root()
    manifest = json.loads((extension_root / "package.json").read_text())

    contributes = manifest["contributes"]
    for language in contributes.get("languages", []):
        configuration = language.get("configuration")
        if configuration is not None:
            _assert_manifest_file_exists(extension_root, configuration)

        icon = language.get("icon")
        if icon is not None:
            _assert_manifest_file_exists(extension_root, icon["light"])
            _assert_manifest_file_exists(extension_root, icon["dark"])

    for grammar in contributes.get("grammars", []):
        _assert_manifest_file_exists(extension_root, grammar["path"])

    for snippet in contributes.get("snippets", []):
        _assert_manifest_file_exists(extension_root, snippet["path"])


def test_vscode_lock_uses_patched_msal_only_for_build_tools():
    lock = json.loads((_extension_root() / "package-lock.json").read_text())
    msal_packages = [
        package
        for path, package in lock["packages"].items()
        if path.endswith("node_modules/@azure/msal-node")
    ]

    assert msal_packages, "expected the VSCE authentication dependency in the lockfile"
    for package in msal_packages:
        # GHSA-xcx6-jx8p-x3h5: reject vulnerable copies, including nested installs.
        assert Version(package["version"]) >= Version("7.1.0")
        assert package.get("dev") is True
