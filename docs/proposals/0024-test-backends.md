# Proposal 0024: Run `geno test` On The Compiled Backends

- Status: Draft
- Authors: Claude Code (drafted and implemented at the project lead's request)
- Created: 2026-11-12
- Discussion: https://github.com/davidiach/geno-lang/issues/239
- Supersedes: None
- Review: None yet. No independent human technical review has taken place.
- Target: the 0.7 development line (`docs/spec/v0.7.md`, Draft)

## Summary

`geno test --backend interpreter|python|js|all` chooses where a program's
examples and `test` blocks run. The default, `interpreter`, is what `geno
test` has always done. `python` and `js` compile the file or project the way
`geno compile` does and run the same examples and test blocks in the compiled
code. `all` runs the three and reports every case whose outcome differs
between them.

```console
$ geno test --backend all .
Backend: interpreter
  PASS  Maths.geno (12/12)
Backend: python
  PASS  Maths.geno (12/12)
Backend: js
  FAIL  Maths.geno (11/12)

Divergences (the backends disagree)
  x Maths.geno: nothing, example 1
        interpreter: passed
        python: passed
        js: failed: expected (), got ()

interpreter: 12 passed, 0 failed
python: 12 passed, 0 failed
js: 11 passed, 1 failed
1 divergence, in 0.41s
```

That output is real: it is the first thing the flag found in this repository
(see Testing And Validation).

## Motivation

Users ship compiled Python and JavaScript, and `geno test` checked neither.
Several 0.6 release-candidate bugs were differences between backends that an
interpreter-only test run cannot see in a user's project (#188, #191, #194),
and the 0.7 line found more in compiled JavaScript (#241, #242, and the
shadowed-parameter loop in #261). The project's own parity suite catches
these for the programs it contains. A user's program is not in it.

## Guide-Level Design

Nothing changes unless the flag is given. With it:

- `geno test --backend python` and `--backend js` report in the same format,
  under a `Backend:` line.
- `geno test --backend all` prints each backend's results, then a
  `Divergences` block, then one summary line per backend. It exits nonzero
  when any backend has a failure or the backends disagree.
- `--filter`, `--timeout`, `--json`, `--target`, `--watch`, `-v` and
  `--fail-on-untested` work as before.
- `--properties` runs on the interpreter only; combining it with another
  backend is an error.
- `--backend js` needs `node`. Without it the run fails with a message that
  says so.

## Reference Design

### 1. What runs

Every `example` clause, panics examples included, of every top-level function
and `impl` method, and every `test` block: the cases the interpreter runs. For
a project, every module's; for a single file, that file's. `--filter` selects
by the same names on every backend, including an impl method's
`Target.Trait.method`, `Target.method` and `method` forms.

`main` is not called, as under the interpreter.

### 2. How a compiled run is built

The file or project goes through the pipeline `geno compile` uses: resolve,
type check, compile. The compiler is put in a test-harness mode, which
differs from an ordinary build in three ways:

- After each function, each of its examples is registered: a thunk for its
  arguments, the function, and a thunk for its expected value. An Int written
  for a Float parameter or result is widened as a call site would widen it.
- Each `test` block is compiled as a function and registered. `assert` is
  compiled only here.
- The call of `main` is left out.

An ordinary build is unchanged: it contains no registry, no test block and no
`assert`.

Because the examples are compiled by the backend under test, an example's own
expressions are tested there too.

### 3. How it is run

A small driver runs the selected cases in a subprocess: `python` with the
interpreter running `geno`, or `node`. For each case it evaluates the
arguments, then the expected value, then makes the call, and writes one line
of JSON to a results file. The parent decides what each record means.

- **Equality** is Geno's, with the tolerance `geno test` gives a Float
  (relative `1e-9`). On JavaScript, where a value does not say whether it is a
  Float, the result's static type does.
- **Rendering.** A failure shows `expected X, got Y` with each backend's own
  diagnostic formatter, which writes values as the interpreter does.
- **Panics examples** are decided by the rule of proposal 0017: only a panic
  in the call satisfies one. A resource limit, a timeout or a host refusal
  does not, and neither does a panic while evaluating the inputs.
- **Contracts.** A failed `requires` or `ensures` is reported as that kind.

### 4. Limits and capabilities

- **Capabilities.** The subprocess is granted what `geno test` grants (`clock`,
  `print`, `random`) and nothing else.
- **Timeout.** Each case has `--timeout` seconds, enforced by the parent: a
  case that runs past it has its process killed and is reported as timed out,
  and the remaining cases continue in a new process. Starting the host and
  loading the program is not charged to a case.
- **Steps.** `--max-steps` counts interpreter steps and does not apply to
  compiled code. The timeout is what bounds a compiled case.
- **A crash** of the host (not a Geno failure) fails the case that was
  running and the rest continue.
- **A failure before any case runs** (the program does not compile for that
  backend, the host is missing, loading it throws) is one error for the file,
  as a load error is on the interpreter.

### 5. `all` and divergences

`all` runs the interpreter, then compiled Python, then compiled JavaScript,
and compares the outcome of each case across the backends that ran it. A case
**diverges** when:

- it passes on one backend and fails on another; or
- it fails on every backend by returning a different value on each.

Two failures with differently worded runtime errors are the same outcome: the
backends word some errors differently (`Index 2 out of bounds for list of
length 1` and `Index 2 out of bounds`). A case one backend could not run,
because its file failed to load there, is not compared; that error is
reported on its own.

A divergence fails the run.

### 6. JSON

A plain run's payload is unchanged. With `--backend python` or `--backend js`
the payload gains `backend`, on the payload and on each file entry; other
keys keep their values.

`--backend all` prints:

```json
{
  "backend": "all",
  "backends": {"interpreter": {...}, "python": {...}, "js": {...}},
  "divergences": [
    {"path": "Maths.geno", "kind": "example", "function": "nothing",
     "ordinal": 0,
     "outcomes": {"interpreter": "passed", "python": "passed",
                  "js": "failed: expected (), got ()"}}
  ],
  "total": 36, "passed": 35, "failed": 1, "errors": 0, "untested": 0,
  "success": false
}
```

Each entry of `backends` is that backend's payload, with its `backend` keys
(the interpreter's too). `ordinal` is the
example's position among its function's examples, from 0.

## Backend And Target Parity

This proposal is a tool for parity and adds no language behavior. One runtime
fix ships with it, found by the flag: on JavaScript a `Unit` function that
ended without a `return` yielded `undefined`, which did not equal `()`. Both
are now the one `Unit` value.

## Compatibility And Migration

Additive. Without `--backend` the command behaves as before, and its `--json`
payload is byte-for-byte what it was.

## Security And Resource Limits

A compiled run executes the program's code in a subprocess with the test
grant, as `geno compile` output run with `--cap clock,print,random` would.
It is not the isolated `geno run` lane: there is no memory or CPU cap, and
compiled JavaScript has no sandbox (see `SECURITY.md`). `geno test` already
runs the same code in-process on the interpreter, so the trust boundary is the
same: run it on code you trust.

The driver reaches the program only through the registry and the runtime's
own helpers. The Python launcher runs in a namespace of its own. The results
file and the plan are in a private temporary directory.

## Testing And Validation

- `geno/tests/test_test_backends.py`: one file with every kind of outcome
  (passes, wrong values, panics examples, `requires` and `ensures` failures,
  defaults, a tuple parameter, Float widening, an impl method, test blocks)
  reports the same passes and failures on each backend; a case past its
  timeout is killed and the rest run; limits and refusals never satisfy a
  panics example; `--filter`; a project, module by module, with `main` not
  run; a real divergence (stack depth); the divergence rule; load failures;
  a missing `node`; `--properties`; JSON and text output.
- Running `geno test --backend all examples/` over this repository's 402
  examples found two things. The JavaScript `Unit` difference above, fixed
  here. And seven examples of `examples/apps/geno-mark/Renderer.geno` that
  fail on the interpreter when the file is tested outside its project, while
  both compiled backends pass them; that one predates this proposal and is
  not fixed here.

## Rollout And Observability

Ships in the 0.7 development line as Experimental
([maturity matrix](../MATURITY.md)): the report and JSON formats may change.

## Alternatives

- **Evaluate examples on the interpreter and pass the values to compiled
  functions.** Simpler, and what the benchmark harness does for Python, but it
  does not test the example's own expressions on the backend, cannot work for
  JavaScript, and needs a value conversion that can itself hide a difference.
- **A harness written in Geno.** Geno cannot catch a panic, so one failing
  example would end the run.
- **One process per case.** Simplest isolation, but a project with hundreds
  of examples would pay the host's start-up hundreds of times.
- **Ask users to write parity tests.** Nobody does.

## Unresolved Questions

1. **Locations and stacks.** A compiled failure reports its message but not
   its Geno location or stack, which the interpreter's does (proposal 0018).
2. **Property checks on compiled backends.** Interpreter only.
3. **Browser builds.** `js` runs on Node; nothing runs in a browser.
4. **Inherent differences.** Stack depth is the host's, so a deep non-tail
   recursion can pass on one backend and not another. It is reported as a
   divergence, which is accurate and may be noise for some projects.
5. **An example whose input is a tuple-valued expression** (not a tuple
   literal) is passed as one argument in a compiled run.
6. **A forged refusal on JavaScript.** JavaScript throws a plain `Error` for a
   capability denial, so a program that panics with that exact wording reads
   as refused. That can fail a panics example, never pass one.

## Implementation Plan

One pull request: the test-harness mode in both compilers; the runner and its
two drivers; `--backend` in the suite runner and the CLI; the JavaScript
`Unit` fix; then this proposal, the spec text, `spec.json` and the docs.
