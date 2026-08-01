"""Tests for scripts/check_version_alignment.py."""

import subprocess
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
    changelog_notes: str = "### Fixed\n\n- Release notes.\n",
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
        f"## [{changelog_version}] - 2026-04-07\n\n{changelog_notes}",
        encoding="utf-8",
    )


def _initialize_git_tags(root: Path, *tags: str) -> None:
    marker = root / ".release-tag-test"
    marker.write_text("release tag fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", marker.name], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Geno Tests",
            "-c",
            "user.email=tests@example.com",
            "commit",
            "-qm",
            "release tag fixture",
        ],
        cwd=root,
        check=True,
    )
    for tag in tags:
        subprocess.run(["git", "tag", tag], cwd=root, check=True)


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
    _initialize_git_tags(tmp_path)

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


def test_collect_errors_requires_dated_changelog_heading(tmp_path):
    _write_alignment_fixture(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(
        "## [0.3.1]\n\n### Fixed\n\n- Release notes.\n",
        encoding="utf-8",
    )

    errors = alignment.collect_errors(tmp_path)

    assert any("dated release heading" in error for error in errors)


def test_collect_errors_requires_changelog_release_notes(tmp_path):
    _write_alignment_fixture(tmp_path, changelog_notes="### Fixed\n")

    errors = alignment.collect_errors(tmp_path)

    assert any("has no release notes" in error for error in errors)


def test_collect_errors_rejects_retrograde_release_tag(tmp_path):
    _write_alignment_fixture(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(
        "## [0.3.1] - 2026-04-07\n\n"
        "### Fixed\n\n"
        "- Current release.\n\n"
        "## [0.3.2] - 2026-04-08\n\n"
        "### Fixed\n\n"
        "- Newer release.\n",
        encoding="utf-8",
    )

    errors = alignment.collect_errors(tmp_path, tag="v0.3.1")

    assert any(
        "Release version 0.3.1 must be newer than changelog version 0.3.2" in error
        for error in errors
    )


def test_collect_errors_rejects_tag_older_than_repository_release(tmp_path):
    _write_alignment_fixture(
        tmp_path,
        python_version="0.3.2",
        package_version="0.3.2",
        lockfile_version="0.3.2",
        lockfile_root_version="0.3.2",
        spec_version="0.3.2",
        changelog_version="0.3.2",
    )
    _initialize_git_tags(tmp_path, "v0.4.2")

    errors = alignment.collect_errors(tmp_path, tag="v0.3.2")

    assert any(
        "Release version 0.3.2 must be newer than existing repository tag v0.4.2"
        in error
        for error in errors
    )


def test_collect_errors_rejects_release_when_git_is_unavailable(tmp_path, monkeypatch):
    _write_alignment_fixture(tmp_path)
    monkeypatch.setattr(alignment.shutil, "which", lambda _name: None)

    errors = alignment.collect_errors(tmp_path, tag="v0.3.1")

    assert any(
        "Cannot validate repository release tags: Git is required" in error
        for error in errors
    )


def test_collect_errors_rejects_release_when_git_tag_fails(tmp_path, monkeypatch):
    _write_alignment_fixture(tmp_path)
    monkeypatch.setattr(alignment.shutil, "which", lambda _name: "git")
    monkeypatch.setattr(
        alignment.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            128,
            stdout="",
            stderr="fatal: not a git repository",
        ),
    )

    errors = alignment.collect_errors(tmp_path, tag="v0.3.1")

    assert any(
        "Cannot validate repository release tags" in error and "git exited 128" in error
        for error in errors
    )


def test_collect_errors_rejects_release_when_git_tag_cannot_start(
    tmp_path, monkeypatch
):
    _write_alignment_fixture(tmp_path)
    monkeypatch.setattr(alignment.shutil, "which", lambda _name: "git")

    def raise_os_error(*_args, **_kwargs):
        raise OSError("access denied")

    monkeypatch.setattr(alignment.subprocess, "run", raise_os_error)

    errors = alignment.collect_errors(tmp_path, tag="v0.3.1")

    assert any(
        "Cannot validate repository release tags" in error and "access denied" in error
        for error in errors
    )


def test_collect_errors_orders_prerelease_versions(tmp_path):
    cases = [
        ("alpha", "0.4.3a2", "0.4.3a1"),
        ("stable", "0.4.3", "0.4.3a1"),
    ]
    for name, current, prior in cases:
        root = tmp_path / name
        root.mkdir()
        _write_alignment_fixture(
            root,
            python_version=current,
            package_version=current,
            lockfile_version=current,
            lockfile_root_version=current,
            spec_version=current,
            changelog_version=current,
        )
        (root / "CHANGELOG.md").write_text(
            f"## [{current}] - 2026-04-08\n\n"
            "### Fixed\n\n"
            "- Current release.\n\n"
            f"## [{prior}] - 2026-04-07\n\n"
            "### Fixed\n\n"
            "- Prior prerelease.\n",
            encoding="utf-8",
        )

        _initialize_git_tags(root, f"v{prior}")
        assert alignment.collect_errors(root, tag=f"v{current}") == []


def test_publish_workflow_fetches_tags_before_version_check():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github" / "workflows" / "publish.yml").read_text(
        encoding="utf-8"
    )

    assert workflow.index("fetch-depth: 0") < workflow.index(
        "Verify release tag matches package version"
    )
