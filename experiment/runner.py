"""
Experiment Runner
=================

Orchestrates benchmark evaluations across models and languages.
"""

import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, cast

# Preserve direct `python experiment/runner.py` usage.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark.runner import BenchmarkRunner, EvaluationResult
from benchmark.schema import Problem, load_problems
from experiment.metrics import (
    compare_results,
    compute_difficulty_breakdown,
    compute_metrics,
)
from experiment.prompts import (
    GENOTYPE_FEW_SHOT_SAMPLES,
    PYTHON_FEW_SHOT_SAMPLES,
    format_geno_prompt,
    format_python_prompt,
    format_repair_prompt,
)


@dataclass
class ExperimentConfig:
    """Configuration for an experiment run."""

    experiment_id: str
    models: list[str]
    languages: list[str] = field(default_factory=lambda: ["geno", "python"])
    trials_per_condition: int = 1
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout_seconds: float = 5.0
    output_dir: str = "results"
    few_shot_examples: int = 0
    repo_revision: str = ""
    model_metadata: dict[str, dict[str, str]] = field(default_factory=dict)
    problems: list[Problem] | None = field(default=None, repr=False)
    # Benchmark v2 repair condition: number of diagnostics-guided repair
    # attempts after a failed evaluation. 0 keeps pure pass@1 behavior.
    max_repair_rounds: int = 0

    def to_dict(self) -> dict:
        """Serialize config without embedding full problem specs."""
        data = {
            "experiment_id": self.experiment_id,
            "models": self.models,
            "languages": self.languages,
            "trials_per_condition": self.trials_per_condition,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "output_dir": self.output_dir,
            "few_shot_examples": self.few_shot_examples,
            "repo_revision": self.repo_revision,
            "model_metadata": self.model_metadata,
            "max_repair_rounds": self.max_repair_rounds,
        }
        if self.problems is not None:
            data["problem_ids"] = [problem.id for problem in self.problems]
        return data


@dataclass
class GenerationResult:
    """Result from generating a solution."""

    model: str
    problem_id: str
    language: str
    trial: int
    prompt: str
    raw_response: str
    extracted_code: str
    generation_time_ms: float
    prompt_hash: str = ""
    tokens_used: int = 0
    error: str = ""
    # 0 for the initial attempt; 1..max_repair_rounds for repair attempts.
    repair_round: int = 0


@dataclass
class ExperimentRun:
    """Complete experiment run data."""

    config: ExperimentConfig
    start_time: str = ""
    end_time: str = ""
    generation_results: list[GenerationResult] = field(default_factory=list)
    evaluation_results: list[EvaluationResult] = field(default_factory=list)
    # Index-aligned repair attempts; initial pass@1 results above stay pure.
    repair_generation_results: list[GenerationResult] = field(default_factory=list)
    repair_evaluation_results: list[EvaluationResult] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize experiment results for JSON output."""
        data = {
            "config": self.config.to_dict(),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "generation_results": [
                asdict(result) for result in self.generation_results
            ],
            "evaluation_results": [
                result.to_dict() for result in self.evaluation_results
            ],
            "metrics": self.metrics,
        }
        if self.repair_generation_results:
            data["repair_generation_results"] = [
                asdict(result) for result in self.repair_generation_results
            ]
            data["repair_evaluation_results"] = [
                result.to_dict() for result in self.repair_evaluation_results
            ]
        return data


class ExperimentRunner:
    """
    Runs benchmark experiments comparing languages across models.

    Usage:
        config = ExperimentConfig(
            experiment_id="exp_001",
            models=["gpt-4", "claude-3"],
        )
        runner = ExperimentRunner(config)
        runner.run()
        runner.save_results()
    """

    def __init__(self, config: ExperimentConfig):
        self.config = config
        if config.problems is not None:
            self.problems = list(config.problems)
        else:
            self.problems = load_problems(
                Path(__file__).parent.parent / "benchmark" / "problems"
            )
        self.benchmark_runner = BenchmarkRunner.for_research(
            timeout_seconds=config.timeout_seconds
        )
        self.run_data = ExperimentRun(config=config)

        # Model generation function (to be set by user)
        self._generate_fn: Callable | None = None

    def set_generator(self, fn: Callable[[str, str, str], str]):
        """
        Set the function used to generate solutions.

        The function should accept:
            - model: str (model identifier)
            - prompt: str (the prompt to send)
            - language: str (target language)

        And return:
            - str: the generated code
        """
        self._generate_fn = fn

    def generate_prompt(self, problem: Problem, language: str) -> str:
        """Generate a prompt for a problem in a given language."""
        n_few_shot = max(0, self.config.few_shot_examples)
        if language == "geno":
            return cast(
                str,
                format_geno_prompt(
                    problem,
                    few_shot_examples=GENOTYPE_FEW_SHOT_SAMPLES[:n_few_shot],
                ),
            )
        elif language == "python":
            return cast(
                str,
                format_python_prompt(
                    problem,
                    few_shot_examples=PYTHON_FEW_SHOT_SAMPLES[:n_few_shot],
                ),
            )
        else:
            raise ValueError(f"Unknown language: {language}")

    def extract_code(self, response: str, language: str) -> str:
        """Extract code from model response."""
        # Try to find code block
        if "```" in response:
            parts = response.split("```")
            for i, part in enumerate(parts):
                if i % 2 == 1:  # Odd indices are inside code blocks
                    # Remove language identifier if present
                    lines = part.strip().split("\n")
                    if lines and lines[0].lower() in ["geno", "python", "py", ""]:
                        return "\n".join(lines[1:])
                    return part.strip()

        # No code block found, return entire response
        return response.strip()

    def run_single_evaluation(
        self, model: str, problem: Problem, language: str, trial: int
    ) -> tuple[GenerationResult, EvaluationResult]:
        """Run a single generation and evaluation."""

        # Generate prompt
        prompt = self.generate_prompt(problem, language)

        # Generate solution
        gen_result = GenerationResult(
            model=model,
            problem_id=problem.id,
            language=language,
            trial=trial,
            prompt=prompt,
            raw_response="",
            extracted_code="",
            generation_time_ms=0.0,
            prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        )

        if self._generate_fn:
            self._generate_into(gen_result, model, prompt, language)
        else:
            # Use canonical solution for testing
            if language == "geno":
                gen_result.extracted_code = problem.geno_solution
            else:
                gen_result.extracted_code = problem.python_solution

        # Evaluate solution
        if language == "geno":
            eval_result = self.benchmark_runner.evaluate_geno(
                problem, gen_result.extracted_code
            )
        else:
            eval_result = self.benchmark_runner.evaluate_python(
                problem, gen_result.extracted_code
            )

        return gen_result, eval_result

    def run(self, progress_callback: Callable[[int, int], None] | None = None):
        """Run the complete experiment."""
        self.run_data.start_time = datetime.now().isoformat()

        total_evals = (
            len(self.config.models)
            * len(self.problems)
            * len(self.config.languages)
            * self.config.trials_per_condition
        )
        current_eval = 0

        for model in self.config.models:
            for problem in self.problems:
                for language in self.config.languages:
                    for trial in range(self.config.trials_per_condition):
                        gen_result, eval_result = self.run_single_evaluation(
                            model, problem, language, trial
                        )
                        self.run_data.generation_results.append(gen_result)
                        self.run_data.evaluation_results.append(eval_result)

                        if (
                            self.config.max_repair_rounds > 0
                            and self._generate_fn
                            and not eval_result.all_passed
                            # A failed generation call (provider error) is an
                            # infrastructure outcome, not a code failure; a
                            # retry that then passes must not count as repair.
                            and not gen_result.error
                        ):
                            self._run_repair_rounds(
                                model,
                                problem,
                                language,
                                trial,
                                gen_result,
                                eval_result,
                            )

                        current_eval += 1
                        if progress_callback:
                            progress_callback(current_eval, total_evals)

        self.run_data.end_time = datetime.now().isoformat()
        self._compute_metrics()
        self._compute_repair_metrics()
        return self.run_data.to_dict()

    def _generate_into(
        self, gen_result: GenerationResult, model: str, prompt: str, language: str
    ) -> None:
        """Call the generator, recording output or failure on *gen_result*."""
        assert self._generate_fn is not None
        start = time.time()
        try:
            response = self._generate_fn(model, prompt, language)
            gen_result.raw_response = response
            gen_result.extracted_code = self.extract_code(response, language)
            gen_result.generation_time_ms = (time.time() - start) * 1000
        # Generators are arbitrary callables; any failure becomes a recorded
        # generation error rather than aborting the experiment.
        except Exception as e:
            gen_result.error = str(e)

    def _repair_diagnostics(self, eval_result: EvaluationResult) -> str:
        """Summarize a failed evaluation the way a developer would see it.

        Includes parse/type errors and visible-example failures only; hidden
        test inputs and outputs are never revealed to the model.
        """
        if not eval_result.parsed:
            return f"The code failed to parse:\n{eval_result.error_message}"
        if not eval_result.type_checked:
            return f"The code failed type checking:\n{eval_result.error_message}"

        visible = eval_result.test_results[: eval_result.visible_total]
        failed_visible = [t for t in visible if not t.passed]
        if failed_visible:
            lines = ["These visible examples failed:"]
            for t in failed_visible:
                detail = (
                    f"- input {t.test_case.input!r}: "
                    f"expected {t.test_case.output!r}, got {t.actual_output!r}"
                )
                if t.error_message:
                    detail += f" ({t.error_message})"
                lines.append(detail)
            return "\n".join(lines)

        return (
            "All visible examples passed, but at least one additional hidden "
            "test failed. Review edge cases (empty inputs, boundaries, "
            "duplicates, negative values) and correct the solution."
        )

    def _run_repair_rounds(
        self,
        model: str,
        problem: Problem,
        language: str,
        trial: int,
        gen_result: GenerationResult,
        eval_result: EvaluationResult,
    ) -> None:
        """Run diagnostics-guided repair attempts for a failed condition."""
        current_gen, current_eval = gen_result, eval_result
        for round_idx in range(1, self.config.max_repair_rounds + 1):
            if current_eval.all_passed:
                break
            prompt = format_repair_prompt(
                original_prompt=gen_result.prompt,
                code=current_gen.extracted_code,
                diagnostics=self._repair_diagnostics(current_eval),
                language=language,
            )
            repair_gen = GenerationResult(
                model=model,
                problem_id=problem.id,
                language=language,
                trial=trial,
                prompt=prompt,
                raw_response="",
                extracted_code="",
                generation_time_ms=0.0,
                prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                repair_round=round_idx,
            )
            self._generate_into(repair_gen, model, prompt, language)

            if language == "geno":
                repair_eval = self.benchmark_runner.evaluate_geno(
                    problem, repair_gen.extracted_code
                )
            else:
                repair_eval = self.benchmark_runner.evaluate_python(
                    problem, repair_gen.extracted_code
                )
            self.run_data.repair_generation_results.append(repair_gen)
            self.run_data.repair_evaluation_results.append(repair_eval)
            if repair_gen.error:
                break
            current_gen, current_eval = repair_gen, repair_eval

    def _compute_repair_metrics(self) -> None:
        """Compute pass-after-repair and conversion rates per model/language."""
        if not self.run_data.repair_generation_results:
            return

        initial: dict[tuple, bool] = {}
        final: dict[tuple, bool] = {}
        for g, e in zip(
            self.run_data.generation_results, self.run_data.evaluation_results
        ):
            key = (g.model, g.problem_id, g.language, g.trial)
            initial[key] = e.all_passed
            final[key] = e.all_passed
        attempted_keys: set[tuple] = set()
        for g, e in zip(
            self.run_data.repair_generation_results,
            self.run_data.repair_evaluation_results,
        ):
            key = (g.model, g.problem_id, g.language, g.trial)
            attempted_keys.add(key)
            # Rounds were appended in order; the last one is the final outcome.
            final[key] = e.all_passed

        summary: dict[str, dict] = {}
        for model in self.config.models:
            for language in self.config.languages:
                keys = [k for k in initial if k[0] == model and k[2] == language]
                if not keys:
                    continue
                total = len(keys)
                initially_passed = sum(1 for k in keys if initial[k])
                attempted = [k for k in keys if k in attempted_keys]
                converted = sum(1 for k in attempted if final[k])
                summary.setdefault(model, {})[language] = {
                    "conditions": total,
                    "pass_at_1": initially_passed / total,
                    "repair_attempted": len(attempted),
                    "repair_converted": converted,
                    "repair_conversion_rate": (
                        converted / len(attempted) if attempted else 0.0
                    ),
                    "pass_after_repair": (initially_passed + converted) / total,
                    "max_repair_rounds": self.config.max_repair_rounds,
                }
        self.run_data.metrics["repair"] = summary

    def _compute_metrics(self):
        """Compute aggregate metrics from results."""
        # Group results by model and language
        results_by_condition: dict[tuple[str, str], list[EvaluationResult]] = {}
        pairs_by_condition: dict[
            tuple[str, str], list[tuple[GenerationResult, EvaluationResult]]
        ] = {}

        for gen, eval_res in zip(
            self.run_data.generation_results, self.run_data.evaluation_results
        ):
            key = (gen.model, gen.language)
            if key not in results_by_condition:
                results_by_condition[key] = []
                pairs_by_condition[key] = []
            results_by_condition[key].append(eval_res)
            pairs_by_condition[key].append((gen, eval_res))

        # Compute per-condition metrics
        for (model, language), results in results_by_condition.items():
            metrics = compute_metrics(results)
            data = metrics.to_dict()
            data.update(
                self._compute_pass_at_metrics(pairs_by_condition[(model, language)])
            )
            self.run_data.metrics[f"{model}_{language}"] = data

        # Compute comparisons (Geno vs Python) for each model
        for model in self.config.models:
            geno_key = (model, "geno")
            python_key = (model, "python")

            if geno_key in results_by_condition and python_key in results_by_condition:
                comparison = compare_results(
                    results_by_condition[geno_key],
                    results_by_condition[python_key],
                    "Geno",
                    "Python",
                )
                self.run_data.metrics[f"{model}_comparison"] = comparison.to_dict()

        # Compute difficulty breakdowns
        problem_difficulties = {p.id: p.difficulty.value for p in self.problems}

        for (model, language), results in results_by_condition.items():
            breakdowns = compute_difficulty_breakdown(results, problem_difficulties)
            self.run_data.metrics[f"{model}_{language}_by_difficulty"] = [
                asdict(b) for b in breakdowns
            ]

    def _compute_pass_at_metrics(
        self,
        pairs: list[tuple[GenerationResult, EvaluationResult]],
    ) -> dict[str, float | int]:
        """Compute per-problem pass@1 and pass@k metrics for one condition."""
        by_problem: dict[str, list[tuple[GenerationResult, EvaluationResult]]] = {}
        for gen, eval_res in pairs:
            by_problem.setdefault(gen.problem_id, []).append((gen, eval_res))

        if not by_problem:
            return {"n_problems": 0, "pass_at_1": 0.0, "pass_at_k": 0.0, "pass_k": 0}

        first_passes = 0
        any_passes = 0
        pass_k = max(len(problem_pairs) for problem_pairs in by_problem.values())
        for problem_pairs in by_problem.values():
            ordered = sorted(problem_pairs, key=lambda pair: pair[0].trial)
            if ordered[0][1].all_passed:
                first_passes += 1
            if any(eval_res.all_passed for _gen, eval_res in ordered):
                any_passes += 1

        n = len(by_problem)
        return {
            "n_problems": n,
            "pass_at_1": first_passes / n,
            "pass_at_k": any_passes / n,
            "pass_k": pass_k,
        }

    def save_results(self, output_dir: str | None = None):
        """Save experiment results to files."""
        out_dir = Path(output_dir or self.config.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Save config
        with open(out_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(self.config.to_dict(), f, indent=2)

        # Save generation results
        gen_data = [asdict(g) for g in self.run_data.generation_results]
        with open(out_dir / "generations.json", "w", encoding="utf-8") as f:
            json.dump(gen_data, f, indent=2)

        # Save evaluation results
        eval_data = [r.to_dict() for r in self.run_data.evaluation_results]
        with open(out_dir / "evaluations.json", "w", encoding="utf-8") as f:
            json.dump(eval_data, f, indent=2)

        # Save repair-round artifacts (only when the condition was active)
        if self.run_data.repair_generation_results:
            repair_gen = [asdict(g) for g in self.run_data.repair_generation_results]
            with open(out_dir / "repair_generations.json", "w", encoding="utf-8") as f:
                json.dump(repair_gen, f, indent=2)
            repair_eval = [r.to_dict() for r in self.run_data.repair_evaluation_results]
            with open(out_dir / "repair_evaluations.json", "w", encoding="utf-8") as f:
                json.dump(repair_eval, f, indent=2)

        # Save metrics
        with open(out_dir / "metrics.json", "w", encoding="utf-8") as f:
            json.dump(self.run_data.metrics, f, indent=2)

        # Save summary report
        report = self._generate_report()
        with open(out_dir / "report.txt", "w", encoding="utf-8") as f:
            f.write(report)

        print(f"Results saved to {out_dir}")

    def _generate_report(self) -> str:
        """Generate a human-readable report."""
        lines = []
        lines.append("=" * 70)
        lines.append(f"EXPERIMENT REPORT: {self.config.experiment_id}")
        lines.append("=" * 70)
        lines.append(f"\nStart: {self.run_data.start_time}")
        lines.append(f"End: {self.run_data.end_time}")
        lines.append(f"Models: {', '.join(self.config.models)}")
        lines.append(f"Problems: {len(self.problems)}")
        lines.append(f"Trials per condition: {self.config.trials_per_condition}")

        for model in self.config.models:
            lines.append(f"\n{'=' * 70}")
            lines.append(f"MODEL: {model}")
            lines.append("=" * 70)

            g_key = f"{model}_geno"
            p_key = f"{model}_python"
            c_key = f"{model}_comparison"

            if g_key in self.run_data.metrics and p_key in self.run_data.metrics:
                g_metrics = self.run_data.metrics[g_key]
                p_metrics = self.run_data.metrics[p_key]

                lines.append(f"\n{'Metric':<35} {'Geno':>15} {'Python':>15}")
                lines.append("-" * 65)
                lines.append(
                    f"{'Parse Success Rate':<35} {g_metrics['parse_success_rate']:>14.1%} {p_metrics['parse_success_rate']:>14.1%}"
                )
                lines.append(
                    f"{'Type Check Success Rate':<35} {g_metrics['typecheck_success_rate']:>14.1%} {p_metrics['typecheck_success_rate']:>14.1%}"
                )
                lines.append(
                    f"{'Visible Test Pass Rate':<35} {g_metrics['visible_pass_rate']:>14.1%} {p_metrics['visible_pass_rate']:>14.1%}"
                )
                lines.append(
                    f"{'Hidden Test Pass Rate':<35} {g_metrics['hidden_pass_rate']:>14.1%} {p_metrics['hidden_pass_rate']:>14.1%}"
                )
                lines.append(
                    f"{'Overall Pass Rate':<35} {g_metrics['overall_pass_rate']:>14.1%} {p_metrics['overall_pass_rate']:>14.1%}"
                )

            if c_key in self.run_data.metrics:
                comp = self.run_data.metrics[c_key]
                lines.append("\nComparison:")
                lines.append(f"  Pass rate difference: {comp['pass_rate_diff']:+.1%}")
                lines.append(
                    f"  Relative improvement: {comp['relative_improvement']:+.1%}"
                )
                lines.append(f"  McNemar's χ²: {comp['mcnemar_statistic']:.3f}")

            # Difficulty breakdown
            d_key = f"{model}_geno_by_difficulty"
            if d_key in self.run_data.metrics:
                lines.append("\nBy Difficulty (Geno):")
                for b in self.run_data.metrics[d_key]:
                    lines.append(
                        f"  {b['difficulty']:<10}: {b['pass_rate']:>6.1%} ({b['n_problems']} problems)"
                    )

        lines.append("\n" + "=" * 70)
        lines.append("END OF REPORT")
        lines.append("=" * 70)

        return "\n".join(lines)


def run_canonical_test():
    """Test the experiment runner with canonical solutions."""
    config = ExperimentConfig(
        experiment_id="canonical_test",
        models=["canonical"],
        trials_per_condition=1,
        output_dir=str(Path(__file__).parent / "results" / "canonical_test"),
    )

    runner = ExperimentRunner(config)

    def progress(current, total):
        print(f"\rProgress: {current}/{total} ({100 * current / total:.1f}%)", end="")

    print("Running canonical solution test...")
    runner.run(progress_callback=progress)
    print("\n")

    runner.save_results()

    # Print summary
    print(runner._generate_report())


if __name__ == "__main__":
    run_canonical_test()
