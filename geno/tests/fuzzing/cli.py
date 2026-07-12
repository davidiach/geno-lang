"""
CLI entry point for the differential fuzzing harness.

Usage:
    python3 -m geno.tests.fuzzing --mode=smoke --timeout=60
    python3 -m geno.tests.fuzzing --mode=deep --programs=5000
    python3 -m geno.tests.fuzzing --replay=geno/tests/fuzzing/failures/failure_*.geno
"""

from __future__ import annotations

import argparse
import glob
import random
import time
from pathlib import Path

from .corpus import load_failure_corpus, load_seed_corpus, save_failure
from .gen import draw_program
from .runner import run_all_backends


def _draw_program(rng: random.Random):  # type: ignore[no-untyped-def]
    """Draw a single generated program using a seeded RNG."""
    return draw_program(rng)


def _run_smoke(
    num_programs: int,
    timeout: float,
    include_js: bool,
    save_all: bool,
    seed: int | None,
) -> int:
    """Run smoke mode: seed corpus + generated programs."""

    if seed is not None:
        rng = random.Random(seed)
    else:
        rng = random.Random()

    divergences = 0
    tested = 0
    crashes = 0

    # Phase 1: seed corpus
    print("--- Seed corpus ---")
    seeds = load_seed_corpus()
    for i, source in enumerate(seeds):
        result = run_all_backends(source, timeout=timeout, include_js=include_js)
        tested += 1
        successful = [b for b in result.backends if b.success]
        if not result.match:
            divergences += 1
            path = save_failure(result)
            print(f"  [{i + 1}/{len(seeds)}] DIVERGENCE (saved to {path})")
            if result.error:
                print(f"    {result.error[:200]}")
        elif len(successful) < 2:
            crashes += 1
            print(f"  [{i + 1}/{len(seeds)}] CRASH (<2 backends succeeded)")
        else:
            print(f"  [{i + 1}/{len(seeds)}] OK")

    # Phase 2: failure regressions
    failures = load_failure_corpus()
    if failures:
        print(f"\n--- Failure regressions ({len(failures)} files) ---")
        for i, (source, oracle) in enumerate(failures):
            result = run_all_backends(
                source, oracle=oracle, timeout=timeout, include_js=include_js
            )
            tested += 1
            if not result.match:
                divergences += 1
                print(f"  [{i + 1}/{len(failures)}] STILL DIVERGENT")
            else:
                print(f"  [{i + 1}/{len(failures)}] FIXED")

    # Phase 3: generated programs
    print(f"\n--- Generated programs ({num_programs}) ---")
    gen_divergences = 0
    gen_crashes = 0

    # Seed Hypothesis's RNG for reproducibility
    if seed is not None:
        import os

        os.environ["HYPOTHESIS_SEED"] = str(seed)

    for i in range(num_programs):
        prog = _draw_program(rng)
        result = run_all_backends(
            prog.source,
            oracle=prog.expected_output,
            timeout=timeout,
            include_js=include_js,
        )
        tested += 1
        successful = [b for b in result.backends if b.success]
        if not result.match:
            divergences += 1
            gen_divergences += 1
            path = save_failure(result)
            print(f"  [{i + 1}/{num_programs}] DIVERGENCE (saved to {path})")
            if result.error:
                print(f"    {result.error[:200]}")
        elif len(successful) < 2:
            crashes += 1
            gen_crashes += 1
            if (i + 1) % 50 == 0 or i == 0:
                print(f"  [{i + 1}/{num_programs}] CRASH (<2 backends)")
        else:
            if save_all:
                path = save_failure(result)
                print(f"  [{i + 1}/{num_programs}] OK (saved to {path})")
            elif (i + 1) % 50 == 0 or i == num_programs - 1:
                print(f"  [{i + 1}/{num_programs}] OK (running...)")

    # Summary
    print("\n" + "=" * 60)
    print("DIFFERENTIAL FUZZING SUMMARY")
    print("=" * 60)
    print(f"  Programs tested: {tested}")
    print(f"  Divergences:     {divergences}")
    print(f"  Crashes:         {crashes}")
    print(f"  Generated divergences: {gen_divergences}")
    print(f"  Generated crashes:     {gen_crashes}")
    if seed is not None:
        print(f"  Seed:            {seed}")
    status = "PASS" if divergences == 0 else "FAIL"
    print(f"  Result:          {status}")
    print("=" * 60)

    return 1 if divergences > 0 else 0


def _run_replay(paths: list[str], timeout: float, include_js: bool) -> int:
    """Replay specific failure files."""
    divergences = 0
    for pattern in paths:
        for path_str in sorted(glob.glob(pattern)):
            path = Path(path_str)
            if path.suffix != ".geno":
                continue
            source = path.read_text()
            oracle = None
            sidecar = path.with_suffix(".json")
            if sidecar.exists():
                try:
                    import json

                    sidecar_data = json.loads(sidecar.read_text())
                    sidecar_oracle = sidecar_data.get("oracle")
                    if sidecar_oracle is None or isinstance(sidecar_oracle, str):
                        oracle = sidecar_oracle
                except (OSError, ValueError, TypeError):
                    oracle = None
            print(f"Replaying {path}...")
            result = run_all_backends(
                source,
                oracle=oracle,
                timeout=timeout,
                include_js=include_js,
            )
            if not result.match:
                divergences += 1
                print(f"  DIVERGENT: {result.error}")
            else:
                print("  OK (no divergence)")
    return 1 if divergences > 0 else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Differential fuzzing harness for Geno"
    )
    parser.add_argument(
        "--mode",
        choices=["smoke", "deep"],
        default="smoke",
        help="Fuzzing mode (default: smoke)",
    )
    parser.add_argument(
        "--programs",
        type=int,
        default=None,
        help="Number of programs to generate (default: 50 smoke, 2000 deep)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Per-program timeout in seconds (default: 5 smoke, 10 deep)",
    )
    parser.add_argument(
        "--no-js",
        action="store_true",
        help="Skip the JS backend",
    )
    parser.add_argument(
        "--save-all",
        action="store_true",
        help="Save every generated program (for corpus building)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--replay",
        nargs="+",
        default=None,
        help="Replay specific failure .geno files (glob patterns supported)",
    )
    args = parser.parse_args(argv)

    if args.replay:
        timeout = args.timeout or 10.0
        return _run_replay(args.replay, timeout, not args.no_js)

    if args.mode == "smoke":
        num_programs = args.programs or 50
        timeout = args.timeout or 5.0
    else:
        num_programs = args.programs or 2000
        timeout = args.timeout or 10.0

    t0 = time.monotonic()
    rc = _run_smoke(
        num_programs=num_programs,
        timeout=timeout,
        include_js=not args.no_js,
        save_all=args.save_all,
        seed=args.seed,
    )
    elapsed = time.monotonic() - t0
    print(f"\nTotal time: {elapsed:.1f}s")
    return rc
