"""Reusable measurement and artifact helpers for the performance suite.

The helpers in this module intentionally know nothing about Geno's compiler or
the benchmark registry.  That keeps the statistical and artifact contracts
small enough to test without running the comparatively expensive workloads.
"""

from __future__ import annotations

import gc
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence, TextIO

SCHEMA_VERSION = 1
LIMIT_ENVIRONMENT_VARIABLES = (
    "GENO_CONSTRAIN_WALL_CLOCK_SECONDS",
    "GENO_MAX_COLLECTION_SIZE",
    "GENO_MAX_INTEGER_BITS",
    "GENO_MAX_STEPS",
    "GENO_MAX_TIMEOUT_SECONDS",
    "GENO_MAX_WALL_CLOCK_SECONDS",
    "GENO_RECURSION_LIMIT",
    "GENO_WORKER_MAX_CPU_TIME",
    "GENO_WORKER_MAX_MEMORY_BYTES",
    "GENO_WORKER_MAX_PROCESSES",
)


@dataclass(frozen=True)
class MeasurementConfig:
    """Controls one process-local paired measurement."""

    warmups: int = 3
    repetitions: int = 7
    target_sample_seconds: float = 0.01
    max_loops: int = 100_000
    bootstrap_resamples: int = 1_000
    seed: int = 1729
    gc_policy: str = "disabled"

    def validate(self) -> None:
        """Reject invalid settings before any workload is executed."""
        if self.warmups < 0:
            raise ValueError("warmups must be non-negative")
        if self.repetitions < 2:
            raise ValueError("repetitions must be at least 2")
        if self.target_sample_seconds <= 0:
            raise ValueError("target sample time must be positive")
        if self.max_loops < 1:
            raise ValueError("max loops must be at least 1")
        if self.bootstrap_resamples < 1:
            raise ValueError("bootstrap resamples must be at least 1")
        if self.gc_policy not in {"disabled", "enabled"}:
            raise ValueError("GC policy must be 'disabled' or 'enabled'")


def configure_utf8_streams() -> None:
    """Use deterministic, non-crashing UTF-8 output when streams permit it."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    if not sorted_values:
        raise ValueError("cannot calculate a percentile of no values")
    index = (len(sorted_values) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[lower]
    weight = index - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def percentile(values: Sequence[int | float], fraction: float) -> float:
    """Return a linearly interpolated percentile for a non-empty sample."""
    return _percentile(sorted(float(value) for value in values), fraction)


def bootstrap_median_ci(
    values: Sequence[float], *, resamples: int, seed: int
) -> list[float]:
    """Return a deterministic percentile-bootstrap 95% CI for the median."""
    if not values:
        raise ValueError("cannot bootstrap no values")
    if len(values) == 1:
        return [values[0], values[0]]
    rng = random.Random(seed)  # noqa: S311
    count = len(values)
    medians = sorted(
        statistics.median(values[rng.randrange(count)] for _ in range(count))
        for _ in range(resamples)
    )
    return [_percentile(medians, 0.025), _percentile(medians, 0.975)]


def summarize_samples(
    values: Sequence[float], *, bootstrap_resamples: int, seed: int
) -> dict[str, Any]:
    """Summarize normalized seconds-per-call samples."""
    if not values:
        raise ValueError("cannot summarize no samples")
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    return {
        "count": len(values),
        "median_seconds": median,
        "mad_seconds": statistics.median(deviations),
        "min_seconds": min(values),
        "max_seconds": max(values),
        "bootstrap_median_ci95_seconds": bootstrap_median_ci(
            values,
            resamples=bootstrap_resamples,
            seed=seed,
        ),
    }


def summarize_comparison(
    samples: Sequence[Mapping[str, Any]], *, bootstrap_resamples: int, seed: int
) -> dict[str, Any]:
    """Summarize paired Geno/Python samples and an interpretable effect size."""
    ratios = [
        float(sample["geno_seconds"]) / max(float(sample["python_seconds"]), 1e-15)
        for sample in samples
    ]
    deltas = [
        float(sample["geno_seconds"]) - float(sample["python_seconds"])
        for sample in samples
    ]
    median_ratio = statistics.median(ratios)
    return {
        "median_ratio": median_ratio,
        "bootstrap_median_ratio_ci95": bootstrap_median_ci(
            ratios,
            resamples=bootstrap_resamples,
            seed=seed + 2,
        ),
        "median_delta_seconds": statistics.median(deltas),
        "relative_effect_percent": (median_ratio - 1.0) * 100.0,
        "common_language_probability_geno_slower": sum(delta > 0 for delta in deltas)
        / len(deltas),
    }


@contextmanager
def applied_gc_policy(policy: str) -> Iterator[None]:
    """Apply and then restore the configured cyclic-GC state."""
    was_enabled = gc.isenabled()
    if policy == "disabled":
        gc.disable()
    else:
        gc.enable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
        else:
            gc.disable()


def _time_batch(
    function: Callable[[], Any], loops: int, timer: Callable[[], float]
) -> float:
    started = timer()
    for _ in range(loops):
        function()
    return timer() - started


def calibrate_loop_count(
    functions: Sequence[Callable[[], Any]],
    *,
    target_sample_seconds: float,
    max_loops: int,
    timer: Callable[[], float] = time.perf_counter,
) -> int:
    """Choose one shared loop count that amplifies the faster comparison arm."""
    single_call_times = [max(_time_batch(fn, 1, timer), 1e-12) for fn in functions]
    fastest = min(single_call_times)
    return min(max_loops, max(1, math.ceil(target_sample_seconds / fastest)))


def measure_paired(
    geno_function: Callable[[], Any],
    python_function: Callable[[], Any],
    *,
    config: MeasurementConfig,
    timer: Callable[[], float] = time.perf_counter,
) -> tuple[int, list[dict[str, Any]]]:
    """Measure a pair with a shared calibration and alternating A/B order."""
    config.validate()
    with applied_gc_policy(config.gc_policy):
        for _ in range(config.warmups):
            geno_function()
            python_function()
        loop_count = calibrate_loop_count(
            (geno_function, python_function),
            target_sample_seconds=config.target_sample_seconds,
            max_loops=config.max_loops,
            timer=timer,
        )
        rng = random.Random(config.seed)  # noqa: S311
        geno_first = bool(rng.getrandbits(1))
        samples: list[dict[str, Any]] = []
        for repetition in range(config.repetitions):
            order = ("geno", "python") if geno_first else ("python", "geno")
            elapsed: dict[str, float] = {}
            for implementation in order:
                function = (
                    geno_function if implementation == "geno" else python_function
                )
                elapsed[implementation] = _time_batch(function, loop_count, timer)
            samples.append(
                {
                    "repetition": repetition,
                    "order": list(order),
                    "loop_count": loop_count,
                    "geno_batch_seconds": elapsed["geno"],
                    "python_batch_seconds": elapsed["python"],
                    "geno_seconds": elapsed["geno"] / loop_count,
                    "python_seconds": elapsed["python"] / loop_count,
                }
            )
            geno_first = not geno_first
    return loop_count, samples


def summarize_case(
    samples: Sequence[Mapping[str, Any]], *, bootstrap_resamples: int, seed: int
) -> dict[str, Any]:
    """Build execution summaries from raw paired samples."""
    geno_values = [float(sample["geno_seconds"]) for sample in samples]
    python_values = [float(sample["python_seconds"]) for sample in samples]
    return {
        "geno": summarize_samples(
            geno_values,
            bootstrap_resamples=bootstrap_resamples,
            seed=seed,
        ),
        "python": summarize_samples(
            python_values,
            bootstrap_resamples=bootstrap_resamples,
            seed=seed + 1,
        ),
        "comparison": summarize_comparison(
            samples,
            bootstrap_resamples=bootstrap_resamples,
            seed=seed,
        ),
    }


def _command_output(command: Sequence[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def collect_environment_metadata(repo_root: Path) -> dict[str, Any]:
    """Collect reproducibility metadata without exposing unrelated secrets."""
    git_sha = _command_output(("git", "rev-parse", "HEAD"), repo_root)
    git_status = _command_output(("git", "status", "--porcelain"), repo_root)
    cpu = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER") or "unknown"
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git": {
            "commit": git_sha,
            "dirty": bool(git_status) if git_status is not None else None,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "platform": platform.platform(),
            "cpu": cpu,
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "node": {"version": _command_output(("node", "--version"), repo_root)},
        "limits": {
            name: os.environ[name]
            for name in LIMIT_ENVIRONMENT_VARIABLES
            if name in os.environ
        },
    }


def build_artifact(
    *,
    metadata: Mapping[str, Any],
    configuration: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    aggregate: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the stable top-level machine-readable payload."""
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": dict(metadata),
        "configuration": dict(configuration),
        "cases": list(cases),
        "aggregate": dict(aggregate),
    }


def write_json_artifact(payload: Mapping[str, Any], destination: str) -> None:
    """Write one UTF-8 JSON artifact, with '-' meaning standard output."""
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if destination == "-":
        print(rendered)
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered + "\n", encoding="utf-8")


def _jsonl_records(payload: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    yield {
        "record_type": "run",
        "schema_version": payload["schema_version"],
        "metadata": payload["metadata"],
        "configuration": payload["configuration"],
    }
    for case in payload["cases"]:
        for compile_sample in case.get("compile_samples", []):
            yield {
                "record_type": "compile_sample",
                "case": case["name"],
                **compile_sample,
            }
        for sample in case.get("samples", []):
            yield {"record_type": "execution_sample", "case": case["name"], **sample}
        yield {
            "record_type": "case_summary",
            "case": case["name"],
            "status": case["status"],
            "summary": case.get("summary"),
            "error": case.get("error"),
        }
    yield {"record_type": "aggregate", **payload["aggregate"]}


def write_jsonl_artifact(payload: Mapping[str, Any], destination: str) -> None:
    """Write metadata, raw samples, and summaries as UTF-8 JSON Lines."""
    lines = "\n".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True)
        for record in _jsonl_records(payload)
    )
    if destination == "-":
        print(lines)
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lines + "\n", encoding="utf-8")


def config_as_dict(config: MeasurementConfig) -> dict[str, Any]:
    """Return a serialization-safe measurement configuration."""
    return asdict(config)


def write_payload_to_stream(payload: Mapping[str, Any], stream: TextIO) -> None:
    """Emit compact JSON for the private fresh-process worker protocol."""
    json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")
