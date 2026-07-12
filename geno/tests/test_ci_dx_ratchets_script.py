"""Tests for scripts/check_ci_dx_ratchets.py."""

from pathlib import Path

from scripts import check_ci_dx_ratchets as ratchets


def test_current_repo_ci_dx_ratchets_pass() -> None:
    assert ratchets.collect_errors() == []


def test_broad_exception_count_includes_bare_and_exception_handlers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "demo.py"
    source.write_text(
        """
def demo(kind):
    try:
        return kind()
    except ValueError:
        return "typed"
    except Exception:
        return "broad"

def bare():
    try:
        return 1
    except:
        return 2
""",
        encoding="utf-8",
    )

    assert ratchets.count_broad_exception_handlers([source]) == 2


def test_debt_budget_measurements_are_reported(tmp_path: Path) -> None:
    (tmp_path / "geno").mkdir()
    (tmp_path / "benchmark").mkdir()
    (tmp_path / "experiment").mkdir()
    (tmp_path / "analysis").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "geno" / "module.py").write_text(
        """
value = 1  # type: ignore[assignment]

def typed(value: int) -> int:
    return value + 1

def missing(value):
    return value

try:
    value += 1
except Exception:
    value = 0
""",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.ruff.lint]
ignore = ["E501", "F401"]

[tool.ruff.lint.per-file-ignores]
"geno/tests/*" = ["S", "PT"]
""",
        encoding="utf-8",
    )

    results = {result.name: result for result in ratchets.check_debt_budgets(tmp_path)}

    assert results["ruff-global-ignores"].actual == 2
    assert results["ruff-per-file-ignores"].actual == 2
    assert results["type-ignore-comments"].actual == 1
    assert results["untyped-functions"].actual == 1
    assert results["broad-exception-handlers"].actual == 1


def test_workflow_surface_reports_missing_required_snippet(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text("jobs:\n  smoke: windows-latest\n")
    (tmp_path / "Makefile").write_text("all:\n\t@true\n")

    errors = ratchets.check_workflow_surface(tmp_path)

    assert any("Windows sandbox config smoke slice" in error for error in errors)
    assert any("sandbox-regression" in error for error in errors)
    assert any("hosted security corpus and bounty job" in error for error in errors)
    assert any("hosted Python dependency audit job" in error for error in errors)
    assert any("dependency-audit local target" in error for error in errors)
    assert any("exact pip-audit CI install pin" in error for error in errors)


def test_test_typing_profile_surface_reports_missing_profile(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.mypy]
exclude = ["^geno/tests/"]
""",
        encoding="utf-8",
    )

    errors = ratchets.check_test_typing_profile_surface(tmp_path)

    assert errors == ["missing staged test typing profile: mypy-tests.ini"]
