"""Regression tests for security documentation drift."""

from __future__ import annotations

import re
from pathlib import Path

from geno.builtin_registry import CAPABILITY_MAP

ROOT = Path(__file__).resolve().parents[2]


def _security_text() -> str:
    return (ROOT / "SECURITY.md").read_text(encoding="utf-8")


def _section(text: str, heading: str, next_heading: str) -> str:
    start = text.index(heading)
    end = text.index(next_heading, start)
    return text[start:end]


def _security_capability_table() -> dict[str, set[str]]:
    table = _section(_security_text(), "### Capability Map", "### Denied by Default")
    rows: dict[str, set[str]] = {}
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        capability_match = re.fullmatch(r"`([^`]+)`", cells[0])
        if capability_match is None:
            continue
        rows[capability_match.group(1)] = set(re.findall(r"`([^`]+)`", cells[1]))
    return rows


def test_security_capability_table_matches_registry():
    documented = _security_capability_table()
    expected = {
        capability: set(builtin_names)
        for capability, builtin_names in CAPABILITY_MAP.items()
    }

    assert documented == expected


def test_js_backend_security_docs_describe_capability_gating():
    section = _section(
        _security_text(),
        "### What the JS Backend Does NOT Provide",
        "### Intended Use Case",
    )

    assert "No capability gating" not in section
    assert "`--cap`" in section
    assert "`globalThis.__GENO_CAPS`" in section
    assert "`RunConfig`" in section
