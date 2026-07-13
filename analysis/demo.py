"""
Analysis Demo
=============

Demonstrates the analysis framework with sample data.
"""

import json
import sys
from pathlib import Path

# Preserve direct `python analysis/demo.py` usage.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.analyzer import ResultsAnalyzer
from analysis.report_generator import ReportGenerator
from analysis.statistics import StatisticalTests
from analysis.visualizations import Visualizer


def generate_sample_data():
    """Generate sample experiment data for demonstration."""

    # Sample config
    config = {
        "experiment_id": "demo_001",
        "models": ["demo_model"],
        "languages": ["geno", "python"],
        "trials_per_condition": 1,
    }

    # Sample generations and evaluations
    # Simulating results where Geno has ~75% pass rate and Python has ~60%
    generations = []
    evaluations = []

    problems = [f"PROB-{i:03d}" for i in range(1, 56)]

    import random

    random.seed(42)

    for problem_id in problems:
        # Determine problem difficulty
        num = int(problem_id.split("-")[1])
        if num <= 10:
            g_prob, p_prob = 0.95, 0.90
        elif num <= 25:
            g_prob, p_prob = 0.85, 0.75
        elif num <= 45:
            g_prob, p_prob = 0.65, 0.50
        else:
            g_prob, p_prob = 0.40, 0.25

        for language in ["geno", "python"]:
            prob = g_prob if language == "geno" else p_prob
            passed = random.random() < prob

            # Determine error category
            if passed:
                error_cat = "none"
            else:
                # Geno has fewer syntax/type errors
                if language == "geno":
                    error_cat = random.choice(
                        ["wrong_answer"] * 7 + ["runtime"] * 2 + ["syntax"]
                    )
                else:
                    error_cat = random.choice(
                        ["wrong_answer"] * 4
                        + ["runtime"] * 3
                        + ["syntax"] * 2
                        + ["type"]
                    )

            gen = {
                "model": "demo_model",
                "problem_id": problem_id,
                "language": language,
                "trial": 0,
                "prompt": f"Solve {problem_id} in {language}",
                "raw_response": "...",
                "extracted_code": "...",
                "generation_time_ms": random.uniform(100, 500),
            }

            eval_res = {
                "problem_id": problem_id,
                "language": language,
                "parsed": error_cat != "syntax",
                "type_checked": error_cat not in ["syntax", "type"],
                "visible_passed": 3 if passed else random.randint(0, 2),
                "visible_total": 3,
                "hidden_passed": 5 if passed else random.randint(0, 3),
                "hidden_total": 5,
                "all_passed": passed,
                "pass_rate": 1.0 if passed else random.uniform(0.2, 0.7),
                "error_category": error_cat,
                "error_message": "" if passed else f"Error in {problem_id}",
                "solution_tokens": random.randint(50, 200),
                "total_execution_time_ms": random.uniform(10, 100),
            }

            generations.append(gen)
            evaluations.append(eval_res)

    return config, generations, evaluations


def run_demo():
    """Run complete analysis demo."""
    print("=" * 70)
    print("GENOTYPE BENCHMARK ANALYSIS DEMO")
    print("=" * 70)
    print()

    # Generate sample data
    print("1. Generating sample experiment data...")
    config, generations, evaluations = generate_sample_data()

    # Save to temporary directory
    demo_dir = Path(__file__).parent / "demo_results"
    demo_dir.mkdir(exist_ok=True)

    with open(demo_dir / "config.json", "w") as f:
        json.dump(config, f, indent=2)
    with open(demo_dir / "generations.json", "w") as f:
        json.dump(generations, f, indent=2)
    with open(demo_dir / "evaluations.json", "w") as f:
        json.dump(evaluations, f, indent=2)

    print(f"   Saved sample data to {demo_dir}")
    print()

    # Run analysis
    print("2. Running analysis...")
    analyzer = ResultsAnalyzer(str(demo_dir))
    analyzer.load_data()
    results = analyzer.run_all_analyses()
    analyzer.save_analysis()
    print()

    # Print summary table
    print("3. Analysis Summary:")
    print(analyzer.get_summary_table())
    print()

    # Demonstrate statistical tests
    print("4. Statistical Tests Demo:")
    print("-" * 40)

    stats = StatisticalTests()

    # McNemar's test
    if results.primary_comparison:
        for model, comp in results.primary_comparison.items():
            cont = comp["contingency"]
            test_result = stats.mcnemar_test(
                cont["both_pass"],
                cont["geno_only"],
                cont["python_only"],
                cont["both_fail"],
            )
            print(f"\nMcNemar's Test for {model}:")
            print(f"  χ² = {test_result.statistic:.4f}")
            print(f"  p = {test_result.p_value:.4f}")
            print(f"  Cohen's g = {test_result.effect_size:.4f}")
            print(f"  {test_result.interpretation}")
    print()

    # Demonstrate visualizations
    print("5. Visualization Demo:")
    print("-" * 40)

    viz = Visualizer()

    # Pass rate comparison
    if results.summary_stats:
        pass_rates = {}
        for condition, stats_data in results.summary_stats.items():
            pass_rates[condition] = stats_data["all_pass_rate"]

        print("\nPass Rate Bar Chart:")
        print(viz.bar_chart(pass_rates, title="Overall Pass Rates"))
    print()

    # Contingency table
    if results.primary_comparison:
        for model, comp in results.primary_comparison.items():
            cont = comp["contingency"]
            print(f"\nContingency Table ({model}):")
            print(
                viz.contingency_table(
                    cont["both_pass"],
                    cont["geno_only"],
                    cont["python_only"],
                    cont["both_fail"],
                    "Geno",
                    "Python",
                )
            )
    print()

    # Generate full report
    print("6. Generating Full Report...")
    generator = ReportGenerator(analyzer)
    report = generator.generate_full_report(str(demo_dir / "report.txt"))
    print(f"   Report saved to {demo_dir / 'report.txt'}")
    print()

    # Also generate markdown
    generator.generate_markdown_report(str(demo_dir / "report.md"))
    print(f"   Markdown report saved to {demo_dir / 'report.md'}")
    print()

    print("=" * 70)
    print("DEMO COMPLETE")
    print("=" * 70)
    print()
    print(f"All outputs saved to: {demo_dir}")
    print()

    # Print first part of the report
    print("Report Preview (first 100 lines):")
    print("-" * 40)
    print("\n".join(report.split("\n")[:100]))


if __name__ == "__main__":
    run_demo()
