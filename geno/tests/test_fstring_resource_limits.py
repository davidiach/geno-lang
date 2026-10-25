"""F-strings stop at the collection budget before joining or later effects.

Set GENO_TEST_PYTHON_EXECUTABLE to also validate generated code on an older
standalone Python runtime without installing Geno or pytest into that runtime.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

from geno.api import RunConfig, run
from geno.compiler import CompileError, compile_to_python
from geno.js_compiler import JSCompileError, compile_to_js
from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE
from geno.tests._script_runner import run_node_code, run_python_code


@pytest.fixture(params=["interpreter", "python", "javascript"])
def lane(request: pytest.FixtureRequest) -> str:
    if request.param == "javascript" and shutil.which("node") is None:
        pytest.skip("Node.js is not installed")
    return str(request.param)


def _run(source: str, lane: str, limit: int) -> tuple[bool, str, str]:
    if lane == "interpreter":
        result = run(
            source,
            RunConfig(
                capabilities={"print"},
                max_collection_size=limit,
                timeout=5,
                check_examples=False,
            ),
        )
        return (
            result.ok,
            result.output,
            " | ".join(d.message for d in result.diagnostics),
        )
    if lane == "python":
        completed = run_python_code(
            f"_GENO_MAX_COLLECTION_SIZE = {limit}\n" + compile_to_python(source),
            python_executable=os.environ.get(
                "GENO_TEST_PYTHON_EXECUTABLE", sys.executable
            ),
            args=["--cap", "print"],
            timeout=10,
        )
    else:
        completed = run_node_code(
            f"globalThis.__GENO_MAX_COLLECTION_SIZE = {limit};\n"
            + str(compile_to_js(source)),
            args=["--cap", "print"],
            timeout=10,
        )
    return completed.returncode == 0, completed.stdout, completed.stderr


def test_stops_evaluating_parts_when_combined_limit_is_exceeded(lane: str) -> None:
    source = """func piece(n: Int) -> String
    example 1 -> "abcdefgh"
    print(n)
    return "abcdefgh"
end func
func main() -> String
    return f"{piece(1)}{piece(2)}{piece(3)}"
end func
"""
    ok, output, error = _run(source, lane, 8)
    assert not ok
    assert output == "1\n2\n"
    assert "String size exceeds limit" in error


def test_literal_overflow_stops_before_next_interpolation(lane: str) -> None:
    source = """func effect() -> Int
    example () -> 1
    print(1)
    return 1
end func
func main() -> String
    return f"123456789{effect()}"
end func
"""
    ok, output, error = _run(source, lane, 8)
    assert not ok
    assert output == ""
    assert "String size exceeds limit" in error


@pytest.mark.parametrize(
    ("body", "limit", "expected"),
    [
        ('print(f"{1}{2}{3}")', 3, "123\n"),
        ('print(f"a{1}b")', 3, "a1b\n"),
        ('print(f"")', 0, "\n"),
        ('let emoji = "😀"\nprint(length(f"{emoji}{emoji}"))', 2, "2\n"),
        (r'print(f"\t{true}\n")', 6, "\ttrue\n\n"),
    ],
)
def test_exact_budget_and_formatting(
    lane: str, body: str, limit: int, expected: str
) -> None:
    ok, output, error = _run(
        "func main() -> Unit\n" + body + "\nend func\n", lane, limit
    )
    assert ok, error
    assert output == expected


def test_many_parts_do_not_create_deep_generated_expressions(lane: str) -> None:
    source = (
        'func main() -> Unit\nlet piece = "a"\nprint(f"'
        + "{piece}" * 512
        + '")\nend func\n'
    )
    ok, output, error = _run(source, lane, 512)
    assert ok, error
    assert output == "a" * 512 + "\n"


@pytest.mark.parametrize("limit", [2, 1])
def test_async_interpolation_preserves_order_and_stops_at_limit(
    lane: str, limit: int
) -> None:
    source = """async func piece(n: Int) -> Int
    print(n)
    return n
end func
func main() -> Unit
    print(f"{await piece(1)}{await piece(2)}{await piece(3)}")
end func
"""
    ok, output, error = _run(source, lane, limit)
    assert not ok
    assert output == ("1\n2\n3\n" if limit == 2 else "1\n2\n")
    assert "String size exceeds limit" in error


def test_async_interpolation_can_complete(lane: str) -> None:
    source = """async func piece(n: Int) -> Int
    print(n)
    return n
end func
func main() -> Unit
    print(f"{await piece(1)}{await piece(2)}")
end func
"""
    ok, output, error = _run(source, lane, 2)
    assert ok, error
    assert output == "1\n2\n12\n"


def test_builder_temporary_does_not_shadow_user_local(lane: str) -> None:
    source = """func main() -> Unit
    let _temp_1 = 8
    print(f"{_temp_1}{4}")
end func
"""
    ok, output, error = _run(source, lane, 2)
    assert ok, error
    assert output == "84\n"


def test_interpolation_in_comprehension_iterable(lane: str) -> None:
    source = """func main() -> Unit
    print([c for c in to_chars(f"{1}{2}")])
end func
"""
    ok, output, error = _run(source, lane, 16)
    assert ok, error
    assert output == '["1", "2"]\n'


def test_async_interpolation_in_comprehension_iterable(lane: str) -> None:
    source = """async func piece(n: Int) -> Int
    return n
end func
func main() -> Unit
    print([c for c in to_chars(f"{await piece(1)}{await piece(2)}")])
end func
"""
    ok, output, error = _run(source, lane, 16)
    assert ok, error
    assert output == '["1", "2"]\n'


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('[f"{await piece(n)}" for n in [1, 2]]', '["1", "2"]\n'),
        (
            '[n for n in [1, 2] if length(f"{await piece(n)}") == 1]',
            "[1, 2]\n",
        ),
    ],
)
@pytest.mark.parametrize("lane", ["interpreter", "python"])
def test_async_interpolation_in_comprehension_body_or_filter(
    lane: str, expression: str, expected: str
) -> None:
    # Python 3.10 needs an explicit await in the outer comprehension: an inner
    # async builder comprehension alone only propagates async status on 3.11+.
    source = (
        "async func piece(n: Int) -> Int\nreturn n\nend func\n"
        f"func main() -> Unit\nprint({expression})\nend func\n"
    )
    ok, output, error = _run(source, lane, 16)
    assert ok, error
    assert output == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('f"{[1.0, 2.0]}"', "[1.0, 2.0]\n"),
        ('f"{(1.0, 2)}"', "(1.0, 2)\n"),
        ('f"{Some((1.0, 2))}"', "Some(value: (1.0, 2))\n"),
        ('f"{[true, false]}"', "[true, false]\n"),
    ],
)
def test_bounded_values_retain_static_formatting(
    lane: str, expression: str, expected: str
) -> None:
    source = f"func main() -> Unit\nprint({expression})\nend func\n"
    ok, output, error = _run(source, lane, 64)
    assert ok, error
    assert output == expected


def test_recursive_values_retain_field_types_through_generic_function(
    lane: str,
) -> None:
    source = """type Tree = Leaf(value: Float) | Branch(left: Tree, right: Tree)
func show[T](value: T) -> String
    example 1 -> "1"
    return f"{value}"
end func
func main() -> Unit
    print(show(Branch(Leaf(1.0), Leaf(2.0))))
end func
"""
    ok, output, error = _run(source, lane, 128)
    assert ok, error
    assert output == "Branch(left: Leaf(value: 1.0), right: Leaf(value: 2.0))\n"


def test_single_nested_value_stops_before_next_interpolation(lane: str) -> None:
    source = """func effect() -> Int
    example () -> 1
    print(1)
    return 1
end func
func main() -> String
    let value = range(0, 32)
    return f"{value}{effect()}"
end func
"""
    ok, output, error = _run(source, lane, 32)
    assert not ok
    assert output == ""
    assert "String size exceeds limit" in error


def test_javascript_value_limit_stops_traversal_before_large_formatting() -> None:
    if shutil.which("node") is None:
        pytest.skip("Node.js is not installed")
    probe = """
const writer = new _GenoFString();
const values = new Array(20);
let visits = 0;
let largestPart = 0;
const originalAppend = writer.append.bind(writer);
writer.append = part => { largestPart = Math.max(largestPart, part.length); originalAppend(part); };
for (let i = 0; i < values.length; i++) {
    Object.defineProperty(values, i, {get() { visits += 1; return "abcdefghijklmnopqrst"; }});
}
let error = "";
try { writer.appendValue(values, ["sequence", null]); }
catch (failure) { error = failure.message; }
console.log(JSON.stringify({visits, largestPart, error}));
"""
    completed = run_node_code(
        "globalThis.__GENO_MAX_COLLECTION_SIZE = 32;\n" + JS_RUNTIME_PRELUDE + probe,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    assert observed["visits"] == 2
    assert observed["largestPart"] <= 2
    assert "String size exceeds limit" in observed["error"]


@pytest.mark.parametrize(
    ("compiler", "error"),
    [(compile_to_python, CompileError), (compile_to_js, JSCompileError)],
)
@pytest.mark.parametrize(
    "body", ["return _GenoFString()", "let _GenoFString = 1\nreturn 1"]
)
def test_builder_cannot_be_accessed_or_shadowed(compiler, error, body: str) -> None:
    source = f"func main() -> Any\n{body}\nend func\n"
    with pytest.raises(error, match="reserved runtime name"):
        compiler(source, typecheck=False)
