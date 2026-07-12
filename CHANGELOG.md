# Changelog

All notable changes to Geno will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-07-13

### Added

- **Centralized builtin metadata**: New `geno/builtin_metadata.py` module provides a single source of truth for capability maps, param names, and completion lists — replaces duplicated data across API, server, REPL, LSP, and both compilers (#312, #313)
- **Backend parity tests**: Negative arithmetic and map tuple-pair test programs in the parity suite (#304, #305)
- **Match exhaustiveness**: Recursive exhaustiveness checking for nested pattern matching with constructor specialization (#306)
- **Package manager lockfile tests**: Coverage for commit pinning, shallow-to-full clone upgrade, and tag-based installs (#307)
- **Server wall-timeout tests**: Direct coverage for child-process execution, timeout termination, worker errors, and large output (#308)
- **Python support metadata test**: Regression test that keeps `pyproject.toml`, CI matrix, and classifiers aligned (#309)
- **Standard library expansions**: String module gains `split`, `join`, `replace`, `to_upper`, `to_lower`, `starts_with`, `ends_with`, `contains`, `split_once`; List module gains `length`, `map`, `filter`; Map module gains `get`, `insert` (#390)
- **LSP semantic symbols**: Trait/impl header resolution, top-level type aliases, cross-file semantic analysis (#352–#359)
- **Selfhost annotation parsing**: Parser can now parse `@untested(...)` annotations on functions (#403)
- **Selfhost structured diagnostics**: All compiler stages emit structured diagnostics with source locations (#451)
- **Selfhost CLI entrypoint**: Real CLI with host bridges for file I/O and process execution (#452)
- **Selfhost parity checks**: CI gates and drift checks between selfhost and Python implementation (#448)
- **Differential parity tests**: Property-based tests comparing interpreter, Python, and JS backends (#449)
- **Browser template smoke tests**: Game loop template validated in CI (#447)
- **CI spec validation**: `spec.json` validated against source metadata on every push (#445)
- **Noxfile**: Local multi-Python testing via `nox` sessions aligned with CI matrix (#503)
- **Dependency lock files**: `requirements-dev.lock` and `requirements.lock` for reproducible builds (#504)
- **Shared test fixtures**: `conftest.py` with reusable fixtures for the test suite (#505)
- **Sandbox tests**: Coverage for `breakpoint`, `memoryview`, and `bytearray` bypass vectors (#498)

### Changed

- **Public preview version**: Prepared `geno-lang` 0.4.0 as the first public package version; release metadata is aligned across Python, the language specification, and the VS Code extension
- **Canonical value semantics**: User-defined constructor values are snapshots when rebound; `with` returns an independent value, while `Array`, `Vec`, `Set`, and `MutableMap` remain explicit reference types
- **Portable integer contract**: JavaScript targets reject `Int` results outside `-(2^53 - 1)` through `2^53 - 1` instead of silently rounding; Python and interpreter execution retain the configurable `max_integer_bits` limit
- **Runtime prelude ownership**: Python and JavaScript runtime preludes are documented as hand-maintained, target-specific implementations guarded by differential parity tests
- **Server process isolation**: `/run` requests now execute in a `multiprocessing.Process` child (spawn context) instead of a thread pool, enabling hard kill on wall-clock timeout (#308)
- **Python support range**: support metadata and docs now align on `>=3.10,<3.14`; badge and public compatibility docs reflect `3.10-3.13` (#309, #416)
- **API parameter ordering**: `geno.run()` now standardizes on `filename` before `config` for new call sites while preserving `run(source, RunConfig(...))` backward compatibility (#380)
- **File extension**: Renamed `.gen` to `.geno` throughout project — CLI, tests, examples, docs, and VS Code extension (#454)
- **Parser modularization**: Split `parser.py` monolith into focused mixin components (#450)
- **Structured logging**: Library modules migrated from `print()` to Python `logging` (#500)
- **Expanded linting**: Ruff rule selection expanded with UP, B, C4, SIM, PT, RUF categories (#501)
- **Exception handling**: Broad `except` catches audited and narrowed with structured logging (#502)
- **Type checking**: mypy `check_untyped_defs` enabled globally; core library type errors fixed (#506)
- **CLI modularization**: Runtime callbacks extracted to `_serve.py`, formatting utilities to `_cli_format.py` (#507)
- **Legacy cleanup**: Legacy app template hidden from `geno init` CLI (#444)

### Fixed

- **Cross-backend semantics**: Interpreter, compiled Python, and compiled JavaScript now agree on mixed `Int`/`Float` equality, numeric `divide`, bare-string `print`, map-update insertion order, constructor copy behavior, and mutable `with` results
- **Example verdict parity**: `geno test` now applies the same mixed-numeric equality semantics as shipped compiled programs
- **Type soundness**: Required named parameters cannot be skipped, mutable generic record fields are invariant, and calls through indexed expressions propagate their effects
- **Parser and diagnostics**: Bare `return` no longer consumes the following line, malformed constructor patterns produce a typed diagnostic instead of an internal crash, duplicate top-level functions are rejected, numeric literal adjacency is diagnosed, and unsupported triple-quoted f-strings fail clearly
- **Runtime hardening**: Overlapping non-capturing regex alternations are rejected, deeply nested JSON returns `Err`, default CLI clock/random capabilities work in process mode, rounding is stable at half boundaries, and structural permutation checks avoid a quadratic fallback
- **Formatter stability**: Binding-position `match` expressions and their following statements retain canonical indentation
- **Arithmetic parity**: Integer division and modulo now use truncation-toward-zero semantics across all three backends (interpreter, compiled Python, compiled JS), matching C/JS behavior instead of Python's floor division (#304)
- **Map pair typing**: `map_from_list`, `map_entries`, and `map_from_entries` now use `List[(K, V)]` tuple pairs instead of `List[List[K]]` for type safety (#305)
- **Result type annotations**: Compiled Python output now emits `Union[Ok[T], Err[E]]` instead of dropping the generic parameters (#311)
- **Package manager lockfile**: `install()` now checks HEAD against locked commit and unshallows when needed; tag-based installs use the correct fetch strategy (#307)
- **Compiled selfhost forward references**: Variant field and function annotations are now quoted strings to avoid `NameError` on recursive ADTs (#404)
- **Sandbox dunder validation**: Thread sandbox now blocks `__getattribute__`/`__getattr__` in class definitions, matching the process sandbox (#365)
- **Runtime**: `list_find` crashes on no match — `None_` not callable (#499)
- **JS compiler**: Removed `Object.freeze` from constructors to allow field mutation (#485)
- **JS runtime**: Corrected `set_to_list` comparator sign (#486)
- **Builtins**: Corrected `list_group_by` return type (#484)
- **Manifest**: Preserve unknown TOML keys on round-trip save (#483)
- **Package manager**: Validate `content_hash` on install even when HEAD matches (#482)
- **Interpreter**: Removed duplicate `call_depth` increment in `_eval_function_call` (#479)
- **LSP**: Added size bounds and LRU eviction to caches to prevent unbounded memory growth (#488)
- **Server**: Startup checks now fail closed on error (#487)
- **CLI**: Escaped `build_error` in dev-server HTML response to prevent XSS (#477)
- **Dep graph**: Parser mixin modules included in compiler fingerprint (#480)
- **Type annotations**: Added annotations to interpreter builtins and typechecker init (#453)
- **Packaging**: Built wheels now include `_js_runtime_support.js`, `packages.json`, and `geno/std/*.geno` runtime assets; publish workflow smoke-tests exercise JS compile, stdlib import, and package index from the installed wheel (#751)

### Security

- **XFF header validation**: Server validates `X-Forwarded-For` headers
- **Module size limits**: Import resolver enforces size caps on module sources
- **Sandbox**: Blocked `io.open()` file access via module proxy (#476)
- **Sandbox**: Reconciled `re` import policy between thread and process modes (#475)
- **Sandbox**: Rejected `__getattr__` override in process sandbox worker (#474)
- **HTTP builtins**: Restricted `http_fetch`/`http_post`/`http_request` to http/https schemes — blocks `file://`, `ftp://`, `data:` URIs (#473)
- **Dep graph**: Replaced unsafe `pickle.loads` with `RestrictedUnpickler` (#481)
- **Server**: Unauthenticated non-loopback connections require explicit opt-in (#446)
- **Project graph**: Enforced path containment for manifest files and dependency entrypoints (#478)

### Performance

- **Lexer**: Regex-based number and identifier scanning replaces character-by-character loops; inlined tokenize loop with local state variables (#296, #300)
- **Parser**: Precedence-climbing expression parser replaces 6 per-level methods; cached current-token fields updated by `_advance()`/`_match()` (#295, #299, #301)
- **Compiler**: `type(x) is T` fast dispatch for statement and expression compilation with `isinstance` slowpath fallback (#298)
- **Typechecker**: Frozen `_BuiltinCheckerState` dataclass caches builtin init state for reuse across `TypeChecker` instances (#302)
- **Dependency cache**: Pickle-based AST isolation with versioned cache protocol; corrupt payloads caught and invalidated (#297, #303)
- **Token layout**: `__slots__`-based `Token` and `SourceLocation` classes replace dataclasses for lower memory and faster construction (#303)

## [0.3.1] - 2026-04-07

### Added

- **Semantic rename/references**: Rename and find-references via symbol table (#214)
- **JS artifact tests**: Pytest coverage for source maps, `.d.ts`, and ESM (#227)
- **Benchmark evidence**: Complete benchmark evidence and performance claims (#223)
- **Release-gate templates**: CI validation script for release-gate templates (#224)
- **Build single-file tests**: Validate `build` single-file `.html` inference (#209)
- **geno-pipe CLI IO**: CLI file input for `geno-pipe` (#212)
- **REPL type completion**: Tests for REPL type completion (#228)
- **`cli_args` builtin**: Access command-line arguments from Geno programs
- **DateTime module**: Typed `DateTime` module for date/time operations
- **`http_respond` builtin**: HTTP response support for `serve` capability

### Changed

- Clarified `geno-form` is a canvas-based demo, not a full form framework (#210, #211)

## [0.3.0] - 2026-04-06

### Added

- **CSV/TOML parsing builtins** (always available):
  - `csv_parse(text)` — parse CSV text into `List[List[String]]`
  - `csv_parse_with_headers(text)` — parse CSV with headers into `List[Map[String, String]]`
  - `toml_parse(text)` — parse TOML text into `Result[JsonValue, String]`
- **Process execution builtins** (capability-gated, `--cap process`):
  - `exec(command)` — run a shell command, returns `Result[ProcessResult, String]`
  - `exec_with_input(command, stdin)` — run a command with piped stdin
  - `ProcessResult` built-in type with `exit_code: Int`, `stdout: String`, `stderr: String`
- **Package manager**: Git-based dependency management
  - `geno install` — install dependencies declared in `geno.toml`
  - `geno add <name> <url> [--branch]` — add a dependency and install it
  - `geno update [name]` — update one or all dependencies to latest commits
  - `geno.lock` lockfile for reproducible builds
  - `geno_modules/` directory for installed dependencies
  - Module resolver fallback: `import Foo` searches `geno_modules/Foo/` if `Foo.geno` not in same directory
- **LSP server**: Full Language Server Protocol support
  - `geno lsp` command (stdio by default, `--tcp --port` for TCP)
  - Diagnostics on open/change/save via `geno.check()`
  - Hover shows type information for builtins
  - Go-to-definition for functions and types
  - Completion for keywords, builtins, and user-defined names
  - VS Code extension updated to use `vscode-languageclient` with fallback to `execFile`
  - Optional dependency: `pip install geno-lang[lsp]` (requires pygls)
- **Bitwise operators**: `&` (AND), `^` (XOR), `<<` (left shift), `>>` (right shift), `~` (NOT), and `bit_or()` builtin for bitwise OR
- **Exponentiation operator**: `**` (right-associative), e.g., `2 ** 10` evaluates to `1024`
- **`else if` chaining**: Flat conditional chains without extra nesting
- **Match guards**: `| pattern when condition -> body` for conditional pattern matching
- **Index assignment**: `arr[0] = value` for `Array`, `Vec`, and `MutableMap` types
- **Field assignment**: `obj.field = value` for constructor values
- **Negative indexing**: `list[-1]` returns the last element, `-2` the second-to-last, etc.
- **Rest/spread patterns**: `[head, ...tail]` in list pattern matching
- **`range()` with step**: `range(start, end, step)` with optional step parameter (supports negative step)
- **New string builtins**: `replace(str, old, new)`, `ends_with(str, suffix)`, `to_upper(str)`
- **Traits and interfaces**: Polymorphic dispatch with `trait` / `impl` blocks
  - `trait Describable ... end trait` defines method signatures with `Self` type
  - `impl Describable for Circle ... end impl` provides concrete implementations
  - Dispatch works across all three backends (interpreter, compiled Python, compiled JS)
  - Multi-constructor ADT dispatch (e.g., `type Shape = Circle(...) | Square(...)`)
  - Duplicate impl detection at type-check time
- **Try/catch error handling**: `try ... catch e: String ... end try` for structured error recovery
  - Catch clause binds the error message as a `String`
  - Works in loops and can be nested
- **F-string interpolation**: `f"Hello {name}, you are {age} years old"` syntax
  - Arbitrary expressions inside `{...}` braces
  - Escape sequences supported in text portions
- **Multi-line block lambdas**: `fn(params) do ... end fn` for multi-statement lambdas
  - Supports `if`, `while`, `for`, `match`, and other statements in the lambda body
  - Return type inferred from `return` statements
- **? propagation operator**: `expr?` for early return on `None`/`Err`
  - In `Option` context: returns `None` from enclosing function if value is `None`, unwraps `Some`
  - In `Result` context: returns `Err` from enclosing function if value is `Err`, unwraps `Ok`
- **Type aliases**: `type Coordinate = (Int, Int)` and `type Predicate = (Int) -> Bool`
  - Generic aliases: `type Pair[T] = (T, T)`
  - Arity validation for generic aliases
- **Tuple destructuring**: `let (x, y): (Int, Int) = expr` in `let`/`var` bindings
- **Set[T] type**: Immutable set with structural equality
  - `set_new`, `set_from_list`, `set_add`, `set_remove`, `set_contains`, `set_size`, `set_to_list`, `set_union`, `set_intersection`
- **Range and sort builtins**:
  - `range(start, end)` — returns `List[Int]` from start (inclusive) to end (exclusive)
  - `sort(list, comparator)` — sort with custom comparator function
  - `sort_by(list, key_fn)` — sort by key extraction function
- **Regex builtins** (capability-gated, `--cap regex`):
  - `regex_match(pattern, text)` — returns `Option[String]` for first match
  - `regex_find_all(pattern, text)` — returns `List[String]` of all matches
  - `regex_replace(pattern, replacement, text)` — returns `String` with replacements
  - Pattern length limit (1000 chars) for ReDoS mitigation
- **Clock/date builtins** (capability-gated, `--cap clock`):
  - `clock_now()` — current Unix timestamp as `Int`
  - `clock_format(timestamp, fmt)` — format timestamp with `%Y`, `%m`, `%d`, `%H`, `%M`, `%S`, `%%` directives
  - `clock_parse(text, fmt)` — parse formatted date string to timestamp
  - `clock_elapsed(start, end)` — compute elapsed seconds between timestamps
- **Expanded file I/O** (capability-gated, `--cap fs`):
  - `fs_list_dir(path)` — returns `List[String]` of directory entries
  - `fs_exists(path)` — returns `Bool` for path existence check
- **App mode**: Graphics, input, and game loop support for interactive applications
  - `geno build` command compiles Geno apps to self-contained HTML files with canvas rendering
  - Graphics builtins: `draw_rect`, `draw_rect_outline`, `draw_circle`, `draw_line`, `draw_text`, `clear_screen`
  - Screen builtins: `screen_width`, `screen_height`
  - Input builtins: `is_key_down`, `is_key_pressed`
  - `with` expression for functional record updates (e.g., `player with (x: new_x)`)
- **Self-hosted frontend + interpreter**: Geno frontend and tree-walking interpreter written in Geno itself (`selfhost/` directory)
  - 8 modules: Tokens, Lexer, Ast, Parser, Types, TypeChecker, Interpreter, Main
  - Host-side parity/drift checks and CLI smoke tests cover the current surface
- **Mutable Array[T]**: Fixed-size mutable array type for interactive and performance-critical programs
  - `array_new`, `array_from_list`, `array_get`, `array_set`, `array_length`, `array_to_list`, `array_copy`, `array_fill`
- **Vec[T]**: Growable mutable vector type
  - `vec_new`, `vec_from_list`, `vec_push`, `vec_pop`, `vec_get`, `vec_set`, `vec_length`, `vec_to_list`
- **MutableMap[K, V]**: Mutable hash map type
  - `mutable_map_new`, `mutable_map_set`, `mutable_map_get`, `mutable_map_contains`, `mutable_map_delete`, `mutable_map_keys`, `mutable_map_size`
- String builtins: `char_code`, `from_char_code`
- File I/O builtins for self-hosting support (capability-gated)
- **Default parameter values**: `func f(x: Int, y: Int = 10) -> Int` — optional params with defaults
  - Required parameters must come before optional parameters
  - Arity check uses range: `min_required <= args <= total_params`
  - Works across all three backends (interpreter, compiled Python, compiled JS)
- **List comprehensions**: `[expr for var: Type in list if cond]` — concise list transformations
  - Optional filter clause with `if`
  - Compiles to native comprehensions in Python, `.filter().map()` in JS
- **Structured error types (`throw`)**: `throw expr` for throwing typed values
  - `throw "message"` throws a string (caught by `catch e: String`)
  - `throw MyError("msg")` throws a user-defined type (caught by `catch e: MyError`)
  - Catch clauses now accept user-defined ADT types in addition to `String`
  - Unmatched types propagate to outer try/catch blocks
- **Async/await**: `async func` and `await expr` for asynchronous programming
  - `Async[T]` type wraps asynchronous return values
  - Interpreter uses lazy evaluation (single-threaded); compilers emit real `async`/`await`
  - `await` is allowed in `async func` and in `main()`
  - Async functions are exempt from `example` clause requirements

### Fixed

- Fix four interpreter bugs: error halting, `float_to_int`, value equality, immutable maps
- Fix array for-loops, duplicate params, empty match, and runtime parity
- Fix `is_numeric_string` JS parity
- Fix 12 review findings across traits, regex, clock, try/catch, type alias, and f-string features
- Prevent path traversal in `bundle_project()` file loading
- Fix 33 ruff lint errors and 16 mypy type errors across core modules
- Fix test collection for experiment/tooling tests in environments without pyyaml

### Security

- Validate dependency names in package manager to prevent `geno_modules/` path traversal
- Block `asyncio` from sandbox import allowlist (exposes subprocess/networking)
- Add f-string collection size limits in compiled output to prevent resource exhaustion
- Restore `re` module to sandbox allowlist (regression from prior security fix broke `clock_parse`)

## [0.2.1] - 2026-03-31

### Added

- **JavaScript compiler backend**: `geno compile --target js` compiles Geno source to standalone JavaScript runnable with Node.js
  - New `JSCompiler` class and `compile_to_js()` API
  - JS runtime prelude with structural equality, deep copy, safety limits, and all builtin functions
  - 126-test suite including parity tests against all 6 example programs
  - Compile-time float division detection (`_float_div`) to work around JS's `Number.isInteger(7.0) === true`
  - Fail-fast on unsupported AST nodes, `_tag` field name collision detection

### Security

- **Sandbox escape fixes**: Block `str.format()` C-level attribute traversal, restrict `type()` to single-arg form, remove `types` and `operator` from safe import allowlist, wrap imported modules in proxy to block C-level escapes
- **Builtin hardening**: Remove dangerous builtins (`id`, `pow`, `object`) from both sandbox modes, fix `hasattr`/`getattr` policy inconsistency, unify `SAFE_DUNDERS` into a single constant
- **Resource exhaustion prevention**: Add collection size limits to multiply operator, enforce collection size limits in compiled code path, pre-check collection size before allocation to prevent OOM, cap timeout and `max_steps` on `/run` endpoint
- **Bounds checking**: Add bounds checking to `set_at` in runtime prelude, add bounds checking to compiled index access via `_safe_index`
- **Parser hardening**: Add nesting depth limit to prevent stack overflow DoS, add nesting depth check to `_parse_not_expr`
- **Input validation**: Reject negative `Content-Length` to prevent unbounded read DoS, reject user-defined names that shadow security-critical prelude functions, detect class definitions in static safety validator
- **Step budget**: Count steps for builtin function calls, count steps in `_values_equal` and `_match_pattern` to prevent budget bypass
- **Server hardening**: Catch unexpected exceptions in `/run` handler to prevent info leak

### Fixed

- Fix process sandbox silently swallowing empty-message exceptions
- Fix interpreter stdout leak, helper name shadowing, server capability enforcement, and example verification parity
- Fix compiler string escaping: escape `\r`, `\t`, and `\0` in generated Python
- Fix exported `safe_hasattr` to allow `SAFE_DUNDERS` like `safe_getattr`
- Mangle Python keywords in compiled output to prevent `SyntaxError`
- Sanitize special float values (`NaN`/`Infinity`) for valid JSON output

### Changed

- Benchmark expanded from 55 to 77 problems across 5 difficulty levels (trivial, easy, medium, hard, expert) with coverage for ADTs, pattern matching, pipelines, Option/Result types, contracts, and new domains (trees, graphs, linked structures, systems)

## [0.2.0] - 2026-02-06

### Added

- **Embedding API**:
  - `geno.run()` and `geno.check()` for programmatic use
  - `RunConfig` with configurable capabilities, timeouts, step budgets, and modules
  - `RunResult` and `CheckResult` with structured diagnostics and timing info
  - `value_to_json()` for serializing Geno values to JSON-compatible Python objects

- **Capability-Based Security**:
  - Capability gating for all I/O and non-deterministic builtins
  - `RunConfig(capabilities={...})` to grant specific capabilities
  - `RUNTIME_CAPABILITY_DENIED` (E412) raised when calling a gated builtin without the capability
  - Capability map: `print`, `clock`, `random`, `fs`, `http`

- **New Builtins**:
  - `clock_now()` — current Unix timestamp as Int (gated by `clock`)
  - `random_int(min, max)` — random integer in range (gated by `random`)
  - `random_float()` — random float in [0, 1) (gated by `random`)
  - `fs_read_text(path)` — read file via host callback (gated by `fs`)
  - `http_fetch(url)` — fetch URL via host callback (gated by `http`)

- **Host Callbacks**:
  - `RunConfig(host_callbacks={...})` for host-provided implementations of `fs_read_text` and `http_fetch`
  - `RUNTIME_HOST_CALLBACK_MISSING` (E413) raised when capability is granted but no callback is provided

- **Step Budgets**:
  - `RunConfig(max_steps=...)` to limit computation (default: 10,000)
  - `RUNTIME_STEP_LIMIT` (E503) raised when step budget is exceeded

- **Error Codes and Diagnostics**:
  - `ErrorCode` enum with structured codes for all error categories
  - `Diagnostic` dataclass with severity, code, message, and location
  - Error code ranges: E1xx (lexer), E2xx (parser), E3xx (type), E4xx (runtime), E5xx (limits)

- **Modules**:
  - `import ModuleName` statement for in-memory module resolution
  - `RunConfig(modules={...})` to provide module sources as a dictionary
  - Transitive module imports with circular import detection
  - `geno bundle` CLI command for packaging multi-file projects

- **Constrained Decoding**:
  - `import` keyword support in the constraints module
  - `allow_type_identifier` field on `AllowedNext` for module name completion

- **Hardening**:
  - Security regression corpus with 8 parametrized test cases
  - Reference Docker configuration (`Dockerfile`, `docker-compose.yml`)
  - Enhanced fuzzing: import fuzzing, capability combo fuzzing, host callback fuzzing
  - Constraint golden tests for `import` keyword
  - Expanded SECURITY.md with capability threat model, host callback trust model, module security, and agent execution guidance

### Changed

- Version bumped from 0.1.1a1 to 0.2.0

## [0.1.1a1] - 2025-01-18 (Alpha Release)

This is an **alpha release** intended for early adopters and research use. The language design may evolve based on LLM code generation experiments.

**What works:**
- Complete language implementation (lexer, parser, type checker, interpreter, compiler)
- Secure sandbox with ProcessSandbox (hard timeouts, output limits)
- 351 tests passing
- Benchmark suite with 55 problems

**What may change:**
- Language syntax and semantics (based on LLM evaluation results)
- Spec details (currently marked as draft)
- API surface for programmatic use

### Security

- **CRITICAL**: Added `safe_getattr` and `safe_hasattr` wrappers to block reflection-based sandbox escapes
- **CRITICAL**: Added static validation enforcement before code execution in strict mode
- **CRITICAL**: Fixed `compile_and_exec()` to set up sandbox BEFORE exec(), preventing initialization attacks
- **CRITICAL**: `run_sandboxed()` now uses `ProcessSandbox` by default for hard timeouts (DoS mitigation)
- **CRITICAL**: `geno run` CLI now uses ProcessSandbox by default for hard timeouts
- Added `ProcessSandbox` class for subprocess-based execution with hard timeouts
- Added `ProcessSandboxConfig` with configurable resource limits (memory, CPU, file size, strict mode, output length)
- Added `max_output_length` enforcement in ProcessSandbox to prevent memory exhaustion
- Added `use_process` parameter to `run_sandboxed()` to opt-out to thread-based sandbox
- Added `--unsafe` flag to `geno run` to use direct interpreter (no process isolation)
- Added `--timeout` flag to `geno run` to configure execution timeout (default: 30s)
- Added safe `__import__` that only allows whitelisted modules (dataclasses, typing, math, copy, functools)
- Fixed `get_field()` in compiler to reject underscore-prefixed field names
- Added `__build_class__` to safe builtins for class definition support (both sandboxes)
- Added `BLOCKED_ATTRIBUTES` frozenset with comprehensive list of dangerous reflection attributes

### Added

- **Interpreter builtins**:
  - `reverse(list)` - Reverse a list
  - `substring(str, start, end)` - Extract substring
  - `is_some(opt)` - Check if Option is Some
  - `is_none(opt)` - Check if Option is None
  - `unwrap(opt)` - Extract value from Some (raises on None)
  - `unwrap_or(opt, default)` - Extract value or return default
  - `float_to_int(f)` - Convert float to int
  - `int_to_float(i)` - Convert int to float

- **Testing**:
  - `test_security.py` - Comprehensive security escape tests
  - `test_cli.py` - CLI command tests (`geno run`, `geno check`, `geno compile`)
  - `test_fuzzing.py` - Property-based fuzzing tests using Hypothesis
  - Runtime security escape tests (`TestRuntimeSecurityEscapes`)
  - ProcessSandbox tests (`TestProcessSandbox`)

- **CI/CD**:
  - Coverage enforcement (fail if < 80%)
  - mypy type checking job
  - bandit security scanning job
  - pytest-timeout for hanging test prevention

- **Documentation**:
  - Threat model section in SECURITY.md
  - Known escape vectors (mitigated) documentation
  - Security architecture documentation
  - Contribution guidelines in README.md

### Changed

- **Spec alignment**:
  - Type conversion functions now documented as `to_string()`, `parse_int()` (matching implementation)
  - Added documentation for 11 extra builtins (`take_while`, `all`, `split_once`, `is_sorted`, etc.)
  - Added documentation for typed holes (`?name: Type`)
  - Added documentation for statement labels

- `parse_int()` now returns `Option[Int]` (Some/None) instead of raising on invalid input
- `SandboxContext.execute()` now enforces validation before execution
- `run_sandboxed()` now enforces validation before execution

### Removed

- **BREAKING**: Removed `ref` parameter documentation from spec (not implemented)
  - `ref` parameters for pass-by-reference were documented but never implemented
  - Implementing them properly would require significant work
  - Removed from spec to maintain spec/implementation alignment

### Fixed

- Fixed import stripping in `compile_and_exec()` that was breaking docstrings
- Fixed `test_getattr_blocked` regex to match actual error messages
- Fixed sandbox to allow `__build_class__` for class definitions

## [0.1.0] - 2024-12-01

### Added

- Initial release of Geno programming language
- Lexer with support for keywords, operators, and literals
- Recursive descent parser
- Static type checker with generics support
- Tree-walking interpreter
- Python code generator/compiler
- Interactive REPL
- Sandbox for safe code execution
- 55-problem benchmark suite
- Experiment framework for LLM evaluation
- Analysis tools and report generation

### Language Features

- Explicit block delimiters (`end func`, `end if`, `end while`)
- Mandatory type annotations
- Specification-first design with `example` clauses
- Immutability by default (`let` vs `var`)
- Named parameters for functions with 3+ parameters
- Exhaustive pattern matching
- Pipeline expressions (`|>`)
- Generic types (`List[T]`, `Option[T]`, `Result[T, E]`)
- Lambda expressions
- Contracts (`requires`, `ensures`)
