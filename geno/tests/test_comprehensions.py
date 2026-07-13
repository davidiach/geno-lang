"""
Tests for Geno list comprehensions.

Covers:
  - Basic comprehensions (map-style)
  - Filtered comprehensions (with `if` clause)
  - Empty result from filtering
  - String element expressions
  - Nested / complex element expressions
  - Type error for non-list iterable
  - Python compiler output (list comprehension syntax)
  - JS compiler output (.filter/.map chains)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import geno
from geno.compiler import compile_and_exec
from geno.js_compiler import compile_to_js


def run(source: str):
    result = geno.run(source, config=geno.RunConfig(timeout=10.0))
    if not result.ok:
        msgs = "; ".join(d.message for d in result.diagnostics)
        raise AssertionError(f"Program failed: {msgs}")
    return result.value_raw


def check(source: str):
    return geno.check(source)


class TestListComprehensions:
    """Tests for list comprehension syntax: [expr for var: Type in iterable]."""

    # ------------------------------------------------------------------
    # 1. Basic comprehension
    # ------------------------------------------------------------------

    def test_basic_comprehension(self):
        """[x * 2 for x: Int in [1, 2, 3]] produces [2, 4, 6]."""
        source = """
func main() -> List[Int]
  let nums: List[Int] = [1, 2, 3]
  return [x * 2 for x: Int in nums]
end func
"""
        assert run(source) == [2, 4, 6]

    # ------------------------------------------------------------------
    # 2. Comprehension with condition
    # ------------------------------------------------------------------

    def test_comprehension_with_condition(self):
        """[x for x: Int in list if x > 3] filters correctly."""
        source = """
func main() -> List[Int]
  let nums: List[Int] = [1, 2, 3, 4, 5]
  return [x for x: Int in nums if x > 3]
end func
"""
        assert run(source) == [4, 5]

    # ------------------------------------------------------------------
    # 3. Empty result from filtering
    # ------------------------------------------------------------------

    def test_empty_result_from_filter(self):
        """Filtering with no matches produces an empty list."""
        source = """
func main() -> List[Int]
  let nums: List[Int] = [1, 2, 3]
  return [x for x: Int in nums if x > 10]
end func
"""
        assert run(source) == []

    # ------------------------------------------------------------------
    # 4. String comprehension
    # ------------------------------------------------------------------

    def test_string_comprehension(self):
        """Comprehension over a list of strings."""
        source = """
func greet(name: String) -> String
  example "world" -> "hello world"
  return "hello " + name
end func

func main() -> List[String]
  let names: List[String] = ["alice", "bob"]
  return [greet(x) for x: String in names]
end func
"""
        result = run(source)
        assert result == ["hello alice", "hello bob"]

    # ------------------------------------------------------------------
    # 5. Nested expression in element_expr
    # ------------------------------------------------------------------

    def test_nested_expression(self):
        """Element expression can be a complex expression like x * x."""
        source = """
func main() -> List[Int]
  let nums: List[Int] = [1, 2, 3, 4]
  return [x * x for x: Int in nums]
end func
"""
        assert run(source) == [1, 4, 9, 16]

    # ------------------------------------------------------------------
    # 6. Type error: non-list iterable
    # ------------------------------------------------------------------

    def test_type_error_non_list_iterable(self):
        """Iterating over a non-list type should produce a type error."""
        source = """
func main() -> List[Int]
  let n: Int = 5
  return [x for x: Int in n]
end func
"""
        result = check(source)
        assert not result.ok
        messages = " ".join(d.message for d in result.diagnostics)
        assert "List" in messages

    # ------------------------------------------------------------------
    # 7. Python compiler output contains list comprehension syntax
    # ------------------------------------------------------------------

    def test_python_compiler_output(self):
        """Compiled Python uses list comprehension syntax."""
        source = """
func main() -> List[Int]
  let nums: List[Int] = [1, 2, 3]
  return [x * 2 for x: Int in nums]
end func
"""
        globals_dict = compile_and_exec(source, timeout=None)
        result = globals_dict["main"]()
        assert result == [2, 4, 6]

    # ------------------------------------------------------------------
    # 8. JS compiler output contains .map( or .filter(
    # ------------------------------------------------------------------

    def test_js_compiler_output_map(self):
        """JS output for a basic comprehension uses .map(."""
        source = """
func main() -> List[Int]
  let nums: List[Int] = [1, 2, 3]
  return [x * 2 for x: Int in nums]
end func
"""
        js = compile_to_js(source)
        assert ".map(" in js

    def test_js_compiler_output_filter(self):
        """JS output for a filtered comprehension uses .filter(."""
        source = """
func main() -> List[Int]
  let nums: List[Int] = [1, 2, 3, 4, 5]
  return [x for x: Int in nums if x > 3]
end func
"""
        js = compile_to_js(source)
        assert ".filter(" in js
        assert ".map(" in js
