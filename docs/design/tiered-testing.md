# Design: Tiered Example/Test Policy

## Motivation

Geno's mandatory `example` clause is a core safety feature: every function ships with executable documentation that doubles as a regression test. However, the current "every function needs an example" model creates friction for:

- **Impure functions** (I/O, side effects) where inputs and outputs are hard to capture
- **UI glue** (event handlers, render loops) where behavior depends on external state
- **Private helpers** where the public API's examples already cover the logic
- **App-mode functions** (`init`, `update`, `render`) that operate on canvas state

This design introduces a tiered model that preserves Geno's testing philosophy while removing unnecessary friction for app code.

## Tiers

### Tier 1: Public pure functions

**Rule:** Must have at least one `example` clause.

**Rationale:** These are the core of Geno's safety model. A pure function with well-chosen examples is self-documenting and self-testing. This is the existing behavior and does not change.

```geno
func fibonacci(n: Int) -> Int
    requires n >= 0
    ensures result >= 0
    example 0 -> 0
    example 1 -> 1
    example 10 -> 55

    if n <= 1 then
        return n
    end if
    var prev: Int = 0
    var curr: Int = 1
    var i: Int = 2
    while i <= n do
        let next: Int = prev + curr
        prev = curr
        curr = next
        i = i + 1
    end while
    return curr
end func fibonacci
```

### Tier 2: Private helper functions

**Rule:** Must have `example` clauses OR be covered by a module-level `test` block.

**Rationale:** Private helpers are implementation details. If the module's public API is well-tested, requiring examples on every internal helper adds noise without safety benefit. A `test` block that exercises the helper through the public API (or directly) is sufficient.

```geno
// Option A: example clause on the helper (still works)
func parse_header(line: String) -> Option[(String, String)]
    example "Content-Type: text/html" -> Some(("Content-Type", "text/html"))
    example "malformed" -> None

    return split_once(line, ": ")
end func parse_header

// Option B: test block covers the helper
func parse_header(line: String) -> Option[(String, String)]
    return split_once(line, ": ")
end func parse_header

test "parse_header handles valid and invalid input"
    let valid: Option[(String, String)] = parse_header("Content-Type: text/html")
    assert valid == Some(("Content-Type", "text/html"))

    let invalid: Option[(String, String)] = parse_header("malformed")
    assert invalid == None
end test
```

### Tier 3: Impure functions and UI glue

**Rule:** Must have `example` clauses, `test` blocks, OR an explicit `@untested("reason")` annotation.

**Rationale:** Impure functions (I/O, side effects) and UI glue (event handlers, render callbacks) often cannot have meaningful example clauses because their behavior depends on external state. Requiring examples would force developers into awkward workarounds (mocking, ignoring the return value). The `@untested` annotation makes the testing gap explicit and auditable.

```geno
// Option A: test block with setup/teardown
func save_report(path: String, data: String) -> Result[Unit, String]
    return Ok(fs_write_text(path, data))
end func save_report

test "save_report writes file correctly"
    let result: Result[Unit, String] = save_report("/tmp/test.txt", "hello")
    assert result == Ok(())
    let content: String = fs_read_text("/tmp/test.txt")
    assert content == "hello"
end test

// Option B: @untested annotation with reason
@untested("renders to canvas, cannot assert pixel output")
func draw_scoreboard(score: Int, lives: Int) -> Unit
    clear_screen("#000000")
    draw_text("Score: " + to_string(score), 10, 10, 20, "#ffffff")
    draw_text("Lives: " + to_string(lives), 10, 40, 20, "#ffffff")
end func draw_scoreboard
```

### Tier 4: `main` and `async` functions

**Rule:** Exempt from `example` and `test` requirements.

**Rationale:** `main` is the program entry point and cannot have meaningful input/output examples. `async` functions involve external I/O that cannot be tested with pure example clauses. This is the existing behavior and does not change.

```geno
func main() -> Unit
    let data: String = await fetch_data("https://api.example.com/data")
    print(data)
end func main

async func fetch_data(url: String) -> String
    return http_fetch(url)
end func fetch_data
```

### Tier 5: Package public APIs

**Rule:** All exported functions must have `example` clauses (not just test blocks).

**Rationale:** Package APIs are consumed by other developers and LLMs. Example clauses serve as inline documentation at the call site. Test blocks are not visible to importers. This tier is stricter than Tier 1 because package APIs have a wider audience.

```geno
// In a published package: examples are mandatory on exports
export func encode_base64(input: String) -> String
    example "" -> ""
    example "hello" -> "aGVsbG8="
    example "Hello, World!" -> "SGVsbG8sIFdvcmxkIQ=="

    // implementation
    ...
end func encode_base64

// Internal helper: covered by test block (Tier 2)
func encode_chunk(bytes: List[Int]) -> String
    // implementation
    ...
end func encode_chunk

test "encode_chunk handles padding"
    assert encode_chunk([72, 101]) == "SG"
end test
```

## Summary Table

| Tier | Scope | Example clause | Test block | @untested | None |
|------|-------|:-----------:|:----------:|:---------:|:----:|
| 1 | Public pure functions | Required | Optional extra | No | No |
| 2 | Private helpers | OK | OK | No | No |
| 3 | Impure / UI glue | OK | OK | OK | No |
| 4 | `main` / `async` | Optional | Optional | Optional | OK |
| 5 | Package exports | Required | Optional extra | No | No |

Tier 5 is the target package-public policy. The current implementation supports
`export`, `test` blocks, and `@untested`, but does not yet enforce the stricter
"exported functions must have examples" rule.

## Migration Path

### Current behavior (v0.3.x)

- Every function except `main`, `async`, `init`, `update`, and `render` must
  have at least one `example` clause unless another implemented exemption
  applies.
- Module-level `test` blocks exist, are executed by `geno test`, and can exempt
  called helpers from the example requirement.
- `@untested("reason")` exists, suppresses the example requirement, and is
  reported by `geno test`; `--fail-on-untested` can make those reported gaps
  fail the test command.
- App-mode helpers are still exempt through the `_is_app_mode` migration flag.
- `export` exists for module visibility, but Tier 5 example enforcement is not
  yet implemented.

### Implemented Phase 1

1. **Add `test` block syntax** (P1-18): Parsed and executed by `geno test`
2. **Add `@untested` annotation** (P1-19): Parsed, suppresses example requirement
3. **Relax enforcement**: Private helpers can use `test` blocks instead of examples
4. **App-mode exemption preserved**: The `_is_app_mode` flag continues to work but is documented as a migration aid that will be replaced by `@untested`

### Remaining Phase 2

5. **Package export enforcement**: Exported functions must have examples
6. **Deprecate `_is_app_mode` exemption**: App-mode functions should use `@untested("reason")` instead

### Phase 3+

7. **Reporting**: `geno test` summary shows coverage by tier
8. **Strictness flag**: `geno check --strict` requires Tier 5 rules everywhere

## Syntax Details

### Test blocks

```geno
test "description string"
    // arbitrary Geno code
    assert expression
    assert expression == expected
end test
```

- Module-level only (not inside functions)
- Can call any function in the module
- Can use `assert` for boolean checks and equality checks
- Can use pattern matching, error handling, and all language features
- Run by `geno test` alongside example clause verification
- Multiple test blocks per module are allowed

### @untested annotation

```geno
@untested("reason string is mandatory")
func render_frame(state: GameState) -> Unit
    // ...
end func render_frame
```

- Placed immediately before the function definition
- Reason string is mandatory (parse error if missing)
- `geno test` reports all `@untested` functions with their reasons in the summary
- Does not suppress `requires`/`ensures` contracts (those are still checked if present)

### Assert

```geno
assert condition                    // fails with "assertion failed"
assert value == expected            // fails with "expected X, got Y"
```

- Only valid inside `test` blocks
- `assert` with `==` provides a diff-style error message
- `assert` without `==` requires a `Bool` expression
