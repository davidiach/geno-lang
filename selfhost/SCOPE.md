# Selfhost Scope Statement

## What selfhost IS today

The `selfhost/` directory implements a **Geno frontend and tree-walking interpreter**
written entirely in Geno. It can:

- Lex Geno source code into tokens (Tokens.geno, Lexer.geno)
- Parse tokens into an AST (Ast.geno, Parser.geno)
- Type-check the AST (Types.geno, TypeChecker.geno)
- Interpret the AST via tree-walking (Interpreter.geno)
- Expose a host-launched CLI entry point for `demo`, `check`, `run`, and `test`
  flows via `Main.geno`

The selfhost covers the core Geno language subset used by its own modules. It does
not yet support every Geno feature (e.g., async/await, traits, app mode).

## Parity budgets

`scripts/check_selfhost_parity.py` is the executable source of truth for
selfhost language and builtin parity gaps. New canonical keywords or builtins
must either be implemented in `selfhost/` or added to a named gap group there;
otherwise the parity check fails.

Keyword gaps are grouped by implementation area so features like
test/export syntax (`test`/`assert`/`export`) remain explicit staged work
rather than untracked drift. `try`/`catch`/`throw` are listed only because
they stay reserved after Geno 0.6 removed them (proposal 0009).

Builtin gaps are ordered with pure host-independent groups first
(`pure_collection_next`, `pure_string_next`, `pure_math_next`,
`pure_option_result_next`, and `pure_path_data_next`) before host-dependent and
browser-target groups. This keeps the next selfhost parity burn-down biased
toward deterministic frontend/runtime work.

## Syntax gaps within recognized keywords

The canonical Python frontend also supports these forms that the experimental
selfhost parser does not yet implement:

- Bare zero-argument examples (`example -> value`); use `example () -> value`.
- Tuple patterns in match arms, including nested tuple patterns. Tuple `let`
  destructuring is also outside the current selfhost parser subset.
- Generic functions (`func first_or[T](xs: List[T], fallback: T) -> T`), from
  the 0.6 draft specification.
- Panic examples (`example 1, 0 panics "division"`, `example () panics`), from
  the 0.7 draft specification (proposal 0017). The selfhost parser still
  requires `->` in every example, and no selfhost file uses the form.
- The forms of accepted proposal 0023: f-string format specs (`f"{x:.2f}"`),
  slices (`xs[1:3]`), named-field patterns (`Cfg(port: p, ..)`) and `if let`.
  No selfhost file uses them.
- Self tail calls in constant stack (accepted proposal 0022). The self-hosted
  interpreter nests every call, so a deep tail recursion it interprets is
  bounded by its host's stack.
- The forms of accepted proposal 0026: compound assignment (`x += 1`), tuple
  patterns in `for` loops and comprehensions, string literals inside f-string
  interpolations, and a trait or impl method receiver written `self` alone.

These are parser/interpreter gaps, so the keyword and builtin parity checks do
not detect them. Canonical frontend and backend tests cover the new forms.

## Example verification gap

The selfhost `test` flow checks an example with `values_equal`, the same
function that implements `==`, so a `Float` result must match its expectation
exactly. Canonical `geno test` accepts a relative tolerance of `1e-9` (#111),
so a rounded expectation such as `22.85714286` for `160.0 / 7.0` passes there
and fails here. Closing the gap needs a separate approximate comparator, since
`==` itself must stay exact.

## Runtime failure report gap

The 0.7 draft specification (section 6.9.1, proposal 0018) makes every
runtime failure report its Geno location and call stack, and makes contract
failures name the function, the call's argument values and the clause text.
The selfhost interpreter keeps its own `rt_error` output: a message without
a location, a Geno stack or contract arguments; a failed `requires` reports
only `Precondition failed`. Programs run under the selfhost compute the same
results and fail in the same places; only the report is shorter. The
canonical interpreter, compiled Python and compiled JavaScript cover the new
report, and the selfhost tests are unchanged.

## Effect inference gap

The selfhost frontend has no effects. Its parser does not accept an effect
annotation (`-> Int with io` on a function, or `with mutation` in a function
type) and builds every function type with an empty effect list; its type
checker registers every builtin with no effects, `format_gtype` leaves
effects out, and nothing infers or compares them. No selfhost source uses an
effect annotation. That gap predates the 0.7 draft. The 0.7 draft
specification (section 3.5, proposal 0019) changes which writes contribute
`mutation`: state that only the writing function or lambda can observe no
longer does. The selfhost has nothing to change for it: it never rejects a
program for its effects, and it cannot parse one that writes an effect
annotation. The canonical type checker covers the rule, and the selfhost
tests are unchanged.

## What selfhost is NOT (yet)

- **Not a compiler**: There is no code-generation backend. The selfhost does not
  produce Python or JavaScript output.
- **Not a standalone distribution**: The current CLI entrypoint still relies on
  the Python-based Geno implementation to launch `selfhost/Main.geno`. It is not
  a packaged native tool or a self-bootstrapped executable.
- **No dedicated selfhost-native bootstrap matrix**: CI does run host-side
  parity/drift checks plus smoke coverage for `selfhost/Main.geno`, but there is
  no separate selfhost-native bootstrap/e2e job.
- **Not a bootstrap path**: You cannot build the selfhost using itself. It requires
  the Python-based Geno implementation to run.

## Target scope (roadmap)

The intended end state is a self-hosted frontend + interpreter that can:

1. Parse its own source files, including `@untested` annotations (#403, done)
2. Produce structured diagnostics with file/line/column spans (#405)
3. Expand parity/drift coverage against the Python implementation (#406)
4. Gain a dedicated selfhost CI/parity job beyond the existing host-side smoke
   coverage (#407)

Code generation (compiling Geno to Python/JS from the selfhost) is a stretch goal
and not currently planned.
