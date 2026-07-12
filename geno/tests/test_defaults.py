"""
Tests for default parameter values in the Geno language
========================================================
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import geno
from geno.compiler import compile_and_exec, compile_to_python
from geno.js_compiler import compile_to_js
from geno.tests._script_runner import run_node_code


def run(source: str):
    """Run a Geno program via the API and return the raw result."""
    result = geno.run(source, config=geno.RunConfig(timeout=10.0))
    if not result.ok:
        msgs = "; ".join(d.message for d in result.diagnostics)
        raise AssertionError(f"Program failed: {msgs}")
    return result.value_raw


def check(source: str):
    """Type-check a Geno program, return CheckResult."""
    return geno.check(source)


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
    result = run_node_code(js_out, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"JS execution failed: {result.stderr}")
    return result.stdout.strip()


class TestDefaults:
    def test_basic_default_param(self):
        """A function with 1 required + 1 optional param uses the default."""
        source = """
func add(x: Int, y: Int = 10) -> Int
  example 5 -> 15
  return x + y
end func

func main() -> Int
  return add(5)
end func
"""
        assert run(source) == 15

    def test_single_optional_param_accepts_zero_arg_example(self):
        """A single defaulted parameter should accept example ()."""
        source = """
func default_one(x: Int = 1) -> Int
  example () -> 1
  return x
end func

func main() -> Int
  return default_one()
end func
"""
        assert run(source) == 1

    def test_multiple_defaults(self):
        """A function with multiple default params uses all defaults."""
        source = """
func make_point(x: Int, y: Int = 0, z: Int = 0) -> Int
  example 1 -> 1
  return x + y + z
end func

func main() -> Int
  return make_point(x: 1)
end func
"""
        assert run(source) == 1

    def test_override_all_defaults(self):
        """Calling with all args provided overrides every default."""
        source = """
func add(x: Int, y: Int = 10) -> Int
  example 5 -> 15
  return x + y
end func

func main() -> Int
  return add(5, 20)
end func
"""
        assert run(source) == 25

    def test_override_all_defaults_named(self):
        """Override all defaults using named args (3+ params)."""
        source = """
func make_point(x: Int, y: Int = 0, z: Int = 0) -> Int
  example 1 -> 1
  return x + y + z
end func

func main() -> Int
  return make_point(x: 1, y: 2, z: 3)
end func
"""
        assert run(source) == 6

    def test_partial_defaults_used(self):
        """Override some defaults while others keep their default value."""
        source = """
func make_point(x: Int, y: Int = 0, z: Int = 0) -> Int
  example 1 -> 1
  return x + y + z
end func

func main() -> Int
  return make_point(x: 1, y: 5)
end func
"""
        assert run(source) == 6

    def test_out_of_order_named_args_with_defaults(self):
        """Named args skipping a defaulted middle param must bind correctly."""
        source = """
func f(a: Int, b: Int = 10, c: Int = 20) -> Int
  example 1 -> 31
  return a + b + c
end func

func main() -> Int
  return f(c: 5, a: 1)
end func
"""
        # a=1, b=10 (default), c=5 → 16
        assert run(source) == 16

    def test_compiled_backends_preserve_skipped_middle_default(self):
        """Compiled calls must pass a sentinel for omitted middle defaults."""
        source = """
func f(a: Int, b: Int = 10, c: Int = 20) -> Int
  example 1 -> 31
  return a + b + c
end func

func main() -> Int
  return f(c: 5, a: 1)
end func
"""
        assert _compile_py_and_run(source) == 16
        assert _compile_js_and_run(source) == "16"

    def test_default_type_mismatch(self):
        """Default value type must match the parameter type."""
        source = """
func bad(x: Int, y: Int = "hello") -> Int
  example 5 -> 5
  return x
end func

func main() -> Int
  return bad(5)
end func
"""
        result = check(source)
        assert not result.ok

    def test_required_before_optional(self):
        """Required params must come before optional params."""
        source = """
func bad(x: Int = 10, y: Int) -> Int
  example 5 -> 15
  return x + y
end func

func main() -> Int
  return bad(5)
end func
"""
        result = check(source)
        assert not result.ok

    def test_compiled_python_contains_defaults(self):
        """Compiled Python should initialize defaults inside the function body."""
        source = """
func add(x: Int, y: Int = 10) -> Int
  example 5 -> 15
  return x + y
end func

func main() -> Int
  return add(5)
end func
"""
        py_code = compile_to_python(source)
        assert "y: 'int' = _GENO_MISSING" in py_code
        assert "if y is _GENO_MISSING:" in py_code
        assert "y = 10" in py_code

    def test_compiled_python_runs_with_default(self):
        """Compiled Python executes correctly using the default value."""
        source = """
func add(x: Int, y: Int = 10) -> Int
  example 5 -> 15
  return x + y
end func

func main() -> Int
  return add(5)
end func
"""
        assert _compile_py_and_run(source) == 15

    def test_compiled_python_runs_override(self):
        """Compiled Python executes correctly when overriding the default."""
        source = """
func add(x: Int, y: Int = 10) -> Int
  example 5 -> 15
  return x + y
end func

func main() -> Int
  return add(5, 20)
end func
"""
        assert _compile_py_and_run(source) == 25

    def test_compiled_python_re_evaluates_mutable_default_each_call(self):
        """Compiled Python should not share mutable defaults across calls."""
        source = """
func next(counter: Array[Int] = array_new(size: 1, default: 0)) -> Int
  example () -> 1
  let current: Int = array_get(counter, 0)
  array_set(array: counter, index: 0, value: current + 1)
  return array_get(counter, 0)
end func

func main() -> Int
  return next() * 10 + next()
end func
"""
        assert _compile_py_and_run(source) == 11

    def test_compiled_js_contains_defaults(self):
        """Compiled JS output should contain default value syntax."""
        source = """
func add(x: Int, y: Int = 10) -> Int
  example 5 -> 15
  return x + y
end func

func main() -> Int
  return add(5)
end func
"""
        js_code = compile_to_js(source)
        assert "= 10" in js_code

    def test_compiled_js_runs_with_default(self):
        """Compiled JS executes correctly using the default value."""
        source = """
func add(x: Int, y: Int = 10) -> Int
  example 5 -> 15
  return x + y
end func

func main() -> Int
  return add(5)
end func
"""
        assert _compile_js_and_run(source) == "15"

    def test_compiled_js_runs_override(self):
        """Compiled JS executes correctly when overriding the default."""
        source = """
func add(x: Int, y: Int = 10) -> Int
  example 5 -> 15
  return x + y
end func

func main() -> Int
  return add(5, 20)
end func
"""
        assert _compile_js_and_run(source) == "25"
