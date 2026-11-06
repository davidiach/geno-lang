# Capability Reference

Geno uses a capability-based security model. By default, sandboxed execution
blocks all I/O operations. You grant specific capabilities to unlock the
builtins your program needs.

## Quick Reference

| Capability | Builtins Unlocked | Use Case |
|------------|-------------------|----------|
| `print` | `print` | Console output |
| `clock` | `clock_now`, `clock_format`, `clock_parse`, `clock_elapsed`, `sleep_ms`, `datetime_now`, `datetime_format`, `datetime_parse`, `datetime_elapsed` | Timestamps, timing |
| `random` | `random_int`, `random_float`, `math_random_int`, `math_random_float` | Random number generation |
| `fs` | `fs_read_text`, `fs_write_text`, `fs_list_dir`, `fs_exists`, `fs_metadata`, `fs_symlink_metadata`, `fs_canonicalize` | File I/O and metadata |
| `http` | `http_fetch`, `http_post`, `http_request` | HTTP requests (http/https only) |
| `regex` | `regex_match`, `regex_find_all`, `regex_replace` | Regular expressions |
| `env` | `env_get`, `env_get_or`, `cli_args` | Environment variables, CLI args |
| `process` | `exec`, `exec_with_input`, `spawn`, `spawn_with_input` | Process execution |
| `stdin` | `stdin_read_all` | Standard input |
| `serve` | `http_listen`, `http_route`, `http_respond` | HTTP server |

## Capabilities by Execution Surface

Capability gating behaves differently depending on how you run your program.

### Compiled mode (default `geno run`)

In the default compiled mode, `geno run` compiles your program to Python and
executes it inside a process sandbox: a worker process with a hard wall-clock
kill at `--timeout` and memory, CPU, file-size and process limits. Without
`--cap`, `print`, `clock`, `random` and the always-available builtins (pure
computation) work, and **all other gated builtins are denied**.

```bash
# Compiled mode — print, clock, random, and pure builtins work
geno run myfile.geno
```

`--cap` grants **exactly** the capabilities you list, for examples and `main`
alike, and the program stays in the process sandbox
([proposal 0013](../proposals/0013-isolated-run-capabilities.md)). The list
replaces the defaults rather than adding to them, so name `print` too if the
program prints:

```bash
# Program arguments: cli_args() needs env
geno run --cap env,print main.geno -- hello world

# Files under the project, the working directory and the path argument
geno run --cap fs,print report.geno -- data/input.csv

# Regular expressions
geno run --cap print,regex lint.geno

# One environment variable, by exact name
geno run --cap env,print --allow-env HOME main.geno
```

| Capability | Default lane | Notes |
|---|---|---|
| `print`, `clock`, `random`, `regex` | granted with `--cap` | `print`, `clock` and `random` also without `--cap` |
| `env` | granted with `--cap` | `cli_args()` works with `env` alone; `env_get`/`env_get_or` read only names allowed with `--allow-env NAME` |
| `fs` | granted with `--cap` | same roots and policy as `--unsafe` (below), plus write quotas |
| `http` | granted with `--cap` on Linux and macOS | refused before launch on Windows for now |
| `stdin` | refused before launch | implemented; waits for its platform gate (below) |
| `process`, `serve` | refused before launch | use `--unsafe --cap ...` for trusted code |

The lane's rules, which [spec v0.7 section 9.11.1](../spec/v0.7.md) states in
full:

- **Refusals happen before the program starts.** `geno run --cap process` and
  `--cap serve` exit 1 with a message naming `--unsafe`: programs started by
  `process` would run outside the sandbox, and a `serve` listener would outlive
  the run's wall-clock limit. A capability the lane cannot grant yet, or not on
  this platform, exits 1 with "not yet available in the process-isolated run
  mode".
- **Environment variables need an allowlist.** `--cap env` exposes no variable
  by itself. Each `--allow-env NAME` (repeatable, exact name) lets
  `env_get` and `env_get_or` read that one variable from your environment;
  reading any other name fails with E412 ("not allowed by the host env
  policy"), even if the variable is unset. Names are case-insensitive on
  Windows, as the Windows environment is, and exact elsewhere. `--allow-env`
  needs an explicit `--cap env` and applies only to this lane.
- **Filesystem writes have quotas.** With `fs`, each `fs_write_text` may write
  at most `--max-file-size-bytes` and the whole run at most
  `--max-total-write-bytes`; both default to 64 MiB with `fs` (0 without it,
  and 0 disables writes). Bytes are counted after newline translation
  (`\r\n` on Windows), reserved before the file is opened and never refunded,
  even for a rewrite. A quota rejection returns `Err` and leaves the file
  unchanged. The `GENO_FS_*` variables below are read only when `fs` is
  granted; `GENO_HTTP_ALLOW_PRIVATE` only when `http` is.
- **TLS trust follows yours.** With `http`, the worker verifies certificates
  against the same trust store as `--unsafe`: your `SSL_CERT_FILE` and
  `SSL_CERT_DIR`, when set (for example a corporate CA bundle), are passed to
  the worker's TLS library and to nothing else. The program cannot read them.
- **Examples share the run.** Examples run before `main` with the same grant,
  the same environment snapshot, filesystem quotas and HTTP policy, and the
  same deadline and output budget: example output is not shown, but it counts
  against `--max-output-length`. Their effects are real. Use
  `--no-check-examples` to skip them in an operational run.
- **Hints stay isolated.** A denial of a capability the lane can grant ends
  with the isolated command to rerun, after the report (0.7 line: the Geno
  stack and the denied call's source line come first):

  ```text
  Stack trace (most recent call last):
    main.geno:2:11 in main
        print(cli_args())
              ^
  Runtime Error: Capability denied: 'cli_args' requires the 'env' capability
  Hint: grant it with --cap; the program stays process-isolated:
    geno run --cap clock,env,print,random main.geno -- hello
  ```

  Only `process`, `serve` and the capabilities refused on this platform get
  `--unsafe` advice.

Programs that read standard input (`stdin_read_all`) still need
`--unsafe --cap stdin,...` (trusted code only). The isolated lane's lazy,
size-bounded stdin reader (milestone 3 of proposal 0013) is implemented, but
`geno run --cap stdin` stays refused before launch on every platform until the
proposal's stdin gate (handle setup, inheritance, timeout and interruption
cleanup on Linux, macOS and Windows) passes. On Windows, programs that use
`http` likewise need `--unsafe --cap http,...` until the HTTP gate has Windows
evidence.

### Standalone compiled output (`geno compile`)

When you compile to a standalone Python file, the compiled output parses
`--cap` flags from its own `sys.argv`:

```bash
geno compile myfile.geno -o out.py
python out.py --cap fs --cap http
```

Without `--cap`, all gated builtins are denied (fail-closed).

Standalone Node.js output follows the same fail-closed capability model. Its
synchronous HTTP helpers use an internal bridge gated by `--cap http`; the
bridge does not expose process execution to Geno code, so `--cap process` is
neither required nor supported on this target. Browser output uses browser
networking APIs and the same `http` capability, subject to browser CORS policy.

### Interpreter mode (`geno run --unsafe --cap`)

With `--unsafe`, the program runs in the tree-walking interpreter in the CLI's
own process, without process isolation or its hard limits: use it only for
trusted code, and for the capabilities the default lane refuses (`process`,
`serve`, `stdin`, and `http` on Windows). When `--cap` is specified,
**only the exact capabilities you list** are granted — there are no implicit
defaults. `--unsafe` reads the whole environment with `env`, has no write
quotas, and streams output.

```bash
# Only process and print are granted — clock, random, http are denied
geno run myfile.geno --unsafe --cap process --cap print

# A server keeps running past any run deadline
geno run myfile.geno --unsafe --cap serve,env
```

If you omit `--cap` with `--unsafe`, the interpreter grants the same defaults
as the default lane: `clock`, `print` and `random`. No lane leaves capability
gating disabled.

### JSON mode (`geno run --json`)

Uses the embedding API in the current process, with cooperative timeout checks.
If you omit `--cap`, gated builtins are denied by default. Grant each required
capability explicitly, for example `geno run myfile.geno --json --cap print`.
JSON mode installs no host callbacks, so a granted `fs`, `http`, `stdin`,
`process` or `serve` builtin fails with E413 ("Host callback not provided").
Use the hosted server or a caller-managed worker process when JSON results are
needed for untrusted code; JSON mode itself does not provide process isolation.

Integer results remain exact JSON numbers, including values beyond Python's
default 4,300-digit conversion limit. This also applies inside collections;
map keys keep their string representation. Geno's integer resource limit still
applies, and serialization does not change Python's process-global digit limit.
Consumers must use a JSON number parser that supports the required precision
(for example, Python's `json.loads(text, parse_int=decimal.Decimal)`).

### Server (`geno serve`)

The HTTP server allows **print**, **clock**, and **random** by default, but a
request must still list the capabilities it wants. Omitting `capabilities`
grants no gated builtins. Operators can expand the allowlist with server
configuration; clients still receive only the requested subset unless an
embedding deliberately sets `default_request_capabilities`.

### Host Resource Scope

Granting `fs`, `http`, or `process` unlocks host resources. In the default
process-isolated `geno run` lane, direct interpreter mode and standalone
Python output, built-in host callbacks apply the scoped policies below (the
default lane refuses `process`):

Generated Node output is a trusted-runtime deployment: `--cap` gates which
APIs are wired, not an OS sandbox. Node `fs` builtins use host APIs directly
and do not honor Python filesystem roots or read-only policy. Geno `process`
builtins are unavailable on `node-cli`; the child process used internally by
the HTTP bridge is not exposed as process capability. The Node HTTP bridge
does enforce the scheme, private-address, redirect, and response-size policies
below, but those checks do not sandbox the artifact. See
[Security Policy](../../SECURITY.md#javascript-backend-limitations).

- `fs` paths are resolved under configured filesystem roots. By default the
  only root is the current working directory and absolute paths are rejected.
  Without an explicit root policy, the CLI also scopes `--cap fs` to the source
  project and explicit path-like program arguments, allowing absolute paths
  inside that scope so tools can receive file paths after `--`. Set
  `GENO_FS_ROOTS` to an `os.pathsep`-separated root list to replace those automatic
  CLI grants; an explicitly empty list is rejected by the CLI. With configured
  roots, absolute paths are rejected by default. Set
  `GENO_FS_ALLOW_ABSOLUTE=1` only for trusted deployments that need absolute
  paths inside those roots. The CLI also honors an explicitly false
  `GENO_FS_ALLOW_ABSOLUTE` value when using automatic roots.
  Set `GENO_FS_READ_ONLY=1` to allow filesystem reads
  while rejecting `fs_write_text`. Symlink escapes are rejected after realpath
  resolution. `fs_metadata` follows the final link, `fs_symlink_metadata`
  inspects that link itself, and `fs_canonicalize` returns an existing absolute
  real path with `/` separators. A final link inside a root may be inspected,
  but followed metadata and canonicalization still reject target escapes. See
  [Filesystem Metadata and Canonicalization](filesystem.md) for the full
  behavior contract.
- `http` requests allow only `http` and `https` and reject loopback,
  link-local, private, multicast, reserved, and unspecified targets by
  default. Redirect targets are checked with the same policy. Set
  `GENO_HTTP_ALLOW_PRIVATE=1` only for trusted local development.
- `process` callbacks require absolute executable paths by default and run
  with a minimal environment unless `env` is also granted. Set
  `GENO_PROCESS_EXECUTABLES` to an `os.pathsep`-separated executable allowlist
  for tighter deployments. Set `GENO_PROCESS_ALLOW_PATH_SEARCH=1` only when
  trusted code may resolve programs through `PATH`.

HTTP response bodies and process stdout/stderr are read through bounded paths
before they are converted into Geno values, so sandbox collection limits are
enforced without first materializing unbounded host output in memory.
Process stdout and stderr are captured through pipes and checked incrementally,
with a separate character limit for each stream. Exceeding either limit stops
the command; its output is never spooled to disk. Explicit process input uses
a temporary file bounded by the collection limit (up to four UTF-8 bytes per
character). These callbacks supervise the invoked command, not an isolated
process tree; use an OS sandbox when executing untrusted external programs.

### Embedding API

```python
import geno
from geno import RunConfig

# capabilities=None (default) — fail closed, gated builtins denied
result = geno.run(source)

# capabilities=set() — deny all gated builtins
result = geno.run(source, config=RunConfig(capabilities=set()))

# Grant specific capabilities
result = geno.run(source, config=RunConfig(capabilities={"print", "fs", "http"}))
```

## Always-Available Builtins

These builtins are pure computation and require no capability grant:

**List operations:**
`length`, `head`, `tail`, `append`, `concat`, `set_at`, `slice`, `filter`,
`map`, `fold`, `contains`, `take_while`, `all`, `reverse`, `range`, `sort`,
`sort_by`, `zip`, `enumerate`, `flat_map`, `list_length`, `list_map`,
`list_filter`, `list_all`, `list_any`, `list_chunk`, `list_drop`,
`list_enumerate`, `list_find`, `list_find_index`, `list_flatten`,
`list_fold_right`, `list_group_by`, `list_intersperse`, `list_take`,
`list_zip`, `list_get`, `list_first`, `list_last`, `sum`, `float_sum`,
`list_min`, `list_max`, `is_empty`, `list_index_of`, `count`, `unique`

**String operations:**
`split`, `join`, `trim`, `to_lower`, `to_upper`, `split_once`, `starts_with`,
`ends_with`, `replace`, `to_chars`, `substring`, `format`, `string_split`,
`string_join`,
`string_replace`, `string_to_upper`, `string_to_lower`, `string_starts_with`,
`string_ends_with`, `string_contains`, `string_split_once`, `string_char_at`,
`string_index_of`, `string_last_index_of`, `string_pad_left`,
`string_pad_right`, `string_repeat`, `string_substring`, `string_trim`,
`string_trim_end`, `string_trim_start`, `string_is_empty`, `string_reverse`,
`string_lines`, `string_words`, `string_is_digit`, `string_is_alpha`,
`string_is_alphanumeric`, `string_is_whitespace`, `string_is_upper`,
`string_is_lower`

**Math operations:**
`add`, `subtract`, `multiply`, `divide`, `sqrt`, `floor`, `ceil`, `round`,
`max`, `min`, `clamp`, `abs`, `bit_or`, `math_abs`, `math_ceil`,
`math_clamp`, `math_cos`, `math_e`, `math_floor`, `math_log`, `math_max`,
`math_min`, `math_pi`, `math_round`, `math_sin`, `math_sqrt`

**Conversions:**
`parse_int`, `parse_float`, `to_string`, `float_to_int`, `int_to_float`

**Option operations:**
`is_some`, `is_none`, `unwrap`, `unwrap_or`, `option_and_then`,
`option_expect`, `option_flatten`, `option_is_none`, `option_is_some`,
`option_map`, `option_to_result`, `option_unwrap_or`

**Result operations:**
`result_and_then`, `result_expect`, `result_is_err`, `result_is_ok`,
`result_map`, `result_map_err`, `result_to_option`, `result_unwrap`,
`result_unwrap_or`

**Errors:**
`panic`

**Map operations:**
`map_insert`, `map_get`, `map_size`, `map_contains_key`, `map_remove`,
`map_keys`, `map_values`, `map_entries`, `map_filter_map`, `map_from_entries`,
`map_from_list`, `map_map_values`, `map_merge`, `map_get_or`, `map_is_empty`

**Path operations** (pure string manipulation):
`path_extension`, `path_filename`, `path_is_absolute`, `path_join`,
`path_parent`. The portable representation uses `/`; `path_is_absolute` also
recognizes canonical Windows drive paths such as `C:/work/file`.

**Array operations:**
`array_new`, `array_from_list`, `array_get`, `array_set`, `array_length`,
`array_to_list`, `array_fill`, `array_copy`

**MutableMap operations:**
`mutable_map_new`, `mutable_map_set`, `mutable_map_get`,
`mutable_map_contains`, `mutable_map_delete`, `mutable_map_size`,
`mutable_map_is_empty`, `mutable_map_keys`

**Vec operations:**
`vec_new`, `vec_push`, `vec_get`, `vec_set`, `vec_length`, `vec_pop`,
`vec_to_list`, `vec_from_list`

**Set operations:**
`set_new`, `set_from_list`, `set_add`, `set_remove`, `set_contains`,
`set_size`, `set_is_empty`, `set_to_list`, `set_union`, `set_intersection`

**Data parsing:**
`json_parse`, `json_stringify`, `json_stringify_pretty`, `json_to_string`, `csv_parse`,
`csv_parse_with_headers`, `toml_parse`

**Character operations:**
`char_code`, `from_char_code`

## Browser Graphics and Input Builtins

Graphics and input builtins require no capability grant, but they are available
only for the `browser` target. Target-aware check and compile paths reject them
for `python-cli`, `node-cli`, and `python-hosted`. Direct interpreter execution
keeps explicit compatibility fallbacks: drawing calls are no-ops,
`screen_width`/`screen_height` return `800`/`600`, keyboard and mouse predicates
return `false`, pointer coordinates return `0`, and text input returns `""`.

`clear_screen`, `draw_rect`, `draw_rect_outline`, `draw_circle`, `draw_line`,
`draw_text`, `screen_width`, `screen_height`, `is_key_down`, `is_key_pressed`,
`mouse_x`, `mouse_y`, `is_mouse_down`, `is_mouse_clicked`, `get_text_input`,
`clear_text_input`

## Capability Denied Errors

If your program calls a gated builtin without the required capability, you
will see error **E412**:

```
Error: Capability denied: 'fs_read_text' requires the 'fs' capability
```

On the 0.7 development line (Draft
[proposal 0018](../proposals/0018-runtime-failure-diagnostics.md)) the
denial is reported like every runtime failure, at the gated call and with
the Geno stack that reached it, on every lane and in compiled programs. In
the default `geno run` lane:

```text
Stack trace (most recent call last):
  myfile.geno:2:11 in main
      match fs_read_text("notes.txt") with
            ^
Runtime Error: Capability denied: 'fs_read_text' requires the 'fs' capability
Hint: grant it with --cap; the program stays process-isolated:
  geno run --cap clock,fs,print,random myfile.geno
```

The JSON diagnostic (`geno run --json`, `geno.api.run()`) carries the same
`location` and `stack`.

**Fix:** Add `--cap fs` to your command, or include `"fs"` in your
`RunConfig.capabilities` set. `--cap` replaces the default grant, so list
every capability the program uses: `geno run --cap fs,print myfile.geno`. In
the default `geno run` lane the hint after the error prints the full command.

## Target Availability

Not all capabilities are available on all compilation targets. For example,
`fs`, `env`, and `process` are not available in browser builds. See
[Supported Targets](../SUPPORTED_TARGETS.md) for the full availability matrix.
