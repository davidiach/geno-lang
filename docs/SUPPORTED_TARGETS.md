# Supported Targets

> Source of truth: [`targets.toml`](../targets.toml) in the project root.
> This document is manually synced with that file.

Geno compiles to multiple backends. Each **target** defines a runtime, available capabilities, and which builtins are accessible. The typechecker uses the target profile to reject APIs that aren't supported at compile time.

## Target vocabulary

Geno uses two related but distinct concepts:

- **Execution targets** (`geno check --target`, `geno test --target`): `python-cli`, `node-cli`, `browser`, `python-hosted`. These describe the runtime environment and determine which builtins are available.
- **Compilation backends** (`geno compile --target`): `python`, `js`. These select the output language.

The execution target determines builtin availability at typecheck time. The compilation backend determines the output format. For example, `node-cli` uses the `js` backend but has a different set of available builtins than `browser`.

For programs shared with JavaScript, portable `Int` values are limited to
`-(2^53 - 1)` through `2^53 - 1`. JavaScript rejects results outside that
range. Python and interpreter execution support the separately configured
`max_integer_bits` limit. See [Portable Runtime Semantics](reference/runtime-semantics.md).

### Manifest target

Set `targets` in `geno.toml` to automatically apply the first target profile:

```toml
entrypoint = "Main"
targets = ["browser"]
```

This is equivalent to passing `--target browser` to target-aware commands such as
`geno check`, `geno run`, and `geno test`. The `geno build` command always applies
the `browser` profile regardless of the manifest target.

For `geno test`, the target profile is applied before execution; examples and
test blocks still run through the interpreter. Backend parity is covered by the
pytest backend parity suites.

## Targets

| Target | Runtime | Entry Command | Compile Command |
|--------|---------|---------------|-----------------|
| `python-cli` | Python | `geno run` | `geno compile -o app.py` |
| `node-cli` | Node.js | `geno compile --target js -o app.js && node app.js` | `geno compile --target js -o app.js` |
| `browser` | Browser | `geno build` | `geno build -o dist/` |
| `python-hosted` | Python | `geno serve` | `geno compile -o handler.py` |

### python-cli

CLI and automation scripts running on the Python interpreter. Full access to filesystem, HTTP, process execution, and environment variables when capabilities are granted.

**Capabilities:** fs, http, process, stdin, env, clock, random, print, regex, serve

### node-cli

CLI and automation scripts compiled to Node.js. Filesystem access uses Node.js `fs` module; HTTP uses Node.js `fetch` through a synchronous bridge that requires both the `http` and `process` capabilities. User-authored process execution is not supported on this target.

**Capabilities:** fs, http, env, clock, random, print, regex

### browser

Browser apps compiled to static HTML/JS artifacts with a canvas. The default output is a `dist/` directory; use `--single-file` when you need one HTML file. No filesystem or environment variable access. Graphics and input builtins are available. Generated browser artifacts grant the browser target capabilities at startup. HTTP is available through browser networking APIs and is subject to the browser's CORS policy.

**Capabilities:** http, clock, random, print, regex

### python-hosted

Server handlers running in a hosted Python environment. Similar to python-cli but process execution is not allowed (security boundary).

**Capabilities:** fs, http, env, clock, random, print, regex, serve

## Builtin Availability Matrix

### Legend

- **Available** - Always available, no capability needed
- **Cap: X** - Available when `--cap X` is granted
- **--** - Unavailable on this target (compile-time error)

### Pure Builtins (available on all targets)

The following builtins require no capabilities and work on every target:

**List operations:** `length`, `head`, `tail`, `append`, `concat`, `set_at`, `slice`, `filter`, `map`, `fold`, `contains`, `take_while`, `all`, `sort`, `sort_by`, `reverse`, `zip`, `enumerate`, `flat_map`

**String operations:** `split`, `join`, `trim`, `to_lower`, `to_upper`, `replace`, `ends_with`, `split_once`, `starts_with`, `to_chars`, `sort_strings`, `contains_substring`, `repeat_string`, `substring`, `format`, `char_code`, `from_char_code`

**Math operations:** `add`, `subtract`, `multiply`, `divide`, `sqrt`, `floor`, `ceil`, `round`, `max`, `abs`, `square`, `clamp`, `bit_or`

**Type predicates:** `is_sorted`, `is_positive`, `is_numeric_string`, `is_permutation`

**Conversions:** `parse_int`, `parse_float`, `to_string`, `float_to_int`, `int_to_float`

**Range:** `range`

**Option operations:** `is_some`, `is_none`, `unwrap`, `unwrap_or`

**Map operations:** `map_insert`, `map_get`

**Array operations:** `array_new`, `array_from_list`, `array_get`, `array_set`, `array_length`, `array_to_list`, `array_fill`, `array_copy`

**Vec operations:** `vec_new`, `vec_from_list`, `vec_push`, `vec_pop`, `vec_get`, `vec_set`, `vec_length`, `vec_to_list`

**MutableMap operations:** `mutable_map_new`, `mutable_map_set`, `mutable_map_get`, `mutable_map_contains`, `mutable_map_delete`, `mutable_map_size`, `mutable_map_keys`

**Set operations:** `set_new`, `set_from_list`, `set_add`, `set_remove`, `set_contains`, `set_size`, `set_to_list`, `set_union`, `set_intersection`

**JSON/Data:** `json_parse`, `json_stringify`, `csv_parse`, `csv_parse_with_headers`, `toml_parse`

### Capability-Gated Builtins

| Builtin | Capability | python-cli | node-cli | browser | python-hosted |
|---------|-----------|------------|----------|---------|---------------|
| `print` | print | Cap: print | Cap: print | Cap: print | Cap: print |
| `clock_now` | clock | Cap: clock | Cap: clock | Cap: clock | Cap: clock |
| `clock_format` | clock | Cap: clock | Cap: clock | Cap: clock | Cap: clock |
| `clock_parse` | clock | Cap: clock | Cap: clock | Cap: clock | Cap: clock |
| `clock_elapsed` | clock | Cap: clock | Cap: clock | Cap: clock | Cap: clock |
| `datetime_now` | clock | Cap: clock | Cap: clock | Cap: clock | Cap: clock |
| `datetime_format` | clock | Cap: clock | Cap: clock | Cap: clock | Cap: clock |
| `datetime_parse` | clock | Cap: clock | Cap: clock | Cap: clock | Cap: clock |
| `datetime_elapsed` | clock | Cap: clock | Cap: clock | Cap: clock | Cap: clock |
| `random_int` | random | Cap: random | Cap: random | Cap: random | Cap: random |
| `random_float` | random | Cap: random | Cap: random | Cap: random | Cap: random |
| `math_random_int` | random | Cap: random | Cap: random | Cap: random | Cap: random |
| `math_random_float` | random | Cap: random | Cap: random | Cap: random | Cap: random |
| `regex_match` | regex | Cap: regex | Cap: regex | Cap: regex | Cap: regex |
| `regex_find_all` | regex | Cap: regex | Cap: regex | Cap: regex | Cap: regex |
| `regex_replace` | regex | Cap: regex | Cap: regex | Cap: regex | Cap: regex |

### Target-Restricted Builtins

| Builtin | Capability | python-cli | node-cli | browser | python-hosted |
|---------|-----------|------------|----------|---------|---------------|
| `sleep_ms` | clock | Cap: clock | -- | -- | -- |
| `fs_read_text` | fs | Cap: fs | Cap: fs | -- | Cap: fs |
| `fs_write_text` | fs | Cap: fs | Cap: fs | -- | Cap: fs |
| `fs_list_dir` | fs | Cap: fs | Cap: fs | -- | Cap: fs |
| `fs_exists` | fs | Cap: fs | Cap: fs | -- | Cap: fs |
| `fs_metadata` | fs | Cap: fs | Cap: fs | -- | Cap: fs |
| `fs_symlink_metadata` | fs | Cap: fs | Cap: fs | -- | Cap: fs |
| `fs_canonicalize` | fs | Cap: fs | Cap: fs | -- | Cap: fs |
| `http_fetch` | http | Cap: http | Cap: http | Cap: http | Cap: http |
| `http_post` | http | Cap: http | Cap: http | Cap: http | Cap: http |
| `http_request` | http | Cap: http | Cap: http | Cap: http | Cap: http |
| `http_listen` | serve | Cap: serve | -- | -- | Cap: serve |
| `http_route` | serve | Cap: serve | -- | -- | Cap: serve |
| `http_respond` | serve | Cap: serve | -- | -- | Cap: serve |
| `exec` | process | Cap: process | -- | -- | -- |
| `exec_with_input` | process | Cap: process | -- | -- | -- |
| `spawn` | process | Cap: process | -- | -- | -- |
| `spawn_with_input` | process | Cap: process | -- | -- | -- |
| `stdin_read_all` | stdin | Cap: stdin | -- | -- | -- |
| `env_get` | env | Cap: env | Cap: env | -- | Cap: env |
| `env_get_or` | env | Cap: env | Cap: env | -- | Cap: env |
| `cli_args` | env | Cap: env | Cap: env | -- | Cap: env |

### Browser-Only Builtins

These builtins are only available on the `browser` target (app mode with `init`/`update`/`render`):

| Builtin | python-cli | node-cli | browser | python-hosted |
|---------|------------|----------|---------|---------------|
| `clear_screen` | -- | -- | Available | -- |
| `draw_rect` | -- | -- | Available | -- |
| `draw_rect_outline` | -- | -- | Available | -- |
| `draw_circle` | -- | -- | Available | -- |
| `draw_line` | -- | -- | Available | -- |
| `draw_text` | -- | -- | Available | -- |
| `screen_width` | -- | -- | Available | -- |
| `screen_height` | -- | -- | Available | -- |
| `is_key_down` | -- | -- | Available | -- |
| `is_key_pressed` | -- | -- | Available | -- |
| `mouse_x` | -- | -- | Available | -- |
| `mouse_y` | -- | -- | Available | -- |
| `is_mouse_down` | -- | -- | Available | -- |
| `is_mouse_clicked` | -- | -- | Available | -- |
| `get_text_input` | -- | -- | Available | -- |
| `clear_text_input` | -- | -- | Available | -- |

## Using Targets

### Checking against a target

```bash
geno check --target browser myapp.geno
```

If `myapp.geno` calls `fs_read_text`, the typechecker will reject it:

```
error: `fs_read_text` is not available on target `browser`
  --> myapp.geno:5:12
  |
5 |     let data = fs_read_text("config.txt")
  |                ^^^^^^^^^^^^
  = help: `fs_read_text` requires the `fs` capability, which is not supported
    on the `browser` target. Use `python-cli` or `node-cli` instead.
```

### Compiling for a target

```bash
# Python CLI
geno compile -o app.py && python app.py --cap fs --cap http

# Node.js CLI
geno compile --target js -o app.js && node app.js

# Browser app
geno build -o dist/

# Hosted server
geno serve --cap fs --cap http
```
