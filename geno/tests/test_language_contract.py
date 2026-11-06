"""Language-contract regression tests for the normative 0.4 series."""

from __future__ import annotations

import json
import re
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


def _draft_changes() -> list[str]:
    """The bullet entries of the Draft spec's 'Changes from 0.6' list."""
    text = (ROOT / "docs/spec/v0.7.md").read_text(encoding="utf-8")
    start = text.index("Changes from 0.6 so far:")
    end = text.index("\nEverything else is unchanged", start)
    return re.split(r"\n- ", text[start:end])[1:]


def test_every_draft_change_links_its_proposal() -> None:
    """Each 0.7 change cites the proposal that made it, and the link resolves."""
    entries = _draft_changes()

    assert entries
    for entry in entries:
        links = re.findall(r"\]\(\.\./proposals/(\d{4}-[a-z0-9-]+\.md)\)", entry)
        assert links, f"no proposal link in: {entry[:80]}"
        for link in links:
            assert (ROOT / "docs/proposals" / link).is_file(), link


def test_the_draft_records_accepted_cold_write_forms() -> None:
    """The accepted 0026 additions coexist with the other 0.7 proposals."""
    additions = [
        addition
        for addition in _load_spec()["development_line"]["additions"]
        if "proposal 0026" in addition
    ]
    assert len(additions) == 5
    assert all("Accepted" in addition for addition in additions)
    (entry,) = [
        entry
        for entry in _draft_changes()
        if "](../proposals/0026-cold-write-forms.md)" in entry
    ]
    assert "(Accepted)" in entry
    for section in ("4.5", "4.6", "6.3.1", "6.6", "7.1.1", "7.13", "10.4", "14"):
        assert section in entry, section


def test_the_draft_records_property_checks() -> None:
    """Property checks (proposal 0017) are in the Draft spec and spec.json."""
    spec = _load_spec()
    text = (ROOT / "docs/spec/v0.7.md").read_text(encoding="utf-8")

    assert any(
        "--properties" in addition for addition in spec["development_line"]["additions"]
    )
    assert "\n### 5.4 Property Checks\n" in text
    assert any(
        "Section 5.4" in entry and "0017-panic-examples-and-property-checks" in entry
        for entry in _draft_changes()
    )


def _runtime_report_section() -> str:
    """Section 6.9.1 of the Draft spec (proposal 0018)."""
    text = (ROOT / "docs/spec/v0.7.md").read_text(encoding="utf-8")
    start = text.index("\n#### 6.9.1 Runtime Failure Reports\n")
    return text[start : text.index("\n### 6.10 ", start)]


def test_the_draft_records_runtime_diagnostics() -> None:
    """Proposal 0018 is in the Draft spec, its Changes list and spec.json."""
    from geno import runtime_report

    spec = _load_spec()
    assert any(
        "proposal 0018" in addition and "`stack`" in addition
        for addition in spec["development_line"]["additions"]
    )
    (entry,) = [
        entry
        for entry in _draft_changes()
        if "](../proposals/0018-runtime-failure-diagnostics.md)" in entry
    ]
    for section in ("4.1.1", "5.1", "5.2", "5.4", "6.9.1", "7.14", "9.11.1"):
        assert section in entry, section

    section = " ".join(_runtime_report_section().split())
    # The bounds, the per-value cap and the clause-text cap the section states
    # are the ones every runtime renders with.
    head, tail = runtime_report.STACK_HEAD, runtime_report.STACK_TAIL
    assert f"keeps its first {head} and its last {tail}" in section
    assert f"cut to {runtime_report.ARGUMENT_DISPLAY_LIMIT} code points" in section
    assert f"cut to {runtime_report.CLAUSE_TEXT_LIMIT} code points" in section
    for topic in (
        "**Async functions.**",
        "creation call",
        "when checked",
        "`checked_values`",
        "`stack_omitted`",
        "`GENO_TRACEBACK=host`",
    ):
        assert topic in section, topic
    # The compatibility note the Changes entry links exists.
    policy = (ROOT / "docs/operations/compatibility-policy.md").read_text()
    assert (
        "\n### 0.7 (draft): runtime failures report a Geno stack, and contract "
        "failures name their arguments\n"
    ) in policy


def _effects_section() -> str:
    """Section 3.5 of the Draft spec (proposal 0019)."""
    text = (ROOT / "docs/spec/v0.7.md").read_text(encoding="utf-8")
    start = text.index("\n### 3.5 Function Effects\n")
    return text[start : text.index("\n## 4. Declarations\n", start)]


def test_the_draft_records_local_mutation_masking() -> None:
    """Proposal 0019 is in the Draft spec, its Changes list and spec.json."""
    spec = _load_spec()
    assert any(
        "proposal 0019" in addition and "known limitation" in addition
        for addition in spec["development_line"]["additions"]
    )
    # The 0.6-normative effect block is left alone, as 0017 and 0018 left theirs.
    assert spec["effect_system"]["inference"].startswith(
        "Effects are inferred from effectful builtins, mutation,"
    )

    link = "](../proposals/0019-local-mutation-is-not-an-effect.md)"
    (entry,) = [entry for entry in _draft_changes() if link in entry]
    assert any(link in line for line in entry.splitlines())
    assert "3.5" in entry.splitlines()[0]
    assert not any(line.lstrip().startswith("- ") for line in entry.splitlines())
    assert "#07-draft-local-mutation-is-not-an-effect)" in entry

    section = " ".join(_effects_section().split())
    assert "The valid effect names in Geno 0.7 are" in section
    assert "Declaring a `var` is never an effect." in section
    # The builtin rule is normative; the checker's gap is a non-normative note.
    assert (
        "Writing a reference collection that is reachable from outside the "
        "writing function or lambda is a `mutation`, whether the write is an "
        "index assignment or a mutating builtin"
    ) in section
    assert "*Known limitation (0.7 draft).*" in section
    assert "does not snapshot it on any backend" in section

    policy = (ROOT / "docs/operations/compatibility-policy.md").read_text()
    assert "\n### 0.7 (draft): local mutation is not an effect\n" in policy


def _isolated_lane_section() -> str:
    """Section 9.11.1 of the Draft spec (proposal 0013)."""
    text = (ROOT / "docs/spec/v0.7.md").read_text(encoding="utf-8")
    start = text.index(
        "\n### 9.11.1 Capability Grants in the Isolated `geno run` Lane\n"
    )
    return text[start : text.index("\n### 9.12 ", start)]


def test_the_draft_records_isolated_run_capabilities() -> None:
    """Proposal 0013 is in the Draft spec, its Changes list and spec.json."""
    spec = _load_spec()
    text = (ROOT / "docs/spec/v0.7.md").read_text(encoding="utf-8")

    assert any(
        "proposal 0013" in addition
        for addition in spec["development_line"]["additions"]
    )
    assert _isolated_lane_section()
    assert any(
        "9.11.1" in entry
        and "](../proposals/0013-isolated-run-capabilities.md)" in entry
        for entry in _draft_changes()
    )
    # Section 9.11's env list names every builtin the `env` grant unlocks.
    builtins = text[text.index("\n### 9.11 Capability-Gated Builtins\n") :]
    assert "\ncli_args()" in builtins[: builtins.index("\n### 9.11.1 ")]


def _platform_rows() -> dict[str, tuple[str, str, str]]:
    """The 9.11.1 platform table: capability -> (Linux, macOS, Windows)."""
    section = _isolated_lane_section()
    table = section[section.index("| Capability | Linux | macOS | Windows |") :]
    rows: dict[str, tuple[str, str, str]] = {}
    for line in table.splitlines()[2:]:
        if not line.startswith("|"):
            break
        names, linux, macos, windows = (
            cell.strip() for cell in line.strip("|").split("|")
        )
        for name in re.findall(r"`([a-z]+)`", names):
            rows[name] = (linux, macos, windows)
    return rows


def test_the_draft_platform_table_matches_the_lane_rules() -> None:
    """The spec's matrix cannot drift from `geno.capabilities` (proposal 0013)."""
    from geno.capabilities import KNOWN_CAPABILITIES, isolated_run_capabilities

    rows = _platform_rows()

    assert set(rows) == set(KNOWN_CAPABILITIES)
    for name, (linux, macos, windows) in rows.items():
        posix = "granted" if name in isolated_run_capabilities("posix") else "refused"
        nt = "granted" if name in isolated_run_capabilities("nt") else "refused"
        assert (linux, macos, windows) == (posix, posix, nt), name


def test_the_draft_names_every_reserved_host_name() -> None:
    from geno.capabilities import ISOLATED_HOST_PRIVATE_NAMES

    section = _isolated_lane_section()
    paragraph = section[section.index("**Host bindings.**") :]
    paragraph = paragraph[: paragraph.index("\n\n")]

    named = set(re.findall(r"`(_[A-Za-z_]+)`", paragraph))
    assert named >= ISOLATED_HOST_PRIVATE_NAMES


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
