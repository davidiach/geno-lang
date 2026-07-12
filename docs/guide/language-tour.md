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

`let` bindings are immutable; use `var` when you need mutation.

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

## Type Definitions (ADTs)

```geno
type Shape = Circle(radius: Float)
           | Rectangle(width: Float, height: Float)
           | Triangle(base: Float, height: Float)
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

### The ? Propagation Operator

```geno
func process(data: Option[String]) -> Option[Int]
    let s: String = data?         // returns None if data is None
    return parse_int(s)
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

### Try/Catch

```geno
func safe_head(items: List[Int]) -> Int
    example [1, 2, 3] -> 1
    example [] -> 0
    try
        return head(items)
    catch e: String
        return 0
    end try
end func
```

### Throw and Structured Errors

Throw string errors or user-defined error types:

```geno
type ValidationError = ValidationError(field: String, message: String)

func validate_age(age: Int) -> Int
    example 25 -> 25
    if age < 0 then
        throw ValidationError("age", "must be non-negative")
    end if
    return age
end func

func main() -> String
    try
        let age: Int = validate_age(-1)
        return to_string(age)
    catch e: ValidationError
        return f"Invalid {e.field}: {e.message}"
    end try
end func
```

`catch e: String` catches runtime errors and thrown strings. `catch e: MyType` catches only thrown values of that type — unmatched types propagate outward.

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
let total: Int = fold(nums, 0, fn(acc: Int, x: Int) -> acc + x)
```

### List Comprehensions

A concise syntax for transforming and filtering lists:

```geno
let nums: List[Int] = [1, 2, 3, 4, 5]
let doubled: List[Int] = [x * 2 for x: Int in nums]          // [2, 4, 6, 8, 10]
let evens: List[Int] = [x for x: Int in nums if x % 2 == 0]  // [2, 4]
```

### Maps (Immutable)

```geno
let m: Map[String, Int] = {}
let m2: Map[String, Int] = map_insert(m, "a", 1)
let val: Option[Int] = map_get(m2, "a")  // Some(1)
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

Split code across files:

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
