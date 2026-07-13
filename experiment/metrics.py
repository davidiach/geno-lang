"""
Evaluation Metrics
==================

Defines metrics for analyzing benchmark results.
"""

import math
from dataclasses import dataclass, field

from benchmark.runner import ErrorCategory, EvaluationResult


@dataclass
class MetricsSummary:
    """Summary metrics for a set of evaluation results."""

    # Sample size
    n_evaluations: int = 0

    # Parse metrics
    parse_success_rate: float = 0.0
    parse_failures: int = 0

    # Type check metrics
    typecheck_success_rate: float = 0.0
    typecheck_failures: int = 0

    # Test metrics
    visible_pass_rate: float = 0.0
    hidden_pass_rate: float = 0.0
    overall_pass_rate: float = 0.0  # All tests pass

    # Error distribution
    error_counts: dict[str, int] = field(default_factory=dict)
    error_rates: dict[str, float] = field(default_factory=dict)

    # Token metrics
    mean_tokens: float = 0.0
    std_tokens: float = 0.0
    min_tokens: int = 0
    max_tokens: int = 0

    # Timing metrics
    mean_execution_ms: float = 0.0
    total_execution_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "n_evaluations": self.n_evaluations,
            "parse_success_rate": self.parse_success_rate,
            "typecheck_success_rate": self.typecheck_success_rate,
            "visible_pass_rate": self.visible_pass_rate,
            "hidden_pass_rate": self.hidden_pass_rate,
            "overall_pass_rate": self.overall_pass_rate,
            "error_counts": self.error_counts,
            "error_rates": self.error_rates,
            "mean_tokens": self.mean_tokens,
            "std_tokens": self.std_tokens,
            "mean_execution_ms": self.mean_execution_ms,
        }


def compute_metrics(results: list[EvaluationResult]) -> MetricsSummary:
    """Compute summary metrics from evaluation results."""
    if not results:
        return MetricsSummary()

    summary = MetricsSummary()
    summary.n_evaluations = len(results)

    # Parse metrics
    parsed = sum(1 for r in results if r.parsed)
    summary.parse_success_rate = parsed / len(results)
    summary.parse_failures = len(results) - parsed

    # Type check metrics
    type_checked = sum(1 for r in results if r.type_checked)
    summary.typecheck_success_rate = type_checked / len(results)
    summary.typecheck_failures = len(results) - type_checked

    # Test metrics
    total_visible = sum(r.visible_total for r in results)
    total_hidden = sum(r.hidden_total for r in results)
    passed_visible = sum(r.visible_passed for r in results)
    passed_hidden = sum(r.hidden_passed for r in results)

    summary.visible_pass_rate = (
        passed_visible / total_visible if total_visible > 0 else 0.0
    )
    summary.hidden_pass_rate = passed_hidden / total_hidden if total_hidden > 0 else 0.0

    all_passed = sum(1 for r in results if r.all_passed)
    summary.overall_pass_rate = all_passed / len(results)

    # Error distribution
    for category in ErrorCategory:
        count = sum(1 for r in results if r.error_category == category)
        summary.error_counts[category.value] = count
        summary.error_rates[category.value] = count / len(results)

    # Token metrics
    tokens = [r.solution_tokens for r in results]
    summary.mean_tokens = sum(tokens) / len(tokens)
    n = len(tokens)
    summary.std_tokens = (
        math.sqrt(sum((t - summary.mean_tokens) ** 2 for t in tokens) / (n - 1))
        if n > 1
        else 0.0
    )
    summary.min_tokens = min(tokens)
    summary.max_tokens = max(tokens)

    # Timing metrics
    times = [r.total_execution_time_ms for r in results]
    summary.mean_execution_ms = sum(times) / len(times)
    summary.total_execution_ms = sum(times)

    return summary


@dataclass
class ComparisonMetrics:
    """Metrics comparing two conditions (e.g., Geno vs Python)."""

    condition_a: str = ""
    condition_b: str = ""

    # Pass rate comparison
    pass_rate_a: float = 0.0
    pass_rate_b: float = 0.0
    pass_rate_diff: float = 0.0  # A - B
    relative_improvement: float = 0.0  # (A - B) / B

    # Paired statistics
    n_both_pass: int = 0
    n_a_only: int = 0
    n_b_only: int = 0
    n_both_fail: int = 0

    # McNemar's test components
    mcnemar_statistic: float = 0.0
    mcnemar_pvalue: float = 0.0

    # Error reduction
    syntax_error_reduction: float = 0.0
    type_error_reduction: float = 0.0
    runtime_error_reduction: float = 0.0

    def to_dict(self) -> dict:
        return {
            "condition_a": self.condition_a,
            "condition_b": self.condition_b,
            "pass_rate_a": self.pass_rate_a,
            "pass_rate_b": self.pass_rate_b,
            "pass_rate_diff": self.pass_rate_diff,
            "relative_improvement": self.relative_improvement,
            "contingency": {
                "both_pass": self.n_both_pass,
                "a_only": self.n_a_only,
                "b_only": self.n_b_only,
                "both_fail": self.n_both_fail,
            },
            "mcnemar_statistic": self.mcnemar_statistic,
            "mcnemar_pvalue": self.mcnemar_pvalue,
            "error_reduction": {
                "syntax": self.syntax_error_reduction,
                "type": self.type_error_reduction,
                "runtime": self.runtime_error_reduction,
            },
        }


def compare_results(
    results_a: list[EvaluationResult],
    results_b: list[EvaluationResult],
    condition_a: str = "A",
    condition_b: str = "B",
) -> ComparisonMetrics:
    """
    Compare two sets of results (paired by problem).

    Assumes results_a and results_b are aligned by problem ID.
    """
    assert len(results_a) == len(results_b), "Result lists must be same length"

    comparison = ComparisonMetrics()
    comparison.condition_a = condition_a
    comparison.condition_b = condition_b

    # Compute pass rates
    passed_a = sum(1 for r in results_a if r.all_passed)
    passed_b = sum(1 for r in results_b if r.all_passed)
    n = len(results_a)

    comparison.pass_rate_a = passed_a / n
    comparison.pass_rate_b = passed_b / n
    comparison.pass_rate_diff = comparison.pass_rate_a - comparison.pass_rate_b

    if comparison.pass_rate_b > 0:
        comparison.relative_improvement = (
            comparison.pass_rate_diff / comparison.pass_rate_b
        )
    else:
        comparison.relative_improvement = (
            float("inf") if comparison.pass_rate_a > 0 else 0.0
        )

    # Contingency table for McNemar's test
    for ra, rb in zip(results_a, results_b):
        a_pass = ra.all_passed
        b_pass = rb.all_passed

        if a_pass and b_pass:
            comparison.n_both_pass += 1
        elif a_pass and not b_pass:
            comparison.n_a_only += 1
        elif not a_pass and b_pass:
            comparison.n_b_only += 1
        else:
            comparison.n_both_fail += 1

    # McNemar's test (with continuity correction)
    b = comparison.n_a_only  # A succeeds, B fails
    c = comparison.n_b_only  # B succeeds, A fails

    if b + c > 0:
        comparison.mcnemar_statistic = (abs(b - c) - 1) ** 2 / (b + c)
        # Approximate p-value using chi-squared survival function (1 df).
        # For 1 df: chi2_sf(x, 1) = erfc(sqrt(x/2)) = erfc(sqrt(x) / sqrt(2)).
        # This avoids a scipy dependency.
        comparison.mcnemar_pvalue = math.erfc(
            math.sqrt(comparison.mcnemar_statistic) / math.sqrt(2)
        )

    # Error reduction analysis
    def count_error(results, category):
        return sum(1 for r in results if r.error_category == category)

    syntax_a = count_error(results_a, ErrorCategory.SYNTAX)
    syntax_b = count_error(results_b, ErrorCategory.SYNTAX)
    if syntax_b > 0:
        comparison.syntax_error_reduction = (syntax_b - syntax_a) / syntax_b

    type_a = count_error(results_a, ErrorCategory.TYPE)
    type_b = count_error(results_b, ErrorCategory.TYPE)
    if type_b > 0:
        comparison.type_error_reduction = (type_b - type_a) / type_b

    runtime_a = count_error(results_a, ErrorCategory.RUNTIME)
    runtime_b = count_error(results_b, ErrorCategory.RUNTIME)
    if runtime_b > 0:
        comparison.runtime_error_reduction = (runtime_b - runtime_a) / runtime_b

    return comparison


@dataclass
class DifficultyBreakdown:
    """Metrics broken down by difficulty level."""

    difficulty: str = ""
    n_problems: int = 0
    pass_rate: float = 0.0
    mean_visible_rate: float = 0.0
    mean_hidden_rate: float = 0.0
    error_distribution: dict[str, float] = field(default_factory=dict)


def compute_difficulty_breakdown(
    results: list[EvaluationResult], problem_difficulties: dict[str, str]
) -> list[DifficultyBreakdown]:
    """Compute metrics broken down by difficulty."""
    # Group by difficulty
    by_difficulty: dict[str, list[EvaluationResult]] = {}
    for r in results:
        diff = problem_difficulties.get(r.problem_id, "unknown")
        if diff not in by_difficulty:
            by_difficulty[diff] = []
        by_difficulty[diff].append(r)

    breakdowns = []
    for diff in ["trivial", "easy", "medium", "hard", "expert"]:
        if diff not in by_difficulty:
            continue

        diff_results = by_difficulty[diff]
        breakdown = DifficultyBreakdown()
        breakdown.difficulty = diff
        breakdown.n_problems = len(diff_results)

        # Pass rate
        passed = sum(1 for r in diff_results if r.all_passed)
        breakdown.pass_rate = passed / len(diff_results)

        # Mean test rates
        visible_rates = [
            r.visible_passed / r.visible_total if r.visible_total > 0 else 0
            for r in diff_results
        ]
        hidden_rates = [
            r.hidden_passed / r.hidden_total if r.hidden_total > 0 else 0
            for r in diff_results
        ]

        breakdown.mean_visible_rate = sum(visible_rates) / len(visible_rates)
        breakdown.mean_hidden_rate = sum(hidden_rates) / len(hidden_rates)

        # Error distribution
        for category in ErrorCategory:
            count = sum(1 for r in diff_results if r.error_category == category)
            breakdown.error_distribution[category.value] = count / len(diff_results)

        breakdowns.append(breakdown)

    return breakdowns


def format_metrics_report(
    geno_metrics: MetricsSummary,
    python_metrics: MetricsSummary,
    comparison: ComparisonMetrics,
) -> str:
    """Format a human-readable metrics report."""
    lines = []
    lines.append("=" * 70)
    lines.append("BENCHMARK EVALUATION REPORT")
    lines.append("=" * 70)

    lines.append("\n## Overall Results\n")
    lines.append(f"{'Metric':<30} {'Geno':>15} {'Python':>15} {'Diff':>10}")
    lines.append("-" * 70)

    lines.append(
        f"{'Parse Success Rate':<30} {geno_metrics.parse_success_rate:>14.1%} {python_metrics.parse_success_rate:>14.1%} {comparison.pass_rate_diff:>+9.1%}"
    )
    lines.append(
        f"{'Type Check Success Rate':<30} {geno_metrics.typecheck_success_rate:>14.1%} {python_metrics.typecheck_success_rate:>14.1%}"
    )
    lines.append(
        f"{'Visible Test Pass Rate':<30} {geno_metrics.visible_pass_rate:>14.1%} {python_metrics.visible_pass_rate:>14.1%}"
    )
    lines.append(
        f"{'Hidden Test Pass Rate':<30} {geno_metrics.hidden_pass_rate:>14.1%} {python_metrics.hidden_pass_rate:>14.1%}"
    )
    lines.append(
        f"{'Overall Pass Rate':<30} {geno_metrics.overall_pass_rate:>14.1%} {python_metrics.overall_pass_rate:>14.1%} {comparison.pass_rate_diff:>+9.1%}"
    )

    lines.append("\n## Contingency Table (Paired Results)\n")
    lines.append(f"{'':20} {'Python Pass':>15} {'Python Fail':>15}")
    lines.append(
        f"{'Geno Pass':<20} {comparison.n_both_pass:>15} {comparison.n_a_only:>15}"
    )
    lines.append(
        f"{'Geno Fail':<20} {comparison.n_b_only:>15} {comparison.n_both_fail:>15}"
    )

    lines.append(f"\nMcNemar's χ² statistic: {comparison.mcnemar_statistic:.3f}")

    lines.append("\n## Error Distribution\n")
    lines.append(f"{'Error Type':<20} {'Geno':>15} {'Python':>15} {'Reduction':>15}")
    lines.append("-" * 65)

    for category in [
        "syntax",
        "type",
        "runtime",
        "wrong_answer",
        "timeout",
        "incomplete",
    ]:
        g_rate = geno_metrics.error_rates.get(category, 0)
        p_rate = python_metrics.error_rates.get(category, 0)
        reduction = (p_rate - g_rate) / p_rate * 100 if p_rate > 0 else 0
        lines.append(
            f"{category:<20} {g_rate:>14.1%} {p_rate:>14.1%} {reduction:>+14.1f}%"
        )

    lines.append("\n## Token Statistics\n")
    lines.append(f"{'Metric':<30} {'Geno':>15} {'Python':>15}")
    lines.append("-" * 60)
    lines.append(
        f"{'Mean Tokens':<30} {geno_metrics.mean_tokens:>15.1f} {python_metrics.mean_tokens:>15.1f}"
    )
    lines.append(
        f"{'Std Tokens':<30} {geno_metrics.std_tokens:>15.1f} {python_metrics.std_tokens:>15.1f}"
    )
    lines.append(
        f"{'Min Tokens':<30} {geno_metrics.min_tokens:>15} {python_metrics.min_tokens:>15}"
    )
    lines.append(
        f"{'Max Tokens':<30} {geno_metrics.max_tokens:>15} {python_metrics.max_tokens:>15}"
    )

    lines.append("\n" + "=" * 70)

    return "\n".join(lines)
