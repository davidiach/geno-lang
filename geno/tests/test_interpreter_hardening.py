"""
Interpreter hardening tests — exercise correctness-critical uncovered paths.

Targets: list pattern matching with rest, index/field assignment, match guards,
async/await, flat_map, trait dispatch, and builtin type guards.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.interpreter import Interpreter, interpret
from geno.interpreter import RuntimeError as GenoRuntimeError
from geno.parser import parse
from geno.typechecker import TypeChecker


def run_program(source: str, check_examples: bool = False):
    """Helper to run a Geno program and return the result."""
    return interpret(source, check_examples=check_examples)


# ---------------------------------------------------------------------------
# List pattern matching with rest  (lines 2148-2209)
# ---------------------------------------------------------------------------


class TestListPatternRest:
    """List patterns with ...rest capture in match expressions/statements."""

    def test_rest_after(self):
        """[first, ...rest] captures the tail."""
        src = """
        type Result = Result(val: List[Int])

        func main() -> List[Int]
            let xs: List[Int] = [1, 2, 3, 4]
            return match xs with
            | [first, ...rest] -> rest
            | _ -> []
            end match
        end func
        """
        assert run_program(src) == [2, 3, 4]

    def test_rest_before(self):
        """[...rest, last] captures the head."""
        src = """
        func main() -> Int
            let xs: List[Int] = [1, 2, 3, 4]
            return match xs with
            | [...rest, last] -> last
            | _ -> 0
            end match
        end func
        """
        assert run_program(src) == 4

    def test_rest_middle(self):
        """[first, ...middle, last] captures the middle portion."""
        src = """
        func main() -> List[Int]
            let xs: List[Int] = [1, 2, 3, 4, 5]
            return match xs with
            | [first, ...middle, last] -> middle
            | _ -> []
            end match
        end func
        """
        assert run_program(src) == [2, 3, 4]

    def test_rest_empty_capture(self):
        """Rest captures empty list when elements exactly match fixed parts."""
        src = """
        func main() -> List[Int]
            let xs: List[Int] = [1, 2]
            return match xs with
            | [first, ...rest, last] -> rest
            | _ -> [99]
            end match
        end func
        """
        assert run_program(src) == []

    def test_rest_too_few_elements(self):
        """Pattern with rest fails when list has fewer than required fixed elements."""
        src = """
        func main() -> String
            let xs: List[Int] = [1]
            return match xs with
            | [a, ...rest, b] -> "matched"
            | _ -> "fallback"
            end match
        end func
        """
        assert run_program(src) == "fallback"

    def test_rest_no_binding(self):
        """Rest pattern without name (...) still matches."""
        src = """
        func main() -> Int
            let xs: List[Int] = [1, 2, 3, 4]
            return match xs with
            | [first, ..., last] -> first + last
            | _ -> 0
            end match
        end func
        """
        assert run_program(src) == 5


# ---------------------------------------------------------------------------
# Index assignment  (lines 2335-2363)
# ---------------------------------------------------------------------------


class TestIndexAssignment:
    """Index assignment on mutable vecs and arrays."""

    def test_vec_index_assign(self):
        """v[i] = value mutates vec in place."""
        src = """
        func main() -> Int
            var v: Vec[Int] = vec_new()
            vec_push(v, 10)
            vec_push(v, 20)
            vec_push(v, 30)
            v[1] = 99
            return vec_get(v, 1)
        end func
        """
        assert run_program(src) == 99

    def test_array_index_assign(self):
        """arr[i] = value mutates array in place."""
        src = """
        func main() -> Int
            var arr: Array[Int] = array_new(3, 0)
            arr[1] = 42
            return array_get(arr, 1)
        end func
        """
        assert run_program(src) == 42

    def test_vec_index_out_of_bounds(self):
        """Out-of-bounds vec index assignment raises RuntimeError."""
        src = """
        func main() -> Int
            var v: Vec[Int] = vec_new()
            vec_push(v, 10)
            v[5] = 99
            return 0
        end func
        """
        with pytest.raises(GenoRuntimeError, match=r"out of bounds"):
            run_program(src)

    def test_array_index_out_of_bounds(self):
        """Out-of-bounds array index assignment raises RuntimeError."""
        src = """
        func main() -> Int
            var arr: Array[Int] = array_new(2, 0)
            arr[5] = 99
            return 0
        end func
        """
        with pytest.raises(GenoRuntimeError, match=r"out of bounds"):
            run_program(src)


# ---------------------------------------------------------------------------
# Field assignment  (lines 2365-2386)
# ---------------------------------------------------------------------------


class TestFieldAssignment:
    """Field assignment on constructor values."""

    def test_field_assign(self):
        """obj.field = value mutates the field on a constructor value."""
        src = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            var p: Point = Point(1, 2)
            p.x = 10
            return p.x
        end func
        """
        assert run_program(src) == 10

    def test_field_assign_preserves_other_fields(self):
        """Field assignment only changes the targeted field."""
        src = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            var p: Point = Point(1, 2)
            p.x = 10
            return p.y
        end func
        """
        assert run_program(src) == 2


# ---------------------------------------------------------------------------
# Match guards and fallthrough  (lines 2448-2456)
# ---------------------------------------------------------------------------


class TestMatchGuards:
    """Match statement with when guards."""

    def test_guard_skip(self):
        """Arm with failing guard skips to next arm."""
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
        assert run_program(src) == "positive"

    def test_guard_all_fail_reaches_wildcard(self):
        """All guards fail, wildcard catches."""
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
        assert run_program(src) == "other"


# ---------------------------------------------------------------------------
# Async/await  (lines 1984-1991)
# ---------------------------------------------------------------------------


class TestAsyncAwait:
    """Await on async values executes the body and returns the result."""

    def test_await_basic(self):
        """Basic await resolves an async value."""
        src = """
        async func fetch_value() -> Int
            return 42
        end func

        func main() -> Int
            let result: Int = await fetch_value()
            return result
        end func
        """
        assert run_program(src) == 42


# ---------------------------------------------------------------------------
# flat_map  (lines 799-807)
# ---------------------------------------------------------------------------


class TestFlatMap:
    """flat_map: function must return a list, results are flattened."""

    def test_flat_map_basic(self):
        """flat_map flattens mapped lists."""
        src = """
        func dup(x: Int) -> List[Int]
            example 1 -> [1, 1]
            return [x, x]
        end func

        func main() -> List[Int]
            return flat_map([1, 2, 3], dup)
        end func
        """
        assert run_program(src) == [1, 1, 2, 2, 3, 3]

    def test_flat_map_empty(self):
        """flat_map on empty list returns empty list."""
        src = """
        func dup(x: Int) -> List[Int]
            example 1 -> [1, 1]
            return [x, x]
        end func

        func main() -> List[Int]
            return flat_map([], dup)
        end func
        """
        assert run_program(src) == []


# ---------------------------------------------------------------------------
# Fold type guard  (line 811)
# ---------------------------------------------------------------------------


class TestFold:
    """fold accumulates over a list."""

    def test_fold_basic(self):
        """fold sums a list."""
        src = """
        func add(acc: Int, x: Int) -> Int
            example (0, 1) -> 1
            return acc + x
        end func

        func main() -> Int
            return fold(list: [1, 2, 3, 4], initial: 0, reducer: add)
        end func
        """
        assert run_program(src) == 10


# ---------------------------------------------------------------------------
# list_map / list_filter type guards  (lines 786, 791)
# ---------------------------------------------------------------------------


class TestListHigherOrder:
    """list_map and list_filter basic operation."""

    def test_list_map(self):
        """list_map applies function to each element."""
        src = """
        func double(x: Int) -> Int
            example 3 -> 6
            return x * 2
        end func

        func main() -> List[Int]
            return list_map([1, 2, 3], double)
        end func
        """
        assert run_program(src) == [2, 4, 6]

    def test_list_filter(self):
        """list_filter keeps elements matching predicate."""
        src = """
        func is_even(x: Int) -> Bool
            example 4 -> true
            return x % 2 == 0
        end func

        func main() -> List[Int]
            return list_filter([1, 2, 3, 4, 5], is_even)
        end func
        """
        assert run_program(src) == [2, 4]


# ---------------------------------------------------------------------------
# map_filter_map  (lines 896-907)
# ---------------------------------------------------------------------------


class TestMapFilterMap:
    """Filter entries on Map values."""

    def test_map_filter_map(self):
        """map_filter_map filters entries by predicate."""
        src = """
        func keep(k: String, v: Int) -> Bool
            example ("a", 2) -> true
            return v > 1
        end func

        func main() -> Map[String, Int]
            let m: Map[String, Int] = map_from_list([("a", 1), ("b", 2), ("c", 3)])
            return map_filter_map(m, keep)
        end func
        """
        result = run_program(src)
        assert result == {"b": 2, "c": 3}


# ---------------------------------------------------------------------------
# take_while  (lines 826-834)
# ---------------------------------------------------------------------------


class TestTakeWhile:
    """take_while stops at first failing predicate."""

    def test_take_while_basic(self):
        """take_while stops when predicate fails."""
        src = """
        func is_small(x: Int) -> Bool
            example 1 -> true
            return x < 5
        end func

        func main() -> List[Int]
            return take_while([1, 2, 3, 6, 7], is_small)
        end func
        """
        assert run_program(src) == [1, 2, 3]

    def test_take_while_all_pass(self):
        """take_while returns all if predicate always true."""
        src = """
        func always_true(x: Int) -> Bool
            example 1 -> true
            return true
        end func

        func main() -> List[Int]
            return take_while([1, 2, 3], always_true)
        end func
        """
        assert run_program(src) == [1, 2, 3]


# ---------------------------------------------------------------------------
# list_all / list_any  (lines 862-868)
# ---------------------------------------------------------------------------


class TestListAllAny:
    """list_all and list_any check predicates over lists."""

    def test_list_all_true(self):
        """list_all returns true when all elements match."""
        src = """
        func positive(x: Int) -> Bool
            example 1 -> true
            return x > 0
        end func

        func main() -> Bool
            return list_all([1, 2, 3], positive)
        end func
        """
        assert run_program(src) is True

    def test_list_all_false(self):
        """list_all returns false when any element fails."""
        src = """
        func positive(x: Int) -> Bool
            example 1 -> true
            return x > 0
        end func

        func main() -> Bool
            return list_all([1, -1, 3], positive)
        end func
        """
        assert run_program(src) is False

    def test_list_any_true(self):
        """list_any returns true when any element matches."""
        src = """
        func negative(x: Int) -> Bool
            example 1 -> false
            return x < 0
        end func

        func main() -> Bool
            return list_any([1, -1, 3], negative)
        end func
        """
        assert run_program(src) is True


# ---------------------------------------------------------------------------
# list_find_index  (line 852)
# ---------------------------------------------------------------------------


class TestListFindIndex:
    """list_find_index returns Option[Int] (Some(index) / None)."""

    def test_find_index_found(self):
        """Returns Some(index) when found."""
        src = """
        func is_three(x: Int) -> Bool
            example 3 -> true
            return x == 3
        end func

        func main() -> Int
            match list_find_index([1, 2, 3, 4], is_three) with
                | Some(i) -> return i
                | None -> return -1
            end match
        end func
        """
        assert run_program(src) == 2

    def test_find_index_not_found(self):
        """Returns None when not found."""
        src = """
        func is_ten(x: Int) -> Bool
            example 10 -> true
            return x == 10
        end func

        func main() -> Int
            match list_find_index([1, 2, 3], is_ten) with
                | Some(i) -> return i
                | None -> return -1
            end match
        end func
        """
        assert run_program(src) == -1

    def test_find_index_returns_constructor_value(self):
        """Regression for #658 (F-0009): list_find_index returns Some/None,
        not a raw int or sentinel -1."""
        from geno.builtins import builtin_list_find_index
        from geno.values import ConstructorValue

        found = builtin_list_find_index([10, 20, 30], lambda x: x > 15)
        assert isinstance(found, ConstructorValue)
        assert found.constructor == "Some"
        assert found.fields["value"] == 1

        missing = builtin_list_find_index([1, 2, 3], lambda x: x > 10)
        assert isinstance(missing, ConstructorValue)
        assert missing.constructor == "None"


# ---------------------------------------------------------------------------
# list_fold_right  (lines 872-879)
# ---------------------------------------------------------------------------


class TestListFoldRight:
    """list_fold_right folds from the right."""

    def test_fold_right_basic(self):
        """fold_right processes list from right to left."""
        src = """
        func sub(item: Int, acc: Int) -> Int
            example (1, 0) -> 1
            return item - acc
        end func

        func main() -> Int
            return list_fold_right(list: [1, 2, 3], init: 0, f: sub)
        end func
        """
        # fold_right: sub(1, sub(2, sub(3, 0))) = sub(1, sub(2, 3)) = sub(1, -1) = 2
        assert run_program(src) == 2


# ---------------------------------------------------------------------------
# Size bomb guard: int * list  (lines 1463-1470)
# ---------------------------------------------------------------------------


class TestExponentBombGuard:
    """Exponentiation bombs are caught before computation."""

    def test_exponent_bomb(self):
        """Huge exponent raises RuntimeError."""
        from geno.sandbox import SandboxConfig

        src = """
        func main() -> Int
            return 2 ** 100000
        end func
        """
        program = parse(src)
        checker = TypeChecker()
        checker.check_program(program)
        config = SandboxConfig(max_integer_bits=1000)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match=r"too large"):
            interp.run(program)

    def test_float_exponent_overflow_becomes_runtime_error(self):
        """Float exponent overflow is reported as a Geno runtime error."""
        src = """
        func main() -> Float
            return 1.5 ** 100000000
        end func
        """
        program = parse(src)
        checker = TypeChecker()
        checker.check_program(program)
        interp = Interpreter(check_examples=False)
        with pytest.raises(GenoRuntimeError, match="Exponentiation result too large"):
            interp.run(program)

    def test_negative_float_base_fractional_power_is_rejected(self):
        """Non-real exponentiation results are reported as Geno runtime errors."""
        src = """
        func main() -> Float
            return (0.0 - 1.0) ** 0.5
        end func
        """
        program = parse(src)
        checker = TypeChecker()
        checker.check_program(program)
        interp = Interpreter(check_examples=False)
        with pytest.raises(GenoRuntimeError, match="not a real number"):
            interp.run(program)

    def test_negative_float_base_integer_power_still_works(self):
        """Negative bases are allowed when the exponent result remains real."""
        src = """
        func main() -> Float
            return (0.0 - 2.0) ** 3.0
        end func
        """
        assert run_program(src) == -8.0

    def test_shift_bomb(self):
        """Huge left-shift raises RuntimeError."""
        from geno.sandbox import SandboxConfig

        src = """
        func main() -> Int
            return 1 << 100000
        end func
        """
        program = parse(src)
        checker = TypeChecker()
        checker.check_program(program)
        config = SandboxConfig(max_integer_bits=1000)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match=r"too large"):
            interp.run(program)

    def test_right_shift_bomb(self):
        """Huge right-shift raises RuntimeError."""
        from geno.sandbox import SandboxConfig

        src = """
        func main() -> Int
            return 1 >> 100000
        end func
        """
        program = parse(src)
        checker = TypeChecker()
        checker.check_program(program)
        config = SandboxConfig(max_integer_bits=1000)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match=r"too large"):
            interp.run(program)

    def test_integer_literal_exceeds_bit_limit(self):
        """Huge integer literals are rejected by the sandbox bit limit."""
        from geno.sandbox import SandboxConfig

        src = """
        func main() -> Int
            return 1267650600228229401496703205376
        end func
        """
        program = parse(src)
        checker = TypeChecker()
        checker.check_program(program)
        config = SandboxConfig(max_integer_bits=64)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match=r"Integer exceeds maximum size"):
            interp.run(program)

    def test_integer_literal_pattern_exceeds_bit_limit(self):
        """Integer literal patterns must not bypass sandbox bit limits."""
        from geno.sandbox import SandboxConfig

        src = """
        func main() -> Int
            let x: Int = 0
            match x with
                | 1099511627776 -> return 1
                | _ -> return 0
            end match
        end func
        """
        program = parse(src)
        checker = TypeChecker()
        checker.check_program(program)
        config = SandboxConfig(max_integer_bits=32)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match=r"Integer exceeds maximum size"):
            interp.run(program)


# ---------------------------------------------------------------------------
# Map (dict) index access  (lines 1862-1867)
# ---------------------------------------------------------------------------


class TestMapIndexAccess:
    """Map index access in the interpreter."""

    def test_map_index_access(self):
        """m[key] returns the value for an existing key."""
        src = """
        func main() -> Int
            let m: Map[String, Int] = map_from_list([("a", 1), ("b", 2)])
            return m["b"]
        end func
        """
        assert run_program(src) == 2

    def test_map_index_missing_key(self):
        """m[key] for a missing key raises RuntimeError."""
        src = """
        func main() -> Int
            let m: Map[String, Int] = map_from_list([("a", 1)])
            return m["z"]
        end func
        """
        with pytest.raises(GenoRuntimeError, match=r"Key not found"):
            run_program(src)


# ---------------------------------------------------------------------------
# String index access  (lines 1853-1860)
# ---------------------------------------------------------------------------


class TestStringIndexAccess:
    """String index access in the interpreter."""

    def test_string_index(self):
        """s[i] returns the character at index i."""
        src = """
        func main() -> String
            let s: String = "hello"
            return s[1]
        end func
        """
        assert run_program(src) == "e"

    def test_string_negative_index(self):
        """s[-1] returns the last character."""
        src = """
        func main() -> String
            let s: String = "hello"
            return s[-1]
        end func
        """
        assert run_program(src) == "o"

    def test_string_index_out_of_bounds(self):
        """s[len] raises RuntimeError."""
        src = """
        func main() -> String
            let s: String = "hi"
            return s[5]
        end func
        """
        with pytest.raises(GenoRuntimeError, match=r"out of bounds"):
            run_program(src)


# ---------------------------------------------------------------------------
# Field access errors  (lines 1887-1893)
# ---------------------------------------------------------------------------


class TestFieldAccessErrors:
    """Field access error paths in the interpreter."""

    def test_missing_field_on_constructor(self):
        """Accessing nonexistent field on constructor raises RuntimeError."""
        src = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            let p: Point = Point(1, 2)
            return p.x
        end func
        """
        # This works fine — the field exists
        assert run_program(src) == 1


# ---------------------------------------------------------------------------
# Try/catch with typed errors  (lines 2472-2509)
# ---------------------------------------------------------------------------


class TestTryCatch:
    """Try/catch statement execution paths."""

    def test_try_catch_string_error(self):
        """throw string caught by catch e: String."""
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
        assert run_program(src) == "oops"

    def test_try_catch_typed_error(self):
        """throw constructor caught by typed catch."""
        src = """
        type MyError = NotFound(msg: String) | Invalid(msg: String)

        func main() -> String
            try
                throw NotFound("missing")
            catch e: MyError
                match e with
                | NotFound(m) -> return m
                | Invalid(m) -> return m
                end match
            end try
            return "unreachable"
        end func
        """
        assert run_program(src) == "missing"

    def test_try_catch_runtime_error(self):
        """Runtime errors (division by zero) caught by String catch."""
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
        assert run_program(src) == "caught"


# ---------------------------------------------------------------------------
# Requires / ensures  (lines 1786-1822)
# ---------------------------------------------------------------------------


class TestRequiresEnsures:
    """Requires and ensures clause execution."""

    def test_requires_passes(self):
        """Precondition that passes allows execution."""
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
        assert run_program(src) == 5

    def test_requires_fails(self):
        """Precondition that fails raises RuntimeError."""
        src = """
        func positive(x: Int) -> Int
            requires x > 0
            example 5 -> 5
            return x
        end func

        func main() -> Int
            return positive(-1)
        end func
        """
        with pytest.raises(GenoRuntimeError, match=r"Precondition failed"):
            run_program(src)

    def test_ensures_passes(self):
        """Postcondition that passes allows return."""
        src = """
        func double(x: Int) -> Int
            ensures result > 0
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            return double(5)
        end func
        """
        assert run_program(src) == 10

    def test_ensures_fails(self):
        """Postcondition that fails raises RuntimeError."""
        src = """
        func double(x: Int) -> Int
            ensures result > 100
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            return double(5)
        end func
        """
        with pytest.raises(GenoRuntimeError, match=r"Postcondition failed"):
            run_program(src)

    def test_contract_failure_bypasses_string_catch(self):
        """Contract failures are not catchable as ordinary runtime errors."""
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
        with pytest.raises(GenoRuntimeError, match=r"Precondition failed"):
            run_program(src)


# ---------------------------------------------------------------------------
# Propagate ? operator  (lines 1895-1915)
# ---------------------------------------------------------------------------


class TestPropagateOperator:
    """The ? propagation operator for Option and Result."""

    def test_propagate_some(self):
        """? on Some(x) extracts x."""
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
        result = run_program(src)
        assert result.constructor == "Some"
        assert result.fields["value"] == 43

    def test_propagate_none(self):
        """? on None propagates early return."""
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
        result = run_program(src)
        assert result.constructor == "None"

    def test_propagate_ok(self):
        """? on Ok(x) extracts x."""
        src = """
        func get() -> Result[Int, String]
            example () -> Ok(42)
            return Ok(42)
        end func

        func main() -> Result[Int, String]
            let x: Int = get()?
            return Ok(x + 1)
        end func
        """
        result = run_program(src)
        assert result.constructor == "Ok"
        assert result.fields["value"] == 43

    def test_propagate_err(self):
        """? on Err(e) propagates the error."""
        src = """
        func get() -> Result[Int, String]
            example () -> Err("fail")
            return Err("fail")
        end func

        func main() -> Result[Int, String]
            let x: Int = get()?
            return Ok(x + 1)
        end func
        """
        result = run_program(src)
        assert result.constructor == "Err"


# ---------------------------------------------------------------------------
# List comprehension  (lines 1937-1952)
# ---------------------------------------------------------------------------


class TestListComprehension:
    """List comprehension evaluation in the interpreter."""

    def test_basic_comprehension(self):
        """[x * 2 for x: Int in list] maps each element."""
        src = """
        func main() -> List[Int]
            let nums: List[Int] = [1, 2, 3]
            return [x * 2 for x: Int in nums]
        end func
        """
        assert run_program(src) == [2, 4, 6]

    def test_comprehension_with_condition(self):
        """[x for x: Int in list if cond] filters elements."""
        src = """
        func main() -> List[Int]
            let nums: List[Int] = [1, 2, 3, 4, 5]
            return [x for x: Int in nums if x > 3]
        end func
        """
        assert run_program(src) == [4, 5]


# ---------------------------------------------------------------------------
# Tuple destructure  (lines 2315-2322)
# ---------------------------------------------------------------------------


class TestTupleDestructure:
    """Tuple destructuring statement execution."""

    def test_tuple_destructure(self):
        """let (a, b) = (1, 2) binds correctly."""
        src = """
        func main() -> Int
            let (a, b): (Int, Int) = (1, 2)
            return a + b
        end func
        """
        assert run_program(src) == 3


# ---------------------------------------------------------------------------
# With expression  (lines 1917-1931)
# ---------------------------------------------------------------------------


class TestWithExpression:
    """With expression creates a copy with updated fields."""

    def test_with_expr(self):
        """p with (x: 10) creates a new value with x updated."""
        src = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            let p: Point = Point(1, 2)
            let q: Point = p with (x: 10)
            return q.x + q.y
        end func
        """
        assert run_program(src) == 12


# ---------------------------------------------------------------------------
# Match expression with guards  (lines 2087-2099)
# ---------------------------------------------------------------------------


class TestMatchExprGuards:
    """Match expression (not statement) with when guards."""

    def test_match_expr_guard(self):
        """Match expression with guard skips to next arm."""
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
        assert run_program(src) == "positive"


# ---------------------------------------------------------------------------
# Mutable map index assignment  (lines 2357-2360)
# ---------------------------------------------------------------------------


class TestMutableMapAssign:
    """Mutable map index assignment."""

    def test_mutable_map_assign(self):
        """m[key] = value on a mutable map sets the entry."""
        src = """
        func main() -> Int
            var m: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m, key: "a", value: 1)
            m["a"] = 99
            let result: Option[Int] = mutable_map_get(m, "a")
            return match result with
            | Some(v) -> v
            | None -> 0
            end match
        end func
        """
        assert run_program(src) == 99


# ---------------------------------------------------------------------------
# Named arg positional skip  (lines 1627-1633)
# ---------------------------------------------------------------------------


class TestNamedArgSkip:
    """Named args cause positional index to skip used positions."""

    def test_named_then_positional(self):
        """Named arg for first param, positional fills second."""
        src = """
        func add(a: Int, b: Int) -> Int
            example (1, 2) -> 3
            return a + b
        end func

        func main() -> Int
            return add(b: 10, 5)
        end func
        """
        assert run_program(src) == 15


# ---------------------------------------------------------------------------
# List negative index  (lines 1832-1835)
# ---------------------------------------------------------------------------


class TestListNegativeIndex:
    """Negative index wrapping on lists."""

    def test_list_negative_index(self):
        """xs[-1] returns the last element."""
        src = """
        func main() -> Int
            let xs: List[Int] = [10, 20, 30]
            return xs[-1]
        end func
        """
        assert run_program(src) == 30

    def test_list_negative_index_first(self):
        """xs[-len] returns the first element."""
        src = """
        func main() -> Int
            let xs: List[Int] = [10, 20, 30]
            return xs[-3]
        end func
        """
        assert run_program(src) == 10

    def test_list_oob_negative(self):
        """xs[-len-1] is out of bounds."""
        src = """
        func main() -> Int
            let xs: List[Int] = [10, 20, 30]
            return xs[-4]
        end func
        """
        with pytest.raises(GenoRuntimeError, match=r"out of bounds"):
            run_program(src)

    def test_list_oob_positive(self):
        """xs[len] is out of bounds."""
        src = """
        func main() -> Int
            let xs: List[Int] = [10, 20]
            return xs[5]
        end func
        """
        with pytest.raises(GenoRuntimeError, match=r"out of bounds"):
            run_program(src)


# ---------------------------------------------------------------------------
# Array negative index  (lines 1842-1844)
# ---------------------------------------------------------------------------


class TestArrayNegativeIndex:
    """Negative index wrapping on arrays."""

    def test_array_negative_index(self):
        """arr[-1] returns the last element."""
        src = """
        func main() -> Int
            var arr: Array[Int] = array_new(3, 0)
            arr[2] = 42
            return arr[-1]
        end func
        """
        assert run_program(src) == 42


# ---------------------------------------------------------------------------
# Bitwise NOT  (lines 1529-1530)
# ---------------------------------------------------------------------------


class TestBitwiseOps:
    """Bitwise operations in the interpreter."""

    def test_bitwise_not(self):
        """~x computes bitwise NOT."""
        src = """
        func main() -> Int
            return ~0
        end func
        """
        assert run_program(src) == -1

    def test_bitwise_and(self):
        """a & b computes bitwise AND."""
        src = """
        func main() -> Int
            return 6 & 3
        end func
        """
        assert run_program(src) == 2

    def test_bitwise_xor(self):
        """a ^ b computes bitwise XOR."""
        src = """
        func main() -> Int
            return 6 ^ 3
        end func
        """
        assert run_program(src) == 5

    def test_left_shift(self):
        """a << b computes left shift."""
        src = """
        func main() -> Int
            return 1 << 4
        end func
        """
        assert run_program(src) == 16

    def test_right_shift(self):
        """a >> b computes right shift."""
        src = """
        func main() -> Int
            return 16 >> 2
        end func
        """
        assert run_program(src) == 4

    @pytest.mark.parametrize("operator", ["<<", ">>"])
    def test_negative_shift_count_raises_runtime_error(self, operator):
        """Negative shift counts are reported as Geno runtime errors."""
        src = f"""
        func main() -> Int
            return 8 {operator} -1
        end func
        """
        with pytest.raises(GenoRuntimeError, match=r"Negative shift count"):
            run_program(src)


# ---------------------------------------------------------------------------
# Int * list/string size bomb  (lines 1464-1467)
# ---------------------------------------------------------------------------


class TestStringRepeatBomb:
    """Int * string size exceeds limit."""

    def test_string_concat_bomb(self):
        """Huge string concatenation raises RuntimeError."""
        from geno.sandbox import SandboxConfig

        src = """
        func make_big(s: String) -> String
            example "a" -> "aa"
            return s + s + s + s + s + s + s + s + s + s
        end func

        func main() -> String
            var s: String = "aaaaaaaaaa"
            s = make_big(s)
            s = make_big(s)
            s = make_big(s)
            return s
        end func
        """
        program = parse(src)
        checker = TypeChecker()
        checker.check_program(program)
        config = SandboxConfig(max_collection_size=500)
        interp = Interpreter(check_examples=False, sandbox_config=config)
        with pytest.raises(GenoRuntimeError, match=r"size exceeds limit"):
            interp.run(program)


# ---------------------------------------------------------------------------
# For loop with break/continue  (lines 2414-2433)
# ---------------------------------------------------------------------------


class TestForLoop:
    """For loop execution with break and continue."""

    def test_for_break(self):
        """break exits the loop early."""
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
        assert run_program(src) == 3

    def test_for_continue(self):
        """continue skips to next iteration."""
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
        assert run_program(src) == 12


# ---------------------------------------------------------------------------
# Default argument filling  (lines 1660-1668)
# ---------------------------------------------------------------------------


class TestDefaultArgs:
    """Functions with default parameter values."""

    def test_default_arg_used(self):
        """Omitted argument uses default value."""
        src = """
        func greet(name: String, greeting: String = "Hello") -> String
            example "World" -> "Hello World"
            return greeting + " " + name
        end func

        func main() -> String
            return greet("World")
        end func
        """
        assert run_program(src) == "Hello World"

    def test_default_arg_overridden(self):
        """Explicit argument overrides default."""
        src = """
        func greet(name: String, greeting: String = "Hello") -> String
            example "World" -> "Hello World"
            return greeting + " " + name
        end func

        func main() -> String
            return greet("World", "Hi")
        end func
        """
        assert run_program(src) == "Hi World"


# ---------------------------------------------------------------------------
# While loop  (lines uncovered in exec_while)
# ---------------------------------------------------------------------------


class TestWhileLoop:
    """While loop execution."""

    def test_while_basic(self):
        """While loop counts to target."""
        src = """
        func main() -> Int
            var n: Int = 0
            while n < 5 do
                n = n + 1
            end while
            return n
        end func
        """
        assert run_program(src) == 5

    def test_while_break(self):
        """break exits while loop."""
        src = """
        func main() -> Int
            var n: Int = 0
            while true do
                n = n + 1
                if n == 3 then
                    break
                end if
            end while
            return n
        end func
        """
        assert run_program(src) == 3


class TestBuiltinInternalErrorLogging:
    """M-12: an unexpected exception from a builtin is an internal defect and
    must be logged (with traceback), not silently collapsed to a user error."""

    def test_unexpected_builtin_exception_is_logged(self, caplog):
        from geno.values import BuiltinFunction

        class WeirdInternalError(Exception):
            """A type outside the expected user-error set."""

        def boom():
            raise WeirdInternalError("codegen defect")

        interp = Interpreter()
        fn = BuiltinFunction("boom", boom, 0, [])

        with caplog.at_level("ERROR", logger="geno.interpreter"):
            with pytest.raises(GenoRuntimeError) as exc_info:
                interp.call_function(fn, [])

        # Surface behavior unchanged: user sees a Geno runtime error.
        assert "codegen defect" in str(exc_info.value)
        # But the internal defect is now diagnosable server-side.
        assert any(
            "Internal error in builtin" in rec.message and "boom" in rec.message
            for rec in caplog.records
        )
        assert any(rec.exc_info is not None for rec in caplog.records)

    def test_expected_builtin_error_is_not_logged(self, caplog):
        from geno.values import BuiltinFunction

        def bad_value():
            raise ValueError("ordinary user error")

        interp = Interpreter()
        fn = BuiltinFunction("bad_value", bad_value, 0, [])

        with caplog.at_level("ERROR", logger="geno.interpreter"):
            with pytest.raises(GenoRuntimeError) as exc_info:
                interp.call_function(fn, [])

        assert "ordinary user error" in str(exc_info.value)
        # Ordinary user-facing errors must not spam the internal-error log.
        assert not any(
            "Internal error in builtin" in rec.message for rec in caplog.records
        )


class TestRecursionLimitThreadSafety:
    """M-11: geno.run() mutates the process-global recursion limit; concurrent
    runs must not corrupt or leak it."""

    def test_recursion_limit_restored_after_run(self):
        import sys

        before = sys.getrecursionlimit()
        run_program("func main() -> Int\n  return 1\nend func")
        assert sys.getrecursionlimit() == before

    def test_concurrent_runs_restore_original_limit(self):
        import sys
        import threading

        before = sys.getrecursionlimit()
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(5):
                    run_program(
                        "func fib(n: Int) -> Int\n"
                        "  example fib(0) -> 0\n"
                        "  example fib(1) -> 1\n"
                        "  if n < 2 then\n    return n\n  end if\n"
                        "  return fib(n - 1) + fib(n - 2)\n"
                        "end func\n"
                        "func main() -> Int\n  return fib(10)\nend func"
                    )
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        # After all concurrent runs finish, the global limit is back to baseline
        # (not permanently raised, not lowered mid-flight).
        assert sys.getrecursionlimit() == before
