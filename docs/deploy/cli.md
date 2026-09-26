# Deploying Geno CLI Applications

## Overview

Geno CLI apps can be compiled to either Python or JavaScript (Node.js) for distribution.

## Executable result contract (v0.5)

A successful `main() -> Int` becomes the process status: the artifact exits with
that value normalized modulo 256 and does not display it. `geno run` and the
standalone Python and Node artifacts agree, so `return 2` exits 2, `return 258`
exits 2 and `return 0 - 1` exits 255. Output produced before the result is
preserved, and a normal nonzero status is not an error: it carries no traceback
and no diagnostic. `main() -> Unit` exits 0 silently, any other declared return
type is still displayed and exits 0, and uncaught runtime errors still exit
nonzero with a diagnostic.

A browser-targeted artifact has no process to give a status to, so it keeps
displaying an `Int` result instead. The compiler decides that from the target
profile, never from whether a `process` global happens to exist at runtime.

Executable behavior is applied only when the artifact is run directly.
Importing generated Python or Node ESM does not invoke `main` or terminate the
importing process. Embedding through `geno.api.run()` returns the value to the
host instead of applying executable behavior.

## Compilation profiles

When a project manifest declares an execution profile compatible with the
selected backend, compilation validates that profile before emission. Otherwise
it preserves the legacy `python-cli` or `node-cli` default. Use `--profile` to
select a compatible profile explicitly (for example, `--profile python-hosted`).
Browser applications use `geno build`, not raw JavaScript compilation.

## Compile to Python

```bash
geno compile Main.geno -o app.py
```

Run the compiled output:

```bash
python3 app.py
```

### Requirements

- Python 3.10 or later
- No external dependencies (runtime is bundled in the output)

### Packaging as a standalone binary

Use PyInstaller to create a single executable:

```bash
pip install pyinstaller
geno compile Main.geno -o app.py
pyinstaller --onefile app.py
```

The binary will be in `dist/app`.

## Compile to JavaScript

```bash
geno compile Main.geno --target js -o app.js
```

Run with Node.js:

```bash
node app.js
```

HTTP calls in compiled Node output require only the `http` capability. Geno's
internal synchronous bridge does not grant user code process execution:

```bash
node app.js --cap http
```

Loopback, private, link-local, multicast, reserved, and unspecified targets are
denied by default, including after redirects. Trusted local deployments can opt
in with `GENO_HTTP_ALLOW_PRIVATE=1`.

### ES Module output

```bash
geno compile Main.geno --target js --esm -o app.mjs
```

ES-module output targets Node.js and supports the same filesystem and HTTP
runtime services. Use `geno build` for browser artifacts.

### Requirements

- Node.js 18 or later

## Multi-module projects

For projects with multiple `.geno` files:

```bash
geno compile Main.geno -o app.py    # Python (imports resolved automatically)
geno compile Main.geno --target js -o app.js  # JavaScript
```

## CI/CD

Example GitHub Actions workflow:

```yaml
name: Build
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install geno-lang
      - run: geno check .
      - run: geno test .
      - run: geno compile Main.geno -o app.py
      - uses: actions/upload-artifact@v4
        with:
          name: app
          path: app.py
```
