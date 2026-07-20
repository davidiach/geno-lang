# Embedding API

Geno can be embedded in Python applications as a library. Parse, type-check,
and execute Geno programs from Python with full control over capabilities,
timeouts, and host-provided builtins.

## Quick Start

```python
import geno

result = geno.run("""
func main() -> Int
    return 42
end func
""")

print(result.ok)      # True
print(result.value)   # 42
```


`geno.run()` is value-oriented even when `main()` returns an `Int`: the value
is stored in `RunResult` and is never converted into a process exit or
`SystemExit`. Process-status translation is reserved for `geno run` and
standalone generated artifacts.

## Core Functions

### `geno.run(source, filename="<api>", config=None) -> RunResult`

Parse, type-check, and execute a Geno program.

```python
from geno import RunConfig

result = geno.run(source, config=RunConfig(
    timeout=10.0,
    capabilities={"print", "fs"},
))
```

### `geno.run_path(path, config=None) -> RunResult`

Resolve a Geno file or project from disk, then execute it. Discovers the
project graph and dependency modules automatically.

```python
result = geno.run_path("my_project/Main.geno", config=RunConfig(
    capabilities={"print", "http"},
))
```

### `geno.check(source, filename="<api>", modules=None) -> CheckResult`

Parse and type-check Geno source code without executing it.

```python
result = geno.check(source)
if not result.ok:
    for d in result.diagnostics:
        print(f"{d.code.value}: {d.message}")
```

### `geno.check_path(path, modules=None) -> CheckResult`

Resolve a Geno file or project from disk, then type-check it.

```python
result = geno.check_path("my_project/Main.geno")
```

### `geno.constrain_prefix(prefix) -> ConstraintResult`

Compute next-token constraints for a partial Geno source prefix. Used by LLM
runtimes to guide code generation.

```python
result = geno.constrain_prefix("func main() -> ")
print(result.valid)          # True
print(result.unclosed_blocks) # ("func",)
```

## Configuration: RunConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timeout` | `float \| None` | `5.0` | Cooperative execution deadline. `None` = no limit. |
| `max_steps` | `int \| None` | `1_000_000` | Maximum interpreter steps. Explicit `None` disables the cooperative step limit. |
| `max_recursion_depth` | `int` | `500` | Maximum call stack depth. |
| `max_output_length` | `int` | `100_000` | Maximum characters of print output. |
| `max_collection_size` | `int` | `10_000_000` | Maximum size for strings and collections. |
| `max_integer_bits` | `int` | `33_219` | Maximum bit length for integer arithmetic results. |
| `capabilities` | `set[str] \| None` | `None` | Capability grants. Omitted/`None` = deny all gated builtins. |
| `host_callbacks` | `dict[str, Callable] \| None` | `None` | Host-provided builtin implementations. |
| `modules` | `dict[str, str] \| None` | `None` | In-memory module sources for `import` resolution. |
| `check_examples` | `bool` | `True` | Verify example clauses at runtime. |
| `monitoring_hook` | `Callable \| None` | `None` | Receives `RunMetrics` after every run. |

The JavaScript backend has the tighter portable `Int` range
`-(2^53 - 1)` through `2^53 - 1`; `max_integer_bits` governs interpreter and
compiled Python execution. See [Portable Runtime Semantics](runtime-semantics.md).

The timeout, step budget, and deadline checks are cooperative. A long-running
builtin cannot be forcibly interrupted while it holds control. Never use this
API as the sole isolation boundary for untrusted code.

`RunConfig` uses the in-process interpreter. OS-backed process limits
(`max_memory_bytes`, `max_cpu_time`, `max_file_size_bytes`, and
`max_processes`) are intentionally unavailable in this API because they cannot
be enforced inside the caller's process. Use `geno.sandbox.ProcessSandboxConfig`
with `run_in_process()` or `SandboxConfig` with `run_sandboxed()` when you need
those process-isolation limits.

## Result Types

### RunResult

```python
@dataclass
class RunResult:
    ok: bool                      # True if execution succeeded
    value: Any                    # Return value of main() (JSON-serializable)
    value_raw: Any                # Raw Python value before conversion
    output: str                   # Captured print() output
    diagnostics: list[Diagnostic] # Structured error/warning list
    timing: Timing                # Per-phase timing breakdown
    steps_used: int               # Interpreter steps consumed
```

### CheckResult

```python
@dataclass
class CheckResult:
    ok: bool                      # True if type-checking passed
    diagnostics: list[Diagnostic] # Structured error/warning list
    timing: Timing                # Per-phase timing breakdown
```

### ConstraintResult

```python
@dataclass
class ConstraintResult:
    allowed_next: AllowedNext     # Valid next tokens
    valid: bool                   # Whether the prefix is valid so far
    error: str | None             # Error message if invalid
    unclosed_blocks: tuple[str, ...] # Open block types
```

### Timing

```python
@dataclass
class Timing:
    total_ms: float    # Total wall-clock time
    lex_ms: float      # Lexing phase
    parse_ms: float    # Parsing phase
    typecheck_ms: float # Type-checking phase
    run_ms: float      # Execution phase
```

### Diagnostic

```python
@dataclass
class Diagnostic:
    code: ErrorCode       # Machine-readable error code (e.g., E300)
    message: str          # Human-readable description
    severity: Severity    # ERROR, WARNING, or INFO
    location: SourceLocation | None
```

## Host Callbacks

Provide custom implementations for capability-gated builtins:

```python
def my_fs_read(path):
    # Custom file reading logic
    return open(path).read()

result = geno.run(source, config=RunConfig(
    capabilities={"fs"},
    host_callbacks={
        "fs_read_text": my_fs_read,
    },
))
```

The same explicit-callback rule applies to `fs_metadata`,
`fs_symlink_metadata`, and `fs_canonicalize`; granting `fs` never installs
ambient filesystem access in `geno.run()`. Metadata callbacks return the usual
interpreter `Result` and built-in ADT values (`ConstructorValue` instances), so
embedders retain full control over path policy and reported metadata.

Host callbacks run as trusted code inside the interpreter. They are **not**
subject to the execution timeout. Implement your own timeouts for I/O
operations.

## Multi-Module Programs

Provide module sources in-memory for `import` resolution:

```python
math_module = """
func double(x: Int) -> Int
    example 3 -> 6
    return x * 2
end func
"""

main_source = """
import Math

func main() -> Int
    return Math.double(21)
end func
"""

result = geno.run(main_source, config=RunConfig(
    modules={"Math": math_module},
))
print(result.value)  # 42
```

## Error Handling

Check `result.ok` and inspect `result.diagnostics` for structured errors:

```python
result = geno.run(source)
if not result.ok:
    for d in result.diagnostics:
        loc = f"{d.location.line}:{d.location.column}" if d.location else "?"
        print(f"[{d.code.value}] {loc}: {d.message}")
```

Error codes follow the pattern `E<phase><number>`:
- **E1xx**: Lexer errors (unexpected character, unterminated string)
- **E2xx**: Parse errors (unexpected token, invalid syntax)
- **E3xx**: Type errors (mismatch, undefined variable, wrong arity)
- **E4xx**: Runtime errors (division by zero, index out of bounds, capability denied)
- **E5xx**: Sandbox errors (timeout, recursion limit, resource limit)
