"""Security and execution contract for the scheduled deep-quality workflow."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "scheduled-quality.yml"


def _workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # PyYAML 1.1 treats the unquoted GitHub Actions key `on` as boolean true.
    value = workflow.get("on", workflow.get(True))
    assert isinstance(value, dict)
    return value


def test_scheduled_quality_has_bounded_manual_and_weekly_triggers() -> None:
    workflow = _workflow()
    triggers = _triggers(workflow)

    assert workflow["permissions"] == {"contents": "read"}
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert workflow["concurrency"]["cancel-in-progress"] is False
    job = workflow["jobs"]["deep-conformance-fuzz"]
    assert job["runs-on"] == "ubuntu-latest"
    assert 1 <= job["timeout-minutes"] <= 90


def test_scheduled_quality_runs_conformance_and_deep_fuzzing() -> None:
    steps = _workflow()["jobs"]["deep-conformance-fuzz"]["steps"]
    by_name = {step.get("name"): step for step in steps if "name" in step}

    conformance = by_name["Run frozen conformance corpus"]["run"]
    assert "scripts/run_conformance.py" in conformance
    assert "--all-retained" in conformance
    assert "--target all" in conformance
    assert "--require-node" in conformance

    fuzz = by_name["Run deep differential fuzzing"]
    assert fuzz["env"]["GENO_FUZZ_DEEP"] == "1"
    assert "geno/tests/test_differential_fuzzing.py" in fuzz["run"]
    assert "--timeout=900" in fuzz["run"]


def test_scheduled_quality_pins_actions_and_retains_failures() -> None:
    steps = _workflow()["jobs"]["deep-conformance-fuzz"]["steps"]
    action_refs = [step["uses"] for step in steps if "uses" in step]

    assert action_refs
    assert all(re.search(r"@[0-9a-f]{40}$", ref) for ref in action_refs)
    upload = next(
        step for step in steps if "actions/upload-artifact@" in step.get("uses", "")
    )
    assert upload["if"] == "failure()"
    assert ".hypothesis/" in upload["with"]["path"]
    assert "geno/tests/fuzzing/failures/" in upload["with"]["path"]
    assert upload["with"]["retention-days"] >= 14
