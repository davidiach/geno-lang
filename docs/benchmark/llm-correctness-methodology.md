# LLM Correctness Benchmark Methodology

This document defines the reproducible run plan for a future public Geno vs
Python LLM correctness benchmark. Public frontier-model publication is deferred,
but the methodology stays tracked so a later run can be audited.

## Research Question

Does prompting frontier LLMs to write Geno produce more correct solutions than
prompting the same models to write Python for the same benchmark tasks?

## Corpus

Use the checked-in benchmark corpus under `benchmark/problems/`. A publishable
run must use all 78 problems unless the results document clearly marks the run
as a smoke test.

Before a publishable run:

```bash
python3 scripts/validate_benchmark.py
python3 scripts/run_experiment.py --config experiment/config.frontier.yaml --dry-run
```

## Conditions

Run every selected problem under every `(model, language, trial)` condition.

- Languages: `geno`, `python`
- Trials per condition: 3 minimum
- Temperature: `0.0`
- Max tokens: `2048` unless a provider requires a documented adjustment
- Execution timeout: `5.0` seconds per evaluation
- Prompting mode: zero-shot unless the config records `few_shot_examples`

The canonical config is `experiment/config.frontier.yaml`. Before running, set
the exact provider model IDs and snapshot metadata in that file. The runner
persists those fields in `results/<experiment_id>/config.json`.

Set `GENO_EXPERIMENT_REPO_REVISION` to the commit being evaluated before a
publishable run, for example with `git rev-parse HEAD`.

## Required Artifacts

A publishable result directory must include:

- `config.json`
- `generations.json`
- `evaluations.json`
- `metrics.json`
- `report.txt`
- the combined `results.json` written by `scripts/run_experiment.py --config`

The raw generation artifacts are part of the result. Do not publish only the
aggregate report.

## Prompts And Repair

The Geno prompt is `GENOTYPE_SPEC` in `experiment/prompts.py`: the rules under
"System Prompt Essentials" in `docs/llm-prompting.md` (a test keeps the two
identical) followed by a complete reference program. The program and the
few-shot samples are checked to type-check, pass their examples, and be in
`geno fmt` form, so the prompt never teaches syntax the language rejects.

Two repair conditions are reported beside pass@1, never instead of it:

- **Autofix** (on by default; `--no-autofix` or `autofix: false` turns it
  off): a Geno solution that fails to parse or type-check is run through
  `geno.fix`, which applies the fixes Geno's diagnostics carry, and evaluated
  again. No model call is made. The fixed code is recorded in
  `autofix_results`.
- **Repair** (`--max-repair-rounds N`): the model is re-prompted with the
  diagnostics of its failed attempt. A Geno attempt's errors are listed as
  `geno check` reports them, with stable code, line and column, and the title
  of the fix each carries. Hidden tests are never revealed.

## Metrics

Report:

- pass@1 for every model and language
- pass@k when `trials_per_condition > 1`
- parse success rate
- type-check success rate
- visible and hidden test pass rates
- aggregate Geno vs Python pass-rate delta
- difficulty breakdown
- error-category breakdown: syntax, type, runtime, wrong answer, timeout,
  incomplete
- Geno pass@1 after autofix, and pass after repair when repair rounds ran

## Publication Gate

Commit a result only when:

- the benchmark corpus validates cleanly
- the run config records exact model IDs and run date
- the result directory contains raw prompts, raw model responses, extracted
  code, evaluations, metrics, and report
- `docs/benchmark/llm-correctness-results.md` links the exact result directory
  and summarizes the topline claim without omitting failures
