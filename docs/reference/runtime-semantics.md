# Runtime Semantics

This page defines the portable behavior shared by the tree-walking interpreter,
the compiled Python backend, and the compiled JavaScript backend. The
cross-backend parity suites execute the same programs through all three engines.
A target may impose a tighter resource limit, but it must not silently compute a
different result.

## Numbers

`Int` and `Float` are distinct static types, but equality between numeric values
is numeric: `2 == 2.0` is `true`. Structural equality applies that rule
recursively inside tuples, lists, maps, and constructor values.

Integer `/` truncates toward zero. Float `/` and `divide` with Float operands
preserve the fractional result, so `divide(7.0, 2.0)` is `3.5`.

JavaScript represents Geno integers with JavaScript `number`. Therefore the
portable `Int` range for programs that target JavaScript is:

```text
-(2^53 - 1) through 2^53 - 1
```

The JavaScript runtime rejects an integer result outside that range instead of
silently rounding it. Interpreter and compiled Python execution can represent
larger integers and enforce the separately configurable `max_integer_bits`
limit (33,219 bits by default). Keep values inside the portable range when the
same program must run on every backend.

Ordered comparisons with a `nan` Float operand (`<`, `<=`, `>`, and `>=`)
are always `false`, including comparisons between two NaN values.

## Values and copies

Primitive values, immutable collections, and user-defined constructor values
have value semantics. Rebinding an existing constructor value creates a
snapshot: later field assignment through a mutable binding does not modify the
previous binding. This applies to declarations, later assignments, and
destructuring bindings, including records nested inside immutable collections.
A `with` expression also creates an independent value, and its result may be
bound with `var` and mutated.

`Array`, `Vec`, `Set`, and `MutableMap` are explicit mutable reference types.
Assignments of these collections share their underlying storage. Construct a
new collection when independent mutable storage is required.

## Bindings and closures

A declaration that reuses a name in the same lexical scope updates the existing
binding. Its type and effect contract must remain unchanged so previously
checked closures stay valid; a captured binding observes later same-type
rebindings. The latest declaration determines mutability: `var x = 1` followed
by `let x = 2` makes subsequent assignment to `x` invalid. If an existing closure
writes that binding, the immutable redeclaration is rejected because it would
invalidate the closure's checked assignment.

A declaration in a nested block creates a separate binding. Module constants
may be shadowed by function parameters and local bindings; initializers and
parameter defaults resolve names in their enclosing scope before introducing
the new binding.

Declaring a local callable after a closure has already resolved that name to a
global callable is rejected: parameter names, defaults, and builtin dispatch
must keep the identity used when the closure was checked. Declare the local
callable before the closure to capture it instead.

Each lambda has its own return, loop, and asynchronous context. Propagation
with `?` returns from that lambda and requires its inferred return type to be
the corresponding `Option` or `Result`; it cannot borrow the enclosing
function's return contract or escape to an enclosing loop.

## Maps

Maps retain insertion order for `map_entries` and other ordered traversal.
Updating an existing key with `map_insert` changes its value without moving the
key; inserting a new key appends it.

Indexing is intentionally partial: `m[key]` returns the value or raises a Geno
runtime error when the key is absent. Use `map_get(m, key) -> Option[V]` when a
missing key is expected and should be handled explicitly.

## Text output

`to_string` returns the canonical Geno representation of a value. `print`
emits that representation followed by a newline, except that a top-level
`String` is written without surrounding quotes. For example,
`print("hello")` writes `hello`, while strings nested in a constructor or
collection retain the canonical quoted representation.

Non-finite `Float` values use `inf`, `-inf`, and `nan` in text output and
`to_string`, including when nested in collections. These display strings do not
change the separate JSON serialization rules for non-finite numbers.

## Entrypoint results and imports

In the Geno 0.5 series `main()`'s return value is the process status at an
executable boundary. A successful `main() -> Int` exits with that value
normalized modulo 256 -- mathematically, so `-1` becomes 255 and `258` becomes
2 -- and is not displayed. `geno run`, standalone compiled Python, the standalone
Node script and directly executed Node ESM all agree. `main() -> Unit` succeeds
with status 0 and displays nothing, and every other declared return type keeps
its displayed result and status 0. Output emitted before a returned result is
preserved, and a normal nonzero status carries no traceback and no diagnostic. A
genuine uncaught runtime error instead exits nonzero and emits a diagnostic: in
the 0.6 releases, a host traceback for a standalone generated artifact, and on
the 0.7 development line the runtime failure report below on every boundary.

Browser-targeted ESM has no process boundary, so it keeps displaying an `Int`
result. That choice is made from the compile target's profile, not by looking for
a `process` global at runtime, so a bundler's polyfill cannot change it.

Classification reads the entrypoint's *resolved* return type, so a type alias for
`Int`, an `async main() -> Int` and a synchronous `main` that awaits all report a
status. A synchronous `main` declared `Async[Int]` that returns without awaiting
keeps that declared type: it is displayed and exits 0 rather than being awaited on
the program's behalf.

Only `main` declared in the selected entry program is invoked. Embedding APIs
such as `geno.api.run()` return the value in `RunResult` and never terminate the
host process. Importing generated Python or Node ESM defines and exports the
program without invoking `main` or exiting the importer.

The self-hosted CLI in `selfhost/Main.geno` applies the same normalization at its
own boundary: an inner program's `Int` result becomes the status that CLI returns,
and every other result kind is success with the value left unprinted.

## Runtime failure reports

*0.7 development line (Draft
[proposal 0018](../proposals/0018-runtime-failure-diagnostics.md); the
contract is [spec v0.7 section 6.9.1](../spec/v0.7.md#691-runtime-failure-reports)).*
A runtime failure is a panic, a runtime error (index, key, division,
overflow and similar), a failed `requires` or `ensures`, a capability or
host-policy denial, or a resource limit. Every one reports where it happened
and the Geno calls that led there, in the same shape on the interpreter, in
compiled Python and in compiled JavaScript:

```text
Stack trace (most recent call last):
  demo.geno:21:21 in main
  demo.geno:15:14 in safe_div
      requires b != 0
               ^
Runtime Error: Precondition failed: safe_div(a: 8, b: 0) requires b != 0
```

- Frames are named Geno functions (`f`, `Type.method`, `Module.f`), outermost
  first, after a `<example>` or `<test>` origin when an example or a `test`
  block made the failing call. Each is positioned inside its own function: at the call it was
  making, or, for the last frame, at the failure. Lambdas, builtins and the
  host are never frames. A stack of more than 20 frames keeps its first 10
  and last 10 and says how many it left out.
- The source line and caret appear where the host can read the program's
  source: the `geno run` lanes. Standalone compiled artifacts carry no source
  and print the frames and the message only.
- The `Runtime Error: <message>` line is always last. The message never
  contains the location or the stack, so no file name, position or frame name
  from the report can satisfy a `panics "substring"` example or a property
  check. Contract failure messages themselves changed in this line (see the
  [compatibility policy](../operations/compatibility-policy.md#07-draft-runtime-failures-report-a-geno-stack-and-contract-failures-name-their-arguments)):
  a substring only the 0.6 wording had (`"Precondition failed for"`,
  `"requires clause"`, `"evaluated to false"`) no longer matches, and an
  argument value or the clause text (`"b != 0"`) can now match.
- A contract failure names the function, the call's argument values (strings
  quoted, each cut to 64 code points) and the clause as written; an `ensures`
  failure adds the result, and the value a rebound parameter had when the
  clause was checked when that differs from the argument.
- `geno run --json`, `geno.api.run()` and `geno serve` report the same facts
  as `location`, `stack`, `stack_omitted` and, for a contract failure,
  `contract`; `geno test --json` violations carry `location`, `stack` and
  `stack_omitted`. These keys are additive.
- An `Err` returned from `main` is a result, not a failure: it stays
  `Error: <value>` with no location or stack.

Compiled Python and JavaScript map host frames back to Geno source through a
compact table that the compiler embeds in every program with a `main`. It is
read only when a failure escapes `main`, so successful calls do no extra work
and the generated file still needs no Geno install. The mapping is
statement-precise where the interpreter is expression-precise in one case: a
statement holding two operations of the same kind (`xs[i] + ys[j]`, or two
calls through function values) reports its start. A host stack overflow's
depth, message and innermost frame are the host's: that frame is where the
host ran out of stack, at the recursive call or inside the callee's first
operation, and every other kept frame is the same on every backend. Compiled index and division messages keep their shorter 0.6
wording. A Node entry build exits with status 1 as soon as the report is
printed, for an async `main` too, so pending async work does not run after
it; a tail of stdout still queued for a POSIX pipe can be lost, as on 0.6's
synchronous fatal path. JavaScript starts an async function at its call, so
an `Async` created in one statement and awaited in another can position its
caller at the creation call; write `await f(...)` as one expression where the
position matters. Browser builds and programs without `main` are unchanged.

Set `GENO_TRACEBACK=host` to also print the host's own traceback after the
report: the interpreter's or the worker's Python traceback on `geno run`, the
generated code's traceback in standalone Python, and the V8 stack in Node.
It is for debugging Geno itself; envelopes, the embedding API and the hosted
server ignore it.

## Runtime implementations

The interpreter and the Python and JavaScript runtime preludes are three
hand-maintained, target-specific implementations. The prelude files are not
generated. Differential and regression parity tests are the executable contract
that keeps numeric operations, equality, copies, maps, formatting, errors, and
builtins aligned across them.

## Timeouts and untrusted execution

`geno.api.run()` executes in the caller's Python process. Its step and deadline
checks are cooperative: a long-running builtin or trusted host callback cannot
be forcibly interrupted while it holds control. The API is appropriate when
the host controls the code or owns an outer process boundary.

Do not use the in-process API as the only isolation boundary for untrusted
code. Use `geno serve`, the normal process-isolated CLI path, or a
caller-managed worker process with a killable wall-clock timeout and resource
limits. Generated JavaScript is likewise intended for a trusted JavaScript
runtime or an isolation boundary supplied by the caller.
