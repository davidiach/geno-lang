#!/usr/bin/env python3
"""Check runtime performance against the budgets in ``perf-budgets.toml``.

The runtime counterpart to ``scripts/check_ci_dx_ratchets.py``: measure a
frozen workload, compare it against a recorded baseline, and fail when a change
makes it meaningfully worse.

Today this enforces CLI latency (``benchmarks/cli_latency.py``). The compiled
Geno-vs-Python ratio in ``benchmarks/RESULTS.md`` is deliberately *not*
enforced here yet — see the note at the bottom of that file for what has to
change about the measurement first.

    python3 scripts/check_perf_ratchets.py            # measure and enforce
    python3 scripts/check_perf_ratchets.py --json     # machine-readable
    python3 scripts/check_perf_ratchets.py --update   # re-record baselines
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
BUDGET_PATH = ROOT / "perf-budgets.toml"

sys.path.insert(0, str(ROOT))

from benchmarks.cli_latency import (  # noqa: E402 - needs ROOT on sys.path
    SuiteResult,
    run_suite,
    select_scenarios,
)


@dataclass(frozen=True)
class CliLatencyBudgets:
    """Recorded baselines and measurement policy for CLI latency."""

    runs: int
    warmups: int
    headroom: float
    baseline_ms: dict[str, float]


@dataclass(frozen=True)
class PerfBudgets:
    """Everything ``perf-budgets.toml`` declares."""

    reference_ms: float
    max_scale: float
    cli_latency: CliLatencyBudgets


@dataclass(frozen=True)
class RatchetResult:
    """One scenario measured against its scaled budget."""

    name: str
    actual_ms: float
    budget_ms: float
    baseline_ms: float

    @property
    def passed(self) -> bool:
        return self.actual_ms <= self.budget_ms

    @property
    def delta_pct(self) -> float:
        """Change against the recorded baseline, in percent."""
        if self.baseline_ms <= 0:
            return 0.0
        return (self.actual_ms / self.baseline_ms - 1.0) * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "actual_ms": round(self.actual_ms, 2),
            "budget_ms": round(self.budget_ms, 2),
            "baseline_ms": round(self.baseline_ms, 2),
            "delta_pct": round(self.delta_pct, 1),
            "passed": self.passed,
        }


def load_budgets(path: Path = BUDGET_PATH) -> PerfBudgets:
    """Parse ``perf-budgets.toml``."""
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    calibration = data["calibration"]
    cli = data["cli_latency"]
    return PerfBudgets(
        reference_ms=float(calibration["reference_ms"]),
        max_scale=float(calibration["max_scale"]),
        cli_latency=CliLatencyBudgets(
            runs=int(cli["runs"]),
            warmups=int(cli["warmups"]),
            headroom=float(cli["headroom"]),
            baseline_ms={
                name: float(value) for name, value in cli["baseline_ms"].items()
            },
        ),
    )


def calibration_scale(
    measured_ms: float, reference_ms: float, max_scale: float
) -> float:
    """Return the factor to widen budgets by on this machine.

    Clamped to ``[1.0, max_scale]``: a machine faster than the reference gets
    free headroom rather than a spurious failure, and a pathologically slow one
    cannot widen the budgets without limit.
    """
    if reference_ms <= 0:
        return 1.0
    return max(1.0, min(measured_ms / reference_ms, max_scale))


def evaluate(result: SuiteResult, budgets: PerfBudgets) -> list[RatchetResult]:
    """Compare a suite run against the recorded baselines."""
    scale = calibration_scale(
        result.calibration_ms, budgets.reference_ms, budgets.max_scale
    )
    results: list[RatchetResult] = []
    for measurement in result.measurements:
        baseline = budgets.cli_latency.baseline_ms.get(measurement.name)
        if baseline is None:
            continue
        results.append(
            RatchetResult(
                name=measurement.name,
                actual_ms=measurement.min_ms,
                budget_ms=baseline * budgets.cli_latency.headroom * scale,
                baseline_ms=baseline,
            )
        )
    return results


def missing_baselines(result: SuiteResult, budgets: PerfBudgets) -> list[str]:
    """Return measured scenarios that have no recorded baseline."""
    return sorted(
        measurement.name
        for measurement in result.measurements
        if measurement.name not in budgets.cli_latency.baseline_ms
    )


def render_baseline_table(result: SuiteResult) -> str:
    """Render the ``[cli_latency.baseline_ms]`` body for a suite run."""
    return "\n".join(
        f"{measurement.name} = {measurement.min_ms:.1f}"
        for measurement in result.measurements
    )


def update_budget_text(text: str, result: SuiteResult) -> str:
    """Return *text* with the calibration reference and baselines refreshed.

    Rewrites only the two generated regions, so the surrounding comments — the
    rationale for the headroom, the re-baselining instructions — survive.
    """
    updated = re.sub(
        r"(?m)^reference_ms = .*$",
        f"reference_ms = {result.calibration_ms:.2f}",
        text,
        count=1,
    )
    section = re.compile(r"(?ms)^(\[cli_latency\.baseline_ms\]\n)(?:[^\[]*?)(?=^\[|\Z)")
    if not section.search(updated):
        raise ValueError("perf-budgets.toml has no [cli_latency.baseline_ms] section")
    return section.sub(
        lambda match: match.group(1) + render_baseline_table(result) + "\n",
        updated,
        count=1,
    )


def format_report(results: Sequence[RatchetResult], scale: float) -> str:
    """Render a human-readable table of ratchet results."""
    lines = [
        f"Performance ratchets (budgets scaled x{scale:.2f} for this machine):",
        f"  {'':<6}{'scenario':<16}{'actual':>10}{'budget':>10}{'vs base':>10}",
    ]
    for entry in results:
        status = "PASS" if entry.passed else "FAIL"
        lines.append(
            f"  [{status}] {entry.name:<16}{entry.actual_ms:>9.1f}ms"
            f"{entry.budget_ms:>9.1f}ms{entry.delta_pct:>+9.1f}%"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="re-record baselines in perf-budgets.toml instead of enforcing them",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help="measure only this scenario (repeatable)",
    )
    parser.add_argument(
        "--runs", type=int, default=None, help="override measured runs per scenario"
    )
    args = parser.parse_args(argv)

    budgets = load_budgets()
    scenarios = select_scenarios(args.scenarios)
    result = run_suite(
        scenarios,
        warmups=budgets.cli_latency.warmups,
        runs=args.runs if args.runs is not None else budgets.cli_latency.runs,
    )

    if args.update:
        if args.scenarios:
            print(
                "Refusing to re-baseline a subset: --update rewrites the whole "
                "table, so run it without --scenario.",
                file=sys.stderr,
            )
            return 1
        BUDGET_PATH.write_text(
            update_budget_text(BUDGET_PATH.read_text(encoding="utf-8"), result),
            encoding="utf-8",
        )
        print(f"Re-recorded baselines in {BUDGET_PATH.relative_to(ROOT)}:")
        print(render_baseline_table(result))
        return 0

    results = evaluate(result, budgets)
    scale = calibration_scale(
        result.calibration_ms, budgets.reference_ms, budgets.max_scale
    )
    unrecorded = missing_baselines(result, budgets)

    if args.json:
        print(
            json.dumps(
                {
                    "calibration_ms": round(result.calibration_ms, 2),
                    "scale": round(scale, 3),
                    "scenarios": {entry.name: entry.to_dict() for entry in results},
                    "missing_baselines": unrecorded,
                },
                indent=2,
            )
        )
    else:
        print(format_report(results, scale))

    failures = [entry for entry in results if not entry.passed]
    if unrecorded:
        print(
            "\nScenarios with no recorded baseline: " + ", ".join(unrecorded),
            file=sys.stderr,
        )
        print("Run --update to record them.", file=sys.stderr)
    if failures or unrecorded:
        if failures and not args.json:
            print(
                "\nPerformance ratchet failed. Either fix the regression, or — if "
                "the cost is intended — re-record the baseline with --update and "
                "commit it alongside the change.",
                file=sys.stderr,
            )
        return 1

    if not args.json:
        print("\nPerformance ratchet check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
