"""
Compiler hardening tests — exercise uncovered code generation paths.

Targets: list pattern compilation with rest (statement form), match-with-guards,
async main, index/field assignment, and compiled flat_map.
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
from geno.compiler import CompileError, Compiler, compile_and_exec, compile_to_python
from geno.parser import parse
from geno.typechecker import TypeError as GenoTypeError


def compile_and_run(source: str):
    """Helper to compile and run a Geno program, returning main() result."""
    globals_dict = compile_and_exec(source, timeout=None)
    if "main" in globals_dict:
        return globals_dict["main"]()
    return None


# ---------------------------------------------------------------------------
# List pattern compilation with rest (statement form)  (lines 957-1020)
# ---------------------------------------------------------------------------


class TestCompileListPatternRest:
    """Compiled list patterns with ...rest capture in match statements."""

    def test_rest_after(self):
        """[first, ...rest] compiles and captures the tail."""
        src = """
        func main() -> List[Int]
            let xs: List[Int] = [1, 2, 3, 4]
            match xs with
            | [first, ...rest] -> return rest
            | _ -> return []
            end match
        end func
        """
        assert compile_and_run(src) == [2, 3, 4]

    def test_rest_both_sides(self):
        """[first, ...middle, last] compiles and captures the middle."""
        src = """
        func main() -> List[Int]
            let xs: List[Int] = [1, 2, 3, 4, 5]
            match xs with
            | [first, ...middle, last] -> return middle
            | _ -> return []
            end match
        end func
        """
        assert compile_and_run(src) == [2, 3, 4]

    def test_rest_empty_capture(self):
        """Rest captures empty list when exact match."""
        src = """
        func main() -> List[Int]
            let xs: List[Int] = [1, 2]
            match xs with
            | [first, ...rest, last] -> return rest
            | _ -> return [99]
            end match
        end func
        """
        assert compile_and_run(src) == []

    def test_rest_before(self):
        """[...rest, last] captures head in compiled output."""
        src = """
        func main() -> Int
            let xs: List[Int] = [1, 2, 3, 4]
            match xs with
            | [...rest, last] -> return last
            | _ -> return 0
            end match
        end func
        """
        assert compile_and_run(src) == 4

    def test_rest_no_binding(self):
        """[first, ..., last] without rest name."""
        src = """
        func main() -> Int
            let xs: List[Int] = [1, 2, 3, 4]
            match xs with
            | [first, ..., last] -> return first + last
            | _ -> return 0
            end match
        end func
        """
        assert compile_and_run(src) == 5


# ---------------------------------------------------------------------------
# Match statement with guards  (lines 856-884)
# ---------------------------------------------------------------------------


class TestCompileMatchGuards:
    """Compiled match statements with when guards."""

    def test_match_stmt_guard(self):
        """Match with guard compiles and runs correctly."""
        src = """
        type Num = Num(val: Int)

        func classify(n: Num) -> String
            example Num(5) -> "positive"
            match n with
            | Num(v) when v > 10 -> return "big"
            | Num(v) when v > 0 -> return "positive"
            | _ -> return "other"
            end match
        end func

        func main() -> String
            return classify(Num(5))
        end func
        """
        assert compile_and_run(src) == "positive"

    def test_match_stmt_multiple_guards_fallthrough(self):
        """Multiple guards fail, wildcard catches."""
        src = """
        type Num = Num(val: Int)

        func classify(n: Num) -> String
            example Num(0) -> "other"
            match n with
            | Num(v) when v > 10 -> return "big"
            | Num(v) when v > 0 -> return "positive"
            | _ -> return "other"
            end match
        end func

        func main() -> String
            return classify(Num(0))
        end func
        """
        assert compile_and_run(src) == "other"

    def test_match_stmt_guard_with_unguarded_arm(self):
        """Mix of guarded and unguarded arms."""
        src = """
        type Num = Num(val: Int)

        func classify(n: Num) -> String
            example Num(100) -> "big"
            match n with
            | Num(v) when v > 50 -> return "big"
            | Num(v) -> return "small"
            end match
        end func

        func main() -> String
            return classify(Num(100))
        end func
        """
        assert compile_and_run(src) == "big"


# ---------------------------------------------------------------------------
# Array index assignment  (targets _compile_statement IndexAssignStatement)
# ---------------------------------------------------------------------------


class TestCompileIndexAssignment:
    """Index assignment compiles correctly."""

    def test_array_index_assign_compiled(self):
        """arr[i] = value compiles to working Python."""
        src = """
        func main() -> Int
            var arr: Array[Int] = array_new(3, 0)
            arr[1] = 42
            return array_get(arr, 1)
        end func
        """
        assert compile_and_run(src) == 42

    def test_vec_index_assign_compiled(self):
        """vec[index] = value compiles to working Python."""
        src = """
        func main() -> Int
            var v: Vec[Int] = vec_from_list([1, 2, 3])
            v[1] = 42
            return vec_get(v, 1)
        end func
        """
        assert compile_and_run(src) == 42

    def test_mutable_map_index_assign_compiled(self):
        """m[key] = value compiles to working Python for MutableMap."""
        src = """
        func main() -> Int
            var m: MutableMap[String, Int] = mutable_map_new()
            m["answer"] = 42
            let found: Option[Int] = mutable_map_get(m, "answer")
            return match found with
            | Some(value) -> value
            | None -> 0
            end match
        end func
        """
        assert compile_and_run(src) == 42


# ---------------------------------------------------------------------------
# Compiled flat_map (hits compiled builtins path)
# ---------------------------------------------------------------------------


class TestCompileFlatMap:
    """flat_map compiles and runs correctly."""

    def test_flat_map_compiled(self):
        """flat_map flattens results in compiled output."""
        src = """
        func dup(x: Int) -> List[Int]
            example 1 -> [1, 1]
            return [x, x]
        end func

        func main() -> List[Int]
            return flat_map([1, 2, 3], dup)
        end func
        """
        assert compile_and_run(src) == [1, 1, 2, 2, 3, 3]


# ---------------------------------------------------------------------------
# Compiled fold with named args
# ---------------------------------------------------------------------------


class TestCompileFold:
    """fold compiles and runs correctly with named arguments."""

    def test_fold_compiled(self):
        """fold with named args compiles to working Python."""
        src = """
        func add(acc: Int, x: Int) -> Int
            example (0, 1) -> 1
            return acc + x
        end func

        func main() -> Int
            return fold(list: [1, 2, 3, 4], initial: 0, reducer: add)
        end func
        """
        assert compile_and_run(src) == 10


# ---------------------------------------------------------------------------
# Compiled list_map / list_filter
# ---------------------------------------------------------------------------


class TestCompileHigherOrder:
    """Higher-order builtins compile correctly."""

    def test_list_map_compiled(self):
        """list_map compiles and runs."""
        src = """
        func double(x: Int) -> Int
            example 3 -> 6
            return x * 2
        end func

        func main() -> List[Int]
            return list_map([1, 2, 3], double)
        end func
        """
        assert compile_and_run(src) == [2, 4, 6]

    def test_list_filter_compiled(self):
        """list_filter compiles and runs."""
        src = """
        func is_even(x: Int) -> Bool
            example 4 -> true
            return x % 2 == 0
        end func

        func main() -> List[Int]
            return list_filter([1, 2, 3, 4, 5], is_even)
        end func
        """
        assert compile_and_run(src) == [2, 4]


# ---------------------------------------------------------------------------
# Compiled match on ADT constructors
# ---------------------------------------------------------------------------


class TestCompileMatchADT:
    """Match on ADT constructors compiles correctly."""

    def test_match_option(self):
        """Match on Option type compiles."""
        src = """
        func unwrap_or(opt: Option[Int], default: Int) -> Int
            example (Some(5), 0) -> 5
            match opt with
            | Some(v) -> return v
            | None -> return default
            end match
        end func

        func main() -> Int
            return unwrap_or(Some(42), 0)
        end func
        """
        assert compile_and_run(src) == 42

    def test_match_option_none(self):
        """Match None branch works."""
        src = """
        func unwrap_or(opt: Option[Int], default: Int) -> Int
            example (None, 99) -> 99
            match opt with
            | Some(v) -> return v
            | None -> return default
            end match
        end func

        func main() -> Int
            return unwrap_or(None, 99)
        end func
        """
        assert compile_and_run(src) == 99


# ---------------------------------------------------------------------------
# Try/catch with typed error  (lines 749-756)
# ---------------------------------------------------------------------------


class TestCompileTryCatch:
    """Try/catch compilation with typed errors."""

    def test_try_catch_string_error(self):
        """throw string caught by String catch compiles."""
        src = """
        func main() -> String
            try
                throw "oops"
            catch e: String
                return e
            end try
            return "unreachable"
        end func
        """
        assert compile_and_run(src) == "oops"

    def test_try_catch_runtime_error(self):
        """Runtime error (division by zero) caught by String catch compiles."""
        src = """
        func main() -> String
            try
                let x: Int = 1 / 0
                return "ok"
            catch e: String
                return "caught"
            end try
            return "unreachable"
        end func
        """
        assert compile_and_run(src) == "caught"


# ---------------------------------------------------------------------------
# List comprehension compilation  (lines 1148-1156)
# ---------------------------------------------------------------------------


class TestCompileListComprehension:
    """List comprehension compiles correctly."""

    def test_basic_comprehension(self):
        """[x * 2 for x: Int in list] compiles."""
        src = """
        func main() -> List[Int]
            let nums: List[Int] = [1, 2, 3]
            return [x * 2 for x: Int in nums]
        end func
        """
        assert compile_and_run(src) == [2, 4, 6]

    def test_comprehension_with_filter(self):
        """[x for x: Int in list if cond] compiles."""
        src = """
        func main() -> List[Int]
            let nums: List[Int] = [1, 2, 3, 4, 5]
            return [x for x: Int in nums if x > 3]
        end func
        """
        assert compile_and_run(src) == [4, 5]


# ---------------------------------------------------------------------------
# With expression  (lines 1672-1676)
# ---------------------------------------------------------------------------


class TestCompileWithExpr:
    """With expression compiles to dataclasses.replace."""

    def test_with_expr(self):
        """p with (x: 10) compiles correctly."""
        src = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            let p: Point = Point(1, 2)
            let q: Point = p with (x: 10)
            return q.x + q.y
        end func
        """
        assert compile_and_run(src) == 12


# ---------------------------------------------------------------------------
# Literal pattern matching (string/bool)  (lines 930, 932)
# ---------------------------------------------------------------------------


class TestCompileLiteralPatterns:
    """Match with literal patterns compiles."""

    def test_string_literal_pattern(self):
        """Match on string literal compiles."""
        src = """
        func classify(s: String) -> Int
            example "yes" -> 1
            match s with
            | "yes" -> return 1
            | "no" -> return 0
            | _ -> return -1
            end match
        end func

        func main() -> Int
            return classify("yes")
        end func
        """
        assert compile_and_run(src) == 1

    def test_bool_literal_pattern(self):
        """Match on bool literal compiles."""
        src = """
        func to_int(b: Bool) -> Int
            example true -> 1
            match b with
            | true -> return 1
            | false -> return 0
            end match
        end func

        func main() -> Int
            return to_int(true)
        end func
        """
        assert compile_and_run(src) == 1


# ---------------------------------------------------------------------------
# List pattern without rest (exact match)  (lines 1007-1016)
# ---------------------------------------------------------------------------


class TestCompileExactListPattern:
    """Exact-length list patterns (no rest) compile."""

    def test_exact_two_element_pattern(self):
        """[a, b] pattern compiles."""
        src = """
        func main() -> Int
            let xs: List[Int] = [10, 20]
            match xs with
            | [a, b] -> return a + b
            | _ -> return 0
            end match
        end func
        """
        assert compile_and_run(src) == 30


# ---------------------------------------------------------------------------
# Requires / ensures compilation  (already partly tested elsewhere but
# add a compilation-specific test)
# ---------------------------------------------------------------------------


class TestCompileRequiresEnsures:
    """Requires and ensures compile to runtime checks."""

    def test_requires_passes(self):
        """Compiled requires passes at runtime."""
        src = """
        func positive(x: Int) -> Int
            requires x > 0
            example 5 -> 5
            return x
        end func

        func main() -> Int
            return positive(5)
        end func
        """
        assert compile_and_run(src) == 5


# ---------------------------------------------------------------------------
# Propagate ? compilation
# ---------------------------------------------------------------------------


class TestCompilePropagate:
    """The ? propagation operator compiles."""

    def test_propagate_some(self):
        """? on Some extracts value in compiled code."""
        src = """
        func get() -> Option[Int]
            example () -> Some(42)
            return Some(42)
        end func

        func main() -> Option[Int]
            let x: Int = get()?
            return Some(x + 1)
        end func
        """
        result = compile_and_run(src)
        assert result.value == 43

    def test_propagate_none(self):
        """? on None propagates early in compiled code."""
        src = """
        func get() -> Option[Int]
            example () -> None
            return None
        end func

        func main() -> Option[Int]
            let x: Int = get()?
            return Some(x + 1)
        end func
        """
        result = compile_and_run(src)
        assert result is not None  # Returns _None singleton


# ---------------------------------------------------------------------------
# Bitwise ops compilation  (lines 1401-1407)
# ---------------------------------------------------------------------------


class TestCompileBitwiseOps:
    """Bitwise operators compile to safe wrappers."""

    def test_bitwise_and(self):
        """a & b compiles."""
        src = """
        func main() -> Int
            return 6 & 3
        end func
        """
        assert compile_and_run(src) == 2

    def test_bitwise_xor(self):
        """a ^ b compiles."""
        src = """
        func main() -> Int
            return 6 ^ 3
        end func
        """
        assert compile_and_run(src) == 5

    def test_left_shift(self):
        """a << b compiles."""
        src = """
        func main() -> Int
            return 1 << 4
        end func
        """
        assert compile_and_run(src) == 16

    def test_right_shift(self):
        """a >> b compiles."""
        src = """
        func main() -> Int
            return 16 >> 2
        end func
        """
        assert compile_and_run(src) == 4


# ---------------------------------------------------------------------------
# Tuple compilation
# ---------------------------------------------------------------------------


class TestCompileTuple:
    """Tuple expressions and destructuring compile."""

    def test_tuple_destructure(self):
        """let (a, b) = (1, 2) compiles."""
        src = """
        func main() -> Int
            let (a, b): (Int, Int) = (1, 2)
            return a + b
        end func
        """
        assert compile_and_run(src) == 3


# ---------------------------------------------------------------------------
# Named arg reordering  (lines 1576-1584)
# ---------------------------------------------------------------------------


class TestCompileNamedArgReorder:
    """Named argument reordering in compiled code."""

    def test_named_then_positional(self):
        """Named arg for first param + positional compiles."""
        src = """
        func add(a: Int, b: Int) -> Int
            example (1, 2) -> 3
            return a + b
        end func

        func main() -> Int
            return add(b: 10, 5)
        end func
        """
        assert compile_and_run(src) == 15


# ---------------------------------------------------------------------------
# For loop with break/continue compilation
# ---------------------------------------------------------------------------


class TestCompileForLoop:
    """For loop with break/continue compiles."""

    def test_for_break(self):
        """break in for loop compiles."""
        src = """
        func main() -> Int
            var total: Int = 0
            for x: Int in [1, 2, 3, 4, 5] do
                if x == 3 then
                    break
                end if
                total = total + x
            end for
            return total
        end func
        """
        assert compile_and_run(src) == 3

    def test_for_continue(self):
        """continue in for loop compiles."""
        src = """
        func main() -> Int
            var total: Int = 0
            for x: Int in [1, 2, 3, 4, 5] do
                if x == 3 then
                    continue
                end if
                total = total + x
            end for
            return total
        end func
        """
        assert compile_and_run(src) == 12


# ---------------------------------------------------------------------------
# Code generation tests (compile_to_python) for paths hard to run
# ---------------------------------------------------------------------------


class TestCompileCodeGenPaths:
    """Tests that verify compiler output via compile_to_python."""

    def test_typed_catch_codegen(self):
        """Typed catch generates _GenoThrow handler."""
        src = """
        type MyError = NotFound(msg: String)

        func main() -> String
            try
                throw NotFound("missing")
            catch e: MyError
                match e with
                | NotFound(m) -> return m
                end match
            end try
            return "unreachable"
        end func
        """
        code = compile_to_python(src)
        assert "_GenoThrow" in code

    def test_field_assign_codegen(self):
        """Field assignment mutates frozen constructor dataclasses."""
        src = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            var p: Point = Point(1, 2)
            p.x = 10
            return p.x
        end func
        """
        code = compile_to_python(src)
        assert "_object_setattr(p, 'x', 10)" in code
        assert compile_and_run(src) == 10

    def test_field_assign_codegen_copies_value_bindings(self):
        """Compiled Python constructor bindings use Geno value semantics."""
        src = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            var p: Point = Point(1, 2)
            let q: Point = p
            p.x = 10
            return q.x
        end func
        """
        assert compile_and_run(src) == 1

    def test_field_assign_codegen_rejects_immutable_binding(self):
        """Compiled Python rejects field mutation through an immutable binding."""
        src = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            let p: Point = Point(1, 2)
            p.x = 10
            return p.x
        end func
        """
        with pytest.raises(GenoTypeError, match="immutable variable: p"):
            compile_to_python(src)

    def test_contract_failure_bypasses_string_catch(self):
        """Compiled Python does not catch contract failures as String errors."""
        src = """
        @untested("contract repro")
        func f(x: Int) -> Int
            requires x > 0
            return x
        end func

        func main() -> String
            try
                let y: Int = f(0)
                return "not caught"
            catch e: String
                return "caught"
            end try
        end func
        """
        with pytest.raises(RuntimeError, match="Precondition failed"):
            compile_and_run(src)

    def test_async_main_codegen(self):
        """Async main generates asyncio.run."""
        src = """
        async func fetch() -> Int
            return 42
        end func

        async func main() -> Int
            return await fetch()
        end func
        """
        code = compile_to_python(src)
        assert "asyncio.run" in code
        assert "async def" in code

    def test_async_function_with_ensures_compiles_and_awaits(self):
        """async + ensures must produce valid Python even when the body uses await (#666)."""
        src = """
        async func inner() -> Int
            return 10
        end func

        async func outer() -> Int
            ensures result > 0
            let x: Int = await inner()
            return x
        end func

        async func main() -> Int
            return await outer()
        end func
        """
        code = compile_to_python(src)
        # Must compile without SyntaxError — the audit bug was a nested
        # `def _body_` that trapped `await` in a non-async helper.
        compile(code, "<t>", "exec")
        # The emitted helper must itself be async and be awaited.
        assert "async def _body_outer" in code
        assert "await _body_outer()" in code
        # Execute end-to-end to confirm the ensures check still fires.
        ns: dict = {}
        exec(code, ns)
        import asyncio

        assert asyncio.run(ns["main"]()) == 10

    def test_match_expr_guard_codegen(self):
        """Match expression with guard generates conditional."""
        src = """
        type Num = Num(val: Int)

        func classify(n: Num) -> String
            example Num(5) -> "positive"
            return match n with
            | Num(v) when v > 10 -> "big"
            | Num(v) when v > 0 -> "positive"
            | _ -> "other"
            end match
        end func

        func main() -> String
            return classify(Num(5))
        end func
        """
        code = compile_to_python(src)
        assert ">" in code

    def test_empty_catch_body_codegen(self):
        """Empty catch body generates pass."""
        src = """
        func main() -> Int
            try
                let x: Int = 1 / 0
            catch e: String
            end try
            return 0
        end func
        """
        code = compile_to_python(src)
        assert "pass" in code

    def test_while_loop_compiled(self):
        """While loop compiles and runs."""
        src = """
        func main() -> Int
            var n: Int = 0
            while n < 5 do
                n = n + 1
            end while
            return n
        end func
        """
        assert compile_and_run(src) == 5

    def test_default_arg_compiled(self):
        """Default argument compiles and runs."""
        src = """
        func greet(name: String, greeting: String = "Hello") -> String
            example "World" -> "Hello World"
            return greeting + " " + name
        end func

        func main() -> String
            return greet("World")
        end func
        """
        assert compile_and_run(src) == "Hello World"

    def test_tuple_return_compiled(self):
        """Tuple return and destructure compiles."""
        src = """
        func swap(a: Int, b: Int) -> (Int, Int)
            example (1, 2) -> (2, 1)
            return (b, a)
        end func

        func main() -> Int
            let (x, y): (Int, Int) = swap(1, 2)
            return x
        end func
        """
        assert compile_and_run(src) == 2

    def test_string_index_compiled(self):
        """String indexing compiles."""
        src = """
        func main() -> String
            let s: String = "hello"
            return s[1]
        end func
        """
        assert compile_and_run(src) == "e"

    def test_map_index_compiled(self):
        """Map indexing compiles."""
        src = """
        func main() -> Int
            let m: Map[String, Int] = map_from_list([("a", 1), ("b", 2)])
            return m["b"]
        end func
        """
        assert compile_and_run(src) == 2


class TestMatchExprFallback:
    """Match expression fallback must be well-formed on both backends (#657)."""

    _NONEXHAUSTIVE_SRC = """
    func main() -> Int
        let x: Int = 5
        let y: Int = match x with
            | 1 -> 10
            | 2 -> 20
        end match
        return y
    end func
    """

    def test_nonexhaustive_match_expr_emits_valid_python(self):
        """Python fallback must not embed a `#` comment inside an expression."""
        code = compile_to_python(self._NONEXHAUSTIVE_SRC, typecheck=False)
        compile(code, "<test>", "exec")

    def test_nonexhaustive_match_expr_raises_at_runtime_on_python(self):
        """When no arm matches, the compiled program raises rather than returning junk."""
        code = compile_to_python(self._NONEXHAUSTIVE_SRC, typecheck=False)
        ns: dict = {}
        exec(code, ns)
        with pytest.raises(RuntimeError, match=r"[Nn]on-exhaustive match"):
            ns["main"]()

    def test_nonexhaustive_match_expr_rejected_by_typechecker(self):
        """Keep the existing static guarantee: typecheck must reject the same program."""
        from geno.types import GenoTypeError

        with pytest.raises(GenoTypeError, match=r"[Nn]on-exhaustive"):
            compile_to_python(self._NONEXHAUSTIVE_SRC)

    def test_nonexhaustive_match_expr_emits_valid_js(self):
        """JS fallback must be a well-formed expression and not a bare `null`."""
        from geno.js_compiler import compile_to_js

        code = compile_to_js(self._NONEXHAUSTIVE_SRC, typecheck=False)
        # The lowered match-expression itself must throw on no-match, not
        # silently evaluate to `null`. The marker message is unique to the
        # compiler emission so it cannot come from the runtime-support module.
        assert "Non-exhaustive match expression" in code

    def test_exhaustive_match_expr_still_runs(self):
        """Exhaustive match expressions must keep working (regression guard)."""
        src = """
        func main() -> Int
            let x: Int = 2
            let y: Int = match x with
                | 1 -> 10
                | _ -> 20
            end match
            return y
        end func
        """
        assert compile_and_run(src) == 20

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

    def test_constructed_match_expr_rejects_empty_arm_body_python(self):
        program, match_expr = self._program_with_match_expr()
        match_expr.arms[0].body = []

        with pytest.raises(CompileError, match="exactly one return statement"):
            Compiler().compile(program)

    def test_constructed_match_expr_rejects_non_return_arm_body_python(self):
        program, match_expr = self._program_with_match_expr()
        loc = match_expr.arms[0].location
        match_expr.arms[0].body = [
            ExpressionStatement(location=loc, expression=IntegerLiteral(loc, 99))
        ]

        with pytest.raises(CompileError, match="exactly one return statement"):
            Compiler().compile(program)

    def test_constructed_match_expr_rejects_multiple_statement_arm_body_python(self):
        program, match_expr = self._program_with_match_expr()
        loc = match_expr.arms[0].location
        match_expr.arms[0].body = [
            ExpressionStatement(location=loc, expression=IntegerLiteral(loc, 99)),
            ReturnStatement(location=loc, value=IntegerLiteral(loc, 1)),
        ]

        with pytest.raises(CompileError, match="exactly one return statement"):
            Compiler().compile(program)
