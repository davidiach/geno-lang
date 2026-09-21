# LLM Prompting Guide for Geno

Guidelines for using large language models to generate, edit, and reason
about Geno source code.

## System Prompt Essentials

When prompting an LLM to write Geno code, include these rules at minimum:

```
You are writing code in Geno, a statically typed, functional-first language.

Key syntax rules:
- Use `let` for immutable bindings: `let x: Int = 5` or `let x = 5`
- Use `var` for mutable bindings: `var count: Int = 0` or `var count = 0`
- Function parameters and return values require type annotations
- Boolean literals are lowercase: `true`, `false`
- String concatenation uses `+` (same as arithmetic addition)
- Integer division truncates: `7 / 2 = 3`
- Functions end with `end func`, loops with `end for` / `end while`
- Every function needs at least one `example` clause
- Functions with 3+ parameters require named arguments at call sites
- Use `Option[T]` instead of null, `Result[T, E]` for errors
- Comments: `// line` or `/* block */`
- Doc comments: `/// text`
- Do not name anything after a Python builtin (`len`, `id`, `map`, `abs`,
  `format`, `type`, `list`, `str`, ...) — these are reserved runtime names and
  are rejected when the program is compiled
```

## Common LLM Mistakes

### 1. Using `let mut` instead of `var`

Wrong:
```
let mut count: Int = 0
```

Correct:
```
var count: Int = 0
```

### 2. Using `True`/`False` (Python-style booleans)

Wrong:
```
return True
```

Correct:
```
return true
```

### 3. Using `++` for string concatenation

Wrong:
```
let greeting: String = "Hello, " ++ name
```

Correct:
```
let greeting: String = "Hello, " + name
```

### 4. Using `end` as a parameter name

`end` is a keyword. Use `stop` instead:

Wrong:
```
func substring(text: String, start: Int, end: Int) -> String
```

Correct:
```
func substring(text: String, start: Int, stop: Int) -> String
```

### 5. Forgetting named arguments for 3+ parameters

Wrong:
```
substring("hello world", 0, 5)
```

Correct:
```
substring(text: "hello world", start: 0, stop: 5)
```

### 6. Forgetting `example` clauses

Wrong:
```
func double(n: Int) -> Int
  return n * 2
end func
```

Correct:
```
func double(n: Int) -> Int
  example (3) -> 6
  example (0) -> 0
  return n * 2
end func
```

### 7. Zero-arg example syntax

Both forms are accepted for a function that takes no parameters:
```
example () -> 42
example -> 42
```

Do not write the input as a value the function never takes — the example input
must match the parameter list.

### 8. Using `get(list, index)` instead of bracket indexing

Wrong:
```
let first: Int = get(xs, 0)
```

Correct:
```
let first: Int = xs[0]
```

### 9. Missing `then` after `if`

Wrong:
```
if x > 0
  return x
end if
```

Correct:
```
if x > 0 then
  return x
end if
```

### 10. Missing `do` after `for`/`while`

Wrong:
```
for i: Int in range(0, 10)
  println(i)
end for
```

Correct:
```
for i: Int in range(0, 10) do
  println(i)
end for
```

### 11. Reserved runtime names

`geno check`, `geno test`, editor diagnostics, and the default `geno run` reject
bindings that would shadow the compiled runtime in programs whose entrypoint
defines `main`. Targetless library checks remain permissive. The names come from the backend's host builtins, so
`len`, `id`, `map`, `abs`, `format`, `type`, `list`, `print`, and `str` are not
usable as function, parameter, or local names.

Wrong:
```
func trim_to(s: String, len: Int) -> String
```

Correct:
```
func trim_to(s: String, count: Int) -> String
```

### 12. Destructuring tuples in `match`

Tuple patterns are supported in match arms, so a pair does not need to be
let-destructured first:
```
match split_once(line, ":") with
    | Some((key, value)) -> return key
    | None -> return ""
end match
```

### 13. Multiline sum types

Variant lists may span lines, and a leading `|` is allowed:
```
type Tx =
    | Deposit(amount: Int)
    | Withdraw(amount: Int)
```

### 14. Matching `parse_int` / `parse_float` as `Result`

`parse_int` returns `Option[Int]` and `parse_float` returns `Option[Float]`.
They never produce `Ok`/`Err`, so a `Result`-style match fails to typecheck.

Wrong:
```
match parse_int(arg) with
  | Ok(n) -> return n
  | Err(e) -> return 0
end match
```

Correct:
```
match parse_int(arg) with
  | Some(n) -> return n
  | None -> return 0
end match
```

To surface a parse failure as a `Result`, wrap the branches yourself:
```
match parse_int(arg) with
  | Some(n) -> return Ok(n)
  | None -> return Err("not a number: " + arg)
end match
```

### 15. Naming a local `result` in a function with `ensures`

In a function with an `ensures` clause, `result` names the return value. A local
binding of the same name is rejected, because `ensures result` would be
ambiguous. Accumulators are the usual place this bites; name them `acc` or `out`.

Wrong:
```
func total(n: Int) -> Int
    ensures result >= 0
    example (3) -> 3

    var result: Int = 0
    var i: Int = 0
    while i < n do
        result = result + i
        i = i + 1
    end while
    return result
end func
```

```
Type Error: `result` is reserved in functions with ensures clauses; rename this
binding so `ensures result` unambiguously refers to the return value
```

Correct:
```
func total(n: Int) -> Int
    ensures result >= 0
    example (3) -> 3

    var acc: Int = 0
    var i: Int = 0
    while i < n do
        acc = acc + i
        i = i + 1
    end while
    return acc
end func
```

A function without `ensures` may use `result` as an ordinary name.

### 16. Updating one element of a list

Lists are immutable, but replacing an element does not mean rebuilding the list
by hand. `set_at` is a prelude builtin -- no import -- and returns a new list:

```
set_at(list: List[T], index: Int, value: T) -> List[T]
```

Wrong -- rebuilding around the index:
```
func swap(xs: List[Int], i: Int, j: Int) -> List[Int]
    example ([1, 2, 3], 0, 2) -> [3, 2, 1]
    var out: List[Int] = []
    var k: Int = 0
    while k < length(xs) do
        if k == i then
            out = append(out, xs[j])
        else
            if k == j then
                out = append(out, xs[i])
            else
                out = append(out, xs[k])
            end if
        end if
        k = k + 1
    end while
    return out
end func
```

Correct:
```
func swap(xs: List[Int], i: Int, j: Int) -> List[Int]
    example ([1, 2, 3], 0, 2) -> [3, 2, 1]
    let a: Int = xs[i]
    let b: Int = xs[j]
    return set_at(list: set_at(list: xs, index: i, value: b), index: j, value: a)
end func
```

`set_at` has three parameters, so its call sites need named arguments. It raises
if the index is out of range. For code that mutates in a loop rather than
threading a new list through each step, `Vec[T]` and `vec_set` are the better
fit.

### 17. Float `example` values and how close is close enough

`Float` examples are compared with a small tolerance rather than exactly, so the
exact binary literal a run prints is not required -- but a convenience rounding
is not enough either. **Keep at least twelve significant digits.**

Wrong -- right to five digits, which is nowhere near close enough:
```
func ratio(a: Float, b: Float) -> Float
    example (160.0, 7.0) -> 22.857
    return a / b
end func
```

Correct:
```
func ratio(a: Float, b: Float) -> Float
    example (160.0, 7.0) -> 22.8571428571
    return a / b
end func
```

Do not tune an expected value down to the last digit that happens to pass. The
tolerance is small and more than one comparator is involved, so a value sitting
just inside it is fragile. Twelve digits clears every path with room to spare.

The reliable habit is not to compute the expected value mentally at all. Run the
function, take the value it prints, and keep it. Where the domain allows it,
prefer `Int` arithmetic instead and sidestep the question -- money is the common
case, since working in cents makes every expected value exact.

### 18. `requires` and `Err` examples on the same function

A `requires` clause is checked before the body runs, so a function cannot both
contract its happy path and example its rejection path. An `example` whose
expected value is `Err(...)` for an input the precondition excludes fails: the
precondition rejects the input before the body can return the `Err`.

Wrong -- `requires` and the `Err` example contradict each other:
```
func to_roman(n: Int) -> Result[String, String]
    requires n >= 1
    example (1) -> Ok("I")
    example (0) -> Err("out of range")

    if n < 1 then
        return Err("out of range")
    end if
    return Ok("I")
end func
```

Correct -- keep the public `Result` API free of preconditions and validate in
the body, and put `requires` on the in-range helper it calls:
```
func roman_digit(n: Int) -> String
    requires n >= 1
    example (1) -> "I"
    return "I"
end func

func to_roman(n: Int) -> Result[String, String]
    example (1) -> Ok("I")
    example (0) -> Err("out of range")

    if n < 1 then
        return Err("out of range")
    end if
    return Ok(roman_digit(n))
end func
```

The rule of thumb: a function that returns `Result` validates its own input, so
it takes no `requires`. A helper that assumes valid input takes the `requires`
and is never called with anything else.

### 19. Multi-file projects still need `import`

Listing a module in `geno.toml` makes it part of the project. It does not bring
its declarations into scope. Every module that uses another module's functions
imports it, and a missing import fails at the call site -- where the name is
used, not where the module is declared.

Given `files = ["Lib", "Main"]` in `geno.toml` and a `double` defined in
`Lib.geno`, this fails:
```
func main() -> Int
    return double(4)
end func
```

Correct:
```
import Lib

func main() -> Int
    return double(4)
end func
```

A plain `import Lib` puts the module's functions in scope unqualified, so
`double(4)` works. The qualified form `Lib.double(4)` is also valid and is worth
preferring when two modules define the same name.

## Prompting Patterns

### Generate a function

```
Write a Geno function that [description].

Requirements:
- Include example clauses demonstrating edge cases
- Use named arguments for calls with 3+ parameters
- Prefer immutable `let` bindings over mutable `var`
- Use pattern matching (`match ... with`) over nested if/else chains
```

### Translate from Python

```
Translate this Python function to Geno:

[Python code]

Remember:
- `True`/`False` → `true`/`false`
- `def` → `func ... end func`
- `elif` → `else if ... then`
- `for x in range(n)` → `for x: Int in range(0, n) do ... end for`
- `None` → use `Option[T]` with `Some(value)` / `None`
- Add type annotations to all parameters and return types
- Add example clauses
```

### Fix Geno code

```
Fix this Geno code. Common issues to check:
- `var` not `let mut` for mutable bindings
- `true`/`false` not `True`/`False`
- `+` not `++` for string concatenation
- Named arguments for 3+ parameter calls
- `then` after `if`, `do` after `for`/`while`
- `end func`/`end if`/`end for`/`end while` block terminators
- `stop` not `end` as parameter name (keyword conflict)
```

## Machine-Readable Specification

A complete machine-readable language specification is available at
`spec.json` in the repository root. It includes all keywords, types,
operators (with precedence), syntax templates, and built-in function
signatures. Feed this to LLMs as structured context for accurate code
generation.

## Providing Context for Code Generation

For best results, include in the LLM context:

1. **`spec.json`** — full language spec (keywords, types, builtins)
2. **Example code** — working Geno programs from `examples/`
3. **Standard library** — files in `geno/std/` for available functions
4. **This guide** — common pitfalls to avoid

## Compilation Targets

Geno compiles to Python and JavaScript. When asking an LLM to reason
about compiled output:

- **Python target**: uses type-directed optimization with safety wrappers
  where needed, resulting in ~1.1x median overhead vs hand-written Python
  on the 30 measured benchmark problems
- **JavaScript target**: used for browser apps with the init/update/render
  lifecycle
- **Hosted runtime**: executes Geno source via HTTP POST to `/run`

## Benchmark Reference

Compiled Geno Python runs at a median 1.09x overhead compared to
equivalent hand-written Python (mean 1.23x; 30 of 77 problems measured,
47 skipped as too noisy for stable timing ratios).
The compiler uses type-directed optimization to emit raw arithmetic for
typed operands and only inserts safety wrappers (`_safe_add`,
`_safe_index`) where types cannot be statically proven safe.

See `benchmarks/RESULTS.md` for detailed per-problem data.
