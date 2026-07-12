"""
Tests for async/await in the Geno language
===========================================
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import geno
from geno.compiler import compile_and_exec, compile_to_python
from geno.js_compiler import compile_to_js


def run(source: str):
    result = geno.run(source, config=geno.RunConfig(timeout=10.0))
    if not result.ok:
        msgs = "; ".join(d.message for d in result.diagnostics)
        raise AssertionError(f"Program failed: {msgs}")
    return result.value_raw


def check(source: str):
    return geno.check(source)


class TestAsync:
    """Tests for async function declaration, await, and compilation."""

    # -----------------------------------------------------------------
    # Interpreter: basic async/await
    # -----------------------------------------------------------------

    def test_basic_async_await_int(self):
        """Async function returning Int, awaited from main."""
        source = """
async func compute(x: Int) -> Int
  return x * 2
end func

func main() -> Int
  let result: Int = await compute(5)
  return result
end func
"""
        assert run(source) == 10

    def test_basic_async_await_string(self):
        """Async function returning String, awaited from main."""
        source = """
async func greet(name: String) -> String
  return "hello " + name
end func

func main() -> String
  let msg: String = await greet("world")
  return msg
end func
"""
        assert run(source) == "hello world"

    def test_async_await_bool(self):
        """Async function returning Bool."""
        source = """
async func is_positive(n: Int) -> Bool
  return n > 0
end func

func main() -> Bool
  let result: Bool = await is_positive(42)
  return result
end func
"""
        assert run(source) is True

    def test_async_await_list(self):
        """Async function returning a List."""
        source = """
async func make_list(n: Int) -> List[Int]
  return [n, n + 1, n + 2]
end func

func main() -> List[Int]
  let xs: List[Int] = await make_list(10)
  return xs
end func
"""
        assert run(source) == [10, 11, 12]

    def test_multiple_awaits(self):
        """Awaiting multiple async calls in sequence."""
        source = """
async func double(x: Int) -> Int
  return x * 2
end func

func main() -> Int
  let a: Int = await double(3)
  let b: Int = await double(a)
  return b
end func
"""
        assert run(source) == 12

    def test_await_in_expression(self):
        """Awaited results used in an arithmetic expression."""
        source = """
async func square(x: Int) -> Int
  return x * x
end func

func main() -> Int
  let a: Int = await square(3)
  let b: Int = await square(4)
  return a + b
end func
"""
        assert run(source) == 25

    def test_async_with_two_params(self):
        """Async function with two parameters (positional)."""
        source = """
async func add(a: Int, b: Int) -> Int
  return a + b
end func

func main() -> Int
  let result: Int = await add(10, 20)
  return result
end func
"""
        assert run(source) == 30

    def test_async_with_named_args(self):
        """Async function with >= 3 params requires named args."""
        source = """
async func combine(a: Int, b: Int, c: Int) -> Int
  return a + b + c
end func

func main() -> Int
  let result: Int = await combine(a: 1, b: 2, c: 3)
  return result
end func
"""
        assert run(source) == 6

    # -----------------------------------------------------------------
    # Type-checking: async functions exempt from examples
    # -----------------------------------------------------------------

    def test_async_exempt_from_examples(self):
        """Async functions should not require example clauses."""
        source = """
async func fetch(x: Int) -> Int
  return x + 1
end func

func main() -> Int
  let val: Int = await fetch(5)
  return val
end func
"""
        result = check(source)
        assert result.ok, (
            "Async function without examples should pass typechecking: "
            + "; ".join(d.message for d in result.diagnostics)
        )

    # -----------------------------------------------------------------
    # Type-checking: negative cases
    # -----------------------------------------------------------------

    def test_await_outside_async_fails(self):
        """Using await inside a non-async function should fail type checking."""
        source = """
async func fetch(x: Int) -> Int
  return x + 1
end func

func helper(x: Int) -> Int
  example 1 -> 2
  let val: Int = await fetch(x)
  return val
end func

func main() -> Int
  return helper(5)
end func
"""
        result = check(source)
        assert not result.ok
        messages = " ".join(d.message for d in result.diagnostics)
        assert "await" in messages.lower()

    # -----------------------------------------------------------------
    # Python compiler output
    # -----------------------------------------------------------------

    def test_python_compiler_async_def(self):
        """Python compiler should emit 'async def' for async functions."""
        source = """
async func compute(x: Int) -> Int
  return x * 2
end func

func main() -> Int
  let result: Int = await compute(5)
  return result
end func
"""
        py_code = compile_to_python(source)
        assert "async def compute" in py_code

    def test_python_compiler_await_expr(self):
        """Python compiler should emit 'await' expressions."""
        source = """
async func compute(x: Int) -> Int
  return x * 2
end func

func main() -> Int
  let result: Int = await compute(5)
  return result
end func
"""
        py_code = compile_to_python(source)
        assert "(await compute(" in py_code

    def test_python_compiler_async_main_asyncio_run(self):
        """When main is async, Python compiler should use asyncio.run."""
        source = """
async func main() -> Int
  return 42
end func
"""
        py_code = compile_to_python(source)
        assert "asyncio.run" in py_code

    def test_python_compiler_sync_main_no_asyncio(self):
        """When main is not async, Python compiler should not use asyncio.run."""
        source = """
func main() -> Int
  return 42
end func
"""
        py_code = compile_to_python(source)
        assert "asyncio.run" not in py_code

    # -----------------------------------------------------------------
    # JS compiler output
    # -----------------------------------------------------------------

    def test_js_compiler_async_function(self):
        """JS compiler should emit 'async function' for async functions."""
        source = """
async func compute(x: Int) -> Int
  return x * 2
end func

func main() -> Int
  let result: Int = await compute(5)
  return result
end func
"""
        js_code = compile_to_js(source)
        assert "async function compute" in js_code

    def test_js_compiler_await_expr(self):
        """JS compiler should emit 'await' expressions."""
        source = """
async func compute(x: Int) -> Int
  return x * 2
end func

func main() -> Int
  let result: Int = await compute(5)
  return result
end func
"""
        js_code = compile_to_js(source)
        assert "await compute(" in js_code

    def test_js_compiler_async_main_iife(self):
        """When main is async, JS compiler should wrap in async IIFE."""
        source = """
async func main() -> Int
  return 42
end func
"""
        js_code = compile_to_js(source)
        assert "async () =>" in js_code
        assert "await main()" in js_code
