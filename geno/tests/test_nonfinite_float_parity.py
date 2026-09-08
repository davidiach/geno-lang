"""Non-finite Float output stays canonical across runtime backends."""

import pytest

from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)


@pytest.mark.parametrize(
    "backend",
    [
        _interpreter_output,
        _compiled_python_output,
        pytest.param(
            _compiled_js_output,
            marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
        ),
    ],
)
def test_nonfinite_float_output_and_generic_stringification(backend):
    # Start with a finite literal so this tests runtime arithmetic/formatting,
    # independently of compiler support for literals that overflow at parse time.
    large = "1" + "0" * 200 + ".0"
    source = f"""
func main() -> Unit
    let large: Float = {large}
    let positive: Float = large * large
    let negative: Float = -positive
    let not_number: Float = positive - positive
    print(positive)
    print(negative)
    print(not_number)
    print([positive, negative, not_number])
    print(to_string([positive, negative, not_number]))
    print(map([positive, negative, not_number], to_string))
    return ()
end func
"""
    assert backend(source) == (
        'inf\n-inf\nnan\n[inf, -inf, nan]\n[inf, -inf, nan]\n["inf", "-inf", "nan"]\n'
    )
