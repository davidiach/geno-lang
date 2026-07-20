"""
Tests for the Geno JavaScript Compiler
=======================================
"""

import json
import pathlib
import re
from types import SimpleNamespace
from typing import cast

import pytest

import geno.js_compiler as js_compiler
from geno.ast_nodes import (
    ExpressionStatement,
    IntegerLiteral,
    MatchExpr,
    ReturnStatement,
)
from geno.js_compiler import JSCompileError, JSCompiler, compile_to_js
from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE
from geno.parser import parse
from geno.tests._script_runner import display_main_result_for_test, run_node_code
from geno.typechecker import TypeChecker
from geno.typechecker import TypeError as GenoTypeError

EXAMPLES_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "examples"


def compile_and_run_js(source: str) -> str:
    """Compile Geno to JS, run via Node, return stdout."""
    js_out = compile_to_js(source)
    assert isinstance(js_out, str)
    js_out = display_main_result_for_test(js_out)
    result = run_node_code(js_out, args=("--cap", "print"), timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"JS execution failed: {result.stderr}")
    return cast(str, result.stdout).strip()


@pytest.mark.parametrize("module_name", ["X = 1; console.log('PWNED')", "default"])
def test_compile_project_rejects_unsafe_module_name(module_name):
    program = parse("")
    graph = SimpleNamespace(
        parsed={module_name: program},
        sorted_modules=[module_name],
        project=SimpleNamespace(entrypoint=module_name),
    )

    with pytest.raises(JSCompileError, match=r"module name|reserved keyword"):
        JSCompiler().compile_project(graph)


def _limit_globals(
    *, max_collection_size: int = 5, max_integer_bits: int | None = None
) -> str:
    lines = [f"globalThis.__GENO_MAX_COLLECTION_SIZE = {max_collection_size};"]
    if max_integer_bits is not None:
        lines.append(f"globalThis.__GENO_MAX_INTEGER_BITS = {max_integer_bits};")
    return "\n".join(lines) + "\n"


def run_js_runtime_script(
    script: str, *, max_collection_size: int = 5, max_integer_bits: int | None = None
) -> str:
    js_code = (
        _limit_globals(
            max_collection_size=max_collection_size,
            max_integer_bits=max_integer_bits,
        )
        + f"{JS_RUNTIME_PRELUDE}\n"
        f"{script}"
    )
    result = run_node_code(js_code, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"JS execution failed: {result.stderr}")
    return cast(str, result.stdout).strip()


def run_compiled_js_with_limit(
    source: str, *, max_collection_size: int = 5, max_integer_bits: int | None = None
):
    js_code = (
        _limit_globals(
            max_collection_size=max_collection_size,
            max_integer_bits=max_integer_bits,
        )
        + f"{compile_to_js(source)}"
    )
    return run_node_code(js_code, timeout=10)


def _slow_tree_shake_prelude(user_code: str) -> str:
    """Reference implementation for prelude tree-shaking regressions."""
    if js_compiler._PRELUDE_SECTIONS is None:
        js_compiler._init_prelude_sections()

    assert js_compiler._PRELUDE_SECTIONS is not None
    assert js_compiler._PRELUDE_ALL_NAMES is not None
    assert js_compiler._PRELUDE_SECTION_DEPS is not None

    needed_names: set[str] = set(js_compiler._CORE_NAMES)
    for name in js_compiler._PRELUDE_ALL_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", user_code):
            needed_names.add(name)

    changed = True
    while changed:
        changed = False
        for index, (names, _source) in enumerate(js_compiler._PRELUDE_SECTIONS):
            if names & needed_names:
                for dep_name in js_compiler._PRELUDE_SECTION_DEPS[index]:
                    if dep_name not in needed_names:
                        needed_names.add(dep_name)
                        changed = True

    parts: list[str] = []
    for names, source in js_compiler._PRELUDE_SECTIONS:
        if not names or (names & needed_names):
            parts.append(source)
    return "".join(parts)


# =============================================================================
# Basics
# =============================================================================


class TestJSCompilerBasics:
    def test_integer_return(self):
        source = """
        func main() -> Int
            return 42
        end func
        """
        assert compile_and_run_js(source) == "42"

    def test_float_return(self):
        source = """
        func main() -> Float
            return 3.14
        end func
        """
        assert compile_and_run_js(source) == "3.14"

    def test_float_return_uses_python_exponent_spelling(self):
        source = """
        func main() -> String
            let tiny: Float = 1.0 / 10000000.0
            let large: Float = 10000000000000000.0
            let larger: Float = 1000000000000000000000.0
            return to_string(tiny) + "|" + to_string(large) + "|" + to_string(larger)
        end func
        """
        assert compile_and_run_js(source) == "1e-07|1e+16|1e+21"

    def test_string_return(self):
        source = """
        func main() -> String
            return "hello"
        end func
        """
        assert compile_and_run_js(source) == "hello"

    def test_boolean_return(self):
        source = """
        func main() -> Bool
            return true
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_list_return(self):
        source = """
        func main() -> List[Int]
            return [1, 2, 3]
        end func
        """
        assert compile_and_run_js(source) == "[1, 2, 3]"

    def test_unit_return(self):
        source = """
        func main() -> Unit
            return ()
        end func
        """
        # Unit is null, so nothing is printed
        assert compile_and_run_js(source) == ""


# =============================================================================
# Arithmetic
# =============================================================================


class TestJSCompilerArithmetic:
    def test_addition(self):
        source = """
        func main() -> Int
            return 3 + 4
        end func
        """
        assert compile_and_run_js(source) == "7"

    def test_subtraction(self):
        source = """
        func main() -> Int
            return 10 - 3
        end func
        """
        assert compile_and_run_js(source) == "7"

    def test_multiplication(self):
        source = """
        func main() -> Int
            return 6 * 7
        end func
        """
        assert compile_and_run_js(source) == "42"

    def test_integer_division(self):
        source = """
        func main() -> Int
            return 7 / 2
        end func
        """
        assert compile_and_run_js(source) == "3"

    def test_float_division(self):
        source = """
        func main() -> Float
            return 7.0 / 2.0
        end func
        """
        assert compile_and_run_js(source) == "3.5"

    def test_float_typed_division_from_function_call(self):
        source = """
        func as_float() -> Float
            example () -> 2.0
            return 2.0
        end func

        func main() -> Float
            return 3 / as_float()
        end func
        """
        assert compile_and_run_js(source) == "1.5"

    def test_float_addition_does_not_use_integer_bigint_path(self):
        source = """
        func main() -> Float
            return 9007199254740991.0 + 1.0
        end func
        """
        assert compile_and_run_js(source) == "9007199254740992.0"

    def test_mixed_float_addition_does_not_use_integer_bigint_path(self):
        source = """
        func main() -> Float
            return 9007199254740991.0 + 1
        end func
        """
        assert compile_and_run_js(source) == "9007199254740992.0"

    def test_float_multiplication_does_not_use_integer_bigint_path(self):
        source = """
        func main() -> Float
            return 9007199254740991.0 * 2.0
        end func
        """
        assert compile_and_run_js(source) == "1.8014398509481982e+16"

    def test_float_power_overflow_is_rejected(self):
        source = """
        func main() -> Float
            return 1.5 ** 100000000
        end func
        """
        result = run_compiled_js_with_limit(source)
        assert result.returncode != 0
        assert "Exponentiation result too large" in result.stderr

    def test_negative_float_base_fractional_power_is_rejected(self):
        source = """
        func main() -> Float
            return (0.0 - 1.0) ** 0.5
        end func
        """
        result = run_compiled_js_with_limit(source)
        assert result.returncode != 0
        assert "not a real number" in result.stderr

    def test_negative_float_base_integer_power_still_works(self):
        source = """
        func main() -> Float
            return (0.0 - 2.0) ** 3.0
        end func
        """
        assert compile_and_run_js(source) == "-8.0"

    def test_float_arithmetic_ignores_integer_bit_limit(self):
        source = """
        func main() -> Float
            return 3.0 + 0.0
        end func
        """
        result = run_compiled_js_with_limit(source, max_integer_bits=1)
        assert result.returncode == 0
        assert result.stdout.strip() == "3.0"

    def test_modulo(self):
        source = """
        func main() -> Int
            return 17 % 5
        end func
        """
        assert compile_and_run_js(source) == "2"

    def test_division_negative_operands(self):
        source = """
        func main() -> List[Int]
            return [(0 - 7) / 2, 7 / (0 - 2), (0 - 7) / (0 - 2)]
        end func
        """
        assert compile_and_run_js(source) == "[-3, -3, 3]"

    def test_modulo_negative_operands(self):
        source = """
        func main() -> List[Int]
            return [(0 - 7) % 3, 7 % (0 - 3), (0 - 7) % (0 - 3)]
        end func
        """
        assert compile_and_run_js(source) == "[-1, 1, -1]"

    def test_modulo_negative_float_operand(self):
        source = """
        func main() -> Float
            return (0.0 - 7.5) % 3.0
        end func
        """
        assert compile_and_run_js(source) == "-1.5"

    def test_unary_negation(self):
        source = """
        func main() -> Int
            return -5
        end func
        """
        assert compile_and_run_js(source) == "-5"

    def test_large_int_literal_rejected_for_js_backend(self):
        source = """
        func main() -> Int
            return 9007199254740993
        end func
        """
        with pytest.raises(JSCompileError, match="safe integer range"):
            compile_to_js(source)

    def test_large_negative_int_literal_rejected_for_js_backend(self):
        source = """
        func main() -> Int
            return -9007199254740993
        end func
        """
        with pytest.raises(JSCompileError, match="safe integer range"):
            compile_to_js(source)

    def test_int_literal_honors_configured_bit_limit(self):
        source = """
        func main() -> Int
            return 1099511627776
        end func
        """
        result = run_compiled_js_with_limit(
            source, max_collection_size=100, max_integer_bits=32
        )
        assert result.returncode != 0
        assert "Integer exceeds maximum size" in result.stderr

    def test_int_literal_pattern_honors_configured_bit_limit(self):
        source = """
        func main() -> Int
            let x: Int = 0
            return match x with
                | 1099511627776 -> 1
                | _ -> 0
            end match
        end func
        """
        result = run_compiled_js_with_limit(
            source, max_collection_size=100, max_integer_bits=32
        )
        assert result.returncode != 0
        assert "Integer exceeds maximum size" in result.stderr

    def test_shift_honors_configured_bit_limit(self):
        source = """
        func main() -> Int
            return 1 << 40
        end func
        """
        result = run_compiled_js_with_limit(
            source, max_collection_size=100, max_integer_bits=32
        )
        assert result.returncode != 0
        assert "Left shift amount too large" in result.stderr

    def test_parse_int_honors_configured_bit_limit(self):
        source = """
        func main() -> Option[Int]
            return parse_int("1099511627776")
        end func
        """
        result = run_compiled_js_with_limit(
            source, max_collection_size=100, max_integer_bits=32
        )
        assert result.returncode != 0
        assert "Integer exceeds maximum size" in result.stderr

    def test_float_to_int_honors_configured_bit_limit(self):
        source = """
        func main() -> Int
            return float_to_int(1099511627776.0)
        end func
        """
        result = run_compiled_js_with_limit(source, max_integer_bits=32)
        assert result.returncode != 0
        assert "Integer exceeds maximum size" in result.stderr

    def test_length_fast_path_honors_configured_bit_limit(self):
        source = """
        func main() -> Int
            return length([0, 0, 0])
        end func
        """
        result = run_compiled_js_with_limit(
            source, max_collection_size=100, max_integer_bits=1
        )
        assert result.returncode != 0
        assert "Integer exceeds maximum size" in result.stderr

    def test_host_integer_helpers_honor_configured_bit_limit(self):
        cases = (
            """
            func main() -> Int
                return list_length([0, 0, 0])
            end func
            """,
            """
            func main() -> Int
                return string_index_of("abc", "c")
            end func
            """,
            """
            func main() -> Int
                return char_code("A")
            end func
            """,
            """
            func main() -> List[(Int, Int)]
                return list_enumerate([0, 0, 0])
            end func
            """,
            """
            func is_one(x: Int) -> Bool
                example 1 -> true
                return x == 1
            end func

            func main() -> Option[Int]
                return list_find_index([0, 0, 1], is_one)
            end func
            """,
        )

        for source in cases:
            result = run_compiled_js_with_limit(
                source, max_collection_size=100, max_integer_bits=1
            )
            assert result.returncode != 0
            assert "Integer exceeds maximum size" in result.stderr

    def test_runtime_clock_datetime_helpers_honor_configured_bit_limit(self):
        script = """
function expectThrows(label, fn, fragment) {
    try {
        fn();
    } catch (error) {
        if (String(error.message).includes(fragment)) return;
        throw new Error(label + " wrong error: " + error.message);
    }
    throw new Error(label + " did not throw");
}

_GENO_CAPS.add("clock");
expectThrows("clock_now", () => clock_now(), "Integer exceeds maximum size");
expectThrows("datetime_now", () => datetime_now(), "Integer exceeds maximum size");
expectThrows("datetime_parse", () => datetime_parse("1970-01-03", "%Y-%m-%d"), "Integer exceeds maximum size");
expectThrows("datetime_elapsed", () => datetime_elapsed(0, 3), "Integer exceeds maximum size");
console.log("ok");
"""
        assert run_js_runtime_script(script, max_integer_bits=1) == "ok"

    def test_clock_format_honors_collection_limit(self):
        script = """
function expectThrows(label, fn, fragment) {
    try {
        fn();
    } catch (error) {
        if (String(error.message).includes(fragment)) return;
        throw new Error(label + " wrong error: " + error.message);
    }
    throw new Error(label + " did not throw");
}

_GENO_CAPS.add("clock");
expectThrows("clock_format", () => clock_format(0, "%Y"), "String size exceeds limit");
expectThrows("datetime_format", () => datetime_format(0, "%Y"), "String size exceeds limit");
console.log("ok");
"""
        assert run_js_runtime_script(script, max_collection_size=2) == "ok"

        sources = (
            """
            func main() -> String
                return clock_format(0, "%Y")
            end func
            """,
            """
            func main() -> String
                return datetime_format(0, "%Y")
            end func
            """,
        )
        for source in sources:
            compiled = compile_to_js(source)
            assert isinstance(compiled, str)
            js_code = _limit_globals(max_collection_size=2) + compiled
            result = run_node_code(js_code, args=("--cap", "clock"), timeout=10)
            assert result.returncode != 0
            assert "String size exceeds limit" in result.stderr

    def test_runtime_app_input_helpers_honor_configured_bit_limit(self):
        script = """
function expectThrows(label, fn, fragment) {
    try {
        fn();
    } catch (error) {
        if (String(error.message).includes(fragment)) return;
        throw new Error(label + " wrong error: " + error.message);
    }
    throw new Error(label + " did not throw");
}

expectThrows("screen_width", () => screen_width(), "Integer exceeds maximum size");
expectThrows("screen_height", () => screen_height(), "Integer exceeds maximum size");
_geno_mouse_x = 3;
_geno_mouse_y = 3;
expectThrows("mouse_x", () => mouse_x(), "Integer exceeds maximum size");
expectThrows("mouse_y", () => mouse_y(), "Integer exceeds maximum size");
console.log("ok");
"""
        assert run_js_runtime_script(script, max_integer_bits=1) == "ok"

    def test_runtime_range_helper_honors_configured_bit_limit(self):
        script = """
try {
    range_(0, 3);
} catch (error) {
    if (String(error.message).includes("Integer exceeds maximum size")) {
        console.log("ok");
    } else {
        throw error;
    }
}
"""
        assert run_js_runtime_script(script, max_integer_bits=1) == "ok"

    def test_runtime_range_and_substring_validate_argument_types(self):
        script = """
function expectThrows(label, fn, fragment) {
    try {
        fn();
    } catch (error) {
        if (String(error.message).includes(fragment)) return;
        throw new Error(label + " wrong error: " + error.message);
    }
    throw new Error(label + " did not throw");
}

expectThrows("range start", () => range_(1.2, 3), "range start must be an integer");
expectThrows("range end", () => range_(1, 3.5), "range end must be an integer");
expectThrows("range step", () => range_(1, 3, "x"), "range step must be an integer");
expectThrows("substring text", () => substring(42, 0, 1), "substring expects string");
expectThrows("substring start", () => substring("abc", 1.2, 2), "substring start must be an integer");
expectThrows("substring stop", () => substring("abc", 1, 2.5), "substring stop must be an integer");
expectThrows("string_substring text", () => string_substring(42, 0, 1), "string_substring text must be a string");
expectThrows("string_substring stop", () => string_substring("abc", 0, 2.5), "string_substring stop must be an integer");
console.log("ok");
"""
        assert run_js_runtime_script(script, max_collection_size=100) == "ok"

    def test_case_helpers_honor_collection_limit_on_unicode_expansion(self):
        cases = (
            """
            func main() -> String
                return to_upper(from_char_code(223))
            end func
            """,
            """
            func main() -> String
                return string_to_upper(from_char_code(223))
            end func
            """,
            """
            func main() -> String
                return to_lower(from_char_code(304))
            end func
            """,
            """
            func main() -> String
                return string_to_lower(from_char_code(304))
            end func
            """,
        )

        for source in cases:
            result = run_compiled_js_with_limit(source, max_collection_size=1)
            assert result.returncode != 0
            assert "String size exceeds limit" in result.stderr

    def test_path_join_honors_collection_limit(self):
        source = """
        func main() -> String
            return path_join("a", "b")
        end func
        """
        result = run_compiled_js_with_limit(source, max_collection_size=2)
        assert result.returncode != 0
        assert "String size exceeds limit" in result.stderr

    def test_int_addition_over_safe_range_raises(self):
        source = """
        func main() -> Int
            return 9007199254740991 + 1
        end func
        """
        with pytest.raises(RuntimeError, match="safe integer range"):
            compile_and_run_js(source)

    def test_int_subtraction_over_safe_range_raises(self):
        source = """
        func main() -> Int
            return -9007199254740991 - 1
        end func
        """
        with pytest.raises(RuntimeError, match="safe integer range"):
            compile_and_run_js(source)

    def test_int_power_over_safe_range_raises(self):
        source = """
        func main() -> Int
            return 2 ** 60
        end func
        """
        with pytest.raises(RuntimeError, match="safe integer range"):
            compile_and_run_js(source)

    def test_negative_exponent_returns_float(self):
        source = """
        func main() -> Float
            return 2 ** -1
        end func
        """
        assert compile_and_run_js(source) == "0.5"

    def test_mixed_float_power_uses_float_path(self):
        source = """
        func main() -> Float
            return 2.0 ** 3
        end func
        """
        assert compile_and_run_js(source) == "8.0"

    def test_zero_base_negative_exponent_raises_division_by_zero(self):
        source = """
        func main() -> Float
            return 0 ** -1
        end func
        """
        with pytest.raises(RuntimeError, match="Division by zero"):
            compile_and_run_js(source)

    def test_float_zero_base_negative_exponent_raises_division_by_zero(self):
        source = """
        func main() -> Float
            return 0.0 ** -1.0
        end func
        """
        with pytest.raises(RuntimeError, match="Division by zero"):
            compile_and_run_js(source)

    def test_zero_base_negative_exponent_caught_by_try_catch(self):
        source = """
        func main() -> String
            try
                let value: Float = 0 ** -1
                return to_string(value)
            catch e: String
                return "caught"
            end try
        end func
        """
        assert compile_and_run_js(source) == "caught"

    def test_operator_precedence(self):
        source = """
        func main() -> Int
            return 2 + 3 * 4
        end func
        """
        assert compile_and_run_js(source) == "14"

    def test_comparison_less(self):
        source = """
        func main() -> Bool
            return 3 < 5
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_comparison_greater(self):
        source = """
        func main() -> Bool
            return 5 > 3
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_equality(self):
        source = """
        func main() -> Bool
            return 5 == 5
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_inequality(self):
        source = """
        func main() -> Bool
            return 5 != 3
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_bitwise_or_large_value(self):
        source = """
        func main() -> Int
            let large: Int = 1 << 40
            return bit_or(large, 1)
        end func
        """
        assert compile_and_run_js(source) == "1099511627777"


# =============================================================================
# Logical
# =============================================================================


class TestJSCompilerLogical:
    def test_and(self):
        source = """
        func main() -> Bool
            return true and true
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_or(self):
        source = """
        func main() -> Bool
            return false or true
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_not(self):
        source = """
        func main() -> Bool
            return not false
        end func
        """
        assert compile_and_run_js(source) == "true"


# =============================================================================
# Variables
# =============================================================================


class TestJSCompilerVariables:
    def test_let_binding(self):
        source = """
        func main() -> Int
            let x: Int = 5
            return x
        end func
        """
        assert compile_and_run_js(source) == "5"

    def test_var_binding(self):
        source = """
        func main() -> Int
            var x: Int = 5
            x = 10
            return x
        end func
        """
        assert compile_and_run_js(source) == "10"

    def test_multiple_bindings(self):
        source = """
        func main() -> Int
            let a: Int = 1
            let b: Int = 2
            let c: Int = 3
            return a + b + c
        end func
        """
        assert compile_and_run_js(source) == "6"


# =============================================================================
# Control Flow
# =============================================================================


class TestJSCompilerControlFlow:
    def test_if_true(self):
        source = """
        func main() -> Int
            if true then
                return 1
            else
                return 0
            end if
        end func
        """
        assert compile_and_run_js(source) == "1"

    def test_if_false(self):
        source = """
        func main() -> Int
            if false then
                return 1
            else
                return 0
            end if
        end func
        """
        assert compile_and_run_js(source) == "0"

    def test_while_loop(self):
        source = """
        func main() -> Int
            var sum: Int = 0
            var i: Int = 1
            while i <= 5 do
                sum = sum + i
                i = i + 1
            end while
            return sum
        end func
        """
        assert compile_and_run_js(source) == "15"

    def test_for_loop(self):
        source = """
        func main() -> Int
            var sum: Int = 0
            for x: Int in [1, 2, 3, 4, 5] do
                sum = sum + x
            end for
            return sum
        end func
        """
        assert compile_and_run_js(source) == "15"


class TestJSCompilerComprehensions:
    def test_filtered_comprehension_evaluates_condition_then_element_per_item(self):
        source = """
        func keep(x: Int) -> Bool with throw
            example 0 -> true
            if x == 2 then
                throw "keep2"
            end if
            return true
        end func

        func emit(x: Int) -> Int with throw
            example 0 -> 0
            if x == 1 then
                throw "emit1"
            end if
            return x
        end func

        func main() -> String
            try
                let xs: List[Int] = [emit(x) for x: Int in [1, 2] if keep(x)]
                return "ok"
            catch e: String
                return e
            end try
        end func
        """
        assert compile_and_run_js(source) == "emit1"


# =============================================================================
# Functions
# =============================================================================


class TestJSCompilerFunctions:
    def test_simple_function(self):
        source = """
        func double(x: Int) -> Int
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            return double(21)
        end func
        """
        assert compile_and_run_js(source) == "42"

    def test_recursive_function(self):
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
            return factorial(5)
        end func
        """
        assert compile_and_run_js(source) == "120"

    def test_named_arguments(self):
        source = """
        func greet(name: String, greeting: String) -> String
            example "Alice", "Hi" -> "Hi"
            return greeting
        end func

        func main() -> String
            return greet(greeting: "Hello", name: "World")
        end func
        """
        assert compile_and_run_js(source) == "Hello"

    def test_higher_order_function(self):
        source = """
        func apply(f: (Int) -> Int, x: Int) -> Int
            example fn(y: Int) -> y, 5 -> 5
            return f(x)
        end func

        func main() -> Int
            return apply(fn(x: Int) -> x * 2, 21)
        end func
        """
        assert compile_and_run_js(source) == "42"


# =============================================================================
# Lambdas
# =============================================================================


class TestJSCompilerLambdas:
    def test_simple_lambda(self):
        source = """
        func main() -> Int
            let f: (Int) -> Int = fn(x: Int) -> x * 2
            return f(21)
        end func
        """
        assert compile_and_run_js(source) == "42"

    def test_lambda_in_map(self):
        source = """
        func main() -> List[Int]
            return map([1, 2, 3], fn(x: Int) -> x * x)
        end func
        """
        assert compile_and_run_js(source) == "[1, 4, 9]"

    def test_lambda_closure(self):
        source = """
        func main() -> Int
            let multiplier: Int = 10
            let f: (Int) -> Int = fn(x: Int) -> x * multiplier
            return f(4)
        end func
        """
        assert compile_and_run_js(source) == "40"


# =============================================================================
# Pipeline
# =============================================================================


class TestJSCompilerPipeline:
    def test_simple_pipeline(self):
        source = """
        func main() -> Int
            return [1, 2, 3, 4, 5] |> length
        end func
        """
        assert compile_and_run_js(source) == "5"

    def test_pipeline_with_placeholder(self):
        source = """
        func main() -> List[Int]
            return [1, 2, 3, 4, 5] |> filter(_, fn(x: Int) -> x > 2)
        end func
        """
        assert compile_and_run_js(source) == "[3, 4, 5]"

    def test_chained_pipeline(self):
        source = """
        func main() -> Int
            return [1, 2, 3, 4, 5]
                |> filter(_, fn(x: Int) -> x > 2)
                |> length
        end func
        """
        assert compile_and_run_js(source) == "3"

    def test_pipeline_placeholder_materializes_piped_value_once(self):
        source = """
        func bump(counter: Array[Int]) -> Int with mutation
            example array_from_list([0]) -> 1
            var mutable_counter: Array[Int] = counter
            mutable_counter[0] = mutable_counter[0] + 1
            return mutable_counter[0]
        end func

        func add(a: Int, b: Int) -> Int
            example (1, 2) -> 3
            return a + b
        end func

        func main() -> Int
            let counter: Array[Int] = array_from_list([0])
            return bump(counter) |> add(_, _)
        end func
        """
        assert compile_and_run_js(source) == "2"

    def test_pipeline_placeholder_preserves_argument_order(self):
        source = """
        func bump(counter: Array[Int]) -> Int with mutation
            example array_from_list([0]) -> 1
            var mutable_counter: Array[Int] = counter
            mutable_counter[0] = mutable_counter[0] + 1
            return mutable_counter[0]
        end func

        func pair(a: Int, b: Int) -> Int
            example (2, 1) -> 21
            return a * 10 + b
        end func

        func main() -> Int
            let counter: Array[Int] = array_from_list([0])
            return bump(counter) |> pair(bump(counter), _)
        end func
        """
        assert compile_and_run_js(source) == "21"


# =============================================================================
# Pattern Matching
# =============================================================================


class TestJSCompilerPatternMatching:
    def test_match_some(self):
        source = """
        func unwrap_opt(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func

        func main() -> Int
            return unwrap_opt(Some(42))
        end func
        """
        assert compile_and_run_js(source) == "42"

    def test_match_none(self):
        source = """
        func unwrap_opt(opt: Option[Int]) -> Int
            example None -> 0
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func

        func main() -> Int
            return unwrap_opt(None)
        end func
        """
        assert compile_and_run_js(source) == "0"

    def test_match_literal(self):
        source = """
        func classify(x: Int) -> String
            example 0 -> "zero"
            match x with
                | 0 -> return "zero"
                | 1 -> return "one"
                | _ -> return "many"
            end match
        end func

        func main() -> String
            return classify(0)
        end func
        """
        assert compile_and_run_js(source) == "zero"

    def test_large_int_literal_pattern_rejected_for_js_backend(self):
        source = """
        func main() -> Int
            let x: Int = 0
            match x with
                | 9007199254740993 -> return 1
                | _ -> return 0
            end match
        end func
        """
        with pytest.raises(JSCompileError, match="safe integer range"):
            compile_to_js(source)

    def test_match_wildcard(self):
        source = """
        func classify(x: Int) -> String
            example 99 -> "other"
            match x with
                | 0 -> return "zero"
                | _ -> return "other"
            end match
        end func

        func main() -> String
            return classify(99)
        end func
        """
        assert compile_and_run_js(source) == "other"

    def test_match_expression(self):
        source = """
        func classify(x: Int) -> String
            example 0 -> "zero"
            return match x with
                | 0 -> "zero"
                | _ -> "other"
            end match
        end func

        func main() -> String
            return classify(0)
        end func
        """
        assert compile_and_run_js(source) == "zero"

    def test_match_temp_avoids_user_binding(self):
        source = """
        func choose(x: Int) -> Int
            example 1 -> 42
            let _temp_1: Int = 41
            match x with
                | 1 -> return _temp_1 + 1
                | _ -> return 0
            end match
        end func

        func main() -> Int
            return choose(1)
        end func
        """
        js_code = compile_to_js(source, source_map=False)
        assert "const _temp_2 = x;" in js_code
        assert compile_and_run_js(source) == "42"


# =============================================================================
# User-defined Types
# =============================================================================


class TestJSCompilerTypes:
    def test_nullary_adt(self):
        source = """
        type Color = Red | Green | Blue

        func color_name(c: Color) -> String
            example Red -> "red"
            match c with
                | Red -> return "red"
                | Green -> return "green"
                | Blue -> return "blue"
            end match
        end func

        func main() -> String
            return color_name(Red)
        end func
        """
        assert compile_and_run_js(source) == "red"

    def test_adt_with_fields(self):
        source = """
        type Shape = Circle(radius: Float) | Rectangle(width: Float, height: Float)

        func area(s: Shape) -> Float
            example Circle(1.0) -> 3.141592653589793
            match s with
                | Circle(r) ->
                    return 3.141592653589793 * r * r
                | Rectangle(w, h) ->
                    return w * h
            end match
        end func

        func main() -> Float
            return area(Rectangle(3.0, 4.0))
        end func
        """
        assert compile_and_run_js(source) == "12.0"

    def test_option_some_none(self):
        source = """
        func get_or_default(opt: Option[Int], default_val: Int) -> Int
            example Some(5), 0 -> 5
            example None, 0 -> 0
            match opt with
                | Some(x) -> return x
                | None -> return default_val
            end match
        end func

        func main() -> List[Int]
            return [get_or_default(Some(42), 0), get_or_default(None, 99)]
        end func
        """
        assert compile_and_run_js(source) == "[42, 99]"

    def test_result_ok_err(self):
        source = """
        func safe_div(a: Int, b: Int) -> Result[Int, String]
            example 10, 2 -> Ok(5)
            if b == 0 then
                return Err("division by zero")
            else
                return Ok(a / b)
            end if
        end func

        func is_ok(r: Result[Int, String]) -> Bool
            example Ok(5) -> true
            match r with
                | Ok(_) -> return true
                | Err(_) -> return false
            end match
        end func

        func main() -> List[Bool]
            return [is_ok(safe_div(10, 2)), is_ok(safe_div(10, 0))]
        end func
        """
        assert compile_and_run_js(source) == "[true, false]"

    def test_tuple_creation(self):
        source = """
        func main() -> List[Int]
            let a: Int = 1
            let b: Int = 2
            return [a, b]
        end func
        """
        assert compile_and_run_js(source) == "[1, 2]"

    def test_field_assignment_on_constructor(self):
        """Field assignment on constructor-created objects must work (not frozen)."""
        source = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            example () -> 10
            var p: Point = Point(3, 4)
            p.x = 10
            return p.x
        end func
        """
        assert compile_and_run_js(source) == "10"

    def test_field_assignment_to_let_rejected_before_codegen(self):
        source = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            let p: Point = Point(3, 4)
            p.x = 10
            return p.x
        end func
        """
        with pytest.raises(GenoTypeError, match="immutable variable: p"):
            compile_to_js(source)

    def test_contract_failure_bypasses_string_catch(self):
        source = """
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
            compile_and_run_js(source)

    def test_with_result_preserves_fields(self):
        """Values created by `with` preserve updated and original fields."""
        source = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            let p: Point = Point(1, 2) with (x: 3)
            return p.x + p.y
        end func
        """
        assert compile_and_run_js(source) == "5"

    def test_bound_with_result_remains_mutable(self):
        """Binding a `with` result must preserve frozen ADT semantics."""
        source = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            var p: Point = Point(1, 2) with (x: 3)
            p.x = 4
            return p.x
        end func
        """
        assert compile_and_run_js(source) == "4"


# =============================================================================
# Builtins
# =============================================================================


class TestJSCompilerBuiltins:
    def test_length(self):
        source = """
        func main() -> Int
            return length([1, 2, 3])
        end func
        """
        assert compile_and_run_js(source) == "3"

    def test_is_permutation_uses_structural_equality(self):
        source = """
        func main() -> Unit
            print(is_permutation([1, 2, 1], [2, 1, 1]))
            print(is_permutation([Some(1), None], [None, Some(1)]))
            print(is_permutation([Some(1), Some(1)], [Some(1), None]))
            return ()
        end func
        """
        assert compile_and_run_js(source) == "true\ntrue\nfalse"

    def test_list_pair_builtins_return_tuples(self):
        source = """
        func main() -> Int
            let zipped: List[(Int, Int)] = list_zip([1], [2])
            let numbered: List[(Int, String)] = list_enumerate(["hi"])
            let (left, right): (Int, Int) = zipped[0]
            let (idx, text): (Int, String) = numbered[0]
            return left + right + idx + length(text)
        end func
        """
        assert compile_and_run_js(source) == "5"

    def test_typed_length_emits_inline_fast_path(self):
        source = """
        func main() -> Int
            return length([1, 2, 3])
        end func
        """
        js_code = compile_to_js(source)
        assert (
            "return _checkIntegerBits((_checkCollectionSize([_checkCollectionSize(1), "
            "_checkCollectionSize(2), _checkCollectionSize(3)])).length);" in js_code
        )

    def test_typed_string_length_emits_unicode_helper(self):
        source = """
        func main() -> Int
            return length("hello")
        end func
        """
        js_code = compile_to_js(source)
        assert (
            'return _checkIntegerBits(_stringLength(_checkCollectionSize("hello")));'
            in js_code
        )

    def test_user_defined_length_is_not_inlined(self):
        source = """
        func length(xs: List[Int]) -> Int
            example [1] -> 42
            return 42
        end func

        func main() -> Int
            return length([1, 2, 3])
        end func
        """
        js_code = compile_to_js(source)
        assert (
            "return length(_checkCollectionSize([_checkCollectionSize(1), "
            "_checkCollectionSize(2), _checkCollectionSize(3)]));" in js_code
        )
        assert compile_and_run_js(source) == "42"

    def test_local_length_binding_is_not_inlined(self):
        source = """
        func main() -> Int
            let length: (List[Int]) -> Int = fn(xs: List[Int]) -> 42
            return length([1, 2, 3])
        end func
        """
        js_code = compile_to_js(source)
        assert (
            "return length(_checkCollectionSize([_checkCollectionSize(1), "
            "_checkCollectionSize(2), _checkCollectionSize(3)]));" in js_code
        )
        assert compile_and_run_js(source) == "42"

    def test_head(self):
        source = """
        func main() -> Int
            return head([1, 2, 3])
        end func
        """
        assert compile_and_run_js(source) == "1"

    def test_tail(self):
        source = """
        func main() -> List[Int]
            return tail([1, 2, 3])
        end func
        """
        assert compile_and_run_js(source) == "[2, 3]"

    def test_append(self):
        source = """
        func main() -> List[Int]
            return append([1, 2], 3)
        end func
        """
        assert compile_and_run_js(source) == "[1, 2, 3]"

    def test_append_supports_async_operands(self):
        source = """
        async func items() -> List[Int]
            return [1, 2]
        end func

        async func main() -> List[Int]
            return append(await items(), 3)
        end func
        """
        assert compile_and_run_js(source) == "[1, 2, 3]"

    def test_user_defined_append_is_not_inlined(self):
        source = """
        func append(xs: List[Int], item: Int) -> List[Int]
            example [1], 2 -> [2]
            return [item]
        end func

        func main() -> List[Int]
            return append([1, 2], 3)
        end func
        """
        js_code = compile_to_js(source)
        assert (
            "return append(_checkCollectionSize([_checkCollectionSize(1), "
            "_checkCollectionSize(2)]), _checkCollectionSize(3));" in js_code
        )
        assert compile_and_run_js(source) == "[3]"

    def test_concat(self):
        source = """
        func main() -> List[Int]
            return concat([1, 2], [3, 4])
        end func
        """
        assert compile_and_run_js(source) == "[1, 2, 3, 4]"

    def test_set_at(self):
        source = """
        func main() -> List[Int]
            return set_at(list: [1, 2, 3], index: 1, value: 9)
        end func
        """
        assert compile_and_run_js(source) == "[1, 9, 3]"

    def test_filter(self):
        source = """
        func main() -> List[Int]
            return filter([1, 2, 3, 4], fn(x: Int) -> x % 2 == 0)
        end func
        """
        assert compile_and_run_js(source) == "[2, 4]"

    def test_map_function(self):
        source = """
        func main() -> List[Int]
            return map([1, 2, 3], fn(x: Int) -> x * 2)
        end func
        """
        assert compile_and_run_js(source) == "[2, 4, 6]"

    def test_fold(self):
        source = """
        func main() -> Int
            return fold(list: [1, 2, 3, 4], initial: 0, reducer: fn(acc: Int, x: Int) -> acc + x)
        end func
        """
        assert compile_and_run_js(source) == "10"

    def test_contains(self):
        source = """
        func main() -> Bool
            return contains([1, 2, 3], 2)
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_reverse(self):
        source = """
        func main() -> List[Int]
            return reverse([1, 2, 3])
        end func
        """
        assert compile_and_run_js(source) == "[3, 2, 1]"

    def test_take_while(self):
        source = """
        func main() -> List[Int]
            return take_while([1, 2, 3, 4, 5], fn(x: Int) -> x < 4)
        end func
        """
        assert compile_and_run_js(source) == "[1, 2, 3]"

    def test_all(self):
        source = """
        func main() -> Bool
            return all([1, 2, 3], fn(x: Int) -> x > 0)
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_slice(self):
        source = """
        func main() -> List[Int]
            return slice(list: [1, 2, 3, 4, 5], start: 1, stop: 4)
        end func
        """
        assert compile_and_run_js(source) == "[2, 3, 4]"

    def test_split(self):
        source = """
        func main() -> List[String]
            return split("a,b,c", ",")
        end func
        """
        assert compile_and_run_js(source) == '["a", "b", "c"]'

    def test_join(self):
        source = """
        func main() -> String
            return join(["a", "b", "c"], "-")
        end func
        """
        assert compile_and_run_js(source) == "a-b-c"

    def test_trim(self):
        source = """
        func main() -> String
            return trim("  hello  ")
        end func
        """
        assert compile_and_run_js(source) == "hello"

    def test_to_lower(self):
        source = """
        func main() -> String
            return to_lower("HELLO")
        end func
        """
        assert compile_and_run_js(source) == "hello"

    def test_starts_with(self):
        source = """
        func main() -> Bool
            return starts_with("hello world", "hello")
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_ends_with(self):
        source = """
        func main() -> Bool
            return ends_with("hello world", "world")
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_string_char_at(self):
        source = """
        func main() -> String
            return string_char_at("abc", 1)
        end func
        """
        assert compile_and_run_js(source) == "b"

    def test_unicode_string_length_uses_code_points(self):
        source = """
        func main() -> Int
            return length(from_char_code(128512))
        end func
        """
        assert compile_and_run_js(source) == "1"

    def test_unicode_string_indexing_uses_code_points(self):
        source = """
        func main() -> Int
            let s: String = from_char_code(128512) + "x"
            return char_code(s[0])
        end func
        """
        assert compile_and_run_js(source) == "128512"

    def test_unicode_string_char_at_uses_code_points(self):
        source = """
        func main() -> Int
            let s: String = from_char_code(128512) + "x"
            return char_code(string_char_at(text: s, index: 0))
        end func
        """
        assert compile_and_run_js(source) == "128512"

    def test_unicode_substring_uses_code_points(self):
        source = """
        func main() -> Int
            let s: String = from_char_code(128512) + "x"
            let head: String = substring(text: s, start: 0, stop: 1)
            return char_code(head) + length(head)
        end func
        """
        assert compile_and_run_js(source) == "128513"

    def test_unicode_string_index_of_uses_code_points(self):
        source = """
        func main() -> Int
            let s: String = from_char_code(128512) + "xy" + from_char_code(128512)
            return (string_index_of(text: s, substring: "x") * 10) + string_last_index_of(text: s, substring: from_char_code(128512))
        end func
        """
        assert compile_and_run_js(source) == "13"

    def test_to_chars(self):
        source = """
        func main() -> List[String]
            return to_chars("abc")
        end func
        """
        assert compile_and_run_js(source) == '["a", "b", "c"]'

    def test_sort_strings(self):
        source = """
        func main() -> List[String]
            return sort_strings(["c", "a", "b"])
        end func
        """
        assert compile_and_run_js(source) == '["a", "b", "c"]'

    def test_substring(self):
        source = """
        func main() -> String
            return substring(text: "hello world", start: 0, stop: 5)
        end func
        """
        assert compile_and_run_js(source) == "hello"

    def test_string_substring(self):
        source = """
        func main() -> String
            return string_substring(text: "hello world", start: 0, stop: 5)
        end func
        """
        assert compile_and_run_js(source) == "hello"

    def test_string_substring_does_not_swap_bounds(self):
        source = """
        func main() -> String
            return string_substring(text: "hello", start: 4, stop: 2)
        end func
        """
        assert compile_and_run_js(source) == ""

    def test_split_once_found(self):
        source = """
        func main() -> Bool
            return is_some(split_once("a=b", "="))
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_split_once_not_found(self):
        source = """
        func main() -> Bool
            return is_none(split_once("abc", "="))
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_set_to_list_sort_order(self):
        """set_to_list comparator must sort numbers before strings correctly."""
        source = """
        func main() -> List[Int]
            let s: Set[Int] = set_from_list([3, 1, 2])
            return set_to_list(s)
        end func
        """
        assert compile_and_run_js(source) == "[1, 2, 3]"


class TestJSCompilerMath:
    def test_add(self):
        source = """
        func main() -> Int
            return add(3, 4)
        end func
        """
        assert compile_and_run_js(source) == "7"

    def test_subtract(self):
        source = """
        func main() -> Int
            return subtract(10, 3)
        end func
        """
        assert compile_and_run_js(source) == "7"

    def test_multiply(self):
        source = """
        func main() -> Int
            return multiply(6, 7)
        end func
        """
        assert compile_and_run_js(source) == "42"

    def test_divide(self):
        source = """
        func main() -> Int
            return divide(10, 3)
        end func
        """
        assert compile_and_run_js(source) == "3"

    def test_divide_negative_operands(self):
        source = """
        func main() -> List[Int]
            return [divide(0 - 7, 2), divide(7, 0 - 2)]
        end func
        """
        assert compile_and_run_js(source) == "[-3, -3]"

    def test_sqrt(self):
        source = """
        func main() -> Float
            return sqrt(9.0)
        end func
        """
        assert compile_and_run_js(source) == "3.0"

    def test_max(self):
        source = """
        func main() -> Int
            return max(3, 7)
        end func
        """
        assert compile_and_run_js(source) == "7"

    def test_abs(self):
        source = """
        func main() -> Int
            return abs(-5)
        end func
        """
        assert compile_and_run_js(source) == "5"

    def test_square(self):
        source = """
        func main() -> Int
            return square(7)
        end func
        """
        assert compile_and_run_js(source) == "49"


class TestJSCompilerConversions:
    def test_parse_int_valid(self):
        source = """
        func main() -> Bool
            return is_some(parse_int("42"))
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_parse_int_invalid(self):
        source = """
        func main() -> Bool
            return is_none(parse_int("abc"))
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_parse_int_out_of_range_returns_none(self):
        source = """
        func main() -> Bool
            return is_none(parse_int("9007199254740993"))
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_parse_int_overlong_input_raises(self):
        source = """
        func main() -> String
            match parse_int(repeat_string("1", 1001)) with
                | Some(v) -> return "some"
                | None -> return "none"
            end match
        end func
        """
        with pytest.raises(RuntimeError, match="parse_int: input string too long"):
            compile_and_run_js(source)

    def test_parse_float_overlong_input_raises(self):
        source = """
        func main() -> String
            match parse_float(repeat_string("1", 1001)) with
                | Some(v) -> return "some"
                | None -> return "none"
            end match
        end func
        """
        with pytest.raises(RuntimeError, match="parse_float: input string too long"):
            compile_and_run_js(source)

    def test_to_string(self):
        source = """
        func main() -> String
            return to_string(42)
        end func
        """
        assert compile_and_run_js(source) == "42"

    def test_to_string_honors_collection_limit(self):
        source = """
        func main() -> String
            return to_string([1, 2])
        end func
        """
        result = run_compiled_js_with_limit(source, max_collection_size=2)
        assert result.returncode != 0
        assert "String size exceeds limit" in result.stderr

    def test_higher_order_to_string_fast_paths_honor_collection_limit(self):
        cases = (
            """
            func main() -> Option[String]
                return option_map(Some([1, 2]), to_string)
            end func
            """,
            """
            func main() -> Result[String, String]
                return result_map(Ok([1, 2]), to_string)
            end func
            """,
            """
            func main() -> Result[String, String]
                return result_map_err(Err([1, 2]), to_string)
            end func
            """,
            """
            func main() -> Map[String, String]
                let m: Map[String, List[Int]] = map_from_entries([("a", [1, 2])])
                return map_map_values(m, to_string)
            end func
            """,
        )

        for source in cases:
            result = run_compiled_js_with_limit(source, max_collection_size=2)
            assert result.returncode != 0
            assert "String size exceeds limit" in result.stderr

    def test_to_string_preserves_float_tuple_and_constructor_format(self):
        source = """
        func main() -> String
            return to_string(1.0) + "|" + to_string((1, 2)) + "|" + to_string(Some(1)) + "|" + to_string(Some("hi"))
        end func
        """
        assert (
            compile_and_run_js(source) == '1.0|(1, 2)|Some(value: 1)|Some(value: "hi")'
        )

    def test_fstring_uses_geno_stringify_format(self):
        source = """
        func main() -> String
            let msg: String = "hi"
            return f"{1.0}|{(1, 2)}|{Some(msg)}"
        end func
        """
        assert compile_and_run_js(source) == '1.0|(1, 2)|Some(value: "hi")'

    def test_to_string_resolves_type_aliases_for_formatting(self):
        source = """
        type Pair = Tuple[Int, Int]
        type Score = Float
        type Box = Box(pair: Pair)

        func score() -> Score
            example () -> 5.0
            return 5.0
        end func

        func main() -> String
            return to_string(Box((1, 2))) + "|" + to_string(score())
        end func
        """
        assert compile_and_run_js(source) == "Box(pair: (1, 2))|5.0"

    def test_main_return_type_alias_preserves_float_format(self):
        source = """
        type Score = Float

        func main() -> Score
            return 5.0
        end func
        """
        assert compile_and_run_js(source) == "5.0"

    def test_float_to_int(self):
        source = """
        func main() -> Int
            return float_to_int(3.7)
        end func
        """
        assert compile_and_run_js(source) == "3"

    def test_float_to_int_out_of_range_raises(self):
        source = """
        func main() -> Int
            return float_to_int(9007199254740992.0)
        end func
        """
        with pytest.raises(RuntimeError, match="safe integer range"):
            compile_and_run_js(source)

    def test_int_to_float(self):
        source = """
        func main() -> Float
            return int_to_float(5)
        end func
        """
        assert compile_and_run_js(source) == "5.0"

    def test_math_stdlib_helpers_reject_unsafe_int_results(self):
        js_code = compile_to_js(
            """
            func main() -> Unit
                return ()
            end func
            """
        )
        assert isinstance(js_code, str)
        js_code += """
_GENO_CAPS.add("random");
const checks = [];
for (const thunk of [
    () => math_floor(9007199254740992),
    () => math_ceil(9007199254740992),
    () => math_round(9007199254740992),
    () => math_random_int(9007199254740992, 9007199254740992),
]) {
    try {
        thunk();
        checks.push(false);
    } catch (error) {
        checks.push(String(error.message).includes("safe integer range"));
    }
}
console.log(JSON.stringify(checks));
"""
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines()[-1] == "[true,true,true,true]"


class TestJSCompilerOptionOps:
    def test_is_some(self):
        source = """
        func main() -> Bool
            return is_some(Some(5))
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_is_none(self):
        source = """
        func main() -> Bool
            return is_none(None)
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_unwrap(self):
        source = """
        func main() -> Int
            return unwrap(Some(42))
        end func
        """
        assert compile_and_run_js(source) == "42"

    def test_unwrap_or(self):
        source = """
        func main() -> Int
            return unwrap_or(None, 99)
        end func
        """
        assert compile_and_run_js(source) == "99"


class TestJSCompilerPrint:
    def test_print(self):
        source = """
        func main() -> Int
            print("hello world")
            return 0
        end func
        """
        output = compile_and_run_js(source)
        assert "hello world" in output

    def test_print_recursive_user_type_preserves_float_format(self):
        source = """
        type FloatList = Cons(head: Float, tail: FloatList) | Nil

        func main() -> String
            print(Cons(1.0, Cons(2.0, Nil)))
            return to_string(Cons(1.0, Cons(2.0, Nil)))
        end func
        """
        assert compile_and_run_js(source).splitlines() == [
            "Cons(head: 1.0, tail: Cons(head: 2.0, tail: Nil))",
            "Cons(head: 1.0, tail: Cons(head: 2.0, tail: Nil))",
        ]


# =============================================================================
# Specifications
# =============================================================================


class TestJSCompilerSpecs:
    def test_requires_passing(self):
        source = """
        func positive(x: Int) -> Int
            requires x > 0
            example 5 -> 5
            return x
        end func

        func main() -> Int
            return positive(5)
        end func
        """
        assert compile_and_run_js(source) == "5"

    def test_requires_failing(self):
        source = """
        func positive(x: Int) -> Int
            requires x > 0
            example 5 -> 5
            return x
        end func

        func main() -> Int
            return positive(0 - 5)
        end func
        """
        with pytest.raises(RuntimeError, match="Precondition failed"):
            compile_and_run_js(source)

    def test_ensures_passing(self):
        source = """
        func double(x: Int) -> Int
            ensures result >= x
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            return double(5)
        end func
        """
        assert compile_and_run_js(source) == "10"

    def test_ensures_failing(self):
        source = """
        func half(x: Int) -> Int
            ensures result > x
            example 2 -> 1
            return x / 2
        end func

        func main() -> Int
            return half(10)
        end func
        """
        with pytest.raises(RuntimeError, match="Postcondition failed"):
            compile_and_run_js(source)

    def test_ensures_rejects_result_parameter_collision_before_compile(self):
        source = """
        func f(result: Int) -> Int
            ensures result == 2
            example 1 -> 2
            return result + 1
        end func
        """
        with pytest.raises(GenoTypeError, match="`result` is reserved"):
            compile_to_js(source)

    def test_async_ensures_awaits_body_helper(self):
        source = """
        async func value() -> Int
            return 1
        end func

        async func main() -> Int
            ensures result == 1
            return await value()
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        helper_match = re.search(r"async function (_temp_\d+)\(\)", js_code)
        assert helper_match is not None
        assert f"const result = await {helper_match.group(1)}();" in js_code
        assert compile_and_run_js(source) == "1"


# =============================================================================
# Contract constant folding
# =============================================================================


class TestJSContractConstantFolding:
    def test_requires_true_omitted(self):
        source = """
        func f(x: Int) -> Int
            requires true
            example 1 -> 1
            return x
        end func

        func main() -> Int
            return f(1)
        end func
        """
        js_code = compile_to_js(source)
        assert "Precondition failed" not in js_code
        assert compile_and_run_js(source) == "1"

    def test_ensures_true_omitted(self):
        source = """
        func f(x: Int) -> Int
            ensures true
            example 1 -> 1
            return x
        end func

        func main() -> Int
            return f(1)
        end func
        """
        js_code = compile_to_js(source)
        assert "Postcondition failed" not in js_code
        assert "_body_f" not in js_code
        assert compile_and_run_js(source) == "1"

    def test_requires_false_compile_error(self):
        source = """
        func f(x: Int) -> Int
            requires false
            example 1 -> 1
            return x
        end func

        func main() -> Int
            return f(1)
        end func
        """
        with pytest.raises(JSCompileError, match="requires false"):
            compile_to_js(source)

    def test_ensures_false_compile_error(self):
        source = """
        func f(x: Int) -> Int
            ensures false
            example 1 -> 1
            return x
        end func

        func main() -> Int
            return f(1)
        end func
        """
        with pytest.raises(JSCompileError, match="ensures false"):
            compile_to_js(source)

    def test_nontrivial_requires_still_emitted(self):
        source = """
        func f(x: Int) -> Int
            requires x > 0
            example 5 -> 5
            return x
        end func

        func main() -> Int
            return f(5)
        end func
        """
        js_code = compile_to_js(source)
        assert "Precondition failed" in js_code


# =============================================================================
# Reserved name protection
# =============================================================================


class TestJSCompilerReservedNames:
    def test_js_keyword_mangling(self):
        source = """
        func main() -> Int
            let class: Int = 42
            return class
        end func
        """
        js_code = compile_to_js(source)
        assert "class_kw" in js_code
        assert compile_and_run_js(source) == "42"

    @pytest.mark.parametrize(
        "name", ["_requireCap", "_validateRegexPattern", "_GENO_CREATE_REQUIRE"]
    )
    def test_all_runtime_prelude_functions_are_reserved(self, name):
        source = f"""
        func {name}(value: String, context: String) -> Unit
            example "x", "y" -> ()
            return ()
        end func
        """
        with pytest.raises(JSCompileError, match="reserved runtime name"):
            compile_to_js(source)

    def test_runtime_name_rejected_as_trait_dispatcher(self):
        source = """
        trait Unsafe
            func _requireCap(self: Self) -> Unit
        end trait
        """
        with pytest.raises(JSCompileError, match="reserved runtime name"):
            compile_to_js(source)

    def test_prelude_name_rejected(self):
        source = """
        func _safe_div(a: Int, b: Int) -> Int
            example 10, 2 -> 0
            return 0
        end func
        """
        with pytest.raises(JSCompileError, match="reserved runtime name"):
            compile_to_js(source)

    def test_float_power_name_rejected(self):
        source = """
        func _float_power(a: Float, b: Float) -> Float
            example 2.0, 3.0 -> 42.0
            return 42.0
        end func
        """
        with pytest.raises(JSCompileError, match="reserved runtime name"):
            compile_to_js(source)

    def test_round_nearest_name_rejected(self):
        source = """
        func _roundNearest(value: Float, context: String) -> Int
            example 1.0, "test" -> 1
            return 1
        end func
        """
        with pytest.raises(JSCompileError, match="reserved runtime name"):
            compile_to_js(source)

    def test_safe_index_set_name_rejected(self):
        source = """
        func _safe_index_set(xs: Array[Int], index: Int, value: Int) -> Unit
            example array_new(1, 0), 0, 7 -> ()
            return ()
        end func
        """
        with pytest.raises(JSCompileError, match="reserved runtime name"):
            compile_to_js(source)

    def test_safe_bitor_name_rejected(self):
        source = """
        func _safe_bitor(a: Int, b: Int) -> Int
            example 1, 2 -> 3
            return bit_or(a, b)
        end func
        """
        with pytest.raises(JSCompileError, match="reserved runtime name"):
            compile_to_js(source)

    @pytest.mark.parametrize("field_name", ["__proto__", "constructor", "prototype"])
    def test_prototype_sensitive_variant_fields_rejected(self, field_name):
        source = f"""
        type Evil = Evil({field_name}: String)
        """
        with pytest.raises(JSCompileError, match=field_name):
            compile_to_js(source)

    def test_tag_variant_field_rejected(self):
        source = """
        type Evil = Evil(_tag: String)
        """
        with pytest.raises(JSCompileError, match="runtime discriminator"):
            compile_to_js(source)

    @pytest.mark.parametrize(
        "field_name",
        ["__proto__", "constructor", "prototype", "_tag"],
    )
    def test_with_expr_emit_rejects_non_inert_record_fields(self, field_name):
        with pytest.raises(JSCompileError, match=field_name):
            JSCompiler()._with_expr_emit("target", [(field_name, "value")])

    @pytest.mark.parametrize(
        "source",
        [
            """
            func echo(_safe_add: Int) -> Int
                example 1 -> 1
                return _safe_add
            end func
            """,
            """
            func main() -> Int
                let _checkCollectionSize: Int = 1
                return _checkCollectionSize
            end func
            """,
            """
            func main() -> Int
                let f: (Int) -> Int = fn(_safe_add: Int) -> _safe_add
                return f(1)
            end func
            """,
            """
            func main() -> Int
                let opt: Option[Int] = Some(1)
                match opt with
                    | Some(_safe_add) -> return _safe_add
                    | None -> return 0
                end match
            end func
            """,
            """
            func main() -> Int
                for _safe_add: Int in [1, 2] do
                    return _safe_add
                end for
                return 0
            end func
            """,
            """
            func main() -> List[Int]
                return [_checkCollectionSize for _checkCollectionSize: Int in [1, 2]]
            end func
            """,
        ],
    )
    def test_reserved_names_rejected_in_local_scopes(self, source):
        with pytest.raises(JSCompileError, match="reserved runtime name"):
            compile_to_js(source)

    @pytest.mark.parametrize(
        "name",
        sorted(js_compiler._JS_EMITTED_LOCAL_HELPER_NAMES),
    )
    def test_all_emitted_local_helpers_are_reserved(self, name: str):
        source = f"""
        func main() -> Int
            let {name}: Int = 1
            return {name}
        end func
        """

        with pytest.raises(JSCompileError, match="reserved runtime name"):
            compile_to_js(source)

    @pytest.mark.parametrize("name", sorted(js_compiler._JS_FIXED_GLOBAL_NAMES))
    def test_fixed_generated_globals_are_reserved(self, name: str):
        source = f"""
        func {name}() -> Int
            example () -> 1
            return 1
        end func

        func main() -> Int
            return {name}()
        end func
        """

        with pytest.raises(JSCompileError, match="reserved runtime name"):
            compile_to_js(source)

    def test_ensures_body_helper_is_hygienic(self):
        source = """
        func f(_body_f: Int) -> Int
            ensures result == _body_f
            example 2 -> 2
            return _body_f
        end func

        func main() -> Int
            return f(2)
        end func
        """

        assert compile_and_run_js(source) == "2"


# =============================================================================
# Structural equality
# =============================================================================


class TestJSCompilerEquality:
    def test_list_equality(self):
        source = """
        func main() -> Bool
            return [1, 2, 3] == [1, 2, 3]
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_list_inequality(self):
        source = """
        func main() -> Bool
            return [1, 2] != [1, 2, 3]
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_constructor_equality(self):
        source = """
        func main() -> Bool
            return Some(5) == Some(5)
        end func
        """
        assert compile_and_run_js(source) == "true"

    def test_constructor_inequality(self):
        source = """
        func main() -> Bool
            return Some(5) != Some(6)
        end func
        """
        assert compile_and_run_js(source) == "true"


# =============================================================================
# Index and field access
# =============================================================================


class TestJSCompilerAccess:
    def test_index_access(self):
        source = """
        func main() -> Int
            let arr: List[Int] = [10, 20, 30]
            return arr[1]
        end func
        """
        assert compile_and_run_js(source) == "20"

    def test_field_access(self):
        source = """
        type Point = MkPoint(x: Int, y: Int)

        func main() -> Int
            let p: Point = MkPoint(3, 4)
            return p.x
        end func
        """
        assert compile_and_run_js(source) == "3"

    def test_index_out_of_bounds(self):
        source = """
        func main() -> Int
            example () -> 0
            let arr: List[Int] = [10, 20, 30]
            return arr[5]
        end func
        """
        with pytest.raises(RuntimeError, match="out of bounds"):
            compile_and_run_js(source)

    def test_index_assign_out_of_bounds(self):
        source = """
        func main() -> Int
            var xs: Array[Int] = array_new(3, 0)
            xs[10] = 99
            return 0
        end func
        """
        with pytest.raises(RuntimeError, match="out of bounds"):
            compile_and_run_js(source)

    def test_index_assign_in_bounds(self):
        source = """
        func main() -> Int
            var xs: Array[Int] = array_new(3, 0)
            xs[1] = 99
            return array_get(xs, 1)
        end func
        """
        assert compile_and_run_js(source) == "99"


# =============================================================================
# Map operations
# =============================================================================


class TestJSCompilerMapOperations:
    """Map operations are tested at the JS runtime level.

    Geno has no map-literal syntax, so maps cannot be created in pure Geno
    source code without a builtin that constructs an empty map.  The
    map_insert / map_get JS runtime functions are exercised indirectly via
    the runtime unit (Node.js inline script) rather than full compilation.
    """

    def test_map_runtime_round_trip(self):
        """Verify map_insert and map_get work in the JS runtime."""
        js_code = compile_to_js(
            """
        func main() -> Int
            return 0
        end func
        """
        )
        assert isinstance(js_code, str)
        # Append a direct runtime exercise after the compiled program
        js_code += """
const m1 = new Map();
const m2 = map_insert(m1, "a", 42);
const m3 = map_insert(m2, "b", 99);
const r1 = map_get(m3, "a");
const r2 = map_get(m3, "missing");
console.log(is_some(r1));
console.log(unwrap(r1));
console.log(is_none(r2));
"""

        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.strip().split("\n")
        # Skip the "0" from main()
        assert lines[-3] == "true"
        assert lines[-2] == "42"
        assert lines[-1] == "true"

    def test_tuple_map_keys_use_structural_equality(self):
        source = """
        func main() -> Int
            let m0: Map[(Int, Int), Int] = map_from_entries([])
            let m1: Map[(Int, Int), Int] = map_insert(map: m0, key: (1, 2), value: 7)
            match map_get(m1, (1, 2)) with
                | Some(v) -> return v
                | None -> return 0
            end match
        end func
        """
        assert compile_and_run_js(source) == "7"

    def test_tuple_map_insert_overwrites_structural_key(self):
        source = """
        func main() -> Int
            let m0: Map[(Int, Int), Int] = map_from_entries([])
            let m1: Map[(Int, Int), Int] = map_insert(map: m0, key: (1, 2), value: 7)
            let m2: Map[(Int, Int), Int] = map_insert(map: m1, key: (1, 2), value: 9)
            match map_get(m2, (1, 2)) with
                | Some(v) -> return (length(map_entries(m2)) * 10) + v
                | None -> return 0
            end match
        end func
        """
        assert compile_and_run_js(source) == "19"

    def test_tuple_mutable_map_keys_use_structural_equality(self):
        source = """
        func main() -> Int
            let m: MutableMap[(Int, Int), Int] = mutable_map_new()
            mutable_map_set(map: m, key: (1, 2), value: 7)
            mutable_map_set(map: m, key: (1, 2), value: 9)
            match mutable_map_get(m, (1, 2)) with
                | Some(v) -> return (mutable_map_size(m) * 10) + v
                | None -> return 0
            end match
        end func
        """
        assert compile_and_run_js(source) == "19"


# =============================================================================
# Error cases
# =============================================================================


class TestJSCompilerErrors:
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

    def test_constructed_match_expr_rejects_empty_arm_body(self):
        program, match_expr = self._program_with_match_expr()
        match_expr.arms[0].body = []

        with pytest.raises(JSCompileError, match="exactly one return statement"):
            JSCompiler().compile(program)

    def test_constructed_match_expr_rejects_non_return_arm_body(self):
        program, match_expr = self._program_with_match_expr()
        loc = match_expr.arms[0].location
        match_expr.arms[0].body = [
            ExpressionStatement(location=loc, expression=IntegerLiteral(loc, 99))
        ]

        with pytest.raises(JSCompileError, match="exactly one return statement"):
            JSCompiler().compile(program)

    def test_constructed_match_expr_rejects_multiple_statement_arm_body(self):
        program, match_expr = self._program_with_match_expr()
        loc = match_expr.arms[0].location
        match_expr.arms[0].body = [
            ExpressionStatement(location=loc, expression=IntegerLiteral(loc, 99)),
            ReturnStatement(location=loc, value=IntegerLiteral(loc, 1)),
        ]

        with pytest.raises(JSCompileError, match="exactly one return statement"):
            JSCompiler().compile(program)

    def test_division_by_zero(self):
        source = """
        func main() -> Int
            example () -> 0
            return 10 / 0
        end func
        """
        with pytest.raises(RuntimeError, match="Division by zero"):
            compile_and_run_js(source)

    def test_modulo_by_zero(self):
        source = """
        func main() -> Int
            example () -> 0
            return 10 % 0
        end func
        """
        with pytest.raises(RuntimeError, match="Division by zero"):
            compile_and_run_js(source)

    def test_modulo_by_zero_caught_by_try_catch(self):
        source = """
        func main() -> String
            try
                let x: Int = 10 % 0
                return "no error"
            catch e: String
                return "caught"
            end try
        end func
        """
        assert compile_and_run_js(source) == "caught"

    def test_head_empty_list(self):
        source = """
        func main() -> Int
            example () -> 0
            return head([])
        end func
        """
        with pytest.raises(RuntimeError, match="head of empty list"):
            compile_and_run_js(source)

    def test_tail_empty_list(self):
        source = """
        func main() -> List[Int]
            example () -> []
            return tail([])
        end func
        """
        with pytest.raises(RuntimeError, match="tail of empty list"):
            compile_and_run_js(source)

    def test_unwrap_none(self):
        source = """
        func main() -> Int
            example () -> 0
            let x: Option[Int] = None
            return unwrap(x)
        end func
        """
        with pytest.raises(RuntimeError, match="unwrap called on None"):
            compile_and_run_js(source)

    def test_requires_failing(self):
        source = """
        func positive(x: Int) -> Int
            requires x > 0
            example 1 -> 1
            return x
        end func

        func main() -> Int
            return positive(-5)
        end func
        """
        with pytest.raises(RuntimeError, match="Precondition failed"):
            compile_and_run_js(source)

    def test_nested_match_expression(self):
        source = """
        type Outer = Deep(x: Int) | Shallow | Empty

        func classify(o: Outer) -> String
            example Deep(1) -> "deep"
            return match o with
                | Deep(x) -> match x with
                    | 0 -> "zero"
                    | _ -> "deep"
                end match
                | Shallow -> "shallow"
                | Empty -> "empty"
            end match
        end func

        func main() -> String
            return classify(Deep(42))
        end func
        """
        assert compile_and_run_js(source) == "deep"

    def test_parse_int_rejects_trailing_garbage(self):
        source = """
        func main() -> Bool
            return is_none(parse_int("42abc"))
        end func
        """
        assert compile_and_run_js(source) == "true"


# =============================================================================
# Parity with interpreter (example programs)
# =============================================================================


class TestJSInterpreterParity:
    """Run each example program through both interpreter and JS compiler."""

    @pytest.fixture(
        params=[
            p
            for p in sorted(EXAMPLES_DIR.glob("*.geno"))
            # Examples using print() have different formatting between interpreter
            # (quotes strings) and JS (raw strings); covered by test_backend_parity.py
            if p.stem not in {"calculator", "csv_processor", "todo_app"}
        ],
        ids=lambda p: p.stem,
    )
    def gen_file(self, request):
        return request.param

    def test_parity(self, gen_file):
        source = gen_file.read_text()

        # Run through interpreter
        from geno.api import RunConfig
        from geno.api import run as api_run

        interp_result = api_run(
            source, filename=str(gen_file), config=RunConfig(check_examples=False)
        )
        assert interp_result.ok, f"Interpreter failed: {interp_result.diagnostics}"

        # Run through JS compiler
        js_output = compile_and_run_js(source)

        # Format interpreter result for comparison
        interp_formatted = _format_geno_value(interp_result.value)
        assert js_output == interp_formatted, (
            f"Backend mismatch for {gen_file.name}:\n"
            f"  interpreter: {interp_formatted!r}\n"
            f"  js compiler: {js_output!r}"
        )


def _format_geno_value(value) -> str:
    """Format a Python Geno value the same way as JS _formatValue."""
    if value is None:
        return "()"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, list):
        return f"[{', '.join(_format_geno_value(x) for x in value)}]"
    if isinstance(value, dict):
        entries = ", ".join(
            f"{_format_geno_value(k)}: {_format_geno_value(v)}"
            for k, v in value.items()
        )
        return f"{{{entries}}}"
    # Constructor values
    from geno._runtime_support import Constructor, _None

    if isinstance(value, _None):
        return "None"
    if isinstance(value, Constructor):
        name = type(value).__name__
        fields = [f for f in vars(value) if not f.startswith("_")]
        if not fields:
            return name
        field_vals = [_format_geno_value(getattr(value, f)) for f in fields]
        return f"{name}({', '.join(field_vals)})"
    return str(value)


# =============================================================================
# Compilation output checks
# =============================================================================


class TestJSCompilerOutput:
    def test_uses_valuesEqual_for_eq(self):
        source = """
        func main() -> Bool
            return 5 == 5
        end func
        """
        js_code = compile_to_js(source)
        assert "_valuesEqual(" in js_code

    def test_uses_safe_div(self):
        source = """
        func main() -> Int
            return 10 / 3
        end func
        """
        js_code = compile_to_js(source)
        assert "_safe_div(" in js_code

    def test_uses_safe_add(self):
        source = """
        func main() -> Int
            return 1 + 2
        end func
        """
        js_code = compile_to_js(source)
        assert "_safe_add(" in js_code

    def test_typed_nonzero_literal_modulo_uses_safe_mod(self):
        source = """
        func main() -> Int
            return 10 % 3
        end func
        """
        js_code = compile_to_js(source)
        assert (
            "return _safe_mod(_checkCollectionSize(10), _checkCollectionSize(3));"
            in js_code
        )

    def test_constructor_not_frozen(self):
        """Constructors must not use Object.freeze so field assignment works."""
        source = """
        type Point = Point(x: Int, y: Int)
        func main() -> Int
            return 0
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        assert "function Point(x, y)" in js_code
        assert "Object.freeze" not in js_code.split("function Point")[1].split("}")[0]

    def test_with_expr_emits_mutable_copy(self):
        """`with` on user-defined types freezes the updated copy."""
        source = """
        type Point = Point(x: Int, y: Int)

        func move(p: Point) -> Point
            example Point(0, 0) -> Point(1, 0)
            return p with (x: p.x + 1)
        end func

        func main() -> Point
            return move(Point(0, 0))
        end func
        """
        js_code = compile_to_js(source)
        assert "Object.freeze({...p, x:" not in js_code
        assert "({...p, x:" in js_code

    def test_typed_modulo_simple_operands_use_safe_mod(self):
        source = """
        func f(a: Int, b: Int) -> Int
            example (10, 3) -> 1
            return a % b
        end func
        """
        js_code = compile_to_js(source)
        assert "return _safe_mod(a, b);" in js_code

    def test_typed_modulo_preserves_left_to_right_evaluation(self):
        source = """
        func left(flag: Bool) -> Int
            example (false) -> 1
            if flag then
                throw "left"
            end if
            return 1
        end func

        func right(flag: Bool) -> Int
            example (false) -> 1
            if flag then
                throw "right"
            end if
            return 1
        end func

        func main() -> String
            try
                let x: Int = left(true) % right(true)
                return "none"
            catch e: String
                return e
            end try
        end func
        """
        assert compile_and_run_js(source) == "left"

    def test_typed_modulo_supports_async_operands(self):
        source = """
        async func value() -> Int
            return 5
        end func

        async func main() -> Int
            return (await value()) % (1 + 1)
        end func
        """
        assert compile_and_run_js(source) == "1"

    def test_untyped_modulo_uses_safe_mod(self):
        source = """
        func main() -> Int
            return 10 % 3
        end func
        """
        js_code = compile_to_js(source, typecheck=False)
        assert (
            "return _safe_mod(_checkCollectionSize(10), _checkCollectionSize(3));"
            in js_code
        )

    def test_typed_string_char_at_emits_unicode_helper(self):
        source = """
        func main() -> String
            return string_char_at("hello", 1)
        end func
        """
        js_code = compile_to_js(source)
        assert (
            'return _stringCharAt(_checkCollectionSize("hello"), '
            "_checkCollectionSize(1));" in js_code
        )

    def test_typed_string_char_at_supports_async_operands(self):
        source = """
        async func text() -> String
            return "hello"
        end func

        async func main() -> String
            return string_char_at(await text(), 1 + 1)
        end func
        """
        assert compile_and_run_js(source) == "l"

    def test_typed_substring_emits_unicode_helper(self):
        source = """
        func main() -> String
            return substring(text: "hello", start: 1, stop: 4)
        end func
        """
        js_code = compile_to_js(source)
        assert (
            'return _stringSubstring(_checkCollectionSize("hello"), '
            "_checkCollectionSize(1), _checkCollectionSize(4));" in js_code
        )

    def test_typed_substring_supports_async_operands(self):
        source = """
        async func text() -> String
            return "hello"
        end func

        async func main() -> String
            return substring(text: await text(), start: 1, stop: 4)
        end func
        """
        assert compile_and_run_js(source) == "ell"

    def test_typed_starts_with_emits_method_fast_path(self):
        source = """
        func main() -> Bool
            return starts_with("hello", "he")
        end func
        """
        js_code = compile_to_js(source)
        assert (
            'return _checkCollectionSize("hello").startsWith(_checkCollectionSize("he"));'
            in js_code
        )

    def test_typed_ends_with_emits_method_fast_path(self):
        source = """
        func main() -> Bool
            return ends_with("hello", "lo")
        end func
        """
        js_code = compile_to_js(source)
        assert (
            'return _checkCollectionSize("hello").endsWith(_checkCollectionSize("lo"));'
            in js_code
        )

    def test_typed_append_emits_inline_fast_path(self):
        source = """
        func main() -> List[Int]
            return append([1, 2], 3)
        end func
        """
        js_code = compile_to_js(source)
        assert "_MAX_COLLECTION_SIZE" in js_code
        assert "[..._temp_" in js_code
        assert 'throw new Error("List size exceeds limit (" +' in js_code

    def test_typed_append_supports_async_operands(self):
        source = """
        async func items() -> List[Int]
            return [1, 2]
        end func

        async func main() -> List[Int]
            return append(await items(), 3)
        end func
        """
        assert compile_and_run_js(source) == "[1, 2, 3]"

    def test_compiled_literal_results_honor_configured_limit(self):
        cases = [
            (
                """
                func main() -> String
                    return "abcd"
                end func
                """,
                "String size exceeds limit",
            ),
            (
                """
                func main() -> List[Int]
                    return [1, 2, 3]
                end func
                """,
                "List size exceeds limit",
            ),
            (
                """
                func main() -> (Int, Int, Int)
                    return (1, 2, 3)
                end func
                """,
                "Tuple size exceeds limit",
            ),
            (
                """
                func main() -> List[List[Int]]
                    return [[1, 2, 3]]
                end func
                """,
                "List size exceeds limit",
            ),
        ]

        for source, message in cases:
            result = run_compiled_js_with_limit(source, max_collection_size=2)
            assert result.returncode != 0
            assert message in result.stderr

    def test_runtime_collection_helpers_honor_configured_limit(self):
        script = """
function expectThrows(label, fn, fragment) {
    try {
        fn();
    } catch (error) {
        if (String(error.message).includes(fragment)) return;
        throw new Error(label + " wrong error: " + error.message);
    }
    throw new Error(label + " did not throw");
}

expectThrows("array_new", () => array_new(6, 0), "Array size exceeds limit");
expectThrows("split", () => split("a,a,a,a,a,a", ","), "List size exceeds limit");
expectThrows("string_split", () => string_split("a,a,a,a,a,a", ","), "List size exceeds limit");
expectThrows("join", () => join(["abc", "def"], ""), "String size exceeds limit");
expectThrows("string_join", () => string_join(["abc", "def"], ""), "String size exceeds limit");
expectThrows("replace", () => replace("aaaa", "a", "bb"), "String size exceeds limit");
expectThrows("string_replace", () => string_replace("aaaa", "a", "bb"), "String size exceeds limit");
_GENO_CAPS.add("regex");
expectThrows("regex_find_all", () => regex_find_all("a", "aaaaaa"), "List size exceeds limit");
expectThrows("regex_replace", () => regex_replace("a", "bb", "aaaa"), "String size exceeds limit");
expectThrows("format", () => format("{}{}", ["abc", "def"]), "String size exceeds limit");
expectThrows("string_pad_left", () => string_pad_left("x", 6, "0"), "String size exceeds limit");
expectThrows("json_parse_array", () => json_parse("[1,2,3,4,5,6]"), "List size exceeds limit");
expectThrows("json_parse_object", () => json_parse('{"a":1,"b":2,"c":3,"d":4,"e":5,"f":6}'), "Map size exceeds limit");
expectThrows("json_parse_error", () => json_parse("x"), "String size exceeds limit");
expectThrows("json_stringify", () => json_stringify({_tag: "JsonArray", items: [{_tag: "JsonString", value: "abc"}]}), "String size exceeds limit");
expectThrows("json_stringify_pretty", () => json_stringify_pretty({_tag: "JsonArray", items: [{_tag: "JsonString", value: "abc"}]}, 2), "String size exceeds limit");
expectThrows("json_to_string", () => json_to_string(["abc"]), "String size exceeds limit");
expectThrows("csv_parse", () => csv_parse("a\\nb\\nc\\nd\\ne\\nf"), "List size exceeds limit");
expectThrows("toml_parse_error", () => toml_parse("["), "String size exceeds limit");
expectThrows("flat_map", () => flat_map([1, 2], x => [x, x, x]), "List size exceeds limit");
expectThrows("list_flatten", () => list_flatten([[1, 2, 3], [4, 5, 6]]), "List size exceeds limit");
expectThrows("list_intersperse", () => list_intersperse([1, 2, 3, 4], 0), "List size exceeds limit");
console.log("ok");
"""
        assert run_js_runtime_script(script) == "ok"

    def test_node_http_bridge_uses_stdin_without_process_cap(self):
        script = """
const cp = require("child_process");
const originalExecFileSync = cp.execFileSync;
let observedArgs = null;
let observedInput = null;
cp.execFileSync = function(_program, args, options) {
    observedArgs = args;
    observedInput = options.input;
    return JSON.stringify({ok: true, status: 200, body: "ok", headers: []});
};
_GENO_CAPS.add("http");
const secret = "request-body-secret";
const result = http_post("https://example.test/path", secret);
cp.execFileSync = originalExecFileSync;
if (result !== "ok") {
    throw new Error("wrong result: " + result);
}
if (observedArgs.join(" ").includes(secret)) {
    throw new Error("request body leaked into argv");
}
const envelope = JSON.parse(observedInput);
if (envelope.body !== secret || envelope.url !== "https://example.test/path") {
    throw new Error("request envelope was not sent through stdin");
}
console.log("ok");
"""
        assert run_js_runtime_script(script) == "ok"

    def test_runtime_csv_parse_counts_astral_fields_by_code_point(self):
        script = """
const smile = String.fromCodePoint(128512);
const rows = csv_parse(smile);
const rowsWithHeaders = csv_parse_with_headers("h\\n" + smile);
const headerValue = map_get(rowsWithHeaders[0], "h").value;

console.log(rows.length);
console.log(Array.from(rows[0][0]).length);
console.log(rows[0][0].codePointAt(0));
console.log(rowsWithHeaders.length);
console.log(map_entries(rowsWithHeaders[0]).length);
console.log(Array.from(headerValue).length);
console.log(headerValue.codePointAt(0));
"""
        assert run_js_runtime_script(script, max_collection_size=1) == (
            "1\n1\n128512\n1\n1\n1\n128512"
        )

    def test_runtime_csv_parse_distinguishes_empty_rows_and_fields(self):
        script = """
const blank = csv_parse("\\n");
const mixed = csv_parse("a\\n\\n");
const quotedEmpty = csv_parse("\\"\\"");
const trailingEmpty = csv_parse("a,");

console.log(blank.length);
console.log(blank[0].length);
console.log(mixed.length);
console.log(mixed[1].length);
console.log(quotedEmpty.length);
console.log(quotedEmpty[0].length);
console.log(quotedEmpty[0][0].length);
console.log(trailingEmpty[0].length);
console.log(trailingEmpty[0][1].length);
"""
        assert run_js_runtime_script(script) == "1\n0\n2\n0\n1\n1\n0\n2\n0"

    def test_runtime_csv_parse_treats_mid_field_quotes_as_literals(self):
        script = """
const rows = csv_parse("a\\\",b\\na\\\"\\\"");

console.log(rows.length);
console.log(rows[0].length);
console.log(rows[0][0].length);
console.log(rows[0][0].codePointAt(1));
console.log(rows[0][1]);
console.log(rows[1].length);
console.log(rows[1][0].length);
console.log(rows[1][0].codePointAt(1));
console.log(rows[1][0].codePointAt(2));
"""
        assert run_js_runtime_script(script) == "2\n2\n2\n34\nb\n1\n3\n34\n34"

    def test_runtime_capability_helpers_honor_configured_limit(self):
        script = """
function expectThrows(label, fn, fragment) {
    try {
        fn();
    } catch (error) {
        if (String(error.message).includes(fragment)) return;
        throw new Error(label + " wrong error: " + error.message);
    }
    throw new Error(label + " did not throw");
}

const fs = require("fs");
const os = require("os");
const path = require("path");

_GENO_CAPS.add("fs");
const bigFile = path.join(os.tmpdir(), "geno-big-" + Date.now() + ".txt");
fs.writeFileSync(bigFile, "abcd", "utf-8");
expectThrows("fs_read_text", () => fs_read_text(bigFile), "String size exceeds limit");
fs.unlinkSync(bigFile);

const bigDir = fs.mkdtempSync(path.join(os.tmpdir(), "geno-dir-"));
for (const name of ["a.txt", "b.txt", "c.txt"]) fs.writeFileSync(path.join(bigDir, name), "x", "utf-8");
expectThrows("fs_list_dir", () => fs_list_dir(bigDir), "List size exceeds limit");

_GENO_CAPS.add("http");
_GENO_CAPS.add("process");
_syncFetch = () => ({ok: true, status: 200, body: "abcd", headers: []});
expectThrows("http_fetch", () => http_fetch("http://example.test"), "String size exceeds limit");
expectThrows("http_post", () => http_post("http://example.test", ""), "String size exceeds limit");
_syncFetch = () => ({ok: true, status: 200, body: "ok", headers: [["a", "1"], ["b", "2"], ["c", "3"]]});
expectThrows("http_request", () => http_request("GET", "http://example.test", [], ""), "List size exceeds limit");

const cp = require("child_process");
const originalSpawnSync = cp.spawnSync;
cp.spawnSync = () => ({status: 0, stdout: "abcd", stderr: ""});
expectThrows("exec", () => exec("ignored"), "String size exceeds limit");
expectThrows("exec_with_input", () => exec_with_input("ignored", ""), "String size exceeds limit");
cp.spawnSync = originalSpawnSync;

_GENO_CAPS.add("env");
process.env.GENO_BIG_ENV = "abcd";
expectThrows("env_get", () => env_get("GENO_BIG_ENV"), "String size exceeds limit");
expectThrows("env_get_or_existing", () => env_get_or("GENO_BIG_ENV", ""), "String size exceeds limit");
expectThrows("env_get_or_default", () => env_get_or("GENO_MISSING_ENV", "abcd"), "String size exceeds limit");

const oldArgvLength = process.argv.length;
process.argv.push("--", "a", "b", "c");
expectThrows("cli_args", () => cli_args(), "List size exceeds limit");
process.argv.length = oldArgvLength;

console.log("ok");
"""
        assert run_js_runtime_script(script, max_collection_size=2) == "ok"

    def test_runtime_map_vec_set_helpers_honor_configured_limit(self):
        script = """
function expectThrows(label, fn, fragment) {
    try {
        fn();
    } catch (error) {
        if (String(error.message).includes(fragment)) return;
        throw new Error(label + " wrong error: " + error.message);
    }
    throw new Error(label + " did not throw");
}

expectThrows("map_insert", () => map_insert(map_from_entries([[0, 0], [1, 1], [2, 2], [3, 3], [4, 4]]), 5, 5), "Map size exceeds limit");
expectThrows("map_merge", () => map_merge(map_from_entries([[0, 0], [1, 1], [2, 2], [3, 3], [4, 4]]), map_from_entries([[5, 5]])), "Map size exceeds limit");
const oversizedMap = new _GENO_MAP([[0, 0], [1, 1], [2, 2], [3, 3], [4, 4], [5, 5]]);
expectThrows("map_entries", () => map_entries(oversizedMap), "List size exceeds limit");
expectThrows("map_from_entries", () => map_from_entries([[0, 0], [1, 1], [2, 2], [3, 3], [4, 4], [5, 5]]), "Map size exceeds limit");

const mutable = mutable_map_new();
for (let i = 0; i < 5; i++) mutable_map_set(mutable, i, i);
expectThrows("mutable_map_set", () => mutable_map_set(mutable, 5, 5), "MutableMap size exceeds limit");

const vec = vec_new();
for (let i = 0; i < 5; i++) vec_push(vec, i);
expectThrows("vec_push", () => vec_push(vec, 5), "Vec size exceeds limit");
expectThrows("vec_from_list", () => vec_from_list([0, 1, 2, 3, 4, 5]), "Vec size exceeds limit");

expectThrows("set_from_list", () => set_from_list([0, 1, 2, 3, 4, 5]), "Set size exceeds limit");
const s = set_from_list([0, 1, 2, 3, 4]);
expectThrows("set_add", () => set_add(s, 5), "Set size exceeds limit");
console.log("ok");
        """
        assert run_js_runtime_script(script) == "ok"

    def test_safe_index_set_supports_vec_and_mutable_map(self):
        script = """
const vec = vec_from_list([1, 2, 3]);
_safe_index_set(vec, 1, 42);
if (vec_get(vec, 1) !== 42) throw new Error("vec index assignment failed");

const mutable = mutable_map_new();
_safe_index_set(mutable, "a", 42);
const found = mutable_map_get(mutable, "a");
if (found._tag !== "Some" || found.value !== 42) {
    throw new Error("mutable map index assignment failed");
}
console.log("ok");
"""
        assert run_js_runtime_script(script) == "ok"

    def test_safe_index_set_prechecks_mutable_map_growth(self):
        script = """
const mutable = mutable_map_new();
for (let i = 0; i < 5; i++) mutable_map_set(mutable, i, i);
try {
    _safe_index_set(mutable, 5, 5);
    throw new Error("expected limit error");
} catch (error) {
    if (!String(error.message).includes("MutableMap size exceeds limit")) {
        throw error;
    }
}
if (mutable_map_size(mutable) !== 5) {
    throw new Error("mutable map grew after limit error");
}
console.log("ok");
"""
        assert run_js_runtime_script(script) == "ok"

    def test_vec_index_assign_compiled(self):
        source = """
        func main() -> Int
            var v: Vec[Int] = vec_from_list([1, 2, 3])
            v[1] = 42
            return vec_get(v, 1)
        end func
        """
        assert compile_and_run_js(source) == "42"

    def test_mutable_map_index_assign_compiled(self):
        source = """
        func main() -> Int
            var m: MutableMap[String, Int] = mutable_map_new()
            m["a"] = 42
            let found: Option[Int] = mutable_map_get(m, "a")
            return match found with
            | Some(value) -> value
            | None -> 0
            end match
        end func
        """
        assert compile_and_run_js(source) == "42"

    def test_uses_safe_index(self):
        source = """
        func main() -> Int
            example () -> 10
            let arr: List[Int] = [10, 20, 30]
            return arr[0]
        end func
        """
        js_code = compile_to_js(source)
        assert "_safe_index(" in js_code

    def test_uses_get_field(self):
        source = """
        type Wrapper = Wrap(value: Int)

        func unwrap_it(w: Wrapper) -> Int
            example Wrap(5) -> 5
            match w with
                | Wrap(x) -> return x
            end match
        end func

        func main() -> Int
            return unwrap_it(Wrap(42))
        end func
        """
        js_code = compile_to_js(source)
        assert "get_field(" in js_code

    def test_get_field_blocks_inherited_properties(self):
        source = """
        type Wrapper = Wrap(value: Int)

        func unwrap_it(w: Wrapper) -> Int
            example Wrap(5) -> 5
            match w with
                | Wrap(x) -> return x
            end match
        end func

        func main() -> Unit
            let value: Int = unwrap_it(Wrap(42))
            return ()
        end func
        """
        js_output = compile_to_js(source)
        assert isinstance(js_output, str)
        js_code = (
            js_output
            + """
const evil = Object.create({ secret: 42 });
try {
    get_field(evil, "secret");
    console.log("leaked");
} catch (error) {
    console.log("blocked");
}
"""
        )
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "blocked"

    def test_type_def_emits_factory(self):
        source = """
        type Color = Red | Green | Blue

        func main() -> Int
            return 42
        end func
        """
        js_code = compile_to_js(source)
        assert "function Red()" in js_code
        assert "function Green()" in js_code
        assert "function Blue()" in js_code
        assert "_tag" in js_code
        assert "Object.freeze" in js_code

    def test_for_loop_uses_const_of(self):
        source = """
        func main() -> Int
            var sum: Int = 0
            for x: Int in [1, 2, 3] do
                sum = sum + x
            end for
            return sum
        end func
        """
        js_code = compile_to_js(source)
        assert "for (const" in js_code
        assert " of " in js_code

    def test_let_immutable_skips_deep_copy(self):
        source = """
        func main() -> Int
            let x: Int = 5
            return x
        end func
        """
        js_code = compile_to_js(source)
        # Immutable types (Int, Float, Bool, String) skip _deepCopy
        assert "const x = _checkCollectionSize(5);" in js_code
        assert "_deepCopy(5)" not in js_code

    def test_let_list_literal_skips_deep_copy(self):
        source = """
        func main() -> List[Int]
            let xs: List[Int] = [1, 2, 3]
            return xs
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        assert "const xs = _checkCollectionSize([" in js_code
        assert "_deepCopy" not in js_code.split("function main")[1]

    def test_var_uses_let(self):
        source = """
        func main() -> Int
            var x: Int = 5
            return x
        end func
        """
        js_code = compile_to_js(source)
        assert "let x = " in js_code

    def test_var_list_literal_skips_deep_copy(self):
        source = """
        func main() -> List[Int]
            var xs: List[Int] = [1, 2, 3]
            return xs
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        assert "let xs = _checkCollectionSize([" in js_code
        assert "_deepCopy" not in js_code.split("function main")[1]

    def test_var_map_from_call_deep_copies(self):
        """Function call results are deep-copied for safety."""
        source = """
        func main() -> Map[String, Int]
            let pairs: List[Tuple[String, Int]] = [("a", 1)]
            var m: Map[String, Int] = map_from_list(pairs)
            return m
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        main_body = js_code.split("function main")[1]
        assert "let m = _deepCopy(" in main_body


class TestJSCompilerStateReset:
    """Regression tests for reused JS compiler instances."""

    def test_compile_clears_stale_trait_dispatch_state(self):
        trait_program = parse(
            """
            type Circle = Circle(radius: Float)

            trait Describable
                func describe(self: Self) -> String
            end trait

            impl Describable for Circle
                func describe(self: Circle) -> String
                    return "Circle"
                end func
            end impl
            """
        )
        simple_program = parse(
            """
            func main() -> Int
                return 1
            end func
            """
        )

        compiler = JSCompiler()
        first_output = compiler.compile(trait_program)
        assert "function describe(self_arg, ...args)" in first_output

        second_output = compiler.compile(simple_program)
        assert "function describe(self_arg, ...args)" not in second_output


class TestJSCompilerSourceMapTracking:
    """Regression tests for optional source map tracking."""

    def test_source_map_tracking_can_be_disabled_explicitly(self):
        source = """
        func main() -> Int
            return 42
        end func
        """
        program = parse(source, filename="tracking.geno")

        default_compiler = JSCompiler()
        default_js = default_compiler.compile(program)
        assert default_compiler._mappings

        sm = json.loads(
            default_compiler.generate_source_map(
                out_file="tracking.js",
                sources_content={"tracking.geno": source},
            )
        )
        assert sm["file"] == "tracking.js"
        assert sm["sources"] == ["tracking.geno"]
        assert sm["mappings"]

        no_map_compiler = JSCompiler(track_source_map=False)
        no_map_js = no_map_compiler.compile(program)
        assert no_map_js == default_js
        assert no_map_compiler._mappings == []
        assert no_map_compiler._source_files == []
        with pytest.raises(RuntimeError, match="track_source_map=True"):
            no_map_compiler.generate_source_map()


class TestJSCompilerPreludeTreeShaking:
    @pytest.mark.parametrize(
        "user_code",
        [
            "function main(){ return _safe_index(xs, 0); }",
            "function main(){ return _formatValue(_safe_add(1, 2)); }",
            "function main(){ return Some(1); }",
            "function main(){ const label = '_safe_mod'; return print_(label); }",
        ],
    )
    def test_tree_shaker_matches_previous_behavior(self, user_code):
        assert js_compiler._tree_shake_prelude(user_code) == _slow_tree_shake_prelude(
            user_code
        )

    @pytest.mark.parametrize(
        "source",
        [
            """
            func main() -> List[Int]
                return append([1, 2], 3)
            end func
            """,
            """
            async func text() -> String
                return "hello"
            end func

            async func main() -> String
                let prefix: String = string_char_at(await text(), 1)
                if starts_with(prefix, "e") and ends_with("hello", "lo") then
                    return substring(text: "hello", start: 1, stop: 4)
                end if
                return ""
            end func
            """,
        ],
    )
    def test_tree_shaker_matches_previous_behavior_on_compiled_programs(self, source):
        program = parse(source)
        TypeChecker().check_program(program)
        user_code = JSCompiler(track_source_map=False).compile(
            program, tree_shake=False
        )
        assert js_compiler._tree_shake_prelude(user_code) == _slow_tree_shake_prelude(
            user_code
        )


# =============================================================================
# _deepCopy alias analysis
# =============================================================================


class TestJSDeepCopyOptimization:
    """Verify _deepCopy is skipped only for alias-free freshly constructed values."""

    def test_list_literal_no_deep_copy(self):
        source = """
        func main() -> List[Int]
            let xs: List[Int] = [1, 2, 3]
            return xs
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        assert "_deepCopy" not in js_code.split("function main")[1]
        assert compile_and_run_js(source) == "[1, 2, 3]"

    def test_constructor_call_no_deep_copy(self):
        source = """
        type Box = MkBox(val: Int)

        func main() -> Int
            let b: Box = MkBox(42)
            return b.val
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        main_section = js_code.split("function main")[1]
        assert "_deepCopy" not in main_section
        assert compile_and_run_js(source) == "42"

    def test_constructor_call_with_mutable_arg_still_deep_copies(self):
        source = """
        type Point = Point(x: Int, y: Int)
        type Wrap = Wrap(p: Point)

        func main() -> Int
            var p: Point = Point(1, 2)
            var w: Wrap = Wrap(p)
            w.p.x = 10
            return p.x
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        main_section = js_code.split("function main")[1]
        assert "let w = _deepCopy(" in main_section
        assert compile_and_run_js(source) == "1"

    def test_function_call_still_deep_copies(self):
        """Function calls may return references to captured state."""
        source = """
        func make_list() -> List[Int]
            example () -> [1, 2]
            return [1, 2]
        end func

        func main() -> List[Int]
            let xs: List[Int] = make_list()
            return xs
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        main_section = js_code.split("function main")[1]
        assert "_deepCopy" in main_section

    def test_variable_alias_still_deep_copies(self):
        source = """
        func main() -> List[Int]
            let xs: List[Int] = [1, 2, 3]
            let ys: List[Int] = xs
            return ys
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        main_section = js_code.split("function main")[1]
        assert main_section.count("_deepCopy") >= 1
        assert compile_and_run_js(source) == "[1, 2, 3]"

    def test_list_literal_with_mutable_element_still_deep_copies(self):
        source = """
        type Point = Point(x: Int, y: Int)

        func main() -> Int
            var p: Point = Point(1, 2)
            var xs: List[Point] = [p]
            xs[0].x = 10
            return p.x
        end func
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        main_section = js_code.split("function main")[1]
        assert "let xs = _deepCopy(" in main_section
        assert compile_and_run_js(source) == "1"

    def test_value_semantics_preserved(self):
        """Ensure mutations don't leak through skipped copies."""
        source = """
        func main() -> List[Int]
            var xs: List[Int] = [1, 2, 3]
            var ys: List[Int] = xs
            ys = append(ys, 4)
            return xs
        end func
        """
        assert compile_and_run_js(source) == "[1, 2, 3]"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


@pytest.mark.parametrize(
    "expression",
    ["_GENO_CAPS", '_validateRegexPattern("a", "test")'],
)
def test_js_compiler_rejects_direct_runtime_internal_reference_without_typecheck(
    expression,
):
    source = f"func main() -> Any\n    return {expression}\nend func\n"

    with pytest.raises(JSCompileError, match="reserved runtime name"):
        compile_to_js(source, typecheck=False)


def test_js_compiler_allows_declared_builtin_without_typecheck():
    source = "func main() -> Int\n    return array_get(array_new(1, 7), 0)\nend func\n"

    assert "function main" in compile_to_js(source, typecheck=False)


@pytest.mark.parametrize("field_name", ["default", "_withGenoFormatter"])
def test_js_compiler_rejects_unsafe_record_field(field_name):
    source = (
        f"type Wrapper = Wrapper({field_name}: Int)\n"
        "func main() -> Int\n"
        "    return 1\n"
        "end func\n"
    )

    with pytest.raises(JSCompileError, match="cannot be represented safely"):
        compile_to_js(source, typecheck=False)


def test_js_compiler_allows_non_constructor_helper_record_field():
    source = (
        "type Wrapper = Wrapper(array_new: Int)\n"
        "func main() -> Int\n"
        "    return Wrapper(7).array_new\n"
        "end func\n"
    )

    assert "function Wrapper(array_new)" in compile_to_js(source)


@pytest.mark.parametrize(
    "name",
    [
        "Array",
        "Error",
        "String",
        "_geno_canvas",
        "document",
        "fetch",
        "parseFloat",
        "parseInt",
        "require",
        "undefined",
    ],
)
def test_js_compiler_rejects_emitted_host_intrinsic_binding(name):
    binding = (
        f"type Host = {name}(value: Int)\n"
        if name[0].isupper()
        else f"func {name}() -> Int\n    return 1\nend func\n"
    )
    source = binding + "func main() -> Int\n    return 1\nend func\n"

    with pytest.raises(JSCompileError, match="reserved runtime name"):
        compile_to_js(source, typecheck=False)


def test_js_single_program_rejects_function_trait_dispatcher_collision():
    source = (
        "type ThingType = Thing(value: Int)\n"
        "trait Runnable\n"
        "    func main(self: Self) -> Int\n"
        "end trait\n"
        "impl Runnable for ThingType\n"
        "    func main(self: ThingType) -> Int\n"
        "        return 1\n"
        "    end func\n"
        "end impl\n"
        "func main() -> Int\n"
        "    return 7\n"
        "end func\n"
    )

    with pytest.raises(JSCompileError, match="conflicts with a trait dispatcher"):
        compile_to_js(source, typecheck=False)


def test_js_single_app_rejects_lifecycle_trait_dispatcher_collision():
    source = (
        "type Model = Model(value: Int)\n"
        "trait Initializable\n"
        "    func init(self: Self) -> Int\n"
        "end trait\n"
        "impl Initializable for Model\n"
        "    func init(self: Model) -> Int\n"
        "        return self.value\n"
        "    end func\n"
        "end impl\n"
        "func init() -> Int\n"
        "    return 0\n"
        "end func\n"
        "func update(model: Int, dt: Float) -> Int\n"
        "    return model\n"
        "end func\n"
        "func render(model: Int) -> Unit\n"
        "    return ()\n"
        "end func\n"
    )

    with pytest.raises(JSCompileError, match="conflicts with a trait dispatcher"):
        compile_to_js(source, typecheck=False)


def test_js_standalone_rejects_runtime_host_parse_float_shadow():
    source = """
func parseFloat(value: String) -> Float
    example "2.5" -> 1.0
    return 1.0
end func
func main() -> Float
    return 1.0
end func
"""

    with pytest.raises(JSCompileError, match="reserved runtime name"):
        compile_to_js(source)
