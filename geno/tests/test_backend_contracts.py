"""Executable backend/runtime contract tests."""

from __future__ import annotations

import pytest

from geno import builtins as builtin_impl
from geno.backend_contract import (
    BROWSER_TARGET,
    browser_interpreter_fallback_names,
    browser_interpreter_fallbacks,
    browser_only_builtin_names,
    builtin_backend_contracts,
)
from geno.builtin_registry import source_builtin_specs
from geno.target_profile import VALID_TARGETS

FALLBACK_CALLS = {
    "clear_screen": (builtin_impl.builtin_clear_screen, ("#000",), None),
    "draw_rect": (builtin_impl.builtin_draw_rect, (0, 0, 10, 10, "#000"), None),
    "draw_rect_outline": (
        builtin_impl.builtin_draw_rect_outline,
        (0, 0, 10, 10, "#000"),
        None,
    ),
    "draw_circle": (builtin_impl.builtin_draw_circle, (0, 0, 10, "#000"), None),
    "draw_line": (builtin_impl.builtin_draw_line, (0, 0, 10, 10, "#000"), None),
    "draw_text": (builtin_impl.builtin_draw_text, ("hi", 0, 0, 16, "#000"), None),
    "screen_width": (builtin_impl.builtin_screen_width, (), 800),
    "screen_height": (builtin_impl.builtin_screen_height, (), 600),
    "is_key_down": (builtin_impl.builtin_is_key_down, ("ArrowLeft",), False),
    "is_key_pressed": (builtin_impl.builtin_is_key_pressed, ("ArrowLeft",), False),
    "mouse_x": (builtin_impl.builtin_mouse_x, (), 0),
    "mouse_y": (builtin_impl.builtin_mouse_y, (), 0),
    "is_mouse_down": (builtin_impl.builtin_is_mouse_down, (), False),
    "is_mouse_clicked": (builtin_impl.builtin_is_mouse_clicked, (), False),
    "get_text_input": (builtin_impl.builtin_get_text_input, (), ""),
    "clear_text_input": (builtin_impl.builtin_clear_text_input, (), None),
}


def test_browser_only_target_matrix_matches_interpreter_fallback_contract():
    assert browser_only_builtin_names() == browser_interpreter_fallback_names()


def test_browser_fallback_contract_has_executable_interpreter_behavior():
    assert set(FALLBACK_CALLS) == set(browser_interpreter_fallbacks())

    for name, (func, args, expected) in FALLBACK_CALLS.items():
        assert func(*args) == expected, name


def _math_contract_names() -> list[str]:
    return sorted(name for name in source_builtin_specs() if name.startswith("math_"))


@pytest.mark.parametrize("name", _math_contract_names())
def test_manifest_derived_math_group_backend_contracts(name: str):
    """Representative group conformance is derived from builtin manifest data."""

    spec = source_builtin_specs()[name]
    contract = builtin_backend_contracts()[name]

    assert contract.source_name == name
    assert contract.runtime_name == spec.runtime_name
    assert contract.runtime_param_names == spec.runtime_param_names
    assert contract.source_param_names == spec.source_param_names
    assert contract.capability == spec.capability
    assert contract.python_backend_name == spec.python_backend_name
    assert contract.js_backend_name == spec.js_backend_name

    expected_status = "capability-gated" if spec.capability else "available"
    for target in sorted(VALID_TARGETS):
        assert contract.status_for(target) == expected_status


def test_browser_graphics_contract_is_target_aware_not_capability_gated():
    contracts = builtin_backend_contracts()

    for name in sorted(browser_interpreter_fallback_names()):
        contract = contracts[name]
        assert contract.capability is None
        assert contract.status_for(BROWSER_TARGET) == "available"
        for target in sorted(VALID_TARGETS - {BROWSER_TARGET}):
            assert contract.status_for(target) == "unavailable"
