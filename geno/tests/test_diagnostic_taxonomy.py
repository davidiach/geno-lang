"""Ownership-boundary regressions for the static diagnostic taxonomy."""

from __future__ import annotations

import pytest

from geno.api import check
from geno.diagnostics import ErrorCode


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "func main() -> Int\n    return missing()\nend func\n",
            ErrorCode.TYPE_UNDEFINED_FUNC,
        ),
        (
            "func main() -> Missing\n    return 1\nend func\n",
            ErrorCode.TYPE_UNDEFINED_TYPE,
        ),
        (
            "func main() -> Int\n    let x = 1\n    return x()\nend func\n",
            ErrorCode.TYPE_NOT_CALLABLE,
        ),
        (
            "func main() -> Int\n    let x = 1\n    x = 2\n    return x\nend func\n",
            ErrorCode.TYPE_IMMUTABLE_ASSIGN,
        ),
        (
            "type Pair = Pair(left: Int, right: Int)\n\n"
            "func main() -> Int\n"
            "    let p = Pair(1, 2)\n"
            "    return p.missing\n"
            "end func\n",
            ErrorCode.TYPE_UNKNOWN_FIELD,
        ),
        (
            "func main() -> Int\n"
            "    match 1 with\n"
            '    | "x" -> return 1\n'
            "    | _ -> return 0\n"
            "    end match\n"
            "end func\n",
            ErrorCode.TYPE_PATTERN_MISMATCH,
        ),
        (
            '@untested("fixture")\n'
            "func f(x: Int, x: Int) -> Int\n"
            "    return x\n"
            "end func\n\n"
            "func main() -> Int\n"
            "    return 0\n"
            "end func\n",
            ErrorCode.TYPE_DUPLICATE_DEFINITION,
        ),
        (
            '@untested("fixture")\n'
            "func work() -> Unit with bogus\n"
            "end func\n\n"
            "func main() -> Unit\n"
            "end func\n",
            ErrorCode.EFFECT_UNKNOWN,
        ),
        (
            'func main() -> Unit with fs\n    print("x")\nend func\n',
            ErrorCode.EFFECT_VIOLATION,
        ),
    ],
)
def test_static_error_uses_owning_code(source: str, expected: ErrorCode) -> None:
    result = check(source)

    assert not result.ok
    assert [diagnostic.code for diagnostic in result.diagnostics] == [expected]
