"""Backend regressions reported while qualifying Geno 0.6 RC.1."""

import json
import shutil
import sys
from typing import cast

import pytest

from geno.api import RunConfig, run
from geno.compiler import compile_to_python
from geno.js_compiler import JSCompiler, compile_to_js
from geno.parser import parse
from geno.tests._script_runner import run_node_code, run_python_code
from geno.typechecker import TypeChecker


def _output(source: str, backend: str) -> str:
    if backend == "interpreter":
        result = run(source, config=RunConfig(capabilities={"print"}))
        assert result.ok, result.diagnostics
        return cast(str, result.output)
    if backend == "python":
        completed = run_python_code(
            compile_to_python(source),
            python_executable=sys.executable,
            args=("--cap", "print"),
        )
    else:
        if shutil.which("node") is None:
            pytest.skip("Node.js not available")
        completed = run_node_code(compile_to_js(source), args=("--cap", "print"))
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    return cast(str, completed.stdout)


@pytest.mark.parametrize("backend", ["interpreter", "python", "javascript"])
@pytest.mark.parametrize("block", [False, True], ids=["expression", "block"])
def test_nested_lambda_captures_its_factory_parameter(backend, block):
    inner = "fn() do\n        return x\n    end fn" if block else "fn() -> x"
    source = f"""
func main() -> Unit
    let make: (Int) -> () -> Int = fn(x: Int) -> {inner}
    let first: () -> Int = make(7)
    let second: () -> Int = make(11)
    print(first())
    print(second())
    print(first())
    return ()
end func
"""
    assert _output(source, backend) == "7\n11\n7\n"


@pytest.mark.parametrize("backend", ["interpreter", "python", "javascript"])
@pytest.mark.parametrize("condition", ["", " if x > 0"])
def test_comprehension_block_lambdas_capture_each_iteration(backend, condition):
    source = f"""
func main() -> Unit
    let fs: List[() -> Int] = [fn() do
        return x
    end fn for x: Int in [1, 2, 3]{condition}]
    print(fs[0]())
    print(fs[1]())
    print(fs[2]())
    return ()
end func
"""
    assert _output(source, backend) == "1\n2\n3\n"


@pytest.mark.parametrize("backend", ["interpreter", "python", "javascript"])
def test_nested_block_lambda_preserves_live_mutable_capture(backend):
    source = """
func main() -> Unit
    var total: Int = 0
    let make = fn(step: Int) -> fn() do
        total = total + step
        return total
    end fn
    let first = make(2)
    let second = make(3)
    print(first())
    print(second())
    print(first())
    return ()
end func
"""
    assert _output(source, backend) == "2\n5\n7\n"


def test_inline_block_lambda_source_map_keeps_following_statement_lines():
    source = """func main() -> Unit
    let make: (Int) -> () -> Int = fn(x: Int) -> fn() do
        return x
    end fn
    let f = make(7)
    print(f())
    return ()
end func
"""
    program = parse(source, "nested.geno")
    TypeChecker().check_program(program)
    compiler = JSCompiler(track_source_map=True)
    generated = compiler.compile(program).splitlines()
    expected = {2: "return x;", 4: "const f =", 5: "print_("}
    for source_line, statement in expected.items():
        mapped = [
            generated[out_line]
            for out_line, _, _, line, _ in compiler._mappings
            if line == source_line
        ]
        assert len(mapped) == 1
        assert statement in mapped[0]


def test_sibling_block_lambda_source_maps_follow_expression_placement():
    source = """func main() -> Unit
    let fs = [fn() do
        return 101
    end fn, fn() do
        return 202
    end fn]
    print(fs[1]())
end func
"""
    program = parse(source, "siblings.geno")
    TypeChecker().check_program(program)
    compiler = JSCompiler(track_source_map=True)
    code = compiler.compile(program)
    assert "\0" not in code
    generated = code.splitlines()
    expected = {2: "(101)", 4: "(202)", 6: "print_("}
    for source_line, statement in expected.items():
        mapped = [
            generated[out_line]
            for out_line, _, _, line, _ in compiler._mappings
            if line == source_line
        ]
        assert len(mapped) == 1
        assert statement in mapped[0]


@pytest.mark.parametrize("backend", ["interpreter", "python", "javascript"])
@pytest.mark.parametrize("indent", [0, 2, 12])
def test_pretty_json_preserves_tagged_values_and_key_order(backend, indent):
    source = f"""
func main() -> Unit
    let value: JsonValue = JsonObject([
        ("__proto__", JsonString("metadata")),
        ("2", JsonFloat(1.0)),
        ("1", JsonArray([JsonFloat(-0.0), JsonObject([("key", JsonString("text"))])]))
    ])
    let text: String = json_stringify_pretty(value, {indent})
    print(text)
    print(json_parse(text) == Ok(value))
    return ()
end func
"""
    value = {"__proto__": "metadata", "2": 1.0, "1": [-0.0, {"key": "text"}]}
    expected = json.dumps(
        value,
        indent=min(indent, 10) if indent > 0 else None,
        ensure_ascii=False,
        separators=(",", ":") if indent <= 0 else None,
    )
    assert _output(source, backend) == expected + "\ntrue\n"


@pytest.mark.parametrize("backend", ["interpreter", "python", "javascript"])
def test_pretty_json_preserves_duplicate_key_replacement(backend):
    source = """
func main() -> Unit
    let value = JsonObject([("2", JsonInt(1)), ("1", JsonInt(2)), ("2", JsonFloat(3.0))])
    print(json_stringify_pretty(value, 0))
    return ()
end func
"""
    assert _output(source, backend) == '{"2":3.0,"1":2}\n'
