"""Constructor calls accept named arguments, as the spec shows (#135)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from geno.parser import parse
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)
from geno.typechecker import TypeChecker
from geno.types import TypeError as GenoTypeError

REPO_ROOT = Path(__file__).resolve().parents[2]

POINT = "type Point = Point(x: Int, y: Int)\n\n"


def test_spec_section_6_3_example_runs(tmp_path: Path) -> None:
    program = tmp_path / "point.geno"
    program.write_text(
        POINT + "func main() -> Int\n"
        "    var p: Point = Point(x: 0, y: 0)\n"
        "    p.x = 10\n"
        "    return p.x\n"
        "end func\n",
        encoding="utf-8",
    )
    for flags in ([], ["--unsafe"]):
        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", *flags, str(program)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 10, (flags, result.stderr[-400:])


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
def test_named_arguments_place_by_field_and_evaluate_in_source_order() -> None:
    source = """
    type Point = Point(x: Int, y: Int)
    type Mixed = Mixed(f: Float, n: Int)

    func say(s: String, n: Int) -> Int
        example "a", 1 -> 1
        print(s)
        return n
    end func

    func main() -> Unit
        print(Point(x: 1, y: 2))
        print(Point(y: 2, x: 1))
        print(Point(1, y: 2))
        print(Point(y: say("y", 2), x: say("x", 1)))
        print(Mixed(n: 1, f: 2))
        print(Some(value: 5))
        return ()
    end func
    """
    _assert_expected_backend_outputs(
        label="named constructor arguments",
        context=source,
        expected=(
            "Point(x: 1, y: 2)\n"
            "Point(x: 1, y: 2)\n"
            "Point(x: 1, y: 2)\n"
            "y\nx\nPoint(x: 1, y: 2)\n"
            "Mixed(f: 2.0, n: 1)\n"
            "Some(value: 5)\n"
        ),
        interp_out=_interpreter_output(source),
        py_out=_compiled_python_output(source),
        js_out=_compiled_js_output(source),
    )


@pytest.mark.parametrize(
    ("call", "message"),
    [
        ("Point(x: 1, z: 2)", "Constructor Point has no field 'z'"),
        ("Point(x: 1, x: 2)", "Field 'x' is given more than once"),
        ("Point(x: 1)", "Constructor Point is missing field 'y'"),
        ("Point(x: 1, 2)", "Positional argument cannot follow a named argument"),
        ("Point(1, 2, z: 3)", "has no field 'z'"),
    ],
)
def test_bad_named_arguments_are_reported(call: str, message: str) -> None:
    program = parse(POINT + f"func main() -> Unit\n    print({call})\nend func\n")
    with pytest.raises(GenoTypeError, match=message):
        TypeChecker().check_program(program)


def test_positional_constructor_calls_are_unchanged() -> None:
    program = parse(POINT + "func main() -> Unit\n    print(Point(1, 2))\nend func\n")
    TypeChecker().check_program(program)
