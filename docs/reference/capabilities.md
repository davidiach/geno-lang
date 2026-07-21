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
executes it inside a process sandbox. `print`, `clock`, `random`, and
always-available builtins (pure computation) work. **All other gated builtins
are denied**.

```bash
# Compiled mode — print, clock, random, and pure builtins work
geno run myfile.geno
```

The default `geno run` mode is process-isolated and does not currently support
host capability callbacks. Use `--unsafe --cap ...` only when you explicitly
want direct interpreter execution with host callbacks:

```bash
# Explicit direct interpreter mode with fs and print granted
geno run --unsafe --cap fs,print myfile.geno
```

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

With `--unsafe`, the program runs in the tree-walking interpreter without
process isolation. When `--cap` is specified, **only the exact capabilities
you list** are granted — there are no implicit defaults. Passing `--cap`
without `--unsafe` or `--json` fails fast rather than silently changing the
execution mode.

```bash
# Only fs and print are granted — clock, random, http are denied
geno run myfile.geno --unsafe --cap fs --cap print

# Grant multiple capabilities
geno run myfile.geno --unsafe --cap print,fs,http
```

If you omit `--cap` entirely with `--unsafe`, the trusted direct interpreter
keeps its legacy ungated behavior. Use `--cap` to restrict it.

### JSON mode (`geno run --json`)

Uses the embedding API. If you omit `--cap`, gated builtins are denied by
default. Grant each required capability explicitly.

### Server (`geno serve`)

The HTTP server allows **print**, **clock**, and **random** by default, but a
request must still list the capabilities it wants. Omitting `capabilities`
grants no gated builtins. Operators can expand the allowlist with server
configuration; clients still receive only the requested subset unless an
embedding deliberately sets `default_request_capabilities`.

### Host Resource Scope

Granting `fs`, `http`, or `process` unlocks host resources. In direct
interpreter mode and standalone Python output, built-in host callbacks apply
the scoped policies below:

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
  The CLI also scopes `--cap fs` to the source project and explicit path-like
  program arguments, so tools can safely receive file paths after `--`. Set
  `GENO_FS_ROOTS` to an `os.pathsep`-separated root list and
  `GENO_FS_ALLOW_ABSOLUTE=1` only for trusted deployments that need absolute
  paths inside those roots. Set `GENO_FS_READ_ONLY=1` to allow filesystem reads
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

### Embedding API

```python
import geno
from geno import RunConfig

# capabilities=None (default) — fail closed, gated builtins denied
result = geno.run(source)

# capabilities=set() — deny all gated builtins
result = geno.run(source, config=RunConfig(capabilities=set()))

# Grant specific capabilities
result = geno.run(source, config=RunConfig(
    capabilities={"print", "fs", "http"}
))
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
`list_zip`

**String operations:**
`split`, `join`, `trim`, `to_lower`, `to_upper`, `split_once`, `starts_with`,
`ends_with`, `replace`, `to_chars`, `sort_strings`, `contains_substring`,
`repeat_string`, `substring`, `format`, `string_split`, `string_join`,
`string_replace`, `string_to_upper`, `string_to_lower`, `string_starts_with`,
`string_ends_with`, `string_contains`, `string_split_once`, `string_char_at`,
`string_index_of`, `string_last_index_of`, `string_pad_left`,
`string_pad_right`, `string_repeat`, `string_substring`, `string_trim`,
`string_trim_end`, `string_trim_start`

**Math operations:**
`add`, `subtract`, `multiply`, `divide`, `sqrt`, `floor`, `ceil`, `round`,
`max`, `clamp`, `abs`, `square`, `bit_or`, `math_abs`, `math_ceil`,
`math_clamp`, `math_cos`, `math_e`, `math_floor`, `math_log`, `math_max`,
`math_min`, `math_pi`, `math_round`, `math_sin`, `math_sqrt`

**Conversions:**
`parse_int`, `parse_float`, `to_string`, `float_to_int`, `int_to_float`

**Option operations:**
`is_some`, `is_none`, `unwrap`, `unwrap_or`, `option_and_then`,
`option_flatten`, `option_is_none`, `option_is_some`, `option_map`,
`option_to_result`, `option_unwrap_or`

**Result operations:**
`result_and_then`, `result_is_err`, `result_is_ok`, `result_map`,
`result_map_err`, `result_to_option`, `result_unwrap_or`

**Map operations:**
`map_insert`, `map_get`, `map_entries`, `map_filter_map`, `map_from_entries`,
`map_from_list`, `map_map_values`, `map_merge`

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
`mutable_map_keys`

**Vec operations:**
`vec_new`, `vec_push`, `vec_get`, `vec_set`, `vec_length`, `vec_pop`,
`vec_to_list`, `vec_from_list`

**Set operations:**
`set_new`, `set_from_list`, `set_add`, `set_remove`, `set_contains`,
`set_size`, `set_to_list`, `set_union`, `set_intersection`

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

**Fix:** Add `--cap fs` to your command, or include `"fs"` in your
`RunConfig.capabilities` set.

## Target Availability

Not all capabilities are available on all compilation targets. For example,
`fs`, `env`, and `process` are not available in browser builds. See
[Supported Targets](../SUPPORTED_TARGETS.md) for the full availability matrix.
