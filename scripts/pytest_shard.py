#!/usr/bin/env python3
"""Run one deterministic, file-level shard of the Geno pytest suite."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = "geno/tests"
_NODE_ID_RE = re.compile(r"^(geno[/\\]tests[/\\]test_[^:]+\.py)::")

COVERAGE_BALANCE_PROFILE = "coverage-ubuntu-py311"
# The remaining hosted files average 54.82 ms per collected node after the
# measured outliers below are removed.
_COVERAGE_FALLBACK_MS_PER_NODE = 55
_COVERAGE_RUNTIME_COST_MS = {
    # Rounded file runtimes from hosted Ubuntu/Python 3.11 coverage. Node counts
    # remain the fallback, while these outliers keep small collection changes
    # from reshuffling the slowest work onto the same runner.
    "geno/tests/test_backend_parity.py": 157_000,
    "geno/tests/test_cli.py": 130_000,
    "geno/tests/test_differential_fuzzing.py": 30_000,
    "geno/tests/test_js_compiler.py": 61_000,
    "geno/tests/test_parity.py": 56_000,
    "geno/tests/test_security_corpus.py": 94_000,
    "geno/tests/test_self_hosting.py": 133_000,
    "geno/tests/test_server.py": 54_000,
    "geno/tests/test_tooling.py": 78_000,
}
_BALANCE_PROFILES = {
    COVERAGE_BALANCE_PROFILE: (
        3,
        _COVERAGE_FALLBACK_MS_PER_NODE,
        _COVERAGE_RUNTIME_COST_MS,
    )
}


def parse_collected_test_counts(output: str) -> dict[str, int]:
    """Return normalized test-file node counts from pytest collection output."""
    counts: Counter[str] = Counter()
    for line in output.splitlines():
        match = _NODE_ID_RE.match(line.strip())
        if match is not None:
            counts[match.group(1).replace("\\", "/")] += 1
    return dict(counts)


def partition_test_files(
    test_counts: dict[str, int],
    shard_count: int,
    *,
    balance_weights: Mapping[str, int] | None = None,
) -> list[list[str]]:
    """Greedily balance whole test files by node count or supplied weights."""
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")
    if not test_counts:
        raise ValueError("no collected tests were found")
    if shard_count > len(test_counts):
        raise ValueError("shard_count cannot exceed the number of test files")
    if any(count < 1 for count in test_counts.values()):
        raise ValueError("every test file must contain at least one collected test")

    weights_by_file = test_counts if balance_weights is None else balance_weights
    if set(weights_by_file) != set(test_counts):
        raise ValueError("balance weights must match the collected test-file set")
    if any(weight < 1 for weight in weights_by_file.values()):
        raise ValueError("every balance weight must be at least 1")

    shards: list[list[str]] = [[] for _ in range(shard_count)]
    weights = [0] * shard_count
    ordered_files = sorted(test_counts, key=lambda path: (-weights_by_file[path], path))
    for path in ordered_files:
        shard_index = min(range(shard_count), key=lambda index: (weights[index], index))
        shards[shard_index].append(path)
        weights[shard_index] += weights_by_file[path]

    for shard in shards:
        shard.sort()
    return shards


def balance_weights_for_profile(
    test_counts: dict[str, int], profile: str, shard_count: int
) -> dict[str, int]:
    """Return live-set weights for a checked-in runtime balance profile."""
    try:
        expected_shards, fallback_ms_per_node, runtime_costs = _BALANCE_PROFILES[
            profile
        ]
    except KeyError as exc:
        raise ValueError(f"unknown balance profile: {profile}") from exc
    if shard_count != expected_shards:
        raise ValueError(
            f"balance profile {profile!r} requires {expected_shards} shards"
        )

    # Treat renamed or removed measured files as profile maintenance failures,
    # rather than silently degrading a reviewed hosted-CI balance profile.
    missing_paths = sorted(set(runtime_costs) - set(test_counts))
    if missing_paths:
        raise ValueError(
            f"balance profile test file was not collected: {missing_paths[0]}"
        )
    return {
        path: runtime_costs.get(path, count * fallback_ms_per_node)
        for path, count in test_counts.items()
    }


def collect_test_counts() -> dict[str, int]:
    """Collect all tests once and return their per-file node counts."""
    command = (
        sys.executable,
        "-m",
        "pytest",
        TEST_ROOT,
        "--collect-only",
        "-q",
        "-o",
        "addopts=",
    )
    result = subprocess.run(  # noqa: S603
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise RuntimeError("pytest collection failed")

    counts = parse_collected_test_counts(result.stdout)
    if not counts:
        raise RuntimeError("pytest collection returned no test node IDs")
    return counts


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=2)
    parser.add_argument(
        "--balance-profile",
        choices=tuple(sorted(_BALANCE_PROFILES)),
        help="Apply a checked-in runtime balance profile after collection.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Print the selected files without running pytest.",
    )
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Collect, partition, and run one pytest shard."""
    args = _parse_args(argv or sys.argv[1:])
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("--shard-index must be in [0, --shard-count)")

    counts = collect_test_counts()
    balance_weights = None
    if args.balance_profile is not None:
        balance_weights = balance_weights_for_profile(
            counts, args.balance_profile, args.shard_count
        )
    shards = partition_test_files(
        counts, args.shard_count, balance_weights=balance_weights
    )
    selected = shards[args.shard_index]
    selected_nodes = sum(counts[path] for path in selected)
    total_nodes = sum(counts.values())
    profile_label = f", profile={args.balance_profile}" if args.balance_profile else ""
    print(
        f"pytest shard {args.shard_index + 1}/{args.shard_count}: "
        f"{len(selected)} files, {selected_nodes}/{total_nodes} nodes{profile_label}",
        flush=True,
    )
    for path in selected:
        print(f"  {path}", flush=True)

    if args.list_only:
        return 0

    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    command = (sys.executable, "-m", "pytest", *selected, *pytest_args)
    return subprocess.run(command, cwd=ROOT, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(main())
