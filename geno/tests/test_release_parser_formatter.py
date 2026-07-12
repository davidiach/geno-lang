"""Release-blocking parser, lexer, and formatter regressions."""

import pytest

from geno.ast_nodes import (
    ExpressionStatement,
    Identifier,
    IfStatement,
    ReturnStatement,
    TupleExpr,
)
from geno.formatter import format_source
from geno.lexer import LexerError, tokenize
from geno.parser import parse


def test_triple_quoted_fstring_is_rejected_clearly() -> None:
    with pytest.raises(LexerError, match="Triple-quoted f-strings are not supported"):
        tokenize('let value = f"""hello"""')


@pytest.mark.parametrize("source", ["let value = 123abc", "let value = 1.5ms"])
def test_number_identifier_adjacency_is_rejected(source: str) -> None:
    with pytest.raises(LexerError, match="separator between numeric literal"):
        tokenize(source)


def test_bare_return_does_not_consume_next_line() -> None:
    program = parse(
        """
func stop() -> Unit
    return
    print("unreachable")
end func
"""
    )

    body = program.definitions[0].body
    assert len(body) == 2
    assert isinstance(body[0], ReturnStatement)
    assert isinstance(body[0].value, TupleExpr)
    assert body[0].value.elements == []
    assert isinstance(body[1], ExpressionStatement)


def test_return_expression_on_same_line_is_preserved() -> None:
    program = parse(
        """
func identity(value: Int) -> Int
    return value
end func
"""
    )

    statement = program.definitions[0].body[0]
    assert isinstance(statement, ReturnStatement)
    assert not isinstance(statement.value, TupleExpr)


def test_else_if_is_whitespace_insensitive() -> None:
    same_line = parse(
        """
func choose(a: Bool, b: Bool) -> Int
    if a then
        return 1
    else if b then
        return 2
    else
        return 3
    end if
end func
"""
    )
    next_line = parse(
        """
func choose(a: Bool, b: Bool) -> Int
    if a then
        return 1
    else
    if b then
        return 2
    else
        return 3
    end if
end func
"""
    )

    same_if = same_line.definitions[0].body[0]
    wrapped_if = next_line.definitions[0].body[0]
    assert isinstance(same_if, IfStatement)
    assert isinstance(wrapped_if, IfStatement)
    assert len(same_if.else_body) == len(wrapped_if.else_body) == 1
    same_nested = same_if.else_body[0]
    wrapped_nested = wrapped_if.else_body[0]
    assert isinstance(same_nested, IfStatement)
    assert isinstance(wrapped_nested, IfStatement)
    assert isinstance(same_nested.condition, Identifier)
    assert isinstance(wrapped_nested.condition, Identifier)
    assert same_nested.condition.name == wrapped_nested.condition.name == "b"
    assert len(same_nested.else_body) == len(wrapped_nested.else_body) == 1


def test_formatter_indents_binding_position_match_and_following_statements() -> None:
    source = """func classify(value: Int) -> Int
let result: Int = match value with
| 0 -> 10
| _ -> 20
end match
return result
end func
"""
    expected = """func classify(value: Int) -> Int
    let result: Int = match value with
        | 0 -> 10
        | _ -> 20
    end match
    return result
end func
"""

    assert format_source(source) == expected
    assert format_source(expected) == expected


def test_formatter_repairs_geno_dash_match_indent_cascade() -> None:
    source = """func update(state: State) -> State
    let next: Int = match state.value with
    | Some(value) -> value
    | None -> 0
end match
let result: State = State(next)
return result
end func
"""
    expected = """func update(state: State) -> State
    let next: Int = match state.value with
        | Some(value) -> value
        | None -> 0
    end match
    let result: State = State(next)
    return result
end func
"""

    assert format_source(source) == expected
