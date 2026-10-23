# Language Tour

This guide covers Geno's core features with runnable examples.

## Variables and Types

```geno
let x: Int = 42           // immutable
var count: Int = 0         // mutable
let name: String = "Geno"
let pi: Float = 3.14
let done: Bool = true
```

Local bindings can include an explicit type annotation, and Geno can also infer
the type from an unambiguous initializer:

```geno
let inferred = 42
var total = 0
```

`let` bindings are immutable; use `var` when you need mutation. Update a `var`
by assigning to it (`total = total + 1`). Writing `var total = ...` again
rebinds it in the same block, or declares a separate variable in a nested
block. `geno check` warns about either redeclaration (W300).

### Module Constants

A `let` written outside any function declares a module constant, which every
function in the file can read. Use it for usage strings, lookup tables and other
fixed values you would otherwise duplicate or wrap in a helper function:

```geno
let usage: String = "roman <number>"
let numerals: List[String] = ["I", "V", "X", "L", "C", "D", "M"]

func help_text() -> String
    example () -> "roman <number>"
    return usage
end func

func largest_numeral() -> String
    example () -> "M"
    return numerals[6]
end func

func main() -> String
    return help_text() + " uses " + largest_numeral()
end func
```

The initializer must be a literal: a number, string or boolean, a negated
number, or a list or tuple of those. A call, an operator expression or a
reference to another binding is rejected, so a module never runs code when it is
imported:

```text
let limit = max_size() + 1
    ^
Module constant 'limit' must be initialized by a literal, but found an operator
expression. Move the computation into a function and call it where the value is
needed.
```

Three more rules follow from that:

- Module constants are immutable. `var` at module level is rejected.
- They are named in `snake_case`, like any other binding. `let USAGE` is a parse
  error, because leading-uppercase names are reserved for types and
  constructors.
- They are private to their own file. `export let` is not supported, and an
  importing module does not see the name. Return the value from an exported
  function when another module needs it.

## Functions

Function parameters and return values require type annotations. Functions also
need at least one `example` clause (except `main`):

```geno
func add(x: Int, y: Int) -> Int
    example 2, 3 -> 5
    example 0, 0 -> 0
    return x + y
end func
```

Example clauses serve as both documentation and tests. They are verified at runtime.

### Float Examples

`Float` examples allow small rounding differences, but the safest habit is to
**keep the full value printed by a real run**:

```geno
func ratio(a: Float, b: Float) -> Float
    example (160.0, 7.0) -> 22.857142857142858
    return a / b
end func
```

A convenience rounding like `22.857` fails. The tolerance deliberately does not
stretch that far: an example that passed on a value correct to only four digits
would hide a real error rather than catch it.

`geno test` compares a `Float` result to a relative tolerance of `1e-9`, so an
expectation rounded to ten or more significant digits passes at any magnitude.
Taking the expected value from a real run and keeping all its digits is still
the simplest habit. When the domain allows it, prefer `Int` arithmetic instead,
which compares exactly: cents rather than fractional currency, for instance.

### Method Calls

Any function can be called on its first argument with a dot, which reads left
to right and chains:

```geno
func shout(text: String) -> String
    example "hi" -> "HI!"
    return text.to_upper() + "!"
end func

func summary(scores: List[Int]) -> Int
    example [50, 70, 90] -> 320
    return scores
        .filter(fn(s: Int) -> s >= 60)
        .map(fn(s: Int) -> s * 2)
        .fold(initial: 0, reducer: fn(acc: Int, s: Int) -> acc + s)
end func
```

The receiver's type picks the function, so `Some(1).map(f)` and `[1].map(f)`
call the `Option` and `List` versions. Builtins work the same way:
`" a ".trim()`, `opt.unwrap_or(0)`, `v.push(x)`, `m.get(key)`, `xs.length()`.

### Named Parameters

Functions with 3 or more parameters require named arguments at the call site:

```geno
func clamp(value: Int, lo: Int, hi: Int) -> Int
    example 5, 0, 10 -> 5
    example -1, 0, 10 -> 0
    if value < lo then return lo end if
    if value > hi then return hi end if
    return value
end func

let result: Int = clamp(value: 15, lo: 0, hi: 10)
```

### Default Parameters

Parameters can have default values. Required parameters must come first:

```geno
func greet(name: String, greeting: String = "Hello") -> String
    example "Alice" -> "Hello, Alice"
    return greeting + ", " + name
end func

let msg: String = greet("Alice")              // "Hello, Alice"
let msg2: String = greet("Bob", "Hi")         // "Hi, Bob"
```

### Contracts

```geno
func factorial(n: Int) -> Int
    requires n >= 0
    ensures result >= 1
    example 5 -> 120
    example 0 -> 1

    if n <= 1 then return 1 end if
    return n * factorial(n - 1)
end func
```

### Generic Functions

A function can work at any type by declaring type parameters in brackets after
its name. Callers never write the types; each call works them out from its
arguments:

```geno
func first_or[T](xs: List[T], fallback: T) -> T
    example [1, 2], 0 -> 1
    example [], "none" -> "none"
    match xs with
        | [] -> return fallback
        | [x, ...] -> return x
    end match
end func

func swap[A, B](pair: (A, B)) -> (B, A)
    example (1, "x") -> ("x", 1)
    let (a, b): (A, B) = pair
    return (b, a)
end func
```

Each example is checked on its own, so one function's examples can use
different types. Inside the function, `T` stands for whatever type the caller
chose, so the body can only do what works for every type: pass the value on,
store it in a list, tuple or record, compare it with `==`, and print it or
convert it with `to_string`. Arithmetic and `<` on a `T` are rejected, because
`T` might be a `String` or a list.

A generic function can also be passed as a value when the surrounding code
fixes its types, as in `map([1.5, 2.5], show)` or
`let render: (Int) -> String = show`.

To compare, add up, or hash values of a type parameter, give it a bound:

```geno
func largest[T: Ord](xs: List[T], fallback: T) -> T
    example [3, 9, 2], 0 -> 9
    example ["b", "a"], "" -> "b"
    var best = fallback
    for x: T in xs do
        if x > best then
            best = x
        end if
    end for
    return best
end func
```

`Ord` allows `<` and friends and is met by `Int`, `Float` and `String`. `Num`
allows arithmetic and is met by `Int` and `Float`. `Hash` allows use as a
`Map` key or `Set` element. A trait name works as a bound too: `[T: Shape]`
lets the function call `Shape`'s methods on a `T`. Calling `largest([true],
false)` is rejected, because `Bool` is not ordered.

## Type Definitions (ADTs)

```geno
type Shape = Circle(radius: Float)
           | Rectangle(width: Float, height: Float)
           | Triangle(base: Float, height: Float)
```

A leading `|` before the first variant is also accepted, which reads better for
larger domain models:

```geno
type Tx =
    | Deposit(amount: Int)
    | Withdraw(amount: Int)
    | Audit
```

Constructors are called by name:

```geno
let s: Shape = Circle(3.14)
let r: Shape = Rectangle(10.0, 5.0)
```

## Pattern Matching

Every case must be handled:

```geno
func area(s: Shape) -> Float
    example Circle(1.0) -> 3.14159
    match s with
        | Circle(r) -> return 3.14159 * r * r
        | Rectangle(w, h) -> return w * h
        | Triangle(b, h) -> return 0.5 * b * h
    end match
end func
```

Tuples can be destructured directly in a match arm:

```geno
func describe(point: (Int, Int)) -> String
    example (0, 0) -> "origin"
    match point with
        | (0, 0) -> return "origin"
        | (x, y) -> return to_string(x) + "," + to_string(y)
    end match
end func
```

A tuple pattern must have the same number of elements as the tuple type. Since
every tuple has that fixed arity, an arm like `| (x, y) ->` already covers every
value and needs no default arm.

### If Expressions and Or-Patterns

`if` produces a value when it is used as one. The `else` branch is required:

```geno
func parity(n: Int) -> String
    example 4 -> "even"
    example 7 -> "odd"
    return if n % 2 == 0 then "even" else "odd" end if
end func
```

A match arm can list alternatives that share a body:

```geno
type Color = Red | Orange | Green | Blue

func warmth(c: Color) -> String
    example Red -> "warm"
    example Blue -> "cool"
    match c with
        | Red | Orange -> return "warm"
        | Green | Blue -> return "cool"
    end match
end func
```

## Option and Result

```geno
// Option[T] = Some(value: T) | None
func find_first_even(nums: List[Int]) -> Option[Int]
    example [1, 3, 4, 5] -> Some(4)
    example [1, 3, 5] -> None
    let filtered: List[Int] = filter(nums, fn(x: Int) -> x % 2 == 0)
    if length(filtered) == 0 then
        return None
    end if
    return Some(head(filtered))
end func

// Result[T, E] = Ok(value: T) | Err(error: E)
func safe_divide(a: Int, b: Int) -> Result[Int, String]
    example 10, 2 -> Ok(5)
    example 10, 0 -> Err("division by zero")
    if b == 0 then
        return Err("division by zero")
    end if
    return Ok(a / b)
end func
```

### Parsing Returns Option, Not Result

`parse_int` and `parse_float` return `Option[Int]` and `Option[Float]`, not
`Result`. Match on `Some`/`None` (not `Ok`/`Err`), and wrap the value in
`Ok`/`Err` yourself when a caller needs a `Result`:

```geno
// parse_int returns Option[Int], not Result: match on Some/None
func parse_port(arg: String) -> Result[Int, String]
    example "8080" -> Ok(8080)
    example "eighty" -> Err("not a number: eighty")
    match parse_int(arg) with
        | None -> return Err("not a number: " + arg)
        | Some(n) -> return Ok(n)
    end match
end func
```

### The ? Propagation Operator

```geno
func process(data: Option[String]) -> Option[Int]
    let s: String = data?         // returns None if data is None
    return parse_int(s)           // Option[Int], so it can be returned directly
end func
```

## Pipelines

Left-to-right data flow with `|>`:

```geno
func process(data: List[Int]) -> List[Int]
    example [1, 2, 3, 4, 5] -> [4, 16]
    return data
        |> filter(_, fn(x: Int) -> x % 2 == 0)
        |> map(_, fn(x: Int) -> x * x)
end func
```

The `_` placeholder marks where the piped value goes.

## Traits

```geno
trait Describable
    func describe(self: Self) -> String
end trait

type Circle = Circle(radius: Float)

impl Describable for Circle
    func describe(self: Circle) -> String
        example Circle(5.0) -> "circle with radius 5.0"
        return f"circle with radius {self.radius}"
    end func
end impl
```

## Error Handling

Geno has one error model. A failure the caller should handle is a value of
type `Result[T, E]`; a bug ends the program with a panic.

### Returning and Propagating Errors

```geno
type ValidationError = ValidationError(field: String, message: String)

func validate_age(age: Int) -> Result[Int, ValidationError]
    example 25 -> Ok(25)
    if age < 0 then
        return Err(ValidationError("age", "must be non-negative"))
    end if
    return Ok(age)
end func

func describe_age(age: Int) -> Result[String, ValidationError]
    example 30 -> Ok("30 years")
    let valid = validate_age(age)?
    return Ok(f"{valid} years")
end func

func main() -> String
    match describe_age(-1) with
        | Ok(text) -> return text
        | Err(e) -> return f"Invalid {e.field}: {e.message}"
    end match
end func
```

`?` returns the `Err` from the enclosing function and unwraps the `Ok`. Builtins
that can fail return `Result` too: `fs_read_text`, `fs_write_text`,
`http_fetch`, `http_post`, `json_parse`, `exec`.

`main` may return a `Result`, so `?` works at the top level. An `Err` is
printed to stderr as `Error: ...` and the program exits with status 1:

```geno
func main() -> Result[Unit, String] with fs, io
    let text = fs_read_text("notes.txt")?
    print(text)
    return Ok(())
end func
```

### Accessors That Cannot Fail

```geno
func first_or_zero(items: List[Int]) -> Int
    example [1, 2, 3] -> 1
    example [] -> 0
    return items.first().unwrap_or(0)
end func
```

`xs.get(i)`, `xs.first()` and `xs.last()` return `Option`. Indexing `xs[i]`
panics when `i` is out of range.

### Panics

```geno
func half(n: Int) -> Int
    example 4 -> 2
    if n % 2 != 0 then
        panic("half expects an even number")
    end if
    return n / 2
end func
```

`panic(message)` ends the program, as does every runtime error (division by
zero, an index out of range, a failed `requires`). Geno code cannot catch a
panic. Use `r.unwrap()` or `r.expect("why")` when an `Err` can only mean a bug.
Geno 0.6 removed `throw`, `try` and `catch`.

### Async/Await

```geno
async func compute(x: Int) -> Int
    return x * x
end func

func main() -> Int
    let a: Int = await compute(5)
    let b: Int = await compute(3)
    return a + b
end func
```

`async func` returns an `Async[T]` value. Use `await` to resolve it. `await` works inside `async func` and `main()`. Async functions are exempt from `example` clause requirements.

## F-Strings

```geno
let name: String = "Alice"
let age: Int = 30
let msg: String = f"Hello {name}, you are {age} years old"
```

## Collections

### Lists (Immutable)

```geno
let nums: List[Int] = [1, 2, 3, 4, 5]
let doubled: List[Int] = map(nums, fn(x: Int) -> x * 2)
let evens: List[Int] = filter(nums, fn(x: Int) -> x % 2 == 0)
let total: Int = fold(list: nums, initial: 0, reducer: fn(acc: Int, x: Int) -> acc + x)
```

Replacing one element returns a new list rather than mutating in place.
`set_at` is a prelude builtin, so it needs no import:

```geno
let nums: List[Int] = [1, 2, 3, 4, 5]
let patched: List[Int] = set_at(list: nums, index: 2, value: 99)  // [1, 2, 99, 4, 5]
// nums is unchanged
```

It raises if the index is out of range. Algorithms that write to the same
collection repeatedly -- in-place sorts and the like -- are usually clearer with
`Vec[T]` and `vec_set`, covered under Mutable Collections below.

### Leaving Out Types

Function parameters and return types are always written out, because they are
the function's contract. Inside a function, Geno fills in the types the
surrounding code already fixes:

```geno
func summarize(scores: List[Int]) -> (Int, List[Int])
    example [1, 2, 3] -> (6, [4, 9])
    let total = scores.fold(initial: 0, reducer: fn(acc, s) -> acc + s)
    let big = [s * s for s in scores if s > 1]
    for s in scores do
        if s < 0 then
            return (0, [])
        end if
    end for
    let result = (total, big)
    let (sum, squares) = result
    return (sum, squares)
end func
```

Here `acc` and `s` are `Int` because `fold` was given a list of `Int` and
`initial: 0`. A lambda with nothing to learn its parameter types from, such as
`let f = fn(x) -> x + 1`, needs them written: `fn(x: Int) -> x + 1`.

### List Comprehensions

A concise syntax for transforming and filtering lists:

```geno
let nums: List[Int] = [1, 2, 3, 4, 5]
let doubled: List[Int] = [x * 2 for x: Int in nums]          // [2, 4, 6, 8, 10]
let evens: List[Int] = [x for x: Int in nums if x % 2 == 0]  // [2, 4]
```

### Maps (Immutable)

```geno
let ages = {"ada": 36, "alan": 41}
let empty: Map[String, Int] = {}
let more = ages.insert("grace", 85)
let maybe: Option[Int] = more.get("grace")  // Some(85)
```

### Mutable Collections

```geno
// Array[T] -- fixed size
let arr: Array[Int] = array_new(size: 10, default: 0)
array_set(arr, 0, 42)

// Vec[T] -- growable
let v: Vec[Int] = vec_new()
vec_push(v, 1)
vec_push(v, 2)

// MutableMap[K, V]
let mm: MutableMap[String, Int] = mutable_map_new()
mutable_map_set(mm, "key", 42)
```

## JSON

Parse and generate JSON using the built-in `JsonValue` type:

```geno
let parsed: Result[JsonValue, String] = json_parse(text: "{\"name\": \"Alice\"}")
match parsed with
    | Ok(val) ->
        match val with
            | JsonObject(entries) -> // work with entries
            | _ -> // handle other types
        end match
    | Err(msg) -> // handle parse error
end match

let output: String = json_stringify(value: JsonObject([("key", JsonString("value"))]))
// output: {"key":"value"}
```

**Note:** `json_parse` returns `Result[JsonValue, String]`, not the original Geno type. A `json_parse` → `json_stringify` round-trip preserves the JSON structure faithfully, but the result is always a `JsonValue` tree — not your original Geno types. Use pattern matching to extract values from the `JsonValue` variants.

## Tuples

A tuple's elements are read by position:

```geno
func swap(pair: (Int, String)) -> (String, Int)
    example (1, "a") -> ("a", 1)
    return (pair.1, pair.0)
end func
```

## Type Aliases

```geno
type Coordinate = (Int, Int)
type Predicate = (Int) -> Bool
type Pair[T] = (T, T)
```

## CSV and TOML Parsing

Parse structured text formats with built-in functions:

```geno
// Parse CSV into rows
let rows: List[List[String]] = csv_parse("name,age\nAlice,30\nBob,25")

// Parse CSV with headers into maps
let records: List[Map[String, String]] = csv_parse_with_headers("name,age\nAlice,30")

// Parse TOML into JsonValue
let config: Result[JsonValue, String] = toml_parse("title = \"My App\"\nport = 8080")
```

## Process Execution

Run shell commands with the `process` capability:

```geno
// geno run myfile.geno --unsafe --cap process
func main() -> String
  let result: Result[ProcessResult, String] = exec("echo hello")
  match result with
  | Ok(pr) ->
    match pr with
    | ProcessResult(code, out, err) -> return out
    end match
  | Err(msg) -> return msg
  end match
end func
```

`exec_with_input(command, stdin)` pipes text to stdin.

## Modules

Split code across files. An import makes the imported module's exports
visible, but not what that module imports in turn: to call a function from
`Utils`, import `Utils` itself.

```geno
// Utils.geno
func helper(x: Int) -> Int
    example 1 -> 2
    return x + 1
end func
```

```geno
// Main.geno
import Utils

func main() -> Int
    return helper(41)
end func
```

### Exports

Control which symbols are visible to importers with `export`:

```geno
// MathLib.geno
export func add(x: Int, y: Int) -> Int
    example 2, 3 -> 5
    return x + y
end func

func internal_helper(x: Int) -> Int    // not visible to importers
    example 1 -> 2
    return x + 1
end func
```

**Migration note:** Modules with no `export` keywords currently expose
all symbols for backward compatibility. This implicit export-all
behavior is temporary. Add explicit `export` to any function or type
that other modules should use.

## Dependencies

Declare git dependencies in `geno.toml`:

```toml
[dependencies.my-lib]
git = "https://github.com/user/my-lib.git"
```

Then run `geno install`. The module resolver automatically finds imported modules in `geno_modules/`.
