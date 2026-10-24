# Proposal 0011: Host Names Are Geno Names, and Limits That Run Ordinary Programs

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
- Roadmap: milestone M8 of [proposal 0003](0003-geno-0.6-language-upgrade.md)

## Summary

A Geno program means the same thing on every backend whatever its host
language calls its own builtins, and ordinary recursive programs run under the
default limits on every lane:

```geno
type Fault = Error(msg: String) | Exception(code: Int)

func len(xs: List[Int]) -> Int
    example [1, 2] -> 2
    return length(xs)
end func

func count(n: Int) -> Int
    example 3 -> 3
    if n == 0 then
        return 0
    end if
    return 1 + count(n - 1)
end func

func main() -> Unit
    let list = [1, 2, 3]
    print(len(list))
    print(Error("disk full"))
    print(count(9000))
end func
```

Before this change the Python compiler rejected `len`, `list` and `Exception`,
the JavaScript compiler rejected `console`, `fetch` and `require`, and
`count(9000)` failed on every Python lane.

## Motivation

- Programs were rejected for names that mean nothing in Geno: a parameter
  `len`, a local `list`, a constructor `Error` or `Warning`, a function
  `fetch`. The set differed between backends, so a program that ran with
  `--target js` failed under `geno run`, and models were told to avoid "any
  Python builtin" (`docs/llm-prompting.md`), a list nobody can apply.
- A top-level function with a builtin's name diverged across backends. At the
  previous commit, 28 of 235 builtins (`max`, `filter`, `map`, `round`,
  `set_add`, ...) defined as user functions compiled on one backend and were
  rejected on the other; in JavaScript `func max` silently replaced the
  runtime's own `max_`.
- The interpreter stopped at call depth 500 and 1,000,000 steps: `fib(24)` ran
  out of steps, and recursion 1,000 deep failed. The compiled lanes stopped
  near 3,000 (sandbox) or 1,000 (standalone script).
- Building or walking a linked structure was quadratic: the interpreter
  re-walked a whole value at every call to enforce collection limits, and the
  compiled runtime's identity checks scanned lists. Building a 9,000-node list
  took over 30 seconds.

## Reference Design

### Host names

Each compiler renames an identifier that would collide with a name its target
or runtime relies on, at every definition and every use:

| Collides with | Emitted as | Examples |
| --- | --- | --- |
| a target keyword | `name_kw` | `class`, `with` (already the rule) |
| a host builtin, intrinsic or runtime helper | `name_g` | `len`, `list`, `Warning`, `console`, `fetch`, `get_field`, `print_` |
| a builtin, when defined at top level of a single-file program | `name_g` | `func max`, `let min = 10` |

Values print and match by their Geno names: a renamed constructor class is
given its Geno name through the runtime helper `_geno_named_constructor`
(compiled Python; the process sandbox forbids `__name__` assignments in user
code), and JavaScript values carry their Geno name in `_tag` already.

Names that begin with `_` and that the runtime uses (`_safe_add`,
`_GENO_CAPS`, `_require_cap`) stay reserved and are rejected, as before:
they name runtime internals and security checks, not things a program means.

A program may not spell both a name and its emitted form: `len` and `len_g`
together are a compile error naming both. Names of the Geno runtime itself
(builtins, and the types and constructors of builtin ADTs such as `Some`) are
never renamed, since a reference to one must reach the runtime.

`geno/host_names.py` holds the shared definitions; each backend's
`_target_name` computes its own renaming and `_mangle_name` adds the
top-level builtin shadows. Modules of a project compile into their own
scopes, so only single-file programs need the shadow renaming. The example
harness looks functions up through `compiled_python_name`.

### Default limits

| Limit | Before | Now |
| --- | --- | --- |
| Geno call depth (`max_recursion_depth`, `--max-recursion-depth`) | 500 | 10,000 |
| Interpreter steps (`max_steps`, `--max-steps`) | 1,000,000 | 20,000,000 |
| Standalone compiled Python | Python's default (about 1,000 frames) | the default call depth |

The hosted server keeps its own, smaller limits: `GENO_MAX_RECURSION_DEPTH`
(default 500) joins `GENO_DEFAULT_MAX_STEPS`.

Every lane runs a depth-10,000 program without crashing the host process:

- **Python 3.12 and later:** Python-to-Python calls use no C stack and C-level
  recursion has its own limit, so runs stay on the caller's thread.
- **Before 3.12:** a Python frame can also spend up to about 1 KiB of C stack.
  An 8 MiB stack segfaults near 8,000 frames on 3.10, and on 3.11 when
  recursing through C slots such as `__eq__`. So:
  - The interpreter (`geno.run`, `--unsafe`, `--json`, `geno test`, the REPL)
    and standalone compiled scripts run on a thread whose stack holds the
    needed frames (`execution_limits.call_with_recursion_room`).
  - The process sandbox worker cannot start threads (`RLIMIT_NPROC`). On Linux
    it raises its soft `RLIMIT_STACK`; elsewhere it keeps the recursion limit
    within its stack.

If no such thread can start, the run falls back to the old limit, so it fails
with a recursion error rather than a crash.

`geno test` now runs examples and test blocks under the same recursion room as
`main`; before, they ran at Python's default limit.

### Linear-time limit checks

The interpreter enforces `max_collection_size` and `max_integer_bits` on every
value a call passes or returns. Once everything a constructor value reaches
is known to fit, the value is marked (`_limits_checked`) and not walked again.
The mark is set only when no mutable container (`Array`, `Vec`, `Set`,
`MutableMap`) is reachable, since those grow in place, and a field assignment,
the one way a constructor value changes, clears it. The compiled
runtime's collection check and value copy keep their identity tables in
dictionaries keyed by the default identity hash instead of scanning lists; `id`
stays out of the sandbox, and the hash is never shown to the program.

## Backend And Target Parity

Each case runs on the interpreter, compiled Python, compiled JavaScript, the
process-sandboxed `geno run` lane and a two-module project
(`geno/tests/test_host_names.py`). A sweep that defines each of the 235
builtins as a user function finds no backend difference.

Compiled JavaScript keeps the JavaScript engine's own stack depth (about
10,000 frames in Node), which the conformance case stays within.

## Compatibility And Migration

Relaxing: every program accepted before is accepted with the same meaning.
Programs that were rejected for a host name now compile, and recursive or
long-running programs that hit the old limits now finish. A program that
relied on the old limits to stop runaway recursion early now runs up to 20
times deeper first. Hosts that want the old bounds can still configure them.

Generated code changes shape: renamed definitions are visible in compiled
output, and an ES module exports a renamed function or constructor under its
emitted name, as it already did for keyword-renamed functions.

## Security And Resource Limits

- The reserved runtime internals stay unreachable: an underscore-prefixed
  runtime name is rejected as a definition or reference exactly as before, and
  a renamed user name can no longer replace a runtime binding. Before this
  change a JavaScript program's `func max` did replace the runtime's `max_`.
- The hosted server's call-depth limit is unchanged and stays fixed for
  clients.
- The sandbox worker keeps `RLIMIT_AS`, which also bounds the stack it grows,
  and keeps `RLIMIT_NPROC`. It runs no thread, and raising the soft stack limit
  only lets the main thread's stack grow within the address-space budget.
- The constructor-value mark records the limits it was checked against, so a
  value checked under one configuration is walked again under a stricter one.

## Testing And Validation

- `geno/tests/test_host_names.py` covers renamed functions, locals, parameters,
  record fields, lambda parameters, comprehension and pattern bindings,
  constructors, trait methods, top-level builtin shadows, two-module projects,
  the sandboxed `geno run` lane, the collision error and the still-reserved
  internals.
- Compiler, JavaScript compiler, module-constant, multi-module, target
  validation, language-surface and LSP tests changed from asserting rejection
  to asserting renaming and correct output.
- Recursion was exercised on Python 3.10, 3.11, 3.12 and 3.13 across `geno run`,
  `--unsafe`, standalone scripts and the embedding API.
- `conformance/v0.6` adds `host-names` and `deep-recursion` on the
  interpreter, Python and JavaScript targets.

## Rollout And Observability

Ships in the 0.6 development line with the rest of
[proposal 0003](0003-geno-0.6-language-upgrade.md). Nothing is configured to
turn it on. A program whose compiled output a person reads will show the
`_g` names, and `geno run --max-recursion-depth` and `--max-steps` still
override the defaults.

## Alternatives

- **Keep rejecting host names, but list them.** The list differs by backend
  and grows with every runtime helper; programs written for one backend still
  fail on another.
- **Rename every identifier** (prefix all user names). Uniform, but makes all
  generated code and stack traces harder to read, and breaks the readable
  output that `geno compile` users rely on.
- **Drop Python 3.10 and 3.11 support** (3.10 reached end of life this month).
  This would remove the deep-stack thread, but 3.11 is supported until 2027,
  and it would be a policy change outside this milestone.
- **Keep the walk, raise only the limits.** Deep structures would still be
  quadratic and time out before reaching the new depth.

## Unresolved Questions

None blocking. The known limits below are candidates for later changes.

## Known Limits

- On Python 3.12 and later, structural equality and hashing of a value nested
  more than a few thousand levels deep stop with a recursion error, because
  CPython's C recursion limit applies to the comparison. Making equality
  iterative is left to a later change.
- Binding a value with `let` copies it in compiled Python (value semantics),
  which costs time linear in its size per binding.
