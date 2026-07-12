#!/usr/bin/env python3
"""
Analyze Experiment Results

This script analyzes experiment results and generates reports.
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import ReportGenerator, ResultsAnalyzer


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Analyze experiment results and generate reports"
    )
    parser.add_argument(
        "--input", type=str, required=True, help="Path to results JSON file"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="report.md",
        help="Output file for report (default: report.md)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "markdown", "latex"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Generate summary only, not full report",
    )
    parser.add_argument(
        "--show-visualizations",
        action="store_true",
        help="Include ASCII visualizations in report",
    )
    return parser.parse_args()


def _safe_print(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(
            text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
                sys.stdout.encoding or "utf-8",
                errors="replace",
            )
        )


def main():
    """Analyze results and generate report."""
    args = parse_args()

    _safe_print("=" * 60)
    _safe_print("GENOTYPE RESULTS ANALYZER")
    _safe_print("=" * 60)
    _safe_print(f"Started: {datetime.now().isoformat()}")
    _safe_print()

    # Load results
    _safe_print(f"Loading results from {args.input}...")
    try:
        with open(args.input, encoding="utf-8") as f:
            results = json.load(f)
        _safe_print("  Done!")
    except FileNotFoundError:
        _safe_print(f"  ERROR: File not found: {args.input}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        _safe_print(f"  ERROR: Invalid JSON: {e}")
        sys.exit(1)

    _safe_print()

    # Check if results are placeholder
    is_placeholder = results.get("status") == "not_implemented"
    if is_placeholder:
        _safe_print("Note: Results contain placeholder data.")
        _safe_print("Run the experiment with LLM integration to get real results.")
        _safe_print()

    # Create analyzer
    analyzer = None
    if not is_placeholder:
        _safe_print("Analyzing results...")
        try:
            analyzer = ResultsAnalyzer(results)
            analyzer.compute_summary()
            _safe_print("  Done!")
        except Exception as e:
            _safe_print(f"  ERROR: Analysis failed: {e}")
            sys.exit(1)

    _safe_print()

    # Generate report
    _safe_print("Generating report...")
    if is_placeholder:
        report = generate_demo_report()
    elif analyzer:
        generator = ReportGenerator(analyzer)

        if args.summary_only:
            report = generator.generate_summary()
        elif args.format == "text":
            report = generator.generate_full_report()
        elif args.format == "markdown":
            report = generator.generate_markdown_report()
        elif args.format == "latex":
            report = generator.generate_latex_tables()
        else:
            raise AssertionError(f"unsupported report format: {args.format}")
    else:
        raise AssertionError("analyzer was not initialized")

    _safe_print("  Done!")
    _safe_print()

    # Save report
    _safe_print(f"Saving report to {args.output}...")
    with open(args.output, "w", encoding="utf-8", newline="\n") as f:
        f.write(report)
    _safe_print("  Done!")
    _safe_print()

    # Print preview
    _safe_print("-" * 60)
    _safe_print("REPORT PREVIEW (first 50 lines)")
    _safe_print("-" * 60)
    lines = report.split("\n")
    for line in lines[:50]:
        _safe_print(line)
    if len(lines) > 50:
        _safe_print(f"\n... ({len(lines) - 50} more lines)")
    _safe_print()

    _safe_print(f"Completed: {datetime.now().isoformat()}")


def generate_demo_report():
    """Generate a demo report with placeholder data."""
    return """# Geno Experiment Results (Demo)

## Overview

This is a demo report. Run the experiment with LLM integration to get real results.

## Experiment Design

- **Problems**: 77 benchmark problems
- **Languages**: Geno vs Python
- **Metrics**: Pass rate, parse success, type check success, error distribution

## Expected Metrics

Based on the language design, we expect:

| Metric | Geno | Python | Difference |
|--------|----------|--------|------------|
| Overall Pass Rate | ~75% | ~60% | +15% |
| Parse Success | ~95% | ~85% | +10% |
| Type Check Success | ~90% | N/A | - |

## Research Questions

1. **RQ1**: Does Geno produce higher-quality LLM-generated code?
2. **RQ2**: Which error types are reduced?
3. **RQ3**: How does difficulty affect the comparison?

## Next Steps

1. Implement LLM API integration in `experiment/runner.py`
2. Run full experiment with multiple models
3. Analyze real results with this script

---
Generated by Geno Analysis Framework
"""


if __name__ == "__main__":
    main()
