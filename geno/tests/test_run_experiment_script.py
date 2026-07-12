"""Tests for scripts/run_experiment.py helpers."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("yaml", reason="pyyaml required for experiment tooling tests")

from scripts import run_experiment


def test_filter_problems_by_difficulties_deduplicates_ids(monkeypatch):
    """Selecting repeated difficulties should not duplicate problems."""
    problems = [
        SimpleNamespace(id="PROB-001"),
        SimpleNamespace(id="PROB-002"),
    ]

    def fake_filter_by_difficulty(_problems, difficulty):
        if difficulty == "easy":
            return [problems[0], problems[1]]
        if difficulty == "medium":
            return [problems[1]]
        return []

    monkeypatch.setattr(
        run_experiment, "filter_by_difficulty", fake_filter_by_difficulty
    )

    filtered = run_experiment.filter_problems_by_difficulties(
        problems, ["easy", "medium", "easy"]
    )

    assert [p.id for p in filtered] == ["PROB-001", "PROB-002"]


def test_parse_args_accepts_expert_difficulty(monkeypatch):
    """CLI should accept expert as a supported difficulty filter."""
    monkeypatch.setattr(
        "sys.argv",
        ["run_experiment.py", "--difficulties", "expert", "--dry-run"],
    )
    args = run_experiment.parse_args()
    assert args.difficulties == ["expert"]


def test_parse_args_accepts_nested_config_defaults(tmp_path, monkeypatch):
    """Config-backed defaults should keep working for the legacy nested schema."""
    config_path = tmp_path / "experiment.yaml"
    config_path.write_text(
        """
models:
  - name: "canonical"
    provider: "local"
    model_snapshot: "repo-canonical"
    temperature: 0.2
    max_tokens: 512
languages:
  - geno
benchmark:
  difficulties:
    - easy
trials:
  per_condition: 2
output:
  directory: "results/from-config"
evaluation:
  execution_timeout: 9
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        ["run_experiment.py", "--config", str(config_path), "--dry-run"],
    )
    args = run_experiment.parse_args()

    assert args.models == ["canonical"]
    assert args.temperature == 0.2
    assert args.max_tokens == 512
    assert args.difficulties == ["easy"]
    assert args.trials == 2
    assert args.output_dir == "results/from-config"
    assert args.languages == ["geno"]
    assert args.timeout_seconds == 9.0
    assert args.model_metadata == {
        "canonical": {"provider": "local", "model_snapshot": "repo-canonical"}
    }


def test_parse_args_accepts_flat_config_defaults(tmp_path, monkeypatch):
    """The documented flat config example should also work via --config."""
    config_path = tmp_path / "experiment.yaml"
    config_path.write_text(
        "\n".join(
            [
                'experiment_id: "exp_from_config"',
                "models:",
                '  - "claude-sonnet-4-6"',
                "languages:",
                '  - "geno"',
                "trials_per_condition: 7",
                "temperature: 0.2",
                "max_tokens: 4096",
                "timeout_seconds: 9.5",
                'output_dir: "results/from-config"',
                "few_shot_examples: 3",
                "difficulties:",
                '  - "easy"',
                '  - "hard"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        ["run_experiment.py", "--config", str(config_path), "--dry-run"],
    )
    args = run_experiment.parse_args()

    assert args.experiment_id == "exp_from_config"
    assert args.models == ["claude-sonnet-4-6"]
    assert args.languages == ["geno"]
    assert args.trials == 7
    assert args.temperature == 0.2
    assert args.max_tokens == 4096
    assert args.timeout_seconds == 9.5
    assert args.output_dir == "results/from-config"
    assert args.few_shot_examples == 3
    assert args.difficulties == ["easy", "hard"]


def test_parse_args_cli_overrides_config_defaults(tmp_path, monkeypatch):
    """Explicit CLI flags should override config-backed defaults."""
    config_path = tmp_path / "experiment.yaml"
    config_path.write_text(
        """
models:
  - name: "gpt-4o"
trials:
  per_condition: 5
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_experiment.py",
            "--config",
            str(config_path),
            "--models",
            "canonical",
            "--trials",
            "1",
            "--dry-run",
        ],
    )
    args = run_experiment.parse_args()

    assert args.models == ["canonical"]
    assert args.trials == 1


def test_resolve_outputs_defaults_results_json_for_config():
    """Config-driven runs should keep writing a legacy results.json by default."""
    args = SimpleNamespace(
        config=str(Path("experiment/config.example.yaml")),
        output=None,
        output_dir="results",
    )

    result_json_path, artifact_dir = run_experiment._resolve_outputs(
        args, "experiment_test"
    )

    assert result_json_path == "results.json"
    assert artifact_dir == "results"


def test_dry_run_reports_repair_inclusive_call_budget(monkeypatch, capsys):
    """The cost summary must show the repair-inclusive call ceiling."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_experiment.py",
            "--models",
            "claude-sonnet-4-6",
            "--trials",
            "1",
            "--track",
            "apps",
            "--max-repair-rounds",
            "2",
            "--dry-run",
        ],
    )

    run_experiment.main()
    out = capsys.readouterr().out

    from benchmark import load_all_problems

    initial = len(load_all_problems("apps")) * 2
    assert f"Total LLM API calls: {initial}" in out
    assert "Max repair rounds: 2" in out
    assert f"Max LLM API calls including repairs: {initial * 3}" in out
