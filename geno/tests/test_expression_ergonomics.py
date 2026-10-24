"""If expressions, or-patterns, tuple fields and map literals (proposal 0008)."""

from __future__ import annotations

import pytest

from geno.ast_nodes import IndexAccess, MapLiteral, MatchExpr
from geno.parser import ParseError, ParseErrors, parse
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)
from geno.typechecker import TypeChecker
from geno.types import TypeError as GenoTypeError


def _check(source: str) -> None:
    TypeChecker().check_program(parse(source))


def _errors(source: str) -> list[GenoTypeError]:
    with pytest.raises(GenoTypeError) as info:
        _check(source)
    return list(getattr(info.value, "errors", [info.value]))


def _main(body: str) -> str:
    return "func main() -> Unit\n" + body + "end func\n"


def _first_value(source: str) -> object:
    return parse(source).definitions[0].body[0].value


def _assert_parity(source: str, expected: str) -> None:
    interp = _interpreter_output(source)
    py = _compiled_python_output(source)
    js = _compiled_js_output(source) if HAS_NODE else expected
    _assert_expected_backend_outputs(
        label="expression ergonomics",
        context=source,
        expected=expected,
        interp_out=interp,
        py_out=py,
        js_out=js,
    )


# ---------------------------------------------------------------------------
# If expressions
# ---------------------------------------------------------------------------


def test_if_expression_is_a_match_on_bool() -> None:
    value = _first_value(_main('    let x = if true then "a" else "b" end if\n'))
    assert isinstance(value, MatchExpr)
    assert value.from_if
    assert [arm.pattern.value for arm in value.arms] == [True, False]


def test_else_if_chains_share_one_end_if() -> None:
    _check(
        "func label(score: Int) -> String\n"
        '    example 95 -> "excellent"\n'
        "    return if score >= 90 then\n"
        '        "excellent"\n'
        "    else if score >= 60 then\n"
        '        "passing"\n'
        "    else\n"
        '        "needs work"\n'
        "    end if\n"
        "end func\n"
    )


def test_if_expression_needs_an_else() -> None:
    with pytest.raises((ParseError, ParseErrors), match="needs an else branch"):
        parse(_main("    let x = if true then 1 end if\n"))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("let x = if 1 then 2 else 3 end if", "If condition must be Bool, got Int"),
        (
            'let x = if true then 2 else "x" end if',
            "If branch type mismatch: expected Int, got String",
        ),
    ],
)
def test_if_expression_type_errors(body: str, message: str) -> None:
    assert _errors(_main(f"    {body}\n"))[0].message == message


# ---------------------------------------------------------------------------
# Or-patterns
# ---------------------------------------------------------------------------

COLOR = "type Color = Red | Orange | Green | Blue\n"


def test_or_pattern_expands_to_one_arm_per_alternative() -> None:
    program = parse(
        COLOR + "func warmth(c: Color) -> String\n"
        '    example Red -> "warm"\n'
        "    return match c with\n"
        '        | Red | Orange -> "warm"\n'
        '        | Green | Blue -> "cool"\n'
        "    end match\n"
        "end func\n"
    )
    match = program.definitions[1].body[0].value
    assert [arm.pattern.constructor for arm in match.arms] == [
        "Red",
        "Orange",
        "Green",
        "Blue",
    ]
    # Each alternative has its own copy of the body.
    assert match.arms[0].body is not match.arms[1].body


def test_or_pattern_alternatives_must_bind_the_same_names() -> None:
    with pytest.raises((ParseError, ParseErrors), match="must bind the same names"):
        parse(
            "type S = A(x: Int) | B(y: Int)\n"
            "func f(s: S) -> Int\n"
            "    example A(1) -> 1\n"
            "    match s with\n"
            "        | A(x) | B(y) -> return 1\n"
            "    end match\n"
            "end func\n"
        )


def test_or_pattern_counts_towards_exhaustiveness() -> None:
    errors = _errors(
        COLOR + "func f(c: Color) -> Int\n"
        "    example Red -> 1\n"
        "    match c with\n"
        "        | Red | Orange | Green -> return 1\n"
        "    end match\n"
        "end func\n"
    )
    assert "missing Blue" in errors[0].message


def test_error_in_an_or_pattern_body_is_reported_once() -> None:
    errors = _errors(
        "type S = A(x: Int) | B(x: Int)\n"
        "func f(s: S) -> Int\n"
        "    example A(1) -> 1\n"
        "    match s with\n"
        '        | A(x) | B(x) -> return x + "a"\n'
        "    end match\n"
        "end func\n"
    )
    assert len(errors) == 1


# ---------------------------------------------------------------------------
# Tuple fields
# ---------------------------------------------------------------------------


def test_tuple_field_is_a_literal_index() -> None:
    value = _first_value(_main("    let x = (1, 2).1\n"))
    assert isinstance(value, IndexAccess)
    assert value.index.value == 1


def test_nested_tuple_fields_need_parentheses() -> None:
    with pytest.raises((ParseError, ParseErrors), match=r"\(t\.0\)\.1"):
        parse(_main("    let t = ((1, 2), 3)\n    let x = t.0.1\n"))


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("let p = (1, 2)\n    print(p.2)", "Tuple index 2 is out of range"),
        (
            "let p = (1, 2)\n    let i = 0\n    print(p[i])",
            "A tuple index must be a number written in the source",
        ),
        ('let p = (1, "a")\n    print(p.1 + 1)', "Cannot apply '+' to String and Int"),
    ],
)
def test_tuple_field_errors(body: str, fragment: str) -> None:
    assert fragment in _errors(_main(f"    {body}\n"))[0].message


# ---------------------------------------------------------------------------
# Map literals
# ---------------------------------------------------------------------------


def test_map_literal_parses() -> None:
    value = _first_value(_main('    let m = {"a": 1, "b": 2,}\n'))
    assert isinstance(value, MapLiteral)
    assert len(value.entries) == 2


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ('let m = {"x": 1, "x": 2}', 'Duplicate key "x" in map literal'),
        ('let m = {"x": 1, 2: 3}', "Map key type mismatch: expected String, got Int"),
        ('let m = {"x": 1, "y": "z"}', "Map value type mismatch"),
        ("let m = {[1]: 2}", "Map key type must be hashable"),
    ],
)
def test_map_literal_errors(body: str, fragment: str) -> None:
    assert fragment in _errors(_main(f"    {body}\n"))[0].message


def test_empty_map_literal_takes_its_type_from_the_annotation() -> None:
    _check(_main("    let m: Map[String, Int] = {}\n    print(m)\n"))


# ---------------------------------------------------------------------------
# Parity
# ---------------------------------------------------------------------------


def test_new_expressions_run_identically_on_every_backend() -> None:
    _assert_parity(
        COLOR + "type Shape = Circle(r: Float) | Ring(r: Float) | Square(side: Float)\n"
        "func warmth(c: Color) -> String\n"
        '    example Red -> "warm"\n'
        "    match c with\n"
        '        | Red | Orange -> return "warm"\n'
        '        | Green | Blue -> return "cool"\n'
        "    end match\n"
        "end func\n"
        "func radius(s: Shape) -> Float\n"
        "    example Circle(1.0) -> 1.0\n"
        "    return match s with\n"
        "        | Circle(r) | Ring(r) -> r\n"
        "        | Square(side) -> side / 2.0\n"
        "    end match\n"
        "end func\n"
        + _main(
            "    let x = 3\n"
            '    print(if x % 2 == 0 then "even" else "odd" end if)\n'
            "    print(if x > 2 then 1.5 else 2 end if)\n"
            "    print([1, 2, 3].map(fn(n) -> if n > 1 then n * 10 else 0 end if))\n"
            "    print(warmth(Orange))\n"
            "    print(radius(Ring(2.5)))\n"
            '    let p = (1, "two", 3.5)\n'
            "    print(p.1)\n"
            "    print(p.2)\n"
            "    let nested = ((1, 2), 3)\n"
            "    print((nested.0).1)\n"
            '    let ages = {"ada": 36, "alan": 41}\n'
            "    print(ages)\n"
            '    print(ages["alan"])\n'
            '    let prices: Map[String, Float] = {"tea": 2, "cake": 3.5}\n'
            "    print(prices)\n"
            '    let grid = {(0, 0): "origin", (1, 0): "east"}\n'
            "    print(grid[(1, 0)])\n"
        ),
        'odd\n1.5\n[0, 20, 30]\nwarm\n2.5\ntwo\n3.5\n2\n{"ada": 36, "alan": 41}\n'
        '41\n{"tea": 2.0, "cake": 3.5}\neast\n',
    )
