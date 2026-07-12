"""Regression tests for scripts/validate_builtin_parity.py."""

from __future__ import annotations

from copy import deepcopy

from geno.interpreter import Interpreter
from scripts.validate_builtin_parity import (
    _load_targets,
    validate_backend_contract_surface,
    validate_backend_surface,
    validate_builtin_parity,
    validate_interpreter_surface,
    validate_target_surface,
    validate_typechecker_surface,
)


def test_current_builtin_parity_passes_validation():
    assert validate_builtin_parity() == []


def test_typechecker_missing_builtin_is_reported():
    from geno.typechecker import TypeChecker

    checker = TypeChecker()
    builtin_types = dict(checker.builtin_types)
    builtin_types.pop("length")

    errors = validate_typechecker_surface(
        builtin_types=builtin_types,
        func_param_names=checker.func_param_names,
    )

    assert any(
        "Typechecker builtin names mismatch" in error and "length" in error
        for error in errors
    )


def test_interpreter_missing_builtin_is_reported():
    interpreter = Interpreter(check_examples=False)
    bindings = dict(interpreter.global_env.bindings)
    bindings.pop("length")

    errors = validate_interpreter_surface(bindings=bindings)

    assert any(
        "Interpreter builtin names mismatch" in error and "length" in error
        for error in errors
    )


def test_backend_remap_drift_is_reported():
    from geno.compiler import Compiler

    python_name_map = dict(Compiler._BUILTIN_NAME_MAP)
    python_name_map.pop("exec")

    errors = validate_backend_surface(python_name_map=python_name_map)

    assert any("Python backend builtin remap mismatch" in error for error in errors)


def test_missing_target_builtin_is_reported():
    targets = deepcopy(_load_targets())
    targets["builtins"].pop("exec")

    errors = validate_target_surface(targets)

    assert any(
        "targets.toml missing capability-gated builtin names" in error
        and "exec" in error
        for error in errors
    )


def test_target_capability_drift_is_reported():
    targets = deepcopy(_load_targets())
    targets["builtins"]["exec"]["capability"] = "http"

    errors = validate_target_surface(targets)

    assert any(
        "targets.toml capability for builtin 'exec' mismatch" in error
        and "process" in error
        for error in errors
    )


def test_target_status_drift_is_reported():
    targets = deepcopy(_load_targets())
    targets["builtins"]["exec"]["node-cli"] = "available"

    errors = validate_target_surface(targets)

    assert any(
        "builtin 'exec' is capability-backed but marked available" in error
        for error in errors
    )


def test_backend_contract_browser_fallback_target_drift_is_reported():
    targets = deepcopy(_load_targets())
    targets["builtins"]["clear_screen"]["python-cli"] = "available"

    errors = validate_backend_contract_surface(targets)

    assert any(
        "Browser fallback builtin 'clear_screen' must be unavailable" in error
        and "python-cli" in error
        for error in errors
    )
