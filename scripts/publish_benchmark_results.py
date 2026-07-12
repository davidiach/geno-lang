#!/usr/bin/env python3
"""Publish Geno-vs-Python benchmark metrics as a Markdown report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _pct(value: Any) -> str:
    if not isinstance(value, int | float):
        return "n/a"
    return f"{value * 100:.1f}%"


def _signed_pct(value: Any) -> str:
    if not isinstance(value, int | float):
        return "n/a"
    return f"{value * 100:+.1f} pp"


def _model_from_comparison_key(key: str) -> str:
    return key[: -len("_comparison")]


def _load_results(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("results payload must be a JSON object")
    return payload


def render_report(payload: dict[str, Any], *, source_path: str) -> str:
    """Render a public benchmark report from an ExperimentRunner payload."""
    if payload.get("status") == "not_implemented":
        raise ValueError("cannot publish placeholder benchmark results")

    config = payload.get("config") or {}
    metrics = payload.get("metrics") or {}
    if not isinstance(config, dict) or not isinstance(metrics, dict):
        raise ValueError("results payload is missing config or metrics")

    comparison_keys = sorted(k for k in metrics if k.endswith("_comparison"))
    if not comparison_keys:
        raise ValueError(
            "results payload has no Geno-vs-Python comparison metrics; "
            "run both languages before publishing"
        )

    generation_count = len(payload.get("generation_results") or [])
    evaluation_count = len(payload.get("evaluation_results") or [])
    problem_ids = config.get("problem_ids") or []
    problem_count = len(problem_ids) if isinstance(problem_ids, list) else "unknown"
    trials = config.get("trials_per_condition", "unknown")

    lines = [
        "# Geno vs Python Benchmark Results",
        "",
        "This report is generated from raw experiment artifacts. Do not edit the",
        "metric tables by hand; rerun `scripts/publish_benchmark_results.py` instead.",
        "",
        "## Run Metadata",
        "",
        f"- Experiment: `{config.get('experiment_id', 'unknown')}`",
        f"- Source artifact: `{source_path}`",
        f"- Started: `{payload.get('start_time', 'unknown')}`",
        f"- Ended: `{payload.get('end_time', 'unknown')}`",
        f"- Problems: `{problem_count}`",
        f"- Trials per condition: `{trials}`",
        f"- Generations: `{generation_count}`",
        f"- Evaluations: `{evaluation_count}`",
        "",
        "## Model Summary",
        "",
        "| Model | Geno pass@1 | Python pass@1 | Geno pass@k | Python pass@k | Geno parse | Python parse | Geno hidden | Python hidden |",
        "|-------|------------:|--------------:|------------:|--------------:|-----------:|-------------:|------------:|--------------:|",
    ]

    for comparison_key in comparison_keys:
        model = _model_from_comparison_key(comparison_key)
        geno_metrics = metrics.get(f"{model}_geno") or {}
        python_metrics = metrics.get(f"{model}_python") or {}
        lines.append(
            "| {model} | {geno_pass_1} | {python_pass_1} | {geno_pass_k} | {python_pass_k} | {geno_parse} | {python_parse} | {geno_hidden} | {python_hidden} |".format(
                model=model,
                geno_pass_1=_pct(geno_metrics.get("pass_at_1")),
                python_pass_1=_pct(python_metrics.get("pass_at_1")),
                geno_pass_k=_pct(geno_metrics.get("pass_at_k")),
                python_pass_k=_pct(python_metrics.get("pass_at_k")),
                geno_parse=_pct(geno_metrics.get("parse_success_rate")),
                python_parse=_pct(python_metrics.get("parse_success_rate")),
                geno_hidden=_pct(geno_metrics.get("hidden_pass_rate")),
                python_hidden=_pct(python_metrics.get("hidden_pass_rate")),
            )
        )

    lines.extend(
        [
            "",
            "## Error Breakdown",
            "",
            "| Model | Language | Syntax | Type | Runtime | Wrong answer | Timeout | Incomplete |",
            "|-------|----------|-------:|-----:|--------:|-------------:|--------:|-----------:|",
        ]
    )
    for comparison_key in comparison_keys:
        model = _model_from_comparison_key(comparison_key)
        for language in ("geno", "python"):
            condition = metrics.get(f"{model}_{language}") or {}
            error_rates = condition.get("error_rates") or {}
            lines.append(
                "| {model} | {language} | {syntax} | {type_} | {runtime} | {wrong} | {timeout} | {incomplete} |".format(
                    model=model,
                    language=language,
                    syntax=_pct(error_rates.get("syntax")),
                    type_=_pct(error_rates.get("type")),
                    runtime=_pct(error_rates.get("runtime")),
                    wrong=_pct(error_rates.get("wrong_answer")),
                    timeout=_pct(error_rates.get("timeout")),
                    incomplete=_pct(error_rates.get("incomplete")),
                )
            )

    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            "The source artifact must include raw prompts, raw responses, extracted code,",
            "evaluations, and aggregate metrics. For a complete publication bundle, keep",
            "the generated `results.json` or the `results/` directory produced by",
            "`ExperimentRunner.save_results()` alongside this report.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish Geno-vs-Python benchmark metrics as Markdown."
    )
    parser.add_argument("--input", required=True, help="Experiment results JSON")
    parser.add_argument("--output", required=True, help="Markdown report path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    try:
        payload = _load_results(input_path)
        report = render_report(payload, source_path=str(input_path))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8", newline="\n")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
