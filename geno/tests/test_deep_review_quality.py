"""Negative controls for differential, benchmark, and statistical review fixes."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

from analysis.statistics import StatisticalTests, chi_square_survival
from benchmark.runner import EvaluationResult
from experiment.metrics import compare_results
from geno.tests.fuzzing import runner

_BACKENDS = ("interpreter", "compiled_py", "compiled_js")
_RUNNERS = ("_run_interpreter", "_run_compiled_python", "_run_compiled_js")


def _inject_backends(monkeypatch, failures=(), *, output="7\n", timeout=False):
    for name, function in zip(_BACKENDS, _RUNNERS):
        success = name not in failures
        result = runner.BackendResult(
            name=name,
            stdout=output if success else None,
            stderr="" if success else ("timeout" if timeout else "injected crash"),
            success=success,
            elapsed_s=0.0,
        )
        monkeypatch.setattr(
            runner,
            function,
            lambda *args, _result=result, **kwargs: _result,
        )


@pytest.mark.parametrize("failures", [(name,) for name in _BACKENDS] + [_BACKENDS])
@pytest.mark.parametrize("timeout", [False, True])
def test_every_backend_crash_or_timeout_fails_parity(monkeypatch, failures, timeout):
    _inject_backends(monkeypatch, failures, timeout=timeout)
    result = runner.run_all_backends("source", oracle="7\n")
    assert not result.match
    error = result.error
    assert error is not None
    assert all(f"{name} failed:" in error for name in failures)


@pytest.mark.parametrize("failures", [(), ("compiled_py", "compiled_js")])
def test_oracle_is_checked_even_with_only_one_success(monkeypatch, failures):
    _inject_backends(monkeypatch, failures, output="wrong\n")
    result = runner.run_all_backends("source", oracle="7\n")
    assert not result.match
    assert result.error is not None
    assert "oracle" in result.error


def test_missing_node_is_distinct_and_can_be_required(monkeypatch):
    _inject_backends(monkeypatch)
    monkeypatch.setattr(
        runner,
        "_run_compiled_js",
        lambda *args, **kwargs: runner.BackendResult(
            "compiled_js", None, "node not available", False, 0.0, available=False
        ),
    )
    assert runner.run_all_backends("source", oracle="7\n").match
    required = runner.run_all_backends("source", oracle="7\n", require_js=True)
    assert not required.match
    assert required.error is not None
    assert "unavailable" in required.error
    assert sum(b.success for b in required.backends) == 2


def test_real_print_runs_on_every_available_backend():
    result = runner.run_all_backends(
        "func main() -> Unit\n    print(7)\n    return ()\nend func\n",
        oracle="7\n",
    )
    assert result.match, result.error
    assert all(b.success for b in result.backends if b.available)
    assert sum(b.success for b in result.backends) >= 2


@pytest.mark.parametrize("failures", [(name,) for name in _BACKENDS] + [_BACKENDS])
def test_fuzz_cli_and_pytest_gate_reject_execution_failure(
    monkeypatch, tmp_path, capsys, failures
):
    pytest.importorskip("hypothesis")
    from geno.tests import test_differential_fuzzing as parity_tests
    from geno.tests.fuzzing import cli

    _inject_backends(monkeypatch, failures)
    monkeypatch.setattr(cli, "load_seed_corpus", lambda: ["source"])
    monkeypatch.setattr(cli, "load_failure_corpus", lambda: [])
    monkeypatch.setattr(cli, "save_failure", lambda result: tmp_path / "failure.geno")
    monkeypatch.setattr(parity_tests, "load_seed_corpus", lambda: ["source"])
    monkeypatch.setattr(parity_tests, "HAS_NODE", True)
    assert cli.main(["--programs", "0", "--seed", "123"]) == 1
    output = capsys.readouterr().out
    assert "FAIL" in output
    assert "Programs successful: 0" in output
    with pytest.raises(AssertionError, match="diverged"):
        parity_tests.TestSeedCorpusParity().test_seed_corpus_agrees()


def test_fuzz_cli_cannot_pass_without_any_programs(monkeypatch, capsys):
    pytest.importorskip("hypothesis")
    from geno.tests.fuzzing import cli

    monkeypatch.setattr(cli, "load_seed_corpus", lambda: [])
    monkeypatch.setattr(cli, "load_failure_corpus", lambda: [])
    assert cli.main(["--programs", "0"]) == 1
    assert "FAIL" in capsys.readouterr().out
    assert cli.main(["--replay", "/nonexistent/geno-review/*.geno"]) == 1


@pytest.fixture
def benchmark_module():
    path = Path(__file__).resolve().parents[2] / "benchmarks" / "run_benchmark.py"
    spec = importlib.util.spec_from_file_location("review_benchmark", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BENCH_SOURCE = """
@untested("review regression")
func run() -> Int
    return 1
end func
"""


@pytest.mark.parametrize("verbose", [False, True])
def test_benchmark_wrong_answer_and_compile_error_fail(
    benchmark_module, monkeypatch, capsys, verbose
):
    benchmark = benchmark_module
    monkeypatch.setattr(
        benchmark,
        "_PROBLEMS",
        [
            ("wrong", _BENCH_SOURCE, lambda: 2, lambda ns: ns["run"]),
            ("invalid", "not valid Geno", lambda: 1, lambda ns: ns["run"]),
        ],
    )
    timed = []

    def record_timing(fn):
        timed.append(fn)
        return 0.01

    monkeypatch.setattr(benchmark, "_time_fn", record_timing)
    assert not benchmark.run_benchmarks(verbose=verbose)
    assert timed == [], "Incorrect cases must not be timed"
    output = capsys.readouterr().out
    assert "MISMATCH" in output
    assert "1 mismatches, 1 errors" in output
    assert "Correctness: 0/2 problems" in output
    assert "Pass rate: 0.0%" in output
    assert "FAIL:" in output


def test_benchmark_errors_remain_in_timing_denominator(
    benchmark_module, monkeypatch, capsys
):
    benchmark = benchmark_module
    good = ("good", _BENCH_SOURCE, lambda: 1, lambda ns: ns["run"])
    monkeypatch.setattr(
        benchmark,
        "_PROBLEMS",
        [good] * 4 + [("invalid", "not valid Geno", lambda: 1, lambda ns: ns["run"])],
    )
    monkeypatch.setattr(benchmark, "_time_fn", lambda fn: 0.01)
    assert not benchmark.run_benchmarks()
    output = capsys.readouterr().out
    assert "Pass rate: 80.0%" in output
    assert "FAIL:" in output


def test_benchmark_correct_cases_retain_timing_threshold(benchmark_module, monkeypatch):
    benchmark = benchmark_module
    monkeypatch.setattr(
        benchmark,
        "_PROBLEMS",
        [("good", _BENCH_SOURCE, lambda: 1, lambda ns: ns["run"])],
    )
    monkeypatch.setattr(benchmark, "_time_fn", lambda fn: 0.01)
    assert benchmark.run_benchmarks()
    timings = iter([0.03, 0.01])
    monkeypatch.setattr(benchmark, "_time_fn", lambda fn: next(timings))
    assert not benchmark.run_benchmarks()


@pytest.mark.parametrize(
    ("a_only", "b_only", "expected"),
    [(13, 4, 0.052345063273163205), (38, 22, 0.05280751141611362), (4, 4, 1.0)],
)
def test_metrics_and_analysis_share_exact_mcnemar_tail(a_only, b_only, expected):
    passed = [True] * a_only + [False] * b_only

    def results(flags):
        return [
            EvaluationResult(
                str(i),
                "geno",
                "",
                parsed=True,
                type_checked=True,
                visible_total=1,
                visible_passed=int(flag),
            )
            for i, flag in enumerate(flags)
        ]

    comparison = compare_results(
        results(passed), results([not flag for flag in passed])
    )
    report = StatisticalTests().mcnemar_test(0, a_only, b_only, 0)
    assert comparison.mcnemar_statistic == report.statistic
    assert comparison.mcnemar_pvalue == report.p_value
    assert report.p_value == pytest.approx(expected, rel=1e-13)
    assert not report.significant


@pytest.mark.parametrize("x", [0.1, 1.0, 10.0, 100.0])
def test_chi_square_known_closed_forms(x):
    assert chi_square_survival(x, 1) == pytest.approx(math.erfc(math.sqrt(x / 2)))
    assert chi_square_survival(x, 2) == pytest.approx(math.exp(-x / 2))
    assert chi_square_survival(x, 4) == pytest.approx(math.exp(-x / 2) * (1 + x / 2))


@pytest.mark.parametrize("x", [0.0, 1e-9, 1.0, 10.0, 1e6])
def test_student_t_one_degree_matches_cauchy_tail(x):
    stats = StatisticalTests()
    expected = math.atan2(1.0, x) / math.pi
    assert stats._t_sf(x, 1) == pytest.approx(expected, rel=1e-13)
    assert stats._t_sf(-x, 1) == pytest.approx(1 - expected, rel=1e-13)


@pytest.mark.parametrize(
    ("df", "quantile"),
    [
        (1, 12.7062047361747),
        (2, 4.30265272974946),
        (5, 2.5705818356363),
        (10, 2.2281388519649385),
        (100, 1.98397151852355),
        (1000, 1.9623390808264074),
    ],
)
def test_student_t_reference_quantiles_and_inverse(df, quantile):
    stats = StatisticalTests()
    assert stats._t_sf(quantile, df) == pytest.approx(0.025, abs=2e-12)
    assert stats._t_ppf(0.975, df) == pytest.approx(quantile, rel=1e-10)
    assert stats._t_ppf(0.025, df) == pytest.approx(-quantile, rel=1e-10)
