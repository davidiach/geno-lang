#!/usr/bin/env python3
"""Run one deterministic, file-level shard of the Geno pytest suite."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = "geno/tests"
_NODE_ID_RE = re.compile(r"^(geno[/\\]tests[/\\]test_[^:]+\.py)::")


def parse_collected_test_counts(output: str) -> dict[str, int]:
    """Return normalized test-file node counts from pytest collection output."""
    counts: Counter[str] = Counter()
    for line in output.splitlines():
        match = _NODE_ID_RE.match(line.strip())
        if match is not None:
            counts[match.group(1).replace("\\", "/")] += 1
    return dict(counts)


def partition_test_files(
    test_counts: dict[str, int], shard_count: int
) -> list[list[str]]:
    """Greedily balance whole test files by collected node count."""
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")
    if not test_counts:
        raise ValueError("no collected tests were found")
    if shard_count > len(test_counts):
        raise ValueError("shard_count cannot exceed the number of test files")
    if any(count < 1 for count in test_counts.values()):
        raise ValueError("every test file must contain at least one collected test")

    shards: list[list[str]] = [[] for _ in range(shard_count)]
    weights = [0] * shard_count
    ordered_files = sorted(test_counts, key=lambda path: (-test_counts[path], path))
    for path in ordered_files:
        shard_index = min(range(shard_count), key=lambda index: (weights[index], index))
        shards[shard_index].append(path)
        weights[shard_index] += test_counts[path]

    for shard in shards:
        shard.sort()
    return shards


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
    shards = partition_test_files(counts, args.shard_count)
    selected = shards[args.shard_index]
    selected_nodes = sum(counts[path] for path in selected)
    total_nodes = sum(counts.values())
    print(
        f"pytest shard {args.shard_index + 1}/{args.shard_count}: "
        f"{len(selected)} files, {selected_nodes}/{total_nodes} nodes",
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
