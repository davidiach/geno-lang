# Proposal 0017: Panic Examples and Contract-Driven Property Checks

- Status: Draft
- Authors: Claude Code (drafted and implemented at the project lead's request)
- Created: 2026-11-05
- Discussion: https://github.com/davidiach/geno-lang/pulls (branch
  `claude/friendly-mccarthy-ot2dc6`)
- Supersedes: None
- Review: None yet; awaiting project lead acceptance under `GOVERNANCE.md`.
- Target: the 0.7 development line, which this proposal opens
  (`docs/spec/v0.7.md`, Draft)

## Summary

Two additions make the contracts a function already states do more work.

An example can say that an input must panic:

```geno
func safe_div(a: Int, b: Int) -> Int
    example 6, 3 -> 2
    example 1, 0 panics "division"
    return a / b
end func

func usage() -> String
    example () panics "not implemented"
    panic("not implemented")
end func
```

`geno test --properties` checks every pure function against its own `requires`
and `ensures` on generated inputs, shrinks any failure to a small
counterexample, and prints it as an `example` line:

```text
$ geno test --properties abs.geno
  PASS  abs.geno (1/1) 15ms

Properties (seed 0, 100 trials per function)
  FAIL  abs_val  abs.geno:1
        ensures result >= 0 failed (result was -1)
        counterexample: -1
        reproduce: geno test --properties --seed 0 --filter abs_val abs.geno

1 passed, 0 failed, in 0.04s
Properties: 0 passed, 1 failed
```

(Seed 0 generates `-1` on the third trial, so nothing is shrunk. A larger
first failure is printed as `counterexample: -1  (shrunk from -37 in 1 step)`.)

`panics` is a contextual word: it means "expects a panic" only directly after an
example's inputs, and stays an ordinary identifier everywhere else. Plain
`geno test` output and exit status do not change. The package version stays
`0.6.0`; the syntax is specified by the new Draft `docs/spec/v0.7.md`.

## Motivation

Geno asks every function for `example` clauses and lets it state `requires` and
`ensures`. The checks behind those clauses are weaker than they look:

- **Contracts are only checked on the inputs someone wrote.** This program
  passes `geno test` (1/1):

  ```geno
  func abs_val(n: Int) -> Int
      ensures result >= 0
      example 3 -> 3
      if n >= 0 then
          return n
      end if
      return n
  end func
  ```

  The postcondition is false for every negative input, and nothing reports it
  until a negative input reaches the function at run time. An `ensures` clause
  is a universally quantified statement, so it should be checked as one.
- **A failure path cannot be stated.** Proposal 0009 made a panic the one way a
  bug ends a program, and proposal 0003 left `example ... panics` as an open
  question. Today every attempt goes wrong in a different way, all checked:

  | Written | Result in 0.6 |
  | --- | --- |
  | `example 1, 0 -> panics` | E301 "Undefined variable: panics. Did you mean 'panic'?", with a fix to `panic` |
  | `example 1, 0 -> panic` (the fix applied) | E300 "Example output type mismatch: expected Int, got (String) -> Never" |
  | `example 1, 0 -> panic("division")` | Passes `geno check` (`Never` is compatible with `Int`), then always fails: the expected value panics before the call |
  | `example 1, 0 panics "division"` | Parse error "Expected '->', got identifier" |

  The only workaround is a `test` block, which cannot express "this call
  panics" either, because Geno code cannot observe a panic.
- **Models write these forms.** Rust's `#[should_panic(expected = ...)]`,
  pytest's `raises(match=...)` and Hypothesis-style property tests are common in
  training data. Pillar P1 of proposal 0003 is first-try correctness: when the
  natural spelling is wrong, the toolchain should say what to write instead.

The two features belong together. A property check needs to know which inputs
are outside a function's domain (its `requires`) and which panics are intended
(its `panics` examples); without the second, every function that panics on
purpose would fail its property check.

## Guide-Level Design

### Panic examples

```geno
example <inputs> panics
example <inputs> panics "substring"
example () panics                       // zero-argument function
```

The example passes when calling the function with those inputs ends in a panic:
a `panic(...)` call or any runtime error, including a failed `requires` or
`ensures`. With a message, the panic's message must also contain the given text,
ignoring case. A function may have only `panics` examples; that satisfies the
rule that every function has at least one example.

Running out of a resource limit is not a panic. An example that hits the step
limit, the timeout, the recursion depth or a size limit fails and names the
limit:

```text
$ geno test spin.geno
  FAIL  spin.geno (0/1)
        x spin: expected a panic, but the call hit the step limit (a resource limit is not a panic)
```

The same is true of a capability denial: in `geno test` an example of a
function that reads the environment would otherwise "pass" only because the
test runner does not grant `env`.

Writing the arrow form gets an error with a fix:

```text
$ geno check div.geno
Type Error: div.geno:3:18: An example that expects a panic is written without an arrow: example 1, 0 panics "division"
      example 1, 0 -> panic("division")
                   ^
  fix: Write 'panics "division"' (`geno check --fix` applies it)
```

`geno check --fix` (and the language server's quick fix) rewrites
`-> panics`, `-> panics "m"`, `-> panic` and `-> panic("m")` to the `panics`
form, and `example -> panics` to `example () panics`.

### Property checks

```text
geno test --properties [--trials N] [--seed S] [--filter GLOB] [--json] PATH
```

For each eligible function the runner generates arguments from the parameter
types, calls the function in the interpreter, and checks the outcome:

- an argument set that the function's own `requires` rejects is **discarded**:
  it is not in the function's domain;
- an `ensures` violation is a **failure**;
- any other panic is a **failure**, unless the function has a `panics` example,
  which says it panics on purpose; an `ensures` violation still fails such a
  function;
- hitting a resource limit is a **failure**: `requires` is how a function says
  an input is too large.

A failing input is shrunk (integers toward 0, shorter strings and lists,
smaller constructors, `None`) and printed as Geno source that can be pasted
after `example`. The fix for a reported failure is one of: correct the body;
add a `requires` that states the domain; or add a `panics` example that states
the panic is intended.

The default seed is fixed, so a run is reproducible and CI is deterministic;
the seed is printed with every failure. Eligible functions are those whose
inferred effects are at most `mutation`, other than `main`, the app lifecycle
hooks and async functions, and whose parameter types all have a generator.
A function in scope whose parameter types have no generator is listed as
skipped with the reason under `-v`; functions out of scope (5.1) are not
listed.

Property checks are opt-in. They run after a file's examples and test blocks,
in their own interpreter, and cannot change what plain `geno test` reports.

## Reference Design

### 1. Syntax

The example production becomes (spec v0.7 section 14):

```ebnf
example        = "example" ( exprs? "->" expr | exprs panics_tail ) ;
panics_tail    = "panics" string? ;   (* "panics" is an identifier token *)
```

Parsing, in `_parse_example_clause`:

1. Parse the inputs as today. The bare-arrow zero-argument form (`example ->
   v`) is unchanged and has no `panics` counterpart: zero-argument panics
   examples write `()`.
2. If the next token is the identifier `panics` **on the same line as the last
   input token**, consume it. Otherwise expect `->` as today.
3. If the next token is a `STRING` on the same line, it is the message. A
   triple-quoted string is a `STRING` token and is accepted; an f-string is not
   (the message is matched statically and rendered in docs).
4. After `panics` or its message, any further token on the same line is E201
   "Expected a message string or the end of the line after 'panics'". Without
   this rule `example 1 panics f"x"` would silently parse the f-string as the
   first body statement.

`panics` is not added to `KEYWORDS`, `spec.json` `keywords`, the selfhost
keyword groups, LSP keyword completions or the VS Code keyword pattern. It stays
a legal name for variables, parameters and functions; `example panics -> 1`
(with a variable named `panics` in scope) and `example x -> panics` (with a
value named `panics` in scope) keep their meaning, because the parser commits
to the panics form only when `panics` follows the inputs without an arrow.

The same-line rule matches how the formatter lays examples out and keeps a
string on the next line (a body statement) from being taken as a message.

### 2. AST

`ExampleClause` (`geno/ast_nodes.py`) becomes:

```python
@dataclass
class ExampleClause(ASTNode):
    input_expr: Expression
    output_expr: Expression | None          # None for a panics example
    panic_message: str | None = None        # the text after `panics`, if any
    # Source positions for fixes; not part of the program's structure.
    arrow_location: SourceLocation | None = field(default=None, repr=False, compare=False)
    expectation_location: SourceLocation | None = field(default=None, repr=False, compare=False)
    end_location: SourceLocation | None = field(default=None, repr=False, compare=False)
    inputs_end_location: SourceLocation | None = field(default=None, repr=False, compare=False)

    @property
    def expects_panic(self) -> bool:
        return self.output_expr is None
```

`arrow_location` is the `->` token, `expectation_location` the first token
after it (or the `panics` word), and `end_location` the last token of the
clause. `inputs_end_location` (added while implementing package 2) is just
after the code before the `->` on its line: the last input, or `example` in
the bare-arrow form. The parser takes it from the source text, trimming the
whitespace before the arrow; it is `None` when the token before the arrow is
on an earlier line or the parser was given no source. A token has no end
position, and whether whitespace precedes the arrow decides whether deleting
`-> ` leaves `0 panics` or glues `0panics`, so a fix that removes the arrow
needs it. All four are `compare=False`, so the formatter's `_ast_key`
comparison is unaffected; `panic_message` is a compared field, so two examples
that differ only in their message do not compare equal. Making `output_expr` optional lets
mypy list every consumer. Every one of them handles `None` (section 4).

A separate `PanicsExample` node was considered and rejected: every consumer
already iterates `specs.examples`, and a second list or a union would double
those loops for one bit of information.

### 3. Type checking

`_check_specs` checks a panics example's inputs exactly as it checks any
example's (same arity, defaults and generic-instantiation rules) and skips the
output check. The generic-bounds check runs with the substitutions the inputs
bind; a type parameter that appears only in the return type is unbound in a
panics example and is not bound-checked.

The "at least one example" rule (`typechecker.py`, `_check_function_def`)
already counts any example, so a function with only panics examples satisfies
it with no change.

**E330 `TYPE_EXAMPLE_PANIC_FORM`** (new; opens a 33x "specification clause"
range in `ErrorCode`): an example whose expected value is written as a panic.
It is reported, before the output expression is checked (so the existing
"Did you mean 'panic'?" E301 typo fix is never offered), when the expected
value is:

| Written | Condition | Fix |
| --- | --- | --- |
| `-> panics` | no binding named `panics` is visible in the example's scope (`global_env.lookup("panics") is None`) | Delete `-> ` |
| `-> panics "m"` | always (the parser records the same-line string when the output is the bare identifier `panics`; a value example cannot carry one) | Delete `-> ` |
| `-> panic` | `panic` resolves to the builtin (`_identifier_resolves_to_builtin`, so a user's own `panic` is respected) | Delete `-> `, write `panics` |
| `-> panic("m")` (or `panic(message: "m")`) with a plain string literal | the callee resolves to the builtin | Delete `-> `, replace `panic(` with `panics `, delete `)` |
| `-> panic(e)` with any other argument | the callee resolves to the builtin | None (the message is not a literal) |

For the bare-arrow zero-argument form, `example -> panics` and friends, the
fix writes `() ` in place of the arrow, giving `example () panics`. "Delete
`-> `" is one edit from `inputs_end_location` to the expectation (to the end
of `panic`, or to the string literal, in the other rows) that writes a single
space, so the result is `example 1, 0 panics` whatever spacing surrounded the
arrow (`0->panic(...)` included); the other positions are
`expectation_location`, `end_location` (the call's `)`) and the string
literal's own location. When `inputs_end_location` is `None` (the arrow starts
its line, or no source text) the diagnostic has no fix. The diagnostic is an
error, reported at the arrow. Its message names the form to write, for
example `An example that expects a panic is written without an arrow:
example 1, 0 panics "division"`.

`-> panic("m")` (and `-> panic(e)`) was accepted by 0.6: such a program passes
`geno check` and compiles with `geno compile`/`geno build`, which never run
examples, although the example itself could never pass (the expected value is
evaluated before the call). Rejecting it is therefore a **breaking change** for
0.7, recorded as one under the compatibility policy (see Compatibility). It
stays an error rather than a warning: a warning would leave an example that can
never pass in an accepted program, and Geno's only warning channel (W300) is a
separate post-check pass with its own API and LSP plumbing that this rule does
not need.

Parse-phase forms with existing codes:

- `example panics` / `example panics "m"` (the inputs are the single token
  `panics`, and the next token is a string or starts a later line, where 0.6
  reported "Expected '->'"): E201 "A zero-argument panics example is written
  `example () panics`", with a fix that inserts `() `. The parser records the
  error and goes on as if `()` were written, so the rest of the file is still
  parsed.
- `panics ""` (also `""""""`): E202 "An empty panics message matches every
  panic; omit it", with a fix that deletes from the end of `panics` to the end
  of the line's code (the message is the last code on its line, or E201
  applies). Also recorded rather than raised.

These codes are already published; their meanings are unchanged.

### 4. Evaluating a panics example

One classifier decides what an exception means, and every evaluator uses it. It
lives in a new module `geno/panic_outcome.py` (it imports `values`, `sandbox`
and `diagnostics`; `harness` and everything above it may import it). Besides
the functions below it exports `CALL_FAILURES` (the exception tuple an
evaluator catches around the call), `classify_compiled_failure` and
`compiled_limit` for compiled Python, and `evaluate_panics_call(call, expected,
*, render, reraise_unsatisfying=False)`, which the interpreter and `geno test`
share:

```python
@dataclass(frozen=True)
class CallFailure:
    kind: Literal["panic", "limit", "refused"]
    message: str               # the Geno-facing message, without location or "Runtime Error:"
    limit: str | None = None   # "step limit", "timeout", "recursion depth", ...

def classify_call_failure(exc: BaseException) -> CallFailure | None: ...
def panic_message_matches(message: str, expected: str) -> bool: ...  # casefolded substring
def check_panics_example(failure: CallFailure | None, returned: object, expected: str | None) -> str | None: ...
```

`classify_call_failure` returns `None` for exceptions that are not Geno-facing
(an internal `TypeError`, say), which callers re-raise into their existing
internal-error handling. Otherwise:

| Exception | Kind |
| --- | --- |
| `StepLimitExceeded`, `sandbox.TimeoutError`, `RecursionLimitError`, other `ResourceLimitExceeded` | `limit` ("step limit", "timeout", "recursion depth limit", "resource limit") |
| Python `RecursionError`, `MemoryError` | `limit` ("host recursion limit", "memory limit") |
| `RuntimeLimitError` (new, see below) | `limit` (named by its `limit` field: "collection size limit", "integer size limit", "numeric range limit", "output limit") |
| `GenoRuntimeError` with E412 (capability denied) or E413 (host callback missing); `SecurityViolation` (E500) | `refused` (one switch, see below) |
| Any other `GenoRuntimeError`, including `ContractViolationError` and E414 `panic(...)` | `panic` |
| Compiled-Python harness only: builtin `RuntimeError` (not `RecursionError`), `ArithmeticError`, `LookupError` | `limit` when the message matches a compiled-runtime limit pattern, else `panic` |

**Limits are marked structurally.** The interpreter and the builtins raise
their sandbox-configurable limits as uncoded `GenoRuntimeError`s today, from
about thirty sites, and the messages vary: besides `<Kind> size exceeds limit
(n > m)`, `Integer exceeds maximum size (n bits)` and `Output limit exceeded`,
the integer-bit limit is also enforced before the operation runs with
`Exponentiation result too large (estimated N bits)` (both `**`
evaluation paths in `interpreter.py`) and `Left/Right shift amount too large (N bits)`
(verified: `example 2, 100000` on `a ** b` raises the first). A message
classifier would miss sites like these. So `geno/values.py` gains

```python
class RuntimeLimitError(GenoRuntimeError):
    """A sandbox-configurable runtime limit was exceeded (not a panic)."""
    def __init__(self, message: str, location: SourceLocation | None = None,
                 error_code: Any | None = None, *,
                 limit: Literal["collection size", "integer size",
                                "numeric range", "output"]) -> None: ...
```

and every site guarded by `SandboxConfig.max_collection_size`,
`max_integer_bits` or `max_output_length` (in the interpreter, and in
`builtins.py` through `_check_collection_size`, `_check_result_string_size`
and the other `max_collection_size` helpers) raises it. The message text is
unchanged, and so is `error_code` (still derived from the message, so the
E499 the security corpus pins stays E499). Re-wrap sites construct
`RuntimeLimitError` too: `builtins._check_result_string_size` re-raises the
limit it caught with its own prefix, and the interpreter's `OverflowError`
path for `**` ("Exponentiation result too large", a `Float` power outside the
host's range) raises it with `limit="numeric range"`. That last one is not
`SandboxConfig` state, but it is a property of the host rather than of the
input (JavaScript returns `Infinity`), and compiled Python reports it with the
same message as the integer pre-check, so the interpreter and the compiled
harness classify it alike. The classifier decides on the type; it never
inspects Geno-side messages, so `panic("List size exceeds limit")` stays a
panic without a special case.

Compiled Python raises plain `RuntimeError`s from `_runtime_support.py` and the
prelude, which this proposal does not restructure (the generated runtime is
shared with every compiled program). For the compiled harness only,
`panic_outcome` keeps a small pattern list: the size, integer, exponentiation,
shift and output-limit messages above, plus `_GenoOutputLimitError` by type.
A bare "Exponentiation result too large" (the `OverflowError` path) is the
numeric range limit; "Exponentiation result too large (estimated N bits)" is the
integer size limit.

Fixed builtin argument caps that are not sandbox configuration
(`parse_int`/`parse_float` "input string too long", regex pattern/text length
and group depth, `json_parse` nesting depth, "exceeds JavaScript safe integer
range") are documented properties of those builtins' domains, independent of
the host's `SandboxConfig`, and are classified as `panic`. The spec lists them.

A test enforces the marking without relying on message text: it walks the AST
of `interpreter.py` and `builtins.py` and finds every `raise` that a limit
guards, meaning a raise in the body of an `if` whose test reads a limit, or in
a `RuntimeError` handler of a `try` that passes a limit to a call. A limit is
`max_collection_size`, `max_integer_bits`, `max_output_length` or
`_effective_max_collection_size`, a local or `self` attribute assigned from
one, or a parameter that some call in the module passes one to, found to a
fixpoint. That last rule is how helpers such as
`_check_collection_size(kind, size, limit)` and `_StringifyWriter` are found. A
call's result is data rather than a limit, unless the call is to one of the few
functions that pass a limit through (`_effective_max_collection_size`, `min`,
...): `to_string` bound to a cap returns a string, not a limit. Every guarded
raise must raise `RuntimeLimitError`; a second test checks that the scan still
finds the known helpers. (Checking every raise in such a function, as first
planned, would flag the ordinary argument errors those builtins also raise.)
A third check runs the same scan over `_runtime_support.py`, with
`_MAX_COLLECTION_SIZE` and `_MAX_INTEGER_BITS` as the limits, renders each
guarded raise's message with placeholder values, and requires the compiled
pattern list to recognize it.
Behavioural tests cover a collection, a string, `**`, `<<`, `>>` and output
limit behind a panics example.

**Refusals.** E412, E413 and E500 are classified by one predicate,
`_refusal_satisfies_panics() -> False`, the only place that decides it, so the
project lead's answer to the open question (Unresolved Questions) is a one-line
change plus its tests. The v0.7 spec names these three codes as an explicit,
documented exception to "any runtime error".

`check_panics_example` returns `None` (pass) or the failure reason:

- the call returned: `expected a panic, but it returned 3`;
- a `panic` whose message does not contain the text:
  `expected a panic containing "division", but it panicked with: Index 5 out of bounds ...`;
- a `limit`: `expected a panic, but the call hit the step limit (a resource limit is not a panic)`;
- `refused`: `expected a panic, but the call was refused: Capability denied: ...`.

The message is matched against `.message`, never `str(exc)`, which carries the
location and a `Runtime Error:` prefix. Matching is a case-insensitive
(`casefold`) substring test: the interpreter says `Division by zero: cannot
compute 1 / 0` and compiled code says `Division by zero`, and an author writes
`"division"`.

The four evaluators:

1. **`Interpreter._verify_examples`** (every `geno run` lane, `geno.api.run`
   with `check_examples`, the hosted server's `check_examples` payload, and the
   conformance interpreter and CLI lanes). A panics example evaluates its
   inputs (an exception there propagates as today: the example is broken, not
   satisfied), then calls the function inside a handler for
   `(GenoRuntimeError, SandboxError, RecursionError, MemoryError)`. A pass
   continues. A returned value or a wrong message raises E409
   `Example verification failed for f: expected a panic ...`. A `limit` or
   `refused` failure re-raises the original exception unchanged, so `geno run`
   still exits with E501-E503, E412 or E499 for it. Examples share `main`'s
   budget in `geno run`, so a limit hit there is the run's own limit.
   `_explain_rejection_example` is never applied to a panics example: a
   rejected precondition is a panic and passes.
2. **`test_runner._run_examples`** (`geno test`). Same steps; every failure,
   including `limit` and `refused`, is recorded as an `example` violation with
   the reason above, and the run continues with the next example. The inputs
   are evaluated under the example's deadline and the call under a deadline of
   its own, so a timeout raised as the call's deadline closes is the call's.
3. **`harness.HarnessRunner._run_example`**, `run_harness_from_source` and
   `run_harness_from_compiled`. `HarnessRunner` gains `classify_failure`
   (default `classify_call_failure`; the compiled harness passes
   `classify_compiled_failure`) and `format_value` (default `repr`; the source
   harness passes the interpreter's). The runner's existing handler classifies
   the exception only when it came from the panics example's call, so no new
   broad handler is added.
4. **Renderers.** The compiled Python docstring writes
   `Example: <inputs> panics "<message>"`, quoting the message with
   `json.dumps` so a message containing `"""` cannot end the docstring.
   `doc_generator` renders `example 1, 0 panics "division"` (HTML-escaped).
   `base_compiler`'s visit already tolerates `None`. LSP hover shows only
   signatures and needs no change. The JavaScript compiler does not emit
   examples.

No new handler catches `Exception` or `BaseException`: `KeyboardInterrupt`,
`ReturnException` and `PropagateException` pass through, and the CI broad
exception ratchet (54/55) is not spent.

Known gap kept as is: `geno run` does not evaluate impl-method examples today
(`geno test` does). This proposal does not change that; panics examples on impl
methods are checked by `geno test` and the harness like other impl examples.
Making `geno run` check impl examples could stop programs that run today, and
belongs in its own change.

### 5. Property checks

A new module `geno/property_check.py`, imported only when `--properties` is
given (the `geno test` import footprint and the `test-medium` perf budget are
unchanged otherwise).

#### 5.1 Eligibility

A function is checked when all hold:

- it is a top-level function of the file or module under test, or an impl
  method (resolved through `trait_impls`, typed from
  `checker.impl_registry[(trait, target)][method]`);
- it is not `main`, `init`, `update` or `render`, not `async`, and not
  `@untested` (that annotation says the author cannot test it, usually because
  of host access);
- its inferred effects (`checker.global_env.lookup(name).effects` after the
  effect fixpoint) are a subset of `{mutation}`;
- it has at least one parameter (a zero-parameter pure function is a constant;
  its examples already cover it);
- every parameter type has a generator (5.2);
- every type parameter can be instantiated with `Int`: its bounds are among
  those `Int` satisfies (`Num`, `Ord`, `Hash` and the other built-in bounds
  `types.py` grants `Int`) or traits with an `impl` for `Int`;
- it matches `--filter` (same matching as examples: qualified impl name,
  `Target.method`, bare name).

A function that fails a condition other than the first four is reported as
`skipped` with a note (`parameter type (Int) -> Int has no generator`,
`type parameter T: Show cannot be instantiated with Int`, ...). The first four
are silently out of scope.

Functions with no `requires` or `ensures` are eligible: the property is then
"does not panic and stays within limits". That is intended pressure, and the
reason the feature is opt-in: `div(a, b)` without `requires b != 0` or a
`panics` example fails at `b = 0`.

#### 5.2 Generators

Types come from the checker that `geno test` already runs: `FuncType`
parameter types with aliases resolved, `TypeVar` for generics, and
`checker.type_defs[name]` (`TypeDefInfo`) for ADTs, including the built-in
`Option`, `Result` and `JsonValue`. The test runner keeps that checker instead
of discarding it (in project mode, the per-module checkers that
`check_project_graph` creates, kept through a new
`retain_module_checkers=True` option).

A generator produces a **sample**: a small immutable tree that renders to Geno
source. The runner realizes a sample by parsing its source as an expression and
evaluating it in the function's module environment, exactly as the example
input would be. The text a report prints is therefore the text that ran; each
call gets fresh values, so a `{mutation}` function cannot alias the arguments
of a later trial; and constructors resolve in module scope as they do in an
example. If a sample cannot be realized in the module (a constructor that is
only reachable qualified, say), the function is skipped with a note. The
property interpreter loads the module before any trial; if loading fails (rare,
since module constants are literals), every function of the module is skipped
with the reason.

Trial `i` of `T` has size `s = round(64 * i / max(1, T - 1))`. Discarded
attempts regenerate at the same size.

| Type | Generation | Rendering |
| --- | --- | --- |
| `Int` | 15% from `{0, 1, -1}`, 1% each `±(2^53 - 1)`, else uniform in `[-B, B]`, `B = 2^(1 + s // 6)` (at most 2048). Always within the JS-safe range. | `-1`, `9007199254740991` |
| `Float` | Finite only. 15% from `{0.0, -0.0, 1.0, -1.0, 0.5}`, else uniform in `(-B, B)`, rounded to a random 0 to 6 decimal places so counterexamples stay readable | Positional decimal, never an exponent (`0.00001`, not `1e-05`), always with a `.` |
| `Bool` | uniform | `true` / `false` |
| `String` | ASCII. 10% from `{"", " ", "0", "a b"}`, else length `0..min(s // 4, 16)` over lowercase letters (40%), digits (20%), space (15%), tab/newline (5%), other printable ASCII including `"` and `\` (20%) | Geno string literal with escapes (`builtins._format_string_literal`) |
| `Unit` | `()` | `()` |
| Tuples | each element | `(a, b)` |
| `List[T]` | length `0..min(1 + s // 8, 8)`, elements at size `s // 2` | `[a, b]` |
| `Option[T]` | `None` 20% | `Some(x)`, `None` |
| `Result[T, E]` | `Ok` 60% | `Ok(x)`, `Err(e)` |
| `Map[K, V]`, `Set[T]` | as `List`, keys deduplicated | `{k: v}`, `{}`, `set_from_list([..])` |
| `Vec[T]`, `Array[T]` | as `List` | `vec_from_list([..])`, `array_from_list([..])` |
| User ADTs, generic or recursive | uniform over variants while depth `< 1 + s // 16`; at the depth bound, only variants of minimal construction depth (a fixpoint over the type definitions) | `Leaf`, `Node(Leaf, 1, Leaf)` (positional) |
| `TypeVar` with satisfiable bounds | as `Int` | |

At the depth bound every structural choice is the smallest one (added while
implementing package 3): an `Option` is `None`, a collection is empty, a
`Result` takes the side of smaller construction depth, and an ADT takes a
minimal variant. Otherwise `type A = A1(next: Option[A])` would keep choosing
`Some` with probability 0.8 at and past the bound, and termination would only
be almost sure. Each structural choice table is a list in definition order.

No generator (the function is skipped): function types, `Async[T]`,
`MutableMap` (there is no one-expression literal to print), `Any`, `Never`,
module types, a type parameter whose bounds `Int` misses, and an ADT with no
finitely constructible variant.

Each function gets its own random stream, `random.Random(sha256(f"{seed}:{module}:{qualified_name}")[:8])`,
so adding, removing or filtering other functions never changes a function's
inputs, and `--seed S --filter f` reproduces exactly what `--seed S` ran for
`f`. `module` is the **logical** module name: the project module name in
project mode, and the file stem for a single file or a file in a manifest-less
directory. It is never derived from a path, so the same seed gives the same
inputs from any working directory, path spelling or CI checkout location (two
same-stem files in different directories merely share a stream key, which is
harmless). `--trials` changes the size schedule, so a reproduce line includes
it when it is not the default; so do `--timeout` and `--max-steps`, from which
the per-call limits derive (5.4). The RNG is host-side and never exposed to
Geno code.

Everything that feeds the stream and the rendering is order-deterministic:
`Map` keys and `Set` elements are deduplicated in generation order
(`dict.fromkeys`), never through a Python `set`, so rendering does not depend on
`PYTHONHASHSEED`; generator choice tables are lists, not sets.

#### 5.3 A trial

The property interpreter is a fresh `Interpreter` for the module, created with
the test sandbox configuration, `capabilities=set()` (fail-closed: no gated
builtin is reachable even if effect inference missed one) and
`check_examples=False`, and loaded with `execute_main=False`. It never shares
the step counter or output length of the examples' interpreter, so property
work never drains the budget examples and test blocks run on, and is not
counted in it. The whole property loop runs inside
`interp.run_with_recursion_room`, and each trial calls `_call_function` with an
empty call stack.

The outcome of a call:

| Outcome | Classification |
| --- | --- |
| Returned | pass |
| `ContractViolationError` with `clause == "requires"`, `reason == "false"` and `call_depth == 1` | **discard** |
| `ContractViolationError` with `clause == "requires"`, `reason == "propagate"` (the clause used `?`) and `call_depth == 1` | **failure** `panic`: the precondition could not be evaluated (like any panic, exempted by a `panics` example) |
| `ContractViolationError` with `clause == "ensures"` and `call_depth == 1` (either reason) | **failure** `ensures` |
| `limit` (section 4) | **failure** `limit` |
| `refused` | the function is reported `skipped` with "needs capability X at run time" (an effect-inference gap, not the author's bug) |
| other `panic` | **failure** `panic`, unless the function has a `panics` example (then pass) |

**Contract violations are classified on structured fields, not message text.**
`ContractViolationError` gains keyword fields, set at all four raise sites
on both interpreter call paths (`_check_requires`/`_check_ensures` and the fast
path that calls them):

```python
clause: Literal["requires", "ensures"] | None = None
reason: Literal["false", "propagate"] | None = None   # evaluated to false / used `?`
clause_index: int | None = None                        # index into specs.requires / specs.ensures
call_depth: int | None = None                          # now set on every variant
result_display: str | None = None                      # ensures only: the returned value, displayed
```

`result_display` was added while implementing package 3: the report shows an
ensures failure's result (`"result": "-1"` in the JSON), and without the field
that value could only be read back out of the message. A
`with_message(message, location)` method copies a violation with a new
message, so the two sites that re-raise an explained violation (`geno run`'s
and `geno test`'s rejection-example explanation) keep every field. The test
that guards the fields walks every `ContractViolationError(...)` call in
`geno/` and requires `clause`, `reason`, `clause_index` and `call_depth`.

`geno test`'s report kind (`requires`/`ensures`/`example`) is read from
`clause` too. One visible consequence: a user `panic("Precondition failed for
x")` used to be reported as a `requires` failure because its text matched the
prefix, and is now reported as an `example` failure.
The message text does not change in this proposal, and no `error_code` is
added (contract violations still report E499; assigning E407/E408 is a
runtime-diagnostics change that belongs with feature F3). The fields are the
contract F3 must keep when it rewrites contract messages: the property runner,
and `_explain_rejection_example` once it is moved onto them in P3, never parse
"Precondition failed for" again. A test fails if any `ContractViolationError`
raise site omits `clause`.

A nested call's requires or ensures failure (depth ≥ 2) is a panic of the
function under test. Any other panic raised while evaluating a top-level
`requires` expression (an index error inside the clause, say) is a failure, not
a discard: a precondition should be total. That includes the `?` variant above.

The ensures failure names its clause by `clause_index`: the report prints the
source of `specs.ensures[clause_index]` on its line, after the `ensures`
keyword, without a trailing `//` comment (the formatter's string-aware scan).
It starts from the clause's location (the keyword), not the condition's: an
expression's location can be an inner operand (`-result <= 0` is located at
`result`, `not (result < 0)` at the parenthesis). A clause continued on later
lines is quoted by its first line. Without the module's source text the clause
is `null`. Contract messages do not carry clause text yet; F3 may supersede
this helper.

A limit failure caused by the per-call deadline is reported as "Execution timed
out after 1 seconds (the property call deadline)" rather than the
interpreter's message, which names the sandbox's own `timeout`. A step-limit
failure is reported as "Step limit exceeded (200000 steps)", without the
interpreter's " at <file>:<line>:<column>" suffix, whose filename is the
module's resolved path (corrected during integration: the JSON message
otherwise depended on where the checkout is).

#### 5.4 Budgets

Budgets are counted in interpreter steps, which are deterministic; wall time is
only a safety stop. (The interpreter runs about 1.3 million steps a second on
the development machine: the 20,000,000-step `--max-steps` default is about
15 s of work, so at the 5 s `--timeout` a looping example ends by timeout, not
by steps. Property checks therefore do not reuse those two numbers as their
per-call caps.)

- **Per call:** a fixed step allowance `PROPERTY_CALL_MAX_STEPS = 200_000`
  (about 0.15 s), clipped to `--max-steps` if that is smaller, and a deadline
  of `min(--timeout, 1 s)`. Generated inputs are small (at most 8 elements,
  `|n| ≤ 2048` outside the two extremes), so a call that needs more is treated
  as unbounded work for a small input, which `requires` should exclude. The
  interpreter gains `Interpreter.step_allowance(n)`, a context manager that
  saves `steps`, sets `steps = 0` and both `max_steps` and the cached
  `_step_limit` to `n`, and restores all three on exit (adding the steps used).
  Because the counter starts at 0, the existing message reads "Step limit
  exceeded (200000 steps)", which is the per-call allowance; the report says
  "hit the step limit (200,000 steps per property call)".
- **Per function, trial phase:** `PROPERTY_TRIAL_STEP_BUDGET = 5_000_000` steps
  and at most `10 × trials` generated argument sets.
- **Per function, shrink phase:** a separate `PROPERTY_SHRINK_STEP_BUDGET =
  4_000_000` steps and at most 500 candidate calls, so a failing trial always
  leaves room to shrink. A looping counterexample costs one per-call allowance
  per looping candidate, so about 20 looping candidates fit. Trying the
  smallest `Int` candidates first (5.5) means that, when every input above a
  threshold loops, the first looping candidate accepted is already less than
  twice the threshold, even from `2^53 - 1`.
- **Safety stop:** `2 × --timeout` of wall time per function (10 s by default),
  across both phases.

Realizing a call's arguments runs under an allowance of its own and counts
against the phase budget; the call then gets the per-call allowance, or what
is left of the phase budget if that is less. Keeping the two apart is what
makes a full allowance exactly `PROPERTY_CALL_MAX_STEPS`, so the message reads
"(200000 steps)" (implementation note, package 3). All the budget constants,
and `PROPERTY_MAX_ATTEMPTS_PER_TRIAL = 10` and
`PROPERTY_SAFETY_STOP_TIMEOUTS = 2`, are in `geno/execution_limits.py`;
`PropertyConfig` takes them as overridable fields so tests can shrink them.

How a cut-off call is classified:

- If the call started with its full per-call allowance (the phase had at least
  `PROPERTY_CALL_MAX_STEPS` left) and hit it, or hit its own per-call deadline,
  recursion depth or a size limit, it is a **`limit` failure**, on any trial.
  A loop first reached on trial 37 is a failure, not a warning.
- If the call started with less than a full allowance because the phase budget
  was nearly spent, and ran out, it is not counted: in the trial phase the run
  stops with status `incomplete` ("stopped after N trials: step budget
  spent") when no failure was found; in the shrink phase shrinking stops and
  the best counterexample so far is reported.
- If the wall-clock safety stop fires, the status is `incomplete` with the note
  "stopped by the wall-clock safety stop", or, during shrinking, the best
  counterexample so far is reported with the note "shrinking stopped by the
  wall-clock safety stop". This is the only machine-speed-dependent outcome,
  and the note says so; with the step caps above it is not expected to bind.

The per-call deadline (1 s against a 0.15 s step allowance) binds only for
calls dominated by builtin work the step counter does not see; such a hit is a
`limit` failure named "timeout". Constants live in `geno/execution_limits.py`.

`--trials` accepts 1 to 10,000 and `--seed` any non-negative integer.
`--trials` or `--seed` without `--properties` is a usage error (exit 2,
"argument --trials: requires --properties"): silently ignoring them would hide
a misconfigured CI job.

#### 5.5 Shrinking

Shrinking is greedy on the sample tree: try candidates in order, accept the
first that fails **the same way** (same kind; for `ensures`, the same
`clause_index`; for `limit`, any limit; a candidate already tried, by its
rendering, is not tried again), repeat until no candidate fails or the
shrink budget (5.4) is spent. Candidates that a top-level `requires` rejects are
not failures. A looping counterexample shrinks like any other: for
`spin(n)` that loops for every positive `n`, a failure at `n = 1500` shrinks to
`1`.

| Sample | Candidates, in order |
| --- | --- |
| `Int n` | `0`, then `n // 2^k` for k = K, ..., 2 (the smallest first), then `n - n // 2^k` for k = 1, 2, ... (from `n / 2` up), then `n ∓ 1`: increasing magnitude |
| `Float x` | `0.0`, `trunc(x)`, `x / 2`, `x` rounded to fewer decimals |
| `String` | `""`, drop the first or last half, drop one character, replace a character with `a` |
| `List`, `Vec`, `Array`, `Set`, `Map` | empty, drop the first or last half, drop one element, shrink one element |
| Tuple, constructor fields | shrink one field |
| ADT value | a minimal-depth variant, a recursive field of the same type (`Node(l, v, r)` to `l` or `r`), then its fields |
| `Option` | `None`, then the inner value |
| `Result` | the inner value |

The `Int` row was corrected while implementing package 5. As first designed,
the candidates after `0` started at `n / 2`, so each accepted candidate at most
halved the input. That is enough from the sizes the trials grow through
(`|n| ≤ 2048`), but a function that is slow only for large inputs is first
caught at the generated extreme `2^53 - 1`, and every failing candidate costs a
full call allowance. Halving one step at a time then spent the 4,000,000-step
shrink budget about 20 halvings in. The language tour's `factorial` (only
`requires n >= 0`) was reported as `134217728` after 6.7 s; a linear `isqrt`
as `17179869184`. Trying the small fractions first costs little, because
they pass cheaply. When every input from some threshold up fails, the first
candidate that fails is less than twice the threshold. The same two functions
now shrink to `3249` (the first `n` whose factorial exceeds the integer-size
limit, in 3.0 s) and `243793920` (just above the step-limit threshold). The
other shrink results in the test suite are unchanged.

#### 5.6 Results

Each function gets one result with a status: `passed`, `failed`, `gave_up`
(fewer than `max(1, trials // 10)` trials survived `requires` and no failure
was found), `incomplete` (5.4) or `skipped`. `gave_up`, `incomplete` and
`skipped` are warnings. The exit status is 1 when any property failed, and
otherwise unchanged. Property results are not added to the example counts
(`total`, `passed`, `failed`) that plain `geno test` reports, but `success`
is false when a property failed. A `passed` function that ran fewer than
`--trials` trials (its `requires` discarded enough inputs to reach the attempt
cap) carries a note saying so.

The library API (package 3) is `geno.property_check`:
`check_module_properties(program, checker, *, module, path, config,
sandbox_config, modules, source)` returns one `PropertyResult` per function in
source order, and `PropertyReport(seed, trials, results).to_dict()` is the JSON
object below. Each result's `failure` also carries a `summary`, the one-line
text the report prints ("ensures result >= 0 failed (result was -1)", "hit the
step limit (200,000 steps per property call)"), which is not in the JSON.

Text output, always printed when `--properties` is given, after the example
report: a header with the seed and trial count, one entry per `failed`
(`FAIL`), `gave_up` (`GAVE UP`) and `incomplete` (`INCOMPLETE`) function (with
`-v`, also `passed` (`PASS`, with the trials run and discarded) and `skipped`
(`SKIP`) functions with their notes; a block with nothing to list says "no
failures or warnings"), and, after the example summary line, a
`Properties: 0 passed, 1 failed` line that adds the gave-up, incomplete and
skipped counts when they are not zero. A failure shows the clause and
result (`ensures result >= 0 failed (result was -1)`) or the panic message or
the limit, the counterexample and what it was shrunk from, and a reproduce
command (shell-quoted, with the file's path as the report shows it). Arguments
render as an example's inputs: `()` for none, `a` for one (a tuple parameter
keeps its parentheses), `a, b` for several. A file's `elapsed_ms` and timing
still cover its examples and test blocks only.

`--json` gains a top-level `properties` key only when `--properties` is given
(so plain `geno test --json` stays byte-identical). It is present in the error
envelope too. Every field is always present, `null` when it does not apply:

```json
"properties": {
  "seed": 0,
  "trials": 100,
  "passed": 0, "failed": 1, "gave_up": 0, "incomplete": 0, "skipped": 0,
  "functions": [
    {
      "path": "abs.geno",
      "function": "abs_val",
      "line": 1,
      "status": "failed",
      "trials_run": 3,
      "discarded": 0,
      "failure": {
        "kind": "ensures",
        "message": "Postcondition failed for abs_val: ensures clause evaluated to false (result was -1)",
        "clause": "result >= 0",
        "result": "-1",
        "limit": null
      },
      "counterexample": {"arguments": "-1", "original": "-1", "shrink_steps": 0},
      "note": null
    }
  ]
}
```

`failure.kind` is `ensures`, `panic` or `limit`. `result` is the interpreter's
display of the returned value. The reproduce command is printed in the text
report only; JSON consumers have the seed, trials, path and name.

Single files, directories without a manifest and projects with `geno.toml` are
all supported. In project mode each module's functions are checked with that
module's checker and environment. Only the project's own modules are checked
(added while implementing package 4): `geno test` on a project also runs the
examples of the standard-library modules and dependency packages it imports,
but their properties are not the project's to fix, and checking them would let
a library function that panics on purpose fail every importing project. A file
with no examples and no test blocks (the early return in `_test_file`, which an
app-mode file reaches) still runs its property checks. `--filter` selects
properties exactly as it selects examples and test blocks (the runner's filter
replaces `PropertyConfig.filter_pattern`). `geno test --watch --properties`
passes the flags through, and `geno.cli.watch.watch_run(..., test_mode=True,
properties=config)` forwards a config the same way; the `geno watch` command
itself gains no flags.

## Backend And Target Parity

Examples are evaluated by the interpreter on every `geno run` lane, in
`geno test`, in `geno.api.run` with `check_examples`, and in the hosted server;
the compiled Python and JavaScript backends never evaluate them. Compiled Python
renders a panics example in the function's docstring; compiled JavaScript
ignores examples, as it does today. So the observable parity requirement is
that a program with panics examples compiles and runs `main` identically on
the interpreter, compiled Python and compiled JavaScript, which
`conformance/v0.7` checks on every lane and `test_backend_parity.py` checks
with a dedicated program.

`run_harness_from_compiled` classifies compiled-Python exceptions with the same
helper, so a panics example passes or fails identically in the interpreter and
compiled harnesses for the messages both backends share. Messages are not
identical across backends (`Division by zero: cannot compute 1 / 0` against
`Division by zero`), which is why matching is a case-insensitive substring and
the docs recommend the text the backends share or the author's own `panic`
message. A `geno test --backend` option (feature F9) would run examples on
compiled backends with this classifier.

Property checks run in the interpreter only. The generators stay within
`±(2^53 - 1)` and finite floats, so a counterexample is a valid input on every
backend.

## Compatibility And Migration

Source compatibility: the new syntax is additive, with one breaking check-time
change (below). `panics` remains an identifier; the parser takes the new form
only where 0.6 reported "Expected '->'". No `.geno` file or benchmark problem in
the repository uses `panics` as a name.

Diagnostics:

| Program | 0.6 | 0.7 draft |
| --- | --- | --- |
| `example 1, 0 -> panics` | E301 with a fix to `panic` | E330 with a fix to `panics` |
| `example 1, 0 -> panic` | E300 | E330 with a fix |
| `example 1, 0 -> panic("m")` | accepted; the example always fails at `geno test`/`geno run` | E330 with a fix |
| `example 1, 0 panics "m"` | E201 | accepted |

**Breaking change (0.7).** The third row rejects programs that 0.6 accepts:
they pass `geno check` and compile and run with `geno compile`, `geno build`
and `geno run --no-check-examples`, none of which evaluate examples. Only their
examples are wrong (the example could never pass), but the program no longer
checks. It is recorded under "Recorded Breaking Changes" in
`docs/operations/compatibility-policy.md` as "0.7 (draft): an example's
expected value cannot be a `panic(...)` call", meeting the policy's five
conditions: (1) this proposal states the motivation and alternatives (a W-code
warning was considered and rejected in section 3); (2) the impact is stated in
`docs/spec/v0.7.md` section 5.3 and the "Changes from 0.6" list; (3)
`CHANGELOG.md` `[Unreleased]` carries the migration example below under a
"Breaking" heading; (4) no frozen case uses the form, and the new expectation
is the v0.7 checker case `diagnostic_panic_example_arrow_form`; (5) the release
owner records it in release evidence when 0.7 is released. `geno check --fix`
performs the migration:

```geno
// before
example 1, 0 -> panic("division")
// after
example 1, 0 panics "division"
```

`-> panic(e)` with a non-literal argument has no automatic fix; the migration
is to write the message text literally or drop it (`example 1, 0 panics`).

No published code changes meaning. E330 is new and is added to `ErrorCode`,
`spec.json` `diagnostics.codes`, and the diagnostic taxonomy tests.

`geno test`: unchanged unless `--properties` is given. `geno test --json`
without `--properties` is unchanged.

API: `ExampleClause.output_expr` becomes `Expression | None`. Code outside the
repository that reads it must check `expects_panic`. `SuiteResult`,
`FileTestResult` and `run_test_suite`/`run_project_test_suite` gain optional
property fields and parameters with defaults that preserve current behavior.

Specification and corpora: `docs/spec/v0.7.md` is created as a Draft fork of
v0.6 with the header "Draft contract for the unreleased 0.7 development line;
v0.6 remains normative for the 0.6 series" and a "Changes from 0.6 so far"
list. v0.4, v0.5 and v0.6 specs and corpora are not edited.
`conformance/v0.7/manifest.toml` (`language_version = "0.7"`,
`status = "development"`) holds only the new cases. When 0.7 is promoted, the
promotion proposal carries the v0.6 cases forward as 0.6 did with 0.5, drops
`status`, and moves `language_series`.

Development-line mechanics (the minimal consistent approach):

- **Conformance runner.** `retained_manifest_paths()` is unchanged (current and
  previous minor), so every test that treats its last entry as the 0.6 corpus
  keeps working. A new `development_manifest_paths()` returns the corpus newer
  than `language_series`, if any. `load_manifest` accepts an optional
  `status = "development"`; it is required on a corpus newer than the
  language series, refused on any other, and at most one such corpus may exist.
  `--all-retained` runs retained corpora then the development corpus, so the
  Makefile, `local_ci.py`, the scheduled workflow and the release runbook pick
  it up without edits, and the help text says so.
- **`spec.json`** keeps describing the normative 0.6 series (`validate_spec.py`
  pins `language_series`, `version` and `human_spec` to the package version). A
  new top-level `development_line` object records the draft:
  `{"series": "0.7", "status": "draft", "human_spec": "docs/spec/v0.7.md",
  "conformance": "conformance/v0.7/manifest.toml", "additions": [...]}`, one
  sentence per addition. `validate_spec.py` checks that the series is newer
  than `language_series`, the files exist, and the conformance manifest is
  marked `development`. Draft syntax is not written into the 0.6 notes,
  keywords or fix prose. E330 must be in `diagnostics.codes` (the validator
  requires the exact `ErrorCode` set; adding a code within a series is allowed
  by its stability rule), and `development_line.additions` says that 0.7
  introduced it.
- **`docs/INDEX.md`** links the v0.7 draft beside v0.6. The compatibility
  policy says that a development corpus, when present, runs with the retained
  ones and is not frozen.

Selfhost: `selfhost/Parser.geno` keeps requiring `->`. `selfhost/SCOPE.md`
lists panics examples under "Syntax gaps", the precedent the bare-arrow example
set, and no selfhost file uses them.

## Security And Resource Limits

- **Limits are never a pass.** A panics example cannot be satisfied by
  exhausting a step, time, recursion, memory, collection, integer or output
  limit, so an example cannot turn a denial-of-service input into a green test.
  Limits are identified by exception type (`RuntimeLimitError` and the
  sandbox's `ResourceLimitExceeded` family), not by message text, and a scan
  test keeps every sandbox-configured limit site on that type. In `geno run`
  the original limit error propagates with its published code; the security
  corpus gains `panics_example_step_limit.geno` (`# EXPECT: E503`, an infinite
  loop behind `example 0 panics`) and `panics_example_integer_limit.geno`
  (`# EXPECT: E499`, `2 ** 100000` behind a panics example).
- **Capabilities fail closed.** A capability denial does not satisfy a panics
  example (`panics_example_capability_denied.geno`, `# EXPECT: E412`). Property
  trials run in an interpreter with no capabilities at all, in addition to the
  effect-based eligibility rule, so generated inputs cannot reach the
  filesystem, network, environment, processes, clock or randomness.
- **Bounded work.** Property checks are opt-in. Per call they are capped at
  200,000 steps and `min(--timeout, 1 s)`; per function at 5,000,000 trial
  steps, 4,000,000 shrink steps, `10 × trials` generated inputs, 500 shrink
  calls and a `2 × --timeout` wall-clock safety stop; `--trials` is capped at
  10,000. Generated collections are at
  most 8 elements and ADTs at most 5 levels deep, far below the collection
  limit.
- **No new trust boundary.** Generation, seeding and shrinking are host-side;
  Geno code cannot read the RNG or the seed. Property work runs in its own
  interpreter, so it cannot exhaust the budget that examples and tests depend
  on. The sandbox, capability checks and limits are unchanged.

## Testing And Validation

New and updated tests, by layer:

- **Parser** (`test_parser.py`, `test_panic_examples.py`): every form
  (`1, 0 panics`, `panics "m"`, `() panics`, triple-quoted message, impl
  methods, inputs ending in a call, lambda, pipeline, negative literal,
  constructor or tuple); `panics` as a variable, parameter and function name;
  `example panics -> 1` with a `panics` variable; a string on the next line not
  taken as the message; f-string or extra token after `panics` (E201);
  `example panics` (E201 with the `()` fix); `panics ""` (E202 with a fix).
- **AST** (`test_ast_nodes.py`): optional `output_expr`, compared
  `panic_message`, non-compared locations.
- **Typechecker** (`test_panic_examples.py`, `test_diagnostic_fixes.py`,
  `test_diagnostic_taxonomy.py`, `test_release_diagnostic_codes.py`): inputs
  checked and arity enforced; only-panics functions satisfy the example rule;
  generic panics examples with return-only type parameters; E330 for every row
  of the table in section 3, each fix applied by `geno.fix()` to a program that
  then checks clean; the old E301 fix to `panic` never offered; user-defined
  `panic` and a `panics` value in scope not diagnosed.
- **Evaluation** (`test_panic_examples.py`, `test_test_runner.py`,
  `test_harness.py`, `test_api.py`, `test_server.py`): pass on `panic`, a
  runtime error, a top-level and a nested `requires` failure, an `ensures`
  failure; case-insensitive message match; failure on no panic, wrong message,
  step limit, timeout, recursion depth, collection limit, integer limit and
  capability denial, each naming its cause; inputs that panic are not a pass;
  `**`, `<<` and `>>` integer-bit limits and the output limit each failing a
  panics example; fixed builtin caps (`parse_int` input too long) satisfying
  one; `panic("List size exceeds limit")` still a panic;
  `geno run` exit codes (E409; limits keep E501-E503/E499; E412) and the
  hosted `check_examples` path; impl-method panics examples in `geno test`;
  compiled-harness parity including the compiled limit patterns; the AST scan
  requiring `RuntimeLimitError` at every sandbox-configured limit raise site;
  the refusal switch exercised in both positions.
- **Renderers** (`test_compiler.py`, `test_doc_generator.py`,
  `test_formatter.py`, `test_formatter_corpus.py`, `test_constraints.py`):
  docstring with a `"""` message; HTML; `geno fmt` round-trip and idempotence
  on messy spacing; constrained-decoding prefixes.
- **LSP** (`test_lsp*.py`): no diagnostics on panics examples; E330 quick fix.
- **Backend parity** (`test_backend_parity.py`): a program with panics
  examples gives the same `main` output on all three backends.
- **Conformance v0.7:** `panic-example-passes` and `panic-example-zero-arg` on
  `interpreter`, `cli-process`, `cli-direct`, `cli-json`, `python`, `js` and
  `js-esm`; `panic-example-no-panic-fails` on `cli-process` and `cli-direct`
  with `expected_exit_class = "nonzero"` and `expected_stderr_contains =
  "expected a panic"`; `panic-example-arrow-form` on `checker` with E330.
  Runner tests for `development_manifest_paths()` with temporary corpora
  (absent, present, unmarked, marked but not newer, two newer).
- **Security corpus** (`test_security_corpus.py`): the three cases above (step
  limit E503, integer limit E499, capability denial E412).
- **Property runner** (`test_property_check.py`, `test_property_cli.py`):
  finds the `abs_val` bug and shrinks it to `-1`; discards `requires`-failing
  inputs and reports `discarded`; a `requires` that uses `?` and propagates is
  a failure, not a discard; classification uses `clause`/`reason`/`call_depth`
  (a test rewrites the contract message text and the results do not change);
  `gave_up` warning when nearly every input is discarded; same seed gives
  identical results and different seeds different streams; the same seed from
  two working directories and two path spellings of the same file gives
  identical output; `--filter` does not change a function's stream; `Map`/`Set`
  rendering stable under two `PYTHONHASHSEED` values; JSON shape and key
  stability; generation and shrinking for every type in 5.2, with the ADT and
  recursion cases built on a non-generic recursive ADT
  (`type IntTree = Leaf | Node(left: IntTree, value: Int, right: IntTree)`), a
  non-recursive generic ADT (`Box[T]`), a recursive generic ADT (`Tree[T]`,
  pasteable since the prerequisite checker fix landed in package 3) and mutually
  recursive ADTs; every rendering parses, type-checks as an example input and
  realizes to an equal value; the panics-example exemption, with `ensures`
  still failing; a function that loops only for positive inputs, first hit on a
  trial after the first, is `failed` with a `limit` (not `incomplete`) and its
  counterexample shrinks to `1`; the per-call allowance, not an absolute step
  count, in the failure text; `incomplete` only when a phase budget or the
  safety stop binds;
  `{mutation}` functions not aliasing arguments; capability fail-closed;
  eligibility exclusions and skip notes; impl methods; project mode; plain
  `geno test` output and JSON unchanged; exit status; `--trials`/`--seed`
  usage errors; the property module not imported without `--properties`.

Release checks: `python3 scripts/run_conformance.py --all-retained --target all
--require-node`, `python3 scripts/validate_spec.py`,
`python3 scripts/check_ci_dx_ratchets.py`, `python3 scripts/check_perf_ratchets.py`,
`python3 -m mypy geno/ --ignore-missing-imports --no-error-summary`, ruff, the
selfhost tests, and `python3 scripts/local_ci.py full`.

## Rollout And Observability

The 0.7 development line opens with this proposal; nothing is released and the
package version stays `0.6.0`. CHANGELOG `[Unreleased]` records both
additions. `docs/MATURITY.md` lists `geno test --properties` as Experimental
while the test runner stays Stable. User documentation:

- `docs/spec/v0.7.md` section 5.3 (panic examples), a new 5.4 (property
  checks: what `requires`, `ensures` and `panics` examples mean to them), the
  6.9 carve-out that a resource limit ends a program like a panic but never
  satisfies a panics example, the 10.4 fix row and the section 14 grammar;
- `docs/llm-prompting.md`: a new numbered section 20 teaching `panics`
  examples and that `requires` states the domain property checks rely on. The
  "System Prompt Essentials" block is left unchanged, so it still equals
  `experiment/prompts.py` `GENO_RULES` and the benchmark prompts do not move
  (changing them is an experiment decision of its own);
- `docs/guide/language-tour.md` (Contracts), `docs/design/tiered-testing.md`,
  `docs/reference/common-pitfalls.md`, the `geno test --help` text and
  `README.md`'s testing paragraph.

Rollback is a revert: the AST change is internal, the corpus is a development
corpus, and no published contract depends on either.

## Alternatives

- **`-> panics` as a magic expected value.** It reads like the existing form,
  but makes `panics` a reserved value in expression position, breaks programs
  that name something `panics`, and cannot carry a message without more
  special syntax. The fix steers to the arrow-less form instead.
- **Make `panics` a keyword.** Unambiguous, but takes a common English word
  away from programs and requires keyword changes in the lexer, `spec.json`,
  selfhost, the LSP and the VS Code grammar. The contextual position is never
  ambiguous.
- **Regular-expression messages (pytest's `match=`).** More expressive, but a
  second regex dialect in examples, with backend differences. A substring
  covers the use.
- **Case-sensitive matching (Rust's `should_panic(expected = ...)`).** Precise,
  but `"division"` would not match the interpreter's `Division by zero`, and
  the interpreter and compiled messages differ in more than case already. Kept
  as an open question.
- **Let a resource limit satisfy `panics`.** Simpler (every failure is a
  panic), but it lets an example pass because of the host's limits, and lets
  `example 0 panics` hide an infinite loop.
- **Properties on by default.** Finds more bugs, but changes the result of
  every existing `geno test` run and flags every function that panics on
  purpose without a `panics` example. Opt-in first; a default can follow
  evidence.
- **A random default seed.** Explores more inputs over time, but makes CI
  non-deterministic. A fixed default with `--seed` keeps runs reproducible.
- **Building runtime values directly in the generators.** Faster, but the
  printed counterexample could drift from what ran. Realizing the rendered
  source makes them identical by construction; the cost is negligible next to
  the call.
- **A `property` declaration (`forall xs: List[Int] -> ...`).** More general,
  but a new language form; `requires`/`ensures` already state the properties
  most functions have.
- **Doing nothing.** Contracts would remain documentation checked on a handful
  of hand-picked inputs, and the failure path of a function would remain
  unstatable.

## Unresolved Questions

- **Capability denial as a panic.** The brief this proposal implements says a
  panics example passes on "any runtime error", naming resource limits as the
  only exception. This design also excludes E412, E413 and E500 because
  whether they occur depends on the host's grants, not on the input: in
  `geno test` every `env` read is denied, so an `env`-using function's panics
  example would pass vacuously. This departs from a fixed decision and needs
  the project lead's explicit confirmation before P1 lands. Until then the
  behaviour sits behind the single `_refusal_satisfies_panics()` predicate in
  `geno/panic_outcome.py`, the v0.7 spec names the three codes as an explicit
  exception, and the security corpus case is the one expectation that would
  flip with it.
- **Case-insensitive matching.** Chosen so the natural `"division"` matches;
  Rust's precedent is case-sensitive. Runtime messages are not a stable
  contract (the compatibility policy excludes exact diagnostic prose, and F3
  plans to change contract messages), so docs recommend matching the author's
  own `panic` text.
- **Generic ADT counterexamples (resolved in package 3).** A pre-existing
  checker bug rejected a nullary constructor of a generic ADT used as a
  constructor argument, in every position, not only in generic functions: with
  `type Tree[T] = Leaf | Node(left: Tree[T], value: T, right: Tree[T])`,
  `Node(Leaf, 1, Leaf)` failed ("Constructor argument 1 type mismatch: expected
  Tree[T], got Tree[__ctor_arg0___fresh_T_2]") as an example input of a
  non-generic `size(t: Tree[Int])`, in `let t: Tree[Int] = ...` and as a call
  argument. Only a bare `Leaf` checked. Left alone, every counterexample deeper
  than a bare nullary constructor of any recursive generic ADT would have been
  unpasteable. The cause turned out to be local to constructor-argument
  inference, so the fix landed as planned, without a waiver. `Tree` stores `T`
  in a field, so it is invariant in `T`, and three things went wrong in
  `_check_constructor_call`:
  1. The argument's type variables were renamed to avoid colliding with the
     constructor's own parameters, which turned `Leaf`'s inference placeholder
     `__fresh_T_2` into the rigid-looking `__ctor_arg0___fresh_T_2`.
  2. The reverse half of the invariant check then compared that variable with
     itself. Binding a variable to itself fails the occurs check.
  3. The constructed type reported the placeholder a parameter was bound to,
     not what a later argument resolved it to.

  The fix:
  - Freshening now renames only rigid type variables; placeholders are unique
    already and keep their names.
  - `_types_compatible_with_subs` accepts an unbound placeholder against
    itself. This rule is restricted to `__fresh_` placeholders.
  - `_bind_or_fresh` applies the substitutions, so `Node(Leaf, 1, Leaf)` is a
    `Tree[Int]`.

  Regression tests in `test_typechecker.py` (`TestNestedNullaryGenericConstructors`)
  cover the example-input, annotated-`let`, call-argument, nested and
  generic-function-body positions. They also check that
  `Node(Leaf, "x", Node(Leaf, 1, Leaf))` and a `Tree[Int]` written for a
  `Tree[Bool]` are still rejected, and that no placeholder name leaks into a
  message. The `Tree[T]` round-trip property test is not an expected failure.
- **`geno run` and impl-method examples.** Left unchecked by `geno run`, as
  today (section 4).
- **Promotion of the 0.7 corpus.** Whether v0.7 carries every v0.6 case forward
  at promotion, as v0.6 did with v0.5, is for the promotion proposal.
- **Default trial count and sizes.** 100 trials and the size table are starting
  values chosen for speed (the prototype ran 100 `abs_val` trials in 1.5 ms);
  they should be revisited with data from the example apps and benchmark
  corpus.

## Implementation Plan

Five sequential packages; each leaves the tree green.

1. **Open the 0.7 line and land panics examples.** AST, parser forms and the
   same-line rules (E201 for a stray token after `panics`), typechecker
   handling of panics examples, `geno/panic_outcome.py` with the refusal
   switch, `RuntimeLimitError` at every sandbox-configured limit site in the
   interpreter and builtins (with the AST scan test), the four evaluators and
   the renderers; `docs/spec/v0.7.md` as a Draft fork with section 5.3,
   6.9 and 14 updated and the "Changes from 0.6" list linking this proposal;
   `spec.json` `development_line` and its validator; the conformance runner's
   development corpus and `conformance/v0.7` with the run cases;
   `docs/INDEX.md`, the compatibility policy, `selfhost/SCOPE.md`, the three
   security corpus cases, and a CHANGELOG entry. Before P1 lands, the project
   lead confirms or reverses the refusal carve-out.
2. **Diagnostics and fixes, and panics documentation.** E330 and its fixes,
   the source positions they need, the E201/E202 targeted parse errors,
   `spec.json` codes, spec 10.4, the checker conformance case, the 0.7
   breaking-change record in the compatibility policy and CHANGELOG, LSP
   quick-fix and constrained-decoding tests, `docs/llm-prompting.md` section 20 and the
   guide, pitfalls and tour updates for panics examples.
3. **Property-check engine.** First the prerequisite checker fix for nested
   nullary constructors of generic ADTs (Unresolved Questions). Then
   `geno/property_check.py` (eligibility, generators, samples, realization,
   trials, classification, budgets, shrinking, results), the interpreter's
   `step_allowance`, the structured `clause`/`reason`/`clause_index`/
   `call_depth` fields on every contract failure (and
   `_explain_rejection_example` moved onto them), retained module checkers,
   and the per-call and per-phase constants in `geno/execution_limits.py`,
   tested as a library (`geno/tests/test_property_check.py`). Realizing a
   sample parses it with a new `geno.parser.parse_expression(source,
   filename)`, which parses exactly one expression.
4. **`geno test --properties`.** CLI flags and usage errors, test-runner
   integration for files and projects (including the early-return path and
   `--filter`), text and JSON reporting, exit status, watch pass-through, and
   the lazy-import guarantee.
5. **Property-check documentation and release validation.** Spec v0.7 section
   5.4, `spec.json` `development_line.additions`, the testing docs,
   `docs/MATURITY.md`, CLI help, CHANGELOG, and the full validation matrix.
