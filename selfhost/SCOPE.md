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
exception control (`try`/`catch`/`throw`) and test/export syntax
(`test`/`assert`/`export`) remain explicit staged work rather than untracked
drift.

Builtin gaps are ordered with pure host-independent groups first
(`pure_collection_next`, `pure_string_next`, `pure_math_next`,
`pure_option_result_next`, and `pure_path_data_next`) before host-dependent and
browser-target groups. This keeps the next selfhost parity burn-down biased
toward deterministic frontend/runtime work.

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
