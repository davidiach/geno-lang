# Proposal 0018: Runtime Failures With Geno Locations, Stacks and Contract Arguments

- Status: Draft
- Authors: Claude Code (drafted and implemented at the project lead's request)
- Created: 2026-11-05
- Discussion: https://github.com/davidiach/geno-lang/pull/233
- Supersedes: None
- Review: None yet; awaiting project lead acceptance under `GOVERNANCE.md`.
- Target: the 0.7 development line (`docs/spec/v0.7.md`, Draft)

## Summary

Every runtime failure reports where it happened in Geno source and how the
program got there. This holds on every backend and every lane that runs Geno
code: the interpreter (`geno run --unsafe`, `geno run --json`, `geno test`,
the embedding API and the hosted server), the process-isolated default
`geno run` lane, and standalone compiled Python and JavaScript. Runtime failures
are panics, runtime errors, contract failures, capability denials and resource
limits. Each one reports:

- the failure location (`file:line:col` of the `panic` call, the failing
  expression, or the failing clause);
- the full Geno call stack, most recent call last. Each frame is a function name
  and a position; a stack deeper than 20 frames shows its first 10 and last 10
  frames and says how many it left out;
- for a contract failure, the function, the clause's source text, the argument
  values and, for `ensures`, the result:

```text
Stack trace (most recent call last):
  demo.geno:21:21 in main
  demo.geno:15:14 in safe_div
      requires b != 0
               ^
Runtime Error: Precondition failed: safe_div(a: 8, b: 0) requires b != 0
```

Compiled programs print this shape too, instead of a Python traceback or a V8
stack of generated code. They do it through a compact Geno trace table that the
compiler embeds in the generated file. The table is read only when a failure
escapes `main`, so a call costs nothing extra, and the output still needs no
Geno install to run. `GENO_TRACEBACK=host` also prints the raw host traceback,
for debugging Geno itself.

`geno run --json`, the embedding API, the hosted server and `geno test --json`
gain structured `stack` (and, where it was missing, `location`) fields, plus a
`contract` object for contract failures. These are additive: existing keys and
values do not change. Exit statuses, error codes and every frozen conformance
case are unchanged. The location and stack never enter the message text, which
proposal 0017's `panics "substring"` matching reads. The feature adds no
syntax. The package version stays `0.6.0`, and the contract is specified by
the Draft `docs/spec/v0.7.md`.

## Motivation

Geno's pitch is check-and-repair. Compile-time diagnostics already carry stable
codes, precise spans and machine-applicable fixes. Runtime failures, the other
half of the repair loop, carry far less. Take this `demo.geno`:

```geno
func check(n: Int) -> Int
    example 1 -> 1
    if n > 5 then
        panic("value too large")
    end if
    return n
end func

func scale(n: Int) -> Int
    example 2 -> 4
    return check(n * 2)
end func

func safe_div(a: Int, b: Int) -> Int
    requires b != 0
    example 6, 3 -> 2
    return a / b
end func

func main() -> Int
    print(to_string(safe_div(a: 8, b: 0)))
    return scale(4)
end func
```

On 0.6.0 it prints this:

| Lane | What a failed `requires` prints today |
|---|---|
| `geno run` (default, isolated) | `Runtime Error: Precondition failed for safe_div: requires clause evaluated to false`, with no location and no stack |
| `geno run --unsafe` | a stack that lists `main` at its *definition* (`20:1`) and `safe_div` at its call site, then an `at` line pointing at the clause; no argument values |
| `python3 demo.py` | a Python traceback into generated code (`demo.py, line 5869, in safe_div`) ending in `_GenoContractViolation: ...` |
| `node demo.js` | a V8 stack of generated JavaScript |

These are the other gaps, from probes on 0.6.0:

- `panic("boom")` two calls deep prints only `Runtime Error: boom` on every
  lane. The interpreter re-raises builtin errors without the call's location,
  so `--json` has no `location` for a panic either.
- The default lane loses everything. Its `main` runs as compiled Python inside
  the worker, and the error crosses the process boundary as a bare string
  (`geno/cli/run.py`, `_print_runtime_error(RuntimeError(error))`).
- An index error three frames deep (`main -> b -> c`) shows only `in c` when it
  is raised while verifying an example: example calls and builtin callbacks
  carry no call site.
- Recursion, step and time limits never carry a stack.
  `RecursionLimitError` is a `SandboxError`, and only `GenoRuntimeError` gets
  `_geno_stack`. A 60-deep recursion that does panic prints 60 unbounded
  lines.
- A failed `ensures` shows the result but not the arguments. Compiled Python
  even shows the host `str()` of the result (`result was hi!`), where the
  interpreter and JavaScript show `"hi!"`.
- `geno test --json` reports `"Runtime error: Runtime Error: boom"`, because it
  builds the text from `str(exc)`.

A model or a person repairing a failure needs four things: what failed, where,
how the program got there, and with which values. Today they have to recover
the last three by adding prints. That is the expensive repair loop the
compile-time side already eliminated.

## Guide-Level Design

### A panic three calls deep

`panic.geno` is `demo.geno` without `safe_div`:

```geno
func check(n: Int) -> Int
    example 1 -> 1
    if n > 5 then
        panic("value too large")
    end if
    return n
end func

func scale(n: Int) -> Int
    example 2 -> 4
    return check(n * 2)
end func

func main() -> Int
    return scale(4)
end func
```

`geno run panic.geno`, `geno run --unsafe panic.geno`, `python3 panic.py` and
`node panic.js` all report the same frames and the same message:

```text
Stack trace (most recent call last):
  panic.geno:15:12 in main
  panic.geno:11:12 in scale
  panic.geno:4:9 in check
      panic("value too large")
      ^
Runtime Error: value too large
```

Read it bottom-up for the cause and top-down for the path. Each frame names a
function and the position *inside that function* where execution was: for
every frame but the last, that is the call that led to the next frame, and for
the last it is the failure itself. This is the convention of Python and
JavaScript tracebacks, which every Geno user and every model already knows. The
CLI lanes print the absolute path of the program's own file; a compiled
artifact prints the path relative to the entry file's directory, so it does
not carry the build machine's layout. The source line and caret appear when
the reporting host can read the program's own source. The CLI lanes can;
standalone compiled artifacts do not embed source, so they omit them.

### A contract failure names its arguments

`demo.geno` fails earlier, in its `requires`:

```text
Stack trace (most recent call last):
  demo.geno:21:21 in main
  demo.geno:15:14 in safe_div
      requires b != 0
               ^
Runtime Error: Precondition failed: safe_div(a: 8, b: 0) requires b != 0
```

The call site (`21:21`, inside `main`) is the frame below the callee, and the
location is the clause that failed. Argument values are rendered the way
diagnostics render values everywhere (strings quoted), and each is truncated to
64 characters. A failed `ensures` also shows the result:

```text
Runtime Error: Postcondition failed: abs_val(x: -1) ensures result >= 0 (result was -1)
```

### Deep recursion is bounded

```geno
func count_down(n: Int) -> Int
    example 0 panics "bottom"
    if n == 0 then
        panic("bottom")
    end if
    return count_down(n - 1)
end func

func main() -> Int
    return count_down(40)
end func
```

The failure has 42 frames (`main` and 41 `count_down`s), and 0.6 prints all
of them. In 0.7 the stack keeps the first 10 and the last 10:

```text
Stack trace (most recent call last):
  deep.geno:10:12 in main
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  ... 22 frames omitted ...
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:6:12 in count_down
  deep.geno:4:9 in count_down
      panic("bottom")
      ^
Runtime Error: bottom
```

### Machine-readable

`geno run --json` gives the same facts as structure. `location` existed
before; `stack` and `contract` are new:

```json
{
  "code": "E499",
  "message": "Precondition failed: safe_div(a: 8, b: 0) requires b != 0",
  "severity": "error",
  "location": {"line": 15, "column": 14, "filename": "/home/me/demo.geno"},
  "stack": [
    {"function": "main", "location": {"line": 21, "column": 21, "filename": "/home/me/demo.geno"}},
    {"function": "safe_div", "location": {"line": 15, "column": 14, "filename": "/home/me/demo.geno"}}
  ],
  "contract": {
    "clause": "requires",
    "reason": "false",
    "clause_index": 0,
    "function": "safe_div",
    "clause_text": "b != 0",
    "arguments": [{"name": "a", "value": "8"}, {"name": "b", "value": "0"}],
    "result": null
  }
}
```

### Debugging Geno itself

`GENO_TRACEBACK=host python3 demo.py` prints the Geno diagnostic and then the
raw Python traceback of the generated code. `GENO_TRACEBACK=host node demo.js`
does the same with the V8 stack. `GENO_TRACEBACK=host geno run ...` does the
same with the interpreter's or the worker's traceback. The variable is for
diagnosing the toolchain; nothing in a Geno program can read it.

## Reference Design

### 1. Vocabulary

- A **runtime failure** is anything that ends a Geno program abnormally once it
  is running: a panic (`panic`, `unwrap`/`expect` on `None`/`Err`, a
  compiler-emitted panic), a runtime error (index, key, division, overflow and
  similar), a contract failure, a capability or host-policy denial, and a
  resource limit (step, time, recursion, collection, integer, numeric range,
  output, memory). An `Err` returned from `main` is **not** a runtime failure:
  it stays `Error: <value>` with no location or stack. This is pinned
  byte-for-byte by the frozen `result-main-err` case.
- A **runtime report** has a label (`Runtime Error`, or the existing
  `Sandbox Error`/`Limit Error` labels the interpreter lanes print for limits),
  a message, an optional failure location, and an optional Geno stack.
- A **Geno stack** is a list of **frames**, outermost first. Each frame has a
  function display name and an optional position (`SourceLocation`). It also
  carries `omitted`, the number of frames left out between the first 10 and
  the last 10.

### 2. Frames

**Which activations are frames.** A frame is an activation of a named Geno
function: a top-level function, an impl method, or a function of an imported
module (the Geno-source standard library included). These are not frames:

- **Lambdas.** A lambda's body runs as part of whichever frame called it. The
  failure location still points into the lambda body exactly, and the frame
  that called the lambda (directly, or through a builtin such as `map`) is
  positioned at the call made from inside the lambda (below).
- **Builtins and runtime helpers** (Python- or JS-implemented: `map`,
  `fs_read_text`, `_safe_index`, trait dispatchers, module factories, `?`
  propagation wrappers, compiler temporaries such as the `ensures` body helper).
  A failure inside a builtin is reported at the builtin's call.
- **The host.** The CLI, the worker, the sandbox, the thread runner and the
  event loop never appear.

One origin pseudo-frame exists: when a failure happens while verifying an
`example`, the outermost frame is `<example>`, positioned at the example clause.
The stack of `main` starts at `main`, because the host's call of `main` has no
Geno source position.

**Names.** A frame's name is the function's name as written. An impl method is
`Type.method`. A function defined in a module other than the entry module is
`Module.function` (`List.find`). Compiled backends never reverse host-name
mangling (`_g`, `print_`). The compiler writes the display name into the trace
table (section 7) when it emits the function.

**Positions.** Frame *i*'s position is the call site of frame *i+1*: the
location of the call expression that started it (for a method call, as the
parser locates it today, the receiver's start). The innermost frame's position
is the failure location. When frame *i+1* was started by a builtin calling a
callback (`xs.map(g)`), its call site is that builtin call's location. When it
was started from inside a lambda, its call site is the call inside the lambda
body, wherever the lambda was defined. The position is `None` only when no
Geno position exists.

**Async functions.** Calling an `async` function creates an `Async` value; the
function's frame exists while its body runs. On the interpreter and compiled
Python the body runs when the value is awaited, so the awaiting frame is
positioned at the `await`: at the awaited call's location when `await`
directly wraps the call (`await helper(5)`, the common form), and otherwise at
the `await` expression's location (`let pending = helper(5)` ... `await
pending`). Async `main` is started by the host and has no call site, like
synchronous `main`. Compiled JavaScript starts an async function eagerly at
the call, so in the split form V8 positions the caller at the *creation* line
when the failure happens before the body's first real suspension (see Backend
And Target Parity). The parity tests and conformance cases therefore use only
the one-expression form `await f(...)`, where every backend gives the same
position.

**Bounds.** `STACK_HEAD = 10` and `STACK_TAIL = 10`. A stack of more than 20
frames keeps frames `[0, 10)` and the last 10, and records
`omitted = total - 20`. Every lane, the text form and the JSON form apply the
same bound.

**Paths.** A location's filename is the one the frontend assigned to the file
(absolute paths on the CLI, `<api>`, `<module:Name>`, `<http>`, `<stdin>`),
with one exception: a file inside the installed standard library is shown as
`std/<Name>.geno` on every lane, so a frame inside `List.chunk` never prints
the install path. The mapping is applied at display time (in
`runtime_report.display_path` and its prelude copies). The AST keeps the
absolute filename that `stdlib_scope.is_stdlib_program` depends on. Compiled
trace tables store paths relative to the entry file's directory (a basename
when the file is elsewhere), `std/<Name>.geno` for the standard library, and
`<stdin>` as given.

### 3. Failure locations

| Failure | Location |
|---|---|
| `panic(m)`, `unwrap`, `expect` and other failing builtins | the builtin call (`FunctionCall.location`), attached by the interpreter's builtin branch to any error raised without a location |
| index / key / division / overflow | the failing node's existing location (`IndexAccess` target start, `BinaryOp` left operand), unchanged |
| capability or host-policy denial | the builtin call, as 0.6 already reports in the interpreter |
| `requires` / `ensures` false or propagating `?` | the clause condition's location, unchanged from 0.6 (decision 4: existing values do not change). The call site is the caller's frame position |
| recursion limit | the call that would have exceeded the depth |
| step limit / cooperative timeout | the location the interpreter already checks at (its message keeps its existing ` at <loc>` text) |
| collection / integer / numeric-range / output limits | the expression or builtin call that produced the value |
| host stack overflow, memory exhaustion, hard wall-clock kill | the innermost mapped position when the host can still produce a stack (Python `RecursionError`, V8 `RangeError`); none for a memory exhaustion or a hard kill, which cannot run a reporter |

This proposal adds no operator-precise or end positions. Carets point where
the AST already points. Adding operator spans is listed under Alternatives.

### 4. Text rendering

`geno/runtime_report.py` renders a runtime report as follows. It is a new, light
module that imports only `geno.diagnostics` and `geno.tokens`, so the
default-lane parent can use it under `test_cli_run_import_footprint.py`.

```text
Stack trace (most recent call last):
  <position> in <name>                 one line per kept frame, outermost first
  ... <N> frames omitted ...           after the 10th frame, only when N > 0
  <position> in <name>
      <source line>                    only when the host can read the source;
      <caret>                          see below
<Label>: <message>
```

- A frame without a position prints `  in <name>`. `1 frame omitted` is
  singular.
- The snippet is the line the innermost frame (or the `at` location) points
  at, shown without its own indentation and four spaces in from the frame
  lines' text (six spaces in all), with the caret under the location's
  column. Tabs before the column are kept in the caret line, so it stays
  aligned.
- When no stack is known but a location is, the frame block is replaced by
  `  at <location>` (followed by the snippet when available). When neither is
  known, only the message line prints, which is exactly 0.6's output.
- The message line is last, so anything a lane prints after a report (the F2
  `Hint:` lines) still follows `Runtime Error: <message>` directly. Location
  and stack are never part of `<message>`.
- A snippet is read only from the program's own sources: the entry file, the
  files the frontend loaded for it, and the installed standard library (via
  the `std/` mapping). In-process lanes take locations from their own
  frontend, which is trusted. The default-lane parent does not run the
  frontend, and its locations come from the untrusted worker payload, so it
  applies a strict rule (`runtime_report.read_snippet_line`): the path must
  end in `.geno`, resolve (symlinks followed) inside the entry file's
  directory tree or the installed standard library, and `os.stat` must show a
  regular file (`S_ISREG`, so never a FIFO, device or directory). The file is
  opened without following a final symlink where the platform allows it, read
  line by line, and the read stops at the target line or after 1 MiB, and a
  line longer than 1000 code points is not printed. Anything else, and any
  `OSError` or decode error, prints no snippet. A worker payload therefore
  cannot make the parent print a line of `.env` or another non-Geno file,
  block on a FIFO, or read a huge file into memory.
- **Text that must fit a budget** (the worker's pre-formatted example-phase
  report, which travels as one string truncated to `max_output_length`) is
  shortened by `runtime_report.fit_report_text(lines, limit)` and never by
  keeping its head. It drops the snippet first, then frame lines from the
  middle, growing the `... N frames omitted ...` count, and as a last resort
  truncates the message line's own text. The final `<Label>: <message>` line
  is always present and always last.

The Python and JavaScript runtime preludes carry their own copies of this
renderer, so compiled output stays standalone. `test_runtime_report_parity`
pins that, without a snippet, the three produce identical bytes for the same
report. The three also share the constants `STACK_HEAD`, `STACK_TAIL`,
`ARGUMENT_DISPLAY_LIMIT` and `CLAUSE_TEXT_LIMIT`, and a test asserts that each
copy has the same values.

### 5. JSON rendering

`geno.diagnostics` gains three frozen, picklable dataclasses:

- `StackFrame(function: str, location: SourceLocation | None)`;
- `GenoStack(frames: tuple[StackFrame, ...], omitted: int = 0)`;
- `ContractDetails(clause, reason, clause_index, function, clause_text,
  arguments: tuple[tuple[str, str], ...], result: str | None,
  checked_values: tuple[tuple[str, str], ...] = ())`.

`Diagnostic` gains `stack: GenoStack | None = None` and
`contract: ContractDetails | None = None`. `to_dict` emits these keys only when
they are set:

- `"stack"`: a list of `{"function": str, "location": {line, column,
  filename}}`. `location` is omitted for a frame without one, as `Diagnostic`
  already omits it.
- `"stack_omitted"`: an integer, only when it is greater than 0. The omitted
  frames lie between `stack[9]` and `stack[10]`.
- `"contract"`: the object shown in the guide. `arguments` is a list of
  `{"name", "value"}` pairs holding the call's arguments, and `result` is
  `null` for `requires`. `checked_values`, in the same shape, appears only for
  an `ensures` failure whose body rebound a parameter to a different value
  (section 6).

No existing key is renamed, removed or given a different value. The one
envelope value that is new where it used to be absent is `location` on panics
and other builtin failures. Codes do not change: a contract failure stays E499
(spec v0.7 section 5.4 already says so), a panic E414, a denial E412. E407 and
E408 stay registered and unused (see Unresolved Questions).

### 6. Contract failures

**Message.** The message names the function, its arguments and the clause:

```text
Precondition failed: <name>(<p1>: <v1>, ...) requires <clause text>
Precondition failed: <name>(...) requires <clause text> ('?' propagated None/Err)
Postcondition failed: <name>(...) ensures <clause text> (result was <r>)
Postcondition failed: <name>(...) ensures <clause text> ('?' propagated None/Err; result was <r>)
```

- `<name>` is the frame display name (section 2).
- The values are the **call's arguments**, as the callee received them (after
  defaults and `Int`-to-`Float` widening). For `requires` they are the
  parameters' values at the check. For `ensures` they can differ: a top-level
  `let n = n + 5` in the body rebinds the parameter's own binding on every
  backend (the interpreter binds into `call_env`; compiled Python rebinds
  through its `nonlocal` helper; JavaScript reassigns), and the clause is
  evaluated against the rebound value by design (0.6 semantics, unchanged).
  Printing that value as `g(n: 6)` for a call `g(1)` would misreport the
  call. So:
  - the interpreter renders from the `args` list `_call_function` already
    holds (and `AsyncValue.args` for async functions), passed to
    `_check_ensures`, at no cost;
  - the compilers decide lexically, at compile time, whether a function with
    `ensures` rebinds a parameter at the body's top level (the case that
    reaches the parameter's own host variable; a nested-block shadow does not,
    and the parity tests pin both). Only such functions get one entry
    statement that keeps the original values in a fresh compiler temporary
    (`_temp_1 = (n,)` / `const _temp_1 = [n];`, collision-free by
    construction); every other function renders its parameters at failure
    time and pays nothing;
  - when a rebound parameter's value at the check differs from its argument,
    the postcondition message adds it after the result:
    `Postcondition failed: g(n: 1) ensures n > 10 (result was 6; n was 6 when checked)`,
    and `contract.checked_values` lists those `{"name", "value"}` pairs. The
    key is absent otherwise.
- A function with no parameters renders as `name()`.
- When the clause text is unknown, `requires clause #1` (1-based) takes its
  place.
- The example-rejection explanation (`_explain_rejection_example`) keeps its
  sentence but starts with the new prefix:
  `Precondition failed: safe_div(a: 1, b: 0) requires b != 0: this example expects Err, ...`.

**Clause text** is captured at parse time, because the checker rewrites method
calls and overloads to internal names, and a rest-of-line read cuts multi-line
clauses short. `RequiresClause` and `EnsuresClause` gain
`source_text: str | None = field(default=None, compare=False)`. `compare=False`
keeps `geno fmt`'s `_same_program` and parser equality blind to spacing, as for
F1's `ExampleClause` positions. The parser fills the field when it has the
source. The text is the source from the end of the keyword to the start of the
first token after the condition, processed in four steps:

1. Remove `//` and `/* */` comments, outside string literals.
2. Replace each whitespace run that contains a newline with one space.
3. Trim the result.
4. Cap it at `CLAUSE_TEXT_LIMIT = 200` code points: a longer text keeps its
   first 197 code points and gains `...`.

Every parse on a path that can execute contracts passes `source`:

- `compiler.py:3329`
- `js_compiler.py:3855` and `js_compiler.py:4144`
- `interpreter.py:3979`
- `test_runner.py:768`
- `module_resolver.py:307`
- `harness.py:473` and `harness.py:555`
- the `repl.py` sites

An AST-scan test pins the list. The LSP and `doc_generator` parses may leave it
unset. `property_check.ensures_clause_text` reads the field first and keeps its
line-based extraction as the fallback, which fixes its multi-line truncation.

**Argument and result rendering.** Values use the diagnostic rendering
(`Interpreter._format_value`, compiled Python `_geno_format`, JS
`_formatValue`): `to_string`'s rendering, with strings quoted at the top level
too, so `f(s: "0")` cannot be confused with `f(s: 0)`. Each value is capped at
`ARGUMENT_DISPLAY_LIMIT = 64` code points: a longer rendering keeps its first
61 code points and gains `...`. A new bounded renderer in each of the three
runtimes (`format_value_bounded(value, limit)`; `_geno_format_bounded` and
`_formatValueBounded(value, limit, shape)` in the preludes) returns exactly what
truncating the full rendering would return, but stops walking a value once it
has more than `limit` code points. Cost is therefore bounded by the limit, not
by the value's size. This matters because property checks raise and discard
many `requires` rejections. JavaScript counts code points, not UTF-16 units,
so astral characters truncate identically. The fix for the Python `ensures`
`str()` bug falls out of this: every backend renders through the same
function. Both prelude renderers are the
existing budgeted f-string writer (`_GenoFString`) with a display budget in
place of the collection limit, so they share its escaping, cycle and set
ordering rules. JavaScript values cannot tell `2.0` from `2` or a tuple from
a list, so the JavaScript compiler passes each argument's and the result's
static display shape, the one f-strings already use
(`_compile_fstring_shape`: Float, tuple, sequence, map and variant-field
positions), and the renderer applies it; compiled records keep using their
own formatter's bounded-writer branch.

**Structured fields.** `ContractViolationError` keeps `clause`, `reason`,
`clause_index`, `call_depth` and `result_display`, with their meanings
unchanged. It gains `function: str | None`, `clause_text: str | None` and
`arguments: tuple[tuple[str, str], ...] | None` (name and rendered value),
and `checked_values` (the same shape, empty unless an `ensures` saw a
rebound parameter). `with_message` carries every field and the stack.
Compiled Python's `_GenoContractViolation` and JavaScript's
`_GenoContractViolation` get the same attributes (JS: `clause`, `reason`,
`clauseIndex`, `function`, `clauseText`, `arguments`, `resultDisplay`,
`checkedValues`). They are built by a prelude factory, which *returns* the
exception (like `_geno_panic_error`): Python
`_geno_contract_violation(clause, reason, index, function, clause_text,
names, values[, result][, checked=...])`, and JavaScript
`_genoContractViolation(clause, reason, index, fn, clauseText, names,
values[, hasResult, result, resultShape][, checked])`, whose `values` and
`checked` are `[value, shape]` pairs (section 6, argument rendering). The `raise` therefore stays in the Geno function's own
frame, on a host line that the trace table maps to the clause. The compiler
passes the clause text, function name and parameter names as ordinary escaped
string literals (Python `repr`; JS `JSON.stringify`) and never as f-string or
template text. Clauses that contain `{`, quotes, backslashes, `${` or
backticks are therefore safe. `call_depth` is `None` on compiled backends, as
F1 allows.

### 7. Interpreter

**Capture.** On the first unwinding Geno frame, `_call_function` captures
`_call_stack` and the failure location and stores them on the exception as
`geno_stack`, replacing the ad-hoc `_geno_stack` list. The capture happens
once per failure and copies the stack once (O(depth), with depth capped by
`max_recursion_depth`); the bounded `GenoStack` (lambda entries skipped, 20
frames kept) is built from it when the report is made. The `except` clause widens from `GenoRuntimeError` to
`GenoRuntimeError | SandboxError`, so recursion, step and cooperative timeout
limits get stacks. `SandboxError` gains an optional `location` attribute, set
by the recursion check (the call) and by the step and timeout checks (the
location they already report).

**Locating in place.** `GenoRuntimeError` gains `set_location(location)`. It
sets `.location` and rebuilds `args` so that `str()` agrees with it. The object
keeps its type, its error code and its trusted host tags (`_HOST_DENIAL_TAGS`),
because it is the same object. The builtin branch calls it for every error
that leaves a builtin without a location, replacing today's
re-create-and-copy-tags path, which was limited to denials. `.message` is
untouched, so `panic_outcome._message_of` and every `panics "substring"` match
read exactly what they read in 0.6.

**What the capture stores.** The capture is a plain tuple copy of
`_call_stack` entries plus the failure location, the same O(depth) copy 0.6's
`_geno_stack` already makes. Lambda folding, display names and the 10/10
bound are applied when the report is built (`runtime_report.build_stack`), not
during unwinding, so outer handlers can still correct an entry.

**Call sites a builtin starts, fixed at failure time.** A callback such as
`g` in `xs.map(g)` is called by the builtin through
`self._call_function(func, [item])` with no location, so its `_call_stack`
entry has `location=None`. Nothing is recorded on the success path. Instead,
the builtin branch's existing `except` handlers (failure-only code) patch the
entry: before the builtin runs nothing changes; when an error leaves the
builtin, `depth = len(self._call_stack)` there equals the depth at the
builtin's entry, and if the captured stack's entry at `depth` has no location,
the handler sets it to the builtin call's location. A new
`except SandboxError` arm does the same and re-raises, so a recursion limit
reached through a callback is patched too. Nested builtins inside callbacks
patch their own depth, innermost first. This replaces the earlier
`_builtin_call_site` save/restore, which would have added a `try/finally` and
two attribute writes to every builtin call on the path the check-medium and
test-medium budgets time.

**Async functions.** `_run_async_value` runs an async body without
`_call_function`, so it gets the same treatment: it pushes
`(display_name, location)` onto `_call_stack`, captures on unwind with the same
`except GenoRuntimeError | SandboxError` rule, and pops in `finally`. The
location is the one section 2 defines: `_eval_await` passes the awaited call's
location when `expr.expr` is a call (or method call), else the `await`
expression's location; the async-`main` entry (`interpreter.py:1664`) passes
`None`. A second `await` of a failed `Async` re-raises the first error, whose
stack was captured the first time.

**Example origin.** Example verification marks the stack's origin, so the
stack begins with `<example>`, positioned at the example clause. Like the
callback patch, this is failure-time work: the example's call is made as in
0.6, with no call site, and the handler that sees the failure
(`Interpreter.mark_example_origin`, used by `_verify_examples` and by
`geno test`'s example runner) sets the outermost entry's call site to the
example clause and the origin name. A failure that is not a stacked call
failure (a mismatch, an input that fails before any call) has nothing to
mark.

**Display names** are computed once, when a function definition creates its
closure (`Type.method`, `Module.function`). They are never computed per call.

**Contracts.** `_check_requires` and `_check_ensures` read the argument values
from `call_env` (already bound) only after a clause has failed, and render them
with the bounded renderer.

### 8. Compiled Python

**Trace table.** For a program with a `main`, the compiler emits two lines as
the first lines of the generated body: the `body` section that
`_compiled_runtime_sections` returns, directly after `RUNTIME_PRELUDE`.
`_insert_compiled_runtime_capability_assignment` splices
`prelude + assignment + body`, so an inserted capability assignment lands
*before* the anchor and never shifts a body offset. The stripped-prelude path
(`stripped_prelude + assignment + rest`) has the same shape. The
capability-insertion layout test stays, and `test_trace_table` adds the
anchor-offset check for both paths:

```python
def _geno_trace_anchor(): pass
_GENO_TRACE = "G\t1\n..."
```

`_GENO_TRACE` is one string constant, so the worker's AST validation sees one
node and parse cost stays flat. It is a line-oriented, versioned record list.
Fields are separated by tabs, and text fields escape `\\`, `\t` and `\n`. Host
lines count from the anchor's `def` line, which is line 0. The records are:

- `F idx path`: a source file;
- `D start end host_name display_name`: the host line range of one emitted Geno
  function and its names. Nested ranges appear for module factories and nested
  helpers;
- `T id file line col`: a Geno statement's location;
- `S host_line id`: from this host line on, generated lines belong to
  statement `id`. The compiler writes one each time the attributed statement
  changes, including when an enclosing statement resumes after a nested block;
- `C id key line col`: a **candidate site** in statement `id`. `key` is the host
  callee the generated code calls there: a Geno function's host name, a
  builtin's host name, or a runtime helper (`_geno_panic`, `_safe_index`,
  `_safe_div`, `_require_cap`, ...). `line col` is the Geno location of the
  call or construct that emitted it;
- `E host_line`: the end of the body.

The contract-check lines (`if not (...)` and the `raise`) get a statement of
their own located at the clause condition. Every statement kind gets an `S`
record, including expression statements, `Assign` and `Assert`, which 0.6's
`# geno:` comments skip. The `E` record precedes the standalone guard, so the
guard's `<module>` lines are outside the body. The existing `# geno:file:line`
comments are left as they are.

**Recording through scratch buffers.** The Python compiler does not write its
output linearly. A block lambda's body is compiled into a temporary
`StringIO` (`compiler.py:3172-3191`) and spliced into the enclosing buffer
later with `self._write(statements)`, possibly inside another such buffer, so a
line counter in `_writeln` would record lines relative to the scratch buffer.
The recorder therefore uses in-band markers, the scheme the JavaScript
backend already uses for its source map (`\0geno-map:N\0`,
`js_compiler.py:1408-1430`, 1467-1484):

- `D` start/end, `S` and the statement half of `C` records are written as
  `\0geno-trace:N\0` markers into whatever buffer is current. Records whose
  data needs no host line (`T`, the key and location of `C`, `F`) go straight
  into side tables.
- `_write` resolves markers only when it writes to the root buffer
  (`self._trace_buffer_depth == 0`; a scratch-buffer swap increments the depth
  and restores it in its `finally`). It computes each marker's host line by
  counting the newlines in the written text before the marker, starting from a
  root line counter that `_write` advances by the newlines of every write. It
  counts newlines in the text, never `_writeln` calls, so multi-line strings,
  prelude text and spliced blocks are all counted correctly. The markers are
  then removed from the text.
- Geno string literals escape NUL in both backends, so a marker cannot collide
  with user text. A final assertion in `compile`/`compile_project` checks that
  no `\0geno-trace:` remains in the output.

Markers cost one `str.count` and one regex substitution per root write that
contains a marker, at compile time only. `test_trace_table` covers a block
lambda nested inside an expression lambda inside a block lambda, a `map`
callback written as a block lambda, and a module project (module factories),
and asserts that each `S` and `D` line names the generated line that holds the
expected code.

**Mapping a failure** (`_geno_failure_report(exc, table, anchor)` in the
prelude) works in five steps:

1. Walk `exc.__traceback__` from outermost to innermost. Keep **body frames**:
   frames that satisfy all three tests:
   - `tb.tb_frame.f_globals is anchor.__globals__` (identity of the program's
     namespace);
   - `f_code.co_filename == anchor.__code__.co_filename`;
   - the line lies between the anchor line and the table's `E` line.

   Record, for each body frame, the `co_name` of the first non-body frame
   directly below it, if any (its "callee key"). Prelude, guard, worker,
   `isolated_host`, thread and event-loop frames are dropped.
   A filename-and-range test alone is not enough. The trusted worker script is
   itself `exec`'d from the `-c` bootstrap (`sandbox.py:1384-1390`), so its
   frames, including its own `def main()` (worker line ~80, which `exec`s the
   program), `safe_print` and `_geno_run_async`, also have
   `co_filename == "<string>"`. In the prelude-blob layout the body starts at
   line 1, so a body longer than the worker script would put the worker's
   `main` inside the Geno `main` range, under the same `co_name`. The worker's
   functions have the worker's globals, not the exec namespace, so the
   identity test excludes them. Prelude functions share the program's globals
   but have their own filename (blob) or lines before the anchor (standalone,
   stripped prelude), so the other two tests exclude them. The rule therefore
   holds in every layout without depending on the relative sizes of the
   prelude and the worker:
   - the standalone file;
   - the `geno_cli` worker's stripped prelude plus body in one `<string>`,
     where prelude lines fall before the anchor;
   - the marshalled-prelude blob, where the prelude has its own filename;
   - capability-assignment insertion.

   The anchor's real line, `co_firstlineno`, is read at failure time, not
   assumed. When the callee key names no candidate in the statement (for
   example the worker's `safe_print`, which the program reaches as `print`,
   raising the output-limit error), the position falls back to the statement,
   which is the `print` call's statement. `test_trace_table` covers a blob
   layout whose body is longer than the worker script and a `safe_print`
   output-limit failure.
2. A body frame is a **Geno frame** when the innermost `D` range that contains
   its line has a `host_name` equal to its `co_name`. Every other body frame is
   **transparent**: comprehension frames before 3.12, wrapper `(lambda ...)()`
   IIFEs, Geno lambdas, the `ensures` body helper, trait dispatchers, module
   factories.
3. The visible frames are the Geno frames, named by `display_name`. Frame *i*'s
   position comes from its **position frame**: the last body frame before Geno
   frame *i+1* (or the innermost body frame, for the last). That is either
   frame *i* itself or a transparent frame such as a lambda inside it.
4. Resolve the position frame's line to its statement through the `S` records.
   The lookup key is the host name of the next Geno frame when that frame
   directly follows the position frame, and otherwise the position frame's
   callee key (the builtin that called a callback, or `_geno_panic`,
   `_safe_index` and the like). If the statement has exactly one candidate with
   that key, the position is the candidate's location. For an awaited async
   function (section 2) the compiler also records an `await` candidate (key
   `<await>`) at each `await` expression. When the next Geno frame directly
   follows, the statement has no candidate with its host name (the split
   form `await pending`), and it has exactly one `<await>` candidate, that
   candidate is the position. Otherwise it is the statement's location. A failure raised inline in a Geno frame with no callee
   resolves to its statement, which for contract checks is the clause.
5. Bound the frames as in section 2. The failure location is the innermost
   frame's position.

Where the interpreter is precise, this gives the same position: `panic`,
`unwrap`, index, division, contract checks, calls between functions,
callbacks, lambdas. The exception is a statement that holds two candidates
with the same key, such as `xs[i] + ys[j]` failing on one index. There the
compiled backends report the statement's start, which is the right line but a
coarser column. Parity tests avoid such statements, and the spec documents the
precision.

**Standalone entry.** `_compiled_main_guard_opening` wraps the
`_geno_run_deep(...)` call in a `try`. On `Exception`, the guard:

1. flushes stdout;
2. calls the prelude's `_geno_report_uncaught(exc, _GENO_TRACE,
   _geno_trace_anchor, stderr=sys.stderr, host_traceback=...)`;
3. raises `SystemExit(1)`.

`host_traceback` is `os.environ.get("GENO_TRACEBACK") == "host"`. The guard
imports `os`, `sys` and, when needed, `traceback` locally, as it already
imports `sys`; the prelude gains no imports. `SystemExit` and
`KeyboardInterrupt` are not caught. The message rule is the worker's existing
one: `str(e)` for the `RuntimeError` family, otherwise `TypeName: str(e)`. The
label is always `Runtime Error`. `_GENO_TRACE`, `_geno_trace_anchor`,
`_geno_failure_report` and `_geno_report_uncaught` join
`_PYTHON_FIXED_GLOBAL_NAMES` and the generated prelude bindings. Every consumer
that rewrites the guard by its opening (the worker capture,
`compile_and_exec`) is updated with it. If the reporter itself fails (a
missing or malformed table), it prints the message line alone. It never prints
a host traceback unasked and never exits with a different status.

**The default `geno run` lane.** In `geno_cli` mode, the worker imports
`_geno_failure_report` from `geno._runtime_support` in its trusted import block.
It never uses the copy in the program namespace. When `main` raises, the
worker:

1. checks that `_GENO_TRACE` is a `str` and `_geno_trace_anchor` a plain
   function in the exec namespace;
2. builds the report;
3. adds a compact `trace` object to its error payload, built to a fixed
   serialized budget (below).

**Payload budget.** The parent keeps only
`max_output_length + _STDERR_RESULT_OVERHEAD` (4096) bytes of worker stderr
(`sandbox.py:1281`, 1335-1336), and the error message alone may already use up
to `max_output_length` characters. Unbudgeted stack fields could push the JSON
line past the capture window, cut it, and turn a runtime error into an
infrastructure failure. So:

- The new fields have a budget of their own,
  `TRACE_PAYLOAD_LIMIT = 3072` bytes of `json.dumps` output (measured after
  JSON escaping, so non-ASCII text cannot inflate past it), and the parent's
  capture window grows by the same reserved amount
  (`_STDERR_TRACE_OVERHEAD = 3072`, added to `_stderr_capture_limit`). The
  message's share and its truncation are unchanged, so a line that fits in
  0.6 still fits.
- The `trace` object is
  `{"files": [path, ...], "location": [file_idx, line, col] | null,
  "stack": [[name, file_idx, line, col], ...], "omitted": n}`. Each path
  appears once in the file table, which a deep recursion through one file
  makes tiny. Names are capped at 128 and paths at 512 code points.
- The worker serializes the object and, while it is over the budget, drops
  kept frames from the middle (the frames next to the elision point go first,
  and `omitted` grows); then replaces each path longer than 128 code points by
  its basename; then drops `stack`, keeping only `location`; then drops the
  object entirely. It never truncates the message to make room.
- The parent validates the object the way it validates the F2 denial tags
  (types, index ranges, positive integers, total size), keeps it as
  `ProcessSandbox.last_failure_location` and `last_failure_stack`, drops
  anything malformed (fail closed: message only), and prints with
  `runtime_report`, adding a snippet under the rule in section 4. The
  capability hint still prints last.

Example-phase failures are interpreted in the worker and keep travelling as
pre-formatted text through `_format_process_frontend_error`, which now uses
the new renderer. The worker passes it `limit=max_output_length`, and a
runtime report is fitted with `fit_report_text` (section 4) instead of being
cut by `_truncate_worker_text`, which keeps the head and would cut exactly the
final message line. Other frontend text (parse and type errors) is returned
whole and still cut at its tail by `_truncate_worker_text`, as in 0.6. Tests cover a panic
whose message is close to `max_output_length`, a run with
`--max-output-length 200`, a non-ASCII message and a 60-deep recursion with
long paths: each reports a parseable runtime error, never an infrastructure
failure, with the `Runtime Error:` line last. The worker
compiles with trace paths left as the frontend assigned them (absolute), so
the default lane prints the same paths as `--unsafe`. `geno compile` and
`compile_to_python` default to entry-relative paths.

### 9. Compiled JavaScript

The JavaScript backend reuses its source-map recorder (`_record_mapping`, the
deferred-marker machinery and prelude-offset handling). Statement records are
always collected for entry builds, not only under `track_source_map`, and the
recorder also records `D` function ranges and `C` candidates. Records written
while an arrow body is buffered (`_mapping_buffer_depth > 0`) go through the
same deferred `\0geno-map:N\0` markers that source-map entries use, resolved
at final placement, so the JavaScript table has the property section 8 gives
the Python recorder. From that data it emits the same `_GENO_TRACE` table
format. It does not embed a V3 source map:
that would need a VLQ decoder in the runtime and would tempt `sourcesContent`
into production artifacts, which `docs/deploy/browser.md` advises against.
`--source-map` output is unchanged.

**Node entry builds** (CommonJS and ESM with a `main` call) are changed in
these ways:

- The first user-code lines are
  `function _genoTraceAnchor() { return new Error(); }` and the `_GENO_TRACE`
  constant. Both are module-local bindings with no side effect, so importing
  an ESM build stays side-effect safe.
- The process-wide settings are made **only on the entry path**: inside the
  ESM entry guard (`_open_esm_entrypoint_guard`, which exists so that
  importing the module is side-effect safe), and immediately before the
  `main()` call in CommonJS. There, `_genoInstallReporter()` sets
  `Error.stackTraceLimit = Infinity` and installs
  `process.on("unhandledRejection", _genoReportUncaught)`. The unlimited trace
  limit lets a deep stack keep its outermost frames: V8 keeps the innermost
  *N*. It is not restored afterwards, because errors raised later by async
  work still need it, and the entry path owns the process. It costs nothing
  on success paths, because Geno control flow (`_PropagateReturn`) is not an
  `Error`; P4 verifies that no hot path constructs `Error`s. A host
  application that imports an ESM build never runs this code, and a
  `test_js_compiler` test asserts that importing an ESM build leaves
  `Error.stackTraceLimit` and the `unhandledRejection` listeners unchanged.
- `const _main_result = main();` and the result handling are wrapped in
  `try { ... } catch (e) { _genoReportUncaught(e); }`.
- The async form's `.catch(e => { console.error(e); ... })` becomes
  `.catch(e => _genoReportUncaught(e))`.
- `_genoReportUncaught` prints the report to stderr, appends `e.stack` when
  `process.env.GENO_TRACEBACK === "host"`, and then calls `process.exit(1)`.
  In 0.6 an uncaught synchronous throw ended Node at once. Merely setting
  `process.exitCode` would let pending work run after the report (for
  example an `Async` created without `await`, which JavaScript starts
  eagerly), printing more output or performing more effects. Exiting at once
  restores 0.6's termination for synchronous `main`, and for async `main` it
  matches the interpreter and compiled Python, which never run an unawaited
  `Async`. The flush behaviour is the same as Node's own fatal-exception path
  (synchronous for files and, on POSIX, pipes). A rejection of an `Async` that
  is never awaited reaches the same reporter through the `unhandledRejection`
  listener instead of Node's raw V8 stack. Tests cover a synchronous `main`
  that panics while an un-awaited async call is pending (no output after the
  report, status 1) and an un-awaited `Async` that fails (Geno report, status
  1).

**Mapping** reads structured V8 call sites. It temporarily installs
`Error.prepareStackTrace = (_, sites) => sites`, reads `e.stack` and restores
the hook, falling back to parsing the stack text if `e.stack` was already
formatted. It calls `_genoTraceAnchor()` the same way to learn the generated
file's URL and the anchor's line. That makes the ESM line delta and any
prologue irrelevant. From there, the algorithm is section 8's:

- A frame is a Geno frame when its function name, ignoring an `async ` prefix
  and any `Type.` receiver prefix, equals the host name of the innermost `D`
  range containing its line.
- Anonymous arrows and `Object.<anonymous>` are transparent.
- Prelude frames have lines before the anchor and are dropped.

**Under `node --enable-source-maps`** with a `--source-map` artifact, Node
rewrites formatted `e.stack` text to `.geno` positions. The structured path is
unaffected, because Node's rewriting happens in its own `prepareStackTrace`,
which the reporter's temporary override replaces; V8 formats a stack lazily,
on first access, and nothing in the runtime reads `e.stack` before the
reporter does. The text fallback (only for an error whose stack was already
formatted) finds no generated-file frames in that case and prints the
message line alone, as the fallback always may. A parity test runs the panic
program under `node --enable-source-maps` with a source map and asserts the
same Geno report.

**Async positions.** V8 starts an async function at its call, so a failure
before the callee's first real suspension shows the caller at the call
expression, and a failure after it shows the caller at its `await` (V8 async
stack traces). With `await f(...)` in one expression both are the same
position, so all three backends agree. In the split form (`let p = f(x)` ...
`await p`) JavaScript may report the creation line where the interpreter and
compiled Python report the `await`. The difference is documented under
Backend And Target Parity, not mapped away.

The new prelude functions are reached by name, so tree-shaking keeps them only
in entry builds. `_GENO_TRACE`, `_genoTraceAnchor` and `_genoReportUncaught`
join the reserved-name lists.

**Browser builds** (`geno build`, no `main` call on a Node host) emit no table
and install no reporter. Errors stay native `Error`s, as in 0.6. The browser
has no process to report to, and stack formats differ by engine.

### 10. Surfaces

- **`geno run --unsafe`, `geno run --watch`, the REPL:** the shared renderer,
  with a snippet. With `GENO_TRACEBACK=host` the interpreter's Python traceback
  follows the report.
- **`geno run --json`, `geno.api.run`/`run_path`:**
  - `_make_diagnostic` copies `geno_stack` and, for a
    `ContractViolationError`, `ContractDetails`;
  - the limit handlers (`api.py` 871-991) attach the location and stack the
    exceptions now carry;
  - `_remap_module_diagnostic_locations` remaps stack-frame locations too and
    rebuilds with `dataclasses.replace`, so new fields survive;
  - `GENO_TRACEBACK` has no effect on envelopes.
- **Hosted server:** `_serialize_run_result` emits the same keys, which pickle
  across its worker pipe. The bounded stack and arguments keep responses inside
  `_bounded_json_response_body`. `GENO_TRACEBACK` is ignored, and host
  tracebacks never reach a client.
- **`geno test`:** a runtime failure during an example records the
  violation's message as `Runtime error: <message>`, without the doubled
  `Runtime Error:` and location prefix that `str(exc)` produced. Text output
  adds the report's frame lines, indented under the violation. JSON violations
  gain optional `location`, `stack` and `stack_omitted`. Classification still
  goes by structured fields (`_classify_runtime_failure`).
- **`geno test --properties`:** reports are unchanged except that the clause
  text comes from the parse-time field. The JSON `failure.message` carries the
  new contract wording.
- **Isolated lane (F2):** as in section 8. A denial's hint and its trusted
  tags are unchanged.

### 11. Invariants

- Exit statuses are unchanged on every lane and target: 1 for an uncaught
  failure, as now.
- Codes are unchanged. Message wording changes only for contract failures, the
  `geno test` runtime-error text and compiled Python's `ensures` result
  display.
- `.message`, compiled `str(exc)` and JS `e.message` never contain the location
  or the stack.
- `Error: <value>` for an `Err` from `main` never gains a location or stack.
- Successful runs print nothing new. No frozen case changes: v0.4, v0.5 and
  v0.6 pass unchanged on every target. In particular, `runtime-error-out-of-bounds`
  keeps `Index 2 out of bounds` in stderr on cli-process, cli-direct, python,
  js and js-esm.

## Backend And Target Parity

Interpreter, compiled Python and compiled JavaScript produce the same report
for the same failure:

- the same label;
- the same message, for panics, contract failures and capability denials;
- the same frame names in the same order, with the same `omitted` count;
- the same frame positions and failure location for every construct listed in
  section 8.

Three differences are documented rather than removed:

- **Statement-level positions.** When one statement contains two candidates
  for the same failure, compiled backends report the statement's start.
- **Async creation versus await (JavaScript).** JavaScript starts an async
  function at its call. When an `Async` is created in one statement and
  awaited in another, a failure before the callee's first suspension
  positions the caller at the creation call on JavaScript, and at the `await`
  on the interpreter and compiled Python. With `await f(...)` written as one
  expression, all three agree; parity tests and conformance use that form.
- **Existing message differences.** Compiled index and division messages are
  shorter than the interpreter's (`Index 7 out of bounds` against
  `... for list of length 3`). This proposal does not reword them. Doing so
  would move the message-inferred codes, `runtime_error_code_for_message`, and
  the frozen substring case is written against the shared prefix.

A host stack overflow's depth differs by host (interpreter
`max_recursion_depth`, Python's recursion limit, V8's stack), so its
`omitted` count is host-specific. Its head and tail frames are not.

Targets:

- the interpreter covers every in-process host (CLI lanes, API, server,
  `geno test`);
- compiled Python covers standalone files and the default lane's worker;
- compiled JavaScript covers Node CommonJS and ESM entry builds;
- browser builds are intentionally unchanged;
- generated code with no `main` (libraries) carries no table.

The self-hosted interpreter (`selfhost/`) keeps its own `rt_error` output, as
`selfhost/SCOPE.md` allows. Its tests must stay green, and `SCOPE.md` records
the lag.

## Compatibility And Migration

- **Source:** none. No syntax or typing change.
- **Behavior:** exit statuses and stdout are unchanged. Stderr changes as
  follows; stderr wording is not a compatibility surface under
  `docs/operations/compatibility-policy.md`, but the changes are recorded
  anyway:
  - The default lane, compiled Python and compiled JS now print a stack.
    Standalone artifacts no longer print a host traceback.
    `test_main_result_compatibility.py:321-330`, which asserts `Traceback`,
    flips to assert the Geno report and its absence.
  - In `--unsafe` output, frame lines change from `  in <name>, <call site>`
    (with `main` at its definition) to `  <position> in <name>`. The `at` line
    disappears when a stack exists, and the snippet moves above the message.
  - A compiled Node program now exits as soon as the report is printed, for
    async `main` too (0.6 let pending async work continue after an async
    `main` rejected). A rejection of an un-awaited `Async` prints a Geno
    report instead of Node's raw stack. The status is 1 in both cases, as
    before.
  - The first stderr line of a failure is now `Stack trace ...` whenever a
    stack exists. Tests that read the first line as the message
    (`test_isolated_run_stdin.py:389-391`) read the `Runtime Error:` line
    instead.
- **Contract wording:** `Precondition failed for f: requires clause evaluated
  to false` becomes `Precondition failed: f(...) requires <clause>`. Tools must
  use `clause` / `reason` / `contract`, as proposal 0017 already asks. The
  change affects `panics "substring"` matching in both directions, and the
  0.7 compatibility notes list both:
  - **Stops matching.** `panics "Precondition failed"` and
    `panics "Postcondition failed"` still match. Substrings of the old
    wording that the new one drops stop matching: `"Precondition failed for"`,
    `"for safe_div:"` (any `"for <name>:"`), `"requires clause"`,
    `"ensures clause"` and `"evaluated to false"`. None exists in the
    repository's corpora (checked by a grep in P1 over `examples/`,
    `geno/std/`, `benchmark/`, `conformance/` and `selfhost/`).
  - **Starts matching.** The message now contains the argument values and the
    clause text, so a substring such as `"b != 0"`, `"0)"`, a function name
    followed by `(`, or a value that a panic message also contains can match a
    contract failure it did not match before. A `panics` example that expected
    a different panic but now hits a contract failure with that text passes
    where it used to fail. Writers who mean "this panic, not a contract
    failure" should include the panic's own words.
- **Documentation sample.** The F1 JSON sample in `docs/spec/v0.7.md` 5.4
  (line 989, `Postcondition failed for abs_val: ...`) is updated in P1,
  together with the wording change, so the draft spec is never stale between
  packages.
- **Diagnostics and API:** additive keys only (`stack`, `stack_omitted`,
  `contract`, and `location` where it was absent). `Diagnostic` grows two
  defaulted fields, so positional construction is unaffected.
- **Artifacts:** generated Python and JavaScript grow by the table, roughly
  one record per statement and call site. Compile-time and startup budgets
  are checked in P3 and P4. `spec.json`'s 0.6 description,
  `"nonzero with diagnostic or standalone-host traceback"`, stays as the 0.6
  record. The 0.7 change goes in `development_line.additions`.
- **Conformance:** frozen corpora are unchanged. Two runner changes, both
  additive:
  - The compiled lanes pass `filename=<case file name>` to
    `compile_to_python` / `compile_to_js` instead of the `<stdin>` default.
    This changes only `# geno:` comments and table paths of frozen cases, never
    their pinned output.
  - Development (schema 2) manifests gain an optional `expected_diagnostic`
    table on the `cli-json` lane: `code`, `location` (a `file:line:col` suffix
    match), `stack` (a list of `file:line:col in name` suffix matches) and
    `stack_omitted`. `load_manifest` accepts a failing envelope only with it.
    `conformance/v0.7` gains the cases in the Testing section.
- **Migration:** a script that scraped `in <name>, <loc>` lines should read
  `--json` `stack` instead. A test that pinned the old contract message should
  match `Precondition failed:` or the structured fields.

## Security And Resource Limits

- **No new capability or host access.** Reporting reads exception and
  traceback objects that the host already holds. It runs after the program has
  stopped: in the trusted worker's handler, or in the standalone guard. The
  program cannot reach the reporter. Its names are reserved on both backends,
  and sandbox AST validation still rejects `__traceback__` and frame access in
  program text.
- **The worker trusts only its own reporter.** It imports the reporter before
  untrusted input. It treats `_GENO_TRACE` as untrusted data: type-checked,
  parsed with bounded loops, and any parse error drops the stack. It never
  calls program-defined methods while mapping. Payload fields are truncated in
  the worker and validated again in the parent, which drops malformed fields.
- **No leaks.** Frames outside the program body are dropped by construction:
  the trusted worker, `isolated_host.py`, the sandbox, prelude helpers. Stdlib
  paths display as `std/...`. Compiled artifacts carry entry-relative paths
  only. Argument values are the program's own data, capped at 64 code points
  each, and diagnostics never render environment values. The default-lane
  parent reads snippets only from regular `.geno` files in the program's tree
  or the standard library, with a bounded read (section 4).
  A new security-corpus entry (`# EXPECT` header style) asserts that a denial
  in the isolated lane reports its call site with no `<string>`,
  `sandbox.py`, `isolated_host.py` or absolute non-program paths. Unit tests
  of the parent's snippet reader feed it forged payload locations that name a
  `.env` file, a non-`.geno` file in the program directory, a FIFO (POSIX
  only), a directory, a symlink out of the tree and a file larger than the
  read cap. Each prints no snippet, and the FIFO case returns without
  blocking.
- **The worker payload is budgeted.** The trace object has a fixed serialized
  budget with its own reserved capture overhead (section 8), so the new fields
  can neither crowd out the message nor turn a runtime error into an
  unparseable payload.
- **Bounded cost.** The interpreter captures once per failure, at O(depth)
  under `max_recursion_depth`. The bounded renderer's cost is bounded by the
  display limit, not the value's size. The JSON stack is at most 20 frames.
  JS `Error.stackTraceLimit = Infinity` is set only on the entry path and
  affects only failure paths. No per-call tracking is added to any backend,
  and the interpreter's builtin call path gains no work on success (callback
  call sites are patched in its failure-only `except` arms).
- **What cannot be reported.** A hard wall-clock kill or a memory exhaustion
  in the worker still ends with no stack. The report says so (message only).
- **The escape hatch.** `GENO_TRACEBACK=host` is read only by the CLI parent,
  the standalone guard and the Node entry. It is never visible to programs,
  envelopes or the hosted server. It exposes host internals by design, as
  `GENO_SANDBOX_DEBUG` already does.

`make security` and `python3 scripts/local_ci.py release` run in P3.

## Testing And Validation

New test files:

- `geno/tests/test_runtime_diagnostics.py` covers the interpreter:
  - frame names, positions and order for a panic at depth 3, an index error,
    a division error, a capability denial and a builtin callback (`map(g)`);
  - a lambda calling a function, and a lambda escaping and called later;
  - an impl method (`Type.method`) and a stdlib function (`std/` display);
  - the `<example>` origin;
  - async functions: async `main` -> `helper` -> `work` panicking (frames
    `main`, `helper`, `work`), `await helper(5)` positioned at the call, the
    split form positioned at the `await`, and a second `await` of a failed
    `Async`;
  - callback call sites patched at failure time: `map(g)` with `g`
    panicking, nested `map` inside a callback, and a recursion limit reached
    through a callback;
  - `ensures` argument display: a top-level parameter rebinding (call
    arguments shown, `checked_values` present) and a nested-block shadow (no
    `checked_values`);
  - recursion limit, step limit and cooperative timeout stacks, asserting
    structure only and never timing or message text;
  - elision at 21 and 42 frames, and `set_location` keeping type, code, tags
    and `.message`;
  - clause-text capture: multi-line clauses, comments, `//` inside strings,
    the 200-code-point cap, and `compare=False` (`geno fmt` round-trip);
  - the bounded renderer equalling full-render-then-truncate on generated
    values, including astral strings, nested quotes and large collections.
- `geno/tests/test_runtime_diagnostics_parity.py` runs the same programs on the
  interpreter, compiled Python and Node. It parses each report into
  label/message/frames/location and asserts equality for:
  - a panic at depth 3, an index error, `requires` and `ensures` with argument
    values;
  - an `ensures` with a string result (the old `str()` bug);
  - recursion-depth elision, a capability denial, async `main` (only the
    one-expression `await f(...)` form), a module project (module factories)
    and ESM;
  - `ensures` with a top-level and a nested-block parameter rebinding;
  - a panic inside a block lambda nested in an expression lambda (the
    scratch-buffer recorder).

  It also checks:
  - standalone output with no Geno import, by running in a clean virtualenv-free
    subprocess with `-I` and no `PYTHONPATH`;
  - `GENO_TRACEBACK=host` on Python, Node and the default lane;
  - the reporter's message-only fallback on a corrupted table;
  - the default lane's payload budget: a panic message close to
    `max_output_length`, `--max-output-length 200`, a non-ASCII message and a
    60-deep recursion with long paths (always a parseable runtime error with
    the message line last);
  - Node: a synchronous `main` panicking with an un-awaited async call
    pending (nothing printed after the report, status 1), an un-awaited
    failing `Async` (Geno report through `unhandledRejection`), and the panic
    program under `node --enable-source-maps` with a `--source-map` artifact.
- `geno/tests/test_trace_table.py`: table encoding round trip with escaping;
  the anchor offset under each layout (standalone, `geno_cli` stripped
  prelude, prelude blob, capability insertion on both splice paths, ESM); a
  blob-layout body longer than the trusted worker script; a `safe_print`
  output-limit failure; and recorder lines for block lambdas nested in
  expression lambdas, block-lambda callbacks and module factories, checked
  against the generated text, on both backends.
- Parent snippet-reader unit tests with forged payload locations (section 4:
  `.env`, non-`.geno`, FIFO, directory, symlink out of tree, oversized file).
- `test_js_compiler.py`: importing an ESM build leaves `Error.stackTraceLimit`
  and the `unhandledRejection` listener count unchanged.
- `geno/tests/test_runtime_report_parity.py`: byte equality of the three
  renderers, and equality of the shared constants.

Existing tests updated, with the old pins replaced by the new shape:

- `test_cli.py::TestRuntimeStackTrace` and `test_capability_denial_diagnostics.py`
  (162-238: frame lines, `endswith` hints kept);
- `test_isolated_run_stdin.py:389`, `test_isolated_run_capabilities.py` and
  `test_isolated_run_fs.py` (the `Runtime Error:` lines are kept);
- `test_main_result_compatibility.py:321-341`;
- the contract-wording pins:
  - `test_backend_parity.py:4550-4590`, `test_interpreter.py:1450-1515`;
  - `test_property_check.py:226,312` and `test_test_runner.py:461-652`;
  - `test_compiler.py:1566-1744` and `test_js_compiler.py:2120-2798`;
  - `test_module_constant_contracts.py:124-128`;
- `test_panic_examples.py`, which adds a test that `panics "substring"` and
  property-check matching never see location or stack text;
- `test_api.py` and `test_server.py` (envelope keys, pickling, remapped
  module locations);
- `test_js_source_maps.py` (main-call lines stay unmapped; `--source-map` output
  unchanged);
- `test_worker_prelude_contract.py` and `test_python_prelude_bindings.py`, after
  `scripts/update_prelude_bindings.py`;
- `test_cli_run_import_footprint.py`, unchanged and still green with
  `runtime_report` imported;
- `test_security_corpus.py` and `scripts/security_bounty.py` markers
  (`Runtime Error:` stays);
- `test_conformance_runner.py` (the `expected_diagnostic` field, the v0.7 case-id
  set);
- `test_language_contract.py` (`test_the_draft_records_runtime_diagnostics`:
  section, Changes bullet linking this proposal, `spec.json` addition);
- `test_docs_snippets.py` (the spec 6.9 sample equals the engine's output after
  path normalization, as F1's property-check sample does).

Conformance v0.7 cases on `cli-process`, `cli-direct`, `python`, `js` and
`js-esm` use path-independent `expected_stderr_contains` fragments such as
`panic_stack.geno:11:12 in scale`, plus a `cli-json` `expected_diagnostic`:

- `runtime-panic-stack`
- `runtime-index-stack`
- `runtime-requires-arguments`
- `runtime-ensures-result`
- `runtime-recursion-elision`
- `runtime-capability-denial-stack`, on the lanes that can withhold the
  capability

Validation per package:

- `python3 -m pytest <focused files> -q -p no:cacheprovider --timeout=300`;
- `python3 scripts/run_conformance.py --all-retained --target all --require-node`;
- `python3 scripts/validate_spec.py` and `scripts/validate_builtin_parity.py`;
- ruff, format and mypy;
- `python3 scripts/check_perf_ratchets.py` (run-hello, run-medium,
  compile-medium, and in P2 also check-medium and test-medium, which time
  example evaluation in the interpreter);
- `make security` (P3);
- `python3 scripts/local_ci.py targeted` per package and
  `python3 scripts/local_ci.py full` at the end.

## Rollout And Observability

No flag: the change is the 0.7 development line's behavior, specified by Draft
spec v0.7 (sections 4.1.1, 5.1, 6.9, 7.14 and 9.11.1), with a "Changes from
0.6" bullet linking this proposal and a `spec.json` `development_line.additions`
entry. CHANGELOG `[Unreleased]` gets Added (stacks, locations, contract
arguments, JSON fields, `GENO_TRACEBACK`) and Changed (contract wording,
compiled artifacts' stderr, `geno test` runtime-error text). The docs that
change are:

- `docs/reference/runtime-semantics.md`, which replaces "host traceback for a
  standalone generated artifact";
- `docs/llm-prompting.md`, which gains a new "Reading runtime failures"
  subsection after "Repair loop with fixes", using only ```text fences so that
  the indexed ```geno fences and `GENO_RULES` stay put;
- `docs/embedding-api.md`;
- `docs/deploy/cli.md`, which adds `GENO_TRACEBACK` to the environment table;
- `docs/deploy/hosted.md`;
- the denial sample in `docs/capabilities.md`;
- the compatibility policy's 0.7 (draft) notes.

Rollback is a revert: there is no persisted format. The trace table is
versioned (`G 1`), so a later table format is detected rather than misread.

## Alternatives

- **Do nothing / document `--unsafe`.** Rejected. The default lane, which most
  users and every benchmark agent use, would stay silent.
- **Per-call frame tracking in compiled code** (push and pop a Geno frame per
  call). Rejected by decision 3 and the perf budgets: it puts a cost on every
  call to answer a question asked once per failure.
- **Locations passed to every safety helper**
  (`_safe_index(xs, i, (3, 12))`). This gives exact columns, but it is a
  per-operation cost and an ABI change for every helper. Candidate sites give
  the same answer at zero runtime cost except in the rare ambiguous statement.
- **Embed a V3 source map and decode it at failure time** (also for Python).
  Rejected: it needs a VLQ decoder in both runtimes, it would tempt
  `sourcesContent` into artifacts, and it carries no function names. The
  compact table reuses the JS recorder's data instead.
- **Reuse the `# geno:` comments.** Rejected: they hold lines only, they skip
  expression statements, and a sandboxed `<string>` exec has no file to read
  them back from.
- **Python 3.11+ `co_positions` for exact columns.** Rejected for now: it is
  unavailable on 3.10, which CI supports, and would make positions differ by
  Python version.
- **Keep the 0.6 frame convention** (`in <callee>, <call site>`, `main` at its
  definition). Rejected: under a "most recent call last" header it reads like
  Python's convention but means something else, and it needs a separate `at`
  line for the failure. With frames carrying their own position, the innermost
  frame is the failure.
- **Message first, stack after.** Rejected. It would break the "hint is
  last" contract of F2 and the Python and JavaScript reading habit. It would
  also bury the message under 20 frame lines in a terminal.
- **Put the call site in `location` for `requires`.** The brief's example
  points there. Rejected because it changes an existing envelope value
  (decision 4). The call site is the caller frame's position, one line above.
- **Attach E407/E408 to contract failures.** Rejected for the same reason:
  it changes the existing `code` value from E499.
- **Render clause text from the AST** (`doc_generator._render_expr` or a new
  printer). Rejected: after checking, the AST holds internal names, and the
  existing renderer drops precedence.
- **Operator-precise carets** (`compare=False` operator locations on
  `BinaryOp`/`IndexAccess`). Deferred. It is independent of this feature and
  would need every backend's candidates to follow.

## Unresolved Questions

1. **"Rendered like `to_string`" (decision 2).** This design quotes top-level
   string arguments (`f(s: "0")`). `to_string` would print them bare
   (`f(s: 0)`), which is ambiguous, so the design uses the diagnostic rendering
   that `ensures`' `result was` already uses. The project lead should confirm
   this reading.
2. **Where a `requires` failure is located (decisions 2 and 4).** The brief's
   example (`at main.geno:7:12`) suggests the call site. Decision 4 keeps
   existing values, so `location` stays the clause and the call site is the
   caller frame. If the lead prefers the call site as `location`, that is a
   one-line change in each backend, but it changes an envelope value.
3. **E407/E408.** Should a later proposal start emitting them for contract
   failures, as a recorded value change?
4. **Compiled positions in ambiguous statements.** Is statement-level
   precision acceptable long term, or should a follow-up add expression-level
   host columns (JS has them, and Python has them from 3.11 on)?
5. **Message unification.** Should compiled index and division messages adopt
   the interpreter's wording in a separate change, with its code impact
   assessed?
6. **Absolute paths in `# geno:` comments.** Generated Python already embeds
   absolute build paths in comments. This proposal does not touch them. A
   follow-up could make them entry-relative like the table.
7. **Interaction with F7 (tail-call elimination).** Self tail calls
   compiled to loops will show as one frame. F7's proposal must state whether
   the stack then reports a repeat count.
8. **Step and timeout messages** still carry ` at <loc>` in their text, now
   duplicated by `location`. Should their wording be cleaned up separately?

## Implementation Plan

Five sequential packages; each leaves the tree green.

1. **Contract failures on every backend.**
   - Parse-time `source_text` on `RequiresClause`/`EnsuresClause`, with
     `source` threaded to every runtime parse site.
   - The bounded renderer in the interpreter, the Python prelude and the JS
     prelude.
   - The new message grammar.
   - `ContractViolationError` fields `function`, `clause_text` and `arguments`
     (`with_message` carrying them).
   - The compiled `_geno_contract_violation` factories, replacing the
     static-string and f-string raises (fixing the `ensures` `str()` bug).
   - The `_explain_rejection_example` prefix, and the property-check clause
     text read from the field.
   - `ensures` argument display from the call's arguments: the interpreter
     passes `args` to `_check_ensures`; the compilers emit the arguments
     snapshot only for functions that rebind a parameter at top level; the
     `checked_values` suffix.
   - The `docs/spec/v0.7.md` 5.4 JSON sample's message, updated with the
     wording, and a corpus grep for the old-wording substrings.

   Every wording pin is updated in the same package. The prelude bindings are
   regenerated.

   *Done when:*
   - a three-backend test shows identical
     `Precondition failed: safe_div(a: 8, b: 0) requires b != 0` and
     `Postcondition failed: shout(s: "hi") ensures ... (result was "hi!")`
     messages;
   - the multi-line clause test passes;
   - `geno fmt` is idempotent on the corpus;
   - all retained conformance passes.
2. **Interpreter stacks and every interpreter-hosted surface.**
   - `diagnostics.StackFrame`/`GenoStack`/`ContractDetails` and the `Diagnostic`
     fields.
   - `geno/runtime_report.py` (bounding, `std/` display, text renderer).
   - Interpreter capture on `GenoRuntimeError | SandboxError` in
     `_call_function` and `_run_async_value` (with the async position rule),
     `set_location`, callback call sites patched in the builtin branch's
     failure-only `except` arms, the `<example>` origin, lambda folding and
     display names.
   - `runtime_report.fit_report_text` for the worker's example-phase text.
   - `cli/_util._print_runtime_error` on the new renderer (`--unsafe`, watch,
     REPL, worker example-phase formatting).
   - `--json`, `api.py` (`_make_diagnostic`, limit handlers, module remap), and
     `server.py` serialization.
   - `geno test` text and JSON violations.
   - `GENO_TRACEBACK=host` for interpreter lanes.

   The CLI, denial, stdin and API pins are updated.

   *Done when:*
   - `test_runtime_diagnostics.py` passes;
   - `--unsafe` and `--json` show the guide's demo output exactly (modulo
     path);
   - the import-footprint test and the perf ratchets (including check-medium
     and test-medium) are green.
3. **Compiled Python mapping.**
   - The table recorder in `compiler.py` (every statement kind, `D`/`S`/`T`/`C`
     records including `<await>` candidates, in-band `\0geno-trace:N\0`
     markers resolved at the root buffer, entry-relative paths, the anchor as
     the body section's first line), with names reserved.
   - `_geno_failure_report` / `_geno_report_uncaught` in `_runtime_support.py`
     (body frames by globals identity, filename and range; bindings
     regenerated).
   - The standalone guard and its rewriting consumers.
   - The worker's trusted import, the budgeted `trace` payload object and the
     reserved capture overhead, and parent validation and printing in
     `sandbox.py` / `cli/run.py` (strict snippet reader, hint last).
   - `GENO_TRACEBACK=host` for standalone and the default lane.
   - The conformance runner passing `filename=`.
   - The security-corpus entry.

   *Done when:*
   - the demo program prints identical frames on `geno run`, `--unsafe` and
     `python3 demo.py`;
   - `test_main_result_compatibility` asserts the Geno report;
   - the Python half of `test_runtime_diagnostics_parity.py` and
     `test_trace_table.py` pass;
   - `make security`, the perf ratchets (run-hello, run-medium,
     compile-medium) and all retained conformance are green.
4. **Compiled JavaScript mapping.**
   - The recorder always on for entry builds, emitting `D`/`C` records (through
     the deferred markers inside buffered arrows) and the table.
   - The anchor, and `_genoInstallReporter()` (`Error.stackTraceLimit`, the
     `unhandledRejection` listener) on the entry path only: inside the ESM
     guard, before `main()` in CommonJS.
   - The `try`/`catch` and async `.catch` reporters ending in
     `process.exit(1)`, CallSite-based mapping and the text fallback, tested
     under `--enable-source-maps`.
   - Tree-shaking by name, reserved names, ESM, and the browser left untouched.
   - `GENO_TRACEBACK=host` on Node.

   *Done when:*
   - the full three-backend `test_runtime_diagnostics_parity.py` (including
     async `main`, a module project and ESM) and `test_runtime_report_parity.py`
     pass;
   - `test_js_source_maps.py` is unchanged and green;
   - all retained conformance passes with `--require-node`.
5. **Specification, documentation and conformance.**
   - Spec v0.7:
     - sections 6.9 (the report, its sample pinned by
       `test_docs_snippets`, the frame-position convention including async
       frames and the JavaScript creation-site difference, and the
       `ensures` argument/`checked_values` rule), 4.1.1, 5.1, 5.4's
       `contract`/`stack` keys (its message sample already updated in P1),
       7.14 and the 9.11.1 denial sample;
     - the "Changes from 0.6" bullet linking this proposal.
   - `spec.json` `development_line.additions` (validated by
     `scripts/validate_spec.py`).
   - `docs/reference/runtime-semantics.md`, the `docs/llm-prompting.md`
     subsection, `docs/embedding-api.md`, `docs/deploy/cli.md`,
     `docs/deploy/hosted.md`, `docs/capabilities.md`, the compatibility-policy
     0.7 notes and `selfhost/SCOPE.md`.
   - CHANGELOG `[Unreleased]`.
   - The runner's `expected_diagnostic` field with tests, and the six
     `conformance/v0.7` cases (formatted with `geno fmt`).
   - `test_language_contract` registration.
   - Formatter, LSP, constrained-decoding grammar (`geno/constraints.py`) and
     VS Code grammar need no change because no syntax or keyword changes; the
     package confirms this with their existing test suites rather than edits.

   *Done when:*
   - `python3 scripts/run_conformance.py --all-retained --target all --require-node`
     passes with the new cases;
   - `scripts/validate_spec.py`, `test_docs_snippets.py`,
     `test_language_contract.py`, `test_formatter_corpus.py` and
     `test_llm_benchmark.py` pass;
   - `python3 scripts/local_ci.py full` is green.
