# Maturity Matrix

What you can rely on today, and what's still evolving.

## Status Definitions

| Status | Meaning |
|---|---|
| **Stable** | Tested, documented, API unlikely to change. Safe for production use. |
| **Beta** | Functional and tested, but API may evolve. Breaking changes will be noted in the changelog. |
| **Experimental** | Works for common cases but has known gaps. Expect rough edges. |
| **Research** | Proof of concept. May not work for your use case. |

## Component Status

| Component | Status | Notes |
|---|---|---|
| Language core (syntax, semantics) | **Beta** | Grammar, type system, and semantics are well covered, but the public pre-1.0 surface can still change. |
| Parser | **Stable** | Full language coverage. Structured diagnostics with source locations. |
| Typechecker | **Stable** | Generics, ADTs, traits, pattern matching, exhaustiveness checking. |
| Python backend (`geno compile`) | **Beta** | Single-file and project-directory compilation flow through project resolution and `DependencyGraph`. Good feature coverage, but the generated runtime surface may still evolve. |
| JS backend (`geno compile --target js`) | **Beta** | Project directories compile through the same pipeline. Source maps, `.d.ts` generation, and ESM output are available. |
| Interpreter (`--unsafe`) | **Beta** | Tree-walking interpreter. Slower than compiled backends but supports all features including capability gating. |
| Browser app mode (`geno build`) | **Beta** | Builds a `dist/` directory by default, with `--single-file` for self-contained HTML. Used by shipped example apps (geno-dash, geno-form). |
| Hosted runtime (`geno serve`) | **Beta** | HTTP server with auth, rate limiting, sandboxed execution, and Prometheus metrics. Not yet battle-tested under production load. |
| Capability system | **Beta** | Capability metadata is derived from the builtin manifest and enforced across interpreter and hosted/runtime entry points. |
| Package manager | **Experimental** | `install`, `add`, `search`, and `update` exist for git-based dependencies with branch/tag pins and lockfile content hashes. Distribution/discovery is still lightweight and repo-centric. |
| LSP server | **Experimental** | Diagnostics, go-to-definition, completion, hover, rename, references, and signature help work. Project-wide analysis uses `ProjectGraph`, but the editor surface still depends on optional LSP dependencies. |
| Self-hosted frontend | **Experimental** | Frontend + interpreter written in Geno. `scripts/check_selfhost_parity.py` currently reports 123/228 builtin coverage (53%), and CLI smoke tests cover demo/check/run/test paths. No code-generation backend or bootstrap path yet. |
| Constrained decoding | **Research** | Novel prefix-validation module for LLM-guided generation. Works but not yet benchmarked against real models. |
| VS Code extension | **Experimental** | Syntax highlighting and LSP integration. Diagnostics depend on an installed Geno CLI and optional LSP dependencies. |
| Formatter (`geno fmt`) | **Experimental** | Works on simple files. Not yet verified on the full selfhost source. |
