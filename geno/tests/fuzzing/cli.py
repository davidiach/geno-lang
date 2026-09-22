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
    require_js: bool = False,
) -> int:
    """Run every corpus entry; failed executions count against the gate."""
    rng = random.Random(seed)
    tested = successful_programs = divergences = crashes = 0
    backend_attempted = backend_succeeded = backend_unavailable = 0

    def check(source: str, oracle: str | None, label: str, *, save: bool) -> None:
        nonlocal tested, successful_programs, divergences, crashes
        nonlocal backend_attempted, backend_succeeded, backend_unavailable
        result = run_all_backends(
            source,
            oracle=oracle,
            timeout=timeout,
            include_js=include_js,
            require_js=require_js,
        )
        tested += 1
        backend_attempted += sum(b.available for b in result.backends)
        backend_succeeded += sum(b.success for b in result.backends)
        backend_unavailable += sum(not b.available for b in result.backends)
        failed = any(b.available and not b.success for b in result.backends)
        if failed:
            crashes += 1
        elif not result.match:
            divergences += 1
        if result.match:
            successful_programs += 1
            status = "OK"
        else:
            status = "CRASH" if failed else "DIVERGENCE"
        if save and (not result.match or save_all):
            path = save_failure(result)
            status += f" (saved to {path})"
        print(f"  {label} {status}")
        if result.error:
            print(f"    {result.error[:500]}")

    print("--- Seed corpus ---")
    seeds = load_seed_corpus()
    for i, source in enumerate(seeds):
        check(source, None, f"[{i + 1}/{len(seeds)}]", save=True)

    failures = load_failure_corpus()
    if failures:
        print(f"\n--- Failure regressions ({len(failures)} files) ---")
        for i, (source, oracle) in enumerate(failures):
            check(source, oracle, f"[{i + 1}/{len(failures)}]", save=False)

    print(f"\n--- Generated programs ({num_programs}) ---")
    for i in range(num_programs):
        prog = _draw_program(rng)
        check(prog.source, prog.expected_output, f"[{i + 1}/{num_programs}]", save=True)

    print("\n" + "=" * 60)
    print("DIFFERENTIAL FUZZING SUMMARY")
    print("=" * 60)
    print(f"  Programs tested:     {tested}")
    print(f"  Programs successful: {successful_programs}")
    print(f"  Divergences:         {divergences}")
    print(f"  Crashes:             {crashes}")
    print(f"  Backend executions:  {backend_attempted}")
    print(f"  Backend successes:   {backend_succeeded}")
    print(f"  Backend failures:    {backend_attempted - backend_succeeded}")
    print(f"  Backend unavailable: {backend_unavailable}")
    if seed is not None:
        print(f"  Seed:                {seed}")
    success = tested > 0 and successful_programs == tested
    print(f"  Result:              {'PASS' if success else 'FAIL'}")
    print("=" * 60)
    return 0 if success else 1


def _run_replay(
    paths: list[str], timeout: float, include_js: bool, require_js: bool = False
) -> int:
    """Replay specific failure files."""
    divergences = 0
    tested = 0
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
            tested += 1
            result = run_all_backends(
                source,
                oracle=oracle,
                timeout=timeout,
                include_js=include_js,
                require_js=require_js,
            )
            if not result.match:
                divergences += 1
                print(f"  DIVERGENT: {result.error}")
            else:
                print("  OK (no divergence)")
    if not tested:
        print("No replay programs found")
    return 1 if not tested or divergences > 0 else 0


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
        "--require-node",
        action="store_true",
        help="Fail if the JavaScript backend is unavailable",
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
    if args.no_js and args.require_node:
        parser.error("--no-js cannot be combined with --require-node")
    if args.programs is not None and args.programs < 0:
        parser.error("--programs must be nonnegative")

    if args.replay:
        timeout = args.timeout or 10.0
        return _run_replay(args.replay, timeout, not args.no_js, args.require_node)

    if args.mode == "smoke":
        num_programs = args.programs if args.programs is not None else 50
        timeout = args.timeout or 5.0
    else:
        num_programs = args.programs if args.programs is not None else 2000
        timeout = args.timeout or 10.0

    t0 = time.monotonic()
    rc = _run_smoke(
        num_programs=num_programs,
        timeout=timeout,
        include_js=not args.no_js,
        save_all=args.save_all,
        seed=args.seed,
        require_js=args.require_node,
    )
    elapsed = time.monotonic() - t0
    print(f"\nTotal time: {elapsed:.1f}s")
    return rc
