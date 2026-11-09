# geno-check

A config and data file validator written in Geno. Validation rules are Geno
functions with example clauses that serve as both documentation and self-tests.

## Concept: Contracts = Validation Rules

Each validation rule is a Geno function. The function's `example` clauses
document expected behavior and double as self-tests via `geno test`. Pipeline
operators chain rules together to accumulate violations.

```
// Pipeline chains rules to accumulate violations
return []
    |> concat(_, check_required(file: f, row_label: r, row: row, field: "name"))
    |> concat(_, check_required(file: f, row_label: r, row: row, field: "email"))
    |> concat(_, check_numeric(file: f, row_label: r, row: row, field: "age"))
```

## Usage

```bash
# Type check
geno check examples/apps/geno-check

# Run self-tests (37 example clauses)
geno test examples/apps/geno-check

# Validate a JSON config
geno run --cap fs,print,env examples/apps/geno-check -- config.json

# Validate a CSV data file
geno run --cap fs,print,env examples/apps/geno-check -- users.csv

# Validate a TOML config
geno run --cap fs,print,env examples/apps/geno-check -- settings.toml
```

## Supported formats

| Format | Validator      | Rules                                    |
|--------|---------------|------------------------------------------|
| JSON   | JsonRules     | Parse validity, required keys            |
| CSV    | CsvRules      | Required fields, numeric, allowed values |
| TOML   | TomlRules     | Parse validity, sections, keys           |

## Violation report

```
[ERROR] users.csv: row.name — required: field 'name' must not be empty (expected: non-empty, actual: empty)
[ERROR] users.csv: row.age — numeric: expected positive integer, got 'abc' (expected: positive integer, actual: abc)
[WARN]  users.csv: row.role — allowed_values: 'superuser' not in allowed values (expected: one of allowed, actual: superuser)

2 error(s), 1 warning(s)
```

Exit code 0 on valid (warnings are OK), exit code 1 on errors.

## Modules

- **Violation** - Core violation type, severity, formatting
- **JsonRules** - JSON config validation with pipeline-chained rules
- **CsvRules** - CSV data validation with field-level checks
- **TomlRules** - TOML config validation with section/key checks
- **Report** - Formats violation list into human-readable output
- **Main** - File type detection, dispatch, exit code logic
