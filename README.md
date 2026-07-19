# Geno

[![CI](https://github.com/davidiach/geno-lang/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/davidiach/geno-lang/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/geno-lang.svg)](https://pypi.org/project/geno-lang/)
[![Status: Preview](https://img.shields.io/badge/status-preview-yellow.svg)](docs/preview-program.md)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

[Install](#quickstart) | [Language Tour](docs/guide/language-tour.md) | [Examples](#examples) | [Documentation](docs/INDEX.md) | [Maturity](docs/MATURITY.md) | [Contributing](CONTRIBUTING.md)

Geno is a statically typed, functional-first programming language for reliable
small programs written with LLMs. You can write Geno directly or generate it
with a model; executable examples, contracts, explicit block boundaries, and
capability-gated effects make the result easier to check and repair. Geno runs
directly and compiles to Python and JavaScript.

> **Project status: Preview.** Geno is ready for evaluation, examples, and early
> tools, but its pre-1.0 language and tooling may change. **Beta** and
> **Experimental** below describe individual component readiness, not overall
> project stability. See the [preview program](docs/preview-program.md) and
> [maturity matrix](docs/MATURITY.md).

Here is the core idea. Save this as `score.geno`:

```geno
func score_label(score: Int) -> String
    requires score >= 0
    example 95 -> "excellent"
    example 70 -> "passing"
    example 40 -> "needs work"

    if score >= 90 then
        return "excellent"
    else if score >= 60 then
        return "passing"
    else
        return "needs work"
    end if
end func

func main() -> String
    return score_label(95)
end func
```

```text
$ geno test score.geno
3 passed, 0 failed

$ geno run score.geno
=> excellent
```

- `example` clauses are executable specifications.
- `requires` states a checked precondition.
- Explicit `end` tokens make generated block boundaries unambiguous.

## Quickstart

Geno requires Python 3.10-3.13. Install the published CLI, create a project,
then test and run it:

```bash
pip install geno-lang
geno --version
geno init hello --template cli
cd hello
geno test Main.geno
geno run Main.geno
```

The generated program finishes with:

```text
>>> Hello, World! <<<
```

Continue with [Getting Started](docs/guide/getting-started.md), or jump to the
[Language Tour](docs/guide/language-tour.md).

## Why Geno?

LLMs are good at producing plausible code. Geno is designed to make plausible
code easier to constrain, validate, and ship.

| Common failure mode | Geno design choice | What you get |
|---|---|---|
| Bracket and indentation drift | `end func`, `end if`, `end match` | Clear parse boundaries in generated code |
| Untested behavior | Required `example` clauses | Inline executable specs for most functions |
| Type confusion | Explicit function signatures plus local inference | Stable interfaces without noisy locals |
| Accidental effects | Capability-gated filesystem, network, process, env, clock, random, print, regex | Effects are visible at the command line |
| Edge-case misses | Exhaustive pattern matching and `requires` / `ensures` contracts | Better compiler feedback before runtime |
| Runtime escape risk | Capability gates and surface-specific sandboxing | Safer execution of generated programs |

## Where Geno Runs

| Surface | Start here | Status | Boundary |
|---|---|---|---|
| Python CLI | `geno run`, `geno test`, `geno compile -o app.py` | Beta | Effectful builtins require explicit capabilities |
| Node.js CLI | `geno compile --target js -o app.js` | Beta | Generated JavaScript is for trusted execution; capability flags do not confine Node.js APIs |
| Browser app | `geno build -o dist/` | Beta | Produces static HTML/JS artifacts with browser-specific capabilities |
| Hosted runtime | `geno serve` | Beta | Provides an HTTP API with isolated execution, auth, rate limiting, and metrics |

Package management, the LSP and VS Code extension, the formatter, and the
self-hosted frontend are experimental. See [Supported Targets](docs/SUPPORTED_TARGETS.md)
and the [Maturity Matrix](docs/MATURITY.md) for the detailed contract.

## Language At A Glance

Geno is immutable by default and includes:

- Static typing with local inference, generics, traits, and `impl` blocks
- Algebraic data types, exhaustive pattern matching, guards, and rest patterns
- `Result` / `Option`, `try` / `catch`, `throw`, and `?` propagation
- Lists, arrays, vectors, maps, mutable maps, and sets
- Lambdas, pipelines with `|>`, async functions, and `await`
- F-strings plus CSV, TOML, and JSON helpers
- Python and JavaScript compilation

The [Language Tour](docs/guide/language-tour.md) explains these features with
runnable examples. Target-specific behavior is documented in
[Portable Runtime Semantics](docs/reference/runtime-semantics.md).
The [Language Specification](docs/spec/v0.4.md) defines the normative 0.4
contract.

## Examples

| Example | What it shows |
|---|---|
| [`examples/fibonacci.geno`](examples/fibonacci.geno) | Recursion, iteration, examples-as-tests |
| [`examples/safe_divide.geno`](examples/safe_divide.geno) | `Result`, `Option`, and pattern matching |
| [`examples/apps/geno-check`](examples/apps/geno-check) | Multi-module CLI validation app |
| [`examples/apps/geno-dash`](examples/apps/geno-dash) | Browser dashboard with canvas widgets |
| [`examples/apps/geno-snap`](examples/apps/geno-snap) | Hosted API mock server |

More release-gated applications are listed in
[Reference Apps](docs/REFERENCE_APPS.md).

## Safety And Limits

Geno separates pure computation from effects. Builtins that touch the
filesystem, network, process execution, environment, clock, random values,
output, or regex require explicit capabilities.

The CLI and hosted runtime use sandboxed execution with process isolation. The
in-process embedding API has cooperative limits, and generated JavaScript is
not a security boundary. Before running untrusted programs, read the
[Security Policy](SECURITY.md), [Execution Surface](docs/runtime/execution-surface.md),
and [Capability Reference](docs/reference/capabilities.md).

The package ecosystem and editor tooling are still young, and breaking changes
remain possible before 1.0. For current limitations, consult the
[Maturity Matrix](docs/MATURITY.md) and [Common Pitfalls](docs/reference/common-pitfalls.md).

## Research

Geno tracks a reproducible [runtime benchmark snapshot](benchmarks/RESULTS.md),
whose committed run meets the suite target of `<=2x` for at least 80% of
measured problems. The [LLM correctness benchmark](docs/benchmark/llm-correctness-results.md)
documents its methodology, but public frontier-model results are deferred and
no Geno-vs-Python correctness advantage is currently claimed.

## Develop Geno

This repository contains the language implementation, standard library,
documentation, benchmarks, example applications, self-hosted frontend, and VS
Code extension.

```bash
git clone https://github.com/davidiach/geno-lang.git
cd geno-lang
pip install -e ".[dev]"
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and local
checks, [GOVERNANCE.md](GOVERNANCE.md) for decision rights, and the
[design proposal process](docs/proposals/README.md) for substantial changes.
Use [GitHub Discussions](https://github.com/davidiach/geno-lang/discussions)
for questions, the [preview feedback template](.github/ISSUE_TEMPLATE/preview_feedback.md)
for early-user reports, and [SECURITY.md](SECURITY.md) for private vulnerability
reporting. Release history is recorded in [CHANGELOG.md](CHANGELOG.md).

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.
