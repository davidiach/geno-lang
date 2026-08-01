#!/usr/bin/env python3
"""Verify version and maturity metadata is consistent across the project."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYPROJECT_STATUS = "3 - Alpha"
_CHANGELOG_RELEASE_HEADING_RE = re.compile(
    r"^## \[(?P<version>\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*))\] "
    r"- \d{4}-\d{2}-\d{2}\s*$",
    re.MULTILINE,
)


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


def get_changelog_release_entries(
    root: Path = ROOT,
) -> list[tuple[str, str]]:
    """Return dated release versions and their section bodies."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    matches = list(_CHANGELOG_RELEASE_HEADING_RE.finditer(text))
    entries: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        entries.append((match.group("version"), text[match.end() : body_end]))
    return entries


def check_changelog_has_version(version: str, root: Path = ROOT) -> bool:
    return any(
        release_version == version
        for release_version, _body in get_changelog_release_entries(root)
    )


def _changelog_entry_has_notes(body: str) -> bool:
    return any(
        re.match(r"^\s*[-*]\s+\S", line) is not None for line in body.splitlines()
    )


def _release_version(version: str) -> Version | None:
    try:
        return Version(version)
    except InvalidVersion:
        return None


def get_repository_release_tags(
    root: Path = ROOT,
) -> list[tuple[str, Version]]:
    """Return parseable release tags visible in the repository."""
    git_executable = shutil.which("git")
    if git_executable is None:
        raise RuntimeError(
            "Git is required to validate existing repository release tags"
        )
    try:
        result = subprocess.run(  # noqa: S603 - resolved git executable, fixed arguments
            [git_executable, "tag", "--list", "v[0-9]*"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(
            f"could not enumerate existing repository release tags: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "no error output"
        raise RuntimeError(
            "could not enumerate existing repository release tags "
            f"(git exited {result.returncode}: {detail})"
        )
    return [
        (tag, parsed)
        for tag in result.stdout.splitlines()
        if (parsed := _release_version(tag.removeprefix("v"))) is not None
    ]


def _latest_version(
    versions: list[tuple[str, Version]],
) -> tuple[str, Version] | None:
    return max(versions, key=lambda item: item[1]) if versions else None


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

    changelog_entries = get_changelog_release_entries(root)
    current_entries = [body for version, body in changelog_entries if version == py_ver]
    if py_ver and not current_entries:
        errors.append(
            "CHANGELOG.md has no entry with a dated release heading "
            f"for current version {py_ver}"
        )
    elif len(current_entries) > 1:
        errors.append(
            f"CHANGELOG.md has duplicate release headings for current version {py_ver}"
        )
    elif current_entries and not _changelog_entry_has_notes(current_entries[0]):
        errors.append(
            f"CHANGELOG.md entry for current version {py_ver} has no release notes"
        )

    if tag is not None:
        valid_tag = re.fullmatch(r"v\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)?", tag)
        if not valid_tag:
            errors.append(f"Release tag must use the form v<version>, got {tag!r}")
        elif py_ver and tag[1:] != py_ver:
            errors.append(
                f"Release tag mismatch: tag={tag[1:]}, geno/_version.py={py_ver}"
            )
        elif py_ver:
            current_version = _release_version(py_ver)
            changelog_versions = [
                (version, parsed)
                for version, _body in changelog_entries
                if version != py_ver
                if (parsed := _release_version(version)) is not None
            ]
            latest_changelog = _latest_version(changelog_versions)
            if current_version is None:
                errors.append(f"Release version {py_ver} is not a valid version")
            elif latest_changelog is not None:
                latest_name, latest_version = latest_changelog
                if current_version <= latest_version:
                    errors.append(
                        f"Release version {py_ver} must be newer than "
                        f"changelog version {latest_name}"
                    )
            try:
                repository_tags = get_repository_release_tags(root)
            except RuntimeError as exc:
                errors.append(f"Cannot validate repository release tags: {exc}")
                repository_tags = []
            repository_versions = [
                (release_tag, parsed)
                for release_tag, parsed in repository_tags
                if release_tag != tag
            ]
            latest_repository = _latest_version(repository_versions)
            if current_version is not None and latest_repository is not None:
                latest_tag, latest_version = latest_repository
                if current_version <= latest_version:
                    errors.append(
                        f"Release version {py_ver} must be newer than "
                        f"existing repository tag {latest_tag}"
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
