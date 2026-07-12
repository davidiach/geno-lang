"""Compiled-backend capability enforcement tests."""

from __future__ import annotations

import inspect
import shutil
import sys
from pathlib import Path

import pytest

from geno import _runtime_support as py_runtime
from geno.builtin_manifest import BUILTIN_MANIFEST
from geno.compiler import compile_to_python
from geno.js_compiler import compile_to_js
from geno.tests._script_runner import run_node_code, run_python_code
from geno.types import GenoTypeError

GATED_BUILTINS = tuple(
    sorted((name, cap) for name, (_params, cap) in BUILTIN_MANIFEST.items() if cap)
)

PYTHON_RUNTIME_NAME_OVERRIDES = {
    "exec": "exec_",
}

JS_RUNTIME = (Path(__file__).resolve().parents[1] / "_js_runtime_support.js").read_text(
    encoding="utf-8"
)

CAPABILITY_DENIAL_PROGRAMS = (
    (
        "print",
        "print",
        """
func main() -> Unit
    print(42)
end func
""",
    ),
    (
        "clock",
        "clock_now",
        """
func main() -> Int
    return clock_now()
end func
""",
    ),
    (
        "random",
        "random_int",
        """
func main() -> Int
    return random_int(min: 1, max: 3)
end func
""",
    ),
    (
        "regex",
        "regex_match",
        """
func main() -> Option[String]
    return regex_match(pattern: "a", text: "a")
end func
""",
    ),
    (
        "serve",
        "http_respond",
        """
func main() -> Unit
    http_respond(status: 200, headers: [], body: "ok")
end func
""",
    ),
)

JS_CAPABILITY_DENIAL_PROGRAMS = tuple(
    program for program in CAPABILITY_DENIAL_PROGRAMS if program[1] != "http_respond"
)


@pytest.mark.parametrize(("builtin_name", "capability"), GATED_BUILTINS)
def test_compiled_python_runtime_helpers_match_manifest_capability_map(
    builtin_name: str, capability: str
) -> None:
    runtime_name = PYTHON_RUNTIME_NAME_OVERRIDES.get(builtin_name, builtin_name)
    helper = getattr(py_runtime, runtime_name)
    source = inspect.getsource(helper)

    assert f'_require_cap("{capability}",' in source


@pytest.mark.parametrize(("builtin_name", "capability"), GATED_BUILTINS)
def test_compiled_js_runtime_helpers_match_manifest_capability_map(
    builtin_name: str, capability: str
) -> None:
    start = JS_RUNTIME.find(f"function {builtin_name}(")
    if start == -1:
        pytest.skip(f"no JS runtime helper for {builtin_name}")

    window = JS_RUNTIME[start : start + 700]
    assert f'_requireCap("{capability}",' in window


@pytest.mark.parametrize(
    ("capability", "builtin_name", "source"), CAPABILITY_DENIAL_PROGRAMS
)
def test_compiled_python_denies_gated_builtins_without_capability(
    capability: str, builtin_name: str, source: str
) -> None:
    result = run_python_code(
        compile_to_python(source),
        python_executable=sys.executable,
        timeout=10,
    )

    assert result.returncode != 0
    assert "Capability denied" in result.stderr
    assert builtin_name in result.stderr
    assert f"--cap {capability}" in result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize(
    ("capability", "builtin_name", "source"), JS_CAPABILITY_DENIAL_PROGRAMS
)
def test_compiled_js_denies_gated_builtins_without_capability(
    capability: str, builtin_name: str, source: str
) -> None:
    result = run_node_code(compile_to_js(source), timeout=10)

    assert result.returncode != 0
    assert "Capability denied" in result.stderr
    assert builtin_name in result.stderr
    assert f"--cap {capability}" in result.stderr


def test_compiled_js_rejects_unavailable_http_respond() -> None:
    source = """
func main() -> Unit
    http_respond(status: 200, headers: [], body: "ok")
end func
"""

    with pytest.raises(GenoTypeError, match=r"http_respond.*node-cli"):
        compile_to_js(source)


def test_compiled_python_allows_gated_builtin_with_capability() -> None:
    source = """
func main() -> String
    return clock_format(timestamp: 0.0, fmt: "%Y")
end func
"""
    result = run_python_code(
        compile_to_python(source),
        python_executable=sys.executable,
        args=("--cap", "clock"),
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1970"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_compiled_js_allows_gated_builtin_with_capability() -> None:
    source = """
func main() -> String
    return clock_format(timestamp: 0.0, fmt: "%Y")
end func
"""
    result = run_node_code(
        compile_to_js(source),
        args=("--cap", "clock"),
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1970"
