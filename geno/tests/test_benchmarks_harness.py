"""Tests for the correctness-first plural benchmark laboratory."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks import harness, lab, surfaces

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def now(self) -> float:
        return self.value

    def function(self, duration: float):
        def run():
            self.value += duration

        return run


def test_measure_paired_calibrates_and_alternates_order():
    clock = _FakeClock()
    config = harness.MeasurementConfig(
        warmups=1,
        repetitions=4,
        target_sample_seconds=0.005,
        max_loops=100,
        bootstrap_resamples=20,
        seed=7,
    )

    loops, samples = harness.measure_paired(
        clock.function(0.001),
        clock.function(0.002),
        config=config,
        timer=clock.now,
    )

    assert loops == 5
    assert samples[0]["order"] == samples[2]["order"]
    assert samples[1]["order"] == samples[3]["order"]
    assert samples[0]["order"] == list(reversed(samples[1]["order"]))
    assert [sample["geno_seconds"] for sample in samples] == pytest.approx([0.001] * 4)
    assert [sample["python_seconds"] for sample in samples] == pytest.approx(
        [0.002] * 4
    )


def test_summary_has_dispersion_confidence_interval_and_effect_size():
    samples = [
        {"geno_seconds": geno, "python_seconds": python}
        for geno, python in ((2.0, 1.0), (2.2, 1.1), (1.8, 0.9), (2.4, 1.2))
    ]

    summary = harness.summarize_case(samples, bootstrap_resamples=100, seed=9)

    assert summary["geno"]["mad_seconds"] == pytest.approx(0.2)
    assert len(summary["geno"]["bootstrap_median_ci95_seconds"]) == 2
    assert summary["comparison"]["median_ratio"] == pytest.approx(2.0)
    assert summary["comparison"]["relative_effect_percent"] == pytest.approx(100.0)
    assert summary["comparison"]["common_language_probability_geno_slower"] == 1.0


def test_correctness_mismatch_is_a_hard_case_error_before_timing(monkeypatch):
    monkeypatch.setattr(
        lab,
        "_compile_case",
        lambda _source: ({"run": lambda: "Geno č"}, {"generated_python_bytes": 1}),
    )

    def fail_if_timed(*_args, **_kwargs):
        raise AssertionError("mismatched cases must not be timed")

    monkeypatch.setattr(lab, "measure_paired", fail_if_timed)
    problem = ("mismatch", "ignored", lambda: "Python", lambda ns: ns["run"])

    result = lab.run_case_local(problem, harness.MeasurementConfig(repetitions=2))

    assert result["status"] == "error"
    assert result["samples"] == []
    assert "correctness mismatch" in result["error"]["message"]
    assert "č" in result["error"]["message"]


def test_json_and_jsonl_artifacts_are_utf8_and_include_raw_samples(tmp_path):
    payload = harness.build_artifact(
        metadata={"cpu": "čip"},
        configuration={"gc_policy": "disabled"},
        cases=[
            {
                "name": "case",
                "status": "ok",
                "compile_samples": [{"geno_codegen_seconds": 0.1}],
                "samples": [
                    {"repetition": 0, "geno_seconds": 0.2, "python_seconds": 0.1}
                ],
                "summary": {"comparison": {"median_ratio": 2.0}},
            }
        ],
        aggregate={"success": True},
    )
    json_path = tmp_path / "raw.json"
    jsonl_path = tmp_path / "raw.jsonl"

    harness.write_json_artifact(payload, str(json_path))
    harness.write_jsonl_artifact(payload, str(jsonl_path))

    assert "čip" in json_path.read_text(encoding="utf-8")
    records = [
        json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["record_type"] for record in records] == [
        "run",
        "compile_sample",
        "execution_sample",
        "case_summary",
        "aggregate",
    ]


def test_parser_help_and_case_selection_are_real_and_deterministic():
    help_text = lab.build_parser().format_help()

    assert "--process-repetitions" in help_text
    assert "--jsonl" in help_text
    assert [problem[0] for problem in lab.select_problems(("01_*", "03_*"))] == [
        "01_fib_rec_25",
        "03_ackermann_3_6",
    ]


def test_surface_summary_has_raw_samples_dispersion_and_tails():
    result = surfaces.summarize([1, 2, 3, 4, 100])

    assert result["median"] == 3
    assert result["mad"] == 1
    assert result["p95"] == pytest.approx(80.8)
    assert result["p99"] == pytest.approx(96.16)
    assert result["samples"] == [1.0, 2.0, 3.0, 4.0, 100.0]


def test_surface_fixture_is_deterministic_and_project_is_selectable(tmp_path):
    fixture = tmp_path / "fixture"
    metadata = surfaces.make_project_fixture(fixture, module_count=3)

    assert metadata["module_count"] == 4
    assert metadata["expected_output"] == "3"
    assert (
        (fixture / "geno.toml")
        .read_text(encoding="utf-8")
        .startswith('entrypoint = "Main"')
    )
    assert "return step2(0)" in (fixture / "Main.geno").read_text(encoding="utf-8")
    help_text = surfaces.build_parser().format_help()
    assert "process_sandbox" in help_text
    assert "--fresh-process-repetitions" in help_text
    assert "--keep-fixture" in help_text


def test_surface_fresh_workers_reject_cross_process_output_nondeterminism(
    monkeypatch, tmp_path
):
    def result(python_hash, javascript_hash):
        return {
            "fresh_process_wall_ns": 1,
            "process_memory": None,
            "import_ns": 1,
            "cold": {"pipeline_ns": 1},
            "output": {
                "python_sha256": python_hash,
                "javascript_sha256": javascript_hash,
            },
            "correctness": {"passed": True},
        }

    results = iter((result("python-a", "js"), result("python-b", "js")))
    monkeypatch.setattr(
        surfaces, "_run_worker", lambda *_args, **_kwargs: next(results)
    )

    merged = surfaces._run_fresh_workers(
        tmp_path / "script.py", "project", tmp_path, 2, 2
    )

    assert merged["correctness"]["passed"] is False
    assert merged["correctness"]["fresh_process_hashes_deterministic"] is False
    assert len(merged["fresh_process_evidence"]["output_hash_pairs"]) == 2


def test_surface_timeout_kills_and_reaps_child():
    class TimedOutProcess:
        def __init__(self):
            self.communications = 0
            self.killed = False

        def communicate(self, timeout=None):
            self.communications += 1
            if self.communications == 1:
                raise subprocess.TimeoutExpired("surface", timeout)
            return "stdout", "stderr"

        def kill(self):
            self.killed = True

    process = TimedOutProcess()

    with pytest.raises(RuntimeError, match="timed out after 1 seconds"):
        surfaces._communicate_with_timeout(
            process,  # type: ignore[arg-type]
            timeout=1,
            context="test surface",
        )

    assert process.killed is True
    assert process.communications == 2


def test_cli_fresh_process_artifact_contains_metadata_compile_and_raw_samples(
    tmp_path,
):
    artifact_path = tmp_path / "benchmark.json"

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "benchmarks" / "lab.py"),
            "02_factorial_20",
            "--warmups",
            "0",
            "--repetitions",
            "2",
            "--process-repetitions",
            "2",
            "--target-time-ms",
            "0.1",
            "--max-loops",
            "100",
            "--bootstrap-resamples",
            "20",
            "--ratio-threshold",
            "999",
            "--pass-rate-target",
            "0",
            "--json",
            str(artifact_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == harness.SCHEMA_VERSION
    assert artifact["metadata"]["git"]["commit"]
    assert artifact["metadata"]["platform"]["cpu"]
    assert artifact["configuration"]["process_repetitions"] == 2
    case = artifact["cases"][0]
    assert case["correctness"] == {"checked": True, "matched": True}
    assert len(case["compile_samples"]) == 2
    assert len(case["samples"]) == 4
    assert {sample["process_index"] for sample in case["samples"]} == {0, 1}
    assert case["summary"]["compile"]["geno_codegen_seconds"]["median_seconds"]
    assert case["summary"]["execution"]["geno"]["mad_seconds"] >= 0


def test_fresh_process_worker_timeout_is_an_error_case(monkeypatch):
    def timed_out_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr(lab.subprocess, "run", timed_out_run)
    problem = lab.select_problems(["02_factorial_20"])[0]
    config = harness.MeasurementConfig(
        warmups=0,
        repetitions=1,
        target_sample_seconds=0.001,
        max_loops=1,
        bootstrap_resamples=20,
        seed=7,
    )

    case = lab.run_case_fresh_processes(problem, config, process_repetitions=2)

    assert case["status"] == "error"
    assert case["error"]["type"] == "WorkerTimeoutError"
    assert case["error"]["message"] == "worker timed out after 300s"
