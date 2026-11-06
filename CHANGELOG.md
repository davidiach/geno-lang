# Changelog

All notable changes to Geno will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

This section opens the 0.7 development line. Nothing in it is released: the
package version stays 0.6.0, and the Draft
[`docs/spec/v0.7.md`](docs/spec/v0.7.md) specifies the additions.

### Added

- Forms models write from other languages' habits, from accepted
  [proposal 0026](docs/proposals/0026-cold-write-forms.md); every previously
  accepted program keeps its meaning:
  - Compound assignment: `total += x`, also `-=`, `*=`, `/=` and `%=`, on a
    variable, field or index. The target may not call a function.
  - Tuple patterns in loops and comprehensions:
    `for (i, word) in words.enumerate() do`, `[k for (k, v) in m.entries()]`.
  - String literals inside f-string interpolations, including ones that hold
    braces, quotes or another f-string: `f"{m.get("the", 0)}"`.
  - A trait or impl method's receiver written `self` alone.
- Fixes that `geno check --fix` applies: `match x` or `match x:` without
  `with`; a TypeScript record type `type P = { x: Int }`; a record update in
  braces (`{ p with x = 1 }`, `{ ...p, x: 1 }`); a Rust struct literal
  (`Point { x: 1, y }`); Python's `for i, x in pairs`; an assignment to a
  `let` binding (declare it with `var`); conversion names such as
  `int_to_string`, `string_of_int`, `parseInt` and `parseFloat`; and a missing
  return type, written as the type the function's `return` values have.
- `trait Name[T]` is one error explaining that traits take no type parameters.
  Indexing a `MutableMap` or `Vec`, or assigning by index to a `Map`, says how
  to read or update it instead.
- Seeded-mistake results add `match x` without `with` and a JavaScript `let`
  for a reassigned variable, and now repair the missing return type: 828 of
  907 mutants repaired, none misrepaired.

- Panic examples, from Draft
  [proposal 0017](docs/proposals/0017-panic-examples-and-property-checks.md):
  `example 1, 0 panics "division"` states that a call must panic, optionally
  with a substring of the panic's message (compared ignoring case), and
  `example () panics` covers a zero-argument function. `panics` is a
  contextual word, so it stays a legal name everywhere else. A panics example
  passes on `panic(...)` or any runtime error, including a failed `requires`
  or `ensures`; exhausting a resource limit (steps, time, recursion, memory,
  collection size, integer size, output) or a host refusal (E412, E413, E500)
  does not satisfy one and is reported by name. `geno run`, `geno test`, the
  harness runners, the embedding API's `check_examples` and the hosted server
  all check them; compiled Python documents them in docstrings and the
  documentation generator renders them.
- E330 (`TYPE_EXAMPLE_PANIC_FORM`): an example whose expected value is written
  as a panic (`-> panics`, `-> panics "m"`, `-> panic`, `-> panic("m")`) is an
  error with a machine-applicable fix that writes the `panics` form, which
  `geno check --fix`, `geno.fix()` and the language server's quick fix apply.
  `-> panics` stays legal when the program defines its own `panics`, and so
  do `-> panic(...)` forms that call the program's own `panic`. A zero-argument
  `example panics` is E201 with a fix that adds `()`, and an empty message,
  `panics ""`, is E202 with a fix that deletes it.
- `docs/spec/v0.7.md`, the Draft contract of the 0.7 development line, forked
  from v0.6, and `conformance/v0.7`, a development corpus marked
  `status = "development"` that `scripts/run_conformance.py --all-retained`
  runs after the retained v0.5 and v0.6 corpora. `spec.json` records the draft
  in a new `development_line` object, which `scripts/validate_spec.py` checks.
- `geno test --properties` (Experimental), from the same proposal
  ([spec v0.7](docs/spec/v0.7.md) section 5.4). It calls every function that
  has no effects but mutation on generated arguments (100 per function, or
  `--trials N` up to 10,000) and judges each call by the function's own
  contracts. An input its `requires` rejects is discarded. An `ensures`
  violation, a panic (unless the function has a panics example) or a resource
  limit fails it. A failing input is shrunk and printed in the form that
  follows `example`, with a reproduce command. Runs are deterministic: the
  default seed is 0, and `--seed S` and `--filter` reproduce a function's
  inputs from any directory. Work is counted in interpreter steps: 200,000
  per call, 5,000,000 per function for trials and 4,000,000 for shrinking.
  Wall time is only a safety stop, per call (`--timeout`, at most 5 s) and
  per function (2 × `--timeout`), and a function it stops is `incomplete`,
  never failed, so the same seed gives the same verdict on a busy machine.
  A call that uses up its full allowance is a `limit` failure; a function
  whose budget runs out first is `incomplete`.
  `gave_up`, `incomplete` and `skipped` are warnings, and only a failed
  property makes the run exit 1. `--json` gains a `properties` object. Single
  files, directories and projects are supported; in a project only its own
  modules are checked. Trials run in an interpreter of their own with no
  capabilities. Plain `geno test`, its JSON and its exit status are
  unchanged, and the property engine is imported only with `--properties`.
- `geno.values.ContractViolationError` carries structured fields: `clause`
  (`"requires"` or `"ensures"`), `reason` (`"false"` or `"propagate"`),
  `clause_index`, `call_depth` (now set on every violation) and
  `result_display`. Property checks, `geno test` and example verification
  read these instead of parsing the message. Also new for embedders and
  tools: `Interpreter.step_allowance(n)`, `geno.parser.parse_expression()` and
  `TypeChecker.check_project_graph(..., retain_module_checkers=True)`.
- Capability grants in the default process-isolated `geno run` lane, from
  accepted [proposal 0013](docs/proposals/0013-isolated-run-capabilities.md)
  ([spec v0.7](docs/spec/v0.7.md) section 9.11.1). `geno run --cap LIST`
  runs with exactly the listed capabilities, for examples and `main`, and
  keeps the hard wall-clock kill and the memory, CPU, file-size and process
  limits, instead of exiting with "--cap is not supported in the default
  process-isolated run mode". Without `--cap` the grant is unchanged
  (`clock`, `print`, `random`). Refused grants exit 1 before the program
  starts: `process` and `serve` with a message naming `--unsafe`, and a
  capability that is not available yet, or not on this platform, as "not
  yet available in the process-isolated run mode". The worker validates the
  grant and host policy from its parent-set configuration before reading
  any input, binds host access only for granted capabilities, and the
  runtime's private names for that channel (`_GENO_HOST`,
  `_GenoHostUnavailable`, `_geno_host_module`, `_geno_environ`) are
  reserved runtime names on both backends, for top-level definitions,
  references and local bindings alike. One milestone per capability:
  - **`regex`** (milestone 1): `geno run --cap print,regex lint.geno`.
    Examples get the same exact grant (`--cap regex` denies `print` in
    examples too), and `sleep_ms` works in examples when `clock` is granted.
  - **`env`** (milestone 2), which addresses
    [#68](https://github.com/davidiach/geno-lang/issues/68):
    `geno run --cap env,print main.geno -- hello world` gives `cli_args()`
    the program arguments. `--cap env` alone exposes no environment
    variable: the new repeatable `--allow-env NAME` lets
    `env_get`/`env_get_or` read one exact name from the caller's environment
    (case-insensitive on Windows, as `os.environ` is there), and only allowed
    variables the caller has are sent to the worker. Reading any other name,
    even one the caller does not have, fails with E412 "environment variable
    'NAME' is not allowed by the host env policy" and a hint naming
    `--allow-env`; the program never sees the worker's own environment.
    Examples and `main` read the same snapshot. `--allow-env` requires an
    explicit `--cap env` grant and is refused under `--unsafe` and `--json`,
    whose environment behaviour is unchanged.
  - **`stdin`** (milestone 3) is implemented but not enabled:
    `geno run --cap stdin ...` is still refused before launch on every
    platform. The proposal keeps it refused until its stdin gate (handle
    setup, inheritance, timeout and interruption cleanup) has passed on
    Linux, macOS and Windows; only Linux and macOS can be validated so far,
    and enabling it on those alone would deviate from that gate, which needs
    the project lead's sign-off. Until then, programs that read standard
    input keep using `geno run --unsafe --cap stdin,...` (trusted code
    only). In place for that step: only a `stdin` grant makes the parent
    duplicate its standard input for the worker alone, and nothing is read
    until `stdin_read_all` asks. Trusted worker code then reads 8 KiB at a
    time against the collection limit and decodes strict UTF-8 with the same
    `Err` text as standalone compiled output; an over-limit error reports
    the characters decoded when the limit was detected (at most 8,192 over
    it). Examples and `main` share one stream and one deadline, with no
    replay.
  - **`fs`** (milestone 4): `geno run --cap fs,print report.geno --
    data/input.csv`. The seven `fs_*` builtins go through a narrow trusted
    facade in the worker with exactly the roots of `--unsafe` (the working
    directory, the source's directory, the project root and the directories
    of path arguments after `--`, or `GENO_FS_ROOTS`), the same symlink and
    absolute-path rules (`GENO_FS_ALLOW_ABSOLUTE`) and the same
    `GENO_FS_READ_ONLY` policy, all read from the caller's environment only
    when `fs` is granted. A path argument widens the roots without `env`,
    while `cli_args()` itself still needs `env`. Writes get two portable
    limits, 64 MiB per file (`--max-file-size-bytes`, which also sets
    `RLIMIT_FSIZE` where available) and 64 MiB per run across examples and
    `main` (new `--max-total-write-bytes`). A write is counted in the UTF-8
    bytes it emits after newline translation, reserved before the file is
    opened, and never refunded, even for a rewrite of the same path or a
    write that then fails; a limit of 0 disables every write, including empty
    ones. Quota rejections return `Err` naming the limit and leave the file
    unchanged, so a program can recover; a read-only write and an
    `fs_exists` outside the roots still raise. Without `fs` both limits stay
    0, as before.
  - **`http`** (milestone 5, Linux and macOS): `geno run --cap http,print
    fetch.geno`. `http_fetch`, `http_post` and `http_request` go through the
    same validated client as `--unsafe`, now run in trusted worker code: only
    `http` and `https`; every connection and redirect hop resolves, validates
    and connects only to public addresses; at most ten redirects, with caller
    headers (`Authorization`, `Cookie`, ...) dropped across origins; proxy
    variables never reroute a request; TLS certificates and hostnames are
    verified against the caller's trust store (`SSL_CERT_FILE` and
    `SSL_CERT_DIR` reach the worker's TLS library only with an `http`
    grant); and response bodies count against the collection limit. Private
    and loopback destinations need `GENO_HTTP_ALLOW_PRIVATE` in the caller's
    environment, read only with an `http` grant and never through
    `--allow-env`. Each connection attempt waits at most 30 seconds and at
    most the time the run has left, examples and `main` alike, and the hard
    kill at `--timeout` still ends any request that cannot return in time.
    The HTTP client modules are loaded during the worker's trusted setup
    only with an `http` grant.
- Runtime failures report their Geno location and call stack, from Draft
  [proposal 0018](docs/proposals/0018-runtime-failure-diagnostics.md)
  ([spec v0.7](docs/spec/v0.7.md) section 6.9.1). A panic, runtime error,
  failed contract, capability denial or resource limit prints the Geno frames
  that led to it, most recent call last, each as `file:line:col in name`
  positioned inside its own function (at the call it was making, or at the
  failure for the last frame), then the source line with a caret where the
  host can read the source, then the `Runtime Error: <message>` line, which
  stays last so hints still follow it:

  ```text
  Stack trace (most recent call last):
    demo.geno:21:21 in main
    demo.geno:15:14 in safe_div
        requires b != 0
                 ^
  Runtime Error: Precondition failed: safe_div(a: 8, b: 0) requires b != 0
  ```

  This holds on every `geno run` lane (the default isolated lane maps its
  compiled `main` back to Geno source in the trusted worker), `--watch`,
  `geno test -v`, and standalone compiled Python and Node CommonJS and ESM
  entry builds, which map host frames through a compact `_GENO_TRACE` table
  the compiler embeds and reads only when a failure escapes `main`: no call
  does extra work and the artifact still runs without Geno installed. Frames
  are named Geno functions (`f`, `Type.method`, `Module.f`, `<example>`
  while an example is verified and `<test>` for a call a `test` block made);
  lambdas, builtins, runtime helpers and host
  frames never appear, and standard-library files show as `std/<Name>.geno`.
  A stack of more than 20 frames keeps its first 10 and last 10 and says how
  many it left out. Recursion, step and cooperative-timeout limits, callbacks
  started by builtins, default parameter values, calls through function
  values and `async` functions get stacks too. The location and stack never
  enter the message, so no file name, position or frame name can satisfy a
  `panics "substring"` example or a property check (contract failure
  messages themselves change; see Changed (breaking)).
- Contract failures name the function, the call's argument values and the
  clause as written (`Precondition failed: safe_div(a: 8, b: 0) requires
  b != 0`), and an `ensures` failure the result
  (`Postcondition failed: shout(s: "hi") ensures length(result) > 10 (result
  was "hi!")`), identically on the interpreter, compiled Python and compiled
  JavaScript. Values are quoted where they are strings and cut to 64 code
  points by bounded renderers that stop walking a large value early. The
  arguments are always the call's: when the body rebound a parameter the
  clause reads, the message adds `; n was 6 when checked`. The clause text is
  captured at parse time (`RequiresClause.source_text`,
  `EnsuresClause.source_text`, ignored by AST equality), which also fixes the
  truncated clause of a multi-line `ensures` in property-check reports.
  `ContractViolationError` and the compiled contract exceptions also carry
  `function`, `clause_text`, `arguments` and `checked_values`.
- `geno run --json`, `geno.api.run()`/`run_path()` and the hosted server's
  `/run` report a runtime failure's `location`, a `stack` list of
  `{"function", "location"}` objects, `stack_omitted` when frames were left
  out, and a `contract` object for a failed `requires`/`ensures` (`clause`,
  `reason`, `clause_index`, `function`, `clause_text`, `arguments`,
  `result`, and `checked_values` when present); `geno test --json`
  violations gain `location`, `stack` and `stack_omitted`. A
  standard-library file is named `std/<Name>.geno` in these fields, as in
  text reports, so a hosted client never sees the server's install path.
  `Diagnostic`
  gains `stack` (`GenoStack` of `StackFrame`s) and `contract`
  (`ContractDetails`). The keys are additive: existing keys keep their
  values, codes are unchanged (a contract failure stays E499), and the one
  value that is new where it was absent is `location` on panics and other
  builtin failures. One existing value changes: a `geno test` violation's
  `message` loses its location prefix (see Changed (breaking)).
- `GENO_TRACEBACK=host` also prints the host's own traceback after the Geno
  report, under `Host traceback (GENO_TRACEBACK=host):`, on `geno run`
  (the interpreter's or the isolated worker's), in standalone compiled
  Python and in Node entry builds, for debugging Geno itself. `--json`, the
  embedding API and the hosted server ignore it.
- `scripts/run_conformance.py` accepts an `expected_diagnostic` table on a
  schema 2 `cli-json` case, stating a failing envelope's `code`,
  `message_contains`, `location`, `stack` and `stack_omitted` as path-suffix
  matches, and `conformance/v0.7` adds six `runtime-*` cases (a panic three
  calls deep, an index error, `requires` arguments, an `ensures` result,
  recursion elision and a capability denial) on `cli-process`, `cli-direct`,
  `cli-json`, `python`, `js` and `js-esm`. The compiled lanes now compile
  with the case's file name.

### Changed (breaking)

- An example's expected value can no longer be a call of the builtin `panic`
  (E330). 0.6 accepted `example 1, 0 -> panic("division")`, because `panic`
  returns `Never`, although the example could never pass: its expected value
  panics before the call is made. Such a program still compiled and ran with
  `geno compile`, `geno build` and `geno run --no-check-examples`; it no longer
  checks. `geno check --fix` migrates it. Recorded in the
  [compatibility policy](docs/operations/compatibility-policy.md#07-draft-an-examples-expected-value-cannot-be-a-panic-call).

  ```geno
  // before (0.6)
  example 1, 0 -> panic("division")
  // after (0.7 draft)
  example 1, 0 panics "division"
  ```

  A message that is not a string literal (`-> panic(reason)`) has no automatic
  fix: write the text literally, or drop it (`example 1, 0 panics`).
- In the default process-isolated `geno run` lane, examples and `main` share
  one output budget and one deadline (proposal 0013), with or without `--cap`.
  Output printed by examples is still never shown, but it now counts against
  the same `--max-output-length` budget as `main`'s output instead of an
  allowance of its own, so a run whose examples print a lot reaches the limit
  earlier; the example phase also runs under the time left of `--timeout`
  rather than a fresh allowance. Timeout messages still name `--timeout`.
  Recorded in the
  [compatibility policy](docs/operations/compatibility-policy.md#07-draft-examples-share-the-isolated-runs-grant-and-budgets).
  To migrate, skip examples in an operational run or raise the limit:

  ```bash
  geno run --no-check-examples --cap env,print main.geno -- hello
  geno run --max-output-length 200000 main.geno
  ```
- `--max-file-size-bytes` and the new `--max-total-write-bytes` default by
  grant (0 without `fs`, 64 MiB with it) and, like the other process sandbox
  limits, are refused under `--unsafe` and `--json` whenever they are given,
  so an explicit `--max-file-size-bytes 0` there, which 0.6 accepted, now
  exits 1. An empty `GENO_FS_ROOTS` stops an isolated run that grants `fs`
  before launch (`Error: GENO_FS_ROOTS must contain at least one filesystem
  root`); without `fs` the variable is not read.
- Contract failure messages change from `Precondition failed for safe_div:
  requires clause evaluated to false` to `Precondition failed:
  safe_div(a: 8, b: 0) requires b != 0` (proposal 0018), which changes what
  `panics "substring"` examples match in both directions. `panics
  "Precondition failed"` and `panics "Postcondition failed"` still match;
  substrings only the old wording had (`"Precondition failed for"`, `"for
  <name>:"`, `"requires clause"`, `"ensures clause"`, `"evaluated to
  false"`) no longer do, and a substring of an argument value or of the
  clause text (`"b != 0"`) can now match a contract failure. No corpus in the
  repository uses an old substring. Recorded in the
  [compatibility policy](docs/operations/compatibility-policy.md#07-draft-runtime-failures-report-a-geno-stack-and-contract-failures-name-their-arguments).
  To migrate, match the new prefix, or read the structured fields:

  ```geno
  // before (0.6)
  example 1, 0 panics "Precondition failed for safe_div"
  // after (0.7 draft)
  example 1, 0 panics "Precondition failed: safe_div("
  ```
- `geno test` violation messages no longer start with a location (proposal
  0018). In 0.6 a located runtime failure in a `test` block (an index, key,
  division or contract failure), or in a file that fails while loading, had
  the message `<file:line:col>: Runtime Error: <message>`, with the file's
  host path; it is now `Runtime Error: <message>`, the form unlocated
  failures such as panics already had, in text output and in `--json`'s
  `message`. The position moves to the violation's new `location` and
  `stack`. Recorded in the
  [compatibility policy](docs/operations/compatibility-policy.md#07-draft-runtime-failures-report-a-geno-stack-and-contract-failures-name-their-arguments).
  To migrate, read the position from `--json`'s `location` rather than
  parsing it out of `message`:

  ```text
  0.6:  "message": "/abs/ti.geno:3:12: Runtime Error: Index 5 out of bounds for list of length 1"
  0.7:  "message": "Runtime Error: Index 5 out of bounds for list of length 1",
        "location": {"file": "/abs/ti.geno", "line": 3, "column": 12, ...}
  ```
- Where Geno requires two function types to be equal (assigning a `var`,
  redeclaring a name in the same block, reassigning an `Option` or list of
  functions through a `var`, binding a generic type parameter from an
  earlier argument), a function whose inferred type lost `mutation` under
  proposal 0019 (see Changed) no longer matches one that kept it. In 0.6
  both carried `mutation`, so they matched by accident. `var f = local_sum`
  followed by `f = shared_bump`, where `local_sum` writes only its own `var`
  and `shared_bump` writes an array through a copied reference, is now E300
  "Type mismatch in assignment: expected (Int) -> Int, got (Int) -> Int with
  mutation". No repository program or benchmark solution hits this, and
  no documentation snippet besides the ones showing the break. Recorded in
  the [compatibility policy](docs/operations/compatibility-policy.md#07-draft-local-mutation-is-not-an-effect).
  To migrate, restore the 0.6 type on the function or on the binding:

  ```geno
  // before (0.6 inferred the effect)
  func local_sum(n: Int) -> Int
  // after (0.7 draft)
  func local_sum(n: Int) -> Int with mutation
  ```

  or `var f: (Int) -> Int with mutation = local_sum`. No automatic fix is
  offered, because either site is a valid choice.

### Changed

- A capability denial in the default `geno run` lane, in `main` or in an
  example, now ends with the isolated command when the lane can grant the
  capability (`Hint: grant it with --cap; the program stays
  process-isolated:` and `geno run --cap clock,env,print,random main.geno --
  hello`) and an unlisted environment variable with an `--allow-env` hint.
  Only `process`, `serve` and the capabilities refused on the platform still
  get `--unsafe` advice. Hints are still derived only from the worker's
  structured denial, so a forged denial text gets none. The README, guides,
  capability, filesystem and execution-surface references, `SECURITY.md`,
  the reference-app READMEs and the website now show `geno run --cap ...`,
  keeping `--unsafe` for `process`, `serve`, `stdin` and trusted code.
- Platform notes for the isolated lane: `http` is refused before launch on
  Windows ("not yet available in the process-isolated run mode on Windows")
  until proposal 0013's HTTP gate has Windows evidence, so Windows programs
  that need it keep `--unsafe --cap http,...` (trusted code only); `stdin` is
  refused on every platform (see Added); `env` names are case-insensitive on
  Windows only; and the `fs` write quotas, which count `\r\n` as two bytes on
  Windows, are the only file-size limit there.
- The process sandbox worker's validator of compiled program code also
  rejects a runtime-internal name bound by a `def`, `class`, parameter,
  `global`, `except ... as` or match pattern, not only one referenced by name.
- Runtime errors raised where the interpreter and the builtins enforce a
  `SandboxConfig` limit (`max_collection_size`, `max_integer_bits`,
  `max_output_length`), and the interpreter's error for a `Float` power
  outside the host's range, are now `geno.values.RuntimeLimitError`, a
  subclass of `GenoRuntimeError`, so a limit is told from a panic by type.
  Their messages and error codes are unchanged.
- `geno test` reads whether a failure was a `requires` or an `ensures` from
  the violation's `clause` field, not its message. A program's own
  `panic("Precondition failed ...")` is therefore reported as an `example`
  failure, not a `requires` failure.
- Runtime failure stderr (proposal 0018): the first line is `Stack trace
  (most recent call last):` whenever a stack exists, and the `Runtime Error:`
  line is last, before any hint; read that line, or `--json`, rather than the
  first. `--unsafe` frames change from `  in <name>, <call site>` (with `main`
  at its definition) to `  <file:line:col> in <name>`, the snippet moves above
  the message, and the separate `at` line appears only when there is no
  stack. Scripts that scraped frame lines should read `--json`'s `stack`.
- Standalone compiled Python and Node entry builds print the Geno report
  instead of a Python traceback into the generated file or a V8 stack of
  generated JavaScript; the status stays 1. A Node entry build now exits as
  soon as the report is printed, for an async `main` too (0.6 let pending
  async work run after an async `main` rejected), and a rejected `Async` that
  nothing awaits is reported the same way, with status 1. The entry path makes
  stdout and stderr blocking before `main` runs, as they are in compiled
  Python, so that exit loses none of the output printed before the failure,
  even through a full or slow pipe (0.6's synchronous fatal path could drop a
  queued tail). `Error.stackTraceLimit`, the `unhandledRejection` listener
  and the blocking mode are set only when the file runs as the entry point,
  so importing an ESM build changes none of them, and a CommonJS build that a host `require()`s keeps
  0.6's behaviour: a failure of its `main` propagates to the requirer (an
  async `main`'s rejection is logged with `process.exitCode = 1`, as before)
  and nothing process-wide changes. A Geno name `module` is renamed in
  JavaScript output, like `require`, so it cannot hide CommonJS's own.
  Builtins that turn a host failure into `Err` or `None` (`json_parse`, the
  `fs_*` functions, `exec`) build that host error with stack capture off, so
  the entry path's unlimited trace limit costs nothing on their success
  paths. The table and its anchor are the first two lines of
  the program's code, so `--source-map` mappings move down by two lines; the
  map format is unchanged. Browser builds and programs without `main` are
  unchanged.
- `geno test` records a runtime failure in an example as `Runtime error:
  <message>`, without the doubled `Runtime Error:` and location prefix, and
  `-v` prints the failure's frames under it. A runtime failure in a `test`
  block is `Runtime Error: <message>` with no location prefix, now that every
  failure is located (see Changed (breaking) for the located failures whose
  0.6 message had one); its stack starts with a `<test>` frame at the test
  body's call.
- The REPL prints the same runtime report for a failing input: its frames,
  with the typed input itself shown as the `<repl>` frame, then the
  `Runtime Error: <message>` line last (0.6 printed the message, then an
  `at` line).
- A failure inside a builtin (`panic`, `unwrap`, `expect`, a denial) carries
  the builtin call's location on the error itself, which the interpreter now
  sets in place, keeping the error's type, code and trusted denial tags; its
  `str()` includes the location, while `.message`, which `panics` examples
  match, is unchanged.
- Local mutation is not an effect, from Draft
  [proposal 0019](docs/proposals/0019-local-mutation-is-not-an-effect.md)
  ([spec v0.7](docs/spec/v0.7.md) sections 3.5, 6.2, 6.3 and 7.6). A
  function or lambda that changes only state its own call created is pure:
  declaring a `var`, assigning one, writing a field or element of a value it
  holds (`var q = p; q.x = 1`), and writing elements of an `Array`, `Vec` or
  `MutableMap` it allocated and never let escape (`var counts =
  array_new(n, 0); counts[i] = counts[i] + 1`) no longer contribute
  `mutation`. A function with a local accumulator can now be passed where
  `(List[Int]) -> Int` is expected (`apply(total, xs)`, `xs.map(total)`),
  where 0.6 reported E300 "expected (List[Int]) -> Int, got (List[Int]) ->
  Int with mutation", and `main() -> Unit with io` may use local `var`s, where
  0.6 reported E310. Index and field writes a caller can observe keep
  `mutation`: through a parameter's collection or a copy of it, below a
  reference-typed field or element (every `array_new(h, array_new(w, 0))`
  table), through a collection that was stored, passed to a function or
  captured, and a lambda's write to a captured variable, which charges the
  lambda and not the function that creates it. A builtin that only uses an
  owned collection (`array_to_list(counts)`, also as a pipeline stage,
  `counts |> array_to_list`) is not an escape. Effects stay static: no
  backend, diagnostic code or syntax changes, a declared `with mutation`
  stays legal, and `geno test --properties` selects the same functions.
  Messages, local-binding hover and the REPL's `:type` print the new
  inferred types, and language-server hover on a function that declares no
  effects now names the ones the checker settled on, `(function, inferred
  `with mutation`)` or `(function, inferred pure)`, so a masked function can
  be told from one that keeps `mutation`. The checker still does not charge
  mutating builtins (`vec_push`, `array_set`, `set_add`, ...) on any
  receiver, as in 0.6; the spec now records that as a known limitation, and
  closing it awaits a decision on the proposal's Unresolved Question 1. As a
  result, a function that writes its caller's collection only through such
  builtins, and that 0.6 charged only because it also updates a local `var`
  such as a loop counter, is now inferred pure; declare `with mutation` on
  it to keep the 0.6 type. `lock_piece` and `clear_lines` in
  `examples/apps/tetris.geno` and `update` in `examples/apps/notetaker.geno`
  had this shape and now declare it. `conformance/v0.7` adds
  `local-mutation-is-pure` on all seven runtime lanes and two checker cases
  that stay E300.

### Fixed

- A tuple pattern matched against a value that is not a tuple is one error:
  the names it binds no longer add "Undefined variable" errors, one of which
  carried a rename fix to an unrelated variable.

- A nullary constructor of a generic ADT nested in another constructor call
  type-checks. With `type Tree[T] = Leaf | Node(left: Tree[T], value: T,
  right: Tree[T])`, `Node(Leaf, 1, Leaf)` was rejected ("expected Tree[T], got
  Tree[__ctor_arg0___fresh_T_2]") as an example input, in an annotated `let`,
  as a call argument and in a generic function's body. It is now a
  `Tree[Int]`, and mistyped values such as `Node(Leaf, "x", Node(Leaf, 1,
  Leaf))` are still rejected.
- A bare nullary constructor of a generic ADT type-checks as an example input
  or a call argument of a generic function. `example Leaf -> 0` for
  `size[T](t: Tree[T])` and `g(Leaf, x)` for `g[T](t: Tree[T], x: T)` were
  rejected ("expected Tree[T], got Tree[__fresh_T_1]").
- `run_harness_from_compiled` grants the same capabilities as
  `run_harness_from_source` (`print`, `clock`, `random`), and it treats a
  compiled capability denial as a refusal rather than a panic. A panics
  example of a function whose call is denied now fails in both runners, as it
  does under `geno test`. Compiled `panic`, `option_expect` and
  `result_expect` mark their exception as a panic, so a panic whose message
  reads like a resource limit (`panic("List size exceeds limit (1 > 0)")`)
  is a panic in the compiled harness too, not the collection size limit.
- A `?` that propagates `None` or `Err` inside a `requires` or `ensures`
  clause is a contract violation in compiled Python and JavaScript, as it is
  in the interpreter (the message ends in `('?' propagated None/Err)`).
  Compiled Python let its internal
  `_PropagateReturn` escape the function (and a caller that used `?` then
  returned the propagated value); compiled JavaScript returned from the
  function silently.
- A list or map literal whose first element has an open type completes it
  from the other elements. `let d: List[Tree[Int]] = [Leaf, Node(Leaf, "s",
  Leaf)]`, `let d: List[Option[Int]] = [None, Some("s")]` and `[Ok(1),
  Err("x")]` under `List[Result[Int, Int]]` passed `geno check` and failed at
  run time with a host error (`"s" + 1`); they are now type errors, and
  `let d = [None, Some(1), Some(2.5)]` infers `List[Option[Float]]` instead
  of asking for an annotation.
- A node parsed from an f-string interpolation carried a location relative
  to the interpolation (line 1), so a type error or a runtime failure inside
  `{...}` pointed at the file's first line. The parser now rebases the
  interpolation's tokens onto the source; parse errors inside an
  interpolation are located as before.
- Compiled Python showed an `ensures` failure's result with the host `str()`
  (`result was hi!`), where the interpreter and JavaScript showed `"hi!"`;
  all three now render it the same way (proposal 0018).
- `geno test --json` reported a runtime failure in an example as
  `"Runtime error: Runtime Error: boom"`; it is now `"Runtime error: boom"`.
- A step-limit or cooperative-timeout message raised inside a
  standard-library function ended in the library's installed host path
  (`... at /usr/lib/.../geno/std/List.geno:20:40`), which the hosted
  server passed on to clients; it now ends in `std/List.geno:20:40`, as
  every runtime report names the standard library (proposal 0018).
- Language-server hover on a local binding showed the type the checker
  recorded in its first pass, where every unannotated function still looks
  pure: `let g = bump` hovered as `g: (Int) -> Int` while the next use of
  `g` showed `with mutation`. A later record for the same binding now
  replaces the earlier one, so hover shows the final pass's type, and the
  first pass's type stays when the final pass stopped at an error.

## [0.6.0] - 2026-10-25

### Changed

- Promote the qualified 0.6.0-rc.5 feature set to a non-prerelease version
  under [accepted proposal 0014](docs/proposals/0014-0.6-stable-promotion.md)
  and its [RC.5 baseline amendment 0016](docs/proposals/0016-0.6-rc5-promotion-baseline.md).
  Promotion changes release metadata and documentation, with no additional
  language, runtime, dependency, or supported-target changes.
- Make [the 0.6 specification](docs/spec/v0.6.md) the normative pre-1.0 contract
  for its series. Keep older specifications and frozen conformance corpora.
- Retain the project's Preview maturity and Alpha package classifier. This
  release does not claim serious-beta or organizational production readiness.

### Fixed

- Include the RC.2 and RC.3 fixes for lambda capture, callback inference,
  floating-point argument semantics, JavaScript JSON formatting, Windows
  package cleanup, TypeScript Option declarations, and imported constructor
  collisions (#188, #189, #190, #191, #194, #195, #209).
- Retain RC.4's faster compiler startup: reuse generated prelude bindings only
  for the exact source hash, preserving the original parser and safety checks (#220).
- Include RC.5's patched Python build minimum and MSAL publishing-tool
  dependency override (#222).
- Make the worker startup-timeout regression test tolerate slow CI scheduling
  while preserving its assertions and production timeout behavior (#216).

### Migration and known limitations

- Users upgrading from 0.5 should follow the [0.6 migration notes](#migration-to-06).
- Pre-existing Git objects hardlinked outside a checkout remain protected and
  may require manual cleanup. New dependency clones avoid shared hardlinks.

## [0.6.0-rc.5] - 2026-10-25

Fifth release candidate for the frozen 0.6 language contract. It includes the
dependency and build security fixes merged after RC.4; stable promotion remains
held for fresh qualification and an explicit promotion-baseline amendment.

### Security

- Raise the isolated Python build requirement to setuptools 84.1.0, matching the
  existing release-tool pin and excluding vulnerable older build backends.
- Update VS Code publishing tooling to patched MSAL Node 7.1.1 with a scoped
  Azure Identity override. The override crosses Identity's declared major-version
  range; initialization, extension tests and packaging are validated, while live
  authenticated publishing is not claimed.

The Geno runtime, frozen language contract, capabilities, targets and performance
budgets are unchanged from RC.4. No new source migration is required.

## [0.6.0-rc.4] - 2026-10-24

Fourth release candidate for the frozen 0.6 language contract. Stable promotion
remains held until fresh qualification and an explicit promotion-baseline update.

### Fixed

- Avoid reparsing the compiler-owned Python runtime prelude during compiler
  startup. A generated binding inventory is used only when the exact source hash
  matches; modified sources retain the original parser and reserved-name checks.
  Backend validation, dependency versions, and performance budgets are unchanged.

## [0.6.0-rc.3] - 2026-10-24

Third release candidate for the 0.6 series, retaining the frozen 0.6 language
contract and the migration guidance below. This remains a prerelease for
evaluation.

### Fixed

- Remove Windows package transaction backups and replaced checkouts containing
  read-only Git object files. New clones keep Git objects private even with
  local URL rewrites; cleanup preserves path containment and reports unrelated
  failures (#190).
- Describe JavaScript `Option[T]` values in generated TypeScript declarations
  as the actual tagged `Some`/`None` union, including parameters and nested
  values (#195).
- Reject conflicting constructor names exposed by unaliased imports
  consistently in project and embedding API checks, before interpretation or
  compilation. Diagnostics retain the original type declaration location;
  repeated, diamond, private and aliased imports preserve their existing scopes
  (#209).

## [0.6.0-rc.2] - 2026-10-24

Second release candidate for the 0.6 series. This remains a prerelease for
evaluation under the same frozen 0.6 language contract and migration guidance
as 0.6.0-rc.1.

### Fixed

- Preserve floating-point argument semantics when an interpreter function is
  called through a contravariantly typed alias, matching both compiled
  backends (#191).
- Preserve lexical capture when nested block lambdas are compiled to Python
  and JavaScript (#188).
- Preserve valid object fields when JavaScript formats pretty JSON (#194).
- Use the expected callback return type when checking block-lambda returns,
  including branches that return `None` before `Some` (#189).

### Known limitations

- Generated TypeScript declarations describe `Option[T]` as `T | null`, while
  the JavaScript runtime returns tagged `Some` and `None` values. TypeScript
  consumers must handle the runtime representation explicitly until #195 is
  fixed.
- Projects importing same-named constructors from different modules can pass
  checking and interpretation but fail in generated Python or JavaScript.
  Keep constructor names distinct across imported modules until #209 is fixed.
- On Windows, package-update cleanup can leave transaction backups containing
  read-only Git object files. The installed dependency contents are unaffected
  (#190).

## [0.6.0-rc.1] - 2026-10-24

Release candidate for the first publication of the 0.6 series, which implements
the [Geno 0.6 upgrade roadmap](docs/proposals/0003-geno-0.6-language-upgrade.md)
and accepted proposals 0004-0012. It is a **prerelease**: it breaks source
compatibility with 0.5 (see "Changed (breaking)" and "Migration to 0.6"), so it
is published for evaluation first and taken to 0.6.0 by a promotion proposal,
as 0.5.0-rc.1 was. Its contract is [docs/spec/v0.6.md](docs/spec/v0.6.md),
which becomes normative at that promotion, and its frozen corpus is
`conformance/v0.6`; `docs/spec/v0.5.md` and `conformance/v0.5` are retained
unchanged.

### Added

- Generic functions ([proposal 0004](docs/proposals/0004-generic-functions.md)):
  `func first_or[T](xs: List[T], fallback: T) -> T`. Calls, example clauses and
  uses as a value each infer the type arguments. Inside the function a type
  parameter is opaque: values can be passed on, stored, compared with `==`,
  printed and converted with `to_string`, but arithmetic, ordering and use as a
  `Map` key or `Set` element are rejected. New diagnostics: E320 for an invalid
  type parameter list (duplicate, shadowing a type, unused, or on `main` or an
  impl method) and E321 for a generic function used as a value whose type
  arguments nothing determines. The JavaScript backend compiles a copy of a
  generic function for each way its type arguments contain `Float`, so a `Float`
  passed through a type parameter prints as `2.0` on every backend. TypeScript
  declarations declare the type parameters.
- Bounds on type parameters
  ([proposal 0005](docs/proposals/0005-type-parameter-bounds.md)):
  `func largest[T: Ord](xs: List[T], fallback: T) -> T`. `Num` permits
  arithmetic, `Ord` ordering and `Hash` use as a map key or set element; a trait
  name permits calling that trait's methods. `Num` implies `Ord`, which implies
  `Hash`. Errors about a missing bound name the bound to add. New diagnostics:
  E322 for an unknown bound and E323 for a type argument that misses one.
- Method-call syntax
  ([proposal 0006](docs/proposals/0006-method-call-syntax.md)):
  `xs.map(f).filter(g).length()`, `s.trim().to_upper()`, `opt.unwrap_or(0)`,
  `v.push(x)`. The receiver's type chooses among trait methods, the program's
  own functions, and builtins, including the prefixed ones (`opt.map` calls
  `option_map`). Fields of function type keep precedence. Unknown methods are
  reported with a suggestion instead of "Unknown field" followed by "Cannot
  call non-function type: Any".
- Types inferred from context
  ([proposal 0007](docs/proposals/0007-contextual-type-inference.md)): lambda
  parameters (`xs.map(fn(x) -> x * 2)`, `fn(acc, x) -> acc + x`), `for` and
  comprehension variables (`for x in xs do`, `[x * 2 for x in xs]`) and tuple
  destructuring (`let (a, b) = pair`) may omit their types. A lambda in a call
  is checked after the call's other arguments, so `apply(fn(x) -> x * 2, 21)`
  works. Function signatures still require annotations.
- If expressions, or-patterns, tuple fields and map literals
  ([proposal 0008](docs/proposals/0008-expression-ergonomics.md)):
  `if c then a else b end if` as a value, `| Red | Orange -> ...` arms,
  `pair.0`, and `{"a": 1}` / `{}`. The language tour documented `{}` for an
  empty map in 0.5, but it did not parse.
- `panic(message)` ends the program with `message` (E414 in the interpreter)
  and has type `Never`, so it fits in any branch and ends a path for
  return-path analysis ([proposal 0009](docs/proposals/0009-result-only-errors.md)).
- `main() -> Result[T, E]`: an `Err` is written to stderr as `Error: <error>`
  with exit status 1, and an `Ok(v)` behaves as a `main` returning `v`, so `?`
  works at the top level of a program. Embedding APIs return the raw `Result`;
  `geno run --json` reports it raw and exits 1 on `Err`
  ([proposal 0009](docs/proposals/0009-result-only-errors.md)).
- Total accessors `xs.get(i)`, `xs.first()` and `xs.last()` (`list_get`,
  `list_first`, `list_last`) return `Option`. `r.unwrap()`, `r.expect(message)`
  and `opt.expect(message)` (`result_unwrap`, `result_expect`, `option_expect`)
  turn a failure into a panic when it can only mean a bug.
- A coherent standard library
  ([proposal 0010](docs/proposals/0010-coherent-standard-library.md)):
  `length()` works on every collection; `Map` gains `contains_key`, `remove`,
  `keys`, `values` and `length`; `xs.sort()` needs no comparator; and a free
  call resolves like the method call it spells, so `map(opt, f)` calls
  `option_map`.
- `geno migrate [path] [--check | --diff]` rewrites 0.5 source for 0.6 wherever
  the rewrite preserves behavior (`throw "x"` to `panic("x")`, `.unwrap()` on
  the I/O builtins that now return `Result`, removed builtins to their
  replacements) and lists what needs a person, such as `try`/`catch`.
- Host names are Geno names
  ([proposal 0011](docs/proposals/0011-host-names-and-runtime-limits.md)): a
  program may name a function `len`, a local `list`, a parameter `console` or
  a constructor `Error`, and a top-level `func max` or `let min` shadows the
  builtin on every backend. The compilers emit such names with a `_g` suffix;
  values still print and match by their Geno names. Underscore-prefixed
  runtime internals stay reserved.
- Default limits that run ordinary programs: call depth 10,000 (was 500) and
  20,000,000 interpreter steps (was 1,000,000) on every local lane, including
  standalone compiled Python scripts and `geno test` examples. Before Python
  3.12 a deep run gets a thread with a large enough stack instead of crashing,
  except in the default process-isolated `geno run` on platforms other than
  Linux: its worker cannot start a thread, so it keeps the recursion limit
  within its stack and a very deep run stops with a recursion error. Use
  Python 3.12 or later, or `--unsafe` for trusted code, to run deeper there.
  The hosted server keeps call depth 500 (`GENO_MAX_RECURSION_DEPTH`).
- Diagnostics carry fixes when the correction is unambiguous: a rename for
  every "Did you mean" suggestion (names, methods, fields, types,
  constructors), argument names for an all-positional call to a function with
  three or more parameters, a missing `then`/`do`, and `let mut`, `True`/`False`,
  `++` and `elif` rewritten to Geno, each with a message that names the Geno
  form. `geno.check()` returns them (`Diagnostic.fixes`), `geno check` prints
  them, `geno check --fix` applies them, and the language server offers them
  as quick fixes over the exact text they replace. An unknown method or field
  is now reported at its name instead of at the receiver.
- Membership and list concatenation
  ([proposal 0012](docs/proposals/0012-what-models-reach-for.md)):
  `x in xs`, `c in "aeiou"`, `key in m`, `x in set` and `x not in xs` are
  comparison operators, and `xs + ys` concatenates two lists of one element
  type (a `List[Int]` joining a `List[Float]` as Floats). Both run through the
  builtins they spell (`contains`, `string_contains`, `map_contains_key`,
  `set_contains`, `concat`) with identical output on every backend.
- Everyday operations models reach for
  ([proposal 0012](docs/proposals/0012-what-models-reach-for.md)):
  `min(a, b)`; `xs.sum()` (Int, or Float for a `List[Float]`, `0.0` when
  empty), `xs.min()` and `xs.max()` (Option, also `min(xs)`, `max(xs)`),
  `xs.is_empty()` (also on strings, maps and sets), `xs.index_of(x)`,
  `xs.count(pred)`, `xs.unique()`; `s.reverse()`, `s.lines()`, `s.words()`
  and the ASCII tests `is_digit`, `is_alpha`, `is_alphanumeric`,
  `is_whitespace`, `is_upper`, `is_lower`; `range(n)`; and
  `m.get(key, default)`. A free call rejected by its argument count
  (`max(xs)`) resolves as the method call it spells, like one rejected by
  its argument type. `conformance/v0.6` adds `familiar-idioms`.
- A method call names its arguments only when it writes three or more: the
  receiver before the dot no longer counts, so `xs.fold(0, f)`,
  `s.replace("a", "b")`, `xs.slice(1, 3)` and `m.insert(k, v)` are accepted.
- `n.to_float()` and `f.to_int()`: `Int` and `Float` methods reach the
  `int_` and `float_` builtins.
- Another language's name for an operation is fixed to Geno's for the
  receiver: `s.chars()`, `ord(c)`, `chr(n)`, `xs.skip(n)`, `m.items()`,
  `m.has(k)`, `m.put(k, v)`, `opt.flat_map(f)`, `"3".to_int()` and others.
  `pow(a, b)`, `s * n` and `", ".join(parts)` get a hint naming the Geno
  form. An edit-distance fix is no longer attached when the names are
  different words (`set`/`get`, `is_upper`/`to_upper`), and a short name gets
  no far-fetched "Did you mean".
- More fixes for habits from other languages: `&&`, `||`, `!`, `=>`, `#`,
  `null`, `else:`, `if c:` and `lambda x: body` are each reported with the
  Geno form and fixed to it; `len`, `str`, `upper`, `strip`, `startswith` and
  similar names are renamed to Geno's builtin; a bare `end` gets its keyword;
  a function whose body ends with a value of its return type gets the missing
  `return`. A missing `then`/`do` is reported at the end of the line it
  belongs on, and a missing return type on the signature. A "Did you mean"
  fix is now attached only when the candidate is unique and one edit away (two
  for names of six letters or more), and a swap of adjacent letters counts as
  one edit, so `lenght` suggests `length` rather than `height`.
- `geno.fix(source)` applies the fixes `geno.check()` reports until none is
  left and returns the fixed source, the number applied and its check
  (`FixResult`).
- Seeded-mistake results (`docs/benchmark/seeded-mistakes.md`,
  `scripts/seeded_mistakes.py`): 27 mistakes models carry over from other
  languages, seeded into the 78 benchmark reference solutions. All 840
  mutants are rejected and reported on the mistake's line, and `geno.fix`
  repairs every mutant of the 25 mistakes that have a fix with no change in
  what the program computes. No model or API key is needed to reproduce it.
- The LLM benchmark speaks 0.6: the Geno prompt carries the rules from
  `docs/llm-prompting.md` (a test keeps them identical) and a reference
  program checked to pass its examples; repair prompts list Geno errors with
  code, line, column and fix; and every run records pass@1 after `geno.fix`
  ("autofix", no model call) beside pure pass@1. `scripts/publish_benchmark_results.py`
  publishes the repair conditions.
- The language server knows the checked program: hover shows the type of
  every local, parameter, inferred lambda and loop variable, pattern binding
  and tuple field, and for a method call the function it resolves to;
  completion after `.` lists the receiver's methods and fields (also while the
  line being typed does not parse); go-to-definition reaches a local's binding
  and a trait method's implementation; signature help works inside method
  calls. The formatter, language server, test runner and docs generator are
  Stable.
- `geno fmt` is Stable and canonical: it also normalizes spacing between
  tokens (one space around binary operators and `->`, after `,` and `:`, none
  inside brackets or before a call's `(`), indents a match arm's body under
  its `| pattern ->`, indents method-chain and operator continuation lines,
  and collapses runs of blank lines. Every Geno program in the repository is
  kept in this form, and a test enforces it.
- Arithmetic on an operand that already failed to type-check no longer adds a
  second "Cannot apply '+' to Any and Int" error.
- The standard library is generic throughout: `List`, `Option` and `Result`
  work at any element type, `Map` at any hashable key type and `Math.abs`,
  `min`, `max` and `clamp` at `Int` or `Float`, instead of `Int` or `String`
  only. The checker no longer substitutes builtin signatures for them.
- `geno check` warns when a `let` or `var` redeclares a mutable variable from
  the same or an enclosing block (W300), as in `var y = f(y)` inside a loop
  where `y = f(y)` was meant. The program still passes. The warning is
  printed to stderr, returned by `geno.check()` and `geno.check_path()` with
  `Severity.WARNING`, and published by the language server. It covers the
  project's own modules, not the standard library or installed dependencies.

### Changed (breaking)

- One error model ([proposal 0009](docs/proposals/0009-result-only-errors.md)):
  `throw`, `try`/`catch` and the `throw` effect are removed. Each is rejected
  with the new E205 and a message naming its replacement. A runtime error,
  failed contract or resource limit can no longer be caught by the program.
- `fs_read_text`, `fs_write_text`, `http_fetch` and `http_post` return
  `Result[T, String]` instead of raising on I/O, path-policy and network
  failures, like `fs_list_dir` and `http_request` already did. Embedding
  `host_callbacks` for them keep returning the plain value and raising on
  failure; the API wraps both.

- `string_index_of` and `string_last_index_of` (`s.index_of(t)`) return
  `Option[Int]` instead of `-1` for "not found".
- Removed builtins: `square`, `is_positive`, `is_sorted`, `is_permutation`,
  `is_numeric_string`, `repeat_string`, `contains_substring` and
  `sort_strings`. Calling one reports E302 with its replacement.
- A block closer's `end` and its keyword must be on the same line. `end` with
  `if` on the next line was read as `end if`, so a bare `end` (Ruby, Lua)
  silently swallowed the next `if`, `for` or `while` statement's keyword. It
  is E201 at the `end`, with a fix that adds the keyword.

### Migration to 0.6

Run `geno migrate` first; it performs every rewrite below that is mechanical
and lists the rest.

- An uncaught `throw "message"` becomes `panic("message")`; the diagnostic text
  is unchanged.
- A thrown and caught user error becomes `return Err(MyError(...))` from a
  function returning `Result[T, MyError]`, handled with `match` or `?`.
- A `try`/`catch` around a runtime error becomes a check (`xs.get(i)`,
  `if d != 0 then`) or is removed and left to panic.
- Delete `throw` from effect lists.
- `string_index_of(s, t)` becomes `s.index_of(t)` with a `match`, or
  `.unwrap_or(-1)` to keep the sentinel.
- `square(x)` becomes `x * x`, `repeat_string` becomes `s.repeat(n)`, and so on
  per the E302 message.
- `let text = fs_read_text(path)` becomes `fs_read_text(path)?` in a function
  returning `Result`, a `match`, or `fs_read_text(path).unwrap()` to keep 0.5's
  failure behavior. The same applies to `fs_write_text`, `http_fetch` and
  `http_post`.

### Fixed

- A lambda in a pipeline stage takes its parameter types from the piped
  value, as it does in a call: `xs |> filter(_, fn(x) -> x > 1)` and
  `xs |> fold(_, 0, fn(acc, x) -> acc + x)` type-check instead of asking for
  annotations.
- A failing `example` clause is reported with its own code, E409, instead of
  E499.
- `geno doc` renders tuple types as Geno writes them, `(A, B)`, instead of
  `Tuple[A, B]`.
- Building or walking a linked structure is linear again: the interpreter no
  longer re-walks a whole value at every call to enforce collection limits,
  and compiled Python no longer scans lists to track identity. A 9,000-node
  list took over 30 seconds to build in the interpreter.
- A JavaScript program's `func max` no longer replaces the runtime's own
  `max_`, and 28 builtin names that one backend accepted as user functions
  and the other rejected now compile on both.
- `import List` no longer makes `length("abc")` a type error, and `import
  Option` no longer breaks `map` on a list: an unqualified standard-library
  import leaves always-available builtins their meaning.
- `geno.run()` and `geno.check()` resolve an imported standard-library module
  (`import List`) as `geno run` does; they ignored it before.
- After a parse error, recovery no longer stops at the `func` of `end func`,
  which reported a spurious "Expected function name" and skipped the next
  definition.
- Imports are not transitive. 0.5 accepted a call to a function from module `A`
  in a program that imported only a module `B` which imports `A`; the program
  ran in the interpreter and failed with a name error in compiled Python and
  JavaScript. `geno check` now rejects the call and suggests `import A`.
- An imported module no longer sees the entry program's functions or imports in
  the interpreter. A module that called the builtin `length` ran another
  module's `length` instead whenever the entry program imported one, while
  compiled Python and JavaScript called the builtin.
- An exported function whose signature contains a tuple, such as
  `export func pair(x: Int) -> (Int, Int)`, is no longer rejected as exposing a
  non-exported type `Tuple`.
- `http_listen` dispatches `OPTIONS` and `PATCH` requests to routes registered
  for them in the interpreter and in generated Python. Before, both methods
  were answered with 501 Unsupported method, so CORS preflight handlers never
  ran. Unregistered paths still return 404.
- `json_stringify`, `json_stringify_pretty` and `json_to_string` check the
  collection size limit before building their output. Before, the whole string
  was built first, so a value that repeats one subtree or string many times,
  or a very large `indent`, could use far more memory than the limit and run
  past the timeout. In the interpreter, a value that repeats a subtree is now
  converted once instead of once per repeat.
- `json_stringify_pretty` treats an `indent` above 10 as 10 in the interpreter
  and in generated Python, as generated JavaScript already did.
- Generated JavaScript renames variables, parameters and functions named
  `eval` or `arguments`, which strict-mode JavaScript forbids. Before, such a
  program compiled but Node.js rejected it with a SyntaxError. A constructor
  field with either name is now a JavaScript compile error, as `class` already
  was.
- Reference apps:
  - geno-form stacks its fields and highlights the active tab.
  - tetris.geno draws all seven pieces correctly and fits the default canvas.
  - App READMEs use `geno run --unsafe --cap`.

## [0.5.0] - 2026-10-14

### Changed

- Promote the frozen 0.5.0-rc.1 feature set to a non-prerelease version under
  [accepted proposal 0002](docs/proposals/0002-0.5-stable-promotion.md) (#177).
  The project lead waived the waiting periods and external human review for
  this promotion; all technical release requirements remain in force.
- Retain the RC's module-level constants, named constructor arguments,
  single-execution async values, stricter type/import validation, and backend,
  formatting, hosted-runtime, and exact-integer JSON fixes described below.
  This promotion changes release metadata and documentation only.
- Geno remains a preview project with the existing pre-1.0 compatibility
  policy and Alpha package classifier. A non-prerelease version does not
  confer serious-beta qualification.

### Migration from 0.4

- A successful `main() -> Int` selects the process exit status modulo 256:
  `2` and `258` exit 2; `0 - 1` exits 255. Its value is no longer printed
  as `=> N`. Print a value and return `Unit` when output is intended.
- JSON mode retains the raw result while exiting with the normalized status.
  Allow intentional nonzero exits in `set -e` or `check=True` callers.
  Printed output requires `--cap print` in JSON mode and
  `RunConfig(capabilities={"print"})` when embedding.
- Embedding still returns the raw value without terminating the caller.
  Generated Python and Node ESM imports remain inert; browser artifacts keep
  their existing display behavior.
- Parameterized entrypoints, ambiguous bare imports, duplicate types or
  constructors, builtin type-name reuse, and duplicate impl methods are
  rejected. Async computations execute once even when awaited repeatedly.
  See the RC.1 migration notes below and [0.5 specification](docs/spec/v0.5.md).

## [0.5.0-rc.1] - 2026-10-14

Release candidate prepared for the first publication of the 0.5 series. It carries accepted
proposal 0001. It is a **prerelease**, because that proposal's Rollout section
permits the executable entrypoint change to be published only as a v0.5
prerelease with curated breaking-change notes; taking it stable needs a new
proposal amending that direction.

`0.5.0-rc.0` below opened the series and was never published, so everything
recorded under it ships here too: module-level constants, the normative
`docs/spec/v0.5.md`, and the `conformance/v0.5` corpus.

### Changed

- **`geno run` honors a `main() -> Int` result as its exit status.** The result
  is normalized modulo 256, so `return 2` exits 2, `return 258` exits 2 and
  `return 0 - 1` exits 255, and it is no longer printed as a `=> 2` line. All
  three lanes agree: the default process-isolated run, `--unsafe`, and `--json`.
  Output written before the result still arrives, and a normal nonzero status
  carries no traceback and no diagnostics. `main() -> Unit`, a missing `main`
  and any other declared return type keep exiting 0, and a non-`Int` result is
  still displayed. This implements section 4.1.1 of `docs/spec/v0.5.md` and is
  a breaking change for anything reading `=> N` from stdout or expecting 0 from
  a program whose `main` returns a nonzero `Int`. (proposal 0001, #105)
- `geno run --json` keeps reporting the **raw** value: `258` stays `258` in the
  envelope while the process exits `2`, with `ok` true and no diagnostics.
  Nothing that is not a process boundary gained a status: `geno.api.run()`,
  `compile_and_exec()`, the generic `ProcessSandbox` and the hosted server
  return the raw result and never terminate their caller. `RunResult` gained
  `entrypoint_kind` so a caller that *is* a boundary can derive a status
  without resolving the program again.
- The standalone compiled Python artifact, the standalone Node script and
  directly executed Node ESM report an `Int` `main` result as their exit status
  too, normalized the same way and no longer printed. The Python artifact applies
  it under `if __name__ == '__main__'` only, and Node sets `process.exitCode`
  rather than calling `process.exit`, so buffered output still flushes and
  importing either artifact remains inert.
- Browser-targeted JavaScript keeps displaying an `Int` result, because it has no
  process boundary. The compiler selects that from the target profile, so a
  bundler's `process` polyfill cannot turn a browser artifact's displayed result
  into an exit status.
- The self-hosted CLI in `selfhost/Main.geno` applies the same contract at its
  own boundary: an inner program's `Int` result becomes the status `cmd_run`
  returns, normalized once, and every other kind stays success with the value
  left unprinted, which is what that adapter has always done.
- `conformance/v0.5` now carries this contract rather than pytest alone. The
  corpus runner gained schema 2 -- `expected_exit_status`, `expected_stderr` and
  `expected_stderr_contains`, `expected_exit_class` and `expected_json_value` --
  and runs seven executable boundaries: the in-process embedding lane, the three
  `geno run` lanes, standalone compiled Python, the Node script and directly
  executed Node ESM. Ten cases carry the `Unit`, `Int` and no-`main` rows of the
  section 4.1.1 result table on every one of those boundaries, including a type
  alias for `Int`, an `async main` and a synchronous `main` that awaits, since
  classification reads the resolved return type. The table's remaining row stays
  in `geno/tests/test_exit_semantics.py`, because a `main` returning some other
  type displays a host's own repr rather than text a case can state; so does
  entry-module ownership, which needs a second module on disk. Schema 1 keeps
  loading, so the frozen `conformance/v0.4` corpus is untouched, and a run whose
  target selection matches no case is now an error instead of an empty pass.

### Migration

A `main() -> Int` whose integer was there to be *seen* prints it and returns
`Unit` instead:

```geno
func main() -> Int
    return double(21)
end func
```

becomes

```geno
func main() -> Unit
    print(double(21))
    return ()
end func
```

`print` takes an `Int` directly and emits the same digits the `=> 42` line used
to. `geno run` grants `print` by default, and so does `--unsafe`, so neither
form needs a new flag; the fail-closed lanes -- `geno run --json` and the
embedding API -- grant nothing, so a program migrated this way needs
`--cap print` there.

A program that meant its integer as a *status* keeps `main() -> Int` and stops
reading stdout for it: the value is what the shell reports as `$?`, modulo 256.

`geno run --json` needs the same attention, in the other direction. Its envelope
is unchanged -- `value` still carries the raw result, so 258 is still 258 -- but
the command now exits with the normalized status, 2 for that envelope.
Anything running it under `set -e`, or with `subprocess(..., check=True)`, now
has to read a perfectly valid envelope despite a nonzero exit.

### Added

- **Named arguments in constructor calls.** `Point(x: 10, y: 20)` parses, as
  sections 6.3 and 7.8 of the spec have always written it and as the grammar's
  `arg = (IDENT ":")? expr` allows for a `TYPE_IDENT` callee. The checker
  validates names against the variant's fields and reports unknown, repeated,
  missing and positional-after-named arguments; arguments are evaluated in
  source order on every backend. (#135)
- Two further rules in `docs/spec/v0.5.md`, so the 0.5 contract now differs from
  v0.4 in three places rather than only section 4.1.1:
  - Section 7.15: an `Async[T]` value runs its computation **at most once**.
    Awaiting it again, including while an earlier await of it is still in
    progress, produces that one run's result, and a run that threw throws the
    same error to every awaiter. `spec.json` carries the rule too. (#136)
  - Section 4.2: a type or type alias name may be defined once per module and may
    not reuse a built-in type name, and the constructors of one type must have
    distinct names. The section's own examples use fresh names instead of
    redefining `Option` and `List`. (#139)

  The document's own summary of how 0.5 differs from v0.4 now names all three
  sections; it had still said section 4.1.1 was the only one.
- Record fields may begin with a single underscore. The spec and `spec.json`
  only ever reserved keywords and dunder names on Python targets, but the
  compiled-Python prelude refused any `_`-prefixed field, so `r._x` passed
  `geno check` and crashed `geno run` and compiled Python. A single leading
  underscore is admitted for a declared field of a Geno record; other `_`
  attributes and dunders stay blocked. (#142)

### Fixed

This candidate includes the defects found by evaluating the 0.5 line. Three reject programs that
0.4.4 accepted, each of which already failed at run time on at least one
backend, and two change printed output; see Migration above for what that means
for an upgrade.

- **Large integer JSON results** remain exact numbers even beyond Python's
  default 4,300-digit conversion ceiling, including nested collections, map
  keys, and sets. `geno run --json` preserves the raw value and its normalized
  exit status without a traceback or a change to Python's global safety limit.
  The CLI and hosted server share the integer encoder; hosted response-size
  checks remain enforced. (#116)
- **Target validation imports only the selected compiler backend**, avoiding
  JavaScript compiler startup during Python checks (and vice versa) while
  preserving all lowering checks and diagnostics. Performance budgets are
  unchanged.
- **A method defined twice in one impl block is reported** as `E309` at the
  second definition, for the entrypoint and for imported modules alike. Every
  backend dispatched to the last definition, so the first body was dead code
  and `geno test` failed its own examples against the other body. Section 4.6
  of `docs/spec/v0.5.md` states the rule, a fourth place the 0.5 contract
  differs from v0.4. Extracted from #43 (#51).
- **`geno test` and the harness helpers compare example numbers one way.**
  `run_harness_from_source` and `run_harness_from_compiled` now use the same
  relative Float tolerance at every depth. Two `Int` values compare exactly,
  including values beyond Float range. (#111)

- **Checks that now reject what ran wrong before**: an entry `main` that
  declares parameters is reported as `E304`, since every host calls it as
  `main()` (#133); a bare name that two unaliased imports both export is
  reported at its use, naming the qualified forms, where the compilers had been
  dropping it as ambiguous and `--unsafe` silently ran the last import (#132);
  and a repeated type name, a repeated constructor within one type, or a user
  type reusing a built-in name is reported at the definition, in both checkers
  and for imported modules as well as the entrypoint, instead of silently
  replacing the earlier meaning (#139).
- **Float typing on the Python backends**: an `Int` held where a `Float` is
  expected stayed an `Int` in the interpreter and compiled Python whenever
  promotion was skipped -- inside a container, at a parameter, default, named or
  destructured argument, in a lambda or block lambda typed to return `Float`, an
  annotated `for` variable, or a mixed `max`/`min` -- so it printed `7` where
  JavaScript printed `7.0`. The checker records the Float positions per
  expression and both Python backends widen along that shape. (#137)
- **`geno run` keeps output printed before an uncaught runtime error**, on all
  three lanes and on a sandbox timeout, where the process-sandbox path returned
  on the worker's error before printing, `--unsafe` read the buffer only on
  success, and `--json` reported an empty `output`. Example-verification output
  is cleared even when verification fails. (#130)
- **Value semantics with aliased parts**: `var pair = Pair(p, p); pair.a.x = 50`
  no longer changes `pair.b.x`. A field write copies one level of each
  constructor, list or map on its target path before writing, so snapshots still
  share structure and a shared DAG does not expand exponentially. (#129)
- **A variant may share its type's name**: `type Shape = Shape(n: Int) | Other`
  no longer rebinds the constructor to a `typing.Union` in compiled Python, and
  annotations naming such a type spell out the union. (#128)
- **Cross-variant equality under the sandbox**: `Some(1) == None` crashed with
  `NameError: NotImplemented` on the sandboxed `geno run` path. (#127)
- **`match` arms that build different variants of a generic type unify**:
  `| 0 -> Err("zero") | _ -> Ok(n)` is accepted, and `Ok(1)`, `Err("x")`,
  `Ok(2.5)` gives `Result[Float, String]` whatever the arm order. Diagnostics no
  longer leak internal `__fresh_*` names. (#134)
- **An index, field or call after a pipeline stage applies to the stage's
  result**: `xs |> map(_, f)[0]` and `x |> make(_).handler()` no longer leave the
  postfix behind as a separate statement, in either parser. (#138)
- **`geno fmt` tracks blocks and brackets by token**, so a match expression not
  bound by `let`/`var`, a block lambda, an `else`/`if` chain, multi-line
  arguments, list literals and pipelines keep their structure. `format_source`
  re-parses its output and raises `FormatError` rather than writing a file whose
  meaning would change; `geno fmt` reports it and the LSP returns no edit. The
  repository's `.geno` files are reformatted to the new fixed point, whitespace
  only. (#131, #145)
- **`geno constrain --validate` treats `else if` and comprehension `for`/`if` as
  continuations**, so valid programs are no longer reported as having unclosed
  blocks, while statements in a `fn ... do` lambda still open a block. (#144)
- **The REPL** shows buffered output before each result, error, test result and
  failed `:load`, reports positions in the user's own input rather than at the
  hidden wrapper's line 4, and type-checks expressions before running them.
  (#143)
- **Compiled JavaScript prints built-in `JsonValue` and `HttpRequest`/
  `HttpResponse` values canonically** rather than as `JsonFloat(value: 5)` and
  header tuples as arrays, and `_deepCopy` carries a constructor's formatter
  over so a copied value keeps its rendering. (#141)
- **`path_parent`, `path_extension` and `path_is_absolute` agree across
  backends**: the JS runtime follows `posixpath` semantics for repeated
  separators and all-dot names, and the Python drive-letter check accepts only
  ASCII letters, as `docs/reference/capabilities.md` describes. (#146)
- **Self-hosted frontend parity**: transparent type aliases resolve in
  signatures, fields, examples and entrypoints (#118), and a match-expression
  arm evaluates to its expression rather than `()` (#140).
- The VS Code extension's transitive `brace-expansion` moves to 5.0.12. (#125)

## [0.5.0-rc.0] - 2026-09-24

Development pre-release opening the 0.5 series. Not published; the version is
dated because `scripts/check_version_alignment.py` requires a dated heading for
whatever version the tree currently declares, not only at tag time.

### Added

- **Module-level constants**: A `let` written outside any function now binds a constant every function in the file can read, replacing the duplicated literals and `@untested` helper functions the pattern used to need. The initializer is restricted to literal forms — a number, string or boolean, a negated number, or a list or tuple of those — so a module still executes nothing when it is imported; a call, an operator expression or a reference to another binding is rejected at parse time. `var` is rejected at module level, `export let` is not supported yet, and constants are private to the declaring module. Names follow the `snake_case` variable convention. (#72)
- `docs/spec/v0.5.md`, the normative 0.5 language contract. Its only change from
  v0.4 is section 4.1.1, which gives `main() -> Int` executable exit semantics
  per accepted proposal 0001. `docs/spec/v0.4.md` is frozen and untouched.
- `conformance/v0.5/`, a seed corpus so the current and retained previous-minor
  corpora both resolve. It carries forward core 0.4 cases that proposal 0001
  does not change, plus a module-constants case, which is a 0.5-only form that
  the frozen v0.4 corpus cannot carry. Exit-semantics cases follow with the
  runner work.

### Changed

- `spec.json`: `language_series` is now `0.5` and `human_spec` points at the
  0.5 document. `runtime_semantics.entrypoint_result` describes the accepted
  contract again -- an `Int` result normalized modulo 256, output preserved
  before exit, and no traceback for a normal nonzero status.

### Notes

The 0.5 specification scaffolding does not implement the executable entrypoint
change. At this pre-release `geno run` and both backends still display an `Int`
result and exit 0; the observable entrypoint change lands with the CLI and
backend work recorded under Unreleased above.

### Fixed

- **Binding and closure soundness**: Same-scope redeclarations retain their type and effect contract, while existing same-type closure rebinding remains observable. A later `let` clears prior `var` mutability. Local function values resolve their own call metadata, lambdas use an independent control-flow context, and duplicate constructor fields are rejected.
- **Effect inference**: Parameter defaults, assignment targets and indices, and normalized named trait arguments contribute their effects consistently to the checked function signature.
- **Backend value and scope parity**: Record reassignment and destructuring preserve snapshots while explicit mutable collections keep reference semantics. Module constants remain readable through local and parameter shadowing, and Python postconditions no longer break parameter rebinding.
- **Callback and numeric parity**: Compiler optimizations require the resolved builtin identity, preserving shadowed callbacks. Ordered comparisons involving NaN now agree across all three execution engines.
- **Project and editor workflows**: Generated libraries are consumable by the documented import, explicit-file checks retain manifest module mappings, bundles use canonical file discovery, and rename includes reverse importers and converts protocol positions for non-BMP text.
- **Quality gates**: Differential fuzzing grants its required print capability and fails on crashes, timeouts, missing required backends, or wrong results. CI explicitly requires Node. Runtime benchmark scoring checks correctness before timing and retains failed workloads in the denominator. Analysis and experiment reports share McNemar statistics and chi-square survival calculations; Student t tails and intervals now use the same distribution rather than inconsistent approximations.
- **JSON execution capabilities**: `geno run --json` now honors its documented fail-closed default. Programs that print must pass `--cap print`; JSON mode continues to use the in-process embedding API.
- **Hosted lifecycle**: Shutdown drains active handlers and workers within a bounded grace period. Readiness checks exercise the hosted worker and return HTTP 503 on failure; `/readyz` and `/livez` distinguish readiness from liveness, and container probes use readiness.

## [0.4.4] - 2026-09-20

### Compatibility

This release takes one deliberate exception to the rule in
`docs/operations/compatibility-policy.md` that a patch preserves documented
source behavior. The target-less check change below (#70) rejects programs that
0.4.3 accepted: with no declared target and an entry module that defines `main`,
`geno check` and `geno test` now validate lowering through the Python backend,
so a name that backend reserves is reported there instead of passing.

The exception is taken because the old behavior withheld a guarantee rather than
made one. 0.4.3 published target-less checks as a "permissive language check
with no backend-lowering guarantee", and the practical effect was that `geno
check` and `geno test` reported success for programs the default `geno run`
could not execute. Every program the new rule rejects was already failing at run
time with the same diagnostic; what changes is when the author is told. No
program that ran on 0.4.3 stops running on 0.4.4, and the frozen conformance
corpus is unchanged.

Migration: rename the binding or parameter that collides. The diagnostic names
the identifier — `'len' is a reserved runtime name and cannot be used as a
function parameter name`.

Rejected from 0.4.4:

```geno
func widen(len: Int) -> Int
    example 2 -> 4
    return len * 2
end func

func main() -> Int
    return widen(2)
end func
```

Accepted:

```geno
func widen(count: Int) -> Int
    example 2 -> 4
    return count * 2
end func

func main() -> Int
    return widen(2)
end func
```

Library modules that define no `main` are unaffected and stay permissive, as
does the `geno.check()` embedding API unless a target is selected explicitly.

### Added

- **Tuple patterns in `match` arms**: `| (a, b) -> ...` now parses, type checks, and runs on the interpreter and both backends, including nested forms such as `| Some((key, value)) ->`. Because every value of a tuple type has that type's arity, an arm whose elements are all variables or wildcards is exhaustive and needs no default arm; mismatched arity, non-tuple scrutinees, and single-element tuple patterns are rejected with targeted diagnostics. (#79)
- **Leading `|` before the first ADT variant**: Sum-type definitions may now begin their variant list with a bar, so idiomatic multiline domain models parse. This — not a one-physical-line requirement — was what rejected multiline sum types. (#78)
- **Bare-arrow zero-argument examples**: `example -> value` is now accepted alongside `example () -> value` and produces an identical AST; example inputs are still arity-checked against the parameter list. (#71)
- **CLI latency ratchets**: `benchmarks/cli_latency.py` times six frozen CLI scenarios and `make perf-ratchets` enforces them against `perf-budgets.toml` in the weekly scheduled-quality workflow. (#48)
- **Hosted header-read budget**: `GENO_HEADER_TIMEOUT_SECONDS` (default 5s) bounds reading a request head independently of the full request budget. (#61)

### Changed

- **Target-less checks validate the default run backend**: `geno check` and `geno test` on a program with no declared target now validate lowering through the backend the default `geno run` uses, so a suite can no longer pass for a program that cannot run. Library modules that define no `main` are never executed by `geno run` and stay exempt, since standalone lowering reserves more names than project lowering. (#70)
- **Interpreter collection caps are now interpreter-local**: Builtins installed on an `Interpreter` enforce only that interpreter's `SandboxConfig.max_collection_size`. The process-wide `geno.builtins.set_max_collection_size` cap no longer tightens (or is clobbered by) live interpreters; it now governs only direct module-level builtin calls. Embedders that relied on the global setter to constrain interpreters should pass the limit via `SandboxConfig` instead.
- **Entrypoint results render in Geno syntax**: Compiled Python and the `geno run` result line now format `main()`'s value through the Geno formatter instead of Python's `repr`, so a `Bool` displays as `true` and a `List[String]` as `["a", "b"]`, matching the JavaScript backend. (#61)
- **Non-finite float display**: `inf`, `-inf`, and `nan` now render identically in text output and `to_string` across the interpreter and both backends, including nested in collections. JSON serialization rules for non-finite numbers are unchanged. (#89)
- **Faster `geno run` startup**: The default isolated `geno run` no longer imports the lexer, parser, typechecker, and dependency graph in the parent process, which the worker owns; `run-hello` drops about 15% and `run-medium` about 7%. (#48)
- **VS Code extension build runtime**: Building and packaging the extension now requires Node.js 22–24, matching the VSCE 4 toolchain. The supported VS Code API remains unchanged. (#104)

### Fixed

- **Sandboxed compile diagnostics**: The default process-isolated `geno run` reported backend rejections as `Compiler Error: the isolated frontend failed safely (CompileError)`, hiding the only actionable text. Compile errors now surface the same message the `geno compile` path prints. (#69)
- **`geno-form` example runs again**: Two local bindings named `len` shadowed a reserved runtime name, so the shipped app failed the default `geno run`.
- **Silent truncation in `geno run`**: The entrypoint guard wrapped the whole `main()` call in `except NameError: pass`, so a `NameError` raised inside a program stopped execution mid-run while the CLI still exited 0. The guard now covers only the entrypoint name lookup, and the runtime prelude no longer names builtins the sandbox withholds. (#61)
- **Block scoping in the Python backend**: A `let` inside a nested block leaked into the enclosing scope, so a program whose `if` body rebound a name returned the inner value where the interpreter and the JavaScript backend returned the outer one — a silently wrong answer across `if`, `else`, `while`, `for`, `try`/`catch` and match arms. Block bindings that shadow an enclosing one are now renamed in the emitted Python. (#61)
- **Same-block rebinding on the JavaScript backend**: `let x = 1` followed by `let x = 2` in one block emitted two `const` declarations, a `SyntaxError`, so the program did not run at all while the other two backends treated it as a rebind. The later occurrences are now plain assignments. (#61)
- **`Int` literals in `Float` positions**: Call arguments, container elements, tuple slots, constructor fields, `Option` payloads and JSON values annotated `Float` rendered as `3` on the interpreter and compiled Python but `3.0` on JavaScript; `json_to_string(JsonFloat(3))` disagreed in machine-readable output. All three paths now use the widened type the typechecker already records. (#61)
- **Order-independent list inference**: Element inference anchored on the first element, so `[2.5, 1, 3]` inferred `List[Float]` while the same values as `[1, 2.5, 3]` were rejected with "expected Int, got Float", even under an explicit `List[Float]` annotation. Inference now widens to the type every element is compatible with; genuinely mixed lists still fail. (#61)
- **Top-level `String` results**: The interpreter's `=>` result line quoted a top-level `String` where both compiled runtimes print it bare, so `geno run` and `geno run --unsafe` disagreed on the same program. Diagnostics keep their quotes. (#61)
- **Call evaluation order and closure bindings**: Named-argument calls now evaluate their arguments in source order rather than parameter order, and a lambda that assigns to an enclosing `var` updates that binding, in compiled Python and JavaScript as in the interpreter. (#89)
- **Import resolution at scale**: Deep import chains resolve without rebuilding the graph, source snapshots and private import namespaces survive normalization, imported namespaces are scoped to their module environment, manifested package identities are preserved in direct imports, and LSP project caches refresh after watched-file changes without losing diagnostics. (#88)
- **Formatter and REPL**: The formatter preserves multiline string literals, and the REPL completes multi-line and nested definitions, including test definitions, instead of ending them at the first blank line. (#90)
- **VS Code diagnostics and launch reporting**: Stale fallback diagnostics are discarded, language-server launch failures are reported instead of failing silently, and startup is cancelled across deactivation. (#86)
- **VS Code API compatibility**: The extension keeps working against VS Code 1.91 after the `@types/vscode` bump. (#83)
- **Experiment analysis**: Statistical corrections, valid experiment pairings, unavailable comparisons, and strict significance cutoffs are preserved in the benchmark and experiment analysis tooling. (#85)
- **Coverage gate**: The 80% floor now measures production code only; `geno/tests/*` is omitted in both the run and report phases, and the coverage shards install the LSP extra so `geno/lsp_server.py` is actually exercised. (#61)
- **Documentation**: The guide and pitfalls reference now state that `parse_int` and `parse_float` return `Option`, not `Result`. (#93)
- **Await in synchronous entrypoints**: A `main` without an `async` modifier can now await in its body or executable contracts on both compiled backends and the process-isolated CLI, matching interpreter behavior for ordinary entrypoint execution. (#108)
- **Browser apps**: Single-file and directory builds share canvas initialization and responsive sizing without enlarging small canvases. Scaled pointer coordinates and input resets after focus or visibility loss prevent misplaced pointers and stuck controls. Builds create missing output parents, and package-lock setup failures produce actionable diagnostics. (#102)
- **Actionable diagnostics**: Missing standard-library and project imports, named arguments, function examples, and examples rejected by preconditions now explain the relevant correction. Import hints respect aliases and exported declarations. (#102, #107)
- **Authoring guidance**: Guides explain reserved `result` bindings, immutable list updates, preconditions, module imports, and preserving full precision in Float examples while comparison paths remain inconsistent. (#109)

### Security

- **Capability grants from untrusted arguments**: Both standalone compiled runtimes scanned the entire argv for `--cap`, including everything after `--`, so a program forwarding untrusted arguments could grant itself `env`, `fs`, `process`, or `http`. Both parsers now stop at the first `--`, matching `cli_args()`. This is a documented-behavior tightening permitted for a patch release because the previous behavior allowed a capability bypass. (#61)
- **Half-open connection exhaustion**: Reading a request head shared the full 30s request budget, so `MAX_CONNECTIONS` partially-sent headers made every endpoint, including `/healthz` and `/metrics`, unreachable with no log line or counter. Head reads now have their own shorter budget and refused connections are counted and warned about. (#61)
- **Hosted input validation and result bounds**: Workers enforce `GENO_MAX_RESPONSE_BODY_BYTES` before sending results over IPC and oversized `/run` and `/constrain` results return HTTP 413; request source, module source, and constraint prefixes must be valid Unicode, and unpaired JSON surrogate escapes and out-of-range timeouts return HTTP 400. (#87)
- **Dependency maintenance**: Updated `cryptography` in the release lock and the VS Code extension's transitive `js-yaml`, `qs`, and `undici` dependencies. (#42, #49, #52, #99, #100)

## [0.4.3] - 2026-08-01

### Changed

- **Toolchain and supply-chain maintenance**: Updated Ruff, pinned GitHub Actions, the PyPI publishing action, and VS Code extension transitive dependencies.
- **Release validation**: Release tags must now point to `main` and be newer than existing changelog and repository releases, with dated, non-empty release notes.

### Fixed

- **Direct Unit results**: `geno run --unsafe` now keeps `main() -> Unit` silent, matching process-sandboxed and standalone execution.

## [0.4.2] - 2026-07-22

### Added

- **Normative language contract**: Published the versioned v0.4 specification, machine-checked effects, diagnostics, integer bounds, compatibility metadata, and a frozen all-backend conformance corpus.
- **Language evolution process**: Added explicit governance, a design-proposal process, compatibility policy, and evidence-based serious-beta qualification criteria.
- **Scheduled quality**: Added a weekly all-backend conformance and 2,000-case differential-fuzz workflow with retained failure artifacts.
- **Editor formatting**: Added LSP whole-document formatting for saved, unsaved, and virtual Geno buffers.
- **Filesystem inspection**: Added capability-gated `fs_metadata`, `fs_symlink_metadata`, and `fs_canonicalize` with typed file kinds, portable millisecond timestamps, scoped symlink handling, and compiled Python/Node parity.

### Changed

- **Target-aware lowering contract**: Checks that name an execution target now validate the checked AST with its canonical compiler backend in memory; manifest compilation selects compatible execution profiles while preserving `compile --target python|js` and adding an explicit `--profile` override.
- **Stable diagnostic ownership**: Parser, type, and effect errors now emit granular E202 and E302-E311 codes at their owning compiler sites, including through the LSP exception bridge.
- **Release qualification**: The release gate and local release plan now require the frozen corpus on the interpreter, Python, and JavaScript backends.

### Fixed

- **Compiled Node HTTP**: Repaired CommonJS and ES-module HTTP with an `http`-only, stdin-based bridge; streamed bounded responses; enforced private-address and redirect policy; preserved duplicate headers and non-2xx bodies across Python and Node; and kept expected `http_request` failures trace-free.
- **Target/compiler parity**: Target-aware CLI and embedding checks now reject backend-invalid integer literals, record fields, runtime names, and project namespaces before artifact emission; invalid manifests cannot be bypassed by an explicit check target, and expected compiler/build failures no longer expose Python tracebacks or partial output paths.
- **VS Code packaging**: The release gate now verifies the transitive `vscode-jsonrpc` runtime is present in the VSIX.
- **Python 3.10 HTTP redirects**: Added explicit HTTP 308 dispatch for compiled Python and hosted callbacks while preserving redirect validation, limits, and method/body semantics.
- **Entrypoint ownership**: Only `main` declared in the selected entry module is invoked; an imported module that exports `main` no longer becomes an accidental entrypoint.
- **Generated ESM import safety**: Node ES modules invoke `main` only when run directly, while imports remain inert and browser-targeted ESM remains free of Node-only imports.
- **v0.4 result compatibility**: Kept `main() -> Int` as an ordinary displayed result with successful process status in primary `geno run` and standalone compiled artifacts; embedding calls return the value without terminating their host, while the legacy self-hosted command adapter remains unchanged.

## [0.4.1] - 2026-07-19

### Changed

- **Supply-chain maintenance**: Consolidated and pinned GitHub Actions, hash-locked release tooling, migrated development coverage to pytest-cov 7, and updated the VS Code TypeScript and language-client toolchain (#9, #13, #15, #16, #17, #20)
- **Extension dependencies**: Resolved known VS Code extension dependency vulnerabilities and refreshed the lockfile (#14)
- **Security support window**: The supported release line is now 0.4.x

### Fixed

- **VS Code packaging**: Packaged the Python language-server runtime in the VSIX and aligned the extension build and release gate (#13)
- **CI timeout stability**: Isolated timeout scheduler regressions and stabilized extension checks after the 0.4.0 release (#13)
- **macOS benchmark stability**: Gave research-only Python benchmark workers an explicit finite memory budget instead of relying on production defaults (#22)
- **macOS sandbox stability**: Raised Darwin's production address-space growth budget to 512 MiB, probed XNU's accepted boundary without architecture assumptions, and stabilized the cross-platform config-transport smoke test (#23, #24)

### Security

- **Untrusted process execution**: Hardened POSIX and Windows worker startup, supervision, cleanup, resource enforcement, compiled-runtime prelude handling, and regex limits (#17)
- **Package integrity**: Hardened source capture, lock and state files, symlink handling, crash recovery, content hashing, and release dependency validation (#17)
- **Hosted boundaries**: Enforced Host, forwarded-header, request-body, and module boundaries and corrected deployment health checks (#17)
- **Regression coverage**: Added security tests for the discovered vulnerability classes; the in-process embedding API remains cooperative and generated JavaScript remains for trusted environments (#17)

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
