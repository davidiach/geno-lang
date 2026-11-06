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

### 0.7 (draft): an example's expected value cannot be a `panic(...)` call

Recorded for the unreleased 0.7 development line. 0.6 accepted
`example 1, 0 -> panic("division")`: `panic` returns `Never`, which every type
accepts, so the program passed `geno check` and compiled and ran with
`geno compile`, `geno build` and `geno run --no-check-examples`, none of which
evaluate examples. The example itself could never pass, because its expected
value panics before the call is made. 0.7 adds panic examples
(`example 1, 0 panics "division"`), and from then on every way of writing a
panic as an expected value (`-> panic(...)`, `-> panic`, `-> panics`,
`-> panics "m"`) is rejected with E330 and a fix that writes the new form. It
meets the five conditions above:

1. [proposal 0017](../proposals/0017-panic-examples-and-property-checks.md)
   (Draft; it must be accepted before 0.7 is released) records the motivation
   and the alternatives, including a warning instead of an error, which it
   rejects;
2. the impact is stated in `docs/spec/v0.7.md` section 5.3.1 and its "Changes
   from 0.6" list;
3. `CHANGELOG.md` `[Unreleased]` carries the migration example:

   ```geno
   // before
   example 1, 0 -> panic("division")
   // after
   example 1, 0 panics "division"
   ```

   `geno check --fix` performs it. `-> panic(e)` with a message that is not a
   string literal has no automatic fix: write the message text literally, or
   drop it (`example 1, 0 panics`);
4. no frozen case uses the form, so `conformance/v0.5` and `conformance/v0.6`
   are unchanged, and the new expectation is the development corpus's
   `diagnostic-panic-example-arrow-form` checker case; and
5. the release owner records it in release evidence when 0.7 is released.

### 0.7 (draft): examples share the isolated run's grant and budgets

Recorded for the unreleased 0.7 development line. In 0.6 the default
process-isolated `geno run` lane checked examples in an interpreter of their
own, always with `clock`, `print` and `random`, and with an output allowance
of their own the size of `--max-output-length`, separate from `main`'s. 0.7
lets that lane grant capabilities with `--cap` and gives examples exactly the
run's grant, host restrictions and cumulative budgets:

- **Output.** Output printed by examples is still never shown, but it now
  counts against the one `--max-output-length` budget that `main` then
  shares. A program whose examples print close to the limit can stop with the
  output limit before or during `main`, even without `--cap`.
- **Deadline.** Examples run in the time left of `--timeout` rather than a
  fresh allowance. The hard kill was already measured from the worker's
  launch, so only the cooperative limit is earlier; timeout messages still
  name `--timeout`.
- **Grant.** With `--cap`, examples get exactly the list: `--cap env` denies
  `print` in examples too. Without `--cap` they get the defaults, as before,
  and an example may now call `sleep_ms` (E413 in 0.6).
- **Process limits.** `--max-file-size-bytes` and the new
  `--max-total-write-bytes` default by grant, and `--unsafe` and `--json`
  refuse either flag when it is given at all, so an explicit
  `--max-file-size-bytes 0` there, which 0.6 accepted and ignored, now exits
  1.

It meets the five conditions above:

1. accepted [proposal 0013](../proposals/0013-isolated-run-capabilities.md)
   records the motivation and the alternatives, and names this enforcement
   change in its compatibility section;
2. the impact is stated in `docs/spec/v0.7.md` sections 5.3.1 and 9.11.1 and
   its "Changes from 0.6" list;
3. `CHANGELOG.md` `[Unreleased]` carries the migration: skip examples in an
   operational run, or raise the limit,

   ```bash
   geno run --no-check-examples --cap env,print main.geno -- hello
   geno run --max-output-length 200000 main.geno
   ```

   and keep effectful work in thin wrappers whose pure helpers carry the
   examples;
4. no frozen case depends on example output or example timing, so
   `conformance/v0.5` and `conformance/v0.6` are unchanged, and the
   development corpus adds the `isolated-grant-regex` and
   `isolated-grant-env-cli-args` cases, which the `cli-process` lane runs
   with the case's declared `--cap` and the compiled Python and JavaScript
   lanes run with the same grant; and
5. the release owner records it in release evidence when 0.7 is released.

### 0.7 (draft): runtime failures report a Geno stack, and contract failures name their arguments

Recorded for the unreleased 0.7 development line. Every runtime failure now
reports its Geno location and call stack on every lane and backend, and a
failed contract names the function, the call's arguments and the clause.
Diagnostic wording and stderr layout are not compatibility surfaces, and exit
statuses, codes and every existing JSON key are unchanged. Three changes are
recorded anyway, because tests and scripts can observe them:

- **What `panics "substring"` matches.** A panics example (section 5.3.1 of
  `docs/spec/v0.7.md`) matches a substring of the failure's message, and the
  contract message changes from
  `Precondition failed for safe_div: requires clause evaluated to false` to
  `Precondition failed: safe_div(a: 8, b: 0) requires b != 0`. Matching
  changes in both directions:
  - *Stops matching.* `panics "Precondition failed"` and
    `panics "Postcondition failed"` still match. Substrings of the old wording
    that the new one drops no longer match a contract failure:
    `"Precondition failed for"`, `"Postcondition failed for"`, any
    `"for <name>:"`, `"requires clause"`, `"ensures clause"` and
    `"evaluated to false"`. No corpus in the repository uses one.
  - *Starts matching.* The message now holds the argument values and the
    clause text, so a substring such as `"b != 0"`, `"0)"`, a function name
    followed by `(`, or a value that a panic's message also contains can
    match a contract failure it did not match before. A panics example that
    expected a different panic but now hits such a contract failure passes
    where it failed. To mean one panic and not a contract failure, include
    that panic's own words.

  The location and the stack never enter the message, so no file name,
  position or frame name can match.
- **Standalone artifacts and the default lane print a Geno report.** A
  standalone compiled Python or Node program prints the frames, mapped back
  to Geno source, and the `Runtime Error:` line instead of a host traceback.
  The first line of a failure's stderr is now `Stack trace (most recent call
  last):` whenever a stack exists, so a script that read the first line as the
  message should read the `Runtime Error:` line, which is always last before
  any hint. `GENO_TRACEBACK=host` restores the host traceback, after the
  report.
- **Node exits at once.** A Node entry build ends with status 1 as soon as it
  has printed the report, for an async `main` too: 0.6 let pending async work
  continue after an async `main` rejected. A rejection of an `Async` nothing
  awaits is reported the same way instead of with Node's raw stack, with the
  same status 1. Like 0.6's synchronous fatal path, the immediate exit can
  lose a tail of stdout still queued for a POSIX pipe beyond what the pipe
  accepted (typically 64 KiB); 0.6's async `main` drained it.

It meets the five conditions above:

1. [proposal 0018](../proposals/0018-runtime-failure-diagnostics.md)
   (Draft; it must be accepted before 0.7 is released) records the
   motivation and the alternatives, including keeping the 0.6 frame
   convention and putting the message first, which it rejects;
2. the impact is stated in `docs/spec/v0.7.md` sections 4.1.1, 5.1, 5.2 and
   6.9.1 and its "Changes from 0.6" list;
3. `CHANGELOG.md` `[Unreleased]` carries the migration: match a contract
   failure by its new prefix or by the structured fields,

   ```geno
   // before
   example 1, 0 panics "Precondition failed for safe_div"
   // after
   example 1, 0 panics "Precondition failed: safe_div("
   ```

   read `geno run --json`'s `stack` and `contract` instead of scraping frame
   lines, and set `GENO_TRACEBACK=host` where a host traceback is wanted;
4. no frozen case depends on contract wording, frame lines or host
   tracebacks, so `conformance/v0.5` and `conformance/v0.6` pass unchanged on
   every target, and the development corpus adds six `runtime-*` cases that
   pin the frames on `cli-process`, `cli-direct`, `python`, `js` and `js-esm`
   and the JSON `stack` with `expected_diagnostic` on `cli-json`; and
5. the release owner records it in release evidence when 0.7 is released.

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

### Development Corpus

While the next minor is being developed, its cases live in a development
corpus: `conformance/v0.7/manifest.toml`, opened by
[proposal 0017](../proposals/0017-panic-examples-and-property-checks.md) for
the Draft [`docs/spec/v0.7.md`](../spec/v0.7.md). It is marked
`status = "development"`, holds only cases new in that line, and is **not
frozen**: its cases may change with the draft until the minor is released.
`scripts/run_conformance.py --all-retained` runs it after the retained
corpora, so release and scheduled evidence keep it green, but it adds no
compatibility promise. The runner refuses a corpus newer than `spec.json`'s
`language_series` that is not marked, a marked corpus that is not newer, and
more than one development corpus. When the minor is promoted, the promotion
proposal carries the previous corpus's cases forward, drops the mark, and the
corpus is frozen like the others.

A schema 2 case may state a failing `geno run --json` envelope with an
`expected_diagnostic` table (proposal
[0018](../proposals/0018-runtime-failure-diagnostics.md)): the diagnostic's
`code`, optional `message_contains` fragments, its `location` and its `stack`
as `file:line:col` and `file:line:col in name` suffixes of the reported path,
and `stack_omitted`. On the `cli-json` lane it takes the place of
`expected_json_value` and of the stderr fragments, which describe the text
report of the lanes that own a stderr. A retained corpus that does not use
the field loads and runs unchanged.

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
