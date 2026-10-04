"""A value typed Float is a Float on every backend (#137)."""

from __future__ import annotations

import pytest

from geno.float_promotion import float_shape
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)
from geno.types import (
    FloatType,
    IntType,
    ListType,
    OptionType,
    ResultType,
    StringType,
    TupleType,
)

SOURCE = """
func mk(n: Int) -> Option[Float]
    example 1 -> Some(1.0)
    return Some(n)
end func

func ok(n: Int) -> Result[Float, String]
    example 1 -> Ok(1.0)
    return Ok(n)
end func

func pair(n: Int) -> (Float, Int)
    example 1 -> (1.0, 1)
    return (n, n)
end func

func main() -> Unit
    let direct: List[Float] = [7]
    let ints: List[Int] = [7]
    let aliased: List[Float] = ints
    var nested: List[List[Float]] = [ints]
    nested = [ints, ints]
    let f: (Int) -> Float = fn(x: Int) -> x
    let g: Float = f(7)
    print(direct)
    print(aliased)
    print(nested)
    print(f(7))
    print(g)
    print(direct == aliased)
    print(mk(4))
    print(ok(5))
    print(pair(6))
    for x: Float in [1, 2] do
        print(x)
    end for
    for y: Float in ints do
        print(y)
    end for
    print(max(3, 2.5))
    print(max(2.5, 3))
    print(max(3, 4))
    print(math_max(3, 2.5))
    print(math_min(2, 2.5))
    return ()
end func
"""

EXPECTED = (
    "[7.0]\n[7.0]\n[[7.0], [7.0]]\n7.0\n7.0\ntrue\n"
    "Some(value: 4.0)\nOk(value: 5.0)\n(6.0, 6)\n"
    "1.0\n2.0\n7.0\n3.0\n3.0\n4\n3.0\n2.0\n"
)


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
def test_float_typed_values_print_as_floats_on_every_backend() -> None:
    _assert_expected_backend_outputs(
        label="Int in a Float slot",
        context=SOURCE,
        expected=EXPECTED,
        interp_out=_interpreter_output(SOURCE),
        py_out=_compiled_python_output(SOURCE),
        js_out=_compiled_js_output(SOURCE),
    )


@pytest.mark.parametrize(
    ("expected", "shape"),
    [
        (IntType(), None),
        (FloatType(), "F"),
        (ListType(IntType()), None),
        (ListType(ListType(FloatType())), ("L", ("L", "F"))),
        (OptionType(FloatType()), ("O", "F")),
        (ResultType(StringType(), FloatType()), ("R", None, "F")),
        (TupleType((FloatType(), IntType())), ("T", ("F", None))),
    ],
)
def test_float_shape(expected, shape) -> None:
    assert float_shape(expected) == shape
