"""Contract tests for the frozen, externally runnable conformance corpus."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.run_conformance import (
    DEFAULT_MANIFEST,
    ManifestError,
    load_manifest,
    retained_manifest_paths,
    run_suite,
)

ROOT = Path(__file__).resolve().parents[2]


def test_v04_manifest_is_frozen_and_complete() -> None:
    manifest = load_manifest()

    assert manifest.path == DEFAULT_MANIFEST.resolve()
    assert manifest.schema_version == 1
    assert manifest.language_version == "0.4"
    assert len(manifest.cases) >= 10
    assert len({case.id for case in manifest.cases}) == len(manifest.cases)
    assert {case.kind for case in manifest.cases} == {"run", "diagnostic"}
    assert all(
        case.path.is_relative_to(DEFAULT_MANIFEST.parent.resolve())
        for case in manifest.cases
    )


def _write_contract(path: Path, series: str) -> None:
    path.write_text(json.dumps({"language_series": series}), encoding="utf-8")


def _touch_manifest(root: Path, series: str) -> Path:
    path = root / f"v{series}" / "manifest.toml"
    path.parent.mkdir(parents=True)
    path.touch()
    return path.resolve()


def test_retained_corpora_follow_declared_language_series(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    corpus_root = tmp_path / "conformance"
    previous = _touch_manifest(corpus_root, "0.4")
    current = _touch_manifest(corpus_root, "0.5")
    _write_contract(spec_path, "0.5")

    assert retained_manifest_paths(
        spec_path=spec_path,
        conformance_root=corpus_root,
    ) == (previous, current)


def test_retained_corpora_reject_a_missing_immediate_minor(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    corpus_root = tmp_path / "conformance"
    _touch_manifest(corpus_root, "0.4")
    _touch_manifest(corpus_root, "0.6")
    _write_contract(spec_path, "0.6")

    with pytest.raises(ManifestError, match="immediately preceding"):
        retained_manifest_paths(
            spec_path=spec_path,
            conformance_root=corpus_root,
        )


def test_retained_corpora_reject_a_cross_major_gap(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    corpus_root = tmp_path / "conformance"
    _touch_manifest(corpus_root, "0.4")
    _touch_manifest(corpus_root, "1.1")
    _write_contract(spec_path, "1.1")

    with pytest.raises(ManifestError, match=r"v1\.0"):
        retained_manifest_paths(
            spec_path=spec_path,
            conformance_root=corpus_root,
        )


def test_v04_checker_and_diagnostic_contracts_pass() -> None:
    results = run_suite(load_manifest(), target="checker")

    assert results
    assert all(result.status == "passed" for result in results), results
    assert {result.target for result in results} == {"checker"}


@pytest.mark.parametrize("target", ["interpreter", "python"])
def test_v04_runtime_contracts_pass(target: str) -> None:
    results = run_suite(load_manifest(), target=target)

    assert results
    assert all(result.status == "passed" for result in results), results
    assert {result.target for result in results} == {target}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_v04_javascript_contracts_pass() -> None:
    results = run_suite(load_manifest(), target="js", require_node=True)

    assert results
    assert all(result.status == "passed" for result in results), results
    assert {result.target for result in results} == {"js"}
