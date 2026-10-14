"""Large Geno integers remain exact JSON numbers without global Python changes."""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from geno.values import ConstructorValue, value_to_json

_BIG = 2**16000 + 7
_BIG_TEXT = str(Decimal(_BIG))


def _run_json(
    tmp_path: Path, return_type: str, body: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    program = tmp_path / "Main.geno"
    program.write_text(
        f"func main() -> {return_type}\n"
        "    let big: Int = 2 ** 16000 + 7\n"
        f"{body}\n"
        "end func\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--json",
            "--no-check-examples",
            str(program),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.stderr == ""
    payload = json.loads(completed.stdout, parse_int=Decimal)
    assert payload["ok"] is True
    assert payload["diagnostics"] == []
    return completed, payload


@pytest.mark.parametrize("negative", [False, True], ids=["positive", "negative"])
def test_cli_preserves_large_integer_and_normalizes_only_exit_status(
    tmp_path: Path, negative: bool
) -> None:
    expected = -_BIG if negative else _BIG
    expression = "0 - big" if negative else "big"
    completed, payload = _run_json(tmp_path, "Int", f"    return {expression}")

    assert completed.returncode == expected % 256
    assert isinstance(payload["value"], Decimal)
    assert payload["value"] == expected


@pytest.mark.parametrize(
    ("return_type", "body", "expected"),
    [
        pytest.param(
            "List[Int]",
            "    return [big, 0 - big]",
            [_BIG, -_BIG],
            id="list-values",
        ),
        pytest.param(
            "Map[Int, Int]",
            "    return map_from_entries([(big, big)])",
            {_BIG_TEXT: _BIG},
            id="integer-map-key",
        ),
        pytest.param(
            "Map[(Int, String), Int]",
            '    return map_from_entries([((big, "key"), big)])',
            {f"({_BIG_TEXT}, 'key')": _BIG},
            id="tuple-map-key",
        ),
        pytest.param(
            "Map[Option[Int], Int]",
            "    return map_from_entries([(Some(big), big)])",
            {f"Some(value: {_BIG_TEXT})": _BIG},
            id="constructor-map-key",
        ),
        pytest.param(
            "MutableMap[Int, Int]",
            "    let values: MutableMap[Int, Int] = mutable_map_new()\n"
            "    mutable_map_set(map: values, key: big, value: big)\n"
            "    return values",
            {"_mutable_map": {_BIG_TEXT: _BIG}},
            id="mutable-map-key",
        ),
        pytest.param(
            "Set[Int]",
            "    return set_from_list([big, 0 - big])",
            {"_set": [-_BIG, _BIG]},
            id="integer-set",
        ),
        pytest.param(
            "Set[(Int, String)]",
            '    return set_from_list([(big, "key")])',
            {"_set": [{"_tuple": [_BIG, "key"]}]},
            id="tuple-set",
        ),
        pytest.param(
            "Set[Option[Int]]",
            "    return set_from_list([Some(big)])",
            {"_set": [{"_constructor": "Some", "fields": {"value": _BIG}}]},
            id="constructor-set",
        ),
    ],
)
def test_cli_preserves_large_integers_in_collections(
    tmp_path: Path, return_type: str, body: str, expected: Any
) -> None:
    completed, payload = _run_json(tmp_path, return_type, body)

    assert completed.returncode == 0
    assert payload["value"] == expected


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="defaults"),
        pytest.param({"indent": 2, "sort_keys": True}, id="pretty"),
        pytest.param(
            {"ensure_ascii": False, "separators": (",", ":")}, id="unicode-compact"
        ),
    ],
)
def test_encoder_preserves_standard_json_behavior(options: dict[str, Any]) -> None:
    from geno.json_encoding import GenoJSONEncoder

    payload: dict[str, Any] = {
        "unicode": "caf\u00e9 \U0001f40d",
        "escaped": '\n"\\\t',
        "values": [None, True, False, 0, -19, 1.25, -0.0, [], {}],
        "keys": {3: "integer", False: "boolean", None: "null"},
    }
    if options.get("sort_keys"):
        # Mixed key types are deliberately not sortable in the standard encoder.
        payload.pop("keys")

    assert json.dumps(payload, cls=GenoJSONEncoder, **options) == json.dumps(
        payload, **options
    )


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_encoder_rejects_nonfinite_numbers_when_required(number: float) -> None:
    from geno.json_encoding import GenoJSONEncoder

    with pytest.raises(ValueError, match="Out of range float"):
        json.dumps({"value": number}, cls=GenoJSONEncoder, allow_nan=False)
    assert json.dumps(number, cls=GenoJSONEncoder) == json.dumps(number)


def test_encoder_preserves_circular_reference_detection() -> None:
    from geno.json_encoding import GenoJSONEncoder

    payload: list[Any] = []
    payload.append(payload)
    with pytest.raises(ValueError, match="Circular reference"):
        json.dumps(payload, cls=GenoJSONEncoder)


def test_large_integer_json_does_not_change_python_digit_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from geno.json_encoding import GenoJSONEncoder

    get_limit = getattr(sys, "get_int_max_str_digits", lambda: None)
    original_limit = get_limit()

    def reject_global_change(*_args: Any) -> None:
        pytest.fail("JSON serialization must not change Python's global digit limit")

    monkeypatch.setattr(
        sys, "set_int_max_str_digits", reject_global_change, raising=False
    )
    normalized = value_to_json(
        {(_BIG, "key"): ConstructorValue("Some", {"value": _BIG})}
    )
    encoded = json.dumps(normalized, cls=GenoJSONEncoder)

    assert json.loads(encoded, parse_int=Decimal) == normalized
    assert get_limit() == original_limit


@pytest.mark.parametrize(
    "value",
    [
        pytest.param((1, "key", (False,)), id="tuple"),
        pytest.param({"text": "a'b", "values": [None, True, -1]}, id="nested-dict"),
        pytest.param(ConstructorValue("Some", {"value": (1, "key")}), id="constructor"),
    ],
)
def test_safe_repr_preserves_existing_key_text(value: Any) -> None:
    from geno.json_encoding import safe_repr

    assert safe_repr(value) == repr(value)


@pytest.mark.parametrize(
    ("number", "expected"),
    [(0, "0"), (1_000_000_001, "1000000001"), (-1_000_000_001, "-1000000001")],
)
def test_decimal_formatter_preserves_zero_chunks_and_sign(
    number: int, expected: str
) -> None:
    from geno.json_encoding import int_to_decimal

    assert int_to_decimal(number) == expected
