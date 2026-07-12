"""
Benchmark Problem Loader
========================

Utilities for loading, validating, and testing benchmark problems.
"""

import sys
from pathlib import Path
from typing import Any, cast

# Preserve direct `python benchmark/loader.py` usage.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.runner import BenchmarkRunner, EvaluationResult
from benchmark.schema import (
    Difficulty,
    Domain,
    Problem,
    load_problems,
    validate_problem,
)


def get_problems_dir() -> Path:
    """Get the default problems directory."""
    return Path(__file__).parent / "problems"


def get_app_problems_dir() -> Path:
    """Get the app-tier (benchmark v2) problems directory."""
    return get_problems_dir() / "apps"


def load_all_problems(track: str = "core") -> list[Problem]:
    """Load problems for a track.

    "core" is the frozen v1 single-function suite (default, preserves
    published comparability); "apps" is the benchmark v2 app-tier track;
    "all" combines both.
    """
    if track == "core":
        return cast("list[Problem]", load_problems(get_problems_dir()))
    if track == "apps":
        return cast("list[Problem]", load_problems(get_app_problems_dir()))
    if track == "all":
        combined = load_problems(get_problems_dir()) + load_problems(
            get_app_problems_dir()
        )
        return sorted(combined, key=lambda p: p.id)
    raise ValueError(f"Unknown track: {track!r} (expected 'core', 'apps', or 'all')")


def load_problem_by_id(problem_id: str, track: str = "all") -> Problem | None:
    """Load a specific problem by ID, searching all tracks by default."""
    problems = load_all_problems(track)
    for p in problems:
        if p.id == problem_id:
            return p
    return None


def filter_by_difficulty(
    problems: list[Problem],
    difficulty: Difficulty | str,
) -> list[Problem]:
    """Filter problems by difficulty level."""
    if isinstance(difficulty, str):
        difficulty = Difficulty(difficulty)
    return [p for p in problems if p.difficulty == difficulty]


def filter_by_domain(problems: list[Problem], domain: Domain) -> list[Problem]:
    """Filter problems by domain."""
    return [p for p in problems if p.domain == domain]


def validate_all_problems(
    problems: list[Problem] | None = None,
) -> dict[str, list[str]]:
    """Validate all problems and return issues."""
    if problems is None:
        problems = load_all_problems()
    issues = {}
    for p in problems:
        problem_issues = validate_problem(p)
        if problem_issues:
            issues[p.id] = problem_issues
    return issues


def run_canonical_solutions(
    problems: list[Problem] | None = None,
) -> dict[str, EvaluationResult]:
    """Test all canonical solutions to verify they pass."""
    if problems is None:
        problems = load_all_problems()
    runner = BenchmarkRunner.for_research()
    results = {}

    for problem in problems:
        # Test Geno solution
        if problem.geno_solution:
            result = runner.evaluate_geno(problem, problem.geno_solution)
            results[f"{problem.id}_geno"] = result

        # Test Python solution
        if problem.python_solution:
            result = runner.evaluate_python(problem, problem.python_solution)
            results[f"{problem.id}_python"] = result

    return results


# Backward-compatible public alias.
test_canonical_solutions = run_canonical_solutions


def print_problem_stats():
    """Print statistics about the problem set."""
    problems = load_all_problems()

    print(f"\nTotal Problems: {len(problems)}")
    print("\nBy Difficulty:")
    for diff in Difficulty:
        count = len(filter_by_difficulty(problems, diff))
        print(f"  {diff.value}: {count}")

    print("\nBy Domain:")
    for dom in Domain:
        count = len(filter_by_domain(problems, dom))
        if count > 0:
            print(f"  {dom.value}: {count}")

    print("\nConstructs Tested:")
    constructs: dict[str, int] = {}
    for p in problems:
        for c in p.constructs_tested:
            constructs[c] = constructs.get(c, 0) + 1
    for c, count in sorted(constructs.items(), key=lambda x: -x[1]):
        print(f"  {c}: {count}")


def verify_problem_set(
    problems: list[Problem] | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run full verification of the problem set and return a summary report."""
    if problems is None:
        problems = load_all_problems()
    issues = validate_all_problems(problems)
    canonical_results = run_canonical_solutions(problems)

    for name, result in canonical_results.items():
        if result.all_passed:
            continue
        problem_id, language = name.rsplit("_", 1)
        issues.setdefault(problem_id, []).append(
            f"{language} canonical solution failed: {result.error_message or 'Wrong answers'}"
        )

    difficulty_distribution = {
        diff.value: len(filter_by_difficulty(problems, diff)) for diff in Difficulty
    }
    domain_distribution = {
        dom.value: len(filter_by_domain(problems, dom))
        for dom in Domain
        if len(filter_by_domain(problems, dom)) > 0
    }

    report = {
        "total_problems": len(problems),
        "valid_problems": len(problems) - len(issues),
        "problems_with_issues": len(issues),
        "difficulty_distribution": difficulty_distribution,
        "domain_distribution": domain_distribution,
        "issues": issues,
        "canonical_results": {
            name: result.to_dict() for name, result in canonical_results.items()
        },
    }

    if not verbose:
        return report

    print("=" * 60)
    print("BENCHMARK PROBLEM SET VERIFICATION")
    print("=" * 60)
    print("\n1. Loading problems...")
    print(f"   Loaded {len(problems)} problems")

    print("\n2. Validating problem structure...")
    if issues:
        print(f"   Found issues in {len(issues)} problems:")
        for pid, prob_issues in issues.items():
            print(f"   {pid}:")
            for issue in prob_issues:
                print(f"      - {issue}")
    else:
        print("   All problems pass structural validation")

    print("\n3. Testing canonical solutions...")
    passed = sum(1 for result in canonical_results.values() if result.all_passed)
    print(f"   Passed: {passed}/{len(canonical_results)}")
    failed = [
        (name, result)
        for name, result in canonical_results.items()
        if not result.all_passed
    ]
    if failed:
        print("   Failed:")
        for name, result in failed:
            print(f"      {name}: {result.error_message or 'Wrong answers'}")

    print_problem_stats()

    print("\n" + "=" * 60)
    print("VERIFICATION COMPLETE")
    print("=" * 60)
    return report


if __name__ == "__main__":
    verify_problem_set()
