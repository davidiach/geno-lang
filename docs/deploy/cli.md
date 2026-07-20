# Deploying Geno CLI Applications

## Overview

Geno CLI apps can be compiled to either Python or JavaScript (Node.js) for distribution.

## Process exit contract

The outermost CLI or generated-script wrapper translates `main()`'s return
value into process behavior.

Only `main` declared by the selected entry program is an entrypoint; an
unqualified imported function named `main` is not invoked as one.
- `main() -> Unit` exits successfully with status `0`.
- `main() -> Int` uses the returned value as the process status. For portable
  status values, Geno normalizes the integer modulo 256, so `258` exits `2`
  and `-1` exits `255` on every supported host.
- Output printed before a normal return is preserved. A nonzero `Int` return
  is an expected exit and does not produce a runtime traceback.
- An uncaught runtime error remains a failure: it exits nonzero and emits a
  diagnostic (or the host traceback for a standalone generated artifact).
- Other `main()` return types retain the pre-0.4 behavior of displaying the
  returned value and exiting successfully.

Exit handling belongs only to executable boundaries. `geno.run()` and
`geno.api.run()` return the value in `RunResult` and never terminate the host
process. Importing generated Python also only defines the compiled program;
`main()` and its exit handling run only when the file is executed as a script.
Generated ES modules likewise call `main()` only when run as Node's entrypoint;
importing one only evaluates its definitions and exports.

`geno run`, the self-hosted `run` command, standalone generated Python, and
standalone generated Node.js use this same contract. Geno sets Node's deferred
`process.exitCode` instead of calling `process.exit()`, allowing buffered
output to drain normally. `geno watch` reports a nonzero returned status and
continues watching for changes.

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

### ES Module output

```bash
geno compile Main.geno --target js --esm -o app.mjs
```

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
