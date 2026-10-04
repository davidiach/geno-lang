"""A record field named with one leading underscore works everywhere (#142)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from geno._runtime_support import Constructor, _GenoVec, get_field
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE = """
type R = R(_x: Int, y: Int)

func bump(r: R) -> R
    example R(1, 2) -> R(2, 2)
    return R(r._x + 1, r.y)
end func

func main() -> Unit
    var r = R(1, 2)
    print(r._x)
    print(r)
    print(bump(r) == R(2, 2))
    r._x = 7
    print(r._x)
    match r with
        | R(a, b) -> print(a + b)
    end match
    let xs = [R(3, 1), R(1, 1)]
    print(xs[1]._x)
    return ()
end func
"""

EXPECTED = "1\nR(_x: 1, y: 2)\ntrue\n7\n9\n1\n"


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
def test_underscore_field_works_on_every_backend() -> None:
    _assert_expected_backend_outputs(
        label="underscore record field",
        context=SOURCE,
        expected=EXPECTED,
        interp_out=_interpreter_output(SOURCE),
        py_out=_compiled_python_output(SOURCE),
        js_out=_compiled_js_output(SOURCE),
    )


def test_underscore_field_works_under_geno_run(tmp_path: Path) -> None:
    program = tmp_path / "record.geno"
    program.write_text(SOURCE, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", str(program)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-400:]
    assert result.stdout == EXPECTED


def test_get_field_still_refuses_private_attributes() -> None:
    class Plain(Constructor):
        __slots__ = ("_hidden",)

    plain = Plain()
    object.__setattr__(plain, "_hidden", 1)
    vec = _GenoVec([1])
    for value, name in [(plain, "_hidden"), (vec, "_elements"), (plain, "__class__")]:
        with pytest.raises(RuntimeError, match="not allowed"):
            get_field(value, name)


NESTED_SOURCE = """
type R = R(_x: Int, y: Int)
type Outer = Outer(_i: R, n: Int)

func main() -> Unit
    var o = Outer(_i: R(_x: 1, y: 2), n: 3)
    let alias = o
    o._i.y = 5
    o._i._x = 4
    print(o)
    print(alias)
    return ()
end func
"""

NESTED_EXPECTED = "Outer(_i: R(_x: 4, y: 5), n: 3)\nOuter(_i: R(_x: 1, y: 2), n: 3)\n"


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
def test_nested_write_through_underscore_field_on_every_backend() -> None:
    _assert_expected_backend_outputs(
        label="nested write through underscore record field",
        context=NESTED_SOURCE,
        expected=NESTED_EXPECTED,
        interp_out=_interpreter_output(NESTED_SOURCE),
        py_out=_compiled_python_output(NESTED_SOURCE),
        js_out=_compiled_js_output(NESTED_SOURCE),
    )


def test_nested_write_through_underscore_field_under_geno_run(tmp_path: Path) -> None:
    program = tmp_path / "nested.geno"
    program.write_text(NESTED_SOURCE, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", str(program)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-400:]
    assert result.stdout == NESTED_EXPECTED
