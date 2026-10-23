"""
Prompt Templates
================

Standardized prompt templates for benchmark evaluations.
"""

from benchmark.schema import (
    format_geno_example_input,
    format_geno_literal,
    format_python_example_call,
    format_python_type,
    python_return_convention,
)

# The rules every Geno prompt carries. Kept identical to the block under
# "System Prompt Essentials" in docs/llm-prompting.md, which documents it;
# geno/tests/test_experiment_prompts.py checks the two agree.
GENO_RULES = """\
You are writing code in Geno, a statically typed, functional-first language.

Key syntax rules:
- Use `let` for immutable bindings: `let x: Int = 5` or `let x = 5`
- Use `var` for mutable bindings: `var count: Int = 0` or `var count = 0`
- Update a `var` by assigning to it: `count = count + 1`, not `var count = count + 1`
- Function parameters and return values require type annotations
- Boolean literals are lowercase: `true`, `false`
- String concatenation uses `+` (same as arithmetic addition)
- Integer division truncates: `7 / 2 = 3`
- Blocks: `if c then ... else if c then ... else ... end if`,
  `for x in xs do ... end for`, `while c do ... end while`,
  `match v with | Some(x) -> ... | None -> ... end match`. `then` and `do` are
  required, and every block closes with `end` and its keyword (`end func`)
- Logical operators are words: `and`, `or`, `not`. There is no `&&`, `||`, `!`
  or `null`: an absent value is `None`
- Return values with `return`; a function's last expression is not returned
  implicitly
- Every function needs at least one `example` clause
- A call that writes 3+ arguments names them: `clamp(value: x, min: 0, max: 9)`.
  A method call's receiver is not counted, so `xs.fold(0, f)` and
  `s.replace("a", "b")` stay positional
- Use `Option[T]` instead of null, `Result[T, E]` for errors. There is no
  `throw`, `try` or `catch`: return `Err(...)`, propagate with `?` (also from
  `main() -> Result[Unit, String]`), and call `panic("...")` only for bugs.
  `fs_read_text`, `fs_write_text`, `http_fetch` and `http_post` return
  `Result[_, String]`. Use `xs.get(i)` / `xs.first()` for an `Option` instead of
  an index that may be out of range
- Generic functions declare type parameters in brackets after the name:
  `func first_or[T](xs: List[T], fallback: T) -> T`. Calls infer them; never
  write `first_or[Int](...)` or `<T>`. To use `<`, arithmetic, or a `T` as a map
  key, add a bound: `[T: Ord]`, `[T: Num]`, `[K: Hash]`, or a trait name
- Lambda parameters, `for` variables, comprehension variables and tuple
  destructuring may omit types when context fixes them: `xs.map(fn(x) -> x * 2)`,
  `for x in xs do`, `[x * 2 for x in xs]`, `let (a, b) = pair`. Function
  parameters and return types are always annotated
- `if` is an expression when used as a value, with a required `else`:
  `let label = if x > 0 then "pos" else "neg" end if`. Match arms can share a
  body: `| Red | Orange -> ...`. Tuple fields: `pair.0`. Map literals:
  `{"a": 1, "b": 2}`, and `{}` for an empty map
- Method calls work on any value: `xs.map(f).filter(g).length()`,
  `s.trim().to_upper()`, `opt.unwrap_or(0)`, `n.to_float()`. The free form works
  too: `map(opt, f)` is `opt.map(f)`
- Membership: `x in xs`, `c in "aeiou"`, `key in m`, `x not in xs`. Lists
  concatenate with `+`: `xs + [4]`
- Every collection has `length()`. Maps: `m.get(k)` (Option), `m.contains_key(k)`,
  `m.insert(k, v)`, `m.remove(k)`, `m.keys()`, `m.values()`.
  `xs.sort()` needs no comparator. `s.index_of(t)` returns `Option[Int]`
- Comments: `// line` or `/* block */`
- Doc comments: `/// text`
- Any ordinary name works, including host names such as `len`, `id`, `list`,
  `str`, `max` or a constructor `Error`; the compilers rename them. Only
  names beginning with `_` that the runtime uses are reserved
"""

# A complete Geno 0.6 program showing the forms the rules describe. The
# prompt test checks it type-checks, its examples pass, and it is formatted.
GENO_REFERENCE_PROGRAM = """\
type Shape = Circle(radius: Float) | Rect(width: Float, height: Float)

/// Sum types are matched; every arm returns a value.
func area(shape: Shape) -> Float
    example Circle(1.0) -> 3.0
    example Rect(2.0, 3.0) -> 6.0
    return match shape with
        | Circle(r) -> 3.0 * r * r
        | Rect(w, h) -> w * h
    end match
end func

/// Errors are values: return Err(...), and the caller propagates with ?.
func parse_ages(text: String) -> Result[List[Int], String]
    example "36, 41" -> Ok([36, 41])
    example "36, x" -> Err("not a number: x")
    var ages: List[Int] = []
    for part in text.split(",") do
        let trimmed = part.trim()
        match parse_int(trimmed) with
            | Some(age) -> ages = ages.append(age)
            | None -> return Err("not a number: " + trimmed)
        end match
    end for
    return Ok(ages)
end func

func describe(text: String) -> Result[String, String]
    example "36, 41" -> Ok("2 ages, oldest 41")
    example "7" -> Ok("1 age, oldest 7")
    let ages = parse_ages(text)?
    let oldest = ages.fold(initial: 0, reducer: fn(best, age) -> max(best, age))
    let noun = if ages.length() == 1 then "age" else "ages" end if
    return Ok(f"{ages.length()} {noun}, oldest {oldest}")
end func

/// Generic functions name their type parameters; bounds enable < and +.
func largest[T: Ord](xs: List[T], fallback: T) -> T
    example ([3, 9, 4], 0) -> 9
    example ([], 5) -> 5
    var best = fallback
    for x in xs do
        if x > best then
            best = x
        end if
    end for
    return best
end func

func main() -> Unit
    let words = ["kiwi", "fig", "apple"]
    let lengths = words.map(fn(w) -> w.length())
    let long = words.filter(fn(w) -> w.length() > 3)
    let k_words = words.filter(fn(w) -> w.starts_with("k"))
    let counts = {"kiwi": 2, "fig": 5}
    print(lengths)
    print(long.join(", ").to_upper())
    print(counts.get("fig").unwrap_or(0))
    print(to_string(largest(lengths, 0)) + " letters, " + to_string(k_words))
    print(describe("36, 41"))
end func
"""

# Language reference for Geno prompts: the rules, then the program.
GENOTYPE_SPEC = (
    "\nGeno Language Quick Reference (Geno 0.6):\n\n"
    + GENO_RULES
    + "\n## Reference Program\n\n```geno\n"
    + GENO_REFERENCE_PROGRAM
    + "```\n"
)

# Zero-shot Geno template
GENOTYPE_ZERO_SHOT = """You are an expert programmer in Geno, a programming language designed for clarity and correctness.

{spec}

## Problem

{description}

## Constraints
{constraints}

## Function Signature
```geno
func {function_name}({params}) -> {return_type}
```

## Examples
{examples}

Write your complete solution in Geno. Include the function signature with examples, and the implementation:
"""

# Few-shot Geno template
GENOTYPE_FEW_SHOT = """You are an expert programmer in Geno, a programming language designed for clarity and correctness.

{spec}

## Example Solutions

{few_shot_examples}

---

## Your Task

{description}

## Constraints
{constraints}

## Function Signature
```geno
func {function_name}({params}) -> {return_type}
```

## Examples
{examples}

Write your complete solution in Geno:
"""

# Zero-shot Python template
PYTHON_ZERO_SHOT = """You are an expert Python programmer.

## Problem

{description}

## Constraints
{constraints}

## Function Signature
```python
def {function_name}({params}) -> {return_type}:
```
{return_conventions}

## Examples
{examples}

Write your complete solution in Python:
"""

# Few-shot Python template
PYTHON_FEW_SHOT = """You are an expert Python programmer.

## Example Solutions

{few_shot_examples}

---

## Your Task

{description}

## Constraints
{constraints}

## Function Signature
```python
def {function_name}({params}) -> {return_type}:
```
{return_conventions}

## Examples
{examples}

Write your complete solution in Python:
"""


def format_geno_prompt(
    problem, few_shot_examples: list | None = None, include_spec: bool = True
) -> str:
    """Format a Geno prompt for a problem."""
    spec = GENOTYPE_SPEC if include_spec else ""

    params = ", ".join(f"{i.name}: {i.type}" for i in problem.inputs)
    constraints = (
        "\n".join(f"- {c}" for c in problem.constraints)
        if problem.constraints
        else "None"
    )

    examples = "\n".join(
        f"    example {format_geno_example_input(e.input, problem.inputs)} -> "
        f"{format_geno_literal(e.output, problem.output.type)}"
        for e in problem.visible_examples
    )

    if few_shot_examples:
        fs_text = "\n\n".join(few_shot_examples)
        return GENOTYPE_FEW_SHOT.format(
            spec=spec,
            few_shot_examples=fs_text,
            description=problem.description,
            constraints=constraints,
            function_name=problem.function_name,
            params=params,
            return_type=problem.output.type,
            examples=examples,
        )
    else:
        return GENOTYPE_ZERO_SHOT.format(
            spec=spec,
            description=problem.description,
            constraints=constraints,
            function_name=problem.function_name,
            params=params,
            return_type=problem.output.type,
            examples=examples,
        )


def format_python_prompt(problem, few_shot_examples: list | None = None) -> str:
    """Format a Python prompt for a problem."""
    params = ", ".join(
        f"{i.name}: {format_python_type(i.type)}" for i in problem.inputs
    )
    return_type = format_python_type(problem.output.type)
    constraints = (
        "\n".join(f"- {c}" for c in problem.constraints)
        if problem.constraints
        else "None"
    )
    return_conventions = python_return_convention(problem.output.type)

    examples = "\n".join(
        f"    # {format_python_example_call(problem, e)}"
        for e in problem.visible_examples
    )

    if few_shot_examples:
        fs_text = "\n\n".join(few_shot_examples)
        return PYTHON_FEW_SHOT.format(
            few_shot_examples=fs_text,
            description=problem.description,
            constraints=constraints,
            function_name=problem.function_name,
            params=params,
            return_type=return_type,
            return_conventions=return_conventions,
            examples=examples,
        )
    else:
        return PYTHON_ZERO_SHOT.format(
            description=problem.description,
            constraints=constraints,
            function_name=problem.function_name,
            params=params,
            return_type=return_type,
            return_conventions=return_conventions,
            examples=examples,
        )


# Sample few-shot examples for Geno, written as Geno 0.6 code. The prompt
# test checks each one type-checks, passes its examples, and is formatted.
GENOTYPE_FEW_SHOT_SAMPLES = [
    # Example 1: Simple function
    """```geno
func double(n: Int) -> Int
    example 5 -> 10
    example 0 -> 0
    return n * 2
end func
```""",
    # Example 2: With loop
    """```geno
func sum_list(arr: List[Int]) -> Int
    example [1, 2, 3] -> 6
    example [] -> 0
    var total = 0
    for x in arr do
        total = total + x
    end for
    return total
end func
```""",
    # Example 3: With recursion
    """```geno
func factorial(n: Int) -> Int
    requires n >= 0
    example 5 -> 120
    example 0 -> 1
    if n <= 1 then
        return 1
    else
        return n * factorial(n - 1)
    end if
end func
```""",
    # Example 4: With Option type
    """```geno
func find_first(arr: List[Int], target: Int) -> Option[Int]
    example [1, 2, 3], 2 -> Some(1)
    example [1, 2, 3], 5 -> None
    var i = 0
    while i < arr.length() do
        if arr[i] == target then
            return Some(i)
        end if
        i = i + 1
    end while
    return None
end func
```""",
    # Example 5: With methods and lambdas
    """```geno
func sum_evens(arr: List[Int]) -> Int
    example [1, 2, 3, 4, 5, 6] -> 12
    example [1, 3, 5] -> 0
    let evens = arr.filter(fn(x) -> x % 2 == 0)
    return evens.fold(initial: 0, reducer: fn(acc, x) -> acc + x)
end func
```""",
]

# Sample few-shot examples for Python
PYTHON_FEW_SHOT_SAMPLES = [
    # Example 1: Simple function
    """```python
def double(n: int) -> int:
    return n * 2
```""",
    # Example 2: With loop
    """```python
def sum_list(arr: list[int]) -> int:
    total = 0
    for x in arr:
        total += x
    return total
```""",
    # Example 3: With recursion
    """```python
def factorial(n: int) -> int:
    if n <= 1:
        return 1
    else:
        return n * factorial(n - 1)
```""",
    # Example 4: With Optional
    """```python
def find_first(arr: list[int], target: int) -> int | None:
    for i, x in enumerate(arr):
        if x == target:
            return i
    return None
```""",
    # Example 5: Functional style
    """```python
def sum_evens(arr: list[int]) -> int:
    return sum(x for x in arr if x % 2 == 0)
```""",
]


# --- Repair-round prompts (benchmark v2) ------------------------------------
# Diagnostics mirror what a developer sees locally: parse/type errors and
# visible example failures. Hidden test details are never leaked.
REPAIR_TEMPLATE = """{original_prompt}

Your previous attempt:
```{language}
{code}
```

The attempt is not correct yet. Diagnostics:
{diagnostics}

Write the corrected complete solution in {language_title}:
"""


def format_repair_prompt(
    original_prompt: str, code: str, diagnostics: str, language: str
) -> str:
    """Build the one-round repair prompt for a failed attempt."""
    return REPAIR_TEMPLATE.format(
        original_prompt=original_prompt.rstrip(),
        language=language,
        code=code.strip(),
        diagnostics=diagnostics.strip(),
        language_title="Geno" if language == "geno" else language.title(),
    )
