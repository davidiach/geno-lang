"""Golden semantic fixtures for representative typechecker behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from geno.parser import parse
from geno.typechecker import TypeChecker
from geno.typechecker import TypeError as GenoTypeError

GOLDEN_DIR = Path(__file__).with_name("golden") / "typechecker"


def _golden_sources() -> list[Path]:
    return sorted(GOLDEN_DIR.glob("*.geno"))


@pytest.mark.parametrize("source_path", _golden_sources(), ids=lambda p: p.stem)
def test_typechecker_semantic_golden(source_path: Path) -> None:
    expected_lines = (
        source_path.with_suffix(".expected").read_text(encoding="utf-8").splitlines()
    )
    assert expected_lines

    source = source_path.read_text(encoding="utf-8")
    program = parse(source)
    expectation = expected_lines[0]

    if expectation == "OK":
        TypeChecker().check_program(program)
        return

    assert expectation == "ERROR"
    with pytest.raises(GenoTypeError) as exc_info:
        TypeChecker().check_program(program)

    message = str(exc_info.value)
    for fragment in expected_lines[1:]:
        assert fragment in message
