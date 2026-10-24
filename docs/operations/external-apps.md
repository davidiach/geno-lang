# External application checks

The **External Apps** workflow tests the candidate wheel against all **101**
applications in [oskarasi/geno-corpus](https://github.com/oskarasi/geno-corpus),
pinned at `977644bbc4a7ca3b0210cf8f024d9c9c67be2b42`. It runs on pull requests,
pushes to `main`/`master`, weekly, and through manual dispatch. It complements
the in-tree examples and release gates; it does not publish packages or replace
the release workflow.

The job builds one wheel from the checked-out candidate with hash-locked build
tools. The driver creates a fresh virtual environment, installs hash-locked
runtime dependencies, and installs that exact wheel with `--no-deps`. It checks
the installed distribution version, package location, pip's recorded artifact
SHA-256, and CLI version before passing the absolute candidate executable to
the corpus harness. The corpus's historical `pin.json` still names 0.4.3; that
pin is recorded as context and never selects or installs the tested Geno.

Each application must pass `geno test`, default process-isolated `geno run`,
and Python compilation. All tiers run, without fail-fast. The driver requires
101 unique manifest IDs, complete result coverage, successful individual stages,
consistent counters, and a harness exit that agrees with the results. Compiled
Python output is not executed by this harness, and application stdout is not
checked. Backend parity and the release's separate exact-output/sitecheck checks
remain relevant.

A release that breaks source compatibility carries applications across with
`geno migrate` (proposal 0003). An application that fails as written must pass
after the candidate's own migration: the driver copies the corpus, runs the
candidate's `geno migrate --diff` and then `geno migrate` in each failing
application, and runs every tier of the copy again, where all 101 must pass.
The receipt records the as-written counts and, under `migrated`, the
applications that needed migration; each one's diff is in the logs.

The workflow uses a read-only token, disables persisted checkout credentials,
and pins both actions and corpus source. Its evidence artifact retains the
wheel, wheel hash, source and corpus revisions, installed location/environment,
commands, logs, and per-application JSON (as written and, when needed, after
migration) for 30 days, including on failure. A
failed setup can have partial evidence; absence of a passing receipt is never
a pass. The virtual environment is not uploaded.

## Local reproduction

Use Python 3.10–3.13 with the development extras installed as described in
`CONTRIBUTING.md`. From the repository root, with that environment active:

```sh
python3 -m pip install --require-hashes -r requirements-release.lock
python3 -m build --wheel --no-isolation --outdir /tmp/geno-candidate-wheel

git clone https://github.com/oskarasi/geno-corpus.git /tmp/geno-external-corpus
git -C /tmp/geno-external-corpus checkout --detach 977644bbc4a7ca3b0210cf8f024d9c9c67be2b42

python3 scripts/check_external_apps.py \
  --wheel /tmp/geno-candidate-wheel/geno_lang-0.5.0-py3-none-any.whl \
  --corpus /tmp/geno-external-corpus \
  --output /tmp/geno-external-app-evidence
```

Choose new directories for each attempt and substitute the actual candidate
wheel filename. The output directory must not already exist. Internet access
is needed for the locked dependency installation. The harness runs with a
15-minute deadline within the workflow's 20-minute job limit. CI qualifies on
Linux/Python 3.11; other local environments are recorded in their receipts.

## Updating the corpus pin

Review upstream application/harness changes before changing the immutable SHA
in both the workflow and `scripts/check_external_apps.py`. Update the expected
application count if the selected corpus changes, then run the complete corpus
against the candidate wheel. Unit tests enforce matching workflow/driver pins
and reject partial or misleading success reports. Changes to this external
corpus do not rewrite Geno's frozen language-conformance fixtures.
