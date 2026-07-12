"""
Experiment Framework
====================

Tools for running and analyzing benchmark experiments.

Usage:
    from experiment import ExperimentConfig, ExperimentRunner

    config = ExperimentConfig(
        experiment_id="my_experiment",
        models=["gpt-4"],
    )
    runner = ExperimentRunner(config)
    runner.run()
    runner.save_results()
"""

from experiment.llm_client import (
    AnthropicClient,
    GeminiClient,
    OpenAIClient,
    create_client,
)
from experiment.metrics import (
    ComparisonMetrics,
    DifficultyBreakdown,
    MetricsSummary,
    compare_results,
    compute_difficulty_breakdown,
    compute_metrics,
    format_metrics_report,
)
from experiment.runner import ExperimentConfig, ExperimentRun, ExperimentRunner

__all__ = [
    "AnthropicClient",
    "ComparisonMetrics",
    "DifficultyBreakdown",
    "ExperimentConfig",
    "ExperimentRun",
    "ExperimentRunner",
    "GeminiClient",
    "MetricsSummary",
    "OpenAIClient",
    "compare_results",
    "compute_difficulty_breakdown",
    "compute_metrics",
    "create_client",
    "format_metrics_report",
]
