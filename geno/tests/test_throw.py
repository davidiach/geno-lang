"""
Tests for Geno throw/catch (structured error types)
=====================================================
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import geno
from geno.compiler import compile_and_exec, compile_to_python
from geno.js_compiler import compile_to_js


def run(source: str):
    """Run a Geno program via the API and return the raw result."""
    result = geno.run(source, config=geno.RunConfig(timeout=10.0))
    if not result.ok:
        msgs = "; ".join(d.message for d in result.diagnostics)
        raise AssertionError(f"Program failed: {msgs}")
    return result.value_raw


def run_output(source: str) -> str:
    """Run a Geno program and return captured output."""
    result = geno.run(source, config=geno.RunConfig(timeout=10.0))
    if not result.ok:
        msgs = "; ".join(d.message for d in result.diagnostics)
        raise AssertionError(f"Program failed: {msgs}")
    return result.output


def check(source: str):
    """Type-check a Geno program, return CheckResult."""
    return geno.check(source)


class TestThrow:
    def test_throw_string_catch_as_string(self):
        """throw a string literal and catch it as String."""
        source = """
func main() -> String
  try
    throw "oops"
  catch e: String
    return e
  end try
  return "unreachable"
end func
"""
        result = run(source)
        assert "oops" in result

    def test_throw_user_defined_error_type(self):
        """throw a user-defined error type and catch it."""
        source = """
type AppError = AppError(message: String)

func main() -> String
  try
    throw AppError("bad input")
  catch e: AppError
    return e.message
  end try
  return "unreachable"
end func
"""
        assert run(source) == "bad input"

    def test_throw_string_from_helper_function(self):
        """throw a string from a helper function, catch it in main."""
        source = """
func failing(x: Int) -> String
  example 0 -> "ok"
  if x > 0 then
    throw "helper failed"
  end if
  return "ok"
end func

func main() -> String
  try
    let result: String = failing(1)
    return result
  catch e: String
    return e
  end try
  return "unreachable"
end func
"""
        result = run(source)
        assert "helper failed" in result

    def test_throw_non_string_non_user_type_rejected(self):
        """throw with a non-String, non-user-defined type should fail type check."""
        source = """
func main() -> Int
  try
    throw 42
  catch e: String
    return 0
  end try
  return 1
end func
"""
        result = check(source)
        assert not result.ok

    def test_catch_wrong_type_does_not_match(self):
        """Catch clause for TypeA must not catch a thrown TypeB."""
        source = """
type ErrA = ErrA(msg: String)
type ErrB = ErrB(msg: String)

func main() -> String
  try
    try
      throw ErrB("inner")
    catch e: ErrA
      return "wrong: caught as ErrA"
    end try
  catch e: String
    return "propagated"
  end try
  return "unreachable"
end func
"""
        assert run(source) == "propagated"

    def test_python_compiler_uses_geno_throw(self):
        """Compiled Python output should reference _GenoThrow or _geno_throw."""
        source = """
func main() -> String
  try
    throw "error"
  catch e: String
    return e
  end try
  return "unreachable"
end func
"""
        python_code = compile_to_python(source)
        assert "_GenoThrow" in python_code or "_geno_throw" in python_code

    def test_js_compiler_uses_geno_throw(self):
        """Compiled JS output should reference _GenoThrow."""
        source = """
func main() -> String
  try
    throw "error"
  catch e: String
    return e
  end try
  return "unreachable"
end func
"""
        js_code = compile_to_js(source)
        assert "_GenoThrow" in js_code
