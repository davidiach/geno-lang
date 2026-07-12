# Execution Surface

This document defines the supported production execution surface for Geno.

## Safe Defaults

- `geno.run()` and `geno.check()` are the supported entry points.
- Hosted deployments should instrument `geno.run()` through `RunConfig(monitoring_hook=...)` or the helper collector in `geno.monitoring`.
- If `RunConfig.capabilities` is omitted, capability gating fails closed.
- Every gated builtin is denied unless its capability is present.
- `fs_read_text` and `http_fetch` require both the matching capability and a host callback.
- Module imports resolve from `RunConfig(modules=...)`, not from the filesystem.

## Pure Builtins

The following builtin families are part of the supported runtime surface and remain available even when `capabilities=set()`:

- List: `length`, `head`, `tail`, `append`, `concat`, `set_at`, `slice`, `filter`, `map`, `fold`, `contains`, `take_while`, `all`, `reverse`, `range`, `sort`, `sort_by`, `zip`, `enumerate`, `flat_map`
- Set: `set_new`, `set_from_list`, `set_add`, `set_remove`, `set_contains`, `set_size`, `set_to_list`, `set_union`, `set_intersection`
- String: `split`, `join`, `trim`, `to_lower`, `split_once`, `starts_with`, `to_chars`, `sort_strings`, `substring`, `contains_substring`, `repeat_string`, `to_upper`, `replace`, `ends_with`, `format`
- Math and conversions: `add`, `subtract`, `multiply`, `divide`, `sqrt`, `floor`, `ceil`, `round`, `max`, `clamp`, `abs`, `square`, `parse_int`, `parse_float`, `to_string`, `float_to_int`, `int_to_float`
- Predicates and helpers: `is_sorted`, `is_positive`, `is_numeric_string`, `is_permutation`
- Option: `is_some`, `is_none`, `unwrap`, `unwrap_or`
- Map: `map_insert`, `map_get`, `map_from_list`, `map_entries`, `map_from_entries`, `map_merge`, `map_filter_map`, `map_map_values`
- Array: `array_new`, `array_from_list`, `array_get`, `array_set`, `array_length`, `array_to_list`, `array_fill`, `array_copy`
- Vec: `vec_new`, `vec_from_list`, `vec_push`, `vec_pop`, `vec_get`, `vec_set`, `vec_length`, `vec_to_list`
- MutableMap: `mutable_map_new`, `mutable_map_set`, `mutable_map_get`, `mutable_map_contains`, `mutable_map_delete`, `mutable_map_size`, `mutable_map_keys`
- Char codes: `char_code`, `from_char_code`
- JSON/CSV/TOML: `json_parse`, `json_stringify`, `json_to_string`, `csv_parse`, `csv_parse_with_headers`, `toml_parse`

## Capability-Gated Builtins

- `print` requires `print`
- `clock_now`, `clock_format`, `clock_parse`, `clock_elapsed` require `clock`
- `random_int`, `random_float` require `random`
- `fs_read_text`, `fs_write_text`, `fs_list_dir`, `fs_exists` require `fs` (read/write also need host callbacks)
- `http_fetch`, `http_post`, `http_request` require `http` (fetch/post/request also need host callbacks)
- `regex_match`, `regex_find_all`, `regex_replace` require `regex`
- `env_get`, `env_get_or`, `cli_args` require `env`
- `exec`, `exec_with_input` require `process`
- `http_listen`, `http_route`, `http_respond` require `serve`

## Sandbox Constraints

Sandboxed Geno execution blocks ambient access to:

- filesystem I/O
- network I/O
- process and OS execution
- dangerous Python builtins such as `eval`, `exec`, and `compile`
- reflection attributes commonly used in sandbox escapes

Additional constraints:

- `RunConfig(timeout=...)` is enforced through the interpreter deadline path
- step budgets default to `RunConfig(max_steps=1_000_000)` and apply only to the
  interpreter / thread-sandbox path; the `ProcessSandbox` path bounds
  compiled Python via wall-clock, memory, collection-size,
  integer-bit, and recursion limits instead. Explicit `max_steps=None`
  disables the cooperative interpreter step budget
- `RunConfig(max_output_length=...)`,
  `RunConfig(max_collection_size=...)`,
  `RunConfig(max_integer_bits=...)`, and
  `RunConfig(max_recursion_depth=...)` apply to interpreter execution
- OS-backed process limits (`max_memory_bytes`, `max_cpu_time`,
  `max_file_size_bytes`, and `max_processes`) are not available through
  `RunConfig` because `geno.run()` is an in-process embedding API
- `SandboxConfig` can express every `ProcessSandboxConfig` resource limit
  for callers that use `run_sandboxed()` / compiled process execution:
  timeout, memory bytes, CPU time, file size, process count, output length,
  collection size, integer bits, recursion depth, and print allowance
- `max_integer_bits` and `max_collection_size` are forwarded to the
  `ProcessSandbox` worker so compiled code's `_safe_add`/`_safe_mul`
  and pre-allocation checks honor the configured values
- output is capped by sandbox configuration

## Supported Module Model

- imports are satisfied from the in-memory `modules` dictionary
- transitive modules must also be present in that dictionary
- circular imports are rejected
- Geno does not read module source from disk during execution

## Benchmark Python Import Allowlist

The benchmark runner has a restricted Python namespace for comparison work. Its supported import allowlist is intentionally small:

- `typing`

All other imports are rejected.

Raw Python benchmark execution is also opt-in at the API level:

- `BenchmarkRunner()` does not allow `evaluate_python()`
- use `BenchmarkRunner.for_research()` or pass `allow_unsafe_python_execution=True` only in controlled local research workflows

## Compilation Targets

| Target | Command | Output | Runtime |
|---|---|---|---|
| Python | `geno compile <file>` | Python source | Python 3.10-3.13 |
| JavaScript | `geno compile --target js <file>` | JavaScript source | Node.js |

The JS backend emits standalone JavaScript that includes the runtime prelude. It does not require any npm packages. The JS compilation path uses the same lexer, parser, and typechecker as the Python path; only the code generation differs.

**Note:** The JS backend does not include a sandbox. It is intended for trusted execution environments (e.g., local development, server-side Node.js with appropriate process isolation).

## Execution Entry Points -- Security Model

| Entry point | Trust level | Intended use |
|---|---|---|
| `geno.api.run()` | **Production embedding API** -- in-process interpreter with cooperative timeout and capability gating | Host-controlled embedding where the caller owns process isolation |
| `geno.server` (`POST /run`) | **Production hosted boundary** -- delegates to `geno.api.run()` in a child process with hard wall-clock timeout | HTTP API for untrusted hosted execution |
| `geno.server` (`POST /constrain`) | **Production** -- validates prefixes in a child process with hard wall-clock timeout and returns allowed-next-token guidance | HTTP API for hosted constrained decoding |
| `compile_and_exec()` | **Build-time / trusted** -- optional timeout, no process isolation | Tests, tooling, trusted callers only |
| `compile_to_js()` | **Build-time / trusted** -- no sandbox, no process isolation | JS code generation for trusted environments |
| REPL (`geno repl`) | **Local / trusted** -- 30 s UX timeout, no process isolation | Developer interactive use |
| Benchmark runner | **Research-only** -- restricted namespace, not a security boundary | Local evaluation of LLM-generated Python |

For untrusted code, use the hosted server boundary or wrap `geno.api.run()` in
caller-managed process isolation. Do not rely on the in-process embedding API
alone for killable wall-clock isolation.

Hosted `/run` and `/constrain` workers use `multiprocessing.get_context("spawn")`
and fail closed with `WorkerSpawnFailed` if a killable child process cannot be
created. There is no supported thread-timeout fallback for hosted untrusted
execution.

## Production Boundary

The production security boundary is the Geno runtime and sandbox. The benchmark runner's raw Python executor is not a supported production sandbox for untrusted Python and should only be used for local research and evaluation workflows.

For hosted services, the deploy-facing health and metrics contract is also part of the supported surface:

- `RunConfig(monitoring_hook=...)` receives a `RunMetrics` payload on every `geno.run()` completion
- `RuntimeMetricsCollector.snapshot()` returns a stable JSON-serializable metrics snapshot for hosted `geno.run()` results and `/constrain` evaluations
- `RuntimeMetricsCollector.health_report()` returns a stable JSON-serializable health payload
- `MetricsSnapshot.to_prometheus_text()` exposes the same counters in text form for Prometheus-style scraping
