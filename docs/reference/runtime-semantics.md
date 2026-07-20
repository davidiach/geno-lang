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

## Entrypoints and process exits

Execution engines return `main()`'s value to their caller. Only an outer
executable boundary translates that value into a process status.

The entrypoint must be the selected program's own `main` declaration. An
imported function named `main` is not invoked as the program entrypoint.
- `main() -> Unit` exits with status 0.
- `main() -> Int` exits with the returned integer normalized modulo 256. The
  value is not displayed as a result.
- Output emitted before a normal return is preserved, including before a
  nonzero exit. Expected nonzero exits do not emit error diagnostics.
- Other accepted return types retain their legacy displayed-result behavior
  and exit successfully.
- Uncaught runtime errors still exit nonzero and emit a useful Geno diagnostic
  or standalone-host traceback.

`geno run`, the self-hosted `run` command, generated Python scripts, and
generated Node.js artifacts share this behavior. Node uses deferred
`process.exitCode` so buffered output can drain. Importing generated Python
does not call `main()` or exit. Generated Node ES modules also call `main()`
only when executed directly, not when imported by another module. `geno watch`
reports nonzero returned statuses but remains alive to observe subsequent
changes.

Embedding and sandbox APIs remain value-oriented: `geno.api.run()`,
`Interpreter.run()`, and process-sandbox result channels return the value and
never terminate their host process.

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
