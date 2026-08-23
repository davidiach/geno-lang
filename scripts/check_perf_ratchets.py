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
import platform
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
    Measurement,
    Scenario,
    SuiteResult,
    run_suite,
    select_scenarios,
)


@dataclass(frozen=True)
class CliLatencyBudgets:
    """Recorded baselines and measurement policy for CLI latency."""

    runs: int
    update_runs: int
    update_passes: int
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
            update_runs=int(cli["update_runs"]),
            update_passes=int(cli["update_passes"]),
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


def merge_suites(passes: Sequence[SuiteResult]) -> SuiteResult:
    """Combine repeated suite passes by keeping the lowest reading of each.

    Within a single pass, sampling more runs stops helping quickly. What does
    not settle is drift *between* passes minutes apart, as the machine's load
    changes — and that drift lands directly in a recorded baseline. Since every
    reading here is a minimum and noise only ever adds time, the lowest across
    passes is the best estimate of the real cost.
    """
    if not passes:
        raise ValueError("no suite passes to merge")
    best: dict[str, Measurement] = {}
    for suite in passes:
        for measurement in suite.measurements:
            current = best.get(measurement.name)
            if current is None or measurement.min_ms < current.min_ms:
                best[measurement.name] = measurement
    order = [measurement.name for measurement in passes[0].measurements]
    return SuiteResult(
        calibration_ms=min(suite.calibration_ms for suite in passes),
        measurements=tuple(best[name] for name in order),
    )


def measure_baseline(
    scenarios: Sequence[Scenario], budgets: CliLatencyBudgets, runs: int
) -> SuiteResult:
    """Measure baselines over repeated passes, keeping the lowest of each."""
    return merge_suites(
        [
            run_suite(scenarios, warmups=budgets.warmups, runs=runs)
            for _ in range(budgets.update_passes)
        ]
    )


def render_baseline_table(result: SuiteResult) -> str:
    """Render the ``[cli_latency.baseline_ms]`` body for a suite run."""
    return "\n".join(
        f"{measurement.name} = {measurement.min_ms:.1f}"
        for measurement in result.measurements
    )


def render_provenance(result: SuiteResult) -> str:
    """Render the comment recording how the baselines below were measured.

    Generated rather than hand-written so it cannot drift from the sample count
    that actually produced the numbers: a baseline recorded from fewer runs is
    a higher baseline, which would quietly loosen the ratchet.
    """
    runs = result.measurements[0].runs if result.measurements else 0
    version = platform.python_version_tuple()
    return (
        f"# Lowest of {runs} runs per pass, recorded on {platform.system()} / "
        f"CPython {version[0]}.{version[1]}."
    )


_BASELINE_SECTION = re.compile(
    r"(?ms)^(?:# (?:Minimum|Lowest) of [^\n]*\n)?(\[cli_latency\.baseline_ms\]\n)"
    r"(?:[^\[]*?)(?=^\[|\Z)"
)


def update_budget_text(text: str, result: SuiteResult) -> str:
    """Return *text* with the calibration reference and baselines refreshed.

    Rewrites only the generated regions — the calibration reference, the
    provenance comment, and the baseline table — so the surrounding commentary
    (the rationale for the headroom, the re-baselining instructions) survives.
    """
    updated = re.sub(
        r"(?m)^reference_ms = .*$",
        f"reference_ms = {result.calibration_ms:.2f}",
        text,
        count=1,
    )
    if not _BASELINE_SECTION.search(updated):
        raise ValueError("perf-budgets.toml has no [cli_latency.baseline_ms] section")
    return _BASELINE_SECTION.sub(
        lambda match: (
            render_provenance(result)
            + "\n"
            + match.group(1)
            + render_baseline_table(result)
            + "\n"
        ),
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
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "also write this run's JSON to PATH. Use this rather than a second "
            "--json invocation when archiving a result: the suite is noisy, so a "
            "re-run is a different measurement and may not show what failed."
        ),
    )
    args = parser.parse_args(argv)

    budgets = load_budgets()
    scenarios = select_scenarios(args.scenarios)
    # Re-baselining samples more than checking does. The recorded baseline is a
    # minimum, and a minimum over fewer runs lands higher, so re-recording at
    # the check's sample count would loosen the ratchet a little every time.
    default_runs = (
        budgets.cli_latency.update_runs if args.update else budgets.cli_latency.runs
    )
    runs = args.runs if args.runs is not None else default_runs
    if args.update:
        result = measure_baseline(scenarios, budgets.cli_latency, runs)
    else:
        result = run_suite(scenarios, warmups=budgets.cli_latency.warmups, runs=runs)

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

    payload = json.dumps(
        {
            "calibration_ms": round(result.calibration_ms, 2),
            "scale": round(scale, 3),
            "scenarios": {entry.name: entry.to_dict() for entry in results},
            "missing_baselines": unrecorded,
        },
        indent=2,
    )
    if args.out is not None:
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
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
