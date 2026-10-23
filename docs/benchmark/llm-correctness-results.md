# LLM Correctness Benchmark Results

This page records the publication status for the Geno vs Python LLM correctness
benchmark.

## Current Status

No live frontier-model result is committed yet: a run needs provider API keys
for at least three frontier models. The harness is ready for Geno 0.6:

- the Geno prompt carries the documented 0.6 rules (kept identical to
  `docs/llm-prompting.md` by a test) and a reference program that type-checks
  and passes its examples;
- repair prompts list Geno errors as `geno check` reports them: stable code,
  line and column, and the fix each carries;
- every run records pass@1 after `geno.fix` (the "autofix" metric) beside
  pure pass@1, with no extra model call.

The reproducible run configuration and methodology are in:

- `experiment/config.frontier.yaml`
- `docs/benchmark/llm-correctness-methodology.md`

## Key-Free Result: Seeded Mistakes

`docs/benchmark/seeded-mistakes.md` is a result anyone can reproduce without a
model: 27 mistakes that models carry over from other languages (`let mut`,
`True`, `&&`, `elif`, `len(xs)`, an implicit return, ...) or make by slip are
seeded into each of the 78 reference solutions, and the 840 mutants are
checked and repaired with `geno.fix`. Every mutant is rejected and reported on
the line of its mistake; the fixes repair all 684 mutants of the 25 mistakes
that have a fix, and none of the 156 mutants of the two that do not (a missing
return type, missing `example` clauses). No repaired program fails a test. It
measures the repair path only, not how often models make each mistake;
regenerate it with `python3 scripts/seeded_mistakes.py --write`.

Local `results/experiment_*` directories in development checkouts may contain
smoke or canonical-generator artifacts. They are not published benchmark
evidence unless this page links them explicitly.

## Future Reproduction Command

After updating `experiment/config.frontier.yaml` with exact provider model IDs:

```bash
python3 scripts/validate_benchmark.py
python3 scripts/run_experiment.py --config experiment/config.frontier.yaml
```

The config-driven run writes a combined `results.json` plus a full artifact
directory under `results/<experiment_id>/`. Do not publish the report until the
checklist below is complete.

## Publication Checklist

- [ ] Results for at least three frontier models
- [ ] Exact model IDs and snapshots recorded
- [ ] Run date and repo revision recorded
- [ ] pass@1 reported for every model and language
- [ ] pass@k reported when multiple trials are available
- [ ] Difficulty breakdown included
- [ ] Error-category breakdown included
- [ ] Geno vs Python comparison summarized
- [ ] Raw prompts, raw responses, extracted code, evaluations, and metrics
      committed or linked from the result directory

## Topline Result

Deferred. No public Geno-vs-Python correctness claim is made from this checkout.
