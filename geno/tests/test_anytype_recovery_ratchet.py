"""Tests for the AnyType recovery ratchet script."""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType


def _load_ratchet() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "check_anytype_recovery.py"
    spec = importlib.util.spec_from_file_location("check_anytype_recovery", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_anytype_recovery_baseline_matches_current_typechecker():
    ratchet = _load_ratchet()

    calls = ratchet.scan_anytype_calls()
    errors = ratchet.compare_to_baseline(ratchet.grouped_counts(calls))

    assert errors == []


def test_anytype_recovery_ratchet_reports_unclassified_sites():
    ratchet = _load_ratchet()

    errors = ratchet.compare_to_baseline(Counter({"_new_recovery_path": 1}))

    assert "_new_recovery_path: 1 unclassified AnyType() call(s)" in errors
