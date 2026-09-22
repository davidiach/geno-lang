"""Postcondition temporaries must not capture ordinary source bindings."""

import sys
from functools import partial
from types import SimpleNamespace

import pytest

from geno.api import RunConfig, run
from geno.compiler import Compiler, compile_to_python
from geno.js_compiler import JSCompiler, compile_to_js
from geno.parser import parse
from geno.tests._script_runner import run_node_code, run_python_code
from geno.typechecker import TypeError as GenoTypeError


@pytest.fixture(params=["python", "js"])
def backend(request):
    if request.param == "python":
        return (
            compile_to_python,
            Compiler,
            partial(run_python_code, python_executable=sys.executable),
        )
    return compile_to_js, JSCompiler, run_node_code


@pytest.mark.parametrize("typecheck", [False, True])
@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "let result = 7\n"
            "func main() -> Int\n"
            "requires result == 7\nensures result == 8\n"
            "return result + 1\nend func\n",
            "8",
        ),
        (
            "let result = 7\n"
            "func main() -> Int\n"
            "ensures all([3], fn(result: Int) -> result == 3)\n"
            "ensures all([1], fn(x: Int) -> result == 8)\n"
            "let read = fn() -> result\nreturn read() + 1\nend func\n",
            "8",
        ),
        (
            "let result = 7\n"
            "func main() -> Int\n"
            "ensures all([3], fn(result: Int) do\n"
            "return result == 3\nend fn)\n"
            "ensures all([1], fn(x: Int) do\n"
            "return result == 8\nend fn)\n"
            "return result + 1\nend func\n",
            "8",
        ),
        (
            "let result = 7\nlet _temp_1 = 3\nlet _temp_2 = 4\n"
            "func main() -> Int\nensures result == 14\n"
            "return result + _temp_1 + _temp_2\nend func\n",
            "14",
        ),
        (
            "let result = 7\n"
            "async func read() -> Int\nreturn result\nend func\n"
            "async func main() -> Int\nensures result == 8\n"
            "return (await read()) + 1\nend func\n",
            "8",
        ),
        (
            "let result = 7\n"
            "func main() -> Float\nensures result == 7.0\n"
            "return result\nend func\n",
            "7.0",
        ),
        (
            "let result = 7\n"
            "func identity(result: Int) -> Int\nexample 4 -> 4\n"
            "return result\nend func\n"
            "func main() -> Int\nlet result = identity(4)\n"
            "return result + 1\nend func\n",
            "5",
        ),
    ],
    ids=[
        "constant",
        "expression-lambdas",
        "block-lambdas",
        "reserved-temps",
        "async",
        "float",
        "shadow",
    ],
)
def test_result_constant_and_contract_scope(backend, typecheck, source, expected):
    compile_fn, _, runner = backend
    interpreted = run(source, RunConfig(check_examples=False))
    assert interpreted.ok, interpreted.diagnostics
    assert str(interpreted.value) == expected
    completed = runner(compile_fn(source, typecheck=typecheck))
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == expected


@pytest.mark.parametrize("typecheck", [False, True])
def test_failed_contract_reports_return_value_not_constant(backend, typecheck):
    source = (
        "let result = 7\nfunc main() -> Int\n"
        "ensures result == 7\nreturn result + 1\nend func\n"
    )
    interpreted = run(source, RunConfig(check_examples=False))
    assert not interpreted.ok
    assert "Postcondition failed" in str(interpreted.diagnostics)
    compile_fn, _, runner = backend
    completed = runner(compile_fn(source, typecheck=typecheck))
    assert completed.returncode != 0
    assert "Postcondition failed" in completed.stderr
    assert "result was 8" in completed.stderr


@pytest.mark.parametrize("binding", ["parameter", "local"])
def test_result_binding_with_ensures_retains_checked_rejection(backend, binding):
    signature = "result: Int" if binding == "parameter" else ""
    local = "let result = 7\n" if binding == "local" else ""
    argument = "7" if binding == "parameter" else ""
    source = (
        f"func add_one({signature}) -> Int\nensures result == 8\n"
        f"example ({argument}) -> 8\n{local}return result + 1\nend func\n"
        f"func main() -> Int\nreturn add_one({argument})\nend func\n"
    )
    compile_fn, _, runner = backend
    with pytest.raises(GenoTypeError, match="`result` is reserved"):
        compile_fn(source)
    # Bypassing static checks must still produce a hygienic result temporary.
    completed = runner(compile_fn(source, typecheck=False))
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "8"


def test_result_constants_remain_private_in_project_contracts(backend):
    graph = SimpleNamespace(
        parsed={
            "Lib": parse(
                "let result = 7\nfunc read() -> Int\n"
                "ensures result == 8\nexample () -> 8\n"
                "return result + 1\nend func\n"
            ),
            "App": parse(
                "import Lib\nlet result = 100\nfunc main() -> Int\n"
                "requires result == 100\nensures result == 9\n"
                "return read() + 1\nend func\n"
            ),
        },
        sorted_modules=["Lib", "App"],
        project=SimpleNamespace(entrypoint="App"),
    )
    _, compiler_type, runner = backend
    completed = runner(compiler_type().compile_project(graph))
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "9"
