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

## Values and copies

Primitive values, immutable collections, and user-defined constructor values
have value semantics. Rebinding an existing constructor value creates a
snapshot: later field assignment through a mutable binding does not modify the
previous binding. A `with` expression also creates an independent value, and
its result may be bound with `var` and mutated.

`Array`, `Vec`, `Set`, and `MutableMap` are explicit mutable reference types.
Assignments of these collections share their underlying storage. Construct a
new collection when independent mutable storage is required.

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

## Entrypoint results and imports

Geno 0.4 treats `main()`'s return value as a program result, not as a process
status. A successful `main() -> Int` is displayed by `geno run` and standalone
compiled Python and Node artifacts, and the process exits with status 0.
`main() -> Unit` also succeeds with status 0. Output emitted before a returned
result is preserved. A genuine uncaught runtime error instead exits nonzero and
emits a diagnostic (or a host traceback for a standalone generated artifact).

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
