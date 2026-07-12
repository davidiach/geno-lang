"""Tests for package-management CLI helpers."""

import pytest

from geno import package_manager
from geno.cli import pkg as pkg_cli
from geno.cli.pkg import pkg_add


def test_pkg_add_curated_uses_index_tag(monkeypatch, capsys):
    captured = {}

    monkeypatch.setattr(
        "geno.package_index.resolve",
        lambda name: {
            "name": name,
            "git": "https://example.com/geno-json.git",
            "tag": "v0.1.0",
        },
    )

    def fake_add(name, url, branch="main", tag=None):
        captured.update(
            {
                "name": name,
                "url": url,
                "branch": branch,
                "tag": tag,
            }
        )

    monkeypatch.setattr("geno.package_manager.add", fake_add)

    pkg_add("geno-json")

    assert captured == {
        "name": "geno-json",
        "url": "https://example.com/geno-json.git",
        "branch": "main",
        "tag": "v0.1.0",
    }
    out = capsys.readouterr().out
    assert "Resolved 'geno-json' from package index" in out
    assert "tag: v0.1.0" in out


def test_pkg_add_explicit_url_defaults_to_main_branch(monkeypatch, capsys):
    captured = {}

    def fake_add(name, url, branch="main", tag=None):
        captured.update(
            {
                "name": name,
                "url": url,
                "branch": branch,
                "tag": tag,
            }
        )

    monkeypatch.setattr("geno.package_manager.add", fake_add)

    pkg_add("custom", "https://example.com/custom.git")

    assert captured == {
        "name": "custom",
        "url": "https://example.com/custom.git",
        "branch": "main",
        "tag": None,
    }
    assert "branch: main" in capsys.readouterr().out


def test_pkg_add_curated_explicit_branch_overrides_tag(monkeypatch, capsys):
    captured = {}

    monkeypatch.setattr(
        "geno.package_index.resolve",
        lambda name: {
            "name": name,
            "git": "https://example.com/geno-json.git",
            "tag": "v0.1.0",
        },
    )

    def fake_add(name, url, branch="main", tag=None):
        captured.update(
            {
                "name": name,
                "url": url,
                "branch": branch,
                "tag": tag,
            }
        )

    monkeypatch.setattr("geno.package_manager.add", fake_add)

    pkg_add("geno-json", branch="develop")

    assert captured == {
        "name": "geno-json",
        "url": "https://example.com/geno-json.git",
        "branch": "develop",
        "tag": None,
    }
    assert "branch: develop" in capsys.readouterr().out


def test_pkg_add_missing_index_package_exits(monkeypatch, capsys):
    monkeypatch.setattr("geno.package_index.resolve", lambda name: None)
    monkeypatch.setattr(
        "geno.package_manager.add",
        lambda *args, **kwargs: pytest.fail("package_manager.add should not run"),
    )

    with pytest.raises(SystemExit) as exc:
        pkg_add("missing")

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Package 'missing' not found in index" in err
    assert "geno add missing <url>" in err


def test_pkg_install_reports_expected_package_errors(monkeypatch, capsys) -> None:
    def fail_install():
        raise ValueError("bad manifest")

    monkeypatch.setattr(package_manager, "install", fail_install)

    with pytest.raises(SystemExit) as exc_info:
        pkg_cli.pkg_install()

    assert exc_info.value.code == 1
    assert "Error installing packages: bad manifest" in capsys.readouterr().err


def test_pkg_install_does_not_swallow_unexpected_errors(monkeypatch) -> None:
    def fail_install():
        raise AssertionError("unexpected bug")

    monkeypatch.setattr(package_manager, "install", fail_install)

    with pytest.raises(AssertionError, match="unexpected bug"):
        pkg_cli.pkg_install()
