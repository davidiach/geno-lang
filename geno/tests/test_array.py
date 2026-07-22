"""
Tests for the Geno Array[T] type
=================================
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import geno
from geno.compiler import compile_and_exec
from geno.js_compiler import compile_to_js
from geno.tests._script_runner import run_node_code


def run(source: str):
    """Run a Geno program via the API and return the raw result."""
    result = geno.run(source, config=geno.RunConfig(timeout=10.0))
    if not result.ok:
        msgs = "; ".join(d.message for d in result.diagnostics)
        raise AssertionError(f"Program failed: {msgs}")
    return result.value_raw


def run_output(source: str) -> str:
    """Run a Geno program and return captured output."""
    result = geno.run(
        source,
        config=geno.RunConfig(timeout=10.0, capabilities={"print"}),
    )
    if not result.ok:
        msgs = "; ".join(d.message for d in result.diagnostics)
        raise AssertionError(f"Program failed: {msgs}")
    return result.output


def check(source: str):
    """Type-check a Geno program, return CheckResult."""
    return geno.check(source)


class TestArrayBasics:
    def test_array_new_and_get(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(3, 0)
  return array_get(arr, 1)
end func
"""
        assert run(source) == 0

    def test_array_set(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(3, 0)
  array_set(array: arr, index: 1, value: 42)
  return array_get(arr, 1)
end func
"""
        assert run(source) == 42

    def test_array_index_syntax(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(3, 10)
  return arr[2]
end func
"""
        assert run(source) == 10

    def test_array_from_list(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_from_list([10, 20, 30])
  return arr[1]
end func
"""
        assert run(source) == 20

    def test_array_to_list(self):
        source = """
func main() -> List[Int]
  let arr: Array[Int] = array_from_list([1, 2, 3])
  return array_to_list(arr)
end func
"""
        assert run(source) == [1, 2, 3]

    def test_array_length(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(5, 0)
  return array_length(arr)
end func
"""
        assert run(source) == 5

    def test_length_builtin_on_array(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(4, 0)
  return length(arr)
end func
"""
        assert run(source) == 4


class TestArrayMutation:
    def test_alias_visibility(self):
        """Mutation through one alias is visible through another."""
        source = """
func main() -> Int
  let a: Array[Int] = array_new(3, 0)
  let b: Array[Int] = a
  array_set(array: a, index: 0, value: 99)
  return b[0]
end func
"""
        assert run(source) == 99

    def test_function_pass_through(self):
        """Array passed to function retains mutations."""
        source = """
func set_first(arr: Array[Int], val: Int) -> Unit
  example array_new(1, 0), 5 -> ()
  array_set(array: arr, index: 0, value: val)
end func

func main() -> Int
  let arr: Array[Int] = array_new(3, 0)
  set_first(arr, 77)
  return arr[0]
end func
"""
        assert run(source) == 77

    def test_loop_mutation(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(5, 0)
  var i: Int = 0
  while i < 5 do
    array_set(array: arr, index: i, value: i * i)
    i = i + 1
  end while
  return arr[3]
end func
"""
        assert run(source) == 9


class TestArrayBounds:
    def test_negative_index_get(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(3, 0)
  return array_get(arr, -1)
end func
"""
        result = geno.run(source, config=geno.RunConfig(timeout=5.0))
        assert not result.ok

    def test_out_of_bounds_get(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(3, 0)
  return array_get(arr, 3)
end func
"""
        result = geno.run(source, config=geno.RunConfig(timeout=5.0))
        assert not result.ok

    def test_out_of_bounds_set(self):
        source = """
func main() -> Unit
  let arr: Array[Int] = array_new(3, 0)
  array_set(array: arr, index: 5, value: 1)
end func
"""
        result = geno.run(source, config=geno.RunConfig(timeout=5.0))
        assert not result.ok

    def test_negative_size(self):
        source = """
func main() -> Unit
  let arr: Array[Int] = array_new(-1, 0)
end func
"""
        result = geno.run(source, config=geno.RunConfig(timeout=5.0))
        assert not result.ok

    def test_index_syntax_out_of_bounds(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(2, 0)
  return arr[2]
end func
"""
        result = geno.run(source, config=geno.RunConfig(timeout=5.0))
        assert not result.ok


class TestArrayTypeChecking:
    def test_type_annotation_accepted(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(3, 0)
  return arr[0]
end func
"""
        result = check(source)
        assert result.ok

    def test_string_array(self):
        source = """
func main() -> String
  let arr: Array[String] = array_new(2, "hello")
  array_set(array: arr, index: 0, value: "world")
  return arr[0]
end func
"""
        assert run(source) == "world"

    def test_length_on_array_typechecks(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(3, 0)
  return length(arr)
end func
"""
        result = check(source)
        assert result.ok


class TestArrayUseCases:
    def test_2d_board(self):
        """Simulate a flat 2D board using a 1D array."""
        source = """
func main() -> Int
  let size: Int = 3
  let board: Array[Int] = array_new(size * size, 0)
  array_set(array: board, index: 1 * size + 2, value: 42)
  return board[1 * size + 2]
end func
"""
        assert run(source) == 42

    def test_accumulator_pattern(self):
        source = """
func main() -> Int
  let counts: Array[Int] = array_new(10, 0)
  let data: List[Int] = [1, 3, 1, 5, 3, 1]
  for x: Int in data do
    let prev: Int = array_get(counts, x)
    array_set(array: counts, index: x, value: prev + 1)
  end for
  return array_get(counts, 1)
end func
"""
        assert run(source) == 3

    def test_print_array(self):
        source = """
func main() -> Unit
  let arr: Array[Int] = array_from_list([1, 2, 3])
  print(arr)
end func
"""
        output = run_output(source)
        assert "Array([1, 2, 3])" in output

    def test_for_loop_over_array(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_from_list([10, 20, 30])
  var total: Int = 0
  for x: Int in arr do
    total = total + x
  end for
  return total
end func
"""
        assert run(source) == 60


def _compile_py_and_run(source: str):
    """Compile to Python, exec, and return main() result."""
    globals_dict = compile_and_exec(source, timeout=None)
    if "main" in globals_dict:
        return globals_dict["main"]()
    return None


def _compile_js_and_run(source: str) -> str:
    """Compile to JS, run via Node, return stdout stripped."""
    js_out = compile_to_js(source)
    assert isinstance(js_out, str)
    result = run_node_code(js_out, args=("--cap", "print"), timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"JS execution failed: {result.stderr}")
    return result.stdout.strip()


class TestArrayCompiledPython:
    def test_basic_set_get(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(3, 0)
  array_set(array: arr, index: 1, value: 42)
  return array_get(arr, 1)
end func
"""
        assert _compile_py_and_run(source) == 42

    def test_alias_mutation(self):
        source = """
func main() -> Int
  let a: Array[Int] = array_new(3, 0)
  let b: Array[Int] = a
  array_set(array: a, index: 0, value: 99)
  return b[0]
end func
"""
        assert _compile_py_and_run(source) == 99

    def test_array_to_list(self):
        source = """
func main() -> List[Int]
  let arr: Array[Int] = array_from_list([10, 20, 30])
  return array_to_list(arr)
end func
"""
        assert _compile_py_and_run(source) == [10, 20, 30]

    def test_index_syntax(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_from_list([5, 6, 7])
  return arr[2]
end func
"""
        assert _compile_py_and_run(source) == 7

    def test_for_loop_over_array(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_from_list([10, 20, 30])
  var total: Int = 0
  for x: Int in arr do
    total = total + x
  end for
  return total
end func
"""
        assert _compile_py_and_run(source) == 60


class TestArrayCompiledJS:
    def test_basic_set_get(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_new(3, 0)
  array_set(array: arr, index: 1, value: 42)
  return array_get(arr, 1)
end func
"""
        assert _compile_js_and_run(source) == "42"

    def test_alias_mutation(self):
        source = """
func main() -> Int
  let a: Array[Int] = array_new(3, 0)
  let b: Array[Int] = a
  array_set(array: a, index: 0, value: 99)
  return b[0]
end func
"""
        assert _compile_js_and_run(source) == "99"

    def test_array_to_list(self):
        source = """
func main() -> List[Int]
  let arr: Array[Int] = array_from_list([10, 20, 30])
  return array_to_list(arr)
end func
"""
        assert _compile_js_and_run(source) == "[10, 20, 30]"

    def test_index_syntax(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_from_list([5, 6, 7])
  return arr[2]
end func
"""
        assert _compile_js_and_run(source) == "7"

    def test_print_array(self):
        source = """
func main() -> Unit
  let arr: Array[Int] = array_from_list([1, 2, 3])
  print(arr)
end func
"""
        assert "Array([1, 2, 3])" in _compile_js_and_run(source)

    def test_for_loop_over_array(self):
        source = """
func main() -> Int
  let arr: Array[Int] = array_from_list([10, 20, 30])
  var total: Int = 0
  for x: Int in arr do
    total = total + x
  end for
  return total
end func
"""
        assert _compile_js_and_run(source) == "60"
