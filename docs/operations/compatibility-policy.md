# Compatibility Policy

This policy defines the source and behavior compatibility promised by Geno's
pre-1.0 releases. It is intentionally narrower than a 1.0 stability guarantee:
Geno may still improve its language design, but accepted programs must not break
casually or without migration evidence.

## Compatibility Surfaces

Geno tracks compatibility separately for:

- source syntax and static semantics
- observable interpreter, Python backend, and JavaScript backend behavior
- stable diagnostic codes (diagnostic wording is not stable)
- the documented Python embedding API
- standard-library names and signatures
- project manifests, lockfiles, and generated artifact formats

A component marked experimental or research in the maturity matrix is outside
the compatibility promise unless a frozen conformance case explicitly covers
it. Security boundaries may fail closed even when that rejects a program that
previously ran.

## Release Rules

Patch releases must preserve documented source behavior and frozen conformance
cases. A patch may tighten a security boundary without deprecation when the old
behavior permits a capability bypass, sandbox escape, unsafe host access, or
resource-limit evasion. The release notes must identify that exception.

Before 1.0, a minor release may make an intentional breaking change only when:

1. an accepted language or public-API proposal explains the motivation and
   alternatives;
2. the compatibility impact is called out explicitly;
3. the changelog includes a migration example;
4. affected frozen cases are retained under their original version, and new
   expectations are added under the new version; and
5. the release owner records the change in release evidence.

Once a surface is documented as stable, removal normally requires a deprecation
in at least one preceding minor release. Longer periods may be chosen for widely
used APIs. Deprecation warnings must name the replacement and intended removal
version when known.

## Recorded Breaking Changes

### 0.5: `main()`'s result is the process exit status

A successful `main() -> Int` no longer prints a `=> N` line. Its result becomes
the host's exit status, normalized modulo 256, at every executable boundary. This
breaks anything that read `=> N` from stdout, or that expected status 0 from a
program whose `main` returns a nonzero `Int`. It meets the five conditions above:

1. accepted proposal
   `docs/proposals/0001-executable-entrypoint-exit-semantics.md` records the
   motivation and the alternatives considered;
2. the impact is stated in `docs/spec/v0.5.md` section 4.1.1,
   `docs/deploy/cli.md` and `docs/reference/runtime-semantics.md`;
3. `CHANGELOG.md` carries the migration example;
4. `conformance/v0.4` is retained unchanged and the new expectations live in
   `conformance/v0.5`, whose schema 2 states an exit status, the stderr channel
   and the `geno run --json` envelope's raw value; and
5. the release owner records it in release evidence per
   `docs/operations/release-runbook.md`.

Nothing that is not a process boundary changed: `geno.api.run()`,
`compile_and_exec()`, the generic sandbox and the hosted server return the raw
result and never terminate their caller, and importing a generated Python or
Node ESM artifact stays inert. Browser-targeted JavaScript keeps displaying an
`Int` result, because it has no process to give a status to.

### 0.6: imports are not transitive

0.5 accepted a call to a function exported by module `A` from a program that
imported only a module `B` which imports `A`. The interpreter ran it, but
compiled Python and JavaScript failed with a name error, so the accepted
program was not portable. From 0.6, `geno check` rejects the call and suggests
`import A`; the migration is to add that import. The rule is stated in
`docs/spec/v0.6.md` section 6.10 and recorded in `CHANGELOG.md`. It was a
defect fix, so it has no proposal of its own, and the conformance corpus runs
single-file programs, so the rule is covered by
`geno/tests/test_qualified_imports.py` instead.

### 0.6: one error model

[Proposal 0009](../proposals/0009-result-only-errors.md) removes `throw`,
`try`/`catch` and the `throw` effect, and changes `fs_read_text`,
`fs_write_text`, `http_fetch` and `http_post` to return `Result`. Programs that
used them fail to check with E205 or a type error naming the `Result` type;
none changes meaning silently. `CHANGELOG.md` carries the migration steps,
`conformance/v0.5` keeps the 0.5 behavior, and `conformance/v0.6` adds the
`error-model` and `diagnostic-removed-*` cases. Embedding hosts that supply
`host_callbacks` for the four builtins need no change.

### 0.6: a coherent standard library

[Proposal 0010](../proposals/0010-coherent-standard-library.md) removes eight
duplicate or one-line builtins and makes `string_index_of` return
`Option[Int]`. Affected programs fail to check with a message naming the
replacement, and `geno migrate` rewrites them. The import and free-call rules
of the same proposal change no accepted program. `conformance/v0.6` adds the
`coherent-stdlib` and `diagnostic-removed-builtin` cases.

### 0.6: `end` and its keyword share a line

A block closer written across two lines, `end` and then `if` on the next
line, was accepted as `end if`. Because the same text is also a bare `end`
followed by a new `if` statement, it is now rejected with E201, reported at
the `end`, with a fix that writes `end if`. `geno fmt` never produced the
two-line form. `conformance/v0.6` adds the `diagnostic-bare-end` case.

## Frozen Conformance Corpus

`conformance/v0.4/manifest.toml` is the first frozen compatibility baseline. It
contains valid programs with exact stdout contracts and invalid programs with
stable diagnostic-code contracts. The runner checks positive cases before
executing them and compares behavior across every declared backend. From the
v0.5 corpus on it compares stdout, stderr and the exit status across every
declared executable boundary, which includes the three `geno run` lanes and
directly executed Node ESM, and it refuses a run whose target selection matches
no case rather than reporting an empty pass.

Run it from the repository root:

```bash
python scripts/run_conformance.py --all-retained --target all --require-node
```

Node.js is optional for local single-backend work. Release and scheduled
evidence must use `--require-node`; silently omitting a declared production
backend is not a passing all-target run.

`conformance/v0.6/manifest.toml` is the current corpus, frozen from
0.6.0-rc.1, and the v0.5 and v0.4 corpora remain in the repository unchanged.
Release qualification runs both the current corpus and at least the immediately
preceding minor corpus. Cases may be corrected only when the original fixture
was internally inconsistent; such corrections require a proposal and a changelog
entry.

## What Users Can Rely On

Within a compatible release line, a conforming program should continue to:

- parse and type-check with the same success or stable diagnostic category;
- produce the same observable value/output on its declared target;
- require no additional capabilities for the same operation; and
- preserve documented project and embedding behavior.

Performance, exact diagnostic prose, generated source formatting, and internal
implementation details are not compatibility surfaces unless a separate
published contract says otherwise.

## Reporting A Regression

Compatibility regressions are bugs. Reports should include the last working
Geno version, first failing version, target, minimal source, and observed
diagnostic or output. A confirmed regression should gain a frozen test before
the issue is closed.
