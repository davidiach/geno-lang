# Geno Product Scope

Geno is a statically typed, functional-first programming language designed to be the safest and most reliable language for LLMs to build small-to-medium applications in. It compiles to Python and JavaScript with mandatory example clauses, a capability-based security model, and specification-first design.

## Target Lanes

Geno targets four application classes. Each lane has a defined runtime, compilation target, and success criteria.

### 1. CLI & Automation (Python)

**Target:** `python-cli`
**Runtime:** Python interpreter
**Commands:** `geno run`, `geno compile -o app.py`

**Success criteria:**
- Multi-module projects compile to a single runnable Python file
- File I/O, HTTP, process execution, and environment variables work via capabilities
- Compiled output runs without the Geno interpreter installed
- Example clauses serve as both documentation and regression tests

### 2. CLI & Automation (Node.js)

**Target:** `node-cli`
**Runtime:** Node.js
**Commands:** `geno compile --target js -o app.js`

**Success criteria:**
- Multi-module projects compile to a single runnable JS file
- File I/O uses Node.js `fs` module; HTTP uses Node.js `fetch`
- Compiled output runs with `node app.js` without Geno installed
- Feature parity with Python CLI target for pure logic and data processing

### 3. Browser Apps and Internal Tools

**Target:** `browser`
**Runtime:** Browser (static HTML/JS artifacts)
**Commands:** `geno build -o dist/`

**Success criteria:**
- App-mode programs (init/update/render) compile to static browser artifacts with canvas
- Graphics, keyboard, and mouse input builtins work
- No server required - output can be served by any static host
- Suitable for internal tools, dashboards, and simple games

### 4. Hosted Server Handlers

**Target:** `python-hosted`
**Runtime:** Hosted Python environment
**Commands:** `geno serve`

**Success criteria:**
- Server handlers compile to Python with HTTP and filesystem access
- Capability model enforces security boundaries (no process execution)
- Handlers can be deployed to standard Python hosting platforms
- Sandbox mode restricts I/O to declared capabilities only

## Non-Goals

The following are explicitly out of scope for the current development phase. Each exclusion has a rationale.

| Non-Goal | Rationale |
|----------|-----------|
| **Native/C backend** | Adds massive compiler complexity with minimal benefit for the target use cases. Python and JS runtimes are sufficient for CLI tools, browser apps, and server handlers. |
| **Desktop/mobile GUI framework** | Browser canvas covers the internal-tools use case. Desktop/mobile apps require platform-specific toolchains that would fragment the development effort. |
| **Large UI framework** | The canvas-based app mode is intentionally minimal. A React/Vue-style component model would add significant surface area without serving the "LLM builds small apps" goal. |
| **Distributed systems / advanced concurrency** | `async`/`await` covers the needed concurrency patterns. Channels, actors, or distributed primitives are premature for the target app sizes. |
| **Large package registry** | A curated standard library and official packages are the priority. A public registry introduces trust, versioning, and supply-chain concerns that are premature. |
| **REPL as primary workflow** | The REPL exists for exploration but the primary workflow is file-based with `geno check` and `geno run`. Investing in REPL ergonomics would detract from compiler quality. |
| **IDE-first development** | LSP support exists and will improve, but the primary consumer is an LLM generating code via CLI. IDE features are secondary to compiler correctness and error quality. |
| **Gradual/dynamic typing** | Static types are a core safety property. Allowing `any` or dynamic types would undermine the "safest language for LLM-generated code" goal. |
| **Macros or metaprogramming** | Keeps the language simple and predictable for LLM generation. Macros create non-obvious code paths that LLMs handle poorly. |
