# Proposal 0009: One Error Model — Result, `?`, and `panic`

- Status: Accepted
- Accepted: 2026-10-24
- Accepted by: David Iach, project lead
- Authors: David Iach (direction), Claude Code (drafting and implementation)
- Created: 2026-10-23
- Discussion: https://github.com/davidiach/geno-lang/pull/212
- Supersedes: None
- Review: No independent review (single-maintainer acceptance under
  `GOVERNANCE.md`). The implementation was reviewed by the project lead and by
  Codex automated review on #212, and by CI.
- Roadmap: milestone M6 of [proposal 0003](0003-geno-0.6-language-upgrade.md)

## Summary

Geno 0.6 has one way to report a failure a caller can handle — return a
`Result` — and one way to stop on a bug — `panic`. `throw`, `try`, `catch` and
the `throw` effect are removed.

```geno
func load(path: String) -> Result[Config, String] with fs
    let text = fs_read_text(path)?
    return parse_config(text)
end func

func main() -> Result[Unit, String] with fs, io
    let config = load("app.toml")?
    print(config.name)
    return Ok(())
end func
```

## Motivation

Geno 0.5 has two error models that do not compose:

- `Result` and `?` carry expected failures in the type, as in Rust, Swift and
  Gleam.
- `throw` and `try`/`catch` carry any `String` or user type out of band, and a
  `catch e: String` also catches runtime errors: division by zero, an index
  out of range, `unwrap` on `None`.

The second model is the source of the open soundness bugs #193, #196 and #197,
each a case where a typed `catch` and the runtime disagree about what was
thrown. It also lets a program catch a bug and keep running on corrupt state,
and it makes every effectful builtin's failure invisible in its type: 0.5's
`fs_read_text` returns `String`, so nothing tells a caller (or a model writing
the caller) that it can fail.

A model writing Geno also has to choose between the two models for every
fallible function, and the choice leaks into every caller. One model removes
the choice.

## Guide-Level Design

A function that can fail in a way its caller should handle returns
`Result[T, E]`. The caller handles it with `match`, passes it on with `?`, or
uses a combinator (`map`, `map_err`, `and_then`, `unwrap_or`):

```geno
match fs_read_text(path) with
    | Ok(text) -> print(text)
    | Err(message) -> print("cannot read " + path + ": " + message)
end match
```

A situation the program cannot recover from is a bug, and `panic` says so:

```geno
func ratio(part: Int, whole: Int) -> Float
    requires whole != 0
    example 1, 4 -> 0.25
    return int_to_float(part) / int_to_float(whole)
end func

let first = match xs.first() with
    | Some(x) -> x
    | None -> panic("xs is never empty here")
end match
```

A panic, and every runtime error (index out of range, division by zero, a
failed contract, `unwrap` on `None`, an exceeded resource limit), ends the
program. Geno code cannot observe or recover from it; the host reports it with
the same diagnostic and nonzero status as an uncaught runtime error in 0.5.

`r.unwrap()` and `r.expect("why")` turn an `Err` into a panic when a failure
is a bug, as `opt.unwrap()` and `opt.expect("why")` do for `None`.

Accessors that cannot fail return `Option`: `xs.get(i)`, `xs.first()`,
`xs.last()`. Indexing `xs[i]` stays the panicking form.

## Reference Design

### Removed forms

| 0.5 | 0.6 diagnostic (E205) |
| --- | --- |
| `throw e` | `'throw' was removed in Geno 0.6. Return Err(...) from a function that returns Result, or call panic("...") for a bug` |
| `try ... catch e: T ... end try` | `'try'/'catch' was removed in Geno 0.6. Call a function that returns Result and handle Err with match, or pass it on with ?` |
| `with throw` | `The 'throw' effect was removed in Geno 0.6; delete it from the effect list` |

`throw`, `try` and `catch` remain reserved words so that 0.5 code gets exactly
one error naming its replacement. E205 (`PARSE_REMOVED_SYNTAX`) is new.

Parser recovery after an error no longer stops at the `func` of `end func`,
which in 0.5 reported a spurious "Expected function name" after the first
error in a function and skipped the next definition.

### `panic`

```text
panic(message: String) -> Never
```

`panic` is an always-available builtin with no effect. Its result type is
`Never`, so a call fits wherever a value is expected, and a statement that is
a call of type `Never` ends its path for return-path analysis. The runtime
failure carries exactly `message`; the interpreter reports it with E414
(`RUNTIME_PANIC`). An uncaught `throw "text"` in 0.5 and `panic("text")` in
0.6 produce the same diagnostic text.

### Total accessors and explicit unwrapping

| Builtin | Type | Method form |
| --- | --- | --- |
| `list_get` | `(List[T], Int) -> Option[T]` | `xs.get(i)` |
| `list_first` | `(List[T]) -> Option[T]` | `xs.first()` |
| `list_last` | `(List[T]) -> Option[T]` | `xs.last()` |
| `result_unwrap` | `(Result[T, E]) -> T` | `r.unwrap()` |
| `result_expect` | `(Result[T, E], String) -> T` | `r.expect(message)` |
| `option_expect` | `(Option[T], String) -> T` | `opt.expect(message)` |

`xs.get(i)` is `Some(xs[i])` exactly when `xs[i]` succeeds, including a
negative index counting from the end. `result_unwrap` panics with
`unwrap called on Err: <error>`, `result_expect` with `<message>: <error>`,
and `option_expect` with `<message>`.

### Fallible host builtins return `Result`

| Builtin | 0.5 | 0.6 |
| --- | --- | --- |
| `fs_read_text` | `(String) -> String` | `(String) -> Result[String, String]` |
| `fs_write_text` | `(String, String) -> Unit` | `(String, String) -> Result[Unit, String]` |
| `http_fetch` | `(String) -> String` | `(String) -> Result[String, String]` |
| `http_post` | `(String, String) -> String` | `(String, String) -> Result[String, String]` |

This matches the builtins that already returned `Result` in 0.5
(`fs_list_dir`, `fs_metadata`, `http_request`, `exec`, `stdin_read_all`).
I/O failures, path-policy denials, and scheme, transport, redirect-policy and
network-policy failures are `Err`. Capability denial, a read-only filesystem
policy on `fs_write_text`, and an exceeded output limit remain failures of the
program, as for every other builtin.

Embedding hosts that supply these four builtins through
`RunConfig.host_callbacks` keep the 0.5 callback contract: the callback
returns the value and raises on failure. The API wraps a returned value as
`Ok`, an `OSError`, `ValueError` or `RuntimeError` as `Err(str(error))`, and
passes an `Ok`/`Err` value through unchanged.

### `main` returning `Result`

`main() -> Result[T, E]` is an executable entrypoint form. `Ok(v)` is handled
as a `main` returning `v: T` would be (`Unit` is silent with status 0, `Int`
sets the status). `Err(e)` writes `Error: <e>` to standard error, using the
display form of `e` (a `String` without quotes), and exits with status 1. So
`?` works at the top level of a program. Embedding APIs return the raw
`Result` value, as they return every other result unnormalized.

## Backend And Target Parity

The interpreter, compiled Python and compiled JavaScript implement every new
builtin and signature, and the same program produces the same output and the
same panic message on all three. Error messages that come from the host
operating system (`fs_read_text` on a missing file) differ by host, as they do
for `fs_list_dir` in 0.5.

## Compatibility And Migration

Breaking, under the pre-1.0 policy, in the 0.6 series:

| 0.5 | 0.6 |
| --- | --- |
| `throw "message"` (never caught) | `panic("message")` |
| `throw MyError(...)` caught by `catch e: MyError` | return `Err(MyError(...))` from a function returning `Result[T, MyError]` |
| `try ... catch e: String ... end try` around a runtime error | check first (`xs.get(i)`, `if d != 0 then`), or let it panic |
| `func f() -> T with throw` | `func f() -> T` or `-> Result[T, E]` |
| `let text = fs_read_text(p)` | `let text = fs_read_text(p)?` in a function returning `Result`, or a `match` |

`geno migrate` (proposal 0010) rewrites the mechanical cases: an uncaught
`throw "message"` becomes `panic("message")`, `throw` disappears from effect
lists, and a call of one of the four builtins whose `String` result is used
directly gets `.unwrap()`, preserving 0.5 behavior. A `try`/`catch` needs a
person or a model to choose the `Result` shape and is reported, not rewritten.

`selfhost/Main.geno`, the only checked-in 0.5 program that used `try`/`catch`,
and the reference applications are migrated in the same change.

## Security And Resource Limits

Removing `catch` closes a class of sandbox probes: a 0.5 program could catch a
runtime error, a contract failure or a limit error raised inside a builtin and
inspect its message. The security corpus entries for those probes are
replaced by `try_catch_removed.geno` (rejected with E205) and by `Result`
versions of the step-metering bombs (`err_value_accumulation_bomb.geno`,
`err_chain_bomb.geno`, `fold_result_amplification_bomb.geno`,
`result_step_evasion_bomb.geno`), which still hit E503.

Policy denials for the four I/O builtins become `Err` values, so a program can
see that a path or address was refused, as it already could through
`fs_list_dir` and `http_request`. A program still cannot forge a capability
denial: `panic("Capability denied: ...")` carries no provenance tag, and the
CLI's hint ignores it (covered by `test_capability_denial_diagnostics.py`).

## Testing And Validation

- `geno/tests/test_error_model.py`: removed forms and their single E205
  diagnostic, `panic` typing and return-path analysis, panic messages on
  every backend, total accessors, `unwrap`/`expect`, `Result` I/O builtins in
  the interpreter host and both runtimes.
- `geno/tests/test_host_policies.py`: policy denials as `Err`, host callback
  wrapping.
- `geno/tests/test_result_main.py`: `main() -> Result` classification (through
  aliases, with and without type checking, `async main`) and its status,
  stdout and stderr on every `geno run` lane, `--json`, the embedding API,
  compiled Python and compiled Node.
- `conformance/v0.6`: the `error-model` run case and the
  `diagnostic-removed-throw`, `-try-catch` and `-throw-effect` cases (E205),
  and `result-main-ok` / `result-main-err` across every executable lane.
- Evaluation-order tests that used `throw`/`catch` to see which operand ran
  first now use `panic` and the failure message.

## Alternatives

- **Keep `try`/`catch` for `Result`-like sugar.** Rejected: two models that a
  model must choose between is the problem being solved.
- **Make panics catchable at an `example` boundary** (`example 0 -> panics`).
  Deferred: useful for testing `requires` clauses, and it does not need a
  language-level catch. Tracked as an open question in proposal 0003.
- **Checked exceptions (`throws` in signatures).** Equivalent to `Result` in
  safety but adds a second propagation mechanism beside `?`.
