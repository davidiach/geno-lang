"""
Tests for the Geno Embedding API
==================================

Tests geno.run(), geno.check(), RunResult, CheckResult,
step counting, timing, capabilities, error codes, and JSON serialization.
"""

import sys
import time
from pathlib import Path

import pytest

import geno
from geno.api import (
    ConstraintResult,
    RunConfig,
    check,
    check_path,
    constrain_prefix,
    run,
    run_path,
    validate_prefix,
)
from geno.diagnostics import Diagnostic, ErrorCode, Severity
from geno.execution_limits import DEFAULT_INTERPRETER_MAX_STEPS
from geno.tests.project_resolution_fixture_helpers import (
    write_dependency_private_collision_fixture,
)
from geno.values import (
    ArrayValue,
    BuiltinFunction,
    ConstructorValue,
    value_to_json,
)

# =============================================================================
# geno.run() - Basic execution
# =============================================================================


class TestRunBasic:
    """Test basic geno.run() functionality."""

    def test_run_simple_program(self):
        source = """
        func main() -> Int
            return 42
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.value == 42
        assert result.value_raw == 42

    def test_run_returns_string(self):
        source = """
        func main() -> String
            return "hello"
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.value == "hello"

    def test_run_supports_string_length(self):
        source = """
        func main() -> Int
            return length("hello")
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.value == 5

    def test_run_supports_string_concatenation(self):
        source = """
        func main() -> String
            return "hel" + "lo"
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.value == "hello"

    def test_run_returns_list(self):
        source = """
        func main() -> List[Int]
            return [1, 2, 3]
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.value == [1, 2, 3]

    def test_run_returns_none_when_no_main(self):
        source = """
        func helper(x: Int) -> Int
            example 1 -> 2
            return x + 1
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.value is None

    def test_run_checks_example_with_single_tuple_parameter(self):
        source = """
        func sum_pair(pair: (Int, Int)) -> Int
            example (1, 2) -> 3
            let (a, b): (Int, Int) = pair
            return a + b
        end func

        func main() -> Int
            return sum_pair((1, 2))
        end func
        """
        result = run(source)
        assert result.ok is True, [d.message for d in result.diagnostics]
        assert result.value == 3

    def test_run_captures_output(self):
        source = """
        func main() -> Int
            print(42)
            return 0
        end func
        """
        result = run(source, config=RunConfig(capabilities={"print"}))
        assert result.ok is True
        assert "42" in result.output

    def test_run_returns_constructor_value(self):
        source = """
        func main() -> Option[Int]
            return Some(42)
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.value == {"_constructor": "Some", "fields": {"value": 42}}

    def test_run_supports_benchmark_builtins(self):
        source = """
        func main() -> List[Int]
            let nums: List[Int] = [1, 2, 3]
            return set_at(list: nums, index: 1, value: max(7, 4))
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.value == [1, 7, 3]

    def test_run_short_circuits_boolean_operators(self):
        source = """
        func main() -> Bool
            return true or is_positive(head([]))
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.value is True


# =============================================================================
# geno.run() - Error handling
# =============================================================================


class TestRunErrors:
    """Test geno.run() error handling."""

    def test_lexer_error(self):
        source = "func main() -> Int\n    return @@@\nend func"
        result = run(source)
        assert result.ok is False
        assert len(result.diagnostics) > 0
        diag = result.diagnostics[0]
        assert isinstance(diag, Diagnostic)
        assert diag.severity == Severity.ERROR

    def test_invalid_module_name_returns_configuration_diagnostic(self):
        source = "func main() -> Int\n    return 0\nend func"

        result = run(
            source,
            RunConfig(modules={"../../examples/fibonacci": source}),
        )

        assert result.ok is False
        assert result.diagnostics[0].code == ErrorCode.PROJECT_RESOLUTION_ERROR
        assert "Invalid module name" in result.diagnostics[0].message
        assert result.timing.total_ms > 0

    def test_parse_error(self):
        source = "func -> end"
        result = run(source)
        assert result.ok is False
        assert len(result.diagnostics) > 0

    def test_type_error(self):
        source = """
        func main() -> Int
            return "not an int"
        end func
        """
        result = run(source)
        assert result.ok is False
        assert len(result.diagnostics) > 0

    def test_check_reports_multiple_type_errors(self) -> None:
        source = """
        func main() -> Int
            let a: Int = "wrong"
            let b: Bool = 1
            return 0
        end func
        """

        result = check(source)

        assert result.ok is False
        messages = [diag.message for diag in result.diagnostics]
        assert len(messages) >= 2
        assert any("let a" in message for message in messages)
        assert any("let b" in message for message in messages)

    def test_run_rejects_immutable_field_assignment(self):
        source = """
        type Box = Box(value: Int)

        func main() -> Int
            let b = Box(1)
            b.value = 2
            return b.value
        end func
        """
        result = run(source, RunConfig(check_examples=False))

        assert result.ok is False
        assert result.diagnostics
        assert "immutable variable: b" in result.diagnostics[0].message

    def test_runtime_error(self):
        source = """
        func main() -> Int
            let x: List[Int] = []
            return head(x)
        end func
        """
        result = run(source)
        assert result.ok is False
        assert len(result.diagnostics) > 0
        # Should have a runtime error code
        diag = result.diagnostics[0]
        assert diag.code is not None

    def test_contract_failure_is_not_caught_as_string_runtime_error(self):
        source = """
        @untested("contract repro")
        func f(x: Int) -> Int
            requires false
            return x
        end func

        func main() -> String
            try
                let y: Int = f(1)
                return "not caught"
            catch e: String
                return "caught"
            end try
        end func
        """
        result = run(source, RunConfig(check_examples=False))

        assert result.ok is False
        assert result.value is None
        assert result.diagnostics
        assert "Precondition failed" in result.diagnostics[0].message

    def test_negative_shift_reports_runtime_diagnostic(self):
        source = """
        func main() -> Int
            return 1 << -1
        end func
        """
        result = run(source)
        assert result.ok is False
        assert len(result.diagnostics) == 1
        diag = result.diagnostics[0]
        assert diag.code == ErrorCode.RUNTIME_UNKNOWN
        assert "Negative shift count" in diag.message

    def test_host_recursion_error_becomes_sandbox_recursion_limit(self, monkeypatch):
        """Host RecursionError must map to E502, not escape the embedding API."""
        from geno.interpreter import Interpreter

        def _boom(self, program, modules=None, execute_main=True):
            raise RecursionError("host stack exhausted")

        monkeypatch.setattr(Interpreter, "run", _boom)

        source = """
        func main() -> Int
            return 1
        end func
        """

        result = run(source)

        assert result.ok is False
        assert len(result.diagnostics) == 1
        diag = result.diagnostics[0]
        assert diag.code == ErrorCode.SANDBOX_RECURSION_LIMIT
        assert "Host recursion limit exceeded" in diag.message


class TestProjectResolutionApi:
    """Test filesystem-backed API wrappers."""

    def test_run_path_resolves_local_project_files_without_manifest(self, tmp_path):
        app = tmp_path / "App.geno"
        app.write_text(
            "import Utils\nfunc main() -> Int\n    return add_one(41)\nend func\n"
        )
        (tmp_path / "Utils.geno").write_text(
            "func add_one(x: Int) -> Int\n"
            "    example 1 -> 2\n"
            "    return x + 1\n"
            "end func\n"
        )

        result = run_path(str(app))

        assert result.ok is True
        assert result.value == 42

    def test_path_apis_preserve_generic_stdlib_forwarders(self, tmp_path):
        app = tmp_path / "Main.geno"
        app.write_text(
            "import List\n"
            "func main() -> List[String]\n"
            '    return List.take(["a", "b"], 1)\n'
            "end func\n"
        )

        check_result = check_path(str(app))
        run_result = run_path(str(app), RunConfig(timeout=None))

        assert check_result.ok is True, [d.message for d in check_result.diagnostics]
        assert run_result.ok is True, [d.message for d in run_result.diagnostics]
        assert run_result.value == ["a"]

    def test_run_path_resolves_imports_for_legacy_direct_file_extensions(
        self, tmp_path
    ):
        app = tmp_path / "App.gen"
        app.write_text(
            "import Utils\nfunc main() -> Int\n    return add_one(41)\nend func\n"
        )
        (tmp_path / "Utils.geno").write_text(
            "func add_one(x: Int) -> Int\n"
            "    example 1 -> 2\n"
            "    return x + 1\n"
            "end func\n"
        )

        result = run_path(str(app))

        assert result.ok is True
        assert result.value == 42

    def test_run_path_resolves_dependency_modules(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\n'
            'files = ["App.geno"]\n'
            "[dependencies.http-utils]\n"
            'git = "https://example.com/http-utils.git"\n'
        )
        (tmp_path / "App.geno").write_text(
            "import HttpUtils\nfunc main() -> Int\n    return status_code()\nend func\n"
        )
        dep_dir = tmp_path / "geno_modules" / "http-utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "HttpUtils.geno").write_text(
            "func status_code() -> Int\n"
            "    example () -> 204\n"
            "    return 204\n"
            "end func\n"
        )

        result = run_path(str(tmp_path / "App.geno"))

        assert result.ok is True
        assert result.value == 204

    def test_run_path_allows_duplicate_private_dependency_module_names(self, tmp_path):
        app, _alpha_utils, _beta_utils = write_dependency_private_collision_fixture(
            tmp_path
        )

        result = run_path(str(app))

        assert result.ok is True
        assert result.value == 12

    def test_run_path_explicit_modules_override_filesystem_modules(self, tmp_path):
        app = tmp_path / "App.geno"
        app.write_text(
            "import Utils\nfunc main() -> Int\n    return answer()\nend func\n"
        )
        (tmp_path / "Utils.geno").write_text(
            "func answer() -> Int\n    example () -> 1\n    return 1\nend func\n"
        )

        result = run_path(
            str(app),
            RunConfig(
                modules={
                    "Utils": (
                        "func answer() -> Int\n"
                        "    example () -> 2\n"
                        "    return 2\n"
                        "end func\n"
                    )
                }
            ),
        )

        assert result.ok is True
        assert result.value == 2

    def test_check_path_resolves_local_project_modules(self, tmp_path):
        app = tmp_path / "App.geno"
        app.write_text(
            "import Types\nfunc main() -> Wrapper\n    return Wrapper(42)\nend func\n"
        )
        (tmp_path / "Types.geno").write_text("type Wrapper = Wrapper(value: Int)\n")

        result = check_path(str(app))

        assert result.ok is True

    def test_check_path_honors_manifest_targets(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App"]\ntargets = ["python-cli"]\n'
        )
        app = tmp_path / "App.geno"
        app.write_text("func main() -> Int\n  return screen_width()\nend func\n")

        result = check_path(str(app))

        assert result.ok is False
        assert any("screen_width" in diag.message for diag in result.diagnostics)
        assert any("python-cli" in diag.message for diag in result.diagnostics)

    def test_check_path_checks_all_manifest_targets(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\n'
            'files = ["App"]\n'
            'targets = ["python-cli", "node-cli"]\n'
        )
        app = tmp_path / "App.geno"
        app.write_text("func main() -> Unit\n  http_listen(8080)\nend func\n")

        result = check_path(str(app))

        assert result.ok is False
        assert any("http_listen" in diag.message for diag in result.diagnostics)
        assert any("node-cli" in diag.message for diag in result.diagnostics)

    def test_check_path_invalid_manifest_target_fails_closed(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App"]\ntargets = ["browzer"]\n'
        )
        app = tmp_path / "App.geno"
        app.write_text("func main() -> Int\n  return 1\nend func\n")

        result = check_path(str(app))

        assert result.ok is False
        assert len(result.diagnostics) == 1
        assert "Unknown target 'browzer'" in result.diagnostics[0].message

    def test_run_path_checks_all_manifest_targets_before_execution(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\n'
            'files = ["App"]\n'
            'targets = ["python-cli", "node-cli"]\n'
        )
        app = tmp_path / "App.geno"
        app.write_text("func main() -> Unit\n  http_listen(8080)\nend func\n")

        result = run_path(str(app))

        assert result.ok is False
        assert any("http_listen" in diag.message for diag in result.diagnostics)
        assert any("node-cli" in diag.message for diag in result.diagnostics)

    def test_check_path_imported_type_error_uses_real_module_path(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Utils"]\n'
        )
        utils = tmp_path / "Utils.geno"
        utils.write_text(
            '@untested("consistency fixture")\n'
            "func helper(x: Int) -> Int\n"
            '  return "oops"\n'
            "end func\n"
        )
        app = tmp_path / "App.geno"
        app.write_text(
            "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        )

        result = check_path(str(app))

        assert result.ok is False
        assert any(
            diag.location is not None
            and Path(diag.location.filename).resolve() == utils.resolve()
            for diag in result.diagnostics
        )

    def test_check_path_allows_duplicate_private_dependency_module_names(
        self, tmp_path
    ):
        app, _alpha_utils, _beta_utils = write_dependency_private_collision_fixture(
            tmp_path
        )

        result = check_path(str(app))

        assert result.ok is True

    def test_run_path_imported_type_error_uses_real_module_path(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Utils"]\n'
        )
        utils = tmp_path / "Utils.geno"
        utils.write_text(
            '@untested("consistency fixture")\n'
            "func helper(x: Int) -> Int\n"
            '  return "oops"\n'
            "end func\n"
        )
        app = tmp_path / "App.geno"
        app.write_text(
            "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        )

        result = run_path(str(app))

        assert result.ok is False
        assert any(
            diag.location is not None
            and Path(diag.location.filename).resolve() == utils.resolve()
            for diag in result.diagnostics
        )

    def test_check_path_overlay_existing_module_keeps_synthetic_location(
        self, tmp_path
    ):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Utils"]\n'
        )
        utils = tmp_path / "Utils.geno"
        utils.write_text(
            "func helper(x: Int) -> Int\n  example 1 -> 1\n  return x\nend func\n"
        )
        app = tmp_path / "App.geno"
        app.write_text(
            "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        )
        overlay = (
            "\n" * 8 + "func helper(x: Int) -> Int\n"
            "  example 1 -> 1\n"
            '  return "oops"\n'
            "end func\n"
        )

        result = check_path(str(app), modules={"Utils": overlay})

        assert result.ok is False
        assert any(
            diag.location is not None
            and diag.location.filename == "<module:Utils>"
            and diag.location.line == 11
            for diag in result.diagnostics
        )

    def test_run_path_missing_file_reports_clear_resolution_error(self):
        result = run_path("/nonexistent/path.geno")

        assert result.ok is False
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].code == ErrorCode.PROJECT_RESOLUTION_ERROR
        assert result.diagnostics[0].message == "File not found: /nonexistent/path.geno"

    def test_check_path_missing_file_reports_clear_resolution_error(self):
        result = check_path("/nonexistent/path.geno")

        assert result.ok is False
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].code == ErrorCode.PROJECT_RESOLUTION_ERROR
        assert result.diagnostics[0].message == "File not found: /nonexistent/path.geno"


class TestConstraintsApi:
    """Test public constrained-prefix API helpers."""

    def test_constrain_prefix_returns_constraint_result(self):
        result = constrain_prefix("func ")

        assert isinstance(result, ConstraintResult)
        assert result.valid is True
        assert "func" in result.unclosed_blocks
        assert result.allowed_next.allow_identifier is True

    def test_validate_prefix_wrapper_reports_invalid_prefix(self):
        is_valid, error = validate_prefix("end")

        assert is_valid is False
        assert error is not None

    def test_top_level_package_exports_constraint_helpers(self):
        result = geno.constrain_prefix("func ")

        assert result.valid is True
        assert geno.validate_prefix("end")[0] is False


# =============================================================================
# Step counting
# =============================================================================


class TestStepCounting:
    """Test interpreter step counting and limits."""

    def test_steps_are_counted(self):
        source = """
        func main() -> Int
            return 42
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.steps_used > 0

    def test_step_limit_enforced(self):
        source = """
        func main() -> Int
            var x: Int = 0
            while x < 1000 do
                x = x + 1
            end while
            return x
        end func
        """
        config = RunConfig(max_steps=50, timeout=5.0)
        result = run(source, config=config)
        assert result.ok is False
        assert any(d.code == ErrorCode.SANDBOX_STEP_LIMIT for d in result.diagnostics)

    def test_positional_run_config_still_applies_capabilities(self):
        source = """
        func main() -> Int
            print(42)
            return 0
        end func
        """
        result = run(source, RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(
            d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED for d in result.diagnostics
        )

    def test_positional_filename_and_config_are_both_supported(self):
        result = run("func -> end", "inline.geno", RunConfig())
        assert result.ok is False
        assert result.diagnostics[0].location is not None
        assert result.diagnostics[0].location.filename == "inline.geno"

    def test_default_step_budget_allows_small_programs(self):
        source = """
        func main() -> Int
            var x: Int = 0
            while x < 100 do
                x = x + 1
            end while
            return x
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.value == 100
        assert result.steps_used > 100

    def test_step_limit_can_be_disabled_explicitly(self):
        cfg = RunConfig(max_steps=None)
        assert cfg.max_steps is None


# =============================================================================
# Timing
# =============================================================================


class TestTiming:
    """Test timing information in results."""

    def test_timing_fields_populated(self):
        source = """
        func main() -> Int
            return 42
        end func
        """
        result = run(source)
        assert result.ok is True
        assert result.timing.total_ms > 0
        assert result.timing.lex_ms >= 0
        assert result.timing.parse_ms >= 0
        assert result.timing.typecheck_ms >= 0
        assert result.timing.run_ms >= 0

    def test_timing_on_error(self):
        source = "func -> end"
        result = run(source)
        assert result.ok is False
        # Should still have timing data for phases that completed
        assert result.timing.total_ms > 0


# =============================================================================
# geno.check()
# =============================================================================


class TestCheck:
    """Test geno.check() functionality."""

    def test_check_valid_program(self):
        source = """
        func main() -> Int
            return 42
        end func
        """
        result = check(source)
        assert result.ok is True
        assert len(result.diagnostics) == 0

    def test_check_type_error(self):
        source = """
        func main() -> Int
            return "hello"
        end func
        """
        result = check(source)
        assert result.ok is False
        assert len(result.diagnostics) > 0

    def test_invalid_module_name_returns_configuration_diagnostic(self):
        source = "func main() -> Int\n    return 0\nend func"

        result = check(
            source,
            modules={"../../examples/fibonacci": source},
        )

        assert result.ok is False
        assert result.diagnostics[0].code == ErrorCode.PROJECT_RESOLUTION_ERROR
        assert "Invalid module name" in result.diagnostics[0].message

    def test_check_parse_error(self):
        source = "func {{{ end"
        result = check(source)
        assert result.ok is False

    def test_check_timing(self):
        source = """
        func add(a: Int, b: Int) -> Int
            example (1, 2) -> 3
            return a + b
        end func
        """
        result = check(source)
        assert result.ok is True
        assert result.timing.total_ms > 0
        assert result.timing.lex_ms >= 0
        assert result.timing.parse_ms >= 0
        assert result.timing.typecheck_ms >= 0


# =============================================================================
# Capabilities
# =============================================================================


class TestCapabilities:
    """Test capability-based builtin gating."""

    def test_print_available_with_capability(self):
        source = """
        func main() -> Int
            print(42)
            return 0
        end func
        """
        result = run(source, config=RunConfig(capabilities={"print"}))
        assert result.ok is True
        assert "42" in result.output

    def test_print_disabled_without_capability(self):
        source = """
        func main() -> Int
            print(42)
            return 0
        end func
        """
        config = RunConfig(capabilities=set())  # Empty set = no caps
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0
        assert "print" in denied[0].message

    def test_print_enabled_with_capability(self):
        source = """
        func main() -> Int
            print(42)
            return 0
        end func
        """
        config = RunConfig(capabilities={"print"})
        result = run(source, config=config)
        assert result.ok is True
        assert "42" in result.output

    def test_pure_builtins_always_available(self):
        source = """
        func main() -> Int
            return length([1, 2, 3])
        end func
        """
        config = RunConfig(capabilities=set())  # No capabilities
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == 3

    def test_recent_pure_builtins_remain_available_without_capabilities(self):
        source = """
        func main() -> Int
            let nums: List[Int] = [1, 2, 3]
            let updated: List[Int] = set_at(list: nums, index: 1, value: max(7, 4))
            let window: List[Int] = slice(list: updated, start: 1, stop: 3)
            let lowered: String = to_lower("HI")
            if starts_with(lowered, "h") then
                return length(window) + length(to_chars(lowered))
            end if
            return 0
        end func
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == 4

    def test_short_form_string_builtins_available_without_capabilities(self):
        """to_upper, ends_with, replace must be usable under capability enforcement."""
        source = """
        func main() -> String
            let upper: String = to_upper("hello")
            let check: Bool = ends_with(upper, "LLO")
            let result: String = replace(text: upper, old: "HELLO", new: "world")
            if check then
                return result
            end if
            return ""
        end func
        """
        config = RunConfig(capabilities=set())  # No capabilities at all
        result = run(source, config=config)
        assert result.ok is True, [d.message for d in result.diagnostics]
        assert result.value == "world"

    def test_stdlib_wrappers_available_without_capabilities(self):
        """Stdlib wrappers (math_*, list_*, option_*, etc.) must be always-available."""
        source = """
        func main() -> Int
            let a: Int = math_abs(-5)
            let b: Int = math_max(a, 10)
            let xs: List[Int] = [1, 2, 3, 4]
            let ys: List[Int] = list_filter(xs, fn(x: Int) -> x > 2)
            let n: Int = list_length(ys)
            return b + n
        end func
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is True, [d.message for d in result.diagnostics]
        assert result.value == 12


class TestExampleAppsUnderCapabilities:
    """Shipped example apps must type-check and not hit capability-denied errors."""

    EXAMPLES_ROOT = Path(__file__).resolve().parents[2] / "examples" / "apps"

    def test_geno_check_type_checks_under_capabilities(self):
        """geno-check must type-check cleanly (requires fs, print, env)."""
        app_dir = self.EXAMPLES_ROOT / "geno-check"
        if not app_dir.exists():
            pytest.skip("geno-check example not found")
        result = check_path(str(app_dir / "Main.geno"))
        assert result.ok is True, [d.message for d in result.diagnostics]

    def test_geno_pipe_type_checks_under_capabilities(self):
        """geno-pipe must type-check cleanly (requires fs, print, env)."""
        app_dir = self.EXAMPLES_ROOT / "geno-pipe"
        if not app_dir.exists():
            pytest.skip("geno-pipe example not found")
        result = check_path(str(app_dir / "Main.geno"))
        assert result.ok is True, [d.message for d in result.diagnostics]

    def test_geno_pipe_runs_with_capabilities(self):
        """geno-pipe with sample data must run under capability enforcement."""
        app_dir = self.EXAMPLES_ROOT / "geno-pipe"
        if not app_dir.exists():
            pytest.skip("geno-pipe example not found")
        config = RunConfig(capabilities={"print", "env"})
        result = run_path(str(app_dir / "Main.geno"), config=config)
        assert result.ok is True, [d.message for d in result.diagnostics]


# =============================================================================
# Diagnostics
# =============================================================================


class TestDiagnostics:
    """Test diagnostic structure and serialization."""

    def test_diagnostic_has_error_code(self):
        source = "func -> end"
        result = run(source)
        assert result.ok is False
        diag = result.diagnostics[0]
        assert isinstance(diag.code, ErrorCode)

    def test_diagnostic_to_dict(self):
        diag = Diagnostic(
            code=ErrorCode.RUNTIME_DIVISION_BY_ZERO,
            message="Division by zero",
            severity=Severity.ERROR,
        )
        d = diag.to_dict()
        assert d["code"] == "E400"
        assert d["message"] == "Division by zero"
        assert d["severity"] == "error"

    def test_diagnostic_to_dict_with_location(self):
        from geno.tokens import SourceLocation

        diag = Diagnostic(
            code=ErrorCode.TYPE_MISMATCH,
            message="Expected Int, got String",
            severity=Severity.ERROR,
            location=SourceLocation(10, 5, "test.geno"),
        )
        d = diag.to_dict()
        assert d["location"]["line"] == 10
        assert d["location"]["column"] == 5
        assert d["location"]["filename"] == "test.geno"

    def test_diagnostic_str(self):
        diag = Diagnostic(
            code=ErrorCode.RUNTIME_UNKNOWN,
            message="Something went wrong",
            severity=Severity.ERROR,
        )
        s = str(diag)
        assert "[E499]" in s
        assert "Something went wrong" in s


# =============================================================================
# JSON Value Serialization
# =============================================================================


class TestValueToJson:
    """Test value_to_json() serialization."""

    def test_primitives(self):
        assert value_to_json(42) == 42
        assert value_to_json(3.14) == 3.14
        assert value_to_json(True) is True
        assert value_to_json("hello") == "hello"
        assert value_to_json(None) is None

    def test_list(self):
        assert value_to_json([1, 2, 3]) == [1, 2, 3]

    def test_nested_list(self):
        assert value_to_json([[1, 2], [3]]) == [[1, 2], [3]]

    def test_tuple(self):
        result = value_to_json((1, "a"))
        assert result == {"_tuple": [1, "a"]}

    def test_dict(self):
        assert value_to_json({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_constructor_value(self):
        val = ConstructorValue("Some", {"value": 42})
        result = value_to_json(val)
        assert result == {"_constructor": "Some", "fields": {"value": 42}}

    def test_none_constructor(self):
        val = ConstructorValue("None", {})
        result = value_to_json(val)
        assert result == {"_constructor": "None", "fields": {}}

    def test_nested_constructor(self):
        val = ConstructorValue("Ok", {"value": ConstructorValue("Some", {"value": 5})})
        result = value_to_json(val)
        assert result == {
            "_constructor": "Ok",
            "fields": {"value": {"_constructor": "Some", "fields": {"value": 5}}},
        }

    def test_builtin_function(self):
        val = BuiltinFunction("length", len, 1, ["list"])
        result = value_to_json(val)
        assert result == {"_function": "length"}

    def test_float_nan_serialized_as_string(self):
        """NaN must become a JSON-safe string, not invalid JSON token."""
        import json

        result = value_to_json(float("nan"))
        assert result == "NaN"
        # Must be valid JSON
        json.dumps(result, allow_nan=False)

    def test_float_inf_serialized_as_string(self):
        """Infinity must become a JSON-safe string, not invalid JSON token."""
        import json

        assert value_to_json(float("inf")) == "Infinity"
        assert value_to_json(float("-inf")) == "-Infinity"
        # Must be valid JSON
        json.dumps(value_to_json(float("inf")), allow_nan=False)
        json.dumps(value_to_json(float("-inf")), allow_nan=False)

    def test_normal_floats_unchanged(self):
        """Normal floats should pass through unmodified."""
        assert value_to_json(3.14) == 3.14
        assert value_to_json(0.0) == 0.0
        assert value_to_json(-1.5) == -1.5

    def test_special_floats_in_nested_structures(self):
        """Special floats inside lists/dicts must also be sanitized."""
        import json

        result = value_to_json([float("inf"), float("nan")])
        assert result == ["Infinity", "NaN"]
        json.dumps(result, allow_nan=False)

    def test_cyclic_array_serialization(self):
        arr = ArrayValue([1])
        arr[0] = arr

        assert value_to_json(arr) == {"_array": ["[Circular]"]}

    def test_cyclic_constructor_serialization(self):
        arr = ArrayValue([0])
        val = ConstructorValue("Box", {"value": arr})
        arr[0] = val

        assert value_to_json(val) == {
            "_constructor": "Box",
            "fields": {"value": {"_array": ["[Circular]"]}},
        }


# =============================================================================
# Error Codes Enum
# =============================================================================


class TestErrorCodes:
    """Test ErrorCode enum completeness and values."""

    def test_lexer_error_codes_exist(self):
        assert ErrorCode.LEX_UNEXPECTED_CHAR.value == "E100"
        assert ErrorCode.LEX_UNTERMINATED_STRING.value == "E101"

    def test_parse_error_codes_exist(self):
        assert ErrorCode.PARSE_UNEXPECTED_TOKEN.value == "E200"

    def test_type_error_codes_exist(self):
        assert ErrorCode.TYPE_MISMATCH.value == "E300"
        assert ErrorCode.TYPE_UNDEFINED_VAR.value == "E301"

    def test_runtime_error_codes_exist(self):
        assert ErrorCode.RUNTIME_DIVISION_BY_ZERO.value == "E400"
        assert ErrorCode.RUNTIME_UNKNOWN.value == "E499"

    def test_sandbox_error_codes_exist(self):
        assert ErrorCode.SANDBOX_SECURITY_VIOLATION.value == "E500"
        assert ErrorCode.SANDBOX_TIMEOUT.value == "E501"
        assert ErrorCode.SANDBOX_STEP_LIMIT.value == "E503"


# =============================================================================
# RunConfig
# =============================================================================


class TestRunConfig:
    """Test RunConfig defaults and behavior."""

    def test_default_config(self):
        cfg = RunConfig()
        assert cfg.timeout == 5.0
        assert cfg.max_steps == DEFAULT_INTERPRETER_MAX_STEPS
        assert cfg.max_recursion_depth == 500
        assert cfg.max_output_length == 100_000
        assert cfg.max_collection_size == 10_000_000
        assert cfg.max_integer_bits == 33_219
        # None is the API sentinel for fail-closed gated builtins.
        assert cfg.capabilities is None
        assert cfg.check_examples is True
        assert cfg.monitoring_hook is None

    def test_custom_config(self):
        cfg = RunConfig(
            timeout=10.0,
            max_steps=1000,
            max_collection_size=123,
            max_integer_bits=64,
            capabilities={"print"},
        )
        assert cfg.timeout == 10.0
        assert cfg.max_steps == 1000
        assert cfg.max_collection_size == 123
        assert cfg.max_integer_bits == 64
        assert cfg.capabilities == {"print"}

    def test_none_timeout_and_zero_output_limit_are_allowed(self):
        cfg = RunConfig(timeout=None, max_output_length=0)
        assert cfg.timeout is None
        assert cfg.max_output_length == 0

    def test_none_max_steps_disables_cooperative_step_limit(self):
        cfg = RunConfig(max_steps=None)
        assert cfg.max_steps is None

    @pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
    def test_invalid_timeout_rejected(self, timeout):
        with pytest.raises(ValueError, match="timeout"):
            RunConfig(timeout=timeout)

    @pytest.mark.parametrize("max_steps", [0, -1, True, 1.5])
    def test_invalid_max_steps_rejected(self, max_steps):
        with pytest.raises(ValueError, match="max_steps"):
            RunConfig(max_steps=max_steps)

    @pytest.mark.parametrize("max_recursion_depth", [0, -1, True, 1.5])
    def test_invalid_max_recursion_depth_rejected(self, max_recursion_depth):
        with pytest.raises(ValueError, match="max_recursion_depth"):
            RunConfig(max_recursion_depth=max_recursion_depth)

    @pytest.mark.parametrize("max_output_length", [-1, True, 1.5])
    def test_invalid_max_output_length_rejected(self, max_output_length):
        with pytest.raises(ValueError, match="max_output_length"):
            RunConfig(max_output_length=max_output_length)

    @pytest.mark.parametrize("max_collection_size", [-1, True, 1.5])
    def test_invalid_max_collection_size_rejected(self, max_collection_size):
        with pytest.raises(ValueError, match="max_collection_size"):
            RunConfig(max_collection_size=max_collection_size)

    @pytest.mark.parametrize("max_integer_bits", [0, -1, True, 1.5])
    def test_invalid_max_integer_bits_rejected(self, max_integer_bits):
        with pytest.raises(ValueError, match="max_integer_bits"):
            RunConfig(max_integer_bits=max_integer_bits)

    def test_collection_limit_forwarded_to_interpreter(self):
        source = """
        func main() -> List[Int]
            let xs: List[Int] = range(0, 256)
            return concat(xs, xs)
        end func
        """
        result = run(source, RunConfig(check_examples=False, max_collection_size=300))
        assert result.ok is False
        assert "size exceeds limit" in result.diagnostics[0].message

    def test_integer_bit_limit_forwarded_to_interpreter(self):
        source = """
        func main() -> Int
            return 1267650600228229401496703205376
        end func
        """
        result = run(source, RunConfig(check_examples=False, max_integer_bits=64))
        assert result.ok is False
        assert "Integer exceeds maximum size" in result.diagnostics[0].message

    def test_host_callbacks_default_none(self):
        cfg = RunConfig()
        assert cfg.host_callbacks is None


# =============================================================================
# Clock Builtin
# =============================================================================


class TestClockBuiltin:
    """Test clock_now builtin."""

    def test_clock_now_returns_int(self):
        source = """
        func main() -> Int
            return clock_now()
        end func
        """
        config = RunConfig(capabilities={"clock"})
        result = run(source, config=config)
        assert result.ok is True
        assert isinstance(result.value, int)
        assert result.value > 0

    def test_clock_now_gated_by_clock_capability(self):
        source = """
        func main() -> Int
            return clock_now()
        end func
        """
        config = RunConfig(capabilities=set())  # No caps
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0
        assert "clock_now" in denied[0].message

    def test_clock_now_denied_when_capabilities_omitted(self):
        source = """
        func main() -> Int
            return clock_now()
        end func
        """
        result = run(source)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0
        assert "clock_now" in denied[0].message


# =============================================================================
# Random Builtins
# =============================================================================


class TestRandomBuiltins:
    """Test random_int and random_float builtins."""

    def test_random_int_in_range(self):
        source = """
        func main() -> Int
            return random_int(min: 1, max: 10)
        end func
        """
        config = RunConfig(capabilities={"random"})
        result = run(source, config=config)
        assert result.ok is True
        assert 1 <= result.value <= 10

    def test_random_float_in_zero_one(self):
        source = """
        func main() -> Float
            return random_float()
        end func
        """
        config = RunConfig(capabilities={"random"})
        result = run(source, config=config)
        assert result.ok is True
        assert 0.0 <= result.value < 1.0

    def test_random_gated_by_random_capability(self):
        source = """
        func main() -> Int
            return random_int(min: 1, max: 10)
        end func
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0

    def test_random_float_gated(self):
        source = """
        func main() -> Float
            return random_float()
        end func
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0


# =============================================================================
# Host Callbacks
# =============================================================================


class TestHostCallbacks:
    """Test fs_read_text and http_fetch with host callbacks."""

    def test_fs_read_text_with_callback(self):
        source = """
        func main() -> String
            return fs_read_text(path: "/test.txt")
        end func
        """

        def fake_fs_read_text(path):
            return f"contents of {path}"

        config = RunConfig(
            capabilities={"fs"},
            host_callbacks={"fs_read_text": fake_fs_read_text},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "contents of /test.txt"

    def test_http_fetch_with_callback(self):
        source = """
        func main() -> String
            return http_fetch(url: "https://example.com")
        end func
        """

        def fake_http_fetch(url):
            return f"response from {url}"

        config = RunConfig(
            capabilities={"http"},
            host_callbacks={"http_fetch": fake_http_fetch},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "response from https://example.com"

    def test_fs_read_text_denied_without_capability(self):
        source = """
        func main() -> String
            return fs_read_text(path: "/test.txt")
        end func
        """
        config = RunConfig(capabilities=set())  # No fs cap
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0

    def test_fs_callback_not_installed_without_capability(self):
        source = """
        func main() -> String
            return fs_read_text(path: "/test.txt")
        end func
        """

        config = RunConfig(
            capabilities=set(),
            host_callbacks={"fs_read_text": lambda path: f"leaked {path}"},
        )
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0

    def test_host_callback_missing_when_cap_granted_but_no_callback(self):
        source = """
        func main() -> String
            return fs_read_text(path: "/test.txt")
        end func
        """
        config = RunConfig(capabilities={"fs"})  # Cap granted, no callback
        result = run(source, config=config)
        assert result.ok is False
        missing = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_HOST_CALLBACK_MISSING
        ]
        assert len(missing) > 0

    def test_http_fetch_missing_when_cap_granted_but_no_callback(self):
        source = """
        func main() -> String
            return http_fetch(url: "https://example.com")
        end func
        """
        config = RunConfig(capabilities={"http"})  # Cap granted, no callback
        result = run(source, config=config)
        assert result.ok is False
        missing = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_HOST_CALLBACK_MISSING
        ]
        assert len(missing) > 0


class TestTimeoutBehavior:
    """Timeout handling should not leave background work running."""

    def test_sleep_ms_rejects_duration_beyond_active_deadline(self, monkeypatch):
        from geno._serve import install_clock_callbacks
        from geno.interpreter import Interpreter
        from geno.sandbox import SandboxConfig
        from geno.sandbox import TimeoutError as SandboxTimeout

        slept = []
        monkeypatch.setattr(time, "sleep", lambda seconds: slept.append(seconds))

        interp = Interpreter(sandbox_config=SandboxConfig(timeout=0.01))
        install_clock_callbacks(interp)

        with pytest.raises(SandboxTimeout, match="Execution timed out"):
            interp.call_function(
                interp.global_env.bindings["sleep_ms"], [2000], timeout=0.01
            )

        assert slept == []

    def test_process_callbacks_bound_subprocess_timeout_by_active_deadline(
        self, monkeypatch
    ):
        import subprocess

        from geno._serve import install_process_callbacks
        from geno.interpreter import Interpreter
        from geno.sandbox import SandboxConfig

        seen_timeouts = []

        def fake_run(*args, **kwargs):
            seen_timeouts.append(kwargs["timeout"])
            return subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout="",
                stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        interp = Interpreter(sandbox_config=SandboxConfig(timeout=0.05))
        install_process_callbacks(interp)

        result = interp.call_function(
            interp.global_env.bindings["spawn"], [sys.executable, []], timeout=0.05
        )

        assert result.constructor == "Ok"
        assert len(seen_timeouts) == 1
        assert 0 < seen_timeouts[0] <= 0.05

    def test_process_active_deadline_timeout_raises_sandbox_timeout(self, monkeypatch):
        import subprocess

        from geno._serve import install_process_callbacks
        from geno.interpreter import Interpreter
        from geno.sandbox import SandboxConfig
        from geno.sandbox import TimeoutError as SandboxTimeout

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

        monkeypatch.setattr(subprocess, "run", fake_run)

        interp = Interpreter(sandbox_config=SandboxConfig(timeout=0.05))
        install_process_callbacks(interp)

        with pytest.raises(SandboxTimeout, match="Execution timed out"):
            interp.call_function(
                interp.global_env.bindings["spawn"], [sys.executable, []], timeout=0.05
            )

    def test_timeout_waits_for_host_callback_completion(self):
        source = """
        func main() -> String
            return fs_read_text(path: "/test.txt")
        end func
        """
        side_effects = []

        def slow_fs_read_text(path):
            time.sleep(0.1)
            side_effects.append(path)
            return "done"

        start = time.perf_counter()
        result = run(
            source,
            config=RunConfig(
                timeout=0.05,
                capabilities={"fs"},
                host_callbacks={"fs_read_text": slow_fs_read_text},
            ),
        )
        elapsed = time.perf_counter() - start

        assert result.ok is False
        timeout_diags = [
            d for d in result.diagnostics if d.code == ErrorCode.SANDBOX_TIMEOUT
        ]
        assert len(timeout_diags) > 0
        assert elapsed >= 0.09
        assert side_effects == ["/test.txt"]

        time.sleep(0.05)
        assert side_effects == ["/test.txt"]
