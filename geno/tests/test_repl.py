"""
Tests for the Geno REPL
=======================

Tests REPL commands, expression evaluation, error recovery,
multi-line input, and environment state management.
"""

import io
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.repl import BANNER, HELP_TEXT, REPL

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_repl() -> REPL:
    """Create a fresh REPL instance."""
    return REPL()


def repl_process(repl: REPL, line: str) -> str:
    """Run _process_input and capture stdout."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        repl._process_input(line)
    return buf.getvalue()


def repl_execute(repl: REPL, source: str) -> str:
    """Run _execute and capture stdout."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        repl._execute(source)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestReplInit:
    def test_initial_state(self):
        repl = make_repl()
        assert repl.running is True
        assert repl.history == []
        assert repl.accumulated_source == []
        assert repl.interpreter is not None
        assert repl.type_checker is not None

    def test_banner_contains_version(self):
        assert "GENO" in BANNER

    def test_help_text_lists_commands(self):
        for cmd in (":help", ":load", ":type", ":ast", ":clear", ":quit"):
            assert cmd in HELP_TEXT


# ---------------------------------------------------------------------------
# :quit / :q
# ---------------------------------------------------------------------------


class TestQuitCommand:
    def test_quit_sets_running_false(self):
        repl = make_repl()
        repl_process(repl, ":quit")
        assert repl.running is False

    def test_q_alias_sets_running_false(self):
        repl = make_repl()
        repl_process(repl, ":q")
        assert repl.running is False

    def test_quit_prints_goodbye(self):
        repl = make_repl()
        output = repl_process(repl, ":quit")
        assert "Goodbye" in output

    def test_quit_case_insensitive(self):
        repl = make_repl()
        repl_process(repl, ":QUIT")
        assert repl.running is False


# ---------------------------------------------------------------------------
# :help
# ---------------------------------------------------------------------------


class TestHelpCommand:
    def test_help_prints_text(self):
        repl = make_repl()
        output = repl_process(repl, ":help")
        assert ":load" in output
        assert ":type" in output
        assert ":ast" in output
        assert ":clear" in output
        assert ":quit" in output

    def test_help_does_not_stop_repl(self):
        repl = make_repl()
        repl_process(repl, ":help")
        assert repl.running is True


# ---------------------------------------------------------------------------
# :clear
# ---------------------------------------------------------------------------


class TestClearCommand:
    def test_clear_resets_environment(self):
        repl = make_repl()
        # Define something
        repl_execute(
            repl,
            "func answer() -> Int\n    example () -> 42\n    return 42\nend func answer",
        )
        # Verify it's callable
        output_before = repl_execute(repl, "answer()")
        assert "42" in output_before

        # Clear and verify the definition is gone
        repl_process(repl, ":clear")
        output_after = repl_execute(repl, "answer()")
        # After clear the function should not be defined; expect an error, not "42"
        assert "42" not in output_after

    def test_clear_prints_confirmation(self):
        repl = make_repl()
        output = repl_process(repl, ":clear")
        assert "cleared" in output.lower()

    def test_clear_keeps_repl_running(self):
        repl = make_repl()
        repl_process(repl, ":clear")
        assert repl.running is True


# ---------------------------------------------------------------------------
# Unknown command
# ---------------------------------------------------------------------------


class TestUnknownCommand:
    def test_unknown_command_prints_error(self):
        repl = make_repl()
        output = repl_process(repl, ":notacommand")
        assert "unknown" in output.lower() or "Unknown" in output

    def test_unknown_command_suggests_help(self):
        repl = make_repl()
        output = repl_process(repl, ":notacommand")
        assert ":help" in output

    def test_unknown_command_keeps_repl_running(self):
        repl = make_repl()
        repl_process(repl, ":notacommand")
        assert repl.running is True


# ---------------------------------------------------------------------------
# Expression evaluation
# ---------------------------------------------------------------------------


class TestExpressionEvaluation:
    def test_integer_literal(self):
        repl = make_repl()
        output = repl_execute(repl, "42")
        assert "42" in output

    def test_arithmetic(self):
        repl = make_repl()
        output = repl_execute(repl, "1 + 2")
        assert "3" in output

    def test_string_literal(self):
        repl = make_repl()
        output = repl_execute(repl, '"hello"')
        assert "hello" in output

    def test_boolean_true(self):
        repl = make_repl()
        output = repl_execute(repl, "true")
        assert "true" in output.lower() or "True" in output

    def test_boolean_false(self):
        repl = make_repl()
        output = repl_execute(repl, "false")
        assert "false" in output.lower() or "False" in output

    def test_addition_result_displayed(self):
        repl = make_repl()
        output = repl_execute(repl, "10 + 5")
        assert "15" in output

    def test_history_records_successful_expressions(self):
        repl = make_repl()
        repl_execute(repl, "1 + 1")
        assert len(repl.history) == 1
        assert "1 + 1" in repl.history[0]

    def test_empty_line_is_ignored(self):
        repl = make_repl()
        output = repl_process(repl, "")
        assert output == ""
        assert repl.history == []

    def test_whitespace_only_line_is_ignored(self):
        repl = make_repl()
        output = repl_process(repl, "   ")
        assert output == ""


# ---------------------------------------------------------------------------
# Function definitions
# ---------------------------------------------------------------------------


class TestFunctionDefinitions:
    def test_define_and_call_function(self):
        repl = make_repl()
        repl_execute(
            repl,
            "func double(x: Int) -> Int\n    example (2) -> 4\n    return x * 2\nend func double",
        )
        output = repl_execute(repl, "double(5)")
        assert "10" in output

    def test_function_definition_prints_defined(self):
        repl = make_repl()
        output = repl_execute(
            repl,
            "func greet() -> Int\n    example () -> 1\n    return 1\nend func greet",
        )
        assert "Defined" in output or "defined" in output or "=>" in output

    def test_function_persists_across_calls(self):
        repl = make_repl()
        repl_execute(
            repl,
            "func triple(x: Int) -> Int\n    example (1) -> 3\n    return x * 3\nend func triple",
        )
        out1 = repl_execute(repl, "triple(2)")
        out2 = repl_execute(repl, "triple(4)")
        assert "6" in out1
        assert "12" in out2


# ---------------------------------------------------------------------------
# Type completion (#228)
# ---------------------------------------------------------------------------


class TestTypeCompletion:
    def test_type_names_in_completions(self):
        repl = make_repl()
        repl_execute(repl, "type Color\n    = Red\n    | Green\n    | Blue")
        completions = repl._get_completions("Col")
        assert "Color" in completions

    def test_constructor_names_in_completions(self):
        repl = make_repl()
        repl_execute(repl, "type Color\n    = Red\n    | Green\n    | Blue")
        assert "Red" in repl._get_completions("Re")
        assert "Green" in repl._get_completions("Gr")

    def test_all_constructors_available(self):
        repl = make_repl()
        repl_execute(
            repl,
            "type Shape\n    = Circle(radius: Float)\n    | Rect(w: Float, h: Float)",
        )
        all_completions = repl._get_completions("")
        assert "Shape" in all_completions
        assert "Circle" in all_completions
        assert "Rect" in all_completions

    def test_clear_resets_type_completions(self):
        repl = make_repl()
        repl_execute(repl, "type Color\n    = Red\n    | Green\n    | Blue")
        assert "Color" in repl._get_completions("Col")
        repl_process(repl, ":clear")
        assert "Color" not in repl._get_completions("Col")

    def test_functions_still_complete(self):
        repl = make_repl()
        repl_execute(
            repl,
            "func double(x: Int) -> Int\n    example (2) -> 4\n    return x * 2\nend func double",
        )
        assert "double" in repl._get_completions("dou")


# ---------------------------------------------------------------------------
# Error recovery
# ---------------------------------------------------------------------------


class TestErrorRecovery:
    def test_syntax_error_does_not_crash(self):
        repl = make_repl()
        output = repl_execute(repl, "func ()")  # invalid syntax
        assert repl.running is True
        assert output != ""  # some error message printed

    def test_type_error_does_not_crash(self):
        repl = make_repl()
        # Define a function with wrong return type to trigger a type error
        output = repl_execute(
            repl,
            'func bad() -> Int\n    example () -> 0\n    return "not an int"\nend func bad',
        )
        assert repl.running is True
        assert "error" in output.lower()  # some error message printed

    def test_runtime_error_does_not_crash(self):
        repl = make_repl()
        # Accessing undefined variable triggers a runtime error
        output = repl_execute(repl, "undefined_variable_xyz")
        assert repl.running is True
        assert "error" in output.lower()

    def test_repl_usable_after_error(self):
        repl = make_repl()
        repl_execute(repl, "!!!invalid!!!")
        # REPL should still work
        output = repl_execute(repl, "1 + 1")
        assert "2" in output


# ---------------------------------------------------------------------------
# :type command
# ---------------------------------------------------------------------------


class TestTypeCommand:
    def test_type_of_integer(self):
        repl = make_repl()
        output = repl_process(repl, ":type 42")
        assert "Int" in output

    def test_type_of_string(self):
        repl = make_repl()
        output = repl_process(repl, ':type "hello"')
        assert "String" in output

    def test_type_of_boolean(self):
        repl = make_repl()
        output = repl_process(repl, ":type true")
        assert "Bool" in output

    def test_type_missing_arg_shows_usage(self):
        repl = make_repl()
        output = repl_process(repl, ":type")
        assert "Usage" in output or "usage" in output


# ---------------------------------------------------------------------------
# :ast command
# ---------------------------------------------------------------------------


class TestAstCommand:
    def test_ast_shows_tokens(self):
        repl = make_repl()
        output = repl_process(repl, ":ast 1 + 2")
        assert "Tokens" in output or "tokens" in output

    def test_ast_shows_ast(self):
        repl = make_repl()
        output = repl_process(repl, ":ast 1 + 2")
        assert "AST" in output

    def test_ast_missing_arg_shows_usage(self):
        repl = make_repl()
        output = repl_process(repl, ":ast")
        assert "Usage" in output or "usage" in output

    def test_ast_function_def(self):
        repl = make_repl()
        source = "func f() -> Int\n    example () -> 0\n    return 0\nend func f"
        output = repl_process(repl, f":ast {source}")
        assert "FunctionDef" in output or "AST" in output


# ---------------------------------------------------------------------------
# :load command
# ---------------------------------------------------------------------------


class TestLoadCommand:
    def test_load_missing_arg_shows_usage(self):
        repl = make_repl()
        output = repl_process(repl, ":load")
        assert "Usage" in output or "usage" in output

    def test_load_nonexistent_file(self):
        repl = make_repl()
        output = repl_process(repl, ":load /nonexistent/path/file.geno")
        assert "not found" in output.lower() or "error" in output.lower()

    def test_load_valid_file(self):
        repl = make_repl()
        source = (
            "func loaded_fn() -> Int\n"
            "    example () -> 99\n"
            "    return 99\n"
            "end func loaded_fn\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".geno", delete=False) as f:
            f.write(source)
            fname = f.name
        try:
            output = repl_process(repl, f":load {fname}")
            assert "loaded" in output.lower() or "Load" in output
        finally:
            os.unlink(fname)

    def test_load_defines_functions(self):
        repl = make_repl()
        source = (
            "func loaded_val() -> Int\n"
            "    example () -> 7\n"
            "    return 7\n"
            "end func loaded_val\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".geno", delete=False) as f:
            f.write(source)
            fname = f.name
        try:
            repl_process(repl, f":load {fname}")
            output = repl_execute(repl, "loaded_val()")
            assert "7" in output
        finally:
            os.unlink(fname)


# ---------------------------------------------------------------------------
# Multi-line input (_read_input)
# ---------------------------------------------------------------------------


class TestMultiLineInput:
    def test_line_continuation_accumulates(self):
        repl = make_repl()
        # Simulate a line ending with backslash
        with patch("builtins.input", return_value="let x: Int = \\"):
            result = repl._read_input()
        # Should return None and accumulate
        assert result is None
        assert len(repl.accumulated_source) == 1

    def test_continuation_prompt_switches(self):
        repl = make_repl()
        repl.accumulated_source = ["something"]
        # When accumulated_source is non-empty, completing the input returns full text
        with patch("builtins.input", return_value="end"):
            result = repl._read_input()
        assert result is not None
        assert "something" in result
        assert "end" in result
        assert repl.accumulated_source == []

    def test_normal_line_returned_directly(self):
        repl = make_repl()
        with patch("builtins.input", return_value="42"):
            result = repl._read_input()
        assert result == "42"

    def test_eof_raises(self):
        repl = make_repl()
        with patch("builtins.input", side_effect=EOFError):
            with pytest.raises(EOFError):
                repl._read_input()


# ---------------------------------------------------------------------------
# _print_ast helper
# ---------------------------------------------------------------------------


class TestPrintAst:
    def test_prints_dataclass_node(self):
        from geno.ast_nodes import IntegerLiteral
        from geno.tokens import SourceLocation

        repl = make_repl()
        loc = SourceLocation("<test>", 1, 1)
        node = IntegerLiteral(location=loc, value=99)

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            repl._print_ast(node, indent=0)
        output = buf.getvalue()
        assert "IntegerLiteral" in output
        assert "99" in output

    def test_prints_non_dataclass_as_repr(self):
        repl = make_repl()
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            repl._print_ast("plain string", indent=0)
        output = buf.getvalue()
        assert "plain string" in output


class TestReplBlockCompletion:
    """Regression tests for #663 / F-0024: ``type`` declarations are
    single-expression aliases / variant lists in Geno — they never use
    ``end``.  The REPL previously treated ``type`` as a block opener,
    so pasting ``type Foo = Bar`` hung waiting for an ``end`` that
    would never come."""

    def test_type_alias_is_not_an_open_block(self):
        assert REPL._has_unclosed_block("type MyInt = Int\n") is False

    def test_type_variant_list_is_not_an_open_block(self):
        source = "type Color = Red | Green | Blue\n"
        assert REPL._has_unclosed_block(source) is False

    def test_regular_block_openers_still_detected(self):
        """Sanity — removing ``type`` from the set must not break the
        other openers."""
        assert REPL._has_unclosed_block("func foo() -> Int\n") is True
        assert REPL._has_unclosed_block("async func foo() -> Int\n") is True
        assert REPL._has_unclosed_block("export func foo() -> Int\n") is True
        assert REPL._has_unclosed_block("export async func foo() -> Int\n") is True
        assert REPL._has_unclosed_block("if x then\n") is True
        assert REPL._has_unclosed_block("while x do\n") is True
        # And complete blocks still close out:
        assert (
            REPL._has_unclosed_block("func foo() -> Int\n    return 0\nend func\n")
            is False
        )


class TestReplExecutionBudget:
    """Each REPL input must get a fresh execution budget; the step and output
    counters must not accumulate across a long-lived session (F: MED-14)."""

    def test_step_budget_resets_between_inputs(self):
        repl = make_repl()
        # Tiny per-input budget: `1 + 1` costs only a few steps, but across many
        # inputs the cumulative count would exceed this without a per-input reset.
        repl.interpreter.max_steps = 40
        repl.interpreter._step_limit = 40

        for _ in range(100):
            out = repl_execute(repl, "1 + 1")
            assert "Step limit" not in out
            assert "=> 2" in out
        # The counter is reset each input, so it never climbs toward the budget.
        assert repl.interpreter.steps < 40

    def test_output_budget_resets_between_inputs(self):
        repl = make_repl()
        # A small cumulative output cap that a single print stays under but that
        # would be exceeded across many inputs without a per-input reset.
        repl.interpreter.sandbox_config.max_output_length = 60

        for _ in range(100):
            out = repl_execute(repl, 'print("hello from this repl input")')
            assert "Output limit" not in out
        assert repl.interpreter._output_length < 60
