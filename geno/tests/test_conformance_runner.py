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
    main,
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


def _only_in_one_corpus() -> str:
    """Return a case id that exactly one retained corpus defines."""
    id_sets = [
        {case.id for case in load_manifest(path).cases}
        for path in retained_manifest_paths()
    ]
    exclusive = set().union(*id_sets) - set.intersection(*id_sets)
    assert exclusive, "expected at least one corpus-specific case"
    return sorted(exclusive)[0]


def test_case_filter_accepts_an_id_only_one_retained_corpus_defines() -> None:
    """`--case` is validated against the union, not each corpus in turn.

    A form introduced in the current series has no case in the retained
    previous one, so validating per corpus would reject a legitimate id.
    """
    case_id = _only_in_one_corpus()

    assert main(["--all-retained", "--case", case_id, "--target", "checker"]) == 0


def test_case_filter_still_rejects_an_id_no_corpus_defines() -> None:
    assert main(["--all-retained", "--case", "no-such-case"]) == 2


def test_run_suite_rejects_an_unknown_id_for_a_single_manifest() -> None:
    """The single-corpus contract is unchanged: strict by default."""
    with pytest.raises(ValueError, match="unknown case ids: no-such-case"):
        run_suite(load_manifest(), case_ids=frozenset({"no-such-case"}))


_PRINTING_PROGRAM = 'func main() -> Unit\n    print("ok")\n    return ()\nend func\n'
_INT_PROGRAM = "func main() -> Int\n    return 3\nend func\n"


def _corpus(
    tmp_path: Path, *, program: str, schema_version: int = 2, **case_fields: object
) -> Path:
    """Write a one-case run corpus and return its manifest path.

    The loader requires a manifest to live in a directory named for its
    ``language_version``, so this builds ``v0.5/`` rather than using tmp_path
    directly.
    """
    tmp_path = tmp_path / "v0.5"
    (tmp_path / "programs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "programs" / "case.geno").write_text(program, encoding="utf-8")
    fields = "\n".join(
        f"{key} = {json.dumps(value)}" for key, value in case_fields.items()
    )
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        f"schema_version = {schema_version}\n"
        'language_version = "0.5"\ndescription = "t"\n\n'
        "[[cases]]\n"
        'id = "case"\n'
        'path = "programs/case.geno"\n'
        'kind = "run"\n'
        f"{fields}\n",
        encoding="utf-8",
    )
    return manifest


def test_expected_exit_status_defaults_to_zero(tmp_path: Path) -> None:
    """Omitting it means 0, which every displayed result kind exits with."""
    manifest = _corpus(
        tmp_path,
        program=_PRINTING_PROGRAM,
        targets=["interpreter"],
        capabilities=["print"],
        expected_stdout="ok\n",
    )

    case = load_manifest(manifest).cases[0]

    assert case.expected_exit_status == 0


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("3", "must be an integer"),
        (True, "must be an integer"),
        (3.5, "must be an integer"),
        (256, "between 0 and 255"),
        (-1, "between 0 and 255"),
    ],
    ids=["string", "bool", "float", "above-255", "negative"],
)
def test_expected_exit_status_is_validated(
    tmp_path: Path, value: object, match: str
) -> None:
    """The manifest states a status a host can report, so 258 is a mistake.

    Normalizing modulo 256 is the implementation's job (`geno/exit_status.py`);
    a case says which status an executable is required to return. `bool` is
    rejected separately because it is an `int` subclass.
    """
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["interpreter"],
        expected_stdout="",
        expected_exit_status=value,
    )

    with pytest.raises(ManifestError, match=match):
        load_manifest(manifest)


def test_diagnostic_case_cannot_expect_an_exit_status(tmp_path: Path) -> None:
    tmp_path = tmp_path / "v0.5"
    (tmp_path / "programs").mkdir(parents=True)
    (tmp_path / "programs" / "case.geno").write_text(_INT_PROGRAM, encoding="utf-8")
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        'schema_version = 2\nlanguage_version = "0.5"\ndescription = "t"\n\n'
        "[[cases]]\n"
        'id = "case"\n'
        'path = "programs/case.geno"\n'
        'kind = "diagnostic"\n'
        'targets = ["checker"]\n'
        'expected_diagnostics = ["E300"]\n'
        "expected_exit_status = 3\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="cannot expect process channels"):
        load_manifest(manifest)


@pytest.mark.parametrize("target", ["interpreter", "python"])
def test_an_int_result_is_reported_as_the_expected_status(
    tmp_path: Path, target: str
) -> None:
    """The contract this corpus field exists for (spec 4.1.1)."""
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=[target],
        expected_stdout="",
        expected_exit_status=3,
    )

    results = run_suite(load_manifest(manifest), target=target)

    assert results
    assert all(result.status == "passed" for result in results), results


@pytest.mark.parametrize("target", ["interpreter", "python"])
def test_a_wrong_expected_status_fails(tmp_path: Path, target: str) -> None:
    """Guards the comparison itself: without it the field would be decorative."""
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=[target],
        expected_stdout="",
        expected_exit_status=4,
    )

    results = run_suite(load_manifest(manifest), target=target)

    assert [result.status for result in results] == ["failed"]
    assert "expected exit status 4, got 3" in results[0].detail


def test_v05_exit_status_contracts_pass() -> None:
    """Every executable target agrees on the whole 4.1.1 table."""
    current = retained_manifest_paths()[-1]
    manifest = load_manifest(current)
    exit_cases = frozenset(
        case.id for case in manifest.cases if case.id.startswith("exit-status-")
    )

    assert len(exit_cases) >= 5, sorted(exit_cases)

    results = run_suite(
        manifest,
        case_ids=exit_cases,
        require_node=shutil.which("node") is not None,
    )

    assert all(result.status == "passed" for result in results), results
    assert {result.target for result in results} >= {
        "checker",
        "interpreter",
        "python",
    }


def test_v04_corpus_stays_on_schema_1() -> None:
    """Relaxing the pin must not quietly migrate the frozen corpus."""
    assert load_manifest().schema_version == 1


def test_schema_1_rejects_the_executable_boundary_fields(tmp_path: Path) -> None:
    """The new fields are schema 2, so an older corpus cannot use them silently."""
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        schema_version=1,
        targets=["interpreter"],
        expected_stdout="",
        expected_exit_status=3,
    )

    with pytest.raises(ManifestError, match="requires schema_version 2"):
        load_manifest(manifest)


def test_an_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    manifest = _corpus(
        tmp_path,
        program=_PRINTING_PROGRAM,
        schema_version=3,
        targets=["interpreter"],
        capabilities=["print"],
        expected_stdout="ok\n",
    )

    with pytest.raises(ManifestError, match="schema_version must be one of: 1, 2"):
        load_manifest(manifest)


def test_exit_status_and_exit_class_are_mutually_exclusive(tmp_path: Path) -> None:
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["interpreter"],
        expected_stdout="",
        expected_exit_status=3,
        expected_exit_class="nonzero",
    )

    with pytest.raises(ManifestError, match="mutually exclusive"):
        load_manifest(manifest)


def test_exit_class_nonzero_requires_expected_stderr_contains(tmp_path: Path) -> None:
    """Otherwise any crash satisfies it, which is not a contract."""
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["interpreter"],
        expected_stdout="",
        expected_exit_class="nonzero",
    )

    with pytest.raises(ManifestError, match="requires expected_stderr_contains"):
        load_manifest(manifest)


def test_an_unknown_exit_class_is_rejected(tmp_path: Path) -> None:
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["interpreter"],
        expected_stdout="",
        expected_exit_class="zero",
        expected_stderr_contains=["x"],
    )

    with pytest.raises(ManifestError, match="must be 'nonzero' when present"):
        load_manifest(manifest)


def test_the_two_stderr_fields_are_mutually_exclusive(tmp_path: Path) -> None:
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["interpreter"],
        expected_stdout="",
        expected_exit_status=3,
        expected_stderr="",
        expected_stderr_contains=["x"],
    )

    with pytest.raises(ManifestError, match="mutually exclusive"):
        load_manifest(manifest)


def test_a_compiled_runtime_error_is_matched_by_class_and_stderr(
    tmp_path: Path,
) -> None:
    """The `expected_exit_class` lane, on the target that really has a stderr."""
    manifest = _corpus(
        tmp_path,
        program=(
            "func boom(xs: List[Int]) -> Int\n"
            "    example [1] -> 1\n"
            "    return xs[7]\n"
            "end func\n"
            "func main() -> Int\n"
            "    return boom([1])\n"
            "end func\n"
        ),
        targets=["python"],
        expected_stdout="",
        expected_exit_class="nonzero",
        expected_stderr_contains=["Error"],
    )

    results = run_suite(load_manifest(manifest), target="python")

    assert [result.status for result in results] == ["passed"], results
