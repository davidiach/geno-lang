# Geno

[![CI](https://github.com/davidiach/geno-lang/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/davidiach/geno-lang/actions/workflows/ci.yml)
[![Python 3.10-3.13](https://img.shields.io/badge/python-3.10--3.13-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/davidiach/geno-lang/blob/main/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Status: Preview](https://img.shields.io/badge/status-preview-yellow.svg)](https://github.com/davidiach/geno-lang/blob/main/docs/preview-program.md)

Geno is a typed programming language for generating reliable small programs with
LLMs. It combines examples-as-tests, explicit control-flow boundaries,
capability-gated effects, and Python/JavaScript backends so generated code is
easier to check, run, and repair. Save this as `score.geno`:

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

## Why Geno?

LLMs are good at producing plausible code. Geno is designed to make the
plausible code easier to constrain, validate, and ship.

| Common failure mode | Geno design choice | What you get |
|---|---|---|
| Bracket and indentation drift | `end func`, `end if`, `end match` | Clear parse boundaries in generated code |
| Untested behavior | Required `example` clauses | Inline executable specs for most functions |
| Type confusion | Explicit function signatures plus local inference | Stable interfaces without noisy locals |
| Accidental effects | Capability-gated filesystem, network, process, env, clock, random, print, regex | Effects are visible at the command line |
| Edge-case misses | Exhaustive pattern matching and `requires` / `ensures` contracts | Better compiler feedback before runtime |
| Runtime escape risk | Capability gates and surface-specific sandboxing | Safer execution of generated programs |

## Quickstart

Install Geno from PyPI with `pip install geno-lang`. For an editable source
checkout, install from the repository root with `pip install -e .`.

Create and run a project:

```bash
geno init hello --template cli
cd hello
geno test Main.geno
geno run Main.geno
```

Expected output:

```text
>>> Hello, World! <<<
```

Explore the repository examples:

```bash
git clone https://github.com/davidiach/geno-lang.git
cd geno-lang
pip install -e ".[dev]"
```

Run, test, and type-check an example:

```bash
geno run examples/fibonacci.geno
geno test examples/fibonacci.geno
geno check examples/fibonacci.geno
```

Compile to Python or JavaScript:

```bash
geno compile examples/quicksort.geno -o quicksort.py
geno compile --target js examples/quicksort.geno -o quicksort.js
```

Build a browser app:

```bash
geno build examples/apps/geno-dash -o dist/
```

## What You Can Build

| Target | Command | Status | Notes |
|---|---|---|---|
| Python CLI | `geno run`, `geno compile -o app.py` | Beta | File I/O, HTTP, process, env, clock, random, print, regex via capabilities |
| Node.js CLI | `geno compile --target js -o app.js` | Beta | Trusted execution only; capability flags do not confine Node APIs |
| Browser app | `geno build -o dist/` | Beta | Canvas apps with graphics/input builtins; `--single-file` available |
| Hosted runtime | `geno serve` | Beta | HTTP API with `/healthz`, `/metrics`, `/run`, and `/constrain` |
| Package manager | `geno install`, `geno add`, `geno update` | Experimental | Git-based dependencies with lockfile pins |
| LSP / VS Code | `geno lsp` | Experimental | Diagnostics, hover, completion, references, rename, signature help |
| Self-hosted frontend | `selfhost/` | Experimental | Geno-in-Geno frontend plus tree-walking interpreter |

See the [maturity matrix](https://github.com/davidiach/geno-lang/blob/main/docs/MATURITY.md) and
[supported targets](https://github.com/davidiach/geno-lang/blob/main/docs/SUPPORTED_TARGETS.md) for the full status breakdown.

## Language In 60 Seconds

Geno is functional-first, statically typed, and explicit about block structure:

```geno
type Result[T, E] = Ok(value: T) | Err(error: E)

func safe_divide(a: Int, b: Int) -> Result[Int, String]
    example (10, 2) -> Ok(5)
    example (10, 0) -> Err("division by zero")

    if b == 0 then
        return Err("division by zero")
    else
        return Ok(a / b)
    end if
end func
```

The language includes:

- ADTs, pattern matching, match guards, and rest patterns
- `List`, `Array`, `Vec`, `Map`, `MutableMap`, and `Set`
- `Result` / `Option`, `try` / `catch`, `throw`, and `?` propagation
- Async functions and `await`
- Pipelines with `|>`
- Traits and `impl` blocks
- F-strings, CSV/TOML/JSON helpers, and capability-gated effects
- Python and JavaScript compilation

## Portable Semantics

The interpreter and both compilers share one observable contract. Mixed
`Int`/`Float` equality is numeric, constructor bindings use value semantics,
`print` writes top-level strings without quotes, and updating an existing map
key preserves insertion order. Map indexing (`m[key]`) is partial and raises
when absent; use `map_get` for an `Option` result.

JavaScript targets support portable integers from `-(2^53 - 1)` through
`2^53 - 1`; Python and the interpreter can use the larger configurable
`max_integer_bits` limit. The target-specific runtime preludes are
hand-maintained and guarded by three-backend parity tests. See
[Portable Runtime Semantics](https://github.com/davidiach/geno-lang/blob/main/docs/reference/runtime-semantics.md) for details.

## Examples

| Example | What it shows |
|---|---|
| [`examples/fibonacci.geno`](https://github.com/davidiach/geno-lang/blob/main/examples/fibonacci.geno) | Recursion, iteration, examples-as-tests |
| [`examples/safe_divide.geno`](https://github.com/davidiach/geno-lang/blob/main/examples/safe_divide.geno) | `Result`, `Option`, and pattern matching |
| [`examples/word_count.geno`](https://github.com/davidiach/geno-lang/blob/main/examples/word_count.geno) | String helpers, lambdas, pipelines |
| [`examples/apps/geno-check`](https://github.com/davidiach/geno-lang/tree/main/examples/apps/geno-check) | Multi-module CLI validation app |
| [`examples/apps/geno-dash`](https://github.com/davidiach/geno-lang/tree/main/examples/apps/geno-dash) | Browser dashboard with canvas widgets |
| [`examples/apps/geno-snap`](https://github.com/davidiach/geno-lang/tree/main/examples/apps/geno-snap) | Hosted API mock server |
| [`examples/apps/geno-mark`](https://github.com/davidiach/geno-lang/tree/main/examples/apps/geno-mark) | Markdown-to-HTML CLI demo |

The release-gated reference apps are documented in
[`docs/REFERENCE_APPS.md`](https://github.com/davidiach/geno-lang/blob/main/docs/REFERENCE_APPS.md).

## Safety Model

Geno separates pure computation from effects. Builtins that touch the filesystem,
network, process execution, environment, clock, random values, output, or regex
are capability-gated.

```bash
geno run tool.geno --unsafe --cap fs --cap http
```

The CLI and hosted runtime use sandboxed execution with process isolation. The
in-process embedding API is cooperative, and generated JavaScript is intended
for trusted environments rather than as a security boundary. Read the
[security policy](https://github.com/davidiach/geno-lang/blob/main/SECURITY.md), [execution surface](https://github.com/davidiach/geno-lang/blob/main/docs/runtime/execution-surface.md),
and [capability reference](https://github.com/davidiach/geno-lang/blob/main/docs/reference/capabilities.md) before exposing Geno
execution to untrusted users.

## Benchmarks And Research

Geno includes two benchmark tracks:

- [Runtime benchmark snapshot](https://github.com/davidiach/geno-lang/blob/main/benchmarks/RESULTS.md): the current committed run
  meets the suite target of `<=2x` for at least 80% of measured problems.
- [LLM correctness benchmark](https://github.com/davidiach/geno-lang/blob/main/docs/benchmark/llm-correctness-results.md):
  methodology and reproducible configuration are tracked, but public
  frontier-model publication is deferred and no Geno-vs-Python advantage is
  claimed yet.

Published Geno-vs-Python result status lives in
[`docs/benchmark/results.md`](https://github.com/davidiach/geno-lang/blob/main/docs/benchmark/results.md). Generated result
reports must be created from raw experiment artifacts with
`scripts/publish_benchmark_results.py`.

Reproduce the validation pass:

```bash
python3 scripts/validate_benchmark.py
```

Run a configured LLM experiment:

```bash
python3 scripts/run_experiment.py --config experiment/config.example.yaml
```

## Project Status

Geno is in preview. It is usable for experiments, examples, and early tools, but
the public pre-1.0 surface can still change.

- Supported Python versions: 3.10-3.13
- License: Apache 2.0
- PyPI distribution: `geno-lang`
- Current version metadata is checked by `scripts/check_version_alignment.py`

## Documentation

Start here:

- [Getting Started](https://github.com/davidiach/geno-lang/blob/main/docs/guide/getting-started.md)
- [Language Tour](https://github.com/davidiach/geno-lang/blob/main/docs/guide/language-tour.md)
- [Common Pitfalls](https://github.com/davidiach/geno-lang/blob/main/docs/reference/common-pitfalls.md)
- [Portable Runtime Semantics](https://github.com/davidiach/geno-lang/blob/main/docs/reference/runtime-semantics.md)
- [Capability Reference](https://github.com/davidiach/geno-lang/blob/main/docs/reference/capabilities.md)
- [Supported Targets](https://github.com/davidiach/geno-lang/blob/main/docs/SUPPORTED_TARGETS.md)

Go deeper:

- [Full Documentation Index](https://github.com/davidiach/geno-lang/blob/main/docs/INDEX.md)
- [Language Specification](https://github.com/davidiach/geno-lang/blob/main/docs/spec/v0.2.md)
- [Embedding API](https://github.com/davidiach/geno-lang/blob/main/docs/reference/embedding-api.md)
- [LLM Prompting Guide](https://github.com/davidiach/geno-lang/blob/main/docs/llm-prompting.md)
- [Release Runbook](https://github.com/davidiach/geno-lang/blob/main/docs/operations/release-runbook.md)

## Development

```bash
git clone https://github.com/davidiach/geno-lang.git
cd geno-lang
pip install -e ".[dev]"
```

Useful local checks:

```bash
python3 -m pytest geno/tests/ -v
python3 -m ruff check geno/ benchmark/ experiment/ analysis/
python3 -m ruff format --check geno/ benchmark/ experiment/ analysis/
python3 -m mypy geno/ --ignore-missing-imports --no-error-summary
python3 scripts/local_ci.py full
```

Release-sensitive changes should pass:

```bash
make release-check
```

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](https://github.com/davidiach/geno-lang/blob/main/CONTRIBUTING.md), then
look at the [preview feedback template](https://github.com/davidiach/geno-lang/blob/main/.github/ISSUE_TEMPLATE/preview_feedback.md)
or the open issue list for scoped work.

Security issues should follow [SECURITY.md](https://github.com/davidiach/geno-lang/blob/main/SECURITY.md).

## License

Apache License 2.0. See [LICENSE](https://github.com/davidiach/geno-lang/blob/main/LICENSE) for details.
