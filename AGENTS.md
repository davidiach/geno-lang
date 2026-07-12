<!-- This file contains shared AI agent guidance. Human contributors: start with CONTRIBUTING.md -->

# AGENTS.md - Geno Project Conventions

## Project Overview

Geno is a statically typed, functional-first programming language that compiles
to Python and JavaScript. The implementation is in Python.

## Repository Layout

```
geno/               # Language implementation (Python)
  tests/            # Test suite (pytest)
  std/              # Standard library (.geno files)
benchmark/          # Benchmark problem corpus
experiment/         # Experiment runner and configuration
analysis/           # Benchmark/result analysis tools
docs/               # Language documentation and specs
examples/           # Example Geno programs
selfhost/           # Self-hosted frontend + interpreter (in Geno)
vscode-geno/        # VS Code extension
scripts/            # Utility and release-gate scripts
spec.json           # Machine-readable language specification
```

## Development Setup

Use `python3` on POSIX shells and `python` on Windows when needed. Start from
the repository root and install the development extras before running checks:

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

Install optional extras only when the task needs them:

```bash
python3 -m pip install -e ".[dev,lsp]"  # LSP work
python3 -m pip install -e ".[dev,llm]"  # experiment runner/provider work
```

## Development Commands

```bash
# Run tests
python3 -m pytest geno/tests/ -v

# Run tests with coverage
python3 -m pytest geno/tests/ -v --tb=short --cov=geno --cov-report=term --cov-report=html --cov-fail-under=80 --timeout=60

# Lint and format check
python3 -m ruff check geno/ benchmark/ experiment/ analysis/
python3 -m ruff format --check geno/ benchmark/ experiment/ analysis/

# Auto-fix lint and formatting
python3 -m ruff check --fix geno/ benchmark/ experiment/ analysis/
python3 -m ruff format geno/ benchmark/ experiment/ analysis/

# Type check
python3 -m mypy geno/ --ignore-missing-imports --no-error-summary

# Local CI wrappers
python3 scripts/local_ci.py targeted --paths <changed-paths...> --tests <pytest-targets...>
python3 scripts/local_ci.py full
python3 scripts/local_ci.py optional
python3 scripts/local_ci.py release

# Release gate
make release-check

# Security-focused checks
make security

# Run a Geno program
python3 -m geno run examples/fibonacci.geno

# Type-check a Geno program
python3 -m geno check examples/fibonacci.geno

# Compile to Python
python3 -m geno compile examples/fibonacci.geno -o out.py

# Compile to JavaScript
python3 -m geno compile examples/fibonacci.geno --target js -o out.js
```

## Geno Language Rules (for writing .geno code)

- **Immutable bindings**: `let x: Type = value` or `let x = value`
- **Mutable bindings**: `var x: Type = value` or `var x = value` (NOT `let mut`)
- **Type annotations**: required for function parameters/returns; local `let`/`var` annotations are optional when the initializer is unambiguous
- **Booleans**: `true` / `false` (lowercase, NOT `True`/`False`)
- **String concat**: `+` (NOT `++`)
- **Indexing**: `xs[i]` (NOT `get(xs, i)`)
- **Named args**: required for functions with 3+ parameters
- **Examples**: every function needs `example` clauses (except `main`, async, app lifecycle)
- **Zero-arg examples**: `example () -> value`
- **Keyword conflicts**: `end`, `in`, `with`, `type` are reserved - do not use them as parameter names
- **Block terminators**: `end func`, `end if`, `end for`, `end while`, `end match`, `end try`
- **`then`** required after `if` condition; **`do`** required after `for`/`while`
- **Integer division** truncates: `7 / 2 = 3`
- **Naming**: PascalCase for types/constructors, snake_case for functions/variables

## Commit Conventions

- Conventional Commits: `type(scope): description`
- Types: feat, fix, docs, test, refactor, ci, chore
- Reference GitHub issues: `#123`

## CI Requirements

- Tests pass on Python 3.10-3.13
- 80% minimum code coverage
- Ruff check + format clean for `geno/`, `benchmark/`, `experiment/`, and `analysis/`
- Mypy clean for `geno/`
- Release-sensitive changes pass `make release-check` or `python3 scripts/local_ci.py release`

## Agent Operating Guidance

- Inspect `git status --short` before editing. Preserve user changes and work with
  dirty files instead of reverting them.
- Keep work scoped to the subsystem being changed. For broad or ambiguous work,
  plan first and agree on milestones before implementation.
- Prefer established local patterns and validators over new abstractions or
  manual synchronization.
- Use subagents for bounded read-heavy work such as exploration, test-gap
  analysis, review, or log triage. Avoid parallel write-heavy work unless the
  write sets are disjoint and coordinated.
- Report exact validation commands, skipped checks, and remaining risks in the
  final handoff.

## Workflow

1. Inspect the worktree and read the relevant code before changing it.
2. Write or update tests for the change.
3. Implement the smallest coherent change across every affected layer.
4. Run focused validation with `python3 scripts/local_ci.py targeted --paths <changed-paths...> --tests <pytest-targets...>`.
5. For parser, typechecker, compiler, runtime, fuzz/property, or backend parity work, consider `python3 scripts/local_ci.py optional`.
6. For broad or release-sensitive changes, run `python3 scripts/local_ci.py full`, `python3 scripts/local_ci.py release`, or `make release-check` as appropriate.
7. If tests fail, fix and re-run. Do not declare done until the relevant checks are green or an environment-only blocker is documented.

## Validation by Subsystem

- **Parser/typechecker/language semantics**: run focused parser/typechecker tests, then targeted local CI for touched files.
- **Python or JavaScript backend behavior**: include backend parity tests and the relevant compiler/JS compiler tests.
- **Builtins, capabilities, and targets**: update `geno/builtin_manifest.py` and derived expectations through validators; run builtin metadata/parity and capability tests.
- **Sandbox, security, or untrusted execution**: run the relevant sandbox/capability/security tests and `make security`; use `python3 scripts/local_ci.py release` for release-sensitive hardening.
- **CLI, package, project resolution, LSP, formatter, or VS Code work**: run the focused tooling tests plus targeted local CI.
- **Docs, examples, stdlib, selfhost, or reference apps**: run docs snippet, stdlib, selfhost, or release app checks as appropriate; release-sensitive examples should pass `python3 scripts/local_ci.py release`.

## Review Guidelines

- Focus reviews on correctness, regressions, missing tests, security boundaries,
  backend parity, public behavior, and release-gate risk.
- Flag capability bypasses, sandbox escapes, unsafe host access, or untrusted
  execution regressions as high priority.
- Check that language changes keep lexer, parser, AST, typechecker,
  interpreter, Python compiler, JavaScript compiler, docs, and `spec.json`
  aligned where applicable.
- Treat review output as advisory. Verify each finding against the real code
  path before changing code.
- Do not spend review comments on formatting that Ruff will handle unless it
  affects readability or behavior.

## Optional Review Closeout

For non-trivial code changes, use Codex review as a final advisory pass after
formatting and focused validation:

```bash
# Dirty local work
codex review --uncommitted

# Branch work
git fetch origin
codex review --base origin/main

# Single committed change
codex review --commit HEAD
```

- Treat review output as advisory; verify each finding against the real code path before changing anything.
- Prefer small fixes at the right ownership boundary, and avoid broad refactors unless they clearly address the bug class.
- If a review-triggered fix changes code, rerun the affected tests and rerun Codex review until no accepted actionable findings remain.
- Do not push just to review; push only when the user requested a push, ship, or PR update.

## Testing

- Framework: pytest
- Test directory: `geno/tests/`
- Test files: `test_*.py`

## Subsystem Independence

Key subsystems are independent; scope work to one area at a time:

- **Compiler pipeline**: lexer -> parser -> typechecker -> compiler/js_compiler
- **Runtime**: interpreter, sandbox, builtins, values
- **Tooling**: LSP, formatter, test_runner, package_manager
- **Standard library**: `geno/std/`
- **Self-hosted frontend + interpreter**: `selfhost/`

## Security and Execution Boundaries

- `geno.api.run()` is an in-process embedding API with cooperative timeout and
  capability gating. For untrusted code, use the hosted server boundary or
  caller-managed process isolation.
- Generated JavaScript does not include a sandbox and is intended for trusted
  execution environments.
- Treat effectful builtins as fail-closed: no filesystem, process, network,
  environment, time, or random access without explicit capability and tests.
- Security corpus entries use structured `# EXPECT` headers; preserve that style
  when adding sandbox or capability regressions.
- Do not weaken production mypy settings to satisfy test typing. Test typing has
  a separate staged profile in `mypy-tests.ini`.

## Key Architecture Notes

- **AST nodes**: defined in `geno/ast_nodes.py`
- **Built-in functions**: implemented in `geno/builtins.py`; capability and named-argument metadata is declared once in `geno/builtin_manifest.py` and derived through `geno/builtin_registry.py`
- **Safety wrappers**: compiled Python uses `_safe_add`, `_safe_index`, etc. for runtime safety
- **Sandbox**: `geno/sandbox.py` provides step-limited, memory-guarded execution
