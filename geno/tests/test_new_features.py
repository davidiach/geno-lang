"""
Tests for new language features: break/continue, mouse input, text input,
format builtin, floor/ceil/round, and recursion limit increase.
"""

import re
from contextlib import contextmanager
from typing import Any, cast

import pytest

from geno.api import RunConfig, run
from geno.compiler import compile_and_exec
from geno.js_compiler import compile_to_js
from geno.lexer import Lexer
from geno.parser import Parser
from geno.target_profile import TargetProfile
from geno.tests._script_runner import run_node_code
from geno.typechecker import TypeChecker, TypeError

# =============================================================================
# Helpers
# =============================================================================


def typecheck(source: str) -> TypeChecker:
    """Parse and typecheck source code, returning the checker."""
    lexer = Lexer(source, "<test>")
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    program = parser.parse_program()
    checker = TypeChecker()
    checker.check_program(program)
    return checker


def run_geno(source: str, capabilities: set[str] | None = None):
    """Run geno source code and return the result value."""
    result = run(source, config=RunConfig(timeout=5.0, capabilities=capabilities))
    assert result.ok, f"Run failed: {result.diagnostics}"
    return result.value


def compile_js(source: str, target_name: str = "node-cli") -> str:
    """Compile geno source to JS and return the JS string."""
    lexer = Lexer(source, "<test>")
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    program = parser.parse_program()
    profile = TargetProfile.load(target_name)
    checker = TypeChecker(target_profile=profile)
    checker.check_program(program)
    result = compile_to_js(source, target_profile=profile)
    assert isinstance(result, str)
    return result


@contextmanager
def _runtime_caps(*capabilities: str):
    """Temporarily grant compiled-Python runtime capabilities for helper tests."""
    from geno import _runtime_support as rs

    saved = rs._GENO_CAPS
    rs._GENO_CAPS = set(capabilities)
    try:
        yield
    finally:
        rs._GENO_CAPS = saved


def _js_runtime_prelude_with_caps(*capabilities: str) -> str:
    """Return the JS runtime prelude with explicit runtime capabilities granted."""
    from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE

    lines = [JS_RUNTIME_PRELUDE, "\n_GENO_CAPS.clear();\n"]
    for capability in capabilities:
        lines.append(f'_GENO_CAPS.add("{capability}");\n')
    return "".join(lines)


# =============================================================================
# Break / Continue
# =============================================================================


class TestBreakContinue:
    """Tests for break and continue statements."""

    def test_break_in_while(self):
        source = """
        func main() -> Int
          var sum: Int = 0
          var i: Int = 0
          while i < 100 do
            i = i + 1
            if i == 5 then
              break
            end if
            sum = sum + i
          end while
          return sum
        end func main
        """
        assert run_geno(source) == 10  # 1+2+3+4

    def test_break_in_for(self):
        source = """
        func main() -> Int
          var sum: Int = 0
          for x: Int in [10, 20, 30, 40, 50] do
            if x == 30 then
              break
            end if
            sum = sum + x
          end for
          return sum
        end func main
        """
        assert run_geno(source) == 30  # 10+20

    def test_continue_in_while(self):
        source = """
        func main() -> Int
          var sum: Int = 0
          var i: Int = 0
          while i < 5 do
            i = i + 1
            if i == 3 then
              continue
            end if
            sum = sum + i
          end while
          return sum
        end func main
        """
        assert run_geno(source) == 12  # 1+2+4+5

    def test_continue_in_for(self):
        source = """
        func main() -> Int
          var sum: Int = 0
          for x: Int in [1, 2, 3, 4, 5] do
            if x == 3 then
              continue
            end if
            sum = sum + x
          end for
          return sum
        end func main
        """
        assert run_geno(source) == 12  # 1+2+4+5

    def test_nested_loops_break_inner(self):
        source = """
        func main() -> Int
          var count: Int = 0
          var i: Int = 0
          while i < 3 do
            i = i + 1
            var j: Int = 0
            while j < 10 do
              j = j + 1
              if j == 2 then
                break
              end if
              count = count + 1
            end while
          end while
          return count
        end func main
        """
        assert run_geno(source) == 3  # inner loop runs 1 iteration, outer runs 3

    def test_break_outside_loop_is_type_error(self):
        source = """
        func main() -> Int
          break
          return 0
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_continue_outside_loop_is_type_error(self):
        source = """
        func main() -> Int
          continue
          return 0
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_break_compiles_to_python(self):
        """Verify break compiles to Python without errors."""
        source = """
        func main() -> Int
          var i: Int = 0
          while i < 10 do
            i = i + 1
            break
          end while
          return i
        end func main
        """
        assert run_geno(source) == 1

    def test_break_compiles_to_js(self):
        """Verify break appears in compiled JS."""
        source = """
        func main() -> Int
          var i: Int = 0
          while i < 10 do
            break
          end while
          return i
        end func main
        """
        js = compile_js(source)
        assert "break;" in js

    def test_continue_compiles_to_js(self):
        """Verify continue appears in compiled JS."""
        source = """
        func main() -> Int
          var i: Int = 0
          while i < 10 do
            i = i + 1
            continue
          end while
          return i
        end func main
        """
        js = compile_js(source)
        assert "continue;" in js


# =============================================================================
# Mouse Input Builtins
# =============================================================================


class TestMouseInput:
    """Tests for mouse input builtins."""

    def test_mouse_x_returns_int(self):
        source = """
        func main() -> Int
          return mouse_x()
        end func main
        """
        assert run_geno(source) == 0

    def test_mouse_y_returns_int(self):
        source = """
        func main() -> Int
          return mouse_y()
        end func main
        """
        assert run_geno(source) == 0

    def test_is_mouse_down_returns_bool(self):
        source = """
        func main() -> Bool
          return is_mouse_down()
        end func main
        """
        assert run_geno(source) == False

    def test_is_mouse_clicked_returns_bool(self):
        source = """
        func main() -> Bool
          return is_mouse_clicked()
        end func main
        """
        assert run_geno(source) == False

    def test_mouse_builtins_typecheck(self):
        source = """
        func main() -> Int
          let x: Int = mouse_x()
          let y: Int = mouse_y()
          let down: Bool = is_mouse_down()
          let clicked: Bool = is_mouse_clicked()
          return x
        end func main
        """
        typecheck(source)

    def test_mouse_builtins_in_js(self):
        source = """
        func main() -> Int
          return mouse_x()
        end func main
        """
        js = compile_js(source, target_name="browser")
        assert "mouse_x()" in js


# =============================================================================
# Text Input Builtins
# =============================================================================


class TestTextInput:
    """Tests for text input builtins."""

    def test_get_text_input_returns_string(self):
        source = """
        func main() -> String
          return get_text_input()
        end func main
        """
        assert run_geno(source) == ""

    def test_clear_text_input_returns_unit(self):
        source = """
        func main() -> Unit
          clear_text_input()
        end func main
        """
        result = run(source, config=RunConfig(timeout=5.0))
        assert result.ok

    def test_text_input_typecheck(self):
        source = """
        func main() -> String
          let text: String = get_text_input()
          clear_text_input()
          return text
        end func main
        """
        typecheck(source)


# =============================================================================
# Format Builtin
# =============================================================================


class TestFormatBuiltin:
    """Tests for the format builtin."""

    def test_basic_format(self):
        source = """
        func main() -> String
          return format("Hello, {}!", ["world"])
        end func main
        """
        assert run_geno(source) == "Hello, world!"

    def test_multiple_placeholders(self):
        source = """
        func main() -> String
          return format("{} + {} = {}", ["1", "2", "3"])
        end func main
        """
        assert run_geno(source) == "1 + 2 = 3"

    def test_no_placeholders(self):
        source = """
        func main() -> String
          return format("no placeholders", [])
        end func main
        """
        assert run_geno(source) == "no placeholders"

    def test_format_with_to_string(self):
        source = """
        func main() -> String
          let score: Int = 42
          return format("Score: {}", [to_string(score)])
        end func main
        """
        assert run_geno(source) == "Score: 42"

    def test_format_typecheck(self):
        source = """
        func main() -> String
          return format("Hello {}", ["world"])
        end func main
        """
        typecheck(source)

    def test_format_mismatched_count(self):
        source = """
        func main() -> String
          return format("{} {}", ["only_one"])
        end func main
        """
        with pytest.raises(Exception):
            run_geno(source)

    def test_format_in_js(self):
        source = """
        func main() -> String
          return format("{} {}", ["a", "b"])
        end func main
        """
        js = compile_js(source)
        assert "format(" in js


# =============================================================================
# Floor / Ceil / Round
# =============================================================================


class TestMathBuiltins:
    """Tests for floor, ceil, and round builtins."""

    def test_floor_positive(self):
        source = """
        func main() -> Int
          return floor(3.7)
        end func main
        """
        assert run_geno(source) == 3

    def test_floor_negative(self):
        source = """
        func main() -> Int
          return floor(int_to_float(-1) - 0.3)
        end func main
        """
        assert run_geno(source) == -2

    def test_ceil_positive(self):
        source = """
        func main() -> Int
          return ceil(3.2)
        end func main
        """
        assert run_geno(source) == 4

    def test_ceil_negative(self):
        source = """
        func main() -> Int
          return ceil(int_to_float(-1) - 0.7)
        end func main
        """
        assert run_geno(source) == -1

    def test_round_up(self):
        source = """
        func main() -> Int
          return round(3.7)
        end func main
        """
        assert run_geno(source) == 4

    def test_round_down(self):
        source = """
        func main() -> Int
          return round(3.2)
        end func main
        """
        assert run_geno(source) == 3

    def test_round_half_up(self):
        source = """
        func main() -> Int
          return round(0.5)
        end func main
        """
        assert run_geno(source) == 1

    def test_round_negative_half(self):
        source = """
        func main() -> Int
          return round(int_to_float(0) - 0.5)
        end func main
        """
        assert run_geno(source) == 0

    def test_floor_ceil_round_typecheck(self):
        source = """
        func main() -> Int
          let a: Int = floor(1.5)
          let b: Int = ceil(1.5)
          let c: Int = round(1.5)
          return a
        end func main
        """
        typecheck(source)

    def test_math_builtins_in_js(self):
        source = """
        func main() -> Int
          return floor(3.5)
        end func main
        """
        js = compile_js(source)
        assert "floor(" in js


# =============================================================================
# Parse Float
# =============================================================================


class TestParseFloat:
    """Tests for the parse_float builtin."""

    def test_parse_float_integer(self):
        source = """
        func main() -> Float
          let result: Option[Float] = parse_float("42")
          match result with
            | Some(v) -> return v
            | None -> return 0.0 - 1.0
          end match
        end func main
        """
        assert run_geno(source) == 42.0

    def test_parse_float_decimal(self):
        source = """
        func main() -> Float
          let result: Option[Float] = parse_float("3.14")
          match result with
            | Some(v) -> return v
            | None -> return 0.0 - 1.0
          end match
        end func main
        """
        assert abs(run_geno(source) - 3.14) < 0.001

    def test_parse_float_invalid(self):
        source = """
        func main() -> Bool
          let result: Option[Float] = parse_float("abc")
          return is_none(result)
        end func main
        """
        assert run_geno(source) == True

    def test_parse_float_negative(self):
        source = """
        func main() -> Float
          let result: Option[Float] = parse_float("-2.5")
          match result with
            | Some(v) -> return v
            | None -> return 0.0
          end match
        end func main
        """
        assert run_geno(source) == -2.5

    def test_parse_float_rejects_scientific(self):
        source = """
        func main() -> Bool
          let result: Option[Float] = parse_float("1e5")
          return is_none(result)
        end func main
        """
        assert run_geno(source) == True

    def test_parse_float_rejects_overflow(self):
        huge = "9" * 400
        source = f"""
        func main() -> Bool
          let result: Option[Float] = parse_float("{huge}")
          return is_none(result)
        end func main
        """
        assert run_geno(source) == True


# =============================================================================
# Recursion Limit
# =============================================================================


class TestRecursionLimit:
    """Test that the recursion limit has been increased."""

    def test_recursion_depth_200(self):
        """Recursion depth of 200 should work (was blocked by old limit of 100)."""
        source = """
        func count_down(n: Int) -> Int
          example 0 -> 0
          if n == 0 then
            return 0
          end if
          return count_down(n - 1)
        end func count_down

        func main() -> Int
          return count_down(200)
        end func main
        """
        assert run_geno(source) == 0


# =============================================================================
# Range Builtin
# =============================================================================


class TestRangeBuiltin:
    """Tests for the range(start, end) builtin."""

    def test_range_basic(self):
        source = """
        func main() -> List[Int]
          return range(0, 5)
        end func main
        """
        assert run_geno(source) == [0, 1, 2, 3, 4]

    def test_range_start_equals_end(self):
        source = """
        func main() -> List[Int]
          return range(3, 3)
        end func main
        """
        assert run_geno(source) == []

    def test_range_start_greater_than_end(self):
        source = """
        func main() -> List[Int]
          return range(5, 2)
        end func main
        """
        assert run_geno(source) == []

    def test_range_negative_start(self):
        source = """
        func main() -> List[Int]
          return range(0 - 2, 3)
        end func main
        """
        assert run_geno(source) == [-2, -1, 0, 1, 2]

    def test_range_single_element(self):
        source = """
        func main() -> List[Int]
          return range(7, 8)
        end func main
        """
        assert run_geno(source) == [7]

    def test_range_typecheck(self):
        source = """
        func main() -> List[Int]
          return range(0, 10)
        end func main
        """
        typecheck(source)

    def test_range_compiled_python(self):
        source = """
        func main() -> List[Int]
          return range(0, 5)
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == [0, 1, 2, 3, 4]

    def test_range_in_js(self):
        source = """
        func main() -> List[Int]
          return range(0, 5)
        end func main
        """
        js = compile_js(source)
        assert "range_(" in js

    def test_range_used_in_pipeline(self):
        source = """
        func main() -> Int
          return length(range(0, 10))
        end func main
        """
        assert run_geno(source) == 10

    def test_range_with_filter(self):
        source = """
        func main() -> List[Int]
          return filter(range(0, 10), fn(x: Int) -> x % 2 == 0)
        end func main
        """
        assert run_geno(source) == [0, 2, 4, 6, 8]


# =============================================================================
# Sort / Sort_by Builtins
# =============================================================================


class TestSortBuiltins:
    """Tests for sort(list, comparator) and sort_by(list, key_fn) builtins."""

    def test_sort_ascending(self):
        source = """
        func main() -> List[Int]
          let nums: List[Int] = [3, 1, 2]
          return sort(nums, fn(a: Int, b: Int) -> a - b)
        end func main
        """
        assert run_geno(source) == [1, 2, 3]

    def test_sort_descending(self):
        source = """
        func main() -> List[Int]
          let nums: List[Int] = [3, 1, 2]
          return sort(nums, fn(a: Int, b: Int) -> b - a)
        end func main
        """
        assert run_geno(source) == [3, 2, 1]

    def test_sort_empty_list(self):
        source = """
        func main() -> List[Int]
          let nums: List[Int] = []
          return sort(nums, fn(a: Int, b: Int) -> a - b)
        end func main
        """
        assert run_geno(source) == []

    def test_sort_single_element(self):
        source = """
        func main() -> List[Int]
          return sort([42], fn(a: Int, b: Int) -> a - b)
        end func main
        """
        assert run_geno(source) == [42]

    def test_sort_by_key(self):
        source = """
        func main() -> List[String]
          let words: List[String] = ["bbb", "a", "cccc", "dd"]
          return sort_by(words, fn(s: String) -> length(s))
        end func main
        """
        assert run_geno(source) == ["a", "dd", "bbb", "cccc"]

    def test_sort_by_negative_key(self):
        source = """
        func main() -> List[Int]
          let nums: List[Int] = [3, 1, 4, 1, 5]
          return sort_by(nums, fn(x: Int) -> 0 - x)
        end func main
        """
        assert run_geno(source) == [5, 4, 3, 1, 1]

    def test_sort_typecheck(self):
        source = """
        func main() -> List[Int]
          return sort([3, 1, 2], fn(a: Int, b: Int) -> a - b)
        end func main
        """
        typecheck(source)

    def test_sort_by_typecheck(self):
        source = """
        func main() -> List[String]
          return sort_by(["b", "a"], fn(s: String) -> length(s))
        end func main
        """
        typecheck(source)

    def test_sort_compiled_python(self):
        source = """
        func main() -> List[Int]
          return sort([3, 1, 2], fn(a: Int, b: Int) -> a - b)
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == [1, 2, 3]

    def test_sort_by_compiled_python(self):
        source = """
        func main() -> List[String]
          let words: List[String] = ["bbb", "a", "dd"]
          return sort_by(words, fn(s: String) -> length(s))
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == ["a", "dd", "bbb"]

    def test_sort_in_js(self):
        source = """
        func main() -> List[Int]
          return sort([3, 1, 2], fn(a: Int, b: Int) -> a - b)
        end func main
        """
        js = compile_js(source)
        assert "sort(" in js

    def test_sort_by_in_js(self):
        source = """
        func main() -> List[String]
          return sort_by(["b", "a"], fn(s: String) -> length(s))
        end func main
        """
        js = compile_js(source)
        assert "sort_by(" in js


# =============================================================================
# Set[T] Builtins
# =============================================================================


class TestSetBuiltins:
    """Tests for Set[T] type and builtins."""

    def test_set_new_and_add(self):
        source = """
        func main() -> Bool
          let s: Set[Int] = set_new()
          set_add(s, 42)
          set_add(s, 10)
          return set_contains(s, 42) and set_size(s) == 2
        end func main
        """
        assert run_geno(source) == True

    def test_set_contains_false(self):
        source = """
        func main() -> Bool
          let s: Set[Int] = set_new()
          set_add(s, 1)
          return set_contains(s, 99)
        end func main
        """
        assert run_geno(source) == False

    def test_set_remove(self):
        source = """
        func main() -> Int
          let s: Set[Int] = set_new()
          set_add(s, 1)
          set_add(s, 2)
          set_add(s, 3)
          set_remove(s, 2)
          return set_size(s)
        end func main
        """
        assert run_geno(source) == 2

    def test_set_from_list(self):
        source = """
        func main() -> Int
          let s: Set[Int] = set_from_list([1, 2, 2, 3, 3, 3])
          return set_size(s)
        end func main
        """
        assert run_geno(source) == 3

    def test_set_to_list(self):
        source = """
        func main() -> List[Int]
          let s: Set[Int] = set_from_list([3, 1, 2])
          return set_to_list(s)
        end func main
        """
        result = run_geno(source)
        assert sorted(result) == [1, 2, 3]

    def test_set_union(self):
        source = """
        func main() -> Int
          let a: Set[Int] = set_from_list([1, 2, 3])
          let b: Set[Int] = set_from_list([3, 4, 5])
          let c: Set[Int] = set_union(a, b)
          return set_size(c)
        end func main
        """
        assert run_geno(source) == 5

    def test_set_intersection(self):
        source = """
        func main() -> Int
          let a: Set[Int] = set_from_list([1, 2, 3, 4])
          let b: Set[Int] = set_from_list([3, 4, 5, 6])
          let c: Set[Int] = set_intersection(a, b)
          return set_size(c)
        end func main
        """
        assert run_geno(source) == 2

    def test_set_deduplicates(self):
        source = """
        func main() -> Int
          let s: Set[Int] = set_new()
          set_add(s, 42)
          set_add(s, 42)
          set_add(s, 42)
          return set_size(s)
        end func main
        """
        assert run_geno(source) == 1

    def test_set_typecheck(self):
        source = """
        func main() -> Bool
          let s: Set[String] = set_from_list(["a", "b"])
          return set_contains(s, "a")
        end func main
        """
        typecheck(source)

    def test_set_compiled_python(self):
        source = """
        func main() -> Bool
          let s: Set[Int] = set_new()
          set_add(s, 1)
          set_add(s, 2)
          return set_contains(s, 1) and set_size(s) == 2
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == True

    def test_set_union_type_mismatch(self):
        """set_union(Set[Int], Set[String]) should be a type error."""
        source = """
        func main() -> Unit
          let a: Set[Int] = set_new()
          let b: Set[String] = set_new()
          let c: Set[Int] = set_union(a, b)
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_set_in_js(self):
        source = """
        func main() -> Bool
          let s: Set[Int] = set_from_list([1, 2, 3])
          return set_contains(s, 2)
        end func main
        """
        js = compile_js(source)
        assert "set_from_list(" in js
        assert "set_contains(" in js

    def test_set_add_unhashable_reports_type_error(self):
        source = """
        func main() -> Unit
          let s: Set[List[Int]] = set_new()
          set_add(s, [1, 2, 3])
        end func main
        """
        with pytest.raises(TypeError, match="Set element type must be hashable"):
            typecheck(source)

    def test_set_from_list_unhashable_reports_type_error(self):
        source = """
        func main() -> Set[List[Int]]
          return set_from_list([[1], [2]])
        end func main
        """
        with pytest.raises(TypeError, match="Set element type must be hashable"):
            typecheck(source)


# =============================================================================
# Type Aliases
# =============================================================================


class TestTypeAliases:
    """Tests for type alias definitions."""

    def test_simple_list_alias(self):
        source = """
        type Numbers = List[Int]

        func main() -> Int
          let ns: Numbers = [1, 2, 3]
          return length(ns)
        end func main
        """
        assert run_geno(source) == 3

    def test_nested_list_alias(self):
        source = """
        type Matrix = List[List[Int]]

        func main() -> Int
          let m: Matrix = [[10, 20], [30, 40]]
          return m[1][0]
        end func main
        """
        assert run_geno(source) == 30

    def test_option_alias(self):
        source = """
        type MaybeInt = Option[Int]

        func main() -> Int
          let r: MaybeInt = Some(42)
          match r with
            | Some(v) -> return v
            | None -> return 0
          end match
        end func main
        """
        assert run_geno(source) == 42

    def test_function_type_alias(self):
        source = """
        type Predicate = (Int) -> Bool

        func main() -> Bool
          let is_pos: Predicate = fn(x: Int) -> x > 0
          return is_pos(42)
        end func main
        """
        assert run_geno(source) is True

    def test_set_alias(self):
        source = """
        type IntSet = Set[Int]

        func main() -> Int
          let s: IntSet = set_from_list([1, 2, 3, 2, 1])
          return set_size(s)
        end func main
        """
        assert run_geno(source) == 3

    def test_generic_alias(self):
        source = """
        type Pair[T] = List[T]

        func main() -> Int
          let p: Pair[Int] = [10, 20]
          return head(p)
        end func main
        """
        assert run_geno(source) == 10

    def test_generic_alias_with_string(self):
        source = """
        type Pair[T] = List[T]

        func main() -> String
          let p: Pair[String] = ["hello", "world"]
          return head(p)
        end func main
        """
        assert run_geno(source) == "hello"

    def test_alias_in_function_params(self):
        source = """
        type Numbers = List[Int]

        func total(ns: Numbers) -> Int
          example [1, 2] -> 3
          return fold(list: ns, initial: 0, reducer: fn(a: Int, b: Int) -> a + b)
        end func total

        func main() -> Int
          let ns: Numbers = [10, 20, 30]
          return total(ns)
        end func main
        """
        assert run_geno(source) == 60

    def test_alias_in_return_type(self):
        source = """
        type Numbers = List[Int]

        func make_nums() -> Numbers
          example () -> [1, 2, 3]
          return [1, 2, 3]
        end func make_nums

        func main() -> Int
          let ns: Numbers = make_nums()
          return length(ns)
        end func main
        """
        assert run_geno(source) == 3

    def test_alias_coexists_with_adt(self):
        """Type aliases and ADTs can coexist in the same program."""
        source = """
        type Color = Red | Green | Blue

        type Colors = List[Color]

        func main() -> Int
          let cs: Colors = [Red, Green, Blue]
          return length(cs)
        end func main
        """
        assert run_geno(source) == 3

    def test_alias_typecheck_mismatch(self):
        """Using wrong type with an alias should be caught."""
        source = """
        type Numbers = List[Int]

        func main() -> Int
          let ns: Numbers = "not a list"
          return 0
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_alias_compiled_python(self):
        source = """
        type Numbers = List[Int]

        func main() -> Int
          let ns: Numbers = [1, 2, 3]
          return length(ns)
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == 3

    def test_alias_compiled_js(self):
        source = """
        type Numbers = List[Int]

        func main() -> Int
          let ns: Numbers = [1, 2, 3]
          return length(ns)
        end func main
        """
        js = compile_js(source)
        assert "function main()" in js

    def test_multiple_aliases(self):
        source = """
        type Name = String
        type Names = List[String]

        func main() -> Int
          let ns: Names = ["Alice", "Bob"]
          return length(ns)
        end func main
        """
        # Note: 'type Name = String' is parsed as ADT (bare name, no []),
        # but 'type Names = List[String]' is a proper alias
        assert run_geno(source) == 2

    def test_recursive_alias_is_type_error(self):
        """Recursive type aliases should produce a clear error."""
        source = """
        type Loop = List[Loop]

        func main() -> Int
          let x: Loop = []
          return 0
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_mutually_recursive_alias_is_type_error(self):
        """Mutually recursive aliases should produce a clear error."""
        source = """
        type A = List[B]
        type B = List[A]

        func main() -> Int
          let x: A = []
          return 0
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)


# =============================================================================
# Tuple Destructuring
# =============================================================================


class TestTupleDestructuring:
    """Tests for tuple destructuring in let/var bindings."""

    def test_let_two_elements(self):
        source = """
        func main() -> Int
          let (x, y): (Int, Int) = (1, 2)
          return x + y
        end func main
        """
        assert run_geno(source) == 3

    def test_let_three_elements(self):
        source = """
        func main() -> Int
          let (a, b, c): (Int, Int, Int) = (10, 20, 30)
          return a + b + c
        end func main
        """
        assert run_geno(source) == 60

    def test_let_mixed_types(self):
        source = """
        func main() -> String
          let (name, age): (String, Int) = ("Alice", 30)
          return name
        end func main
        """
        assert run_geno(source) == "Alice"

    def test_var_destructure_mutable(self):
        source = """
        func main() -> Int
          var (x, y): (Int, Int) = (1, 2)
          x = 10
          y = 20
          return x + y
        end func main
        """
        assert run_geno(source) == 30

    def test_let_destructure_immutable(self):
        """let destructuring should not allow mutation."""
        source = """
        func main() -> Int
          let (x, y): (Int, Int) = (1, 2)
          x = 10
          return x
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_destructure_from_function(self):
        source = """
        func pair() -> (Int, Int)
          example () -> (1, 2)
          return (1, 2)
        end func pair

        func main() -> Int
          let (a, b): (Int, Int) = pair()
          return a + b
        end func main
        """
        assert run_geno(source) == 3

    def test_size_mismatch_too_few_names(self):
        """Destructuring 3-tuple into 2 names should fail."""
        source = """
        func main() -> Int
          let (x, y): (Int, Int, Int) = (1, 2, 3)
          return x
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_size_mismatch_too_many_names(self):
        """Destructuring 2-tuple into 3 names should fail."""
        source = """
        func main() -> Int
          let (x, y, z): (Int, Int) = (1, 2)
          return x
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_type_mismatch(self):
        """Value type must match declared tuple type."""
        source = """
        func main() -> Int
          let (x, y): (Int, Int) = (1, "two")
          return x
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_non_tuple_type_error(self):
        """Destructuring requires a tuple type annotation."""
        source = """
        func main() -> Int
          let (x, y): Int = 42
          return x
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_tuple_type_annotation_both_syntaxes(self):
        """Both (Int, Int) and Tuple[Int, Int] work as type annotations."""
        source = """
        func main() -> Int
          let (a, b): Tuple[Int, Int] = (1, 2)
          return a + b
        end func main
        """
        assert run_geno(source) == 3

    def test_compiled_python(self):
        source = """
        func main() -> Int
          let (x, y): (Int, Int) = (3, 4)
          return x + y
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == 7

    def test_compiled_js(self):
        source = """
        func main() -> Int
          let (x, y): (Int, Int) = (3, 4)
          return x + y
        end func main
        """
        js = compile_js(source)
        assert "[x, y]" in js

    def test_nested_in_function(self):
        source = """
        func main() -> Int
          let pair: (Int, Int) = (10, 20)
          let (a, b): (Int, Int) = pair
          return a + b
        end func main
        """
        assert run_geno(source) == 30


# =============================================================================
# F-String Interpolation
# =============================================================================


class TestFStrings:
    """Tests for f-string interpolation."""

    def test_basic_fstring(self):
        source = """
        func main() -> String
          let name: String = "Alice"
          return f"Hello, {name}!"
        end func main
        """
        assert run_geno(source) == "Hello, Alice!"

    def test_fstring_with_arithmetic(self):
        source = """
        func main() -> String
          let x: Int = 5
          return f"x + 1 = {x + 1}"
        end func main
        """
        assert run_geno(source) == "x + 1 = 6"

    def test_fstring_multiple_exprs(self):
        source = """
        func main() -> String
          let name: String = "Alice"
          let age: Int = 30
          return f"Hello, {name}! You are {age}."
        end func main
        """
        assert run_geno(source) == "Hello, Alice! You are 30."

    def test_fstring_no_interpolation(self):
        source = """
        func main() -> String
          return f"just a plain string"
        end func main
        """
        assert run_geno(source) == "just a plain string"

    def test_fstring_only_expr(self):
        source = """
        func main() -> String
          let x: Int = 42
          return f"{x}"
        end func main
        """
        assert run_geno(source) == "42"

    def test_fstring_adjacent_exprs(self):
        source = """
        func main() -> String
          let a: Int = 1
          let b: Int = 2
          return f"{a}{b}"
        end func main
        """
        assert run_geno(source) == "12"

    def test_fstring_with_bool(self):
        source = """
        func main() -> String
          let flag: Bool = true
          return f"flag is {flag}"
        end func main
        """
        assert run_geno(source) == "flag is true"

    def test_fstring_uses_geno_format_for_nested_values(self):
        source = """
        func main() -> String
          return f"values: {[true, false]}"
        end func main
        """
        assert run_geno(source) == "values: [true, false]"

    def test_fstring_with_float(self):
        source = """
        func main() -> String
          let pi: Float = 3.14
          return f"pi = {pi}"
        end func main
        """
        assert run_geno(source) == "pi = 3.14"

    def test_fstring_with_function_call(self):
        source = """
        func double(n: Int) -> Int
          example (3) -> 6
          return n * 2
        end func double

        func main() -> String
          return f"double(5) = {double(n: 5)}"
        end func main
        """
        assert run_geno(source) == "double(5) = 10"

    def test_fstring_empty(self):
        source = """
        func main() -> String
          return f""
        end func main
        """
        assert run_geno(source) == ""

    def test_fstring_escape_sequences(self):
        source = """
        func main() -> String
          let x: Int = 1
          return f"a\\tb\\n{x}"
        end func main
        """
        assert run_geno(source) == "a\tb\n1"

    def test_fstring_type_error_undefined_var(self):
        source = """
        func main() -> String
          return f"hello {undefined_var}"
        end func main
        """
        with pytest.raises(Exception):
            run_geno(source)

    def test_fstring_parse_error_empty_braces(self):
        source = """
        func main() -> String
          return f"hello {}"
        end func main
        """
        with pytest.raises(Exception):
            run_geno(source)

    def test_fstring_parse_error_nested_braces(self):
        source = """
        func main() -> String
          return f"hello { {1} }"
        end func main
        """
        with pytest.raises(Exception):
            run_geno(source)

    def test_fstring_compiled_python(self):
        source = """
        func main() -> String
          let name: String = "Alice"
          let age: Int = 30
          return f"Hello, {name}! You are {age}."
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == "Hello, Alice! You are 30."

    def test_fstring_compiled_python_uses_geno_format(self):
        source = """
        func main() -> String
          let flag: Bool = true
          return f"flag is {flag}"
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == "flag is true"

    def test_fstring_compiled_js(self):
        source = """
        func main() -> String
          let name: String = "Alice"
          let age: Int = 30
          return f"Hello, {name}! You are {age}."
        end func main
        """
        js = compile_js(source)
        assert "to_string" in js
        assert "`" in js  # template literal

    def test_fstring_with_list_expr(self):
        source = """
        func main() -> String
          let items: List[Int] = [1, 2, 3]
          return f"count = {length(list: items)}"
        end func main
        """
        assert run_geno(source) == "count = 3"

    def test_f_as_identifier(self):
        """Ensure 'f' alone is still a valid identifier."""
        source = """
        func main() -> Int
          let f: Int = 42
          return f
        end func main
        """
        assert run_geno(source) == 42


# =============================================================================
# Multi-Line Lambdas
# =============================================================================


class TestMultiLineLambdas:
    """Tests for block lambdas: fn(params) do ... end fn."""

    def test_basic_block_lambda(self):
        source = """
        func main() -> Int
          example () -> 6
          let f: (Int, Int) -> Int = fn(a: Int, b: Int) do
            let sum: Int = a + b
            return sum * 2
          end fn
          return f(1, 2)
        end func main
        """
        assert run_geno(source) == 6

    def test_block_lambda_with_if(self):
        source = """
        func main() -> Int
          example () -> 10
          let abs_val: (Int) -> Int = fn(x: Int) do
            if x < 0 then
              return 0 - x
            else
              return x
            end if
          end fn
          return abs_val(-10)
        end func main
        """
        assert run_geno(source) == 10

    def test_block_lambda_passed_to_map(self):
        source = """
        func main() -> List[Int]
          example () -> [1, 4, 9]
          let items: List[Int] = [1, 2, 3]
          return map(items, fn(x: Int) do
            let squared: Int = x * x
            return squared
          end fn)
        end func main
        """
        assert run_geno(source) == [1, 4, 9]

    def test_block_lambda_passed_to_filter(self):
        source = """
        func main() -> List[Int]
          example () -> [2, 4]
          let items: List[Int] = [1, 2, 3, 4, 5]
          return filter(items, fn(x: Int) do
            let even: Bool = x % 2 == 0
            return even
          end fn)
        end func main
        """
        assert run_geno(source) == [2, 4]

    def test_block_lambda_multiple_statements(self):
        source = """
        func main() -> Int
          example () -> 15
          let compute: (Int) -> Int = fn(n: Int) do
            let a: Int = n + 1
            let b: Int = a * 2
            let c: Int = b + 3
            return c
          end fn
          return compute(5)
        end func main
        """
        assert run_geno(source) == 15

    def test_block_lambda_with_while(self):
        source = """
        func main() -> Int
          example () -> 10
          let sum_to: (Int) -> Int with mutation = fn(n: Int) do
            var total: Int = 0
            var i: Int = 1
            while i <= n do
              total = total + i
              i = i + 1
            end while
            return total
          end fn
          return sum_to(4)
        end func main
        """
        assert run_geno(source) == 10

    def test_block_lambda_with_for(self):
        source = """
        func main() -> Int
          example () -> 6
          let sum_list: (List[Int]) -> Int with mutation = fn(items: List[Int]) do
            var total: Int = 0
            for item: Int in items do
              total = total + item
            end for
            return total
          end fn
          return sum_list([1, 2, 3])
        end func main
        """
        assert run_geno(source) == 6

    def test_expression_lambda_still_works(self):
        source = """
        func main() -> Int
          example () -> 10
          let double: (Int) -> Int = fn(x: Int) -> x * 2
          return double(5)
        end func main
        """
        assert run_geno(source) == 10

    def test_block_lambda_compiled_python(self):
        source = """
        func main() -> Int
          let f: (Int, Int) -> Int = fn(a: Int, b: Int) do
            let sum: Int = a + b
            return sum * 2
          end fn
          return f(1, 2)
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == 6

    def test_block_lambda_compiled_js(self):
        source = """
        func main() -> Int
          let f: (Int, Int) -> Int = fn(a: Int, b: Int) do
            let sum: Int = a + b
            return sum * 2
          end fn
          return f(1, 2)
        end func main
        """
        js = compile_js(source)
        assert "=>" in js

    def test_nested_block_lambdas(self):
        source = """
        func main() -> Int
          example () -> 12
          let outer: (Int) -> Int = fn(x: Int) do
            let inner: (Int) -> Int = fn(y: Int) do
              return y * 2
            end fn
            return inner(x) + x
          end fn
          return outer(4)
        end func main
        """
        assert run_geno(source) == 12

    def test_block_lambda_captures_closure(self):
        source = """
        func main() -> Int
          example () -> 15
          let base: Int = 10
          let add_base: (Int) -> Int = fn(x: Int) do
            return x + base
          end fn
          return add_base(5)
        end func main
        """
        assert run_geno(source) == 15


# =============================================================================
# Try/Catch Error Handling
# =============================================================================


class TestTryCatch:
    """Tests for try/catch error handling."""

    def test_catch_division_by_zero(self):
        source = """
        func main() -> String
          example () -> "caught"
          try
            let x: Int = 10 / 0
            return "ok"
          catch e: String
            return "caught"
          end try
        end func main
        """
        assert run_geno(source) == "caught"

    def test_catch_error_message(self):
        source = """
        func main() -> String
          example () -> "error: Division by zero: cannot compute 10 / 0"
          try
            let x: Int = 10 / 0
            return "ok"
          catch e: String
            return f"error: {e}"
          end try
        end func main
        """
        result = run_geno(source)
        assert "ivision by zero" in result

    def test_no_error_skips_catch(self):
        source = """
        func main() -> String
          example () -> "ok"
          try
            let x: Int = 10 / 2
            return "ok"
          catch e: String
            return "caught"
          end try
        end func main
        """
        assert run_geno(source) == "ok"

    def test_return_from_catch(self):
        source = """
        func main() -> Int
          example () -> 42
          try
            let x: Int = 1 / 0
            return 0
          catch e: String
            return 42
          end try
        end func main
        """
        assert run_geno(source) == 42

    def test_catch_with_local_bindings(self):
        source = """
        func main() -> String
          example () -> "error happened"
          try
            let x: Int = 10 / 0
            return "ok"
          catch e: String
            let msg: String = "error happened"
            return msg
          end try
        end func main
        """
        assert run_geno(source) == "error happened"

    def test_try_catch_in_loop(self):
        source = """
        func main() -> Int
          example () -> 2
          var count: Int = 0
          for i: Int in [0, 1, 0] do
            try
              let x: Int = 10 / i
              count = count
            catch e: String
              count = count + 1
            end try
          end for
          return count
        end func main
        """
        assert run_geno(source) == 2

    def test_nested_try_catch(self):
        source = """
        func main() -> String
          example () -> "inner caught"
          try
            try
              let x: Int = 1 / 0
              return "ok"
            catch e: String
              return "inner caught"
            end try
          catch e: String
            return "outer caught"
          end try
        end func main
        """
        assert run_geno(source) == "inner caught"

    def test_uncaught_error_propagates(self):
        source = """
        func fail() -> Int
          example (1) -> 1
          return 1 / 0
        end func fail

        func main() -> Int
          example () -> 0
          return fail()
        end func main
        """
        with pytest.raises(Exception):
            run_geno(source)

    def test_catch_type_must_be_string(self):
        source = """
        func main() -> Int
          try
            return 1
          catch e: Int
            return 0
          end try
        end func main
        """
        with pytest.raises(Exception):
            run_geno(source)

    def test_compiled_python(self):
        source = """
        func main() -> String
          try
            let x: Int = 10 / 0
            return "ok"
          catch e: String
            return "caught"
          end try
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == "caught"

    def test_compiled_js(self):
        source = """
        func main() -> String
          try
            let x: Int = 10 / 0
            return "ok"
          catch e: String
            return "caught"
          end try
        end func main
        """
        js = compile_js(source)
        assert "try {" in js
        assert "catch" in js

    def test_try_no_error_compiled_python(self):
        source = """
        func main() -> Int
          try
            let x: Int = 10 / 2
            return x
          catch e: String
            return 0
          end try
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == 5


# =============================================================================
# Propagate (?) Operator
# =============================================================================


class TestPropagateOperator:
    """Tests for the ? operator on Option and Result types."""

    def test_option_some_propagates_value(self):
        source = """
        func get_value() -> Option[Int]
            example () -> Some(42)
            return Some(42)
        end func

        func main() -> Option[Int]
            let x: Int = get_value()?
            return Some(x + 1)
        end func main
        """
        result = run_geno(source)
        assert result == {"_constructor": "Some", "fields": {"value": 43}}

    def test_option_none_propagates_early(self):
        source = """
        func get_value() -> Option[Int]
            example () -> None
            return None
        end func

        func main() -> Option[Int]
            let x: Int = get_value()?
            return Some(x + 1)
        end func main
        """
        result = run_geno(source)
        assert result == {"_constructor": "None", "fields": {}}

    def test_result_ok_propagates_value(self):
        source = """
        func safe_div(a: Int, b: Int) -> Result[Int, String]
            example (10, 2) -> Ok(5)
            if b == 0 then
                return Err("division by zero")
            end if
            return Ok(a / b)
        end func

        func main() -> Result[Int, String]
            let x: Int = safe_div(10, 2)?
            return Ok(x + 1)
        end func main
        """
        result = run_geno(source)
        assert result == {"_constructor": "Ok", "fields": {"value": 6}}

    def test_result_err_propagates_early(self):
        source = """
        func safe_div(a: Int, b: Int) -> Result[Int, String]
            example (10, 0) -> Err("division by zero")
            if b == 0 then
                return Err("division by zero")
            end if
            return Ok(a / b)
        end func

        func main() -> Result[Int, String]
            let x: Int = safe_div(10, 0)?
            return Ok(x + 1)
        end func main
        """
        result = run_geno(source)
        assert result == {
            "_constructor": "Err",
            "fields": {"error": "division by zero"},
        }

    def test_chained_propagation(self):
        source = """
        func first() -> Option[Int]
            example () -> Some(10)
            return Some(10)
        end func

        func second() -> Option[Int]
            example () -> Some(20)
            return Some(20)
        end func

        func main() -> Option[Int]
            let a: Int = first()?
            let b: Int = second()?
            return Some(a + b)
        end func main
        """
        result = run_geno(source)
        assert result == {"_constructor": "Some", "fields": {"value": 30}}

    def test_chained_propagation_fails_on_second(self):
        source = """
        func first() -> Option[Int]
            example () -> Some(10)
            return Some(10)
        end func

        func second() -> Option[Int]
            example () -> None
            return None
        end func

        func main() -> Option[Int]
            let a: Int = first()?
            let b: Int = second()?
            return Some(a + b)
        end func main
        """
        result = run_geno(source)
        assert result == {"_constructor": "None", "fields": {}}

    def test_propagate_in_expression(self):
        source = """
        func get_val() -> Option[Int]
            example () -> Some(5)
            return Some(5)
        end func

        func main() -> Option[Int]
            return Some(get_val()? * 2)
        end func main
        """
        result = run_geno(source)
        assert result == {"_constructor": "Some", "fields": {"value": 10}}

    def test_typechecker_rejects_non_option_result(self):
        source = """
        func get_val() -> Int
            example () -> 5
            return 5
        end func

        func main() -> Int
            let x: Int = get_val()?
            return x
        end func main
        """
        with pytest.raises(TypeError, match="requires Option or Result"):
            typecheck(source)

    def test_typechecker_rejects_option_in_non_option_func(self):
        source = """
        func get_val() -> Option[Int]
            example () -> Some(5)
            return Some(5)
        end func

        func main() -> Int
            let x: Int = get_val()?
            return x
        end func main
        """
        with pytest.raises(
            TypeError, match="requires enclosing function to return Option"
        ):
            typecheck(source)

    def test_typechecker_rejects_result_in_non_result_func(self):
        source = """
        func get_val() -> Result[Int, String]
            example () -> Ok(5)
            return Ok(5)
        end func

        func main() -> Int
            let x: Int = get_val()?
            return x
        end func main
        """
        with pytest.raises(
            TypeError, match="requires enclosing function to return Result"
        ):
            typecheck(source)

    def test_compiled_python_option_some(self):
        source = """
        func get_value() -> Option[Int]
            example () -> Some(42)
            return Some(42)
        end func

        func main() -> Option[Int]
            let x: Int = get_value()?
            return Some(x + 1)
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        result = globals_dict["main"]()
        assert type(result).__name__ == "Some"
        assert result.value == 43

    def test_compiled_python_option_none(self):
        source = """
        func get_value() -> Option[Int]
            example () -> None
            return None
        end func

        func main() -> Option[Int]
            let x: Int = get_value()?
            return Some(x + 1)
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        result = globals_dict["main"]()
        assert type(result).__name__ == "_None"

    def test_compiled_python_result_ok(self):
        source = """
        func safe_div(a: Int, b: Int) -> Result[Int, String]
            example (10, 2) -> Ok(5)
            if b == 0 then
                return Err("division by zero")
            end if
            return Ok(a / b)
        end func

        func main() -> Result[Int, String]
            let x: Int = safe_div(10, 2)?
            return Ok(x + 1)
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        result = globals_dict["main"]()
        assert type(result).__name__ == "Ok"
        assert result.value == 6

    def test_compiled_python_result_err(self):
        source = """
        func safe_div(a: Int, b: Int) -> Result[Int, String]
            example (10, 0) -> Err("division by zero")
            if b == 0 then
                return Err("division by zero")
            end if
            return Ok(a / b)
        end func

        func main() -> Result[Int, String]
            let x: Int = safe_div(10, 0)?
            return Ok(x + 1)
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        result = globals_dict["main"]()
        assert type(result).__name__ == "Err"
        assert result.error == "division by zero"

    def test_compiled_js_option(self):
        source = """
        func get_value() -> Option[Int]
            example () -> Some(42)
            return Some(42)
        end func

        func main() -> Option[Int]
            let x: Int = get_value()?
            return Some(x + 1)
        end func main
        """
        js = compile_js(source)
        assert "_propagate" in js

    def test_compiled_js_result(self):
        source = """
        func safe_div(a: Int, b: Int) -> Result[Int, String]
            example (10, 0) -> Err("division by zero")
            if b == 0 then
                return Err("division by zero")
            end if
            return Ok(a / b)
        end func

        func main() -> Result[Int, String]
            let x: Int = safe_div(10, 0)?
            return Ok(x + 1)
        end func main
        """
        js = compile_js(source)
        assert "_propagate" in js
        assert "_PropagateReturn" in js

    def test_propagate_through_try_catch(self):
        source = """
        func get_value() -> Option[Int]
            example () -> None
            return None
        end func

        func main() -> Option[Int]
            try
                let x: Int = get_value()?
                return Some(x)
            catch e: String
                return Some(0)
            end try
        end func main
        """
        result = run_geno(source)
        assert result == {"_constructor": "None", "fields": {}}

    def test_propagate_through_try_catch_compiled(self):
        source = """
        func get_value() -> Option[Int]
            example () -> None
            return None
        end func

        func main() -> Option[Int]
            try
                let x: Int = get_value()?
                return Some(x)
            catch e: String
                return Some(0)
            end try
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None)
        result = globals_dict["main"]()
        assert type(result).__name__ == "_None"

    def test_propagate_in_requires_returns_runtime_error(self):
        source = """
        func maybe() -> Option[Bool]
            example () -> None
            return None
        end func

        func main() -> Option[Int]
            requires maybe()?
            example () -> Some(1)
            return Some(1)
        end func main
        """
        result = run(source, config=RunConfig(timeout=5.0))
        assert not result.ok
        assert result.diagnostics
        assert "requires clause cannot use '?'" in result.diagnostics[0].message

    def test_propagate_in_ensures_returns_runtime_error(self):
        source = """
        func maybe() -> Option[Bool]
            example () -> None
            return None
        end func

        func main() -> Option[Int]
            ensures maybe()?
            example () -> Some(1)
            return Some(1)
        end func main
        """
        result = run(source, config=RunConfig(timeout=5.0))
        assert not result.ok
        assert result.diagnostics
        assert "ensures clause cannot use '?'" in result.diagnostics[0].message

    def test_typechecker_rejects_result_error_type_mismatch(self):
        source = """
        func get_val() -> Result[Int, String]
            example () -> Ok(5)
            return Ok(5)
        end func

        func main() -> Result[Int, Int]
            let x: Int = get_val()?
            return Ok(x)
        end func main
        """
        with pytest.raises(TypeError, match="Error type mismatch"):
            typecheck(source)


# =============================================================================
# Regex Builtins
# =============================================================================

_REGEX_REDOS_BYPASSES = [
    "a?" * 22 + "a" * 22 + "X",
    r"a+a{5000}b",
    "😀+😀{5000}b",
    r"a+aa{4999}b",
    "a+" + "a" * 997 + "b",
    r"a*\x61*X",
    r"a*(?:)a*X",
    r"a*()a*X",
    r"a*(a*)X",
    r"a*b*a*X",
    r"a*b?a*X",
    r"\u0061*a*X",
    r"a*(?=a*)a*X",
    r"(?i)a*A*X",
    r"[]a]*a*X",
    r"[^]]*a*X",
    r"\N{LATIN SMALL LETTER A}*a*X",
]
_REGEX_SAFETY_ERROR = (
    "advanced or encoded|nested quantifiers|adjacent repeated atoms|"
    "multiple variable quantifiers"
)
_REGEX_NONPORTABLE_PATTERNS = [
    r"\w+",
    r"\d+",
    r"\s+",
    r"\bword",
    ".",
    r"\_",
    "$",
    "a*|b",
    "{",
    "a{b",
    "a{10001}",
    "a{2,1}",
    "]",
    "a]",
    "(])",
]


class TestRegexBuiltins:
    """Tests for regex_match, regex_find_all, regex_replace builtins."""

    def test_regex_match_found(self):
        source = """
        func main() -> Option[String]
            return regex_match("[0-9]+", "abc123def")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"regex"})
        result = globals_dict["main"]()
        assert type(result).__name__ == "Some"
        assert result.value == "123"

    def test_regex_match_not_found(self):
        source = """
        func main() -> Option[String]
            return regex_match("[0-9]+", "abcdef")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"regex"})
        result = globals_dict["main"]()
        assert type(result).__name__ == "_None"

    def test_regex_find_all(self):
        source = """
        func main() -> List[String]
            return regex_find_all("[0-9]+", "abc123def456")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"regex"})
        assert globals_dict["main"]() == ["123", "456"]

    def test_regex_find_all_no_matches(self):
        source = """
        func main() -> List[String]
            return regex_find_all("[0-9]+", "abcdef")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"regex"})
        assert globals_dict["main"]() == []

    def test_regex_replace(self):
        source = """
        func main() -> String
            return regex_replace(pattern: "[0-9]+", replacement: "X", text: "a1b2c3")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"regex"})
        assert globals_dict["main"]() == "aXbXcX"

    def test_regex_replace_no_match(self):
        source = """
        func main() -> String
            return regex_replace(pattern: "[0-9]+", replacement: "X", text: "abc")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"regex"})
        assert globals_dict["main"]() == "abc"

    def test_regex_replace_does_not_use_unbounded_re_sub(self):
        from unittest import mock

        from geno import builtins as builtins_module

        with mock.patch.object(
            builtins_module.re, "sub", side_effect=AssertionError("re.sub")
        ):
            assert builtins_module.builtin_regex_replace("(a)", r"X\1", "a") == "Xa"

    def test_regex_replace_rejects_oversized_group_reference(self):
        from geno import builtins as builtins_module
        from geno.values import RuntimeError as GenoRuntimeError

        replacement = "\\" + "1" * 5000
        with pytest.raises(
            GenoRuntimeError, match="invalid replacement group reference"
        ):
            builtins_module.builtin_regex_replace("(a)", replacement, "a")

    def test_regex_match_interpreter(self):
        source = """
        func main() -> Option[String]
            return regex_match("[a-z]+", "123hello456")
        end func main
        """
        result = run_geno(source, capabilities={"regex"})
        assert result == {"_constructor": "Some", "fields": {"value": "hello"}}

    def test_regex_find_all_interpreter(self):
        source = """
        func main() -> List[String]
            return regex_find_all("[a-z]+", "1abc2def3")
        end func main
        """
        result = run_geno(source, capabilities={"regex"})
        assert result == ["abc", "def"]

    def test_regex_replace_interpreter(self):
        source = """
        func main() -> String
            return regex_replace(pattern: "[aeiou]", replacement: "*", text: "hello")
        end func main
        """
        result = run_geno(source, capabilities={"regex"})
        assert result == "h*ll*"

    def test_regex_compiled_js(self):
        source = """
        func main() -> String
            return regex_replace(pattern: "[0-9]+", replacement: "N", text: "a1b2")
        end func main
        """
        js = compile_js(source)
        assert "regex_replace" in js

    def test_typechecker_regex_match_type(self):
        source = """
        func main() -> Option[String]
            return regex_match("[0-9]+", "abc123")
        end func main
        """
        typecheck(source)

    def test_typechecker_regex_find_all_type(self):
        source = """
        func main() -> List[String]
            return regex_find_all("[0-9]+", "abc123")
        end func main
        """
        typecheck(source)

    def test_regex_find_all_with_capture_groups(self):
        source = """
        func main() -> List[String]
            return regex_find_all("([a-z]+)[0-9][0-9][0-9]", "abc123def456")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"regex"})
        assert globals_dict["main"]() == ["abc123", "def456"]

    def test_regex_match_rejects_nested_quantifiers(self):
        from geno.builtins import builtin_regex_match

        with pytest.raises(Exception, match="nested quantifiers"):
            builtin_regex_match("(a+)+$", "a" * 24 + "!")

    def test_regex_match_rejects_identical_alternation_with_quantifier(self):
        from geno.builtins import builtin_regex_match

        with pytest.raises(Exception, match="overlapping alternation"):
            builtin_regex_match("(a|a)+$", "aaaa")

    def test_regex_match_rejects_prefix_overlapping_alternation(self):
        from geno.builtins import builtin_regex_match

        with pytest.raises(Exception, match="overlapping alternation"):
            builtin_regex_match("(a|aa)+$", "a" * 24 + "!")

    @pytest.mark.parametrize("pattern", ["(a|[a])+$", "(?:a|aa)+$"])
    def test_regex_match_rejects_semantically_overlapping_alternation(self, pattern):
        from geno.builtins import builtin_regex_match

        with pytest.raises(
            Exception, match=r"overlapping alternation|advanced or encoded"
        ):
            builtin_regex_match(pattern, "a" * 24 + "!")

    def test_regex_match_rejects_sequential_quantified_literals(self):
        from geno.builtins import builtin_regex_match

        with pytest.raises(Exception, match="adjacent repeated atoms"):
            builtin_regex_match("a*a*a*a*b", "aaaa!")

    def test_regex_match_rejects_sequential_quantified_char_classes(self):
        from geno.builtins import builtin_regex_match

        with pytest.raises(Exception, match="adjacent repeated atoms"):
            builtin_regex_match("[a]*[a]*b", "aaaa!")

    @pytest.mark.parametrize(
        "pattern",
        ["a*.*X", "(?P<x>a+)(?P=x)+X"],
    )
    def test_regex_match_rejects_remaining_redos_patterns(self, pattern):
        from geno.builtins import builtin_regex_match

        with pytest.raises(
            Exception,
            match=r"backreferences|adjacent repeated atoms|advanced or encoded",
        ):
            builtin_regex_match(pattern, "a" * 100 + "!")

    def test_regex_match_rejects_multiple_variable_quantifiers(self):
        from geno.builtins import builtin_regex_match

        with pytest.raises(Exception, match="multiple variable quantifiers"):
            builtin_regex_match("a*b*c", "aaabbbc")

    @pytest.mark.parametrize("pattern", _REGEX_REDOS_BYPASSES)
    def test_regex_match_rejects_portable_subset_bypasses(self, pattern):
        from geno.builtins import builtin_regex_match

        with pytest.raises(Exception, match=_REGEX_SAFETY_ERROR):
            builtin_regex_match(pattern, "a" * 100 + "!")

    @pytest.mark.parametrize("pattern", _REGEX_NONPORTABLE_PATTERNS)
    def test_regex_match_rejects_cross_backend_semantic_differences(self, pattern):
        from geno.builtins import builtin_regex_match

        with pytest.raises(Exception, match="advanced or encoded"):
            builtin_regex_match(pattern, "é١\r_word")

    def test_regex_match_allows_escaped_literal_group_text(self):
        from geno.builtins import builtin_regex_match

        result = builtin_regex_match(r"\(a\|a\)\+", "prefix (a|a)+ suffix")
        assert result.constructor == "Some"
        assert result.fields["value"] == "(a|a)+"

    def test_compiled_regex_match_rejects_prefix_overlapping_alternation(self):
        from geno._runtime_support import regex_match

        with _runtime_caps("regex"):
            with pytest.raises(Exception, match="overlapping alternation"):
                regex_match("(a|aa)+$", "a" * 24 + "!")


class TestCompiledRegexValidation:
    """Compiled runtime must enforce the same ReDoS mitigations as the interpreter."""

    @pytest.fixture(autouse=True)
    def _grant_regex_capability(self):
        with _runtime_caps("regex"):
            yield

    def test_compiled_regex_rejects_nested_quantifiers(self):
        from geno._runtime_support import regex_match as compiled_regex_match

        with pytest.raises(Exception, match="nested quantifiers"):
            compiled_regex_match("(a+)+$", "a" * 24 + "!")

    def test_compiled_regex_rejects_backreferences(self):
        from geno._runtime_support import regex_find_all as compiled_regex_find_all

        with pytest.raises(Exception, match="backreferences"):
            compiled_regex_find_all(r"(a)\1", "aa")

    def test_compiled_regex_replace_does_not_use_unbounded_re_sub(self):
        from unittest import mock

        from geno import _runtime_support as compiled_runtime

        with mock.patch.object(
            compiled_runtime._re,
            "sub",
            side_effect=AssertionError("re.sub"),
        ):
            assert compiled_runtime.regex_replace("(a)", r"X\1", "a") == "Xa"

    def test_compiled_regex_replace_rejects_oversized_group_reference(self):
        from geno._runtime_support import regex_replace as compiled_regex_replace

        replacement = "\\" + "1" * 5000
        with pytest.raises(RuntimeError, match="invalid replacement group reference"):
            compiled_regex_replace("(a)", replacement, "a")

    def test_compiled_regex_rejects_identical_alternation_with_quantifier(self):
        from geno._runtime_support import regex_replace as compiled_regex_replace

        with pytest.raises(Exception, match="overlapping alternation"):
            compiled_regex_replace("(a|a)+", "x", "aaaa")

    @pytest.mark.parametrize("pattern", ["(a|[a])+$", "(?:a|aa)+$"])
    def test_compiled_regex_rejects_semantically_overlapping_alternation(self, pattern):
        from geno._runtime_support import regex_match as compiled_regex_match

        with pytest.raises(
            Exception, match=r"overlapping alternation|advanced or encoded"
        ):
            compiled_regex_match(pattern, "a" * 24 + "!")

    def test_compiled_regex_rejects_sequential_quantified_atoms(self):
        from geno._runtime_support import regex_match as compiled_regex_match

        with pytest.raises(Exception, match="adjacent repeated atoms"):
            compiled_regex_match("a*a*a*a*b", "aaaa!")

    @pytest.mark.parametrize(
        "pattern",
        ["a*.*X", "(?P<x>a+)(?P=x)+X"],
    )
    def test_compiled_regex_rejects_remaining_redos_patterns(self, pattern):
        from geno._runtime_support import regex_match as compiled_regex_match

        with pytest.raises(
            Exception,
            match=r"backreferences|adjacent repeated atoms|advanced or encoded",
        ):
            compiled_regex_match(pattern, "a" * 100 + "!")

    @pytest.mark.parametrize("pattern", _REGEX_REDOS_BYPASSES)
    def test_compiled_regex_rejects_portable_subset_bypasses(self, pattern):
        from geno._runtime_support import regex_match as compiled_regex_match

        with pytest.raises(Exception, match=_REGEX_SAFETY_ERROR):
            compiled_regex_match(pattern, "a" * 100 + "!")

    @pytest.mark.parametrize("pattern", _REGEX_NONPORTABLE_PATTERNS)
    def test_compiled_regex_rejects_cross_backend_semantic_differences(self, pattern):
        from geno._runtime_support import regex_match as compiled_regex_match

        with pytest.raises(Exception, match="advanced or encoded"):
            compiled_regex_match(pattern, "é١\r_word")

    def test_compiled_regex_rejects_oversize_text(self):
        from geno._runtime_support import regex_match as compiled_regex_match

        with pytest.raises(Exception, match="too long"):
            compiled_regex_match("a", "a" * 10_001)


class TestCompiledJSRegexValidation:
    """JS runtime must enforce the same ReDoS mitigations as the Python runtime.

    Regression for #660 / F-0014: previously the JS runtime only validated
    pattern length, while Python also rejected backreferences, nested
    quantifiers, overlapping alternation, and oversize text.
    """

    @staticmethod
    def _run_js_expect_error(
        call_js: str, capabilities: tuple[str, ...] = ("regex",)
    ) -> tuple[int, str]:
        script = (
            _js_runtime_prelude_with_caps(*capabilities)
            + "\ntry { "
            + call_js
            + '; console.log("NO_ERROR"); }'
            + " catch (e) { console.log(e.message); }\n"
        )
        result = run_node_code(script, timeout=10)
        assert result.returncode == 0, result.stderr
        return result.returncode, result.stdout.strip()

    def test_js_regex_rejects_backreferences(self):
        _, out = self._run_js_expect_error('regex_find_all("(a)\\\\1", "aa")')
        assert "backreferences" in out

    def test_js_regex_rejects_nested_quantifiers(self):
        _, out = self._run_js_expect_error('regex_match("(a+)+$", "aaaa!")')
        assert "nested quantifiers" in out

    @pytest.mark.parametrize("escape", ["\\u0661", "\\u00b2"])
    def test_js_regex_rejects_non_ascii_quantifier_digits(self, escape):
        _, out = self._run_js_expect_error(f'regex_match("a{{{escape}}}", "a")')
        assert "advanced or encoded" in out

    @pytest.mark.parametrize("pattern", ["a{000001}", "a{000001,000002}"])
    def test_js_regex_allows_zero_padded_bounded_quantifiers(self, pattern):
        _, out = self._run_js_expect_error(f'regex_match("{pattern}", "aa")')
        assert out == "NO_ERROR"

    def test_js_regex_group_nesting_limit(self):
        _, maximum = self._run_js_expect_error(
            'regex_match("(".repeat(128) + "a" + ")".repeat(128), "a")'
        )
        assert maximum == "NO_ERROR"

        _, excessive = self._run_js_expect_error(
            'regex_match("(".repeat(129) + "a" + ")".repeat(129), "a")'
        )
        assert "group nesting too deep" in excessive

    def test_js_regex_rejects_overlapping_alternation_identical(self):
        _, out = self._run_js_expect_error('regex_match("(a|a)+$", "aaaa")')
        assert "overlapping alternation" in out

    def test_js_regex_rejects_overlapping_alternation_prefix(self):
        _, out = self._run_js_expect_error('regex_match("(a|aa)+$", "aaaa")')
        assert "overlapping alternation" in out

    @pytest.mark.parametrize("pattern", ["(a|[a])+$", "(?:a|aa)+$"])
    def test_js_regex_rejects_semantically_overlapping_alternation(self, pattern):
        _, out = self._run_js_expect_error(
            f'regex_match("{pattern}", "aaaaaaaaaaaaaaaaaaaaaaaa!")'
        )
        assert "overlapping alternation" in out or "advanced or encoded" in out

    @pytest.mark.parametrize("pattern", _REGEX_REDOS_BYPASSES[:9])
    def test_js_regex_rejects_portable_subset_bypasses(self, pattern):
        escaped = pattern.replace("\\", "\\\\").replace('"', '\\"')
        _, out = self._run_js_expect_error(
            f'regex_match("{escaped}", "aaaaaaaaaaaaaaaaaaaaaaaa!")'
        )
        assert re.search(_REGEX_SAFETY_ERROR, out)

    @pytest.mark.parametrize("pattern", _REGEX_NONPORTABLE_PATTERNS)
    def test_js_regex_rejects_cross_backend_semantic_differences(self, pattern):
        escaped = pattern.replace("\\", "\\\\").replace('"', '\\"')
        _, out = self._run_js_expect_error(f'regex_match("{escaped}", "é١\\r_word")')
        assert "advanced or encoded" in out

    def test_js_regex_rejects_sequential_quantified_atoms(self):
        _, out = self._run_js_expect_error('regex_match("a*a*a*a*b", "aaaa!")')
        assert "adjacent repeated atoms" in out

    def test_js_regex_rejects_wildcard_overlap(self):
        _, out = self._run_js_expect_error('regex_match("a*.*X", "aaaaaaaa!")')
        assert "adjacent repeated atoms" in out or "advanced or encoded" in out

    def test_js_regex_allows_astral_literal_character_class(self):
        _, out = self._run_js_expect_error(
            'regex_match("[\\u{1F600}]*x", "\\u{1F600}x")'
        )
        assert out == "NO_ERROR"

    def test_js_regex_rejects_named_backreference(self):
        _, out = self._run_js_expect_error(
            'regex_match("(?<x>a+)\\\\k<x>+X", "aaaaaaaa!")'
        )
        assert "backreferences" in out

    def test_js_regex_rejects_oversize_text(self):
        _, out = self._run_js_expect_error('regex_match("a", "a".repeat(10001))')
        assert "too long" in out

    def test_js_regex_rejects_oversize_replacement(self):
        _, out = self._run_js_expect_error(
            'regex_replace("a", "b".repeat(10001), "aaaa")'
        )
        assert "too long" in out

    def test_js_regex_replace_does_not_use_unbounded_native_replace(self):
        script = (
            _js_runtime_prelude_with_caps("regex")
            + "\nString.prototype.replace = function() { throw new Error('native replace'); };\n"
            + 'console.log(regex_replace("(a)", "X\\\\1", "a"));\n'
        )
        result = run_node_code(script, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "Xa"

    def test_js_regex_allows_escaped_literal_group_text(self):
        # Positive case: escaped metacharacters inside a group should still match.
        script = (
            _js_runtime_prelude_with_caps("regex")
            + '\nconst r = regex_match("\\\\(a\\\\|a\\\\)\\\\+", "prefix (a|a)+ suffix");\n'
            + "console.log(r._tag + ':' + (r.value || ''));\n"
        )
        result = run_node_code(script, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "Some:(a|a)+"


class TestCompiledPadValidation:
    """Python and JS runtimes must share the same single-character fill contract
    for string padding.

    Regression for #660 / F-0015.
    """

    def test_python_compiled_pad_left_rejects_multichar_fill(self):
        from geno._runtime_support import string_pad_left

        with pytest.raises(Exception, match="single character"):
            string_pad_left("x", 5, "ab")

    def test_python_compiled_pad_right_rejects_multichar_fill(self):
        from geno._runtime_support import string_pad_right

        with pytest.raises(Exception, match="single character"):
            string_pad_right("x", 5, "ab")

    def test_python_compiled_pad_left_rejects_empty_fill(self):
        from geno._runtime_support import string_pad_left

        with pytest.raises(Exception, match="single character"):
            string_pad_left("x", 5, "")

    def test_python_compiled_pad_left_rejects_non_string_fill(self):
        from geno._runtime_support import string_pad_left

        with pytest.raises(Exception, match="must be a string"):
            string_pad_left("x", 5, 1)

    def test_js_compiled_pad_left_rejects_multichar_fill(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error(
            'string_pad_left("x", 5, "ab")'
        )
        assert "single character" in out

    def test_js_compiled_pad_right_rejects_multichar_fill(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error(
            'string_pad_right("x", 5, "ab")'
        )
        assert "single character" in out

    def test_js_compiled_pad_left_rejects_empty_fill(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error(
            'string_pad_left("x", 5, "")'
        )
        assert "single character" in out

    def test_js_compiled_pad_left_rejects_non_string_fill(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error(
            'string_pad_left("x", 5, 1)'
        )
        assert "must be a string" in out

    def test_js_compiled_pad_accepts_single_char_fill(self):
        from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE

        script = (
            JS_RUNTIME_PRELUDE
            + '\nconsole.log(string_pad_left("7", 3, "0"));\n'
            + 'console.log(string_pad_right("7", 3, "-"));\n'
        )
        result = run_node_code(script, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines() == ["007", "7--"]


class TestMathDomainParity:
    """math_sqrt and math_log must reject invalid domains the same way on
    interpreter, compiled Python, and compiled JS.

    Regression for #659 / F-0010 and F-0011: previously JS silently returned
    NaN / -Infinity while Python raised.
    """

    # --- Interpreter builtin --------------------------------------------------
    def test_interpreter_math_sqrt_rejects_negative(self):
        from geno.builtins import builtin_math_sqrt

        with pytest.raises(Exception, match="non-negative"):
            builtin_math_sqrt(-1.0)

    def test_interpreter_math_log_rejects_zero(self):
        from geno.builtins import builtin_math_log

        with pytest.raises(Exception, match="positive"):
            builtin_math_log(0.0)

    def test_interpreter_math_log_rejects_negative(self):
        from geno.builtins import builtin_math_log

        with pytest.raises(Exception, match="positive"):
            builtin_math_log(-1.0)

    def test_interpreter_math_sqrt_accepts_zero(self):
        from geno.builtins import builtin_math_sqrt

        assert builtin_math_sqrt(0.0) == 0.0

    # --- Compiled Python runtime ---------------------------------------------
    def test_compiled_python_math_sqrt_rejects_negative(self):
        from geno._runtime_support import math_sqrt

        with pytest.raises(Exception, match="non-negative"):
            math_sqrt(-1.0)

    def test_compiled_python_math_log_rejects_zero(self):
        from geno._runtime_support import math_log

        with pytest.raises(Exception, match="positive"):
            math_log(0.0)

    def test_compiled_python_math_log_rejects_negative(self):
        from geno._runtime_support import math_log

        with pytest.raises(Exception, match="positive"):
            math_log(-1.0)

    def test_compiled_python_math_sqrt_rejects_non_number(self):
        from geno._runtime_support import math_sqrt

        with pytest.raises(Exception, match="expected number"):
            math_sqrt("x")

    # --- Compiled JS runtime -------------------------------------------------
    def test_compiled_js_math_sqrt_rejects_negative(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error("math_sqrt(-1.0)")
        assert "non-negative" in out

    def test_compiled_js_math_log_rejects_zero(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error("math_log(0.0)")
        assert "positive" in out

    def test_compiled_js_math_log_rejects_negative(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error("math_log(-1.0)")
        assert "positive" in out

    def test_compiled_js_math_sqrt_rejects_non_number(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error('math_sqrt("x")')
        assert "expected number" in out

    def test_compiled_js_math_sqrt_accepts_valid(self):
        from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE

        script = JS_RUNTIME_PRELUDE + "\nconsole.log(math_sqrt(9.0));\n"
        result = run_node_code(script, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "3"


class TestDateTimeFmtParity:
    """clock_format / clock_parse / datetime_format / datetime_parse must
    accept the same documented directive subset on interpreter, compiled
    Python, and compiled JS.

    Regression for #659 / F-0012 and F-0013: previously the Python backend
    accepted any `strftime`/`strptime` directive while JS only handled
    `%Y %m %d %H %M %S`, producing silent backend skew.
    """

    @pytest.fixture(autouse=True)
    def _grant_clock_capability(self):
        with _runtime_caps("clock"):
            yield

    # --- Interpreter builtin --------------------------------------------------
    def test_interpreter_clock_format_rejects_unknown_directive(self):
        from geno.builtins import builtin_clock_format

        with pytest.raises(Exception, match="unsupported format directive"):
            builtin_clock_format(0.0, "%j")

    def test_interpreter_clock_parse_rejects_unknown_directive(self):
        from geno.builtins import builtin_clock_parse

        with pytest.raises(Exception, match="unsupported format directive"):
            builtin_clock_parse("x", "%j")

    def test_interpreter_clock_format_rejects_trailing_percent(self):
        from geno.builtins import builtin_clock_format

        with pytest.raises(Exception, match="trailing '%'"):
            builtin_clock_format(0.0, "year: %")

    def test_interpreter_datetime_format_rejects_unknown_directive(self):
        # Interpreter datetime_format delegates to clock_format.
        from geno.builtins import builtin_datetime_format

        with pytest.raises(Exception, match="unsupported format directive"):
            builtin_datetime_format(0, "%A")

    # --- Compiled Python runtime ---------------------------------------------
    def test_compiled_python_clock_format_rejects_unknown_directive(self):
        from geno._runtime_support import clock_format

        with pytest.raises(Exception, match="unsupported format directive"):
            clock_format(0.0, "%j")

    def test_compiled_python_clock_parse_rejects_unknown_directive(self):
        from geno._runtime_support import clock_parse

        with pytest.raises(Exception, match="unsupported format directive"):
            clock_parse("x", "%j")

    def test_compiled_python_clock_format_rejects_non_string_fmt(self):
        from geno._runtime_support import clock_format

        with pytest.raises(Exception, match="fmt must be a string"):
            clock_format(0.0, cast(Any, 1))

    def test_compiled_python_clock_parse_rejects_non_string_text(self):
        from geno._runtime_support import clock_parse

        with pytest.raises(Exception, match="text must be a string"):
            clock_parse(cast(Any, 1), "%Y")

    def test_compiled_python_datetime_format_delegates_to_clock_format(self):
        from geno._runtime_support import datetime_format

        # Must honor the narrow contract (delegates through clock_format).
        with pytest.raises(Exception, match="unsupported format directive"):
            datetime_format(0, "%A")
        # Positive case: documented directives still work.
        assert datetime_format(0, "%Y-%m-%d") == "1970-01-01"

    def test_compiled_python_datetime_parse_preserves_pre_1970(self):
        from geno._runtime_support import Some, datetime_parse

        result = datetime_parse("1969-12-31", "%Y-%m-%d")
        assert isinstance(result, Some)
        assert result.value == -86400

    def test_compiled_python_clock_parse_rejects_invalid_calendar_date(self):
        from geno._runtime_support import None_, clock_parse

        assert clock_parse("2024-13-01", "%Y-%m-%d") is None_
        assert clock_parse("2024-02-30", "%Y-%m-%d") is None_

    def test_compiled_python_datetime_parse_rejects_invalid_calendar_date(self):
        from geno._runtime_support import None_, datetime_parse

        assert datetime_parse("2024-13-01", "%Y-%m-%d") is None_

    # --- Compiled JS runtime -------------------------------------------------
    def test_compiled_js_clock_format_rejects_unknown_directive(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error(
            'clock_format(0, "%j")',
            capabilities=("clock",),
        )
        assert "unsupported format directive" in out

    def test_compiled_js_clock_parse_rejects_unknown_directive(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error(
            'clock_parse("x", "%j")',
            capabilities=("clock",),
        )
        assert "unsupported format directive" in out

    def test_compiled_js_clock_format_rejects_non_string_fmt(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error(
            "clock_format(0, 1)",
            capabilities=("clock",),
        )
        assert "fmt must be a string" in out

    def test_compiled_js_clock_parse_rejects_non_string_text(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error(
            'clock_parse(1, "%Y")',
            capabilities=("clock",),
        )
        assert "text must be a string" in out

    def test_compiled_js_clock_format_rejects_trailing_percent(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error(
            'clock_format(0, "year: %")',
            capabilities=("clock",),
        )
        assert "trailing '%'" in out

    def test_compiled_js_datetime_format_rejects_unknown_directive(self):
        _, out = TestCompiledJSRegexValidation._run_js_expect_error(
            'datetime_format(0, "%A")',
            capabilities=("clock",),
        )
        assert "unsupported format directive" in out

    def test_compiled_js_datetime_format_accepts_full_subset(self):
        script = (
            _js_runtime_prelude_with_caps("clock")
            + '\nconsole.log(datetime_format(0, "%Y-%m-%d %H:%M:%S"));\n'
            + 'console.log(datetime_format(0, "%%Y-%m-%d"));\n'
        )
        result = run_node_code(script, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines() == [
            "1970-01-01 00:00:00",
            "%Y-01-01",
        ]

    def test_compiled_js_datetime_parse_round_trip(self):
        script = (
            _js_runtime_prelude_with_caps("clock")
            + '\nconst r = datetime_parse("1970-01-02", "%Y-%m-%d");\n'
            + "console.log(r._tag + ':' + (r._tag === 'Some' ? r.value : ''));\n"
        )
        result = run_node_code(script, timeout=10)
        assert result.returncode == 0, result.stderr
        # 1970-01-02 UTC == 86400 seconds past epoch.
        assert result.stdout.strip() == "Some:86400"

    def test_compiled_js_clock_parse_rejects_invalid_calendar_date(self):
        script = (
            _js_runtime_prelude_with_caps("clock")
            + '\nconsole.log(clock_parse("2024-13-01", "%Y-%m-%d")._tag);\n'
            + 'console.log(clock_parse("2024-02-30", "%Y-%m-%d")._tag);\n'
        )
        result = run_node_code(script, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines() == ["None", "None"]

    def test_compiled_js_datetime_parse_rejects_invalid_calendar_date(self):
        script = (
            _js_runtime_prelude_with_caps("clock")
            + '\nconsole.log(datetime_parse("2024-13-01", "%Y-%m-%d")._tag);\n'
        )
        result = run_node_code(script, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "None"


class TestCompiledArrayIteration:
    """Compiled _GenoArray iteration must snapshot to match interpreter."""

    def test_geno_array_iter_snapshots(self):
        from geno._runtime_support import _GenoArray

        arr = _GenoArray([1, 2, 3])
        collected = []
        for x in arr:
            arr[0] = 99
            collected.append(x)
        assert collected == [1, 2, 3]


# =============================================================================
# File I/O Builtins (Expanded)
# =============================================================================


class TestFileIOBuiltins:
    """Tests for fs_list_dir and fs_exists builtins."""

    def test_fs_exists_type(self):
        source = """
        func main() -> Bool
            return fs_exists("/some/path")
        end func main
        """
        typecheck(source)

    def test_fs_list_dir_type(self):
        source = """
        func main() -> Result[List[String], String]
            return fs_list_dir("/some/path")
        end func main
        """
        typecheck(source)

    def test_fs_exists_with_callback(self):
        source = """
        func main() -> Bool
            return fs_exists("/real/file.txt")
        end func main
        """

        def fake_fs_exists(path):
            return path == "/real/file.txt"

        config = RunConfig(
            capabilities={"fs"},
            host_callbacks={"fs_exists": fake_fs_exists},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value is True

    def test_fs_exists_false_with_callback(self):
        source = """
        func main() -> Bool
            return fs_exists("/nonexistent")
        end func main
        """

        def fake_fs_exists(path):
            return False

        config = RunConfig(
            capabilities={"fs"},
            host_callbacks={"fs_exists": fake_fs_exists},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value is False

    def test_fs_list_dir_with_callback(self):
        source = """
        func main() -> Result[List[String], String]
            return fs_list_dir("/mydir")
        end func main
        """
        from geno.values import ConstructorValue

        def fake_fs_list_dir(path):
            return ConstructorValue("Ok", {"value": ["a.txt", "b.txt"]})

        config = RunConfig(
            capabilities={"fs"},
            host_callbacks={"fs_list_dir": fake_fs_list_dir},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == {
            "_constructor": "Ok",
            "fields": {"value": ["a.txt", "b.txt"]},
        }

    def test_fs_list_dir_error_with_callback(self):
        source = """
        func main() -> Result[List[String], String]
            return fs_list_dir("/bad")
        end func main
        """
        from geno.values import ConstructorValue

        def fake_fs_list_dir(path):
            return ConstructorValue("Err", {"error": "not found"})

        config = RunConfig(
            capabilities={"fs"},
            host_callbacks={"fs_list_dir": fake_fs_list_dir},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == {
            "_constructor": "Err",
            "fields": {"error": "not found"},
        }

    def test_fs_exists_denied_without_capability(self):
        source = """
        func main() -> Bool
            return fs_exists("/test")
        end func main
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is False
        assert any("denied" in d.message.lower() for d in result.diagnostics)

    def test_fs_list_dir_denied_without_capability(self):
        source = """
        func main() -> Result[List[String], String]
            return fs_list_dir("/test")
        end func main
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is False
        assert any("denied" in d.message.lower() for d in result.diagnostics)

    def test_fs_list_dir_compiled_js(self):
        source = """
        func main() -> Result[List[String], String]
            return fs_list_dir("/mydir")
        end func main
        """
        js = compile_js(source)
        assert "fs_list_dir" in js

    def test_fs_exists_compiled_js(self):
        source = """
        func main() -> Bool
            return fs_exists("/myfile")
        end func main
        """
        js = compile_js(source)
        assert "fs_exists" in js


# =============================================================================
# Clock/DateTime Builtins
# =============================================================================


class TestClockBuiltins:
    """Tests for clock_format, clock_parse, clock_elapsed builtins."""

    def test_clock_elapsed(self):
        source = """
        func main() -> Float
            return clock_elapsed(start: 10.0, end_time: 15.0)
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"clock"})
        assert globals_dict["main"]() == 5.0

    def test_clock_elapsed_interpreter(self):
        source = """
        func main() -> Float
            return clock_elapsed(start: 10.0, end_time: 15.0)
        end func main
        """
        result = run_geno(source, capabilities={"clock"})
        assert result == 5.0

    def test_clock_format(self):
        source = """
        func main() -> String
            return clock_format(timestamp: 0.0, fmt: "%Y-%m-%d")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"clock"})
        assert globals_dict["main"]() == "1970-01-01"

    def test_clock_format_with_time(self):
        source = """
        func main() -> String
            return clock_format(timestamp: 3661.0, fmt: "%H:%M:%S")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"clock"})
        assert globals_dict["main"]() == "01:01:01"

    def test_clock_format_interpreter(self):
        source = """
        func main() -> String
            return clock_format(timestamp: 0.0, fmt: "%Y-%m-%d")
        end func main
        """
        result = run_geno(source, capabilities={"clock"})
        assert result == "1970-01-01"

    def test_clock_parse_valid(self):
        source = """
        func main() -> Option[Float]
            return clock_parse(text: "1970-01-01", fmt: "%Y-%m-%d")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"clock"})
        result = globals_dict["main"]()
        assert type(result).__name__ == "Some"
        assert result.value == 0.0

    def test_clock_parse_invalid(self):
        source = """
        func main() -> Option[Float]
            return clock_parse(text: "not-a-date", fmt: "%Y-%m-%d")
        end func main
        """
        globals_dict = compile_and_exec(source, timeout=None, capabilities={"clock"})
        result = globals_dict["main"]()
        assert type(result).__name__ == "_None"

    def test_clock_parse_interpreter(self):
        source = """
        func main() -> Option[Float]
            return clock_parse(text: "2000-06-15", fmt: "%Y-%m-%d")
        end func main
        """
        result = run_geno(source, capabilities={"clock"})
        assert result["_constructor"] == "Some"
        assert result["fields"]["value"] == 961027200.0

    def test_clock_compiled_js(self):
        source = """
        func main() -> Float
            return clock_elapsed(start: 10.0, end_time: 25.0)
        end func main
        """
        js = compile_js(source)
        assert "clock_elapsed" in js

    def test_typechecker_clock_format_types(self):
        source = """
        func main() -> String
            return clock_format(timestamp: 0.0, fmt: "%Y")
        end func main
        """
        typecheck(source)

    def test_typechecker_clock_parse_types(self):
        source = """
        func main() -> Option[Float]
            return clock_parse(text: "2020", fmt: "%Y")
        end func main
        """
        typecheck(source)


# =============================================================================
# Traits / Interfaces
# =============================================================================


TRAIT_SOURCE_BASIC = """
trait Describable
    func describe(self: Self) -> String
end trait

type Circle = Circle(radius: Float)
type Square = Square(side: Float)

impl Describable for Circle
    func describe(self: Circle) -> String
        example Circle(1.0) -> "Circle"
        return "Circle"
    end func
end impl

impl Describable for Square
    func describe(self: Square) -> String
        example Square(1.0) -> "Square"
        return "Square"
    end func
end impl
"""


class TestTraitParsing:
    """Tests for parsing trait and impl definitions."""

    def test_parse_trait_def(self):
        source = """
        trait Describable
            func describe(self: Self) -> String
        end trait
        """
        lexer = Lexer(source, "<test>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        assert len(program.definitions) == 1
        from geno.ast_nodes import TraitDef

        trait = program.definitions[0]
        assert isinstance(trait, TraitDef)
        assert trait.name == "Describable"
        assert len(trait.methods) == 1
        assert trait.methods[0].name == "describe"

    def test_parse_impl_def(self):
        source = (
            TRAIT_SOURCE_BASIC
            + """
        func main() -> String
            example () -> "Circle"
            let c: Circle = Circle(5.0)
            return describe(c)
        end func main
        """
        )
        lexer = Lexer(source, "<test>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        from geno.ast_nodes import ImplDef, TraitDef

        traits = [d for d in program.definitions if isinstance(d, TraitDef)]
        impls = [d for d in program.definitions if isinstance(d, ImplDef)]
        assert len(traits) == 1
        assert len(impls) == 2
        assert impls[0].trait_name == "Describable"
        assert impls[0].target_type == "Circle"
        assert impls[1].target_type == "Square"

    def test_parse_trait_multiple_methods(self):
        source = """
        trait Shape
            func area(self: Self) -> Float
            func perimeter(self: Self) -> Float
        end trait
        """
        lexer = Lexer(source, "<test>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        from geno.ast_nodes import TraitDef

        trait = program.definitions[0]
        assert isinstance(trait, TraitDef)
        assert len(trait.methods) == 2
        assert trait.methods[0].name == "area"
        assert trait.methods[1].name == "perimeter"


class TestTraitTypeChecker:
    """Tests for typechecking trait and impl definitions."""

    def test_typecheck_basic_trait(self):
        source = (
            TRAIT_SOURCE_BASIC
            + """
        func main() -> String
            example () -> "Circle"
            let c: Circle = Circle(5.0)
            return describe(c)
        end func main
        """
        )
        typecheck(source)

    def test_typecheck_dispatch_both_types(self):
        source = (
            TRAIT_SOURCE_BASIC
            + """
        func main() -> String
            example () -> "Square"
            let s: Square = Square(3.0)
            return describe(s)
        end func main
        """
        )
        typecheck(source)

    def test_typecheck_missing_impl_method(self):
        source = """
        trait Describable
            func describe(self: Self) -> String
        end trait

        type Circle = Circle(radius: Float)

        impl Describable for Circle
        end impl

        func main() -> String
            example () -> "x"
            return "x"
        end func main
        """
        with pytest.raises(TypeError, match="Missing implementation"):
            typecheck(source)

    def test_typecheck_wrong_return_type(self):
        source = """
        trait Describable
            func describe(self: Self) -> String
        end trait

        type Circle = Circle(radius: Float)

        impl Describable for Circle
            func describe(self: Circle) -> Int
                example Circle(1.0) -> 42
                return 42
            end func
        end impl

        func main() -> String
            example () -> "x"
            return "x"
        end func main
        """
        with pytest.raises(TypeError, match="return type mismatch"):
            typecheck(source)

    def test_typecheck_unknown_trait(self):
        source = """
        type Circle = Circle(radius: Float)

        impl UnknownTrait for Circle
        end impl

        func main() -> String
            example () -> "x"
            return "x"
        end func main
        """
        with pytest.raises(TypeError, match="Unknown trait"):
            typecheck(source)

    def test_typecheck_unknown_type(self):
        source = """
        trait Describable
            func describe(self: Self) -> String
        end trait

        impl Describable for UnknownType
        end impl

        func main() -> String
            example () -> "x"
            return "x"
        end func main
        """
        with pytest.raises(TypeError, match="Unknown type"):
            typecheck(source)


class TestTraitInterpreter:
    """Tests for interpreting trait dispatch."""

    def test_basic_dispatch_circle(self):
        source = (
            TRAIT_SOURCE_BASIC
            + """
        func main() -> String
            example () -> "Circle"
            let c: Circle = Circle(5.0)
            return describe(c)
        end func main
        """
        )
        assert run_geno(source) == "Circle"

    def test_basic_dispatch_square(self):
        source = (
            TRAIT_SOURCE_BASIC
            + """
        func main() -> String
            example () -> "Square"
            let s: Square = Square(3.0)
            return describe(s)
        end func main
        """
        )
        assert run_geno(source) == "Square"

    def test_dispatch_multiple_calls(self):
        source = (
            TRAIT_SOURCE_BASIC
            + """
        func main() -> String
            example () -> "Circle"
            let c: Circle = Circle(5.0)
            let s: Square = Square(3.0)
            let d1: String = describe(c)
            let d2: String = describe(s)
            return d1
        end func main
        """
        )
        assert run_geno(source) == "Circle"

    def test_trait_dispatch_supports_named_args(self):
        source = """
        trait Decoratable
            func decorate(self: Self, prefix: String, suffix: String) -> String
        end trait

        type Circle = Circle(radius: Float)

        impl Decoratable for Circle
            func decorate(self: Circle, prefix: String, suffix: String) -> String
                example (Circle(1.0), "[", "]") -> "[Circle]"
                return prefix + "Circle" + suffix
            end func
        end impl

        func main() -> String
            example () -> "[Circle]"
            let c: Circle = Circle(2.0)
            return decorate(prefix: "[", self: c, suffix: "]")
        end func
        """
        assert run_geno(source) == "[Circle]"

    def test_trait_dispatch_named_args_use_matching_trait_signature(self):
        source = """
        trait BoxFmt
            func render(self: Self, prefix: String, suffix: String) -> String
        end trait

        trait LabelFmt
            func render(self: Self, left: String, right: String) -> String
        end trait

        type Box = Box(value: Int)
        type Label = Label(value: String)

        impl BoxFmt for Box
            func render(self: Box, prefix: String, suffix: String) -> String
                example (Box(1), "<", ">") -> "<Box>"
                return prefix + "Box" + suffix
            end func
        end impl

        impl LabelFmt for Label
            func render(self: Label, left: String, right: String) -> String
                example (Label("x"), "[", "]") -> "[Label]"
                return left + "Label" + right
            end func
        end impl

        func main() -> String
            example () -> "[Label]"
            let label: Label = Label("demo")
            return render(self: label, left: "[", right: "]")
        end func
        """
        assert run_geno(source) == "[Label]"


class TestTraitCompiler:
    """Tests for compiled trait dispatch (Python backend)."""

    def test_compiled_dispatch_circle(self):
        source = (
            TRAIT_SOURCE_BASIC
            + """
        func main() -> String
            example () -> "Circle"
            let c: Circle = Circle(5.0)
            return describe(c)
        end func main
        """
        )
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == "Circle"

    def test_compiled_dispatch_square(self):
        source = (
            TRAIT_SOURCE_BASIC
            + """
        func main() -> String
            example () -> "Square"
            let s: Square = Square(3.0)
            return describe(s)
        end func main
        """
        )
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == "Square"

    def test_compiled_dispatch_named_args_use_matching_trait_signature(self):
        source = """
        trait BoxFmt
            func render(self: Self, prefix: String, suffix: String) -> String
        end trait

        trait LabelFmt
            func render(self: Self, left: String, right: String) -> String
        end trait

        type Box = Box(value: Int)
        type Label = Label(value: String)

        impl BoxFmt for Box
            func render(self: Box, prefix: String, suffix: String) -> String
                example (Box(1), "<", ">") -> "<Box>"
                return prefix + "Box" + suffix
            end func
        end impl

        impl LabelFmt for Label
            func render(self: Label, left: String, right: String) -> String
                example (Label("x"), "[", "]") -> "[Label]"
                return left + "Label" + right
            end func
        end impl

        func main() -> String
            example () -> "[Label]"
            let label: Label = Label("demo")
            return render(self: label, left: "[", right: "]")
        end func
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == "[Label]"


class TestTraitJSCompiler:
    """Tests for JS compilation of trait dispatch."""

    def test_js_output_contains_dispatch(self):
        source = (
            TRAIT_SOURCE_BASIC
            + """
        func main() -> String
            example () -> "Circle"
            let c: Circle = Circle(5.0)
            return describe(c)
        end func main
        """
        )
        js_code = compile_js(source)
        # Mangled impl methods should appear
        assert "Describable_describe_Circle" in js_code
        assert "Describable_describe_Square" in js_code
        # Dispatch wrapper should use _tag
        assert "_tag" in js_code

    def test_js_dispatch_wrapper_present(self):
        source = (
            TRAIT_SOURCE_BASIC
            + """
        func main() -> String
            example () -> "Circle"
            let c: Circle = Circle(5.0)
            return describe(c)
        end func main
        """
        )
        js_code = compile_js(source)
        # The dispatch function 'describe' should be defined
        assert "function describe(self_arg, ...args)" in js_code

    def test_js_dispatch_named_args_use_matching_trait_signature(self):
        source = """
        trait BoxFmt
            func render(self: Self, prefix: String, suffix: String) -> String
        end trait

        trait LabelFmt
            func render(self: Self, left: String, right: String) -> String
        end trait

        type Box = Box(value: Int)
        type Label = Label(value: String)

        impl BoxFmt for Box
            func render(self: Box, prefix: String, suffix: String) -> String
                example (Box(1), "<", ">") -> "<Box>"
                return prefix + "Box" + suffix
            end func
        end impl

        impl LabelFmt for Label
            func render(self: Label, left: String, right: String) -> String
                example (Label("x"), "[", "]") -> "[Label]"
                return left + "Label" + right
            end func
        end impl

        func main() -> String
            example () -> "[Label]"
            let label: Label = Label("demo")
            return render(self: label, left: "[", right: "]")
        end func
        """
        js_code = compile_js(source)
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[Label]"


# ── Multi-constructor ADT trait dispatch ───────────────────────────────

TRAIT_MULTI_CONSTRUCTOR = """
trait Describable
    func describe(self: Self) -> String
end trait

type Shape = Circle(radius: Float) | Square(side: Float)

impl Describable for Shape
    func describe(self: Shape) -> String
        example Circle(1.0) -> "shape"
        return "shape"
    end func
end impl
"""


class TestTraitMultiConstructorADT:
    """Tests for trait dispatch on multi-constructor ADTs (BUG 2 fix)."""

    def test_interpreter_circle_constructor(self):
        source = (
            TRAIT_MULTI_CONSTRUCTOR
            + """
        func main() -> String
            example () -> "shape"
            let c: Shape = Circle(5.0)
            return describe(c)
        end func main
        """
        )
        assert run_geno(source) == "shape"

    def test_interpreter_square_constructor(self):
        source = (
            TRAIT_MULTI_CONSTRUCTOR
            + """
        func main() -> String
            example () -> "shape"
            let s: Shape = Square(3.0)
            return describe(s)
        end func main
        """
        )
        assert run_geno(source) == "shape"

    def test_compiled_circle_constructor(self):
        source = (
            TRAIT_MULTI_CONSTRUCTOR
            + """
        func main() -> String
            example () -> "shape"
            let c: Shape = Circle(5.0)
            return describe(c)
        end func main
        """
        )
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == "shape"

    def test_compiled_square_constructor(self):
        source = (
            TRAIT_MULTI_CONSTRUCTOR
            + """
        func main() -> String
            example () -> "shape"
            let s: Shape = Square(3.0)
            return describe(s)
        end func main
        """
        )
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == "shape"

    def test_js_multi_constructor_includes(self):
        source = (
            TRAIT_MULTI_CONSTRUCTOR
            + """
        func main() -> String
            example () -> "shape"
            let c: Shape = Circle(5.0)
            return describe(c)
        end func main
        """
        )
        js_code = compile_js(source)
        # Should use .includes() for multi-constructor dispatch
        assert "includes(self_arg._tag)" in js_code


# ── Multi-argument trait methods ───────────────────────────────────────

TRAIT_MULTI_ARG = """
trait Formatter
    func format_with(self: Self, prefix: String) -> String
end trait

type Label = Label(text: String)

impl Formatter for Label
    func format_with(self: Label, prefix: String) -> String
        example (Label("hello"), "tag") -> "tag"
        return prefix
    end func
end impl
"""


class TestTraitMultiArgMethods:
    """Tests for trait methods with multiple arguments (BUG 1 fix)."""

    def test_interpreter_multi_arg(self):
        """Verify the second argument flows through dispatch."""
        source = (
            TRAIT_MULTI_ARG
            + """
        func main() -> String
            example () -> "tag"
            let l: Label = Label("hello")
            return format_with(l, "tag")
        end func main
        """
        )
        assert run_geno(source) == "tag"

    def test_compiled_multi_arg(self):
        """Verify the second argument flows through compiled dispatch."""
        source = (
            TRAIT_MULTI_ARG
            + """
        func main() -> String
            example () -> "tag"
            let l: Label = Label("hello")
            return format_with(l, "tag")
        end func main
        """
        )
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == "tag"

    def test_js_multi_arg_spread(self):
        source = (
            TRAIT_MULTI_ARG
            + """
        func main() -> String
            example () -> "tag"
            let l: Label = Label("hello")
            return format_with(l, "tag")
        end func main
        """
        )
        js_code = compile_js(source)
        assert "...args" in js_code


# ── Duplicate impl detection ──────────────────────────────────────────


class TestTraitDuplicateImpl:
    """Tests for duplicate impl detection (BUG 5 fix)."""

    def test_duplicate_impl_rejected(self):
        source = """
        trait Describable
            func describe(self: Self) -> String
        end trait

        type Color = Color(name: String)

        impl Describable for Color
            func describe(self: Color) -> String
                example Color("r") -> "c"
                return "c"
            end func
        end impl

        impl Describable for Color
            func describe(self: Color) -> String
                example Color("r") -> "c"
                return "c"
            end func
        end impl

        func main() -> String
            example () -> "c"
            return "c"
        end func main
        """
        with pytest.raises(Exception, match="Duplicate implementation"):
            run_geno(source)


# ── Review fix tests ──────────────────────────────────────────────────


class TestReviewFixes:
    """Tests for bugs found during the 10-PR review sweep."""

    # PR #58: Bare type alias
    def test_bare_type_alias(self):
        source = """
        type Name = String

        func main() -> Name
            example () -> "hello"
            return "hello"
        end func main
        """
        assert run_geno(source) == "hello"

    # PR #58: Generic alias arity validation
    def test_generic_alias_missing_params(self):
        source = """
        type Pair[T] = Tuple[T, T]

        func main() -> Pair
            example () -> (1, 1)
            return (1, 1)
        end func main
        """
        with pytest.raises(Exception, match=r"expects.*type parameter"):
            run_geno(source)

    # PR #59: String literal inside f-string expression
    def test_fstring_rejects_string_literal_in_expr(self):
        from geno.lexer import Lexer, LexerError

        with pytest.raises(LexerError, match=r"String literals.*not allowed"):
            Lexer('f"val is {"hi"}"', "<test>").tokenize()

    # PR #65: Regex pattern length limit
    def test_regex_pattern_length_limit(self):
        from geno.builtins import builtin_regex_match

        with pytest.raises(Exception, match="pattern too long"):
            builtin_regex_match("a" * 1001, "test")

    # PR #69: Regex input length guard
    def test_regex_text_length_limit(self):
        from geno.builtins import builtin_regex_match

        with pytest.raises(Exception, match="text too long"):
            builtin_regex_match("a+", "a" * 10001)

    # PR #67: Literal percent in clock_format
    def test_clock_format_literal_percent(self):
        from geno.builtins import builtin_clock_format

        result = builtin_clock_format(0, "%%Y")
        assert result == "%Y"

    # PR #67: Negative timestamp rejection
    def test_clock_format_negative_timestamp(self):
        from geno.builtins import builtin_clock_format

        with pytest.raises(Exception, match="negative timestamps"):
            builtin_clock_format(-1, "%Y")

    # PR #68: Self return type in trait
    def test_trait_self_return_type(self):
        source = """
        trait Cloneable
            func clone(self: Self) -> Self
        end trait

        type Point = Point(x: Int)

        impl Cloneable for Point
            func clone(self: Point) -> Point
                example Point(1) -> Point(1)
                return self
            end func
        end impl

        func main() -> Int
            example () -> 5
            let p: Point = Point(5)
            let p2: Point = clone(p)
            return p2.x
        end func main
        """
        assert run_geno(source) == 5


# =============================================================================
# HTTP Serve Builtins
# =============================================================================


class TestHttpServe:
    """Tests for http_listen, http_route serve builtins."""

    def test_http_route_typechecks(self):
        source = """
        @untested("serve")
        func handler(req: HttpRequest) -> HttpResponse
            return HttpResponse(200, "ok", [])
        end func

        @untested("serve")
        func main() -> Unit
            http_route(method: "GET", path: "/test", handler: handler)
        end func
        """
        typecheck(source)

    def test_http_listen_typechecks(self):
        source = """
        @untested("serve")
        func main() -> Unit
            http_listen(8080)
        end func
        """
        typecheck(source)

    def test_capability_denied_without_serve(self):
        source = """
        @untested("serve")
        func main() -> Unit
            http_listen(8080)
        end func
        """
        result = run(source, config=RunConfig(capabilities=set()))
        assert not result.ok
        assert "Capability denied" in (
            result.diagnostics[0].message if result.diagnostics else ""
        )

    def test_http_request_type_defined(self):
        source = """
        @untested("test")
        func get_method(req: HttpRequest) -> String
            return req.method
        end func
        """
        typecheck(source)

    def test_http_respond_typechecks(self):
        source = """
        @untested("serve")
        func main() -> HttpResponse
            return http_respond(status: 200, headers: [], body: "ok")
        end func
        """
        typecheck(source)

    def test_http_respond_returns_http_response(self):
        source = """
        @untested("serve")
        func handler(req: HttpRequest) -> HttpResponse
            return http_respond(status: 200, headers: [("Content-Type", "text/plain")], body: "hello")
        end func

        @untested("serve")
        func main() -> Unit
            http_route(method: "GET", path: "/", handler: handler)
        end func
        """
        typecheck(source)

    def test_http_respond_wrong_arg_type(self):
        source = """
        @untested("serve")
        func main() -> HttpResponse
            return http_respond(status: "200", headers: [], body: "ok")
        end func
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_http_respond_capability_denied(self):
        source = """
        @untested("serve")
        func main() -> HttpResponse
            return http_respond(status: 200, headers: [], body: "ok")
        end func
        """
        result = run(source, config=RunConfig(capabilities=set()))
        assert not result.ok
        assert "Capability denied" in (
            result.diagnostics[0].message if result.diagnostics else ""
        )

    def test_http_respond_runs_with_serve_cap(self):
        source = """
        @untested("serve")
        func main() -> HttpResponse
            return http_respond(status: 200, headers: [], body: "ok")
        end func
        """
        result = run(source, config=RunConfig(capabilities={"serve"}))
        assert result.ok
        assert result.value_raw.constructor == "HttpResponse"
        assert result.value_raw.fields["status"] == 200
        assert result.value_raw.fields["body"] == "ok"

    def test_http_respond_compiles_python(self):
        from geno.compiler import Compiler

        source = """
        @untested("serve")
        func main() -> HttpResponse
            return http_respond(status: 200, headers: [], body: "ok")
        end func
        """
        tokens = Lexer(source, "<test>").tokenize()
        program = Parser(tokens).parse_program()
        checker = TypeChecker()
        checker.check_program(program)
        compiler = Compiler()
        py_code = compiler.compile(program)
        assert "http_respond" in py_code

    def test_http_respond_rejected_on_node_cli(self):
        source = """
        @untested("serve")
        func main() -> HttpResponse
            return http_respond(status: 200, headers: [], body: "ok")
        end func
        """
        with pytest.raises(TypeError, match=r"http_respond.*node-cli"):
            compile_js(source)


# =============================================================================
# cli_args builtin
# =============================================================================


class TestCliArgs:
    def test_cli_args_typechecks(self):
        source = """
        @untested("env")
        func main() -> List[String]
            return cli_args()
        end func
        """
        typecheck(source)

    def test_cli_args_returns_list_string(self):
        source = """
        @untested("env")
        func main() -> Int
            return cli_args()
        end func
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_cli_args_capability_denied(self):
        source = """
        @untested("env")
        func main() -> List[String]
            return cli_args()
        end func
        """
        result = run(source, config=RunConfig(capabilities=set()))
        assert not result.ok
        assert "Capability denied" in (
            result.diagnostics[0].message if result.diagnostics else ""
        )

    def test_cli_args_runs_with_env_cap(self):
        source = """
        @untested("env")
        func main() -> List[String]
            return cli_args()
        end func
        """
        result = run(source, config=RunConfig(capabilities={"env"}))
        assert result.ok
        assert result.value_raw == []

    def test_cli_args_compiles_python(self):
        from geno.compiler import Compiler

        source = """
        @untested("env")
        func main() -> List[String]
            return cli_args()
        end func
        """
        tokens = Lexer(source, "<test>").tokenize()
        program = Parser(tokens).parse_program()
        checker = TypeChecker()
        checker.check_program(program)
        compiler = Compiler()
        py_code = compiler.compile(program)
        assert "cli_args" in py_code

    def test_cli_args_compiles_js(self):
        source = """
        @untested("env")
        func main() -> List[String]
            return cli_args()
        end func
        """
        js = compile_js(source)
        assert "cli_args" in js

    def test_cli_args_builtin_reads_argv(self):
        """Test that builtin_cli_args reads from sys.argv after '--'."""
        import sys

        from geno.builtins import builtin_cli_args

        original = sys.argv[:]
        try:
            sys.argv = ["geno", "run", "test.geno", "--", "hello", "world"]
            assert builtin_cli_args() == ["hello", "world"]
        finally:
            sys.argv = original

    def test_cli_args_builtin_empty_without_separator(self):
        """Test that builtin_cli_args returns [] without '--'."""
        import sys

        from geno.builtins import builtin_cli_args

        original = sys.argv[:]
        try:
            sys.argv = ["geno", "run", "test.geno"]
            assert builtin_cli_args() == []
        finally:
            sys.argv = original

    def test_cli_args_runtime_reads_env_var(self):
        """Test that compiled cli_args reads GENO_CLI_ARGS env var."""
        import json
        import os

        from geno._runtime_support import cli_args as rt_cli_args

        os.environ["GENO_CLI_ARGS"] = json.dumps(["foo", "bar"])
        # Also need env cap
        os.environ["GENO_CAPS_OVERRIDE"] = "env"
        try:
            # cli_args requires cap; set it via the module-level _GENO_CAPS
            import geno._runtime_support as rts

            saved = rts._GENO_CAPS
            rts._GENO_CAPS = {"env"}
            try:
                assert rt_cli_args() == ["foo", "bar"]
            finally:
                rts._GENO_CAPS = saved
        finally:
            os.environ.pop("GENO_CLI_ARGS", None)
            os.environ.pop("GENO_CAPS_OVERRIDE", None)


# =============================================================================
# Type Inference for let/var bindings
# =============================================================================


class TestTypeInferenceLet:
    """Type inference: let x = value infers type from RHS."""

    def test_infer_int(self):
        """let x = 42 infers Int."""
        source = """
        func main() -> Int
            let x = 42
            return x
        end func
        """
        result = run(source)
        assert result.ok
        assert result.value == 42

    def test_infer_string(self):
        """let s = "hello" infers String."""
        source = """
        func main() -> String
            let s = "hello"
            return s
        end func
        """
        result = run(source)
        assert result.ok
        assert result.value == "hello"

    def test_infer_bool(self):
        """let b = true infers Bool."""
        source = """
        func main() -> Bool
            let b = true
            return b
        end func
        """
        result = run(source)
        assert result.ok
        assert result.value is True

    def test_infer_float(self):
        """let f = 3.14 infers Float."""
        source = """
        func main() -> Float
            let f = 3.14
            return f
        end func
        """
        result = run(source)
        assert result.ok
        assert result.value == 3.14

    def test_infer_list(self):
        """let xs = [1, 2, 3] infers List[Int]."""
        source = """
        func main() -> Int
            let xs = [1, 2, 3]
            return length(xs)
        end func
        """
        result = run(source)
        assert result.ok
        assert result.value == 3

    def test_infer_from_function_return(self):
        """let result = some_function() infers from return type."""
        source = """
        func double(x: Int) -> Int
            example 2 -> 4
            return x * 2
        end func

        func main() -> Int
            let result = double(21)
            return result
        end func
        """
        result = run(source)
        assert result.ok
        assert result.value == 42

    def test_explicit_annotation_still_works(self):
        """Explicit annotation takes precedence."""
        source = """
        func main() -> Int
            let x: Int = 42
            return x
        end func
        """
        result = run(source)
        assert result.ok
        assert result.value == 42

    def test_type_mismatch_with_annotation(self):
        """Explicit annotation with wrong type produces error."""
        source = """
        func main() -> Int
            let x: String = 42
            return 0
        end func
        """
        result = run(source)
        assert not result.ok

    def test_infer_nested_expression(self):
        """Inference works on complex expressions."""
        source = """
        func main() -> Int
            let a = 10
            let b = a + 20
            return b
        end func
        """
        result = run(source)
        assert result.ok
        assert result.value == 30


class TestTypeInferenceVar:
    """Type inference for var bindings."""

    def test_infer_var_int(self):
        """var x = 0 infers Int and allows mutation."""
        source = """
        func main() -> Int
            var count = 0
            var count: Int = count + 1
            return count
        end func
        """
        result = run(source)
        assert result.ok
        assert result.value == 1

    def test_infer_var_string(self):
        """var s = "hello" infers String."""
        source = """
        func main() -> String
            var s = "hello"
            var s: String = s + " world"
            return s
        end func
        """
        result = run(source)
        assert result.ok
        assert result.value == "hello world"


class TestTypeInferenceTypecheck:
    """Verify typechecker handles inference correctly."""

    def test_infer_typechecks(self):
        """Inferred let passes typechecker."""
        source = """
        func check_val() -> Int
            example () -> 42
            let x = 42
            return x
        end func
        """
        typecheck(source)

    def test_infer_type_error_on_use(self):
        """Using inferred value in wrong context is caught."""
        source = """
        func check_val() -> String
            example () -> "a"
            let x = 42
            return x
        end func
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_infer_list_element_type(self):
        """Inferred list type tracks element type."""
        source = """
        func check_val() -> Int
            example () -> 1
            let xs = [1, 2, 3]
            return xs[0]
        end func
        """
        typecheck(source)

    def test_infer_from_builtin_call(self):
        """Inference from builtin call return type."""
        source = """
        func check_val() -> Int
            example () -> 3
            let xs = [1, 2, 3]
            let n = length(xs)
            return n
        end func
        """
        typecheck(source)

    def test_infer_rejects_empty_list(self):
        """Inference rejects ambiguous empty collection literals."""
        source = """
        func check_val() -> Int
            example () -> 0
            let xs = []
            return 0
        end func
        """
        with pytest.raises(TypeError, match="Cannot infer a concrete type"):
            typecheck(source)

    def test_infer_rejects_unconstrained_generic_builtin(self):
        """Inference rejects generic zero-arg builtins that collapse to Any."""
        source = """
        func check_val() -> Int
            example () -> 0
            let m = mutable_map_new()
            mutable_map_set(map: m, key: "x", value: "oops")
            let v = unwrap(mutable_map_get(m, "x"))
            return v
        end func
        """
        with pytest.raises(TypeError, match="Cannot infer a concrete type"):
            typecheck(source)


class TestTypeInferenceCompiled:
    """Verify compiled backends handle inferred types."""

    def test_compiled_python_inferred(self):
        """Compiled Python handles inferred let."""
        source = """
        func main() -> Int
            let x = 42
            let y = x + 8
            return y
        end func
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == 50

    def test_compiled_python_inferred_string(self):
        """Compiled Python handles inferred string let."""
        source = """
        func main() -> String
            let greeting = "hello"
            return greeting
        end func
        """
        globals_dict = compile_and_exec(source, timeout=None)
        assert globals_dict["main"]() == "hello"

    def test_compiled_js_inferred(self):
        """Compiled JS handles inferred let."""
        source = """
        func main() -> Int
            let x = 42
            let y = x + 8
            return y
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert "50" in result.stdout


class TestTypeInferenceParsing:
    """Verify the parser handles inference syntax."""

    def test_parse_let_without_annotation(self):
        """Parser accepts let x = value."""
        from geno.ast_nodes import FunctionDef, LetStatement

        source = """
        func main() -> Int
            let x = 42
            return x
        end func
        """
        lexer = Lexer(source, "<test>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        let_stmt = func.body[0]
        assert isinstance(let_stmt, LetStatement)
        assert let_stmt.name == "x"
        assert let_stmt.type_annotation is None

    def test_parse_let_with_annotation(self):
        """Parser still handles let x: Int = value."""
        from geno.ast_nodes import FunctionDef, LetStatement

        source = """
        func main() -> Int
            let x: Int = 42
            return x
        end func
        """
        lexer = Lexer(source, "<test>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        let_stmt = func.body[0]
        assert isinstance(let_stmt, LetStatement)
        assert let_stmt.name == "x"
        assert let_stmt.type_annotation is not None

    def test_parse_var_without_annotation(self):
        """Parser accepts var x = value."""
        from geno.ast_nodes import FunctionDef, VarStatement

        source = """
        func main() -> Int
            var x = 0
            return x
        end func
        """
        lexer = Lexer(source, "<test>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        var_stmt = func.body[0]
        assert isinstance(var_stmt, VarStatement)
        assert var_stmt.name == "x"
        assert var_stmt.type_annotation is None
