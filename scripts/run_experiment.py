#!/usr/bin/env python3
"""
Run Geno vs Python Experiment

Runs the comparative experiment between Geno and Python code generation
using specified LLM models. Each model name is auto-routed to the
correct provider (Anthropic, OpenAI, or Google) via ``create_client``.

Prerequisites — set the API key(s) for the providers you're using:

    export ANTHROPIC_API_KEY=<ANTHROPIC_API_KEY>
    export OPENAI_API_KEY=<OPENAI_API_KEY>
    export GOOGLE_API_KEY=<GOOGLE_API_KEY>

Example usage:

    # Dry-run to see how many API calls will be made
    python scripts/run_experiment.py \\
        --models claude-sonnet-4-6 gpt-5.4 gemini-2.5-pro \\
        --trials 3 --dry-run

    # Full run (writes results to results/<experiment_id>/)
    python scripts/run_experiment.py \\
        --models claude-sonnet-4-6 gpt-5.4 gemini-2.5-pro \\
        --trials 3

    # Quick smoke-test with one model and easy problems only
    python scripts/run_experiment.py \\
        --models claude-sonnet-4-6 --trials 1 \\
        --difficulties easy --output results/smoke
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import cast

# Preserve direct `python scripts/run_experiment.py ...` usage.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmark import filter_by_difficulty, load_all_problems
from experiment import ExperimentConfig, ExperimentRunner
from experiment.llm_client import create_client


def _prefer_utf8_stdio() -> None:
    """Keep report output writable on non-UTF-8 Windows locales."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def filter_problems_by_difficulties(problems, difficulties):
    """Filter problems by difficulty and de-duplicate by problem ID."""
    if not difficulties:
        return problems

    filtered = []
    seen_ids = set()
    for diff in difficulties:
        for problem in filter_by_difficulty(problems, diff):
            if problem.id in seen_ids:
                continue
            seen_ids.add(problem.id)
            filtered.append(problem)
    return filtered


def _shared_model_setting(model_entries, key):
    """Return a shared per-model setting, or None if models disagree."""
    values = {
        entry[key]
        for entry in model_entries
        if isinstance(entry, dict) and entry.get(key) is not None
    }
    if len(values) == 1:
        return values.pop()
    return None


def _load_config_defaults(config_path):
    """Load CLI defaults from a YAML experiment config."""
    if not config_path:
        return {}

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError("pyyaml is required to use --config") from exc

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    if not isinstance(config, dict):
        raise ValueError("Experiment config must be a YAML mapping")

    defaults: dict[str, object] = {}
    if config.get("experiment_id") is not None:
        defaults["experiment_id"] = str(config["experiment_id"])

    model_entries = config.get("models") or []
    model_names = []
    model_metadata: dict[str, dict[str, str]] = {}
    for entry in model_entries:
        if isinstance(entry, dict):
            name = entry.get("name")
            if name:
                model_name = str(name)
                model_names.append(model_name)
                metadata = {
                    str(key): str(value)
                    for key, value in entry.items()
                    if key not in {"name", "temperature", "max_tokens"}
                    and value is not None
                }
                if metadata:
                    model_metadata[model_name] = metadata
        elif entry:
            model_names.append(str(entry))
    if model_names:
        defaults["models"] = model_names
    if model_metadata:
        defaults["model_metadata"] = model_metadata

    benchmark_config = config.get("benchmark") or {}
    difficulties = benchmark_config.get("difficulties")
    if difficulties is None:
        difficulties = config.get("difficulties")
    if difficulties:
        defaults["difficulties"] = list(difficulties)

    trials_config = config.get("trials") or {}
    trials = trials_config.get("per_condition")
    if trials is None:
        trials = config.get("trials_per_condition")
    if trials is not None:
        defaults["trials"] = int(trials)

    temperature = _shared_model_setting(model_entries, "temperature")
    if temperature is None and config.get("temperature") is not None:
        temperature = config["temperature"]
    if temperature is not None:
        defaults["temperature"] = float(temperature)

    max_tokens = _shared_model_setting(model_entries, "max_tokens")
    if max_tokens is None and config.get("max_tokens") is not None:
        max_tokens = config["max_tokens"]
    if max_tokens is not None:
        defaults["max_tokens"] = int(max_tokens)

    output_config = config.get("output") or {}
    output_dir = output_config.get("directory")
    if output_dir is None:
        output_dir = config.get("output_dir")
    if output_dir:
        defaults["output_dir"] = output_dir

    if config.get("languages"):
        defaults["languages"] = list(config["languages"])

    evaluation_config = config.get("evaluation") or {}
    timeout_seconds = evaluation_config.get("execution_timeout")
    if timeout_seconds is None:
        timeout_seconds = trials_config.get("timeout")
    if timeout_seconds is None:
        timeout_seconds = config.get("timeout_seconds")
    if timeout_seconds is not None:
        defaults["timeout_seconds"] = float(timeout_seconds)

    if config.get("few_shot_examples") is not None:
        defaults["few_shot_examples"] = int(config["few_shot_examples"])

    if config.get("max_repair_rounds") is not None:
        defaults["max_repair_rounds"] = int(config["max_repair_rounds"])

    if config.get("track"):
        defaults["track"] = str(config["track"])

    return defaults


def _build_parser(defaults):
    """Build the argument parser with optional config-backed defaults."""
    parser = argparse.ArgumentParser(
        description="Run Geno vs Python code generation experiment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Supported model prefixes:\n"
            "  claude-*   → Anthropic  (needs ANTHROPIC_API_KEY)\n"
            "  gpt-*, o1* → OpenAI     (needs OPENAI_API_KEY)\n"
            "  gemini-*   → Google     (needs GOOGLE_API_KEY)\n"
        ),
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to experiment configuration YAML file",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=defaults.get("models", ["claude-sonnet-4-6"]),
        help="LLM models to evaluate (default: claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--difficulties",
        nargs="+",
        choices=["trivial", "easy", "medium", "hard", "expert"],
        default=defaults.get("difficulties"),
        help="Filter by difficulty (default: all)",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=defaults.get("trials", 3),
        help="Number of trials per condition (default: 3)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=defaults.get("temperature", 0.0),
        help="Sampling temperature (default: 0.0)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=defaults.get("max_tokens", 2048),
        help="Max tokens per generation (default: 2048)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Results output path. Paths ending in .json write a legacy single "
            "results JSON file; other paths are treated as output directories."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=defaults.get("output_dir"),
        help="Directory for multi-file experiment artifacts",
    )
    parser.add_argument(
        "--track",
        choices=["core", "apps", "all"],
        default=defaults.get("track", "core"),
        help="Problem track: core (frozen v1), apps (v2 app tier), or all",
    )
    parser.add_argument(
        "--max-repair-rounds",
        type=int,
        default=defaults.get("max_repair_rounds", 0),
        help=(
            "Diagnostics-guided repair attempts after a failed evaluation "
            "(default: 0, pure pass@1)"
        ),
    )
    parser.add_argument(
        "--canonical",
        action="store_true",
        help="Use canonical solutions instead of LLM calls (for testing the pipeline)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show configuration without running experiment",
    )
    return parser


def parse_args(argv=None):
    """Parse command line arguments."""
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=str)
    pre_args, _ = pre_parser.parse_known_args(argv)

    try:
        defaults = _load_config_defaults(pre_args.config)
    except (FileNotFoundError, ImportError, OSError, ValueError) as exc:
        _build_parser({}).error(str(exc))

    parser = _build_parser(defaults)
    args = parser.parse_args(argv)
    args.languages = defaults.get("languages", ["geno", "python"])
    args.timeout_seconds = defaults.get("timeout_seconds", 5.0)
    args.experiment_id = defaults.get("experiment_id")
    args.few_shot_examples = defaults.get("few_shot_examples", 0)
    args.model_metadata = defaults.get("model_metadata", {})
    return args


def _wants_json_output(path):
    """Return True when a path should be treated as a legacy JSON output file."""
    return Path(path).suffix.lower() == ".json"


def _resolve_outputs(args, experiment_id):
    """Resolve legacy JSON output and artifact directory paths."""
    default_artifact_dir = str(Path("results") / experiment_id)
    result_json_path = None

    if args.output and _wants_json_output(args.output):
        result_json_path = args.output
        artifact_dir = args.output_dir
    else:
        artifact_dir = args.output_dir or args.output

    if args.config and result_json_path is None and args.output is None:
        result_json_path = "results.json"

    if artifact_dir is None and result_json_path is None:
        artifact_dir = default_artifact_dir

    return result_json_path, artifact_dir or default_artifact_dir


def _use_canonical_mode(models, explicit_flag):
    """Decide whether to run against canonical solutions instead of live models."""
    if explicit_flag:
        return True
    if models == ["canonical"]:
        return True
    if "canonical" in models:
        raise ValueError(
            "Model 'canonical' cannot be combined with provider-backed models. "
            "Use only --models canonical, or pass --canonical with explicit labels."
        )
    return False


def _current_repo_revision() -> str:
    """Return the caller-provided git commit for publication metadata."""
    return os.environ.get("GENO_EXPERIMENT_REPO_REVISION", "")


def main():
    """Run the experiment."""
    _prefer_utf8_stdio()
    args = parse_args()

    print("=" * 60)
    print("GENO BENCHMARK EXPERIMENT RUNNER")
    print("=" * 60)
    print(f"Started: {datetime.now().isoformat()}")
    print()

    # Load problems
    print("Loading benchmark problems...")
    problems = load_all_problems(track=args.track)
    print(f"  Loaded {len(problems)} total problems")

    # Filter by difficulty if specified
    if args.difficulties:
        problems = filter_problems_by_difficulties(problems, args.difficulties)
        print(
            f"  Filtered to {len(problems)} problems ({', '.join(args.difficulties)})"
        )

    print()

    # Create experiment ID and output dir
    experiment_id = (
        args.experiment_id or f"experiment_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    result_json_path, output_dir = _resolve_outputs(args, experiment_id)
    try:
        use_canonical = _use_canonical_mode(args.models, args.canonical)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    # Create configuration
    config = ExperimentConfig(
        experiment_id=experiment_id,
        problems=problems,
        models=args.models,
        languages=args.languages,
        trials_per_condition=args.trials,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout_seconds=args.timeout_seconds,
        output_dir=output_dir,
        few_shot_examples=args.few_shot_examples,
        max_repair_rounds=args.max_repair_rounds,
        repo_revision=_current_repo_revision(),
        model_metadata={
            model: args.model_metadata.get(model, {}) for model in args.models
        },
    )

    # Show configuration
    total_calls = len(problems) * len(args.models) * len(args.languages) * args.trials
    print("Experiment Configuration:")
    print(f"  Experiment ID: {experiment_id}")
    print(f"  Problems: {len(problems)}")
    print(f"  Models: {', '.join(args.models)}")
    print(f"  Languages: {', '.join(args.languages)}")
    print(f"  Trials per condition: {args.trials}")
    print(f"  Temperature: {args.temperature}")
    print(f"  Total evaluations: {total_calls}")
    if not use_canonical:
        print(f"  Total LLM API calls: {total_calls}")
        if args.max_repair_rounds > 0:
            max_calls = total_calls * (1 + args.max_repair_rounds)
            print(f"  Max repair rounds: {args.max_repair_rounds}")
            print(f"  Max LLM API calls including repairs: {max_calls}")
    if result_json_path:
        print(f"  Results JSON: {result_json_path}")
    if output_dir:
        print(f"  Artifacts Dir: {output_dir}")
    print()

    if args.dry_run:
        print("Dry run - not executing experiment")
        return

    # Create runner
    print("Initializing experiment runner...")
    runner = ExperimentRunner(config)

    # Wire up LLM client (unless using canonical solutions)
    if not use_canonical:
        # Create a client per model, keyed by model name
        clients = {}
        for model_name in args.models:
            try:
                clients[model_name] = create_client(
                    model_name,
                    max_tokens=args.max_tokens,
                    temperature=args.temperature,
                )
                print(f"  {model_name}: {type(clients[model_name]).__name__}")
            except ImportError as e:
                print(f"  ERROR: {e}")
                sys.exit(1)
            except ValueError as e:
                print(f"  ERROR: {e}")
                sys.exit(1)

        # The generator dispatches to the right client per model
        def generate(model: str, prompt: str, language: str) -> str:
            return cast(str, clients[model].generate(model, prompt, language))

        runner.set_generator(generate)
    else:
        print("  Using canonical solutions (no LLM calls)")

    print()

    # Run experiment
    print("Running experiment...")
    print("-" * 60)

    def progress(current, total):
        pct = 100 * current / total
        print(f"\r  Progress: {current}/{total} ({pct:.1f}%)", end="", flush=True)

    results = runner.run(progress_callback=progress)
    print()
    print("-" * 60)
    print()

    # Save results
    if output_dir:
        runner.save_results(output_dir)
        print()
    if result_json_path:
        result_path = Path(result_json_path)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Saving combined results to {result_path}...")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str)
        print("  Done!")
        print()

    # Print report
    print(runner._generate_report())

    # Print cache stats
    if not use_canonical and clients:
        print("\nCache / Token Statistics:")
        for model_name, client in clients.items():
            stats = client.cache_stats.to_dict()
            print(
                f"  {model_name}: {stats['requests']} requests, "
                f"{stats['input_tokens']} input tokens, "
                f"{stats['output_tokens']} output tokens"
            )
            if stats["cache_hit_rate"] > 0:
                print(f"    Cache hit rate: {stats['cache_hit_rate']:.1%}")

    print(f"\nCompleted: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
