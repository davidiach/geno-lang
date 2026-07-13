"""Tests for the local CI workflow helper."""

import sys

import pytest

import scripts.local_ci as local_ci
from scripts.local_ci import (
    build_full_steps,
    build_optional_steps,
    build_release_steps,
    build_targeted_steps,
    main,
)


class TestBuildTargetedSteps:
    def test_python_changes_include_lint_type_compile_and_pytest(self, tmp_path):
        source_file = tmp_path / "demo.py"
        source_file.write_text("print('demo')\n")
        test_file = tmp_path / "test_demo.py"
        test_file.write_text("def test_demo():\n    assert True\n")

        steps = build_targeted_steps(
            [str(source_file)],
            [str(test_file)],
        )

        names = [step.name for step in steps]
        assert names == [
            "ruff-check-targeted",
            "ruff-format-targeted",
            "mypy-targeted",
            "compileall-targeted",
            "pytest-targeted",
        ]
        mypy_step = next(step for step in steps if step.name == "mypy-targeted")
        assert mypy_step.soft_fail_issue is None

    def test_geno_source_changes_include_security_check(self):
        steps = build_targeted_steps(["geno/lsp_server.py"], [])

        assert "ruff-security-targeted" in [step.name for step in steps]

    def test_non_python_paths_require_explicit_tests(self, tmp_path):
        note_file = tmp_path / "notes.md"
        note_file.write_text("# Notes\n")

        with pytest.raises(ValueError, match="No runnable local CI steps"):
            build_targeted_steps([str(note_file)], [])

    def test_pytest_only_targeted_run_is_allowed(self, tmp_path):
        test_file = tmp_path / "test_demo.py"
        test_file.write_text("def test_demo():\n    assert True\n")

        steps = build_targeted_steps([], [str(test_file)])

        assert [step.name for step in steps] == ["pytest-targeted"]

    def test_pytest_node_ids_are_preserved(self, tmp_path):
        test_file = tmp_path / "test_demo.py"
        test_file.write_text("def test_demo():\n    assert True\n")

        steps = build_targeted_steps([], [f"{test_file}::test_demo"])

        assert steps[0].command == (
            sys.executable,
            "-m",
            "pytest",
            "-q",
            f"{test_file}::test_demo",
        )


class TestBuildFullSteps:
    def test_full_plan_treats_repo_wide_mypy_as_required(self):
        steps = build_full_steps()

        mypy_step = next(step for step in steps if step.name == "mypy-full")
        assert mypy_step.soft_fail_issue is None

    def test_full_plan_includes_anytype_recovery_ratchet(self):
        steps = {step.name: step for step in build_full_steps()}

        assert steps["anytype-recovery-ratchet"].command == (
            sys.executable,
            "scripts/check_anytype_recovery.py",
        )

    def test_full_plan_includes_ci_dx_ratchets(self):
        steps = {step.name: step for step in build_full_steps()}

        assert steps["ci-dx-ratchets"].command == (
            sys.executable,
            "scripts/check_ci_dx_ratchets.py",
        )

    def test_full_and_release_plans_do_not_include_optional_jobs(self):
        full_names = [step.name for step in build_full_steps()]
        release_names = [step.name for step in build_release_steps()]

        assert "optional-test-collection" not in full_names
        assert "fuzz-property-tests" not in full_names
        assert "optional-test-collection" not in release_names
        assert "fuzz-property-tests" not in release_names

    def test_release_plan_extends_full_plan(self):
        full_names = [step.name for step in build_full_steps()]
        release_names = [step.name for step in build_release_steps()]

        for name in full_names:
            assert name in release_names
        assert "version-alignment" in release_names
        assert "validate-spec" in release_names
        assert "validate-supported-targets" in release_names
        assert "selfhost-parity" in release_names
        assert "validate-benchmark" in release_names
        assert "release-gate-templates" in release_names
        assert "release-gate-vscode" in release_names
        assert "release-gate-apps" in release_names

    def test_release_plan_includes_release_check_parity_commands(self):
        steps = {step.name: step for step in build_release_steps()}

        assert steps["release-gate-templates"].env == {"PYTHON": sys.executable}
        assert steps["release-gate-vscode"].env == {"PYTHON": sys.executable}
        assert steps["release-gate-apps"].command == (
            sys.executable,
            "scripts/release_gate_apps.py",
        )
        assert steps["validate-spec"].command == (
            sys.executable,
            "scripts/validate_spec.py",
        )
        assert steps["validate-supported-targets"].command == (
            sys.executable,
            "scripts/validate_supported_targets.py",
        )
        assert steps["selfhost-parity"].command == (
            sys.executable,
            "scripts/check_selfhost_parity.py",
        )
        assert steps["ci-dx-ratchets"].command == (
            sys.executable,
            "scripts/check_ci_dx_ratchets.py",
        )

    def test_release_plan_follows_release_check_order(self):
        release_names = [step.name for step in build_release_steps()]

        assert release_names == [
            "version-alignment",
            "dependency-lock-gate",
            "release-gate-templates",
            "release-gate-vscode",
            "release-gate-apps",
            "builtin-parity",
            "validate-spec",
            "validate-supported-targets",
            "ruff-check-full",
            "ruff-format-full",
            "mypy-full",
            "anytype-recovery-ratchet",
            "ci-dx-ratchets",
            "ruff-security-full",
            "pytest-full",
            "examples-check",
            "selfhost-parity",
            "validate-benchmark",
        ]


class TestBuildOptionalSteps:
    def test_optional_plan_matches_hosted_optional_jobs(self):
        steps = build_optional_steps()

        assert [step.name for step in steps] == [
            "optional-test-collection",
            "fuzz-property-tests",
        ]
        assert steps[0].command == (
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "geno/tests/test_backend_parity.py",
            "geno/tests/test_fuzzing.py",
            "geno/tests/test_property_based.py",
            "geno/tests/test_differential_fuzzing.py",
            "-q",
        )
        assert steps[1].command == (
            sys.executable,
            "-m",
            "pytest",
            "geno/tests/test_property_based.py",
            "geno/tests/test_fuzzing.py",
            "geno/tests/test_differential_fuzzing.py",
            "-q",
            "--tb=short",
            "--timeout=60",
        )


class TestCliParsing:
    def test_dry_run_is_accepted_after_subcommand(self):
        assert main(["full", "--dry-run"]) == 0

    def test_optional_dry_run_routes_to_optional_plan(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_run_steps(steps, dry_run=False):
            captured["names"] = [step.name for step in steps]
            captured["dry_run"] = dry_run
            return 0

        monkeypatch.setattr(local_ci, "run_steps", fake_run_steps)

        assert main(["optional", "--dry-run"]) == 0
        assert captured == {
            "names": ["optional-test-collection", "fuzz-property-tests"],
            "dry_run": True,
        }

    def test_global_dry_run_routes_to_optional_plan(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_run_steps(steps, dry_run=False):
            captured["names"] = [step.name for step in steps]
            captured["dry_run"] = dry_run
            return 0

        monkeypatch.setattr(local_ci, "run_steps", fake_run_steps)

        assert main(["--dry-run", "optional"]) == 0
        assert captured == {
            "names": ["optional-test-collection", "fuzz-property-tests"],
            "dry_run": True,
        }
