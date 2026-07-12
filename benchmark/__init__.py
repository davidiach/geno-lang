"""
Geno Benchmark Suite
========================

Provides tools for evaluating LLM-generated solutions against benchmark problems.

Usage:
    from benchmark import load_problems, BenchmarkRunner, summarize_results

    problems = load_problems()
    runner = BenchmarkRunner()
    results = [runner.evaluate_geno(p, solution) for p in problems]
    summary = summarize_results(results, problems)
"""

from benchmark.loader import (
    filter_by_difficulty,
    filter_by_domain,
    get_problems_dir,
    load_all_problems,
    load_problem_by_id,
    print_problem_stats,
    test_canonical_solutions,
    validate_all_problems,
    verify_problem_set,
)
from benchmark.runner import (
    BenchmarkRunner,
    BenchmarkSummary,
    ErrorCategory,
    EvaluationResult,
    TestResult,
    summarize_results,
)
from benchmark.schema import (
    Difficulty,
    Domain,
    Problem,
    TestCase,
    TypeSignature,
    load_problems,
    save_problems,
    validate_problem,
)

__all__ = [
    "BenchmarkRunner",
    "BenchmarkSummary",
    "Difficulty",
    "Domain",
    "ErrorCategory",
    "EvaluationResult",
    "Problem",
    "TestCase",
    "TestResult",
    "TypeSignature",
    "filter_by_difficulty",
    "filter_by_domain",
    "get_problems_dir",
    "load_all_problems",
    "load_problem_by_id",
    "load_problems",
    "print_problem_stats",
    "save_problems",
    "summarize_results",
    "test_canonical_solutions",
    "validate_all_problems",
    "validate_problem",
    "verify_problem_set",
]
