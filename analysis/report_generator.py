"""
Report Generator
================

Generates comprehensive reports from benchmark analysis results.
"""

import sys
from datetime import datetime
from pathlib import Path

# Preserve direct `python analysis/report_generator.py ...` usage.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.analyzer import AnalysisResults, ResultsAnalyzer
from analysis.statistics import StatisticalTests
from analysis.visualizations import Visualizer


class ReportGenerator:
    """
    Generates formatted reports from analysis results.

    Supports multiple output formats:
    - Plain text (terminal/txt file)
    - Markdown (for documentation)
    - LaTeX (for academic papers)
    """

    def __init__(self, analyzer: ResultsAnalyzer | None = None):
        self.analyzer = analyzer
        self.visualizer = Visualizer()
        self.stats = StatisticalTests()
        self.results: AnalysisResults | None = None

        if analyzer:
            self.results = analyzer.analysis_results

    def load_results(self, results_dir: str):
        """Load results from a directory."""
        self.analyzer = ResultsAnalyzer(results_dir)
        self.analyzer.load_data()
        self.results = self.analyzer.run_all_analyses()

    def generate_full_report(self, output_path: str | None = None) -> str:
        """Generate a complete report."""
        sections = []

        sections.append(self._generate_header())
        sections.append(self._generate_executive_summary())
        sections.append(self._generate_methodology_section())
        sections.append(self._generate_results_section())
        sections.append(self._generate_statistical_analysis())
        sections.append(self._generate_discussion())
        sections.append(self._generate_appendix())

        report = "\n\n".join(sections)

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"Report saved to {output_path}")

        return report

    def generate_summary(self) -> str:
        """Generate a concise summary report."""
        if not self.analyzer:
            return "No results available."
        return self.analyzer.get_summary_table()

    def _generate_header(self) -> str:
        """Generate report header."""
        lines = []
        lines.append("╔" + "═" * 78 + "╗")
        lines.append("║" + " " * 78 + "║")
        lines.append("║" + "GENOTYPE BENCHMARK EVALUATION REPORT".center(78) + "║")
        lines.append(
            "║" + "LLM-Native Programming Language Assessment".center(78) + "║"
        )
        lines.append("║" + " " * 78 + "║")
        lines.append(
            "║"
            + f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}".center(78)
            + "║"
        )
        lines.append("║" + " " * 78 + "║")
        lines.append("╚" + "═" * 78 + "╝")
        return "\n".join(lines)

    def _generate_executive_summary(self) -> str:
        """Generate executive summary section."""
        lines = []
        lines.append("=" * 80)
        lines.append("EXECUTIVE SUMMARY")
        lines.append("=" * 80)
        lines.append("")

        if not self.results:
            return "\n".join(lines) + "No results available."

        # Key findings
        lines.append("KEY FINDINGS")
        lines.append("-" * 40)

        # Extract main comparison results
        for model, comparison in self.results.primary_comparison.items():
            g_rate = comparison["geno_pass_rate"]
            p_rate = comparison["python_pass_rate"]
            diff = comparison["difference"]
            rel_imp = comparison["relative_improvement"]

            lines.append(f"\nModel: {model}")
            lines.append(f"  • Geno pass rate: {g_rate:.1%}")
            lines.append(f"  • Python pass rate: {p_rate:.1%}")
            lines.append(f"  • Absolute improvement: {diff:+.1%}")
            lines.append(f"  • Relative improvement: {rel_imp:+.1%}")

            # Statistical significance
            cont = comparison["contingency"]
            test_result = self.stats.mcnemar_test(
                cont["both_pass"],
                cont["geno_only"],
                cont["python_only"],
                cont["both_fail"],
            )
            lines.append(f"  • Statistical significance: p={test_result.p_value:.4f}")
            lines.append(f"    ({test_result.interpretation})")

        # Error reduction highlights
        if self.results.error_analysis.get("reductions"):
            lines.append("\nERROR REDUCTION HIGHLIGHTS")
            lines.append("-" * 40)
            for model, reductions in self.results.error_analysis["reductions"].items():
                lines.append(f"\nModel: {model}")
                for error_type, reduction in reductions.items():
                    if reduction > 0:
                        lines.append(
                            f"  • {error_type.capitalize()} errors: {reduction:+.1%}"
                        )

        return "\n".join(lines)

    def _generate_methodology_section(self) -> str:
        """Generate methodology summary."""
        lines = []
        lines.append("=" * 80)
        lines.append("METHODOLOGY")
        lines.append("=" * 80)
        lines.append("")

        lines.append("EXPERIMENTAL DESIGN")
        lines.append("-" * 40)
        lines.append("""
This evaluation compares code generation quality between Geno (an LLM-native
programming language) and Python (baseline) across a standardized benchmark of
programming problems.

Design: Within-subjects comparative study
• Each problem solved in both languages
• Paired comparison enables McNemar's test
• Controls for problem-specific difficulty

Evaluation Metrics:
• Parse success rate: Code compiles without syntax errors
• Type check success rate: Code passes static type checking
• Visible test pass rate: Passes example test cases
• Hidden test pass rate: Passes held-out test cases
• Overall pass rate: Passes ALL tests (primary metric)

Statistical Tests:
• McNemar's test for paired binary outcomes
• Chi-square test for error category independence
• Cohen's g for effect size
""")

        return "\n".join(lines)

    def _generate_results_section(self) -> str:
        """Generate detailed results section."""
        lines = []
        lines.append("=" * 80)
        lines.append("DETAILED RESULTS")
        lines.append("=" * 80)
        lines.append("")

        if not self.results:
            return "\n".join(lines) + "No results available."

        # Summary statistics table
        lines.append("SUMMARY STATISTICS")
        lines.append("-" * 40)

        headers = ["Condition", "N", "Parse", "TypeCk", "Visible", "Hidden", "All Pass"]
        rows = []

        for condition, stats in self.results.summary_stats.items():
            rows.append(
                [
                    condition,
                    str(stats["n"]),
                    f"{stats['parse_rate']:.1%}",
                    f"{stats['typecheck_rate']:.1%}",
                    f"{stats['visible_pass_rate']:.1%}",
                    f"{stats['hidden_pass_rate']:.1%}",
                    f"{stats['all_pass_rate']:.1%}",
                ]
            )

        lines.append(self.visualizer.table(headers, rows))
        lines.append("")

        # Comparison visualizations
        lines.append("PASS RATE COMPARISON")
        lines.append("-" * 40)

        for model, comparison in self.results.primary_comparison.items():
            lines.append(f"\n{model}:")
            lines.append(
                self.visualizer.comparison_diagram(
                    comparison["geno_pass_rate"],
                    comparison["python_pass_rate"],
                    "Geno",
                    "Python",
                    "Pass Rate Comparison",
                )
            )

            # Contingency table
            cont = comparison["contingency"]
            lines.append("")
            lines.append(
                self.visualizer.contingency_table(
                    cont["both_pass"],
                    cont["geno_only"],
                    cont["python_only"],
                    cont["both_fail"],
                    "Geno",
                    "Python",
                )
            )

        # Error distribution
        lines.append("\n" + "=" * 40)
        lines.append("ERROR DISTRIBUTION")
        lines.append("-" * 40)

        if self.results.error_analysis.get("rates"):
            for condition, rates in self.results.error_analysis["rates"].items():
                lines.append(f"\n{condition}:")
                error_data = {k: v for k, v in rates.items() if v > 0}
                lines.append(self.visualizer.bar_chart(error_data, max_bar_width=30))

        # Difficulty breakdown
        lines.append("\n" + "=" * 40)
        lines.append("RESULTS BY DIFFICULTY")
        lines.append("-" * 40)

        if self.results.difficulty_analysis:
            for difficulty in ["trivial", "easy", "medium", "hard", "expert"]:
                if difficulty not in self.results.difficulty_analysis:
                    continue

                lines.append(f"\n{difficulty.upper()}")
                stats = self.results.difficulty_analysis[difficulty]

                diff_data = {}
                for condition, data in stats.items():
                    diff_data[condition] = data["pass_rate"]

                lines.append(self.visualizer.bar_chart(diff_data, max_bar_width=25))

        # Domain breakdown
        lines.append("\n" + "=" * 40)
        lines.append("RESULTS BY DOMAIN")
        lines.append("-" * 40)

        if self.results.domain_analysis:
            for domain in sorted(self.results.domain_analysis):
                lines.append(f"\n{domain.upper()}")
                stats = self.results.domain_analysis[domain]

                domain_data = {}
                for condition, data in stats.items():
                    domain_data[condition] = data["pass_rate"]

                lines.append(self.visualizer.bar_chart(domain_data, max_bar_width=25))

        return "\n".join(lines)

    def _generate_statistical_analysis(self) -> str:
        """Generate statistical analysis section."""
        lines = []
        lines.append("=" * 80)
        lines.append("STATISTICAL ANALYSIS")
        lines.append("=" * 80)
        lines.append("")

        if not self.results:
            return "\n".join(lines) + "No results available."

        # Primary hypothesis test
        lines.append("PRIMARY HYPOTHESIS TEST")
        lines.append("-" * 40)
        lines.append("""
H0: There is no difference in pass rate between Geno and Python
H1: Geno has a different pass rate than Python

Test: McNemar's test for paired binary data
""")

        for model, comparison in self.results.primary_comparison.items():
            cont = comparison["contingency"]
            test_result = self.stats.mcnemar_test(
                cont["both_pass"],
                cont["geno_only"],
                cont["python_only"],
                cont["both_fail"],
            )

            lines.append(f"\nModel: {model}")
            lines.append(f"  Chi-square statistic: {test_result.statistic:.4f}")
            lines.append(f"  P-value: {test_result.p_value:.4f}")
            lines.append(f"  Effect size (Cohen's g): {test_result.effect_size:.4f}")
            lines.append(
                f"  Significant at α=0.05: {'Yes' if test_result.significant else 'No'}"
            )
            lines.append(f"  Interpretation: {test_result.interpretation}")

        # Error category analysis
        lines.append("\n" + "=" * 40)
        lines.append("ERROR CATEGORY ANALYSIS")
        lines.append("-" * 40)
        lines.append("""
H0: Error distribution is independent of language
H1: Error distribution depends on language

Test: Chi-square test of independence
""")

        if self.results.error_analysis.get("counts"):
            # Build contingency table for chi-square test
            for model in {
                k.rsplit("_", 1)[0] for k in self.results.error_analysis["counts"]
            }:
                g_key = f"{model}_geno"
                p_key = f"{model}_python"

                if g_key not in self.results.error_analysis["counts"]:
                    continue
                if p_key not in self.results.error_analysis["counts"]:
                    continue

                g_counts = self.results.error_analysis["counts"][g_key]
                p_counts = self.results.error_analysis["counts"][p_key]

                # Build observed matrix
                categories = ["syntax", "type", "runtime", "wrong_answer", "none"]
                observed = [
                    [g_counts.get(cat, 0) for cat in categories],
                    [p_counts.get(cat, 0) for cat in categories],
                ]

                chi2_result = self.stats.chi_square_test(observed)

                lines.append(f"\nModel: {model}")
                lines.append(f"  Chi-square statistic: {chi2_result.statistic:.4f}")
                lines.append(f"  P-value: {chi2_result.p_value:.4f}")
                lines.append(
                    f"  Effect size (Cramér's V): {chi2_result.effect_size:.4f}"
                )
                lines.append(f"  Interpretation: {chi2_result.interpretation}")

        # Confidence intervals
        lines.append("\n" + "=" * 40)
        lines.append("CONFIDENCE INTERVALS")
        lines.append("-" * 40)

        for condition, stats in self.results.summary_stats.items():
            n = stats["n"]
            successes = int(stats["all_pass_rate"] * n)
            ci = self.stats.wilson_confidence_interval(successes, n)
            lines.append(
                f"{condition}: {stats['all_pass_rate']:.1%} (95% CI: {ci[0]:.1%} - {ci[1]:.1%})"
            )

        return "\n".join(lines)

    def _generate_discussion(self) -> str:
        """Generate discussion section."""
        lines = []
        lines.append("=" * 80)
        lines.append("DISCUSSION")
        lines.append("=" * 80)
        lines.append("")

        lines.append("INTERPRETATION OF RESULTS")
        lines.append("-" * 40)
        lines.append("""
The results should be interpreted in the context of the experimental design
and known limitations:

1. Training Data Bias
   - Python is heavily represented in LLM training data
   - Geno is novel and requires in-context learning
   - Results may underestimate Geno's potential with better prompting

2. Problem Coverage
   - Benchmark covers standard algorithmic problems
   - May not generalize to all programming domains
   - Results strongest for similar problem types

3. Evaluation Metrics
   - Test-based evaluation may miss partial correctness
   - Some valid solutions may fail due to output format
   - Human evaluation could provide additional insights

4. Statistical Considerations
   - Sample size provides adequate power for primary analysis
   - Multiple comparisons require correction
   - Effect sizes should guide practical significance
""")

        lines.append("\nIMPLICATIONS")
        lines.append("-" * 40)

        if self.results and self.results.primary_comparison:
            # Summarize main finding
            for model, comparison in self.results.primary_comparison.items():
                diff = comparison["difference"]
                if diff > 0.05:
                    lines.append(f"""
For {model}:
• Geno shows meaningful improvement over Python
• Design features appear to reduce LLM code generation errors
• Results support the hypothesis that language design matters for LLM coding
""")
                elif diff > 0:
                    lines.append(f"""
For {model}:
• Geno shows modest improvement over Python
• Some design features show promise but effect is limited
• Further optimization of language or prompting may be needed
""")
                else:
                    lines.append(f"""
For {model}:
• No clear advantage for Geno over Python
• Training data familiarity may outweigh design benefits
• Consider alternative evaluation approaches
""")

        return "\n".join(lines)

    def _generate_appendix(self) -> str:
        """Generate appendix with detailed data."""
        lines = []
        lines.append("=" * 80)
        lines.append("APPENDIX")
        lines.append("=" * 80)
        lines.append("")

        lines.append("A. BENCHMARK PROBLEM SUMMARY")
        lines.append("-" * 40)
        lines.append("""
The benchmark consists of 77 problems across multiple difficulty levels:
• Trivial: 12 problems (basic operations)
• Easy: 32 problems (simple algorithms)
• Medium: 23 problems (moderate complexity)
• Hard: 7 problems (advanced algorithms)
• Expert: 3 problems (system-level challenges)

Problems cover domains including:
• Arrays and lists
• Strings
• Mathematics
• Sorting and searching
• Dynamic programming
• Recursion
""")

        lines.append("\nB. STATISTICAL TEST DETAILS")
        lines.append("-" * 40)
        lines.append("""
McNemar's Test:
• Used for paired binary data
• Compares discordant pairs (where outcomes differ)
• Chi-square approximation with continuity correction
• Effect size: Cohen's g = (b-c)/(b+c)

Chi-Square Test:
• Tests independence of categorical variables
• Compares observed vs expected frequencies
• Effect size: Cramér's V

Confidence Intervals:
• Wilson score interval for proportions
• More accurate than normal approximation for extreme proportions
""")

        lines.append("\nC. RAW DATA AVAILABILITY")
        lines.append("-" * 40)
        lines.append("""
Complete data files are available in the results directory:
• config.json: Experiment configuration
• generations.json: All generated solutions
• evaluations.json: Evaluation results
• metrics.json: Pre-computed metrics
• analysis.json: Analysis outputs
""")

        lines.append("\n" + "=" * 80)
        lines.append("END OF REPORT")
        lines.append("=" * 80)

        return "\n".join(lines)

    # ==========================================================================
    # Alternative Output Formats
    # ==========================================================================

    def generate_markdown_report(self, output_path: str | None = None) -> str:
        """Generate report in Markdown format."""
        lines = []

        lines.append("# Geno Benchmark Evaluation Report")
        lines.append("")
        lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
        lines.append("")

        lines.append("## Executive Summary")
        lines.append("")

        if self.results:
            for model, comparison in self.results.primary_comparison.items():
                lines.append(f"### {model}")
                lines.append("")
                lines.append("| Metric | Value |")
                lines.append("|--------|-------|")
                lines.append(f"| Geno Pass Rate | {comparison['geno_pass_rate']:.1%} |")
                lines.append(
                    f"| Python Pass Rate | {comparison['python_pass_rate']:.1%} |"
                )
                lines.append(f"| Improvement | {comparison['difference']:+.1%} |")
                lines.append("")

        lines.append("## Results by Difficulty")
        lines.append("")

        if self.results and self.results.difficulty_analysis:
            lines.append("| Difficulty | Geno | Python |")
            lines.append("|------------|----------|--------|")

            for difficulty in ["trivial", "easy", "medium", "hard"]:
                if difficulty in self.results.difficulty_analysis:
                    stats = self.results.difficulty_analysis[difficulty]
                    g_rate = p_rate = "N/A"
                    for condition, data in stats.items():
                        if "geno" in condition:
                            g_rate = f"{data['pass_rate']:.1%}"
                        elif "python" in condition:
                            p_rate = f"{data['pass_rate']:.1%}"
                    lines.append(f"| {difficulty.capitalize()} | {g_rate} | {p_rate} |")

        lines.append("")
        lines.append("## Results by Domain")
        lines.append("")

        if self.results and self.results.domain_analysis:
            conditions = sorted(
                {
                    condition
                    for stats in self.results.domain_analysis.values()
                    for condition in stats
                }
            )
            lines.append("| Domain | " + " | ".join(conditions) + " |")
            lines.append("|--------|" + "|".join(["--------"] * len(conditions)) + "|")

            for domain in sorted(self.results.domain_analysis):
                stats = self.results.domain_analysis[domain]
                rates = [
                    f"{stats[condition]['pass_rate']:.1%}"
                    if condition in stats
                    else "N/A"
                    for condition in conditions
                ]
                lines.append(f"| {domain} | " + " | ".join(rates) + " |")

        report = "\n".join(lines)

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(report)

        return report

    def generate_latex_tables(self, output_path: str | None = None) -> str:
        """Generate LaTeX tables for academic paper."""
        lines = []

        lines.append("% Main Results Table")
        lines.append("\\begin{table}[htbp]")
        lines.append("\\centering")
        lines.append("\\caption{Overall Pass Rates by Language}")
        lines.append("\\label{tab:main_results}")
        lines.append("\\begin{tabular}{lccc}")
        lines.append("\\toprule")
        lines.append("Model & Geno & Python & Difference \\\\")
        lines.append("\\midrule")

        if self.results:
            for model, comparison in self.results.primary_comparison.items():
                g = comparison["geno_pass_rate"] * 100
                p = comparison["python_pass_rate"] * 100
                d = comparison["difference"] * 100
                lines.append(f"{model} & {g:.1f}\\% & {p:.1f}\\% & {d:+.1f}\\% \\\\")

        lines.append("\\bottomrule")
        lines.append("\\end{tabular}")
        lines.append("\\end{table}")

        latex = "\n".join(lines)

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(latex)

        return latex


def generate_report(results_dir: str, output_path: str = "report.txt"):
    """Convenience function to generate a report."""
    generator = ReportGenerator()
    generator.load_results(results_dir)
    return generator.generate_full_report(output_path)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        results_dir = sys.argv[1]
        output = sys.argv[2] if len(sys.argv) > 2 else "report.txt"
        generate_report(results_dir, output)
    else:
        print("Usage: python report_generator.py <results_dir> [output_file]")
