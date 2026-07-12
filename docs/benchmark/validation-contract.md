# Benchmark Validation Contract

`scripts/validate_benchmark.py` is the canonical integrity check for the benchmark corpus.

## What Validation Covers

Validation checks that:

- every YAML problem file loads successfully
- required problem fields are present and well-formed
- benchmark difficulty and domain metadata are valid
- canonical Geno and Python solutions execute successfully
- benchmark inputs and outputs match the schema expected by the runner

## What A Failure Means

A failing validation run means at least one of these is true:

- a benchmark problem file is malformed
- a canonical solution is broken
- runtime behavior changed and the benchmark corpus no longer matches it
- the benchmark schema, loader, and runner disagree on how values are encoded

For release purposes, all of those are correctness failures. Treat them as release blockers.

## Current Release Rule

The release gate is:

```bash
make release-check
```

That gate includes:

```bash
python3 -m pytest geno/tests/ -v
python3 scripts/validate_benchmark.py
```

## Benchmark Limitations

- The benchmark is a research corpus, not a complete behavioral specification of the language.
- Passing validation does not prove absence of runtime or sandbox bugs.
- Benchmark execution of raw Python solutions is a developer convenience, not a production sandbox.
- Raw Python benchmark execution now requires an explicit opt-in through `BenchmarkRunner.for_research()` or `allow_unsafe_python_execution=True`.
- Benchmark results should only be published from commits that pass validation cleanly.

## When To Re-Run Validation

Re-run validation whenever any of the following change:

- builtin behavior
- interpreter semantics
- capability gating
- benchmark schema or loader logic
- benchmark problem files
- experiment or evaluation logic that affects canonical execution
