#!/usr/bin/env python3
"""Run one deterministic, file-level shard of the Geno pytest suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence, TypedDict

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = "geno/tests"
_NODE_ID_RE = re.compile(r"^(geno[/\\]tests[/\\]test_[^:]+\.py)::")
_NORMALIZED_TEST_PATH_RE = re.compile(r"^geno/tests/(?:[^/]+/)*test_[^/]+\.py$")
_PLAN_SCHEMA_VERSION = 1

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


class ShardPlan(TypedDict):
    """Deterministic data shared by every worker executing one shard plan."""

    schema_version: int
    shard_count: int
    balance_profile: str | None
    total_nodes: int
    collected_test_counts: dict[str, int]
    balance_weights: dict[str, int]
    shards: list[list[str]]


class ShardPlanManifest(TypedDict):
    """One worker's wrapper around a common deterministic shard plan."""

    shard_index: int
    selected_shard: list[str]
    plan_sha256: str
    plan: ShardPlan


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


def shard_plan_sha256(plan: ShardPlan) -> str:
    """Return the SHA-256 of a plan's canonical compact JSON encoding."""
    canonical = json.dumps(
        plan,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_shard_plan_manifest(
    *,
    test_counts: Mapping[str, int],
    balance_weights: Mapping[str, int],
    shards: Sequence[Sequence[str]],
    shard_index: int,
    balance_profile: str | None,
) -> ShardPlanManifest:
    """Build one deterministic manifest from the exact in-memory execution plan."""
    plan: ShardPlan = {
        "schema_version": _PLAN_SCHEMA_VERSION,
        "shard_count": len(shards),
        "balance_profile": balance_profile,
        "total_nodes": sum(test_counts.values()),
        "collected_test_counts": dict(sorted(test_counts.items())),
        "balance_weights": dict(sorted(balance_weights.items())),
        "shards": [list(shard) for shard in shards],
    }
    return {
        "shard_index": shard_index,
        "selected_shard": list(shards[shard_index]),
        "plan_sha256": shard_plan_sha256(plan),
        "plan": plan,
    }


def write_shard_plan_manifest(
    path: Path,
    *,
    test_counts: Mapping[str, int],
    balance_weights: Mapping[str, int],
    shards: Sequence[Sequence[str]],
    shard_index: int,
    balance_profile: str | None,
) -> ShardPlanManifest:
    """Write one shard plan manifest as stable, human-readable JSON."""
    manifest = build_shard_plan_manifest(
        test_counts=test_counts,
        balance_weights=balance_weights,
        shards=shards,
        shard_index=shard_index,
        balance_profile=balance_profile,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _normalized_test_path(value: object, label: str) -> str:
    if not isinstance(value, str) or _NORMALIZED_TEST_PATH_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a normalized test-file path")
    return value


def _parse_shard_plan(value: object, source: Path) -> ShardPlan:
    if not isinstance(value, dict):
        raise ValueError(f"{source}: plan must be a JSON object")
    expected_keys = {
        "schema_version",
        "shard_count",
        "balance_profile",
        "total_nodes",
        "collected_test_counts",
        "balance_weights",
        "shards",
    }
    if set(value) != expected_keys:
        raise ValueError(f"{source}: plan has unexpected or missing fields")

    schema_version = _positive_int(value["schema_version"], "schema version")
    if schema_version != _PLAN_SCHEMA_VERSION:
        raise ValueError(f"{source}: unsupported shard plan schema version")
    shard_count = _positive_int(value["shard_count"], "shard count")
    total_nodes = _positive_int(value["total_nodes"], "total nodes")
    balance_profile = value["balance_profile"]
    if balance_profile is not None and (
        not isinstance(balance_profile, str) or not balance_profile
    ):
        raise ValueError(f"{source}: balance profile must be a string or null")

    raw_counts = value["collected_test_counts"]
    if not isinstance(raw_counts, dict) or not raw_counts:
        raise ValueError(f"{source}: collected test counts must be a nonempty object")
    test_counts: dict[str, int] = {}
    for raw_path, raw_count in raw_counts.items():
        path = _normalized_test_path(raw_path, "collected test path")
        test_counts[path] = _positive_int(raw_count, f"node count for {path}")
    if total_nodes != sum(test_counts.values()):
        raise ValueError(f"{source}: total nodes do not match collected test counts")

    raw_weights = value["balance_weights"]
    if not isinstance(raw_weights, dict):
        raise ValueError(f"{source}: balance weights must be an object")
    balance_weights: dict[str, int] = {}
    for raw_path, raw_weight in raw_weights.items():
        path = _normalized_test_path(raw_path, "balance weight path")
        balance_weights[path] = _positive_int(raw_weight, f"balance weight for {path}")
    if set(balance_weights) != set(test_counts):
        raise ValueError(f"{source}: balance weights must match collected test files")

    raw_shards = value["shards"]
    if not isinstance(raw_shards, list) or len(raw_shards) != shard_count:
        raise ValueError(f"{source}: shard assignments do not match shard count")
    shards: list[list[str]] = []
    for index, raw_shard in enumerate(raw_shards):
        if not isinstance(raw_shard, list) or not raw_shard:
            raise ValueError(f"{source}: shard {index} must be a nonempty list")
        shard = [
            _normalized_test_path(path, f"shard {index} path") for path in raw_shard
        ]
        if shard != sorted(shard):
            raise ValueError(f"{source}: shard {index} paths must be sorted")
        shards.append(shard)

    assigned_counts = Counter(path for shard in shards for path in shard)
    if assigned_counts != Counter(test_counts.keys()):
        raise ValueError(
            f"{source}: shard plan must assign every collected test file exactly once"
        )

    return {
        "schema_version": schema_version,
        "shard_count": shard_count,
        "balance_profile": balance_profile,
        "total_nodes": total_nodes,
        "collected_test_counts": dict(sorted(test_counts.items())),
        "balance_weights": dict(sorted(balance_weights.items())),
        "shards": shards,
    }


def _load_shard_plan_manifest(path: Path) -> ShardPlanManifest:
    try:
        raw_manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: could not read shard plan JSON") from exc
    if not isinstance(raw_manifest, dict):
        raise ValueError(f"{path}: shard plan manifest must be a JSON object")
    if set(raw_manifest) != {
        "shard_index",
        "selected_shard",
        "plan_sha256",
        "plan",
    }:
        raise ValueError(f"{path}: manifest has unexpected or missing fields")

    plan = _parse_shard_plan(raw_manifest["plan"], path)
    shard_index = raw_manifest["shard_index"]
    if type(shard_index) is not int or not 0 <= shard_index < plan["shard_count"]:
        raise ValueError(f"{path}: shard index is outside the plan range")
    if path.name != f"shard-plan.{shard_index}.json":
        raise ValueError(f"{path}: filename does not match shard index")

    raw_selected = raw_manifest["selected_shard"]
    if not isinstance(raw_selected, list):
        raise ValueError(f"{path}: selected shard must be a list")
    selected_shard = [
        _normalized_test_path(item, "selected shard path") for item in raw_selected
    ]
    if selected_shard != plan["shards"][shard_index]:
        raise ValueError(f"{path}: selected shard does not match the full plan")

    plan_sha256 = raw_manifest["plan_sha256"]
    if (
        not isinstance(plan_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", plan_sha256) is None
    ):
        raise ValueError(f"{path}: plan fingerprint must be lowercase SHA-256")
    if plan_sha256 != shard_plan_sha256(plan):
        raise ValueError(f"{path}: plan fingerprint does not match its contents")

    return {
        "shard_index": shard_index,
        "selected_shard": selected_shard,
        "plan_sha256": plan_sha256,
        "plan": plan,
    }


def validate_shard_plan_manifests(paths: Sequence[Path]) -> ShardPlan:
    """Validate that independently generated manifests describe one exact plan."""
    if not paths:
        raise ValueError("no shard plan manifests were provided")
    manifests = [_load_shard_plan_manifest(path) for path in paths]
    reference = manifests[0]
    shard_count = reference["plan"]["shard_count"]
    if len(manifests) != shard_count:
        raise ValueError(
            f"expected {shard_count} shard plan manifests, found {len(manifests)}"
        )

    indices = [manifest["shard_index"] for manifest in manifests]
    if sorted(indices) != list(range(shard_count)):
        raise ValueError("shard plan manifest indices must be complete and unique")
    for manifest in manifests[1:]:
        if (
            manifest["plan_sha256"] != reference["plan_sha256"]
            or manifest["plan"] != reference["plan"]
        ):
            raise ValueError("independently collected pytest shard plans disagree")
    return reference["plan"]


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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--shard-index", type=int)
    mode.add_argument(
        "--validate-plan-manifests",
        type=Path,
        nargs="+",
        metavar="PATH",
        help="Validate independently generated shard plan manifests.",
    )
    parser.add_argument("--shard-count", type=int, default=2)
    parser.add_argument(
        "--balance-profile",
        choices=tuple(sorted(_BALANCE_PROFILES)),
        help="Apply a checked-in runtime balance profile after collection.",
    )
    parser.add_argument(
        "--plan-manifest",
        type=Path,
        help=(
            "Write the complete deterministic execution plan as "
            "shard-plan.<shard-index>.json."
        ),
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
    if args.validate_plan_manifests is not None:
        if (
            args.plan_manifest is not None
            or args.balance_profile is not None
            or args.list_only
            or args.pytest_args
        ):
            raise SystemExit("plan validation mode cannot use shard execution options")
        try:
            plan = validate_shard_plan_manifests(args.validate_plan_manifests)
        except ValueError as exc:
            print(f"pytest shard plan validation failed: {exc}", file=sys.stderr)
            return 1
        print(
            f"validated {plan['shard_count']} matching pytest shard plans: "
            f"{len(plan['collected_test_counts'])} files, "
            f"{plan['total_nodes']} nodes",
            flush=True,
        )
        return 0

    assert args.shard_index is not None
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("--shard-index must be in [0, --shard-count)")
    if (
        args.plan_manifest is not None
        and args.plan_manifest.name != f"shard-plan.{args.shard_index}.json"
    ):
        raise SystemExit(
            f"--plan-manifest filename must be shard-plan.{args.shard_index}.json"
        )

    counts = collect_test_counts()
    balance_weights = None
    if args.balance_profile is not None:
        balance_weights = balance_weights_for_profile(
            counts, args.balance_profile, args.shard_count
        )
    shards = partition_test_files(
        counts, args.shard_count, balance_weights=balance_weights
    )
    effective_weights = counts if balance_weights is None else balance_weights
    selected = shards[args.shard_index]
    if args.plan_manifest is not None:
        manifest = write_shard_plan_manifest(
            args.plan_manifest,
            test_counts=counts,
            balance_weights=effective_weights,
            shards=shards,
            shard_index=args.shard_index,
            balance_profile=args.balance_profile,
        )
        print(
            f"wrote pytest shard plan {args.plan_manifest}: {manifest['plan_sha256']}",
            flush=True,
        )
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
