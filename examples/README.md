# Geno Examples

This directory contains small Geno programs and larger sample apps that are
useful for learning the language and checking target behavior.

## Quick Examples

Top-level `.geno` files are focused language samples:

- `fibonacci.geno` - recursion and simple CLI execution
- `quicksort.geno` - list processing and pattern matching
- `safe_divide.geno` - `Result`-style error handling
- `word_count.geno` - string and collection operations
- `csv_processor.geno` - CSV parsing and data transformation
- `calculator.geno`, `colors.geno`, `shapes.geno`, `todo_app.geno` - app-mode and ADT examples

Run a CLI example with:

```bash
python3 -m geno run examples/fibonacci.geno
```

Type-check an example without running it:

```bash
python3 -m geno check examples/quicksort.geno
```

## Apps

`examples/apps/` contains multi-file projects and browser app demos. The
release-gated reference apps are documented in
[`docs/REFERENCE_APPS.md`](../docs/REFERENCE_APPS.md).

Common commands:

```bash
# Check a project
python3 -m geno check examples/apps/geno-check

# Run example clauses
python3 -m geno test examples/apps/geno-dash

# Build a browser app to dist/
python3 -m geno build examples/apps/geno-dash -o dist/

# Build a single HTML artifact
python3 -m geno build examples/apps/geno-dash --single-file -o geno-dash.html
```
