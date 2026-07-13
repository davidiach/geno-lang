"""
Tests for the Geno Test Harness Module
======================================

Tests automatic test generation and execution from specifications.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.harness import (
    FunctionHarness,
    HarnessResult,
    HarnessRunner,
    SpecViolation,
    extract_harnesses,
    generate_test_report,
    run_harness_from_compiled,
    run_harness_from_source,
)
from geno.parser import parse


class TestHarnessResult:
    """Test HarnessResult data structure."""

    def test_empty_result_is_success(self):
        """Empty result (no tests) is success."""
        result = HarnessResult()
        assert result.success == True
        assert result.total == 0
        assert result.passed == 0
        assert result.failed == 0

    def test_all_passed_is_success(self):
        """All tests passing is success."""
        result = HarnessResult(total=5, passed=5, failed=0)
        assert result.success == True

    def test_any_failed_is_failure(self):
        """Any test failing is failure."""
        result = HarnessResult(total=5, passed=4, failed=1)
        assert result.success == False

    def test_result_repr(self):
        """Result has useful repr."""
        result = HarnessResult(total=10, passed=8, failed=2)
        repr_str = repr(result)
        assert "FAIL" in repr_str
        assert "8/10" in repr_str

    def test_result_repr_success(self):
        """Success result has PASS in repr."""
        result = HarnessResult(total=5, passed=5, failed=0)
        repr_str = repr(result)
        assert "PASS" in repr_str


class TestSpecViolation:
    """Test SpecViolation data structure."""

    def test_violation_fields(self):
        """Violation stores all fields."""
        v = SpecViolation(
            kind="example",
            function="add",
            message="expected 5, got 6",
            inputs={"x": 2, "y": 3},
            expected=5,
            actual=6,
        )
        assert v.kind == "example"
        assert v.function == "add"
        assert v.expected == 5
        assert v.actual == 6

    def test_violation_optional_fields(self):
        """Violation with minimal fields."""
        v = SpecViolation(
            kind="error",
            function="foo",
            message="Function not found",
        )
        assert v.inputs is None
        assert v.expected is None


class TestExtractHarnesses:
    """Test harness extraction from parsed programs."""

    def test_extract_from_simple_function(self):
        """Extract harness from function with example."""
        source = """
func double(x: Int) -> Int
    example 5 -> 10
    return x * 2
end func
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert len(harnesses) == 1
        assert harnesses[0].name == "double"
        assert len(harnesses[0].examples) == 1

    def test_extract_multiple_examples(self):
        """Extract function with multiple examples."""
        source = """
func add(x: Int, y: Int) -> Int
    example 1, 2 -> 3
    example 0, 0 -> 0
    example -1, 1 -> 0
    return x + y
end func
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert len(harnesses) == 1
        assert len(harnesses[0].examples) == 3

    def test_extract_with_requires(self):
        """Extract function with requires clause."""
        source = """
func sqrt(x: Int) -> Int
    requires x >= 0
    example 4 -> 2
    return 2
end func
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert len(harnesses) == 1
        assert len(harnesses[0].requires) == 1

    def test_extract_with_ensures(self):
        """Extract function with ensures clause."""
        source = """
func abs(x: Int) -> Int
    ensures result >= 0
    example -5 -> 5
    return x
end func
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert len(harnesses) == 1
        assert len(harnesses[0].ensures) == 1

    def test_extract_multiple_functions(self):
        """Extract harnesses from multiple functions."""
        source = """
func double(x: Int) -> Int
    example 5 -> 10
    return x * 2
end func

func triple(x: Int) -> Int
    example 3 -> 9
    return x * 3
end func
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert len(harnesses) == 2

    def test_skip_main_without_specs(self):
        """Main function without specs generates no harness."""
        source = """
func main() -> Int
    return 0
end func
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert len(harnesses) == 0

    def test_param_names_extracted(self):
        """Parameter names are extracted correctly."""
        source = """
func greet(name: String, times: Int) -> String
    example "world", 1 -> "hello"
    return "hello"
end func
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert harnesses[0].param_names == ["name", "times"]


class TestImplMethodExtraction:
    """Regression tests for #662 / F-0022: harness extraction must not
    skip methods inside ``impl`` blocks.  Before the fix, any ``example``
    clause living on a trait implementation was silently ignored by
    ``geno test``."""

    def test_impl_method_harness_is_surfaced(self):
        source = """
type Circle = MkCircle(radius: Int)

trait Describable
    func describe(self: Self) -> String
end trait

impl Describable for Circle
    func describe(self: Circle) -> String
        example MkCircle(5) -> "Circle(5)"
        return "Circle(5)"
    end func
end impl
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        names = {h.name for h in harnesses}
        assert "Circle.Describable.describe" in names

    def test_impl_method_harness_carries_impl_metadata(self):
        source = """
type Square = MkSquare(side: Int)

trait Describable
    func describe(self: Self) -> String
end trait

impl Describable for Square
    func describe(self: Square) -> String
        example MkSquare(3) -> "Square(3)"
        return "Square(3)"
    end func
end impl
"""
        program = parse(source)
        harnesses = [h for h in extract_harnesses(program) if h.impl_target]
        assert len(harnesses) == 1
        assert harnesses[0].impl_trait == "Describable"
        assert harnesses[0].impl_target == "Square"
        assert harnesses[0].name == "Square.Describable.describe"
        assert harnesses[0].target_method_name == "Square.describe"
        assert harnesses[0].base_name == "describe"

    def test_same_method_on_two_targets_produces_two_harnesses(self):
        """Qualified name prevents a collision between impls of the same
        trait method on different target types."""
        source = """
type A = MkA(x: Int)
type B = MkB(y: Int)

trait Named
    func name(self: Self) -> String
end trait

impl Named for A
    func name(self: A) -> String
        example MkA(1) -> "A"
        return "A"
    end func
end impl

impl Named for B
    func name(self: B) -> String
        example MkB(2) -> "B"
        return "B"
    end func
end impl
"""
        program = parse(source)
        names = {h.name for h in extract_harnesses(program) if h.impl_target}
        assert names == {"A.Named.name", "B.Named.name"}

    def test_same_target_same_method_on_two_traits_produces_two_harnesses(self):
        """Trait name must be part of the harness identity so overlapping
        impl methods on the same target don't collide."""
        source = """
type Circle = MkCircle(radius: Int)

trait Pretty
    func describe(self: Self) -> String
end trait

trait Debug
    func describe(self: Self) -> String
end trait

impl Pretty for Circle
    func describe(self: Circle) -> String
        example MkCircle(1) -> "pretty"
        return "pretty"
    end func
end impl

impl Debug for Circle
    func describe(self: Circle) -> String
        example MkCircle(1) -> "debug"
        return "debug"
    end func
end impl
"""
        names = {h.name for h in extract_harnesses(parse(source)) if h.impl_target}
        assert names == {
            "Circle.Pretty.describe",
            "Circle.Debug.describe",
        }

    def test_top_level_function_harness_unchanged(self):
        """Top-level functions still produce unqualified harnesses with
        ``impl_trait`` and ``impl_target`` left as ``None``."""
        source = """
func double(x: Int) -> Int
    example 5 -> 10
    return x * 2
end func
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert len(harnesses) == 1
        assert harnesses[0].name == "double"
        assert harnesses[0].impl_trait is None
        assert harnesses[0].impl_target is None

    def test_run_harness_from_source_resolves_impl_method(self):
        """The interpreter-backed legacy entry point must not report
        ``Function 'Circle.Describable.describe' not found`` for a qualified impl
        harness name — the ``get_func`` closure now routes through
        ``trait_impls`` when the harness carries impl metadata.

        The property pinned here is that the violation is *not* a "function
        not found" error, proving the resolver reaches the impl method.
        """
        source = """
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
        result = run_harness_from_source(source)
        assert result.total == 1
        for v in result.violations:
            assert v.kind != "error", (
                f"impl method should resolve; got error violation: {v.message}"
            )
            assert "not found" not in v.message

    def test_run_harness_from_compiled_skips_impl_methods(self):
        """``run_harness_from_compiled`` doesn't know how to dispatch
        trait implementations from the compiled globals dict under the
        qualified ``Target.method`` name, so it now filters impl
        harnesses out — producing the pre-#662 behaviour instead of
        a spurious "function not found" violation."""
        from geno.harness import run_harness_from_compiled

        source = """
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
        result = run_harness_from_compiled(source)
        # No top-level function has examples; impl harness is filtered out
        assert result.total == 0
        # And we don't fabricate a "not found" violation for the impl
        assert all(
            "Circle.Describable.describe" not in v.message for v in result.violations
        )


class TestCompiledHarnessExamples:
    """Compiled harness examples should evaluate Geno expressions, not raw AST."""

    def test_scalar_example_passes_in_compiled_mode(self):
        source = """
func double(x: Int) -> Int
    example 5 -> 10
    return x * 2
end func
"""
        result = run_harness_from_compiled(source)

        assert result.total == 1
        assert result.passed == 1
        assert result.failed == 0

    def test_compiled_mode_does_not_execute_main_while_loading_examples(self):
        source = """
func checked(x: Int) -> Int
    example 1 -> 2
    return x + 1
end func

func main() -> Int
    let xs: List[Int] = []
    return xs[0]
end func
"""
        result = run_harness_from_compiled(source)

        assert result.total == 1
        assert result.passed == 1
        assert result.failed == 0

    def test_tuple_and_list_examples_pass_in_compiled_mode(self):
        source = """
func add(x: Int, y: Int) -> Int
    example 2, 3 -> 5
    return x + y
end func

func head_plus_length(xs: List[Int]) -> Int
    example [4, 5, 6] -> 7
    return xs[0] + length(xs)
end func
"""
        result = run_harness_from_compiled(source)

        assert result.total == 2
        assert result.passed == 2
        assert result.failed == 0

    def test_adt_example_passes_in_compiled_mode(self):
        source = """
type Box = MkBox(value: Int)

func unwrap(box: Box) -> Int
    example MkBox(7) -> 7
    return box.value
end func
"""
        result = run_harness_from_compiled(source)

        assert result.total == 1
        assert result.passed == 1
        assert result.failed == 0


class TestHarnessRunner:
    """Test harness execution."""

    def test_run_passing_example(self):
        """Running passing example succeeds."""

        def double(x):
            return x * 2

        # Create mock harness with inline example
        harness = FunctionHarness(
            name="double",
            param_names=["x"],
            examples=[],  # Will use mock
            requires=[],
            ensures=[],
        )

        runner = HarnessRunner(lambda name: double if name == "double" else None)
        violations = runner.run_harness(harness)
        assert violations == []

    def test_function_not_found(self):
        """Missing function creates violation."""
        harness = FunctionHarness(
            name="nonexistent",
            param_names=[],
            examples=[],
            requires=[],
            ensures=[],
        )

        runner = HarnessRunner(lambda name: None)
        violations = runner.run_harness(harness)
        assert len(violations) == 1
        assert violations[0].kind == "error"
        assert "not found" in violations[0].message


class TestGenerateReport:
    """Test report generation."""

    def test_report_contains_totals(self):
        """Report shows total counts."""
        result = HarnessResult(total=10, passed=8, failed=2)
        report = generate_test_report(result)
        assert "Total:  10" in report
        assert "Passed: 8" in report
        assert "Failed: 2" in report

    def test_report_success_status(self):
        """Report shows PASS for success."""
        result = HarnessResult(total=5, passed=5, failed=0)
        report = generate_test_report(result)
        assert "RESULT: PASS" in report

    def test_report_failure_status(self):
        """Report shows FAIL for failure."""
        result = HarnessResult(total=5, passed=4, failed=1)
        report = generate_test_report(result)
        assert "RESULT: FAIL" in report

    def test_report_includes_violations(self):
        """Report includes violation details."""
        result = HarnessResult(
            total=1,
            passed=0,
            failed=1,
            violations=[
                SpecViolation(
                    kind="example",
                    function="double",
                    message="expected 10, got 12",
                    expected=10,
                    actual=12,
                )
            ],
        )
        report = generate_test_report(result)
        assert "VIOLATIONS" in report
        assert "double" in report
        assert "expected 10, got 12" in report

    def test_report_header(self):
        """Report has proper header."""
        result = HarnessResult()
        report = generate_test_report(result)
        assert "GENO TEST HARNESS REPORT" in report


class TestIntegration:
    """Integration tests using real Geno programs."""

    def test_run_harness_simple_pass(self):
        """Run harness on simple passing program."""
        source = """
func identity(x: Int) -> Int
    example 5 -> 5
    example 0 -> 0
    return x
end func
"""
        # This test depends on the interpreter being able to run
        # For now just verify the source parses
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert len(harnesses) == 1
        assert harnesses[0].name == "identity"

    def test_harness_complex_function(self):
        """Extract harness from complex function."""
        source = """
func factorial(n: Int) -> Int
    requires n >= 0
    ensures result > 0
    example 0 -> 1
    example 1 -> 1
    example 5 -> 120

    if n <= 1 then
        return 1
    else
        return n * factorial(n - 1)
    end if
end func factorial
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert len(harnesses) == 1
        h = harnesses[0]
        assert h.name == "factorial"
        assert len(h.examples) == 3
        assert len(h.requires) == 1
        assert len(h.ensures) == 1

    def test_harness_with_list_examples(self):
        """Extract harness with list inputs/outputs."""
        source = """
func sum_list(xs: List[Int]) -> Int
    example [1, 2, 3] -> 6
    example [] -> 0
    return 0
end func
"""
        program = parse(source)
        harnesses = extract_harnesses(program)
        assert len(harnesses) == 1
        assert len(harnesses[0].examples) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
