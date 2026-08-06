# Geno performance laboratory

`run_benchmark.py` is the historical, human-readable compiled-Python suite.
Its workloads and output remain unchanged:

```powershell
python benchmarks/run_benchmark.py
python benchmarks/run_benchmark.py -v
```

`lab.py` wraps the same 77 registered workloads with correctness-first,
calibrated measurement and machine-readable evidence:

```powershell
# Real argparse help and case discovery
python benchmarks/lab.py --help
python benchmarks/lab.py --list

# Exact names and shell-style patterns select focused cases
python benchmarks/lab.py 01_fib_rec_25 "7?_*"

# Fresh-process evidence suitable for comparing candidate branches
python benchmarks/lab.py "0[1-3]_*" `
  --process-repetitions 3 `
  --json artifacts/compiled-python.json `
  --jsonl artifacts/compiled-python.jsonl
```

The laboratory applies this sequence per case and process:

1. Compile Geno to Python, compile the generated Python, and execute the module,
   recording those phases separately from workload execution.
2. Execute Geno and hand-written Python once and require equal deterministic
   results. Exceptions and mismatches stop that case before timing and make the
   run fail.
3. Apply one recorded GC policy, warm both implementations, and calibrate one
   shared loop count against the faster arm. Fast cases are amplified instead
   of being conditionally dropped at a fixed cutoff.
4. Measure paired samples with a seeded initial order and alternating A/B order.
5. Report medians, median absolute deviations, deterministic percentile-
   bootstrap 95% confidence intervals, relative effect, and the fraction of
   pairs where Geno is slower.

JSON contains run metadata, configuration, raw samples, compile samples, case
summaries, and the aggregate result. JSONL emits the same evidence as `run`,
`compile_sample`, `execution_sample`, `case_summary`, and `aggregate` records.
Metadata includes commit/dirty state, OS, CPU, Python, Node availability, and an
allowlist of active Geno resource-limit environment variables; unrelated
environment values are never captured. Artifacts are UTF-8.

Wall-clock ratios are deliberately not a mandatory CI gate. Use at least three
fresh processes and compare raw artifacts on a controlled host before treating
a change as performance evidence.

## Current scope and gaps

This is a Phase-2 foundation, not complete performance-surface coverage. It
covers generated-Python compilation and steady-state execution for the legacy
microbenchmarks. The following need separate, workload-specific laboratories:

- lexer, parser, typechecker/effect, interpreter, and JavaScript phases;
- CLI, sandbox worker, server, project-resolution, and LSP cold/warm latency;
- explicit first-execution latency distinct from correctness validation;
- reliable RSS/allocation peaks and subprocess startup decomposition;
- input-size scaling curves, adversarial slopes, and generated-code snapshots;
- baseline/candidate artifact comparison with characterized host-specific
  regression thresholds.

Generated Python byte size is recorded now so future comparisons can detect
obvious code-size regressions. Compile and host-module execution samples are
also retained individually across fresh processes rather than folded into the
steady-state workload ratio.
