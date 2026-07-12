# geno-pipe

A CSV data pipeline built in Geno. Reads CSV input, applies transforms
(filter, sort, select, take), and outputs in multiple formats (CSV, JSON,
aligned table, Markdown).

## Usage

### With a CSV file (requires `fs`, `print`, and `env` capabilities)

```bash
geno run --cap fs,print,env examples/apps/geno-pipe -- input.csv
```

The file path is passed as a CLI argument after `--`. The tool validates
that the file has a `.csv` extension before reading it.

### With built-in sample data (no file needed)

```bash
geno run --cap print,env examples/apps/geno-pipe
```

When no arguments are provided after `--`, the pipeline runs against
built-in sample data so you can see the output without preparing an
input file. The `env` capability is always required (for `cli_args()`).

## Pipeline

The default pipeline demonstrates:

1. **Filter** rows where `role == "Engineer"`
2. **Sort** by `name` (lexicographic ascending)
3. **Select** columns `name`, `age`, `city`
4. **Take** first 3 rows

Output is printed in all four supported formats: CSV, JSON, table, and
Markdown.

## Running tests

```bash
python3 -m geno test examples/apps/geno-pipe
```
