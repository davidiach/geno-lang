# Proposal 0000: Capability Grants in the Isolated `geno run` Lane

- Status: Draft
- Authors: Claude Code (AI-assisted draft for maintainer review)
- Created: 2026-10-16
- Discussion: https://github.com/davidiach/geno-lang/issues/68
- Supersedes: None

## Summary

`geno run --cap <list> FILE` should run in the default process-isolated lane
with exactly the listed capabilities, instead of exiting with an error. Today a
user who needs any capability beyond `print`, `clock` and `random` must leave
that lane. `--json` grants capabilities but provides no host access for `fs`,
`http`, `process`, `stdin` or `serve`, so in practice that means `--unsafe`.
That flag gives up the hard wall-clock kill,
the memory, CPU, file-size and process limits, and crash isolation.

The lane gains capabilities in three steps:

1. `regex`, `env` and `stdin`. These need no new host access from the worker:
   the regex engine is already loaded there, and the parent supplies the
   environment, program arguments and stdin.
2. `fs`, scoped to the same roots as `geno run --unsafe --cap fs`.
3. `http`, under the same scheme, address, redirect and size policy that
   other Python surfaces enforce.

`process` and `serve` stay unavailable in the isolated lane. Asking for either
fails before launch with a message that names `--unsafe`.

The worker binds host access only for granted capabilities. It does this in its
trusted setup, through private names that generated program code cannot
reference. The capability check in each builtin stays as the first gate; a
missing binding is a second, independent one.

The change is additive: no source program changes meaning, and commands that
exit 1 today start working. It targets 0.6.0, because
[proposal 0002](0002-0.5-stable-promotion.md) deferred [#68](https://github.com/davidiach/geno-lang/issues/68) beyond 0.5.0.

## Motivation

### The default lane cannot grant anything

The isolated lane grants `print`, `clock` and `random`, and rejects `--cap`
outright:

```text
$ geno run --cap env main.geno -- hello
Error: --cap is not supported in the default process-isolated run mode. Use
--unsafe --cap ... to run with capability callbacks in the direct interpreter,
or use --json --cap ... for embedding API execution.
```

Issue [#68](https://github.com/davidiach/geno-lang/issues/68) reports the result from upgrading ten flagship apps. A real CLI entry
point needs `cli_args()`, which needs `env`. The only working command is
`geno run --unsafe --cap env,print`. Apps therefore adopted a split: a pure
`run(args)`, a capability-free demo `main`, and an `@untested cli_main` that
`geno run` cannot select ([#80](https://github.com/davidiach/geno-lang/issues/80)).

### The CLI now recommends leaving the sandbox

Since [#187](https://github.com/davidiach/geno-lang/pull/187), every capability denial in the default lane ends with a hint, and
the hint recommends `--unsafe`:

```text
Runtime Error: Capability denied: 'cli_args' requires the 'env' capability
Hint: the default 'geno run' lane grants only clock, print, random and does not accept --cap.
  To grant 'env', rerun without process isolation (trusted code only):
  geno run --unsafe --cap clock,env,print,random main.geno -- hello
```

It says the same for `regex`. The regex engine already runs inside the worker,
so a regex program loses process isolation to gain nothing.

The repository's own guidance follows the hint. Outside the tests, 35
occurrences in 18 files tell readers to run `--unsafe --cap`. The files are app
READMEs, guides, the reference docs, the website and blog posts, the changelog,
and the CLI's own messages.

| Capability | Mentions |
|---|---|
| `print` | 17 |
| `fs` | 16 |
| `env` | 10 |
| `serve` | 4 |
| `http`, `clock`, `random` | 3 each |
| `process` | 1 |

`fs` and `env` are what real programs need. The website's capability section
says the default lane refuses `--cap` "so a grant can never quietly switch off
process isolation". That principle is right, but the lane enforces it by
sending users to a mode with less isolation.

### The suggested `--json` route does not cover host capabilities

The error above offers `--json --cap` as the other option. That works for
`env`, but the embedding API has no host callbacks for `fs`, `http`, `process`,
`stdin` or `serve`:

```text
$ geno run --json --cap fs,print readf.geno
... "Host callback not provided for 'fs_read_text'. Provide a callback via RunConfig.host_callbacks."
```

### Forwarding the grant alone is not enough

The worker already accepts a capability set (`runtime_capabilities`, read into
`_GENO_CAPS`), so the obvious fix is to pass the user's grant through. It fails.
The compiled runtime reaches the host through `import os`,
`import subprocess`, `import socket` and `open`. The worker's import gate and
restricted builtins block every one of these, by design. With the grant
forwarded through the existing `compile_and_exec(..., capabilities=...)` path,
all three of `cli_args()`, `env_get()` and `fs_read_text()` fail the same way:

```text
RuntimeError: Import of 'os' is not allowed
```

The lane needs a deliberate way to give trusted runtime code host access,
limited to what was granted.

## Guide-Level Design

```text
$ geno run --cap env,print main.geno -- hello world
hello,world

$ geno run --cap fs,print report.geno -- data/input.csv
...

$ geno run --cap print,regex lint.geno
```

The rules a user sees:

- **No `--cap`: unchanged.** The program gets `print`, `clock` and `random`.
- **With `--cap`, the grant is exactly the list.** This matches `--unsafe`,
  `--json`, `geno serve` and standalone compiled output. `--cap env` alone
  denies `print`, and the denial hint names the full flag to use.
- **Capabilities available in the isolated lane:** `print`, `clock`, `random`,
  `regex`, `env`, `stdin`, `fs` and `http`, with the last three added in later
  phases.
- **`process` and `serve` are refused before the program starts:**

  ```text
  Error: --cap process is not available in the process-isolated run mode:
  programs it starts would run outside the sandbox. Use --unsafe --cap ...
  for trusted code.
  ```

- **The denial hint keeps the program isolated** whenever the missing
  capability is available in the lane:

  ```text
  Runtime Error: Capability denied: 'cli_args' requires the 'env' capability
  Hint: grant it with --cap; the program stays process-isolated:
    geno run --cap clock,env,print,random main.geno -- hello
  ```

  The hint mentions `--unsafe` only for `process` and `serve`.
- **`fs` has the same scope as `--unsafe --cap fs`:** the working directory,
  the source file's directory, the project root, and any path-like arguments
  after `--`.
- **The lane stays batch-oriented.** Output still appears when the program
  ends, as it does today. When `stdin` is granted, stdin is read to end of
  file before the program starts.

## Reference Design

### Parsing and validation in the parent

- `run_file` accepts `capabilities` in process mode and drops the blanket
  rejection.
  - `geno/__main__.py` already validates names with
    `normalize_capability_values`.
  - A grant containing `process` or `serve` exits 1 with the message above,
    before the worker starts.
- `--max-file-size-bytes` gets a "lane default" (argparse default `None`):
  - 0 when `fs` is not granted, which is today's value;
  - 64 MiB when it is (see [Filesystem](#filesystem-fs)).

  An explicit value always wins, including `0`, which makes every write fail.
- `--cap` help text drops "Requires --unsafe or --json".

### Trust channel

Three kinds of input reach the worker, and none of them comes from the program:

- **Grant and policy flags** travel in the worker configuration the parent sets
  (`GENO_SANDBOX_CONFIG`). That is the grant set, read-only mode and the
  private-address switch. The worker reads them at startup, before any
  untrusted input. Reading the grant early lets the worker preload the host
  modules for granted capabilities before it freezes the Darwin address-space
  baseline (see [HTTP](#http-http)).
- **Data the program may read** travels in the request the parent writes. The
  environment snapshot and the stdin text are sent only when `env` or `stdin`
  is granted.
- **The program arguments are always sent.** They already reach the worker
  today through `GENO_CLI_ARGS`, and trusted setup needs them to compute the
  `fs` roots even when `env` is not granted, as in
  `geno run --cap fs,print main.geno -- /data/input.csv`. Only the
  program-visible binding is gated: `cli_args` still requires `env`.
- **The `fs` roots** are computed by trusted code in the worker, before the
  program runs, from the file name, the resolved project root and the program
  arguments. This is how `--unsafe` computes them (see
  [Filesystem](#filesystem-fs)).

The worker validates the grant before use. Any name outside the lane's
supported set, `process` and `serve` included, is a `startup_error`, so
nothing runs. `_prepare_process_run` stops hard-coding
`DEFAULT_ALLOWED_CAPABILITIES` and returns the configured grant. A run without
`--cap` gets the defaults exactly as today.

### Host bindings

Trusted worker setup builds one private table, called `_GENO_HOST` here, and
injects it into the compiled program's globals before the runtime prelude runs.
It holds only what the granted capabilities need:

| Capability | Binding in the worker | Supplied by |
|---|---|---|
| `print`, `clock`, `random`, `regex` | none new; existing injected modules | worker (today) |
| `env` | environment snapshot (read-only mapping) and program arguments | parent |
| `stdin` | stdin text, bounded | parent |
| `fs` | filesystem facade plus root and write policy | worker imports; policy from parent and the resolved project |
| `http` | client modules plus address policy | worker preloads; policy from parent |

The runtime prelude (`geno/_runtime_support.py`) reaches the host only through
two private helpers:

- `_geno_host_module(name)` returns the bound object.
- `_geno_environ()` returns the environment.

Standalone compiled output has no `_GENO_HOST`. There the helpers import for
real, so standalone behaviour and startup cost do not change. In the worker,
a name missing from the table raises at once, and nothing falls back to an
import.

Every gated builtin therefore passes two independent gates:

1. `_require_cap` checks the grant, as today, and still sets the denial tag
   that [#187](https://github.com/davidiach/geno-lang/pull/187) relies on.
2. The host object exists only when the capability was granted. A gating bug
   in one builtin cannot reach `os` or `socket` in a run without `fs` or
   `http`.

Generated program code cannot name the table or the helpers:

- They join `_WORKER_COMPILED_INTERNAL_NAMES`, which the worker's validator
  rejects in the generated tail. This runs on every request, whatever the
  parent's strict setting.
- The compiler derives its reserved runtime names from the prelude, so a Geno
  function, binding or reference that uses one of these names fails to
  compile.

Bindings expose the narrowest object the builtins need. For `fs`, that means
a facade with the path functions, `open`, `listdir`, `stat` and `lstat`, not
the `os` module with `system`, `fork` and `kill`. HTTP needs the standard
client modules whole and binds them only under `http`.

### Environment and arguments (`env`)

- The parent snapshots its environment before it sets `GENO_CLI_ARGS`. It
  sends the snapshot only when `env` is granted.
- `env_get` and `env_get_or` read the snapshot. `cli_args` returns the program
  arguments from the request.
- The worker's own process environment does not change. It keeps the sandbox
  configuration variables, the temporary `HOME` and the `-I` interpreter mode,
  so a variable in the caller's environment cannot alter how the worker sets
  itself up.
- A program sees the caller's real `HOME`, `PATH` and so on, as it would under
  `--unsafe`. It never sees the worker's substitutes.

### Standard input (`stdin`)

- When `stdin` is granted, the parent reads its stdin to end of file before
  launching the worker. It sends the text as a separate frame after the
  request. Without the grant, the parent never touches stdin.
- The read is bounded. It stops one character past the collection limit, so
  `stdin_read_all` reports the same size error as other surfaces.
- Invalid UTF-8 returns `Err`, as it does today.
- Reading ahead is consistent with the lane's buffered output. An interactive
  program waits for end of file before it starts; the docs must say so.

### Filesystem (`fs`)

- **Roots.** The worker computes the roots in `_prepare_process_run`, which is
  trusted code that runs before the program. It calls
  `_explicit_fs_roots_for_run(filename, project_root, program_args)`, the
  function `--unsafe` uses, with absolute paths allowed inside those roots.
- **Read-only mode.** The parent reads `GENO_FS_READ_ONLY` and forwards it as
  policy; the worker does not inherit the variable.
- **Shared policy code.** Symlink escapes and the metadata, canonicalize and
  read-only rules go through the prelude's existing policy code, which
  standalone output already runs.
- **Write limit.** `RLIMIT_FSIZE` defaults to 64 MiB per file when `fs` is
  granted. That covers the largest string the runtime can produce: 10,000,000
  characters at up to 4 UTF-8 bytes each, about 40 MB. Windows has no per-file
  limit, as today.
- **Over-limit writes.** A write past the limit must fail with a Geno message
  such as `fs_write_text: file exceeds the sandbox file-size limit (67108864
  bytes)`. The prototype surfaced a raw `OSError: [Errno 27] File too large`.

### HTTP (`http`)

- **Policy.** The prelude's existing policy applies:
  - only the `http` and `https` schemes;
  - loopback, private, link-local and similar targets rejected;
  - redirect targets checked against the same rules;
  - bounded response bodies.

  The parent forwards `GENO_HTTP_ALLOW_PRIVATE` as policy.
- **Preloading.** The worker imports the client modules in trusted setup, only
  when `http` is granted:
  - On Linux, `socket`, `ssl`, `http.client` and `urllib.request` took about
    49 ms and added 7.5 MiB of resident memory.
  - Importing them before the Darwin baseline is frozen keeps that cost out of
    the program's memory budget.
- **Timeouts.** The hard wall-clock limit also stops a request that is stuck
  in a blocking call. In-process lanes cannot preempt such a call.
- **No threads.** The client must not start threads: `RLIMIT_NPROC` stays at
  1, and on Linux that limit counts threads.

### Why `process` and `serve` stay out

- **`process`:** programs it starts run outside the Python-level sandbox and
  carry only the rlimits. On Linux and macOS, `RLIMIT_NPROC` counts every
  process the user owns, so it cannot express "this run may start N children".
  Granting it would leave "isolated" meaning little.
- **`serve`:** a listener runs until it is stopped. The lane's default
  30-second wall clock and buffered output would kill the server and hold back
  its logs.

Both stay under `--unsafe`. Supporting them in an isolated mode needs its own
proposal, for example with an explicit `--timeout none` and streamed output.

### Example checking

Before `main`, the worker checks examples in the interpreter with the default
grant set and no host callbacks. This proposal keeps that behaviour. An
example that calls a granted host builtin fails here but passes under
`--unsafe`, which runs examples with the grant. See the unresolved questions.

### Diagnostics

- The process-mode `--cap` rejection is removed for supported capabilities and
  replaced by the narrower `process`/`serve` message.
- `_default_lane_capability_hint` suggests `geno run --cap ...` for supported
  capabilities and keeps `--unsafe` advice for the other two.
  - It still takes the capability only from the worker's structured denial tag
    (`ProcessSandbox.last_capability_denied`).
  - The protection [#187](https://github.com/davidiach/geno-lang/pull/187) added against forged messages stays.
- E412, its wording and the call-site location are unchanged.

## Backend And Target Parity

Language semantics do not change: the same builtins, capability names, effects
and E412 error. The interpreter (`--unsafe`, `--json`), standalone Python,
Node, browser and hosted surfaces keep their current behaviour. `--target`
still affects checking only; the isolated lane always executes compiled
Python.

After this change the isolated lane matches `--unsafe --cap` on everything
the runtime policy decides:
- the `fs` roots, read-only mode and absolute-path rule;
- the `http` policy;
- the environment the program sees;
- the program arguments.

Differences that remain, all documented:

| Topic | Isolated lane | `--unsafe` |
|---|---|---|
| Output | shown when the program ends | streamed |
| `stdin` | read to EOF before start | read on call |
| Examples | default grant, no host access | run with the grant |
| Hard limits | wall clock (kill), memory, CPU, file size, processes | none; cooperative timeout and step limit |
| `process`, `serve` | refused | available |

## Compatibility And Migration

- **Source:** no change. No frozen conformance case covers CLI run modes, and
  none changes.
- **CLI behaviour:** additive. A command that exited 1 with "`--cap` is not
  supported" now runs. Nothing that runs today changes behaviour. The
  `--max-file-size-bytes` default changes only when `fs` is granted, which
  could not happen before.
- **Diagnostics:** the rejection message and the hint text change. The
  compatibility policy does not treat wording as stable. Tests that pin the
  [#187](https://github.com/davidiach/geno-lang/pull/187) hint are updated.
- **Docs:** each phase replaces `--unsafe --cap` with `--cap` for the
  capabilities it adds. That covers app READMEs, guides, reference docs and the
  website, including the capability section, Figure 8-1 and the fail-closed
  post. Apps that need `serve` or `process` keep `--unsafe`. Before and after:

  ```text
  # before
  geno run --unsafe --cap env,print main.geno -- hello
  # after
  geno run --cap env,print main.geno -- hello
  ```

- **Release:** 0.6.0, recorded in the changelog under Added. Proposal 0002
  froze 0.5.0 against new CLI behaviour, and this change makes no breaking
  change that would need the pre-1.0 minor-release exception.

## Security And Resource Limits

**What isolation means today.** The lane combines three things:
- a Python-level sandbox: restricted builtins, an import allowlist and
  validation of generated code;
- operating-system resource limits;
- a hard kill.

There is no OS-level filesystem or network confinement. The worker runs as the
invoking user. Without grants, the Python-level sandbox is what keeps a program
away from files and sockets. A Python-level escape already means full access
as that user, with or without grants.

**What grants change.** Granting `fs` or `http` gives the program real access,
within the prelude's policy, from inside a process that still carries every
resource limit. It does not weaken OS confinement, because there is none. It
widens the trusted runtime surface that a program can drive. The mitigations
are:

- grants come only from the parent-set configuration and are validated before
  untrusted input;
- the capability check and the binding's absence are independent gates;
- generated code cannot name the bindings, which both the compiler and the
  worker enforce, and tests assert;
- facades expose only what the builtins use;
- tests check that gated builtins return Geno values, never host objects.

**Comparison with the status quo.** Users grant these capabilities today with
`--unsafe`, so those runs have none of the hard limits. For every run that uses
`fs`, `env`, `stdin`, `http` or `regex`, this proposal is a strict improvement
in isolation.

**Environment exposure.** Granting `env` exposes every environment variable,
secrets included, exactly as `--unsafe --cap env` does. Combined with `http`,
that enables exfiltration. The capability docs must say so. The hosted server
already requires an allowlist for `env`; a CLI allowlist is an unresolved
question.

**Denial of service.** Every limit stays in force: wall clock, memory, CPU,
output, collection size, integer size and recursion. On top of that:
- `fs` allows writes up to the per-file limit, but the number of files is
  bounded only by the timeout;
- `http` uses the network until the timeout;
- the `stdin` read-ahead is bounded.

**Fail-closed properties.** These are kept:
- no `--cap` behaves as today;
- unknown names are rejected;
- `process` and `serve` are refused;
- a bad grant or policy in the worker is a startup error, so nothing runs.

**Supply chain.** No new dependencies.

## Testing And Validation

- **Parent:** grant validation, `process`/`serve` refusal and the
  `--max-file-size-bytes` defaults. Also that, without `--cap`, the default
  grant is unchanged.
- **Worker:**
  - an unsupported or malformed grant in the config is a startup error;
  - each host binding is absent when its capability is not granted;
  - generated code that names `_GENO_HOST` or a helper is rejected;
  - Geno code that defines or references those names fails to compile.

  A prototype confirmed the last two with the existing mechanisms.
- **Per capability**, in the isolated lane:
  - `regex`;
  - `env`: `cli_args`, `env_get` returning the caller's value, and the
    caller's `HOME` rather than the sandbox's;
  - `stdin`: UTF-8, invalid UTF-8 and over-limit input;
  - `fs`: read, write, list, metadata and canonicalize; `..` and symlink
    escapes; absolute paths inside and outside the roots; a path argument
    after `--` authorizing its directory under an `fs`-only grant, while
    `cli_args` is still denied; `GENO_FS_READ_ONLY`; the over-limit write
    message;
  - `http`: a local test server with `GENO_HTTP_ALLOW_PRIVATE`, scheme and
    private-address rejection, redirects and size limits.
- **Parity:** the same programs under `--cap` and `--unsafe --cap` produce
  the same output and the same errors.
- **Diagnostics:** the [#187](https://github.com/davidiach/geno-lang/pull/187) tests in
  `geno/tests/test_capability_denial_diagnostics.py` are updated for the new
  hint and still prove a forged message gets no hint.
- **Security corpus:** new `# EXPECT` entries run in the isolated lane: a path
  escape, an absolute path outside the roots, and a grant without `fs` that
  calls a filesystem builtin.
- **Platforms:** Linux, macOS (the Darwin baseline with the `http` preload)
  and Windows (Job Object limits, no per-file limit).
- **Gates:** `make security`, `python3 scripts/local_ci.py release`, the docs
  snippet tests and the website build tests for the updated figures.

### Prototype evidence

A throwaway prototype, not proposed for merge, implemented `env` and `fs`
this way. It changed about 85 lines across `geno/cli/run.py`, `geno/sandbox.py`
and `geno/_runtime_support.py`, using raw module bindings rather than facades.
In the isolated lane:

- `--cap env,print` returned the program arguments and the caller's
  environment.
- `--cap fs,print` read and wrote inside the roots and refused `..` and
  absolute escapes.
- `--cap print,regex` matched.
- `--cap process` was still refused.

The worker rejected generated code that named any of the three private names,
and the compiler rejected Geno definitions and references using them. With
warm bytecode caches, a granted `env` or `fs` run took the same time as an
ungranted one, about 0.5 s on the test machine. `--unsafe` took about 0.17 s,
and that gap is the existing cost of the worker.

## Rollout And Observability

Three implementation pull requests, each independently revertible. Reverting
one restores the fail-closed rejection for its capabilities.

1. **Plumbing, `regex`, `env` and `stdin`:**
   - the config channel, `_GENO_HOST` and the helpers;
   - the `process`/`serve` refusal and the new hint;
   - docs and website text for these capabilities.

   This step fixes the case [#68](https://github.com/davidiach/geno-lang/issues/68) reports.
2. **`fs`:** the facade, the root policy, the file-size default and the write
   error message.
3. **`http`:** the preload and the policy forwarding.

No feature flag is needed, because the behaviour only applies when `--cap` is
passed. Each PR adds a changelog entry under Unreleased.

Follow-ups outside this proposal:
- an issue for host callbacks in `--json` mode;
- making `compile_and_exec(capabilities=..., timeout=...)` honour host
  capabilities, or reject them, instead of failing with an import error;
- [#67](https://github.com/davidiach/geno-lang/issues/67), whose README and guide text can then show the isolated command;
- [#80](https://github.com/davidiach/geno-lang/issues/80).

## Alternatives

1. **Do nothing and document `--unsafe`.** This keeps steering users out of
   the hard limits, and it contradicts the fail-closed message on the website.
2. **Ungate `cli_args`, or give it its own `args` capability.** This would fix
   the most common case without host access. It changes the capability
   manifest for every backend and spec §9.11, and it does nothing for `fs`. It
   can still be decided separately (see below).
3. **Run the interpreter with `geno/_serve.py` host callbacks inside the
   worker** whenever `--cap` is given. This reuses the `--unsafe`
   implementation, but it puts two execution engines behind one lane, runs
   slower, and departs from the compiled semantics the lane documents.
4. **Have the parent perform host operations for the worker over a channel.**
   Without OS-level confinement of the worker, this adds a protocol and
   latency but no protection. It becomes worthwhile together with alternative
   5.
5. **Add OS-level confinement:** Landlock or seccomp on Linux,
   `sandbox-exec` on macOS, AppContainer on Windows. This would confine the
   worker to the granted roots and no network. It is the strongest option, but
   platform-specific. It complements this proposal rather than being a
   prerequisite.
6. **Make `--cap` additive in the default lane**, so that `--cap env` means
   the defaults plus `env`. This is friendlier for one-capability runs, but the
   same flag would mean different things in different lanes.
7. **Let `--cap` switch to `--unsafe` automatically.** This is the silent
   downgrade the current rejection exists to prevent.

## Unresolved Questions

1. **Facades or raw modules for `fs`?** The proposal asks for a facade. Is the
   extra code worth it, compared with raw `os` behind both gates?
2. **Write limits.** Is 64 MiB per file the right default? Should there also
   be a total-bytes-written budget?
3. **Examples.** Should example checking in this lane receive the run's grant,
   with host access before `main`, to match `--unsafe`?
4. **Standard input.** Should `stdin` be read ahead, as proposed, or passed as
   an inherited handle so programs read it lazily? The handle approach is more
   complex on Windows.
5. **Environment scope.** Should the CLI offer a way to narrow `env`, such as
   `--env NAME` or a prefix allowlist, as the hosted server requires?
6. **`cli_args`.** Should it move out of `env` (alternative 2)? That decision
   affects every lane and the specification, so it should be a separate one.
7. **Independent review.** `GOVERNANCE.md` requires an independent technical
   review for a security-boundary proposal, which stays open for at least 14
   days. Who reviews it? See [#28](https://github.com/davidiach/geno-lang/issues/28).
