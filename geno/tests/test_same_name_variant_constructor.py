"""A variant named like its multi-variant type stays a constructor (#128)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE = """
type Shape = Shape(n: Int) | Other
type Box[T] = Box(item: T) | NoBox

func size(s: Shape) -> Int
    example Other -> 0
    return match s with
        | Shape(n) -> n
        | Other -> 0
    end match
end func

func main() -> Unit
    print(Shape(1))
    print(Other)
    print(size(Shape(4)))
    let b: Box[Int] = Box(3)
    print(b)
    return ()
end func
"""

EXPECTED = "Shape(n: 1)\nOther\n4\nBox(item: 3)\n"


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
def test_same_name_variant_constructs_on_every_backend() -> None:
    _assert_expected_backend_outputs(
        label="same-name variant constructor",
        context=SOURCE,
        expected=EXPECTED,
        interp_out=_interpreter_output(SOURCE),
        py_out=_compiled_python_output(SOURCE),
        js_out=_compiled_js_output(SOURCE),
    )


def test_same_name_variant_constructs_under_geno_run(tmp_path: Path) -> None:
    program = tmp_path / "shape.geno"
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


def test_annotations_name_the_whole_type() -> None:
    import types
    import typing

    from geno.compiler import compile_to_python

    module = types.ModuleType("geno_same_name_hints")
    sys.modules[module.__name__] = module
    try:
        exec(compile(compile_to_python(SOURCE), "<geno>", "exec"), module.__dict__)
        hints = typing.get_type_hints(module.size)
    finally:
        del sys.modules[module.__name__]
    assert set(typing.get_args(hints["s"])) == {module.Shape, module.Other}
