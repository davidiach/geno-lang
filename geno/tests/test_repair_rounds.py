"""
Tests for the benchmark v2 repair-round condition and problem tracks.
"""

import pytest

pytest.importorskip("yaml", reason="pyyaml required for experiment tooling tests")

from benchmark.loader import load_all_problems
from benchmark.runner import EvaluationResult
from benchmark.schema import Problem, TypeSignature
from benchmark.schema import TestCase as BenchmarkTestCase
from experiment.runner import ExperimentConfig, ExperimentRunner


def make_problem() -> Problem:
    from benchmark.schema import Difficulty, Domain

    return Problem(
        id="PROB-REPAIR",
        name="Identity",
        difficulty=Difficulty.EASY,
        domain=Domain.MATH,
        description="Return the input unchanged.",
        function_name="identity",
        inputs=[TypeSignature("x", "Int")],
        output=TypeSignature("out", "Int"),
        visible_examples=[BenchmarkTestCase(1, 1)],
        hidden_tests=[BenchmarkTestCase(2, 2)],
        geno_solution="",
        python_solution="",
    )


BROKEN_PYTHON = "```python\ndef identity(x):\n    return 0\n```"
FIXED_PYTHON = "```python\ndef identity(x):\n    return x\n```"


def make_runner(max_repair_rounds: int) -> ExperimentRunner:
    config = ExperimentConfig(
        experiment_id="repair-test",
        models=["scripted"],
        languages=["python"],
        trials_per_condition=1,
        problems=[make_problem()],
        max_repair_rounds=max_repair_rounds,
    )
    return ExperimentRunner(config)


class TestRepairRounds:
    def test_disabled_by_default_makes_single_generation(self):
        calls: list[str] = []

        def generator(model: str, prompt: str, language: str) -> str:
            calls.append(prompt)
            return BROKEN_PYTHON

        runner = make_runner(max_repair_rounds=0)
        runner.set_generator(generator)

        runner.run()

        assert len(calls) == 1
        assert runner.run_data.repair_generation_results == []
        assert "repair" not in runner.run_data.metrics

    def test_failed_attempt_is_repaired_with_diagnostics(self):
        prompts: list[str] = []

        def generator(model: str, prompt: str, language: str) -> str:
            prompts.append(prompt)
            return BROKEN_PYTHON if len(prompts) == 1 else FIXED_PYTHON

        runner = make_runner(max_repair_rounds=2)
        runner.set_generator(generator)
        runner.run()

        # Initial pass@1 result stays a failure; repair converts it.
        assert len(prompts) == 2
        assert not runner.run_data.evaluation_results[0].all_passed
        assert runner.run_data.repair_evaluation_results[0].all_passed
        assert runner.run_data.repair_generation_results[0].repair_round == 1

        # The repair prompt carries the failed code and visible diagnostics.
        repair_prompt = prompts[1]
        assert "Your previous attempt" in repair_prompt
        assert "return 0" in repair_prompt
        assert "visible examples failed" in repair_prompt

        stats = runner.run_data.metrics["repair"]["scripted"]["python"]
        assert stats["pass_at_1"] == 0.0
        assert stats["repair_attempted"] == 1
        assert stats["repair_converted"] == 1
        assert stats["pass_after_repair"] == 1.0

    def test_repair_stops_after_max_rounds(self):
        calls: list[str] = []

        def generator(model: str, prompt: str, language: str) -> str:
            calls.append(prompt)
            return BROKEN_PYTHON

        runner = make_runner(max_repair_rounds=2)
        runner.set_generator(generator)

        runner.run()

        # 1 initial + 2 repair attempts, no more.
        assert len(calls) == 3
        stats = runner.run_data.metrics["repair"]["scripted"]["python"]
        assert stats["repair_converted"] == 0
        assert stats["pass_after_repair"] == 0.0

    def test_initial_generation_error_skips_repair(self):
        """Provider failures must not contaminate repair metrics.

        A transient generation error followed by a successful retry is
        infrastructure recovery, not code repair.
        """
        calls: list[str] = []

        def generator(model: str, prompt: str, language: str) -> str:
            calls.append(prompt)
            if len(calls) == 1:
                raise RuntimeError("provider blip")
            return FIXED_PYTHON

        runner = make_runner(max_repair_rounds=2)
        runner.set_generator(generator)

        runner.run()

        assert len(calls) == 1
        assert runner.run_data.generation_results[0].error == "provider blip"
        assert runner.run_data.repair_generation_results == []
        assert "repair" not in runner.run_data.metrics

    def test_passing_attempt_triggers_no_repair(self):
        calls: list[str] = []

        def generator(model: str, prompt: str, language: str) -> str:
            calls.append(prompt)
            return FIXED_PYTHON

        runner = make_runner(max_repair_rounds=2)
        runner.set_generator(generator)

        runner.run()

        assert len(calls) == 1
        assert runner.run_data.repair_generation_results == []

    def test_hidden_only_failure_diagnostics_leak_nothing(self):
        runner = make_runner(max_repair_rounds=1)
        eval_result = EvaluationResult(
            problem_id="PROB-REPAIR",
            language="python",
            solution_code="def identity(x): ...",
            parsed=True,
            type_checked=True,
            visible_passed=1,
            visible_total=1,
            hidden_passed=0,
            hidden_total=1,
        )

        diagnostics = runner._repair_diagnostics(eval_result)

        assert "hidden test" in diagnostics
        # The hidden test for this problem maps 2 -> 2; neither may appear.
        assert "2" not in diagnostics


class TestProblemTracks:
    def test_core_track_is_default_and_unchanged(self):
        core = load_all_problems()
        assert [p.id for p in core] == [p.id for p in load_all_problems("core")]
        assert all(p.id.startswith("PROB-") for p in core)

    def test_apps_track_is_disjoint_from_core(self):
        apps = load_all_problems("apps")
        core_ids = {p.id for p in load_all_problems("core")}
        assert all(p.id.startswith("APP-") for p in apps)
        assert not core_ids & {p.id for p in apps}

    def test_all_track_is_union(self):
        combined = load_all_problems("all")
        assert len(combined) == len(load_all_problems("core")) + len(
            load_all_problems("apps")
        )

    def test_unknown_track_raises(self):
        with pytest.raises(ValueError, match="Unknown track"):
            load_all_problems("nope")

    def test_load_problem_by_id_searches_all_tracks(self):
        from benchmark.loader import load_problem_by_id

        assert load_problem_by_id("APP-001") is not None
        assert load_problem_by_id("PROB-001") is not None
        assert load_problem_by_id("APP-001", track="core") is None


class TestAppProblemSet:
    """The app-tier problems must satisfy the same validation contract as v1."""

    def test_app_problems_validate_and_canonicals_pass(self):
        from benchmark.loader import run_canonical_solutions, validate_all_problems

        apps = load_all_problems("apps")
        assert apps, "app track should not be empty"

        issues = validate_all_problems(apps)
        assert issues == {}

        results = run_canonical_solutions(apps)
        failures = {
            name: result.error_message
            for name, result in results.items()
            if not result.all_passed
        }
        assert failures == {}
