# LLM Correctness Benchmark Results

This page records the publication status for the Geno vs Python LLM correctness
benchmark.

## Current Status

No live frontier-model result is committed, and public frontier-model
publication is currently deferred. The repo keeps the reproducible run
configuration and publication methodology ready for a future run in:

- `experiment/config.frontier.yaml`
- `docs/benchmark/llm-correctness-methodology.md`

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
