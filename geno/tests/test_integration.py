"""
Integration tests for Geno example programs and cross-cutting pipeline.

Covers:
  - All .geno example files through both backends
  - End-to-end pipeline: source -> lex -> parse -> typecheck -> compile -> run
  - Interpreter vs compiler output agreement
  - Cross-module error propagation via the high-level API
  - Edge cases in the full pipeline
"""

import pathlib

import pytest

import geno
from geno.compiler import compile_and_exec, compile_to_python
from geno.interpreter import interpret
from geno.lexer import Lexer
from geno.parser import Parser
from geno.typechecker import TypeChecker

EXAMPLES_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "examples"


def _example_files():
    """Discover all .geno files in the examples directory."""
    return sorted(EXAMPLES_DIR.glob("*.geno"))


def _example_ids():
    return [f.stem for f in _example_files()]


# ---------------------------------------------------------------------------
# Example programs: interpreter + compiler
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gen_file", _example_files(), ids=_example_ids())
class TestExamplePrograms:
    """Run each example through both backends."""

    def test_interpreter(self, gen_file):
        """Example runs successfully through the interpreter."""
        source = gen_file.read_text()
        result = interpret(source, str(gen_file))
        assert result is not None

    def test_compiler(self, gen_file):
        """Example compiles and main() is callable."""
        source = gen_file.read_text()
        globals_dict = compile_and_exec(
            source, str(gen_file), timeout=None, capabilities={"print"}
        )
        assert "main" in globals_dict
        result = globals_dict["main"]()
        assert result is not None

    def test_backends_agree(self, gen_file):
        """Interpreter and compiler produce the same result."""
        source = gen_file.read_text()

        interp_result = interpret(source, str(gen_file))

        globals_dict = compile_and_exec(
            source, str(gen_file), timeout=None, capabilities={"print"}
        )
        compiler_result = globals_dict["main"]()

        assert interp_result == compiler_result, (
            f"Backend mismatch:\n"
            f"  interpreter: {interp_result!r}\n"
            f"  compiler:    {compiler_result!r}"
        )


# ---------------------------------------------------------------------------
# End-to-end pipeline: each phase individually
# ---------------------------------------------------------------------------

SIMPLE_SOURCE = """
func add(a: Int, b: Int) -> Int
    example (1, 2) -> 3
    return a + b
end func add

func main() -> Int
    return add(10, 32)
end func main
"""


class TestFullPipeline:
    """Verify each phase of the pipeline produces correct output."""

    def test_lex_produces_tokens(self):
        lexer = Lexer(SIMPLE_SOURCE, "<test>")
        tokens = lexer.tokenize()
        token_names = [t.type.name for t in tokens]
        assert "FUNC" in token_names
        assert "EOF" in token_names

    def test_parse_produces_program(self):
        lexer = Lexer(SIMPLE_SOURCE, "<test>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        assert len(program.definitions) == 2

    def test_typecheck_passes(self):
        lexer = Lexer(SIMPLE_SOURCE, "<test>")
        tokens = lexer.tokenize()
        program = Parser(tokens).parse_program()
        checker = TypeChecker()
        checker.check_program(program)  # must not raise

    def test_interpreter_correct_value(self):
        result = interpret(SIMPLE_SOURCE)
        assert result == 42

    def test_compiler_correct_value(self):
        g = compile_and_exec(SIMPLE_SOURCE, timeout=None)
        assert g["main"]() == 42

    def test_compile_to_python_contains_function(self):
        python_src = compile_to_python(SIMPLE_SOURCE)
        assert "def add" in python_src or "add" in python_src

    def test_api_run_correct_value(self):
        result = geno.run(SIMPLE_SOURCE)
        assert result.ok
        assert result.value == 42


# ---------------------------------------------------------------------------
# Error propagation through the API layer
# ---------------------------------------------------------------------------


class TestErrorPropagation:
    def test_lex_error_surfaced_by_api(self):
        result = geno.run("func main() -> Int\n    return $$$\nend func main")
        assert not result.ok
        assert len(result.diagnostics) > 0

    def test_parse_error_surfaced_by_api(self):
        result = geno.run("this is not valid geno code !!!")
        assert not result.ok
        assert len(result.diagnostics) > 0

    def test_type_error_surfaced_by_api(self):
        source = """
        func main() -> Int
            return "not an int"
        end func main
        """
        result = geno.run(source)
        assert not result.ok
        assert len(result.diagnostics) > 0

    def test_type_error_contains_message(self):
        source = """
        func main() -> Int
            return "wrong"
        end func main
        """
        result = geno.run(source)
        assert not result.ok
        msg = result.diagnostics[0].message
        assert len(msg) > 0

    def test_runtime_error_surfaced_by_api(self):
        # Division-by-zero style: head on empty list
        source = """
        func main() -> Int
            let xs: List[Int] = []
            return head(xs)
        end func main
        """
        result = geno.run(source)
        assert not result.ok

    def test_successful_run_has_no_diagnostics(self):
        result = geno.run("func main() -> Int\n    return 0\nend func main\n")
        assert result.ok
        assert result.diagnostics == []

    def test_check_valid_source(self):
        result = geno.check("func main() -> Int\n    return 0\nend func main\n")
        assert result.ok

    def test_check_type_error(self):
        source = 'func main() -> Int\n    return "oops"\nend func main\n'
        result = geno.check(source)
        assert not result.ok
        assert len(result.diagnostics) > 0


# ---------------------------------------------------------------------------
# Cross-cutting: interpreter and compiler agree on language features
# ---------------------------------------------------------------------------

FEATURE_CASES = [
    (
        "recursion",
        """
        func factorial(n: Int) -> Int
            example 0 -> 1
            example 5 -> 120
            if n == 0 then
                return 1
            else
                return n * factorial(n - 1)
            end if
        end func factorial
        func main() -> Int
            return factorial(6)
        end func main
        """,
        720,
    ),
    (
        "list_operations",
        """
        func main() -> Int
            let xs: List[Int] = [1, 2, 3, 4, 5]
            return length(xs)
        end func main
        """,
        5,
    ),
    (
        "string_concat",
        """
        func main() -> String
            let a: String = "hello"
            let b: String = " world"
            return a + b
        end func main
        """,
        "hello world",
    ),
    (
        "boolean_logic",
        """
        func main() -> Bool
            return true and not false
        end func main
        """,
        True,
    ),
    (
        "nested_if",
        """
        func classify(n: Int) -> String
            example 0 -> "zero"
            example 1 -> "positive"
            example (-1) -> "negative"
            if n > 0 then
                return "positive"
            else
                if n < 0 then
                    return "negative"
                else
                    return "zero"
                end if
            end if
        end func classify
        func main() -> String
            return classify(0)
        end func main
        """,
        "zero",
    ),
    (
        "while_loop",
        """
        func sum_to(n: Int) -> Int
            example 0 -> 0
            example 5 -> 15
            var total: Int = 0
            var i: Int = 1
            while i <= n do
                total = total + i
                i = i + 1
            end while
            return total
        end func sum_to
        func main() -> Int
            return sum_to(10)
        end func main
        """,
        55,
    ),
    (
        "pattern_matching",
        """
        type Shape = Circle(radius: Int) | Rect(width: Int, height: Int)

        func area(s: Shape) -> Int
            example Circle(1) -> 3
            match s with
            | Circle(r) ->
                return r * r * 3
            | Rect(w, h) ->
                return w * h
            end match
        end func area

        func main() -> Int
            return area(Rect(4, 5))
        end func main
        """,
        20,
    ),
    (
        "higher_order_functions",
        """
        func apply(f: (Int) -> Int, x: Int) -> Int
            example (fn(n: Int) -> n, 5) -> 5
            return f(x)
        end func apply

        func double(x: Int) -> Int
            example 3 -> 6
            return x * 2
        end func double

        func main() -> Int
            return apply(double, 7)
        end func main
        """,
        14,
    ),
]


@pytest.mark.parametrize(
    "name,source,expected", FEATURE_CASES, ids=[c[0] for c in FEATURE_CASES]
)
class TestBackendsAgreeOnFeatures:
    """Both backends produce the expected output for each language feature."""

    def test_interpreter(self, name, source, expected):
        result = interpret(source)
        assert result == expected, (
            f"{name}: interpreter returned {result!r}, expected {expected!r}"
        )

    def test_compiler(self, name, source, expected):
        g = compile_and_exec(source, timeout=None)
        result = g["main"]()
        assert result == expected, (
            f"{name}: compiler returned {result!r}, expected {expected!r}"
        )

    def test_backends_agree(self, name, source, expected):
        interp = interpret(source)
        g = compile_and_exec(source, timeout=None)
        compiled = g["main"]()
        assert interp == compiled, (
            f"{name}: backend mismatch — interpreter={interp!r}, compiler={compiled!r}"
        )


# ---------------------------------------------------------------------------
# API timing and output capture
# ---------------------------------------------------------------------------


class TestApiMetadata:
    def test_timing_fields_populated(self):
        source = "func main() -> Int\n    return 1\nend func main\n"
        result = geno.run(source)
        assert result.ok
        assert result.timing.total_ms >= 0
        assert result.timing.parse_ms >= 0
        assert result.timing.typecheck_ms >= 0
        assert result.timing.run_ms >= 0

    def test_output_captured(self):
        source = """
        func main() -> Int
            print("hello from geno")
            return 0
        end func main
        """
        result = geno.run(source, config=geno.RunConfig(capabilities={"print"}))
        assert result.ok
        assert "hello from geno" in result.output

    def test_value_raw_matches_value_for_int(self):
        source = "func main() -> Int\n    return 7\nend func main\n"
        result = geno.run(source)
        assert result.ok
        assert result.value == 7

    def test_steps_used_positive(self):
        source = "func main() -> Int\n    return 1\nend func main\n"
        result = geno.run(source)
        assert result.ok
        assert result.steps_used > 0


# ---------------------------------------------------------------------------
# Fibonacci integration (known values)
# ---------------------------------------------------------------------------

FIB_SOURCE = (EXAMPLES_DIR / "fibonacci.geno").read_text()


class TestFibonacciExample:
    def test_interpreter_returns_list(self):
        result = interpret(FIB_SOURCE, str(EXAMPLES_DIR / "fibonacci.geno"))
        assert isinstance(result, list)
        assert len(result) == 10

    def test_first_ten_values_correct(self):
        result = interpret(FIB_SOURCE, str(EXAMPLES_DIR / "fibonacci.geno"))
        assert result == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]

    def test_compiler_agrees(self):
        g = compile_and_exec(FIB_SOURCE, timeout=None)
        assert g["main"]() == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
