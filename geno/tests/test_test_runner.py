"""Tests for geno test runner."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from geno.harness import HarnessResult
from geno.sandbox import SandboxConfig
from geno.test_runner import (
    SuiteResult,
    discover_files,
    run_project_test_suite,
    run_test_suite,
)
from geno.tests.project_resolution_fixture_helpers import (
    write_dependency_private_collision_fixture,
)
from geno.values import GenoRuntimeError


def _first_harness_result(result: SuiteResult) -> HarnessResult:
    harness_result = result.file_results[0].harness_result
    assert harness_result is not None
    return harness_result


class TestDiscoverFiles:
    def test_single_file(self, tmp_path):
        f = tmp_path / "test.geno"
        f.write_text("func main() -> Int\n    return 1\nend func\n")
        assert discover_files(f) == [f]

    def test_legacy_gen_single_file(self, tmp_path):
        f = tmp_path / "test.gen"
        f.write_text("func main() -> Int\n    return 1\nend func\n")
        assert discover_files(f) == [f]

    def test_non_geno_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("print('hi')")
        assert discover_files(f) == []

    def test_directory(self, tmp_path):
        (tmp_path / "a.geno").write_text("")
        (tmp_path / "b.geno").write_text("")
        (tmp_path / "c.txt").write_text("")
        files = discover_files(tmp_path)
        assert len(files) == 2
        assert all(f.suffix == ".geno" for f in files)

    def test_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "a.geno").write_text("")
        (sub / "b.geno").write_text("")
        files = discover_files(tmp_path)
        assert len(files) == 2


class TestRunTestSuite:
    def test_passing_examples(self, tmp_path):
        f = tmp_path / "math.geno"
        f.write_text(
            "func double(x: Int) -> Int\n"
            "    example 3 -> 6\n"
            "    example 0 -> 0\n"
            "    return x * 2\n"
            "end func\n"
        )
        result = run_test_suite([f])
        assert result.success
        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0

    def test_failing_example(self, tmp_path):
        f = tmp_path / "bad.geno"
        f.write_text(
            "func broken(x: Int) -> Int\n    example 3 -> 999\n    return x\nend func\n"
        )
        result = run_test_suite([f])
        assert not result.success
        assert result.failed == 1

    def test_float_examples_allow_rounding_noise(self, tmp_path):
        f = tmp_path / "floats.geno"
        f.write_text(
            "func noisy() -> Float\n"
            "    example () -> 0.3\n"
            "    return 0.1 + 0.2\n"
            "end func\n"
            "\n"
            "func noisy_vec() -> Vec[Float]\n"
            "    example () -> vec_from_list([0.3])\n"
            "    return vec_from_list([0.1 + 0.2])\n"
            "end func\n"
        )

        result = run_test_suite([f])

        assert result.success
        assert result.total == 2
        assert result.passed == 2

    def test_parse_error_reported(self, tmp_path):
        f = tmp_path / "invalid.geno"
        f.write_text("this is not valid geno code!!!")
        result = run_test_suite([f])
        assert not result.success
        assert result.errors == 1
        assert result.file_results[0].error is not None

    def test_filter_by_name(self, tmp_path):
        f = tmp_path / "funcs.geno"
        f.write_text(
            "func add(x: Int, y: Int) -> Int\n"
            "    example 1, 2 -> 3\n"
            "    return x + y\n"
            "end func\n"
            "\n"
            "func sub(x: Int, y: Int) -> Int\n"
            "    example 5, 3 -> 2\n"
            "    return x - y\n"
            "end func\n"
        )
        result = run_test_suite([f], filter_pattern="add")
        assert result.total == 1
        assert result.passed == 1

    def test_no_examples_file(self, tmp_path):
        f = tmp_path / "empty.geno"
        f.write_text("func main() -> Int\n    return 42\nend func\n")
        result = run_test_suite([f])
        assert result.success
        assert result.total == 0

    def test_multiple_files(self, tmp_path):
        (tmp_path / "a.geno").write_text(
            "func inc(x: Int) -> Int\n    example 1 -> 2\n    return x + 1\nend func\n"
        )
        (tmp_path / "b.geno").write_text(
            "func dec(x: Int) -> Int\n    example 5 -> 4\n    return x - 1\nend func\n"
        )
        files = discover_files(tmp_path)
        result = run_test_suite(files)
        assert result.success
        assert result.total == 2
        assert len(result.file_results) == 2

    def test_test_block_honors_explicit_step_limit(self, tmp_path):
        f = tmp_path / "loop.geno"
        f.write_text(
            "func spin() -> Int\n"
            "    var i = 0\n"
            "    while true do\n"
            "        i = i + 1\n"
            "    end while\n"
            "    return i\n"
            "end func spin\n\n"
            'test "loop is bounded"\n'
            "    assert spin() == 0\n"
            "end test\n"
        )

        result = run_test_suite(
            [f],
            sandbox_config=SandboxConfig(timeout=5.0, max_steps=20),
        )

        assert not result.success
        assert result.failed == 1
        harness_result = result.file_results[0].harness_result
        assert harness_result is not None
        violation = harness_result.violations[0]
        assert violation.kind == "test"
        assert "Step limit exceeded" in violation.message

    def test_example_honors_explicit_step_limit(self, tmp_path):
        f = tmp_path / "example_loop.geno"
        f.write_text(
            "func spin() -> Int\n"
            "    example () -> 0\n"
            "    var i = 0\n"
            "    while true do\n"
            "        i = i + 1\n"
            "    end while\n"
            "    return i\n"
            "end func spin\n"
        )

        result = run_test_suite(
            [f],
            sandbox_config=SandboxConfig(timeout=5.0, max_steps=20),
        )

        assert not result.success
        assert result.failed == 1
        harness_result = result.file_results[0].harness_result
        assert harness_result is not None
        violation = harness_result.violations[0]
        assert violation.kind == "example"
        assert "Step limit exceeded" in violation.message

    def test_recursive_directory_does_not_execute_nested_project_main(self, tmp_path):
        app_dir = tmp_path / "apps" / "nested-app"
        app_dir.mkdir(parents=True)
        (app_dir / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        main_file = app_dir / "Main.geno"
        main_file.write_text(
            "func helper() -> Int\n"
            "    example () -> 1\n"
            "    return 1\n"
            "end func\n\n"
            "func main() -> Int\n"
            "    return 1 / 0\n"
            "end func\n"
        )

        files = discover_files(tmp_path / "apps")
        result = run_test_suite(files)

        assert files == [main_file]
        assert result.success
        assert result.total == 1
        assert result.passed == 1

    def test_to_dict(self, tmp_path):
        f = tmp_path / "test.geno"
        f.write_text(
            "func id(x: Int) -> Int\n"
            "    example 42 -> 42\n"
            "    return x\n"
            "end func\n\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "    return id(42)\n"
            "end func\n"
        )
        result = run_test_suite([f])
        d = result.to_dict()
        assert d["success"] is True
        assert d["total"] == 1
        assert d["untested"] == 1
        assert len(d["files"]) == 1
        assert d["files"][0]["untested"] == [
            {"function": "main", "reason": "entry point"}
        ]

    def test_cli_json_includes_untested_and_can_fail_on_it(self, tmp_path):
        f = tmp_path / "test.geno"
        f.write_text(
            '@untested("entry point")\nfunc main() -> Int\n    return 1\nend func\n'
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "test",
                "--json",
                "--fail-on-untested",
                str(f),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        payload = json.loads(result.stdout)
        assert result.returncode != 0
        assert payload["success"] is False
        assert payload["untested"] == 1
        assert payload["files"][0]["untested"] == [
            {"function": "main", "reason": "entry point"}
        ]

    def test_with_imports(self, tmp_path):
        (tmp_path / "Utils.geno").write_text(
            "func double(x: Int) -> Int\n"
            "    example 3 -> 6\n"
            "    return x * 2\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Utils\n\n"
            "func quadruple(x: Int) -> Int\n"
            "    example 3 -> 12\n"
            "    return double(double(x))\n"
            "end func\n"
        )
        result = run_test_suite([tmp_path / "Main.geno"])
        assert result.success
        assert result.total == 1


class TestImplMethodExamples:
    """Regression tests for #662 / F-0022: ``geno test`` must descend
    into ``impl`` blocks.  Previously ``_run_examples`` iterated
    ``interp.functions`` — which excludes impl methods — so examples on
    trait implementations were silently skipped."""

    def test_impl_method_example_is_exercised(self, tmp_path):
        f = tmp_path / "shapes.geno"
        f.write_text(
            """
type Circle = MkCircle(radius: Int)

trait Describable
    func describe(self: Self) -> String
end trait

impl Describable for Circle
    func describe(self: Circle) -> String
        example MkCircle(5) -> "Circle"
        return "Circle"
    end func
end impl

func main() -> Int
    return 0
end func
"""
        )
        result = run_test_suite([f])
        assert result.success
        assert result.total == 1
        assert result.passed == 1
        assert result.failed == 0

    def test_filter_pattern_matches_impl_method_base_name(self, tmp_path):
        """``--filter describe`` must still select the impl method
        now that impl harnesses use qualified names (F-0022 polish)."""
        f = tmp_path / "shapes.geno"
        f.write_text(
            """
type Circle = MkCircle(radius: Int)

trait Describable
    func describe(self: Self) -> String
end trait

impl Describable for Circle
    func describe(self: Circle) -> String
        example MkCircle(5) -> "Circle"
        return "Circle"
    end func
end impl

func other(x: Int) -> Int
    example 1 -> 1
    return x
end func

func main() -> Int
    return 0
end func
"""
        )
        result = run_test_suite([f], filter_pattern="describe")
        assert result.success
        # Matches the impl method, not `other`
        assert result.total == 1
        assert result.passed == 1

    def test_filter_pattern_matches_legacy_target_method_alias(self, tmp_path):
        """A filter like ``Circle.describe`` should keep working even though
        the canonical harness name now includes the trait to avoid collisions."""
        f = tmp_path / "shapes.geno"
        f.write_text(
            """
type Circle = MkCircle(radius: Int)

trait Describable
    func describe(self: Self) -> String
end trait

impl Describable for Circle
    func describe(self: Circle) -> String
        example MkCircle(5) -> "Circle"
        return "Circle"
    end func
end impl

func other(x: Int) -> Int
    example 1 -> 1
    return x
end func

func main() -> Int
    return 0
end func
"""
        )
        result = run_test_suite([f], filter_pattern="Circle.describe")
        assert result.success
        assert result.total == 1
        assert result.passed == 1

    def test_impl_method_violation_surfaces(self, tmp_path):
        """A broken impl method is reported (not silently skipped)."""
        f = tmp_path / "shapes.geno"
        f.write_text(
            """
type Square = MkSquare(side: Int)

trait Describable
    func describe(self: Self) -> String
end trait

impl Describable for Square
    func describe(self: Square) -> String
        example MkSquare(3) -> "Square"
        return "WRONG"
    end func
end impl

func main() -> Int
    return 0
end func
"""
        )
        result = run_test_suite([f])
        assert not result.success
        assert result.failed == 1
        violation = _first_harness_result(result).violations[0]
        assert violation.function == "Square.Describable.describe"
        assert violation.kind == "example"


class TestContractViolationClassification:
    """Regression tests for #662 / F-0021: ``requires`` / ``ensures``
    failures are labelled as such by the test report instead of being
    collapsed into generic ``example`` failures."""

    def test_requires_violation_is_labelled_requires(self, tmp_path):
        f = tmp_path / "bad_requires.geno"
        f.write_text(
            """
func sqrt(x: Int) -> Int
    requires x >= 0
    example -1 -> 0
    return 0
end func

func main() -> Int
    return 0
end func
"""
        )
        result = run_test_suite([f])
        assert result.failed == 1
        violations = _first_harness_result(result).violations
        assert len(violations) == 1
        assert violations[0].kind == "requires"
        assert "Precondition failed" in violations[0].message

    def test_ensures_violation_is_labelled_ensures(self, tmp_path):
        f = tmp_path / "bad_ensures.geno"
        f.write_text(
            """
func abs_broken(x: Int) -> Int
    ensures result >= 0
    example -5 -> -5
    return x
end func

func main() -> Int
    return 0
end func
"""
        )
        result = run_test_suite([f])
        assert result.failed == 1
        violations = _first_harness_result(result).violations
        assert len(violations) == 1
        assert violations[0].kind == "ensures"
        assert "Postcondition failed" in violations[0].message

    def test_plain_example_mismatch_remains_example_kind(self, tmp_path):
        """Sanity: a plain value mismatch (no requires/ensures involved)
        stays categorised as ``example`` — we only re-classify the
        runtime failures produced by contract enforcement."""
        f = tmp_path / "plain.geno"
        f.write_text(
            """
func broken(x: Int) -> Int
    example 3 -> 999
    return x
end func

func main() -> Int
    return 0
end func
"""
        )
        result = run_test_suite([f])
        assert result.failed == 1
        violations = _first_harness_result(result).violations
        assert len(violations) == 1
        assert violations[0].kind == "example"

    def test_user_runtime_message_containing_contract_text_stays_example(
        self, tmp_path
    ):
        """Only the raw contract prefixes should reclassify failures; user
        errors that merely mention the words should remain ordinary example
        failures."""
        f = tmp_path / "tricky.geno"
        f.write_text(
            """
func tricky(x: Int) -> Int
    example 1 -> 1
    throw "user says: Precondition failed for demo"
end func

func main() -> Int
    return 0
end func
"""
        )
        result = run_test_suite([f])
        assert result.failed == 1
        violations = _first_harness_result(result).violations
        assert len(violations) == 1
        assert violations[0].kind == "example"


class TestProjectTestSuite:
    """Tests for run_project_test_suite (multi-module via ProjectGraph)."""

    def test_missing_target_becomes_clear_suite_error(self):
        """Missing project targets should preserve a clear file-not-found message."""
        missing = Path("/definitely/missing/geno-project")

        result = run_project_test_suite(missing)

        assert result.success is False
        assert result.errors == 1
        assert result.total == 0
        assert len(result.file_results) == 1
        assert (
            result.file_results[0].error == f"Project Error: File not found: {missing}"
        )

    def test_project_name_collision_becomes_suite_error(self, tmp_path):
        """Project-level resolver failures should not escape as exceptions."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Utils"]\n\n'
            '[dependencies.utils]\ngit = "https://example.com/utils.git"\n'
        )
        (tmp_path / "App.geno").write_text(
            "import Utils\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "  return helper()\n"
            "end func\n"
        )
        (tmp_path / "Utils.geno").write_text(
            "func helper() -> Int\n  example () -> 1\n  return 1\nend func\n"
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text(
            "func helper() -> Int\n  example () -> 2\n  return 2\nend func\n"
        )

        result = run_project_test_suite(tmp_path)

        assert result.success is False
        assert result.errors == 1
        assert result.total == 0
        assert len(result.file_results) == 1
        assert result.file_results[0].error is not None
        assert "Project Error:" in result.file_results[0].error
        assert "Module name collision" in result.file_results[0].error

    def test_dependency_private_module_collisions_do_not_break_suite(self, tmp_path):
        """Dependency-private helpers can reuse stems across different packages."""
        write_dependency_private_collision_fixture(tmp_path)

        result = run_project_test_suite(tmp_path)

        assert result.success is True
        assert result.errors == 0
        assert result.failed == 0
        assert result.total == 5

    def test_runtime_load_error_becomes_suite_error(self, tmp_path, monkeypatch):
        """Expected interpreter runtime errors should be normalized."""
        (tmp_path / "geno.toml").write_text('entrypoint = "App"\nfiles = ["App"]\n')
        (tmp_path / "App.geno").write_text(
            '@untested("entry point")\nfunc main() -> Int\n  return 1\nend func\n'
        )

        def _boom(self, program, modules=None, execute_main=True):
            raise GenoRuntimeError("boom")

        monkeypatch.setattr("geno.interpreter.Interpreter.run", _boom)

        result = run_project_test_suite(tmp_path)

        assert result.success is False
        assert result.errors == 1
        assert result.file_results[0].error == "Runtime Error: Runtime Error: boom"

    def test_unexpected_loader_bug_is_not_swallowed(self, tmp_path, monkeypatch):
        """Unexpected implementation bugs should still surface to the caller."""
        (tmp_path / "geno.toml").write_text('entrypoint = "App"\nfiles = ["App"]\n')
        (tmp_path / "App.geno").write_text(
            '@untested("entry point")\nfunc main() -> Int\n  return 1\nend func\n'
        )

        def _boom(self, program, modules=None, execute_main=True):
            raise AttributeError("unexpected bug")

        monkeypatch.setattr("geno.interpreter.Interpreter.run", _boom)

        with pytest.raises(AttributeError, match="unexpected bug"):
            run_project_test_suite(tmp_path)

    def test_examples_from_all_modules(self, tmp_path):
        """Examples in imported modules are discovered and tested."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Math"]\n'
        )
        (tmp_path / "Math.geno").write_text(
            "func double(x: Int) -> Int\n  example 3 -> 6\n  return x * 2\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Math\n"
            "func quadruple(x: Int) -> Int\n"
            "  example 2 -> 8\n"
            "  return double(double(x))\n"
            "end func\n"
        )

        result = run_project_test_suite(tmp_path)
        assert result.success
        # 1 example from Math.double + 1 from Main.quadruple
        assert result.total == 2
        assert result.passed == 2
        assert len(result.file_results) == 2

    def test_manifest_target_type_errors_fail_project_suite(self, tmp_path):
        """Project tests must enforce the same manifest target as geno check."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main"]\ntargets = ["python-cli"]\n'
        )
        (tmp_path / "Main.geno").write_text(
            "func main() -> Int\n  return screen_width()\nend func\n"
        )

        result = run_project_test_suite(tmp_path)

        assert result.success is False
        assert result.errors == 1
        assert result.file_results[0].error is not None
        assert "screen_width" in result.file_results[0].error
        assert "python-cli" in result.file_results[0].error

    def test_cross_module_dependencies_resolve(self, tmp_path):
        """Cross-module function calls work during test execution."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Lib"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "func inc(x: Int) -> Int\n  example 0 -> 1\n  return x + 1\nend func\n"
        )
        (tmp_path / "App.geno").write_text(
            "import Lib\n"
            "func add_two(x: Int) -> Int\n"
            "  example 5 -> 7\n"
            "  return inc(inc(x))\n"
            "end func\n"
        )

        result = run_project_test_suite(tmp_path)
        assert result.success
        assert result.total == 2
        assert result.passed == 2

    def test_filter_pattern_applies(self, tmp_path):
        """Filter pattern limits which examples are tested."""
        (tmp_path / "geno.toml").write_text('files = ["Funcs"]\n')
        (tmp_path / "Funcs.geno").write_text(
            "func add(x: Int, y: Int) -> Int\n"
            "  example 1, 2 -> 3\n"
            "  return x + y\n"
            "end func\n"
            "\n"
            "func sub(x: Int, y: Int) -> Int\n"
            "  example 5, 3 -> 2\n"
            "  return x - y\n"
            "end func\n"
        )

        result = run_project_test_suite(tmp_path, filter_pattern="add")
        assert result.total == 1
        assert result.passed == 1

    def test_single_file_path_resolves_sibling_imports(self, tmp_path):
        """Direct file targets still pull in sibling imports without geno.toml."""
        (tmp_path / "Utils.geno").write_text(
            "func double(x: Int) -> Int\n  example 3 -> 6\n  return x * 2\nend func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(
            "import Utils\n"
            "func quadruple(x: Int) -> Int\n"
            "  example 2 -> 8\n"
            "  return double(double(x))\n"
            "end func\n"
        )

        result = run_project_test_suite(main_file)

        assert result.success
        assert result.total == 2
        assert result.passed == 2
        assert {Path(fr.path).name for fr in result.file_results} == {
            "Main.geno",
            "Utils.geno",
        }


class TestTestErrorDetail:
    """M-28: internal toolchain errors during a test run must be logged with
    a traceback and tagged with the exception type, not masqueraded as a plain
    user test failure with only str(e)."""

    def test_geno_facing_error_is_unchanged_and_not_logged(self, caplog):
        from geno.test_runner import _test_error_detail

        exc = GenoRuntimeError("Precondition failed: x > 0")
        with caplog.at_level("ERROR", logger="geno.test_runner"):
            detail = _test_error_detail(exc)

        assert detail == str(exc)
        assert not any(
            "Internal error while running tests" in rec.message
            for rec in caplog.records
        )

    def test_internal_error_is_tagged_and_logged(self, caplog):
        from geno.test_runner import _test_error_detail

        exc = AttributeError("'NoneType' object has no attribute 'items'")
        with caplog.at_level("ERROR", logger="geno.test_runner"):
            detail = _test_error_detail(exc)

        # Type name prefixed so the failure is not misattributed to user code.
        assert detail.startswith("AttributeError:")
        assert "items" in detail
        # And the traceback is captured server-side.
        assert any(
            "Internal error while running tests" in rec.message
            and rec.exc_info is not None
            for rec in caplog.records
        )

    def test_type_error_is_geno_facing_not_internal(self, caplog):
        """A user type error must render cleanly (single-file `geno test` path),
        not be tagged/logged as an internal toolchain defect."""
        from geno.test_runner import _test_error_detail
        from geno.tokens import SourceLocation
        from geno.types import GenoTypeError

        loc = SourceLocation(line=1, column=1, filename="x.geno")
        exc = GenoTypeError("cannot apply '+' to Int, String", loc)
        with caplog.at_level("ERROR", logger="geno.test_runner"):
            detail = _test_error_detail(exc)

        assert detail == str(exc)
        assert not any(
            "Internal error while running tests" in rec.message
            for rec in caplog.records
        )

    def test_single_file_type_error_reports_cleanly(self, tmp_path, caplog):
        """End-to-end: `geno test` on a single file with a type error reports it
        as a clean file error without logging a fake internal defect."""
        from geno.test_runner import run_test_suite

        src = tmp_path / "Bad.geno"
        src.write_text(
            'func bad(x: Int) -> Int\n  example bad(1) -> 1\n  return x + "s"\nend func\n'
        )
        with caplog.at_level("ERROR", logger="geno.test_runner"):
            result = run_test_suite([src])

        # The file is reported with an error, but it is not the internal-defect
        # message and no internal-error traceback was logged.
        assert not result.success
        assert not any(
            "Internal error while running tests" in rec.message
            for rec in caplog.records
        )
