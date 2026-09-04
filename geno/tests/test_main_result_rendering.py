"""The entrypoint's return value must be rendered in Geno syntax.

The compiled Python entrypoint used a bare ``print(result)``, so a Geno value
reached the user as Python's ``repr``: a ``Bool`` surfaced as ``True`` rather
than ``true``, and a ``List[String]`` as ``['a', 'b']`` rather than
``["a", "b"]``.  The JS backend already rendered through the Geno formatter,
so the two backends disagreed on the single line a user is most likely to see
-- including in the shipped ``examples/``.

These cases sit outside ``PARITY_PROGRAMS`` because that harness compares
captured ``print`` output, and the interpreter's embedding API does not emit a
result line at all.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from geno.compiler import compile_to_python
from geno.js_compiler import compile_to_js

HAS_NODE = shutil.which("node") is not None

# (return type, expression, expected rendering)
RESULT_CASES = [
    pytest.param("Bool", "true", "true", id="bool_true"),
    pytest.param("Bool", "false", "false", id="bool_false"),
    pytest.param("Int", "42", "42", id="int"),
    pytest.param("List[String]", '["a", "b"]', '["a", "b"]', id="list_of_string"),
    pytest.param("List[Int]", "[1, 2, 3]", "[1, 2, 3]", id="list_of_int"),
    pytest.param("String", '"hi"', "hi", id="top_level_string"),
    pytest.param("List[String]", '["a"]', '["a"]', id="nested_string_stays_quoted"),
]


def _program(return_type: str, expression: str) -> str:
    return f"func main() -> {return_type}\n    return {expression}\nend func main\n"


def _run_compiled(source: str, *, target: str) -> str:
    """Compile and execute, returning the final stdout line."""
    if target == "python":
        code, suffix, argv = compile_to_python(source), ".py", [sys.executable]
    else:
        code, suffix, argv = compile_to_js(source), ".mjs", ["node"]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(code)
        path = handle.name
    try:
        completed = subprocess.run(
            [*argv, path], capture_output=True, text=True, timeout=60, check=False
        )
        assert completed.returncode == 0, completed.stderr[-400:]
        return completed.stdout.strip().splitlines()[-1]
    finally:
        Path(path).unlink(missing_ok=True)


@pytest.mark.parametrize(("return_type", "expression", "expected"), RESULT_CASES)
def test_compiled_python_renders_result_in_geno_syntax(
    return_type: str, expression: str, expected: str
) -> None:
    """`print(result)` would give Python's repr here, not Geno syntax."""
    rendered = _run_compiled(_program(return_type, expression), target="python")
    assert rendered == expected


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
@pytest.mark.parametrize(("return_type", "expression", "expected"), RESULT_CASES)
def test_compiled_js_renders_result_in_geno_syntax(
    return_type: str, expression: str, expected: str
) -> None:
    rendered = _run_compiled(_program(return_type, expression), target="js")
    assert rendered == expected


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
@pytest.mark.parametrize(("return_type", "expression", "expected"), RESULT_CASES)
def test_both_backends_render_the_result_identically(
    return_type: str, expression: str, expected: str
) -> None:
    source = _program(return_type, expression)
    assert _run_compiled(source, target="python") == _run_compiled(source, target="js")


@pytest.mark.parametrize(("return_type", "expression", "expected"), RESULT_CASES)
def test_geno_run_result_line_matches_the_compiled_backend(
    tmp_path: Path, return_type: str, expression: str, expected: str
) -> None:
    """The CLI's `=>` line must agree with the code it just compiled."""
    program = tmp_path / "prog.geno"
    program.write_text(_program(return_type, expression), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "geno", "run", str(program)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-400:]
    assert completed.stdout.strip().splitlines()[-1] == f"=> {expected}"


@pytest.mark.parametrize(("return_type", "expression", "expected"), RESULT_CASES)
def test_geno_run_unsafe_matches_the_compiled_backends(
    tmp_path: Path, return_type: str, expression: str, expected: str
) -> None:
    """`--unsafe` runs the interpreter, and its `=>` line must agree.

    The interpreter's result line called ``_format_value``, which quotes a
    String at every depth, while both compiled runtimes render a *top-level*
    String bare (``_geno_format(_top_level=True)``, and ``_stringifyValue``'s
    ``topLevel`` in the JS runtime).  The same program therefore printed
    ``=> hi`` compiled and ``=> "hi"`` interpreted.  The interpreter's own
    ``print`` already applied the top-level rule inline; only the `=>` line
    missed it.
    """
    program = tmp_path / "prog.geno"
    program.write_text(_program(return_type, expression), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "geno", "run", "--unsafe", str(program)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-400:]
    assert completed.stdout.strip().splitlines()[-1] == f"=> {expected}"


def test_interpreter_display_and_diagnostic_formatting_differ_on_purpose() -> None:
    """A diagnostic keeps its quotes; user-facing output does not."""
    from geno.interpreter import Interpreter

    interpreter = Interpreter()
    assert interpreter.format_display_value("hi") == "hi"
    assert interpreter._format_value("hi") == '"hi"'
    # Nesting is unaffected: only the top level is bare.
    assert interpreter.format_display_value(["a", "b"]) == '["a", "b"]'
