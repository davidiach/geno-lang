"""Constants must not silently alias a distinct name after backend mangling."""

import sys
from functools import partial
from types import SimpleNamespace

import pytest

from geno.compiler import CompileError, Compiler, compile_to_python
from geno.js_compiler import JSCompileError, JSCompiler, compile_to_js
from geno.parser import parse
from geno.tests._script_runner import run_node_code, run_python_code


@pytest.mark.parametrize(
    "compile_fn,error_type",
    [(compile_to_python, CompileError), (compile_to_js, JSCompileError)],
)
@pytest.mark.parametrize("typecheck", [False, True])
@pytest.mark.parametrize(
    "source",
    [
        "let class = 1\nlet class_kw = 2\n"
        "func main() -> Int\nreturn class + class_kw\nend func\n",
        "let class_kw = 2\nlet class = 1\n"
        "func main() -> Int\nreturn class + class_kw\nend func\n",
        "let class = 1\n"
        "func class_kw() -> Int\nexample () -> 2\nreturn 2\nend func\n"
        "func main() -> Int\nreturn class + class_kw()\nend func\n",
        "let class = 1\n"
        "func main() -> Int\nlet class_kw = 2\nreturn class + class_kw\nend func\n",
        "let class = 1\n"
        "func add(class_kw: Int) -> Int\nexample 2 -> 3\n"
        "return class + class_kw\nend func\n"
        "func main() -> Int\nreturn add(2)\nend func\n",
    ],
    ids=["constants", "reversed-constants", "function", "local", "parameter"],
)
def test_distinct_names_cannot_alias_a_module_constant(
    compile_fn, error_type, typecheck, source
):
    with pytest.raises(error_type, match=r"[Mm]odule constant.*conflicts"):
        compile_fn(source, typecheck=typecheck)


@pytest.mark.parametrize(
    "compiler_type,error_type",
    [(Compiler, CompileError), (JSCompiler, JSCompileError)],
)
def test_imported_function_cannot_alias_a_module_constant(compiler_type, error_type):
    graph = SimpleNamespace(
        parsed={
            "Lib": parse(
                "func class_kw() -> Int\nexample () -> 2\nreturn 2\nend func\n"
            ),
            "App": parse(
                "import Lib\nlet class = 1\n"
                "func main() -> Int\nreturn class + class_kw()\nend func\n"
            ),
        },
        sorted_modules=["Lib", "App"],
        project=SimpleNamespace(entrypoint="App"),
    )
    with pytest.raises(error_type, match=r"[Mm]odule constant.*conflicts"):
        compiler_type().compile_project(graph)


@pytest.mark.parametrize(
    "compile_fn,runner",
    [
        (compile_to_python, partial(run_python_code, python_executable=sys.executable)),
        (compile_to_js, run_node_code),
    ],
)
@pytest.mark.parametrize("shadow", [False, True])
def test_keyword_constant_and_same_name_shadowing_remain_valid(
    compile_fn, runner, shadow
):
    source = "let class = 1\nfunc main() -> Int\n"
    if shadow:
        source += "let class = 2\n"
    source += "return class\nend func\n"
    result = runner(compile_fn(source))
    assert result.stderr == "", result.stderr
    # `main` returns the constant, which an `Int` main reports as its exit
    # status from 0.5 on rather than printing (spec 4.1.1).
    assert result.returncode == (2 if shadow else 1)
