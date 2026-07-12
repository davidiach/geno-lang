"""Tests for first-class test blocks (P1-18)."""

import subprocess
import tempfile
from pathlib import Path

import pytest

from geno.api import RunConfig, run
from geno.ast_nodes import AssertStatement, TestBlock
from geno.lexer import Lexer
from geno.parser import Parser
from geno.tests._script_runner import run_node_code


def _parse(source: str):
    tokens = Lexer(source).tokenize()
    return Parser(tokens).parse_program()


class TestTestBlockParsing:
    """Test that test blocks parse correctly."""

    def test_basic_test_block(self):
        source = """
        func double(x: Int) -> Int
            example 2 -> 4
            return x * 2
        end func double

        test "double works"
            assert double(3) == 6
            assert double(0) == 0
        end test
        """
        program = _parse(source)
        test_blocks = [d for d in program.definitions if isinstance(d, TestBlock)]
        assert len(test_blocks) == 1
        assert test_blocks[0].name == "double works"
        assert len(test_blocks[0].body) == 2

    def test_assert_statements_parsed(self):
        source = """
        func id(x: Int) -> Int
            example 1 -> 1
            return x
        end func id

        test "assertions"
            assert true
            assert 1 == 1
        end test
        """
        program = _parse(source)
        test_blocks = [d for d in program.definitions if isinstance(d, TestBlock)]
        assert len(test_blocks) == 1
        for stmt in test_blocks[0].body:
            assert isinstance(stmt, AssertStatement)

    def test_multiple_test_blocks(self):
        source = """
        func add(a: Int, b: Int) -> Int
            example (1, 2) -> 3
            return a + b
        end func add

        test "add positive"
            assert add(1, 2) == 3
        end test

        test "add zero"
            assert add(0, 0) == 0
        end test
        """
        program = _parse(source)
        test_blocks = [d for d in program.definitions if isinstance(d, TestBlock)]
        assert len(test_blocks) == 2
        assert test_blocks[0].name == "add positive"
        assert test_blocks[1].name == "add zero"

    def test_test_block_with_let_and_assert(self):
        source = """
        func double(x: Int) -> Int
            example 2 -> 4
            return x * 2
        end func double

        test "complex test"
            let result: Int = double(5)
            assert result == 10
        end test
        """
        program = _parse(source)
        test_blocks = [d for d in program.definitions if isinstance(d, TestBlock)]
        assert len(test_blocks) == 1
        assert len(test_blocks[0].body) == 2  # let + assert


class TestTestBlockExecution:
    """Test that test blocks execute correctly via geno test."""

    def test_passing_test_block(self):
        source = """
        func double(x: Int) -> Int
            example 2 -> 4
            return x * 2
        end func double

        test "double works"
            assert double(3) == 6
        end test
        """
        result = run(source, config=RunConfig(check_examples=True))
        assert result.ok is True

    def test_test_block_runs_in_interpreter(self):
        """Test blocks should not cause errors during normal execution."""
        source = """
        func add(a: Int, b: Int) -> Int
            example (1, 2) -> 3
            return a + b
        end func add

        test "add works"
            assert add(2, 3) == 5
        end test

        func main() -> Int
            return add(10, 20)
        end func main
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == 30

    def test_failing_assert_in_interpreter(self):
        """Test blocks are skipped during normal run — only geno test executes them."""
        source = """
        func double(x: Int) -> Int
            example 2 -> 4
            return x * 2
        end func double

        test "will fail"
            assert double(3) == 999
        end test

        func main() -> Int
            return 0
        end func main
        """
        # Normal run should succeed — test blocks aren't run during normal execution
        result = run(source, config=RunConfig())
        assert result.ok is True


class TestTestBlockTestRunner:
    """Test that geno test discovers and runs test blocks."""

    def test_test_runner_runs_test_blocks(self):
        from geno.test_runner import _test_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".geno", delete=False) as f:
            f.write("""
func double(x: Int) -> Int
    example 2 -> 4
    return x * 2
end func double

test "double basics"
    assert double(0) == 0
    assert double(5) == 10
end test
""")
            f.flush()
            result = _test_file(Path(f.name))

        assert result.error is None
        assert result.harness_result is not None
        # 1 example + 1 test block = 2 total
        assert result.harness_result.total == 2
        assert result.harness_result.passed == 2
        assert result.harness_result.failed == 0

    def test_test_runner_keeps_tuple_value_for_single_parameter_example(self):
        from geno.test_runner import _test_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".geno", delete=False) as f:
            f.write("""
func sum_pair(pair: (Int, Int)) -> Int
    example (1, 2) -> 3
    let (a, b): (Int, Int) = pair
    return a + b
end func sum_pair
""")
            f.flush()
            result = _test_file(Path(f.name))

        assert result.error is None
        assert result.harness_result is not None
        assert result.harness_result.total == 1
        assert result.harness_result.passed == 1
        assert result.harness_result.failed == 0

    def test_test_runner_reports_failing_test_block(self):
        from geno.test_runner import _test_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".geno", delete=False) as f:
            f.write("""
func double(x: Int) -> Int
    example 2 -> 4
    return x * 2
end func double

test "will fail"
    assert double(3) == 999
end test
""")
            f.flush()
            result = _test_file(Path(f.name))

        assert result.error is None
        assert result.harness_result is not None
        assert result.harness_result.failed == 1
        violations = result.harness_result.violations
        assert len(violations) == 1
        assert violations[0].kind == "test"
        assert violations[0].function == "will fail"

    def test_test_block_with_pattern_matching(self):
        source = """
        func safe_div(a: Int, b: Int) -> Result[Int, String]
            example (10, 2) -> Ok(5)
            example (10, 0) -> Err("division by zero")

            if b == 0 then
                return Err("division by zero")
            end if
            return Ok(a / b)
        end func safe_div

        test "safe_div error handling"
            let r: Result[Int, String] = safe_div(10, 0)
            let is_err: Bool = match r with
                | Ok(_) -> false
                | Err(_) -> true
            end match
            assert is_err
        end test

        func main() -> Int
            return 0
        end func main
        """
        result = run(source, config=RunConfig())
        assert result.ok is True


class TestTestBlockCompilation:
    """Test that test blocks are skipped in compiled output."""

    def test_python_compiled_skips_test_blocks(self):
        from geno.compiler import Compiler

        source = """
        func double(x: Int) -> Int
            example 2 -> 4
            return x * 2
        end func double

        test "double works"
            assert double(3) == 6
        end test

        func main() -> Int
            return double(21)
        end func main
        """
        program = _parse(source)
        compiler = Compiler()
        python_code = compiler.compile(program)
        # Test block should not appear in compiled output
        assert "double works" not in python_code
        # But main should work
        python_code += "\n__result__ = main()\n"
        env: dict[str, object] = {}
        exec(python_code, env)
        assert env["__result__"] == 42

    def test_js_compiled_skips_test_blocks(self):
        from geno.js_compiler import compile_to_js

        source = """
        func double(x: Int) -> Int
            example 2 -> 4
            return x * 2
        end func double

        test "double works"
            assert double(3) == 6
        end test

        func main() -> Int
            return double(21)
        end func main
        """
        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        assert "double works" not in js_code
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "42"
