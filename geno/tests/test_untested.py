"""Tests for @untested annotation (P1-19)."""

import tempfile
from pathlib import Path

import pytest

from geno.api import RunConfig, run
from geno.lexer import Lexer
from geno.parser import ParseError, ParseErrors, Parser


def _parse(source: str):
    tokens = Lexer(source).tokenize()
    return Parser(tokens).parse_program()


class TestUntestedParsing:
    """Test that @untested annotation parses correctly."""

    def test_basic_untested(self):
        source = """
        @untested("renders canvas")
        func draw_board() -> Unit
            return ()
        end func draw_board
        """
        program = _parse(source)
        func = program.definitions[0]
        assert func.name == "draw_board"
        assert func.untested_reason == "renders canvas"

    def test_untested_with_params(self):
        source = """
        @untested("handles user input")
        func on_click(x: Int, y: Int) -> Unit
            return ()
        end func on_click
        """
        program = _parse(source)
        func = program.definitions[0]
        assert func.untested_reason == "handles user input"

    def test_untested_on_async_func(self):
        source = """
        @untested("network I/O")
        async func fetch_data(url: String) -> String
            return "data"
        end func fetch_data
        """
        program = _parse(source)
        func = program.definitions[0]
        assert func.untested_reason == "network I/O"
        assert func.is_async is True

    def test_missing_reason_fails(self):
        source = """
        @untested()
        func bad() -> Unit
            return ()
        end func bad
        """
        with pytest.raises((ParseError, ParseErrors)):
            _parse(source)

    def test_unknown_annotation_fails(self):
        source = """
        @deprecated("old")
        func bad() -> Unit
            return ()
        end func bad
        """
        with pytest.raises((ParseError, ParseErrors)):
            _parse(source)

    def test_untested_on_type_fails(self):
        source = """
        @untested("not valid")
        type Color = Red | Green | Blue
        """
        with pytest.raises((ParseError, ParseErrors)):
            _parse(source)


class TestUntestedTypechecking:
    """Test that @untested suppresses example requirement."""

    def test_untested_skips_example_requirement(self):
        source = """
        @untested("renders to canvas")
        func draw_board() -> Unit
            return ()
        end func draw_board

        func main() -> Int
            return 0
        end func main
        """
        result = run(source, config=RunConfig())
        assert result.ok is True

    def test_without_untested_requires_example(self):
        source = """
        func draw_board() -> Unit
            return ()
        end func draw_board
        """
        result = run(source, config=RunConfig())
        assert result.ok is False
        assert any("example" in d.message for d in result.diagnostics)

    def test_untested_with_other_functions(self):
        source = """
        func double(x: Int) -> Int
            example 2 -> 4
            return x * 2
        end func double

        @untested("UI rendering")
        func render_score(score: Int) -> Unit
            return ()
        end func render_score

        func main() -> Int
            return double(21)
        end func main
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == 42


class TestUntestedTestRunner:
    """Test that geno test reports @untested functions."""

    def test_untested_reported_in_summary(self):
        from geno.test_runner import _test_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".geno", delete=False) as f:
            f.write("""
func double(x: Int) -> Int
    example 2 -> 4
    return x * 2
end func double

@untested("renders to canvas")
func draw_score(score: Int) -> Unit
    return ()
end func draw_score
""")
            f.flush()
            result = _test_file(Path(f.name))

        assert result.error is None
        assert result.harness_result is not None
        assert result.harness_result.passed == 1  # example passes
        assert len(result.harness_result.untested) == 1
        name, reason = result.harness_result.untested[0]
        assert name == "draw_score"
        assert reason == "renders to canvas"

    def test_multiple_untested_functions(self):
        from geno.test_runner import _test_file

        with tempfile.NamedTemporaryFile(mode="w", suffix=".geno", delete=False) as f:
            f.write("""
func add(a: Int, b: Int) -> Int
    example (1, 2) -> 3
    return a + b
end func add

@untested("canvas rendering")
func draw() -> Unit
    return ()
end func draw

@untested("event handler")
func on_click() -> Unit
    return ()
end func on_click
""")
            f.flush()
            result = _test_file(Path(f.name))

        assert result.error is None
        assert result.harness_result is not None
        assert len(result.harness_result.untested) == 2
