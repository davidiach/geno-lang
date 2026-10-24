"""The LLM correctness benchmark for Geno 0.6 (milestone M10).

The prompt teaches 0.6 and stays in step with the documented rules, the
runner reports what Geno's fixes repair, and the seeded-mistake results
page is current.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("yaml", reason="pyyaml required for experiment tooling tests")

import geno
from benchmark.loader import load_all_problems
from benchmark.schema import Difficulty, Domain, Problem, TypeSignature
from benchmark.schema import TestCase as BenchmarkTestCase
from benchmark.seeded_mistakes import MISTAKES, evaluate, seed
from experiment.prompts import (
    GENO_REFERENCE_PROGRAM,
    GENO_RULES,
    GENOTYPE_FEW_SHOT_SAMPLES,
    format_geno_prompt,
)
from experiment.runner import ExperimentConfig, ExperimentRunner
from geno.formatter import format_source

_ROOT = Path(__file__).resolve().parents[2]


def _fenced_geno(text: str) -> str:
    return text.split("```geno\n", 1)[1].split("```", 1)[0]


class TestPrompt:
    def test_rules_match_the_documented_system_prompt(self) -> None:
        guide = (_ROOT / "docs" / "llm-prompting.md").read_text(encoding="utf-8")
        section = guide[guide.index("## System Prompt Essentials") :]
        documented = re.search(r"```\n(.*?)```", section, re.S)
        assert documented is not None
        assert documented.group(1) == GENO_RULES

    @pytest.mark.parametrize(
        "program",
        [GENO_REFERENCE_PROGRAM]
        + [_fenced_geno(sample) for sample in GENOTYPE_FEW_SHOT_SAMPLES],
    )
    def test_prompt_programs_check_pass_their_examples_and_are_formatted(
        self, program: str
    ) -> None:
        result = geno.run(program, config=geno.RunConfig(capabilities=["print"]))
        assert result.ok, [str(d) for d in result.diagnostics]
        assert format_source(program) == program

    def test_every_geno_prompt_carries_the_rules_and_the_reference(self) -> None:
        problem = load_all_problems()[0]
        prompt = format_geno_prompt(problem)
        assert GENO_RULES in prompt
        assert GENO_REFERENCE_PROGRAM in prompt


def _identity_problem() -> Problem:
    return Problem(
        id="PROB-AUTOFIX",
        name="Identity",
        difficulty=Difficulty.EASY,
        domain=Domain.MATH,
        description="Return the input unchanged.",
        function_name="identity",
        inputs=[TypeSignature("x", "Int")],
        output=TypeSignature("out", "Int"),
        visible_examples=[BenchmarkTestCase(1, 1)],
        hidden_tests=[BenchmarkTestCase(2, 2)],
    )


# A model's answer with two habits from other languages in it.
_RUSTY_GENO = (
    "```geno\n"
    "func identity(x: Int) -> Int\n"
    "    example 1 -> 1\n"
    "    let mut y = x\n"
    "    y\n"
    "end func\n"
    "```"
)


def _run(language_answers: dict[str, str], **config: object) -> ExperimentRunner:
    prompts: list[str] = []

    def generator(model: str, prompt: str, language: str) -> str:
        prompts.append(prompt)
        return language_answers[language]

    runner = ExperimentRunner(
        ExperimentConfig(
            experiment_id="autofix-test",
            models=["scripted"],
            languages=list(language_answers),
            problems=[_identity_problem()],
            **config,  # type: ignore[arg-type]
        )
    )
    runner.set_generator(generator)
    runner.run()
    runner.prompts = prompts  # type: ignore[attr-defined]
    return runner


class TestAutofix:
    def test_fixes_turn_a_rejected_answer_into_a_pass_without_changing_pass_at_1(
        self,
    ) -> None:
        runner = _run({"geno": _RUSTY_GENO})
        assert runner.run_data.metrics["autofix"]["scripted"] == {
            "conditions": 1,
            "pass_at_1": 0.0,
            "autofix_attempted": 1,
            "autofix_converted": 1,
            "pass_after_autofix": 1.0,
        }
        [entry] = runner.run_data.autofix_results
        assert entry["fixes_applied"] == 2
        assert "    var y = x\n    return y\n" in entry["fixed_code"]
        assert runner.run_data.metrics["scripted_geno"]["pass_at_1"] == 0.0
        assert "autofix_results" in runner.run_data.to_dict()

    def test_autofix_can_be_turned_off(self) -> None:
        runner = _run({"geno": _RUSTY_GENO}, autofix=False)
        assert "autofix" not in runner.run_data.metrics
        assert runner.run_data.autofix_results == []

    def test_a_repair_prompt_lists_geno_errors_with_codes_and_fixes(self) -> None:
        runner = _run({"geno": _RUSTY_GENO}, max_repair_rounds=1)
        repair_prompt = runner.prompts[1]  # type: ignore[attr-defined]
        assert "The code failed to parse:\n" in repair_prompt
        assert "- [E202] line 3, column 5: Geno has no 'let mut'" in repair_prompt
        assert "  fix: Use 'var'" in repair_prompt


class TestPublishedReport:
    def test_repair_conditions_are_published_beside_pass_at_1(self) -> None:
        from scripts.publish_benchmark_results import render_report

        runner = _run({"geno": _RUSTY_GENO, "python": "def identity(x):\n    return x"})
        payload = runner.run_data.to_dict()
        report = render_report(payload, source_path="results.json")
        assert "## Repair Conditions" in report
        assert "| scripted | geno | 0.0% | 100.0% | 1/1 | n/a | n/a |" in report


class TestSeededMistakes:
    @pytest.mark.parametrize("mistake", MISTAKES, ids=lambda m: m.name)
    def test_every_mistake_is_seeded_somewhere(self, mistake) -> None:
        problems = load_all_problems()
        assert any(seed(mistake, p.geno_solution) for p in problems)

    def test_a_seeded_mistake_is_rejected_and_repaired(self) -> None:
        problems = [p for p in load_all_problems() if "    var " in p.geno_solution]
        [summary] = evaluate(problems[:1], [m for m in MISTAKES if m.name == "let mut"])
        assert (summary.seeded, summary.rejected, summary.repaired) == (1, 1, 1)
        assert summary.misrepaired == 0

    def test_the_published_page_is_current(self) -> None:
        from scripts.seeded_mistakes import REPORT, build_report

        assert REPORT.read_text(encoding="utf-8") == build_report(), (
            "run `python3 scripts/seeded_mistakes.py --write`"
        )
