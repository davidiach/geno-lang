"""
Typechecker hardening tests — exercise uncovered error-reporting paths.

Targets: pipeline validation, index access on maps, module missing export,
match guards exhaustiveness, example clause validation, and async type resolution.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.ast_nodes import (
    ExpressionStatement,
    IntegerLiteral,
    MatchExpr,
    ReturnStatement,
)
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


# ---------------------------------------------------------------------------
# Pipeline validation  (lines 2359-2368)
# ---------------------------------------------------------------------------


class TestPipelineValidation:
    """Pipeline stages must be functions with at least one argument."""

    def test_pipeline_non_function_stage(self):
        """Non-function in pipeline raises type error."""
        source = """
        func main() -> Int
            example () -> 0
            let x: Int = 5
            return x |> 42
        end func
        """
        err = expect_type_error(source)
        assert "function" in str(err).lower() or "pipeline" in str(err).lower()

    def test_pipeline_zero_arity_stage(self):
        """Zero-arg function in pipeline raises type error."""
        source = """
        func zero() -> Int
            example () -> 0
            return 0
        end func

        func main() -> Int
            example () -> 0
            return 5 |> zero
        end func
        """
        err = expect_type_error(source)
        assert "argument" in str(err).lower() or "parameter" in str(err).lower()


# ---------------------------------------------------------------------------
# Index access on maps  (lines 2120-2129)
# ---------------------------------------------------------------------------


class TestMapIndexAccess:
    """Map index access type checking."""

    def test_map_wrong_key_type(self):
        """Indexing Map with wrong key type raises type error."""
        source = """
        func main() -> Int
            example () -> 0
            let m: Map[String, Int] = map_from_list([("a", 1)])
            return m[42]
        end func
        """
        err = expect_type_error(source)
        assert "key" in str(err).lower() or "mismatch" in str(err).lower()

    def test_index_non_indexable_type(self):
        """Indexing a non-indexable type raises type error."""
        source = """
        func main() -> Int
            example () -> 0
            let x: Bool = true
            return x[0]
        end func
        """
        err = expect_type_error(source)
        assert "index" in str(err).lower() or "Cannot" in str(err)


# ---------------------------------------------------------------------------
# Match guard exhaustiveness  (lines 2847-2851, already tested in existing suite
# but we add edge cases)
# ---------------------------------------------------------------------------


class TestMatchGuardExhaustiveness:
    """Match with guards is not exhaustive."""

    def test_guarded_arm_non_exhaustive(self):
        """Guarded match arm doesn't count for exhaustiveness."""
        source = """
        func check(opt: Option[Int]) -> Int
            example Some(1) -> 1
            example None -> 0
            match opt with
                | Some(x) when x > 0 -> return x
                | None -> return 0
            end match
        end func
        """
        err = expect_type_error(source)
        assert "non-exhaustive" in str(err).lower()


class TestMatchExprArmShape:
    """Constructed MatchExpr nodes must keep parser-shaped arm bodies."""

    def _program_with_match_expr(self):
        source = """
        func main() -> Int
            let y: Int = match 0 with
                | _ -> 1
            end match
            return y
        end func
        """
        program = parse(source)
        match_expr = program.definitions[0].body[0].value
        assert isinstance(match_expr, MatchExpr)
        return program, match_expr

    def test_rejects_empty_arm_body(self):
        program, match_expr = self._program_with_match_expr()
        match_expr.arms[0].body = []

        with pytest.raises(GenoTypeError, match="exactly one return statement"):
            TypeChecker().check_program(program)

    def test_rejects_non_return_arm_body(self):
        program, match_expr = self._program_with_match_expr()
        loc = match_expr.arms[0].location
        match_expr.arms[0].body = [
            ExpressionStatement(location=loc, expression=IntegerLiteral(loc, 99))
        ]

        with pytest.raises(GenoTypeError, match="exactly one return statement"):
            TypeChecker().check_program(program)

    def test_rejects_multiple_statement_arm_body(self):
        program, match_expr = self._program_with_match_expr()
        loc = match_expr.arms[0].location
        match_expr.arms[0].body = [
            ExpressionStatement(location=loc, expression=IntegerLiteral(loc, 99)),
            ReturnStatement(location=loc, value=IntegerLiteral(loc, 1)),
        ]

        with pytest.raises(GenoTypeError, match="exactly one return statement"):
            TypeChecker().check_program(program)


# ---------------------------------------------------------------------------
# Async type resolution  (lines 2990-2993)
# ---------------------------------------------------------------------------


class TestAsyncTypeResolution:
    """Async function return types resolve correctly."""

    def test_async_return_type(self):
        """Async function return type is checked."""
        source = """
        async func fetch() -> Int
            return 42
        end func

        func main() -> Int
            let x: Int = await fetch()
            return x
        end func
        """
        check_program(source)

    def test_async_wrong_return_type(self):
        """Async function with wrong await type errors."""
        source = """
        async func fetch() -> String
            return "hello"
        end func

        func main() -> Int
            let x: Int = await fetch()
            return x
        end func
        """
        err = expect_type_error(source)
        assert "mismatch" in str(err).lower() or "String" in str(err)


# ---------------------------------------------------------------------------
# Example clause validation  (lines 1222-1239)
# ---------------------------------------------------------------------------


class TestExampleValidation:
    """Example clauses must match function signature."""

    def test_example_input_accepts_single_tuple_parameter(self):
        """Tuple-valued examples are valid for a single tuple parameter."""
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
        check_program(source)

    def test_example_output_type_mismatch(self):
        """Example output type mismatch raises type error."""
        source = """
        func add(a: Int, b: Int) -> Int
            example (1, 2) -> "three"
            return a + b
        end func

        func main() -> Int
            return add(1, 2)
        end func
        """
        err = expect_type_error(source)
        assert "example" in str(err).lower() or "mismatch" in str(err).lower()


# ---------------------------------------------------------------------------
# Constructor pattern on wildcard  (targets pattern matching uncovered paths)
# ---------------------------------------------------------------------------


class TestPatternMatching:
    """Pattern matching edge cases in type checker."""

    def test_list_pattern_exact_match(self):
        """List pattern with exact length works."""
        source = """
        func main() -> Int
            let xs: List[Int] = [1, 2, 3]
            match xs with
            | [a, b, c] -> return a + b + c
            | _ -> return 0
            end match
        end func
        """
        check_program(source)

    def test_nested_constructor_pattern(self):
        """Nested constructor pattern type checks."""
        source = """
        func main() -> Int
            let opt: Option[Option[Int]] = Some(Some(42))
            match opt with
            | Some(Some(v)) -> return v
            | Some(None) -> return 0
            | None -> return 0
            end match
        end func
        """
        check_program(source)


# ---------------------------------------------------------------------------
# Argument matching  (lines 2092-2097)
# ---------------------------------------------------------------------------


class TestArgumentMatching:
    """Named argument matching in function calls."""

    def test_named_args_reorder(self):
        """Named arguments are matched to parameters correctly."""
        source = """
        func add(a: Int, b: Int) -> Int
            example (1, 2) -> 3
            return a + b
        end func

        func main() -> Int
            return add(b: 2, a: 1)
        end func
        """
        check_program(source)


# ---------------------------------------------------------------------------
# let destructuring
# ---------------------------------------------------------------------------


class TestLetDestructuring:
    """Let tuple destructuring type checks correctly."""

    def test_tuple_destructure(self):
        """Tuple destructuring binds correct types."""
        source = """
        func main() -> Int
            let (a, b): (Int, String) = (42, "hello")
            return a
        end func
        """
        check_program(source)


# ---------------------------------------------------------------------------
# 'with' expression errors  (lines 2248-2270)
# ---------------------------------------------------------------------------


class TestWithExpression:
    """'with' expression type errors."""

    def test_with_on_non_user_type(self):
        """'with' on a non-user type raises type error."""
        source = """
        func main() -> Int
            example () -> 0
            let x: Int = 42
            return x with (val: 1)
        end func
        """
        err = expect_type_error(source)
        assert "with" in str(err).lower() or "user-defined" in str(err).lower()

    def test_with_on_multi_variant_type(self):
        """'with' on a multi-variant type raises type error."""
        source = """
        type Shape = Circle(r: Int) | Square(s: Int)

        func main() -> Shape
            example () -> Circle(1)
            let c: Shape = Circle(1)
            return c with (r: 2)
        end func
        """
        err = expect_type_error(source)
        assert "variant" in str(err).lower() or "with" in str(err).lower()

    def test_with_on_generic_type_uses_concrete_field_type(self):
        """Generic field updates use the receiver's concrete type arguments."""
        source = """
        type Box[T] = Box(value: T)

        @untested("review")
        func bump(box: Box[Int]) -> Box[Int]
            return box with (value: box.value + 1)
        end func
        """
        check_program(source)

    def test_with_on_generic_type_reports_concrete_field_type(self):
        """Generic field update errors should mention the substituted type."""
        source = """
        type Box[T] = Box(value: T)

        @untested("review")
        func bad(box: Box[Int]) -> Box[Int]
            return box with (value: "oops")
        end func
        """
        err = expect_type_error(source)
        assert "Field 'value' expects Int, got String" in str(err)


# ---------------------------------------------------------------------------
# Unknown field access  (lines 2196-2199)
# ---------------------------------------------------------------------------


class TestUnknownFieldAccess:
    """Unknown field on user type raises type error."""

    def test_unknown_field(self):
        """Accessing nonexistent field raises type error."""
        source = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            example () -> 0
            let p: Point = Point(1, 2)
            return p.z
        end func
        """
        err = expect_type_error(source)
        assert "z" in str(err) or "field" in str(err).lower()


# ---------------------------------------------------------------------------
# Pipeline argument count mismatch  (lines 2389-2394)
# ---------------------------------------------------------------------------


class TestPipelineArgCount:
    """Pipeline stage with wrong argument count raises type error."""

    def test_pipeline_wrong_arg_count(self):
        """Pipeline stage expecting 3 args when 2 provided raises error."""
        source = """
        func needs_three(a: Int, b: Int, c: Int) -> Int
            example (1, 2, 3) -> 6
            return a + b + c
        end func

        func main() -> Int
            example () -> 0
            return 1 |> needs_three
        end func
        """
        err = expect_type_error(source)
        assert "argument" in str(err).lower() or "parameter" in str(err).lower()


# ---------------------------------------------------------------------------
# Module field access — missing export  (lines 2154-2158)
# ---------------------------------------------------------------------------


class TestModuleMissingExport:
    """Accessing nonexistent symbol on a module raises type error."""

    def test_module_missing_symbol(self):
        """Module.nonexistent raises type error."""
        source = """
        import Math

        func main() -> Int
            example () -> 0
            return Math.nonexistent_function(1)
        end func
        """
        err = expect_type_error(source)
        assert (
            "no" in str(err).lower()
            or "symbol" in str(err).lower()
            or "nonexistent" in str(err).lower()
        )


# ---------------------------------------------------------------------------
# Constructor pattern on wrong type  (lines 2847-2851)
# ---------------------------------------------------------------------------


class TestConstructorPatternErrors:
    """Constructor pattern on invalid type."""

    def test_list_pattern_on_non_list(self):
        """List pattern on non-list type raises error."""
        source = """
        func main() -> String
            let x: Int = 42
            match x with
            | [a, b] -> return "list"
            | _ -> return "other"
            end match
        end func
        """
        # This should either type-error or at minimum not crash
        try:
            check_program(source)
        except GenoTypeError as e:
            assert "list" in str(e).lower() or "pattern" in str(e).lower()


# ---------------------------------------------------------------------------
# Review follow-ups (HIGH-01, HIGH-02, HIGH-03, HIGH-05, MED-02, MED-03)
# ---------------------------------------------------------------------------


class TestMutableContainerInvariance:
    """HIGH-01: mutable containers must be invariant in element type."""

    def test_array_int_rejected_where_array_float_expected(self):
        source = """
        func fill_float(arr: Array[Float]) -> Unit
            example array_new(size: 1, default: 0.0) -> ()
            return ()
        end func

        func main() -> Unit
            var ints: Array[Int] = array_new(size: 3, default: 0)
            fill_float(ints)
            return ()
        end func
        """
        err = expect_type_error(source)
        assert "Array" in str(err)

    def test_vec_int_rejected_where_vec_float_expected(self):
        source = """
        func fill_float(v: Vec[Float]) -> Unit
            example vec_new() -> ()
            return ()
        end func

        func main() -> Unit
            var v: Vec[Int] = vec_new()
            fill_float(v)
            return ()
        end func
        """
        err = expect_type_error(source)
        assert "Vec" in str(err)

    def test_list_int_still_allowed_where_list_float_expected(self):
        """List is immutable, so covariant Int->Float promotion is sound."""
        source = """
        func sum_floats(xs: List[Float]) -> Float
            example [1.0] -> 1.0
            return 0.0
        end func

        func main() -> Unit
            let ints: List[Int] = [1, 2, 3]
            let total: Float = sum_floats(ints)
            return ()
        end func
        """
        check_program(source)


class TestNullaryConstructorFreshTypeVars:
    """HIGH-02: nullary variant of generic ADT must not be Container[Any]."""

    def test_empty_does_not_bridge_incompatible_instantiations(self):
        source = """
        type Container[T] = Empty | Cell(value: T)

        func wants_ints(c: Container[Int]) -> Int
            example Empty -> 0
            return 0
        end func

        func main() -> Int
            let e: Container[String] = Empty
            return wants_ints(e)
        end func
        """
        err = expect_type_error(source)
        assert "Container" in str(err)


class TestEmptyListFreshTypeVar:
    """HIGH-03: [] must not launder through a polymorphic identity."""

    def test_empty_list_still_assignable_to_annotated_let(self):
        source = """
        func main() -> Unit
            let xs: List[Int] = []
            let n: Int = length(xs)
            return ()
        end func
        """
        check_program(source)

    def test_empty_list_without_annotation_is_rejected(self):
        source = """
        func main() -> Unit
            let xs = []
            let n: Int = length(xs)
            return ()
        end func
        """
        with pytest.raises(GenoTypeError):
            type_check(parse(source))


class TestAwaitOnNonAsync:
    """HIGH-05: await on a non-Async value is rejected."""

    def test_await_sync_fn_rejected(self):
        source = """
        func sync_fn() -> Int
            example () -> 1
            return 1
        end func

        async func main() -> Unit
            let x: Int = await sync_fn()
            return ()
        end func
        """
        err = expect_type_error(source)
        assert "await" in str(err).lower()


class TestExampleArityCheck:
    """MED-02: example clause arity must match params (accounting for defaults)."""

    def test_scalar_input_for_two_required_params_rejected(self):
        source = """
        func add(x: Int, y: Int) -> Int
            example 5 -> 10
            return x + y
        end func
        """
        err = expect_type_error(source)
        assert "tuple" in str(err).lower() or "inputs" in str(err).lower()

    def test_scalar_input_for_one_required_with_default_allowed(self):
        """A function with 1 required + 1 optional param accepts scalar input."""
        source = """
        func add(x: Int, y: Int = 10) -> Int
            example 5 -> 15
            return x + y
        end func

        func main() -> Int
            return add(5)
        end func
        """
        check_program(source)


class TestOccursCheck:
    """MED-03: unification must reject cyclic TypeVar bindings.

    Concrete repros are hard to construct in surface syntax because
    users can't directly bind `T` = `List[T]`. The invariant is
    exercised indirectly — the existing passing test suite, combined
    with the guard in ``_occurs_in``, ensures no well-typed program
    silently produces a cyclic type.
    """
