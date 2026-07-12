# Local CI Workflow

This document defines the local verification workflow for contributors and
maintainers. It complements hosted GitHub Actions and gives pull requests a
reproducible validation record.

## Why This Exists

Pull requests need one documented local workflow so review does not depend on
ad hoc command selection. Hosted CI remains authoritative for merge readiness,
but local validation catches most issues before review.

## Commands

The entrypoint is:

```bash
python3 scripts/local_ci.py <mode> [options]
```

Supported modes:

- `targeted`: scoped validation for touched files and explicit test targets
- `full`: repo-wide validation
- `optional`: opt-in fuzz and property checks that mirror hosted optional
  commands
- `release`: `full` plus release-gate scripts for packaging, docs/spec,
  target metadata, self-host parity, benchmark, and templates

Examples:

```bash
# Typical subsystem PR
python3 scripts/local_ci.py targeted \
  --paths geno/lsp_server.py geno/project_resolution.py geno/tests/test_lsp.py \
  --tests geno/tests/test_lsp.py geno/tests/test_api.py

# Broad refactor or merge-readiness sweep
python3 scripts/local_ci.py full

# Optional fuzz/property confidence sweep
python3 scripts/local_ci.py optional

# Release-sensitive changes
python3 scripts/local_ci.py release
```

`make local-ci` runs `full`, and `make local-ci-release` runs `release`.

## Expectations

Minimum expectations:

- Most PRs must run `targeted` against the touched subsystem and relevant tests.
- Broad cross-cutting work should run `full`.
- Parser, typechecker, compiler, or runtime changes should consider `optional`
  when fuzz/property coverage is relevant.
- Release, scaffolding, benchmark, or packaging changes should run `release`.

`targeted` covers the scoped Python quality gates that are practical for changed
files:

- `ruff check`
- `ruff format --check`
- file-scoped `mypy` with `--follow-imports=skip`
- `compileall`
- `ruff` security lint for `geno/` paths
- explicit `pytest` targets

`full` covers the repo-wide local gates:

- repo-wide `ruff check`
- repo-wide `ruff format --check`
- repo-wide `ruff` security lint
- `AnyType` recovery and CI/DX debt ratchets
- repo-wide `pytest` with coverage and timeout settings
- top-level example type checks

`release` adds release-sensitive guardrails in `make release-check` order, with
the full gate's example type checks kept after pytest:

- version alignment and dependency/install validation
- init template, VS Code package, and example app gates
- builtin parity, language spec, supported target, self-host parity, and
  benchmark validators

`optional` covers the hosted optional/fuzz command shapes without adding them to
every local full run. It is command-level parity; local developer environments
may already include dependencies that the hosted collection job intentionally
omits.

- collect backend parity, fuzzing, property, and differential fuzzing tests
- run property, fuzzing, and differential fuzzing tests with short tracebacks
  and the hosted timeout setting

Deep fuzzing remains a manual or scheduled check via `GENO_FUZZ_DEEP=1`.

The production mypy profile in `pyproject.toml` intentionally excludes
`geno/tests/`. The staged test-typing profile lives in `mypy-tests.ini` so test
typing can tighten independently without weakening the production gate.

## CI/DX Ratchets

`scripts/check_ci_dx_ratchets.py` keeps broad CI hardening work executable
without making every PR pay for a new heavy job. The ratchet currently tracks:

- global Ruff ignores and per-file Ruff ignores
- non-test `# type: ignore` comments
- non-test functions missing parameter or return annotations
- non-test bare or `Exception` handlers
- presence of Windows, optional-runtime, LSP, VS Code, and sandbox-regression
  coverage in the hosted/local CI surfaces

The budgets are ceilings. Reducing any count should lower the matching budget in
the same change. Increasing a count requires a deliberate budget update and a
reason in review.

Function-length and complexity ratchets are intentionally deferred until the
first hot-path refactor lands behind tests. When that happens, add the measured
baseline to `scripts/check_ci_dx_ratchets.py` instead of introducing another
untracked checklist item.

## PR Traceability

Every PR should include a `Local Validation` section with:

- the exact commands that were run
- the outcome of those commands
- any intentionally skipped checks
- any expected environmental skips or local constraints that affected validation

The pull request template includes this section so the workflow stays visible in
review.
