"""Resource limits are enforced before expanding shared values or types."""

import json
import re
import sys

import pytest

from geno import RunConfig, run
from geno.compiler import compile_to_python
from geno.js_compiler import compile_to_js
from geno.tests._script_runner import run_node_code, run_python_code
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)
from geno.types import GenoTypeError

_DAG = """type Tree = Leaf | Branch(left: Tree, right: Tree)

func main() -> String
    var tree: Tree = Leaf
    for _i in range(0, 18) do
        tree = Branch(tree, tree)
    end for
    return ENCODE(tree)
end func
"""

_RAW_DAG = """func main() -> String
    var tree: JsonValue = JsonNull
    for _i in range(0, 18) do
        tree = JsonArray([tree, tree])
    end for
    return ENCODE(tree)
end func
"""

_BACKENDS = [
    "interpreter",
    "python",
    pytest.param(
        "js", marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    ),
]


def _run_with_limit(source: str, backend: str, limit: int) -> tuple[bool, str]:
    if backend == "interpreter":
        result = run(
            source,
            config=RunConfig(max_collection_size=limit),
        )
        return result.ok, " ".join(
            diagnostic.message for diagnostic in result.diagnostics
        )
    if backend == "python":
        process = run_python_code(
            f"_GENO_MAX_COLLECTION_SIZE = {limit}\n" + compile_to_python(source),
            python_executable=sys.executable,
            timeout=10,
        )
    else:
        process = run_node_code(
            f"globalThis.__GENO_MAX_COLLECTION_SIZE = {limit};\n"
            + str(compile_to_js(source))
        )
    return process.returncode == 0, process.stderr


@pytest.mark.parametrize(
    "call",
    ["json_encode(tree)", "json_encode_pretty(tree, 2)"],
)
@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize("dag", [_DAG, _RAW_DAG], ids=["adt", "json-value"])
def test_shared_json_output_is_rejected_during_the_walk(backend, call, dag):
    source = dag.replace("ENCODE(tree)", call)
    ok, error = _run_with_limit(source, backend, 1000)
    assert not ok
    assert "String size exceeds limit" in error
    # The lower bound crosses the cap by at most one node or key.
    # Checking only after expansion reports the actual multi-megabyte size.
    size = re.search(r"\((\d+) > 1000\)", error)
    assert size is not None and int(size[1]) < 1040


@pytest.mark.parametrize("backend", _BACKENDS)
def test_raw_json_nesting_is_bounded(backend):
    source = _RAW_DAG.replace("range(0, 18)", "range(0, 129)")
    source = source.replace("[tree, tree]", "[tree]").replace(
        "ENCODE(tree)", "json_encode(tree)"
    )
    ok, error = _run_with_limit(source, backend, 10000)
    assert not ok
    assert "value nested too deeply (max 128)" in error


@pytest.mark.parametrize("backend", _BACKENDS)
def test_synthesized_option_wrappers_consume_a_decode_budget(backend):
    wrapper = "Option[" * 40 + "Int" + "]" * 40
    source = f"""func main() -> Result[List[{wrapper}], String]
    return json_decode[List[{wrapper}]]("[1,2,3]")
end func
"""
    ok, error = _run_with_limit(source, backend, 100)
    assert not ok
    assert "Decoded JSON size exceeds limit (101 > 100)" in error


@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize(("options", "should_succeed"), [(1, True), (4, False)])
def test_decode_bounds_typed_depth_without_reducing_normal_json_depth(
    backend, options, should_succeed
):
    fields = "Option[" * options + "Chain" + "]" * options
    body = json.dumps('{"next":' * 128 + "null" + "}" * 128)
    source = f"""type Chain = Chain(next: {fields})

func main() -> Unit with io
    match json_decode[Chain]({body}) with
        | Ok(_) -> print("ok")
        | Err(message) -> print(message)
    end match
end func
"""
    runner = {
        "interpreter": _interpreter_output,
        "python": _compiled_python_output,
        "js": _compiled_js_output,
    }[backend]
    output = runner(source)
    expected = "ok" if should_succeed else "$: decoded value nested too deeply"
    assert output.strip() == expected


def test_typed_json_encoding_charges_interpreter_steps():
    result = run(
        _DAG.replace("ENCODE(tree)", "json_encode(tree)"),
        config=RunConfig(max_steps=1000),
    )
    assert not result.ok
    [diagnostic] = result.diagnostics
    assert "Step limit exceeded" in diagnostic.message


@pytest.mark.parametrize(
    "definitions",
    [
        "type Nest[T] = Stop | More(next: Nest[List[T]])",
        "type Nest[T] = Stop | More(next: Nest[(T, T)])",
        "\n".join(
            [
                "type Nest[T] = Stop | More(next: X0[T])",
                *(f"type X{i}[T] = Wrap{i}(next: X{i + 1}[(T, T)])" for i in range(16)),
                "type X16[T] = Last(value: T)",
            ]
        ),
    ],
)
def test_expanding_json_types_report_a_type_error(definitions):
    source = (
        definitions
        + """

func main() -> String
    let value: Nest[Int] = Stop
    return json_encode(value)
end func
"""
    )
    result = run(source)
    assert not result.ok
    [diagnostic] = result.diagnostics
    assert diagnostic.code.value == "E332"
    assert "too complex" in diagnostic.message
    for compile_source in (compile_to_python, compile_to_js):
        with pytest.raises(GenoTypeError, match="too complex"):
            compile_source(source)
