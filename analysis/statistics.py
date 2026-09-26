"""
Statistical Tests
=================

Implementations of statistical tests for benchmark analysis.
"""

import math
from dataclasses import dataclass
from typing import List, Tuple


def chi_square_survival(x: float, df: int) -> float:
    """Chi-square upper tail for positive integer degrees of freedom.

    Integer/half-integer incomplete gamma recurrences avoid the inaccurate
    normal approximation, in particular the significance boundary at df=1.
    """
    if df <= 0:
        raise ValueError("degrees of freedom must be positive")
    if math.isnan(x):
        return math.nan
    if x <= 0:
        return 1.0
    if math.isinf(x):
        return 0.0
    z = x / 2
    if z == 0:
        return 1.0
    shape = 0.5 if df % 2 else 1.0
    first = math.erfc(math.sqrt(z)) if df % 2 else math.exp(-z)
    terms = [first]
    while shape < df / 2:
        terms.append(math.exp(shape * math.log(z) - z - math.lgamma(shape + 1)))
        shape += 1
    return min(1.0, math.fsum(terms))


def mcnemar_statistic_pvalue(
    a_only: int, b_only: int, continuity_correction: bool = True
) -> tuple[float, float]:
    """Shared McNemar chi-square policy for experiment metrics and reports."""
    if a_only < 0 or b_only < 0:
        raise ValueError("discordant counts must be nonnegative")
    discordant = a_only + b_only
    if not discordant:
        return 0.0, 1.0
    difference = abs(a_only - b_only)
    if continuity_correction:
        difference = max(0, difference - 1)
    statistic = difference**2 / discordant
    return statistic, chi_square_survival(statistic, 1)


def _beta_fraction(a: float, b: float, x: float) -> float:
    """Evaluate the incomplete beta continued fraction using Lentz's method."""
    tiny = 1e-300
    c = 1.0
    d = 1 - (a + b) * x / (a + 1)
    if abs(d) < tiny:
        d = tiny
    d = 1 / d
    value = d
    for m in range(1, 1001):
        even = m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m))
        odd = -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))
        for coefficient in (even, odd):
            d = 1 + coefficient * d
            c = 1 + coefficient / c
            if abs(d) < tiny:
                d = tiny
            if abs(c) < tiny:
                c = tiny
            d = 1 / d
            delta = d * c
            value *= delta
        if abs(delta - 1) <= 4e-15:
            return value
    raise ArithmeticError("Incomplete beta continued fraction did not converge")


def _regularized_beta(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta, choosing the stable continued-fraction tail."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    factor = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1) / (a + b + 2):
        result = factor * _beta_fraction(a, b, x) / a
    else:
        result = 1 - factor * _beta_fraction(b, a, 1 - x) / b
    return min(1.0, max(0.0, result))


@dataclass
class TestResult:
    """Result of a statistical test."""

    test_name: str
    statistic: float
    p_value: float
    effect_size: float
    effect_size_name: str
    significant: bool
    confidence_interval: Tuple[float, float] = (0.0, 0.0)
    interpretation: str = ""

    def to_dict(self) -> dict:
        return {
            "test_name": self.test_name,
            "statistic": self.statistic,
            "p_value": self.p_value,
            "effect_size": self.effect_size,
            "effect_size_name": self.effect_size_name,
            "significant": self.significant,
            "confidence_interval": self.confidence_interval,
            "interpretation": self.interpretation,
        }


class StatisticalTests:
    """
    Collection of statistical tests for benchmark analysis.

    All tests are implemented from scratch to avoid external dependencies.
    For production use, consider scipy.stats for more robust implementations.
    """

    def __init__(self, alpha: float = 0.05):
        self.alpha = alpha

    # ==========================================================================
    # Primary Tests
    # ==========================================================================

    def mcnemar_test(
        self,
        both_pass: int,
        a_only: int,
        b_only: int,
        both_fail: int,
        continuity_correction: bool = True,
    ) -> TestResult:
        """
        McNemar's test for paired binary data.

        Tests whether the marginal probabilities are equal.

        Args:
            both_pass: Count where both conditions pass
            a_only: Count where only condition A passes
            b_only: Count where only condition B passes
            both_fail: Count where both conditions fail
            continuity_correction: Apply Yates' correction

        Returns:
            TestResult with chi-square statistic and p-value
        """
        b = a_only
        c = b_only

        if b + c == 0:
            return TestResult(
                test_name="McNemar's Test",
                statistic=0.0,
                p_value=1.0,
                effect_size=0.0,
                effect_size_name="Cohen's g",
                significant=False,
                interpretation="No discordant pairs; cannot compute test.",
            )

        chi2, p_value = mcnemar_statistic_pvalue(b, c, continuity_correction)

        # Effect size: Cohen's g
        cohens_g = (b - c) / (b + c)

        # Interpretation
        if abs(cohens_g) < 0.05:
            effect_interp = "negligible"
        elif abs(cohens_g) < 0.15:
            effect_interp = "small"
        elif abs(cohens_g) < 0.25:
            effect_interp = "medium"
        else:
            effect_interp = "large"

        significant = p_value < self.alpha

        if significant:
            if cohens_g > 0:
                interp = f"Condition A significantly better ({effect_interp} effect)"
            else:
                interp = f"Condition B significantly better ({effect_interp} effect)"
        else:
            interp = "No significant difference between conditions"

        return TestResult(
            test_name="McNemar's Test",
            statistic=chi2,
            p_value=p_value,
            effect_size=cohens_g,
            effect_size_name="Cohen's g",
            significant=significant,
            interpretation=interp,
        )

    def chi_square_test(self, observed: List[List[int]]) -> TestResult:
        """
        Chi-square test of independence.

        Tests whether two categorical variables are independent.

        Args:
            observed: 2D list of observed frequencies

        Returns:
            TestResult with chi-square statistic and p-value
        """
        rows = len(observed)
        cols = len(observed[0])

        # Calculate row and column totals
        row_totals = [sum(row) for row in observed]
        col_totals = [sum(observed[i][j] for i in range(rows)) for j in range(cols)]
        total = sum(row_totals)

        if total == 0:
            return TestResult(
                test_name="Chi-Square Test",
                statistic=0.0,
                p_value=1.0,
                effect_size=0.0,
                effect_size_name="Cramér's V",
                significant=False,
                interpretation="No observations",
            )

        # Calculate expected frequencies
        expected = [
            [row_totals[i] * col_totals[j] / total for j in range(cols)]
            for i in range(rows)
        ]

        # Calculate chi-square statistic
        chi2 = 0.0
        for i in range(rows):
            for j in range(cols):
                if expected[i][j] > 0:
                    chi2 += (observed[i][j] - expected[i][j]) ** 2 / expected[i][j]

        # Degrees of freedom
        df = (rows - 1) * (cols - 1)

        # P-value
        p_value = self._chi2_sf(chi2, df)

        # Effect size: Cramér's V
        min_dim = min(rows - 1, cols - 1)
        if min_dim > 0 and total > 0:
            cramers_v = math.sqrt(chi2 / (total * min_dim))
        else:
            cramers_v = 0.0

        significant = p_value < self.alpha

        return TestResult(
            test_name="Chi-Square Test",
            statistic=chi2,
            p_value=p_value,
            effect_size=cramers_v,
            effect_size_name="Cramér's V",
            significant=significant,
            interpretation=f"Variables are {'dependent' if significant else 'independent'}",
        )

    def paired_t_test(self, data_a: List[float], data_b: List[float]) -> TestResult:
        """
        Paired t-test for comparing two related samples.

        Args:
            data_a: Observations from condition A
            data_b: Observations from condition B (same length as data_a)

        Returns:
            TestResult with t-statistic and p-value
        """
        assert len(data_a) == len(data_b), "Data must have same length"

        n = len(data_a)
        if n < 2:
            return TestResult(
                test_name="Paired t-test",
                statistic=0.0,
                p_value=1.0,
                effect_size=0.0,
                effect_size_name="Cohen's d",
                significant=False,
                interpretation="Insufficient data",
            )

        # Differences
        differences = [a - b for a, b in zip(data_a, data_b)]

        # Mean and standard deviation of differences
        mean_diff = sum(differences) / n
        var_diff = sum((d - mean_diff) ** 2 for d in differences) / (n - 1)
        std_diff = math.sqrt(var_diff) if var_diff > 0 else 0.0

        if std_diff == 0:
            return TestResult(
                test_name="Paired t-test",
                statistic=float("inf") if mean_diff != 0 else 0.0,
                p_value=0.0 if mean_diff != 0 else 1.0,
                effect_size=float("inf") if mean_diff != 0 else 0.0,
                effect_size_name="Cohen's d",
                significant=mean_diff != 0,
                interpretation="Zero variance in differences",
            )

        # T-statistic
        t_stat = mean_diff / (std_diff / math.sqrt(n))

        # P-value (two-tailed)
        p_value = 2 * self._t_sf(abs(t_stat), df=n - 1)

        # Effect size: Cohen's d
        cohens_d = mean_diff / std_diff

        significant = p_value < self.alpha

        return TestResult(
            test_name="Paired t-test",
            statistic=t_stat,
            p_value=p_value,
            effect_size=cohens_d,
            effect_size_name="Cohen's d",
            significant=significant,
            confidence_interval=self._confidence_interval_diff(mean_diff, std_diff, n),
            interpretation=f"Mean difference: {mean_diff:.3f} (d={cohens_d:.2f})",
        )

    def proportion_test(
        self, successes_a: int, total_a: int, successes_b: int, total_b: int
    ) -> TestResult:
        """
        Two-proportion z-test.

        Tests whether two proportions are significantly different.

        Args:
            successes_a: Number of successes in group A
            total_a: Total observations in group A
            successes_b: Number of successes in group B
            total_b: Total observations in group B

        Returns:
            TestResult with z-statistic and p-value
        """
        if total_a == 0 or total_b == 0:
            return TestResult(
                test_name="Two-Proportion Z-Test",
                statistic=0.0,
                p_value=1.0,
                effect_size=0.0,
                effect_size_name="Cohen's h",
                significant=False,
                interpretation="Insufficient data",
            )

        p1 = successes_a / total_a
        p2 = successes_b / total_b

        # Pooled proportion
        p_pooled = (successes_a + successes_b) / (total_a + total_b)

        # Standard error
        se = math.sqrt(p_pooled * (1 - p_pooled) * (1 / total_a + 1 / total_b))

        if se == 0:
            return TestResult(
                test_name="Two-Proportion Z-Test",
                statistic=0.0,
                p_value=1.0 if p1 == p2 else 0.0,
                effect_size=0.0,
                effect_size_name="Cohen's h",
                significant=p1 != p2,
                interpretation="Zero standard error",
            )

        # Z-statistic
        z_stat = (p1 - p2) / se

        # P-value (two-tailed)
        p_value = 2 * self._normal_sf(abs(z_stat))

        # Effect size: Cohen's h
        phi1 = 2 * math.asin(math.sqrt(p1))
        phi2 = 2 * math.asin(math.sqrt(p2))
        cohens_h = phi1 - phi2

        significant = p_value < self.alpha

        return TestResult(
            test_name="Two-Proportion Z-Test",
            statistic=z_stat,
            p_value=p_value,
            effect_size=cohens_h,
            effect_size_name="Cohen's h",
            significant=significant,
            interpretation=f"p1={p1:.3f}, p2={p2:.3f}, diff={p1 - p2:+.3f}",
        )

    # ==========================================================================
    # Confidence Intervals
    # ==========================================================================

    def wilson_confidence_interval(
        self, successes: int, total: int, confidence: float = 0.95
    ) -> Tuple[float, float]:
        """
        Wilson score interval for a proportion.

        More accurate than normal approximation, especially for small samples
        or proportions near 0 or 1.
        """
        if total == 0:
            return (0.0, 1.0)

        p = successes / total
        z = self._normal_ppf((1 + confidence) / 2)

        denominator = 1 + z**2 / total
        center = (p + z**2 / (2 * total)) / denominator
        margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denominator

        return (max(0, center - margin), min(1, center + margin))

    def _confidence_interval_diff(
        self, mean_diff: float, std_diff: float, n: int, confidence: float = 0.95
    ) -> Tuple[float, float]:
        """Confidence interval for mean difference."""
        t_crit = self._t_ppf((1 + confidence) / 2, df=n - 1)
        margin = t_crit * std_diff / math.sqrt(n)
        return (mean_diff - margin, mean_diff + margin)

    # ==========================================================================
    # Multiple Testing Correction
    # ==========================================================================

    def bonferroni_correction(
        self, p_values: List[float], alpha: float | None = None
    ) -> List[Tuple[float, bool]]:
        """
        Bonferroni correction for multiple testing.

        Returns adjusted p-values and significance decisions.
        """
        alpha = self.alpha if alpha is None else alpha
        n_tests = len(p_values)
        if n_tests == 0:
            return []
        adjusted_alpha = alpha / n_tests
        return [(min(1.0, p * n_tests), p < adjusted_alpha) for p in p_values]

    def fdr_correction(
        self, p_values: List[float], alpha: float | None = None
    ) -> List[Tuple[float, bool]]:
        """
        Benjamini-Hochberg FDR correction.

        Returns adjusted p-values and significance decisions.
        """
        alpha = self.alpha if alpha is None else alpha
        n = len(p_values)

        # Sort p-values with original indices
        indexed = sorted(enumerate(p_values), key=lambda x: x[1])

        # Calculate adjusted p-values
        adjusted = [0.0] * n
        for rank, (idx, p) in enumerate(indexed, 1):
            adjusted[idx] = p * n / rank

        # Enforce monotonicity in rank order before restoring input order.
        min_so_far = 1.0
        for idx, _p in reversed(indexed):
            min_so_far = min(min_so_far, adjusted[idx])
            adjusted[idx] = min_so_far

        return [(adj_p, adj_p < alpha) for adj_p in adjusted]

    # ==========================================================================
    # Helper Functions (Statistical Distributions)
    # ==========================================================================

    def _chi2_sf(self, x: float, df: int) -> float:
        """Survival function for chi-square distribution."""
        if df <= 0:
            return 1.0
        return chi_square_survival(x, df)

    def _t_sf(self, x: float, df: int) -> float:
        """Student t upper tail via the regularized incomplete beta function."""
        if df <= 0:
            raise ValueError("degrees of freedom must be positive")
        if math.isnan(x):
            return math.nan
        if df == 1:
            return math.atan2(1.0, x) / math.pi
        squared = x * x
        if squared < df:
            tail = 0.5 - 0.5 * _regularized_beta(squared / (df + squared), 0.5, df / 2)
        else:
            tail = 0.5 * _regularized_beta(df / (df + squared), df / 2, 0.5)
        return tail if x >= 0 else 1 - tail

    def _t_ppf(self, p: float, df: int) -> float:
        """Invert the same t distribution used for p-values and intervals."""
        if df <= 0:
            raise ValueError("degrees of freedom must be positive")
        if p <= 0:
            return -math.inf
        if p >= 1:
            return math.inf
        if p == 0.5:
            return 0.0
        tail_probability = min(p, 1 - p)
        low, high = 0.0, 1.0
        while self._t_sf(high, df) > tail_probability:
            high *= 2
        for _ in range(100):
            middle = (low + high) / 2
            if self._t_sf(middle, df) > tail_probability:
                low = middle
            else:
                high = middle
        quantile = (low + high) / 2
        return quantile if p > 0.5 else -quantile

    def _normal_sf(self, x: float) -> float:
        """Survival function for standard normal distribution."""
        return 0.5 * math.erfc(x / math.sqrt(2))

    def _normal_ppf(self, p: float) -> float:
        """Percent point function for standard normal distribution."""
        # Approximation using rational function
        if p <= 0:
            return float("-inf")
        if p >= 1:
            return float("inf")
        if p == 0.5:
            return 0.0

        # Use symmetry
        if p > 0.5:
            return -self._normal_ppf(1 - p)

        t = math.sqrt(-2 * math.log(p))

        # Rational approximation coefficients
        c0, c1, c2 = 2.515517, 0.802853, 0.010328
        d1, d2, d3 = 1.432788, 0.189269, 0.001308

        return -(t - (c0 + c1 * t + c2 * t**2) / (1 + d1 * t + d2 * t**2 + d3 * t**3))


# Convenience functions
def run_mcnemar(both_pass: int, a_only: int, b_only: int, both_fail: int) -> TestResult:
    """Run McNemar's test."""
    return StatisticalTests().mcnemar_test(both_pass, a_only, b_only, both_fail)


def run_chi_square(observed: List[List[int]]) -> TestResult:
    """Run chi-square test."""
    return StatisticalTests().chi_square_test(observed)


def run_paired_t(data_a: List[float], data_b: List[float]) -> TestResult:
    """Run paired t-test."""
    return StatisticalTests().paired_t_test(data_a, data_b)


def run_proportion_test(s_a: int, n_a: int, s_b: int, n_b: int) -> TestResult:
    """Run two-proportion z-test."""
    return StatisticalTests().proportion_test(s_a, n_a, s_b, n_b)
