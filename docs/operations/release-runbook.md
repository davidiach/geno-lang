# Release Runbook

This runbook defines the minimum release process for Geno runtime, benchmark, and tooling changes.

## Roles

- Release owner: the engineer cutting the release and collecting release evidence.
- Reviewer: a second engineer who reviews the diff and confirms the gate results.
- Incident owner: the engineer assigned to rollback or hotfix if the release regresses.

One person may fill multiple roles for a small release, but the release owner is always explicit.

## Preconditions

- `main` is green in CI.
- The release owner has reviewed the exact diff being shipped.
- The working tree is clean before tagging or pushing release metadata.
- The release owner can reproduce the release gate locally.

## Mandatory Gate

Run the canonical release gate from repo root:

```bash
make release-check
```

This must complete successfully. The `Makefile` is the source of truth for the
exact gate; at the time of writing it covers:

- version alignment
- dependency lock and install validation
- init template scaffolding/check/test
- VS Code extension packaging
- example app validation
- builtin registry and runtime parity validation
- language spec validation
- supported target documentation validation
- ruff lint and format checks
- mypy over `geno/`
- security linting
- pytest over `geno/tests/` with coverage and per-test timeouts
- selfhost parity checks
- benchmark corpus validation

Abort the release if any gate step fails.

## Dependency Lock Policy

Python lockfiles are generated with `pip-compile --generate-hashes`. Every
requirement entry in `requirements.lock` and `requirements-dev.lock` must be
exactly pinned and must include at least one `--hash=sha256:...` content hash.

The fast dependency validator checks that direct lockfile pins still satisfy
`pyproject.toml`, that every lockfile requirement is hash-covered, and that the
VS Code package lock matches `vscode-geno/package.json`. The install gate uses
`pip install --require-hashes` in a throwaway virtual environment; this is the
stale-hash check, because pip rejects any artifact whose downloaded content no
longer matches the checked-in hash.

Do not hand-edit lockfiles. Regenerate them with the documented compile command
in each lockfile header, then run:

```bash
python3 scripts/validate_dependencies.py --check-installs
```

## Publish Artifact Metadata

PyPI publishing must validate built artifact metadata before upload. The
publish workflow runs `python -m twine check --strict dist/*` after building
artifacts and before `pypa/gh-action-pypi-publish`. The release dependency
validator checks that ordering so a workflow edit cannot bypass the metadata
gate.

## Release Steps

1. Sync to the intended release commit.

```bash
git fetch origin
git checkout main
git pull --ff-only origin main
```

2. Run the local gate.

```bash
make release-check
```

3. Review the final release diff.

- Confirm runtime/security changes are intentional.
- Confirm benchmark problem edits are intentional.
- Confirm documentation updates match the shipped behavior.
- Confirm no unrelated local changes are included.

4. Record release evidence.

- Commit SHA
- Date and time
- Release owner
- Reviewer
- `make release-check` result
- CI run URL or identifier

5. Create the release tag when applicable.

The tag must match the package version in `geno/_version.py` and use semantic
versioning (`vX.Y.Z`). For a public preview release, publish a GitHub Release
with curated notes instead of relying on the raw tag diff.

```bash
git tag -a vX.Y.Z -m "Geno vX.Y.Z"
git push origin vX.Y.Z
```

If the project is shipping by commit SHA only, record the SHA in the release notes instead of tagging.

## Post-Release Smoke Checks

Run a small smoke pass immediately after release:

```bash
python3 -m geno check examples/fibonacci.geno
python3 scripts/validate_benchmark.py
```

If the release includes hosted execution, also verify:

- a simple `geno.run()` call succeeds
- denied capabilities still fail closed
- required host callbacks are installed in the target environment

## Rollback Procedure

Never rewrite public `main` history for rollback. Use a revert or a forward fix.

1. Identify the bad release commit or tag.
2. Freeze further release activity until ownership is clear.
3. Revert the offending commit(s).

```bash
git revert <sha>
```

4. Re-run the release gate.

```bash
make release-check
```

5. Push the revert and communicate the rollback commit SHA.
6. Cut a replacement patch release only after the revert or fix is green.

## Hotfix Procedure

Use a hotfix when rollback is more damaging than a minimal forward fix.

1. Branch from the released commit.
2. Make the narrowest possible fix.
3. Re-run `make release-check`.
4. Get reviewer signoff on the hotfix diff.
5. Merge, tag, and publish a new patch release.

## Release Blockers

Do not release if any of the following are true:

- `make release-check` fails
- CI is red on `main`
- benchmark validation reports issues
- a new sandbox or capability finding is still open
- the support owner for the release is not identified
