"""Language-contract regression tests for the normative 0.4 series."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from scripts.validate_spec import validate_spec

ROOT = Path(__file__).resolve().parents[2]


def _load_spec() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((ROOT / "spec.json").read_text()))


def test_normative_contract_is_in_sync() -> None:
    assert validate_spec(_load_spec()) == []


def test_effect_drift_is_rejected() -> None:
    spec = _load_spec()
    spec["effect_system"]["valid_effects"].remove("throw")

    errors = validate_spec(spec)

    assert any(
        "Effect names mismatch" in error and "throw" in error for error in errors
    )


def test_diagnostic_code_drift_is_rejected() -> None:
    spec = _load_spec()
    spec["diagnostics"]["codes"].remove("E311")

    errors = validate_spec(spec)

    assert any(
        "Diagnostic codes mismatch" in error and "E311" in error for error in errors
    )


def test_language_series_drift_is_rejected() -> None:
    spec = _load_spec()
    spec["language_series"] = "0.3"

    errors = validate_spec(spec)

    assert any("Language series mismatch" in error for error in errors)


def test_portable_integer_bound_drift_is_rejected() -> None:
    spec = _load_spec()
    spec["runtime_semantics"]["portable_javascript_int"]["max"] += 1

    errors = validate_spec(spec)

    assert any("Portable integer bounds mismatch" in error for error in errors)


def test_contract_arrays_reject_non_string_entries() -> None:
    spec = _load_spec()
    spec["effect_system"]["valid_effects"].append(None)

    errors = validate_spec(spec)

    assert any(
        "effect_system.valid_effects" in error and "only strings" in error
        for error in errors
    )


def test_contract_arrays_reject_duplicate_entries() -> None:
    spec = _load_spec()
    spec["diagnostics"]["codes"].append("E100")

    errors = validate_spec(spec)

    assert any(
        "diagnostics.codes" in error and "duplicate entries" in error
        for error in errors
    )


def test_normative_human_spec_is_identifiable() -> None:
    spec = _load_spec()
    path = ROOT / spec["human_spec"]
    opening = path.read_text().splitlines()[:12]

    assert opening[0] == "# Geno Language Specification v0.4"
    assert any("normative" in line.lower() for line in opening)
