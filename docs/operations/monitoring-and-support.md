# Monitoring And Support

This document defines the supported production surface, the developer-only surface, and the minimum monitoring and incident expectations for Geno.

## Support Matrix

Production-supported surfaces:

- Geno parsing, type checking, and interpretation through `geno.check()` and `geno.run()`
- The deploy-facing monitoring contract in `geno.monitoring` and `RunConfig(monitoring_hook=...)`
- Capability gating and host callback installation in `geno/api.py`
- Geno sandbox enforcement in `geno/sandbox.py`
- Module resolution through `RunConfig(modules=...)`
- The release gate `make release-check`

Developer-only surfaces:

- Benchmark execution of raw Python solutions in `benchmark/runner.py`
- Experiment orchestration in `experiment/`
- Offline result analysis and report generation in `analysis/`
- Utility scripts in `scripts/` other than the release gate inputs

Developer-only means useful, tested, and maintained, but not a promised production security boundary or public compatibility target.

## Metrics

Hosted execution should emit at least these counters or rates. The supported in-process contract is:

```python
from geno import RunConfig, RuntimeMetricsCollector, run

collector = RuntimeMetricsCollector(service="geno-api", revision="build-sha")
result = run(source, config=RunConfig(monitoring_hook=collector.record))

health_payload = collector.health_report().to_dict()
metrics_payload = collector.snapshot().to_dict()
prometheus_text = collector.snapshot().to_prometheus_text()
```

The hook receives a normalized `RunMetrics` payload on every `geno.run()` exit path, including lex/parse/type failures, timeouts, capability denials, and runtime errors. Monitoring hooks are best-effort: if the hook raises, `geno.run()` still returns its original result and emits a runtime warning instead of changing execution behavior.

Minimum counters or rates:

- Total runs
- Successful runs
- Syntax/parse error rate
- Type error rate
- Runtime error rate
- Timeout rate
- Capability denied rate
- Host callback missing rate
- Average wall-clock execution time
- Average step count if step budgets are enabled

If benchmark validation is part of a release or nightly job, also track:

- Benchmark validation success/failure
- Canonical benchmark pass rate
- Number of benchmark problems with issues

The built-in collector exposes these names in Prometheus-friendly form:

- `geno_http_post_requests_total`
- `geno_http_post_requests_by_endpoint_total`
- `geno_http_post_requests_by_status_total`
- `geno_http_post_requests_by_outcome_total`
- `geno_run_requests_total`
- `geno_run_success_total`
- `geno_run_syntax_error_total`
- `geno_run_type_error_total`
- `geno_run_runtime_error_total`
- `geno_run_timeout_total`
- `geno_run_capability_denied_total`
- `geno_run_host_callback_missing_total`
- `geno_run_security_violation_total`
- `geno_run_resource_limit_total`
- `geno_run_wall_time_ms_total`
- `geno_run_wall_time_ms_average`
- `geno_run_steps_total`
- `geno_run_steps_average`
- `geno_constrain_requests_total`
- `geno_constrain_valid_total`
- `geno_constrain_invalid_total`
- `geno_constrain_timeout_total`
- `geno_constrain_wall_time_ms_total`
- `geno_constrain_wall_time_ms_average`
- `geno_benchmark_validation_total`
- `geno_benchmark_validation_failures_total`

When you run the packaged hosted server, the HTTP metrics cover handled `/run` and `/constrain` requests, including auth, rate-limit, bad-request, and concurrency rejects. The execution metrics cover `geno.run()` results plus `/constrain` evaluations that reach constraint checking. `RunConfig(monitoring_hook=...)` remains the supported per-run hook for `geno.run()` itself.

For a minimal stdlib HTTP adapter that exposes `/healthz`, `/metrics`, and a sample `/run` endpoint, see [examples/monitoring_http_adapter.py](../../examples/monitoring_http_adapter.py).
The packaged deployment entry point is [server.py](../../geno/server.py), which is exposed as `python -m geno serve` and `geno-serve`.

## Alert Thresholds

Use these minimum thresholds unless a deployment has stricter requirements:

- Timeout rate `> 1%` for 15 minutes: warning
- Timeout rate `> 5%` for 15 minutes: page
- Runtime error rate `> 3%` for 15 minutes without a known rollout: warning
- Runtime error rate `> 10%` for 15 minutes: page
- Host callback missing count `> 0` after a release: page
- Capability denied spikes that exceed the trailing 7-day baseline by `3x`: investigate
- Benchmark validation failure on `main`: block release
- Canonical benchmark pass rate below `100%` on the release gate: block release

## Incident Severity

- `P0`: sandbox escape, capability bypass, or confirmed arbitrary host access
- `P1`: release gate broken on `main`, widespread timeout spike, or host callback failures in production
- `P2`: elevated runtime/type/parse failures with a workaround available
- `P3`: documentation drift or developer-only tooling regressions

## Triage Procedure

1. Record the failing commit SHA, environment, and first observed time.
2. Classify the incident severity.
3. Decide between rollback and hotfix using `docs/operations/release-runbook.md`.
4. Preserve failing inputs, diagnostics, and benchmark reports.
5. Add a regression test before closing the incident when the failure was code-related.

## Ownership Expectations

- Every release has one explicit release owner.
- Every production incident has one explicit incident owner.
- Security-sensitive findings are tracked against the runtime owners, not deferred to benchmark or analysis tooling owners.

## Benchmark Failure Expectations

Treat a failing `scripts/validate_benchmark.py` run as a release-blocking correctness issue. A failure means at least one of the following is true:

- a benchmark spec is malformed
- a canonical solution no longer matches the runtime or typechecker
- a runtime or builtin regression broke benchmark execution
- the benchmark schema and evaluator disagree about value encoding

Do not publish benchmark-derived claims from a commit that fails validation.
