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
