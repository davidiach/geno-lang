"""
Tests for async/await in the Geno language
===========================================
"""

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

import geno
from geno.ast_nodes import FunctionDef
from geno.compiler import compile_to_python
from geno.entrypoint import is_async_execution_form
from geno.js_compiler import compile_to_js
from geno.parser import parse
from geno.tests._script_runner import run_node_code, run_python_code

HAS_NODE = shutil.which("node") is not None


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


# ---------------------------------------------------------------------------
# `await` inside a synchronous `main`
#
# The typechecker accepts `await` directly inside any `main`, with or without
# an `async` modifier (TypeChecker._check_await_expr).  Such an entrypoint is
# an asynchronous execution form: a host that lowers it synchronously emits
# `await` outside an async function, which neither backend accepts.
# ---------------------------------------------------------------------------

SYNC_MAIN_WITH_AWAIT = """
async func twice(x: Int) -> Int
  return x * 2
end func

func main() -> Unit
  let doubled: Int = await twice(21)
  print(doubled)
end func
"""

SYNC_MAIN_RETURNING_ASYNC = """
async func twice(x: Int) -> Int
  return x * 2
end func

func main() -> Async[Int]
  return twice(21)
end func
"""

# A `requires` or `ensures` condition is emitted inside the function by both
# backends, and the typechecker accepts `await` there because specs are checked
# while `main` is still the enclosing function.  An example clause is
# verification data, not emitted code, so it does not change the lowering.
ENSURES_AWAIT_MAIN = """
async func floor_value() -> Int
  return 1
end func

func main() -> Int
  ensures result > await floor_value()
  print(2)
  return 2
end func
"""

REQUIRES_AWAIT_MAIN = """
async func floor_value() -> Int
  return 1
end func

func main() -> Int
  requires (await floor_value()) > 0
  print(2)
  return 2
end func
"""

EXAMPLE_AWAIT_MAIN = """
async func floor_value() -> Int
  return 1
end func

func main() -> Int
  example () -> await floor_value()
  print(2)
  return 2
end func
"""


def _entry_function(source: str, name: str = "main") -> FunctionDef:
    """Parse *source* and return its own definition of *name*."""
    return next(
        defn
        for defn in parse(source).definitions
        if isinstance(defn, FunctionDef) and defn.name == name
    )


class TestSyncMainWithAwait:
    """A synchronous `main` that awaits is lowered and awaited exactly once."""

    def test_python_compiler_emits_async_main_and_asyncio_run(self):
        py_code = compile_to_python(SYNC_MAIN_WITH_AWAIT)
        assert "async def main" in py_code
        assert "asyncio.run(main())" in py_code

    def test_js_compiler_emits_async_main_and_awaited_entry(self):
        js_code = compile_to_js(SYNC_MAIN_WITH_AWAIT)
        assert "async function main" in js_code
        assert "await main()" in js_code

    def test_compiled_python_runs(self):
        completed = run_python_code(
            compile_to_python(SYNC_MAIN_WITH_AWAIT),
            python_executable=sys.executable,
            args=["--cap", "print"],
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == "42\n"

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_compiled_node_runs(self):
        completed = run_node_code(
            compile_to_js(SYNC_MAIN_WITH_AWAIT), args=["--cap", "print"]
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == "42\n"

    def test_ensures_on_an_awaiting_main_runs_on_both_backends(self):
        """The ensures body helper must be async too, and its call awaited."""
        source = """
async func twice(x: Int) -> Int
  return x * 2
end func

func main() -> Int
  ensures result > 0
  let doubled: Int = await twice(21)
  print(doubled)
  return doubled
end func
"""
        completed = run_python_code(
            compile_to_python(source),
            python_executable=sys.executable,
            args=["--cap", "print"],
        )
        # `main` prints and then returns 42, which is its exit status from 0.5
        # on (spec 4.1.1); the print still lands on stdout.
        assert completed.returncode == 42, completed.stderr
        assert "42" in completed.stdout

        if HAS_NODE:
            completed = run_node_code(compile_to_js(source), args=["--cap", "print"])
            assert completed.returncode == 42, completed.stderr
            assert "42" in completed.stdout

    @pytest.mark.parametrize(
        ("source", "clause"),
        [
            pytest.param(ENSURES_AWAIT_MAIN, "ensures", id="ensures"),
            pytest.param(REQUIRES_AWAIT_MAIN, "requires", id="requires"),
        ],
    )
    def test_await_in_a_contract_clause_runs_on_both_backends(
        self, source: str, clause: str
    ):
        """A contract condition is emitted inside the function, so it counts."""
        py_code = compile_to_python(source)
        assert "async def main" in py_code, f"{clause} did not force an async main"

        completed = run_python_code(
            py_code, python_executable=sys.executable, args=["--cap", "print"]
        )
        assert completed.returncode == 2, completed.stderr
        assert "2" in completed.stdout

        if HAS_NODE:
            completed = run_node_code(compile_to_js(source), args=["--cap", "print"])
            assert completed.returncode == 2, completed.stderr
            assert "2" in completed.stdout

    def test_await_only_in_an_example_clause_keeps_main_synchronous(self):
        """Examples are verification data, never emitted into the function."""
        py_code = compile_to_python(EXAMPLE_AWAIT_MAIN)
        assert "async def main" not in py_code

        completed = run_python_code(
            py_code, python_executable=sys.executable, args=["--cap", "print"]
        )
        assert completed.returncode == 2, completed.stderr

    def test_sync_main_returning_an_async_value_is_not_awaited(self):
        """Returning an async value without awaiting keeps `main` synchronous."""
        py_code = compile_to_python(SYNC_MAIN_RETURNING_ASYNC)
        assert "async def main" not in py_code
        assert "asyncio.run" not in py_code

        js_code = compile_to_js(SYNC_MAIN_RETURNING_ASYNC)
        assert "async function main" not in js_code
        assert "await main()" not in js_code


class TestIsAsyncExecutionForm:
    """Unit coverage for the shared async-execution-form predicate."""

    def test_async_modifier_is_an_async_form(self):
        source = """
async func work() -> Int
  return 1
end func
"""
        assert is_async_execution_form(_entry_function(source, "work"))

    def test_plain_function_is_not_an_async_form(self):
        source = """
func work() -> Int
  example () -> 1
  return 1
end func
"""
        assert not is_async_execution_form(_entry_function(source, "work"))

    def test_main_awaiting_in_its_own_body_is_an_async_form(self):
        assert is_async_execution_form(_entry_function(SYNC_MAIN_WITH_AWAIT))

    def test_main_returning_an_async_value_is_not_an_async_form(self):
        assert not is_async_execution_form(_entry_function(SYNC_MAIN_RETURNING_ASYNC))

    def test_non_main_awaiting_is_not_an_async_form(self):
        """`await` outside async/main is a type error, so parse without checking."""
        source = """
async func twice(x: Int) -> Int
  return x * 2
end func

func helper() -> Int
  return await twice(21)
end func
"""
        assert not is_async_execution_form(_entry_function(source, "helper"))

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param(ENSURES_AWAIT_MAIN, id="ensures"),
            pytest.param(REQUIRES_AWAIT_MAIN, id="requires"),
        ],
    )
    def test_await_in_a_contract_clause_is_an_async_form(self, source: str):
        assert is_async_execution_form(_entry_function(source))

    def test_await_only_in_an_example_clause_is_not_an_async_form(self):
        assert not is_async_execution_form(_entry_function(EXAMPLE_AWAIT_MAIN))

    def test_await_inside_a_nested_lambda_is_not_the_enclosing_form(self):
        """A lambda owns its own async scope, so its `await` does not escape."""
        source = """
async func twice(x: Int) -> Int
  return x * 2
end func

func main() -> Unit
  let f = fn() -> await twice(21)
  print(f())
end func
"""
        assert not is_async_execution_form(_entry_function(source))
