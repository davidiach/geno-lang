"""
Differential backend runner.

Executes a Geno program through all available backends and compares stdout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BackendResult:
    """Result of running a program through a single backend."""

    name: str
    stdout: str | None  # None if backend unavailable or crashed
    stderr: str
    success: bool
    elapsed_s: float
    available: bool = True


@dataclass
class DiffResult:
    """Result of comparing all backend outputs."""

    source: str
    oracle: str | None  # expected stdout from generator, if available
    backends: list[BackendResult] = field(default_factory=list)
    match: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Individual backend runners
# ---------------------------------------------------------------------------


def _has_node() -> bool:
    """Check whether Node.js is available on the PATH."""
    return shutil.which("node") is not None


def _run_interpreter(
    source: str, timeout: float = 5.0, max_steps: int | None = 100_000
) -> BackendResult:
    """Run source via the Geno interpreter."""
    from geno.api import RunConfig, run

    t0 = time.monotonic()
    try:
        config = RunConfig(timeout=timeout, max_steps=max_steps, capabilities={"print"})
        result = run(source, config=config)
        elapsed = time.monotonic() - t0
        if result.ok:
            stdout = (
                result.output if isinstance(result.output, str) else str(result.output)
            )
            return BackendResult(
                name="interpreter",
                stdout=stdout,
                stderr="",
                success=True,
                elapsed_s=elapsed,
            )
        diags = "; ".join(d.message for d in result.diagnostics)
        return BackendResult(
            name="interpreter",
            stdout=None,
            stderr=diags,
            success=False,
            elapsed_s=elapsed,
        )
    except Exception as e:
        return BackendResult(
            name="interpreter",
            stdout=None,
            stderr=str(e),
            success=False,
            elapsed_s=time.monotonic() - t0,
        )


def _run_compiled_python(source: str, timeout: float = 10.0) -> BackendResult:
    """Compile to Python and execute as subprocess."""
    from geno.compiler import compile_to_python

    t0 = time.monotonic()
    try:
        python_code = compile_to_python(source)
    except Exception as e:
        return BackendResult(
            name="compiled_py",
            stdout=None,
            stderr=f"Compilation failed: {e}",
            success=False,
            elapsed_s=time.monotonic() - t0,
        )
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", encoding="utf-8", delete=False
        ) as f:
            f.write(python_code)
            f.flush()
            tmp_path = f.name
        proc = subprocess.run(
            [sys.executable, tmp_path, "--cap", "print"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=timeout,
        )
        elapsed = time.monotonic() - t0
        if proc.returncode != 0:
            return BackendResult(
                name="compiled_py",
                stdout=None,
                stderr=proc.stderr,
                success=False,
                elapsed_s=elapsed,
            )
        return BackendResult(
            name="compiled_py",
            stdout=proc.stdout,
            stderr=proc.stderr,
            success=True,
            elapsed_s=elapsed,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run() already kills the child on timeout
        return BackendResult(
            name="compiled_py",
            stdout=None,
            stderr="timeout",
            success=False,
            elapsed_s=time.monotonic() - t0,
        )
    except Exception as e:
        return BackendResult(
            name="compiled_py",
            stdout=None,
            stderr=str(e),
            success=False,
            elapsed_s=time.monotonic() - t0,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except (OSError, UnboundLocalError):
            pass


def _run_compiled_js(source: str, timeout: float = 10.0) -> BackendResult:
    """Compile to JS and execute via Node.js."""
    if not _has_node():
        return BackendResult(
            name="compiled_js",
            stdout=None,
            stderr="node not available",
            success=False,
            elapsed_s=0.0,
            available=False,
        )
    from geno.js_compiler import compile_to_js

    t0 = time.monotonic()
    try:
        js_code = compile_to_js(source)
        if isinstance(js_code, tuple):
            js_code = js_code[0]
    except Exception as e:
        return BackendResult(
            name="compiled_js",
            stdout=None,
            stderr=f"Compilation failed: {e}",
            success=False,
            elapsed_s=time.monotonic() - t0,
        )
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", encoding="utf-8", delete=False
        ) as f:
            f.write(js_code)
            f.flush()
            tmp_path = f.name
        proc = subprocess.run(
            ["node", tmp_path, "--cap", "print"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        elapsed = time.monotonic() - t0
        if proc.returncode != 0:
            return BackendResult(
                name="compiled_js",
                stdout=None,
                stderr=proc.stderr,
                success=False,
                elapsed_s=elapsed,
            )
        return BackendResult(
            name="compiled_js",
            stdout=proc.stdout,
            stderr=proc.stderr,
            success=True,
            elapsed_s=elapsed,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run() already kills the child on timeout
        return BackendResult(
            name="compiled_js",
            stdout=None,
            stderr="timeout",
            success=False,
            elapsed_s=time.monotonic() - t0,
        )
    except Exception as e:
        return BackendResult(
            name="compiled_js",
            stdout=None,
            stderr=str(e),
            success=False,
            elapsed_s=time.monotonic() - t0,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except (OSError, UnboundLocalError):
            pass


# ---------------------------------------------------------------------------
# Differential comparison
# ---------------------------------------------------------------------------


def _normalize_output(s: str) -> str:
    """Normalize backend stdout for comparison."""
    return s.rstrip()


def run_all_backends(
    source: str,
    oracle: str | None = None,
    *,
    timeout: float = 5.0,
    subprocess_timeout: float = 10.0,
    max_steps: int | None = 100_000,
    include_js: bool = True,
    require_js: bool = False,
) -> DiffResult:
    """Run source through all backends and compare outputs.

    Args:
        source: Geno source code.
        oracle: Expected stdout from the generator (if available).
        timeout: Interpreter timeout in seconds.
        subprocess_timeout: Subprocess timeout for compiled backends.
        max_steps: Interpreter step limit.
        include_js: Whether to include the JS backend.
        require_js: Fail if Node.js is unavailable (for Node-enabled CI).

    Returns:
        DiffResult with comparison details.
    """
    result = DiffResult(source=source, oracle=oracle)

    # Run all backends
    interp = _run_interpreter(source, timeout=timeout, max_steps=max_steps)
    result.backends.append(interp)

    compiled_py = _run_compiled_python(source, timeout=subprocess_timeout)
    result.backends.append(compiled_py)

    if include_js or require_js:
        compiled_js = _run_compiled_js(source, timeout=subprocess_timeout)
        result.backends.append(compiled_js)

    # Every available backend is required. A crash must never improve parity
    # by silently removing that implementation from the comparison.
    errors = [
        f"{b.name} failed: {b.stderr}"
        for b in result.backends
        if b.available and (not b.success or b.stdout is None)
    ]
    if require_js and any(not b.available for b in result.backends):
        errors.append("Required JavaScript backend is unavailable: node not available")
    successful = [b for b in result.backends if b.success and b.stdout is not None]
    if len(successful) < 2:
        errors.append("Fewer than two backends executed successfully")

    # Check every successful output, including the single-survivor case.
    if successful:
        reference_name = "oracle" if oracle is not None else successful[0].name
        reference_out = _normalize_output(
            oracle if oracle is not None else successful[0].stdout or ""
        )
        for backend in successful:
            actual = _normalize_output(backend.stdout or "")
            if actual != reference_out:
                errors.append(
                    f"Output mismatch between {reference_name} and {backend.name}.\n"
                    f"  {reference_name}: {reference_out!r}\n"
                    f"  {backend.name}: {actual!r}"
                )

    result.match = not errors
    if errors:
        result.error = "\n".join(errors)

    return result
