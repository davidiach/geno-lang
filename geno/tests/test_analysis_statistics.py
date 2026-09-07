"""Regression checks for experiment comparisons and multiple testing."""

from itertools import permutations

import pytest

from analysis.analyzer import ResultsAnalyzer
from analysis.statistics import StatisticalTests
from benchmark.runner import EvaluationResult
from experiment.metrics import compare_results


def test_unevaluated_or_failed_program_is_not_a_passing_solution():
    result = EvaluationResult("P1", "geno", "")
    assert not result.all_passed
    result.parsed = result.type_checked = True
    assert not result.all_passed
    result.visible_passed = result.visible_total = 1
    assert result.all_passed
    result.type_checked = False
    assert not result.all_passed


def test_fdr_adjustment_is_invariant_to_input_order():
    stats = StatisticalTests()
    expected = {0.01: 0.03, 0.04: 0.06, 0.9: 0.9}
    for values in permutations(expected):
        results = stats.fdr_correction(list(values))
        assert [p for p, _ in results] == pytest.approx([expected[p] for p in values])
        assert [reject for _, reject in results] == [p == 0.01 for p in values]


def test_bonferroni_returns_adjusted_probabilities():
    result = StatisticalTests().bonferroni_correction([0.01, 0.04, 0.9])
    assert [p for p, _ in result] == pytest.approx([0.03, 0.12, 1.0])
    assert [reject for _, reject in result] == [True, False, False]


@pytest.mark.parametrize("method", ["bonferroni_correction", "fdr_correction"])
def test_multiple_testing_empty_and_explicit_zero_alpha(method):
    correction = getattr(StatisticalTests(), method)
    assert correction([]) == []
    assert correction([0.01], alpha=0.0) == [(0.01, False)]


def test_identical_conditions_do_not_report_significance():
    result = EvaluationResult("P1", "geno", "", visible_total=1)
    comparison = compare_results([result], [result])
    assert comparison.mcnemar_statistic == 0.0
    assert comparison.mcnemar_pvalue == 1.0


def test_empty_comparison_has_no_evidence_of_a_difference():
    comparison = compare_results([], [])
    assert comparison.pass_rate_a == comparison.pass_rate_b == 0.0
    assert comparison.mcnemar_pvalue == 1.0


def test_comparison_rejects_misaligned_problems():
    a = EvaluationResult("P1", "geno", "")
    b = EvaluationResult("P2", "python", "")
    with pytest.raises(ValueError, match="align"):
        compare_results([a], [b])
    with pytest.raises(ValueError, match="length"):
        compare_results([a], [])


def test_analysis_uses_only_complete_language_pairs():
    analyzer = ResultsAnalyzer(
        {
            "generation_results": [
                {"model": "m", "problem_id": "P1", "trial": 0, "language": "geno"},
                {"model": "m", "problem_id": "P1", "trial": 0, "language": "python"},
                {"model": "m", "problem_id": "P2", "trial": 0, "language": "geno"},
            ],
            "evaluation_results": [{"all_passed": True}] * 3,
        }
    )
    analyzer.load_data()
    analyzer._compute_primary_comparison()
    comparison = analyzer.analysis_results.primary_comparison["m"]
    assert comparison["n_problem_trials"] == comparison["unique_problems"] == 1
    assert comparison["geno_pass_rate"] == comparison["python_pass_rate"] == 1.0
    assert comparison["contingency"]["geno_only"] == 0
