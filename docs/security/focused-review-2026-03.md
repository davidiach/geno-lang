# Focused Security Review - March 5, 2026

## Scope

Reviewed files:

- `geno/sandbox.py`
- `geno/api.py`
- `geno/interpreter.py`

Related consistency check:

- `benchmark/runner.py`

## Method

- Re-reviewed capability gating and host callback installation paths.
- Re-reviewed timeout and execution deadline behavior.
- Re-reviewed blocked builtin, blocked module, and blocked attribute handling.
- Checked the benchmark runner for policy drift relative to the main sandbox.

## Findings

Resolved during this pass:

- The benchmark runner now reuses the main sandbox validation and safe attribute wrappers instead of maintaining a weaker partial copy.
- Newly added pure builtins are now explicitly preserved by capability gating when `RunConfig(capabilities=set())` is used.

## Post-Review Fixes (April 2026)

An adversarial follow-up review identified five issues that were fixed in v0.2.1:

1. **Module proxy leaked non-allowlisted modules** via public attributes
   (e.g. `typing.sys` → raw `sys` module). Fixed by blocking non-allowlisted
   modules instead of wrapping them.
2. **`typing.ForwardRef._evaluate()` reached real `eval()`** inside the sandbox.
   Fixed by blocking `ForwardRef` and `get_type_hints` at the module proxy level,
   plus AST validator defense-in-depth for private attribute access.
3. **Capability model was fail-open** when `RunConfig.capabilities` was omitted
   and `host_callbacks` were provided. Fixed: `_allowed_gated_builtins(None)`
   now returns an empty set, and `_install_host_callbacks` requires explicit
   capability opt-in.
4. **`create_handler(allowed_capabilities=set())`** silently restored defaults
   due to falsey empty-set check. Fixed with explicit `None` check.
5. **`compile_to_html()` title injection** — unescaped title parameter. Fixed
   with `html.escape()`.

## Residual Risk

- The Geno runtime and sandbox are the production security boundary.
- The benchmark runner's raw Python execution path remains a developer-only convenience and is not a supported sandbox for untrusted Python.
- Host callbacks remain part of the host application's trust boundary and must validate their own inputs and outputs.
- The `_MODULE_BLOCKED_ATTRIBUTES` blocklist for `typing` is a named blocklist, not an allowlist. Future stdlib changes could introduce new bypass paths through allowlisted modules.

## Review Outcome

For the reviewed production runtime files, the current release has:

- fail-closed capability behavior (verified by regression tests)
- no known host callback bypass through denied capabilities
- module proxy that blocks non-allowlisted modules and dangerous typing constructs
- cooperative timeout behavior that does not return before the executing call chain completes
- explicit documentation of supported and unsupported surfaces
