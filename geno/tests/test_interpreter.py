"""
Tests for the Geno Interpreter
==============================
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.interpreter import Interpreter, SetValue, interpret
from geno.interpreter import RuntimeError as GenoRuntimeError
from geno.parser import parse
from geno.typechecker import TypeChecker
from geno.typechecker import TypeError as GenoTypeError


def run_program(source: str, check_examples: bool = False):
    """Helper to run a Geno program and return the result."""
    return interpret(source, check_examples=check_examples)


def run_function(source: str, func_name: str, args: list):
    """Helper to run a specific function with arguments."""
    program = parse(source)
    checker = TypeChecker()
    checker.check_program(program)
    interp = Interpreter(check_examples=False)
    interp.run(program)
    func = interp.global_env.lookup(func_name)
    return interp._call_function(func, args)


class TestInterpreterBasics:
    """Basic interpreter functionality tests."""

    def test_return_integer(self):
        """Return an integer value."""
        source = """
        func main() -> Int
            example () -> 42
            return 42
        end func
        """
        result = run_program(source)
        assert result == 42

    def test_return_float(self):
        """Return a float value."""
        source = """
        func main() -> Float
            example () -> 3.14
            return 3.14
        end func
        """
        result = run_program(source)
        assert abs(result - 3.14) < 0.001

    def test_int_return_promotes_to_float_runtime_value(self):
        """A Float return annotation materializes an Int return as float."""
        source = """
        func main() -> Float
            return 2
        end func
        """
        result = run_program(source)
        assert result == 2.0
        assert type(result) is float

    def test_match_expr_int_arm_promotes_to_float_runtime_value(self):
        source = """
        func main() -> Float
            example () -> 2.0
            let y: Float = match 1 with
                | 0 -> 1.5
                | _ -> 2
            end match
            return y
        end func
        """
        result = run_program(source, check_examples=True)
        assert result == 2.0
        assert type(result) is float

    def test_return_string(self):
        """Return a string value."""
        source = """
        func main() -> String
            example () -> "hello"
            return "hello"
        end func
        """
        result = run_program(source)
        assert result == "hello"

    def test_return_bool(self):
        """Return a boolean value."""
        source = """
        func main() -> Bool
            example () -> true
            return true
        end func
        """
        result = run_program(source)
        assert result is True

    def test_type_alias_export_hides_private_runtime_imports(self):
        """Runtime import loading treats exported aliases as explicit exports."""
        module = parse(
            """
            export type UserId = Int

            func private_user_id() -> Int
                example () -> 1
                return 1
            end func
            """
        )
        program = parse(
            """
            import Types

            @untested("runtime visibility")
            func main() -> Int
                return private_user_id()
            end func
            """
        )

        interp = Interpreter(check_examples=False)
        with pytest.raises(GenoRuntimeError, match="private_user_id"):
            interp.run(program, modules={"Types": module})


class TestInterpreterArithmetic:
    """Arithmetic operation tests."""

    def test_addition(self):
        """Integer addition."""
        source = """
        func main() -> Int
            example () -> 7
            return 3 + 4
        end func
        """
        assert run_program(source) == 7

    def test_subtraction(self):
        """Integer subtraction."""
        source = """
        func main() -> Int
            example () -> 2
            return 5 - 3
        end func
        """
        assert run_program(source) == 2

    def test_multiplication(self):
        """Integer multiplication."""
        source = """
        func main() -> Int
            example () -> 12
            return 3 * 4
        end func
        """
        assert run_program(source) == 12

    def test_division(self):
        """Integer division."""
        source = """
        func main() -> Int
            example () -> 3
            return 10 / 3
        end func
        """
        assert run_program(source) == 3

    def test_modulo(self):
        """Integer modulo."""
        source = """
        func main() -> Int
            example () -> 1
            return 10 % 3
        end func
        """
        assert run_program(source) == 1

    def test_operator_precedence(self):
        """Operator precedence is correct."""
        source = """
        func main() -> Int
            example () -> 14
            return 2 + 3 * 4
        end func
        """
        assert run_program(source) == 14


class TestInterpreterComparison:
    """Comparison operation tests."""

    def test_equality(self):
        """Equality comparison."""
        source = """
        func main() -> Bool
            example () -> true
            return 5 == 5
        end func
        """
        assert run_program(source) is True

    def test_inequality(self):
        """Inequality comparison."""
        source = """
        func main() -> Bool
            example () -> true
            return 5 != 3
        end func
        """
        assert run_program(source) is True

    def test_less_than(self):
        """Less than comparison."""
        source = """
        func main() -> Bool
            example () -> true
            return 3 < 5
        end func
        """
        assert run_program(source) is True

    def test_greater_than(self):
        """Greater than comparison."""
        source = """
        func main() -> Bool
            example () -> true
            return 5 > 3
        end func
        """
        assert run_program(source) is True


class TestInterpreterLogical:
    """Logical operation tests."""

    def test_and_true(self):
        """Logical and with true operands."""
        source = """
        func main() -> Bool
            example () -> true
            return true and true
        end func
        """
        assert run_program(source) is True

    def test_and_false(self):
        """Logical and with false operand."""
        source = """
        func main() -> Bool
            example () -> false
            return true and false
        end func
        """
        assert run_program(source) is False

    def test_or_true(self):
        """Logical or with false operands."""
        source = """
        func main() -> Bool
            example () -> true
            return false or true
        end func
        """
        assert run_program(source) is True

    def test_not(self):
        """Logical not."""
        source = """
        func main() -> Bool
            example () -> false
            return not true
        end func
        """
        assert run_program(source) is False


class TestInterpreterVariables:
    """Variable binding tests."""

    def test_let_binding(self):
        """Let binding."""
        source = """
        func main() -> Int
            example () -> 5
            let x: Int = 5
            return x
        end func
        """
        assert run_program(source) == 5

    def test_var_binding(self):
        """Var binding and assignment."""
        source = """
        func main() -> Int
            example () -> 10
            var x: Int = 5
            x = 10
            return x
        end func
        """
        assert run_program(source) == 10

    def test_shadowing(self):
        """Variable shadowing in nested scope."""
        source = """
        func main() -> Int
            example () -> 5
            let x: Int = 5
            if true then
                let x: Int = 10
            end if
            return x
        end func
        """
        assert run_program(source) == 5


class TestInterpreterControlFlow:
    """Control flow tests."""

    def test_if_true(self):
        """If statement with true condition."""
        source = """
        func main() -> Int
            example () -> 1
            if true then
                return 1
            else
                return 0
            end if
        end func
        """
        assert run_program(source) == 1

    def test_if_false(self):
        """If statement with false condition."""
        source = """
        func main() -> Int
            example () -> 0
            if false then
                return 1
            else
                return 0
            end if
        end func
        """
        assert run_program(source) == 0

    def test_while_loop(self):
        """While loop."""
        source = """
        func main() -> Int
            example () -> 55
            var sum: Int = 0
            var i: Int = 1
            while i <= 10 do
                sum = sum + i
                i = i + 1
            end while
            return sum
        end func
        """
        assert run_program(source) == 55

    def test_for_loop(self):
        """For loop."""
        source = """
        func main() -> Int
            example () -> 6
            var sum: Int = 0
            for x: Int in [1, 2, 3] do
                sum = sum + x
            end for
            return sum
        end func
        """
        assert run_program(source) == 6


class TestInterpreterFunctions:
    """Function call tests."""

    def test_simple_function(self):
        """Simple function call."""
        source = """
        func double(x: Int) -> Int
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            example () -> 10
            return double(5)
        end func
        """
        assert run_program(source) == 10

    def test_recursive_function(self):
        """Recursive function call."""
        source = """
        func factorial(n: Int) -> Int
            example 5 -> 120
            if n <= 1 then
                return 1
            else
                return n * factorial(n - 1)
            end if
        end func

        func main() -> Int
            example () -> 120
            return factorial(5)
        end func
        """
        assert run_program(source) == 120

    def test_mutual_recursion(self):
        """Mutually recursive functions."""
        source = """
        func is_even(n: Int) -> Bool
            example 4 -> true
            if n == 0 then
                return true
            else
                return is_odd(n - 1)
            end if
        end func

        func is_odd(n: Int) -> Bool
            example 3 -> true
            if n == 0 then
                return false
            else
                return is_even(n - 1)
            end if
        end func

        func main() -> Bool
            example () -> true
            return is_even(10)
        end func
        """
        assert run_program(source) is True


class TestInterpreterLists:
    """List operation tests."""

    def test_list_literal(self):
        """List literal."""
        source = """
        func main() -> List[Int]
            example () -> [1, 2, 3]
            return [1, 2, 3]
        end func
        """
        result = run_program(source)
        assert result == [1, 2, 3]

    def test_list_length(self):
        """List length."""
        source = """
        func main() -> Int
            example () -> 3
            return length([1, 2, 3])
        end func
        """
        assert run_program(source) == 3

    def test_list_head(self):
        """List head."""
        source = """
        func main() -> Int
            example () -> 1
            return head([1, 2, 3])
        end func
        """
        assert run_program(source) == 1

    def test_list_tail(self):
        """List tail."""
        source = """
        func main() -> List[Int]
            example () -> [2, 3]
            return tail([1, 2, 3])
        end func
        """
        result = run_program(source)
        assert result == [2, 3]

    def test_list_index(self):
        """List indexing."""
        source = """
        func main() -> Int
            example () -> 2
            let arr: List[Int] = [1, 2, 3]
            return arr[1]
        end func
        """
        assert run_program(source) == 2

    def test_list_find_no_match_returns_none(self):
        """list_find returns None_ (not crash) when no element matches."""
        source = """
        func always_false(x: Int) -> Bool
            example 1 -> false
            return false
        end func

        func main() -> String
            example () -> "None"
            let result: Option[Int] = list_find([1, 2, 3], always_false)
            match result with
                | None -> return "None"
                | Some(v) -> return "Some"
            end match
        end func
        """
        result = run_program(source)
        assert result == "None"

    def test_list_find_with_match_returns_some(self):
        """list_find returns Some(x) when predicate matches."""
        source = """
        func is_even(x: Int) -> Bool
            example 2 -> true
            example 3 -> false
            return x % 2 == 0
        end func

        func main() -> Int
            example () -> 2
            let result: Option[Int] = list_find([1, 2, 3], is_even)
            match result with
                | Some(v) -> return v
                | None -> return 0
            end match
        end func
        """
        result = run_program(source)
        assert result == 2

    def test_list_group_by_returns_tuples(self):
        """list_group_by returns List[(K, List[T])] with tuple pairs."""
        source = """
        func classify(x: Int) -> Int
            example 1 -> 0
            example 2 -> 1
            if x < 3 then
                return 0
            end if
            return 1
        end func

        func main() -> List[(Int, List[Int])]
            example () -> [(0, [1, 2]), (1, [3, 4])]
            return list_group_by([1, 2, 3, 4], classify)
        end func
        """
        result = run_program(source)
        assert isinstance(result, list)
        assert len(result) == 2
        assert isinstance(result[0], tuple)
        assert result[0] == (0, [1, 2])
        assert result[1] == (1, [3, 4])


class TestInterpreterPatternMatching:
    """Pattern matching tests."""

    def test_match_some(self):
        """Match Some constructor."""
        source = """
        func unwrap(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func

        func main() -> Int
            example () -> 5
            return unwrap(Some(5))
        end func
        """
        assert run_program(source) == 5

    def test_match_none(self):
        """Match None constructor."""
        source = """
        func unwrap(opt: Option[Int]) -> Int
            example None -> 0
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func

        func main() -> Int
            example () -> 0
            return unwrap(None)
        end func
        """
        assert run_program(source) == 0

    def test_match_wildcard(self):
        """Match with wildcard pattern."""
        source = """
        func classify(x: Int) -> String
            example 5 -> "other"
            match x with
                | 0 -> return "zero"
                | 1 -> return "one"
                | _ -> return "other"
            end match
        end func

        func main() -> String
            example () -> "other"
            return classify(5)
        end func
        """
        assert run_program(source) == "other"


class TestInterpreterPipelines:
    """Pipeline expression tests."""

    def test_simple_pipeline(self):
        """Simple pipeline."""
        source = """
        func main() -> Int
            example () -> 3
            return [1, 2, 3] |> length
        end func
        """
        assert run_program(source) == 3

    def test_pipeline_with_args(self):
        """Pipeline with additional arguments."""
        source = """
        func main() -> List[Int]
            example () -> [2, 4]
            return [1, 2, 3, 4] |> filter(_, fn(x: Int) -> x % 2 == 0)
        end func
        """
        result = run_program(source)
        assert result == [2, 4]


class TestInterpreterLambdas:
    """Lambda expression tests."""

    def test_simple_lambda(self):
        """Simple lambda application."""
        source = """
        func main() -> Int
            example () -> 10
            let double: (Int) -> Int = fn(x: Int) -> x * 2
            return double(5)
        end func
        """
        assert run_program(source) == 10

    def test_lambda_in_map(self):
        """Lambda used in map."""
        source = """
        func main() -> List[Int]
            example () -> [1, 4, 9]
            return map([1, 2, 3], fn(x: Int) -> x * x)
        end func
        """
        result = run_program(source)
        assert result == [1, 4, 9]


class TestInterpreterExampleVerification:
    """Example clause verification tests."""

    def test_correct_examples(self):
        """Correct examples pass verification."""
        source = """
        func add(a: Int, b: Int) -> Int
            example 2, 3 -> 5
            example 0, 0 -> 0
            return a + b
        end func
        """
        # Should not raise
        run_program(source, check_examples=True)

    def test_incorrect_example(self):
        """Incorrect example fails verification."""
        source = """
        func add(a: Int, b: Int) -> Int
            example 2, 3 -> 6
            return a + b
        end func
        """
        with pytest.raises(GenoRuntimeError):
            run_program(source, check_examples=True)

    def test_float_examples_allow_rounding_noise(self):
        """Float examples use tolerance for binary floating-point noise."""
        source = """
        func main() -> Float
            example () -> 0.3
            return 0.1 + 0.2
        end func
        """

        run_program(source, check_examples=True)

    def test_nested_float_examples_allow_rounding_noise(self):
        """Float tolerance applies inside lists and ADT constructor fields."""
        source = """
        type Box = Box(value: Float)

        func totals() -> List[Float]
            example () -> [0.3]
            return [0.1 + 0.2]
        end func

        func boxed() -> Box
            example () -> Box(0.3)
            return Box(0.1 + 0.2)
        end func

        func vector() -> Vec[Float]
            example () -> vec_from_list([0.3])
            return vec_from_list([0.1 + 0.2])
        end func

        func number_set() -> Set[Float]
            example () -> set_from_list([0.3])
            return set_from_list([0.1 + 0.2])
        end func

        func make_map(x: Float) -> MutableMap[String, Float]
            example 0.3 -> make_map(0.3)
            let m: MutableMap[String, Float] = mutable_map_new()
            mutable_map_set(map: m, key: "x", value: x)
            return m
        end func

        func mutable_map() -> MutableMap[String, Float]
            example () -> make_map(0.3)
            return make_map(0.1 + 0.2)
        end func

        func main() -> Int
            example () -> 0
            return 0
        end func
        """

        run_program(source, check_examples=True)

    def test_runtime_float_equality_remains_exact(self):
        """Example tolerance does not change ordinary runtime equality."""
        source = """
        func main() -> Bool
            return (0.1 + 0.2) == 0.3
        end func
        """

        assert run_program(source) is False

    def test_approximate_set_matching_is_not_greedy(self):
        """Set examples use one-to-one matching, not iteration-order greed."""

        class OrderedSetData:
            def __init__(self, items):
                self.items = items

            def __iter__(self):
                return iter(self.items)

            def __len__(self):
                return len(self.items)

        def ordered_set(items):
            value = SetValue()
            object.__setattr__(value, "_data", OrderedSetData(items))
            return value

        interp = Interpreter(check_examples=False)
        actual = ordered_set([3.0e-12, 2.1e-12])
        expected = ordered_set([3.0e-12, 3.9e-12])

        assert interp._values_equal(actual, expected, approximate_floats=True)

    def test_approximate_float_mode_keeps_map_keys_exact(self):
        """Float tolerance applies to values, not map keys."""
        interp = Interpreter(check_examples=False)

        assert not interp._values_equal(
            {0.1 + 0.2: "actual"},
            {0.3: "expected"},
            approximate_floats=True,
        )


class TestInterpreterErrors:
    """Runtime error tests."""

    def test_division_by_zero(self):
        """Division by zero raises error."""
        source = """
        func main() -> Int
            example () -> 0
            return 1 / 0
        end func
        """
        with pytest.raises(GenoRuntimeError):
            run_program(source)

    def test_undefined_variable(self):
        """Undefined variable raises error (caught at type-check time)."""
        source = """
        func main() -> Int
            example () -> 0
            return undefined_var
        end func
        """
        # In Geno, undefined variables are caught by the type checker
        with pytest.raises(GenoTypeError):
            run_program(source)

    def test_head_empty_list(self):
        """Head of empty list raises error."""
        source = """
        func main() -> Int
            example () -> 0
            return head([])
        end func
        """
        with pytest.raises(GenoRuntimeError):
            run_program(source)


class TestStepBudgetEnforcement:
    """Test that the step budget is enforced on all execution paths."""

    def test_list_comparison_counts_steps(self):
        """Comparing large lists must consume steps, not bypass the budget."""
        from geno.sandbox import SandboxConfig, StepLimitExceeded

        source = """
        func main() -> Bool
            let a: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
            let b: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
            return a == b
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        # With a generous budget, comparison should succeed
        config_ok = SandboxConfig(max_steps=500)
        interp = Interpreter(check_examples=False, sandbox_config=config_ok)
        result = interp.run(program)
        assert result is True
        # Each element comparison should have consumed a step
        assert interp.steps > 10

        # With a very tight budget, comparison should hit the limit
        config_tight = SandboxConfig(max_steps=5)
        interp_tight = Interpreter(check_examples=False, sandbox_config=config_tight)
        with pytest.raises(StepLimitExceeded):
            interp_tight.run(program)

    def test_pattern_matching_counts_steps(self):
        """Pattern matching must consume steps per recursive match."""
        from geno.sandbox import SandboxConfig, StepLimitExceeded

        source = """
        func check(opt: Option[Int]) -> Int
            example Some(1) -> 1

            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func

        func main() -> Int
            return check(Some(42))
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        config = SandboxConfig(max_steps=500)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        result = interp.run(program)
        assert result == 42
        # Pattern matching should have consumed steps for _match_pattern calls
        assert interp.steps > 5

    def test_builtin_calls_count_steps(self):
        """Every builtin function call must consume at least one step."""
        from geno.sandbox import SandboxConfig

        source = """
        func main() -> Int
            return length([1, 2, 3])
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        config = SandboxConfig(max_steps=500)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        result = interp.run(program)
        assert result == 3
        steps_with_builtin = interp.steps

        # Run a version without builtins for comparison
        source_no_builtin = """
        func main() -> Int
            return 3
        end func
        """
        program2 = parse(source_no_builtin)
        checker2 = TypeChecker()
        checker2.check_program(program2)

        config2 = SandboxConfig(max_steps=500)
        interp2 = Interpreter(check_examples=False, sandbox_config=config2)
        interp2.run(program2)
        steps_without_builtin = interp2.steps

        # The builtin call should have consumed extra steps
        assert steps_with_builtin > steps_without_builtin

    def test_chained_builtins_hit_step_limit(self):
        """Chained builtin calls must accumulate steps and can exhaust budget."""
        from geno.sandbox import SandboxConfig, StepLimitExceeded

        source = """
        func main() -> List[Int]
            return map([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], fn(x: Int) -> x * 2)
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        # Very tight budget — map calls builtin + closure per element
        config = SandboxConfig(max_steps=3)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(StepLimitExceeded):
            interp.run(program)

    def test_example_verification_shares_step_budget(self):
        """MED-05: steps spent verifying examples count against max_steps so
        the advertised budget is a single honest global bound, not doubled
        by moving work into `example` clauses.
        """
        from geno.sandbox import SandboxConfig, StepLimitExceeded

        # helper() is only exercised by its example clause. With check_examples
        # on, example verification alone must be enough to trip a tight budget.
        source = """
        func helper(n: Int) -> Int
            example (0) -> 0
            example (1) -> 1
            example (2) -> 2
            example (3) -> 3
            example (4) -> 4
            example (5) -> 5
            return n
        end func

        func main() -> Int
            return 0
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        config = SandboxConfig(max_steps=3)
        interp = Interpreter(check_examples=True, sandbox_config=config)
        with pytest.raises(StepLimitExceeded):
            interp.run(program)

    def test_example_verification_does_not_reset_steps(self):
        """MED-05: self.steps is not zeroed after _verify_examples — the
        counter reflects total work done across examples + main.
        """
        from geno.sandbox import SandboxConfig

        source = """
        func helper(n: Int) -> Int
            example (1) -> 2
            return n + 1
        end func

        func main() -> Int
            return helper(10)
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        config = SandboxConfig(max_steps=500)
        interp_with = Interpreter(check_examples=True, sandbox_config=config)
        interp_with.run(program)
        steps_with_examples = interp_with.steps

        config2 = SandboxConfig(max_steps=500)
        interp_without = Interpreter(check_examples=False, sandbox_config=config2)
        interp_without.run(program)
        steps_without_examples = interp_without.steps

        # Example verification must add steps to the shared counter.
        assert steps_with_examples > steps_without_examples


class TestCollectionSizeLimit:
    """Test that collection size limits prevent memory exhaustion."""

    def test_string_doubling_hits_limit(self):
        """Doubling a string in a loop must be caught by collection size limit."""
        from geno.interpreter import RuntimeError as GenoRuntimeError
        from geno.sandbox import SandboxConfig

        source = """
        func main() -> String
            var s: String = "aaaaaaaaaa"
            var i: Int = 0
            while i < 30 do
                s = s + s
                i = i + 1
            end while
            return s
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        config = SandboxConfig(max_collection_size=100_000)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match="size exceeds limit"):
            interp.run(program)

    def test_concat_builtin_hits_limit(self):
        """concat() builtin must also respect collection size limit."""
        from geno.interpreter import RuntimeError as GenoRuntimeError
        from geno.sandbox import SandboxConfig

        source = """
        func main() -> List[Int]
            var xs: List[Int] = [1, 2, 3, 4, 5]
            var i: Int = 0
            while i < 30 do
                xs = concat(xs, xs)
                i = i + 1
            end while
            return xs
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        config = SandboxConfig(max_collection_size=10_000)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match="size exceeds limit"):
            interp.run(program)

    def test_collection_limits_do_not_leak_between_interpreters(self):
        """Builtin prechecks should respect each interpreter's own limit."""
        from geno.sandbox import SandboxConfig

        source = """
        func main() -> Int
            let xs: List[Int] = range(0, 256)
            let ys: List[Int] = concat(xs, xs)
            return length(ys)
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        small = Interpreter(
            check_examples=False,
            sandbox_config=SandboxConfig(max_collection_size=300),
        )
        with pytest.raises(GenoRuntimeError, match="size exceeds limit"):
            small.run(program)

        large = Interpreter(
            check_examples=False,
            sandbox_config=SandboxConfig(max_collection_size=1_000),
        )
        assert large.run(program) == 512

    def test_small_collections_allowed(self):
        """Normal-sized collections should work fine."""
        from geno.sandbox import SandboxConfig

        source = """
        func main() -> String
            return "hello" + " " + "world"
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        config = SandboxConfig(max_collection_size=100)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        result = interp.run(program)
        assert result == "hello world"

    def test_vec_push_hits_limit(self):
        """vec_push() must enforce the sandbox collection size limit."""
        from geno.interpreter import RuntimeError as GenoRuntimeError
        from geno.sandbox import SandboxConfig

        source = """
        func main() -> Int
            let v: Vec[Int] = vec_new()
            vec_push(v, 1)
            vec_push(v, 2)
            vec_push(v, 3)
            vec_push(v, 4)
            vec_push(v, 5)
            vec_push(v, 6)
            return vec_length(v)
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        config = SandboxConfig(max_collection_size=5)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match="Vec size exceeds limit"):
            interp.run(program)

    def test_mutable_map_set_hits_limit(self):
        """mutable_map_set() must enforce the sandbox collection size limit."""
        from geno.interpreter import RuntimeError as GenoRuntimeError
        from geno.sandbox import SandboxConfig

        source = """
        func main() -> Int
            let m: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m, key: "a", value: 1)
            mutable_map_set(map: m, key: "b", value: 2)
            mutable_map_set(map: m, key: "c", value: 3)
            mutable_map_set(map: m, key: "d", value: 4)
            mutable_map_set(map: m, key: "e", value: 5)
            mutable_map_set(map: m, key: "f", value: 6)
            return mutable_map_size(m)
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)

        config = SandboxConfig(max_collection_size=5)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match="MutableMap size exceeds limit"):
            interp.run(program)


class TestPreComputationSizeCheck:
    """Size must be checked BEFORE allocation to prevent OOM on huge multipliers."""

    def test_huge_string_multiply_rejected_before_alloc(self):
        """'a' * 10^18 must raise without attempting allocation."""
        from geno.interpreter import RuntimeError as GenoRuntimeError
        from geno.sandbox import SandboxConfig

        source = """
        func main() -> String
            return "a" * 1000000000000000000
        end func
        """
        program = parse(source)
        config = SandboxConfig(max_collection_size=100_000)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match="size exceeds limit"):
            interp.run(program)


class TestPrintDoesNotLeakToStdout:
    """Interpreter print must capture to buffer only, not write to stdout."""

    def test_print_captured_not_leaked(self, capsys):
        """print() must appear in get_output(), not on host stdout."""
        source = """
        func main() -> Int
            print(42)
            return 0
        end func
        """
        program = parse(source)
        interp = Interpreter(check_examples=False)
        interp.run(program)
        captured = capsys.readouterr()
        assert captured.out == "", f"print leaked to stdout: {captured.out!r}"
        assert "42" in interp.get_output()


class TestCallFunctionDepthTracking:
    """Verify _call_function tracks recursion depth for builtin HOF calls."""

    def test_map_recursive_callback_respects_depth_limit(self):
        """A recursive function called via map must hit the depth limit."""
        from geno.sandbox import RecursionLimitError

        source = """
        func bomb(x: Int) -> Int
            example (0) -> 0
            return bomb(x)
        end func

        func main() -> List[Int]
            example () -> [0]
            return map([1], bomb)
        end func
        """
        with pytest.raises(RecursionLimitError, match="recursion depth"):
            run_program(source)

    def test_fold_recursive_callback_respects_depth_limit(self):
        """A recursive function called via fold must hit the depth limit."""
        from geno.sandbox import RecursionLimitError

        source = """
        func bomb(acc: Int, x: Int) -> Int
            example (0, 0) -> 0
            return bomb(acc, x)
        end func

        func main() -> Int
            example () -> 0
            return fold(list: [1], initial: 0, reducer: bomb)
        end func
        """
        with pytest.raises(RecursionLimitError, match="recursion depth"):
            run_program(source)


class TestRecursionDepthAccuracy:
    """Verify recursion depth matches the configured limit exactly."""

    def test_recursion_depth_matches_limit(self):
        """A function that recurses exactly max_recursion_depth times should succeed."""
        source = """
func count_down(n: Int) -> Int
    example 0 -> 0
    if n == 0 then
        return 0
    end if
    return count_down(n: n - 1)
end func

func main() -> Int
    return count_down(n: 99)
end func
"""
        result = run_program(source)
        assert result == 0

    def test_recursion_depth_not_halved(self):
        """Recursion limit should not be halved by double-counting."""
        source = """
func recurse(n: Int) -> Int
    example 0 -> 0
    if n == 0 then
        return 0
    end if
    return recurse(n: n - 1)
end func

func main() -> Int
    return recurse(n: 490)
end func
"""
        # Default limit is 500; with the old double-counting bug this
        # would fail at depth ~250.  After the fix it should succeed.
        result = run_program(source)
        assert result == 0

    def test_recursive_default_argument_respects_depth_limit(self):
        """Recursive default arguments should hit Geno's recursion guard."""
        from geno.sandbox import RecursionLimitError

        source = """
func recurse(x: Int = recurse()) -> Int
    example (0) -> 0
    return x
end func

func main() -> Int
    return recurse()
end func
"""
        with pytest.raises(RecursionLimitError, match="recursion depth"):
            run_program(source)


class TestFoldIntermediateSizeCheck:
    """Fold must check collection size on intermediate accumulator values."""

    def test_fold_intermediate_list_overflow(self):
        """fold that builds an oversized intermediate list must raise."""
        from geno.sandbox import SandboxConfig

        source = """
        func grow(acc: List[Int], x: Int) -> List[Int]
            example ([1], 0) -> [1, 1]
            return concat(acc, acc)
        end func

        func main() -> Int
            example () -> 0
            return length(fold(list: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21], initial: [1], reducer: grow))
        end func
        """
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)
        config = SandboxConfig(max_collection_size=100_000)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match="size exceeds limit"):
            interp.run(program)


class TestBindingCopySemantics:
    """let/var bindings should copy value containers but preserve reference types."""

    def test_var_binding_deep_copies_nested_constructor_fields(self):
        source = """
        type Point = Point(x: Int)
        type Wrap = Wrap(p: Point)

        func main() -> Int
            var x: Wrap = Wrap(Point(1))
            var y: Wrap = x
            y.p.x = 2
            return x.p.x
        end func main
        """
        assert run_program(source) == 1

    def test_var_binding_handles_recursive_constructor_cycles(self):
        source = """
        type Node = Node(next: Option[Node])

        func main() -> Int
            var x: Node = Node(None)
            x.next = Some(x)
            var y: Node = x
            y.next = None
            return match x.next with
            | Some(value) -> 1
            | None -> 0
            end match
        end func main
        """
        assert run_program(source) == 1

    def test_array_bindings_preserve_reference_semantics(self):
        source = """
        func main() -> Int
            let a: Array[Int] = array_new(1, 0)
            let b: Array[Int] = a
            array_set(array: a, index: 0, value: 99)
            return array_get(b, 0)
        end func main
        """
        assert run_program(source) == 99

    def test_nested_array_references_remain_shared(self):
        source = """
        func main() -> Int
            let x: List[Array[Int]] = [array_new(1, 0)]
            let y: List[Array[Int]] = x
            array_set(array: head(y), index: 0, value: 99)
            return array_get(head(x), 0)
        end func main
        """
        assert run_program(source) == 99


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
