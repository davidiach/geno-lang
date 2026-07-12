"""
Benchmark Problem Schema
========================

Defines the structure and validation for benchmark problems.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


class Difficulty(Enum):
    """Problem difficulty levels."""

    TRIVIAL = "trivial"  # Single construct, <10 lines
    EASY = "easy"  # 2-3 constructs, 10-30 lines
    MEDIUM = "medium"  # Multiple constructs, 30-60 lines
    HARD = "hard"  # Complex algorithms, 60-150 lines
    EXPERT = "expert"  # System-level, 150+ lines


class Domain(Enum):
    """Problem domains."""

    ARRAYS = "arrays"
    STRINGS = "strings"
    TREES = "trees"
    GRAPHS = "graphs"
    DYNAMIC_PROGRAMMING = "dp"
    MATH = "math"
    SORTING = "sorting"
    SEARCHING = "searching"
    LINKED_STRUCTURES = "linked"
    SYSTEMS = "systems"
    RECURSION = "recursion"
    PATTERN_MATCHING = "matching"


@dataclass
class TypeSignature:
    """Type signature for function inputs/outputs."""

    name: str
    type: str


@dataclass
class TestCase:
    """A single test case with input and expected output."""

    input: Any  # Can be single value or list of values
    output: Any  # Expected output
    description: str = ""  # Optional description


def _is_unit_type(geno_type: str | None) -> bool:
    """Return whether a benchmark type denotes Geno Unit."""
    return (geno_type or "").strip() in {"Unit", "()"}


def _is_unit_value(value: Any) -> bool:
    """Return whether JSON-backed data represents Unit."""
    return value is None or value == [] or value == ()


def _requires_explicit_option_payload(geno_type: str | None) -> bool:
    """Return whether a Python Option payload must keep the Some wrapper."""
    type_text = geno_type or ""
    return (
        _is_unit_type(geno_type)
        or _type_args(type_text, "Option") is not None
        or _type_args(type_text, "Result") is not None
    )


def _allows_none_payload(geno_type: str | None) -> bool:
    """Return whether None can represent a value for this payload type."""
    return _is_unit_type(geno_type) or _type_args(geno_type or "", "Option") is not None


def format_geno_literal(value: Any, expected_type: str | None = None) -> str:
    """Render benchmark data as a Geno source literal."""
    if (
        expected_type is not None
        and expected_type.startswith("Option[")
        and value == "None"
    ):
        return "None"
    if (
        expected_type is not None
        and expected_type.startswith("Option[")
        and value == {"None": None}
    ):
        return "None"
    if _is_unit_type(expected_type):
        if _is_unit_value(value):
            return "()"
        raise TypeError(f"Cannot render {value!r} as Geno type {expected_type!r}")
    tuple_args = _tuple_type_args(expected_type or "")
    if tuple_args is not None:
        if not isinstance(value, list | tuple):
            raise TypeError(
                f"Expected sequence for tuple type {expected_type!r}, got {value!r}"
            )
        if len(value) != len(tuple_args):
            raise TypeError(
                f"Expected {len(tuple_args)} values for tuple type "
                f"{expected_type!r}, got {len(value)}"
            )
        parts = [
            format_geno_literal(item, tuple_args[index])
            for index, item in enumerate(value)
        ]
        suffix = "," if len(parts) == 1 else ""
        return "(" + ", ".join(parts) + suffix + ")"

    option_args = _type_args(expected_type or "", "Option")
    if option_args and len(option_args) == 1:
        if value is None or value == "None" or value == {"None": None}:
            return "None"
        if isinstance(value, dict) and len(value) == 1 and set(value) == {"Some"}:
            payload = value["Some"]
            if payload is None and not _allows_none_payload(option_args[0]):
                raise TypeError(
                    f"Cannot render None payload for variant 'Some' "
                    f"as Geno type {option_args[0]!r}"
                )
            return f"Some({format_geno_literal(payload, option_args[0])})"
        raise TypeError(f"Cannot render {value!r} as Geno type {expected_type!r}")

    result_args = _type_args(expected_type or "", "Result")
    if result_args and len(result_args) == 2:
        if not isinstance(value, dict) or len(value) != 1:
            raise TypeError(f"Cannot render {value!r} as Geno type {expected_type!r}")
        if set(value) == {"Ok"}:
            payload = value["Ok"]
            if payload is None and not _allows_none_payload(result_args[0]):
                raise TypeError(
                    f"Cannot render None payload for variant 'Ok' "
                    f"as Geno type {result_args[0]!r}"
                )
            return f"Ok({format_geno_literal(payload, result_args[0])})"
        if set(value) == {"Err"}:
            payload = value["Err"]
            if payload is None and not _allows_none_payload(result_args[1]):
                raise TypeError(
                    f"Cannot render None payload for variant 'Err' "
                    f"as Geno type {result_args[1]!r}"
                )
            return f"Err({format_geno_literal(payload, result_args[1])})"
        raise TypeError(f"Cannot render {value!r} as Geno type {expected_type!r}")

    if value is None:
        if expected_type is not None and expected_type.startswith("Option["):
            return "None"
        raise TypeError(f"Cannot render None as Geno type {expected_type!r}")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        list_args = _type_args(expected_type or "", "List")
        item_type = list_args[0] if list_args and len(list_args) == 1 else None
        return (
            "["
            + ", ".join(format_geno_literal(item, item_type) for item in value)
            + "]"
        )
    if isinstance(value, dict) and len(value) == 1:
        variant, inner = next(iter(value.items()))
        payload_type = _variant_payload_type(expected_type, str(variant))
        if payload_type is None:
            if expected_type is not None:
                raise TypeError(
                    f"Cannot render variant {variant!r} as Geno type {expected_type!r}"
                )
            if inner is None:
                return str(variant)
            return f"{variant}({format_geno_literal(inner)})"
        if inner is None:
            if _is_unit_type(payload_type):
                return f"{variant}(())"
            if _type_args(payload_type or "", "Option"):
                return f"{variant}(None)"
            raise TypeError(
                f"Cannot render None payload for variant {variant!r} "
                f"as Geno type {payload_type!r}"
            )
        return f"{variant}({format_geno_literal(inner, payload_type)})"
    raise TypeError(f"Cannot render {value!r} as a Geno literal")


def _variant_payload_type(geno_type: str | None, variant: str) -> str | None:
    """Return the expected payload type for simple benchmark ADT constructors."""
    option_args = _type_args(geno_type or "", "Option")
    if option_args and len(option_args) == 1 and variant == "Some":
        return option_args[0]

    result_args = _type_args(geno_type or "", "Result")
    if result_args and len(result_args) == 2:
        if variant == "Ok":
            return result_args[0]
        if variant == "Err":
            return result_args[1]

    return None


def format_geno_example_input(values: Any, inputs: list[TypeSignature]) -> str:
    """Render benchmark example input values for a Geno example clause."""
    if not inputs:
        return "()"
    if len(inputs) > 1 and isinstance(values, list):
        return ", ".join(
            format_geno_literal(value, sig.type)
            for value, sig in zip(values, inputs, strict=True)
        )
    expected_type = inputs[0].type if inputs else None
    if len(inputs) == 1 and isinstance(values, list) and len(values) == 1:
        return format_geno_literal(values[0], expected_type)
    return format_geno_literal(values, expected_type)


def _type_args(geno_type: str, name: str) -> list[str] | None:
    prefix = f"{name}["
    if not geno_type.startswith(prefix) or not geno_type.endswith("]"):
        return None

    body = geno_type[len(prefix) : -1]
    return _split_type_args(body)


def _tuple_type_args(geno_type: str) -> list[str] | None:
    """Return tuple type arguments for Tuple[...] or canonical (...)."""
    tuple_args = _type_args(geno_type, "Tuple")
    if tuple_args is not None:
        return tuple_args

    stripped = geno_type.strip()
    if _is_unit_type(stripped):
        return None
    if not stripped.startswith("(") or not stripped.endswith(")"):
        return None

    return _split_type_args(stripped[1:-1])


def _split_type_args(body: str) -> list[str]:
    """Split generic or tuple type arguments at top-level commas."""
    if not body.strip():
        return []

    parts: list[str] = []
    start = 0
    bracket_depth = 0
    paren_depth = 0
    for index, char in enumerate(body):
        if char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth -= 1
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth -= 1
        elif char == "," and bracket_depth == 0 and paren_depth == 0:
            parts.append(body[start:index].strip())
            start = index + 1
    parts.append(body[start:].strip())
    return parts


def format_python_type(geno_type: str) -> str:
    """Convert a Geno type into a Python prompt type hint."""
    if geno_type.strip() == "()":
        return "None"

    conversions = {
        "Int": "int",
        "Float": "float",
        "Bool": "bool",
        "String": "str",
        "Unit": "None",
    }
    if geno_type in conversions:
        return conversions[geno_type]

    list_args = _type_args(geno_type, "List")
    if list_args and len(list_args) == 1:
        return f"list[{format_python_type(list_args[0])}]"

    option_args = _type_args(geno_type, "Option")
    if option_args and len(option_args) == 1:
        if _requires_explicit_option_payload(option_args[0]):
            return "dict[str, object] | None"
        return f"{format_python_type(option_args[0])} | None"

    tuple_args = _tuple_type_args(geno_type)
    if tuple_args is not None:
        return "tuple[" + ", ".join(format_python_type(arg) for arg in tuple_args) + "]"

    result_args = _type_args(geno_type, "Result")
    if result_args and len(result_args) == 2:
        return "dict[str, object]"

    return "object"


def format_python_literal(value: Any, expected_type: str | None = None) -> str:
    """Render benchmark data as a Python source literal for prompts."""
    if _is_unit_type(expected_type):
        if _is_unit_value(value):
            return "None"
        raise TypeError(f"Cannot render {value!r} as Python type {expected_type!r}")

    option_args = _type_args(expected_type or "", "Option")
    if option_args and len(option_args) == 1:
        if value is None or value == "None" or value == {"None": None}:
            return "None"
        if isinstance(value, dict) and set(value) == {"Some"}:
            if value["Some"] is None and not _allows_none_payload(option_args[0]):
                raise TypeError(
                    f"Cannot render None payload as Python type {option_args[0]!r}"
                )
            if _requires_explicit_option_payload(option_args[0]):
                return (
                    '{"Some": '
                    + format_python_literal(value["Some"], option_args[0])
                    + "}"
                )
            return format_python_literal(value["Some"], option_args[0])
        if isinstance(value, dict) and set(value) == {"None"}:
            raise TypeError(f"Cannot render {value!r} as Python type {expected_type!r}")
        raise TypeError(f"Cannot render {value!r} as Python type {expected_type!r}")

    result_args = _type_args(expected_type or "", "Result")
    if result_args and len(result_args) == 2:
        if not isinstance(value, dict) or len(value) != 1:
            raise TypeError(f"Cannot render {value!r} as Python type {expected_type!r}")
        if set(value) == {"Ok"}:
            if value["Ok"] is None and not _allows_none_payload(result_args[0]):
                raise TypeError(
                    f"Cannot render None payload as Python type {result_args[0]!r}"
                )
            return '{"Ok": ' + format_python_literal(value["Ok"], result_args[0]) + "}"
        if set(value) == {"Err"}:
            if value["Err"] is None and not _allows_none_payload(result_args[1]):
                raise TypeError(
                    f"Cannot render None payload as Python type {result_args[1]!r}"
                )
            return (
                '{"Err": ' + format_python_literal(value["Err"], result_args[1]) + "}"
            )
        raise TypeError(f"Cannot render {value!r} as Python type {expected_type!r}")

    tuple_args = _tuple_type_args(expected_type or "")
    if tuple_args is not None:
        if not isinstance(value, list | tuple):
            raise TypeError(
                f"Expected sequence for tuple type {expected_type!r}, got {value!r}"
            )
        if len(value) != len(tuple_args):
            raise TypeError(
                f"Expected {len(tuple_args)} values for tuple type "
                f"{expected_type!r}, got {len(value)}"
            )
        parts = [
            format_python_literal(item, tuple_args[index])
            for index, item in enumerate(value)
        ]
        suffix = "," if len(parts) == 1 else ""
        return "(" + ", ".join(parts) + suffix + ")"

    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        list_args = _type_args(expected_type or "", "List")
        item_type = list_args[0] if list_args and len(list_args) == 1 else None
        return (
            "["
            + ", ".join(format_python_literal(item, item_type) for item in value)
            + "]"
        )
    if isinstance(value, dict):
        items = [
            f"{format_python_literal(key)}: {format_python_literal(inner)}"
            for key, inner in value.items()
        ]
        return "{" + ", ".join(items) + "}"
    raise TypeError(f"Cannot render {value!r} as a Python literal")


def format_python_example_input(values: Any, inputs: list[TypeSignature] | int) -> str:
    """Render benchmark example input values for a Python prompt comment."""
    if isinstance(inputs, int):
        input_types: list[TypeSignature] = []
        input_count = inputs
    else:
        input_types = inputs
        input_count = len(inputs)

    if input_count == 0:
        return ""

    if isinstance(values, list) and input_count > 1:
        return ", ".join(
            format_python_literal(
                value,
                input_types[index].type if input_types else None,
            )
            for index, value in enumerate(values)
        )
    if isinstance(values, list) and input_count == 1:
        expected_type = input_types[0].type if input_types else None
        return (
            format_python_literal(values[0], expected_type)
            if len(values) == 1
            else format_python_literal(values, expected_type)
        )
    expected_type = input_types[0].type if input_types else None
    return format_python_literal(values, expected_type)


def format_python_example_call(problem: "Problem", testcase: TestCase) -> str:
    """Render one visible example as an executable-looking Python call comment."""
    args = format_python_example_input(testcase.input, problem.inputs)
    output = format_python_literal(testcase.output, problem.output.type)
    return f"{problem.function_name}({args}) -> {output}"


def python_return_convention(output_type: str) -> str:
    """Return any extra Python prompt convention needed for schema ADTs."""
    option_args = _type_args(output_type, "Option")
    if (
        option_args
        and len(option_args) == 1
        and _requires_explicit_option_payload(option_args[0])
    ):
        return 'For nested Option or Option[Unit] returns, use {"Some": value} for present values and None for absent.'
    if _type_args(output_type, "Result"):
        return 'For Result returns, use {"Ok": value} for success and {"Err": message} for errors.'
    return ""


@dataclass
class Problem:
    """
    A benchmark problem specification.

    This defines everything needed to:
    1. Present the problem to an LLM
    2. Evaluate generated solutions
    3. Compare across languages
    """

    # Identification
    id: str  # Unique identifier: "PROB-001"
    name: str  # Human-readable name
    difficulty: Difficulty
    domain: Domain

    # Problem description
    description: str  # Full problem description

    # Function signature
    function_name: str  # Name of function to implement
    inputs: list[TypeSignature]  # Input parameters
    output: TypeSignature  # Return type

    # Test cases
    visible_examples: list[TestCase]  # Given to LLM in prompt
    hidden_tests: list[TestCase]  # For evaluation only

    # Constraints
    constraints: list[str] = field(default_factory=list)

    # Solutions
    geno_solution: str = ""  # Canonical solution in Geno
    python_solution: str = ""  # Equivalent Python solution

    # Metadata
    tags: list[str] = field(default_factory=list)
    source: str = ""  # Origin (e.g., "LeetCode #1", "Original")
    time_complexity: str = ""  # Expected time complexity
    space_complexity: str = ""  # Expected space complexity
    constructs_tested: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "problem": {
                "id": self.id,
                "name": self.name,
                "difficulty": self.difficulty.value,
                "domain": self.domain.value,
                "description": self.description,
                "signature": {
                    "function_name": self.function_name,
                    "inputs": [{"name": i.name, "type": i.type} for i in self.inputs],
                    "output": {"name": self.output.name, "type": self.output.type},
                },
                "examples": {
                    "visible": [
                        {
                            "input": e.input,
                            "output": e.output,
                            "description": e.description,
                        }
                        for e in self.visible_examples
                    ],
                    "hidden": [
                        {
                            "input": e.input,
                            "output": e.output,
                            "description": e.description,
                        }
                        for e in self.hidden_tests
                    ],
                },
                "constraints": self.constraints,
                "canonical_solutions": {
                    "geno": self.geno_solution,
                    "python": self.python_solution,
                },
                "metadata": {
                    "tags": self.tags,
                    "source": self.source,
                    "time_complexity": self.time_complexity,
                    "space_complexity": self.space_complexity,
                    "constructs_tested": self.constructs_tested,
                },
            }
        }

    def to_yaml(self) -> str:
        """Convert to YAML string."""
        return str(yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False))

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "Problem":
        """Create Problem from dictionary."""
        p = data["problem"]
        return cls(
            id=p["id"],
            name=p["name"],
            difficulty=Difficulty(p["difficulty"]),
            domain=Domain(p["domain"]),
            description=p["description"],
            function_name=p["signature"]["function_name"],
            inputs=[
                TypeSignature(i["name"], i["type"]) for i in p["signature"]["inputs"]
            ],
            output=TypeSignature(
                p["signature"]["output"]["name"], p["signature"]["output"]["type"]
            ),
            visible_examples=[
                TestCase(e["input"], e["output"], e.get("description", ""))
                for e in p["examples"]["visible"]
            ],
            hidden_tests=[
                TestCase(e["input"], e["output"], e.get("description", ""))
                for e in p["examples"]["hidden"]
            ],
            constraints=p.get("constraints", []),
            geno_solution=p["canonical_solutions"].get("geno", ""),
            python_solution=p["canonical_solutions"].get("python", ""),
            tags=p["metadata"].get("tags", []),
            source=p["metadata"].get("source", ""),
            time_complexity=p["metadata"].get("time_complexity", ""),
            space_complexity=p["metadata"].get("space_complexity", ""),
            constructs_tested=p["metadata"].get("constructs_tested", []),
        )

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "Problem":
        """Create Problem from YAML string."""
        data = yaml.safe_load(yaml_str)
        return cls.from_dict(data)

    def generate_geno_prompt(self) -> str:
        """Generate prompt for Geno solution."""
        examples = "\n".join(
            f"    example {format_geno_example_input(e.input, self.inputs)} -> "
            f"{format_geno_literal(e.output, self.output.type)}"
            for e in self.visible_examples
        )

        params = ", ".join(f"{i.name}: {i.type}" for i in self.inputs)

        return f"""You are an expert programmer in Geno.

Solve the following problem:

{self.description}

Constraints:
{chr(10).join("- " + c for c in self.constraints)}

Write a function with this signature:
func {self.function_name}({params}) -> {self.output.type}

Examples:
{examples}

Write your complete solution in Geno:"""

    def generate_python_prompt(self) -> str:
        """Generate prompt for Python solution."""
        examples = "\n".join(
            f"    # {format_python_example_call(self, e)}"
            for e in self.visible_examples
        )

        params = ", ".join(
            f"{i.name}: {format_python_type(i.type)}" for i in self.inputs
        )
        ret_type = format_python_type(self.output.type)
        return_convention = python_return_convention(self.output.type)
        convention_block = f"\n{return_convention}\n" if return_convention else ""

        return f"""You are an expert Python programmer.

Solve the following problem:

{self.description}

Constraints:
{chr(10).join("- " + c for c in self.constraints)}

Write a function with this signature:
def {self.function_name}({params}) -> {ret_type}:
{convention_block}
Examples:
{examples}

Write your complete solution in Python:"""


def save_problems(problems: list[Problem], directory: Path) -> None:
    """Save problems to YAML files."""
    directory.mkdir(parents=True, exist_ok=True)

    for problem in problems:
        filepath = directory / f"{problem.id}.yaml"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(problem.to_yaml())


def load_problems(directory: Path) -> list[Problem]:
    """Load problems from YAML files."""
    problems = []

    for filepath in directory.glob("*.yaml"):
        with open(filepath, encoding="utf-8") as f:
            problem = Problem.from_yaml(f.read())
            problems.append(problem)

    return sorted(problems, key=lambda p: p.id)


def validate_problem(problem: Problem) -> list[str]:
    """Validate a problem specification. Returns list of issues."""
    issues = []

    # Required fields
    if not problem.id:
        issues.append("Missing problem ID")
    if not problem.name:
        issues.append("Missing problem name")
    if not problem.description:
        issues.append("Missing description")
    if not problem.function_name:
        issues.append("Missing function name")

    # Examples
    if len(problem.visible_examples) < 1:
        issues.append("Need at least 1 visible example")
    if len(problem.hidden_tests) < 3:
        issues.append("Need at least 3 hidden tests")

    # Solutions
    if not problem.geno_solution:
        issues.append("Missing Geno solution")
    if not problem.python_solution:
        issues.append("Missing Python solution")

    # Edge cases check: verify at least one test uses an empty collection as input
    def _has_empty_collection(val) -> bool:
        """Check if val is or contains an empty list/string."""
        if isinstance(val, list) and len(val) == 0:
            return True
        if isinstance(val, str) and len(val) == 0:
            return True
        if isinstance(val, list):
            return any(_has_empty_collection(item) for item in val)
        if isinstance(val, tuple):
            return any(_has_empty_collection(item) for item in val)
        return False

    has_empty = any(
        _has_empty_collection(e.input)
        for e in problem.visible_examples + problem.hidden_tests
    )
    non_empty_markers = [
        "non-empty",
        "at least one element",
        "at least one character",
    ]
    constraint_text = " ".join(problem.constraints).lower()
    description_text = problem.description.lower()
    disallows_empty = (
        any(
            marker in description_text or marker in constraint_text
            for marker in non_empty_markers
        )
        or "< length(" in constraint_text
    )

    if (
        not has_empty
        and not disallows_empty
        and problem.domain in [Domain.ARRAYS, Domain.STRINGS]
    ):
        issues.append("Consider adding empty collection test case")

    return issues
