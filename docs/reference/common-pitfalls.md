# Common Pitfalls

The most common issues new Geno users encounter, with explanations and fixes.

## 1. Named Arguments Required for 3+ Parameters

Functions with three or more parameters require named arguments at call sites.

```
// Error: expected named arguments
let result: String = replace("hello world", "world", "geno")
```

```
// Fix: use parameter names
let result: String = replace("hello world", old: "world", new: "geno")
```

**Why:** Named arguments prevent argument-order bugs in functions with many
parameters. Functions with 1-2 parameters can be called positionally.

## 2. Capability Denied

```
Error [E412]: Capability denied: 'print' requires the 'print' capability
```

Your program is calling a builtin that requires a capability you haven't
granted. In `geno run`, pair `--cap` with `--unsafe` to explicitly choose the
direct interpreter capability mode:

```bash
# Fix: grant the capabilities you need
geno run myfile.geno --unsafe --cap print,fs,http
```

See [Capability Reference](capabilities.md) for the full table.

## 3. Immutable Binding Reassignment

```
// Error: cannot assign to immutable binding 'x'
let x: Int = 5
x = 10
```

```
// Fix: use var for mutable bindings
var x: Int = 5
x = 10
```

`let` creates an immutable binding. Use `var` when you need to reassign.
Prefer `let` by default — mutability should be intentional.

## 4. Missing Example Clauses

```
// Error: function 'add' must have at least one example clause
func add(a: Int, b: Int) -> Int
    return a + b
end func
```

```
// Fix: add example clauses
func add(a: Int, b: Int) -> Int
    example 1, 2 -> 3
    example 0, 0 -> 0
    return a + b
end func
```

Every function must have at least one `example` clause. Exemptions:
`main`, app lifecycle hooks (`init`, `update`, `render`), async functions,
functions called from `test` blocks, and functions annotated with `@untested`.

Examples serve as both documentation and lightweight tests — they are
verified at runtime. For impure functions that cannot have deterministic
examples, use the `@untested` annotation.

## 5. Boolean Literals Are Lowercase

```
// Error: undefined variable 'True'
let flag: Bool = True
```

```
// Fix: use lowercase
let flag: Bool = true
```

Geno uses `true` and `false`, not Python-style `True`/`False`.

## 6. No Null — Use Option[T]

Geno has no null. Use `Option[T]` with `Some(value)` and `None` to represent
optional values.

```geno
func find_user(id: Int) -> Option[String]
    example 1 -> Some("Alice")
    example 999 -> None
    if id == 1 then
        return Some("Alice")
    end if
    return None
end func
```

Use the `?` operator to unwrap and propagate `None`. The `?` operator
unwraps `Some(x)` to `x`, or returns `None` from the enclosing function:

```geno
func greet_user(id: Int) -> Option[String]
    example 1 -> Some("Hello, Alice")
    example 999 -> None
    let name: String = find_user(id)?   // String, not Option[String]
    return Some("Hello, " + name)
end func
```

## 7. Non-Exhaustive Pattern Matching

```
// Error: non-exhaustive match — missing variant 'None'
match maybe_value with
    | Some(x) -> return x
end match
```

```
// Fix: handle all variants
match maybe_value with
    | Some(x) -> return x
    | None -> return 0
end match
```

`match` expressions must cover all variants of the type being matched. Use
a wildcard `_` pattern if you want to ignore some cases:

```geno
match value with
    | Some(x) -> return x
    | _ -> return 0
end match
```

## 8. String Concatenation Uses +

```
// Error: undefined function '++'
let greeting: String = "Hello, " ++ name
```

```
// Fix: use + (same operator as arithmetic)
let greeting: String = "Hello, " + name
```

You can also use f-strings: `f"Hello, {name}"`.

## 9. Integer Division Truncates Toward Zero

```geno
let x: Int = 7 / 2    // x = 3 (not 3.5)
let y: Int = -7 / 2   // y = -3 (truncates toward zero)
```

This matches C and JavaScript semantics. For float division, convert first:

```geno
let x: Float = int_to_float(7) / int_to_float(2)   // x = 3.5
```

## 10. Block Delimiters

Every block must be closed with an `end` keyword matching the block type:

```geno
func greet(name: String) -> String
    example "World" -> "Hello, World!"
    if length(name) == 0 then
        return "Hello, stranger!"
    end if                              // not end, not }
    return "Hello, " + name + "!"
end func                                // not end, not }
```

Block delimiters: `end func`, `end if`, `end for`, `end while`, `end match`,
`end try`, `end trait`, `end impl`.

The `if` keyword requires `then` after the condition. The `for` and `while`
keywords require `do` after the condition.

```geno
if x > 0 then        // 'then' required
    // ...
end if

for i in range(10) do // 'do' required
    // ...
end for
```
