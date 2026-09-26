"""
Differential backend parity tests.

Generates small valid Geno programs via Hypothesis and verifies identical
observable output across the interpreter, compiled Python, and compiled JS
backends.
"""

import shutil
import sys

import pytest

# Skip the entire module when hypothesis isn't installed (it's a dev dep).
# Catch AttributeError too, in case hypothesis is partially installed (e.g. as
# a namespace package shadow without the real implementation).
try:
    from hypothesis import example, given, settings
    from hypothesis import strategies as st

    # Force-resolve a strategy attribute used at module scope below; if a
    # shadow install is missing it, fail the import here rather than at
    # class-body evaluation.
    _ = st.integers
except (ImportError, AttributeError):
    pytest.skip("hypothesis not installed", allow_module_level=True)

from geno.compiler import compile_to_python
from geno.interpreter import Interpreter
from geno.js_compiler import compile_to_js
from geno.parser import parse
from geno.tests._script_runner import run_node_code, run_python_code

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NODE = shutil.which("node")


def _run_python(source: str) -> str:
    """Compile Geno to Python and execute, return stdout."""
    py_code = compile_to_python(source)
    result = run_python_code(
        py_code,
        python_executable=sys.executable,
        args=("--cap", "print"),
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Python backend failed: {result.stderr}")
    return result.stdout.strip()


def _run_js(source: str) -> str:
    """Compile Geno to JS and execute via Node, return stdout."""
    node = _NODE
    if node is None:
        pytest.skip("Node.js not available")
    assert node is not None
    js_code = compile_to_js(source)
    if isinstance(js_code, tuple):
        js_code = js_code[0]
    result = run_node_code(
        js_code, node_executable=node, args=("--cap", "print"), timeout=10
    )
    if result.returncode != 0:
        raise RuntimeError(f"JS backend failed: {result.stderr}")
    stdout = result.stdout
    assert isinstance(stdout, str)
    return stdout.strip()


def _run_interpreter(source: str) -> str:
    """Run via interpreter, return its printed output.

    The generated programs print their value rather than returning it, because
    from 0.5 an `Int` `main` result is the process exit status and could not
    carry the negative and out-of-range values generated here. So all three
    sides are now compared on the same channel, which is what this module's
    "identical observable output" means. The interpreter buffers `print`
    instead of writing to the host's stdout, so read that buffer.
    """
    interp = Interpreter(check_examples=False)
    interp.run(parse(source))
    return interp.get_output().strip()


# ---------------------------------------------------------------------------
# Program generators
# ---------------------------------------------------------------------------

# Small integers to avoid overflow in multiplication
small_int = st.integers(min_value=-100, max_value=100)

# Operators that should behave identically
arith_op = st.sampled_from(["+", "-", "*"])


@st.composite
def arith_expr(draw, depth=0):
    """Generate a small arithmetic expression string."""
    if depth > 2 or draw(st.booleans()):
        return str(draw(small_int))
    left = draw(arith_expr(depth=depth + 1))
    right = draw(arith_expr(depth=depth + 1))
    op = draw(arith_op)
    return f"({left} {op} {right})"


@st.composite
def arith_program(draw):
    """Generate a Geno program that prints an arithmetic result."""
    expr = draw(arith_expr())
    return (
        '@untested("generated")\n'
        "func main() -> Unit\n"
        f"    print({expr})\n"
        "    return ()\n"
        "end func\n"
    )


@st.composite
def div_program(draw):
    """Generate a Geno program with integer division."""
    a = draw(small_int)
    # Avoid division by zero
    b = draw(small_int.filter(lambda x: x != 0))
    return (
        '@untested("generated")\n'
        "func main() -> Unit\n"
        f"    print({a} / {b})\n"
        "    return ()\n"
        "end func\n"
    )


@st.composite
def mod_program(draw):
    """Generate a Geno program with modulo."""
    a = draw(small_int)
    b = draw(small_int.filter(lambda x: x != 0))
    return (
        '@untested("generated")\n'
        "func main() -> Unit\n"
        f"    print({a} % {b})\n"
        "    return ()\n"
        "end func\n"
    )


# ---------------------------------------------------------------------------
# Parity tests
# ---------------------------------------------------------------------------


class TestArithmeticParity:
    """Interpreter and compiled backends agree on pure arithmetic."""

    @given(prog=arith_program())
    @settings(max_examples=50, deadline=5000)
    @example(
        prog=(
            '@untested("generated")\n'
            "func main() -> Unit\n"
            "    print(((-7) + 3) * 2)\n"
            "    return ()\n"
            "end func\n"
        )
    )
    def test_interpreter_vs_python(self, prog):
        interp_result = _run_interpreter(prog)
        py_result = _run_python(prog)
        assert interp_result == py_result, (
            f"Interpreter={interp_result}, Python={py_result}\n{prog}"
        )

    @given(prog=arith_program())
    @settings(max_examples=50, deadline=5000)
    def test_interpreter_vs_js(self, prog):
        if _NODE is None:
            pytest.skip("Node.js not available")
        interp_result = _run_interpreter(prog)
        js_result = _run_js(prog)
        assert interp_result == js_result, (
            f"Interpreter={interp_result}, JS={js_result}\n{prog}"
        )


class TestDivisionParity:
    """Integer division truncation is consistent across backends."""

    @given(prog=div_program())
    @settings(max_examples=30, deadline=5000)
    @example(
        prog=(
            '@untested("generated")\n'
            "func main() -> Unit\n"
            "    print(7 / 2)\n"
            "    return ()\n"
            "end func\n"
        )
    )
    @example(
        prog=(
            '@untested("generated")\n'
            "func main() -> Unit\n"
            "    print((-7) / 2)\n"
            "    return ()\n"
            "end func\n"
        )
    )
    def test_div_interpreter_vs_python(self, prog):
        interp_result = _run_interpreter(prog)
        py_result = _run_python(prog)
        assert interp_result == py_result, (
            f"Interpreter={interp_result}, Python={py_result}\n{prog}"
        )

    @given(prog=div_program())
    @settings(max_examples=30, deadline=5000)
    def test_div_interpreter_vs_js(self, prog):
        if _NODE is None:
            pytest.skip("Node.js not available")
        interp_result = _run_interpreter(prog)
        js_result = _run_js(prog)
        assert interp_result == js_result, (
            f"Interpreter={interp_result}, JS={js_result}\n{prog}"
        )


class TestModuloParity:
    """Modulo semantics are consistent across backends."""

    @given(prog=mod_program())
    @settings(max_examples=30, deadline=5000)
    @example(
        prog=(
            '@untested("generated")\n'
            "func main() -> Unit\n"
            "    print((-7) % 3)\n"
            "    return ()\n"
            "end func\n"
        )
    )
    def test_mod_interpreter_vs_python(self, prog):
        interp_result = _run_interpreter(prog)
        py_result = _run_python(prog)
        assert interp_result == py_result, (
            f"Interpreter={interp_result}, Python={py_result}\n{prog}"
        )

    @given(prog=mod_program())
    @settings(max_examples=30, deadline=5000)
    def test_mod_interpreter_vs_js(self, prog):
        if _NODE is None:
            pytest.skip("Node.js not available")
        interp_result = _run_interpreter(prog)
        js_result = _run_js(prog)
        assert interp_result == js_result, (
            f"Interpreter={interp_result}, JS={js_result}\n{prog}"
        )
