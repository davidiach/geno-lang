"""
Tests for the Geno Type Checker
===============================
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.parser import parse
from geno.typechecker import TypeChecker, type_check
from geno.typechecker import TypeError as GenoTypeError


def check_program(source: str):
    """Helper to type check a Geno program."""
    program = parse(source)
    type_check(program)


def expect_type_error(source: str) -> GenoTypeError:
    """Helper to check that source produces a type error."""
    program = parse(source)
    with pytest.raises(GenoTypeError) as exc_info:
        type_check(program)
    return exc_info.value  # type: ignore[no-any-return]


class TestTypecheckerBasics:
    """Basic type checking tests."""

    def test_checker_init_cache_keeps_builtin_state_isolated(self):
        """Later TypeChecker instances should not inherit mutable builtin edits."""
        first = TypeChecker()
        length_type = first.global_env.lookup("length")
        assert length_type is not None
        first.global_env.bind("user_symbol", length_type)
        first.func_param_names["length"].append("extra")
        first.type_defs["Option"].variants["Oops"] = []

        second = TypeChecker()
        assert second.global_env.lookup("user_symbol") is None
        assert second.func_param_names["length"] == ["list"]
        assert "Oops" not in second.type_defs["Option"].variants

    def test_valid_integer_return(self):
        """Valid integer return type."""
        source = """
        func main() -> Int
            return 42
        end func
        """
        check_program(source)  # Should not raise

    def test_valid_float_return(self):
        """Valid float return type."""
        source = """
        func main() -> Float
            return 3.14
        end func
        """
        check_program(source)

    def test_int_return_is_compatible_with_float_return(self):
        """Int values remain assignable to Float return annotations."""
        source = """
        func main() -> Float
            return 2
        end func
        """
        check_program(source)

    def test_valid_string_return(self):
        """Valid string return type."""
        source = """
        func main() -> String
            return "hello"
        end func
        """
        check_program(source)

    def test_valid_bool_return(self):
        """Valid boolean return type."""
        source = """
        func main() -> Bool
            return true
        end func
        """
        check_program(source)

    def test_return_type_mismatch(self):
        """Return type mismatch produces error."""
        source = """
        func main() -> Int
            return "hello"
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()


class TestTypecheckerVariables:
    """Variable type checking tests."""

    def test_valid_let_binding(self):
        """Valid let binding."""
        source = """
        func main() -> Int
            let x: Int = 5
            return x
        end func
        """
        check_program(source)

    def test_let_type_mismatch(self):
        """Let binding type mismatch."""
        source = """
        func main() -> Int
            let x: Int = "hello"
            return x
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()

    def test_undefined_variable(self):
        """Undefined variable produces error."""
        source = """
        func main() -> Int
            return x
        end func
        """
        error = expect_type_error(source)
        assert "undefined" in error.message.lower()

    def test_var_binding(self):
        """Valid var binding with assignment."""
        source = """
        func main() -> Int
            var x: Int = 5
            x = 10
            return x
        end func
        """
        check_program(source)

    def test_immutable_assignment(self):
        """Assignment to immutable variable produces error."""
        source = """
        func main() -> Int
            let x: Int = 5
            x = 10
            return x
        end func
        """
        error = expect_type_error(source)
        assert "immutable" in error.message.lower()

    def test_immutable_shadow_rejects_assignment(self):
        """A `let` that shadows an outer `var` must itself be immutable (#656, F-0001)."""
        source = """
        func main() -> Int
            var x: Int = 1
            if true then
                let x: Int = 2
                x = 3
            end if
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "immutable" in error.message.lower()

    def test_assignment_type_mismatch(self):
        """Assignment type mismatch."""
        source = """
        func main() -> Int
            var x: Int = 5
            x = "hello"
            return x
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()

    def test_let_rejects_generic_builtin_bound_to_concrete_function_type(self):
        """let bindings should not treat generic builtin TypeVars as wildcards."""
        source = """
        func main() -> String
            let f: (Option[Int]) -> String = unwrap
            return f(Some(1))
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()

    def test_assignment_rejects_generic_builtin_bound_to_concrete_function_type(self):
        """Assignments should reject generic builtins bound to narrower function types."""
        source = """
        func main() -> String
            var f: (Option[Int]) -> String = fn(opt: Option[Int]) -> "ok"
            f = unwrap
            return f(Some(1))
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()

    def test_let_allows_monomorphic_instantiation_of_generic_function_value(self):
        """let bindings may instantiate a generic function to a concrete function type."""
        source = """
        func main() -> String
            let f: (Int) -> String = to_string
            return f(1)
        end func
        """
        check_program(source)


class TestTypecheckerTypeVarSoundness:
    """TypeVar wildcard checks should stay limited to call-site inference."""

    def test_default_value_rejects_generic_builtin_for_concrete_function_type(self):
        """Default values should not accept generic builtins for concrete function types."""
        source = """
        @untested("typevar")
        func call_with_default(f: (Option[Int]) -> String = unwrap) -> String
            return f(Some(1))
        end func

        func main() -> String
            return call_with_default()
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()

    def test_return_rejects_generic_builtin_for_concrete_function_type(self):
        """Return statements should reject generic builtins for concrete function types."""
        source = """
        @untested("typevar")
        func make_unwrapper() -> (Option[Int]) -> String
            return unwrap
        end func

        func main() -> String
            let f: (Option[Int]) -> String = make_unwrapper()
            return f(Some(1))
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()

    def test_higher_order_generic_builtin_callback_infers_concrete_result(self):
        """Higher-order generic callbacks should resolve to concrete return types."""
        source = """
        func main() -> List[Int]
            return map([Some(1)], unwrap)
        end func
        """
        check_program(source)


class TestTypecheckerArithmetic:
    """Arithmetic type checking tests."""

    def test_int_arithmetic(self):
        """Integer arithmetic."""
        source = """
        func main() -> Int
            return 1 + 2 * 3 - 4 / 2
        end func
        """
        check_program(source)

    def test_float_arithmetic(self):
        """Float arithmetic."""
        source = """
        func main() -> Float
            return 1.0 + 2.0 * 3.0
        end func
        """
        check_program(source)

    def test_mixed_arithmetic_returns_float(self):
        """Mixed int/float arithmetic returns float."""
        source = """
        func main() -> Float
            return 1 + 2.0
        end func
        """
        check_program(source)

    def test_invalid_arithmetic_types(self):
        """Arithmetic on non-numeric types produces error."""
        source = """
        func main() -> Int
            return "a" + 1
        end func
        """
        error = expect_type_error(source)
        assert "cannot apply" in error.message.lower()

    @pytest.mark.parametrize(
        "expr",
        [
            'math_abs("x")',
            'math_min("a", "b")',
            'math_max("a", "b")',
            'math_clamp(value: "b", lo: "a", hi: "c")',
            'clamp(value: "b", min: "a", max: "c")',
            'max("a", "b")',
            'abs("x")',
            'square("x")',
        ],
    )
    def test_numeric_typevar_builtins_reject_non_numeric_arguments(self, expr):
        source = f"""
        func main() -> String
            return {expr}
        end func
        """

        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()


class TestTypecheckerComparison:
    """Comparison type checking tests."""

    def test_int_comparison(self):
        """Integer comparison."""
        source = """
        func main() -> Bool
            return 5 < 10
        end func
        """
        check_program(source)

    def test_float_and_mixed_numeric_ordering(self):
        """Numeric ordering permits Int/Float combinations."""
        source = """
        func main() -> Bool
            return 5 < 10.0 and 3.5 >= 3
        end func
        """
        check_program(source)

    def test_string_ordering(self):
        """String ordering remains valid."""
        source = """
        func main() -> Bool
            return "a" < "b"
        end func
        """
        check_program(source)

    def test_equality(self):
        """Equality comparison."""
        source = """
        func main() -> Bool
            return 5 == 5
        end func
        """
        check_program(source)

    def test_structural_equality_still_allowed(self):
        """Equality still accepts compatible structural values."""
        source = """
        type Box = Box(value: Int)

        func main() -> Bool
            return [1] == [1] and Box(1) == Box(1)
        end func
        """
        check_program(source)

    @pytest.mark.parametrize(
        "expr",
        [
            "true < false",
            "[1] < [2]",
            "(1, 2) < (1, 3)",
            "Box(1) < Box(2)",
        ],
    )
    def test_ordering_rejects_non_orderable_types(self, expr):
        """Ordering operators require numeric or string operands."""
        source = f"""
        type Box = Box(value: Int)

        func main() -> Bool
            return {expr}
        end func
        """
        error = expect_type_error(source)
        assert "operands must both be numeric" in error.message


class TestTypecheckerLogical:
    """Logical operation type checking tests."""

    def test_logical_and(self):
        """Logical and requires booleans."""
        source = """
        func main() -> Bool
            return true and false
        end func
        """
        check_program(source)

    def test_logical_or(self):
        """Logical or requires booleans."""
        source = """
        func main() -> Bool
            return true or false
        end func
        """
        check_program(source)

    def test_logical_not(self):
        """Logical not requires boolean."""
        source = """
        func main() -> Bool
            return not false
        end func
        """
        check_program(source)

    def test_invalid_and_operand(self):
        """Non-boolean operand in and produces error."""
        source = """
        func main() -> Bool
            return 5 and true
        end func
        """
        error = expect_type_error(source)
        assert "bool" in error.message.lower()


class TestTypecheckerControlFlow:
    """Control flow type checking tests."""

    def test_if_statement(self):
        """If statement with boolean condition."""
        source = """
        func main() -> Int
            if true then
                return 1
            else
                return 0
            end if
        end func
        """
        check_program(source)

    def test_if_non_boolean_condition(self):
        """If with non-boolean condition produces error."""
        source = """
        func main() -> Int
            if 5 then
                return 1
            else
                return 0
            end if
        end func
        """
        error = expect_type_error(source)
        assert "bool" in error.message.lower()

    def test_while_loop(self):
        """While loop with boolean condition."""
        source = """
        func main() -> Int
            var x: Int = 0
            while x < 10 do
                x = x + 1
            end while
            return x
        end func
        """
        check_program(source)

    def test_while_non_boolean_condition(self):
        """While with non-boolean condition produces error."""
        source = """
        func main() -> Int
            while 5 do
                return 1
            end while
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "bool" in error.message.lower()

    def test_for_loop(self):
        """For loop with proper types."""
        source = """
        func main() -> Int
            var sum: Int = 0
            for x: Int in [1, 2, 3] do
                sum = sum + x
            end for
            return sum
        end func
        """
        check_program(source)

    def test_for_loop_type_mismatch(self):
        """For loop variable type mismatch."""
        source = """
        func main() -> Int
            var sum: Int = 0
            for x: String in [1, 2, 3] do
                sum = sum + 1
            end for
            return sum
        end func
        """
        error = expect_type_error(source)
        assert "match" in error.message.lower()  # "doesn't match"


class TestTypecheckerFunctions:
    """Function type checking tests."""

    def test_function_call(self):
        """Valid function call."""
        source = """
        func double(x: Int) -> Int
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            return double(21)
        end func
        """
        check_program(source)

    def test_wrong_arg_count(self):
        """Wrong argument count produces error."""
        source = """
        func double(x: Int) -> Int
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            return double(1, 2)
        end func
        """
        error = expect_type_error(source)
        assert "argument" in error.message.lower()

    def test_wrong_arg_type(self):
        """Wrong argument type produces error."""
        source = """
        func double(x: Int) -> Int
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            return double("hello")
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()

    def test_undefined_function(self):
        """Undefined function produces error."""
        source = """
        func main() -> Int
            return undefined_func(42)
        end func
        """
        error = expect_type_error(source)
        assert "undefined" in error.message.lower()


class TestTypecheckerLists:
    """List type checking tests."""

    def test_list_literal(self):
        """Valid list literal."""
        source = """
        func main() -> List[Int]
            return [1, 2, 3]
        end func
        """
        check_program(source)

    def test_list_element_mismatch(self):
        """Mixed types in list produces error."""
        source = """
        func main() -> List[Int]
            return [1, "two", 3]
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()

    def test_list_index(self):
        """Valid list indexing."""
        source = """
        func main() -> Int
            let arr: List[Int] = [1, 2, 3]
            return arr[0]
        end func
        """
        check_program(source)

    def test_list_index_non_int(self):
        """Non-integer list index produces error."""
        source = """
        func main() -> Int
            let arr: List[Int] = [1, 2, 3]
            return arr["zero"]
        end func
        """
        error = expect_type_error(source)
        assert "int" in error.message.lower()


class TestTypecheckerLambdas:
    """Lambda type checking tests."""

    def test_simple_lambda(self):
        """Valid lambda expression."""
        source = """
        func main() -> Int
            let f: (Int) -> Int = fn(x: Int) -> x * 2
            return f(21)
        end func
        """
        check_program(source)

    def test_lambda_param_mismatch(self):
        """Lambda parameter type mismatch."""
        source = """
        func main() -> Int
            let f: (String) -> Int = fn(x: Int) -> x * 2
            return f("hello")
        end func
        """
        # This should produce an error due to param type mismatch
        error = expect_type_error(source)
        # The error can be about type compatibility
        assert "mismatch" in error.message.lower() or "type" in error.message.lower()


class TestTypecheckerPatternMatching:
    """Pattern matching type checking tests."""

    def test_match_option(self):
        """Valid match on Option type."""
        source = """
        func unwrap(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func

        func main() -> Int
            return unwrap(Some(42))
        end func
        """
        check_program(source)

    def test_match_literal(self):
        """Match with literal patterns."""
        source = """
        func classify(x: Int) -> String
            example 0 -> "zero"
            match x with
                | 0 -> return "zero"
                | _ -> return "other"
            end match
        end func

        func main() -> String
            return classify(5)
        end func
        """
        check_program(source)


class TestTypecheckerRecursivePatternExhaustiveness:
    """Pattern exhaustiveness checking tests."""

    def test_non_exhaustive_option_missing_none(self):
        """Missing None case in Option match produces error."""
        source = """
        func unwrap(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "None" in error.message

    def test_non_exhaustive_option_missing_some(self):
        """Missing Some case in Option match produces error."""
        source = """
        func check(opt: Option[Int]) -> Int
            example None -> 0
            match opt with
                | None -> return 0
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "Some" in error.message

    def test_exhaustive_with_wildcard(self):
        """Wildcard pattern makes match exhaustive."""
        source = """
        func unwrap(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | _ -> return 0
            end match
        end func
        """
        check_program(source)  # Should not raise

    def test_exhaustive_with_variable(self):
        """Variable pattern makes match exhaustive."""
        source = """
        func unwrap(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | other -> return 0
            end match
        end func
        """
        check_program(source)  # Should not raise

    def test_non_exhaustive_user_type(self):
        """Missing variant in user-defined type produces error."""
        source = """
        type Color = Red | Green | Blue

        func to_int(c: Color) -> Int
            example Red -> 0
            match c with
                | Red -> return 0
                | Green -> return 1
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "Blue" in error.message

    def test_exhaustive_user_type(self):
        """All variants covered is exhaustive."""
        source = """
        type Color = Red | Green | Blue

        func to_int(c: Color) -> Int
            example Red -> 0
            match c with
                | Red -> return 0
                | Green -> return 1
                | Blue -> return 2
            end match
        end func
        """
        check_program(source)  # Should not raise

    def test_constructor_from_other_type_rejected(self):
        """Constructor patterns must belong to the scrutinee type."""
        source = """
        type Foo = Foo(value: Int)
        type Bar = Bar(value: Int)

        func read(foo: Foo) -> Int
            example Foo(1) -> 0
            match foo with
                | Bar(value) -> return value
                | _ -> return 0
            end match
        end func
        """
        error = expect_type_error(source)
        assert "does not belong" in error.message
        assert "Bar" in error.message


class TestNonFiniteMatchExhaustiveness:
    """Non-finite types (Int, Float, String, List, Tuple) require a default arm."""

    def test_int_match_without_default_errors(self):
        source = """
        func check(x: Int) -> String
            example 0 -> "zero"
            match x with
                | 1 -> return "one"
                | 2 -> return "two"
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "default arm" in error.message

    def test_int_match_with_wildcard_ok(self):
        source = """
        func check(x: Int) -> String
            example 0 -> "other"
            match x with
                | 1 -> return "one"
                | _ -> return "other"
            end match
        end func
        """
        check_program(source)

    def test_int_match_with_variable_ok(self):
        source = """
        func check(x: Int) -> String
            example 0 -> "other"
            match x with
                | 1 -> return "one"
                | n -> return "other"
            end match
        end func
        """
        check_program(source)

    def test_float_match_without_default_errors(self):
        source = """
        func check(x: Float) -> String
            example 0.0 -> "zero"
            match x with
                | 1.0 -> return "one"
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "default arm" in error.message

    def test_match_expr_int_float_arms_are_order_independent(self):
        source = """
        func compute(x: Int) -> Float
            example 0 -> 2.0
            let y: Float = match x with
                | 0 -> 2
                | _ -> 1.5
            end match
            return y
        end func
        """
        check_program(source)

    def test_string_match_without_default_errors(self):
        source = """
        func check(s: String) -> Int
            example "x" -> 0
            match s with
                | "hello" -> return 1
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "default arm" in error.message

    def test_string_match_with_wildcard_ok(self):
        source = """
        func check(s: String) -> Int
            example "x" -> 0
            match s with
                | "hello" -> return 1
                | _ -> return 0
            end match
        end func
        """
        check_program(source)

    def test_list_match_without_default_errors(self):
        source = """
        func check(xs: List[Int]) -> Int
            example [1] -> 1
            match xs with
                | [] -> return 0
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "default arm" in error.message

    def test_list_match_with_wildcard_ok(self):
        source = """
        func check(xs: List[Int]) -> Int
            example [1] -> 0
            match xs with
                | [] -> return 0
                | _ -> return 1
            end match
        end func
        """
        check_program(source)

    def test_list_match_empty_plus_rest_is_exhaustive(self):
        source = """
        func check(xs: List[Int]) -> Int
            example [1] -> 1
            match xs with
                | [] -> return 0
                | [x, ...rest] -> return x
            end match
        end func
        """
        check_program(source)

    def test_list_match_rest_only_is_exhaustive(self):
        source = """
        func check(xs: List[Int]) -> Int
            example [1] -> 1
            match xs with
                | [...rest] -> return length(rest)
            end match
        end func
        """
        check_program(source)

    def test_list_match_can_still_be_non_exhaustive_without_default(self):
        source = """
        func check(xs: List[Int]) -> Int
            example [1] -> 1
            match xs with
                | [] -> return 0
                | [x, y, ...rest] -> return x + y
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "default arm" in error.message

    def test_list_match_constrained_head_element_requires_default(self):
        # [] covers the empty list and [true, ...rest] covers non-empty lists
        # whose head is true, but [false] stays unmatched.  A constrained
        # element must not be treated as covering its whole length class.
        source = """
        func check(xs: List[Bool]) -> Int
            example [] -> 0
            match xs with
                | [] -> return 0
                | [true, ...rest] -> return 1
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "default arm" in error.message

    def test_list_match_catchall_head_with_rest_is_exhaustive(self):
        # The companion to the test above: a catch-all head with a rest does
        # cover its whole length class, so this stays exhaustive.
        source = """
        func check(xs: List[Bool]) -> Int
            example [] -> 0
            match xs with
                | [] -> return 0
                | [b, ...rest] -> return 1
            end match
        end func
        """
        check_program(source)


class TestTypecheckerPipelines:
    """Pipeline type checking tests."""

    def test_simple_pipeline(self):
        """Valid simple pipeline."""
        source = """
        func main() -> Int
            return [1, 2, 3] |> length
        end func
        """
        check_program(source)

    def test_pipeline_with_filter(self):
        """Valid pipeline with filter."""
        source = """
        func main() -> List[Int]
            return [1, 2, 3, 4] |> filter(_, fn(x: Int) -> x > 2)
        end func
        """
        check_program(source)

    def test_chained_pipeline(self):
        """Valid chained pipeline."""
        source = """
        func main() -> Int
            return [1, 2, 3]
                |> filter(_, fn(x: Int) -> x > 1)
                |> length
        end func
        """
        check_program(source)

    def test_qualified_pipeline_stage(self):
        """Qualified module functions can be used as pipeline stages."""
        source = """
        import Math

        func main() -> Int
            return -3 |> Math.abs
        end func
        """
        math_source = """
        func abs(x: Int) -> Int
            example -3 -> 3
            if x < 0 then
                return 0 - x
            end if
            return x
        end func
        """
        checker = TypeChecker()
        checker.check_program(parse(source), modules={"Math": parse(math_source)})

    def test_multiple_placeholders_must_match_each_parameter(self):
        """Each placeholder must satisfy the type of its parameter slot."""
        source = """
        func consume(x: Int, y: String) -> Int
            example 1, "a,b" -> 2
            return length(split(y, ","))
        end func

        func main() -> Int
            return 1 |> consume(_, _)
        end func
        """
        error = expect_type_error(source)
        assert "pipeline type mismatch" in error.message.lower()


class TestTypecheckerConstructors:
    """Constructor type checking tests."""

    def test_some_constructor(self):
        """Valid Some constructor."""
        source = """
        func main() -> Option[Int]
            return Some(42)
        end func
        """
        check_program(source)

    def test_none_constructor(self):
        """Valid None constructor."""
        source = """
        func main() -> Option[Int]
            return None
        end func
        """
        check_program(source)

    def test_unknown_constructor(self):
        """Unknown constructor produces error."""
        source = """
        func main() -> Int
            return Unknown(42)
        end func
        """
        error = expect_type_error(source)
        assert "unknown" in error.message.lower()

    def test_constructor_argument_type_mismatch_rejected(self):
        """Constructor arguments must match declared field types."""
        source = """
        type Point = Point(x: Int)

        func main() -> Point
            return Point("x")
        end func
        """
        error = expect_type_error(source)
        assert "expected Int" in error.message
        assert "String" in error.message

    def test_nested_generic_constructor_mismatch_rejected(self):
        """Nested generic constructor fields should validate their element types."""
        source = """
        type Box[T] = Box(items: List[T])

        func main() -> Box[Int]
            let box: Box[Int] = Box(["x"])
            return box
        end func
        """
        error = expect_type_error(source)
        assert "Box[Int]" in error.message
        assert "Box[String]" in error.message

    def test_nested_generic_constructor_return_mismatch_rejected(self):
        """Return annotations should reject ill-typed generic constructors."""
        source = """
        type Box[T] = Box(items: List[T])

        func main() -> Box[Int]
            return Box(["x"])
        end func
        """
        error = expect_type_error(source)
        assert "Box[Int]" in error.message
        assert "Box[String]" in error.message

    def test_nested_generic_constructor_infers_success_case(self):
        """Well-typed nested generic constructors should still infer correctly."""
        source = """
        type Box[T] = Box(items: List[T])

        func main() -> Box[Int]
            let box: Box[Int] = Box([1, 2, 3])
            return box
        end func
        """
        check_program(source)

    def test_constructor_rejects_incompatible_generic_function_value(self):
        """Concrete function fields must reject incompatible generic functions."""
        source = """
        type Handler = Handler(run: (Option[Int]) -> String)

        func main() -> Handler
            return Handler(unwrap)
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()

    def test_constructor_allows_monomorphic_instantiation_of_generic_function_value(
        self,
    ):
        """Concrete function fields may instantiate compatible generic functions."""
        source = """
        type Renderer = Renderer(run: (Int) -> String)

        func main() -> Renderer
            return Renderer(to_string)
        end func
        """
        check_program(source)


class TestTypecheckerUserTypes:
    """User-defined type checking tests."""

    def test_custom_type(self):
        """Valid custom type definition."""
        source = """
        type Point = MkPoint(x: Int, y: Int)

        func main() -> Int
            let p: Point = MkPoint(1, 2)
            return 0
        end func
        """
        check_program(source)

    def test_duplicate_constructors_across_types_are_rejected(self):
        """Constructor names should stay unique across type definitions."""
        source = """
        type A = Same | OnlyA
        type B = Same | OnlyB

        func main() -> Int
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "Constructor 'Same'" in str(error)
        assert "already defined" in str(error)

    def test_unknown_user_type_in_function_signature_rejected(self):
        """Unknown types in function signatures should fail during resolution."""
        source = """
        @untested("test")
        func id(x: Missing) -> Missing
            return x
        end func
        """
        error = expect_type_error(source)
        assert "Unknown type: 'Missing'" in str(error)

    def test_unknown_user_type_in_field_declaration_rejected(self):
        """Unknown field annotation types should be rejected."""
        source = """
        type Wrapper = Wrapper(value: Missing)

        func main() -> Int
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "Unknown type: 'Missing'" in str(error)

    def test_wrong_user_type_arity_in_alias_rejected(self):
        """User-defined types must be used with the declared number of type args."""
        source = """
        type Box[T] = Box(value: T)
        type Alias = Box[Int, String]

        func main() -> Int
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "Type 'Box' expects 1 type parameter(s) but got 2" in str(error)

    def test_mutually_recursive_user_types_still_typecheck(self):
        """Rejecting unknown types must not break recursive type declarations."""
        source = """
        type Left = Left(right: Right)
        type Right = Right(left: Left)

        func main() -> Int
            return 0
        end func
        """
        check_program(source)

    def test_unknown_user_type_in_trait_signature_rejected(self):
        """Trait signatures should reject unknown annotation types too."""
        source = """
        trait Bad
            func f(x: Missing) -> Int
        end trait
        """
        error = expect_type_error(source)
        assert "Unknown type: 'Missing'" in str(error)

    def test_wrong_user_type_arity_in_trait_signature_rejected(self):
        """Trait signatures should enforce declared generic arity."""
        source = """
        type Box[T] = Box(value: T)

        trait Bad
            func f(x: Box[Int, String]) -> Int
        end trait
        """
        error = expect_type_error(source)
        assert "Type 'Box' expects 1 type parameter(s) but got 2" in str(error)

    def test_self_outside_trait_signature_rejected(self):
        """`Self` should only be valid in trait method signatures."""
        source = """
        @untested("test")
        func id(x: Self) -> Self
            return x
        end func
        """
        error = expect_type_error(source)
        assert "Type 'Self' is only valid in trait method signatures" in str(error)


class TestTraitSelfSubstitution:
    """Trait impl signatures should substitute `Self` structurally."""

    def test_impl_accepts_nested_self_in_params_and_returns(self):
        source = """
        trait Collectable
            func collect(self: Self, items: List[Self]) -> List[Self]
        end trait

        type Point = Point(x: Int)

        impl Collectable for Point
            func collect(self: Point, items: List[Point]) -> List[Point]
                example (Point(1), [Point(2)]) -> [Point(2)]
                return items
            end func
        end impl

        func main() -> Int
            example () -> 2
            let p: Point = Point(1)
            let ps: List[Point] = collect(p, [Point(2)])
            return ps[0].x
        end func
        """
        check_program(source)

    def test_impl_rejects_wrong_nested_self_return_type(self):
        source = """
        trait CloneList
            func clones(self: Self) -> List[Self]
        end trait

        type Point = Point(x: Int)

        impl CloneList for Point
            func clones(self: Point) -> List[Int]
                example Point(1) -> [1]
                return [self.x]
            end func
        end impl
        """
        error = expect_type_error(source)
        assert "return type mismatch" in str(error)
        assert "expected List[Point], got List[Int]" in str(error)


class TestUserTypeVariance:
    """Variance soundness for user-defined generic types (#616)."""

    def test_mutable_array_field_rejects_covariant_assign(self):
        source = """
        type Box[T] = Box(items: Array[T])

        func main() -> Int
            let ib: Box[Int] = Box(array_new(2, 0))
            let fb: Box[Float] = ib
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "Box" in str(error)

    def test_immutable_list_field_allows_covariant_assign(self):
        source = """
        type Wrapper[T] = Wrapper(items: List[T])

        func main() -> Int
            let iw: Wrapper[Int] = Wrapper([1, 2])
            let fw: Wrapper[Float] = iw
            return 0
        end func
        """
        check_program(source)

    def test_vec_field_makes_param_invariant(self):
        source = """
        type Container[T] = Container(data: Vec[T])

        func main() -> Int
            let ints: Vec[Int] = vec_new()
            let ic: Container[Int] = Container(ints)
            let fc: Container[Float] = ic
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "Container" in str(error)

    def test_set_field_makes_param_invariant(self):
        source = """
        type Holder[T] = Holder(vals: Set[T])

        func main() -> Int
            let ints: Set[Int] = set_new()
            let ih: Holder[Int] = Holder(ints)
            let fh: Holder[Float] = ih
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "Holder" in str(error)

    def test_nested_mutable_in_list_makes_param_invariant(self):
        source = """
        type Nested[T] = Nested(rows: List[Array[T]])

        func main() -> Int
            let ni: Nested[Int] = Nested([])
            let nf: Nested[Float] = ni
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "Nested" in str(error)

    def test_nested_covariant_usertype_stays_covariant(self):
        source = """
        type Box[T] = Box(items: List[T])
        type Wrap[T] = Wrap(box: Box[T])

        func main() -> Int
            let wi: Wrap[Int] = Wrap(Box([1, 2]))
            let wf: Wrap[Float] = wi
            return 0
        end func
        """
        check_program(source)

    def test_recursive_covariant_usertype_stays_covariant(self):
        source = """
        type Node[T] = Node(next: Option[Node[T]], value: T)

        func main() -> Int
            let ni: Node[Int] = Node(None, 1)
            let nf: Node[Float] = ni
            return 0
        end func
        """
        check_program(source)

    def test_async_field_infers_constructor_type_vars(self):
        source = """
        type TaskBox[T] = TaskBox(job: Async[Array[T]])

        async func build() -> Array[Int]
            return array_new(1, 0)
        end func

        @untested("review")
        func main() -> Int
            let ti: TaskBox[Int] = TaskBox(build())
            return 0
        end func
        """
        check_program(source)

    def test_async_wrapper_propagates_nested_invariance(self):
        source = """
        type TaskBox[T] = TaskBox(job: Async[Array[T]])

        async func build() -> Array[Int]
            return array_new(1, 0)
        end func

        func main() -> Int
            let ti: TaskBox[Int] = TaskBox(build())
            let tf: TaskBox[Float] = ti
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "declared type TaskBox[Float]" in str(error)
        assert "value has type TaskBox[Int]" in str(error)

    def test_no_type_params_no_issue(self):
        source = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            let p: Point = Point(1, 2)
            return 0
        end func
        """
        check_program(source)


class TestTypecheckerNamedArgs:
    """Named argument type checking tests."""

    def test_valid_named_args(self):
        """Valid named arguments."""
        source = """
        func greet(name: String, greeting: String) -> String
            example "Alice", "Hi" -> "Hi"
            return greeting
        end func

        func main() -> String
            return greet(greeting: "Hello", name: "World")
        end func
        """
        check_program(source)

    def test_unknown_param_name(self):
        """Unknown parameter name produces error."""
        source = """
        func add(a: Int, b: Int) -> Int
            example 1, 2 -> 3
            return a + b
        end func

        func main() -> Int
            return add(x: 1, y: 2)
        end func
        """
        error = expect_type_error(source)
        assert (
            "unknown" in error.message.lower() or "parameter" in error.message.lower()
        )


class TestTypecheckerNamedArgsRequired:
    """Tests for requiring named arguments with 3+ parameters."""

    def test_three_params_positional_fails(self):
        """Positional args with 3+ params produces error."""
        source = """
        func make_point(x: Int, y: Int, z: Int) -> Int
            example 1, 2, 3 -> 6
            return x + y + z
        end func

        func main() -> Int
            return make_point(1, 2, 3)
        end func
        """
        error = expect_type_error(source)
        assert "named arguments" in error.message.lower()

    def test_three_params_named_passes(self):
        """Named args with 3+ params passes."""
        source = """
        func make_point(x: Int, y: Int, z: Int) -> Int
            example 1, 2, 3 -> 6
            return x + y + z
        end func

        func main() -> Int
            return make_point(x: 1, y: 2, z: 3)
        end func
        """
        check_program(source)  # Should not raise

    def test_two_params_positional_passes(self):
        """Positional args with 2 params is allowed."""
        source = """
        func add(a: Int, b: Int) -> Int
            example 1, 2 -> 3
            return a + b
        end func

        func main() -> Int
            return add(1, 2)
        end func
        """
        check_program(source)  # Should not raise

    def test_builtin_three_params_named(self):
        """Built-in with 3+ params requires named args."""
        source = """
        func main() -> Int
            return fold(list: [1, 2, 3], initial: 0, reducer: fn(acc: Int, x: Int) -> acc + x)
        end func
        """
        check_program(source)  # Should not raise

    def test_builtin_three_params_positional_fails(self):
        """Built-in with 3+ params without named args fails."""
        source = """
        func main() -> Int
            return fold([1, 2, 3], 0, fn(acc: Int, x: Int) -> acc + x)
        end func
        """
        error = expect_type_error(source)
        assert "named arguments" in error.message.lower()


class TestTypecheckerBuiltins:
    """Built-in function type checking tests."""

    def test_length(self):
        """Type check length function."""
        source = """
        func main() -> Int
            return length([1, 2, 3])
        end func
        """
        check_program(source)

    def test_length_accepts_builtin_named_arg(self):
        """length accepts its registered builtin parameter name."""
        source = """
        func main() -> Int
            return length(list: [1, 2, 3])
        end func
        """
        check_program(source)

    def test_length_rejects_unknown_named_arg(self):
        """length should reject unknown named arguments before runtime."""
        source = """
        func main() -> Int
            return length(foo: [1, 2, 3])
        end func
        """
        error = expect_type_error(source)
        assert "Unknown parameter name: foo" in error.message

    def test_range_accepts_builtin_named_args(self):
        """range accepts its registered builtin parameter names."""
        source = """
        func main() -> Int
            let xs: List[Int] = range(start: 1, 4)
            return length(xs)
        end func
        """
        check_program(source)

    def test_range_accepts_third_positional_arg(self):
        """range still accepts positional step as its third argument."""
        source = """
        func main() -> Int
            let xs: List[Int] = range(1, 10, 2)
            return length(xs)
        end func
        """
        check_program(source)

    def test_range_rejects_unknown_named_args(self):
        """range should reject unknown named arguments before runtime."""
        source = """
        func main() -> Int
            let xs: List[Int] = range(foo: 1, bar: 3)
            return length(xs)
        end func
        """
        error = expect_type_error(source)
        assert "Unknown parameter name: foo" in error.message

    def test_range_rejects_mixed_unknown_named_arg(self):
        """range should reject unknown names even with positional args."""
        source = """
        func main() -> Int
            let xs: List[Int] = range(1, nope: 5)
            return length(xs)
        end func
        """
        error = expect_type_error(source)
        assert "Unknown parameter name: nope" in error.message

    def test_range_rejects_named_step_arg(self):
        """range step remains positional-only in the source builtin table."""
        source = """
        func main() -> Int
            let xs: List[Int] = range(start: 1, 10, step: 2)
            return length(xs)
        end func
        """
        error = expect_type_error(source)
        assert "Unknown parameter name: step" in error.message

    def test_head(self):
        """Type check head function."""
        source = """
        func main() -> Int
            return head([1, 2, 3])
        end func
        """
        check_program(source)

    def test_map_function(self):
        """Type check map function."""
        source = """
        func main() -> List[Int]
            return map([1, 2, 3], fn(x: Int) -> x * 2)
        end func
        """
        check_program(source)

    def test_filter(self):
        """Type check filter function."""
        source = """
        func main() -> List[Int]
            return filter([1, 2, 3], fn(x: Int) -> x > 1)
        end func
        """
        check_program(source)

    def test_fold(self):
        """Type check fold function with named args (3+ params require named args)."""
        source = """
        func main() -> Int
            return fold(list: [1, 2, 3], initial: 0, reducer: fn(acc: Int, x: Int) -> acc + x)
        end func
        """
        check_program(source)

    def test_list_group_by_return_type(self):
        """list_group_by returns List[(K, List[T])], not List[List[T]]."""
        source = """
        func classify(x: Int) -> Int
            example 1 -> 0
            return x
        end func

        func main() -> List[(Int, List[Int])]
            return list_group_by([1, 2, 3], classify)
        end func
        """
        check_program(source)

    def test_list_group_by_wrong_return_type_rejected(self):
        """list_group_by with old List[List[T]] return type should fail."""
        source = """
        func classify(x: Int) -> Int
            example 1 -> 0
            return x
        end func

        func main() -> List[List[Int]]
            return list_group_by([1, 2, 3], classify)
        end func
        """
        expect_type_error(source)


class TestTypecheckerTuples:
    """Tuple type checking tests."""

    def test_unit_type(self):
        """Unit type (empty tuple)."""
        source = """
        func main() -> Unit
            return ()
        end func
        """
        check_program(source)


class TestTypecheckerPatternExhaustiveness:
    """Recursive exhaustiveness checks for match patterns."""

    def test_nested_option_result_match_is_non_exhaustive(self):
        source = """
        func unwrap(opt: Option[Result[Int, String]]) -> Int
            example Some(Ok(5)) -> 5
            example Some(Err("oops")) -> -1
            example None -> 0
            match opt with
                | Some(Ok(x)) -> return x
                | None -> return 0
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive patterns" in error.message.lower()
        assert "Some(Err(<string>))" in error.message

    def test_nested_option_result_match_is_exhaustive(self):
        source = """
        func unwrap(opt: Option[Result[Int, String]]) -> Int
            example Some(Ok(5)) -> 5
            example Some(Err("oops")) -> -1
            example None -> 0
            match opt with
                | Some(Ok(x)) -> return x
                | Some(Err(_)) -> return -1
                | None -> return 0
            end match
        end func
        """
        check_program(source)

    def test_multi_field_constructor_coverage_is_checked(self):
        source = """
        type Pair = Pair(left: Option[Int], right: Option[Int])

        func classify(pair: Pair) -> Int
            example Pair(Some(1), Some(2)) -> 1
            match pair with
                | Pair(Some(_), _) -> return 1
                | Pair(_, Some(_)) -> return 2
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive patterns" in error.message.lower()
        assert "Pair(None, None)" in error.message

    def test_guarded_arm_does_not_count_as_exhaustive(self):
        source = """
        func guarded(opt: Option[Int]) -> Int
            example Some(1) -> 1
            example None -> 0
            match opt with
                | Some(x) when x > 0 -> return x
                | None -> return 0
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive patterns" in error.message.lower()


class TestNestedListExhaustivenessWitness:
    """Nested list-in-ADT witnesses should cite the list structure, not '_'."""

    def test_option_list_empty_only_cites_nonempty(self):
        source = """
        func check(opt: Option[List[Int]]) -> Int
            example None -> 0
            match opt with
                | Some([]) -> return 0
                | None -> return -1
            end match
        end func
        """
        error = expect_type_error(source)
        assert "Some([_, ...])" in error.message

    def test_option_list_nonempty_only_cites_empty(self):
        source = """
        func check(opt: Option[List[Int]]) -> Int
            example None -> 0
            match opt with
                | Some([x, ...rest]) -> return x
                | None -> return -1
            end match
        end func
        """
        error = expect_type_error(source)
        assert "Some([])" in error.message

    def test_option_list_both_covered_passes(self):
        source = """
        func check(opt: Option[List[Int]]) -> Int
            example None -> 0
            match opt with
                | Some([]) -> return 0
                | Some([x, ...rest]) -> return x
                | None -> return -1
            end match
        end func
        """
        check_program(source)

    def test_option_list_singleton_only_is_still_non_exhaustive(self):
        source = """
        func check(opt: Option[List[Int]]) -> Int
            example None -> 0
            match opt with
                | Some([]) -> return 0
                | Some([x]) -> return x
                | None -> return -1
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "Some(" in error.message

    def test_nested_list_coverage_still_checks_later_constructor_fields(self):
        source = """
        type Pair = Pair(xs: List[Int], flag: Bool)

        func check(p: Pair) -> Int
            example Pair([], true) -> 0
            match p with
                | Pair([], true) -> return 0
                | Pair([x, ...rest], false) -> return x
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "Pair(" in error.message


class TestTypedExhaustivenessWitness:
    """Witness messages should include typed placeholders for open fields."""

    def test_nested_int_in_adt_shows_typed_witness(self):
        source = """
        type Wrap = Wrap(val: Int) | Empty

        func get(w: Wrap) -> Int
            example Wrap(1) -> 1
            example Empty -> 0
            match w with
                | Empty -> return 0
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "Wrap(<int>)" in error.message

    def test_tuple_field_in_adt_shows_typed_tuple_witness(self):
        source = """
        type Wrap = Wrap(pair: (Int, String)) | Empty

        func get(w: Wrap) -> Int
            example Wrap((1, "ok")) -> 1
            example Empty -> 0
            match w with
                | Empty -> return 0
            end match
        end func
        """
        error = expect_type_error(source)
        assert "non-exhaustive" in error.message.lower()
        assert "Wrap((<int>, <string>))" in error.message


class TestTypecheckerMapBuiltins:
    """Map builtin typing tests."""

    def test_map_pair_builtins_use_tuple_pairs(self):
        source = """
        func main() -> String
            let m: Map[Int, String] = map_from_list([(1, "one"), (2, "two")])
            let entries: List[(Int, String)] = map_entries(m)
            let rebuilt: Map[Int, String] = map_from_entries(entries)
            match map_get(rebuilt, 2) with
                | Some(text) -> return text
                | None -> return "missing"
            end match
        end func
        """
        check_program(source)

    def test_map_from_list_rejects_wrong_value_type(self):
        source = """
        func main() -> Int
            let m: Map[Int, String] = map_from_list([(1, 2)])
            return 0
        end func
        """
        error = expect_type_error(source)
        assert "mismatch" in error.message.lower()

    def test_map_rejects_unhashable_list_key_type(self):
        source = """
        func main() -> Int
            let m0: Map[List[Int], Int] = map_from_entries([])
            let k: List[Int] = [1]
            let m: Map[List[Int], Int] = map_insert(map: m0, key: k, value: 2)
            match map_get(m, k) with
                | Some(v) -> return v
                | None -> return 0
            end match
        end func
        """
        error = expect_type_error(source)
        assert "Map key type must be hashable, got List[Int]" in error.message

    def test_map_from_entries_rejects_inferred_unhashable_key_type(self):
        source = """
        func main() -> Int
            let m = map_from_entries([([1], 2)])
            return length(map_entries(m))
        end func
        """
        error = expect_type_error(source)
        assert "Map key type must be hashable, got List[Int]" in error.message

    def test_mutable_map_rejects_unhashable_list_key_type(self):
        source = """
        func main() -> Int
            var m: MutableMap[List[Int], Int] = mutable_map_new()
            let k: List[Int] = [1]
            mutable_map_set(map: m, key: k, value: 2)
            match mutable_map_get(m, k) with
                | Some(v) -> return v
                | None -> return 0
            end match
        end func
        """
        error = expect_type_error(source)
        assert "MutableMap key type must be hashable, got List[Int]" in error.message

    def test_set_rejects_unhashable_list_element_type(self):
        source = """
        func main() -> Int
            var s: Set[List[Int]] = set_new()
            set_add(s, [1])
            return set_size(s)
        end func
        """
        error = expect_type_error(source)
        assert "Set element type must be hashable, got List[Int]" in error.message

    def test_set_from_list_rejects_inferred_unhashable_element_type(self):
        source = """
        func main() -> Int
            let s = set_from_list([[1], [2]])
            return set_size(s)
        end func
        """
        error = expect_type_error(source)
        assert "Set element type must be hashable, got List[Int]" in error.message

    def test_set_allows_hashable_option_element_type(self):
        source = """
        func main() -> Int
            var s: Set[Option[Int]] = set_new()
            set_add(s, Some(1))
            set_add(s, None)
            return set_size(s)
        end func
        """
        check_program(source)

    def test_list_pair_builtins_use_tuple_pairs(self):
        source = """
        func main() -> Int
            let zipped: List[(Int, Int)] = list_zip([1, 2], [3, 4])
            let numbered: List[(Int, String)] = list_enumerate(["go"])
            let (left, right): (Int, Int) = zipped[0]
            let (idx, text): (Int, String) = numbered[0]
            return left + right + idx + length(text)
        end func
        """
        check_program(source)

    def test_stdlib_list_module_typechecks(self):
        source = Path("geno/std/List.geno").read_text(encoding="utf-8")
        program = parse(source, filename="geno/std/List.geno")
        type_check(program)


class TestTypecheckerReturnPathAnalysis:
    """Return path analysis tests."""

    def test_missing_return_simple(self):
        """Function with no return statement produces error."""
        source = """
        func bad() -> Int
            example () -> 5
            let x: Int = 5
        end func
        """
        error = expect_type_error(source)
        assert "may not return" in error.message.lower()

    def test_missing_return_in_if_branch(self):
        """Function missing return in one if branch produces error."""
        source = """
        func bad(x: Int) -> Int
            example 1 -> 1
            if x > 0 then
                return 1
            else
                let y: Int = 0
            end if
        end func
        """
        error = expect_type_error(source)
        assert "may not return" in error.message.lower()

    def test_return_in_both_branches(self):
        """Function with return in both branches passes."""
        source = """
        func good(x: Int) -> Int
            example 1 -> 1
            if x > 0 then
                return 1
            else
                return 0
            end if
        end func
        """
        check_program(source)  # Should not raise

    def test_throw_in_else_branch_counts_as_return(self):
        """Throwing in one branch satisfies return-path analysis."""
        source = """
        func good(x: Int) -> Int
            example 1 -> 1
            if x > 0 then
                return x
            else
                throw "negative"
            end if
        end func
        """
        check_program(source)  # Should not raise

    def test_return_in_match(self):
        """Function with return in all match arms passes."""
        source = """
        func unwrap(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func
        """
        check_program(source)  # Should not raise

    def test_missing_return_in_match_arm(self):
        """Function missing return in match arm produces error."""
        source = """
        func bad(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | None -> let y: Int = 0
            end match
        end func
        """
        error = expect_type_error(source)
        assert "may not return" in error.message.lower()

    def test_unit_function_no_return_ok(self):
        """Unit function without explicit return is ok."""
        source = """
        func do_nothing() -> Unit
            example () -> ()
            let x: Int = 5
        end func
        """
        check_program(source)  # Should not raise


class TestTypecheckerSpecTypechecking:
    """Specification clause type checking tests."""

    def test_requires_must_be_bool(self):
        """Requires clause must be Bool."""
        source = """
        func bad(x: Int) -> Int
            requires x
            example 5 -> 5
            return x
        end func
        """
        error = expect_type_error(source)
        assert "requires" in error.message.lower()
        assert "bool" in error.message.lower()

    def test_ensures_must_be_bool(self):
        """Ensures clause must be Bool."""
        source = """
        func bad(x: Int) -> Int
            ensures result
            example 5 -> 5
            return x
        end func
        """
        error = expect_type_error(source)
        assert "ensures" in error.message.lower()
        assert "bool" in error.message.lower()

    def test_valid_requires(self):
        """Valid requires clause passes."""
        source = """
        func good(x: Int) -> Int
            requires x > 0
            example 5 -> 5
            return x
        end func
        """
        check_program(source)  # Should not raise

    def test_valid_ensures_with_result(self):
        """Valid ensures clause with result passes."""
        source = """
        func good(x: Int) -> Int
            ensures result > 0
            example 5 -> 6
            return x + 1
        end func
        """
        check_program(source)  # Should not raise

    @pytest.mark.parametrize(
        ("source", "binding_kind"),
        [
            (
                """
        func bad(result: Int) -> Int
            ensures result > 0
            example 5 -> 6
            return result + 1
        end func
        """,
                "parameter",
            ),
            (
                """
        func bad(x: Int) -> Int
            ensures result > 0
            example 5 -> 6
            let result: Int = x + 1
            return result
        end func
        """,
                "binding",
            ),
            (
                """
        func bad(values: List[Int]) -> Int
            ensures result > 0
            example [1] -> 1
            match values with
                | [result, ...rest] -> return result
                | [] -> return 0
            end match
        end func
        """,
                "pattern binding",
            ),
        ],
    )
    def test_ensures_reserves_result_for_return_value(
        self, source: str, binding_kind: str
    ):
        error = expect_type_error(source)
        assert "`result` is reserved" in error.message
        assert binding_kind in error.message

    def test_print_builtin_typechecks(self):
        """Print builtin is properly typechecked."""
        source = """
        func main() -> Unit
            print(42)
        end func
        """
        check_program(source)  # Should not raise


class TestReviewFixes:
    """Tests for codebase review fixes."""

    def test_for_loop_over_array_accepted(self):
        """For loop over Array[T] should type check successfully."""
        source = """
        func main() -> Int
            let arr: Array[Int] = array_from_list([1, 2, 3])
            var total: Int = 0
            for x: Int in arr do
                total = total + x
            end for
            return total
        end func
        """
        check_program(source)  # Should not raise

    def test_duplicate_param_names_rejected(self):
        """Duplicate parameter names should be rejected."""
        source = """
        func bad(x: Int, x: Int) -> Int
            example 1, 2 -> 3
            return x + x
        end func
        """
        err = expect_type_error(source)
        assert "Duplicate parameter name" in str(err)

    def test_length_user_defined_not_special_cased(self):
        """User-defined 'length' function should not get builtin special-casing."""
        source = """
        func length(x: Int) -> Int
            example 5 -> 5
            return x
        end func

        func main() -> Int
            return length(42)
        end func
        """
        check_program(source)  # Should not raise


class TestFieldAccessSoundness:
    """Regression tests for field access on multi-variant sum types."""

    def test_single_variant_field_access_allowed(self):
        """Field access on a single-variant type should work."""
        source = """
        type Point = Point(x: Int, y: Int)

        func get_x(p: Point) -> Int
            example Point(3, 4) -> 3
            return p.x
        end func
        """
        check_program(source)  # Should not raise

    def test_multi_variant_shared_field_allowed(self):
        """Field access is OK if the field exists on ALL variants."""
        source = """
        type Shape = Circle(name: String, radius: Int) | Square(name: String, side: Int)

        func get_name(s: Shape) -> String
            example Circle("c", 1) -> "c"
            return s.name
        end func
        """
        check_program(source)  # Should not raise

    def test_multi_variant_missing_field_rejected(self):
        """Field access must be rejected if the field is missing from any variant."""
        source = """
        type Shape = Circle(radius: Int) | Square(side: Int)

        func get_radius(s: Shape) -> Int
            example Circle(5) -> 5
            return s.radius
        end func
        """
        err = expect_type_error(source)
        assert "does not exist on all variants" in str(err)
        assert "Square" in str(err)

    def test_multi_variant_no_fields_variant_rejected(self):
        """Field access rejected when one variant has no fields at all."""
        source = """
        type MaybeInt = HasValue(value: Int) | Empty

        func get_val(m: MaybeInt) -> Int
            example HasValue(5) -> 5
            return m.value
        end func
        """
        err = expect_type_error(source)
        assert "does not exist on all variants" in str(err)
        assert "Empty" in str(err)

    def test_multi_variant_shared_field_type_mismatch_rejected(self):
        """Shared fields must resolve to the same type on every variant."""
        source = """
        type Shape = Circle(name: String) | Square(name: Int)

        func get_name(s: Shape) -> String
            example Circle("c") -> "c"
            return s.name
        end func
        """
        err = expect_type_error(source)
        assert "inconsistent types" in str(err)
        assert "Circle: String" in str(err)
        assert "Square: Int" in str(err)

    def test_single_variant_generic_field_access_uses_concrete_type_args(self):
        """Generic field access should substitute concrete type arguments."""
        source = """
        type Box[T] = Box(value: T)

        func wrong(box: Box[Int]) -> String
            example Box(1) -> "1"
            return box.value
        end func
        """
        err = expect_type_error(source)
        assert "String" in str(err)
        assert "Int" in str(err)

    def test_single_variant_missing_field_reports_direct_error(self):
        """Single-variant types should not suggest pattern matching."""
        source = """
        type Point = Point(x: Int)

        func get_y(p: Point) -> Int
            example Point(1) -> 1
            return p.y
        end func
        """
        err = expect_type_error(source)
        assert "Unknown field 'y' on type Point" in str(err)
        assert "pattern matching" not in str(err)

    def test_field_assignment_on_non_record_target_rejected(self):
        """Field assignment should fail on non-user-defined targets."""
        source = """
        func main() -> Int
            example () -> 0
            var x: Int = 0
            x.y = 1
            return x
        end func
        """
        err = expect_type_error(source)
        assert "Cannot assign field 'y' on type Int" in str(err)


# ---------------------------------------------------------------------------
# Index assignment type errors (lines 1911-1943)
# ---------------------------------------------------------------------------


class TestIndexAssignmentErrors:
    def test_index_assignment_requires_mutable_binding(self):
        source = """
        func main() -> Int
            let arr: Array[Int] = array_new(1, 0)
            arr[0] = 1
            return array_get(arr, 0)
        end func
        """
        err = expect_type_error(source)
        assert "immutable variable: arr" in str(err)

    def test_nested_index_assignment_requires_mutable_root(self):
        source = """
        type Box = Box(items: Array[Int])

        func main() -> Int
            let box: Box = Box(array_new(1, 0))
            box.items[0] = 1
            return array_get(box.items, 0)
        end func
        """
        err = expect_type_error(source)
        assert "immutable variable: box" in str(err)

    def test_index_assignment_allows_mutable_binding(self):
        source = """
        func main() -> Int
            var arr: Array[Int] = array_new(1, 0)
            arr[0] = 1
            return array_get(arr, 0)
        end func
        """
        check_program(source)

    def test_vec_non_int_index(self):
        source = """
        func main() -> Int
            example () -> 0
            var v: Vec[Int] = vec_new()
            v["bad"] = 1
            return 0
        end func
        """
        err = expect_type_error(source)
        assert "Int" in str(err) or "index" in str(err).lower()

    def test_vec_wrong_value_type(self):
        source = """
        func main() -> Int
            example () -> 0
            var v: Vec[Int] = vec_new()
            vec_push(v, 1)
            v[0] = "string"
            return 0
        end func
        """
        err = expect_type_error(source)
        assert "mismatch" in str(err).lower()

    def test_index_assign_on_non_indexable(self):
        source = """
        func main() -> Int
            example () -> 0
            var x: Int = 5
            x[0] = 1
            return 0
        end func
        """
        err = expect_type_error(source)
        assert "Cannot use index assignment" in str(err) or "index" in str(err).lower()


# ---------------------------------------------------------------------------
# Field assignment type errors (lines 1952-1975)
# ---------------------------------------------------------------------------


class TestFieldAssignmentErrors:
    def test_field_assignment_requires_mutable_binding(self):
        source = """
        type Box = Box(value: Int)
        func main() -> Int
            let b = Box(1)
            b.value = 2
            return b.value
        end func
        """
        err = expect_type_error(source)
        assert "immutable variable: b" in str(err)

    def test_field_assignment_allows_mutable_binding(self):
        source = """
        type Box = Box(value: Int)
        func main() -> Int
            var b = Box(1)
            b.value = 2
            return b.value
        end func
        """
        check_program(source)

    def test_field_type_mismatch(self):
        source = """
        type Point = Point(x: Int, y: Int)
        func main() -> Int
            example () -> 0
            var p: Point = Point(1, 2)
            p.x = "wrong"
            return 0
        end func
        """
        err = expect_type_error(source)
        assert "mismatch" in str(err).lower()

    def test_no_such_field(self):
        source = """
        type Point = Point(x: Int, y: Int)
        func main() -> Int
            example () -> 0
            var p: Point = Point(1, 2)
            p.z = 5
            return 0
        end func
        """
        err = expect_type_error(source)
        assert "No field 'z' on type Point" in str(err)
        assert "pattern matching" not in str(err)


# ---------------------------------------------------------------------------
# List pattern errors (lines 3180-3202)
# ---------------------------------------------------------------------------


class TestListPatternErrors:
    def test_list_pattern_on_non_list(self):
        source = """
        func main() -> Int
            example () -> 0
            let x: Int = 5
            match x with
                | [a, b] -> return a
                | _ -> return 0
            end match
        end func
        """
        err = expect_type_error(source)
        assert "list" in str(err).lower() or "pattern" in str(err).lower()


# ---------------------------------------------------------------------------
# Result exhaustiveness (lines 3063-3068)
# ---------------------------------------------------------------------------


class TestResultExhaustiveness:
    def test_missing_err_arm(self):
        source = """
        func check(r: Result[Int, String]) -> Int
            example Ok(1) -> 1
            match r with
                | Ok(v) -> return v
            end match
        end func
        """
        err = expect_type_error(source)
        assert "Err" in str(err) or "exhaustive" in str(err).lower()

    def test_missing_ok_arm(self):
        source = """
        func check(r: Result[Int, String]) -> Int
            example Ok(1) -> 1
            match r with
                | Err(e) -> return 0
            end match
        end func
        """
        err = expect_type_error(source)
        assert "Ok" in str(err) or "exhaustive" in str(err).lower()


# ---------------------------------------------------------------------------
# Duplicate named argument (lines 2550-2553)
# ---------------------------------------------------------------------------


class TestDuplicateNamedArg:
    def test_duplicate_named_argument(self):
        source = """
        func add(a: Int, b: Int, c: Int) -> Int
            example (1, 2, 3) -> 6
            return a + b + c
        end func
        func main() -> Int
            example () -> 0
            return add(a: 1, b: 2, a: 3)
        end func
        """
        err = expect_type_error(source)
        assert "Duplicate" in str(err) or "duplicate" in str(err)


class TestUserTypeInvariance:
    """Invariant user-type parameters must stay exact in compatibility checks."""

    def test_usertype_invariant_rejects_covariant_promotion(self):
        """Box[Int] must not be assignable to Box[Float]."""
        source = """
        type Box[T] = Box(items: Array[T])

        func take_box(b: Box[Float]) -> Float
            example (Box(array_new(1, 1.0))) -> 1.0
            match b with
                | Box(items) -> return items[0]
            end match
        end func

        func main() -> Float
            let b: Box[Int] = Box(array_new(1, 42))
            return take_box(b)
        end func
        """
        error = expect_type_error(source)
        err_str = str(error).lower()
        assert "box" in err_str
        assert "mismatch" in err_str

    def test_usertype_same_param_still_works(self):
        """Box[Int] assignable to Box[Int] — invariance allows exact match."""
        source = """
        type Box[T] = Box(items: Array[T])

        func take_box(b: Box[Int]) -> Int
            example (Box(array_new(1, 1))) -> 1
            match b with
                | Box(items) -> return items[0]
            end match
        end func

        func main() -> Int
            let b: Box[Int] = Box(array_new(1, 42))
            return take_box(b)
        end func
        """
        check_program(source)

    def test_usertype_invariant_rejects_generic_builtin_inference(self):
        """Generic builtin inference must not reintroduce UserType covariance."""
        source = """
        type Box[T] = Box(items: Array[T])

        func main() -> List[Box[Float]]
            let xs: List[Box[Float]] = concat([Box(array_new(1, 1.0))], [Box(array_new(1, 2))])
            return xs
        end func
        """
        error = expect_type_error(source)
        err_str = str(error).lower()
        assert "box" in err_str
        assert "mismatch" in err_str

    def test_set_invariant_rejects_generic_builtin_inference(self):
        """Substitution-based checks must preserve Set[T] invariance too."""
        source = """
        func main() -> Set[Float]
            let a: Set[Float] = set_from_list([1.0])
            let b: Set[Int] = set_from_list([2])
            let c: Set[Float] = set_union(a, b)
            return c
        end func
        """
        error = expect_type_error(source)
        err_str = str(error).lower()
        assert "set" in err_str
        assert "mismatch" in err_str


class TestNeverType:
    """throw expressions return NeverType (bottom), assignable to any type."""

    def test_throw_in_if_branch_compatible_with_return_type(self):
        """throw can appear in an if-branch where a value is expected."""
        source = """
        func safe_div(a: Int, b: Int) -> Int
            example (10, 2) -> 5
            if b == 0 then
                throw "division by zero"
            end if
            return a / b
        end func

        func main() -> Int
            return safe_div(10, 2)
        end func
        """
        check_program(source)

    def test_throw_in_match_arm(self):
        """throw can appear in a match arm alongside typed return values."""
        source = """
        func unwrap_or_throw(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | None -> throw "was None"
            end match
        end func

        func main() -> Int
            return unwrap_or_throw(Some(42))
        end func
        """
        check_program(source)

    def test_throw_in_first_match_expression_arm(self):
        """Bottom should not make match-expression result typing order-sensitive."""
        source = """
        func main() -> Int
            return match Some(1) with
                | None -> throw "none"
                | Some(x) -> x
            end match
        end func
        """
        check_program(source)

    def test_throw_argument_does_not_bind_generic_builtin_typevar(self):
        """Bottom arguments should not poison generic call inference."""
        source = """
        func main() -> Int
            return add(throw "boom", 1)
        end func
        """
        check_program(source)

    def test_throw_argument_keeps_generic_builder_compatible_with_context(self):
        """Divergent generic arguments should still fit concrete return contexts."""
        source = """
        func main() -> Array[Int]
            return array_new(size: 2, default: throw "boom")
        end func
        """
        check_program(source)

    def test_throw_first_list_element_keeps_literal_compatible(self):
        """List literal typing should not depend on throw element position."""
        source = """
        func main() -> List[Int]
            return [throw "boom", 1]
        end func
        """
        check_program(source)

    def test_block_lambda_return_join_ignores_never(self):
        """Block lambda return inference should ignore divergent paths."""
        source = """
        func main() -> Int with throw
            let f: (Bool) -> Int with throw = fn(flag: Bool) do
                if flag then
                    return throw "boom"
                end if
                return 1
            end fn
            return f(false)
        end func
        """
        check_program(source)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
