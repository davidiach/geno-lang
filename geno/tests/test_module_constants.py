"""
Tests for module-level constants (#72)
=======================================

A module-level ``let`` binds a compile-time constant. The initializer is
restricted to literal forms so that importing a module never executes code,
which is the property that keeps this feature independent of the entrypoint
work in proposal 0001.
"""

import subprocess
import sys

import pytest

from geno.api import run
from geno.ast_nodes import ModuleConstant
from geno.compiler import CompileError, compile_to_python
from geno.formatter import format_source
from geno.interpreter import interpret
from geno.js_compiler import JSCompileError, compile_to_js
from geno.lexer import Lexer
from geno.parser import ParseError, ParseErrors, Parser
from geno.symbol_table import build_symbol_table
from geno.tests._script_runner import run_node_code, run_python_code
from geno.typechecker import TypeChecker
from geno.typechecker import TypeError as GenoTypeError

USAGE_PROGRAM = (
    'let usage: String = "roman <number>"\n'
    'let numerals: List[String] = ["I", "V", "X"]\n'
    "let offset = -2\n"
    "\n"
    "func help_text() -> String\n"
    '    example () -> "roman <number>"\n'
    "    return usage\n"
    "end func\n"
    "\n"
    "func shifted(n: Int) -> Int\n"
    "    example (10) -> 8\n"
    "    return n + offset\n"
    "end func\n"
    "\n"
    "func main() -> String\n"
    "    return help_text() + numerals[1] + to_string(shifted(n: 10))\n"
    "end func\n"
)


def _parse(source, filename="<test>"):
    return Parser(Lexer(source, filename).tokenize()).parse_program()


def _check(source, filename="<test>"):
    program = _parse(source, filename)
    TypeChecker().check_program(program)
    return program


class TestParsing:
    def test_annotated_constant_parses(self):
        program = _parse('let usage: String = "hi"\n')
        (defn,) = program.definitions
        assert isinstance(defn, ModuleConstant)
        assert defn.name == "usage"
        assert defn.type_annotation is not None

    def test_annotation_is_optional(self):
        (defn,) = _parse("let limit = 3999\n").definitions
        assert isinstance(defn, ModuleConstant)
        assert defn.type_annotation is None

    @pytest.mark.parametrize(
        "initializer",
        ['"text"', "42", "-42", "3.5", "-3.5", "true", "[1, 2, 3]", '(1, "a", false)'],
    )
    def test_literal_initializers_are_accepted(self, initializer):
        (defn,) = _parse(f"let value = {initializer}\n").definitions
        assert isinstance(defn, ModuleConstant)

    @pytest.mark.parametrize(
        ("initializer", "expected"),
        [
            ("other", "a reference to another binding"),
            ("size()", "a function call"),
            ("1 + 2", "an operator expression"),
            ('f"x{1}"', "an interpolated string"),
            ("[x for x: Int in [1] if true]", "a list comprehension"),
            ("[1, size()]", "a function call"),
            ("(1, other)", "a reference to another binding"),
            ("not true", "an operator expression"),
        ],
    )
    def test_non_literal_initializers_are_rejected(self, initializer, expected):
        with pytest.raises(ParseError) as excinfo:
            _parse(f"let value = {initializer}\n")
        assert expected in str(excinfo.value)
        assert "must be initialized by a literal" in str(excinfo.value)

    def test_var_at_module_level_is_rejected(self):
        with pytest.raises((ParseError, ParseErrors)) as excinfo:
            _parse("var count = 0\n")
        assert "must be immutable" in str(excinfo.value)

    def test_export_let_is_rejected(self):
        with pytest.raises((ParseError, ParseErrors)) as excinfo:
            _parse("export let usage = 1\n")
        assert "not yet supported" in str(excinfo.value)

    def test_tuple_destructuring_is_rejected(self):
        with pytest.raises((ParseError, ParseErrors)) as excinfo:
            _parse("let (a, b): (Int, Int) = (1, 2)\n")
        assert "Tuple destructuring is not supported" in str(excinfo.value)

    def test_upper_case_name_is_rejected_with_a_naming_hint(self):
        with pytest.raises((ParseError, ParseErrors)) as excinfo:
            _parse('let USAGE = "hi"\n')
        assert "snake_case" in str(excinfo.value)

    def test_untested_annotation_is_rejected(self):
        with pytest.raises((ParseError, ParseErrors)) as excinfo:
            _parse('@untested("reason")\nlet usage = 1\n')
        assert "only valid on functions" in str(excinfo.value)

    def test_definition_error_still_lists_the_other_forms(self):
        with pytest.raises((ParseError, ParseErrors)) as excinfo:
            _parse("return 1\n")
        assert "'let'" in str(excinfo.value)


class TestTypeChecking:
    def test_constant_is_visible_in_every_function(self):
        _check(USAGE_PROGRAM)

    def test_constant_declared_after_its_use_still_resolves(self):
        _check(
            "func help_text() -> String\n"
            '    example () -> "hi"\n'
            "    return usage\n"
            "end func\n"
            "\n"
            'let usage: String = "hi"\n'
            "\n"
            "func main() -> String\n"
            "    return help_text()\n"
            "end func\n"
        )

    def test_declared_type_must_match_the_literal(self):
        with pytest.raises(GenoTypeError) as excinfo:
            _check("let n: String = 1\n\nfunc main() -> Int\n    return 1\nend func\n")
        assert "Type mismatch in 'let n'" in str(excinfo.value)

    def test_an_uninferable_literal_needs_an_annotation(self):
        with pytest.raises(GenoTypeError) as excinfo:
            _check("let xs = []\n\nfunc main() -> Int\n    return 1\nend func\n")
        assert "Cannot infer a concrete type for 'let xs'" in str(excinfo.value)

    def test_duplicate_constants_are_rejected(self):
        with pytest.raises(GenoTypeError) as excinfo:
            _check(
                "let n = 1\nlet n = 2\n\nfunc main() -> Int\n    return n\nend func\n"
            )
        assert "Duplicate module constant: 'n'" in str(excinfo.value)

    def test_a_function_may_not_reuse_a_constant_name(self):
        with pytest.raises((GenoTypeError, Exception)) as excinfo:
            _check(
                "let n = 1\n"
                "\n"
                "func n() -> Int\n"
                "    example () -> 1\n"
                "    return 1\n"
                "end func\n"
            )
        assert "conflicts with the module constant" in str(excinfo.value)

    def test_a_local_binding_may_shadow_a_constant(self):
        assert (
            run(
                'let name: String = "outer"\n'
                "\n"
                "func main() -> String\n"
                '    let name: String = "inner"\n'
                "    return name\n"
                "end func\n"
            ).value
            == "inner"
        )

    def test_an_int_constant_promotes_to_a_declared_float(self):
        assert (
            run(
                "let f: Float = 3\n\nfunc main() -> String\n    return to_string(f)\nend func\n"
            ).value
            == "3.0"
        )


class TestExecution:
    def test_interpreter_verifies_examples_that_read_a_constant(self):
        assert interpret(USAGE_PROGRAM, "<test>", check_examples=True) == (
            "roman <number>V8"
        )

    def test_backends_agree_with_the_interpreter(self):
        expected = "roman <number>V8"
        assert run(USAGE_PROGRAM).value == expected

        python_source = compile_to_python(USAGE_PROGRAM, "<test>")
        python_result = run_python_code(python_source, python_executable=sys.executable)
        assert python_result.returncode == 0, python_result.stderr
        assert python_result.stdout.strip() == expected

        js_source = compile_to_js(USAGE_PROGRAM, "<test>")
        js_result = run_node_code(js_source)
        assert js_result.returncode == 0, js_result.stderr
        assert js_result.stdout.strip() == expected

    def test_constants_are_emitted_before_the_functions_that_read_them(self):
        python_source = compile_to_python(USAGE_PROGRAM, "<test>")
        assert python_source.index("usage: 'str' = ") < python_source.index(
            "def help_text("
        )

        js_source = compile_to_js(USAGE_PROGRAM, "<test>")
        assert js_source.index("const usage = ") < js_source.index(
            "function help_text("
        )

    @pytest.mark.parametrize(
        ("compile_fn", "error_type", "reserved_name"),
        [
            (compile_to_python, CompileError, "_geno_deepcopy"),
            (compile_to_js, JSCompileError, "_deepCopy"),
        ],
    )
    def test_a_reserved_runtime_name_is_rejected(
        self, compile_fn, error_type, reserved_name
    ):
        source = (
            f"let {reserved_name} = 1\n\nfunc main() -> Int\n    return 1\nend func\n"
        )
        with pytest.raises(error_type) as excinfo:
            compile_fn(source, "<test>")
        assert "module constant" in str(excinfo.value)


class TestTooling:
    def test_the_formatter_round_trips_a_module_constant(self):
        formatted = format_source(USAGE_PROGRAM)
        assert formatted.startswith('let usage: String = "roman <number>"\n')
        assert format_source(formatted) == formatted

    def test_the_symbol_table_records_the_constant(self):
        table = build_symbol_table(_parse(USAGE_PROGRAM), "<test>")
        kinds = {defn.name: defn.kind for defn in table.definitions}
        assert kinds["usage"] == "variable"
        assert kinds["numerals"] == "variable"


class TestModulePrivacy:
    @pytest.fixture()
    def project(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Lib"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            'let prefix: String = "lib:"\n'
            "\n"
            "export func label(x: Int) -> String\n"
            '    example 3 -> "lib:3"\n'
            "    return prefix + to_string(x)\n"
            "end func\n"
        )
        return tmp_path

    def _geno(self, args, cwd):
        return subprocess.run(
            [sys.executable, "-m", "geno", *args],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(cwd),
        )

    def test_a_module_may_use_its_own_constant(self, project):
        (project / "App.geno").write_text(
            "import Lib\n\nfunc main() -> String\n    return label(5)\nend func\n"
        )
        result = self._geno(["run", "App.geno"], project)
        assert result.returncode == 0, result.stderr
        assert "lib:5" in result.stdout

    def test_an_importing_module_does_not_see_the_constant(self, project):
        (project / "App.geno").write_text(
            "import Lib\n\nfunc main() -> String\n    return prefix\nend func\n"
        )
        result = self._geno(["check", "App.geno"], project)
        assert result.returncode != 0
        assert "Undefined variable: prefix" in (result.stdout + result.stderr)
