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

# Language specification excerpt for Geno prompts
GENOTYPE_SPEC = """
Geno Language Quick Reference:

## Functions
```geno
func name(param: Type, ...) -> ReturnType
    requires condition     // precondition (optional)
    ensures condition      // postcondition (optional)
    example input -> output   // example (recommended)

    // function body
    return value
end func name
```

## Types
- Int, Float, Bool, String, Unit
- List[T], Option[T], Tuple[T1, T2]
- Custom: type Name = Variant1(field: T) | Variant2

## Variables
- let x: Int = 5      // immutable
- var x: Int = 5      // mutable

## Control Flow
```geno
if condition then
    // body
else if condition then
    // body
else
    // body
end if

while condition do
    // body
end while "optional label"

for x: Int in [1, 2, 3] do
    // body; the loop variable requires a type annotation
end for
```

## Pattern Matching
```geno
match value with
    | Pattern1 -> result1
    | Pattern2(x) -> result2
    | _ -> default
end match
```

## Pipeline
```geno
data |> function1(_) |> function2(_, arg)
```

## Built-in Functions
- length(list), head(list), tail(list)
- append(list, item), concat(list1, list2)
- filter(list, fn), map(list, fn), fold(list, initial, fn)
- contains(list, element)
- slice(list: xs, start: i, stop: j)
- max(a, b), abs(n), clamp(value: v, min: lo, max: hi)  // no min(a, b) builtin
- to_chars(string), to_lower(string), to_upper(string), trim(text)
- split(text, delimiter), join(parts, separator)
- starts_with(text, prefix), ends_with(text, suffix)
- to_string(value), parse_int(text) -> Option[Int]

## Named Arguments
Most calls with 3 or more arguments require named arguments:
slice(list: xs, start: 0, stop: 2)
Pipeline stages stay positional: xs |> fold(_, 0, fn(acc: Int, x: Int) -> acc + x)
"""

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


# Sample few-shot examples for Geno
GENOTYPE_FEW_SHOT_SAMPLES = [
    # Example 1: Simple function
    """```geno
func double(n: Int) -> Int
    example 5 -> 10
    example 0 -> 0

    return n * 2
end func double
```""",
    # Example 2: With loop
    """```geno
func sum_list(arr: List[Int]) -> Int
    example [1, 2, 3] -> 6
    example [] -> 0

    var total: Int = 0
    var i: Int = 0

    while i < length(arr) do
        total = total + arr[i]
        i = i + 1
    end while

    return total
end func sum_list
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
end func factorial
```""",
    # Example 4: With Option type
    """```geno
func find_first(arr: List[Int], target: Int) -> Option[Int]
    example [1, 2, 3], 2 -> Some(1)
    example [1, 2, 3], 5 -> None

    var i: Int = 0
    while i < length(arr) do
        if arr[i] == target then
            return Some(i)
        end if
        i = i + 1
    end while
    return None
end func find_first
```""",
    # Example 5: With pipeline
    """```geno
func sum_evens(arr: List[Int]) -> Int
    example [1, 2, 3, 4, 5, 6] -> 12
    example [1, 3, 5] -> 0

    let evens: List[Int] = arr |> filter(_, fn(x: Int) -> x % 2 == 0)
    return evens |> fold(_, 0, fn(acc: Int, x: Int) -> acc + x)
end func sum_evens
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
