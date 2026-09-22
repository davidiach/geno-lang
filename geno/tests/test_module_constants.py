"""
Tests for module-level constants (#72)
=======================================

A module-level ``let`` binds a compile-time constant. The initializer is
restricted to literal forms so that importing a module never executes code,
which is the property that keeps this feature independent of the entrypoint
work in proposal 0001.
"""

import contextlib
import io
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
from geno.lsp_completions import extract_completion_symbols
from geno.parser import ParseError, ParseErrors, Parser
from geno.repl import REPL
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


def _repl_execute(repl, source):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        repl._execute(source)
    return buffer.getvalue()


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


class TestNameCollisions:
    """Every top-level name a backend emits beside a constant must not clash.

    Python silently rebinds a colliding top-level name and JavaScript refuses
    to parse a second ``const``, so each of these has to be rejected before
    codegen rather than discovered at run time.
    """

    TRAIT_PROGRAM = (
        "let describe = 7\n"
        "\n"
        "trait Describable\n"
        "    func describe(self: Self) -> String\n"
        "end trait\n"
        "\n"
        "type Circle = Circle(radius: Float)\n"
        "\n"
        "impl Describable for Circle\n"
        "    func describe(self: Circle) -> String\n"
        '        example Circle(5.0) -> "circle"\n'
        '        return "circle"\n'
        "    end func\n"
        "end impl\n"
        "\n"
        "func main() -> Int\n"
        "    return describe\n"
        "end func\n"
    )

    def test_typechecker_rejects_a_trait_dispatcher_collision(self):
        with pytest.raises(GenoTypeError) as excinfo:
            _check(self.TRAIT_PROGRAM)
        assert "conflicts with a trait dispatcher" in str(excinfo.value)

    @pytest.mark.parametrize(
        ("compile_fn", "error_type"),
        [(compile_to_python, CompileError), (compile_to_js, JSCompileError)],
    )
    def test_backends_reject_a_trait_dispatcher_collision_without_typechecking(
        self, compile_fn, error_type
    ):
        with pytest.raises(error_type) as excinfo:
            compile_fn(self.TRAIT_PROGRAM, "<test>", typecheck=False)
        assert "conflicts with a trait dispatcher" in str(excinfo.value)

    @pytest.mark.parametrize("compile_fn", [compile_to_python, compile_to_js])
    def test_a_prelude_name_is_rejected_under_either_spelling(self, compile_fn):
        # 'print' is reserved in the Python prelude under its own name and in
        # the JavaScript prelude as the mangled 'print_'. Both are rejected,
        # and both name the identifier the author actually wrote.
        source = "let print = 1\n\nfunc main() -> Int\n    return print\nend func\n"
        with pytest.raises((CompileError, JSCompileError)) as excinfo:
            compile_fn(source, "<test>")
        assert "'print' is a reserved runtime name" in str(excinfo.value)


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

    def test_the_symbol_table_resolves_a_type_annotation_on_a_constant(self):
        # The initializer is a literal and names nothing, but the annotation
        # can name a user type, which rename and find-references have to see.
        source = "type Count = Int\n\nlet default_count: Count = 1\n"
        table = build_symbol_table(_parse(source), "<test>")
        count_def = next(d for d in table.definitions if d.name == "Count")
        assert [ref.name for ref in table.refs_for_def(count_def)] == ["Count"]

    def test_completion_offers_the_constant_without_exporting_it(self):
        source = "let answer = 42\n\nfunc main() -> Int\n    return answer\nend func\n"
        all_symbols, exported = extract_completion_symbols(source)
        assert ("answer", "variable") in {(s.name, s.kind) for s in all_symbols}
        assert "answer" not in {s.name for s in exported}
        assert "main" in {s.name for s in exported}

    def test_completion_keeps_the_constant_private_beside_an_export(self):
        source = (
            "let secret = 1\n\n"
            "export func pub() -> Int\n"
            "    example () -> 1\n"
            "    return secret\n"
            "end func\n"
        )
        all_symbols, exported = extract_completion_symbols(source)
        assert "secret" in {s.name for s in all_symbols}
        assert {s.name for s in exported} == {"pub"}

    def test_completion_regex_fallback_keeps_the_constant_private(self):
        # An unterminated string defeats the lexer, so completion falls back to
        # the regex scanner; the constant must not leak into the exports there
        # either, where a file with no `export` keyword exports everything.
        source = 'let answer = 42\n\nfunc main() -> String\n    return "oops\n'
        all_symbols, exported = extract_completion_symbols(source)
        assert "answer" in {s.name for s in all_symbols}
        assert {s.name for s in exported} == {"main"}

    def test_the_repl_evaluates_a_module_constant(self):
        repl = REPL()
        assert "Defined." in _repl_execute(repl, "let answer = 42")
        assert "42" in _repl_execute(repl, "answer")

    def test_the_repl_reports_a_module_level_var_as_a_language_error(self):
        # VAR reaches the parser's "module-level bindings must be immutable"
        # diagnostic instead of being read as a bare expression.
        output = _repl_execute(REPL(), "var answer = 42")
        assert "immutable" in output


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

    def test_a_constant_wins_over_an_imported_name_it_shadows(self, project):
        # A locally defined function already shadows an imported name of the
        # same spelling; a constant has to behave the same way, on every path.
        (project / "App.geno").write_text(
            "import Lib\n"
            "\n"
            "let label = 7\n"
            "\n"
            "func main() -> Int\n"
            "    return label\n"
            "end func\n"
        )
        run_result = self._geno(["run", "App.geno"], project)
        assert run_result.returncode == 0, run_result.stderr
        assert "7" in run_result.stdout

        js_path = project / "app.js"
        compile_result = self._geno(
            ["compile", "App.geno", "--target", "js", "-o", str(js_path)], project
        )
        assert compile_result.returncode == 0, compile_result.stderr
        node_result = run_node_code(js_path.read_text())
        assert node_result.returncode == 0, node_result.stderr
        assert node_result.stdout.strip() == "7"

    def test_an_importing_module_does_not_see_the_constant(self, project):
        (project / "App.geno").write_text(
            "import Lib\n\nfunc main() -> String\n    return prefix\nend func\n"
        )
        result = self._geno(["check", "App.geno"], project)
        assert result.returncode != 0
        assert "Undefined variable: prefix" in (result.stdout + result.stderr)

    @pytest.mark.parametrize("aliased", [False, True])
    @pytest.mark.parametrize("constant_last", [False, True])
    def test_project_tests_use_the_owning_module_scope(
        self, tmp_path, aliased, constant_last
    ):
        from geno.test_runner import run_project_test_suite

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Lib"]\n'
        )
        for module, value in [("Lib", "lib"), ("App", "app")]:
            constant = f'let prefix = "{value}"\n'
            source = (
                "export func label() -> String\n"
                "    example () -> prefix\n"
                f'    example () -> "{value}"\n'
                "    return prefix\n"
                "end func\n"
            )
            source = source + constant if constant_last else constant + source
            source += (
                'test "own module bindings"\n'
                f'    assert prefix == "{value}"\n'
                f'    assert label() == "{value}"\n'
                "end test\n"
            )
            if module == "App":
                namespace = "L" if aliased else "Lib"
                source = ("import Lib as L\n" if aliased else "import Lib\n") + source
                source += (
                    'test "imported function retains its module"\n'
                    f'    assert {namespace}.label() == "lib"\n'
                    "end test\n"
                )
            (tmp_path / f"{module}.geno").write_text(source)

        result = run_project_test_suite(tmp_path)

        assert result.success, result.to_dict()
        assert result.passed == result.total == 7

    def test_project_module_scopes_share_the_step_budget(self, tmp_path):
        from geno.sandbox import SandboxConfig
        from geno.test_runner import run_project_test_suite, run_test_suite

        source = (
            "let limit = 5\n"
            'test "bounded work"\n'
            "    var total = 0\n"
            "    for n: Int in range(0, limit) do\n"
            "        total = total + n\n"
            "    end for\n"
            "    assert total == 10\n"
            "end test\n"
        )
        paths = [tmp_path / f"{name}.geno" for name in ["First", "Second", "Third"]]
        for path in paths:
            path.write_text(source)
            single = run_test_suite(
                [path], sandbox_config=SandboxConfig(timeout=5.0, max_steps=50)
            )
            assert single.success, single.to_dict()

        (tmp_path / "geno.toml").write_text('files = ["First", "Second", "Third"]\n')
        result = run_project_test_suite(
            tmp_path, sandbox_config=SandboxConfig(timeout=5.0, max_steps=50)
        )

        assert not result.success
        assert result.passed == 1
        assert result.failed == 2
        assert result.total == 3
        assert any(
            "Step limit exceeded" in violation.message
            for file_result in result.file_results
            if file_result.harness_result is not None
            for violation in file_result.harness_result.violations
        )

    @pytest.mark.parametrize("call", ["answer()", "Lib.answer()", "L.answer()"])
    @pytest.mark.parametrize("helper_first", [False, True])
    def test_imported_private_helpers_keep_their_module_scope(
        self, tmp_path, call, helper_first
    ):
        from geno.api import run_path
        from geno.test_runner import run_project_test_suite

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Lib", "Base", "Other"]\n'
        )
        for name, value in [("Base", 1), ("Other", 100)]:
            (tmp_path / f"{name}.geno").write_text(
                "func value() -> Int\n"
                f"    example () -> {value}\n"
                f"    return {value}\n"
                "end func\n"
            )
        helper = (
            "func private_help(n: Int) -> Int\n"
            "    example 2 -> 42\n"
            "    if n == 0 then\n"
            "        return offset + Local.value()\n"
            "    end if\n"
            "    return private_help(n - 1)\n"
            "end func\n"
        )
        public = (
            "export func answer() -> Int\n"
            "    example () -> 42\n"
            "    return private_help(2)\n"
            "end func\n"
        )
        (tmp_path / "Lib.geno").write_text(
            "import Base as Local\nlet offset = 41\n"
            + (helper + public if helper_first else public + helper)
        )
        (tmp_path / "App.geno").write_text(
            ("import Lib as L\n" if call.startswith("L.") else "import Lib\n")
            + "import Other as Local\n"
            "let offset = 100\n"
            "func private_help(n: Int) -> Int\n"
            "    example 2 -> 99\n"
            "    return 99\n"
            "end func\n"
            "func read_answer() -> Int\n"
            "    example () -> 42\n"
            f"    return {call}\n"
            "end func\n"
            'test "imported private helper"\n'
            "    assert read_answer() == 42\n"
            "end test\n"
            "func main() -> Int\n"
            "    return read_answer()\n"
            "end func\n"
        )

        suite = run_project_test_suite(tmp_path)
        assert suite.success, suite.to_dict()
        result = run_path(str(tmp_path))
        assert result.ok, result.diagnostics
        assert result.value == 42

    @pytest.mark.parametrize(
        "definitions, expression",
        [
            ("type Private = Private(number: Int)\n", "Private(offset).number + 1"),
            (
                "trait Value\n"
                "    func value(self: Self) -> Int\n"
                "end trait\n"
                "type Item = Item(number: Int)\n"
                "impl Value for Item\n"
                "    func value(self: Item) -> Int\n"
                "        example Item(1) -> 42\n"
                "        return self.number + offset\n"
                "    end func\n"
                "end impl\n",
                "value(Item(1))",
            ),
        ],
    )
    def test_imported_functions_keep_private_runtime_definitions(
        self, tmp_path, definitions, expression
    ):
        from geno.api import run_path
        from geno.test_runner import run_project_test_suite

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Lib"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "let offset = 41\n" + definitions + "export func answer() -> Int\n"
            "    example () -> 42\n"
            f"    return {expression}\n"
            "end func\n"
        )
        (tmp_path / "App.geno").write_text(
            "import Lib\n"
            "let offset = 100\n"
            "func read_answer() -> Int\n"
            "    example () -> 42\n"
            "    return answer()\n"
            "end func\n"
            'test "imported definitions"\n'
            "    assert answer() == 42\n"
            "end test\n"
            "func main() -> Int\n"
            "    return answer()\n"
            "end func\n"
        )

        suite = run_project_test_suite(tmp_path)
        assert suite.success, suite.to_dict()
        result = run_path(str(tmp_path))
        assert result.ok, result.diagnostics
        assert result.value == 42

    @pytest.mark.parametrize(
        "expression",
        ["private_help()", "Lib.private_help()", "L.private_help()", "Private(42)"],
    )
    def test_imported_private_runtime_definitions_remain_inaccessible(self, expression):
        from geno.interpreter import Interpreter
        from geno.interpreter import RuntimeError as GenoRuntimeError

        library = _parse(
            "let offset = 42\n"
            "type Private = Private(number: Int)\n"
            "func private_help() -> Int\n"
            "    example () -> 42\n"
            "    return offset\n"
            "end func\n"
            "export func answer() -> Int\n"
            "    example () -> 42\n"
            "    return private_help()\n"
            "end func\n"
        )
        program = _parse(
            ("import Lib as L\n" if expression.startswith("L.") else "import Lib\n")
            + "func main() -> Int\n"
            f"    return {expression}\n"
            "end func\n"
        )
        with pytest.raises(GenoTypeError, match=r"private_help|Private"):
            TypeChecker().check_program(program, modules={"Lib": library})
        if "private_help" in expression:
            with pytest.raises(GenoRuntimeError, match="private_help"):
                Interpreter(check_examples=False).run(program, modules={"Lib": library})

    def test_project_module_scopes_share_the_output_budget(self, tmp_path):
        from geno.sandbox import SandboxConfig
        from geno.test_runner import run_project_test_suite, run_test_suite

        source = (
            'let message = "abcd"\n'
            'test "bounded output"\n'
            "    print(message)\n"
            "end test\n"
        )
        for name in ["First", "Second"]:
            path = tmp_path / f"{name}.geno"
            path.write_text(source)
            single = run_test_suite(
                [path], sandbox_config=SandboxConfig(max_output_length=6)
            )
            assert single.success, single.to_dict()

        (tmp_path / "geno.toml").write_text('files = ["First", "Second"]\n')
        result = run_project_test_suite(
            tmp_path, sandbox_config=SandboxConfig(max_output_length=6)
        )

        assert not result.success
        assert result.passed == result.failed == 1
        assert any(
            "Output" in violation.message
            for file_result in result.file_results
            if file_result.harness_result is not None
            for violation in file_result.harness_result.violations
        )
