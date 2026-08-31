"""Tests for scripts/pytest_shard.py."""

import json
from pathlib import Path

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
