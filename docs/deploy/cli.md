# Deploying Geno CLI Applications

## Overview

Geno CLI apps can be compiled to either Python or JavaScript (Node.js) for distribution.

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
