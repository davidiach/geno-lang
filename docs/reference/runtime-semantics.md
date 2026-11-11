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

## Effects and local state

*0.7 development line (Draft
[proposal 0019](../proposals/0019-local-mutation-is-not-an-effect.md); the
contract is [spec v0.7 section 3.5](../spec/v0.7.md#35-function-effects)).*
Effects are a static property. No backend reads them, so the rule below
changes how no program runs; it relies on the copy rules above. A write that
only the writing function or lambda can observe is not a `mutation`:

- Assigning one of its own variables. Each call has its own variables, and
  only closures created during the call share them. A closure that assigns
  such a variable is charged `mutation` itself, because its later calls see
  the change.
- Writing a field or element of a value its own variable holds. Binding,
  assigning, destructuring and storing a constructor value take a snapshot,
  so no other variable or collection shares it (passing it as an argument,
  and on compiled JavaScript a `for` loop over an `Array`, are the
  exceptions described below).
- Writing the elements of an `Array`, `Vec` or `MutableMap` held by an
  *owned* variable: one initialized and reassigned only from allocating
  builtins (`array_new`, `vec_new`, `mutable_map_new`, `array_copy` and the
  others spec 3.5 lists), whose collection is never stored, passed to a
  function that is not a borrowing builtin, copied into another variable or
  mentioned in a lambda. Returning it hands the caller a new object.

A write below a reference-typed element is still charged, owned root or not:
`array_new(h, array_new(w, 0))` puts the same row array in every slot, so
`g[i][j] = v` changes every row on every backend.

Three current behaviours sit at the edge of that argument. The test suite
pins each one on all three backends, recording compiled JavaScript's output
separately where it differs, so a change to any of them shows up
deliberately.

- **Argument passing does not snapshot.** Passing a `var`-held constructor
  value to a function hands the callee the same object, so a closure the
  callee builds over that parameter sees later in-place writes through the
  caller's `var`:

  ```geno
  type P = P(x: Int)

  @untested("returns a closure")
  func keep(q: P) -> () -> Int
      return fn() -> q.x
  end func

  func main() -> Unit with io
      var s = P(x: 1)
      let peek = keep(s)
      s.x = 7
      print(peek())
  end func
  ```

  This prints `7` on every backend, which departs from the snapshot rule of
  spec section 3.2.1; it is a known runtime deviation (proposal 0019,
  Unresolved Question 7). Masking `s.x = 7` is still sound: the alias changes
  only what `main` itself computes, and the closure can reach storage a
  caller sees only through a write that is charged or through an uncharged
  builtin store.

  A whole variable passed as an argument (`keep(s)`, or `keep_list(ys)` for
  a `List` of records) aliases on every backend. An element or field read
  passed as an argument (`keep(a[0])`, `keep(ys[0])`, `keep(b.p)`, then
  `a[0].x = 5`) does not alias on any backend: the closure returns `0`,
  because the field write first gives the slot its own copy of the record.
  Binding the same read first (`let q = a[0]`) takes a snapshot as well.
  Compiled JavaScript returned `5` here before this was fixed.
- **A `for` loop over an `Array` iterates a snapshot.** A write the loop
  body makes to a later slot (`a[2] = 99`) or to a field of an element
  (`a[1].x = n`) does not show up in the same loop, on any backend. Compiled
  JavaScript iterated the live array before this was fixed.
- **Mutating builtins carry no effect.** As in 0.6, the checker does not
  charge `vec_push`, `vec_set`, `array_set`, `array_fill`, `set_add`,
  `mutable_map_set` or the other mutators on any receiver, so a function
  that pushes onto a parameter's `Vec` is inferred pure. That is a known
  limitation of the checker, not the rule: spec 3.5 says such a write is a
  `mutation`, and a later checker may enforce it. Together with masking it
  lets a write reach a caller in three ways, each through such a store: a
  builtin stores a closure over one of the function's variables into the
  caller's collection (`vec_push(param_v, fn() -> n)`, then `n = 5`); a
  pure callback stores a closure like `peek` above; or, most commonly, a
  function writes its caller's collection with a builtin
  (`array_set(array: grid, ...)`) and also updates a local loop counter,
  which 0.6 charged and 0.7 masks, so the function is inferred pure. Declare
  `with mutation` on a function that changes a caller's collection through a
  builtin.

Effects are inferred within a module. An importing module currently treats an
imported function that declares no effects as pure, whatever its body does.
That is an existing checker limitation, independent of this rule, so declare
the effects of exported functions that have any.

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
  `stack_omitted`. These keys are additive. A `geno test` violation's
  message no longer starts with the location (0.6 put
  `<file:line:col>: ` before a located failure's `Runtime Error:`).
- An `Err` returned from `main` is a result, not a failure: it stays
  `Error: <value>` with no location or stack.

Compiled Python and JavaScript map host frames back to Geno source through a
compact table that the compiler embeds in every program with a `main`. It is
read only when a failure escapes `main`, so successful calls do no extra work
and the generated file still needs no Geno install. The mapping is
statement-precise where the interpreter is expression-precise in one case: a
statement in which two operations could have made the failing call reports
its start rather than guess. That is two operations of the same kind
(`xs[i] + ys[j]`, or two calls through function values), or a call of a
function or builtin by name next to a call through a function value that
could hold it (`check(1) + f(9)`). A parameter or local that shadows a
function or builtin name, such as a callback parameter called `format`,
counts as a function value. A host stack overflow's
depth, message and innermost frame are the host's: that frame is where the
host ran out of stack, at the recursive call or inside the callee's first
operation, and every other kept frame is the same on every backend. Compiled index and division messages keep their shorter 0.6
wording. A Node entry build exits with status 1 as soon as the report is
printed, for an async `main` too, so pending async work does not run after
it; its stdout and stderr are blocking, as compiled Python's are, so no
output printed before the failure is lost to a full pipe. JavaScript starts an async function at its call, so
an `Async` created in one statement and awaited in another can position its
caller at the creation call; write `await f(...)` as one expression where the
position matters. Browser builds and programs without `main` are unchanged.

A function's call to itself in tail position replaces the running call
(Draft [proposal 0022](../proposals/0022-self-tail-calls.md),
[spec v0.7 section 4.1.4](../spec/v0.7.md#414-self-tail-calls)), so the calls
it replaced leave no frames: a failure in the 40th step of a tail-recursive
loop shows the function once, and no count of the replaced calls. Such calls
do not count toward the recursion depth limit; the step limit and the timeout
still stop a tail loop that never ends.

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
