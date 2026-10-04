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


BAD_MODULE = "type Int = Foo | Bar\n\nfunc helper() -> Int\n    example () -> 1\n    return 1\nend func\n"
IMPORTER = "import Bad\n" + MAIN


def test_imported_module_is_validated() -> None:
    with pytest.raises(GenoTypeError, match="Type 'Int' is built in"):
        TypeChecker().check_program(parse(IMPORTER), modules={"Bad": parse(BAD_MODULE)})


def _write_import_project(tmp_path):
    (tmp_path / "Bad.geno").write_text(BAD_MODULE, encoding="utf-8")
    main = tmp_path / "Main.geno"
    main.write_text(IMPORTER, encoding="utf-8")
    return main


def test_geno_test_rejects_an_imported_redefinition(tmp_path) -> None:
    import os
    import subprocess
    import sys
    from pathlib import Path

    main = _write_import_project(tmp_path)
    repo_root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": str(repo_root)}
    result = subprocess.run(
        [sys.executable, "-m", "geno", "test", str(main)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=60,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Type 'Int' is built in" in result.stdout + result.stderr


def test_selfhost_checker_validates_imported_modules(tmp_path) -> None:
    from geno.tests.test_cli import _run_selfhost_cli

    main = _write_import_project(tmp_path)
    result = _run_selfhost_cli("check", str(main))
    assert result.returncode != 0
    assert "Type 'Int' is built in" in result.stdout + result.stderr
