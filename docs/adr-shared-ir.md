# ADR: Shared IR for Python and JavaScript Backends

**Status:** Decided — skip shared IR, extract shared data instead  
**Date:** 2026-04-06  
**Issue:** #161

## Context

Geno has two compilation backends: `compiler.py` (Python, 1866 lines) and
`js_compiler.py` (JavaScript, 1662 lines). Both consume the same AST from
`ast_nodes.py` and follow the same visitor pattern. Additionally, the runtime
support files (`_runtime_support.py` at 2217 lines, `_js_runtime_support.js`
at 1800 lines) implement the same builtins in each target language. The
question is whether introducing a shared intermediate representation would
reduce duplication and maintenance burden.

## Duplication Analysis

### High duplication (mechanical, data-level)

| Area | Overlap | Lines each |
|------|---------|------------|
| `_init_builtin_param_names` | 100% identical dict | ~250 |
| First-pass collection (type_defs, func_param_names, traits, impls) | ~90% | ~30 |
| Reserved-name validation loop | ~90% | ~20 |
| Statement dispatch (`_compile_statement`) | identical routing | ~30 |
| Method set structure | 30 identically-named `_compile_*` methods | — |

### Low duplication (genuinely target-specific)

| Area | Python | JavaScript |
|------|--------|------------|
| Division | `_safe_div` only | `_safe_div` + `_float_div` (float context) |
| Equality | native `==` | `_valuesEqual` deep comparison |
| Subtraction/modulo | `_safe_sub`, `_safe_mod` | native `-`, `%` |
| Boolean ops | `bool(x and y)` | `(x && y)` |
| Scoping | indentation + `pass` | `{}`/`const`/`let` |
| Constructors | frozen dataclasses | frozen plain objects |
| App mode | not supported | `requestAnimationFrame` game loop |
| Async | `asyncio.run()` | `async/await` + `.catch()` |
| Name mangling | Python `keyword.kwlist` | JS reserved words |
| Bitwise invert | `_safe_invert` | native `~` |

### Runtime support

The runtime files (`_runtime_support.py`, `_js_runtime_support.js`) implement
~100 builtins each in their native language. These cannot share code regardless
of whether an IR exists — they are inherently target-specific.

Both preludes are hand-maintained source files, not generated artifacts. Their
shared semantic contract is enforced by differential tests that execute the
same corpus through the interpreter, compiled Python, and compiled JavaScript.
A backend-specific optimization is acceptable only when those observable
results remain aligned.

## Decision

**Skip the shared IR. Extract shared data to a common module instead.**

### Rationale

1. **The real duplication is narrow and data-level.** The `_init_builtin_param_names`
   dict (~250 lines, 100% identical) and the first-pass collection logic (~30 lines,
   ~90% identical) account for most of the true duplication. These can be extracted
   to a shared module with trivial refactoring — no IR needed.

2. **An IR adds indirection for marginal gain.** Each backend would still need a
   complete set of code emitters for every AST node. The IR would effectively be
   "the AST with some pre-processing" — which is what the first pass already does.
   The abstraction layer would cost more to maintain than it saves.

3. **Backend-specific logic is substantial.** Division semantics, equality semantics,
   scoping rules, boolean evaluation, and async patterns all differ between targets.
   An IR that abstracts over these would either be too low-level (biased toward one
   target) or too high-level (losing information both backends need), requiring
   target-specific escape hatches that negate the benefit.

4. **Only two backends exist, and both evolve together.** A shared IR pays off
   with 3+ backends or when backends change frequently. With two beta backends
   that change in sync (new builtins are added to both at once), the maintenance
   cost of the IR itself would likely exceed the savings.

5. **The AST already IS the shared representation.** Both compilers consume the same
   typed `ast_nodes` definitions. Adding an intermediate layer between AST and output
   would add complexity without meaningful new abstraction.

## Recommended Refactoring (without IR)

Two targeted extractions that capture ~80% of the deduplication benefit:

1. **Extract `_BUILTIN_PARAM_NAMES` to `geno/_builtin_params.py`.**  
   Single source of truth for the ~150-entry param name dict. Both compilers import
   it instead of maintaining their own copy.

2. **Extract first-pass collection to a shared helper.**  
   A function like `collect_definitions(program) -> DefinitionIndex` that returns
   `type_defs`, `func_param_names`, `trait_defs`, `impl_defs`, and `trait_dispatch`.
   Both compilers call it instead of duplicating the collection loop.

These are Phase 3+ candidates — they reduce duplication without introducing
architectural risk.
