# Geno vs Python Benchmark Results

No live frontier-model Geno-vs-Python result set is published in this checkout,
and public frontier-model publication is currently deferred.

The benchmark problem set itself is mechanically validated with:

```bash
python scripts/validate_benchmark.py
```

Publication rules:

- Publish only reports generated from raw experiment artifacts.
- Keep raw prompts, raw model responses, extracted code, evaluations, and
  aggregate metrics with the report.
- Do not state a Geno advantage until at least one real frontier-model run has
  a generated report and this page links the raw artifact directory.

Generate a report from a completed experiment with:

```bash
python scripts/publish_benchmark_results.py \
  --input results.json \
  --output docs/benchmark/results.md
```
