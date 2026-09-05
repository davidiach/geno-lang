#!/usr/bin/env python3
"""Run one deterministic, file-level shard of the Geno pytest suite."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence, TypedDict

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = "geno/tests"
_NODE_ID_RE = re.compile(r"^(geno[/\\]tests[/\\]test_[^:]+\.py)::")
_NORMALIZED_TEST_PATH_RE = re.compile(r"^geno/tests/(?:[^/]+/)*test_[^/]+\.py$")
_PLAN_SCHEMA_VERSION = 1
_TIMING_SCHEMA_VERSION = 1
_TIMING_MEASUREMENT = "sum-pytest-report-phase-durations-ms"
_TIMING_PHASES = ("setup", "call", "teardown")
_RUNNER_IMAGE_OS_ENV = "ImageOS"
_RUNNER_IMAGE_VERSION_ENV = "ImageVersion"

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


class FileTiming(TypedDict):
    """Measured pytest report durations for one selected test file."""

    path: str
    node_count: int
    call_report_count: int
    passed_count: int
    skipped_count: int
    xfailed_count: int
    xpassed_count: int
    failed_count: int
    setup_ms: int
    call_ms: int
    teardown_ms: int
    total_ms: int


class TimingProvenance(TypedDict):
    """Source and runtime identity needed to compare retained timing samples."""

    commit_sha: str
    github_run_id: str
    github_run_attempt: str
    runner_os: str
    runner_arch: str
    runner_image_os: str
    runner_image_version: str
    python_version: str
    pytest_version: str
    coverage_version: str
    environment_sha256: str


class ShardTimingManifest(TypedDict):
    """Runtime telemetry tied to one signed shard plan."""

    schema_version: int
    shard_index: int
    plan_sha256: str
    measurement: str
    provenance: TimingProvenance
    pytest_exitstatus: int
    session_elapsed_ms: int
    reported_elapsed_ms: int
    unattributed_elapsed_ms: int
    files: list[FileTiming]


def _seconds_to_ms(seconds: float) -> int:
    return max(0, round(seconds * 1_000))


def _timing_provenance() -> TimingProvenance:
    """Capture stable run and environment facts without another subprocess."""
    environment: dict[str, str] = {
        "runner_os": os.environ.get("RUNNER_OS", platform.system()),
        "runner_arch": os.environ.get("RUNNER_ARCH", platform.machine()),
        "runner_image_os": os.environ.get(_RUNNER_IMAGE_OS_ENV, "local"),
        "runner_image_version": os.environ.get(_RUNNER_IMAGE_VERSION_ENV, "local"),
        "python_version": platform.python_version(),
        "pytest_version": str(pytest.__version__),
        "coverage_version": importlib.metadata.version("coverage"),
    }
    environment_sha256 = hashlib.sha256(
        json.dumps(environment, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "commit_sha": os.environ.get("GITHUB_SHA", "local"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
        "runner_os": environment["runner_os"],
        "runner_arch": environment["runner_arch"],
        "runner_image_os": environment["runner_image_os"],
        "runner_image_version": environment["runner_image_version"],
        "python_version": environment["python_version"],
        "pytest_version": environment["pytest_version"],
        "coverage_version": environment["coverage_version"],
        "environment_sha256": environment_sha256,
    }


class _FileTimingPlugin:
    """Opt-in pytest plugin that attributes report wall time to test files."""

    def __init__(self, output: Path, shard_index: int, plan_sha256: str) -> None:
        self.output = output
        self.shard_index = shard_index
        self.plan_sha256 = plan_sha256
        self.started_ns = 0
        self.phase_seconds: dict[str, dict[str, float]] = {}
        self.node_ids: dict[str, set[str]] = {}
        self.call_report_node_ids: dict[str, set[str]] = {}
        self.node_outcomes: dict[str, dict[str, str]] = {}

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.started_ns = time.perf_counter_ns()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when not in _TIMING_PHASES:
            return
        node_parts = report.nodeid.split("::", 1)
        path = node_parts[0].replace("\\", "/")
        normalized_node_id = (
            path if len(node_parts) == 1 else f"{path}::{node_parts[1]}"
        )
        phases = self.phase_seconds.setdefault(path, dict.fromkeys(_TIMING_PHASES, 0.0))
        phases[report.when] += report.duration
        self.node_ids.setdefault(path, set()).add(normalized_node_id)
        if report.when == "call":
            self.call_report_node_ids.setdefault(path, set()).add(normalized_node_id)

        outcome = None
        if report.failed:
            outcome = "failed"
        elif report.skipped:
            outcome = "xfailed" if hasattr(report, "wasxfail") else "skipped"
        elif report.when == "call" and report.passed:
            outcome = "xpassed" if hasattr(report, "wasxfail") else "passed"
        if outcome is not None:
            self.node_outcomes.setdefault(path, {})[normalized_node_id] = outcome

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(
        self, session: pytest.Session, exitstatus: int | pytest.ExitCode
    ) -> None:
        elapsed_ms = _seconds_to_ms(
            (time.perf_counter_ns() - self.started_ns) / 1_000_000_000
        )
        files: list[FileTiming] = []
        for path in sorted(self.phase_seconds):
            phase_ms = {
                phase: _seconds_to_ms(self.phase_seconds[path][phase])
                for phase in _TIMING_PHASES
            }
            total_ms = sum(phase_ms.values())
            outcomes = Counter(self.node_outcomes.get(path, {}).values())
            files.append(
                {
                    "path": path,
                    "node_count": len(self.node_ids[path]),
                    "call_report_count": len(
                        self.call_report_node_ids.get(path, set())
                    ),
                    "passed_count": outcomes["passed"],
                    "skipped_count": outcomes["skipped"],
                    "xfailed_count": outcomes["xfailed"],
                    "xpassed_count": outcomes["xpassed"],
                    "failed_count": outcomes["failed"],
                    "setup_ms": phase_ms["setup"],
                    "call_ms": phase_ms["call"],
                    "teardown_ms": phase_ms["teardown"],
                    "total_ms": total_ms,
                }
            )
        reported_ms = sum(file["total_ms"] for file in files)
        manifest: ShardTimingManifest = {
            "schema_version": _TIMING_SCHEMA_VERSION,
            "shard_index": self.shard_index,
            "plan_sha256": self.plan_sha256,
            "measurement": _TIMING_MEASUREMENT,
            "provenance": _timing_provenance(),
            "pytest_exitstatus": int(exitstatus),
            "session_elapsed_ms": elapsed_ms,
            "reported_elapsed_ms": reported_ms,
            "unattributed_elapsed_ms": max(0, elapsed_ms - reported_ms),
            "files": files,
        }
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_name(f".{self.output.name}.tmp")
        temporary.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.output)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register internal options when this module is loaded as a pytest plugin."""
    group = parser.getgroup("geno-shard-timing")
    group.addoption("--geno-file-timings-json", dest="geno_file_timings_json")
    group.addoption("--geno-shard-index", dest="geno_shard_index", type=int)
    group.addoption("--geno-plan-sha256", dest="geno_plan_sha256")


def pytest_configure(config: pytest.Config) -> None:
    """Enable timing hooks only for an explicitly requested shard run."""
    output = config.getoption("geno_file_timings_json")
    if output is None:
        return
    shard_index = config.getoption("geno_shard_index")
    plan_sha256 = config.getoption("geno_plan_sha256")
    if shard_index is None or plan_sha256 is None:
        raise pytest.UsageError(
            "Geno file timing requires a shard index and plan fingerprint"
        )
    config.pluginmanager.register(
        _FileTimingPlugin(Path(output), shard_index, plan_sha256),
        "geno-file-timing",
    )


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


def _nonnegative_int(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _normalized_test_path(value: object, label: str) -> str:
    if not isinstance(value, str) or _NORMALIZED_TEST_PATH_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a normalized test-file path")
    return value


def _parse_timing_provenance(value: object, source: Path) -> TimingProvenance:
    if not isinstance(value, dict):
        raise ValueError(f"{source}: timing provenance must be a JSON object")
    identity_keys = {"commit_sha", "github_run_id", "github_run_attempt"}
    environment_keys = {
        "runner_os",
        "runner_arch",
        "runner_image_os",
        "runner_image_version",
        "python_version",
        "pytest_version",
        "coverage_version",
    }
    expected_keys = identity_keys | environment_keys | {"environment_sha256"}
    if set(value) != expected_keys:
        raise ValueError(
            f"{source}: timing provenance has unexpected or missing fields"
        )
    strings = {
        key: _nonempty_string(value[key], f"timing provenance {key}")
        for key in identity_keys | environment_keys
    }
    environment = {key: strings[key] for key in environment_keys}
    expected_sha256 = hashlib.sha256(
        json.dumps(environment, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    environment_sha256 = _nonempty_string(
        value["environment_sha256"], "timing environment fingerprint"
    )
    if environment_sha256 != expected_sha256:
        raise ValueError(f"{source}: timing environment fingerprint is invalid")
    return {
        "commit_sha": strings["commit_sha"],
        "github_run_id": strings["github_run_id"],
        "github_run_attempt": strings["github_run_attempt"],
        "runner_os": strings["runner_os"],
        "runner_arch": strings["runner_arch"],
        "runner_image_os": strings["runner_image_os"],
        "runner_image_version": strings["runner_image_version"],
        "python_version": strings["python_version"],
        "pytest_version": strings["pytest_version"],
        "coverage_version": strings["coverage_version"],
        "environment_sha256": environment_sha256,
    }


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


def _load_shard_timing_manifest(path: Path, plan: ShardPlan) -> ShardTimingManifest:
    try:
        raw_manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: could not read shard timing JSON") from exc
    if not isinstance(raw_manifest, dict):
        raise ValueError(f"{path}: shard timing manifest must be a JSON object")
    expected_keys = {
        "schema_version",
        "shard_index",
        "plan_sha256",
        "measurement",
        "provenance",
        "pytest_exitstatus",
        "session_elapsed_ms",
        "reported_elapsed_ms",
        "unattributed_elapsed_ms",
        "files",
    }
    if set(raw_manifest) != expected_keys:
        raise ValueError(f"{path}: timing manifest has unexpected or missing fields")

    schema_version = _positive_int(raw_manifest["schema_version"], "schema version")
    if schema_version != _TIMING_SCHEMA_VERSION:
        raise ValueError(f"{path}: unsupported shard timing schema version")
    shard_index = raw_manifest["shard_index"]
    if type(shard_index) is not int or not 0 <= shard_index < plan["shard_count"]:
        raise ValueError(f"{path}: timing shard index is outside the plan range")
    if path.name != f"shard-timing.{shard_index}.json":
        raise ValueError(f"{path}: timing filename does not match shard index")

    plan_sha256 = raw_manifest["plan_sha256"]
    if (
        not isinstance(plan_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", plan_sha256) is None
    ):
        raise ValueError(f"{path}: timing plan fingerprint must be lowercase SHA-256")
    if plan_sha256 != shard_plan_sha256(plan):
        raise ValueError(f"{path}: timing plan fingerprint does not match the plan")
    if raw_manifest["measurement"] != _TIMING_MEASUREMENT:
        raise ValueError(f"{path}: unsupported shard timing measurement")
    provenance = _parse_timing_provenance(raw_manifest["provenance"], path)

    pytest_exitstatus = _nonnegative_int(
        raw_manifest["pytest_exitstatus"], "pytest exit status"
    )
    if pytest_exitstatus != 0:
        raise ValueError(f"{path}: timing manifest is from a failed pytest run")
    session_elapsed_ms = _nonnegative_int(
        raw_manifest["session_elapsed_ms"], "session elapsed milliseconds"
    )
    reported_elapsed_ms = _nonnegative_int(
        raw_manifest["reported_elapsed_ms"], "reported elapsed milliseconds"
    )
    unattributed_elapsed_ms = _nonnegative_int(
        raw_manifest["unattributed_elapsed_ms"],
        "unattributed elapsed milliseconds",
    )

    raw_files = raw_manifest["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError(f"{path}: timing files must be a nonempty list")
    files: list[FileTiming] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict) or set(raw_file) != {
            "path",
            "node_count",
            "call_report_count",
            "passed_count",
            "skipped_count",
            "xfailed_count",
            "xpassed_count",
            "failed_count",
            "setup_ms",
            "call_ms",
            "teardown_ms",
            "total_ms",
        }:
            raise ValueError(f"{path}: timing file has unexpected or missing fields")
        test_path = _normalized_test_path(raw_file["path"], "timing file path")
        node_count = _positive_int(
            raw_file["node_count"], f"timing node count for {test_path}"
        )
        call_report_count = _nonnegative_int(
            raw_file["call_report_count"],
            f"timing call report count for {test_path}",
        )
        outcome_counts = {
            outcome: _nonnegative_int(
                raw_file[f"{outcome}_count"],
                f"timing {outcome} count for {test_path}",
            )
            for outcome in ("passed", "skipped", "xfailed", "xpassed", "failed")
        }
        if sum(outcome_counts.values()) != node_count:
            raise ValueError(f"{path}: timing outcomes do not sum for {test_path}")
        if call_report_count > node_count:
            raise ValueError(
                f"{path}: timing call report count exceeds nodes for {test_path}"
            )
        if call_report_count < outcome_counts["passed"] + outcome_counts["xpassed"]:
            raise ValueError(
                f"{path}: timing call report count misses completed calls for "
                f"{test_path}"
            )
        if outcome_counts["failed"] != 0:
            raise ValueError(f"{path}: successful timing contains failed tests")
        setup_ms = _nonnegative_int(
            raw_file["setup_ms"], f"setup milliseconds for {test_path}"
        )
        call_ms = _nonnegative_int(
            raw_file["call_ms"], f"call milliseconds for {test_path}"
        )
        teardown_ms = _nonnegative_int(
            raw_file["teardown_ms"], f"teardown milliseconds for {test_path}"
        )
        total_ms = _nonnegative_int(
            raw_file["total_ms"], f"total milliseconds for {test_path}"
        )
        if total_ms != setup_ms + call_ms + teardown_ms:
            raise ValueError(f"{path}: timing phases do not sum for {test_path}")
        files.append(
            {
                "path": test_path,
                "node_count": node_count,
                "call_report_count": call_report_count,
                "passed_count": outcome_counts["passed"],
                "skipped_count": outcome_counts["skipped"],
                "xfailed_count": outcome_counts["xfailed"],
                "xpassed_count": outcome_counts["xpassed"],
                "failed_count": outcome_counts["failed"],
                "setup_ms": setup_ms,
                "call_ms": call_ms,
                "teardown_ms": teardown_ms,
                "total_ms": total_ms,
            }
        )

    file_paths = [file["path"] for file in files]
    if file_paths != sorted(file_paths) or len(set(file_paths)) != len(file_paths):
        raise ValueError(f"{path}: timing file paths must be sorted and unique")
    if file_paths != plan["shards"][shard_index]:
        raise ValueError(f"{path}: timing files do not match the selected shard")
    for file in files:
        expected_count = plan["collected_test_counts"][file["path"]]
        if file["node_count"] != expected_count:
            raise ValueError(
                f"{path}: timing node count does not match the plan for {file['path']}"
            )
    if reported_elapsed_ms != sum(file["total_ms"] for file in files):
        raise ValueError(f"{path}: reported timing total does not match file timings")
    rounding_tolerance_ms = (len(files) * len(_TIMING_PHASES) + 2) // 2
    if reported_elapsed_ms > session_elapsed_ms + rounding_tolerance_ms:
        raise ValueError(f"{path}: reported timing exceeds session timing")
    if unattributed_elapsed_ms != max(0, session_elapsed_ms - reported_elapsed_ms):
        raise ValueError(f"{path}: unattributed timing does not match session timing")

    return {
        "schema_version": schema_version,
        "shard_index": shard_index,
        "plan_sha256": plan_sha256,
        "measurement": _TIMING_MEASUREMENT,
        "provenance": provenance,
        "pytest_exitstatus": pytest_exitstatus,
        "session_elapsed_ms": session_elapsed_ms,
        "reported_elapsed_ms": reported_elapsed_ms,
        "unattributed_elapsed_ms": unattributed_elapsed_ms,
        "files": files,
    }


def validate_shard_timing_manifests(
    paths: Sequence[Path], plan: ShardPlan, *, allow_mixed_attempts: bool = False
) -> list[ShardTimingManifest]:
    """Validate timing telemetry against the exact agreed shard plan."""
    if len(paths) != plan["shard_count"]:
        raise ValueError(
            f"expected {plan['shard_count']} shard timing manifests, found {len(paths)}"
        )
    manifests = [_load_shard_timing_manifest(path, plan) for path in paths]
    indices = [manifest["shard_index"] for manifest in manifests]
    if sorted(indices) != list(range(plan["shard_count"])):
        raise ValueError("shard timing manifest indices must be complete and unique")
    run_identities = {
        (
            manifest["provenance"]["commit_sha"],
            manifest["provenance"]["github_run_id"],
            None
            if allow_mixed_attempts
            else manifest["provenance"]["github_run_attempt"],
        )
        for manifest in manifests
    }
    if len(run_identities) != 1:
        raise ValueError("shard timing manifests come from different CI runs")
    environment_fingerprints = {
        manifest["provenance"]["environment_sha256"] for manifest in manifests
    }
    if len(environment_fingerprints) != 1:
        raise ValueError(
            "shard timing manifests come from different runtime environments"
        )
    return sorted(manifests, key=lambda manifest: manifest["shard_index"])


def print_shard_timing_summary(manifests: Sequence[ShardTimingManifest]) -> None:
    """Print stable hosted evidence for the next balance-profile update."""
    print("validated pytest shard timings:", flush=True)
    attempts = {manifest["provenance"]["github_run_attempt"] for manifest in manifests}
    if len(attempts) > 1:
        print(
            "  mixed CI attempts: valid for coverage, not a comparable timing sample",
            flush=True,
        )
    for manifest in manifests:
        executed = sum(file["call_report_count"] for file in manifest["files"])
        skipped = sum(
            file["skipped_count"] + file["xfailed_count"] for file in manifest["files"]
        )
        print(
            f"  shard {manifest['shard_index']}: "
            f"{manifest['reported_elapsed_ms']} ms reported, "
            f"{manifest['session_elapsed_ms']} ms session, "
            f"{executed} call reports, {skipped} skipped/xfail, "
            f"attempt={manifest['provenance']['github_run_attempt']}, "
            f"env={manifest['provenance']['environment_sha256'][:12]}",
            flush=True,
        )
    slowest = sorted(
        (file for manifest in manifests for file in manifest["files"]),
        key=lambda file: (-file["total_ms"], file["path"]),
    )[:15]
    print("  slowest files:", flush=True)
    for file in slowest:
        print(f"    {file['total_ms']:>7} ms  {file['path']}", flush=True)


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
    parser.add_argument(
        "--timing-manifests",
        type=Path,
        nargs="+",
        metavar="PATH",
        help="Validate timing manifests against the agreed shard plan.",
    )
    parser.add_argument(
        "--allow-mixed-attempts",
        action="store_true",
        help=(
            "Allow successful shards from retries of the same CI run for coverage. "
            "Mixed attempts are not a comparable timing sample."
        ),
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
        "--timing-manifest",
        type=Path,
        help=(
            "Write per-file pytest timings as shard-timing.<shard-index>.json. "
            "Requires --plan-manifest."
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
    if args.allow_mixed_attempts and (
        args.validate_plan_manifests is None or args.timing_manifests is None
    ):
        raise SystemExit("--allow-mixed-attempts requires timing validation mode")
    if args.validate_plan_manifests is not None:
        if (
            args.plan_manifest is not None
            or args.timing_manifest is not None
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
        timings = None
        if args.timing_manifests is not None:
            try:
                timings = validate_shard_timing_manifests(
                    args.timing_manifests,
                    plan,
                    allow_mixed_attempts=args.allow_mixed_attempts,
                )
            except ValueError as exc:
                print(f"pytest shard timing validation failed: {exc}", file=sys.stderr)
                return 1
        print(
            f"validated {plan['shard_count']} matching pytest shard plans: "
            f"{len(plan['collected_test_counts'])} files, "
            f"{plan['total_nodes']} nodes",
            flush=True,
        )
        if timings is not None:
            print_shard_timing_summary(timings)
        return 0

    assert args.shard_index is not None
    if args.timing_manifests is not None:
        raise SystemExit("--timing-manifests requires plan validation mode")
    if not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("--shard-index must be in [0, --shard-count)")
    if (
        args.plan_manifest is not None
        and args.plan_manifest.name != f"shard-plan.{args.shard_index}.json"
    ):
        raise SystemExit(
            f"--plan-manifest filename must be shard-plan.{args.shard_index}.json"
        )
    if args.timing_manifest is not None:
        if args.plan_manifest is None:
            raise SystemExit("--timing-manifest requires --plan-manifest")
        if args.list_only:
            raise SystemExit("--timing-manifest cannot be used with --list-only")
        if args.timing_manifest.name != f"shard-timing.{args.shard_index}.json":
            raise SystemExit(
                f"--timing-manifest filename must be shard-timing."
                f"{args.shard_index}.json"
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
    plan_manifest = None
    if args.plan_manifest is not None:
        plan_manifest = write_shard_plan_manifest(
            args.plan_manifest,
            test_counts=counts,
            balance_weights=effective_weights,
            shards=shards,
            shard_index=args.shard_index,
            balance_profile=args.balance_profile,
        )
        print(
            f"wrote pytest shard plan {args.plan_manifest}: "
            f"{plan_manifest['plan_sha256']}",
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
    plugin_args: list[str] = []
    timing_path = None
    if args.timing_manifest is not None:
        assert plan_manifest is not None
        timing_path = args.timing_manifest.resolve()
        timing_path.unlink(missing_ok=True)
        plugin_args = [
            "-p",
            "scripts.pytest_shard",
            "--geno-file-timings-json",
            str(timing_path),
            "--geno-shard-index",
            str(args.shard_index),
            "--geno-plan-sha256",
            plan_manifest["plan_sha256"],
        ]
    command = (
        sys.executable,
        "-m",
        "pytest",
        *plugin_args,
        *selected,
        *pytest_args,
    )
    returncode = subprocess.run(  # noqa: S603
        command, cwd=ROOT, check=False
    ).returncode
    if returncode != 0:
        if timing_path is not None:
            timing_path.unlink(missing_ok=True)
        return returncode
    if timing_path is None:
        return 0
    try:
        assert plan_manifest is not None
        timing = _load_shard_timing_manifest(timing_path, plan_manifest["plan"])
    except ValueError as exc:
        timing_path.unlink(missing_ok=True)
        print(f"pytest shard timing validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"wrote pytest shard timing {timing_path}: "
        f"{timing['reported_elapsed_ms']} ms reported",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
