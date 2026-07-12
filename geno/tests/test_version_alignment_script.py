"""Tests for scripts/check_version_alignment.py."""

from pathlib import Path

from scripts import check_version_alignment as alignment


def _write_alignment_fixture(
    root: Path,
    *,
    python_version: str = "0.3.1",
    package_version: str = "0.3.1",
    lockfile_version: str = "0.3.1",
    lockfile_root_version: str = "0.3.1",
    spec_version: str = "0.3.1",
    status: str = "3 - Alpha",
    changelog_version: str = "0.3.1",
) -> None:
    (root / "geno").mkdir()
    (root / "vscode-geno").mkdir()

    (root / "geno" / "_version.py").write_text(
        f'__version__ = "{python_version}"\n',
        encoding="utf-8",
    )
    (root / "vscode-geno" / "package.json").write_text(
        '{\n  "name": "geno",\n  "version": "' + package_version + '"\n}\n',
        encoding="utf-8",
    )
    (root / "vscode-geno" / "package-lock.json").write_text(
        "{\n"
        '  "name": "geno",\n'
        f'  "version": "{lockfile_version}",\n'
        '  "packages": {\n'
        '    "": {\n'
        f'      "version": "{lockfile_root_version}"\n'
        "    }\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\nclassifiers = ["Development Status :: ' + status + '"]\n',
        encoding="utf-8",
    )
    (root / "spec.json").write_text(
        '{"version": "' + spec_version + '"}\n',
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        f"## [{changelog_version}] - 2026-04-07\n",
        encoding="utf-8",
    )


def test_collect_errors_accepts_aligned_versions(tmp_path):
    _write_alignment_fixture(tmp_path)

    assert alignment.collect_errors(tmp_path) == []


def test_collect_errors_detects_vscode_lockfile_mismatch(tmp_path):
    _write_alignment_fixture(
        tmp_path,
        lockfile_version="0.1.0",
        lockfile_root_version="0.1.0",
    )

    errors = alignment.collect_errors(tmp_path)

    assert any("package-lock.json=0.1.0" in error for error in errors)
    assert any('package-lock.json packages[""]=0.1.0' in error for error in errors)


def test_collect_errors_detects_status_spec_and_missing_changelog_mismatch(tmp_path):
    _write_alignment_fixture(
        tmp_path,
        status="4 - Beta",
        spec_version="0.3.0",
        changelog_version="0.3.0",
    )

    errors = alignment.collect_errors(tmp_path)

    assert any("development status mismatch" in error for error in errors)
    assert any("spec.json=0.3.0" in error for error in errors)
    assert any("CHANGELOG.md has no entry" in error for error in errors)


def test_collect_errors_accepts_matching_release_tag(tmp_path):
    _write_alignment_fixture(tmp_path)

    assert alignment.collect_errors(tmp_path, tag="v0.3.1") == []


def test_collect_errors_detects_release_tag_mismatch(tmp_path):
    _write_alignment_fixture(tmp_path)

    errors = alignment.collect_errors(tmp_path, tag="v0.3.2")

    assert any(
        "Release tag mismatch: tag=0.3.2, geno/_version.py=0.3.1" in error
        for error in errors
    )


def test_collect_errors_rejects_malformed_release_tag(tmp_path):
    _write_alignment_fixture(tmp_path)

    errors = alignment.collect_errors(tmp_path, tag="0.3.1")

    assert any("Release tag must use the form" in error for error in errors)
