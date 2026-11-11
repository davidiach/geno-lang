"""`if let PATTERN = value then ... end if` (proposal 0023).

The parser turns it into a two-arm match, so every backend runs it with the
match machinery it already has. These tests pin the surface: what it accepts,
where its bindings are visible, what it reports, and that the interpreter,
compiled Python and compiled JavaScript agree.
"""

import pytest

import geno
from geno.ast_nodes import MatchExpr, MatchStatement, WildcardPattern
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

PROGRAM = """
type Shape = Circle(r: Int) | Square(side: Int) | Dot

func first_or(xs: List[Int], d: Int) -> Int
    example [], 7 -> 7
    example [3, 4], 7 -> 3
    return if let [x, ...] = xs then x else d end if
end func

func describe(o: Option[Int]) -> String
    example Some(2) -> "two-ish"
    example Some(9) -> "some 9"
    example None -> "none"
    if let Some(2) | Some(3) = o then
        return "two-ish"
    else if let Some(n) = o then
        return f"some {n}"
    else
        return "none"
    end if
end func

func area(s: Shape) -> Int
    example Circle(r: 2) -> 12
    example Dot -> 0
    var total = 0
    if let Circle(r) = s then
        total = 3 * r * r
    end if
    if s == Dot then
        total = 0
    else if let Square(side) = s then
        total = side * side
    end if
    return total
end func

func chained(a: Option[Int], b: Result[Int, String]) -> Int
    example Some(1), Ok(2) -> 1
    example None, Ok(2) -> 2
    example None, Err("e") -> 0
    return if let Some(x) = a then x else if let Ok(y) = b then y else 0 end if
end func

func head(xs: List[Int]) -> Option[Int]
    example [] -> None
    example [4] -> Some(4)
    return if let [x, ...] = xs then Some(x) else None end if
end func

func main() -> Unit with io
    print(first_or([], 7))
    print(first_or([3, 4], 7))
    print(describe(Some(3)))
    print(describe(Some(9)))
    print(describe(None))
    print(area(Circle(r: 2)))
    print(area(Square(side: 3)))
    print(area(Dot))
    print(chained(None, Ok(2)))
    print(chained(None, Err("e")))
    let pair = (1, "a")
    if let (n, s) = pair then
        print(f"{n}{s}")
    end if
    if let Some(v) = head([10, 20]) then
        print(v)
    else
        print("empty")
    end if
    let none: List[Int] = []
    if let Some(v) = head(none) then
        print(v)
    end if
end func
"""

EXPECTED = "7\n3\ntwo-ish\nsome 9\nnone\n12\n9\n0\n2\n0\n1a\n10\n"


def _parse(source: str):
    return parse(source)


def _messages(source: str) -> list[str]:
    return [d.message for d in geno.check(source).diagnostics]


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_runs_if_let_the_same(backend):
    assert backend(PROGRAM) == EXPECTED


def test_the_program_checks_and_its_examples_hold():
    assert _messages(PROGRAM) == []


def test_if_let_is_a_match_with_a_wildcard_arm_for_the_else_branch():
    program = _parse(
        "func f(o: Option[Int]) -> Int\n"
        "    example None -> 0\n"
        "    if let Some(x) = o then\n"
        "        return x\n"
        "    end if\n"
        "    return if let Some(y) = o then y else 0 end if\n"
        "end func\n"
    )
    statement, returned = program.definitions[0].body
    assert isinstance(statement, MatchStatement)
    assert [type(arm.pattern).__name__ for arm in statement.arms] == [
        "ConstructorPattern",
        "WildcardPattern",
    ]
    expression = returned.value
    assert isinstance(expression, MatchExpr)
    assert expression.from_if_let and not expression.from_if
    assert isinstance(expression.arms[-1].pattern, WildcardPattern)


@pytest.mark.parametrize(
    ("body", "variable"),
    [
        (
            "if let Some(x) = o then\n        return x\n    else\n        return x\n    end if",
            "x",
        ),
        ("if let Some(x) = o then\n        return x\n    end if\n    return x", "x"),
    ],
    ids=["else-branch", "after-the-statement"],
)
def test_bindings_are_visible_only_in_the_then_branch(body, variable):
    source = (
        f"func f(o: Option[Int]) -> Int\n    example None -> 0\n    {body}\nend func\n"
    )
    assert any(f"Undefined variable: {variable}" in m for m in _messages(source))


def test_a_pattern_of_the_wrong_type_is_a_type_error():
    source = (
        "func f(o: Option[Int]) -> Int\n"
        "    example None -> 0\n"
        "    if let Ok(x) = o then\n"
        "        return x\n"
        "    end if\n"
        "    return 0\n"
        "end func\n"
    )
    assert "Constructor Ok does not belong to type Option" in _messages(source)


def test_expression_branches_must_agree_and_say_so_as_if_branches():
    source = (
        "func f(o: Option[Int]) -> Int\n"
        "    example None -> 0\n"
        '    return if let Some(x) = o then x else "no" end if\n'
        "end func\n"
    )
    assert "If branch type mismatch: expected Int, got String" in _messages(source)


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("if let Some(x) o then", "Expected '=' after the pattern"),
        ("if let Some(x) = o when x > 1 then", "if let takes no guard"),
        ("return if let Some(x) = o when x > 1 then x else 0 end if", "takes no guard"),
        ("return if let Some(x) = o then x end if", "needs an else branch"),
        ("return if let Some(x) o then x else 0 end if", "Expected '=' after"),
    ],
)
def test_syntax_mistakes_are_explained(line, message):
    source = (
        "func f(o: Option[Int]) -> Int\n"
        "    example None -> 0\n"
        f"    {line}\n"
        "        return 1\n"
        "    end if\n"
        "    return 0\n"
        "end func\n"
    )
    with pytest.raises(ParseError, match=message):
        _parse(source)


def test_an_or_pattern_must_bind_the_same_names():
    source = (
        "func f(o: Option[Int]) -> Int\n"
        "    example None -> 0\n"
        "    if let Some(x) | None = o then\n"
        "        return 1\n"
        "    end if\n"
        "    return 0\n"
        "end func\n"
    )
    with pytest.raises(ParseError, match="must bind the same names"):
        _parse(source)


def test_an_empty_then_branch_is_allowed_like_a_plain_if():
    source = (
        "func f(o: Option[Int]) -> Int\n"
        "    example None -> 0\n"
        "    if let Some(x) = o then\n"
        "    end if\n"
        "    return 0\n"
        "end func\n"
    )
    assert _messages(source) == []


def test_the_formatter_keeps_if_let_as_written():
    source = (
        "func f(o: Option[Int]) -> Int\n"
        "    example None -> 0\n"
        "    if let Some(x) = o then\n"
        "        return x\n"
        "    else if let None = o then\n"
        "        return 0\n"
        "    end if\n"
        "    return if let Some(y) = o then y else 0 end if\n"
        "end func\n"
    )
    assert format_source(source) == source
    untidy = source.replace("        return x\n", "  return x\n")
    assert format_source(untidy) == source
