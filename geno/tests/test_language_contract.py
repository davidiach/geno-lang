"""Language-contract regression tests for the normative 0.4 series."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.validate_spec import validate_spec

ROOT = Path(__file__).resolve().parents[2]


def _load_spec() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((ROOT / "spec.json").read_text()))


def test_normative_contract_is_in_sync() -> None:
    assert validate_spec(_load_spec()) == []


def test_effect_drift_is_rejected() -> None:
    spec = _load_spec()
    spec["effect_system"]["valid_effects"].remove("regex")

    errors = validate_spec(spec)

    assert any(
        "Effect names mismatch" in error and "regex" in error for error in errors
    )


def test_diagnostic_code_drift_is_rejected() -> None:
    spec = _load_spec()
    spec["diagnostics"]["codes"].remove("E311")

    errors = validate_spec(spec)

    assert any(
        "Diagnostic codes mismatch" in error and "E311" in error for error in errors
    )


def test_the_draft_panic_form_code_is_published() -> None:
    """E330 (proposal 0017) is in the code list, and its addition is recorded."""
    spec = _load_spec()
    assert "E330" in spec["diagnostics"]["codes"]
    assert any("E330" in addition for addition in spec["development_line"]["additions"])

    spec["diagnostics"]["codes"].remove("E330")
    errors = validate_spec(spec)

    assert any(
        "Diagnostic codes mismatch" in error and "E330" in error for error in errors
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
    """The normative document must name the series `spec.json` declares.

    Derived from `language_series` rather than hardcoded, so a series flip is
    caught when the document and the contract disagree instead of failing on
    every flip.
    """
    spec = _load_spec()
    path = ROOT / spec["human_spec"]
    opening = path.read_text().splitlines()[:12]

    assert opening[0] == f"# Geno Language Specification v{spec['language_series']}"
    assert any("normative" in line.lower() for line in opening)


def test_development_line_names_the_draft_spec_and_corpus() -> None:
    """The Draft 0.7 line (proposal 0017) is recorded beside the 0.6 contract."""
    spec = _load_spec()
    line = spec["development_line"]

    assert line["series"] == "0.7"
    assert line["status"] == "draft"
    assert (ROOT / line["human_spec"]).is_file()
    assert (ROOT / line["conformance"]).is_file()
    assert any("panics" in addition for addition in line["additions"])
    # The normative fields still describe the 0.6 series.
    assert spec["language_series"] == "0.6"
    assert spec["human_spec"] == "docs/spec/v0.6.md"


def test_the_draft_spec_says_it_is_not_normative() -> None:
    opening = (ROOT / "docs/spec/v0.7.md").read_text().splitlines()[:12]

    text = " ".join(line.lstrip("> ") for line in opening)
    assert opening[0] == "# Geno Language Specification v0.7"
    assert "Draft contract for the unreleased 0.7 development line" in text
    assert "v0.6 remains normative for the 0.6 series" in text


def test_an_absent_development_line_is_valid() -> None:
    spec = _load_spec()
    del spec["development_line"]

    assert validate_spec(spec) == []


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("series", "0.6", "must be newer than language series"),
        ("series", "seven", "major.minor"),
        ("status", "normative", "status' must be 'draft'"),
        ("human_spec", "docs/spec/v0.6.md", "Development human specification"),
        ("conformance", "conformance/v0.6/manifest.toml", "Development conformance"),
        ("additions", [], "non-empty list of strings"),
        ("additions", ["x", "x"], "duplicate entries"),
    ],
)
def test_development_line_drift_is_rejected(
    field: str, value: object, fragment: str
) -> None:
    spec = _load_spec()
    spec["development_line"][field] = value

    errors = validate_spec(spec)

    assert any(fragment in error for error in errors), errors


def test_development_line_must_be_an_object() -> None:
    spec = _load_spec()
    spec["development_line"] = "0.7"

    assert any("must be an object" in error for error in validate_spec(spec))
