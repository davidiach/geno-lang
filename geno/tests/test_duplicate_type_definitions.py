"""Duplicate and built-in type names are rejected at the definition (#139)."""

from __future__ import annotations

import pytest

from geno.parser import parse
from geno.typechecker import _BUILTIN_ADT_NAMES, _RESERVED_TYPE_NAMES, TypeChecker
from geno.types import TypeError as GenoTypeError

MAIN = "\nfunc main() -> Unit\n    print(1)\nend func\n"


def _check(source: str) -> None:
    TypeChecker().check_program(parse(source + MAIN))


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "type Shape = Circle(r: Int) | Square(s: Int)\ntype Shape = Dot | Line\n",
            "Duplicate type definition 'Shape'",
        ),
        (
            "type Shape = Dot | Line\ntype Shape = List[Int]\n",
            "Duplicate type definition 'Shape'",
        ),
        (
            "type Signal = Ping(n: Int) | Ping(s: String)\n",
            "Duplicate constructor 'Ping' in type 'Signal'",
        ),
        ("type Int = Foo | Bar\n", "Type 'Int' is built in"),
        ("type String = Text(s: Int)\n", "Type 'String' is built in"),
        ("type Option[T] = Some(value: T) | None\n", "Type 'Option' is built in"),
        (
            "type List[T] = Cons(head: T, tail: List[T]) | Nil\n",
            "Type 'List' is built in",
        ),
        ("type JsonValue = J | K\n", "Type 'JsonValue' is built in"),
        ("type Int = String\n", "Type 'Int' is built in"),
    ],
)
def test_rejected(source: str, message: str) -> None:
    with pytest.raises(GenoTypeError, match=message):
        _check(source)


def test_distinct_types_and_spec_examples_still_check() -> None:
    _check(
        "type Color = Red | Green | Blue\n"
        "type Maybe[T] = Just(value: T) | Nothing\n"
        "type Stack[T] = Push(top: T, rest: Stack[T]) | Empty\n"
        "type Point = Point(x: Int, y: Int)\n"
        "type Pair = (Int, Int)\n"
    )


def test_builtin_adt_list_matches_the_checker() -> None:
    assert set(TypeChecker().type_defs) == set(_BUILTIN_ADT_NAMES)
    assert _BUILTIN_ADT_NAMES <= _RESERVED_TYPE_NAMES


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("type Int = Foo | Bar\n", "Type 'Int' is built in"),
        (
            "type Signal = Ping(n: Int) | Ping(s: String)\n",
            "Duplicate constructor 'Ping' in type 'Signal'",
        ),
    ],
)
def test_selfhost_checker_agrees(tmp_path, source: str, message: str) -> None:
    from geno.tests.test_cli import _run_selfhost_cli

    program = tmp_path / "t.geno"
    program.write_text(source + MAIN, encoding="utf-8")
    result = _run_selfhost_cli("check", str(program))
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
