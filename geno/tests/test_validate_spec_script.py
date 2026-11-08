"""Regression tests for scripts/validate_spec.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.validate_spec import validate_spec

ROOT = Path(__file__).resolve().parents[2]


def _load_spec() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((ROOT / "spec.json").read_text()))


def test_current_spec_passes_validation():
    assert validate_spec(_load_spec()) == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("series", "0.6", "must be newer"),
        ("series", "0.7.0", "major.minor"),
        ("status", "accepted", "must be 'draft'"),
        ("human_spec", "docs/spec/v0.6.md", "human specification mismatch"),
        ("conformance", "conformance/v0.6/manifest.toml", "corpus mismatch"),
        ("additions", [], "non-empty list"),
        ("additions", ["a", "a"], "duplicate entries"),
    ],
)
def test_invalid_development_contract_is_reported(
    field: str, value: object, message: str
) -> None:
    spec = _load_spec()
    spec["development_line"][field] = value

    assert any(message in error for error in validate_spec(spec))


def test_development_line_is_optional_for_released_contracts() -> None:
    spec = _load_spec()
    del spec["development_line"]

    assert validate_spec(spec) == []


def test_development_corpus_cannot_claim_to_be_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import validate_spec as validator

    spec = _load_spec()
    line = spec["development_line"]
    for path in (line["human_spec"], line["conformance"]):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((ROOT / path).read_text(encoding="utf-8"), encoding="utf-8")
    corpus = tmp_path / line["conformance"]
    corpus.write_text(
        corpus.read_text(encoding="utf-8").replace(
            'status = "development"', 'status = "frozen"'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    errors: list[str] = []

    validator._validate_development_line(spec, errors)

    assert any("must be marked status = 'development'" in error for error in errors)


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


STALE_UNGATED_CLAIM = "Omitting --cap leaves capability gating disabled"


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        (
            "cli",
            [
                "'capabilities.surfaces.cli' must name `clock` from "
                "'capabilities.default_grants.geno_run'",
                "'capabilities.surfaces.cli' must say 'no gated builtins' for the "
                "empty 'capabilities.default_grants.geno_run_json'",
            ],
        ),
        (
            "embedding_api",
            [
                "'capabilities.surfaces.embedding_api' must say 'no gated builtins' "
                "for the empty 'capabilities.default_grants.embedding_api'"
            ],
        ),
        (
            "browser_builds",
            [
                "'capabilities.surfaces.browser_builds' must name `http` from "
                "'capabilities.default_grants.browser_builds'"
            ],
        ),
    ],
)
def test_stale_ungated_surface_prose_is_reported(surface, expected):
    """#248: prose that claims gating is disabled names no real grant."""
    spec = _load_spec()
    spec["capabilities"]["surfaces"][surface] = STALE_UNGATED_CLAIM

    errors = validate_spec(spec)

    for message in expected:
        assert message in errors


def test_main_hosted_server_claim_is_reported():
    """The 0.6.0 hosted text said omitted capabilities get the allowed set."""
    spec = _load_spec()
    spec["capabilities"]["surfaces"]["hosted_server"] = (
        "Omitting capabilities in /run requests defaults to the server's allowed "
        "capability set"
    )

    errors = validate_spec(spec)

    assert (
        "'capabilities.surfaces.hosted_server' must say 'no gated builtins' for the "
        "empty 'capabilities.default_grants.hosted_server'"
    ) in errors


def test_true_sentence_mentioning_gating_disabled_is_accepted():
    """No keyword ban: a correct sentence may say no lane disables gating."""
    spec = _load_spec()
    spec["capabilities"]["description"] += (
        " No surface leaves capability gating disabled."
    )

    assert validate_spec(spec) == []


def test_default_grant_drift_is_reported():
    spec = _load_spec()
    grants = spec["capabilities"]["default_grants"]
    grants["geno_run_unsafe"] = ["clock", "fs", "print", "random"]
    grants["browser_builds"].remove("regex")

    errors = validate_spec(spec)

    assert (
        "'capabilities.default_grants.geno_run_unsafe' mismatch: extra ['fs']" in errors
    )
    assert (
        "'capabilities.default_grants.browser_builds' mismatch: missing ['regex']"
        in errors
    )


def test_default_grants_must_cover_each_lane():
    spec = _load_spec()
    del spec["capabilities"]["default_grants"]["repl"]

    errors = validate_spec(spec)

    assert "'capabilities.default_grants' mismatch: missing ['repl']" in errors


def test_stale_capability_description_is_reported():
    spec = _load_spec()
    spec["capabilities"]["description"] = (
        "Direct embedding and CLI execution leave gating disabled when "
        "capabilities are omitted."
    )

    errors = validate_spec(spec)

    assert (
        "'capabilities.description' must be a string that points to `default_grants`"
    ) in errors


def test_capability_surfaces_must_cover_each_surface():
    spec = _load_spec()
    del spec["capabilities"]["surfaces"]["compiled_programs"]

    errors = validate_spec(spec)

    assert "'capabilities.surfaces' mismatch: missing ['compiled_programs']" in errors


def test_source_default_grants_follow_the_shared_defaults():
    """The run, unsafe, test and REPL lanes share DEFAULT_ALLOWED_CAPABILITIES."""
    from geno.capabilities import DEFAULT_ALLOWED_CAPABILITIES
    from scripts.validate_spec import get_source_default_grants

    grants = get_source_default_grants()
    for lane in ("geno_run", "geno_run_unsafe", "geno_test", "repl"):
        assert grants[lane] == set(DEFAULT_ALLOWED_CAPABILITIES)
    for lane in (
        "embedding_api",
        "geno_run_json",
        "compiled_programs",
        "hosted_server",
    ):
        assert grants[lane] == set()
