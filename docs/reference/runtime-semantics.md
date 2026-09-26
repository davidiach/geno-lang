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
genuine uncaught runtime error instead exits nonzero and emits a diagnostic (or a
host traceback for a standalone generated artifact).

Browser-targeted ESM has no process boundary, so it keeps displaying an `Int`
result. That choice is made from the compile target's profile, not by looking for
a `process` global at runtime, so a bundler's polyfill cannot change it.

Only `main` declared in the selected entry program is invoked. Embedding APIs
such as `geno.api.run()` return the value in `RunResult` and never terminate the
host process. Importing generated Python or Node ESM defines and exports the
program without invoking `main` or exiting the importer.

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
