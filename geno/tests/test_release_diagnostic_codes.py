"""Release regressions for stable, machine-readable diagnostic codes."""

from __future__ import annotations

import pytest

from geno.api import CheckResult, RunResult, check, run
from geno.diagnostics import ErrorCode


def _codes(result: CheckResult | RunResult) -> list[ErrorCode]:
    return [diagnostic.code for diagnostic in result.diagnostics]


def test_unterminated_string_uses_granular_lexer_code() -> None:
    result = check('func main() -> String\n    return "oops\nend func\n')

    assert not result.ok
    assert _codes(result) == [ErrorCode.LEX_UNTERMINATED_STRING]


def test_missing_required_token_uses_expected_token_code() -> None:
    result = check("func main() -> Int\n    return 1\n")

    assert not result.ok
    assert ErrorCode.PARSE_EXPECTED_TOKEN in _codes(result)


def test_undefined_variable_uses_granular_type_code() -> None:
    result = check("func main() -> Int\n    return missing\nend func\n")

    assert not result.ok
    assert _codes(result) == [ErrorCode.TYPE_UNDEFINED_VAR]


def test_an_expected_panic_uses_the_specification_clause_code() -> None:
    """E330 (0.7 draft, proposal 0017) owns every arrow spelling of a panic."""
    result = check(
        "func div(a: Int, b: Int) -> Int\n"
        "    example 6, 3 -> 2\n"
        "    example 1, 0 -> panic\n"
        "    return a / b\n"
        "end func\n"
    )

    assert not result.ok
    assert _codes(result) == [ErrorCode.TYPE_EXAMPLE_PANIC_FORM]
    assert ErrorCode.TYPE_EXAMPLE_PANIC_FORM.value == "E330"
    assert result.diagnostics[0].to_dict()["code"] == "E330"


def test_a_misspelled_panics_example_keeps_the_parse_codes() -> None:
    """`example panics` and `panics ""` reuse E201 and E202, unchanged."""
    zero = check(
        "func usage() -> String\n"
        "    example panics\n"
        '    panic("not implemented")\n'
        "end func\n"
    )
    empty = check(
        "func div(a: Int, b: Int) -> Int\n"
        '    example 1, 0 panics ""\n'
        "    return a / b\n"
        "end func\n"
    )

    assert _codes(zero) == [ErrorCode.PARSE_EXPECTED_TOKEN]
    assert _codes(empty) == [ErrorCode.PARSE_INVALID_SYNTAX]


def test_wrong_call_arity_uses_granular_type_code() -> None:
    result = check(
        '@untested("diagnostic fixture")\n'
        "func add(a: Int, b: Int) -> Int\n"
        "    return a + b\n"
        "end func\n\n"
        "func main() -> Int\n"
        "    return add(1)\n"
        "end func\n"
    )

    assert not result.ok
    assert _codes(result) == [ErrorCode.TYPE_WRONG_ARITY]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "func main() -> Int\n    return 1 / 0\nend func\n",
            ErrorCode.RUNTIME_DIVISION_BY_ZERO,
        ),
        (
            "func main() -> Int\n"
            "    let xs: List[Int] = []\n"
            "    return xs[0]\n"
            "end func\n",
            ErrorCode.RUNTIME_INDEX_OUT_OF_BOUNDS,
        ),
        (
            "func main() -> Int\n"
            '    let values = map_from_list([("answer", 42)])\n'
            '    return values["missing"]\n'
            "end func\n",
            ErrorCode.RUNTIME_KEY_NOT_FOUND,
        ),
    ],
)
def test_common_runtime_failures_use_granular_codes(
    source: str, expected: ErrorCode
) -> None:
    result = run(source)

    assert not result.ok
    assert _codes(result) == [expected]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("$", ErrorCode.LEX_UNEXPECTED_CHAR),
        ("wat", ErrorCode.PARSE_INVALID_SYNTAX),
        (
            'func main() -> Int\n    return "wrong"\nend func\n',
            ErrorCode.TYPE_MISMATCH,
        ),
    ],
)
def test_uncategorized_errors_keep_phase_fallback_codes(
    source: str, expected: ErrorCode
) -> None:
    result = check(source)

    assert not result.ok
    assert _codes(result) == [expected]


def test_uncategorized_runtime_error_keeps_unknown_fallback_code() -> None:
    result = run("func main() -> Int\n    return head([])\nend func\n")

    assert not result.ok
    assert _codes(result) == [ErrorCode.RUNTIME_UNKNOWN]
