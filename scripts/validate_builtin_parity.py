#!/usr/bin/env python3
"""Validate builtin metadata parity across runtime surfaces."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VALID_AVAILABILITY = frozenset({"available", "capability-gated", "unavailable"})


def _format_names(names: set[str]) -> str:
    return ", ".join(sorted(names))


def _record_set_mismatch(
    errors: list[str],
    label: str,
    expected: set[str],
    actual: set[str],
) -> None:
    missing = expected - actual
    extra = actual - expected
    if not missing and not extra:
        return

    pieces: list[str] = []
    if missing:
        pieces.append(f"missing [{_format_names(missing)}]")
    if extra:
        pieces.append(f"extra [{_format_names(extra)}]")
    errors.append(f"{label}: " + "; ".join(pieces))


def _load_targets(path: Path | None = None) -> dict[str, Any]:
    target_path = path or (ROOT / "targets.toml")
    return cast(dict[str, Any], tomllib.loads(target_path.read_text(encoding="utf-8")))


def validate_manifest_registry_surface() -> list[str]:
    """Return errors for manifest/registry drift."""
    from geno.builtin_manifest import BUILTIN_MANIFEST
    from geno.builtin_registry import (
        BUILTIN_PARAM_NAMES,
        BUILTIN_REGISTRY,
        CAPABILITY_MAP,
        SOURCE_BUILTIN_NAME_OVERRIDES,
        build_builtin_signatures,
        source_builtin_specs,
    )

    errors: list[str] = []
    _record_set_mismatch(
        errors,
        "Manifest/runtime builtin names mismatch",
        set(BUILTIN_MANIFEST),
        set(BUILTIN_PARAM_NAMES),
    )
    _record_set_mismatch(
        errors,
        "Manifest/registry runtime names mismatch",
        set(BUILTIN_MANIFEST),
        set(BUILTIN_REGISTRY),
    )

    expected_source_names = {
        SOURCE_BUILTIN_NAME_OVERRIDES.get(runtime_name, runtime_name)
        for runtime_name in BUILTIN_MANIFEST
    }
    source_specs = source_builtin_specs()
    _record_set_mismatch(
        errors,
        "Manifest/registry source names mismatch",
        expected_source_names,
        set(source_specs),
    )

    signatures = build_builtin_signatures()
    _record_set_mismatch(
        errors,
        "Registry/signature builtin names mismatch",
        set(source_specs),
        set(signatures),
    )

    capability_by_builtin = {
        builtin_name: capability
        for capability, builtin_names in CAPABILITY_MAP.items()
        for builtin_name in builtin_names
    }
    for runtime_name, (params, capability) in BUILTIN_MANIFEST.items():
        spec = BUILTIN_REGISTRY.get(runtime_name)
        if spec is None:
            continue
        if tuple(params) != spec.runtime_param_names:
            errors.append(
                f"Runtime params for builtin '{runtime_name}' mismatch: "
                f"manifest has {params!r}, registry has {list(spec.runtime_param_names)!r}"
            )
        if spec.capability != capability:
            errors.append(
                f"Capability for builtin '{spec.source_name}' mismatch: "
                f"manifest has {capability!r}, registry has {spec.capability!r}"
            )
        expected_always_available = capability is None
        if spec.always_available != expected_always_available:
            errors.append(
                f"Availability classification for builtin '{spec.source_name}' "
                f"mismatch: expected always_available={expected_always_available}, "
                f"got {spec.always_available}"
            )
        mapped_capability = capability_by_builtin.get(spec.source_name)
        if capability != mapped_capability:
            errors.append(
                f"CAPABILITY_MAP entry for builtin '{spec.source_name}' mismatch: "
                f"manifest has {capability!r}, map has {mapped_capability!r}"
            )
    return errors


def validate_typechecker_surface(
    *,
    builtin_types: Mapping[str, tuple[Any, list[str]]] | None = None,
    func_param_names: Mapping[str, list[str]] | None = None,
) -> list[str]:
    """Return errors for typechecker builtin signature/param drift."""
    from geno.builtin_registry import (
        build_builtin_signatures,
        source_builtin_param_name_lists,
    )
    from geno.typechecker import TypeChecker

    checker = None
    if builtin_types is None or func_param_names is None:
        checker = TypeChecker()
    actual_builtin_types = (
        builtin_types if builtin_types is not None else checker.builtin_types  # type: ignore[union-attr]
    )
    actual_param_names = (
        func_param_names if func_param_names is not None else checker.func_param_names  # type: ignore[union-attr]
    )
    expected_signatures = build_builtin_signatures()
    expected_params = source_builtin_param_name_lists()

    errors: list[str] = []
    _record_set_mismatch(
        errors,
        "Typechecker builtin names mismatch",
        set(expected_signatures),
        set(actual_builtin_types),
    )
    _record_set_mismatch(
        errors,
        "Typechecker param-name builtin names mismatch",
        set(expected_params),
        set(actual_param_names),
    )

    for name, expected_signature in expected_signatures.items():
        actual_entry = actual_builtin_types.get(name)
        if actual_entry is None:
            continue
        actual_signature, actual_params_from_type = actual_entry
        expected_param_names = expected_params[name]
        if actual_signature != expected_signature:
            errors.append(
                f"Typechecker signature for builtin '{name}' mismatch: "
                f"expected {expected_signature}, got {actual_signature}"
            )
        if actual_params_from_type != expected_param_names:
            errors.append(
                f"Typechecker builtin_types params for builtin '{name}' mismatch: "
                f"expected {expected_param_names!r}, got {actual_params_from_type!r}"
            )
        actual_params = actual_param_names.get(name)
        if actual_params != expected_param_names:
            errors.append(
                f"Typechecker func_param_names for builtin '{name}' mismatch: "
                f"expected {expected_param_names!r}, got {actual_params!r}"
            )
    return errors


def validate_interpreter_surface(
    *,
    bindings: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return errors for interpreter builtin registration drift."""
    from geno.builtin_registry import interpreter_builtin_param_name_lists
    from geno.interpreter import Interpreter
    from geno.values import BuiltinFunction

    actual_bindings = (
        bindings
        if bindings is not None
        else Interpreter(check_examples=False).global_env.bindings
    )
    expected_params = interpreter_builtin_param_name_lists()
    actual_builtin_names = {
        name
        for name, value in actual_bindings.items()
        if isinstance(value, BuiltinFunction)
    }

    errors: list[str] = []
    _record_set_mismatch(
        errors,
        "Interpreter builtin names mismatch",
        set(expected_params),
        actual_builtin_names,
    )

    for name, expected_param_names in expected_params.items():
        value = actual_bindings.get(name)
        if not isinstance(value, BuiltinFunction):
            continue
        if value.param_names != expected_param_names:
            errors.append(
                f"Interpreter params for builtin '{name}' mismatch: "
                f"expected {expected_param_names!r}, got {value.param_names!r}"
            )
        if value.arity >= 0 and value.arity != len(expected_param_names):
            errors.append(
                f"Interpreter arity for builtin '{name}' mismatch: "
                f"expected {len(expected_param_names)}, got {value.arity}"
            )
    return errors


def validate_backend_surface(
    *,
    python_name_map: Mapping[str, str] | None = None,
    js_name_map: Mapping[str, str] | None = None,
    python_func_param_names: Mapping[str, list[str]] | None = None,
    js_func_param_names: Mapping[str, list[str]] | None = None,
) -> list[str]:
    """Return errors for compiler backend builtin metadata drift."""
    from geno.builtin_registry import (
        builtin_param_name_lists,
        js_backend_builtin_helper_names,
        js_backend_builtin_name_map,
        python_backend_builtin_helper_names,
        python_backend_builtin_name_map,
    )
    from geno.compiler import RESERVED_PRELUDE_NAMES, Compiler
    from geno.js_compiler import JS_RESERVED_PRELUDE_NAMES, JSCompiler

    expected_python_map = python_backend_builtin_name_map()
    expected_js_map = js_backend_builtin_name_map()
    actual_python_map = (
        python_name_map if python_name_map is not None else Compiler._BUILTIN_NAME_MAP
    )
    actual_js_map = (
        js_name_map if js_name_map is not None else JSCompiler._BUILTIN_NAME_MAP
    )

    errors: list[str] = []
    if dict(actual_python_map) != expected_python_map:
        errors.append(
            "Python backend builtin remap mismatch: "
            f"expected {expected_python_map!r}, got {dict(actual_python_map)!r}"
        )
    if dict(actual_js_map) != expected_js_map:
        errors.append(
            "JavaScript backend builtin remap mismatch: "
            f"expected {expected_js_map!r}, got {dict(actual_js_map)!r}"
        )

    missing_python_helpers = (
        python_backend_builtin_helper_names() - RESERVED_PRELUDE_NAMES
    )
    if missing_python_helpers:
        errors.append(
            "Python backend helper names missing from reserved prelude names: "
            + _format_names(set(missing_python_helpers))
        )
    missing_js_helpers = js_backend_builtin_helper_names() - JS_RESERVED_PRELUDE_NAMES
    if missing_js_helpers:
        errors.append(
            "JavaScript backend helper names missing from reserved prelude names: "
            + _format_names(set(missing_js_helpers))
        )

    expected_params = builtin_param_name_lists()
    compiler = None
    js_compiler = None
    if python_func_param_names is None:
        compiler = Compiler()
    if js_func_param_names is None:
        js_compiler = JSCompiler()
    actual_python_params = (
        python_func_param_names
        if python_func_param_names is not None
        else compiler.func_param_names  # type: ignore[union-attr]
    )
    actual_js_params = (
        js_func_param_names
        if js_func_param_names is not None
        else js_compiler.func_param_names  # type: ignore[union-attr]
    )
    for label, actual_params in (
        ("Python backend", actual_python_params),
        ("JavaScript backend", actual_js_params),
    ):
        _record_set_mismatch(
            errors,
            f"{label} param-name builtin names mismatch",
            set(expected_params),
            set(actual_params),
        )
        for name, expected_param_names in expected_params.items():
            actual_param_names = actual_params.get(name)
            if actual_param_names != expected_param_names:
                errors.append(
                    f"{label} params for builtin '{name}' mismatch: "
                    f"expected {expected_param_names!r}, got {actual_param_names!r}"
                )
    return errors


def validate_backend_contract_surface(
    targets: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return errors for runtime/backend target contract drift."""
    from geno.backend_contract import (
        BROWSER_TARGET,
        browser_interpreter_fallback_names,
        browser_only_builtin_names,
        builtin_backend_contracts,
    )
    from geno.builtin_registry import source_builtin_specs
    from geno.target_profile import VALID_TARGETS

    errors: list[str] = []
    try:
        contracts = builtin_backend_contracts(targets)
    except ValueError as exc:
        return [f"Backend contract target availability invalid: {exc}"]

    source_specs = source_builtin_specs()
    _record_set_mismatch(
        errors,
        "Backend contract source names mismatch",
        set(source_specs),
        set(contracts),
    )

    for name, spec in sorted(source_specs.items()):
        contract = contracts.get(name)
        if contract is None:
            continue
        if contract.runtime_name != spec.runtime_name:
            errors.append(
                f"Backend contract runtime name for builtin '{name}' mismatch: "
                f"expected {spec.runtime_name!r}, got {contract.runtime_name!r}"
            )
        if contract.runtime_param_names != spec.runtime_param_names:
            errors.append(
                f"Backend contract runtime params for builtin '{name}' mismatch: "
                f"expected {list(spec.runtime_param_names)!r}, "
                f"got {list(contract.runtime_param_names)!r}"
            )
        if contract.source_param_names != spec.source_param_names:
            errors.append(
                f"Backend contract source params for builtin '{name}' mismatch: "
                f"expected {list(spec.source_param_names)!r}, "
                f"got {list(contract.source_param_names)!r}"
            )
        if contract.capability != spec.capability:
            errors.append(
                f"Backend contract capability for builtin '{name}' mismatch: "
                f"expected {spec.capability!r}, got {contract.capability!r}"
            )
        if contract.python_backend_name != spec.python_backend_name:
            errors.append(
                f"Backend contract Python helper for builtin '{name}' mismatch: "
                f"expected {spec.python_backend_name!r}, "
                f"got {contract.python_backend_name!r}"
            )
        if contract.js_backend_name != spec.js_backend_name:
            errors.append(
                f"Backend contract JavaScript helper for builtin '{name}' mismatch: "
                f"expected {spec.js_backend_name!r}, got {contract.js_backend_name!r}"
            )

    browser_only = browser_only_builtin_names(targets)
    interpreter_fallbacks = browser_interpreter_fallback_names()
    _record_set_mismatch(
        errors,
        "Browser-only interpreter fallback contract mismatch",
        set(browser_only),
        set(interpreter_fallbacks),
    )

    for name in sorted(interpreter_fallbacks):
        contract = contracts.get(name)
        if contract is None:
            continue
        if contract.status_for(BROWSER_TARGET) != "available":
            errors.append(
                f"Browser fallback builtin '{name}' is not available on "
                f"target '{BROWSER_TARGET}'"
            )
        for target in sorted(VALID_TARGETS - {BROWSER_TARGET}):
            if contract.status_for(target) != "unavailable":
                errors.append(
                    f"Browser fallback builtin '{name}' must be unavailable on "
                    f"target '{target}', got {contract.status_for(target)!r}"
                )
    return errors


def validate_target_surface(
    targets: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return errors for targets.toml and TargetProfile builtin drift."""
    from geno.builtin_registry import CAPABILITY_MAP, source_builtin_specs
    from geno.target_profile import VALID_TARGETS, TargetProfile

    raw = targets if targets is not None else _load_targets()
    target_infos = cast(Mapping[str, Any], raw.get("targets", {}))
    builtin_infos = cast(Mapping[str, Any], raw.get("builtins", {}))
    source_specs = source_builtin_specs()
    known_capabilities = set(CAPABILITY_MAP)
    targets_to_check = tuple(sorted(VALID_TARGETS))
    expected_gated = {
        name for name, spec in source_specs.items() if spec.capability is not None
    }

    errors: list[str] = []
    _record_set_mismatch(
        errors,
        "Target names mismatch",
        set(VALID_TARGETS),
        set(target_infos),
    )
    _record_set_mismatch(
        errors,
        "targets.toml missing capability-gated builtin names",
        expected_gated,
        set(builtin_infos) & expected_gated,
    )
    unknown_target_builtins = set(builtin_infos) - set(source_specs)
    if unknown_target_builtins:
        errors.append(
            "targets.toml has unknown explicit builtin names: "
            + _format_names(unknown_target_builtins)
        )

    target_capabilities: dict[str, set[str]] = {}
    for target in targets_to_check:
        raw_target_info = target_infos.get(target, {})
        caps = set(cast(Mapping[str, Any], raw_target_info).get("capabilities", []))
        target_capabilities[target] = caps
        unknown_caps = caps - known_capabilities
        if unknown_caps:
            errors.append(
                f"Target '{target}' declares unknown capabilities: "
                + _format_names(unknown_caps)
            )

    for builtin_name, raw_info in sorted(builtin_infos.items()):
        info = cast(Mapping[str, Any], raw_info)
        spec = source_specs.get(builtin_name)
        if spec is None:
            errors.append(f"targets.toml has unknown builtin '{builtin_name}'")
            continue

        declared_capability = info.get("capability")
        if declared_capability != spec.capability:
            errors.append(
                f"targets.toml capability for builtin '{builtin_name}' mismatch: "
                f"expected {spec.capability!r}, got {declared_capability!r}"
            )

        for target in targets_to_check:
            if target not in info:
                errors.append(
                    f"targets.toml builtin '{builtin_name}' is missing status for "
                    f"target '{target}'"
                )
                continue
            status = info.get(target)
            if status not in VALID_AVAILABILITY:
                errors.append(
                    f"targets.toml builtin '{builtin_name}' has invalid status for "
                    f"target '{target}': {status!r}"
                )
                continue
            if status == "available" and spec.capability is not None:
                errors.append(
                    f"targets.toml builtin '{builtin_name}' is capability-backed "
                    f"but marked available on target '{target}'"
                )
            if status == "capability-gated":
                if spec.capability is None:
                    errors.append(
                        f"targets.toml builtin '{builtin_name}' is gated on target "
                        f"'{target}' but the registry has no capability"
                    )
                    continue
                if spec.capability not in target_capabilities.get(target, set()):
                    errors.append(
                        f"targets.toml builtin '{builtin_name}' is gated by "
                        f"{spec.capability!r} on target '{target}', but that target "
                        "does not declare the capability"
                    )

    # Verify the runtime loader derives the same unavailable/gated views.
    for target in targets_to_check:
        if target not in target_infos:
            continue
        profile = TargetProfile.load(target)
        expected_unavailable = {
            name
            for name, info in builtin_infos.items()
            if cast(Mapping[str, Any], info).get(target) == "unavailable"
        }
        expected_gated_map = {
            name: cast(str, cast(Mapping[str, Any], info).get("capability"))
            for name, info in builtin_infos.items()
            if cast(Mapping[str, Any], info).get(target) == "capability-gated"
        }
        if profile.unavailable != expected_unavailable:
            errors.append(
                f"TargetProfile unavailable builtins for '{target}' mismatch: "
                f"expected {sorted(expected_unavailable)!r}, "
                f"got {sorted(profile.unavailable)!r}"
            )
        if profile.capability_gated != expected_gated_map:
            errors.append(
                f"TargetProfile gated builtins for '{target}' mismatch: "
                f"expected {expected_gated_map!r}, got {profile.capability_gated!r}"
            )
        if profile.capabilities != target_capabilities[target]:
            errors.append(
                f"TargetProfile capabilities for '{target}' mismatch: "
                f"expected {sorted(target_capabilities[target])!r}, "
                f"got {sorted(profile.capabilities)!r}"
            )
    return errors


def validate_builtin_parity() -> list[str]:
    """Return all builtin parity validation errors."""
    return [
        *validate_manifest_registry_surface(),
        *validate_typechecker_surface(),
        *validate_interpreter_surface(),
        *validate_backend_surface(),
        *validate_backend_contract_surface(),
        *validate_target_surface(),
    ]


def main() -> int:
    errors = validate_builtin_parity()
    if errors:
        print("builtin parity validation errors:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("builtin parity validation OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
