"""``json_decode`` of an integer literal past JavaScript's safe range.

A Float accepts any JSON number (proposal 0021), so ``json_decode[Float]``
of ``9007199254740993`` is ``Ok(9007199254740992.0)`` on every backend.
Compiled JavaScript used to reject the literal while parsing, before the
target type was known (0.7 review). An Int or a JsonValue still cannot hold
such a value there, which stays the documented JavaScript limit.
"""

import pytest

from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

needs_node = pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")

PROGRAM = """
func main() -> Unit with io
    let a: Result[Float, String] = json_decode("9007199254740993")
    print(a)
    let b: Result[List[Float], String] = json_decode("[1, 18446744073709551616]")
    print(b)
    let c: Result[Map[String, Float], String] = json_decode("{\\"a\\": -123456789012345678901234567890}")
    print(c)
    let d: Result[Float, String] = json_decode("1" + string_repeat("0", 400))
    print(d)
end func
"""

EXPECTED = (
    "Ok(value: 9007199254740992.0)\n"
    "Ok(value: [1.0, 1.8446744073709552e+19])\n"
    'Ok(value: {"a": -1.2345678901234568e+29})\n'
    'Err(error: "$: expected Float, got an Int out of its range")\n'
)


@pytest.mark.parametrize(
    "backend",
    [
        _interpreter_output,
        _compiled_python_output,
        pytest.param(_compiled_js_output, marks=needs_node),
    ],
)
def test_a_float_takes_an_integer_literal_of_any_size(backend):
    assert backend(PROGRAM) == EXPECTED


@needs_node
def test_js_still_refuses_a_wide_int_for_an_int_or_a_json_value():
    source = """
func main() -> Unit with io
    let a: Result[Int, String] = json_decode("9007199254740993")
    print(a)
    let b: Result[List[JsonValue], String] = json_decode("[[18446744073709551616]]")
    print(b)
    print(json_parse("18446744073709551616"))
end func
"""
    refused = 'Err(error: "json_parse integer literal exceeds JavaScript safe integer range")\n'
    assert _compiled_js_output(source) == refused * 3


@needs_node
def test_a_wide_int_in_a_large_json_value_is_an_err_not_a_crash():
    """The wide-integer check walks a large array without spreading it into
    one call, which overflowed V8's argument limit (PR review)."""
    source = """
func main() -> Unit with io
    let text = "[9007199254740993," + string_repeat("0,", 150000) + "0]"
    let decoded: Result[JsonValue, String] = json_decode(text)
    print(decoded)
end func
"""
    assert _compiled_js_output(source) == (
        'Err(error: "json_parse integer literal exceeds JavaScript safe integer range")\n'
    )


@pytest.mark.parametrize(
    "backend",
    [
        "interpreter",
        "python",
        pytest.param("js", marks=needs_node),
    ],
)
@pytest.mark.parametrize(
    ("zeros", "expected_error"),
    [
        (10000, "Integer exceeds maximum size (33220 bits)"),
        (20000, "Integer exceeds maximum size (more than 33219 bits)"),
    ],
)
def test_json_decode_float_enforces_integer_bit_limit_across_backends(
    backend, zeros, expected_error
):
    from geno import RunConfig, run

    source = f"""
func main() -> Unit with io
    let r: Result[Float, String] = json_decode("1" + string_repeat("0", {zeros}))
    print(r)
end func
"""
    if backend == "interpreter":
        result = run(source, config=RunConfig(capabilities={"print"}))
        assert not result.ok
        assert expected_error in result.diagnostics[0].message
    elif backend == "python":
        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            _compiled_python_output(source)
    else:
        with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
            _compiled_js_output(source)
