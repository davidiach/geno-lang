#!/usr/bin/env python3
"""Verify version and maturity metadata is consistent across the project."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYPROJECT_STATUS = "3 - Alpha"


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


def get_python_version(root: Path = ROOT) -> str:
    text = (root / "geno" / "_version.py").read_text()
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else ""


def get_vscode_version(root: Path = ROOT) -> str:
    data = json.loads((root / "vscode-geno" / "package.json").read_text())
    return _string_value(data.get("version"))


def get_vscode_lockfile_versions(root: Path = ROOT) -> tuple[str, str]:
    data = json.loads((root / "vscode-geno" / "package-lock.json").read_text())
    top_level = _string_value(data.get("version"))
    packages = data.get("packages")
    if not isinstance(packages, dict):
        return top_level, ""
    package_root = _string_value(packages.get("", {}).get("version"))
    return top_level, package_root


def get_pyproject_status(root: Path = ROOT) -> str:
    text = (root / "pyproject.toml").read_text()
    m = re.search(r'"Development Status :: (\d+ - \w+)"', text)
    return m.group(1) if m else ""


def get_spec_version(root: Path = ROOT) -> str:
    data = json.loads((root / "spec.json").read_text())
    return _string_value(data.get("version"))


def check_changelog_has_version(version: str, root: Path = ROOT) -> bool:
    text = (root / "CHANGELOG.md").read_text()
    return f"[{version}]" in text


def collect_errors(root: Path = ROOT, *, tag: str | None = None) -> list[str]:
    errors: list[str] = []
    py_ver = get_python_version(root)
    vsc_ver = get_vscode_version(root)

    if not py_ver:
        errors.append("Could not read version from geno/_version.py")
    if not vsc_ver:
        errors.append("Could not read version from vscode-geno/package.json")

    if py_ver and vsc_ver and py_ver != vsc_ver:
        errors.append(
            f"Version mismatch: geno/_version.py={py_ver}, "
            f"vscode-geno/package.json={vsc_ver}"
        )

    try:
        lock_top, lock_root = get_vscode_lockfile_versions(root)
    except FileNotFoundError:
        errors.append("Could not read version from vscode-geno/package-lock.json")
        lock_top = ""
        lock_root = ""

    if py_ver and lock_top and py_ver != lock_top:
        errors.append(
            "Version mismatch: "
            f"geno/_version.py={py_ver}, "
            f"vscode-geno/package-lock.json={lock_top}"
        )

    if py_ver and lock_root and py_ver != lock_root:
        errors.append(
            "Version mismatch: "
            f"geno/_version.py={py_ver}, "
            f'vscode-geno/package-lock.json packages[""]={lock_root}'
        )

    status = get_pyproject_status(root)
    if status != EXPECTED_PYPROJECT_STATUS:
        errors.append(
            "pyproject.toml development status mismatch: "
            f"expected {EXPECTED_PYPROJECT_STATUS}, got {status or '<missing>'}"
        )

    try:
        spec_ver = get_spec_version(root)
    except FileNotFoundError:
        errors.append("Could not read version from spec.json")
        spec_ver = ""

    if py_ver and spec_ver and py_ver != spec_ver:
        errors.append(
            f"Version mismatch: geno/_version.py={py_ver}, spec.json={spec_ver}"
        )

    if py_ver and not check_changelog_has_version(py_ver, root):
        errors.append(f"CHANGELOG.md has no entry for current version {py_ver}")

    if tag is not None:
        if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)?", tag):
            errors.append(f"Release tag must use the form v<version>, got {tag!r}")
        elif py_ver and tag[1:] != py_ver:
            errors.append(
                f"Release tag mismatch: tag={tag[1:]}, geno/_version.py={py_ver}"
            )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify version and maturity metadata alignment."
    )
    parser.add_argument(
        "--tag",
        help="also require a release tag such as v0.3.1 to match the package version",
    )
    args = parser.parse_args(argv)

    py_ver = get_python_version(ROOT)
    status = get_pyproject_status(ROOT)
    errors = collect_errors(ROOT, tag=args.tag)

    if errors:
        print("Version alignment errors:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"Version alignment OK: {py_ver} (status: {status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
