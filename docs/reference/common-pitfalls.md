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
granted. Grant it with `--cap`; the default `geno run` lane keeps the program
process-isolated, and the hint after the error prints the command to use:

```bash
# Fix: grant the capabilities you need
geno run myfile.geno --cap print,fs,http
```

`--cap` grants exactly its list, so `--cap fs` alone denies `print`: name
every capability the program uses. Environment variables also need
`--allow-env NAME` for each one the program reads. Only `process`, `serve`
and `stdin` (and `http` on Windows) need `--unsafe --cap ...`, which drops
process isolation and is for trusted code only.

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

Built-in parsers follow the same convention: `parse_int` returns
`Option[Int]` and `parse_float` returns `Option[Float]`, not `Result`. Match
on `Some`/`None` rather than `Ok`/`Err`:

```geno
func parse_or_zero(text: String) -> Int
    example "42" -> 42
    example "abc" -> 0
    match parse_int(text) with
        | Some(n) -> return n
        | None -> return 0
    end match
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
`end trait`, `end impl`.

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

## 11. Redeclaring a `var` Instead of Updating It

```
// Warning [W300]: 'var y' declares a new variable that hides the mutable 'y'
var y: Int = 110
for i: Int in range(0, 3) do
    var y: Int = y + 40     // a second y, gone at end for
end for
// y is still 110 here
```

```
// Fix: assign to the existing var
var y: Int = 110
for i: Int in range(0, 3) do
    y = y + 40
end for
```

Inside a nested block, `let` and `var` declare a separate variable. The new
`y` hides the outer one until the block ends, and the outer `y` keeps its old
value. In the same block, redeclaration instead updates the existing binding;
closures created before the redeclaration observe its new value. Redeclaring
with `let` also makes the binding immutable.

`geno check` reports W300 when a `let` or `var` redeclares a name whose nearest
binding is a `var`, in the same block or an enclosing one. The warning describes
which case applies. It also checks lambdas in default parameter values. The
program still passes; use assignment to update a `var` without redeclaring it,
or rename the variable if you meant to have two.

## 12. Expecting a Panic After an Arrow

```
// Error [E330]: An example that expects a panic is written without an arrow:
// example 1, 0 panics "division"
func safe_div(a: Int, b: Int) -> Int
    example 6, 3 -> 2
    example 1, 0 -> panic("division")
    return a / b
end func
```

```
// Fix: write `panics` after the inputs, with no arrow
func safe_div(a: Int, b: Int) -> Int
    example 6, 3 -> 2
    example 1, 0 panics "division"
    return a / b
end func
```

A panic is not a value, so an example cannot expect one after `->`. The panics
example form is new in the 0.7 development line
([spec v0.7](../spec/v0.7.md), section 5.3.1). Geno 0.6 accepted
`-> panic("division")`, although the example could never pass: its expected
value panicked before the call was made. `-> panics`, `-> panics "m"`,
`-> panic` and `-> panic("m")` are all E330, and `geno check --fix` rewrites
each to the `panics` form. A zero-argument function writes
`example () panics`; `example panics` is E201, with a fix that adds the `()`.

The text after `panics` is optional. When given, it must appear in the panic's
message, ignoring case. Prefer the text of the function's own `panic` call, or
leave it out: runtime error messages, such as a failed `requires`, differ
between backends and may change between releases. An empty message
(`panics ""`) matches everything and is rejected (E202).

A panics example fails when the call returns, panics with another message, or
runs out of a resource limit (steps, time, recursion depth, memory, collection
or integer size, output). A limit is not a panic, so `example 0 panics` cannot
hide an infinite loop.

## 13. Writes a Caller Can See Are `mutation`

```
// Error [E300]: Argument 1 type mismatch: expected (Array[Int]) -> Int,
// got (Array[Int]) -> Int with mutation
func bump(counter: Array[Int]) -> Int
    example array_new(1, 0) -> 1
    var alias = counter
    alias[0] = alias[0] + 1
    return alias[0]
end func

func apply(f: (Array[Int]) -> Int, cells: Array[Int]) -> Int
    example fn(a: Array[Int]) -> array_length(a), array_new(2, 0) -> 2
    return f(cells)
end func

func main() -> Unit with io
    print(apply(bump, array_new(1, 0)))
end func
```

```
// Fix: work on an array the function allocates, so the caller's is untouched
func bump(counter: Array[Int]) -> Int
    example array_new(1, 0) -> 1
    var own = array_copy(counter)
    own[0] = own[0] + 1
    return own[0]
end func
```

In the 0.7 development line ([spec v0.7](../spec/v0.7.md), section 3.5),
changing state that only the function's own call can see is not an effect: a
local `var`, the fields of a record held in one, and an `Array`, `Vec` or
`MutableMap` the function allocates (`array_new`, `array_copy`, `vec_new`,
`mutable_map_new` and the other allocating builtins) and keeps to itself. Such
a function can be passed where a pure function type is expected. A write the
caller can see keeps `mutation`. `var alias = counter` copies the reference,
not the array, so `alias[0] = ...` changes the caller's array. Either write
to a collection the function allocates, as in the fix, or keep the effect and
declare it where the function is passed: `f: (Array[Int]) -> Int with
mutation`.

These shapes also keep `mutation`, although they can look local:

- **Two-dimensional tables.** `g[i][j] = v` writes below a reference-typed
  element, even when every row is fresh. With
  `var g = array_new(h, array_new(w, 0))`, every row is the same array.
- **Rolling buffers.** `prev = cur` copies the reference, so after
  `cur = array_new(n, 0)` a write to either one is charged.
- **Accumulators updated through a user higher-order function.** A lambda
  passed to your own `each` that does `total = total + x` assigns a variable
  captured from the function around it. The lambda carries `mutation`, so
  `each` must accept a `with mutation` callback and every call of `each` is
  charged. A `for` loop over the list keeps the accumulator local.
- **Collections passed to helpers.** Passing a fresh collection to a
  function of your own, storing it in a list, record or tuple that is not
  returned, or mentioning it in a lambda lets it escape, and every write to
  it is then charged. Builtins that use their collection argument without
  keeping it (`array_length`, `array_to_list`, `length`, `vec_push` on its
  receiver), whether called directly or as a pipeline stage
  (`counts |> array_to_list`), and the container of `in` do not count as
  escapes.

The current checker infers no effect for mutating builtins (`vec_push`,
`array_set`, `set_add`, `mutable_map_set`), even on a parameter. That is a
known limitation, not the rule, so declare `with mutation` on a function that
changes a caller's collection through them. This matters more in 0.7: a
function such as `fill(grid: Array[Int]) -> Unit` that loops with a local
`var i` and calls `array_set(array: grid, ...)` was charged in 0.6 because of
the counter, and is now inferred pure unless it declares the effect.
