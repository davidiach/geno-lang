"""Regression tests for scripts/validate_spec.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from scripts.validate_spec import validate_spec

ROOT = Path(__file__).resolve().parents[2]


def _load_spec() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((ROOT / "spec.json").read_text()))


def test_current_spec_passes_validation():
    assert validate_spec(_load_spec()) == []


def test_missing_builtin_is_reported():
    spec = _load_spec()
    spec["builtins"]["string"] = [
        func for func in spec["builtins"]["string"] if func["name"] != "split"
    ]

    errors = validate_spec(spec)

    assert any(
        "Builtin category 'string' mismatch" in error and "split" in error
        for error in errors
    )


def test_missing_capability_is_reported():
    spec = _load_spec()
    spec["capabilities"]["map"].pop("regex")

    errors = validate_spec(spec)

    assert any(
        "Capabilities mismatch" in error and "regex" in error for error in errors
    )


def test_missing_execution_target_is_reported():
    spec = _load_spec()
    spec["execution_targets"] = [
        target for target in spec["execution_targets"] if target != "browser"
    ]

    errors = validate_spec(spec)

    assert any(
        "Execution targets mismatch" in error and "browser" in error for error in errors
    )


def test_missing_compilation_target_is_reported():
    spec = _load_spec()
    spec["compilation_targets"] = ["python"]

    errors = validate_spec(spec)

    assert any(
        "Compilation targets mismatch" in error and "javascript" in error
        for error in errors
    )


def test_stale_local_binding_annotation_note_is_reported():
    spec = _load_spec()
    spec["syntax"]["let_binding"]["immutable"] = "let name: Type = expr"
    spec["syntax"]["let_binding"]["mutable"] = "var name: Type = expr"
    spec["syntax"]["let_binding"]["notes"] = [
        "let requires type annotation",
        "var for mutable bindings (NOT let mut)",
    ]

    errors = validate_spec(spec)

    assert any(
        "let_binding.immutable" in error and "optional local type annotation" in error
        for error in errors
    )
    assert any(
        "let_binding.mutable" in error and "optional local type annotation" in error
        for error in errors
    )
    assert any("must not claim local let requires" in error for error in errors)


def test_stale_group_by_signature_is_reported():
    spec = _load_spec()
    for func in spec["builtins"]["list"]:
        if func["name"] == "group_by":
            func["signature"] = "(lst: List[T], key: (T) -> K) -> Map[K, List[T]]"
            break
    else:
        raise AssertionError("group_by missing from spec builtins")

    errors = validate_spec(spec)

    assert any(
        "Builtin signature 'list.group_by' mismatch" in error
        and "List[(K, List[T])]" in error
        for error in errors
    )


def test_stale_regex_replace_argument_order_is_reported():
    spec = _load_spec()
    for func in spec["builtins"]["regex"]:
        if func["name"] == "regex_replace":
            func["signature"] = (
                "(pattern: String, text: String, replacement: String) -> String"
            )
            break
    else:
        raise AssertionError("regex_replace missing from spec builtins")

    errors = validate_spec(spec)

    assert any(
        "Builtin signature 'regex.regex_replace' mismatch" in error
        and "replacement: String, text: String" in error
        for error in errors
    )


def test_stale_vec_pop_signature_is_reported():
    spec = _load_spec()
    for func in spec["builtins"]["mutable_collections"]:
        if func["name"] == "vec_pop":
            func["signature"] = "(vec: Vec[T]) -> T"
            break
    else:
        raise AssertionError("vec_pop missing from spec builtins")

    errors = validate_spec(spec)

    assert any(
        "Builtin signature 'mutable_collections.vec_pop' mismatch" in error
        and "Option[T]" in error
        for error in errors
    )


def test_stale_serialization_signatures_are_reported():
    spec = _load_spec()
    stale_signatures = {
        "json_parse": "(text: String) -> T",
        "toml_parse": "(text: String) -> Map[String, T]",
    }
    for func in spec["builtins"]["serialization"]:
        if func["name"] in stale_signatures:
            func["signature"] = stale_signatures[func["name"]]

    errors = validate_spec(spec)

    assert any(
        "Builtin signature 'serialization.json_parse' mismatch" in error
        and "Result[JsonValue, String]" in error
        for error in errors
    )
    assert any(
        "Builtin signature 'serialization.toml_parse' mismatch" in error
        and "Result[JsonValue, String]" in error
        for error in errors
    )


def test_stale_named_argument_signature_is_reported():
    spec = _load_spec()
    for func in spec["builtins"]["string"]:
        if func["name"] == "split":
            func["signature"] = "(s: String, sep: String) -> List[String]"
            break
    else:
        raise AssertionError("split missing from spec builtins")

    errors = validate_spec(spec)

    assert any(
        "Builtin signature 'string.split' mismatch" in error
        and "text: String, delimiter: String" in error
        for error in errors
    )


def test_missing_signature_is_reported():
    spec = _load_spec()
    for func in spec["builtins"]["array"]:
        if func["name"] == "array_new":
            func.pop("signature")
            break
    else:
        raise AssertionError("array_new missing from spec builtins")

    errors = validate_spec(spec)

    assert any(
        "Builtin signature 'array.array_new' mismatch" in error and "got None" in error
        for error in errors
    )


def test_capability_map_value_drift_is_reported():
    spec = _load_spec()
    spec["capabilities"]["map"]["clock"] = ["clock_now"]

    errors = validate_spec(spec)

    assert any(
        "Capability map 'clock' mismatch" in error
        and "datetime_now" in error
        and "sleep_ms" in error
        for error in errors
    )
