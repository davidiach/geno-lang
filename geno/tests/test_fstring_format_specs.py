"""F-string format specs: `f"{price:.2f}"` (proposal 0023).

The spec is a portable subset of Python's format mini-language, and Python's
own `format` is the oracle here: every backend must write exactly what it
writes, ties included (compiled JavaScript converts doubles exactly instead
of using `toFixed`, which rounds a tie the other way).
"""

import itertools

import pytest

import geno
from geno._runtime_support import _geno_format_spec
from geno.ast_nodes import FORMAT_OPERATOR, BinaryOp
from geno.diagnostics import ErrorCode
from geno.format_spec import (
    FormatSpecError,
    format_value,
    parse_format_spec,
    value_kind,
)
from geno.formatter import format_source
from geno.parser import parse
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

BACKENDS = [
    _interpreter_output,
    _compiled_python_output,
    pytest.param(
        _compiled_js_output,
        marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
    ),
]

INTS = [0, 7, -7, 42, 255, -255, 1234567, -1234567, 9007199254740991]
INT_SPECS = [
    "d", "5", "<5", ">5", "^5", "^6", "05", "05d", "+d", " d", "+05d", ",", ",d",
    ">12,", "x", "X", "o", "b", "08b", "+x", "*>8", "_^9d", "#<4",
    ".2f", "8.1f", "e", ".3e", ".0%", ",.2f", "+.1f",
]  # fmt: skip
# Ties (0.5, 2.5, 0.125, 1.005 is not one), tiny, huge and negative zero.
FLOATS = [
    0.0, -0.0, 0.5, 1.5, 2.5, -2.5, 0.125, 0.375, 1.005, 3.14159, -3.14159,
    1234567.891, 0.000123456, 123456789012345680.0, 1e-7, 9.995, 99.5, 0.045,
    -0.04, 1e21, 5e-324, 1.7976931348623157e308,
]  # fmt: skip
FLOAT_SPECS = [
    "f", ".0f", ".1f", ".2f", ".3f", ".10f", "8.2f", "<10.1f", "^11.2f", "010.2f",
    "+.2f", " .1f", ",.2f", ">14,.1f", "e", ".0e", ".2e", ".5e", "+.3e", "12.2e",
    "%", ".0%", ".1%", "8.2%", "*>12.3f",
]  # fmt: skip
TEXTS = ["", "a", "héllo", "naïve café", "日本語"]
TEXT_SPECS = ["", "s", "8", "<8", ">8", "^8", "^9", "*^9", ".3", ".3s", "6.2", "->7s"]


def _geno_float(value: float) -> str:
    """A Geno float literal for ``value``; Geno writes no exponents."""
    text = format(value, ".340f").rstrip("0")
    return text + "0" if text.endswith(".") else text


def _program(lines: list[str]) -> str:
    return "func main() -> Unit with io\n" + "".join(lines) + "end func\n"


def _grid() -> tuple[str, str]:
    lines: list[str] = []
    expected: list[str] = []
    for index, value in enumerate(INTS):
        lines.append(f"    let i{index} = {value}\n")
        for spec in INT_SPECS:
            lines.append(f'    print(f"[{{i{index}:{spec}}}]")\n')
            number = float(value) if spec[-1] in "fe%" else value
            expected.append(f"[{format(number, spec)}]")
    for index, value in enumerate(FLOATS):
        lines.append(f"    let x{index}: Float = {_geno_float(value)}\n")
        for spec in FLOAT_SPECS:
            lines.append(f'    print(f"[{{x{index}:{spec}}}]")\n')
            expected.append(f"[{format(value, spec)}]")
    for index, value in enumerate(TEXTS):
        lines.append(f'    let s{index} = "{value}"\n')
        for spec in TEXT_SPECS:
            lines.append(f'    print(f"[{{s{index}:{spec}}}]")\n')
            expected.append(f"[{format(value, spec)}]")
    return _program(lines), "".join(line + "\n" for line in expected)


OTHER = """
type P = P(x: Int)

func main() -> Unit with io
    let n = 42
    let x = 3.14159
    let xs = [1, 2, 3]
    let m = {"a": 1}
    print(f"[{xs:>12}] [{true:>6}] [{P(x: 1):<8}] [{None:5}] [{(1, "a"):^10}]")
    print(f"[{x:10}] [{x:<10}] [{2.0:6}]")
    print(f"[{xs[1:]:>8}] [{max(a: n, b: 7):4}] [{n::>5}] [{m["a"]:03d}]")
    print(f"[{if n > 1 then "big" else "small" end if:>5}] [{f"{n:x}":>4}]")
    print(f"{n:5}{n:<5}|{"a:b":>5}|{n:}|")
end func
"""
OTHER_EXPECTED = (
    '[   [1, 2, 3]] [  true] [P(x: 1) ] [None ] [ (1, "a") ]\n'
    "[   3.14159] [3.14159   ] [   2.0]\n"
    "[  [2, 3]] [  42] [:::42] [001]\n"
    "[  big] [  2a]\n"
    "   4242   |  a:b|42|\n"
)


def _diagnostics(source: str):
    return geno.check(source).diagnostics


def _one(interpolation: str, declare: str = "let n = 42") -> str:
    return _program([f"    {declare}\n", f'    print(f"{interpolation}")\n'])


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_formats_like_python(backend):
    source, expected = _grid()
    actual = backend(source)
    if actual != expected:
        pairs = zip(actual.splitlines(), expected.splitlines())
        wrong = [f"{got} != {want}" for got, want in pairs if got != want]
        pytest.fail(f"{len(wrong)} differ, first: {wrong[:5]}")


@pytest.mark.parametrize("backend", BACKENDS)
def test_specs_on_other_values_and_inside_larger_interpolations(backend):
    """Text padding of any value, and colons that are not the spec's.

    A Float without a type is padded as `to_string` writes it, right-aligned
    like any number. A named argument, a slice, a map index and a string
    holding a colon do not start a spec.
    """
    assert backend(OTHER) == OTHER_EXPECTED


def test_the_python_prelude_and_the_interpreter_agree_on_every_spec():
    cases = itertools.chain(
        ((value, spec, "int") for value in INTS for spec in INT_SPECS[:23]),
        ((value, spec, "float") for value in FLOATS for spec in FLOAT_SPECS),
        ((value, spec, "text") for value in TEXTS for spec in TEXT_SPECS),
    )
    for value, spec, kind in cases:
        assert _geno_format_spec(value, spec, kind) == format_value(value, spec, kind)
        assert _geno_format_spec(value, spec) == format_value(value, spec, kind)


def test_an_interpolation_with_a_spec_is_a_format_operation():
    program = parse(_one("{n:>5}"))
    part = program.definitions[0].body[1].expression.arguments[0].value.parts[0]
    assert isinstance(part, BinaryOp)
    assert part.operator == FORMAT_OPERATOR
    assert part.left.name == "n"
    assert part.right.value == ">5"


@pytest.mark.parametrize(
    ("interpolation", "declare", "message"),
    [
        ("{n:zz}", "let n = 42", "Invalid format spec 'zz'"),
        ("{n:g}", "let n = 4.2", "Unknown format type 'g'"),
        ("{n:d}", "let n = 4.2", "needs an Int; use 'f' or 'e' for a Float"),
        ("{n:x}", 'let n = "a"', "Format type 'x' in spec 'x' needs an Int"),
        ("{n:.2f}", 'let n = "a"', "needs an Int or a Float"),
        ("{n:.2}", "let n = 4.2", "needs a type for a Float: add f, e or %"),
        ("{n:+}", "let n = 4.2", "needs a type for a Float"),
        ("{n:.2}", "let n = 42", "gives a precision to an Int; write '.2f'"),
        ("{n:.2d}", "let n = 42", "gives a precision to an integer type"),
        ("{n:,x}", "let n = 42", "groups digits with ',' under type 'x'"),
        ("{n:+}", 'let n = "a"', "a sign, 0 or ',' on a value that is not a number"),
        ("{n:05s}", 'let n = "a"', "uses a sign, 0 or ',' with type 's'"),
        ("{n:<05d}", "let n = 42", "both an alignment and a 0 flag"),
        ("{n:0,d}", "let n = 42", "combines the 0 flag with ','"),
        ("{n:2000}", "let n = 42", "over the limit of 1000"),
        ("{n:.200f}", "let n = 42", "over the limit of 100"),
    ],
)
def test_a_spec_that_does_not_fit_is_reported_with_its_own_code(
    interpolation, declare, message
):
    diagnostics = _diagnostics(_one(interpolation, declare))
    assert [d.code for d in diagnostics] == [ErrorCode.TYPE_FORMAT_SPEC]
    assert message in diagnostics[0].message


def test_the_error_points_at_the_interpolation():
    (diagnostic,) = _diagnostics(_one("ab {n:zz}"))
    assert (diagnostic.location.line, diagnostic.location.column) == (3, 17)


def test_the_checker_names_how_each_value_is_formatted():
    spec = parse_format_spec("8")
    assert value_kind(spec, "int", "8") == "int"
    assert value_kind(spec, "float", "8") == "text"
    assert value_kind(spec, "text", "8") == "text"
    assert value_kind(parse_format_spec(".1f"), "int", ".1f") == "float"
    with pytest.raises(FormatSpecError):
        value_kind(parse_format_spec("d"), "text", "d")


def test_an_empty_interpolation_before_a_spec_is_still_an_error():
    with pytest.raises(geno.parser.ParseError, match="Empty expression in f-string"):
        parse(_one("{:>5}"))


def test_the_formatter_leaves_specs_alone():
    source = _one("[{n:>5}] [{n:05d}] [{n::^7}]")
    assert format_source(source) == source
