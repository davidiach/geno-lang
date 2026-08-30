"""Tests for scripts/pytest_shard.py."""

import pytest

from scripts import pytest_shard


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


def test_main_list_only_reports_selected_shard(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        pytest_shard,
        "collect_test_counts",
        lambda: {
            "geno/tests/test_alpha.py": 3,
            "geno/tests/test_beta.py": 2,
        },
    )

    assert (
        pytest_shard.main(["--shard-index", "1", "--shard-count", "2", "--list-only"])
        == 0
    )
    output = capsys.readouterr().out
    assert "pytest shard 2/2: 1 files, 2/5 nodes" in output
    assert "geno/tests/test_beta.py" in output
