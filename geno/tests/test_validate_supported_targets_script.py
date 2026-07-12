"""Regression tests for scripts/validate_supported_targets.py."""

from __future__ import annotations

from pathlib import Path

from scripts.validate_supported_targets import _load_targets, validate_supported_targets

ROOT = Path(__file__).resolve().parents[2]


def test_current_supported_targets_docs_pass_validation():
    assert validate_supported_targets() == []


def test_supported_targets_docs_clarify_test_target_execution_model():
    docs_text = (ROOT / "docs" / "SUPPORTED_TARGETS.md").read_text(encoding="utf-8")

    assert "geno test --target" in docs_text
    assert "target profile" in docs_text
    assert "interpreter" in docs_text
    assert "backend parity" in docs_text


def test_target_capability_drift_is_reported():
    targets = _load_targets()
    docs_text = (ROOT / "docs" / "SUPPORTED_TARGETS.md").read_text(encoding="utf-8")
    docs_text = docs_text.replace(
        "**Capabilities:** http, clock, random, print, regex",
        "**Capabilities:** http, process, clock, random, print, regex",
        1,
    )

    errors = validate_supported_targets(targets, docs_text)

    assert any(
        "Capabilities for target 'browser' mismatch" in error and "process" in error
        for error in errors
    )


def test_builtin_status_drift_is_reported():
    targets = _load_targets()
    docs_text = (ROOT / "docs" / "SUPPORTED_TARGETS.md").read_text(encoding="utf-8")
    docs_text = docs_text.replace(
        "| `exec` | process | Cap: process | -- | -- | -- |",
        "| `exec` | process | Cap: process | Cap: process | -- | -- |",
        1,
    )

    errors = validate_supported_targets(targets, docs_text)

    assert any(
        "Status for builtin 'exec' on target 'node-cli' mismatch" in error
        for error in errors
    )


def test_extra_builtin_row_is_reported():
    targets = _load_targets()
    docs_text = (ROOT / "docs" / "SUPPORTED_TARGETS.md").read_text(encoding="utf-8")
    docs_text = docs_text.replace(
        "| `regex_replace` | regex | Cap: regex | Cap: regex | Cap: regex | Cap: regex |",
        (
            "| `regex_replace` | regex | Cap: regex | Cap: regex | Cap: regex | Cap: regex |\n"
            "| `old_builtin` | fake | -- | -- | -- | -- |"
        ),
        1,
    )

    errors = validate_supported_targets(targets, docs_text)

    assert any(
        "Extra builtin rows" in error and "old_builtin" in error for error in errors
    )


def test_duplicate_builtin_row_is_reported():
    targets = _load_targets()
    docs_text = (ROOT / "docs" / "SUPPORTED_TARGETS.md").read_text(encoding="utf-8")
    row = "| `exec` | process | Cap: process | -- | -- | -- |"
    docs_text = docs_text.replace(row, f"{row}\n{row}", 1)

    errors = validate_supported_targets(targets, docs_text)

    assert any("Duplicate builtin row for 'exec'" in error for error in errors)


def test_extra_target_row_and_section_are_reported():
    targets = _load_targets()
    docs_text = (ROOT / "docs" / "SUPPORTED_TARGETS.md").read_text(encoding="utf-8")
    docs_text = docs_text.replace(
        "| `python-hosted` | Python | `geno serve` | `geno compile -o handler.py` |",
        (
            "| `python-hosted` | Python | `geno serve` | `geno compile -o handler.py` |\n"
            "| `wasm-cli` | WebAssembly | `geno wasm` | `geno compile --target wasm` |"
        ),
        1,
    )
    docs_text += "\n### wasm-cli\n\n**Capabilities:** print\n"

    errors = validate_supported_targets(targets, docs_text)

    assert any("Extra target rows" in error and "wasm-cli" in error for error in errors)
    assert any("unknown target 'wasm-cli'" in error for error in errors)


def test_target_table_metadata_drift_is_reported():
    targets = _load_targets()
    docs_text = (ROOT / "docs" / "SUPPORTED_TARGETS.md").read_text(encoding="utf-8")
    docs_text = docs_text.replace(
        "| `browser` | Browser | `geno build` | `geno build -o dist/` |",
        "| `browser` | Browser | `geno serve` | `geno build -o dist/` |",
        1,
    )

    errors = validate_supported_targets(targets, docs_text)

    assert any("Target 'browser' entry command mismatch" in error for error in errors)


def test_builtin_table_header_target_drift_is_reported():
    targets = _load_targets()
    docs_text = (ROOT / "docs" / "SUPPORTED_TARGETS.md").read_text(encoding="utf-8")
    docs_text = docs_text.replace(
        "| Builtin | Capability | python-cli | node-cli | browser | python-hosted |",
        "| Builtin | Capability | python-cli | wasm-cli | browser | python-hosted |",
        1,
    )

    errors = validate_supported_targets(targets, docs_text)

    assert any("Builtin table target columns mismatch" in error for error in errors)
