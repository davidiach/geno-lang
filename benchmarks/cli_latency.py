#!/usr/bin/env python3
"""CLI latency benchmark for the ``geno`` command line.

Measures wall-clock time for a frozen set of CLI invocations against frozen
fixture programs. The numbers feed ``scripts/check_perf_ratchets.py``, which
enforces them against the budgets in ``perf-budgets.toml``.

Two decisions make the measurement usable as a CI gate rather than a noisy
graph:

*Minimum, not mean.* Scheduler noise only ever adds time, so the minimum of N
runs is the most stable estimator of the real cost. The median is reported
alongside it for context but is not what the ratchet checks.

*Calibration.* Hosted runners vary by more than the regressions worth
catching. Every suite run also times a fixed CPU-bound workload, so budgets
recorded on one machine can be scaled to another instead of being retuned per
runner.

Run it directly for a one-off measurement::

    python benchmarks/cli_latency.py
    python benchmarks/cli_latency.py --json --runs 9
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

DEFAULT_WARMUPS = 2
DEFAULT_RUNS = 5

# Fixed CPU-bound workload used to normalize budgets across machines. Bytecode
# compilation is the right proxy: the cost this suite is mostly measuring is
# module import, which is dominated by compiling and executing module bodies.
_CALIBRATION_FUNCTIONS = 300
_CALIBRATION_REPEATS = 5


@dataclass(frozen=True)
class Scenario:
    """One frozen CLI invocation to measure."""

    name: str
    args: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class Measurement:
    """Timing result for a single scenario."""

    name: str
    min_ms: float
    median_ms: float
    runs: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_ms": round(self.min_ms, 2),
            "median_ms": round(self.median_ms, 2),
            "runs": self.runs,
        }


@dataclass(frozen=True)
class SuiteResult:
    """A full suite run: calibration plus one measurement per scenario."""

    calibration_ms: float
    measurements: tuple[Measurement, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "calibration_ms": round(self.calibration_ms, 2),
            "scenarios": {m.name: m.to_dict() for m in self.measurements},
        }

    def by_name(self) -> dict[str, Measurement]:
        return {m.name: m for m in self.measurements}


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="version",
        args=("--version",),
        description="startup floor: argument dispatch with no frontend work",
    ),
    Scenario(
        name="check-medium",
        args=("check", "{medium}"),
        description="frontend: resolve, parse and typecheck a 150-line program",
    ),
    Scenario(
        name="test-medium",
        args=("test", "{medium}"),
        description="frontend plus interpreter: run 14 example clauses",
    ),
    Scenario(
        name="run-hello",
        args=("run", "{hello}"),
        description="process-isolated run of a minimal program: pure overhead",
    ),
    Scenario(
        name="run-medium",
        args=("run", "{medium}"),
        description="process-isolated run of a realistic program",
    ),
    Scenario(
        name="compile-medium",
        args=("compile", "{medium}"),
        description="codegen: compile to Python on stdout",
    ),
)

_FIXTURE_PATHS = {
    "hello": FIXTURES / "hello.geno",
    "medium": FIXTURES / "medium.geno",
}


def scenario_command(scenario: Scenario, python: str = sys.executable) -> list[str]:
    """Return the argv for *scenario* with fixture placeholders resolved."""
    args = [
        arg.format(**{name: str(path) for name, path in _FIXTURE_PATHS.items()})
        for arg in scenario.args
    ]
    return [python, "-m", "geno", *args]


def _calibration_source() -> str:
    """Build the deterministic Python source used for calibration."""
    return "\n".join(
        f"def _calibration_fn_{index}(value):\n"
        f"    total = value + {index}\n"
        f"    for item in range(3):\n"
        f"        total = total + item * {index}\n"
        f"    return total\n"
        for index in range(_CALIBRATION_FUNCTIONS)
    )


def calibrate(repeats: int = _CALIBRATION_REPEATS) -> float:
    """Return the machine's speed on a fixed CPU-bound workload, in ms.

    Higher means slower. Budgets recorded on one machine are scaled by the
    ratio of this number to the reference recorded in ``perf-budgets.toml``.
    """
    source = _calibration_source()
    samples: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        compile(source, "<calibration>", "exec")
        samples.append((time.perf_counter() - start) * 1000.0)
    return min(samples)


def measure(
    scenario: Scenario,
    *,
    warmups: int = DEFAULT_WARMUPS,
    runs: int = DEFAULT_RUNS,
    python: str = sys.executable,
) -> Measurement:
    """Time one scenario, failing loudly if the command does not succeed."""
    command = scenario_command(scenario, python)
    samples: list[float] = []
    for index in range(warmups + runs):
        start = time.perf_counter()
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        elapsed = (time.perf_counter() - start) * 1000.0
        if completed.returncode != 0:
            raise RuntimeError(
                f"scenario {scenario.name!r} failed with exit code "
                f"{completed.returncode}: {completed.stderr.strip()[:400]}"
            )
        if index >= warmups:
            samples.append(elapsed)
    return Measurement(
        name=scenario.name,
        min_ms=min(samples),
        median_ms=statistics.median(samples),
        runs=runs,
    )


def run_suite(
    scenarios: Sequence[Scenario] = SCENARIOS,
    *,
    warmups: int = DEFAULT_WARMUPS,
    runs: int = DEFAULT_RUNS,
    python: str = sys.executable,
) -> SuiteResult:
    """Measure every scenario and record the machine calibration.

    Calibration runs both before and after the scenarios and keeps the lower
    reading. Measured after the suite alone it picks up the contention the
    suite itself created, which would silently widen every budget.
    """
    before = calibrate()
    measurements = tuple(
        measure(scenario, warmups=warmups, runs=runs, python=python)
        for scenario in scenarios
    )
    after = calibrate()
    return SuiteResult(calibration_ms=min(before, after), measurements=measurements)


def select_scenarios(names: Sequence[str] | None) -> tuple[Scenario, ...]:
    """Return the scenarios matching *names*, or all of them when empty."""
    if not names:
        return SCENARIOS
    known = {scenario.name: scenario for scenario in SCENARIOS}
    unknown = sorted(set(names) - set(known))
    if unknown:
        raise SystemExit(
            f"unknown scenario(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(known))}"
        )
    return tuple(known[name] for name in names)


def format_report(result: SuiteResult) -> str:
    """Render a human-readable table of the suite result."""
    lines = [
        f"CLI latency (calibration {result.calibration_ms:.1f} ms, "
        f"min of {result.measurements[0].runs if result.measurements else 0} runs)",
        f"  {'scenario':<16} {'min ms':>9} {'median ms':>11}",
    ]
    for measurement in result.measurements:
        lines.append(
            f"  {measurement.name:<16} {measurement.min_ms:>9.1f} "
            f"{measurement.median_ms:>11.1f}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--runs", type=int, default=DEFAULT_RUNS, help="measured runs per scenario"
    )
    parser.add_argument(
        "--warmups",
        type=int,
        default=DEFAULT_WARMUPS,
        help="discarded warmup runs per scenario",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help="measure only this scenario (repeatable)",
    )
    args = parser.parse_args(argv)

    result = run_suite(
        select_scenarios(args.scenarios), warmups=args.warmups, runs=args.runs
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
