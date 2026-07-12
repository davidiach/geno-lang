"""
Results Analyzer
================

Core analysis functionality for benchmark experiment results.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Preserve direct `python analysis/analyzer.py ...` usage.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.runner import ErrorCategory

JsonDict = dict[str, Any]
JsonList = list[JsonDict]


@dataclass
class AnalysisResults:
    """Container for all analysis results."""

    # Summary statistics
    summary_stats: dict[str, Any] = field(default_factory=dict)

    # Primary analysis
    primary_comparison: dict[str, Any] = field(default_factory=dict)

    # Secondary analyses
    error_analysis: dict[str, Any] = field(default_factory=dict)
    difficulty_analysis: dict[str, Any] = field(default_factory=dict)
    domain_analysis: dict[str, Any] = field(default_factory=dict)

    # Statistical tests
    statistical_tests: dict[str, Any] = field(default_factory=dict)

    # Per-problem results
    problem_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_stats": self.summary_stats,
            "primary_comparison": self.primary_comparison,
            "error_analysis": self.error_analysis,
            "difficulty_analysis": self.difficulty_analysis,
            "domain_analysis": self.domain_analysis,
            "statistical_tests": self.statistical_tests,
            "problem_results": self.problem_results,
        }


class ResultsAnalyzer:
    """
    Analyzes benchmark experiment results.

    Usage:
        analyzer = ResultsAnalyzer("results/experiment_001")
        analyzer.load_data()
        results = analyzer.run_all_analyses()
    """

    def __init__(self, results_source: Any) -> None:
        self.results_source = results_source
        self.results_dir = (
            Path(results_source)
            if isinstance(results_source, str | os.PathLike)
            else None
        )
        self.config: JsonDict = {}
        self.generations: JsonList = []
        self.evaluations: JsonList = []
        self.metrics: JsonDict = {}
        self.analysis_results = AnalysisResults()
        self._difficulty_by_problem_id: dict[str, str] | None = None
        self._domain_by_problem_id: dict[str, str] | None = None

    def load_data(self) -> None:
        """Load all experiment data from results directory."""
        if isinstance(self.results_source, dict):
            config = self.results_source.get("config", {})
            self.config = config if isinstance(config, dict) else {}

            generations = self.results_source.get(
                "generation_results",
                self.results_source.get("generations", []),
            )
            self.generations = generations if isinstance(generations, list) else []

            evaluations = self.results_source.get(
                "evaluation_results",
                self.results_source.get("evaluations", []),
            )
            self.evaluations = evaluations if isinstance(evaluations, list) else []

            metrics = self.results_source.get("metrics", {})
            self.metrics = metrics if isinstance(metrics, dict) else {}
            return

        if self.results_dir is None:
            raise ValueError("results_source must be a mapping or a results path")

        # Load config
        config_path = self.results_dir / "config.json"
        if config_path.exists():
            with open(config_path) as f:
                self.config = json.load(f)

        # Load generations
        gen_path = self.results_dir / "generations.json"
        if gen_path.exists():
            with open(gen_path) as f:
                self.generations = json.load(f)

        # Load evaluations
        eval_path = self.results_dir / "evaluations.json"
        if eval_path.exists():
            with open(eval_path) as f:
                self.evaluations = json.load(f)

        # Load pre-computed metrics
        metrics_path = self.results_dir / "metrics.json"
        if metrics_path.exists():
            with open(metrics_path) as f:
                self.metrics = json.load(f)

    def run_all_analyses(self) -> AnalysisResults:
        """Run all analyses and return results."""
        if (
            not self.config
            and not self.generations
            and not self.evaluations
            and not self.metrics
        ):
            self.load_data()
        self._compute_summary_stats()
        self._compute_primary_comparison()
        self._compute_error_analysis()
        self._compute_difficulty_analysis()
        self._compute_domain_analysis()
        self._compute_problem_level_results()

        return self.analysis_results

    def compute_summary(self) -> AnalysisResults:
        """Backward-compatible alias for running analyses."""
        return self.run_all_analyses()

    def _compute_summary_stats(self) -> None:
        """Compute summary statistics for each condition."""
        # Group evaluations by model and language
        by_condition: dict[tuple[str, str], JsonList] = {}

        for gen, eval_res in zip(self.generations, self.evaluations):
            key = (gen.get("model", "unknown"), gen.get("language", "unknown"))
            if key not in by_condition:
                by_condition[key] = []
            by_condition[key].append(eval_res)

        stats: dict[str, dict[str, float | int]] = {}
        for (model, language), evals in by_condition.items():
            n = len(evals)
            parsed = sum(1 for e in evals if e.get("parsed", False))
            type_checked = sum(1 for e in evals if e.get("type_checked", False))
            all_passed = sum(1 for e in evals if e.get("all_passed", False))

            total_visible = sum(e.get("visible_total", 0) for e in evals)
            passed_visible = sum(e.get("visible_passed", 0) for e in evals)
            total_hidden = sum(e.get("hidden_total", 0) for e in evals)
            passed_hidden = sum(e.get("hidden_passed", 0) for e in evals)

            stats[f"{model}_{language}"] = {
                "n": n,
                "parse_rate": parsed / n if n > 0 else 0,
                "typecheck_rate": type_checked / n if n > 0 else 0,
                "all_pass_rate": all_passed / n if n > 0 else 0,
                "visible_pass_rate": passed_visible / total_visible
                if total_visible > 0
                else 0,
                "hidden_pass_rate": passed_hidden / total_hidden
                if total_hidden > 0
                else 0,
            }

        self.analysis_results.summary_stats = stats

    def _compute_primary_comparison(self) -> None:
        """Compute primary Geno vs Python comparison."""
        # Get unique models
        models: set[str] = set()
        for gen in self.generations:
            models.add(gen.get("model", "unknown"))

        comparisons: dict[str, dict[str, Any]] = {}
        for model in models:
            # Pair results by problem and trial. Experiments can run multiple
            # trials per condition; each trial is a separate paired observation.
            by_problem_trial: dict[tuple[str, Any], dict[str, JsonDict]] = {}
            for gen, eval_res in zip(self.generations, self.evaluations):
                if gen.get("model") != model:
                    continue

                problem_id = str(gen.get("problem_id", "unknown"))
                trial = gen.get("trial", 0)
                language = str(gen.get("language", "unknown"))

                pair_key = (problem_id, trial)
                if pair_key not in by_problem_trial:
                    by_problem_trial[pair_key] = {}
                by_problem_trial[pair_key][language] = eval_res

            # Build contingency table
            both_pass = 0
            geno_only = 0
            python_only = 0
            both_fail = 0

            for _pair_key, results in by_problem_trial.items():
                g_pass = results.get("geno", {}).get("all_passed", False)
                p_pass = results.get("python", {}).get("all_passed", False)

                if g_pass and p_pass:
                    both_pass += 1
                elif g_pass and not p_pass:
                    geno_only += 1
                elif not g_pass and p_pass:
                    python_only += 1
                else:
                    both_fail += 1

            n = both_pass + geno_only + python_only + both_fail
            g_pass_rate = (both_pass + geno_only) / n if n > 0 else 0
            p_pass_rate = (both_pass + python_only) / n if n > 0 else 0

            # McNemar's test statistic
            b = geno_only
            c = python_only
            if b + c > 0:
                mcnemar_stat = (abs(b - c) - 1) ** 2 / (b + c)
            else:
                mcnemar_stat = 0

            # Cohen's g effect size
            if b + c > 0:
                cohens_g = (b - c) / (b + c)
            else:
                cohens_g = 0

            comparisons[model] = {
                "n_problems": n,
                "n_problem_trials": n,
                "unique_problems": len(
                    {problem_id for problem_id, _trial in by_problem_trial}
                ),
                "geno_pass_rate": g_pass_rate,
                "python_pass_rate": p_pass_rate,
                "difference": g_pass_rate - p_pass_rate,
                "relative_improvement": (g_pass_rate - p_pass_rate) / p_pass_rate
                if p_pass_rate > 0
                else 0,
                "contingency": {
                    "both_pass": both_pass,
                    "geno_only": geno_only,
                    "python_only": python_only,
                    "both_fail": both_fail,
                },
                "mcnemar_statistic": mcnemar_stat,
                "cohens_g": cohens_g,
            }

        self.analysis_results.primary_comparison = comparisons

    def _compute_error_analysis(self) -> None:
        """Analyze error category distributions."""
        # Group by model and language
        error_counts: dict[str, dict[str, int]] = {}

        for gen, eval_res in zip(self.generations, self.evaluations):
            key = f"{gen.get('model')}_{gen.get('language')}"
            if key not in error_counts:
                error_counts[key] = {cat.value: 0 for cat in ErrorCategory}

            error_cat = eval_res.get("error_category", "none")
            error_counts[key][error_cat] = error_counts[key].get(error_cat, 0) + 1

        # Compute rates
        error_rates: dict[str, dict[str, float]] = {}
        for key, counts in error_counts.items():
            total = sum(counts.values())
            error_rates[key] = {
                cat: count / total if total > 0 else 0 for cat, count in counts.items()
            }

        # Compute error reductions (Geno vs Python)
        reductions: dict[str, dict[str, float]] = {}
        models = {str(gen.get("model", "unknown")) for gen in self.generations}

        for model in models:
            g_key = f"{model}_geno"
            p_key = f"{model}_python"

            if g_key in error_rates and p_key in error_rates:
                reductions[model] = {}
                for cat in ["syntax", "type", "runtime", "wrong_answer"]:
                    g_rate = error_rates[g_key].get(cat, 0)
                    p_rate = error_rates[p_key].get(cat, 0)
                    if p_rate > 0:
                        reductions[model][cat] = (p_rate - g_rate) / p_rate
                    else:
                        reductions[model][cat] = 0

        self.analysis_results.error_analysis = {
            "counts": error_counts,
            "rates": error_rates,
            "reductions": reductions,
        }

    def _compute_difficulty_analysis(self) -> None:
        """Analyze results by difficulty level."""
        # Need problem difficulty mapping - extract from evaluations
        by_difficulty: dict[str, dict[str, JsonList]] = {}

        for gen, eval_res in zip(self.generations, self.evaluations):
            problem_id = str(gen.get("problem_id", ""))
            difficulty = self._infer_difficulty(problem_id)
            if difficulty == "unknown":
                difficulty = self._difficulty_value(gen.get("difficulty")) or "unknown"

            language = gen.get("language", "unknown")
            model = gen.get("model", "unknown")
            key = f"{model}_{language}"

            if difficulty not in by_difficulty:
                by_difficulty[difficulty] = {}
            if key not in by_difficulty[difficulty]:
                by_difficulty[difficulty][key] = []

            by_difficulty[difficulty][key].append(eval_res)

        # Compute pass rates by difficulty
        difficulty_stats: dict[str, dict[str, dict[str, float | int]]] = {}
        for difficulty, conditions in by_difficulty.items():
            difficulty_stats[difficulty] = {}
            for key, evals in conditions.items():
                n = len(evals)
                passed = sum(1 for e in evals if e.get("all_passed", False))
                difficulty_stats[difficulty][key] = {
                    "n": n,
                    "pass_rate": passed / n if n > 0 else 0,
                }

        self.analysis_results.difficulty_analysis = difficulty_stats

    def _infer_difficulty(self, problem_id: str) -> str:
        """Return difficulty from benchmark metadata."""
        if not problem_id:
            return "unknown"
        return self._problem_difficulties().get(str(problem_id), "unknown")

    @staticmethod
    def _difficulty_value(value: Any) -> str | None:
        """Normalize a difficulty value from generated result metadata."""
        if hasattr(value, "value"):
            value = value.value
        return value if isinstance(value, str) and value else None

    def _problem_difficulties(self) -> dict[str, str]:
        """Load benchmark problem difficulties once for analysis lookups."""
        if self._difficulty_by_problem_id is None:
            try:
                from benchmark.loader import load_all_problems

                self._difficulty_by_problem_id = {
                    problem.id: problem.difficulty.value
                    for problem in load_all_problems()
                }
            except Exception:
                self._difficulty_by_problem_id = {}
        return self._difficulty_by_problem_id

    def _compute_domain_analysis(self) -> None:
        """Analyze results by problem domain."""
        by_domain: dict[str, dict[str, JsonList]] = {}

        for gen, eval_res in zip(self.generations, self.evaluations):
            problem_id = str(gen.get("problem_id", ""))
            domain = self._infer_domain(problem_id)
            if domain == "unknown":
                domain = self._domain_value(gen.get("domain")) or "unknown"

            language = gen.get("language", "unknown")
            model = gen.get("model", "unknown")
            key = f"{model}_{language}"

            if domain not in by_domain:
                by_domain[domain] = {}
            if key not in by_domain[domain]:
                by_domain[domain][key] = []

            by_domain[domain][key].append(eval_res)

        domain_stats: dict[str, dict[str, dict[str, float | int]]] = {}
        for domain, conditions in by_domain.items():
            domain_stats[domain] = {}
            for key, evals in conditions.items():
                n = len(evals)
                passed = sum(1 for e in evals if e.get("all_passed", False))
                domain_stats[domain][key] = {
                    "n": n,
                    "pass_rate": passed / n if n > 0 else 0,
                }

        self.analysis_results.domain_analysis = domain_stats

    def _infer_domain(self, problem_id: str) -> str:
        """Return problem domain from benchmark metadata."""
        if not problem_id:
            return "unknown"
        return self._problem_domains().get(str(problem_id), "unknown")

    @staticmethod
    def _domain_value(value: Any) -> str | None:
        """Normalize a domain value from generated result metadata."""
        if hasattr(value, "value"):
            value = value.value
        return value if isinstance(value, str) and value else None

    def _problem_domains(self) -> dict[str, str]:
        """Load benchmark problem domains once for analysis lookups."""
        if self._domain_by_problem_id is None:
            try:
                from benchmark.loader import load_all_problems

                self._domain_by_problem_id = {
                    problem.id: problem.domain.value for problem in load_all_problems()
                }
            except Exception:
                self._domain_by_problem_id = {}
        return self._domain_by_problem_id

    def _compute_problem_level_results(self) -> None:
        """Compute per-problem comparison results."""
        # Group by problem
        by_problem: dict[str, dict[str, Any]] = {}

        for gen, eval_res in zip(self.generations, self.evaluations):
            problem_id = str(gen.get("problem_id", "unknown"))
            model = str(gen.get("model", "unknown"))
            language = str(gen.get("language", "unknown"))

            if problem_id not in by_problem:
                by_problem[problem_id] = {"problem_id": problem_id}

            key = f"{model}_{language}"
            by_problem[problem_id][key] = {
                "passed": eval_res.get("all_passed", False),
                "pass_rate": eval_res.get("pass_rate", 0),
                "error": eval_res.get("error_category", "none"),
            }

        self.analysis_results.problem_results = list(by_problem.values())

    def save_analysis(self, output_path: str | None = None) -> None:
        """Save analysis results to JSON."""
        if output_path:
            path = Path(output_path)
        elif self.results_dir is not None:
            path = self.results_dir / "analysis.json"
        else:
            path = Path("analysis.json")

        with open(path, "w") as f:
            json.dump(self.analysis_results.to_dict(), f, indent=2)

        print(f"Analysis saved to {path}")

    def get_summary_table(self) -> str:
        """Generate a summary table as text."""
        lines = []
        lines.append("=" * 80)
        lines.append("ANALYSIS SUMMARY")
        lines.append("=" * 80)

        # Summary stats
        lines.append("\n## Summary Statistics\n")
        lines.append(f"{'Condition':<30} {'N':>6} {'Parse':>8} {'Type':>8} {'Pass':>8}")
        lines.append("-" * 60)

        for condition, stats in self.analysis_results.summary_stats.items():
            lines.append(
                f"{condition:<30} {stats['n']:>6} "
                f"{stats['parse_rate']:>7.1%} {stats['typecheck_rate']:>7.1%} "
                f"{stats['all_pass_rate']:>7.1%}"
            )

        # Primary comparison
        lines.append("\n## Primary Comparison (Geno vs Python)\n")
        for model, comp in self.analysis_results.primary_comparison.items():
            lines.append(f"\nModel: {model}")
            lines.append(f"  Geno pass rate: {comp['geno_pass_rate']:.1%}")
            lines.append(f"  Python pass rate: {comp['python_pass_rate']:.1%}")
            lines.append(f"  Difference: {comp['difference']:+.1%}")
            lines.append(f"  Relative improvement: {comp['relative_improvement']:+.1%}")
            lines.append(f"  McNemar χ²: {comp['mcnemar_statistic']:.3f}")
            lines.append(f"  Cohen's g: {comp['cohens_g']:.3f}")

            cont = comp["contingency"]
            lines.append(
                f"  Contingency: Both pass={cont['both_pass']}, "
                f"G only={cont['geno_only']}, "
                f"P only={cont['python_only']}, "
                f"Both fail={cont['both_fail']}"
            )

        # Error analysis
        if self.analysis_results.error_analysis.get("reductions"):
            lines.append("\n## Error Reductions\n")
            for model, reductions in self.analysis_results.error_analysis[
                "reductions"
            ].items():
                lines.append(f"\nModel: {model}")
                for cat, reduction in reductions.items():
                    lines.append(f"  {cat}: {reduction:+.1%}")

        # Difficulty analysis
        if self.analysis_results.difficulty_analysis:
            lines.append("\n## By Difficulty\n")
            for difficulty in ["trivial", "easy", "medium", "hard", "expert"]:
                if difficulty in self.analysis_results.difficulty_analysis:
                    stats = self.analysis_results.difficulty_analysis[difficulty]
                    lines.append(f"\n{difficulty.upper()}")
                    for condition, data in stats.items():
                        lines.append(
                            f"  {condition}: {data['pass_rate']:.1%} (n={data['n']})"
                        )

        # Domain analysis
        if self.analysis_results.domain_analysis:
            lines.append("\n## By Domain\n")
            for domain in sorted(self.analysis_results.domain_analysis):
                stats = self.analysis_results.domain_analysis[domain]
                lines.append(f"\n{domain.upper()}")
                for condition, data in stats.items():
                    lines.append(
                        f"  {condition}: {data['pass_rate']:.1%} (n={data['n']})"
                    )

        lines.append("\n" + "=" * 80)

        return "\n".join(lines)


def analyze_results(results_dir: str) -> AnalysisResults:
    """Convenience function to analyze results."""
    analyzer = ResultsAnalyzer(results_dir)
    analyzer.load_data()
    results = analyzer.run_all_analyses()
    analyzer.save_analysis()
    print(analyzer.get_summary_table())
    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        analyze_results(sys.argv[1])
    else:
        print("Usage: python analyzer.py <results_dir>")
