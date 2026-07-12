# Reference Apps

The release gate validates every app candidate under `examples/apps/`: each
project or standalone app must check, test, and build/compile/run successfully
before a release is tagged. The six apps below are the primary reference set;
they collectively exercise all four target lanes and key language features.

## 1. geno-check

**Target:** `python-cli`
**Description:** A config and data file validator. Reads JSON, CSV, or TOML files and reports violations against typed validation rules.

**Modules:**
- `Main.geno` — CLI entry point, file type detection, validation dispatch
- `Violation.geno` — Violation type definition
- `JsonRules.geno` — JSON validation rules
- `CsvRules.geno` — CSV validation rules
- `TomlRules.geno` — TOML validation rules
- `Report.geno` — Violation report formatting

**Capabilities required:** `fs`, `print`

**Exercises:**
- Multi-module project compilation (Python)
- File I/O builtins (`fs_read_text`)
- User-defined types and pattern matching
- Pipeline expressions (`|>`)
- Result type for error handling

## 2. geno-dash

**Target:** `browser`
**Description:** A live data dashboard rendered on canvas. Fetches metrics and displays bar charts, stat cards, and sparklines.

**Modules:**
- `Main.geno` — App init/update/render lifecycle, state management
- `Data.geno` — Metric generation and data fetching
- `Widgets.geno` — UI widget components (charts, cards, sparklines)
- `Layout.geno` — Dashboard grid layout
- `Theme.geno` — Color palette and visual theming

**Capabilities required:** `print`

**Exercises:**
- App mode (init/update/render lifecycle)
- Multi-module browser compilation to HTML
- Graphics builtins (`draw_rect`, `draw_text`, `clear_screen`)
- Higher-order functions (`map`, `fold`, `filter`)
- Float math operations
- List processing pipelines

## 3. geno-snap

**Target:** `python-hosted`
**Description:** An API mock server. Handler functions use example clauses as mock responses, and `requires` clauses validate incoming requests.

**Modules:**
- `Main.geno` — Route registration, server startup via `http_listen`
- `Models.geno` — Data model type definitions
- `Validate.geno` — Request validation logic
- `Responses.geno` — Response formatting and JSON serialization
- `Routes.geno` — Route handler functions

**Capabilities required:** `serve`, `print`

**Exercises:**
- Hosted server handler compilation
- HTTP server builtins (`http_listen`, `http_route`)
- JSON serialization (`json_to_string`)
- Input validation with `requires` contracts
- Multi-module project compilation (Python)

## 4. geno-form

**Target:** `browser`
**Description:** A canvas-based form builder demo. Type definitions drive field layout, constraints drive validation rules, and example clauses drive placeholder values. Forms are rendered on canvas using the browser target's graphics primitives -- this demonstrates Geno's type system, not HTML form generation.

**Modules:**
- `Main.geno` — App entry, form rendering loop (init/update/render lifecycle)
- `Types.geno` — Core ADTs: InputKind, FieldDef, FieldValue, FieldError, FormState
- `Fields.geno` — Field constructor functions (text, email, number, bool, select)
- `Validate.geno` — Runtime validation from field constraints
- `Render.geno` — Canvas-based form rendering
- `Forms.geno` — Example form definitions (registration, contact)

**Capabilities required:** `print`

**Exercises:**
- App mode lifecycle
- Multi-module browser compilation
- Graphics builtins for canvas form UI
- Pattern matching on form field types
- User-defined types (ADTs) for form modeling

## 5. geno-pipe

**Target:** `python-cli`
**Description:** A data transformation pipeline. Reads CSV/JSON, pipes through transforms using `|>`, and writes output in multiple formats.

**Modules:**
- `Main.geno` — CLI entry, input/output file handling
- `Types.geno` — Data types for rows, columns, tables
- `Transform.geno` — Filter, map, sort, group, aggregate operations
- `Parse.geno` — CSV/JSON input parsing
- `Format.geno` — Output formatting (CSV, JSON, table, markdown)

**Capabilities required:** `fs`, `print`

**Exercises:**
- Multi-module project compilation (Python)
- File I/O builtins
- Pipeline expressions (`|>`) throughout
- Higher-order functions for transforms
- Pattern matching on data types

## 6. geno-log

**Target:** `node-cli`
**Description:** A Node.js log summarizer. Compiles to JavaScript, runs under Node, and verifies its stdout against a checked-in golden output.

**Modules:**
- `Main.geno` — Node CLI entry point
- `Types.geno` — Event data model
- `Stats.geno` — Status counts, latency, and health score helpers
- `Report.geno` — Human-readable report formatting

**Capabilities required:** none

**Exercises:**
- Multi-module project compilation (JavaScript)
- Node CLI execution
- End-to-end golden output verification
- User-defined types and field access
- List iteration and integer aggregation

## Target Lane Coverage

| App | python-cli | node-cli | browser | python-hosted |
|-----|:----------:|:--------:|:-------:|:-------------:|
| geno-check | X | | | |
| geno-dash | | | X | |
| geno-snap | | | | X |
| geno-form | | | X | |
| geno-pipe | X | | | |
| geno-log | | X | | |

## Additional Release-Gated Showcase Apps

The following apps are useful public examples and are also validated by
`scripts/release_gate_apps.py`:

| App | Target | Purpose |
|-----|--------|---------|
| `geno-mark` | `python-cli` | Markdown-to-HTML CLI demo using filesystem input |
| `calculator.geno` | `browser` | Single-file canvas calculator app |
| `notetaker.geno` | `browser` | Single-file canvas note-taking demo |
| `tetris.geno` | `browser` | Single-file canvas game demo |

## Feature Coverage Matrix

| Feature | check | dash | snap | form | pipe | log |
|---------|:-----:|:----:|:----:|:----:|:----:|:---:|
| Multi-module compilation | X | X | X | X | X | X |
| User-defined types (ADTs) | X | | X | X | X | X |
| Pattern matching | X | | X | X | X | |
| Pipeline expressions | X | | | | X | |
| Higher-order functions | | X | | | X | |
| Requires/ensures | | | X | X | | |
| Result type | X | | | | | |
| File I/O | X | | | | X | |
| HTTP server | | | X | | | |
| JSON builtins | X | | X | | | |
| Graphics builtins | | X | | X | | |
| App mode lifecycle | | X | | X | | |
| Node CLI golden output | | | | | | X |
