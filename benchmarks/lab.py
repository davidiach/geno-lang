#!/usr/bin/env python3
"""Correctness-first performance laboratory for the legacy benchmark cases.

``run_benchmark.py`` remains the stable human-readable historical entry point.
This command adds calibrated paired measurements, fresh-process repetition,
and machine-readable evidence without changing any benchmark workload.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.harness import (  # noqa: E402
    MeasurementConfig,
    build_artifact,
    collect_environment_metadata,
    config_as_dict,
    configure_utf8_streams,
    measure_paired,
    summarize_case,
    summarize_samples,
    write_json_artifact,
    write_jsonl_artifact,
    write_payload_to_stream,
)
from benchmarks.run_benchmark import _PROBLEMS, Problem  # noqa: E402
from geno.compiler import compile_to_python  # noqa: E402

DEFAULT_RATIO_THRESHOLD = 2.0
DEFAULT_PASS_RATE_TARGET = 80.0


def _case_seed(seed: int, name: str) -> int:
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return seed ^ int.from_bytes(digest[:4], "big")


def _compile_case(source: str) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    generated = compile_to_python(source)
    codegen_seconds = time.perf_counter() - started

    started = time.perf_counter()
    code = compile(generated, "<geno-benchmark>", "exec")
    host_compile_seconds = time.perf_counter() - started

    namespace: dict[str, Any] = {}
    started = time.perf_counter()
    exec(code, namespace)  # noqa: S102
    host_exec_seconds = time.perf_counter() - started
    return namespace, {
        "geno_codegen_seconds": codegen_seconds,
        "host_compile_seconds": host_compile_seconds,
        "host_exec_seconds": host_exec_seconds,
        "generated_python_bytes": len(generated.encode("utf-8")),
    }


def _display_value(value: Any) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= 500 else rendered[:497] + "..."


def _summarize_compile_samples(
    samples: Sequence[Mapping[str, Any]], config: MeasurementConfig
) -> dict[str, Any]:
    summary = {
        phase: summarize_samples(
            [float(sample[phase]) for sample in samples],
            bootstrap_resamples=config.bootstrap_resamples,
            seed=config.seed + offset,
        )
        for offset, phase in enumerate(
            ("geno_codegen_seconds", "host_compile_seconds", "host_exec_seconds")
        )
    }
    sizes = [int(sample["generated_python_bytes"]) for sample in samples]
    summary["generated_python_bytes"] = {
        "count": len(sizes),
        "median": float(statistics.median(sizes)),
        "min": min(sizes),
        "max": max(sizes),
    }
    return summary


def run_case_local(
    problem: Problem, config: MeasurementConfig, *, process_index: int = 0
) -> dict[str, Any]:
    """Compile, check, and measure one problem inside the current process."""
    name, source, python_function, call_builder = problem
    case_config = MeasurementConfig(
        **{**config_as_dict(config), "seed": _case_seed(config.seed, name)}
    )
    correctness_checked = False
    try:
        namespace, compile_sample = _compile_case(source)
        geno_function = call_builder(namespace)

        # Correctness is deliberately outside every timed region.  A mismatch
        # returns an error payload immediately, so no invalid timing survives.
        correctness_checked = True
        geno_result = geno_function()
        python_result = python_function()
        if geno_result != python_result:
            raise ValueError(
                "correctness mismatch: "
                f"Geno={_display_value(geno_result)}, "
                f"Python={_display_value(python_result)}"
            )

        loop_count, samples = measure_paired(
            geno_function,
            python_function,
            config=case_config,
        )
        for sample in samples:
            sample["process_index"] = process_index
        compile_sample["process_index"] = process_index
        return {
            "name": name,
            "status": "ok",
            "correctness": {"checked": True, "matched": True},
            "loop_counts": [loop_count],
            "compile_samples": [compile_sample],
            "samples": samples,
            "summary": {
                "compile": _summarize_compile_samples([compile_sample], case_config),
                "execution": summarize_case(
                    samples,
                    bootstrap_resamples=case_config.bootstrap_resamples,
                    seed=case_config.seed,
                ),
            },
        }
    except Exception as error:  # benchmark failures are artifact data
        return {
            "name": name,
            "status": "error",
            "correctness": {"checked": correctness_checked, "matched": False},
            "loop_counts": [],
            "compile_samples": [],
            "samples": [],
            "summary": None,
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }


def _worker_command(
    name: str, config: MeasurementConfig, process_index: int
) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker-case",
        name,
        "--worker-process-index",
        str(process_index),
        "--warmups",
        str(config.warmups),
        "--repetitions",
        str(config.repetitions),
        "--target-time-ms",
        str(config.target_sample_seconds * 1_000),
        "--max-loops",
        str(config.max_loops),
        "--bootstrap-resamples",
        str(config.bootstrap_resamples),
        "--seed",
        str(config.seed),
        "--gc",
        config.gc_policy,
    ]


def _merge_process_results(
    name: str,
    results: Sequence[Mapping[str, Any]],
    config: MeasurementConfig,
) -> dict[str, Any]:
    failures = [result for result in results if result["status"] != "ok"]
    if failures:
        first = failures[0]
        return {
            "name": name,
            "status": "error",
            "correctness": first.get("correctness"),
            "loop_counts": [],
            "compile_samples": [],
            "samples": [],
            "summary": None,
            "error": first.get("error"),
        }
    compile_samples = [
        sample for result in results for sample in result["compile_samples"]
    ]
    samples = [sample for result in results for sample in result["samples"]]
    loop_counts = [count for result in results for count in result["loop_counts"]]
    case_seed = _case_seed(config.seed, name)
    return {
        "name": name,
        "status": "ok",
        "correctness": {"checked": True, "matched": True},
        "loop_counts": loop_counts,
        "compile_samples": compile_samples,
        "samples": samples,
        "summary": {
            "compile": _summarize_compile_samples(compile_samples, config),
            "execution": summarize_case(
                samples,
                bootstrap_resamples=config.bootstrap_resamples,
                seed=case_seed,
            ),
        },
    }


def run_case_fresh_processes(
    problem: Problem, config: MeasurementConfig, *, process_repetitions: int
) -> dict[str, Any]:
    """Measure a case in independent child interpreters and merge raw samples."""
    name = problem[0]
    results: list[Mapping[str, Any]] = []
    for process_index in range(process_repetitions):
        try:
            completed = subprocess.run(  # noqa: S603
                _worker_command(name, config, process_index),
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except subprocess.TimeoutExpired as timeout_error:
            results.append(
                {
                    "name": name,
                    "status": "error",
                    "correctness": {"checked": False, "matched": False},
                    "error": {
                        "type": "WorkerTimeoutError",
                        "message": f"worker timed out after {timeout_error.timeout}s",
                    },
                }
            )
            continue
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError:
            result = {
                "name": name,
                "status": "error",
                "correctness": {"checked": False, "matched": False},
                "error": {
                    "type": "WorkerProtocolError",
                    "message": (
                        f"worker exited {completed.returncode}; stderr="
                        f"{completed.stderr[-500:]!r}"
                    ),
                },
            }
        results.append(result)
    return _merge_process_results(name, results, config)


def select_problems(selectors: Sequence[str]) -> list[Problem]:
    """Resolve exact names or shell-style patterns in registry order."""
    if not selectors:
        return list(_PROBLEMS)
    selected: list[Problem] = []
    for problem in _PROBLEMS:
        if any(fnmatch.fnmatchcase(problem[0], selector) for selector in selectors):
            selected.append(problem)
    return selected


def build_aggregate(
    cases: Sequence[Mapping[str, Any]],
    *,
    ratio_threshold: float,
    pass_rate_target: float,
) -> dict[str, Any]:
    successful = [case for case in cases if case["status"] == "ok"]
    ratios = [
        float(case["summary"]["execution"]["comparison"]["median_ratio"])
        for case in successful
    ]
    passed = sum(ratio <= ratio_threshold for ratio in ratios)
    pass_rate = passed / len(ratios) * 100.0 if ratios else 0.0
    sorted_ratios = sorted(ratios)
    p90_index = max(0, math.ceil(0.9 * len(sorted_ratios)) - 1)
    errors = len(cases) - len(successful)
    return {
        "selected": len(cases),
        "measured": len(successful),
        "passed": passed,
        "slow": len(successful) - passed,
        "errors": errors,
        "ratio_threshold": ratio_threshold,
        "pass_rate_target_percent": pass_rate_target,
        "pass_rate_percent": pass_rate,
        "median_ratio": (
            float(statistics.median(sorted_ratios)) if sorted_ratios else None
        ),
        "p90_ratio": sorted_ratios[p90_index] if sorted_ratios else None,
        "success": errors == 0 and bool(ratios) and pass_rate >= pass_rate_target,
    }


def _print_human_report(
    cases: Sequence[Mapping[str, Any]],
    aggregate: Mapping[str, Any],
    *,
    verbose: bool,
) -> None:
    print(f"\nGeno vs Python Benchmark Laboratory  ({len(cases)} cases)")
    print("=" * 78)
    print(
        f"{'#':>3}  {'Problem':<28} {'Geno (ms)':>10} "
        f"{'Python (ms)':>11} {'Ratio':>7} {'Pass':>5}"
    )
    print("-" * 78)
    threshold = float(aggregate["ratio_threshold"])
    for index, case in enumerate(cases, 1):
        if case["status"] != "ok":
            print(
                f"{index:3}  {case['name']:<28} {'ERROR':>10} {'':>11} {'':>7} {'ERR':>5}"
            )
            print(f"     {case['error']['type']}: {case['error']['message']}")
            continue
        execution = case["summary"]["execution"]
        geno_ms = execution["geno"]["median_seconds"] * 1_000
        python_ms = execution["python"]["median_seconds"] * 1_000
        comparison = execution["comparison"]
        ratio = comparison["median_ratio"]
        status = "OK" if ratio <= threshold else "SLOW"
        print(
            f"{index:3}  {case['name']:<28} {geno_ms:10.3f} "
            f"{python_ms:11.3f} {ratio:7.2f}x {status:>5}"
        )
        if verbose:
            ci = comparison["bootstrap_median_ratio_ci95"]
            compile_ms = (
                case["summary"]["compile"]["geno_codegen_seconds"]["median_seconds"]
                * 1_000
            )
            print(
                f"     loops={case['loop_counts']} ratio CI95="
                f"[{ci[0]:.2f}x, {ci[1]:.2f}x] codegen={compile_ms:.3f} ms"
            )
    print("-" * 78)
    print(
        f"\nResults: {aggregate['measured']} measured, {aggregate['passed']} passed "
        f"(≤{threshold:g}×), {aggregate['slow']} slow, {aggregate['errors']} errors"
    )
    print(
        f"Pass rate: {aggregate['pass_rate_percent']:.1f}% "
        f"(target: ≥{aggregate['pass_rate_target_percent']:g}%)"
    )
    if aggregate["median_ratio"] is not None:
        print(f"Median ratio: {aggregate['median_ratio']:.2f}x")
        print(f"P90 ratio:    {aggregate['p90_ratio']:.2f}x")
    print("\n" + ("PASS" if aggregate["success"] else "FAIL"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run correctness-first calibrated Geno/Python benchmark comparisons. "
            "Selectors accept exact names or shell-style patterns."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("selectors", nargs="*", metavar="CASE")
    parser.add_argument("--list", action="store_true", help="list case names and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument(
        "--process-repetitions",
        type=int,
        default=1,
        help="independent Python processes per case; use 3+ for merge evidence",
    )
    parser.add_argument("--target-time-ms", type=float, default=10.0)
    parser.add_argument("--max-loops", type=int, default=100_000)
    parser.add_argument("--bootstrap-resamples", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--gc", choices=("disabled", "enabled"), default="disabled")
    parser.add_argument(
        "--ratio-threshold", type=float, default=DEFAULT_RATIO_THRESHOLD
    )
    parser.add_argument(
        "--pass-rate-target", type=float, default=DEFAULT_PASS_RATE_TARGET
    )
    parser.add_argument("--json", metavar="PATH", help="write summary + raw JSON")
    parser.add_argument("--jsonl", metavar="PATH", help="write record-oriented JSONL")
    parser.add_argument("--worker-case", help=argparse.SUPPRESS)
    parser.add_argument(
        "--worker-process-index", type=int, default=0, help=argparse.SUPPRESS
    )
    return parser


def _measurement_config(args: argparse.Namespace) -> MeasurementConfig:
    config = MeasurementConfig(
        warmups=args.warmups,
        repetitions=args.repetitions,
        target_sample_seconds=args.target_time_ms / 1_000,
        max_loops=args.max_loops,
        bootstrap_resamples=args.bootstrap_resamples,
        seed=args.seed,
        gc_policy=args.gc,
    )
    config.validate()
    return config


def main(argv: Sequence[str] | None = None) -> int:
    configure_utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = _measurement_config(args)
    except ValueError as error:
        parser.error(str(error))
    if args.process_repetitions < 1:
        parser.error("process repetitions must be at least 1")
    if args.ratio_threshold <= 0:
        parser.error("ratio threshold must be positive")
    if not 0 <= args.pass_rate_target <= 100:
        parser.error("pass rate target must be between 0 and 100")
    if args.json == "-" and args.jsonl == "-":
        parser.error("JSON and JSONL cannot both use standard output")

    if args.list:
        for problem in _PROBLEMS:
            print(problem[0])
        return 0

    if args.worker_case:
        matching = select_problems((args.worker_case,))
        if len(matching) != 1 or matching[0][0] != args.worker_case:
            parser.error(f"unknown worker case: {args.worker_case}")
        result = run_case_local(
            matching[0], config, process_index=args.worker_process_index
        )
        write_payload_to_stream(result, sys.stdout)
        return 0 if result["status"] == "ok" else 1

    problems = select_problems(args.selectors)
    if not problems:
        parser.error("selectors matched no benchmark cases")

    cases = []
    for problem in problems:
        if args.process_repetitions == 1:
            case = run_case_local(problem, config)
        else:
            case = run_case_fresh_processes(
                problem,
                config,
                process_repetitions=args.process_repetitions,
            )
        cases.append(case)
    aggregate = build_aggregate(
        cases,
        ratio_threshold=args.ratio_threshold,
        pass_rate_target=args.pass_rate_target,
    )
    configuration = {
        **config_as_dict(config),
        "process_repetitions": args.process_repetitions,
        "selectors": list(args.selectors),
        "ratio_threshold": args.ratio_threshold,
        "pass_rate_target_percent": args.pass_rate_target,
        "clock": "time.perf_counter",
        "paired_order": "seeded initial arm, then alternating",
        "compile_excluded_from_execution": True,
    }
    artifact = build_artifact(
        metadata=collect_environment_metadata(REPO_ROOT),
        configuration=configuration,
        cases=cases,
        aggregate=aggregate,
    )
    machine_stdout = args.json == "-" or args.jsonl == "-"
    if not machine_stdout:
        _print_human_report(cases, aggregate, verbose=args.verbose)
    if args.json:
        write_json_artifact(artifact, args.json)
    if args.jsonl:
        write_jsonl_artifact(artifact, args.jsonl)
    return 0 if aggregate["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
