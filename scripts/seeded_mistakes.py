#!/usr/bin/env python3
"""Seed common LLM mistakes into the benchmark solutions and repair them.

Prints the Markdown report, or with ``--write`` updates
docs/benchmark/seeded-mistakes.md. ``--check`` fails when that page is out
of date. See benchmark/seeded_mistakes.py for the method.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.loader import load_all_problems  # noqa: E402
from benchmark.seeded_mistakes import evaluate, render_report  # noqa: E402

REPORT = ROOT / "docs" / "benchmark" / "seeded-mistakes.md"


def build_report() -> str:
    problems = load_all_problems()
    return render_report(evaluate(problems), len(problems))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help=f"update {REPORT}")
    mode.add_argument("--check", action="store_true", help="fail if it is stale")
    args = parser.parse_args(argv)

    report = build_report()
    if args.write:
        REPORT.write_text(report, encoding="utf-8")
        print(f"Wrote {REPORT.relative_to(ROOT)}")
    elif args.check:
        if REPORT.read_text(encoding="utf-8") != report:
            print(
                f"{REPORT.relative_to(ROOT)} is out of date; run "
                "`python3 scripts/seeded_mistakes.py --write`",
                file=sys.stderr,
            )
            return 1
        print(f"{REPORT.relative_to(ROOT)} is current")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
