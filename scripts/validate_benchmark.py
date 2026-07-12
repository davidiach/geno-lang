#!/usr/bin/env python3
"""
Validate Benchmark Problems

This script validates all benchmark problems for:
- YAML syntax correctness
- Required field presence
- Canonical solution validity
- Test case consistency
- Construct coverage across the benchmark
- Difficulty and domain distribution balance
"""

import json
import os
import platform
import sys
from collections import Counter
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark import Difficulty, Domain, load_all_problems, verify_problem_set
from benchmark.schema import format_geno_example_input, format_geno_literal

# Key language constructs that should be covered by at least one problem.
# Constructs commented out are ones that cannot currently be tested due to
# language limitations (e.g., no map literal syntax for map_insert/map_get,
# `end` keyword clash with substring's named arg).
KEY_CONSTRUCTS = [
    "function",
    "if-else",
    "while",
    "for",
    "return",
    "type",
    "match",
    "constructor",
    "field-destructuring",
    "pipeline",
    "lambda",
    "filter",
    "map",
    "fold",
    "option",
    "some-none",
    "result",
    "ok-err",
    "requires",
    "ensures",
    "recursion",
    "multi-function",
    "named-arguments",
    # "map_insert", "map_get", "unwrap", "unwrap_or",  # need map literal syntax
    "split",
    "join",
    "trim",
    "starts_with",
    "to_lower",
    "parse_int",
    "to_string",
    "set_at",
    "slice",
    "concat",
    "append",
    "to_chars",
]

BENCHMARK_BUDGETS = {
    "max_trivial_easy_ratio": 0.60,
    "min_hard_expert_ratio": 0.10,
    "min_domain_problem_count": 1,
    "thin_domain_problem_count": 2,
    "min_key_construct_problem_count": 1,
}


def check_construct_coverage(problems):
    """Check which key constructs have benchmark coverage."""
    construct_problems = {}
    for p in problems:
        for c in p.constructs_tested:
            construct_problems.setdefault(c, []).append(p.id)

    covered = []
    uncovered = []
    for c in KEY_CONSTRUCTS:
        if c in construct_problems:
            covered.append((c, len(construct_problems[c])))
        else:
            uncovered.append(c)

    return covered, uncovered, construct_problems


def _difficulty_counts(problems):
    counts = Counter(p.difficulty.value for p in problems)
    return {
        difficulty.value: counts.get(difficulty.value, 0) for difficulty in Difficulty
    }


def _domain_counts(problems):
    counts = Counter(p.domain.value for p in problems)
    return {domain.value: counts.get(domain.value, 0) for domain in Domain}


def _construct_counts(problems):
    counts = Counter(
        construct for problem in problems for construct in problem.constructs_tested
    )
    return {
        name: count
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    }


def _construct_combination_distribution(problems):
    combinations = {}
    for problem in problems:
        constructs = tuple(sorted(set(problem.constructs_tested)))
        combinations.setdefault(constructs, []).append(problem.id)

    return [
        {
            "constructs": list(constructs),
            "count": len(problem_ids),
            "problem_ids": sorted(problem_ids),
        }
        for constructs, problem_ids in sorted(
            combinations.items(),
            key=lambda item: (-len(item[1]), ",".join(item[0])),
        )
    ]


def _budget_findings(problems, construct_counts):
    total = len(problems)
    difficulty_counts = _difficulty_counts(problems)
    domain_counts = _domain_counts(problems)

    trivial_easy = (
        difficulty_counts[Difficulty.TRIVIAL.value]
        + difficulty_counts[Difficulty.EASY.value]
    )
    hard_expert = (
        difficulty_counts[Difficulty.HARD.value]
        + difficulty_counts[Difficulty.EXPERT.value]
    )

    warnings = []
    if total and trivial_easy / total > BENCHMARK_BUDGETS["max_trivial_easy_ratio"]:
        warnings.append(
            {
                "kind": "difficulty_skew",
                "message": (
                    f"{trivial_easy}/{total} problems are trivial/easy; "
                    f"budget is <= {BENCHMARK_BUDGETS['max_trivial_easy_ratio']:.0%}"
                ),
            }
        )
    if total and hard_expert / total < BENCHMARK_BUDGETS["min_hard_expert_ratio"]:
        warnings.append(
            {
                "kind": "hard_expert_coverage",
                "message": (
                    f"{hard_expert}/{total} problems are hard/expert; "
                    f"budget is >= {BENCHMARK_BUDGETS['min_hard_expert_ratio']:.0%}"
                ),
            }
        )

    min_domain_count = BENCHMARK_BUDGETS["min_domain_problem_count"]
    thin_domain_count = BENCHMARK_BUDGETS["thin_domain_problem_count"]
    empty_domains = [
        domain for domain, count in domain_counts.items() if count < min_domain_count
    ]
    thin_domains = [
        domain
        for domain, count in domain_counts.items()
        if min_domain_count <= count < thin_domain_count
    ]
    if empty_domains:
        warnings.append(
            {
                "kind": "empty_domains",
                "domains": empty_domains,
                "message": (
                    f"Domains below minimum-domain budget ({min_domain_count}): "
                    + ", ".join(empty_domains)
                ),
            }
        )
    if thin_domains:
        warnings.append(
            {
                "kind": "thin_domains",
                "domains": thin_domains,
                "message": (
                    f"Domains below thin-domain budget ({thin_domain_count}): "
                    + ", ".join(thin_domains)
                ),
            }
        )

    uncovered_constructs = [
        construct
        for construct in KEY_CONSTRUCTS
        if construct_counts.get(construct, 0)
        < BENCHMARK_BUDGETS["min_key_construct_problem_count"]
    ]
    if uncovered_constructs:
        warnings.append(
            {
                "kind": "uncovered_key_constructs",
                "constructs": uncovered_constructs,
                "message": (
                    "Key constructs with no benchmark coverage: "
                    + ", ".join(uncovered_constructs)
                ),
            }
        )

    return {
        "warnings": warnings,
        "empty_domains": empty_domains,
        "thin_domains": thin_domains,
        "uncovered_key_constructs": uncovered_constructs,
    }


def build_benchmark_analysis(problems, report):
    """Build a machine-readable benchmark corpus analysis report."""
    difficulty_distribution = _difficulty_counts(problems)
    domain_distribution = _domain_counts(problems)
    construct_distribution = _construct_counts(problems)
    construct_combination_distribution = _construct_combination_distribution(problems)

    domains = {}
    for domain in Domain:
        domain_problems = [p for p in problems if p.domain == domain]
        domain_constructs = _construct_counts(domain_problems)
        domains[domain.value] = {
            "total": len(domain_problems),
            "problem_ids": [p.id for p in domain_problems],
            "difficulty_distribution": _difficulty_counts(domain_problems),
            "construct_distribution": domain_constructs,
            "hard_expert_problem_ids": [
                p.id
                for p in domain_problems
                if p.difficulty in (Difficulty.HARD, Difficulty.EXPERT)
            ],
        }

    budget_findings = _budget_findings(problems, construct_distribution)
    return {
        "environment": {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "executable": sys.executable,
        },
        "budgets": BENCHMARK_BUDGETS,
        "totals": {
            "problems": len(problems),
            "valid_problems": report["valid_problems"],
            "problems_with_issues": report["problems_with_issues"],
        },
        "difficulty_distribution": difficulty_distribution,
        "domain_distribution": domain_distribution,
        "construct_distribution": construct_distribution,
        "construct_combination_distribution": construct_combination_distribution,
        "domain_analysis": domains,
        "budget_findings": budget_findings,
        "drift_snapshot": {
            "total_problems": len(problems),
            "difficulty_distribution": difficulty_distribution,
            "domain_distribution": domain_distribution,
            "construct_distribution": construct_distribution,
            "construct_combination_distribution": construct_combination_distribution,
            "empty_domains": budget_findings["empty_domains"],
            "thin_domains": budget_findings["thin_domains"],
            "uncovered_key_constructs": budget_findings["uncovered_key_constructs"],
        },
    }


def check_distribution_balance(problems):
    """Check if difficulty and domain distributions are reasonably balanced."""
    warnings = []

    # Difficulty check
    total = len(problems)
    trivial_easy = sum(
        1 for p in problems if p.difficulty in (Difficulty.TRIVIAL, Difficulty.EASY)
    )
    if total > 0 and trivial_easy / total > 0.6:
        warnings.append(
            f"Difficulty skew: {trivial_easy}/{total} ({trivial_easy * 100 // total}%) "
            f"problems are trivial/easy (target: <60%)"
        )

    hard_expert = sum(
        1 for p in problems if p.difficulty in (Difficulty.HARD, Difficulty.EXPERT)
    )
    if total > 0 and hard_expert / total < 0.1:
        warnings.append(
            f"Few hard/expert problems: {hard_expert}/{total} ({hard_expert * 100 // total}%) "
            f"(target: >=10%)"
        )

    # Domain check: warn about declared domains with zero problems
    domain_counts = _domain_counts(problems)
    empty_domains = [domain for domain, count in domain_counts.items() if count == 0]
    if empty_domains:
        warnings.append(f"Domains with no problems: {', '.join(empty_domains)}")

    return warnings


def check_geno_prompt_examples(problems):
    """Ensure generated Geno prompt examples parse and typecheck as Geno."""
    from geno.lexer import Lexer
    from geno.parser import Parser
    from geno.typechecker import TypeChecker

    issues = {}
    for problem in problems:
        if not problem.visible_examples:
            continue

        params = ", ".join(f"{i.name}: {i.type}" for i in problem.inputs)
        examples = "\n".join(
            f"    example {format_geno_example_input(e.input, problem.inputs)} -> "
            f"{format_geno_literal(e.output, problem.output.type)}"
            for e in problem.visible_examples
        )
        return_literal = format_geno_literal(
            problem.visible_examples[0].output,
            problem.output.type,
        )
        source = f"""func {problem.function_name}({params}) -> {problem.output.type}
{examples}
    return {return_literal}
end func
"""
        try:
            tokens = Lexer(source, filename=f"{problem.id}-prompt.geno").tokenize()
            program = Parser(tokens).parse_program()
            TypeChecker().check_program(program)
        except Exception as exc:
            issues.setdefault(problem.id, []).append(
                f"Generated Geno prompt examples are invalid: {exc}"
            )

    return issues


def check_python_prompts(problems):
    """Ensure generated Python prompts do not expose schema-only ADT artifacts."""
    from experiment.prompts import format_python_prompt

    issues = {}
    for problem in problems:
        prompts = [
            ("schema", problem.generate_python_prompt()),
            ("experiment", format_python_prompt(problem)),
        ]
        for source, prompt in prompts:
            prompt_issues: list[str] = []
            if "Optional[" in prompt:
                prompt_issues.append(
                    f"{source} Python prompt uses Optional without an import"
                )
            if "Result[" in prompt:
                prompt_issues.append(
                    f"{source} Python prompt uses undefined Result type"
                )
            if "{'Some':" in prompt or '"Some":' in prompt:
                prompt_issues.append(
                    f"{source} Python prompt exposes Option schema dictionaries"
                )
            if "-> 'None'" in prompt:
                prompt_issues.append(
                    f"{source} Python prompt renders Option None as a string"
                )
            if prompt_issues:
                issues.setdefault(problem.id, []).extend(prompt_issues)

    return issues


def _parse_args(argv):
    import argparse

    parser = argparse.ArgumentParser(description="Validate Geno benchmark problems")
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Write machine-readable benchmark analysis to this JSON path.",
    )
    parser.add_argument(
        "--strict-budgets",
        action="store_true",
        help="Exit non-zero when benchmark budget warnings are present.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    """Run benchmark validation."""
    args = _parse_args(argv)
    print("=" * 60)
    print("GENOTYPE BENCHMARK VALIDATION")
    print("=" * 60)
    print()

    # Load all problems
    print("Loading problems...")
    try:
        problems = load_all_problems()
        print(f"  Loaded {len(problems)} problems")
    except Exception as e:
        print(f"  ERROR: Failed to load problems: {e}")
        sys.exit(1)

    print()

    # Verify problem set
    print("Verifying problem set...")
    report = verify_problem_set(problems)

    # Verify generated Geno prompts
    print("Verifying generated Geno prompt examples...")
    prompt_issues = check_geno_prompt_examples(problems)
    if prompt_issues:
        for problem_id, issues in prompt_issues.items():
            report["issues"].setdefault(problem_id, []).extend(issues)
        report["problems_with_issues"] = len(report["issues"])
        report["valid_problems"] = (
            report["total_problems"] - report["problems_with_issues"]
        )
        print(f"  Found prompt issues in {len(prompt_issues)} problems")
    else:
        print("  All generated Geno prompt examples parse and typecheck")

    print("Verifying generated Python prompts...")
    python_prompt_issues = check_python_prompts(problems)
    if python_prompt_issues:
        for problem_id, issues in python_prompt_issues.items():
            report["issues"].setdefault(problem_id, []).extend(issues)
        report["problems_with_issues"] = len(report["issues"])
        report["valid_problems"] = (
            report["total_problems"] - report["problems_with_issues"]
        )
        print(f"  Found Python prompt issues in {len(python_prompt_issues)} problems")
    else:
        print("  All generated Python prompts avoid schema-only ADT artifacts")

    analysis = build_benchmark_analysis(problems, report)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(analysis, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"  Wrote benchmark analysis JSON: {args.json_output}")

    print()
    print("-" * 60)
    print("SUMMARY")
    print("-" * 60)
    print(f"  Total problems: {report['total_problems']}")
    print(f"  Valid problems: {report['valid_problems']}")
    print(f"  Problems with issues: {report['problems_with_issues']}")
    print()

    # Difficulty distribution
    print("Difficulty Distribution:")
    for diff in Difficulty:
        count = report["difficulty_distribution"].get(diff.value, 0)
        print(f"  {diff.value}: {count}")
    print()

    # Domain distribution
    print("Domain Distribution:")
    for dom in Domain:
        count = report.get("domain_distribution", {}).get(dom.value, 0)
        marker = " (EMPTY)" if count == 0 else ""
        print(f"  {dom.value}: {count}{marker}")
    print()

    # Construct coverage
    print("-" * 60)
    print("CONSTRUCT COVERAGE")
    print("-" * 60)
    covered, uncovered, _construct_map = check_construct_coverage(problems)

    print(f"\nCovered constructs ({len(covered)}/{len(KEY_CONSTRUCTS)}):")
    for name, count in sorted(covered, key=lambda x: -x[1]):
        print(f"  {name}: {count} problems")

    if uncovered:
        print(f"\nUNCOVERED constructs ({len(uncovered)}):")
        for name in uncovered:
            print(f"  - {name}")
    else:
        print("\nAll key constructs are covered!")
    print()

    # Distribution balance warnings
    dist_warnings = check_distribution_balance(problems)
    budget_warnings = analysis["budget_findings"]["warnings"]
    warning_messages = [warning["message"] for warning in budget_warnings]
    for message in warning_messages:
        if message not in dist_warnings:
            dist_warnings.append(message)
    if dist_warnings:
        print("-" * 60)
        print("DISTRIBUTION WARNINGS")
        print("-" * 60)
        for w in dist_warnings:
            print(f"  WARNING: {w}")
        print()

    # Show issues if any
    if report["issues"]:
        print("-" * 60)
        print("ISSUES FOUND")
        print("-" * 60)
        for problem_id, issues in report["issues"].items():
            print(f"\n{problem_id}:")
            for issue in issues:
                print(f"  - {issue}")
        print()
        sys.exit(1)
    if args.strict_budgets and budget_warnings:
        print("Benchmark budget warnings are strict in this run.")
        print()
        sys.exit(1)
    else:
        print("All problems validated successfully!")
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
