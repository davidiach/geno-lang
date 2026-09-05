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
    assert any("release lockfile audit" in error for error in errors)
    assert any("Linux release-lock audit guard" in error for error in errors)
    assert any("exact pip-audit CI install pin" in error for error in errors)


def test_workflow_surface_reports_efficiency_regressions(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(
        """jobs:
  lsp-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@example
      - run: python -m pytest geno/tests/ -q
  test:
    runs-on: ubuntu-latest
    steps:
      - run: python -m pytest geno/tests/ --cov=geno
  release-check:
    runs-on: ubuntu-latest
    steps:
      - run: make release-check
""",
        encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")

    errors = ratchets.check_workflow_surface(tmp_path)

    assert any("has no timeout" in error for error in errors)
    assert any("pip caching" in error for error in errors)
    assert any("LSP job must not repeat" in error for error in errors)
    assert any("coverage shard job missing" in error for error in errors)
    assert any("coverage report job missing" in error for error in errors)
    assert any("non-duplicating validator slice" in error for error in errors)
    assert any("cache must include the release lockfile" in error for error in errors)
    assert any("release validator slice missing" in error for error in errors)
    assert any("release-gate workflow missing" in error for error in errors)
    assert any("main/master branch coverage" in error for error in errors)


def test_workflow_surface_reports_compatibility_shard_regressions(
    tmp_path: Path,
) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow = (ratchets.ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    workflow = workflow.replace("        shard: [0, 1]", "        shard: [0]", 1)
    workflow = workflow.replace(
        "          --shard-index ${{ matrix.shard }} \\\n          --shard-count 2",
        "          --shard-index 0 \\\n          --shard-count 2",
        1,
    )
    workflow = workflow.replace(
        "          --shard-count 2 \\\n",
        "          --shard-count 2 \\\n"
        "          --balance-profile coverage-ubuntu-py311 \\\n",
        1,
    )
    workflow = workflow.replace(
        "    needs: compatibility-ubuntu-312",
        "    needs: compatibility-ubuntu-310",
        1,
    )
    (workflow_dir / "ci.yml").write_text(workflow, encoding="utf-8")
    (tmp_path / "Makefile").write_text(
        (ratchets.ROOT / "Makefile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    errors = ratchets.check_workflow_surface(tmp_path)

    assert (
        "hosted compatibility-ubuntu-310 job missing two-way compatibility matrix"
        in errors
    )
    assert (
        "hosted compatibility-ubuntu-310 job missing matrix-bound shard selection"
        in errors
    )
    assert (
        "hosted compatibility-ubuntu-312-report job missing compatibility shard dependency"
        in errors
    )
    assert (
        "hosted compatibility-ubuntu-310 job must not use the coverage balance profile"
        in errors
    )


def test_workflow_surface_reports_coverage_balance_profile_regression(
    tmp_path: Path,
) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow = (ratchets.ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    workflow = workflow.replace(
        "          --balance-profile coverage-ubuntu-py311 \\\n", "", 1
    )
    (workflow_dir / "ci.yml").write_text(workflow, encoding="utf-8")
    (tmp_path / "Makefile").write_text(
        (ratchets.ROOT / "Makefile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    errors = ratchets.check_workflow_surface(tmp_path)

    assert (
        "hosted coverage shard job missing measured Ubuntu/Python 3.11 coverage "
        "balance profile" in errors
    )


def test_workflow_surface_reports_shard_plan_regressions(tmp_path: Path) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow = (ratchets.ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    workflow = workflow.replace(
        "          --plan-manifest coverage-data/shard-plan.${{ matrix.shard }}.json "
        "\\\n",
        "",
        1,
    )
    workflow = workflow.replace(
        "        test -s coverage-data/shard-plan.${{ matrix.shard }}.json\n",
        "",
        1,
    )
    workflow = workflow.replace(
        "          coverage-data/shard-plan.${{ matrix.shard }}.json\n",
        "",
        1,
    )
    workflow = workflow.replace(
        '          --validate-plan-manifests "${plan_files[@]}" \\\n',
        "",
        1,
    )
    (workflow_dir / "ci.yml").write_text(workflow, encoding="utf-8")
    (tmp_path / "Makefile").write_text(
        (ratchets.ROOT / "Makefile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    errors = ratchets.check_workflow_surface(tmp_path)

    assert "hosted coverage shard job missing shard plan manifest output" in errors
    assert "hosted coverage shard job missing nonempty shard plan guard" in errors
    assert (
        "hosted coverage shard job missing paired coverage, shard plan, and timing "
        "artifact upload" in errors
    )
    assert (
        "hosted coverage report job missing shard plan agreement validation" in errors
    )


def test_workflow_surface_reports_shard_timing_regressions(
    tmp_path: Path,
) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflow = (ratchets.ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    workflow = workflow.replace(
        "          --timing-manifest coverage-data/shard-timing.${{ matrix.shard }}.json "
        "\\\n",
        "",
        1,
    )
    workflow = workflow.replace(
        "        test -s coverage-data/shard-timing.${{ matrix.shard }}.json\n",
        "",
        1,
    )
    workflow = workflow.replace(
        "          coverage-data/shard-timing.${{ matrix.shard }}.json\n",
        "",
        1,
    )
    workflow = workflow.replace("        retention-days: 14\n", "", 1)
    workflow = workflow.replace("        overwrite: true\n", "", 1)
    workflow = workflow.replace("          --allow-mixed-attempts \\\n", "", 1)
    workflow = workflow.replace(
        '          --timing-manifests "${timing_files[@]}"\n',
        "",
        1,
    )
    workflow = workflow.replace(
        "        name: coverage-data-${{ matrix.shard }}\n",
        "        name: mistyped-coverage-${{ matrix.shard }}\n",
        1,
    )
    workflow = workflow.replace(
        "        pattern: coverage-data-*\n",
        "        pattern: mistyped-coverage-*\n",
        1,
    )
    workflow = workflow.replace(
        "        path: coverage-data\n",
        "        path: mistyped-coverage\n",
        1,
    )
    workflow = workflow.replace(
        "find coverage-data -maxdepth 1 -type f -name 'shard-timing.*.json' -print",
        "find coverage-data -maxdepth 1 -type f -name 'mistyped-timing.*.json' -print",
        1,
    )
    workflow = workflow.replace(
        "find coverage-data -maxdepth 1 -type f -name 'shard-plan.*.json' -print",
        "find coverage-data -maxdepth 1 -type f -name 'mistyped-plan.*.json' -print",
        1,
    )
    workflow = workflow.replace(
        "find coverage-data -maxdepth 1 -type f -name 'coverage.*' -print",
        "find coverage-data -maxdepth 1 -type f -name 'mistyped-coverage.*' -print",
        1,
    )
    upload_action = (
        "      uses: actions/upload-artifact@"
        "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1\n"
    )
    workflow = workflow.replace(upload_action, upload_action * 2, 1)
    (workflow_dir / "ci.yml").write_text(workflow, encoding="utf-8")
    (tmp_path / "Makefile").write_text(
        (ratchets.ROOT / "Makefile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    errors = ratchets.check_workflow_surface(tmp_path)

    assert "hosted coverage shard job missing shard timing manifest output" in errors
    assert "hosted coverage shard job missing nonempty shard timing guard" in errors
    assert (
        "hosted coverage shard job missing paired coverage, shard plan, and timing "
        "artifact upload" in errors
    )
    assert "hosted coverage shard job missing timing evidence retention" in errors
    assert (
        "hosted coverage shard job missing coverage artifact replacement on retry"
        in errors
    )
    assert (
        "hosted coverage report job missing coverage validation across partial retries"
        in errors
    )
    assert (
        "hosted coverage shard job missing retry-stable coverage artifact "
        "producer name" in errors
    )
    assert "hosted coverage shard job must use one artifact upload action" in errors
    assert (
        "hosted coverage report job missing retry-stable coverage artifact "
        "consumer pattern" in errors
    )
    assert (
        "hosted coverage report job missing coverage artifact download path" in errors
    )
    assert "hosted coverage report job missing exact shard plan artifact glob" in errors
    assert (
        "hosted coverage report job missing exact shard timing artifact glob" in errors
    )
    assert "hosted coverage report job missing exact coverage artifact glob" in errors
    assert (
        "hosted coverage report job missing shard timing agreement validation" in errors
    )


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
