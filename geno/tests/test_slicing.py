"""Slices of a List or a String: `xs[a:b]`, `xs[a:]`, `xs[:b]`, `xs[:]` (proposal 0023).

A slice follows Python's rule on every backend: a negative bound counts from
the end, a bound past either end is clamped, and a String slices by code
point. It therefore never fails at run time. The parser represents it as a
binary operation on the target and its pair of bounds.
"""

import pytest

import geno
from geno.ast_nodes import SLICE_OPERATOR, BinaryOp, IndexAccess, slice_bounds
from geno.formatter import format_source
from geno.parser import ParseError, parse
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

XS = [1, 2, 3, 4, 5]
TEXT = "héllo wörld"
# (written bounds, Python slice) pairs; Python's own slicing is the oracle.
BOUNDS = [
    ("1:3", slice(1, 3)),
    (":2", slice(None, 2)),
    ("3:", slice(3, None)),
    (":", slice(None, None)),
    ("-2:", slice(-2, None)),
    (":-1", slice(None, -1)),
    ("-3:-1", slice(-3, -1)),
    ("3:99", slice(3, 99)),
    ("-99:2", slice(-99, 2)),
    ("99:", slice(99, None)),
    (":-99", slice(None, -99)),
    ("4:2", slice(4, 2)),
    ("0:0", slice(0, 0)),
]


def _geno_list(values: list[int]) -> str:
    return "[" + ", ".join(str(value) for value in values) + "]"


def _program() -> tuple[str, str]:
    lines = [f"    let xs = {_geno_list(XS)}", f'    let s = "{TEXT}"']
    expected = []
    for written, python_slice in BOUNDS:
        lines.append(f"    print(xs[{written}])")
        expected.append(_geno_list(XS[python_slice]))
        lines.append(f"    print(s[{written}])")
        expected.append(TEXT[python_slice])
    source = "func main() -> Unit with io\n" + "\n".join(lines) + "\nend func\n"
    return source, "".join(line + "\n" for line in expected)


EXTRA = """
func middle(xs: List[Int]) -> List[Int]
    example [1, 2, 3, 4] -> [2, 3]
    example [1] -> []
    example [] -> []
    return xs[1:-1]
end func

func count(n: Int) -> Int
    example 1 -> 1
    print(n)
    return n
end func

func main() -> Unit with io
    let xs = [1, 2, 3, 4, 5]
    let i = 1
    print(xs[i:i + 2])
    print(middle(xs))
    print([[1, 2], [3, 4], [5]][1:][0][:1])
    print(f"{xs[1:3]} and {"abc"[0:1]}")
    print(xs[count(1):count(3)])
    print(length(xs[:0]))
    print(xs)
end func
"""
EXTRA_EXPECTED = (
    "[2, 3]\n[2, 3, 4]\n[3]\n[2, 3] and a\n1\n3\n[2, 3]\n0\n[1, 2, 3, 4, 5]\n"
)


def _messages(source: str) -> list[str]:
    return [d.message for d in geno.check(source).diagnostics]


def _function(statement: str) -> str:
    return (
        "func f(xs: List[Int], s: String, a: Array[Int], m: Map[String, Int]) -> Int\n"
        '    example [1], "a", array_new(1, 0), {"a": 1} -> 0\n'
        f"    {statement}\n"
        "    return 0\n"
        "end func\n"
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_slices_like_python(backend):
    source, expected = _program()
    assert backend(source) == expected


@pytest.mark.parametrize("backend", BACKENDS)
def test_slices_compose_with_the_rest_of_the_language(backend):
    """Variables and calls as bounds, chained slices, slices in f-strings.

    The bounds are evaluated once, start before stop, and the list is not
    changed by slicing it.
    """
    assert backend(EXTRA) == EXTRA_EXPECTED


def test_the_examples_of_a_slicing_function_hold():
    assert _messages(EXTRA) == []


def test_a_slice_is_a_binary_operation_on_the_target_and_its_bounds():
    program = parse(_function("let y = xs[1:]"))
    value = program.definitions[0].body[0].value
    assert isinstance(value, BinaryOp)
    assert value.operator == SLICE_OPERATOR
    start, stop = slice_bounds(value)
    assert start is not None and start.value == 1
    assert stop is None


def test_a_single_index_is_still_an_index():
    program = parse(_function("let y = xs[1]"))
    assert isinstance(program.definitions[0].body[0].value, IndexAccess)


def test_a_slice_keeps_the_type_of_what_it_slices():
    assert _messages(_function("let y: List[Int] = xs[1:]")) == []
    assert _messages(_function("let y: String = s[:2]")) == []
    assert any(
        "declared type String, but the value has type List[Int]" in message
        for message in _messages(_function("let y: String = xs[1:]"))
    )


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ('let y = xs["a":2]', "Slice start must be Int, got String"),
        ("let y = xs[:1.5]", "Slice stop must be Int, got Float"),
        ("let y = a[0:1]", "Cannot slice Array[Int]: a slice takes part of a List"),
        ("let y = m[0:1]", "Cannot slice Map[String, Int]"),
        ("let y = (1, 2)[0:1]", "Cannot slice (Int, Int)"),
    ],
)
def test_what_cannot_be_sliced_is_a_type_error(statement, message):
    assert any(message in found for found in _messages(_function(statement)))


@pytest.mark.parametrize("statement", ["let y = xs[::2]", "let y = xs[1:2:3]"])
def test_a_step_is_rejected_with_the_alternatives(statement):
    with pytest.raises(ParseError, match=r"takes no step.*reverse\(xs\)"):
        parse(_function(statement))


def test_a_slice_cannot_be_assigned_to():
    with pytest.raises(ParseError, match="Invalid assignment target"):
        parse(_function("xs[0:1] = [2]"))


def test_the_formatter_writes_a_slice_without_spaces_around_its_colon():
    untidy = _function("let y = xs[ 1 : -1 ][ : 2]")
    tidy = _function("let y = xs[1:-1][:2]")
    assert format_source(untidy) == tidy
    assert format_source(tidy) == tidy


def test_the_formatter_still_spaces_bounds_annotations_and_named_arguments():
    source = (
        "func pick[T: Ord](xs: List[T], m: Map[String, Int]) -> List[T]\n"
        '    example [1], {"a": 1} -> [1]\n'
        '    return pick(xs: xs[:], m: m)[0:m["a"]]\n'
        "end func\n"
    )
    assert format_source(source) == source
