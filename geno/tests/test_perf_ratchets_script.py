"""Tests for scripts/check_perf_ratchets.py and benchmarks/cli_latency.py.

These cover the ratchet's decision logic, not its timings: asserting on real
wall-clock numbers would make the suite fail whenever CI is busy. The
measurement itself is exercised by ``make perf-ratchets``.
"""

from pathlib import Path

import pytest

from benchmarks import cli_latency
from scripts import check_perf_ratchets as ratchets


def _suite(calibration_ms: float, **scenarios: float) -> cli_latency.SuiteResult:
    return cli_latency.SuiteResult(
        calibration_ms=calibration_ms,
        measurements=tuple(
            cli_latency.Measurement(
                name=name, min_ms=value, median_ms=value * 1.05, runs=5
            )
            for name, value in scenarios.items()
        ),
    )


def test_repo_budgets_load() -> None:
    budgets = ratchets.load_budgets()

    assert budgets.reference_ms > 0
    assert budgets.cli_latency.headroom > 1.0
    assert budgets.cli_latency.runs >= 1


def test_rebaselining_samples_at_least_as_hard_as_checking() -> None:
    # A baseline is a minimum, so a baseline sampled more thinly than the check
    # lands higher and quietly loosens the ratchet on every re-record.
    cli = ratchets.load_budgets().cli_latency

    assert cli.update_runs >= cli.runs
    assert cli.update_passes >= 1


def test_every_scenario_has_a_recorded_baseline() -> None:
    budgets = ratchets.load_budgets()
    recorded = set(budgets.cli_latency.baseline_ms)

    assert {scenario.name for scenario in cli_latency.SCENARIOS} == recorded


def test_scenario_fixtures_exist() -> None:
    for scenario in cli_latency.SCENARIOS:
        for argument in cli_latency.scenario_command(scenario):
            if argument.endswith(".geno"):
                assert Path(argument).is_file(), argument


def test_calibration_scale_never_tightens_budgets() -> None:
    # A machine twice as fast as the reference gets the recorded budget, not a
    # halved one: the calibration workload does not perfectly track CLI cost.
    assert ratchets.calibration_scale(4.0, 8.0, 4.0) == 1.0


def test_calibration_scale_relaxes_on_slower_machines() -> None:
    assert ratchets.calibration_scale(16.0, 8.0, 4.0) == pytest.approx(2.0)


def test_calibration_scale_is_capped() -> None:
    assert ratchets.calibration_scale(800.0, 8.0, 4.0) == 4.0


def test_evaluate_flags_a_regression_beyond_headroom() -> None:
    budgets = ratchets.PerfBudgets(
        reference_ms=8.0,
        max_scale=4.0,
        cli_latency=ratchets.CliLatencyBudgets(
            runs=5,
            update_runs=9,
            update_passes=3,
            warmups=2,
            headroom=1.2,
            baseline_ms={"run-hello": 100.0},
        ),
    )

    within = ratchets.evaluate(_suite(8.0, **{"run-hello": 119.0}), budgets)
    beyond = ratchets.evaluate(_suite(8.0, **{"run-hello": 121.0}), budgets)

    assert within[0].passed
    assert not beyond[0].passed
    assert beyond[0].delta_pct == pytest.approx(21.0)


def test_evaluate_scales_budgets_by_calibration() -> None:
    budgets = ratchets.PerfBudgets(
        reference_ms=8.0,
        max_scale=4.0,
        cli_latency=ratchets.CliLatencyBudgets(
            runs=5,
            update_runs=9,
            update_passes=3,
            warmups=2,
            headroom=1.2,
            baseline_ms={"run-hello": 100.0},
        ),
    )

    # Twice as slow a machine: the same 200 ms reading is now within budget.
    on_slow_machine = ratchets.evaluate(_suite(16.0, **{"run-hello": 200.0}), budgets)

    assert on_slow_machine[0].passed
    assert on_slow_machine[0].budget_ms == pytest.approx(240.0)


def test_unrecorded_scenarios_are_reported_rather_than_ignored() -> None:
    budgets = ratchets.PerfBudgets(
        reference_ms=8.0,
        max_scale=4.0,
        cli_latency=ratchets.CliLatencyBudgets(
            runs=5,
            update_runs=9,
            update_passes=3,
            warmups=2,
            headroom=1.2,
            baseline_ms={"run-hello": 100.0},
        ),
    )
    result = _suite(8.0, **{"run-hello": 100.0, "run-medium": 100.0})

    assert ratchets.missing_baselines(result, budgets) == ["run-medium"]
    assert [entry.name for entry in ratchets.evaluate(result, budgets)] == ["run-hello"]


def test_update_budget_text_rewrites_only_the_generated_regions() -> None:
    original = ratchets.BUDGET_PATH.read_text(encoding="utf-8")
    updated = ratchets.update_budget_text(
        original, _suite(9.5, **{"version": 12.34, "run-hello": 56.78})
    )

    assert "reference_ms = 9.50" in updated
    assert "version = 12.3" in updated
    assert "run-hello = 56.8" in updated
    # Comments explaining the policy must survive a re-baseline.
    assert "Re-baselining is a deliberate act." in updated
    assert "[calibration]" in updated
    assert "headroom" in updated


def test_update_budget_text_requires_the_baseline_section() -> None:
    with pytest.raises(ValueError, match="baseline_ms"):
        ratchets.update_budget_text("[calibration]\nreference_ms = 1.0\n", _suite(1.0))


def test_select_scenarios_rejects_unknown_names() -> None:
    with pytest.raises(SystemExit, match="unknown scenario"):
        cli_latency.select_scenarios(["not-a-scenario"])


def test_select_scenarios_defaults_to_the_full_suite() -> None:
    assert cli_latency.select_scenarios(None) == cli_latency.SCENARIOS
    assert cli_latency.select_scenarios([]) == cli_latency.SCENARIOS


def test_merge_suites_keeps_the_lowest_reading_of_each_scenario() -> None:
    merged = ratchets.merge_suites(
        [
            _suite(9.0, **{"version": 60.0, "run-hello": 500.0}),
            _suite(8.0, **{"version": 65.0, "run-hello": 470.0}),
            _suite(8.5, **{"version": 61.0, "run-hello": 480.0}),
        ]
    )
    by_name = {m.name: m.min_ms for m in merged.measurements}

    assert by_name == {"version": 60.0, "run-hello": 470.0}
    assert merged.calibration_ms == 8.0


def test_merge_suites_preserves_scenario_order() -> None:
    merged = ratchets.merge_suites(
        [
            _suite(8.0, **{"version": 60.0, "run-hello": 500.0}),
            _suite(8.0, **{"version": 65.0, "run-hello": 470.0}),
        ]
    )

    assert [m.name for m in merged.measurements] == ["version", "run-hello"]


def test_merge_suites_rejects_an_empty_sample() -> None:
    with pytest.raises(ValueError, match="no suite passes"):
        ratchets.merge_suites([])


def test_provenance_records_the_sample_count_actually_used() -> None:
    result = ratchets.merge_suites([_suite(8.0, **{"version": 60.0})])

    assert "of 5 runs per pass" in ratchets.render_provenance(result)


def test_update_budget_text_replaces_a_stale_provenance_comment() -> None:
    text = (
        "[calibration]\nreference_ms = 1.0\n\n"
        "# Minimum of 9 runs, recorded on Solaris / CPython 2.7.\n"
        "[cli_latency.baseline_ms]\nversion = 999.0\n"
    )

    updated = ratchets.update_budget_text(text, _suite(2.0, **{"version": 60.0}))

    assert "Solaris" not in updated
    assert "999.0" not in updated
    assert updated.count("[cli_latency.baseline_ms]") == 1
    assert "version = 60.0" in updated


def test_update_budget_text_leaves_a_following_section_intact() -> None:
    # The baseline table is currently last in the file. It should stay safe to
    # append another section after it.
    text = (
        "[calibration]\nreference_ms = 1.0\n\n"
        "# Lowest of 9 runs per pass, recorded on Linux / CPython 3.11.\n"
        "[cli_latency.baseline_ms]\nversion = 999.0\n\n"
        "[future_section]\nkeep_me = true\n"
    )

    updated = ratchets.update_budget_text(text, _suite(2.0, **{"version": 60.0}))

    assert "[future_section]\nkeep_me = true\n" in updated
    assert "999.0" not in updated
    assert updated.count("version = 60.0") == 1


def test_update_budget_text_is_idempotent() -> None:
    text = ratchets.BUDGET_PATH.read_text(encoding="utf-8")
    result = _suite(8.0, **{"version": 60.0, "run-hello": 400.0})

    once = ratchets.update_budget_text(text, result)
    twice = ratchets.update_budget_text(once, result)

    assert once == twice
    assert twice.count("[cli_latency.baseline_ms]") == 1
    assert twice.count("# Lowest of") == 1
