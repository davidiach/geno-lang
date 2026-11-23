"""An Int past CPython's ``int_max_str_digits`` limit still becomes text.

Geno's Ints reach ``max_integer_bits`` (33,219 bits, about 10,000 digits),
but CPython 3.11+ refuses to write an int with more decimal digits than
``sys.get_int_max_str_digits()`` (4,300 by default) and raises a host
``ValueError``. Every place an Int becomes decimal text on the interpreter
and in compiled Python writes it in pieces under that limit instead, without
changing the process-wide setting. (Compiled JavaScript cannot hold such an
Int, so it has no counterpart.)
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from geno._runtime_support import (
    _geno_format_spec,
    _geno_int_text,
    _json_dumps_under_limit,
)
from geno.builtins import _json_dumps_under_limit as _interpreter_json_dumps
from geno.format_spec import format_value
from geno.tests.test_backend_parity import _compiled_python_output, _interpreter_output
from geno.values import int_text

BIG = 2**15000 + 7
DIGITS = str(Decimal(BIG))
NEGATIVE = "-" + DIGITS

_get_limit = getattr(sys, "get_int_max_str_digits", None)
_set_limit = getattr(sys, "set_int_max_str_digits", None)
needs_digit_limit = pytest.mark.skipif(
    _set_limit is None, reason="this Python has no int_max_str_digits limit"
)


def _grouped(digits: str) -> str:
    head = len(digits) % 3 or 3
    groups = [digits[:head]] + [
        digits[index : index + 3] for index in range(head, len(digits), 3)
    ]
    return ",".join(groups)


SOURCE = """
type Box = Box(n: Int)

func main() -> Unit with io
    let x = 2 ** 15000 + 7
    let y = 0 - x
    print(to_string(x))
    print(f"{x}")
    print(f"<{y}>")
    print(f"{x:,d}")
    print(f"{y:+,}")
    print(f"{x:>20}")
    print(f"{x:^9d}")
    print(f"{x:s}")
    print(f"{x:x}")
    print(json_encode(x))
    print(json_encode([x, y]))
    print(json_encode_pretty({"k": x}, indent: 2))
    print(json_stringify(JsonInt(y)))
    print(to_string([x]))
    print(to_string(Box(n: y)))
    print("a" + to_string(x))
    print(x)
    print(Some(x))
    print(length(to_string(x * x)))
end func
"""

EXPECTED = (
    "\n".join(
        [
            DIGITS,
            DIGITS,
            f"<{NEGATIVE}>",
            _grouped(DIGITS),
            "-" + _grouped(DIGITS),
            DIGITS,
            DIGITS,
            DIGITS,
            format(BIG, "x"),
            DIGITS,
            f"[{DIGITS},{NEGATIVE}]",
            '{\n  "k": ' + DIGITS + "\n}",
            NEGATIVE,
            f"[{DIGITS}]",
            f"Box(n: {NEGATIVE})",
            "a" + DIGITS,
            DIGITS,
            f"Some(value: {DIGITS})",
            str(len(str(Decimal(BIG * BIG)))),
        ]
    )
    + "\n"
)


@pytest.mark.parametrize("backend", [_interpreter_output, _compiled_python_output])
def test_every_int_rendering_writes_all_its_digits(backend) -> None:
    assert backend(SOURCE) == EXPECTED


def test_the_process_wide_digit_limit_is_left_alone() -> None:
    before = _get_limit() if _get_limit is not None else None
    assert _interpreter_output(SOURCE) == EXPECTED
    after = _get_limit() if _get_limit is not None else None
    assert after == before


TESTS_SOURCE = """
func big_text(n: Int) -> String
    example 15000 panics
    return to_string(2 ** n)
end func

func big_length(n: Int) -> Int
    example 15000 -> 4516
    return length(to_string(2 ** n))
end func

test "an f-string of a big Int"
    let x = 2 ** 15000
    assert length(f"{x}") == 4516
end test
"""


@pytest.mark.parametrize("backend", ["interpreter", "python"])
def test_writing_a_big_int_is_not_a_panic_or_an_internal_error(
    tmp_path: Path, backend: str
) -> None:
    """The conversion succeeds, so a ``panics`` example on it fails."""
    program = tmp_path / "digits.geno"
    program.write_text(TESTS_SOURCE, encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "test",
            "-v",
            "--backend",
            backend,
            str(program),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = completed.stdout + completed.stderr
    assert "Internal error" not in output
    assert "Traceback" not in output
    assert "Exceeds the limit" not in output
    assert "2 passed, 1 failed" in output, output
    assert "big_text" in output


@contextmanager
def _limit(digits: int) -> Iterator[None]:
    """Set the process-wide limit for a moment: 0 is none, 640 the lowest."""
    assert _get_limit is not None and _set_limit is not None
    original = _get_limit()
    _set_limit(digits)
    try:
        yield
    finally:
        _set_limit(original)


SPECS = [
    "", "d", "1000", "<1000", ">1000", "^1000", "^999d", "*^1000", "0>1000",
    "01000", "+01000d", " 1000", "+", " d", "-d", ",", ",d", "*<1000,",
    "_>1000,d", "+,", "x", "08b",
]  # fmt: skip
NUMBERS = [10**699 + 12345, -(10**699) - 1, 10**640, -(2**2200), 2**33219 - 1]


@needs_digit_limit
@pytest.mark.parametrize("number", NUMBERS, ids=lambda n: f"{n.bit_length()}bits")
def test_specs_on_a_long_int_format_as_python_does_without_its_limit(number) -> None:
    expected = {}
    with _limit(0):
        for spec in SPECS:
            expected[spec] = format(number, spec)
        plain = str(number)
    with _limit(640):
        for spec in SPECS:
            assert format_value(number, spec, "int") == expected[spec], spec
            assert _geno_format_spec(number, spec, "int") == expected[spec], spec
            assert _geno_format_spec(number, spec) == expected[spec], spec
        assert int_text(number) == plain
        assert _geno_int_text(number) == plain


@needs_digit_limit
@pytest.mark.parametrize("indent", [None, 3])
def test_json_writes_a_long_int_at_any_depth_as_python_does_without_its_limit(
    indent,
) -> None:
    import json

    value = {
        "a": [10**699, {"b": -(10**700), "c": [1.5, None, True, "é"]}],
        "d": [],
        "e": {},
        "f": "x",
    }
    with _limit(0):
        expected = json.dumps(
            value,
            indent=indent,
            separators=(",", ":") if indent is None else (",", ": "),
            ensure_ascii=indent is None,
            allow_nan=False,
        )
    with _limit(640):
        ensure_ascii = indent is None
        assert (
            _json_dumps_under_limit(
                "json_stringify", value, indent=indent, ensure_ascii=ensure_ascii
            )
            == expected
        )
        assert (
            _interpreter_json_dumps(
                "json_stringify",
                value,
                10_000_000,
                indent=indent,
                ensure_ascii=ensure_ascii,
            )
            == expected
        )


@needs_digit_limit
def test_json_errors_other_than_the_digit_limit_are_unchanged() -> None:
    with _limit(640):
        for dumps in (
            lambda value: _json_dumps_under_limit("json_stringify", value),
            lambda value: _interpreter_json_dumps("json_stringify", value, 1000),
        ):
            with pytest.raises(ValueError, match="Out of range float"):
                dumps([10**700, float("nan")])


LIMITS_SOURCE = """
func shl(e: Int) -> Int
    example 3 -> 8
    example 2 ** 15000 panics
    return 1 << e
end func

func pw(e: Int) -> Int
    example 2 ** 15000 panics
    return 3 ** e
end func

func key(n: Int) -> Int
    example 2 ** 15000 panics "Key not found: (2817"
    let m: Map[(Int, Int), Int] = {(1, 2): 3}
    return m[(n, 1)]
end func

func round_trip(n: Int) -> Bool
    example 15000 -> true
    return json_parse(json_encode(2 ** n)) == Ok(JsonInt(2 ** n))
end func
"""


@pytest.mark.parametrize("backend", ["interpreter", "python"])
def test_long_ints_in_limit_messages_keys_and_json_text(
    tmp_path: Path, backend: str
) -> None:
    """A shift or exponent past the limit by a long Int is the integer size
    limit on both backends, not the host's digit-limit error as a panic; a
    key holding a long Int is named in full; json_parse reads back what
    json_encode writes (0.7 review)."""
    from geno.test_runner import run_test_suite

    program = tmp_path / "limits.geno"
    program.write_text(LIMITS_SOURCE, encoding="utf-8")
    result = run_test_suite([program], backend=backend).file_results[0]
    assert result.error is None
    harness = result.harness_result
    assert harness is not None
    assert harness.passed == 3
    assert sorted(v.function for v in harness.violations) == ["pw", "shl"]
    for violation in harness.violations:
        assert "integer size limit" in violation.message, violation.message
        assert "Exceeds the limit" not in violation.message


def test_a_json_literal_past_the_integer_limit_is_refused_early() -> None:
    """A literal with more digits than any Int within the limit has is the
    integer size limit, before its digits are read (0.7 review)."""
    from geno.api import RunConfig, run

    source = (
        "func main() -> Unit with io\n"
        f'    print(json_parse("1{"0" * 20000}"))\n'
        "end func\n"
    )
    result = run(source, config=RunConfig(timeout=10.0, capabilities={"print"}))
    assert not result.ok
    assert "Integer exceeds maximum size" in result.diagnostics[0].message
    with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
        _compiled_python_output(source)


def test_process_sandbox_round_trips_long_int_results() -> None:
    """ProcessSandbox serializes and parses >4,300-digit Int results without
    hitting CPython's int_max_str_digits ValueError."""
    from geno.sandbox import ProcessSandboxConfig, run_in_process

    config = ProcessSandboxConfig(timeout=15.0)
    expected = 10**5000

    value, output = run_in_process("__result__ = 10 ** 5000\n", config)
    assert output == ""
    assert value == expected

    nested, _ = run_in_process(
        "__result__ = [10 ** 5000, -(10 ** 5000), {'n': 10 ** 5000}]\n",
        config,
    )
    assert nested == [expected, -expected, {"n": expected}]

    non_json, _ = run_in_process("__result__ = {10 ** 5000}\n", config)
    assert non_json == "{" + int_text(expected) + "}"

    with pytest.raises(RuntimeError, match="result_too_large"):
        run_in_process(
            "__result__ = 10 ** 50_000\n",
            ProcessSandboxConfig(timeout=15.0, max_output_length=1000),
        )
    with pytest.raises(RuntimeError, match="result_too_large"):
        run_in_process(
            "__result__ = {10 ** 50_000}\n",
            ProcessSandboxConfig(timeout=15.0, max_output_length=1000),
        )
