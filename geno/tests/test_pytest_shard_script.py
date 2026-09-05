"""Tests for scripts/pytest_shard.py."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pytest_shard


def _write_plan_manifests(
    tmp_path: Path,
    *,
    counts: dict[str, int] | None = None,
    shards: list[list[str]] | None = None,
) -> list[Path]:
    counts = counts or {
        "geno/tests/test_alpha.py": 3,
        "geno/tests/test_beta.py": 2,
        "geno/tests/test_gamma.py": 1,
    }
    weights = dict(counts)
    shards = shards or pytest_shard.partition_test_files(
        counts, 3, balance_weights=weights
    )
    paths = [tmp_path / f"shard-plan.{index}.json" for index in range(3)]
    for index, path in enumerate(paths):
        pytest_shard.write_shard_plan_manifest(
            path,
            test_counts=counts,
            balance_weights=weights,
            shards=shards,
            shard_index=index,
            balance_profile="demo-profile",
        )
    return paths


def _write_timing_manifests(tmp_path: Path, plan: pytest_shard.ShardPlan) -> list[Path]:
    fingerprint = pytest_shard.shard_plan_sha256(plan)
    paths = [tmp_path / f"shard-timing.{index}.json" for index in range(3)]
    for index, path in enumerate(paths):
        files: list[pytest_shard.FileTiming] = [
            {
                "path": test_path,
                "node_count": plan["collected_test_counts"][test_path],
                "call_report_count": plan["collected_test_counts"][test_path],
                "passed_count": plan["collected_test_counts"][test_path],
                "skipped_count": 0,
                "xfailed_count": 0,
                "xpassed_count": 0,
                "failed_count": 0,
                "setup_ms": 1,
                "call_ms": 2 + index,
                "teardown_ms": 0,
                "total_ms": 3 + index,
            }
            for test_path in plan["shards"][index]
        ]
        reported_ms = sum(file["total_ms"] for file in files)
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "shard_index": index,
                    "plan_sha256": fingerprint,
                    "measurement": pytest_shard._TIMING_MEASUREMENT,
                    "provenance": pytest_shard._timing_provenance(),
                    "pytest_exitstatus": 0,
                    "session_elapsed_ms": reported_ms + 10,
                    "reported_elapsed_ms": reported_ms,
                    "unattributed_elapsed_ms": 10,
                    "files": files,
                }
            ),
            encoding="utf-8",
        )
    return paths


def test_parse_collected_test_counts_normalizes_paths() -> None:
    output = """
geno/tests/test_alpha.py::test_one
geno/tests/test_alpha.py::test_two
geno\\tests\\test_beta.py::TestDemo::test_three
6039 tests collected in 1.23s
"""

    assert pytest_shard.parse_collected_test_counts(output) == {
        "geno/tests/test_alpha.py": 2,
        "geno/tests/test_beta.py": 1,
    }


def test_partition_test_files_is_balanced_complete_and_stable() -> None:
    counts = {
        "geno/tests/test_alpha.py": 8,
        "geno/tests/test_beta.py": 7,
        "geno/tests/test_gamma.py": 5,
        "geno/tests/test_delta.py": 4,
    }

    first = pytest_shard.partition_test_files(counts, 2)
    second = pytest_shard.partition_test_files(dict(reversed(counts.items())), 2)

    assert first == second
    assert first == [
        ["geno/tests/test_alpha.py", "geno/tests/test_delta.py"],
        ["geno/tests/test_beta.py", "geno/tests/test_gamma.py"],
    ]
    assert sorted(path for shard in first for path in shard) == sorted(counts)
    weights = [sum(counts[path] for path in shard) for shard in first]
    assert weights == [12, 12]


def test_partition_test_files_uses_complete_runtime_weights_stably() -> None:
    counts = {
        "geno/tests/test_alpha.py": 8,
        "geno/tests/test_beta.py": 7,
        "geno/tests/test_gamma.py": 5,
        "geno/tests/test_delta.py": 4,
    }
    weights = {
        "geno/tests/test_alpha.py": 1,
        "geno/tests/test_beta.py": 100,
        "geno/tests/test_gamma.py": 1,
        "geno/tests/test_delta.py": 1,
    }

    first = pytest_shard.partition_test_files(counts, 2, balance_weights=weights)
    second = pytest_shard.partition_test_files(
        dict(reversed(counts.items())),
        2,
        balance_weights=dict(reversed(weights.items())),
    )

    assert first == second
    assert first == [
        ["geno/tests/test_beta.py"],
        [
            "geno/tests/test_alpha.py",
            "geno/tests/test_delta.py",
            "geno/tests/test_gamma.py",
        ],
    ]
    assert sorted(path for shard in first for path in shard) == sorted(counts)


@pytest.mark.parametrize(
    "weights",
    [
        {"geno/tests/test_alpha.py": 1},
        {
            "geno/tests/test_alpha.py": 1,
            "geno/tests/test_beta.py": 0,
        },
    ],
)
def test_partition_test_files_rejects_invalid_runtime_weights(
    weights: dict[str, int],
) -> None:
    counts = {
        "geno/tests/test_alpha.py": 1,
        "geno/tests/test_beta.py": 1,
    }

    with pytest.raises(ValueError, match="balance weight"):
        pytest_shard.partition_test_files(counts, 2, balance_weights=weights)


def test_balance_profile_uses_runtime_outliers_and_node_fallback() -> None:
    counts = dict.fromkeys(pytest_shard._COVERAGE_RUNTIME_COST_MS, 1)
    counts["geno/tests/test_new_file.py"] = 7

    weights = pytest_shard.balance_weights_for_profile(
        counts, pytest_shard.COVERAGE_BALANCE_PROFILE, 3
    )

    assert weights["geno/tests/test_backend_parity.py"] == 157_000
    assert weights["geno/tests/test_new_file.py"] == 7 * 55
    assert set(weights) == set(counts)

    first = pytest_shard.partition_test_files(counts, 3, balance_weights=weights)
    reversed_counts = dict(reversed(counts.items()))
    reversed_weights = pytest_shard.balance_weights_for_profile(
        reversed_counts, pytest_shard.COVERAGE_BALANCE_PROFILE, 3
    )
    second = pytest_shard.partition_test_files(
        reversed_counts, 3, balance_weights=reversed_weights
    )
    assert first == second
    assert sorted(path for shard in first for path in shard) == sorted(counts)


def test_coverage_profile_heavy_groups_survive_small_collection_growth() -> None:
    counts = {
        "geno/tests/test_backend_parity.py": 240,
        "geno/tests/test_cli.py": 131,
        "geno/tests/test_differential_fuzzing.py": 5,
        "geno/tests/test_js_compiler.py": 333,
        "geno/tests/test_parity.py": 6,
        "geno/tests/test_security_corpus.py": 115,
        "geno/tests/test_self_hosting.py": 47,
        "geno/tests/test_server.py": 247,
        "geno/tests/test_tooling.py": 133,
    }
    counts.update(
        {f"geno/tests/test_ordinary_{index:03}.py": 38 for index in range(122)}
    )
    counts["geno/tests/test_ordinary_000.py"] += 7
    assert sum(counts.values()) == 5_900

    expected_heavy_groups = [
        {
            "geno/tests/test_backend_parity.py",
            "geno/tests/test_js_compiler.py",
            "geno/tests/test_server.py",
        },
        {
            "geno/tests/test_parity.py",
            "geno/tests/test_self_hosting.py",
            "geno/tests/test_tooling.py",
        },
        {
            "geno/tests/test_cli.py",
            "geno/tests/test_differential_fuzzing.py",
            "geno/tests/test_security_corpus.py",
        },
    ]

    for added_nodes in (0, 10):
        grown_counts = dict(counts)
        grown_counts["geno/tests/test_ordinary_000.py"] += added_nodes
        weights = pytest_shard.balance_weights_for_profile(
            grown_counts, pytest_shard.COVERAGE_BALANCE_PROFILE, 3
        )
        shards = pytest_shard.partition_test_files(
            grown_counts, 3, balance_weights=weights
        )
        heavy_groups = [
            set(shard) & set(pytest_shard._COVERAGE_RUNTIME_COST_MS) for shard in shards
        ]
        totals = [sum(weights[path] for path in shard) for shard in shards]

        assert heavy_groups == expected_heavy_groups
        assert max(totals) - min(totals) < 2_500
        assert sorted(path for shard in shards for path in shard) == sorted(
            grown_counts
        )


def test_shard_plan_manifests_validate_round_trip(tmp_path: Path, capsys) -> None:
    paths = _write_plan_manifests(tmp_path)

    plan = pytest_shard.validate_shard_plan_manifests(paths)

    assert plan["total_nodes"] == 6
    assert plan["shard_count"] == 3
    assert (
        len(
            {
                json.loads(path.read_text(encoding="utf-8"))["plan_sha256"]
                for path in paths
            }
        )
        == 1
    )
    assert (
        pytest_shard.main(["--validate-plan-manifests", *(str(path) for path in paths)])
        == 0
    )
    assert "validated 3 matching pytest shard plans" in capsys.readouterr().out


def test_file_timing_plugin_aggregates_phases_and_node_ids(
    tmp_path: Path, monkeypatch
) -> None:
    clock = iter((1_000_000_000, 2_250_000_000))
    monkeypatch.setattr(pytest_shard.time, "perf_counter_ns", lambda: next(clock))
    output = tmp_path / "shard-timing.0.json"
    plugin = pytest_shard._FileTimingPlugin(output, 0, "a" * 64)
    plugin.pytest_sessionstart(None)
    for report in (
        SimpleNamespace(
            nodeid=r"geno\tests\test_alpha.py::test_one",
            when="setup",
            duration=0.004,
            failed=False,
            skipped=False,
            passed=True,
        ),
        SimpleNamespace(
            nodeid="geno/tests/test_alpha.py::test_one",
            when="call",
            duration=0.050,
            failed=False,
            skipped=False,
            passed=True,
        ),
        SimpleNamespace(
            nodeid="geno/tests/test_alpha.py::test_one",
            when="teardown",
            duration=0.006,
            failed=False,
            skipped=False,
            passed=True,
        ),
        SimpleNamespace(
            nodeid="geno/tests/test_alpha.py::test_two",
            when="call",
            duration=0.040,
            failed=False,
            skipped=False,
            passed=True,
        ),
    ):
        plugin.pytest_runtest_logreport(report)
    plugin.pytest_sessionfinish(None, pytest.ExitCode.OK)

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["pytest_exitstatus"] == 0
    assert manifest["session_elapsed_ms"] == 1_250
    assert manifest["reported_elapsed_ms"] == 100
    assert manifest["unattributed_elapsed_ms"] == 1_150
    assert manifest["files"] == [
        {
            "path": "geno/tests/test_alpha.py",
            "node_count": 2,
            "call_report_count": 2,
            "passed_count": 2,
            "skipped_count": 0,
            "xfailed_count": 0,
            "xpassed_count": 0,
            "failed_count": 0,
            "setup_ms": 4,
            "call_ms": 90,
            "teardown_ms": 6,
            "total_ms": 100,
        }
    ]


def test_file_timing_plugin_runs_in_nested_pytest(
    tmp_path: Path,
) -> None:
    output = tmp_path / "shard-timing.0.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            (
                "geno/tests/test_pytest_shard_script.py::"
                "test_parse_collected_test_counts_normalizes_paths"
            ),
            "-q",
            "-o",
            "addopts=",
            "-p",
            "scripts.pytest_shard",
            "--geno-file-timings-json",
            str(output),
            "--geno-shard-index",
            "0",
            "--geno-plan-sha256",
            "a" * 64,
        ],
        cwd=pytest_shard.ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["files"][0]["node_count"] == 1
    assert manifest["files"][0]["path"] == ("geno/tests/test_pytest_shard_script.py")


def test_file_timing_plugin_records_setup_and_runtime_skips(
    tmp_path: Path, monkeypatch
) -> None:
    clock = iter((1_000_000_000, 1_100_000_000))
    monkeypatch.setattr(pytest_shard.time, "perf_counter_ns", lambda: next(clock))
    output = tmp_path / "shard-timing.0.json"
    plugin = pytest_shard._FileTimingPlugin(output, 0, "a" * 64)
    plugin.pytest_sessionstart(None)
    for report in (
        SimpleNamespace(
            nodeid="geno/tests/test_alpha.py::test_setup_skip",
            when="setup",
            duration=0.010,
            failed=False,
            skipped=True,
            passed=False,
        ),
        SimpleNamespace(
            nodeid="geno/tests/test_alpha.py::test_runtime_skip",
            when="setup",
            duration=0.005,
            failed=False,
            skipped=False,
            passed=True,
        ),
        SimpleNamespace(
            nodeid="geno/tests/test_alpha.py::test_runtime_skip",
            when="call",
            duration=0.020,
            failed=False,
            skipped=True,
            passed=False,
        ),
    ):
        plugin.pytest_runtest_logreport(report)
    plugin.pytest_sessionfinish(None, pytest.ExitCode.OK)

    timing = json.loads(output.read_text(encoding="utf-8"))["files"][0]
    assert timing["node_count"] == 2
    assert timing["call_report_count"] == 1
    assert timing["skipped_count"] == 2
    assert timing["passed_count"] == 0


def test_shard_timing_manifests_validate_round_trip(tmp_path: Path, capsys) -> None:
    plan_paths = _write_plan_manifests(tmp_path)
    plan = pytest_shard.validate_shard_plan_manifests(plan_paths)
    timing_paths = _write_timing_manifests(tmp_path, plan)

    manifests = pytest_shard.validate_shard_timing_manifests(timing_paths, plan)

    assert [manifest["shard_index"] for manifest in manifests] == [0, 1, 2]
    assert (
        pytest_shard.main(
            [
                "--validate-plan-manifests",
                *(str(path) for path in plan_paths),
                "--timing-manifests",
                *(str(path) for path in timing_paths),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "validated pytest shard timings" in output
    assert "slowest files" in output


def test_main_labels_timing_validation_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_paths = _write_plan_manifests(tmp_path)
    plan = pytest_shard.validate_shard_plan_manifests(plan_paths)
    timing_paths = _write_timing_manifests(tmp_path, plan)
    timing_paths[0].write_text("{}", encoding="utf-8")

    assert (
        pytest_shard.main(
            [
                "--validate-plan-manifests",
                *(str(path) for path in plan_paths),
                "--timing-manifests",
                *(str(path) for path in timing_paths),
            ]
        )
        == 1
    )
    assert "pytest shard timing validation failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("fingerprint", "fingerprint"),
        ("node-count", "node count"),
        ("missing-file", "nonempty list"),
        ("negative-duration", "nonnegative integer"),
        ("bool-duration", "nonnegative integer"),
        ("phase-total", "phases do not sum"),
        ("outcome-total", "outcomes do not sum"),
        ("call-count", "call report count exceeds"),
        ("call-count-low", "call report count misses completed calls"),
        ("failed-outcome", "successful timing contains failed"),
        ("provenance", "environment fingerprint"),
        ("impossible-session", "reported timing exceeds session"),
        ("session-total", "unattributed timing"),
    ],
)
def test_shard_timing_validation_rejects_invalid_data(
    tmp_path: Path, mutation: str, message: str
) -> None:
    plan = pytest_shard.validate_shard_plan_manifests(_write_plan_manifests(tmp_path))
    timing_paths = _write_timing_manifests(tmp_path, plan)
    manifest = json.loads(timing_paths[0].read_text(encoding="utf-8"))
    if mutation == "fingerprint":
        manifest["plan_sha256"] = "0" * 64
    elif mutation == "node-count":
        manifest["files"][0]["node_count"] += 1
        manifest["files"][0]["call_report_count"] += 1
        manifest["files"][0]["passed_count"] += 1
    elif mutation == "missing-file":
        manifest["files"] = []
        manifest["reported_elapsed_ms"] = 0
        manifest["unattributed_elapsed_ms"] = manifest["session_elapsed_ms"]
    elif mutation == "negative-duration":
        manifest["files"][0]["call_ms"] = -1
    elif mutation == "bool-duration":
        manifest["files"][0]["call_ms"] = True
    elif mutation == "phase-total":
        manifest["files"][0]["total_ms"] += 1
    elif mutation == "outcome-total":
        manifest["files"][0]["passed_count"] -= 1
    elif mutation == "call-count":
        manifest["files"][0]["call_report_count"] += 1
    elif mutation == "call-count-low":
        manifest["files"][0]["call_report_count"] = 0
    elif mutation == "failed-outcome":
        manifest["files"][0]["passed_count"] -= 1
        manifest["files"][0]["failed_count"] += 1
    elif mutation == "provenance":
        manifest["provenance"]["environment_sha256"] = "0" * 64
    elif mutation == "impossible-session":
        manifest["session_elapsed_ms"] = 0
        manifest["unattributed_elapsed_ms"] = 0
    else:
        manifest["unattributed_elapsed_ms"] += 1
    timing_paths[0].write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        pytest_shard.validate_shard_timing_manifests(timing_paths, plan)


def test_shard_timing_validation_rejects_incomplete_set(tmp_path: Path) -> None:
    plan = pytest_shard.validate_shard_plan_manifests(_write_plan_manifests(tmp_path))
    timing_paths = _write_timing_manifests(tmp_path, plan)

    with pytest.raises(ValueError, match="expected 3 shard timing manifests"):
        pytest_shard.validate_shard_timing_manifests(timing_paths[:2], plan)


def test_shard_timing_validation_rejects_mixed_ci_runs(tmp_path: Path) -> None:
    plan = pytest_shard.validate_shard_plan_manifests(_write_plan_manifests(tmp_path))
    timing_paths = _write_timing_manifests(tmp_path, plan)
    manifest = json.loads(timing_paths[1].read_text(encoding="utf-8"))
    manifest["provenance"]["github_run_attempt"] = "2"
    timing_paths[1].write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="different CI runs"):
        pytest_shard.validate_shard_timing_manifests(timing_paths, plan)


def test_failed_job_retry_preserves_successful_shard_timings(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    plan_paths = _write_plan_manifests(tmp_path)
    plan = pytest_shard.validate_shard_plan_manifests(plan_paths)
    timing_paths = _write_timing_manifests(tmp_path, plan)
    # Re-run just the failed leg: the other two uploads remain from attempt 1.
    manifest = json.loads(timing_paths[1].read_text(encoding="utf-8"))
    manifest["provenance"]["github_run_attempt"] = "2"
    timing_paths[1].write_text(json.dumps(manifest), encoding="utf-8")

    assert (
        pytest_shard.main(
            [
                "--validate-plan-manifests",
                *(str(path) for path in plan_paths),
                "--timing-manifests",
                *(str(path) for path in timing_paths),
                "--allow-mixed-attempts",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "not a comparable timing sample" in output
    assert "attempt=1" in output
    assert "attempt=2" in output


@pytest.mark.parametrize("field", ["commit_sha", "github_run_id"])
def test_retry_validation_still_rejects_different_ci_runs(
    tmp_path: Path, field: str
) -> None:
    plan = pytest_shard.validate_shard_plan_manifests(_write_plan_manifests(tmp_path))
    timing_paths = _write_timing_manifests(tmp_path, plan)
    manifest = json.loads(timing_paths[1].read_text(encoding="utf-8"))
    manifest["provenance"][field] = "another-run"
    timing_paths[1].write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="different CI runs"):
        pytest_shard.validate_shard_timing_manifests(
            timing_paths, plan, allow_mixed_attempts=True
        )


def test_shard_timing_validation_rejects_mixed_runtime_environments(
    tmp_path: Path, monkeypatch
) -> None:
    plan = pytest_shard.validate_shard_plan_manifests(_write_plan_manifests(tmp_path))
    timing_paths = _write_timing_manifests(tmp_path, plan)
    manifest = json.loads(timing_paths[1].read_text(encoding="utf-8"))
    monkeypatch.setenv(pytest_shard._RUNNER_IMAGE_VERSION_ENV, "different-image")
    manifest["provenance"] = pytest_shard._timing_provenance()
    timing_paths[1].write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="different runtime environments"):
        pytest_shard.validate_shard_timing_manifests(timing_paths, plan)


def test_shard_timing_validation_rejects_filename_index_mismatch(
    tmp_path: Path,
) -> None:
    plan = pytest_shard.validate_shard_plan_manifests(_write_plan_manifests(tmp_path))
    timing_path = _write_timing_manifests(tmp_path, plan)[0]
    renamed = tmp_path / "shard-timing.copy.json"
    renamed.write_text(timing_path.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError, match="filename does not match"):
        pytest_shard._load_shard_timing_manifest(renamed, plan)


def test_shard_plan_validation_rejects_collection_drift(tmp_path: Path) -> None:
    paths = _write_plan_manifests(tmp_path)
    changed_counts = {
        "geno/tests/test_alpha.py": 4,
        "geno/tests/test_beta.py": 2,
        "geno/tests/test_gamma.py": 1,
    }
    changed_shards = pytest_shard.partition_test_files(changed_counts, 3)
    pytest_shard.write_shard_plan_manifest(
        paths[1],
        test_counts=changed_counts,
        balance_weights=changed_counts,
        shards=changed_shards,
        shard_index=1,
        balance_profile="demo-profile",
    )

    with pytest.raises(ValueError, match="plans disagree"):
        pytest_shard.validate_shard_plan_manifests(paths)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("fingerprint", "fingerprint"),
        ("schema", "schema version"),
        ("selected", "selected shard"),
    ],
)
def test_shard_plan_validation_rejects_tampering(
    tmp_path: Path, mutation: str, message: str
) -> None:
    paths = _write_plan_manifests(tmp_path)
    manifest = json.loads(paths[0].read_text(encoding="utf-8"))
    if mutation == "fingerprint":
        manifest["plan_sha256"] = "0" * 64
    elif mutation == "schema":
        manifest["plan"]["schema_version"] = 2
    else:
        manifest["selected_shard"] = ["geno/tests/test_beta.py"]
    paths[0].write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        pytest_shard.validate_shard_plan_manifests(paths)


def test_shard_plan_validation_rejects_incomplete_manifest_set(
    tmp_path: Path,
) -> None:
    paths = _write_plan_manifests(tmp_path)

    with pytest.raises(ValueError, match="expected 3 shard plan manifests"):
        pytest_shard.validate_shard_plan_manifests(paths[:2])


def test_shard_plan_validation_rejects_non_exact_assignment(tmp_path: Path) -> None:
    invalid_shards = [
        ["geno/tests/test_alpha.py"],
        ["geno/tests/test_alpha.py"],
        ["geno/tests/test_gamma.py"],
    ]
    paths = _write_plan_manifests(tmp_path, shards=invalid_shards)

    with pytest.raises(ValueError, match="exactly once"):
        pytest_shard.validate_shard_plan_manifests(paths)


def test_balance_profile_rejects_wrong_shard_count() -> None:
    with pytest.raises(ValueError, match="requires 3 shards"):
        pytest_shard.balance_weights_for_profile(
            {}, pytest_shard.COVERAGE_BALANCE_PROFILE, 2
        )


def test_balance_profile_rejects_missing_profiled_file() -> None:
    with pytest.raises(ValueError, match="was not collected"):
        pytest_shard.balance_weights_for_profile(
            {}, pytest_shard.COVERAGE_BALANCE_PROFILE, 3
        )


def test_balance_profile_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError, match="unknown balance profile"):
        pytest_shard.balance_weights_for_profile({}, "unknown", 3)


@pytest.mark.parametrize(
    ("counts", "shard_count", "message"),
    [
        ({}, 2, "no collected tests"),
        ({"geno/tests/test_alpha.py": 1}, 0, "at least 1"),
        ({"geno/tests/test_alpha.py": 1}, 2, "cannot exceed"),
        ({"geno/tests/test_alpha.py": 0}, 1, "at least one collected test"),
    ],
)
def test_partition_test_files_rejects_invalid_inputs(
    counts: dict[str, int], shard_count: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        pytest_shard.partition_test_files(counts, shard_count)


def test_main_list_only_reports_selected_shard(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        pytest_shard,
        "collect_test_counts",
        lambda: {
            "geno/tests/test_alpha.py": 3,
            "geno/tests/test_beta.py": 2,
        },
    )

    plan_path = tmp_path / "shard-plan.1.json"
    assert (
        pytest_shard.main(
            [
                "--shard-index",
                "1",
                "--shard-count",
                "2",
                "--plan-manifest",
                str(plan_path),
                "--list-only",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "pytest shard 2/2: 1 files, 2/5 nodes" in output
    assert "geno/tests/test_beta.py" in output
    manifest = json.loads(plan_path.read_text(encoding="utf-8"))
    assert manifest["shard_index"] == 1
    assert manifest["selected_shard"] == ["geno/tests/test_beta.py"]


def test_main_rejects_plan_manifest_filename_that_cannot_be_validated(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit, match=r"shard-plan\.1\.json"):
        pytest_shard.main(
            [
                "--shard-index",
                "1",
                "--shard-count",
                "2",
                "--plan-manifest",
                str(tmp_path / "plan.json"),
                "--list-only",
            ]
        )


def test_main_writes_and_validates_shard_timing(tmp_path: Path, monkeypatch) -> None:
    counts = {
        "geno/tests/test_alpha.py": 3,
        "geno/tests/test_beta.py": 2,
    }
    monkeypatch.setattr(pytest_shard, "collect_test_counts", lambda: counts)
    timing_path = tmp_path / "shard-timing.1.json"

    def fake_run(command, *, cwd, check):
        assert cwd == pytest_shard.ROOT
        assert check is False
        args = list(command)
        output = Path(args[args.index("--geno-file-timings-json") + 1])
        plan_sha256 = args[args.index("--geno-plan-sha256") + 1]
        output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "shard_index": 1,
                    "plan_sha256": plan_sha256,
                    "measurement": pytest_shard._TIMING_MEASUREMENT,
                    "provenance": pytest_shard._timing_provenance(),
                    "pytest_exitstatus": 0,
                    "session_elapsed_ms": 60,
                    "reported_elapsed_ms": 50,
                    "unattributed_elapsed_ms": 10,
                    "files": [
                        {
                            "path": "geno/tests/test_beta.py",
                            "node_count": 2,
                            "call_report_count": 2,
                            "passed_count": 2,
                            "skipped_count": 0,
                            "xfailed_count": 0,
                            "xpassed_count": 0,
                            "failed_count": 0,
                            "setup_ms": 5,
                            "call_ms": 40,
                            "teardown_ms": 5,
                            "total_ms": 50,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pytest_shard.subprocess, "run", fake_run)

    assert (
        pytest_shard.main(
            [
                "--shard-index",
                "1",
                "--shard-count",
                "2",
                "--plan-manifest",
                str(tmp_path / "shard-plan.1.json"),
                "--timing-manifest",
                str(timing_path),
            ]
        )
        == 0
    )
    assert (
        json.loads(timing_path.read_text(encoding="utf-8"))["files"][0]["total_ms"]
        == 50
    )


def test_main_removes_stale_timing_and_preserves_pytest_failure(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        pytest_shard,
        "collect_test_counts",
        lambda: {"geno/tests/test_alpha.py": 1},
    )
    timing_path = tmp_path / "shard-timing.0.json"
    timing_path.write_text("stale", encoding="utf-8")

    def fake_failed_run(command, *, cwd, check):
        args = list(command)
        output = Path(args[args.index("--geno-file-timings-json") + 1])
        output.write_text("fresh failed-run timing", encoding="utf-8")
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(pytest_shard.subprocess, "run", fake_failed_run)

    assert (
        pytest_shard.main(
            [
                "--shard-index",
                "0",
                "--shard-count",
                "1",
                "--plan-manifest",
                str(tmp_path / "shard-plan.0.json"),
                "--timing-manifest",
                str(timing_path),
            ]
        )
        == 1
    )
    assert not timing_path.exists()


def test_main_removes_post_run_invalid_timing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        pytest_shard,
        "collect_test_counts",
        lambda: {"geno/tests/test_alpha.py": 1},
    )
    timing_path = tmp_path / "shard-timing.0.json"

    def fake_success_with_invalid_timing(command, *, cwd, check):
        args = list(command)
        output = Path(args[args.index("--geno-file-timings-json") + 1])
        output.write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        pytest_shard.subprocess, "run", fake_success_with_invalid_timing
    )

    assert (
        pytest_shard.main(
            [
                "--shard-index",
                "0",
                "--shard-count",
                "1",
                "--plan-manifest",
                str(tmp_path / "shard-plan.0.json"),
                "--timing-manifest",
                str(timing_path),
            ]
        )
        == 1
    )
    assert not timing_path.exists()


@pytest.mark.parametrize(
    ("extra_args", "message"),
    [
        (["--timing-manifest", "shard-timing.0.json"], "requires --plan-manifest"),
        (
            [
                "--plan-manifest",
                "shard-plan.0.json",
                "--timing-manifest",
                "timing.json",
            ],
            r"shard-timing\.0\.json",
        ),
    ],
)
def test_main_rejects_invalid_timing_options(
    extra_args: list[str], message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        pytest_shard.main(["--shard-index", "0", "--shard-count", "1", *extra_args])


def test_main_list_only_applies_balance_profile(monkeypatch, capsys) -> None:
    counts = dict.fromkeys(pytest_shard._COVERAGE_RUNTIME_COST_MS, 1)
    counts.update(
        {
            "geno/tests/test_alpha.py": 6,
            "geno/tests/test_beta.py": 5,
            "geno/tests/test_gamma.py": 4,
        }
    )
    monkeypatch.setattr(
        pytest_shard,
        "collect_test_counts",
        lambda: counts,
    )

    assert (
        pytest_shard.main(
            [
                "--shard-index",
                "2",
                "--shard-count",
                "3",
                "--balance-profile",
                pytest_shard.COVERAGE_BALANCE_PROFILE,
                "--list-only",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert f"profile={pytest_shard.COVERAGE_BALANCE_PROFILE}" in output
