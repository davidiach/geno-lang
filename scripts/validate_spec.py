#!/usr/bin/env python3
"""Validate spec.json against canonical source metadata."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_STD_FUNC_RE = re.compile(r"^func\s+([A-Za-z_][A-Za-z0-9_]*)\(", re.MULTILINE)

_SPEC_BUILTIN_SOURCE_OVERRIDES: dict[tuple[str, str], str] = {
    ("list", "zip"): "list_zip",
    ("list", "enumerate"): "list_enumerate",
    ("list", "flatten"): "list_flatten",
    ("list", "chunk"): "list_chunk",
    ("list", "take"): "list_take",
    ("list", "drop"): "list_drop",
    ("list", "find"): "list_find",
    ("list", "find_index"): "list_find_index",
    ("list", "all"): "list_all",
    ("list", "any"): "list_any",
    ("list", "intersperse"): "list_intersperse",
    ("list", "group_by"): "list_group_by",
    ("higher_order", "fold_right"): "list_fold_right",
    ("string", "trim_start"): "string_trim_start",
    ("string", "trim_end"): "string_trim_end",
    ("string", "pad_left"): "string_pad_left",
    ("string", "pad_right"): "string_pad_right",
    ("string", "index_of"): "string_index_of",
    ("string", "last_index_of"): "string_last_index_of",
    ("math", "abs"): "math_abs",
    ("math", "min"): "math_min",
    ("math", "max"): "math_max",
    ("math", "clamp"): "math_clamp",
    ("math", "floor"): "math_floor",
    ("math", "ceil"): "math_ceil",
    ("math", "round"): "math_round",
    ("math", "sqrt"): "math_sqrt",
    ("math", "log"): "math_log",
    ("math", "sin"): "math_sin",
    ("math", "cos"): "math_cos",
    ("math", "pi"): "math_pi",
    ("math", "e"): "math_e",
    ("math", "random_int"): "math_random_int",
    ("math", "random_float"): "math_random_float",
}

_SOURCE_SIGNATURE_OVERRIDES: dict[str, str] = {
    "range": "(start: Int, end: Int, step?: Int positional-only) -> List[Int]",
}


def load_spec(path: Path | None = None) -> dict[str, Any]:
    spec_path = path or (ROOT / "spec.json")
    return cast(dict[str, Any], json.loads(spec_path.read_text()))


def get_source_keywords() -> set[str]:
    from geno.tokens import KEYWORDS

    return set(KEYWORDS.keys())


def get_source_compilation_targets() -> set[str]:
    # Geno currently has two code generation backends: Python and JavaScript.
    return {"python", "javascript"}


def get_source_execution_targets() -> set[str]:
    from geno.target_profile import VALID_TARGETS

    return set(VALID_TARGETS)


def get_source_capabilities() -> set[str]:
    from geno.builtin_registry import CAPABILITY_MAP

    return set(CAPABILITY_MAP.keys())


def get_source_lifecycle() -> set[str]:
    return {"init", "update", "render"}


def _read_stdlib_function_names(module_name: str) -> set[str]:
    text = (ROOT / "geno" / "std" / f"{module_name}.geno").read_text()
    return set(_STD_FUNC_RE.findall(text))


def _require_direct_builtin_names(*names: str) -> set[str]:
    from geno.builtin_registry import BUILTIN_REGISTRY

    source_names = {spec.source_name for spec in BUILTIN_REGISTRY.values()}
    required = set(names)
    missing = required - source_names
    if missing:
        raise RuntimeError(
            "validate_spec.py expected builtin names missing from source metadata: "
            + ", ".join(sorted(missing))
        )
    return required


def _require_stdlib_builtin_names(module_name: str, *names: str) -> set[str]:
    exported = _read_stdlib_function_names(module_name)
    required = set(names)
    missing = required - exported
    if missing:
        raise RuntimeError(
            f"validate_spec.py expected stdlib functions missing from {module_name}.geno: "
            + ", ".join(sorted(missing))
        )
    return required


def get_source_builtin_categories() -> dict[str, set[str]]:
    return {
        "list": _require_direct_builtin_names(
            "length",
            "head",
            "tail",
            "append",
            "concat",
            "contains",
            "reverse",
            "slice",
            "set_at",
            "is_sorted",
        )
        | _require_stdlib_builtin_names(
            "List",
            "zip",
            "enumerate",
            "flatten",
            "chunk",
            "take",
            "drop",
            "find",
            "find_index",
            "all",
            "any",
            "intersperse",
            "group_by",
        ),
        "higher_order": _require_direct_builtin_names(
            "map",
            "filter",
            "fold",
            "flat_map",
            "take_while",
        )
        | _require_stdlib_builtin_names("List", "fold_right"),
        "string": _require_direct_builtin_names(
            "split",
            "join",
            "trim",
            "to_lower",
            "to_upper",
            "replace",
            "starts_with",
            "ends_with",
            "substring",
            "contains_substring",
            "char_code",
            "from_char_code",
            "to_chars",
            "repeat_string",
            "sort_strings",
            "string_char_at",
            "split_once",
        )
        | _require_stdlib_builtin_names(
            "String",
            "trim_start",
            "trim_end",
            "pad_left",
            "pad_right",
            "index_of",
            "last_index_of",
        ),
        "math": _require_direct_builtin_names("divide", "range")
        | _require_stdlib_builtin_names(
            "Math",
            "abs",
            "min",
            "max",
            "clamp",
            "floor",
            "ceil",
            "round",
            "sqrt",
            "log",
            "sin",
            "cos",
            "pi",
            "e",
            "random_int",
            "random_float",
        ),
        "parsing": _require_direct_builtin_names(
            "parse_int",
            "parse_float",
            "is_numeric_string",
            "format",
        ),
        "option_result": _require_direct_builtin_names(
            "is_some",
            "is_none",
            "unwrap",
            "unwrap_or",
            "result_unwrap_or",
            "result_is_ok",
            "result_is_err",
            "result_to_option",
        ),
        "map": _require_direct_builtin_names(
            "map_insert",
            "map_get",
            "map_from_list",
            "map_merge",
            "map_entries",
            "map_from_entries",
        ),
        "array": _require_direct_builtin_names(
            "array_new",
            "array_from_list",
            "array_get",
            "array_set",
            "array_length",
            "array_to_list",
            "array_fill",
            "array_copy",
        ),
        "mutable_collections": _require_direct_builtin_names(
            "mutable_map_new",
            "mutable_map_set",
            "mutable_map_get",
            "mutable_map_contains",
            "mutable_map_delete",
            "mutable_map_size",
            "mutable_map_keys",
            "vec_new",
            "vec_push",
            "vec_get",
            "vec_set",
            "vec_length",
            "vec_pop",
            "vec_to_list",
            "vec_from_list",
            "set_new",
            "set_from_list",
            "set_add",
            "set_remove",
            "set_contains",
            "set_size",
            "set_to_list",
            "set_union",
            "set_intersection",
        ),
        "io": _require_direct_builtin_names("print"),
        "graphics": _require_direct_builtin_names(
            "draw_rect",
            "draw_rect_outline",
            "draw_circle",
            "draw_line",
            "draw_text",
            "clear_screen",
            "screen_width",
            "screen_height",
        ),
        "input": _require_direct_builtin_names(
            "is_key_down",
            "is_key_pressed",
            "is_mouse_down",
            "is_mouse_clicked",
            "mouse_x",
            "mouse_y",
            "get_text_input",
            "clear_text_input",
        ),
        "regex": _require_direct_builtin_names(
            "regex_match",
            "regex_find_all",
            "regex_replace",
        ),
        "serialization": _require_direct_builtin_names(
            "json_parse",
            "json_stringify",
            "json_stringify_pretty",
            "csv_parse",
            "csv_parse_with_headers",
            "toml_parse",
        ),
        "datetime": _require_direct_builtin_names(
            "clock_now",
            "clock_format",
            "clock_elapsed",
            "datetime_now",
            "datetime_format",
            "datetime_parse",
            "datetime_elapsed",
            "clock_parse",
            "sleep_ms",
        ),
        "environment": _require_direct_builtin_names("env_get", "env_get_or"),
        "path": _require_direct_builtin_names(
            "path_join",
            "path_parent",
            "path_filename",
            "path_extension",
            "path_is_absolute",
        ),
    }


def get_expected_builtin_signatures() -> dict[str, dict[str, str]]:
    from geno.builtin_registry import (
        source_builtin_param_name_lists,
        source_builtin_specs,
    )

    def source_name(category: str, spec_name: str) -> str:
        return _SPEC_BUILTIN_SOURCE_OVERRIDES.get((category, spec_name), spec_name)

    def format_signature(name: str) -> str:
        override = _SOURCE_SIGNATURE_OVERRIDES.get(name)
        if override is not None:
            return override

        signature = specs[name].signature
        if signature is None:
            raise RuntimeError(
                f"validate_spec.py expected signature metadata for builtin {name!r}"
            )
        params = ", ".join(
            f"{param_name}: {param_type}"
            for param_name, param_type in zip(
                source_param_names[name],
                signature.param_types,
            )
        )
        return f"({params}) -> {signature.return_type}"

    specs = source_builtin_specs()
    source_param_names = source_builtin_param_name_lists()
    categories = get_source_builtin_categories()
    result: dict[str, dict[str, str]] = {}
    for category, names in categories.items():
        result[category] = {}
        for spec_name in names:
            expected_source_name = source_name(category, spec_name)
            if expected_source_name not in specs:
                raise RuntimeError(
                    "validate_spec.py expected source builtin missing from "
                    f"metadata for spec builtin {category}.{spec_name}: "
                    f"{expected_source_name}"
                )
            result[category][spec_name] = format_signature(expected_source_name)
    return result


def _record_set_mismatch(
    errors: list[str],
    label: str,
    expected: set[str],
    actual: set[str],
) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if not missing and not extra:
        return

    parts: list[str] = []
    if missing:
        parts.append(f"missing {missing}")
    if extra:
        parts.append(f"extra {extra}")
    errors.append(f"{label}: " + "; ".join(parts))


def _spec_capabilities(spec: dict[str, Any], errors: list[str]) -> set[str]:
    raw = spec.get("capabilities", {})
    if isinstance(raw, list):
        return {item for item in raw if isinstance(item, str)}

    if not isinstance(raw, dict):
        errors.append("'capabilities' must be a list or object in spec.json")
        return set()

    raw_map = raw.get("map", {})
    if not isinstance(raw_map, dict):
        errors.append("'capabilities.map' must be an object in spec.json")
        return set()

    return {name for name in raw_map if isinstance(name, str)}


def _validate_capability_map(spec: dict[str, Any], errors: list[str]) -> None:
    from geno.builtin_registry import CAPABILITY_MAP

    raw = spec.get("capabilities", {})
    if not isinstance(raw, dict):
        return
    raw_map = raw.get("map", {})
    if not isinstance(raw_map, dict):
        return

    for capability, expected_names in sorted(CAPABILITY_MAP.items()):
        raw_names = raw_map.get(capability, [])
        if not isinstance(raw_names, list):
            errors.append(
                f"Capability map entry '{capability}' must be a list in spec.json"
            )
            continue
        actual_names = {name for name in raw_names if isinstance(name, str)}
        _record_set_mismatch(
            errors,
            f"Capability map '{capability}' mismatch",
            set(expected_names),
            actual_names,
        )


def _validate_local_binding_syntax(spec: dict[str, Any], errors: list[str]) -> None:
    syntax = spec.get("syntax", {})
    if not isinstance(syntax, dict):
        errors.append("'syntax' must be an object in spec.json")
        return

    let_binding = syntax.get("let_binding", {})
    if not isinstance(let_binding, dict):
        errors.append("'syntax.let_binding' must be an object in spec.json")
        return

    if let_binding.get("immutable") != "let name[: Type] = expr":
        errors.append(
            "'syntax.let_binding.immutable' must show optional local type annotation"
        )
    if let_binding.get("mutable") != "var name[: Type] = expr":
        errors.append(
            "'syntax.let_binding.mutable' must show optional local type annotation"
        )

    notes = let_binding.get("notes", [])
    if not isinstance(notes, list):
        errors.append("'syntax.let_binding.notes' must be a list in spec.json")
        return

    note_text = " ".join(note for note in notes if isinstance(note, str)).lower()
    if "requires type annotation" in note_text:
        errors.append(
            "'syntax.let_binding.notes' must not claim local let requires a type annotation"
        )
    if "annotations are optional" not in note_text:
        errors.append(
            "'syntax.let_binding.notes' must mention optional local let/var annotations"
        )


def validate_spec(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    _record_set_mismatch(
        errors,
        "Keywords mismatch",
        get_source_keywords(),
        set(spec.get("keywords", [])),
    )
    _record_set_mismatch(
        errors,
        "Compilation targets mismatch",
        get_source_compilation_targets(),
        set(spec.get("compilation_targets", [])),
    )
    _record_set_mismatch(
        errors,
        "Execution targets mismatch",
        get_source_execution_targets(),
        set(spec.get("execution_targets", [])),
    )
    _record_set_mismatch(
        errors,
        "Capabilities mismatch",
        get_source_capabilities(),
        _spec_capabilities(spec, errors),
    )
    _validate_capability_map(spec, errors)
    _record_set_mismatch(
        errors,
        "Browser lifecycle mismatch",
        get_source_lifecycle(),
        set(
            spec.get("app_lifecycle", {})
            .get("browser", {})
            .get("required_functions", [])
        ),
    )
    _validate_local_binding_syntax(spec, errors)

    spec_builtins = spec.get("builtins", {})
    if not isinstance(spec_builtins, dict):
        errors.append("'builtins' must be an object in spec.json")
        return errors

    expected_categories = get_source_builtin_categories()
    expected_signatures_by_category = get_expected_builtin_signatures()
    _record_set_mismatch(
        errors,
        "Builtin categories mismatch",
        set(expected_categories.keys()),
        set(spec_builtins.keys()),
    )

    for category, expected in expected_categories.items():
        raw_funcs = spec_builtins.get(category, [])
        actual: set[str] = set()
        actual_signatures: dict[str, str | None] = {}
        if isinstance(raw_funcs, list):
            for func in raw_funcs:
                if isinstance(func, dict) and isinstance(func.get("name"), str):
                    actual.add(func["name"])
                    actual_signatures[func["name"]] = cast(
                        str | None, func.get("signature")
                    )
        else:
            errors.append(f"Builtin category '{category}' must be a list in spec.json")
            continue

        _record_set_mismatch(
            errors,
            f"Builtin category '{category}' mismatch",
            expected,
            actual,
        )

        for name, expected_signature in expected_signatures_by_category.get(
            category, {}
        ).items():
            actual_signature = actual_signatures.get(name)
            if actual_signature != expected_signature:
                errors.append(
                    f"Builtin signature '{category}.{name}' mismatch: "
                    f"expected {expected_signature!r}, got {actual_signature!r}"
                )

    return errors


def main() -> int:
    errors = validate_spec(load_spec())
    if errors:
        print("spec.json validation errors:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("spec.json validation OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
