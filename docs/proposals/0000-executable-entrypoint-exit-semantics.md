# Proposal 0000: Executable Entrypoint Exit Semantics

- Status: Draft
- Authors: David Iach
- Created: 2026-07-21
- Discussion: https://github.com/davidiach/geno-lang/issues/31
- Supersedes: None

## Summary

Geno v0.5 should treat the result of the selected executable entrypoint as a
process-boundary contract. A selected `main() -> Unit` exits successfully, and
a selected `main() -> Int` supplies the process status modulo 256 without being
printed automatically. Normal nonzero results preserve prior output and do not
produce runtime traces.

The rule applies consistently to `geno run`, process-sandbox execution, the
self-hosted runner, and standalone Python and Node artifacts. Embedding APIs,
imported generated Python modules, and imported Node-targeted ESM remain
values-and-functions interfaces: they must never terminate the host process
merely because `main` returns an integer. Browser auto-start artifacts and the
default standalone JavaScript script are executable outputs, not import APIs.

This is an intentional behavior change from the documented v0.4.1 CLI contract
and therefore targets v0.5. It must not ship in a v0.4 patch release.

## Motivation

Geno v0.4.1 displays an integer returned by `main`. That is convenient for
small examples but prevents a serious command-line application from reporting
a normal failure status to CI. Applications must throw to fail, which turns an
expected result into a runtime diagnostic. In the direct interpreter path that
workaround can also lose buffered report output.

The external `geno-sitecheck` application exposed the problem. A broken-link
report should be printed in full and followed by status 2, without a traceback.
That behavior is a normal executable contract, not an exceptional language
failure.

PR #30 implemented and dogfooded the executable-boundary behavior across the
required execution paths. It landed on `main` before compatibility review
identified the minor-version requirement. The merged code is evidence, not
acceptance: `main` is unreleased staging until this proposal completes review.
If the proposal is rejected, the behavior must be reverted before the next
public release.

## Guide-Level Design

Returning `Unit` means success:

```geno
func main() -> Unit
    print("complete")
end func
```

The program prints `complete` and exits 0.

Returning an integer communicates status instead of display output:

```geno
func main() -> Int
    let broken: Int = check_links()
    print_report()
    if broken > 0 then
        return 2
    end if
    return 0
end func
```

The complete report is written before the process exits. A broken report exits
2 without a runtime trace.

A program that used the v0.4 display convention migrates from:

```geno
func main() -> Int
    return double(21)
end func
```

to:

```geno
func main() -> Unit
    print(double(21))
end func
```

It may instead print the value and return 0 when an explicit integer status is
useful to the surrounding control flow.

## Reference Design

### Entrypoint selection

Only `main` declared in the selected entry module is executable entrypoint
state. A function named `main` in an imported module is an ordinary function.
Import discovery never changes entrypoint ownership.

### Result normalization

At an executable boundary:

1. `main() -> Unit` produces status 0.
2. `main() -> Int` produces `result % 256`, in the inclusive range 0 through
   255. It produces no implicit stdout.
3. Other currently accepted `main` return types retain their v0.4 display
   behavior and status 0 for v0.5. Their future deprecation is outside this
   proposal.
4. An entry module with no `main` retains the existing successful no-op
   behavior: status 0 and no implicit output. A type error or an uncaught
   runtime error is not a normal result; existing diagnostics and nonzero
   failure behavior apply.

Modulo 256 is defined as mathematical modulo, so `-1` normalizes to 255 and
`258` normalizes to 2. The normalization is performed once, at the executable
host boundary, after the language value has been produced.

### Output and diagnostics

All output accepted before a normal `main` result must be delivered before the
host terminates. Hosts should set an exit status and return normally rather than
calling an immediate termination primitive that can discard buffered output.

A normal nonzero integer result must not be wrapped as a runtime exception and
must not emit a traceback or runtime diagnostic. Genuine uncaught errors still
produce the target's useful Geno-facing diagnostic and a nonzero process status.

### Embedding and imports

`geno.api.run()` and equivalent embedding result channels return the raw Geno
value. They never call `sys.exit`, set the embedding process status, or invoke a
Node termination primitive. An embedded integer result of 258 is returned as
258, not normalized to 2.

Generated Python invokes `main` only from its script guard. Importing the module
does not invoke `main` and cannot exit the importer. Node-targeted ESM uses a
direct-entry check for the same reason. The default JavaScript output remains a
standalone script that evaluates `main`; this proposal does not establish a
CommonJS `require()` contract. Browser-targeted ESM remains an auto-start
artifact: it evaluates `main` and, because it has no process boundary, displays
an integer result. Browser ESM does not acquire Node-only imports merely to
implement executable status behavior.

### Long-running tools

Watch mode reports the returned nonzero status for that run and continues
watching. It does not terminate the watcher. Process-sandbox execution carries
the normal result through its structured child-to-parent channel so the parent
can emit captured output before returning the normalized CLI status.

## Backend And Target Parity

- The direct interpreter returns the raw `main` value internally; the CLI owns
  status normalization and output ordering.
- Process-sandbox mode normalizes and tags the result in the isolated worker;
  the parent CLI emits captured output and returns the tagged status.
- Standalone generated Python applies the rule under `if __name__ ==
  "__main__"` only.
- The standalone generated Node script and Node-targeted ESM set the normal
  process status when directly executed. Node-targeted ESM imports remain
  inert; the standalone script does not define a CommonJS import contract.
- Browser-targeted ESM is an auto-start artifact with no process-exit boundary;
  it retains display behavior for an integer result.
- The self-hosted `run` command applies the same normalization at its own CLI
  boundary.
- Hosted callbacks and `geno.api.run()` expose the raw result and never exit the
  server or embedding process.

Capability requirements are unchanged. Exit-status handling is not a new
capability and does not authorize filesystem, process, network, environment,
time, or random access.

## Compatibility And Migration

This proposal changes observable v0.4.1 behavior for `main() -> Int`. It must be
called out as a v0.5 breaking change. The migration is to print intentional
display values explicitly and return `Unit` or a separate integer status.

Before a v0.5 release:

- retain `conformance/v0.4` unchanged;
- add `conformance/v0.5` with the new executable contract;
- add a v0.5 schema version while continuing to load the retained v0.4 schema;
- give executable run cases exact `expected_stdout` and
  `expected_exit_status` fields, plus either exact `expected_stderr` or
  `expected_stderr_contains` assertions;
- let runtime-error cases use an `expected_exit_class = "nonzero"` assertion,
  mutually exclusive with an exact status, together with required diagnostic
  substrings;
- add explicit `cli-direct` and `cli-process` targets. The runner must launch
  `geno run --unsafe` and default process-isolated `geno run` as child
  processes and compare all three host channels instead of using
  `geno.api.run()` for executable cases;
- update the compiled Python and JavaScript runners to return captured stdout,
  stderr, and status instead of treating every nonzero status as a harness
  failure;
- add v0.5 cases for Unit status 0, Int status 2, negative and overflowing
  normalization, output-before-nonzero, uncaught errors, and Python/Node parity;
- restore the versioned v0.4 specification to its historical display contract
  where necessary and publish the accepted rule in a v0.5 specification;
- update `spec.json`, the compatibility matrix, getting-started examples, and
  release notes to identify the break and migration.

The Python embedding API is behavior-compatible: it continues returning the raw
value and never terminates the host. Generated-module import behavior becomes
safer and is not a supported break.

## Security And Resource Limits

The proposal does not weaken sandboxing, capability checks, target validation,
collection limits, integer limits, or hosted network policy. An integer status
is control data at an already-authorized executable boundary.

Keeping termination out of embedding APIs prevents untrusted or library code
from exiting a host process. Direct-entry guards prevent import-time denial of
service. Normal-return status handling preserves buffered output and avoids the
data loss associated with immediate termination primitives.

Uncaught runtime errors remain distinguishable from expected application
failure. Hosts must not catch security or resource-limit failures and convert
them into a normal integer result.

## Testing And Validation

Acceptance and release require focused tests for:

- Unit status 0 and Int status 2;
- modulo behavior for negative and greater-than-255 values;
- output preservation before normal nonzero exit;
- absence of traces for expected nonzero results;
- useful diagnostics and nonzero status for uncaught runtime errors;
- raw-value, non-terminating embedding behavior;
- inert generated Python and Node-targeted ESM imports;
- direct and process-sandbox `geno run` parity;
- standalone Python and Node parity;
- self-hosted runner and watch-mode behavior;
- entry-module ownership in multi-module programs.

Normal-result cases must assert exact stdout, empty stderr, and exact status for
`cli-direct`, `cli-process`, compiled Python, and compiled JavaScript. Uncaught
runtime-error cases must assert nonzero status and required Geno-facing stderr
text; they must also assert that the expected normal-exit trace suppression has
not hidden useful diagnostics. Embedding cases continue through
`geno.api.run()` and assert the raw returned value without a process boundary.

The new v0.5 conformance cases must run on those executable targets while the
retained v0.4 corpus remains green. Targeted, optional, full, security, and
release validation must pass on Python 3.10 through 3.13 and the supported
operating systems. The external `geno-sitecheck` dogfood must print its report,
return a normal nonzero integer, and produce no traceback.

## Rollout And Observability

1. Keep this proposal in Draft for at least 14 calendar days.
2. Obtain project-lead acceptance and an independent technical review.
3. Resolve all compatibility, backend, security, and conformance questions.
4. Add the v0.5 specification, corpus, migration fixtures, and release evidence
   in reviewed implementation PRs.
5. Re-run the canonical release gate and external dogfood from built artifacts.
6. Publish only as a v0.5 prerelease with curated breaking-change notes.

Release evidence records the accepted proposal, candidate commit, CI and
release-gate runs, conformance output, dogfood result, release owner,
independent reviewer, and support owner. A post-release smoke verifies exit 0,
exit 2 with preserved output, an uncaught error, embedding, and installed
Python/Node artifacts.

If review rejects the design, revert the exit-semantics behavior on `main`
before any package or tag is published. Do not publish the behavior under a
v0.4 patch number or waive the compatibility rule.

## Alternatives

### Keep printing integer results

This preserves v0.4 behavior but leaves serious CLI programs unable to signal a
normal failure status. It does not satisfy the dogfood requirement.

### Add an `exit(status)` builtin

An explicit builtin can be useful in the future, but it introduces an effectful
termination primitive, complicates embedding safety, and does not make ordinary
functional return values useful at the executable boundary.

### Require throwing for nonzero status

This conflates expected application failure with a language/runtime error,
adds diagnostics to routine CI failures, and risks buffered-output loss.

### Add an opt-in CLI flag

A compatibility flag would fragment behavior across runners and compiled
artifacts. A versioned semantic change provides one portable contract.

### Change `main` to return `Result`

A typed application-error protocol may be worth a separate proposal. It is
more invasive and still needs a mapping from application errors to process
status.

## Unresolved Questions

1. Should a later release deprecate executable `main` return types other than
   `Unit` and `Int`, or retain their display behavior indefinitely?
2. Should the conformance manifest field be named `expected_exit_status` or
   `expected_status`, and how should diagnostic cases represent host status?
3. Should a future explicit `exit` facility exist for early termination, and
   if so, which effect and embedding restrictions should govern it?
