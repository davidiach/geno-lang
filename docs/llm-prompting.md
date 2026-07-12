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

Wrong:
```
example -> 42
```

Correct:
```
example () -> 42
```

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
