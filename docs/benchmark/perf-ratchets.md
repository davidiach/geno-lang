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

**Headroom of 20%.** Repeated readings of an unchanged scenario vary by around
5% here — `check-medium`, which no recent change touches, moved -5.1% between
arms of a paired A/B run. 20% is four times that noise floor, which is about as
tight as this measurement supports.

That sets a floor on what the ratchet can see, and it is worth being concrete
about where the floor lands. Removing the duplicate frontend import from the
`geno run` parent — a change worth making, and the one this harness was built
alongside — is worth 17.2% on `run-hello` (548.9 ms to 468.3 ms, paired, lowest
of 9 runs across 3 alternating passes). That is *below* the 20% threshold: the
ratchet would not have caught its reintroduction.

The conclusion is not to tighten the threshold until that example passes, which
would trade a missed regression for recurring false failures on a busy runner.
It is that a wall-clock ratchet is the wrong instrument for a regression with a
precise structural signature. `geno/tests/test_cli_run_import_footprint.py`
asserts that specific property directly — no frontend module loaded in the
`geno run` parent — and fails deterministically, on any machine, at any load.
Where a regression can be pinned to a property, assert the property; the
ratchet is the net for the drift that cannot be.

## Re-baselining sampling

`--update` measures differently from the check, for a reason worth knowing
before changing either number.

Sampling more runs *within* a pass stops helping quickly: 21 runs was no more
stable than 9 (2.7% vs 3.4% spread over three repeats). What does not settle is
drift *between* passes minutes apart, as machine load changes — single-pass
re-baselines of `run-hello` ranged over 466-515 ms on one container, a 10.5%
spread that lands directly in the recorded baseline and silently re-tunes the
ratchet's sensitivity.

So `--update` runs the suite `update_passes` times and keeps each scenario's
lowest reading. Every reading is already a minimum and noise only ever adds
time, so the lowest across passes is the best estimate of the real cost. That
took the same spread to about 1.8%. It costs roughly a minute, which is fine
for something done deliberately and rarely.

`update_runs` is also held at or above the check's `runs`, and a test enforces
that: a baseline sampled more thinly than the check lands higher, which would
loosen the ratchet a little on every re-record. The provenance comment above
the baseline table is generated from the count actually used, so it cannot
drift from the numbers underneath it.

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

- **CI wall time.** CI now caches pip downloads, collects coverage on one
  canonical matrix leg, and avoids duplicate release/LSP suites. Hosted cold-
  and warm-cache timings are not recorded yet, and `pytest-xdist` remains
  unvalidated for this suite.
- **LLM pass@1.** `experiment/` is a complete harness pointed at 79 benchmark
  problems, and no result has ever been committed
  (`docs/benchmark/llm-correctness-results.md`). This is the number closest to
  Geno's actual thesis; the thing to iterate on there is the language surface —
  diagnostics, `docs/llm-prompting.md`, error recovery — with the error-category
  breakdown as the profiler.
- **Peak memory.** Nothing measures RSS. `geno/sandbox.py` already sets
  rlimits, so the hook exists.
