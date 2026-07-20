"""
Tests for the Geno Compiler
===========================
"""

import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, cast

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import geno.compiler as compiler_module
from geno.compiler import (
    CompileError,
    Compiler,
    _compiled_main_script_wrapper,
    _insert_compiled_runtime_capability_assignment,
    _strip_runtime_prelude_imports,
    _trusted_runtime_prelude_line_count,
    compile_and_exec,
    compile_to_python,
)
from geno.parser import parse
from geno.sandbox import ProcessSandbox, ProcessSandboxConfig
from geno.typechecker import TypeError as GenoTypeError


def compile_and_run(source: str):
    """Helper to compile and run a Geno program, returning main() result."""
    globals_dict = compile_and_exec(source, timeout=None)
    if "main" in globals_dict:
        return globals_dict["main"]()
    return None


def _compiled_runtime_env(
    max_collection_size: int = 10, max_integer_bits: int | None = None
):
    runtime_path = Path(__file__).resolve().parents[1] / "_runtime_support.py"
    env = {"_GENO_MAX_COLLECTION_SIZE": max_collection_size}
    if max_integer_bits is not None:
        env["_GENO_MAX_INTEGER_BITS"] = max_integer_bits
    exec(runtime_path.read_text(), env)
    return env


def _compiled_python_process_result(source: str, max_collection_size: int = 10):
    python_code = _strip_runtime_prelude_imports(compile_to_python(source))
    trusted_prelude_line_count = _trusted_runtime_prelude_line_count(python_code)
    for main_returns_int in (True, False):
        main_guard = _compiled_main_script_wrapper(
            is_async=False, main_returns_int=main_returns_int
        )
        if main_guard in python_code:
            python_code = python_code.replace(main_guard, "\n\n__result__ = main()\n")
    sandbox = ProcessSandbox(
        ProcessSandboxConfig(
            timeout=5,
            max_collection_size=max_collection_size,
            strict=False,
            compiled_runtime_prelude=True,
            trusted_prelude_line_count=trusted_prelude_line_count,
        )
    )
    result, _output, error = sandbox._execute_compiler_output(python_code)
    if error is not None:
        raise RuntimeError(error)
    return result


def test_compiled_process_record_update_uses_runtime_replace():
    source = """
type Point = Point(x: Int, y: Int)

func main() -> Int
    let p: Point = Point(1, 2)
    let q: Point = p with (x: 10)
    return q.x
end func
"""

    assert _compiled_python_process_result(source) == 10


@pytest.mark.parametrize("module_name", ["Evil = 1; pwn", "class"])
def test_compile_project_rejects_unsafe_module_name(module_name):
    program = parse("")
    graph = SimpleNamespace(
        parsed={module_name: program},
        sorted_modules=[module_name],
        project=SimpleNamespace(entrypoint=module_name),
    )

    with pytest.raises(CompileError, match=r"module name|reserved keyword"):
        Compiler().compile_project(graph)


class TestCompilerBasics:
    """Basic compiler functionality tests."""

    def test_compile_integer_return(self):
        """Compile and run returning an integer."""
        source = """
        func main() -> Int
            return 42
        end func
        """
        assert compile_and_run(source) == 42

    def test_compile_int_return_promotes_to_float_runtime_value(self):
        """Compiled Python materializes Float return annotations at runtime."""
        source = """
        func main() -> Float
            return 2
        end func
        """

        result = compile_and_run(source)

        assert result == 2.0
        assert type(result) is float

    def test_compile_match_expr_int_arm_promotes_to_float_runtime_value(self):
        source = """
        func main() -> Float
            let y: Float = match 1 with
                | 0 -> 1.5
                | _ -> 2
            end match
            return y
        end func
        """

        result = compile_and_exec(source, timeout=5.0)

        assert result["__result__"] == 2.0
        assert type(result["__result__"]) is float

    def test_compile_and_exec_process_sandbox_uses_trusted_prelude_prefix(self):
        """Timeout path keeps compiled runtime prelude usable under AST checks."""
        source = """
        func main() -> Int
            return 42
        end func
        """

        result = compile_and_exec(source, timeout=5.0)

        assert result["__result__"] == 42
        assert result["__output__"] == ""

    @pytest.mark.skipif(sys.platform != "darwin", reason="Darwin stability check")
    def test_compile_and_exec_process_sandbox_repeated_on_darwin(self):
        """Compiled workers must have stable Darwin VM headroom."""
        for _attempt in range(3):
            self.test_compile_and_exec_process_sandbox_uses_trusted_prelude_prefix()

    def test_compile_and_exec_process_sandbox_runs_async_main(self):
        """Timeout path awaits async main without exposing asyncio imports."""
        source = """
        async func fetch() -> Int
            return 42
        end func

        async func main() -> Int
            return await fetch()
        end func
        """

        result = compile_and_exec(source, timeout=5.0)

        assert result["__result__"] == 42
        assert result["__output__"] == ""

    def test_trusted_runtime_prelude_line_count_finds_generated_boundary(self):
        """The trusted prefix ends before compiled Geno program-body code."""
        source = """
        func main() -> Int
            return 42
        end func
        """
        python_code = _strip_runtime_prelude_imports(compile_to_python(source))

        trusted_line_count = _trusted_runtime_prelude_line_count(python_code)
        lines = python_code.splitlines()

        assert trusted_line_count > 0
        assert "# Generated Code Follows" in "\n".join(lines[:trusted_line_count])
        assert "def main" in "\n".join(lines[trusted_line_count:])

    def test_strip_runtime_prelude_imports_keeps_same_line_code_after_docstring(self):
        """Docstring stripping must not line-drop code after a semicolon."""
        python_code = '"""hello"""; x = 1\nprint(x)'

        stripped = _strip_runtime_prelude_imports(python_code)

        assert stripped == "x = 1\nprint(x)"

    def test_windowed_strip_matches_full_parse(self):
        """The line-window fast path must match a full-parse strip exactly.

        The windowed probe in _strip_leading_docstring_and_imports exists
        only for speed (the runtime prelude is ~4k lines and was fully
        ast-parsed on every CLI run); its result must be byte-identical to
        analysing the full parse, including sources engineered to defeat
        the first window.
        """
        import ast as ast_mod

        from geno.compiler import (
            _strip_leading_docstring_and_imports,
            _strip_using_tree,
        )
        from geno.runtime_prelude import RUNTIME_PRELUDE

        def full_parse_strip(source: str) -> str:
            try:
                tree = ast_mod.parse(source)
            except SyntaxError:
                return source
            return _strip_using_tree(source, tree)

        cases = {
            "real prelude": RUNTIME_PRELUDE,
            "no docstring": "import os\nimport sys\nx = 1\n",
            "docstring only": '"""doc"""\n',
            "all imports": '"""d"""\nimport os\nimport sys\n',
            "empty": "",
            "no prefix": "x = 1\ny = 2\n",
            "syntax error": "def broken(:\n",
            "multiline import crossing the window": (
                '"""doc"""\n' + "\n" * 150 + "from os import (\n    path,\n)\nx = 1\n"
            ),
            "import block longer than the first window": (
                '"""doc"""\n' + "import os\n" * 300 + "x = 1\n"
            ),
        }
        for name, source in cases.items():
            assert _strip_leading_docstring_and_imports(source) == full_parse_strip(
                source
            ), name

    def test_stripped_runtime_prelude_has_no_dangerous_dunder_classes(self):
        """The compiler-owned runtime prelude must not define attribute-bypass
        dunders (custom __getattribute__ etc.) in any class body.

        Defense-in-depth invariant: the sandbox worker's class-dunder check
        runs over the whole executed program including this prelude, so a
        violation here would make every compiled run fail. Keeping the
        prelude clean is also what lets that check stay a hard guard rather
        than something the prelude would need an exemption from.
        """
        import ast

        from geno.compiler import _stripped_runtime_prelude
        from geno.sandbox import _DANGEROUS_DUNDERS

        tree = ast.parse(_stripped_runtime_prelude())
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for child in ast.walk(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if child.name in _DANGEROUS_DUNDERS:
                        offenders.append(f"{node.name}.{child.name}")
                elif isinstance(child, ast.Assign):
                    for target in child.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id in _DANGEROUS_DUNDERS
                        ):
                            offenders.append(f"{node.name}.{target.id}")
                elif isinstance(child, ast.AnnAssign) and isinstance(
                    child.target, ast.Name
                ):
                    if child.target.id in _DANGEROUS_DUNDERS:
                        offenders.append(f"{node.name}.{child.target.id}")
        assert offenders == []

    def test_trusted_runtime_prelude_line_count_uses_structural_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Marker-looking text inside the prelude does not move the boundary."""
        fake_prelude = (
            'marker_text = """\n'
            "# Generated Code Follows\n"
            '"""\n'
            "helper = 1\n"
            "# Generated Code Follows\n"
        )
        body = "def main():\n    return helper\n"
        monkeypatch.setattr(compiler_module, "RUNTIME_PRELUDE", fake_prelude)

        python_code = _strip_runtime_prelude_imports(fake_prelude + body)
        trusted_line_count = _trusted_runtime_prelude_line_count(python_code)
        lines = python_code.splitlines()

        assert trusted_line_count == len(fake_prelude.splitlines())
        assert lines[trusted_line_count - 1].strip() == "# Generated Code Follows"
        assert lines[trusted_line_count].startswith("def main")

    def test_trusted_runtime_prelude_line_count_fails_closed_without_prefix(self):
        """Arbitrary marker text outside the compiler prelude is not trusted."""
        python_code = (
            'x = """\n'
            "# Generated Code Follows\n"
            '"""\n'
            "actual = 1\n"
            "# Generated Code Follows\n"
            "body = 2\n"
        )

        assert _trusted_runtime_prelude_line_count(python_code) == 0

    def test_compiled_runtime_capability_grants_precede_generated_body(self):
        """Embedded compiled execution grants caps before generated program code."""
        source = """
        func main() -> String
            return clock_format(timestamp: 0.0, fmt: "%Y")
        end func
        """
        python_code = _strip_runtime_prelude_imports(compile_to_python(source))

        python_code = _insert_compiled_runtime_capability_assignment(
            python_code, {"clock"}
        )
        trusted_line_count = _trusted_runtime_prelude_line_count(python_code)
        lines = python_code.splitlines()
        cap_line = next(
            idx
            for idx, line in enumerate(lines, 1)
            if line.strip() == "_GENO_CAPS = {'clock',}"
        )
        main_line = next(
            idx for idx, line in enumerate(lines, 1) if line.startswith("def main")
        )

        assert trusted_line_count < cap_line < main_line

    def test_compile_and_exec_process_sandbox_grants_caps_before_main(self):
        """Process-sandbox compiled execution still honors explicit caps."""
        source = """
        func main() -> String
            return clock_format(timestamp: 0.0, fmt: "%Y")
        end func
        """

        result = compile_and_exec(source, timeout=5.0, capabilities={"clock"})

        assert result["__result__"] == "1970"

    def test_compile_float_return(self):
        """Compile and run returning a float."""
        source = """
        func main() -> Float
            return 3.14
        end func
        """
        result = compile_and_run(source)
        assert abs(result - 3.14) < 0.001

    def test_compile_string_return(self):
        """Compile and run returning a string."""
        source = """
        func main() -> String
            return "hello"
        end func
        """
        assert compile_and_run(source) == "hello"

    def test_compile_bool_return(self):
        """Compile and run returning a boolean."""
        source = """
        func main() -> Bool
            return true
        end func
        """
        assert compile_and_run(source) is True

    def test_compile_list_return(self):
        """Compile and run returning a list."""
        source = """
        func main() -> List[Int]
            return [1, 2, 3]
        end func
        """
        assert compile_and_run(source) == [1, 2, 3]

    def test_compile_result_annotation_preserves_type_parameters(self):
        """Compiled Result annotations should keep Ok/Err generic parameters."""
        source = """
        func safe_div(a: Int, b: Int) -> Result[Int, String]
            example 4, 2 -> Ok(2)
            if b == 0 then
                return Err("zero")
            end if
            return Ok(a / b)
        end func
        """
        python_code = compile_to_python(source)

        assert (
            "def safe_div(a: 'int', b: 'int') -> 'Union[Ok[int], Err[str]]':"
            in python_code
        )


class TestCompilerArithmetic:
    """Arithmetic operation compilation tests."""

    def test_addition(self):
        """Compile addition."""
        source = """
        func main() -> Int
            return 3 + 4
        end func
        """
        assert compile_and_run(source) == 7

    def test_subtraction(self):
        """Compile subtraction."""
        source = """
        func main() -> Int
            return 10 - 3
        end func
        """
        assert compile_and_run(source) == 7

    def test_multiplication(self):
        """Compile multiplication."""
        source = """
        func main() -> Int
            return 6 * 7
        end func
        """
        assert compile_and_run(source) == 42

    def test_division_integers(self):
        """Compile integer division."""
        source = """
        func main() -> Int
            return 10 / 3
        end func
        """
        assert compile_and_run(source) == 3

    def test_division_floats(self):
        """Compile float division."""
        source = """
        func main() -> Float
            return 10.0 / 4.0
        end func
        """
        assert compile_and_run(source) == 2.5

    def test_modulo(self):
        """Compile modulo."""
        source = """
        func main() -> Int
            return 17 % 5
        end func
        """
        assert compile_and_run(source) == 2

    def test_division_negative_operands(self):
        """Compile integer division with truncation-toward-zero semantics."""
        source = """
        func main() -> List[Int]
            return [(0 - 7) / 2, 7 / (0 - 2), (0 - 7) / (0 - 2)]
        end func
        """
        assert compile_and_run(source) == [-3, -3, 3]

    def test_modulo_negative_operands(self):
        """Compile modulo with truncation-paired remainder semantics."""
        source = """
        func main() -> List[Int]
            return [(0 - 7) % 3, 7 % (0 - 3), (0 - 7) % (0 - 3)]
        end func
        """
        assert compile_and_run(source) == [-1, 1, -1]

    def test_modulo_negative_float_operand(self):
        """Compile float modulo with backend-parity semantics."""
        source = """
        func main() -> Float
            return (0.0 - 7.5) % 3.0
        end func
        """
        assert compile_and_run(source) == pytest.approx(-1.5)

    def test_divide_builtin_negative_operands(self):
        """Compiled divide() builtin should truncate toward zero."""
        source = """
        func main() -> List[Int]
            return [divide(0 - 7, 2), divide(7, 0 - 2)]
        end func
        """
        assert compile_and_run(source) == [-3, -3]

    def test_operator_precedence(self):
        """Compile with correct operator precedence."""
        source = """
        func main() -> Int
            return 2 + 3 * 4
        end func
        """
        assert compile_and_run(source) == 14

    def test_unary_negation(self):
        """Compile unary negation."""
        source = """
        func main() -> Int
            return -5
        end func
        """
        assert compile_and_run(source) == -5


class TestCompilerComparison:
    """Comparison operation compilation tests."""

    def test_equality(self):
        """Compile equality comparison."""
        source = """
        func main() -> Bool
            return 5 == 5
        end func
        """
        assert compile_and_run(source) is True

    def test_inequality(self):
        """Compile inequality comparison."""
        source = """
        func main() -> Bool
            return 5 != 3
        end func
        """
        assert compile_and_run(source) is True

    def test_less_than(self):
        """Compile less than comparison."""
        source = """
        func main() -> Bool
            return 3 < 5
        end func
        """
        assert compile_and_run(source) is True

    def test_greater_than(self):
        """Compile greater than comparison."""
        source = """
        func main() -> Bool
            return 5 > 3
        end func
        """
        assert compile_and_run(source) is True

    def test_less_or_equal(self):
        """Compile less than or equal comparison."""
        source = """
        func main() -> Bool
            return 5 <= 5
        end func
        """
        assert compile_and_run(source) is True

    def test_greater_or_equal(self):
        """Compile greater than or equal comparison."""
        source = """
        func main() -> Bool
            return 5 >= 3
        end func
        """
        assert compile_and_run(source) is True


class TestCompilerLogical:
    """Logical operation compilation tests."""

    def test_and(self):
        """Compile logical and."""
        source = """
        func main() -> Bool
            return true and true
        end func
        """
        assert compile_and_run(source) is True

    def test_or(self):
        """Compile logical or."""
        source = """
        func main() -> Bool
            return false or true
        end func
        """
        assert compile_and_run(source) is True

    def test_not(self):
        """Compile logical not."""
        source = """
        func main() -> Bool
            return not false
        end func
        """
        assert compile_and_run(source) is True


class TestCompilerVariables:
    """Variable binding compilation tests."""

    def test_let_binding(self):
        """Compile let binding."""
        source = """
        func main() -> Int
            let x: Int = 5
            return x
        end func
        """
        assert compile_and_run(source) == 5

    def test_var_binding(self):
        """Compile var binding with assignment."""
        source = """
        func main() -> Int
            var x: Int = 5
            x = 10
            return x
        end func
        """
        assert compile_and_run(source) == 10

    def test_multiple_bindings(self):
        """Compile multiple bindings."""
        source = """
        func main() -> Int
            let a: Int = 1
            let b: Int = 2
            let c: Int = 3
            return a + b + c
        end func
        """
        assert compile_and_run(source) == 6


class TestCompilerCopySemantics:
    """Test that let/var produce independent copies of collections."""

    def test_var_list_copies(self):
        """var binding of a List should produce an independent copy."""
        source = """
        func main() -> Int
            let xs: List[Int] = [1, 2, 3]
            var ys: List[Int] = xs
            ys = set_at(list: ys, index: 0, value: 99)
            return xs[0]
        end func
        """
        assert compile_and_run(source) == 1


class TestCompilerControlFlow:
    """Control flow compilation tests."""

    def test_if_true(self):
        """Compile if statement with true condition."""
        source = """
        func main() -> Int
            if true then
                return 1
            else
                return 0
            end if
        end func
        """
        assert compile_and_run(source) == 1

    def test_if_false(self):
        """Compile if statement with false condition."""
        source = """
        func main() -> Int
            if false then
                return 1
            else
                return 0
            end if
        end func
        """
        assert compile_and_run(source) == 0

    def test_while_loop(self):
        """Compile while loop."""
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
        assert compile_and_run(source) == 15

    def test_for_loop(self):
        """Compile for loop."""
        source = """
        func main() -> Int
            var sum: Int = 0
            for x: Int in [1, 2, 3, 4, 5] do
                sum = sum + x
            end for
            return sum
        end func
        """
        assert compile_and_run(source) == 15


class TestCompilerFunctions:
    """Function compilation tests."""

    def test_simple_function(self):
        """Compile simple function call."""
        source = """
        func double(x: Int) -> Int
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            return double(21)
        end func
        """
        assert compile_and_run(source) == 42

    def test_recursive_function(self):
        """Compile recursive function."""
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
        assert compile_and_run(source) == 120

    def test_higher_order_function(self):
        """Compile higher-order function."""
        source = """
        func apply(f: (Int) -> Int, x: Int) -> Int
            example fn(y: Int) -> y, 5 -> 5
            return f(x)
        end func

        func main() -> Int
            return apply(fn(x: Int) -> x * 2, 21)
        end func
        """
        assert compile_and_run(source) == 42


class TestCompilerLambdas:
    """Lambda expression compilation tests."""

    def test_simple_lambda(self):
        """Compile simple lambda."""
        source = """
        func main() -> Int
            let f: (Int) -> Int = fn(x: Int) -> x * 2
            return f(21)
        end func
        """
        assert compile_and_run(source) == 42

    def test_lambda_in_map(self):
        """Compile lambda used in map."""
        source = """
        func main() -> List[Int]
            return map([1, 2, 3], fn(x: Int) -> x * x)
        end func
        """
        assert compile_and_run(source) == [1, 4, 9]

    def test_lambda_closure(self):
        """Compile lambda with closure."""
        source = """
        func main() -> Int
            let multiplier: Int = 10
            let f: (Int) -> Int = fn(x: Int) -> x * multiplier
            return f(4)
        end func
        """
        assert compile_and_run(source) == 40

    def test_for_loop_lambda_captures_each_iteration_value(self):
        """Closures created in a for loop should keep the iteration value."""
        source = """
        func main() -> Int
            var fs: List[() -> Int] = []
            for x: Int in [1, 2, 3] do
                fs = append(fs, fn() -> x)
            end for
            return fs[0]() * 100 + fs[1]() * 10 + fs[2]()
        end func
        """
        assert compile_and_run(source) == 123

    def test_for_loop_lambda_captures_iteration_value_inside_call_args(self):
        """Loop capture should also work when the variable is nested in call args."""
        source = """
        func identity(x: Int) -> Int
            example 1 -> 1
            return x
        end func

        func main() -> Int
            var fs: List[() -> Int] = []
            for x: Int in [1, 2, 3] do
                fs = append(fs, fn() -> identity(x))
            end for
            return fs[0]() * 100 + fs[1]() * 10 + fs[2]()
        end func
        """
        assert compile_and_run(source) == 123


class TestCompilerPatternMatching:
    """Pattern matching compilation tests."""

    def test_match_some(self):
        """Compile match on Some constructor."""
        source = """
        func unwrap(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func

        func main() -> Int
            return unwrap(Some(42))
        end func
        """
        assert compile_and_run(source) == 42

    def test_match_none(self):
        """Compile match on None constructor."""
        source = """
        func unwrap(opt: Option[Int]) -> Int
            example None -> 0
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func

        func main() -> Int
            return unwrap(None)
        end func
        """
        assert compile_and_run(source) == 0

    def test_match_literal(self):
        """Compile match on literal pattern."""
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
        assert compile_and_run(source) == "zero"

    def test_match_wildcard(self):
        """Compile match with wildcard pattern."""
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
        assert compile_and_run(source) == "other"


class TestCompilerPipeline:
    """Pipeline expression compilation tests."""

    def test_simple_pipeline(self):
        """Compile simple pipeline."""
        source = """
        func main() -> Int
            return [1, 2, 3, 4, 5] |> length
        end func
        """
        assert compile_and_run(source) == 5

    def test_pipeline_with_placeholder(self):
        """Compile pipeline with placeholder."""
        source = """
        func main() -> List[Int]
            return [1, 2, 3, 4, 5] |> filter(_, fn(x: Int) -> x > 2)
        end func
        """
        assert compile_and_run(source) == [3, 4, 5]

    def test_chained_pipeline(self):
        """Compile chained pipeline."""
        source = """
        func main() -> Int
            return [1, 2, 3, 4, 5]
                |> filter(_, fn(x: Int) -> x > 2)
                |> length
        end func
        """
        assert compile_and_run(source) == 3

    def test_pipeline_placeholder_materializes_piped_value_once(self):
        """Placeholder stages must not re-evaluate the piped expression."""
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
        assert compile_and_run(source) == 2

    def test_pipeline_placeholder_preserves_argument_order(self):
        """Non-placeholder arguments still evaluate left-to-right after the pipe."""
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
        assert compile_and_run(source) == 21


class TestCompilerBuiltins:
    """Built-in function compilation tests."""

    def test_length(self):
        """Compile length function."""
        source = """
        func main() -> Int
            return length([1, 2, 3])
        end func
        """
        assert compile_and_run(source) == 3

    def test_typed_length_emits_len_fast_path(self):
        """Typed length should inline raw len() for sized values: a
        materialized container's length is bounded by memory, far below the
        JS safe-integer range and any integer-bits limit."""
        source = compile_to_python(
            """
            func main() -> Int
                return length("hello")
            end func
            """
        )
        assert "return length(" not in source
        body = source[source.find("def main") :]
        assert "len(" in body

    def test_head(self):
        """Compile head function."""
        source = """
        func main() -> Int
            return head([1, 2, 3])
        end func
        """
        assert compile_and_run(source) == 1

    def test_tail(self):
        """Compile tail function."""
        source = """
        func main() -> List[Int]
            return tail([1, 2, 3])
        end func
        """
        assert compile_and_run(source) == [2, 3]

    def test_append(self):
        """Compile append function."""
        source = """
        func main() -> List[Int]
            return append([1, 2], 3)
        end func
        """
        assert compile_and_run(source) == [1, 2, 3]

    def test_typed_append_emits_inline_fast_path(self):
        """Typed append should inline the hot path instead of calling append()."""
        source = compile_to_python(
            """
            func main() -> List[Int]
                return append([1, 2], 3)
            end func
            """
        )
        assert "append(" not in source.split("def append")[0]
        assert "_list_size_exceeded(" in source

    def test_typed_append_preserves_left_to_right_evaluation(self):
        """Typed append must evaluate list before item, matching call semantics."""
        source = """
        func make_list(flag: Bool) -> List[Int]
            example (false) -> [1]
            if flag then
                throw "list"
            end if
            return [1]
        end func

        func make_item(flag: Bool) -> Int
            example (false) -> 1
            if flag then
                throw "item"
            end if
            return 1
        end func

        func main() -> String
            try
                let xs: List[Int] = append(make_list(true), make_item(true))
                return "none"
            catch e: String
                return e
            end try
        end func
        """
        assert compile_and_run(source) == "list"

    def test_typed_append_allows_falsey_item(self):
        """Typed append fast path must preserve falsey items like 0."""
        source = """
        func main() -> List[Int]
            return append([1, 2], 0)
        end func
        """
        assert compile_and_run(source) == [1, 2, 0]

    def test_concat(self):
        """Compile concat function."""
        source = """
        func main() -> List[Int]
            return concat([1, 2], [3, 4])
        end func
        """
        assert compile_and_run(source) == [1, 2, 3, 4]

    def test_set_at(self):
        """Compile set_at function."""
        source = """
        func main() -> List[Int]
            return set_at(list: [1, 2, 3], index: 1, value: 9)
        end func
        """
        assert compile_and_run(source) == [1, 9, 3]

    def test_to_chars(self):
        """Compile to_chars function."""
        source = """
        func main() -> List[String]
            return to_chars("abc")
        end func
        """
        assert compile_and_run(source) == ["a", "b", "c"]

    def test_typed_string_char_at_emits_inline_fast_path(self):
        """Typed string_char_at should inline guarded indexing."""
        source = compile_to_python(
            """
            func main() -> String
                return string_char_at(text: "hello", index: 1)
            end func
            """
        )
        assert "string_char_at(" not in source.split("def string_char_at")[0]

    def test_typed_string_char_at_preserves_left_to_right_evaluation(self):
        """Typed string_char_at must evaluate text before index."""
        source = """
        func make_text(flag: Bool) -> String
            example (false) -> "ok"
            if flag then
                throw "text"
            end if
            return "ok"
        end func

        func make_index(flag: Bool) -> Int
            example (false) -> 0
            if flag then
                throw "index"
            end if
            return 0
        end func

        func main() -> String
            try
                let c: String = string_char_at(text: make_text(true), index: make_index(true))
                return "none"
            catch e: String
                return e
            end try
        end func
        """
        assert compile_and_run(source) == "text"

    def test_typed_substring_emits_inline_fast_path(self):
        """Typed substring should inline raw slicing with clamped bounds."""
        source = compile_to_python(
            """
            func main() -> String
                return substring(text: "hello", start: 1, stop: 4)
            end func
            """
        )
        assert "substring(" not in source.split("def substring")[0]

    def test_typed_substring_preserves_empty_result(self):
        """Typed substring fast path must still return empty string correctly."""
        source = """
        func main() -> String
            return substring(text: "hello", start: 2, stop: 2)
        end func
        """
        assert compile_and_run(source) == ""

    def test_typed_starts_with_emits_inline_fast_path(self):
        """Typed starts_with should inline Python's native string helper."""
        source = compile_to_python(
            """
            func main() -> Bool
                return starts_with(text: "hello", prefix: "he")
            end func
            """
        )
        assert "starts_with(" not in source.split("def starts_with")[0]
        assert ".startswith(" in source
        assert '("he" if 2 <= _MAX_COLLECTION_SIZE' in source

    def test_typed_starts_with_preserves_left_to_right_evaluation(self):
        """Typed starts_with must evaluate text before prefix."""
        source = """
        func make_text(flag: Bool) -> String
            example (false) -> "hello"
            if flag then
                throw "text"
            end if
            return "hello"
        end func

        func make_prefix(flag: Bool) -> String
            example (false) -> "h"
            if flag then
                throw "prefix"
            end if
            return "h"
        end func

        func main() -> String
            try
                let result: Bool = starts_with(text: make_text(true), prefix: make_prefix(true))
                return "none"
            catch e: String
                return e
            end try
        end func
        """
        assert compile_and_run(source) == "text"

    def test_typed_starts_with_allows_empty_prefix(self):
        """Typed starts_with fast path must preserve empty-prefix truthiness."""
        source = """
        func main() -> Bool
            return starts_with(text: "hello", prefix: "")
        end func
        """
        assert compile_and_run(source) is True

    def test_typed_ends_with_emits_inline_fast_path(self):
        """Typed ends_with should inline Python's native string helper."""
        source = compile_to_python(
            """
            func main() -> Bool
                return ends_with(text: "hello", suffix: "lo")
            end func
            """
        )
        assert "ends_with(" not in source.split("def ends_with")[0]
        assert ".endswith(" in source
        assert '("lo" if 2 <= _MAX_COLLECTION_SIZE' in source

    def test_typed_ends_with_allows_empty_suffix(self):
        """Typed ends_with fast path must preserve empty-suffix truthiness."""
        source = """
        func main() -> Bool
            return ends_with(text: "hello", suffix: "")
        end func
        """
        assert compile_and_run(source) is True

    def test_filter(self):
        """Compile filter function."""
        source = """
        func main() -> List[Int]
            return filter([1, 2, 3, 4], fn(x: Int) -> x % 2 == 0)
        end func
        """
        assert compile_and_run(source) == [2, 4]

    def test_map_function(self):
        """Compile map function."""
        source = """
        func main() -> List[Int]
            return map([1, 2, 3], fn(x: Int) -> x * 2)
        end func
        """
        assert compile_and_run(source) == [2, 4, 6]

    def test_fold(self):
        """Compile fold function."""
        source = """
        func main() -> Int
            return fold(list: [1, 2, 3, 4], initial: 0, reducer: fn(acc: Int, x: Int) -> acc + x)
        end func
        """
        assert compile_and_run(source) == 10


class TestCompilerUserTypes:
    """User-defined type compilation tests."""

    def test_custom_type_generates_code(self):
        """Compile custom algebraic data type generates valid class."""
        source = """
        type Color = Red | Green | Blue

        func main() -> Int
            return 42
        end func
        """
        # Just verify it compiles and runs
        assert compile_and_run(source) == 42

    def test_custom_type_with_fields(self):
        """Custom type with fields generates correct class."""
        source = """
        type Point = MkPoint(x: Int, y: Int)

        func main() -> Int
            return 42
        end func
        """
        python_code = compile_to_python(source)
        assert "class MkPoint" in python_code
        assert "x: 'int'" in python_code
        assert "y: 'int'" in python_code


class TestForwardReferenceQuoting:
    """Verify all annotation sites use string-quoting for forward references."""

    def test_let_annotation_is_quoted(self):
        """Let statement annotations must be string-quoted."""
        source = """
        type Color = Red | Blue

        func main() -> Int
            let c: Color = Red()
            match c with
                | Red -> return 1
                | Blue -> return 2
            end match
        end func
        """
        python_code = compile_to_python(source)
        # The let annotation should be quoted like ADT fields and function sigs
        assert "c: 'Color'" in python_code or "c: 'Red | Blue'" in python_code

    def test_var_annotation_is_quoted(self):
        """Var statement annotations must be string-quoted."""
        source = """
        type Shape = Circle(r: Int) | Square(s: Int)

        func main() -> Int
            var shape: Shape = Circle(5)
            shape = Square(3)
            match shape with
                | Circle(r) -> return r
                | Square(s) -> return s
            end match
        end func
        """
        python_code = compile_to_python(source)
        assert "shape: 'Shape'" in python_code or "shape: 'Circle'" in python_code

    def test_let_with_generic_type_is_quoted(self):
        """Let with generic type (List, Option) must be quoted."""
        source = """
        type Item = Item(name: String)

        func main() -> Int
            let items: List[Item] = [Item("a")]
            return length(items)
        end func
        """
        python_code = compile_to_python(source)
        assert "'list[Item]'" in python_code

    def test_function_defined_before_type_runs(self):
        """Function using a type defined later in source order must work."""
        source = """
        func make_color() -> Color
            example () -> Red()
            return Red()
        end func

        type Color = Red | Blue

        func main() -> Int
            let c: Color = make_color()
            match c with
                | Red -> return 1
                | Blue -> return 2
            end match
        end func
        """
        assert compile_and_run(source) == 1

    def test_recursive_adt_let_binding(self):
        """Recursive ADT used in let binding compiles and runs."""
        source = """
        type Tree = Leaf(val: Int) | Node(left: Tree, right: Tree)

        func tree_sum(t: Tree) -> Int
            example Leaf(5) -> 5
            match t with
                | Leaf(v) -> return v
                | Node(l, r) -> return tree_sum(l) + tree_sum(r)
            end match
        end func

        func main() -> Int
            let tree: Tree = Node(Node(Leaf(1), Leaf(2)), Leaf(3))
            return tree_sum(tree)
        end func
        """
        assert compile_and_run(source) == 6

    def test_function_param_annotations_quoted(self):
        """Function parameter annotations must be string-quoted."""
        source = """
        type Pair = MkPair(a: Int, b: Int)

        func sum_pair(p: Pair) -> Int
            example MkPair(1, 2) -> 3
            match p with
                | MkPair(a, b) -> return a + b
            end match
        end func

        func main() -> Int
            return sum_pair(MkPair(3, 4))
        end func
        """
        python_code = compile_to_python(source)
        assert "p: 'Pair'" in python_code

    def test_return_type_annotation_quoted(self):
        """Function return type annotations must be string-quoted."""
        source = """
        type Wrapper = Wrap(val: Int)

        func make_wrapper(x: Int) -> Wrapper
            example 5 -> Wrap(5)
            return Wrap(x)
        end func

        func main() -> Int
            match make_wrapper(42) with
                | Wrap(v) -> return v
            end match
        end func
        """
        python_code = compile_to_python(source)
        assert "-> 'Wrapper'" in python_code


class TestCompilerNamedArgs:
    """Named argument compilation tests."""

    def test_named_args_user_function(self):
        """Compile function call with named arguments."""
        source = """
        func greet(name: String, greeting: String) -> String
            example "Alice", "Hi" -> "Hi"
            return greeting
        end func

        func main() -> String
            return greet(greeting: "Hello", name: "World")
        end func
        """
        assert compile_and_run(source) == "Hello"

    def test_named_args_builtin(self):
        """Compile built-in function call with named arguments."""
        source = """
        func main() -> List[Int]
            return append(element: 4, list: [1, 2, 3])
        end func
        """
        assert compile_and_run(source) == [1, 2, 3, 4]


class TestCompilerTuples:
    """Tuple expression compilation tests."""

    def test_unit_tuple(self):
        """Compile unit tuple (empty tuple)."""
        source = """
        func main() -> Unit
            return ()
        end func
        """
        assert compile_and_run(source) is None

    def test_list_pair_builtins_return_tuples(self):
        """list_zip/list_enumerate results should flow into tuple destructuring."""
        source = """
        func main() -> Int
            let zipped: List[(Int, Int)] = list_zip([1], [2])
            let numbered: List[(Int, String)] = list_enumerate(["hi"])
            let (left, right): (Int, Int) = zipped[0]
            let (idx, text): (Int, String) = numbered[0]
            return left + right + idx + length(text)
        end func
        """
        assert compile_and_run(source) == 5

    def test_list_find_no_match_returns_none_in_compiled_python(self):
        """Compiled Python list_find should return None_ on a miss."""
        source = """
        func always_false(x: Int) -> Bool
            example 1 -> false
            return false
        end func

        func main() -> String
            example () -> "None"
            let result: Option[Int] = list_find([1, 2, 3], always_false)
            match result with
                | None -> return "None"
                | Some(v) -> return "Some"
            end match
        end func
        """
        assert compile_and_run(source) == "None"


class TestCompilerSpecs:
    """Specification clause compilation tests."""

    def test_spec_docstring(self):
        """Verify specs are compiled to docstrings."""
        source = """
        func add(a: Int, b: Int) -> Int
            requires a >= 0
            requires b >= 0
            ensures result >= 0
            example 1, 2 -> 3
            return a + b
        end func
        """
        python_code = compile_to_python(source)
        assert "Requires:" in python_code
        assert "Ensures:" in python_code
        assert "Example:" in python_code

    def test_requires_runtime_check(self):
        """Verify requires clauses generate runtime checks."""
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
        python_code = compile_to_python(source)
        assert 'raise _GenoContractViolation("Precondition failed' in python_code

    def test_ensures_runtime_check(self):
        """Verify ensures clauses generate runtime checks."""
        source = """
        func double(x: Int) -> Int
            ensures result > x
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            return double(5)
        end func
        """
        python_code = compile_to_python(source)
        assert "raise _GenoContractViolation" in python_code
        assert "Postcondition failed" in python_code

    def test_ensures_rejects_result_parameter_collision_before_compile(self):
        source = """
        func f(result: Int) -> Int
            ensures result == 2
            example 1 -> 2
            return result + 1
        end func
        """
        with pytest.raises(GenoTypeError, match="`result` is reserved"):
            compile_to_python(source)

    def test_requires_passes_at_runtime(self):
        """Compiled requires check passes when condition met."""
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
        assert compile_and_run(source) == 5

    def test_requires_fails_at_runtime(self):
        """Compiled requires check fails when condition not met."""
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
        import pytest

        with pytest.raises(RuntimeError) as exc_info:
            compile_and_run(source)
        assert "Precondition failed" in str(exc_info.value)

    def test_ensures_passes_at_runtime(self):
        """Compiled ensures check passes when condition met."""
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
        assert compile_and_run(source) == 10

    def test_ensures_fails_at_runtime(self):
        """Compiled ensures check fails when condition not met."""
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
        import pytest

        with pytest.raises(RuntimeError) as exc_info:
            compile_and_run(source)
        assert "Postcondition failed" in str(exc_info.value)


class TestContractConstantFolding:
    """Constant-fold trivial requires/ensures clauses."""

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
        python_code = compile_to_python(source)
        assert "Precondition failed" not in python_code
        assert compile_and_run(source) == 1

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
        python_code = compile_to_python(source)
        assert "Postcondition failed" not in python_code
        assert "_body_f" not in python_code
        assert compile_and_run(source) == 1

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
        with pytest.raises(CompileError, match="requires false"):
            compile_to_python(source)

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
        with pytest.raises(CompileError, match="ensures false"):
            compile_to_python(source)

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
        python_code = compile_to_python(source)
        assert "Precondition failed" in python_code


class TestCompilerRuntimePrelude:
    """Runtime prelude tests."""

    def test_safe_div_in_prelude(self):
        """Verify _safe_div is in prelude."""
        source = """
        func main() -> Int
            return 42
        end func
        """
        python_code = compile_to_python(source)
        assert "_safe_div" in python_code

    def test_constructor_classes(self):
        """Verify constructor classes are in prelude."""
        source = """
        func main() -> Int
            return 42
        end func
        """
        python_code = compile_to_python(source)
        assert "class Some" in python_code
        assert "class _None" in python_code
        assert "class Ok" in python_code
        assert "class Err" in python_code


class TestCompilerStringEscaping:
    """Test that the compiler correctly escapes special characters in strings."""

    def test_string_with_carriage_return(self):
        """Strings with \\r must round-trip through the compiler."""
        source = 'func main() -> String\n    return "hello\\rworld"\nend func\n'
        assert compile_and_run(source) == "hello\rworld"

    def test_string_with_tab(self):
        """Strings with \\t must round-trip through the compiler."""
        source = 'func main() -> String\n    return "col1\\tcol2"\nend func\n'
        assert compile_and_run(source) == "col1\tcol2"

    def test_string_with_newline(self):
        """Strings with \\n must round-trip through the compiler."""
        source = 'func main() -> String\n    return "line1\\nline2"\nend func\n'
        assert compile_and_run(source) == "line1\nline2"

    def test_string_with_mixed_escapes(self):
        """Strings with mixed escape sequences must round-trip."""
        source = 'func main() -> String\n    return "a\\rb\\nc\\td"\nend func\n'
        assert compile_and_run(source) == "a\rb\nc\td"

    def test_compiled_string_with_cr_is_valid_python(self):
        """Compiled output with \\r must be valid Python source."""
        source = 'func main() -> String\n    return "has\\rcr"\nend func\n'
        python_code = compile_to_python(source)
        # Must compile without SyntaxError
        compile(python_code, "<test>", "exec")


class TestCompilerPatternMatchFieldSafety:
    """Test that pattern matching uses safe field access."""

    def test_pattern_match_uses_get_field(self):
        """Pattern matching in compiled code must use get_field(), not direct attr access."""
        source = """
        type Wrapper = Wrap(value: Int)

        func unwrap(w: Wrapper) -> Int
            example Wrap(5) -> 5
            match w with
                | Wrap(x) -> return x
            end match
        end func

        func main() -> Int
            return unwrap(Wrap(42))
        end func
        """
        python_code = compile_to_python(source)
        # Verify get_field is used instead of direct attribute access
        assert "get_field(" in python_code
        # Should NOT have direct .value access in pattern matching
        # (there may be .value in dataclass definition, that's fine)
        result = compile_and_run(source)
        assert result == 42

    def test_pattern_match_blocks_dunder_field(self):
        """Fields starting with _ must be blocked by get_field at runtime."""
        source = """
        type Bad = Bad(_secret: Int)

        func leak(b: Bad) -> Int
            example Bad(42) -> 42
            match b with
                | Bad(x) -> return x
            end match
        end func

        func main() -> Int
            example () -> 42
            return leak(Bad(42))
        end func
        """
        python_code = compile_to_python(source)
        # The compiled code should use get_field which blocks _-prefixed access
        assert "get_field(" in python_code
        with pytest.raises(RuntimeError, match="not allowed"):
            compile_and_run(source)


class TestCompilerReservedNameProtection:
    """Test that user code cannot shadow security-critical prelude names."""

    def test_function_named_get_field_rejected(self):
        """A function named get_field would shadow the field access safety check."""
        source = """
        func get_field(x: Int) -> Int
            example 5 -> 5
            return x
        end func
        """
        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

    @pytest.mark.parametrize("name", ["_require_cap", "_validate_regex_pattern"])
    def test_all_runtime_prelude_functions_are_reserved(self, name):
        source = f"""
        func {name}(value: String, context: String) -> Unit
            example "x", "y" -> ()
            return ()
        end func
        """
        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

    def test_runtime_name_rejected_as_import_alias(self):
        source = """
        import Utils as Constructor
        func main() -> Int
            return 1
        end func
        """
        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

    def test_runtime_name_rejected_as_trait_dispatcher(self):
        source = """
        trait Unsafe
            func _require_cap(self: Self) -> Unit
        end trait
        """
        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

    def test_function_named_safe_div_rejected(self):
        """A function named _safe_div would shadow safe division."""
        source = """
        func _safe_div(a: Int, b: Int) -> Int
            example 10, 2 -> 0
            return 0
        end func
        """
        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

    def test_function_named_safe_index_set_rejected(self):
        """A function named _safe_index_set would shadow index assignment safety."""
        source = """
        func _safe_index_set(xs: Array[Int], index: Int, value: Int) -> Unit
            example array_new(1, 0), 0, 7 -> ()
            return ()
        end func
        """
        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

    @pytest.mark.parametrize(
        "name",
        [
            "_runtime_codecs",
            "_runtime_posixpath",
            "_runtime_random",
            "_runtime_time",
        ],
    )
    def test_function_named_runtime_module_alias_rejected(self, name: str):
        """User functions cannot replace modules injected into the sandbox."""
        source = f"""
        func {name}(x: Int) -> Int
            example 5 -> 5
            return x
        end func
        """
        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

    def test_type_named_constructor_rejected(self):
        """A type named Constructor would shadow the base class."""
        source = """
        type Constructor = Foo(x: Int)
        """
        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

    def test_variant_named_some_rejected(self):
        """A variant named Some would shadow the Option constructor."""
        source = """
        type MyOpt = Some(x: Int) | MyNone
        """
        with pytest.raises(Exception, match="Constructor 'Some' is already defined"):
            compile_to_python(source)

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
                let _check_collection_size: Int = 1
                return _check_collection_size
            end func
            """,
            """
            func main() -> Int
                var get_field: Int = 1
                return get_field
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
            func main() -> String
                try
                    throw "boom"
                catch _safe_add: String
                    return _safe_add
                end try
                return "ok"
            end func
            """,
            """
            func main() -> Int
                let (_safe_add, other): (Int, Int) = (1, 2)
                return other
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
                return [_safe_add for _safe_add: Int in [1, 2]]
            end func
            """,
        ],
    )
    def test_reserved_names_rejected_in_local_scopes(self, source):
        """Runtime helper names cannot be rebound in nested user scopes."""
        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

    @pytest.mark.parametrize(
        "name",
        sorted(compiler_module._PYTHON_EMITTED_LOCAL_HELPER_NAMES),
    )
    def test_all_emitted_local_helpers_are_reserved(self, name: str):
        source = f"""
        func main() -> Int
            let {name}: Int = 1
            return {name}
        end func
        """

        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

    @pytest.mark.parametrize("name", sorted(compiler_module._PYTHON_FIXED_GLOBAL_NAMES))
    def test_fixed_generated_globals_are_reserved(self, name: str):
        source = f"""
        func {name}() -> Int
            example () -> 1
            return 1
        end func

        async func main() -> Int
            return {name}()
        end func
        """

        with pytest.raises(CompileError, match="reserved runtime name"):
            compile_to_python(source)

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

        assert compile_and_run(source) == 2

    def test_normal_names_allowed(self):
        """Normal function and type names should compile fine."""
        source = """
        func double(x: Int) -> Int
            example 5 -> 10
            return x * 2
        end func

        func main() -> Int
            example () -> 10
            return double(5)
        end func
        """
        python_code = compile_to_python(source)
        assert "def double" in python_code


class TestCompilerIndexBoundsChecking:
    """Test that compiled code supports negative indexing and rejects out-of-bounds indices."""

    def test_negative_index_supported(self):
        """Negative indices wrap around: arr[-1] is the last element."""
        source = """
        func main() -> Int
            example () -> 0
            let arr: List[Int] = [10, 20, 30]
            return arr[-1]
        end func
        """
        assert compile_and_run(source) == 30

    def test_out_of_bounds_index_rejected(self):
        """Out-of-bounds indices must raise an error."""
        source = """
        func main() -> Int
            example () -> 0
            let arr: List[Int] = [10, 20, 30]
            return arr[5]
        end func
        """
        with pytest.raises(
            (RuntimeError, IndexError),
            match=r"[Ii]ndex.*out of bounds|list index out of range",
        ):
            compile_and_run(source)

    def test_valid_index_works(self):
        """Valid indices should work correctly."""
        source = """
        func main() -> Int
            example () -> 20
            let arr: List[Int] = [10, 20, 30]
            return arr[1]
        end func
        """
        assert compile_and_run(source) == 20

    def test_compiled_uses_safe_index(self):
        """Compiled output must use _safe_index, not direct indexing."""
        source = """
        func main() -> Int
            example () -> 10
            let arr: List[Int] = [10, 20, 30]
            return arr[0]
        end func
        """
        python_code = compile_to_python(source)
        assert "_safe_index(" in python_code

    def test_catch_string_handles_native_index_error(self):
        """catch e: String should catch raw IndexError from fast-path indexing."""
        source = """
        func main() -> String
            try
                let xs: List[Int] = [1]
                let value: Int = xs[5]
                return "nope"
            catch e: String
                return e
            end try
        end func
        """
        message = compile_and_run(source)
        assert isinstance(message, str)
        assert message != "nope"
        assert "range" in message or "Index" in message


class TestCompilerCollectionSizeLimits:
    """Test that compiled code enforces collection size limits."""

    def test_string_doubling_hits_limit(self):
        """Doubling a string in a loop must be stopped by the size limit."""
        source = """
        func main() -> String
            example () -> "x"
            var s: String = "x"
            var i: Int = 0
            while i < 30 do
                s = s + s
                i = i + 1
            end while
            return s
        end func
        """
        with pytest.raises(RuntimeError, match="size exceeds limit"):
            compile_and_run(source)

    def test_concat_builtin_hits_limit(self):
        """The concat runtime function must enforce the limit."""
        source = """
        func grow(lst: List[Int], n: Int) -> List[Int]
            example [1], 0 -> [1]
            if n == 0 then
                return lst
            end if
            return grow(concat(lst, lst), n - 1)
        end func

        func main() -> Int
            example () -> 0
            let big: List[Int] = grow([1], 25)
            return 0
        end func
        """
        with pytest.raises(RuntimeError, match="size exceeds limit"):
            compile_and_run(source)

    def test_small_collections_allowed(self):
        """Normal-sized collections should work fine."""
        source = """
        func main() -> String
            example () -> "hellohello"
            let s: String = "hello"
            return s + s
        end func
        """
        assert compile_and_run(source) == "hellohello"

    def test_compiled_literal_results_honor_process_collection_limit(self):
        """Compiled direct literals must honor process-sandbox collection caps."""
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
            with pytest.raises(RuntimeError, match=message):
                _compiled_python_process_result(source, max_collection_size=2)

    def test_format_runtime_builtin_hits_limit(self):
        """format_ in runtime prelude must enforce collection size limits."""
        env = _compiled_runtime_env(10)

        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["format_"]("{}{}", ["12345", "678901"])  # type: ignore[operator]

    def test_runtime_string_list_helpers_precheck_configured_limit(self):
        """Compiled runtime string/list helpers must precheck the configured cap."""
        env = _compiled_runtime_env(5)
        env["_GENO_CAPS"] = frozenset({"regex"})

        cases = [
            (lambda: env["split"]("a,a,a,a,a,a", ","), "List size exceeds limit"),
            (
                lambda: env["string_split"]("a,a,a,a,a,a", ","),
                "List size exceeds limit",
            ),
            (lambda: env["join"](["abc", "def"], ""), "String size exceeds limit"),
            (
                lambda: env["string_join"](["abc", "def"], ""),
                "String size exceeds limit",
            ),
            (lambda: env["replace"]("aaaa", "a", "bb"), "String size exceeds limit"),
            (
                lambda: env["string_replace"]("aaaa", "a", "bb"),
                "String size exceeds limit",
            ),
            (lambda: env["regex_find_all"]("a", "aaaaaa"), "List size exceeds limit"),
            (
                lambda: env["regex_replace"]("a", "bb", "aaaa"),
                "String size exceeds limit",
            ),
            (lambda: env["string_pad_left"]("x", 6, "0"), "String size exceeds limit"),
            (lambda: env["string_pad_right"]("x", 6, "0"), "String size exceeds limit"),
            (
                lambda: env["flat_map"]([1, 2], lambda x: [x, x, x]),
                "List size exceeds limit",
            ),
            (
                lambda: env["list_flatten"]([[1, 2, 3], [4, 5, 6]]),
                "List size exceeds limit",
            ),
            (
                lambda: env["list_intersperse"]([1, 2, 3, 4], 0),
                "List size exceeds limit",
            ),
            (lambda: env["path_join"]("abc", "def"), "String size exceeds limit"),
            (lambda: env["to_string"]([1, 2]), "String size exceeds limit"),
        ]

        for call, message in cases:
            with pytest.raises(RuntimeError, match=message):
                call()

    def test_runtime_case_helpers_precheck_unicode_expansion(self):
        """Case conversion can expand Unicode strings and must enforce caps."""
        env = _compiled_runtime_env(1)

        cases = (
            lambda: env["to_upper"](chr(223)),  # type: ignore[operator]
            lambda: env["string_to_upper"](chr(223)),  # type: ignore[operator]
            lambda: env["to_lower"](chr(304)),  # type: ignore[operator]
            lambda: env["string_to_lower"](chr(304)),  # type: ignore[operator]
        )

        for call in cases:
            with pytest.raises(RuntimeError, match="String size exceeds limit"):
                call()

    def test_compiled_case_helpers_honor_limit_on_unicode_expansion(self):
        """Compiled case helper calls must reject Unicode-expanded results."""
        sources = (
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

        for source in sources:
            python_code = compile_to_python(source)
            env = {"_GENO_MAX_COLLECTION_SIZE": 1, "__name__": "__test__"}
            exec(python_code, env)
            main = cast(Callable[[], object], env["main"])
            with pytest.raises(RuntimeError, match="String size exceeds limit"):
                main()

    def test_compiled_path_join_honors_collection_limit(self):
        """Compiled path_join calls must reject joined paths over the cap."""
        source = """
        func main() -> String
            return path_join("a", "b")
        end func
        """
        python_code = compile_to_python(source)
        env = {"_GENO_MAX_COLLECTION_SIZE": 2, "__name__": "__test__"}
        exec(python_code, env)
        main = cast(Callable[[], object], env["main"])

        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            main()

    def test_compiled_to_string_honors_collection_limit(self):
        """Compiled to_string calls must reject formatted values over the cap."""
        source = """
        func main() -> String
            return to_string([1, 2])
        end func
        """
        python_code = compile_to_python(source)
        env = {"_GENO_MAX_COLLECTION_SIZE": 2, "__name__": "__test__"}
        exec(python_code, env)
        main = cast(Callable[[], object], env["main"])

        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            main()

    def test_compiled_clock_format_honors_collection_limit(self):
        """Compiled clock formatting must reject expanded results over the cap."""
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
            python_code = compile_to_python(source)
            env = {"_GENO_MAX_COLLECTION_SIZE": 2, "__name__": "__test__"}
            exec(python_code, env)
            env["_GENO_CAPS"] = frozenset({"clock"})
            main = cast(Callable[[], object], env["main"])

            with pytest.raises(RuntimeError, match="String size exceeds limit"):
                main()

    def test_runtime_serialization_helpers_honor_configured_limit(self):
        """Compiled runtime serialization helpers must enforce collection caps."""
        env = _compiled_runtime_env(2)

        with pytest.raises(RuntimeError, match="List size exceeds limit"):
            env["json_parse"]("[1,2,3]")

        with pytest.raises(RuntimeError, match="Map size exceeds limit"):
            env["json_parse"]('{"a":1,"b":2,"c":3}')

        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["json_parse"]("x")

        with pytest.raises(RuntimeError, match="List size exceeds limit"):
            env["csv_parse"]("a\nb\nc")

        with pytest.raises(RuntimeError, match="Map size exceeds limit"):
            env["toml_parse"]("a = 1\nb = 2\nc = 3")

        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["toml_parse"]("[")

        env = _compiled_runtime_env(5)
        json_array = env["JsonArray"]([env["JsonString"]("abc")])

        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["json_stringify"](json_array)

        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["json_stringify_pretty"](json_array, 2)

        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["json_to_string"](["abc"])

    def test_runtime_capability_helpers_honor_configured_limit(
        self, tmp_path, monkeypatch
    ):
        """Compiled runtime capability helpers must enforce collection caps."""
        env = _compiled_runtime_env(2)
        env["_GENO_CAPS"] = frozenset(
            {"clock", "env", "fs", "http", "process", "stdin"}
        )

        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["clock_format"](0, "%Y")
        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["datetime_format"](0, "%Y")

        big_file = tmp_path / "big.txt"
        big_file.write_text("abcd")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["fs_read_text"]("big.txt")

        for name in ("a.txt", "b.txt", "c.txt"):
            (tmp_path / name).write_text("x")
        with pytest.raises(RuntimeError, match="List size exceeds limit"):
            env["fs_list_dir"](".")

        class _Response:
            status = 200

            def __init__(self, body):
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size=-1):
                body, self._body = self._body, b""
                return body

            def getheaders(self):
                return [("a", "1"), ("b", "2"), ("c", "3")]

        def _open_http_url(*args, **kwargs):
            body = b"ok" if kwargs["fn_name"] == "http_request" else b"abcd"
            return _Response(body)

        env["_open_http_url"] = _open_http_url
        monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")
        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["http_fetch"]("http://example.test")
        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["http_post"]("http://example.test", "")
        with pytest.raises(RuntimeError, match="List size exceeds limit"):
            env["http_request"]("GET", "http://example.test", [], "")

        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["spawn"](sys.executable, ["-c", "print('abcd')"])

        monkeypatch.setattr(sys, "stdin", io.StringIO("abcd"))
        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["stdin_read_all"]()

        monkeypatch.setenv("GENO_BIG_ENV", "abcd")
        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["env_get"]("GENO_BIG_ENV")
        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["env_get_or"]("GENO_BIG_ENV", "")
        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            env["env_get_or"]("GENO_MISSING_ENV", "abcd")

        monkeypatch.setenv("GENO_CLI_ARGS", '["a","b","c"]')
        with pytest.raises(RuntimeError, match="List size exceeds limit"):
            env["cli_args"]()

    def test_runtime_map_vec_set_helpers_precheck_configured_limit(self):
        """Compiled runtime map/vector/set helpers must honor the configured cap."""
        env = _compiled_runtime_env(5)

        with pytest.raises(RuntimeError, match="Map size exceeds limit"):
            env["map_insert"]({i: i for i in range(5)}, 5, 5)

        with pytest.raises(RuntimeError, match="Map size exceeds limit"):
            env["map_merge"]({i: i for i in range(5)}, {5: 5})

        with pytest.raises(RuntimeError, match="List size exceeds limit"):
            env["map_entries"]({i: i for i in range(6)})

        with pytest.raises(RuntimeError, match="Map size exceeds limit"):
            env["map_from_entries"]([(i, i) for i in range(6)])

        mutable_map = env["mutable_map_new"]()
        for i in range(5):
            env["mutable_map_set"](mutable_map, i, i)
        with pytest.raises(RuntimeError, match="MutableMap size exceeds limit"):
            env["mutable_map_set"](mutable_map, 5, 5)

        vec = env["vec_new"]()
        for i in range(5):
            env["vec_push"](vec, i)
        with pytest.raises(RuntimeError, match="Vec size exceeds limit"):
            env["vec_push"](vec, 5)

        with pytest.raises(RuntimeError, match="Vec size exceeds limit"):
            env["vec_from_list"]([0] * 6)

        with pytest.raises(RuntimeError, match="Set size exceeds limit"):
            env["set_from_list"](list(range(6)))

        geno_set = env["set_from_list"](list(range(5)))
        with pytest.raises(RuntimeError, match="Set size exceeds limit"):
            env["set_add"](geno_set, 5)

    def test_runtime_safe_index_set_supports_vec_and_mutable_map(self):
        """Compiled index assignment helper must support mutable reference types."""
        env = _compiled_runtime_env(5)

        vec = env["vec_from_list"]([1, 2, 3])
        env["_safe_index_set"](vec, 1, 42)
        assert env["vec_get"](vec, 1) == 42

        mutable_map = env["mutable_map_new"]()
        env["_safe_index_set"](mutable_map, "a", 42)
        result = env["mutable_map_get"](mutable_map, "a")
        assert isinstance(result, env["Some"])
        assert result.value == 42

    def test_runtime_safe_index_set_prechecks_mutable_map_growth(self):
        """Compiled map index assignment must fail before mutating past the cap."""
        env = _compiled_runtime_env(5)
        mutable_map = env["mutable_map_new"]()
        for i in range(5):
            env["mutable_map_set"](mutable_map, i, i)

        with pytest.raises(RuntimeError, match="MutableMap size exceeds limit"):
            env["_safe_index_set"](mutable_map, 5, 5)

        assert env["mutable_map_size"](mutable_map) == 5

    def test_runtime_array_helpers_honor_configured_collection_limit(self):
        """MED-01: compiled runtime array helpers must use the configured
        collection cap, not the old hardcoded 10M default.
        """
        env = _compiled_runtime_env(10)

        with pytest.raises(RuntimeError, match="Array size exceeds limit"):
            env["array_new"](20, 0)  # type: ignore[operator]

        with pytest.raises(RuntimeError, match="Array size exceeds limit"):
            env["array_from_list"]([0] * 20)  # type: ignore[operator]

    def test_runtime_string_repeat_helpers_honor_configured_collection_limit(self):
        """MED-01: compiled runtime repeat helpers must reject oversized
        results before allocating them.
        """
        env = _compiled_runtime_env(10)

        with pytest.raises(RuntimeError, match="collection size limit"):
            env["repeat_string"]("ab", 20)  # type: ignore[operator]

        with pytest.raises(RuntimeError, match="collection size limit"):
            env["string_repeat"]("ab", 20)  # type: ignore[operator]

    def test_runtime_prelude_reads_max_integer_bits(self):
        """MED-04: _runtime_support.py picks up _GENO_MAX_INTEGER_BITS from
        globals the same way it picks up _GENO_MAX_COLLECTION_SIZE.
        """
        runtime_path = Path(__file__).resolve().parents[1] / "_runtime_support.py"

        # Injected override is honored.
        env: dict = {"_GENO_MAX_INTEGER_BITS": 64}
        exec(runtime_path.read_text(), env)
        assert env["_MAX_INTEGER_BITS"] == 64
        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            env["_safe_mul"](2**50, 2**50)  # type: ignore[operator]

        # Absent override falls back to the default.
        default_env: dict = {}
        exec(runtime_path.read_text(), default_env)
        assert default_env["_MAX_INTEGER_BITS"] == 33_219

    def test_check_collection_size_enforces_integer_bits(self):
        """LOW-03: _check_collection_size also guards integer bit length so
        fold / map_ / format_ callers get defense-in-depth against future
        compiler optimizations that might elide _safe_* wrappers.
        """
        runtime_path = Path(__file__).resolve().parents[1] / "_runtime_support.py"
        env: dict = {"_GENO_MAX_INTEGER_BITS": 64}
        exec(runtime_path.read_text(), env)

        # Booleans are int subclasses but must not trip the check.
        assert env["_check_collection_size"](True) is True

        # Normal ints pass; huge ints fail.
        assert env["_check_collection_size"](2**60) == 2**60
        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            env["_check_collection_size"](2**100)

    def test_fold_catches_integer_accumulator_growth(self):
        """LOW-03: fold's _check_collection_size(acc) now stops an integer
        accumulator that blows past the bit ceiling.
        """
        runtime_path = Path(__file__).resolve().parents[1] / "_runtime_support.py"
        env: dict = {"_GENO_MAX_INTEGER_BITS": 64}
        exec(runtime_path.read_text(), env)

        # Reducer that squares the accumulator each step, bypassing _safe_mul
        # (simulates an optimizer eliding the wrapper).
        def squarer(acc, _x):
            return acc * acc

        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            env["fold"](list(range(20)), 3, squarer)

    def test_map_catches_integer_element_growth(self):
        """LOW-03: map_ now checks each produced element so a closure that
        returns an overlarge int is rejected.
        """
        runtime_path = Path(__file__).resolve().parents[1] / "_runtime_support.py"
        env: dict = {"_GENO_MAX_INTEGER_BITS": 64}
        exec(runtime_path.read_text(), env)

        def big(_x):
            return 2**100

        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            env["map_"]([1], big)

    def test_format_rejects_overlarge_int_values(self):
        """LOW-03: format_ rejects overlarge ints before str-ing them."""
        runtime_path = Path(__file__).resolve().parents[1] / "_runtime_support.py"
        env: dict = {"_GENO_MAX_INTEGER_BITS": 64}
        exec(runtime_path.read_text(), env)

        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            env["format_"]("{}", [2**100])

    def test_compiled_uses_safe_add(self):
        """Compiled + must be guarded: inline bit check for Int variables,
        _safe_add for non-Int operands. Small literal addends are exempt
        (bounded constants cannot produce integer-bomb growth)."""
        source = """
        func add(a: Int, b: Int) -> Int
            example (1, 2) -> 3
            return a + b
        end func
        """
        python_code = compile_to_python(source)
        body = python_code[python_code.find("def add") :]
        assert ".bit_length() <= _MAX_INTEGER_BITS else _int_oob(" in body

        literal_source = """
        func main() -> Int
            example () -> 3
            return 1 + 2
        end func
        """
        literal_code = compile_to_python(literal_source)
        assert "return ((1) + (2))" in literal_code

        string_source = """
        func main() -> String
            example () -> "ab"
            return "a" + "b"
        end func
        """
        assert "_safe_add(" in compile_to_python(string_source)

    def test_compiled_int_fast_paths_enforce_bit_limit(self):
        """Typed integer arithmetic must still go through guarded helpers."""
        source = """
        func main() -> Int
            example () -> 0
            return 9223372036854775808 + 9223372036854775808
        end func
        """
        python_code = compile_to_python(source)
        env = {"_GENO_MAX_INTEGER_BITS": 64, "__name__": "__test__"}
        exec(python_code, env)
        main = cast(Callable[[], object], env["main"])

        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            main()

    @pytest.mark.parametrize(
        ("operator", "left_value", "right_value"),
        [
            ("+", "9223372036854775808", "9223372036854775808"),
            ("*", "1099511627776", "1099511627776"),
            ("-", "-9223372036854775808", "9223372036854775808"),
        ],
    )
    def test_compiled_numeric_float_paths_enforce_int_bit_limit(
        self, operator: str, left_value: str, right_value: str
    ):
        """Float-typed arithmetic must guard runtime Int values."""
        source = f"""
        @untested("large runtime values")
        func op_float(a: Float, b: Float) -> Float
            return a {operator} b
        end func

        func main() -> Float
            let a: Int = {left_value}
            let b: Int = {right_value}
            return op_float(a, b)
        end func
        """
        python_code = compile_to_python(source)
        env = {"_GENO_MAX_INTEGER_BITS": 64, "__name__": "__test__"}
        exec(python_code, env)
        main = cast(Callable[[], object], env["main"])

        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            main()

    def test_compiled_int_literal_honors_bit_limit(self):
        """Direct integer literals must not bypass runtime bit limits."""
        source = """
        func main() -> Int
            return 1267650600228229401496703205376
        end func
        """
        python_code = compile_to_python(source)
        env = {"_GENO_MAX_INTEGER_BITS": 64, "__name__": "__test__"}
        exec(python_code, env)
        main = cast(Callable[[], object], env["main"])

        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            main()

    def test_compiled_int_literal_pattern_honors_bit_limit(self):
        """Integer literal patterns must not bypass runtime bit limits."""
        source = """
        func main() -> Int
            let x: Int = 0
            return match x with
                | 1099511627776 -> 1
                | _ -> 0
            end match
        end func
        """
        python_code = compile_to_python(source)
        env = {"_GENO_MAX_INTEGER_BITS": 32, "__name__": "__test__"}
        exec(python_code, env)
        main = cast(Callable[[], object], env["main"])

        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            main()

    @pytest.mark.parametrize(
        ("source_body", "a_value", "expected"),
        [
            ("return a ^ 7", -1, -8),
            ("return a & (0 - 4)", -5, -8),
            ("return ~b", -5, -8),
        ],
    )
    def test_compiled_bitwise_ops_honor_bit_limit_with_negatives(
        self, source_body: str, a_value: int, expected: int
    ):
        """& ^ ~ can grow a negative result by one bit (e.g. -5 & -4 == -8,
        -1 ^ 7 == -8, ~7 == -8) and must keep their guarded helpers so
        tightened bit limits reject the result."""
        source = f"""
        @untested("limit probe")
        func op(a: Int, b: Int) -> Int
            {source_body}
        end func

        func main() -> Int
            return op(0 - {-a_value}, 7)
        end func
        """
        python_code = compile_to_python(source)

        env = {"_GENO_MAX_INTEGER_BITS": 3, "__name__": "__test__"}
        exec(python_code, env)
        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            cast(Callable[[], object], env["main"])()

        # Under the default limit the value computes normally.
        default_env: dict = {"__name__": "__test__"}
        exec(python_code, default_env)
        assert cast(Callable[..., object], default_env["op"])(a_value, 7) == expected

    def test_compiled_small_addend_relaxation_is_deliberate(self):
        """DOCUMENTED CONTRACT (see _GROWTH_FREE_ADDEND_MAX_BITS): Int +/-
        with a small literal addend skips the per-operation bits guard for
        performance, so under a host-tightened limit the escaping value may
        exceed the ceiling by one bit instead of raising. The value cannot
        grow further unchecked: any guarded use (here var+var addition)
        still trips the configured limit. This test encodes the policy
        decision; do not \"fix\" either direction without revisiting
        benchmarks/RESULTS.md."""
        source = """
        func main() -> Int
            let x: Int = 7
            let y: Int = x + 1
            return y + y
        end func
        """
        python_code = compile_to_python(source)
        env = {"_GENO_MAX_INTEGER_BITS": 3, "__name__": "__test__"}
        exec(python_code, env)
        main = cast(Callable[[], object], env["main"])

        # x + 1 == 8 (4 bits) is allowed to escape under the relaxation,
        # but the guarded var+var addition y + y re-trips the limit.
        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            main()

    def test_runtime_numeric_helpers_honor_integer_bit_limit(self):
        """Runtime helpers that create ints must honor configured bit limits."""
        runtime_path = Path(__file__).resolve().parents[1] / "_runtime_support.py"
        env: dict = {"_GENO_MAX_INTEGER_BITS": 32}
        exec(runtime_path.read_text(), env)

        too_large = float(2**40)
        for helper_name in (
            "floor_",
            "ceil_",
            "round_",
            "float_to_int",
            "math_floor",
            "math_ceil",
            "math_round",
        ):
            with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
                env[helper_name](too_large)

        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            env["parse_int"](str(2**40))

    def test_runtime_host_integer_helpers_honor_integer_bit_limit(self):
        """Host-derived integer results must honor configured bit limits."""
        env = _compiled_runtime_env(max_collection_size=100, max_integer_bits=1)
        env["_GENO_CAPS"] = frozenset({"clock", "random"})

        arr = env["array_from_list"]([0, 0, 0])  # type: ignore[operator]
        mutable = env["mutable_map_new"]()  # type: ignore[operator]
        vec = env["vec_new"]()  # type: ignore[operator]
        for key in ("a", "b", "c"):
            env["mutable_map_set"](mutable, key, 0)  # type: ignore[operator]
            env["vec_push"](vec, 0)  # type: ignore[operator]
        set_value = env["set_from_list"](["a", "b", "c"])  # type: ignore[operator]

        helper_calls = (
            lambda: env["length"]([0, 0, 0]),  # type: ignore[operator]
            lambda: env["list_length"]([0, 0, 0]),  # type: ignore[operator]
            lambda: env["array_length"](arr),  # type: ignore[operator]
            lambda: env["mutable_map_size"](mutable),  # type: ignore[operator]
            lambda: env["vec_length"](vec),  # type: ignore[operator]
            lambda: env["set_size"](set_value),  # type: ignore[operator]
            lambda: env["string_index_of"]("abc", "c"),  # type: ignore[operator]
            lambda: env["string_last_index_of"]("abc", "c"),  # type: ignore[operator]
            lambda: env["char_code"]("A"),  # type: ignore[operator]
            lambda: env["enumerate"]([0, 0, 0]),  # type: ignore[operator]
            lambda: env["list_enumerate"]([0, 0, 0]),  # type: ignore[operator]
            lambda: env["list_find_index"]([0, 0, 1], lambda x: x == 1),  # type: ignore[operator]
            lambda: env["clock_now"](),  # type: ignore[operator]
            lambda: env["random_int"](3, 3),  # type: ignore[operator]
            lambda: env["math_random_int"](3, 3),  # type: ignore[operator]
            lambda: env["datetime_now"](),  # type: ignore[operator]
            lambda: env["datetime_parse"]("1970-01-03", "%Y-%m-%d"),  # type: ignore[operator]
            lambda: env["datetime_elapsed"](0, 3),  # type: ignore[operator]
            lambda: env["screen_width"](),  # type: ignore[operator]
            lambda: env["screen_height"](),  # type: ignore[operator]
            lambda: env["range_"](0, 3),  # type: ignore[operator]
        )

        for call in helper_calls:
            with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
                call()

    def test_runtime_range_and_substring_validate_argument_types(self):
        """Compiled runtime helpers should match interpreter type diagnostics."""
        env = _compiled_runtime_env(max_collection_size=100)
        cases = (
            (
                "range start",
                lambda: env["range_"](1.2, 3),  # type: ignore[operator]
                "range start must be an integer",
            ),
            (
                "range end",
                lambda: env["range_"](1, 3.5),  # type: ignore[operator]
                "range end must be an integer",
            ),
            (
                "range step",
                lambda: env["range_"](1, 3, "x"),  # type: ignore[operator]
                "range step must be an integer",
            ),
            (
                "substring text",
                lambda: env["substring"](42, 0, 1),  # type: ignore[operator]
                "substring expects string, got int",
            ),
            (
                "substring start",
                lambda: env["substring"]("abc", 1.2, 2),  # type: ignore[operator]
                "substring start must be an integer",
            ),
            (
                "substring stop",
                lambda: env["substring"]("abc", 1, 2.5),  # type: ignore[operator]
                "substring stop must be an integer",
            ),
            (
                "string_substring text",
                lambda: env["string_substring"](42, 0, 1),  # type: ignore[operator]
                "string_substring text must be a string, got int",
            ),
            (
                "string_substring stop",
                lambda: env["string_substring"]("abc", 0, 2.5),  # type: ignore[operator]
                "string_substring stop must be an integer",
            ),
        )

        for _label, call, message in cases:
            with pytest.raises(RuntimeError, match=message):
                call()

    def test_compiled_parse_int_honors_bit_limit(self):
        """Compiled parse_int results must not bypass runtime bit limits."""
        source = """
        func main() -> Option[Int]
            return parse_int("1099511627776")
        end func
        """
        python_code = compile_to_python(source)
        env = {"_GENO_MAX_INTEGER_BITS": 32, "__name__": "__test__"}
        exec(python_code, env)
        main = cast(Callable[[], object], env["main"])

        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            main()

    def test_compiled_length_fast_path_honors_bit_limit(self):
        """Compiled length fast paths must check their host len() result."""
        source = """
        func main() -> Int
            return length([0, 0, 0])
        end func
        """
        python_code = compile_to_python(source)
        env = {"_GENO_MAX_INTEGER_BITS": 1, "__name__": "__test__"}
        exec(python_code, env)
        main = cast(Callable[[], object], env["main"])

        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            main()

    def test_compiled_host_integer_helpers_honor_bit_limit(self):
        """Compiled runtime helpers must check synthesized index results."""
        sources = (
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

        for source in sources:
            python_code = compile_to_python(source)
            env = {"_GENO_MAX_INTEGER_BITS": 1, "__name__": "__test__"}
            exec(python_code, env)
            main = cast(Callable[[], object], env["main"])
            with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
                main()

    def test_compiled_int_mul_and_sub_use_safe_helpers(self):
        """Int * must always keep the integer-bits guard, and - must keep it
        unless an operand is a small literal addend."""
        source = """
        func f(a: Int, b: Int) -> Int
            example (3, 4) -> 7
            return (a * b) - (a - b)
        end func
        """
        python_code = compile_to_python(source)
        body = python_code[python_code.find("def f") :]
        # one guard for *, one at the root of the subtraction chain (the
        # inner a - b compiles raw and is re-checked by the root guard)
        assert body.count(".bit_length() <= _MAX_INTEGER_BITS else _int_oob(") == 2

        literal_mul = """
        func main() -> Int
            example () -> 12
            return 3 * 4
        end func
        """
        mul_code = compile_to_python(literal_mul)
        mul_body = mul_code[mul_code.find("def main") :]
        assert mul_body.count(".bit_length() <= _MAX_INTEGER_BITS else _int_oob(") == 1


class TestCompilerSetAtBoundsChecking:
    """Test that compiled set_at rejects negative and out-of-bounds indices."""

    def test_set_at_negative_index_rejected(self):
        """Negative indices must be rejected, matching interpreter behavior."""
        source = """
        func main() -> List[Int]
            example () -> [1]
            let arr: List[Int] = [10, 20, 30]
            return set_at(list: arr, index: -1, value: 99)
        end func
        """
        with pytest.raises(RuntimeError, match="out of range"):
            compile_and_run(source)

    def test_set_at_out_of_bounds_rejected(self):
        """Out-of-bounds indices must raise an error."""
        source = """
        func main() -> List[Int]
            example () -> [1]
            let arr: List[Int] = [10, 20, 30]
            return set_at(list: arr, index: 5, value: 99)
        end func
        """
        with pytest.raises(RuntimeError, match="out of range"):
            compile_and_run(source)

    def test_set_at_valid_index_works(self):
        """Valid indices should work correctly."""
        source = """
        func main() -> Int
            example () -> 99
            let arr: List[Int] = [10, 20, 30]
            let updated: List[Int] = set_at(list: arr, index: 1, value: 99)
            return updated[1]
        end func
        """
        assert compile_and_run(source) == 99


class TestCompilerMultiplySafety:
    """Test that compiled * operator enforces collection size limits."""

    def test_compiled_uses_safe_mul(self):
        """Compiled output must use _safe_mul for * operator."""
        source = """
        func main() -> Int
            example () -> 6
            return 2 * 3
        end func
        """
        python_code = compile_to_python(source)
        assert "_safe_mul(" in python_code

    def test_integer_multiply_works(self):
        """Normal integer multiplication should work fine."""
        source = """
        func main() -> Int
            example () -> 6
            return 2 * 3
        end func
        """
        assert compile_and_run(source) == 6

    def test_safe_mul_rejects_huge_string(self):
        """_safe_mul must reject string repetition exceeding the limit."""
        from geno._runtime_support import _MAX_COLLECTION_SIZE, _safe_mul

        with pytest.raises(RuntimeError, match="size exceeds limit"):
            _safe_mul("x", _MAX_COLLECTION_SIZE + 1)

    def test_safe_mul_allows_small_result(self):
        """_safe_mul should allow normal numeric multiplication."""
        from geno._runtime_support import _safe_mul

        assert _safe_mul(7, 6) == 42

    def test_safe_mul_pre_checks_huge_multiplier(self):
        """_safe_mul must reject before allocating when multiplier is huge."""
        from geno._runtime_support import _safe_mul

        # 10**18 would OOM if allocated; pre-check must catch it
        with pytest.raises(RuntimeError, match="size exceeds limit"):
            _safe_mul("a", 10**18)

    def test_safe_mul_pre_checks_reversed_operands(self):
        """_safe_mul must also pre-check int * str."""
        from geno._runtime_support import _safe_mul

        with pytest.raises(RuntimeError, match="size exceeds limit"):
            _safe_mul(10**18, "a")

    def test_safe_add_pre_checks_concatenation(self):
        """_safe_add must reject before allocating huge concatenation."""
        from geno._runtime_support import _MAX_COLLECTION_SIZE, _safe_add

        big = "x" * (_MAX_COLLECTION_SIZE // 2 + 1)
        with pytest.raises(RuntimeError, match="size exceeds limit"):
            _safe_add(big, big)


class TestCompilerBitwiseExponentSafety:
    """Test that compiled integer-heavy operators enforce bit limits."""

    def test_compiled_uses_safe_pow(self):
        source = compile_to_python(
            """
            func main() -> Int
                return 2 ** 8
            end func
            """
        )
        assert "_safe_pow(" in source

    def test_compiled_uses_safe_lshift(self):
        source = compile_to_python(
            """
            func main() -> Int
                return 1 << 3
            end func
            """
        )
        assert "_safe_lshift(" in source

    def test_compiled_huge_lshift_raises(self):
        source = """
        func main() -> Int
            return 1 << 40000
        end func
        """
        with pytest.raises(RuntimeError, match="Left shift amount too large"):
            compile_and_run(source)

    def test_compiled_huge_pow_raises(self):
        source = """
        func main() -> Int
            return 2 ** 40000
        end func
        """
        with pytest.raises(RuntimeError, match="Exponentiation result too large"):
            compile_and_run(source)

    def test_compiled_float_pow_overflow_raises_runtime_error(self):
        source = """
        func main() -> Float
            return 1.5 ** 100000000
        end func
        """
        with pytest.raises(RuntimeError, match="Exponentiation result too large"):
            compile_and_run(source)

    def test_compiled_negative_float_base_fractional_pow_raises_runtime_error(self):
        source = """
        func main() -> Float
            return (0.0 - 1.0) ** 0.5
        end func
        """
        with pytest.raises(RuntimeError, match="not a real number"):
            compile_and_run(source)

    def test_compiled_negative_float_base_integer_pow_still_works(self):
        source = """
        func main() -> Float
            return (0.0 - 2.0) ** 3.0
        end func
        """
        assert compile_and_run(source) == -8.0

    def test_compiled_zero_base_negative_pow_raises_division_by_zero(self):
        source = """
        func main() -> Int
            return 0 ** -1
        end func
        """
        with pytest.raises(RuntimeError, match="Division by zero"):
            compile_and_run(source)

    def test_compiled_uses_safe_invert(self):
        source = compile_to_python(
            """
            func main() -> Int
                return ~1
            end func
            """
        )
        assert "_safe_invert(" in source


class TestPythonKeywordMangling:
    """Python keywords used as Geno identifiers must be mangled to valid Python."""

    def test_let_with_python_keyword(self):
        """let using a Python keyword name compiles and runs correctly."""
        source = """
        func main() -> Int
            let class: Int = 42
            return class
        end func
        """
        assert compile_and_run(source) == 42

    def test_var_with_python_keyword(self):
        """var using a Python keyword name compiles and runs correctly."""
        source = """
        func main() -> Int
            var yield: Int = 10
            yield = 20
            return yield
        end func
        """
        assert compile_and_run(source) == 20

    def test_function_param_python_keyword(self):
        """Function parameter named as a Python keyword compiles correctly."""
        source = """
        func add_one(lambda: Int) -> Int
            example 5 -> 6
            return lambda + 1
        end func

        func main() -> Int
            return add_one(lambda: 5)
        end func
        """
        assert compile_and_run(source) == 6

    def test_for_loop_var_python_keyword(self):
        """For loop variable named as a Python keyword compiles correctly."""
        source = """
        func main() -> Int
            var total: Int = 0
            for global: Int in [1, 2, 3] do
                total = total + global
            end for
            return total
        end func
        """
        assert compile_and_run(source) == 6

    def test_compiled_output_has_no_bare_keywords(self):
        """Compiled Python source must not contain bare Python keywords as identifiers."""
        source = """
        func main() -> Int
            let class: Int = 99
            return class
        end func
        """
        python_code = compile_to_python(source)
        # The compiled code should use class_kw, not bare class
        assert "class_kw" in python_code
        assert "class:" not in python_code.split("class_kw")[0].split("\n")[-1]


class TestMangledHelperNameShadowing:
    """User-defined functions must not shadow mangled builtin helpers."""

    def test_print_underscore_rejected(self):
        """func print_(...) must be rejected — it shadows the runtime helper."""
        from geno.compiler import CompileError

        source = """
        func print_(x: Int) -> Int
            example 1 -> 1
            return x
        end func
        """
        with pytest.raises(CompileError, match="print_"):
            compile_to_python(source)

    def test_map_underscore_rejected(self):
        """func map_(...) must be rejected — it shadows the runtime helper."""
        from geno.compiler import CompileError

        source = """
        func map_(x: Int) -> Int
            example 1 -> 1
            return x
        end func
        """
        with pytest.raises(CompileError, match="map_"):
            compile_to_python(source)

    def test_filter_underscore_rejected(self):
        """func filter_(...) must be rejected — it shadows the runtime helper."""
        from geno.compiler import CompileError

        source = """
        func filter_(x: Int) -> Int
            example 1 -> 1
            return x
        end func
        """
        with pytest.raises(CompileError, match="filter_"):
            compile_to_python(source)

    def test_exec_underscore_rejected(self):
        """func exec_(...) must be rejected — it shadows the runtime helper."""
        from geno.compiler import CompileError

        source = """
        func exec_(x: Int) -> Int
            example 1 -> 1
            return x
        end func
        """
        with pytest.raises(CompileError, match="exec_"):
            compile_to_python(source)


class TestPipelineGapFixes:
    """Regression tests for compiler pipeline gap fixes."""

    def test_import_statement_is_skipped(self):
        """ImportStatement is silently skipped (handled by multi-module pipeline)."""
        from geno.ast_nodes import ImportStatement, Program
        from geno.tokens import SourceLocation

        loc = SourceLocation(1, 1, "<test>")
        program = Program(
            location=loc,
            definitions=[ImportStatement(location=loc, module_name="Utils")],
        )
        compiler = Compiler()
        # Should not raise — imports are resolved at the project level
        result = compiler.compile(program)
        assert "Utils" not in result

    def test_typed_hole_emits_runtime_error(self):
        """TypedHole must compile to _typed_hole() call, not silent None."""
        source = compile_to_python(
            """
            func main() -> Int
                return 42
            end func
            """,
            typecheck=False,
        )
        # The runtime prelude must define _typed_hole
        assert "def _typed_hole" in source

        # Directly test the compiler's expression output
        from geno.ast_nodes import TypedHole
        from geno.tokens import SourceLocation

        loc = SourceLocation(1, 1, "<test>")
        compiler = Compiler()
        from geno.ast_nodes import SimpleType

        hole = TypedHole(
            location=loc,
            name="my_hole",
            hole_type=SimpleType(location=loc, name="Int"),
        )
        result = compiler._compile_expr(hole)
        assert "_typed_hole" in result
        assert "my_hole" in result
        assert "None" not in result

    def test_match_expr_uses_unique_temp_vars(self):
        """Inline match must use unique temp vars, not hardcoded _match_val."""
        source = compile_to_python(
            """
            func check(x: Option[Int]) -> Int
                example Some(1) -> 1
                example None -> 0
                let result: Int = match x with
                    | Some(v) -> v
                    | None -> 0
                end match
                return result
            end func
            """
        )
        # Must NOT contain the old hardcoded _match_val name
        assert "_match_val" not in source
        # Must contain a _temp_ variable instead
        assert "_temp_" in source

    def test_expression_subclass_falls_back_to_compatibility_path(self):
        """Expression subclasses should still compile via the slow path."""
        from geno.ast_nodes import Identifier
        from geno.tokens import SourceLocation

        class DerivedIdentifier(Identifier):
            pass

        loc = SourceLocation(1, 1, "<test>")
        compiler = Compiler()
        expr = DerivedIdentifier(location=loc, name="value")

        assert compiler._compile_expr(expr) == "value"

    def test_statement_subclass_falls_back_to_compatibility_path(self):
        """Statement subclasses should still emit code and source comments."""
        from geno.ast_nodes import IntegerLiteral, ReturnStatement
        from geno.tokens import SourceLocation

        class DerivedReturnStatement(ReturnStatement):
            pass

        loc = SourceLocation(3, 1, "<test>")
        compiler = Compiler()
        stmt = DerivedReturnStatement(
            location=loc,
            value=IntegerLiteral(location=loc, value=7),
        )

        compiler._compile_statement(stmt)

        assert compiler.output.getvalue() == "# geno:<test>:3\nreturn 7\n"

    def test_source_location_comments_escape_filename_controls(self):
        """Generated source comments should not let filenames create code lines."""
        src = "func main() -> Int\n    return 1\nend func\n"
        filename = 'evil.geno\nprint("INJECT")\r\x1b#'

        compiled = compile_to_python(src, filename=filename)

        compile(compiled, "<generated>", "exec")
        assert '# geno:evil.geno\\nprint("INJECT")\\r\\x1b#:1' in compiled
        assert '\nprint("INJECT")\n' not in compiled
        assert all(
            line.lstrip().startswith("#")
            for line in compiled.splitlines()
            if "INJECT" in line
        )

    def test_propagate_subclass_is_detected(self):
        """Subclassed propagate nodes should still enable the try/except wrapper."""
        from geno.ast_nodes import (
            FunctionDef,
            Identifier,
            PropagateExpr,
            ReturnStatement,
            SimpleType,
            SpecBlock,
        )
        from geno.tokens import SourceLocation

        class DerivedPropagateExpr(PropagateExpr):
            pass

        loc = SourceLocation(1, 1, "<test>")
        func = FunctionDef(
            location=loc,
            name="main",
            params=[],
            return_type=SimpleType(location=loc, name="Int"),
            specs=SpecBlock(),
            body=[
                ReturnStatement(
                    location=loc,
                    value=DerivedPropagateExpr(
                        location=loc,
                        operand=Identifier(location=loc, name="value"),
                    ),
                )
            ],
        )

        assert Compiler._uses_propagate(func) is True


class TestCompilerStateReset:
    """Regression tests for reused compiler instances."""

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

        compiler = Compiler()
        first_output = compiler.compile(trait_program)
        assert "def describe(self_arg, *_args):" in first_output

        second_output = compiler.compile(simple_program)
        assert "def describe(self_arg, *_args):" not in second_output


class TestCompilerTemporaries:
    """Regression tests for generated helper names."""

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

        compiled = compile_to_python(source, typecheck=True)
        assert "_temp_2 = x" in compiled
        assert compile_and_run(source) == 42


class TestInterpreterCompilerParity:
    """Tests that interpreter and compiler produce identical results."""

    def test_constructor_repr_matches_interpreter(self):
        """Compiled to_string(Some(42)) must use ': ' not '=' in field format."""
        source = """
        func main() -> String
            return to_string(Some(42))
        end func
        """
        result = compile_and_run(source)
        assert result == "Some(value: 42)", f"got {result!r}"

    def test_constructor_repr_preserves_string_quotes(self):
        """Compiled constructor repr must keep quoted string fields."""
        source = """
        func main() -> String
            return to_string(Some("hi"))
        end func
        """
        result = compile_and_run(source)
        assert result == 'Some(value: "hi")', f"got {result!r}"

    def test_user_type_repr_matches_interpreter(self):
        """User-defined type to_string must match interpreter format."""
        source = """
        type Point = MkPoint(x: Int, y: Int)

        func main() -> String
            return to_string(MkPoint(1, 2))
        end func
        """
        result = compile_and_run(source)
        assert result == "MkPoint(x: 1, y: 2)", f"got {result!r}"

    def test_nullary_constructor_repr(self):
        """Nullary constructor to_string has no parenthesized fields."""
        source = """
        func main() -> String
            return to_string(None)
        end func
        """
        result = compile_and_run(source)
        assert result == "_None", f"got {result!r}"

    def test_and_returns_bool(self):
        """Compiled 'and' must return a bool, not the operand value."""
        source = """
        func main() -> Bool
            return true and true
        end func
        """
        result = compile_and_run(source)
        assert result is True
        assert type(result) is bool

    def test_or_returns_bool(self):
        """Compiled 'or' must return a bool, not the operand value."""
        source = """
        func main() -> Bool
            return false or true
        end func
        """
        result = compile_and_run(source)
        assert result is True
        assert type(result) is bool

    def test_and_short_circuits_to_false(self):
        """Compiled 'and' with falsy left must return False (bool)."""
        source = """
        func main() -> Bool
            return false and true
        end func
        """
        result = compile_and_run(source)
        assert result is False
        assert type(result) is bool

    def test_subtraction_overflow_guard(self):
        """Compiled var-var subtraction must guard against integer overflow."""
        source = compile_to_python(
            """
            func sub(a: Int, b: Int) -> Int
                example (1, 2) -> -1
                return a - b
            end func
            """
        )
        body = source[source.find("def sub") :]
        assert ".bit_length() <= _MAX_INTEGER_BITS else _int_oob(" in body

    def test_typed_nonzero_literal_modulo_uses_numeric_helper(self):
        """Typed Int % Int should use the truncation-aware numeric helper."""
        source = compile_to_python(
            """
            func main() -> Int
                return 10 % 3
            end func
            """
        )
        assert "_safe_mod(" not in source.split("def _safe_mod")[0]
        assert "return ((10) % 3 if (10) >= 0 else -(-(10) % 3))" in source

    def test_modulo_by_zero_raises(self):
        """Compiled modulo by zero must raise RuntimeError."""
        source = """
        func main() -> Int
            return 10 % 0
        end func
        """
        with pytest.raises(RuntimeError, match="Division by zero"):
            compile_and_run(source)

    def test_modulo_by_zero_caught_by_try_catch(self):
        """try/catch must catch RuntimeError from guarded typed %."""
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
        assert compile_and_run(source) == "caught"

    def test_typed_modulo_simple_operands_use_numeric_helper(self):
        """Typed % on identifiers inlines the non-negative fast case and
        falls back to the truncation helper for negatives and zero."""
        source = compile_to_python(
            """
            func f(a: Int, b: Int) -> Int
                example (10, 3) -> 1
                return a % b
            end func
            """
        )
        assert "return ((a) % (b) if (a) >= 0 < (b) else _int_mod((a), (b)))" in source

    def test_typed_modulo_non_simple_operands_use_numeric_helper(self):
        """Typed % should preserve operand expressions via the numeric helper."""
        source = compile_to_python(
            """
            func f(a: Int, b: Int) -> Int
                example (10, 3) -> 3
                return (a + 1) % (b + 1)
            end func
            """
        )
        body = source[source.find("def f") :]
        assert "return _int_mod(((a) + (1)), ((b) + (1)))" in body

    def test_typed_modulo_preserves_left_to_right_evaluation(self):
        """Typed % must match interpreter left-to-right operand evaluation."""
        source = """
        func left(flag: Bool) -> Int
            example (false) -> 1
            if flag then
                throw "left"
            end if
            return 1
        end func

        func right(zero: Bool) -> Int
            example (false) -> 2
            example (true) -> 0
            if zero then
                return 0
            end if
            return 2
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
        assert compile_and_run(source) == "left"

    def test_untyped_modulo_uses_safe_mod(self):
        """Untyped modulo falls back to _safe_mod wrapper."""
        source = compile_to_python(
            """
            func main() -> Int
                return 10 % 3
            end func
            """,
            typecheck=False,
        )
        assert "_safe_mod(" in source


class TestLiteralPatternStringEscape:
    """HIGH-12: string literal patterns must escape special characters."""

    def test_pattern_with_quote_produces_valid_python(self):
        src = """
        func classify(s: String) -> Int
            example "a" -> 1
            example "say \\"hi\\"" -> 2
            match s with
                | "a" -> return 1
                | "say \\"hi\\"" -> return 2
                | _ -> return 0
            end match
        end func

        func main() -> Int
            return classify("say \\"hi\\"")
        end func
        """
        # Must both compile and execute — previously this produced
        # invalid Python: `s == "say "hi""`.
        assert compile_and_run(src) == 2

    def test_pattern_with_backslash_produces_valid_python(self):
        src = """
        func classify(s: String) -> Int
            example "\\\\" -> 1
            match s with
                | "\\\\" -> return 1
                | _ -> return 0
            end match
        end func

        func main() -> Int
            return classify("\\\\")
        end func
        """
        assert compile_and_run(src) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


@pytest.mark.parametrize(
    "expression",
    ["_GENO_CAPS", '_bounded_regex_replace("a", "b", "a")'],
)
def test_python_compiler_rejects_direct_runtime_internal_reference_without_typecheck(
    expression,
):
    source = f"func main() -> Any\n    return {expression}\nend func\n"

    with pytest.raises(CompileError, match="reserved runtime name"):
        compile_to_python(source, typecheck=False)


def test_python_compiler_allows_declared_builtin_without_typecheck():
    source = "func main() -> Int\n    return array_get(array_new(1, 7), 0)\nend func\n"

    assert "def main" in compile_to_python(source, typecheck=False)


@pytest.mark.parametrize(
    "field_name", ["class", "__slots__", "__annotations__", "__module__"]
)
def test_python_compiler_rejects_unsafe_record_field(field_name):
    source = (
        f"type Wrapper = Wrapper({field_name}: Int)\n"
        "func main() -> Int\n"
        "    return 1\n"
        "end func\n"
    )

    with pytest.raises(CompileError, match="cannot be represented safely"):
        compile_to_python(source, typecheck=False)


@pytest.mark.parametrize(
    "name", ["IndexError", "RuntimeError", "__name__", "bool", "len", "list", "set"]
)
def test_python_compiler_rejects_emitted_host_intrinsic_binding(name):
    binding = (
        f"type Host = {name}(value: Int)\n"
        if name[0].isupper()
        else f"func {name}() -> Int\n    return 1\nend func\n"
    )
    source = binding + "func main() -> Int\n    return 1\nend func\n"

    with pytest.raises(CompileError, match="reserved runtime name"):
        compile_to_python(source, typecheck=False)


def test_python_single_program_rejects_function_trait_dispatcher_collision():
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

    with pytest.raises(CompileError, match="conflicts with a trait dispatcher"):
        compile_to_python(source, typecheck=False)


def test_python_standalone_rejects_runtime_prelude_builtin_shadow():
    source = """
func set() -> Int
    example () -> 1
    return 1
end func
func main() -> Int
    return set_size(set_from_list([1, 2]))
end func
"""

    with pytest.raises(CompileError, match="reserved runtime name"):
        compile_to_python(source)
