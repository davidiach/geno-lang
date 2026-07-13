# Experiment: LLM Correctness Benchmark

This directory contains the infrastructure for running comparative
experiments that evaluate LLM code generation quality across Geno and
Python using a standardized benchmark suite.

## What the Experiment Measures

The experiment answers one core question: **does Geno's language design
produce higher-quality LLM-generated code compared to Python?**

Each benchmark problem is presented to one or more LLM models in both
Geno and Python. Generated solutions are evaluated on:

| Metric | Description |
|---|---|
| **Parse success rate** | Code compiles without syntax errors |
| **Type check success rate** | Code passes static type checking (Geno only) |
| **Visible test pass rate** | Passes the example test cases shown in the prompt |
| **Hidden test pass rate** | Passes held-out test cases not shown to the model |
| **Overall pass rate (pass@1)** | All tests pass on a single generation attempt |

When `trials_per_condition > 1`, pass@k can be derived from the raw
results (k successful generations out of n trials).

Statistical analysis uses McNemar's test (paired binary outcomes),
chi-square tests (error category independence), and Cohen's g (effect
size). All statistical tests are implemented in pure Python with no
scipy dependency.

## Prerequisites

### Python version

Python 3.10 or later (the codebase uses `int | None` union syntax).
CI tests against Python 3.10, 3.11, 3.12, and 3.13.

### Dependencies

```bash
# From the repository root:
pip install -r requirements.txt          # pyyaml (required)
pip install -r requirements-dev.txt      # pytest, ruff, etc. (for development)

# LLM provider SDK (install the one you need):
pip install anthropic    # for Claude models
pip install openai       # for OpenAI models
```

### API keys

Set the appropriate environment variable for your LLM provider:

```bash
export ANTHROPIC_API_KEY="<ANTHROPIC_API_KEY>"   # for Claude models
export OPENAI_API_KEY="<OPENAI_API_KEY>"         # for OpenAI models
```

**Cost warning:** A full experiment run makes
`num_problems * num_models * num_languages * trials_per_condition` API
calls. With 77 problems, 2 languages, and 5 trials that is 770 calls
per model. Estimate costs before running.

## Repository Layout

```
experiment/
  __init__.py          # Public API: ExperimentConfig, ExperimentRunner
  runner.py            # Orchestrates generation, evaluation, metrics
  metrics.py           # MetricsSummary, ComparisonMetrics, difficulty breakdown
  llm_client.py        # LLMClient (Anthropic Claude with prompt caching)
  prompts.py           # Prompt templates and Geno language spec excerpt
  README.md            # This file

benchmark/
  problems/            # 77 YAML problem definitions (PROB-001 .. PROB-077)
  schema.py            # Problem dataclass, YAML loader, validator
  runner.py            # BenchmarkRunner: evaluates generated code
  loader.py            # load_all_problems(), filter helpers, verification

analysis/
  analyzer.py          # ResultsAnalyzer: loads and analyzes saved results
  statistics.py        # McNemar, chi-square, paired t-test, CIs
  report_generator.py  # Full reports in text, Markdown, and LaTeX
  visualizations.py    # ASCII bar charts, tables, comparison diagrams

scripts/
  run_experiment.py    # CLI entry point for running experiments
  analyze_results.py   # CLI entry point for analyzing saved results
  validate_benchmark.py  # Verify benchmark problem integrity

experiment/config.example.yaml  # Flat YAML example for --config
```

## How to Run

### Step 1 -- Verify the benchmark suite

Confirm canonical solutions pass before running any LLM experiment:

```bash
python3 scripts/validate_benchmark.py
```

This loads all 77 problems, validates their structure, and executes the
canonical Geno and Python solutions against every test case.

### Step 2 -- Dry run (no API calls)

Preview the experiment configuration without making API calls:

```bash
python3 scripts/run_experiment.py \
  --config experiment/config.example.yaml \
  --models claude-sonnet-4-6 \
  --trials 5 \
  --dry-run
```

CLI flags override values loaded from `--config`.

### Step 3a -- Run with the CLI script

```bash
python3 scripts/run_experiment.py \
  --config experiment/config.example.yaml \
  --models claude-sonnet-4-6 \
  --trials 5 \
  --difficulties easy medium hard \
  --output results/my_experiment.json
```

Without a generator function wired up, the runner falls back to canonical
solutions (useful for testing the pipeline end-to-end).

### Step 3b -- Run with the LLMClient (Anthropic Claude)

```python
from experiment import ExperimentConfig, ExperimentRunner
from experiment.llm_client import LLMClient

config = ExperimentConfig(
    experiment_id="exp_claude_2026_04",
    models=["claude-sonnet-4-6"],
    languages=["geno", "python"],
    trials_per_condition=5,
    temperature=0.0,
    max_tokens=2048,
    output_dir="results/exp_claude_2026_04",
)

client = LLMClient(model="claude-sonnet-4-6")
runner = ExperimentRunner(config)
runner.set_generator(client.generate)

runner.run(progress_callback=lambda cur, total: print(f"{cur}/{total}"))
runner.save_results()

# Print cache statistics (prompt caching saves ~90% on input tokens)
print(client.cache_stats.to_dict())
```

### Step 3c -- Run with a custom generator

Any function with the signature `(model: str, prompt: str, language: str) -> str`
can be plugged in:

```python
import openai

def openai_generate(model: str, prompt: str, language: str) -> str:
    response = openai.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=2048,
    )
    return response.choices[0].message.content

runner.set_generator(openai_generate)
```

## How Model/Version/Date Are Captured

The experiment automatically records:

- **Experiment ID**: set in `ExperimentConfig.experiment_id` (convention:
  `exp_<model>_<YYYYMMDD>`)
- **Model identifier**: the `model` string passed to the generator (e.g.,
  `claude-sonnet-4-6`, `gpt-4`)
- **Start/end timestamps**: ISO-8601 in `ExperimentRun.start_time` /
  `end_time`
- **Configuration snapshot**: saved to `config.json` in the output
  directory (temperature, max_tokens, trials, problem list)
- **Per-generation metadata**: model, problem_id, language, trial index,
  generation time, token count, raw response

To pin an exact model version, use the full model identifier from your
provider (e.g., `claude-sonnet-4-6` rather than `claude-3-sonnet`).

## Where Results Are Stored

The CLI script writes one combined JSON payload to `--output`:

```
results/my_experiment.json
```

The Python API's `ExperimentRunner.save_results()` writes a directory tree
under `output_dir`:

```
results/<experiment_id>/
  config.json         # Experiment configuration snapshot
  generations.json    # All prompts and raw model responses
  evaluations.json    # Per-problem evaluation results
  metrics.json        # Aggregated metrics (pass rates, comparisons)
  report.txt          # Human-readable summary report
```

The `results/` directory is gitignored. To share results, commit the
output directory or archive it separately.

## How to Analyze Results

### Quick summary

```bash
python3 scripts/analyze_results.py \
  --input results/my_experiment.json \
  --output report.md \
  --format markdown
```

### Programmatic analysis

```python
from analysis import ResultsAnalyzer, ReportGenerator

analyzer = ResultsAnalyzer("results/exp_claude_2026_04")
analyzer.load_data()
results = analyzer.run_all_analyses()

# Print summary table
print(analyzer.get_summary_table())

# Generate full report (text, markdown, or LaTeX)
generator = ReportGenerator(analyzer)
generator.generate_full_report("report.txt")
generator.generate_markdown_report("report.md")
generator.generate_latex_tables("tables.tex")

# Save analysis JSON
analyzer.save_analysis()
```

### Statistical tests

```python
from analysis.statistics import StatisticalTests

stats = StatisticalTests(alpha=0.05)

# McNemar's test for paired pass/fail data
result = stats.mcnemar_test(
    both_pass=40, a_only=12, b_only=5, both_fail=20,
)
print(result.p_value, result.effect_size, result.interpretation)

# Wilson confidence interval for a proportion
ci = stats.wilson_confidence_interval(successes=60, total=77)
print(f"95% CI: {ci[0]:.1%} - {ci[1]:.1%}")
```

## Configuration Reference

The flat YAML example at `experiment/config.example.yaml` is accepted by
`scripts/run_experiment.py --config ...`. These fields map onto the
runner configuration used by the CLI:

| Field | Type | Default | Description |
|---|---|---|---|
| `experiment_id` | str | generated timestamp | Unique run identifier |
| `models` | list[str] | `["gpt-4"]` | Model identifiers to evaluate |
| `languages` | list[str] | `["geno", "python"]` | Target languages |
| `trials_per_condition` | int | `5` | Generations per problem/model/language |
| `temperature` | float | `0.0` | Sampling temperature |
| `max_tokens` | int | `2048` | Max response tokens |
| `timeout_seconds` | float | `5.0` | Per-evaluation execution timeout |
| `output_dir` | str | `"results"` | Artifact directory recorded in `ExperimentConfig` and used by `save_results()` in the Python API |
| `few_shot_examples` | int | `0` | Number of few-shot examples in prompt |
| `difficulties` | list[str] | all problems | CLI-side difficulty filter before building `ExperimentConfig` |

The Python API also supports `problems=[...]` directly on
`ExperimentConfig`, but that is not part of the YAML CLI schema.

## Reproducing a Run

To reproduce an experiment from a clean checkout:

```bash
git clone https://github.com/davidiach/geno-lang.git
cd geno-lang
pip install -r requirements.txt
pip install anthropic   # or openai

# Verify the benchmark suite passes
python3 scripts/validate_benchmark.py

# Set your API key
export ANTHROPIC_API_KEY="<ANTHROPIC_API_KEY>"

# Run the experiment
python3 scripts/run_experiment.py \
  --config experiment/config.example.yaml \
  --models claude-sonnet-4-6 \
  --trials 5 \
  --output results/reproduction.json

# Analyze results
python3 scripts/analyze_results.py \
  --input results/reproduction.json \
  --output report.md
```

For deterministic output set `temperature=0.0` (the default). Note that
even at temperature 0, LLM outputs are not guaranteed to be bitwise
identical across runs due to provider-side batching and quantization.

## Testing the Pipeline Without API Keys

The experiment runner includes a built-in canonical-solution test that
requires no API keys:

```bash
python3 -m experiment.runner
```

This runs all 77 problems using the canonical solutions (not LLM-generated)
and verifies the evaluation pipeline produces correct metrics. Use this to
confirm the infrastructure works before spending API credits.
