# Proposal 0013: Capability Grants in the Isolated `geno run` Lane

- Status: Accepted
- Accepted: 2026-10-24
- Accepted by: David Iach, project lead
- Review: No independent human technical review; Codex automated advisory reviews and focused documentation validation (details below)
- Authors: Claude Code and Codex (AI-assisted draft for maintainer review)
- Created: 2026-10-16
- Updated: 2026-10-24
- Target release: 0.7.0 or later (tentative; next feature cycle after 0.6)
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

The proposed rollout separates five milestones:

1. Grant selection and `regex`. The regex engine is already loaded in the
   worker, so this first step needs no new host bindings.
2. `env` and program arguments, with an empty-by-default exact-name environment
   allowlist and the same grants for examples and `main`. This step addresses
   [#68](https://github.com/davidiach/geno-lang/issues/68).
3. `stdin`, read lazily inside the supervised worker under the run's deadline.
4. `fs`, through a narrow facade with the existing roots and read-only policy,
   plus portable per-file and aggregate write limits.
5. `http`, preserving the existing validated connection, redirect, proxy and
   response-size protections under the remaining run deadline.

Each milestone needs its own implementation and validation; unsupported
capabilities remain refused until their milestone lands.

`process` and `serve` stay unavailable in the isolated lane. Asking for either
fails before launch with a message that names `--unsafe`.

The worker binds host access only for granted capabilities. It does this in its
trusted setup, through private names that generated program code cannot
reference. The capability check in each builtin stays as the first gate; a
missing binding is a second, independent one.

The proposal adds capability selection without changing language syntax or
builtin names. Example checking will share the run's grants and cumulative
budgets; the compatibility implications are recorded below. Implementation
targets the next feature cycle
after 0.6, provisionally 0.7.0 or later, subject to implementation validation.
This is not a delivery commitment or an addition to the current 0.6 release
candidate. [Proposal 0002](0002-0.5-stable-promotion.md) originally deferred
[#68](https://github.com/davidiach/geno-lang/issues/68) beyond 0.5.0.

### Acceptance record (2026-10-24)

The problem remains on `main` at `4e70283` (0.6.0-rc.3):
[`run_file` still rejects every explicit `--cap` in process mode](https://github.com/davidiach/geno-lang/blob/4e70283e04f1521cc1164a270e2b8a9d63ba30e7/geno/cli/run.py#L619).
David Iach, project lead, formally accepted this proposal on 2026-10-24 and
authorized its merge once all required checks pass. Acceptance is recorded
under the existing sole-maintainer provision in `GOVERNANCE.md`: no
independent human technical review took place. The review actually received
is documented below; automated review and CI are not independent human approval.

The accepted design resolves the environment, example, stdin, filesystem and
HTTP policy questions. It does not enable any capability. Platform mechanics
and executable evidence remain gates for the corresponding implementation
milestones. This acceptance does not waive security, parity, supported-platform
or release validation and does not commit to a shipping date.

The filesystem-only argument-forwarding review finding is already addressed:
trusted worker setup receives program arguments independently of the
program-visible `env` grant. Merge this accepted design record separately from
implementation after the required checks pass. Merging this document does not
close #68.

### Recorded design decisions (2026-10-24)

1. **Environment:** require `env` plus an exact-name allowlist for variable
   reads. The allowlist defaults to empty; `cli_args()` needs only `env`.
   Send only allowed caller values as a snapshot, keep worker setup separate,
   and preserve the distinction between denied and allowed-but-missing names.
2. **Examples:** use exactly the run's grants and host restrictions before
   `main`, with one overall deadline and shared cumulative resource budgets.
   Authorized I/O is real, including consumption of the same stdin stream.
   Preserve `--no-check-examples` and recommend pure helpers with supplied data
   plus separate integration tests for effectful wrappers.
3. **Stdin:** read lazily through a dedicated input handle inside the supervised
   worker. The parent does not read stdin to EOF before supervision. Reads are
   incremental and size-bounded, preserve UTF-8 error behavior, and share the
   deadline covering startup, examples and `main`. There is no rewind or replay.
4. **Filesystem boundary:** expose only a narrow facade and route every
   operation through the existing root, symlink and read-only policy. Do not
   inject raw `os` or unrestricted file access.
5. **Writes:** default to 64 MiB per file and 64 MiB cumulatively per run,
   configurable and enforced in the facade on Windows and POSIX. Count actual
   UTF-8 bytes after the existing platform newline conversion across examples
   and `main`, including rewrites. Reserve a write's cost
   before opening or truncating a file; do not refund failed attempts after
   reservation. These are conservative policy defaults, not measured needs.
6. **HTTP:** reuse the existing validated connection and redirect machinery,
   including destination checks, cross-origin credential stripping, proxy
   bypass protection and bounded responses. Private-network access requires
   trusted host opt-in. Keep that policy separate from program-visible
   environment access and bound requests by the remaining run deadline.

The detailed contracts and required regression cases are specified below.
Acceptance records the design direction, not completed implementation,
cross-platform validation or independent human technical review.

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

The repository's own guidance follows the hint. At draft creation on
2026-10-16, 35 occurrences in 18 files outside the tests told readers to run
`--unsafe --cap`. This historical count has not been rerun for the refresh.
The files were app
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

- **No `--cap`: default grant unchanged.** The program gets `print`, `clock`
  and `random`.
- **With `--cap`, the grant is exactly the list.** This matches `--unsafe`,
  `--json`, `geno serve` and standalone compiled output. `--cap env` alone
  denies `print`, and the denial hint names the full flag to use.
- **Capabilities proposed for the isolated lane:** `print`, `clock`, `random`,
  `regex`, `env`, `stdin`, `fs` and `http`. The defaults are available today;
  the others follow the five milestones above. A capability remains refused
  until its implementation lands, and the CLI must name that limitation.
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

  During rollout, this hint applies only to capabilities already implemented
  in the isolated lane. Unsupported capabilities retain explicit alternative-mode
  advice. After all five milestones, only `process` and `serve` need that advice.
- **`fs` has the same roots as `--unsafe --cap fs`:** the working directory,
  the source file's directory, the project root, and any path-like arguments
  after `--`. Isolated writes additionally default to 64 MiB per file and
  64 MiB cumulatively per run, shared by examples and `main`. Rewrites consume
  the budget again; the limits apply on Windows as well as POSIX.
- **Environment reads need an allowlist.** `--cap env` enables `cli_args()`
  without exposing environment variables. The proposed repeatable option
  `--allow-env NAME` permits individual variables; it never grants `env`
  implicitly. For example, `geno run --cap env,print --allow-env HOME main.geno`
  permits `HOME`, while other variable reads are denied.
- **Examples share the run's grants and restrictions.** They execute before
  `main` and may perform authorized I/O. The deadline and cumulative resource
  budgets cover the whole run. `--no-check-examples` remains available.
- **Stdin is read only on demand.** Granting `stdin` without calling
  `stdin_read_all()` consumes nothing. Examples and `main` share one stream:
  data consumed by an example is unavailable to `main`.
- **The lane stays batch-oriented.** Output still appears when the program
  ends. Lazy input does not change `stdin_read_all()`'s contract: a call still
  waits for EOF, subject to size bounds and the overall run deadline.

## Reference Design

### Parsing and validation in the parent

- `run_file` accepts `capabilities` in process mode and replaces the blanket
  rejection with validation against the capabilities implemented so far.
  - `geno/__main__.py` already validates names with
    `normalize_capability_values`.
  - A grant containing `process` or `serve` exits 1 with the message above,
    before the worker starts.
- `--max-file-size-bytes` gets a "lane default" (argparse default `None`):
  - 0 when `fs` is not granted, which is today's value;
  - 64 MiB (67,108,864 bytes) when it is.
- Add process-only `--max-total-write-bytes` with the same lane defaults:
  0 without `fs`, or 64 MiB with it. Validate both limits as nonnegative
  integers. Explicit values override defaults but never grant `fs` or bypass
  read-only policy. A zero value for either limit disables all facade write
  operations, including empty writes or truncation.
- The per-file limit applies through the portable facade as well as any
  available OS file-size limit; the aggregate counter belongs to the whole
  run. See [Filesystem](#filesystem-fs).
- `--cap` help text lists the capabilities currently supported in process mode
  and identifies those that still require another execution mode.
- The `env` milestone adds repeatable `--allow-env NAME` for exact variable
  names, defaulting to an empty set. No wildcard or prefix grants are added.
  The option requires an explicit `env` grant in isolated mode; invalid
  combinations fail before launch rather than granting access implicitly.
  This proposal does not add the option to `--unsafe` or `--json`.

### Trust channel

Trusted setup receives these inputs from the parent; the Geno program cannot
select its own grant, policy or inherited handles:

- **Grant and policy flags** travel in the worker configuration the parent sets
  (`GENO_SANDBOX_CONFIG`). That includes the grant set, environment allowlist,
  read-only mode, both write limits and the private-address switch. The worker
  validates these fields at startup, before any untrusted input. Reading the grant early lets the worker preload the host
  modules for granted capabilities before it freezes the Darwin address-space
  baseline (see [HTTP](#http-http)).
- **Environment data** travels in the request only when `env` is granted.
  The parent snapshots only allowed, present variables. The separate allowlist
  lets the worker distinguish a denied name from an allowed-but-missing one.
- **Stdin data** is not buffered into the request. Only when `stdin` is granted,
  trusted setup receives a dedicated input handle separate from the worker's
  control protocol. The parent does not consume input, and the worker reads
  only when a permitted builtin call requests it, under supervision.
- **The program arguments are always sent.** They already reach the worker
  today through `GENO_CLI_ARGS`, and trusted setup needs them to compute the
  `fs` roots even when `env` is not granted, as in
  `geno run --cap fs,print main.geno -- /data/input.csv`. Only the
  program-visible binding is gated: `cli_args` still requires `env`.
- **The `fs` roots** are computed by trusted code in the worker, before the
  program runs, from the file name, the resolved project root and the program
  arguments. This is how `--unsafe` computes them (see
  [Filesystem](#filesystem-fs)).
- **Host policy is separate from environment data.** The parent resolves
  `GENO_FS_READ_ONLY` and `GENO_HTTP_ALLOW_PRIVATE` into trusted configuration,
  even when `env` is not granted. Runtime policy checks use that configuration,
  never the filtered environment snapshot, and expose no additional variables
  to the program. Allowing a variable for `env_get` does not alter policy.

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
| `env` | allowlist, snapshot of allowed values (read-only mapping) and program arguments | parent |
| `stdin` | private bounded reader over a dedicated input handle | parent supplies handle; supervised worker reads on demand |
| `fs` | filesystem facade, roots, read-only policy and shared write-budget state | worker imports; policy from parent and the resolved project |
| `http` | client modules plus address policy | worker preloads; policy from parent |

The runtime prelude (`geno/_runtime_support.py`) reaches the host only through
two private helpers:

- `_geno_host_module(name)` returns the bound object.
- `_geno_environ()` returns the filtered snapshot in the isolated worker;
  environment builtins also enforce the separate exact-name allowlist.

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

Bindings expose only the operations the builtins need. The filesystem facade
provides guarded read, write, list, existence, metadata and canonicalization
operations. Every operation reaches the established root, symlink and read-only
checks; writes also pass both budgets. Raw `os`, unrestricted `open` and host
handles are not exposed to Geno code. Reuse the existing policy validators
rather than maintain a second set inside the facade.

HTTP's trusted runtime may bind the standard client modules needed by the
existing validated handlers, only under `http`. Program code cannot reach
those modules or select an unvalidated opener.

### Environment and arguments (`env`)

Decision: the isolated lane uses an exact-name allowlist that defaults to empty.
The `env` capability and variable policy are separate gates.

- `cli_args()` remains under `env` and returns program arguments from the
  request. It needs no allowed variable names. The proposal does not introduce
  a new argument capability or expose `GENO_CLI_ARGS` as a shortcut around
  variable policy.
- `--allow-env NAME` selects individual variables for `env_get` and
  `env_get_or`. Start with exact names only, without wildcard or prefix grants.
  Explicitly allowing a secret-bearing name authorizes that particular value;
  granting `env` alone does not.
- The parent snapshots only allowed, present variables before setting its
  internal `GENO_CLI_ARGS`. The request contains no unlisted caller values,
  and no environment snapshot is supplied without `env`.
- The worker carries both the allowlist and the filtered snapshot. An unlisted
  name raises a host environment-policy denial (E412), even if it is absent
  from the caller's environment. An allowed-but-missing name retains the
  builtin's existing missing-value or default behavior. Reuse the embedding
  API's environment-policy semantics and validators rather than inventing a
  second interpretation.
- The worker's own process environment stays separate. It keeps its sandbox
  configuration variables, temporary `HOME` and isolated interpreter setup.
  The program never falls back to that environment for a missing snapshot
  value. If explicitly allowed, `HOME` or `PATH` comes from the caller's
  snapshot, not the worker's substitutes.
- Examples and `main` use the same allowlist and snapshot. Existing
  `--unsafe`, `--json`, embedding and standalone behavior is unchanged;
  moving a variable-reading program to isolated mode requires the corresponding
  explicit allowlist.

### Standard input (`stdin`)

Decision: read lazily inside the supervised worker through a dedicated input
handle. Do not buffer stdin in the parent before worker launch. Isolated
`stdin` remains refused until the implementation passes the platform checks
below.

- The handle is separate from the request/control protocol and is available
  only through trusted private bindings when `stdin` is granted. Neither the
  parent nor worker consumes input merely because the capability is present.
  Without the grant, no program-input handle is inherited.
- `stdin_read_all()` reads on demand in bounded chunks. Bound raw bytes and
  decoded characters before accumulating an oversized result; do not first
  read the complete stream and then check its size. Enforce the collection
  limit and existing size-error contract with bounded UTF-8 decoder buffering.
- Invalid UTF-8 returns `Err`, preserving existing behavior. A successful call
  still reads until EOF. Output remains buffered, so this is not a promise of
  interactive prompting.
- One wall-clock deadline covers worker startup, frontend work, examples,
  blocking input reads and `main`. Start accounting before worker launch;
  never reset the deadline when entering a new phase or builtin. No parent
  input acquisition may delay the start of supervision. Timeout or interruption
  terminates the supervised worker and releases its handles and any helper
  resources; no reader may remain blocked after cleanup.
- Examples and `main` consume one shared stream. Bytes consumed by an example
  are unavailable to later examples and `main`. Do not rewind, cache or replay
  input for a new phase. Reads after EOF return the existing empty-input result.
  Prefer examples over pure helpers taking supplied strings and separate
  fixture-based integration tests for stdin wrappers; use
  `--no-check-examples` when an operational run must skip effectful examples.
- The milestone must cover Linux, macOS and Windows handle setup, inheritance,
  timeout and interruption cleanup. Until these checks pass, granting stdin
  must remain unavailable in the isolated lane.

### Filesystem (`fs`)

Decision: use a narrow filesystem facade with portable per-file and aggregate
write limits. Keep the existing roots and read-only policy.

- **Roots.** Trusted worker setup calls
  `_explicit_fs_roots_for_run(filename, project_root, program_args)`, the
  function `--unsafe` uses. Absolute paths are allowed only within those roots.
  Program arguments reach setup even under an `fs`-only grant, while
  `cli_args()` remains denied without `env`.
- **Policy.** Route every operation through the established root, symlink,
  metadata, canonicalization and read-only validators. Trusted configuration
  carries the parent's `GENO_FS_READ_ONLY` policy separately from any
  program-visible environment snapshot. The facade must not offer an unchecked
  operation or return host handles to Geno code.
- **Defaults.** Use 64 MiB per file and 64 MiB cumulatively per run. These are
  conservative, configurable policy choices, not benchmark-derived capacity
  requirements. Both examples and compiled `main` use the same facade policy
  and aggregate counter.
- **Portable enforcement.** The facade enforces both byte limits on Windows
  and POSIX. Where available, `RLIMIT_FSIZE` also applies the configured
  per-file limit as an additional guard. Its absence on Windows does not
  remove the Geno-level limit. A zero limit disables facade writes, including
  zero-byte writes and truncation.
- **Accounting.** Count the UTF-8 bytes actually emitted after the existing
  platform newline conversion, not source characters or net file growth.
  Preserve `fs_write_text`'s current text-write behavior: on Windows, newline
  expansion must be included before reservation. The implementation must emit
  exactly the bytes it budgeted, with no additional unchecked translation.
  For `fs_write_text`, the per-file check covers the complete resulting file
  size. Validate capability, path, read-only policy and both limits before
  opening or truncating the destination. Reserve the full encoded write cost
  from the aggregate budget before I/O. Every rewrite counts again, even for
  the same path; a failed I/O attempt after reservation is not refunded.
  A capability, policy or quota rejection must leave the destination unchanged.
- **Errors.** Per-file, aggregate and zero-limit rejection return `Err(String)`
  from `fs_write_text`, preserving its `Result[Unit, String]` contract.
  Name the exceeded or disabled limit and configured byte count in the error.
  Both the example interpreter and compiled path return the same result
  category; these quota rejections do not themselves raise an execution error
  or terminate the run. A caller may handle `Err` and continue, and a later
  smaller write may succeed if it fits the unchanged remaining allowance.
  Rejection before reservation consumes no budget; an I/O failure after
  reservation does not refund it.
  Missing `fs` still follows the existing E412 capability-denial behavior.
  Collection/output limits and hard timeout, memory or CPU enforcement retain
  their existing behavior. An ordinary I/O failure can leave a partial write;
  this proposal does not promise transactional rollback.
- **Scope.** The budgets bound Geno-mediated file writes. They are not disk
  quotas, do not account for filesystem metadata or pre-existing contents,
  and do not constrain external processes. The existing process deadline
  continues to bound work, including repeated empty-file operations.

### HTTP (`http`)

Decision: reuse the existing validated connection and redirect machinery with
the same network policy, bounded by the remaining run deadline. Do not add an
alternate unvalidated client stack.

- **Destinations.** Permit only `http` and `https`. Preserve the existing
  rejection of non-public destinations unless the trusted host explicitly
  opts in. Resolve and validate destination addresses, then connect to the
  validated addresses using `_create_validated_http_connection` and the
  existing HTTP/HTTPS handlers; do not validate once and reconnect through an
  unchecked resolution path. Preserve TLS certificate and hostname checks.
- **Redirects.** Apply the same scheme and destination checks to every hop.
  Keep the existing redirect-count and method/body rules, including removal
  of credentials and other caller headers across origins. Validate the actual
  connection destination for each hop.
- **Proxies.** Preserve the explicit `ProxyHandler({})` behavior. Neither the
  worker's environment nor variables permitted for program reads may silently
  route requests through an unvalidated proxy.
- **Trusted policy.** The parent resolves `GENO_HTTP_ALLOW_PRIVATE` and sends
  it as trusted configuration. This works without an `env` grant and is
  independent of the program's variable allowlist. A program cannot enable
  private-network access by naming or reading a variable.
- **Bounds.** Reuse the existing bounded response readers and size-error
  behavior. Each request, connection attempt and redirect uses at most the
  smaller of the normal operation timeout and the remaining run time. The
  overall deadline also covers DNS, TLS and blocked body reads; the supervisor
  terminates a worker that cannot return before that deadline. No hop or
  example-to-main transition resets the budget.
- **Preloading.** Trusted setup loads only the client modules required for a
  granted `http` capability before freezing the Darwin address-space baseline.
  This is a setup requirement, not a measured startup-cost claim. Validate its
  effect on supported platforms in the implementation milestone.
- **No threads.** Do not add client background threads or relax the existing
  process limit. The implementation must preserve the worker's resource
  restrictions while reusing the validated client machinery.

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

Decision: examples receive exactly the run's capability grant, including its
host restrictions. This replaces the current isolated worker's fixed default
grant and absent host callbacks.

- With explicit `--cap`, both examples and `main` get exactly that set.
  Without it, both get the existing defaults. Never restore omitted default
  capabilities during example checking: `--cap env` denies `print` in both
  phases, while a `regex` grant permits regex examples.
- Apply the same environment allowlist and snapshot, filesystem roots and
  write policy, and HTTP restrictions in the example interpreter and compiled
  execution. Implement each capability's example support in the same milestone
  as its compiled support; do not add a hidden example-only capability subset.
- Examples execute before `main` and may perform authorized I/O. These effects
  are real and are not rolled back before `main`. In particular, stdin is one
  shared stream with no phase-specific snapshot, rewind or replay.
- The run has one overall deadline and aggregate output budget. Carry output
  accounting into the next phase rather than allocating a fresh full allowance
  to `main`. Process memory and CPU limits cover the whole worker; per-value
  collection and integer limits apply in each phase. A phase transition must
  not increase a shared budget.
- Existing interpreter-only step limits remain interpreter-only. Process mode
  currently ignores `--max-steps`; this proposal does not introduce compiled
  step accounting or promise a shared step counter across execution engines.
- Keep `--no-check-examples` as the explicit way to skip automatic examples.
  Recommend pure helpers that accept supplied data, with separate integration
  tests for effectful wrappers. The CLI and capability docs must make pre-main
  side effects and stdin consumption visible.

### Diagnostics

- The process-mode `--cap` rejection is removed only for capabilities whose
  milestone has landed. Other requests fail before launch with a diagnostic
  naming the unsupported capability; `process` and `serve` stay refused.
- `_default_lane_capability_hint` suggests `geno run --cap ...` for supported
  capabilities. Capabilities not yet implemented, plus `process` and `serve`,
  retain explicit alternative-mode advice.
  - It still takes the capability only from the worker's structured denial tag
    (`ProcessSandbox.last_capability_denied`).
  - The protection [#187](https://github.com/davidiach/geno-lang/pull/187) added against forged messages stays.
- Distinguish a missing `env` capability from an environment-policy denial.
  If `env` is already granted, an unlisted variable must identify the
  `--allow-env NAME` policy requirement instead of suggesting `--cap env`
  again. Diagnostics must not include variable values.
- Preserve the existing E412 category and call-site reporting for capability
  and environment-policy denials. Missing-capability wording stays unchanged.

## Backend And Target Parity

Language semantics do not change: the same builtins, capability names, effects
and E412 error. The interpreter (`--unsafe`, `--json`), standalone Python,
Node, browser and hosted surfaces keep their current behaviour. `--target`
still affects checking only; the isolated lane always executes compiled
Python.

For implemented capabilities, the intended parity with `--unsafe --cap` covers
the filesystem roots, read-only mode and absolute-path rule, the HTTP policy,
and program arguments. Examples use the run grant in both lanes. Isolated
environment access intentionally differs: it exposes only explicitly allowed
snapshot values. Portable file-write quotas, shared aggregate budgets and hard
limits can also cause an isolated run to stop where direct execution continues.

Behavior and differences under the recorded decisions:

| Topic | Isolated lane | `--unsafe` |
|---|---|---|
| Output | shown when the program ends | streamed |
| `stdin` | read on call in worker; size-bounded; shared run deadline and stream | read on call |
| Examples | exact run grant and restrictions; shared deadline, output/write budgets and stdin | run with the grant |
| Environment | exact-name allowlist, empty by default; snapshot of allowed values | full caller environment with `env` |
| Write quotas | portable per-file and aggregate byte limits, shared by examples and `main` | existing direct-mode behavior; no new quotas |
| Hard limits | wall clock (kill), memory, CPU, file size where supported, processes | none; cooperative timeout and step limit |
| `process`, `serve` | refused | available |

## Compatibility And Migration

- **Source:** no change. No frozen conformance case covers CLI run modes, and
  none changes.
- **CLI behaviour:** supported explicit grants stop failing at the blanket
  `--cap` rejection and instead execute under the chosen policies. The default
  capability set remains unchanged. Shared cumulative budgets may cause a run
  with many automatic examples to reach its limit earlier than under separate
  phase budgets; document and test that enforcement change. The
  `--max-file-size-bytes` default changes only when `fs` is granted.
- **Write quotas:** isolated `fs` runs have portable per-file and total-write
  limits; direct execution retains its existing behavior. Large exports or
  repeated writes may require an explicit higher limit. A zero limit disables
  all facade writes, and quota rejection occurs before truncation. The
  aggregate budget is not reclaimed after failed reserved writes. Per-file,
  aggregate and zero-limit rejection return `Err` from `fs_write_text`; they
  do not by themselves turn the run into an execution error.
- **Environment migration:** `--cap env` alone is sufficient for `cli_args()`.
  Programs using `env_get` or `env_get_or` must explicitly allow each variable
  with `--allow-env NAME` when moving from `--unsafe` to isolated mode. Unlisted
  access is a policy denial, not a missing-value fallback. Existing execution
  modes and their environment defaults are unchanged.
- **Example effects:** examples may perform only granted I/O before `main`,
  and stdin they consume is not replayed. Document pure example helpers,
  integration-test fixtures and `--no-check-examples` for operational runs.
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

- **Release:** implementation is deferred to the next feature cycle after
  0.6, provisionally 0.7.0 or later. Main is already at 0.6.0-rc.3; this proposal
  does not expand that candidate. Acceptance establishes a design direction,
  not a shipping promise. Each implemented milestone gets its own changelog
  entry under Added in the applicable release.

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
`--unsafe`, so those runs have none of the hard limits. The proposal aims to
retain hard limits while enabling those runs. That improvement depends on
validated host bindings and policies, including lazy stdin reads inside the
supervised worker under the run deadline. The historical prototype does not
establish the recorded allowlist, example-accounting or stdin properties.

**Environment exposure.** The exact-name allowlist defaults to empty. Only
allowed snapshot values cross to the worker; `cli_args()` needs no variable
exposure. Unlisted reads fail with an environment-policy denial, with no fallback
to the worker's process environment. An explicitly allowed secret can still be
sent over granted HTTP, including by an example; document that scope. Existing
`--unsafe` full-environment behavior remains unchanged.

**Denial of service.** Existing wall-clock, memory, CPU, output, collection,
integer and recursion limits stay in force. Step guards retain their existing
interpreter-only scope. On top of that:
- `fs` has per-file and aggregate byte limits enforced in the facade, with
  one counter across examples and `main`; the number of files and filesystem
  metadata costs are still bounded only indirectly by the deadline;
- `http` uses the network until the timeout;
- stdin is read only inside the supervised worker, bounded in size and by
  the same deadline as startup, examples and main execution;
- examples share cumulative run budgets rather than multiplying them, and
  granting stdin without calling it consumes no input.

**Fail-closed properties.** These are kept:
- without `--cap`, the default capability set remains unchanged;
- unknown names are rejected;
- `process` and `serve` are refused;
- a bad grant or policy in the worker is a startup error, so nothing runs.

**Supply chain.** No new dependencies.

## Testing And Validation

- **Parent:** grant validation, `process`/`serve` refusal, and defaults,
  explicit overrides and invalid values for `--max-file-size-bytes` and
  `--max-total-write-bytes`. Also that, without `--cap`, the default
  grant is unchanged; `--allow-env` requires an explicit isolated `env` grant;
  and only permitted, present variables are sent in the snapshot.
- **Worker:**
  - an unsupported or malformed grant in the config is a startup error;
  - each host binding is absent when its capability is not granted;
  - generated code that names `_GENO_HOST` or a helper is rejected;
  - Geno code that defines or references those names fails to compile.

  Validate both rejection paths against the implemented bindings; historical
  prototype reports do not substitute for these regressions.
- **Per capability**, in the isolated lane:
  - `regex`;
  - `env`: `cli_args` with an empty allowlist; exact-name allowed values;
    E412 for unlisted reads, including `env_get_or`; allowed-but-missing values
    retaining their existing behavior; omission of unlisted secrets from the
    payload; and separation from the worker's setup environment;
  - `stdin`: UTF-8 across chunk boundaries, invalid UTF-8, byte and character
    bounds and over-limit input; no consumption when denied or granted but
    unused; a pipe that never reaches EOF; slow input; timeout during a read;
    interruption and Windows handle cleanup; read-after-EOF behavior; shared
    stream consumption by examples and `main`; and no deadline reset between
    startup, examples, input reads and `main`;
  - examples: identical explicit grants without restored defaults; `regex`
    examples; allowed and denied environment, filesystem and HTTP operations
    before `main`; visible authorized side effects; aggregate output and
    deadline enforcement across phases; preservation of interpreter-only step
    guards; and `--no-check-examples` skipping those effects;
  - `fs`: read, write, list, metadata and canonicalize; `..` and symlink
    escapes; absolute paths inside and outside the roots; a path argument
    after `--` authorizing its directory under an `fs`-only grant, while
    `cli_args` is still denied; read-only policy without an `env` grant;
    per-file and aggregate limits on Windows and POSIX; multibyte UTF-8 and
    newline-heavy Windows accounting (including LF/CRLF mixtures); rewrites
    and failed attempts consuming reservations; shared
    example/main counters; zero limits rejecting empty writes and truncation;
    unchanged destinations after policy or quota rejection; distinct per-file,
    aggregate and disabled-limit error messages; identical `Err` results in
    example/interpreter and compiled paths; callers recovering from quota
    rejection and continuing; and preservation of E412 and hard-limit behavior;
  - `http`: local servers under trusted private-network opt-in without an
    `env` grant; scheme and non-public-address rejection; validated address
    pinning and DNS-rebinding regressions; redirect target, count and
    cross-origin-header handling; proxy-variable bypass attempts; TLS
    validation; bounded bodies; and DNS, connection, redirect and body-read
    stalls under the same run deadline.
- **Parity:** with equivalent policies, inputs and grants, the same
  programs under `--cap` and `--unsafe --cap` produce the same output and errors
  within the isolated lane's limits. Add explicit expectations for documented
  differences in environment scope, write quotas, example execution and stdin
  behavior.
- **Diagnostics:** the [#187](https://github.com/davidiach/geno-lang/pull/187) tests in
  `geno/tests/test_capability_denial_diagnostics.py` are updated for the new
  hint and still prove a forged message gets no hint.
- **Security corpus:** new `# EXPECT` entries run in the isolated lane: a path
  escape, an absolute path outside the roots, and a grant without `fs` that
  calls a filesystem builtin.
- **Platforms:** Linux, macOS (the Darwin baseline with the `http` preload)
  and Windows (Job Object limits and portable facade write quotas, without
  POSIX file-size rlimits).
- **Gates:** `make security`, `python3 scripts/local_ci.py release`, the docs
  snippet tests and the website build tests for the updated figures.

### Prototype background

A historical throwaway prototype explored `env` and `fs` before these
decisions. Reproducible artifacts and a documented measurement environment
are not included in this proposal, so no prototype timings, memory figures,
patch-size measurements or startup-overhead claims are retained as evidence.
It did not validate the recorded allowlist, shared budgets, lazy stdin,
portable write quotas or complete HTTP contract. Acceptance and implementation
must rely on the stated design, review and required validation, not on the
prototype anecdotes.

## Rollout And Observability

This proposal was accepted by David Iach on 2026-10-24 and assigned permanent
number 0013. The project lead authorized marking PR #192 ready and merging
the accepted design record once all required checks pass. Implementation stays
in separate reviewed PRs, and acceptance or merging this document does not
close #68.

Implementation is planned for 0.7.0 or later through five small milestones,
each in a separate reviewed PR and independently revertible:

1. **Grant selection and `regex`:** validate the configured grant, preserve
   defaults when `--cap` is absent, give examples the exact run grant with
   shared accounting, and update hints and docs only for supported capabilities.
   No new host binding.
2. **`env` and arguments:** add the private host-binding channel and helpers,
   the empty-by-default exact-name allowlist and filtered snapshot; test
   argument access and identical environment policy for examples and `main`.
   This is the milestone intended to address
   [#68](https://github.com/davidiach/geno-lang/issues/68); close it only after
   implementation and validation.
3. **`stdin`:** implement a dedicated input handle and lazy bounded reads in
   the supervised worker, one shared stream and deadline, interruption cleanup,
   and Linux, macOS and Windows handle tests. Keep the capability refused
   until those checks pass.
4. **`fs`:** implement the narrow facade, existing roots and read-only policy,
   portable per-file and aggregate quotas, and shared reservation accounting.
   Preserve filesystem-only argument forwarding and pass the Windows/POSIX
   quota and policy tests before enabling the capability.
5. **`http`:** integrate the existing validated client with trusted policy,
   preload, redirects, proxy protection, bounded responses and the remaining
   deadline. Pass the network-policy, timeout and supported-platform checks
   before enabling the capability.

Every stage continues to reject capabilities it has not implemented.
`process` and `serve` remain refused throughout. Reverting a stage restores
fail-closed rejection for its capabilities; shared plumbing must retain any
interfaces later stages still depend on.

No feature flag is proposed. New capability selection is explicit, while
shared budget enforcement also covers runs using the default grant and is
documented as a compatibility change above. Each implementation PR adds its
own changelog entry and scoped tests, then runs the applicable security,
parity and release gates. No implementation or capability behavior changes
are included in this design-only PR.

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

## Implementation Gates And Formal Acceptance

The policy choices above are recorded. Remaining implementation work includes
the dedicated stdin-handle and cleanup mechanism for each platform, portable
write accounting, and integration of the existing HTTP client under the
worker's import and resource restrictions. These require executable evidence
before their milestones are enabled, not a completed implementation before
formal acceptance of the design.

- **Stdin gate:** validate unused input, shared stream semantics, never-ending
  pipes, timeouts, interruption and handle cleanup on Linux, macOS and Windows.
- **Filesystem gate:** validate every facade operation, root/read-only policy,
  both quotas and shared reservation accounting on Windows and POSIX.
- **HTTP gate:** validate destination pinning, every redirect, credential
  stripping, proxy protection, TLS, response bounds, deadlines and preloading
  on the supported platforms.
- **Release gates:** run the applicable security, parity and release checks
  for each implementation PR. Keep a capability refused until its gate passes.
- **Formal acceptance:** completed on 2026-10-24 by David Iach, project lead,
  with permanent number 0013. Merge remains conditional on passing required
  checks and is separate from implementation. Target 0.7.0 or later remains
  tentative, and #68 remains open until implementation and validation resolve it.

### Review record (2026-10-24)

No independent human technical review took place. David Iach accepted the
proposal using the existing sole-maintainer provision in `GOVERNANCE.md`.

The review received consists of Codex automated advisory review and focused
documentation validation. Two final automated reviewers checked the whole
proposal and its runtime/security contracts. Windows newline-byte accounting
and write-quota result semantics were clarified, then both reviewers reported
no remaining actionable findings on the design published in `29d85ff`.
The PR description records exact validation commands and results, including
the acceptance/numbering checks. Automated review and CI are not independent
human approval or proof of an implementation's runtime safety.

The proposal has been open since 2026-10-16, satisfying the process's minimum
seven-day public-review period. While Geno has one active maintainer,
`GOVERNANCE.md` permits the project lead to accept without independent review
if its absence and the review actually received are recorded. Independent
human review remains desirable for this security boundary; see
[#28](https://github.com/davidiach/geno-lang/issues/28).

**Formal acceptance is recorded.** On 2026-10-24, the project lead directed:

> I formally accept PR #192. Record the acceptance and lack of independent human review, assign a permanent proposal number, and merge it once all required checks pass.

This authorizes acceptance metadata, permanent numbering and merge after the
required checks pass. It does not authorize bypassing checks or claim that the
future capability implementations have been validated.
