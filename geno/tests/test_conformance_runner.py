"""Contract tests for the frozen, externally runnable conformance corpus."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from scripts.run_conformance import (
    DEFAULT_MANIFEST,
    RUNTIME_TARGETS,
    SCHEMA_2_ONLY_FIELDS,
    ConformanceCase,
    ManifestError,
    _envelope_mismatches,
    development_manifest_paths,
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


def _write_manifest(root: Path, series: str, status: str | None = None) -> Path:
    path = root / f"v{series}" / "manifest.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        f'language_version = "{series}"\n'
        + (f'status = "{status}"\n' if status is not None else ""),
        encoding="utf-8",
    )
    return path.resolve()


def test_no_development_corpus_is_the_default(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    corpus_root = tmp_path / "conformance"
    _write_manifest(corpus_root, "0.5")
    _write_manifest(corpus_root, "0.6")
    _write_contract(spec_path, "0.6")

    assert (
        development_manifest_paths(spec_path=spec_path, conformance_root=corpus_root)
        == ()
    )


def test_a_marked_newer_corpus_is_the_development_corpus(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    corpus_root = tmp_path / "conformance"
    _write_manifest(corpus_root, "0.5")
    _write_manifest(corpus_root, "0.6")
    development = _write_manifest(corpus_root, "0.7", "development")
    _write_contract(spec_path, "0.6")

    assert development_manifest_paths(
        spec_path=spec_path, conformance_root=corpus_root
    ) == (development,)
    # The retained corpora are what they were.
    assert retained_manifest_paths(
        spec_path=spec_path, conformance_root=corpus_root
    ) == (
        (corpus_root / "v0.5" / "manifest.toml").resolve(),
        (corpus_root / "v0.6" / "manifest.toml").resolve(),
    )


def test_an_unmarked_newer_corpus_is_refused(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    corpus_root = tmp_path / "conformance"
    _write_manifest(corpus_root, "0.6")
    _write_manifest(corpus_root, "0.7")
    _write_contract(spec_path, "0.6")

    with pytest.raises(ManifestError, match="must be marked"):
        development_manifest_paths(spec_path=spec_path, conformance_root=corpus_root)


def test_a_marked_corpus_that_is_not_newer_is_refused(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    corpus_root = tmp_path / "conformance"
    _write_manifest(corpus_root, "0.5")
    _write_manifest(corpus_root, "0.6", "development")
    _write_contract(spec_path, "0.6")

    with pytest.raises(ManifestError, match="only a corpus newer"):
        development_manifest_paths(spec_path=spec_path, conformance_root=corpus_root)


def test_two_development_corpora_are_refused(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    corpus_root = tmp_path / "conformance"
    _write_manifest(corpus_root, "0.6")
    _write_manifest(corpus_root, "0.7", "development")
    _write_manifest(corpus_root, "0.8", "development")
    _write_contract(spec_path, "0.6")

    with pytest.raises(ManifestError, match="at most one development corpus"):
        development_manifest_paths(spec_path=spec_path, conformance_root=corpus_root)


def test_the_repository_development_corpus_is_v07() -> None:
    (path,) = development_manifest_paths()
    manifest = load_manifest(path)

    assert manifest.language_version == "0.7"
    assert manifest.status == "development"
    assert {case.id for case in manifest.cases} >= {
        "panic-example-passes",
        "panic-example-zero-arg",
        "panic-example-no-panic-fails",
        "isolated-grant-regex",
        "isolated-grant-env-cli-args",
        "isolated-grant-is-exact",
        *_RUNTIME_REPORT_CASES,
    }
    assert all(load_manifest(p).status is None for p in retained_manifest_paths())


# Proposal 0018's v0.7 cases: a runtime failure's location and Geno stack on
# every lane that reports one (the `interpreter` lane is an embedding boundary
# and returns diagnostics as data).
_RUNTIME_REPORT_CASES = (
    "runtime-panic-stack",
    "runtime-index-stack",
    "runtime-requires-arguments",
    "runtime-ensures-result",
    "runtime-recursion-elision",
    "runtime-capability-denial-stack",
)
_RUNTIME_REPORT_LANES = {
    "cli-process",
    "cli-direct",
    "cli-json",
    "python",
    "js",
    "js-esm",
}


@pytest.mark.parametrize("case_id", _RUNTIME_REPORT_CASES)
def test_the_runtime_report_cases_run_on_every_reporting_lane(case_id: str) -> None:
    """Every lane reports the same frames: the text lanes on stderr, `--json`
    in its envelope's `expected_diagnostic` (backend parity, proposal 0018)."""
    (path,) = development_manifest_paths()
    manifest = load_manifest(path)
    (case,) = [case for case in manifest.cases if case.id == case_id]

    assert set(case.targets) == _RUNTIME_REPORT_LANES
    assert case.expected_exit_class == "nonzero"
    assert case.expected_diagnostic is not None
    assert case.expected_diagnostic.stack
    # Each frame fragment names the program by its file name only, so it holds
    # for the CLI lanes' absolute paths and the compiled lanes' relative ones.
    frames = [
        fragment for fragment in case.expected_stderr_contains if " in " in fragment
    ]
    assert frames
    assert all(fragment.startswith(case.path.name) for fragment in frames)
    results = run_suite(manifest, case_ids=frozenset({case_id}), timeout=60.0)

    assert {result.target for result in results} == {"checker", *case.targets}
    _assert_lanes_passed(results)


_ISOLATED_GRANT_CASES = {
    "isolated-grant-regex": {"print", "regex"},
    "isolated-grant-env-cli-args": {"env", "print"},
}


_COMPILED_LANES = {"python", "js", "js-esm"}


def _assert_lanes_passed(results) -> None:
    """Every lane passed; only the Node lanes may skip, and only without Node."""
    for result in results:
        if result.target in {"js", "js-esm"} and shutil.which("node") is None:
            assert result.status in {"passed", "skipped"}, result
        else:
            assert result.status == "passed", result


@pytest.mark.parametrize("case_id", sorted(_ISOLATED_GRANT_CASES))
def test_the_isolated_grant_cases_run_on_every_lane(case_id: str) -> None:
    """Proposal 0013's v0.7 cases agree across the four lanes that run examples
    and the compiled Python and JavaScript backends (backend parity)."""
    (path,) = development_manifest_paths()
    manifest = load_manifest(path)
    (case,) = [case for case in manifest.cases if case.id == case_id]

    assert set(case.targets) == {
        "interpreter",
        "cli-process",
        "cli-direct",
        "cli-json",
        *_COMPILED_LANES,
    }
    assert set(case.capabilities) == _ISOLATED_GRANT_CASES[case_id]
    results = run_suite(manifest, case_ids=frozenset({case_id}), timeout=60.0)

    assert {result.target for result in results} == {"checker", *case.targets}
    _assert_lanes_passed(results)


def test_the_isolated_grant_is_exact_in_every_lane_with_a_stderr() -> None:
    """`--cap regex` denies `print`: no lane restores the default grant."""
    (path,) = development_manifest_paths()
    manifest = load_manifest(path)

    results = run_suite(
        manifest, case_ids=frozenset({"isolated-grant-is-exact"}), timeout=60.0
    )

    assert {result.target for result in results} == {
        "checker",
        "cli-process",
        "cli-direct",
        *_COMPILED_LANES,
    }
    _assert_lanes_passed(results)


def test_all_retained_runs_the_development_corpus_last(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--all-retained", "--target", "checker"]) == 0
    headings = [
        line for line in capsys.readouterr().out.splitlines() if "conformance:" in line
    ]
    assert headings[-1].startswith("Geno 0.7 conformance:")
    assert headings[-1].endswith("development cases")
    assert all(heading.endswith("frozen cases") for heading in headings[:-1])


def test_a_manifest_status_other_than_development_is_refused(tmp_path: Path) -> None:
    manifest = _corpus(
        tmp_path,
        program=_PRINTING_PROGRAM,
        targets=["interpreter"],
        capabilities=["print"],
        expected_stdout="ok\n",
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'description = "t"', 'description = "t"\nstatus = "frozen"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="status must be"):
        load_manifest(manifest)


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
    exclusive = set().union(*id_sets) - id_sets[0].intersection(*id_sets[1:])
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
_PANIC_PROGRAM = (
    "func check(n: Int) -> Int\n"
    "    example 1 -> 1\n"
    "    if n > 5 then\n"
    '        panic("too large")\n'
    "    end if\n"
    "    return n\n"
    "end func\n"
    "\n"
    "func main() -> Int\n"
    "    return check(9)\n"
    "end func\n"
)
_WRAPPING_PROGRAM = "func main() -> Int\n    return 258\nend func\n"
# One valid value per schema 2 field, so the version gate below covers all of
# them rather than only the one it was written for.
_SCHEMA_2_SAMPLES: dict[str, object] = {
    "expected_exit_status": 3,
    "expected_exit_class": "nonzero",
    "expected_stderr": "",
    "expected_stderr_contains": ["boom"],
    "expected_json_value": "3",
    "expected_diagnostic": {"code": "E414"},
}


def _toml_value(value: object) -> str:
    """Render a manifest field: JSON spelling, except tables (inline TOML)."""
    if isinstance(value, dict):
        fields = ", ".join(
            f"{key} = {_toml_value(item)}" for key, item in value.items()
        )
        return f"{{ {fields} }}"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(value)


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
        f"{key} = {_toml_value(value)}" for key, value in case_fields.items()
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


def test_the_schema_2_field_samples_stay_complete() -> None:
    """Keeps the version gate below honest when a field is added."""
    assert set(_SCHEMA_2_SAMPLES) == set(SCHEMA_2_ONLY_FIELDS)


@pytest.mark.parametrize("field", SCHEMA_2_ONLY_FIELDS)
def test_schema_1_rejects_the_executable_boundary_fields(
    tmp_path: Path, field: str
) -> None:
    """The new fields are schema 2, so an older corpus cannot use them silently.

    The version gate runs before every field's own rule, so a value that would
    also fail those rules still reports the schema first, which is the message a
    corpus author needs.
    """
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        schema_version=1,
        targets=["interpreter"],
        expected_stdout="",
        **{field: _SCHEMA_2_SAMPLES[field]},
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


@pytest.mark.parametrize("target", ["cli-process", "cli-direct"])
def test_the_cli_lanes_report_an_int_result_as_the_status(
    tmp_path: Path, target: str
) -> None:
    """Both `geno run` lanes are child processes, so the status is observable."""
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=[target],
        expected_stdout="",
        expected_exit_status=3,
    )

    results = run_suite(load_manifest(manifest), target=target)

    assert [result.status for result in results] == ["passed"], results


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_node_esm_lane_reports_an_int_result_as_the_status(
    tmp_path: Path,
) -> None:
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["js-esm"],
        expected_stdout="",
        expected_exit_status=3,
    )

    results = run_suite(load_manifest(manifest), target="js-esm", require_node=True)

    assert [result.status for result in results] == ["passed"], results


def test_the_json_lane_keeps_the_value_raw_while_the_process_normalizes(
    tmp_path: Path,
) -> None:
    """The split proposal 0001 asks for: 258 in the envelope, 2 from the process."""
    manifest = _corpus(
        tmp_path,
        program=_WRAPPING_PROGRAM,
        targets=["cli-json"],
        expected_stdout="",
        expected_exit_status=2,
        expected_stderr="",
        expected_json_value="258",
    )

    results = run_suite(load_manifest(manifest), target="cli-json")

    assert [result.status for result in results] == ["passed"], results


def test_a_normalized_envelope_value_fails(tmp_path: Path) -> None:
    """The single easiest mistake here is normalizing the envelope too."""
    manifest = _corpus(
        tmp_path,
        program=_WRAPPING_PROGRAM,
        targets=["cli-json"],
        expected_stdout="",
        expected_exit_status=2,
        expected_json_value="2",
    )

    results = run_suite(load_manifest(manifest), target="cli-json")

    assert [result.status for result in results] == ["failed"]
    assert "expected envelope value 2, got 258" in results[0].detail


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [([], "failed"), (["print"], "passed")],
    ids=["ungranted", "granted"],
)
def test_the_json_lane_is_fail_closed_on_capabilities(
    tmp_path: Path, capabilities: list[str], expected: str
) -> None:
    """`geno run --json` grants nothing by default, so a case must name `print`.

    Without the grant the envelope reports E412 instead of running, which is the
    behavior #113 settled on and the reason this lane passes `--cap` at all. The
    ungranted half matters because a lane that silently granted `print` would
    pass every case here for the wrong reason.
    """
    manifest = _corpus(
        tmp_path / "-".join(capabilities or ["none"]),
        program=_PRINTING_PROGRAM,
        targets=["cli-json"],
        capabilities=capabilities,
        expected_stdout="ok\n",
        expected_json_value='{"_tuple": []}',
    )

    results = run_suite(load_manifest(manifest), target="cli-json")

    assert [result.status for result in results] == [expected], results


_REGEX_PROGRAM = (
    "func main() -> Unit\n"
    '    print(regex_replace(pattern: "o", replacement: "0", text: "geno"))\n'
    "    return ()\n"
    "end func\n"
)


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [(["print"], "failed"), (["print", "regex"], "passed")],
    ids=["ungranted", "granted"],
)
def test_the_process_lane_grants_the_declared_capabilities(
    tmp_path: Path, capabilities: list[str], expected: str
) -> None:
    """`cli-process` passes the case's grant as `--cap` (proposal 0013).

    Without it the isolated lane's default grant (`clock`, `print`, `random`)
    would deny `regex`; with `print` alone the run must still be denied, which
    shows the lane passes exactly the declared list rather than the defaults.
    """
    manifest = _corpus(
        tmp_path / "-".join(capabilities),
        program=_REGEX_PROGRAM,
        targets=["cli-process"],
        capabilities=capabilities,
        expected_stdout="gen0\n",
    )

    results = run_suite(load_manifest(manifest), target="cli-process", timeout=60.0)

    assert [result.status for result in results] == [expected], results


@pytest.mark.parametrize(
    ("unsafe", "capabilities", "expected"),
    [
        (False, ["regex", "print"], ["run", "--cap", "print,regex"]),
        (True, ["print"], ["run", "--unsafe", "--cap", "print"]),
        (False, [], ["run"]),
    ],
    ids=["process", "direct", "process-default"],
)
def test_the_cli_lanes_build_their_cap_arguments(
    monkeypatch: pytest.MonkeyPatch,
    unsafe: bool,
    capabilities: list[str],
    expected: list[str],
) -> None:
    """Both `geno run` lanes pass the declared grant the same way."""
    import subprocess

    from scripts import run_conformance

    seen: list[list[str]] = []

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(run_conformance.subprocess, "run", fake_run)
    case = ConformanceCase(
        id="case",
        path=Path("case.geno"),
        kind="run",
        targets=("cli-process",),
        capabilities=frozenset(capabilities),
        expected_stdout="",
        expected_exit_status=0,
        expected_exit_class=None,
        expected_stderr=None,
        expected_stderr_contains=(),
        expected_json_value=None,
        expected_diagnostics=(),
    )

    run_conformance._run_cli(case, "", 1.0, unsafe=unsafe)

    (argv,) = seen
    assert argv[2:] == ["geno", *expected, "case.geno"]


def test_a_cli_json_case_requires_an_expected_envelope_value(tmp_path: Path) -> None:
    """Otherwise the lane would only re-check what the others already check."""
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["cli-json"],
        expected_stdout="",
        expected_exit_status=3,
    )

    with pytest.raises(ManifestError, match="requires expected_json_value"):
        load_manifest(manifest)


def test_expected_json_value_requires_the_cli_json_target(tmp_path: Path) -> None:
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["interpreter"],
        expected_stdout="",
        expected_exit_status=3,
        expected_json_value="3",
    )

    with pytest.raises(ManifestError, match="requires the cli-json target"):
        load_manifest(manifest)


def test_expected_json_value_must_hold_json(tmp_path: Path) -> None:
    """It is JSON text so that `null` is expressible and omission is distinct."""
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["cli-json"],
        expected_stdout="",
        expected_exit_status=3,
        expected_json_value="{oops",
    )

    with pytest.raises(ManifestError, match="not valid JSON"):
        load_manifest(manifest)


def test_cli_json_cannot_state_a_failing_envelope(tmp_path: Path) -> None:
    """Its diagnostics land in the envelope, not on the stderr the class checks."""
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["cli-json"],
        expected_stdout="",
        expected_exit_class="nonzero",
        expected_stderr_contains=["Error"],
        expected_json_value="null",
    )

    with pytest.raises(ManifestError, match="cannot express a failing envelope"):
        load_manifest(manifest)


def _json_case(tmp_path: Path, expected_json_value: str = "3") -> ConformanceCase:
    manifest = _corpus(
        tmp_path,
        program=_INT_PROGRAM,
        targets=["cli-json"],
        expected_stdout="",
        expected_exit_status=3,
        expected_json_value=expected_json_value,
    )
    return load_manifest(manifest).cases[0]


def _envelope(**overrides: object) -> dict[str, object]:
    """A report shaped like the real one, for the shape checks below."""
    envelope: dict[str, object] = {
        "ok": True,
        "value": 3,
        "output": "",
        "diagnostics": [],
        "timing": {
            "total_ms": 2.84,
            "lex_ms": 0.3,
            "parse_ms": 0.21,
            "typecheck_ms": 1.27,
            "run_ms": 1.06,
        },
        "steps_used": 10,
    }
    envelope.update(overrides)
    return envelope


def test_a_well_formed_envelope_has_no_mismatches(tmp_path: Path) -> None:
    assert _envelope_mismatches(_json_case(tmp_path), _envelope()) == []


def test_an_extra_envelope_key_is_allowed(tmp_path: Path) -> None:
    """A later timing phase must not fail a corpus retained from this series."""
    envelope = _envelope()
    envelope["cached"] = False
    timing = envelope["timing"]
    assert isinstance(timing, dict)
    timing["lower_ms"] = 0.4

    assert _envelope_mismatches(_json_case(tmp_path), envelope) == []


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"ok": False}, "envelope ok true"),
        ({"diagnostics": [{"code": "E412"}]}, "no envelope diagnostics"),
        ({"value": 4}, "envelope value 3, got 4"),
        ({"timing": None}, "envelope timing table"),
        ({"timing": {"total_ms": 1.0}}, "timing.run_ms"),
        ({"timing": {"run_ms": -1.0}}, "timing.run_ms"),
        ({"timing": {"run_ms": "fast"}}, "timing.run_ms"),
        ({"timing": {"run_ms": True}}, "timing.run_ms"),
        ({"steps_used": "ten"}, "steps_used to be an integer"),
        ({"steps_used": True}, "steps_used to be an integer"),
        ({"steps_used": -1}, "steps_used to be non-negative"),
    ],
    ids=[
        "not-ok",
        "diagnostics",
        "value",
        "no-timing",
        "missing-phase",
        "negative-phase",
        "text-phase",
        "bool-phase",
        "text-steps",
        "bool-steps",
        "negative-steps",
    ],
)
def test_the_envelope_shape_is_a_contract(
    tmp_path: Path, overrides: dict[str, object], match: str
) -> None:
    """Timings vary per run, so only the shape is checked -- but it is checked."""
    mismatches = _envelope_mismatches(_json_case(tmp_path), _envelope(**overrides))

    assert any(match in mismatch for mismatch in mismatches), mismatches


@pytest.mark.parametrize(
    ("expected_json_value", "actual", "matches"),
    [
        ("258", 258, True),
        ("true", True, True),
        ("null", None, True),
        ('{"_tuple": []}', {"_tuple": []}, True),
        ("[1, 2]", [1, 2], True),
        ("1", True, False),
        ("0", False, False),
        ("1", 1.0, False),
        ('"3"', 3, False),
        ("[1, 2]", [1, 2, 3], False),
        ('{"a": 1}', {"b": 1}, False),
        ('{"a": 1}', {"a": True}, False),
    ],
    ids=[
        "int",
        "bool",
        "null",
        "unit-tuple",
        "list",
        "true-is-not-one",
        "false-is-not-zero",
        "float-is-not-int",
        "string-is-not-int",
        "shorter-list",
        "other-key",
        "nested-bool",
    ],
)
def test_the_envelope_value_comparison_keeps_json_types(
    tmp_path: Path, expected_json_value: str, actual: object, matches: bool
) -> None:
    """`True == 1` in Python, so a bare `==` would accept the wrong JSON type.

    The value is the entrypoint's declared result, so a backend reporting `true`
    where the contract says `1` is a real disagreement, not a formatting one.
    """
    case = _json_case(tmp_path, expected_json_value=expected_json_value)

    mismatches = _envelope_mismatches(case, _envelope(value=actual))

    assert bool(mismatches) is not matches, mismatches


def test_a_target_absent_from_the_selected_corpus_is_an_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Otherwise it prints `0 passed, 0 failed` and exits 0, which reads green.

    The frozen v0.4 corpus has no case for any lane schema 2 introduced, so
    naming one without a manifest used to exercise nothing and succeed.
    """
    exit_code = main(["--target", "cli-process"])

    assert exit_code == 2
    assert "selected no cases" in capsys.readouterr().err


def test_a_target_only_one_retained_corpus_defines_still_runs() -> None:
    """The guard above is about the whole run, not one corpus within it."""
    assert main(["--all-retained", "--target", "cli-json"]) == 0


def test_every_declared_target_has_a_runner() -> None:
    """A target in ALL_TARGETS with no runner would silently KeyError mid-suite."""
    from scripts.run_conformance import ALL_TARGETS

    for target in ALL_TARGETS:
        assert main(["--all-retained", "--target", target, "--case", "no-such"]) == 2


def test_v05_covers_the_whole_declared_result_table() -> None:
    """Spec 4.1.1 classifies on the resolved return type, so each form is a case.

    An alias, an `async main` and a synchronous `main` that awaits all resolve to
    `Int`, and a corpus that carried only the literal form would not show that.
    The no-`main` row belongs here for the same reason: every other coverage test
    derives its inputs from whichever cases the manifest happens to carry, so
    dropping a case would leave them green and reopen the row it covered.
    """
    manifest = load_manifest(retained_manifest_paths()[-1])
    case_ids = {case.id for case in manifest.cases}

    assert {
        "exit-status-unit",
        "exit-status-int",
        "exit-status-int-wraps",
        "exit-status-int-negative",
        "exit-status-output-before-result",
        "exit-status-aliased-int",
        "exit-status-async-main",
        "exit-status-await-in-sync-main",
        "exit-status-no-main",
        "runtime-error-out-of-bounds",
    } <= case_ids, sorted(case_ids)


def test_the_runtime_error_case_covers_every_lane_owning_a_stderr() -> None:
    """The `expected_exit_class` contract, on each boundary that has the channel.

    The `interpreter` lane hands diagnostics back to its caller as data because
    it is an embedding boundary, and `cli-json` reports them inside the envelope,
    so neither can satisfy a stderr expectation.
    """
    manifest = load_manifest(retained_manifest_paths()[-1])
    error_cases = [
        case for case in manifest.cases if case.expected_exit_class == "nonzero"
    ]

    assert error_cases
    for case in error_cases:
        assert set(case.targets) == set(RUNTIME_TARGETS) - {
            "interpreter",
            "cli-json",
        }, case.id


def test_v05_exit_status_cases_cover_every_executable_boundary() -> None:
    """The contract is that all of them agree, so each case must run on all."""
    manifest = load_manifest(retained_manifest_paths()[-1])
    exit_cases = [case for case in manifest.cases if case.id.startswith("exit-status-")]

    assert exit_cases
    for case in exit_cases:
        assert set(case.targets) == set(RUNTIME_TARGETS), case.id


_PANIC_DIAGNOSTIC: dict[str, object] = {
    "code": "E414",
    "message_contains": ["too large"],
    "location": "case.geno:4:9",
    "stack": ["case.geno:10:12 in main", "case.geno:4:9 in check"],
}


def _failing_json_corpus(tmp_path: Path, **overrides: object) -> Path:
    fields: dict[str, Any] = {
        "targets": ["cli-json"],
        "expected_stdout": "",
        "expected_exit_class": "nonzero",
        "expected_stderr_contains": ["case.geno:4:9 in check"],
        "expected_diagnostic": _PANIC_DIAGNOSTIC,
    }
    fields.update(overrides)
    return _corpus(
        tmp_path,
        program=_PANIC_PROGRAM,
        **{key: value for key, value in fields.items() if value is not None},
    )


def test_expected_diagnostic_states_a_failing_envelope(tmp_path: Path) -> None:
    """Proposal 0018: `cli-json` checks a failure's diagnostic, location and stack."""
    (case,) = load_manifest(_failing_json_corpus(tmp_path)).cases

    assert case.expected_diagnostic is not None
    assert case.expected_diagnostic.code == "E414"
    assert case.expected_diagnostic.location == "case.geno:4:9"
    assert case.expected_diagnostic.stack == (
        "case.geno:10:12 in main",
        "case.geno:4:9 in check",
    )
    assert case.expected_diagnostic.stack_omitted == 0
    assert case.expected_json_value is None

    results = run_suite(
        load_manifest(_failing_json_corpus(tmp_path)), target="cli-json"
    )

    assert [result.status for result in results] == ["passed"], results


def test_a_wrong_expected_frame_fails_the_json_lane(tmp_path: Path) -> None:
    manifest = _failing_json_corpus(
        tmp_path,
        expected_diagnostic={
            **_PANIC_DIAGNOSTIC,
            "stack": ["case.geno:10:5 in main", "case.geno:4:9 in check"],
        },
    )

    (result,) = run_suite(load_manifest(manifest), target="cli-json")

    assert result.status == "failed"
    assert "expected diagnostic stack" in result.detail


@pytest.mark.parametrize(
    ("diagnostic", "match"),
    [
        ({"location": "case.geno:4:9"}, "expected_diagnostic.code"),
        ({"code": "E414", "where": "here"}, "unknown keys: where"),
        ({"code": "E414", "location": "case.geno:4"}, "file:line:col"),
        ({"code": "E414", "location": "case.geno:4:9 in check"}, "not a frame"),
        ({"code": "E414", "stack": ["case.geno:4:9"]}, "'file:line:col in name'"),
        ({"code": "E414", "stack": ["case.geno:x:9 in check"]}, "file:line:col"),
        ({"code": "E414", "stack_omitted": 2}, "stack_omitted requires stack"),
        ({"code": "E414", "stack": [], "stack_omitted": -1}, "non-negative"),
        ({"code": "E414", "stack": [], "stack_omitted": True}, "must be an integer"),
        ({"code": "E414", "message_contains": "too"}, "list of strings"),
    ],
    ids=[
        "no-code",
        "unknown-key",
        "short-location",
        "location-is-frame",
        "frame-without-name",
        "frame-bad-line",
        "omitted-without-stack",
        "negative-omitted",
        "bool-omitted",
        "message-not-list",
    ],
)
def test_expected_diagnostic_is_validated(
    tmp_path: Path, diagnostic: dict[str, object], match: str
) -> None:
    manifest = _failing_json_corpus(tmp_path, expected_diagnostic=diagnostic)

    with pytest.raises(ManifestError, match=match):
        load_manifest(manifest)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"targets": ["cli-direct"]}, "requires the cli-json target"),
        ({"expected_json_value": "null"}, "mutually exclusive"),
        (
            {"expected_exit_class": None, "expected_exit_status": 0},
            "requires a nonzero exit status",
        ),
        (
            {
                "expected_exit_class": None,
                "expected_exit_status": 1,
                "expected_stderr_contains": None,
                "expected_stderr": "",
            },
            "cannot be combined with expected_stderr",
        ),
    ],
    ids=["no-json-lane", "with-value", "zero-status", "exact-stderr"],
)
def test_expected_diagnostic_fits_only_a_failing_json_case(
    tmp_path: Path, overrides: dict[str, object], match: str
) -> None:
    manifest = _failing_json_corpus(tmp_path, **overrides)

    with pytest.raises(ManifestError, match=match):
        load_manifest(manifest)


def _failing_envelope(**diagnostic_overrides: object) -> dict[str, object]:
    diagnostic: dict[str, object] = {
        "code": "E414",
        "message": "too large",
        "severity": "error",
        "location": {"line": 4, "column": 9, "filename": "/work/case.geno"},
        "stack": [
            {
                "function": "main",
                "location": {"line": 10, "column": 12, "filename": "/work/case.geno"},
            },
            {
                "function": "check",
                "location": {"line": 4, "column": 9, "filename": "/work/case.geno"},
            },
        ],
    }
    diagnostic.update(diagnostic_overrides)
    return _envelope(ok=False, value=None, diagnostics=[diagnostic])


def _failing_case(tmp_path: Path, **diagnostic: object) -> ConformanceCase:
    expected = {**_PANIC_DIAGNOSTIC, **diagnostic}
    manifest = _failing_json_corpus(tmp_path, expected_diagnostic=expected)
    return load_manifest(manifest).cases[0]


def test_a_matching_failing_envelope_has_no_mismatches(tmp_path: Path) -> None:
    assert _envelope_mismatches(_failing_case(tmp_path), _failing_envelope()) == []


def test_a_windows_path_matches_at_its_separator(tmp_path: Path) -> None:
    location = {"line": 4, "column": 9, "filename": "C:\\work\\case.geno"}
    envelope = _failing_envelope(location=location)

    assert _envelope_mismatches(_failing_case(tmp_path), envelope) == []


@pytest.mark.parametrize(
    ("envelope", "match"),
    [
        (_envelope(ok=True, value=None, diagnostics=[]), "envelope ok false"),
        (_failing_envelope(code="E401"), "diagnostic code 'E414'"),
        (_failing_envelope(message="other"), "message to contain 'too large'"),
        (
            _failing_envelope(
                location={"line": 4, "column": 9, "filename": "/work/other_case.geno"}
            ),
            "diagnostic location",
        ),
        (_failing_envelope(location=None), "diagnostic location"),
        (_failing_envelope(stack=[]), "diagnostic stack"),
        (_failing_envelope(stack_omitted=3), "stack_omitted 0, got 3"),
        (_failing_envelope(stack_omitted=0), "positive integer"),
        (
            {**_failing_envelope(), "diagnostics": [{"code": "E414"}] * 2},
            "one envelope diagnostic",
        ),
        ({**_failing_envelope(), "value": 3}, "no envelope value"),
    ],
    ids=[
        "succeeded",
        "code",
        "message",
        "file-name-boundary",
        "no-location",
        "no-frames",
        "unexpected-omitted",
        "zero-omitted-key",
        "two-diagnostics",
        "value",
    ],
)
def test_a_failing_envelope_is_checked_field_by_field(
    tmp_path: Path, envelope: dict[str, object], match: str
) -> None:
    mismatches = _envelope_mismatches(_failing_case(tmp_path), envelope)

    assert any(match in mismatch for mismatch in mismatches), mismatches


def test_an_expected_omitted_count_is_checked(tmp_path: Path) -> None:
    case = _failing_case(tmp_path, stack_omitted=4)

    assert _envelope_mismatches(case, _failing_envelope(stack_omitted=4)) == []
    assert any(
        "stack_omitted 4" in mismatch
        for mismatch in _envelope_mismatches(case, _failing_envelope())
    )


def test_the_stderr_fragments_are_not_applied_to_the_json_envelope(
    tmp_path: Path,
) -> None:
    """`--json` reports a failure in the envelope, so its stderr stays unread."""
    from scripts.run_conformance import TargetOutcome, _channel_mismatches

    case = _failing_case(tmp_path)
    outcome = TargetOutcome("", "", 1, _failing_envelope())

    assert _channel_mismatches(case, outcome) == []
    assert _channel_mismatches(case, TargetOutcome("", "", 1)) == [
        "expected stderr to contain 'case.geno:4:9 in check', got ''"
    ]
