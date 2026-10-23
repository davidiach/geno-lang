"""Bounds on generic type parameters: ``[T: Ord]``, ``[T: Num]`` (proposal 0005)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from geno.diagnostics import ErrorCode
from geno.js_compiler import compile_to_js
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

SHAPES = """trait Shape
    func area(self: Self) -> Float
end trait

type Sq = Sq(side: Float)
type Rect = Rect(w: Float, h: Float)
type Circle = Circle(r: Float)

impl Shape for Sq
    func area(self: Sq) -> Float
        example Sq(2.0) -> 4.0
        return self.side * self.side
    end func
end impl

impl Shape for Rect
    func area(self: Rect) -> Float
        example Rect(1.0, 2.0) -> 2.0
        return self.w * self.h
    end func
end impl

func total_area[T: Shape](shapes: List[T]) -> Float
    example [Sq(1.0), Sq(2.0)] -> 5.0
    var total = 0.0
    for s: T in shapes do
        total = total + area(s)
    end for
    return total
end func

"""

LARGEST = """func largest[T: Ord](xs: List[T], fallback: T) -> T
    example [3, 9, 2], 0 -> 9
    example ["b", "a"], "" -> "b"
    var best = fallback
    for x: T in xs do
        if x > best then
            best = x
        end if
    end for
    return best
end func

"""

NUMERIC = """func add_all[T: Num](xs: List[T], zero: T) -> T
    example [1, 2, 3], 0 -> 6
    example [1.5, 2.5], 0.0 -> 4.0
    var acc = zero
    for x: T in xs do
        acc = acc + x
    end for
    return acc
end func

func halve[T: Num](x: T) -> T
    example 7 -> 3
    example 7.0 -> 3.5
    return x / 2
end func

func absolute[T: Num](x: T) -> T
    example -3 -> 3
    example 2.5 -> 2.5
    if x < 0 then
        return -x
    end if
    return x
end func

"""


def _check(source: str) -> None:
    TypeChecker().check_program(parse(source))


def _errors(source: str) -> list[GenoTypeError]:
    with pytest.raises(GenoTypeError) as info:
        _check(source)
    return list(getattr(info.value, "errors", [info.value]))


def _assert_parity(source: str, expected: str) -> None:
    interp = _interpreter_output(source)
    py = _compiled_python_output(source)
    js = _compiled_js_output(source) if HAS_NODE else expected
    _assert_expected_backend_outputs(
        label="bounded generic",
        context=source,
        expected=expected,
        interp_out=interp,
        py_out=py,
        js_out=js,
    )


def test_bounds_are_parsed_per_type_parameter() -> None:
    defn = parse(
        "func f[T: Ord + Hash, U, V: Num](x: T, y: U, z: V) -> T\n"
        "    example 1, 2, 3 -> 1\n"
        "    return x\n"
        "end func\n"
    ).definitions[0]
    assert defn.type_params == ["T", "U", "V"]
    assert defn.type_param_bounds == {"T": ["Ord", "Hash"], "V": ["Num"]}


def test_bounded_bodies_check() -> None:
    _check(SHAPES + LARGEST + NUMERIC)


def test_ord_bound_permits_ordering_and_hashing() -> None:
    _check(
        "func dedupe_sorted[T: Ord](xs: List[T]) -> List[T]\n"
        "    example [2, 1, 2] -> [1, 2]\n"
        "    let seen: Set[T] = set_from_list(xs)\n"
        "    return set_to_list(seen)\n"
        "end func\n"
    )


def test_num_bound_allows_mixing_with_int_and_float() -> None:
    _check(
        "func scale[T: Num](x: T) -> T\n"
        "    example 2 -> 5\n"
        "    return x * 2 + 1\n"
        "end func\n"
        "func ratio[T: Num](x: T) -> Float\n"
        "    example 1 -> 0.5\n"
        "    return x * 0.5\n"
        "end func\n"
    )


@pytest.mark.parametrize(
    ("call", "fragment"),
    [
        ("largest([[1]], [])", "Type List[Int] does not satisfy the bound 'Ord'"),
        ("largest([true], false)", "Type Bool does not satisfy the bound 'Ord'"),
        ('add_all(["a"], "")', "Type String does not satisfy the bound 'Num'"),
        ("total_area([Circle(1.0)])", "add `impl Shape for Circle`"),
    ],
)
def test_calls_must_satisfy_bounds(call: str, fragment: str) -> None:
    errors = _errors(
        SHAPES
        + LARGEST
        + NUMERIC
        + f"func main() -> Unit\n    print({call})\nend func\n"
    )
    matching = [error for error in errors if fragment in error.message]
    assert matching, [error.message for error in errors]
    assert matching[0].error_code == ErrorCode.TYPE_UNSATISFIED_BOUND


def test_examples_must_satisfy_bounds() -> None:
    errors = _errors(
        "func pick[T: Ord](a: T, b: T) -> T\n"
        "    example true, false -> true\n"
        "    return a\n"
        "end func\n"
    )
    assert errors[0].error_code == ErrorCode.TYPE_UNSATISFIED_BOUND


def test_function_values_must_satisfy_bounds() -> None:
    errors = _errors(
        "func pick[T: Ord](a: T, b: T) -> T\n"
        "    example 1, 2 -> 2\n"
        "    if a > b then\n"
        "        return a\n"
        "    end if\n"
        "    return b\n"
        "end func\n"
        "func main() -> Unit\n"
        "    let f: (Bool, Bool) -> Bool = pick\n"
        "    print(f(true, false))\n"
        "end func\n"
    )
    assert [error.error_code for error in errors] == [ErrorCode.TYPE_UNSATISFIED_BOUND]


def test_unbounded_caller_must_declare_the_bound_it_forwards() -> None:
    errors = _errors(
        LARGEST + "func wrap[T](xs: List[T], fallback: T) -> T\n"
        "    example [1], 0 -> 1\n"
        "    return largest(xs, fallback)\n"
        "end func\n"
    )
    assert "declare it on the caller's own parameter: [T: Ord]" in errors[0].message


def test_bounded_caller_may_forward_its_bound() -> None:
    _check(
        LARGEST + "func wrap[T: Ord](xs: List[T], fallback: T) -> T\n"
        "    example [1], 0 -> 1\n"
        "    return largest(xs, fallback)\n"
        "end func\n"
        "func wrap_num[T: Num](xs: List[T], fallback: T) -> T\n"
        "    example [1], 0 -> 1\n"
        "    return largest(xs, fallback)\n"
        "end func\n"
    )


def test_unknown_bound_reports_e322_once() -> None:
    errors = _errors(
        "func f[T: Ordd](x: T) -> T\n    example 1 -> 1\n    return x\nend func\n"
    )
    assert [error.error_code for error in errors] == [ErrorCode.TYPE_UNKNOWN_BOUND]
    assert "Did you mean 'Ord'?" in errors[0].message


@pytest.mark.parametrize(
    ("body", "hint"),
    [
        ("return x + x", "[T: Num]"),
        ("if x > x then return x end if\n    return x", "[T: Ord]"),
    ],
)
def test_missing_bound_errors_name_the_bound_to_add(body: str, hint: str) -> None:
    errors = _errors(
        f"func f[T](x: T) -> T\n    example 1 -> 1\n    {body}\nend func\n"
    )
    assert hint in errors[0].message


def test_two_numeric_parameters_do_not_mix() -> None:
    errors = _errors(
        "func f[T: Num, U: Num](a: T, b: U) -> T\n"
        "    example 1, 2 -> 3\n"
        "    return a + b\n"
        "end func\n"
    )
    assert "Cannot apply '+' to T and U" in errors[0].message


def test_trait_bound_without_impls_cannot_call_other_traits() -> None:
    errors = _errors(
        SHAPES + "trait Named\n"
        "    func name(self: Self) -> String\n"
        "end trait\n"
        "func label[T: Shape](x: T) -> String\n"
        '    example Sq(1.0) -> "x"\n'
        "    return name(x)\n"
        "end func\n"
    )
    assert errors


def test_bounded_generics_run_identically_on_every_backend() -> None:
    _assert_parity(
        SHAPES + LARGEST + NUMERIC + "func main() -> Unit\n"
        "    print(total_area([Rect(1.0, 3.0), Rect(2.0, 2.0)]))\n"
        "    print(largest([1.5, 0.5], 0.0))\n"
        '    print(largest(["pear", "apple"], ""))\n'
        "    print(add_all([2.0, 2.0], 0.0))\n"
        "    print(add_all([1, 2], 0))\n"
        "    print(halve(9))\n"
        "    print(halve(9.0))\n"
        "    print(halve(8.0))\n"
        "    print(absolute(-4.0))\n"
        "    print(absolute(-4))\n"
        "    print(abs(halve(-3.0)))\n"
        "end func\n",
        "7.0\n1.5\npear\n4.0\n3\n4\n4.5\n4.0\n4.0\n4\n1.5\n",
    )


def test_js_erased_numeric_body_divides_as_int() -> None:
    """Instantiations with Float compile their own copy, so the base is Int."""
    js = compile_to_js(
        NUMERIC + "func main() -> Unit\n    print(halve(9) + halve(2))\nend func\n"
    )
    base = js[js.index("function halve(") : js.index("}", js.index("function halve("))]
    assert "_safe_div" in base
    assert "halve$g" not in js


def test_generic_standard_library_math_and_map(tmp_path: Path) -> None:
    program = tmp_path / "Main.geno"
    program.write_text(
        "import Math\n"
        "import Map\n"
        "func main() -> Unit\n"
        "    print(Math.max(2.5, 1.5))\n"
        "    print(Math.abs(-3))\n"
        "    let empty: Map[Int, String] = Map.from_list([])\n"
        '    let m = Map.insert(m: empty, key: 1, value: "one")\n'
        "    print(Map.get(m, 1))\n"
        "end func\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--unsafe",
            "--cap",
            "print",
            str(program),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == '2.5\n3\nSome(value: "one")\n'
