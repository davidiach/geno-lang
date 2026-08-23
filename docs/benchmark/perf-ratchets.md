# Performance Ratchets

Geno already ratchets code debt: `scripts/check_ci_dx_ratchets.py` records a
budget for each kind of escape hatch and fails when a change spends more of it.
Performance ratchets apply the same rule to runtime numbers — measure a frozen
workload, record what it costs, fail when a change makes it meaningfully worse.

The point is not the numbers themselves. It is that once a number is recorded
and enforced, improving it becomes an ordinary, repeatable task: profile, change
something, re-measure, keep the win. An unmeasured number cannot be climbed,
and an unratcheted win does not stay won.

## What is enforced today

CLI latency, via `benchmarks/cli_latency.py` and `perf-budgets.toml`:

```bash
make perf-ratchets              # measure and enforce
make perf-ratchets-update       # re-record the baselines after an intended change
```

Six scenarios run against two frozen fixtures in `benchmarks/fixtures/`:

| Scenario | What it covers |
|---|---|
| `version` | startup floor: argument dispatch, no frontend work |
| `check-medium` | resolve, parse and typecheck a 150-line program |
| `test-medium` | the above plus interpreting 14 example clauses |
| `run-hello` | process-isolated run of a minimal program — nearly pure overhead |
| `run-medium` | process-isolated run of a realistic program |
| `compile-medium` | codegen to Python |

`run-hello` is the sensitive one. It does almost no language work, so anything
that inflates startup shows up there first and largest.

## How the measurement stays honest

**Minimum, not mean.** Scheduler noise only ever adds time, so the minimum of
N runs is the most stable estimator of what the work actually costs. The median
is reported for context but is not what the ratchet checks.

**Calibration.** Each suite run also times a fixed CPU-bound workload, before
and after the scenarios, and keeps the lower reading. Budgets are scaled by
`measured / reference`, so baselines recorded on one machine survive being
checked on a slower one. Measuring calibration only after the suite would pick
up the contention the suite itself created and silently widen every budget.

The scale only ever relaxes budgets, never tightens them, and is capped at 4x.
A faster machine gets free headroom rather than a spurious failure from a
calibration workload that does not perfectly track CLI cost, and a
pathologically noisy runner cannot switch the check off entirely.

**Headroom of 20%.** Repeat suite runs vary by roughly 5% on a quiet machine.
20% absorbs that while still catching the regressions worth catching — for
scale, importing the frontend in the `geno run` parent process, which the
worker then imports again, costs about 25% on `run-hello`.

## Re-baselining

Re-recording is deliberate, and belongs in the same commit as the change that
moved the number:

```bash
python3 scripts/check_perf_ratchets.py --update
```

Record wins as well as costs. An unrecorded win is just headroom for the next
regression to hide in.

Baselines are machine-specific. Re-record them on the machine that enforces
them; calibration exists to keep the check meaningful elsewhere, not to make
one machine's numbers authoritative everywhere.

## What is not enforced yet

The compiled Geno-vs-Python ratio in `benchmarks/RESULTS.md` is the other
obvious candidate, and it is deliberately excluded. As currently defined the
metric is not portable across machines, so wiring it in as-is would produce a
failure that reflects the runner rather than the code. See "Portability of
these numbers" in that file for the measured evidence and what would have to
change first.

Other unmeasured numbers, roughly in order of value:

- **CI wall time.** No pip caching on `setup-python`, no `pytest-xdist`,
  coverage on all five matrix legs, and `release-check` re-running the full
  suite after the matrix already ran it.
- **LLM pass@1.** `experiment/` is a complete harness pointed at 79 benchmark
  problems, and no result has ever been committed
  (`docs/benchmark/llm-correctness-results.md`). This is the number closest to
  Geno's actual thesis; the thing to iterate on there is the language surface —
  diagnostics, `docs/llm-prompting.md`, error recovery — with the error-category
  breakdown as the profiler.
- **Peak memory.** Nothing measures RSS. `geno/sandbox.py` already sets
  rlimits, so the hook exists.
