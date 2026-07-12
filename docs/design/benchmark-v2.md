# Benchmark v2: Measuring Geno's Advantage Without a Saturation Ceiling

Status: proposal (2026-06-12). Benchmark v1 (the current 78-problem suite)
remains frozen for comparability; v2 is additive.

## Motivation

A pilot run (frontier-class model, interactive condition, 78 problems × 2
languages, single trial) scored **97.4% pass@1 in both Geno and Python — an
exact tie at the ceiling**. Two structural conclusions follow:

1. **The suite is saturated for frontier models.** At a 97%+ pass rate there
   are too few failures for McNemar's test to detect any language effect;
   a tie is the forced outcome regardless of whether Geno helps.
2. **Most observed failures were measurement artifacts, not capability
   limits.** Of four pilot failures: two traced to constructs the problems
   require but the zero-shot spec excerpt did not document (for-loop syntax,
   the named-argument rule, `slice` typing), and one to the Python prompt
   suggesting a signature (`dict[str, object]`) that crashed in the
   evaluation sandbox. All three artifacts are fixed alongside this proposal.

Raising raw difficulty alone is a treadmill: a suite tuned to be hard for
mid-2026 frontier models saturates again within a model generation, and every
re-tune breaks comparability of published numbers. v2 therefore changes what
is measured, in three additive ways.

## 1. Capability sweep (run change, no new problems)

Geno's design hypothesis — guardrails make generated code more reliable —
predicts the **Geno−Python delta grows as model capability decreases**.
Frontier-only runs cannot observe this once the ceiling is hit.

- Run the existing suite across a frontier tier *and* a small tier per
  provider (see `experiment/config.sweep.yaml`).
- Primary readout: per-model paired delta (McNemar) plotted against model
  tier, plus parse-success and typecheck-success deltas (the guardrail
  metrics where Geno's syntax design should show first).
- Falsifiable: if the delta is ~0 at every tier, the "safer language for
  LLM codegen" claim needs revision — that is worth knowing before v1.0.
- Economic headline if it holds: "a small model writing Geno matches a
  frontier model writing Python."

## 2. App-tier task track (new problems)

The v1 problems are single-function, LeetCode-style. They barely exercise
the design bets that distinguish Geno (multi-function structure, ADT-heavy
domain modeling, examples-as-tests at scale, capability-gated effects) and
they do not resemble the four product lanes in `docs/SCOPE.md`.

Add a separate **`apps/` track** of 15–25 problems, each specified as a
small behavioral contract (5–10 named behaviors + hidden tests), e.g.:

- a CSV report tool (parse, filter, aggregate, format) — pure subset of the
  `geno-check` lane
- a token-bucket rate limiter with a small stateful API
- an order-book / inventory state machine using ADTs + exhaustive match
- a config validator producing structured `Result` errors
- a text-template renderer with escaping rules

Scoring is unchanged (visible + hidden tests), but problems are expected to
need 50–200 lines and several functions/types. Difficulty tiers within the
track: `app-small`, `app-medium`.

v1 vs v2 reporting stays separate; published results name the track and
problem-set version explicitly (extends the existing publication checklist).

## 3. Repair-round condition (new metric)

Geno's pitch is "easier to **check, run, and repair**", and the pilot showed
Geno's diagnostics are unusually instructive (e.g. the named-argument error
states the exact fix). Measure that directly:

- Condition A (existing): pass@1, single shot.
- Condition B: on failure, return the compiler/test diagnostics to the model
  for **one** repair attempt; report pass@1+repair and the repair-conversion
  rate per language.

This is the closest condition to real LLM-codegen workflows, and it is the
metric where structured diagnostics + inline examples should differentiate
from Python tracebacks. Implementation: a second generation call per failed
evaluation; the runner already records per-evaluation error messages, so the
repair prompt is `original prompt + extracted code + diagnostics`.

## Sequencing

1. **Done with this proposal:** spec-excerpt fixes, sandbox-annotation fix,
   regression tests, `config.sweep.yaml`.
2. Author the app-tier problems (validation contract applies: canonical
   solutions in both languages, mechanically verified by
   `scripts/validate_benchmark.py`). Status: APP-001..015 landed — 15
   problems, reaching the bottom of the 15–25 target; further additions
   are optional extensions.
3. Implement the repair-round condition in `experiment/runner.py`
   (`max_repair_rounds: int = 0` config field; default keeps current
   behavior).
4. First publication-grade run: capability sweep on v1 + app track, both
   conditions, exact model snapshots recorded per the existing checklist.

## Non-goals

- Chasing frontier-difficulty LeetCode problems (saturates again, measures
  general capability rather than language design).
- Changing v1 problems or prompts beyond the artifact fixes above (published
  comparability).
- Agentic/multi-turn harnesses beyond one repair round (a different,
  much more expensive experiment; revisit after v2 data).
