"""Tests for scripts/check_selfhost_parity.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

from geno.builtin_registry import all_builtin_names

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_selfhost_parity.py"


def _load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_selfhost_parity_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCheckSelfhostParityScript:
    def test_check_keywords_fails_on_unknown_canonical_gap(self) -> None:
        module: Any = _load_script_module()
        module.warnings.clear()
        module.errors.clear()
        module.SELFHOST_REQUIRED_KEYWORDS = {"present"}
        module.KNOWN_KEYWORD_GAPS = {"known_gap"}
        module.get_canonical_keywords = lambda: {"present", "known_gap", "new_gap"}
        module.get_selfhost_keywords = lambda: {"present"}

        module.check_keywords()

        assert any("new_gap" in error for error in module.errors)

    def test_check_keywords_allows_known_gap(self) -> None:
        module: Any = _load_script_module()
        module.warnings.clear()
        module.errors.clear()
        module.SELFHOST_REQUIRED_KEYWORDS = {"present"}
        module.KNOWN_KEYWORD_GAPS = {"known_gap"}
        module.get_canonical_keywords = lambda: {"present", "known_gap"}
        module.get_selfhost_keywords = lambda: {"present"}

        module.check_keywords()

        assert module.errors == []

    def test_check_keywords_fails_on_resolved_known_gap(self) -> None:
        module: Any = _load_script_module()
        module.warnings.clear()
        module.errors.clear()
        module.SELFHOST_REQUIRED_KEYWORDS = {"present"}
        module.KNOWN_KEYWORD_GAPS = {"known_gap"}
        module.get_canonical_keywords = lambda: {"present", "known_gap"}
        module.get_selfhost_keywords = lambda: {"present", "known_gap"}

        module.check_keywords()

        assert any("known_gap" in error for error in module.errors)

    def test_get_canonical_builtins_uses_full_registry_surface(self) -> None:
        module = _load_script_module()
        assert module.get_canonical_builtins() == set(all_builtin_names())

    def test_keyword_gap_groups_cover_declared_gaps(self) -> None:
        module = _load_script_module()

        assert (
            module._union_groups(module.SELFHOST_KEYWORD_GAP_GROUPS)
            == module.KNOWN_KEYWORD_GAPS
        )
        assert "try" in module.SELFHOST_KEYWORD_GAP_GROUPS["exception_control"]
        assert "test" in module.SELFHOST_KEYWORD_GAP_GROUPS["tests_and_exports"]
        assert "export" in module.SELFHOST_KEYWORD_GAP_GROUPS["tests_and_exports"]

    def test_builtin_gap_groups_cover_declared_gaps(self) -> None:
        module = _load_script_module()

        assert (
            module._union_groups(module.SELFHOST_BUILTIN_GAP_GROUPS)
            == module.KNOWN_BUILTIN_GAPS
        )
        assert list(module.SELFHOST_BUILTIN_GAP_GROUPS)[:5] == [
            "pure_collection_next",
            "pure_string_next",
            "pure_math_next",
            "pure_option_result_next",
            "pure_path_data_next",
        ]
        assert "exec" in module.SELFHOST_BUILTIN_GAP_GROUPS["host_dependent"]

    def test_check_budget_groups_reports_duplicate_gap(self) -> None:
        module: Any = _load_script_module()
        module.warnings.clear()
        module.errors.clear()
        module.SELFHOST_KEYWORD_GAP_GROUPS = {
            "exception_control": {"try"},
            "duplicate_exception_control": {"try"},
        }
        module.KNOWN_KEYWORD_GAPS = {"try"}
        module.SELFHOST_BUILTIN_GAP_GROUPS = {"pure_collection_next": {"range"}}
        module.KNOWN_BUILTIN_GAPS = {"range"}

        module.check_budget_groups()

        assert any("try" in error for error in module.errors)

    def test_check_budget_groups_reports_declared_gap_drift(self) -> None:
        module: Any = _load_script_module()
        module.warnings.clear()
        module.errors.clear()
        module.SELFHOST_KEYWORD_GAP_GROUPS = {"exception_control": {"try"}}
        module.KNOWN_KEYWORD_GAPS = {"try", "catch"}
        module.SELFHOST_BUILTIN_GAP_GROUPS = {"pure_collection_next": {"range"}}
        module.KNOWN_BUILTIN_GAPS = {"range"}

        module.check_budget_groups()

        assert any("catch" in error for error in module.errors)

    def test_check_builtins_fails_on_unknown_canonical_gap(self) -> None:
        module: Any = _load_script_module()
        module.warnings.clear()
        module.errors.clear()
        module.KNOWN_BUILTIN_GAPS = {"known_gap"}
        module.get_canonical_builtins = lambda: {"present", "known_gap", "new_gap"}
        module.get_selfhost_builtins = lambda: {"present"}

        module.check_builtins()

        assert any("new_gap" in error for error in module.errors)

    def test_check_builtins_allows_known_gap(self) -> None:
        module: Any = _load_script_module()
        module.warnings.clear()
        module.errors.clear()
        module.KNOWN_BUILTIN_GAPS = {"known_gap"}
        module.get_canonical_builtins = lambda: {"present", "known_gap"}
        module.get_selfhost_builtins = lambda: {"present"}

        module.check_builtins()

        assert module.errors == []

    def test_check_builtins_fails_on_phantom_builtin(self) -> None:
        module: Any = _load_script_module()
        module.warnings.clear()
        module.errors.clear()
        module.KNOWN_BUILTIN_GAPS = set()
        module.get_canonical_builtins = lambda: {"present"}
        module.get_selfhost_builtins = lambda: {"present", "phantom_builtin"}

        module.check_builtins()

        assert any("phantom_builtin" in error for error in module.errors)

    def test_check_builtins_fails_on_resolved_known_gap(self) -> None:
        module: Any = _load_script_module()
        module.warnings.clear()
        module.errors.clear()
        module.KNOWN_BUILTIN_GAPS = {"known_gap"}
        module.get_canonical_builtins = lambda: {"present", "known_gap"}
        module.get_selfhost_builtins = lambda: {"present", "known_gap"}

        module.check_builtins()

        assert any("known_gap" in error for error in module.errors)
