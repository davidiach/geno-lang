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


@dataclass
class DiffResult:
    """Result of comparing all backend outputs."""

    source: str
    oracle: str | None  # expected stdout from generator, if available
    backends: list[BackendResult] = field(default_factory=list)
    match: bool = True
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
        config = RunConfig(timeout=timeout, max_steps=max_steps)
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(python_code)
            f.flush()
            tmp_path = f.name
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
            f.write(js_code)
            f.flush()
            tmp_path = f.name
        proc = subprocess.run(
            ["node", tmp_path],
            capture_output=True,
            text=True,
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
) -> DiffResult:
    """Run source through all backends and compare outputs.

    Args:
        source: Geno source code.
        oracle: Expected stdout from the generator (if available).
        timeout: Interpreter timeout in seconds.
        subprocess_timeout: Subprocess timeout for compiled backends.
        max_steps: Interpreter step limit.
        include_js: Whether to include the JS backend.

    Returns:
        DiffResult with comparison details.
    """
    result = DiffResult(source=source, oracle=oracle)

    # Run all backends
    interp = _run_interpreter(source, timeout=timeout, max_steps=max_steps)
    result.backends.append(interp)

    compiled_py = _run_compiled_python(source, timeout=subprocess_timeout)
    result.backends.append(compiled_py)

    if include_js:
        compiled_js = _run_compiled_js(source, timeout=subprocess_timeout)
        result.backends.append(compiled_js)

    # Collect successful outputs
    successful = [b for b in result.backends if b.success and b.stdout is not None]
    if len(successful) < 2:
        # Not enough backends succeeded for a meaningful comparison.
        # If only one succeeded and we have an oracle, compare against that.
        if len(successful) == 1 and oracle is not None:
            actual = _normalize_output(successful[0].stdout)  # type: ignore[arg-type]
            expected = _normalize_output(oracle)
            if actual != expected:
                result.match = False
                result.error = (
                    f"Only {successful[0].name} succeeded; "
                    f"output differs from oracle.\n"
                    f"  {successful[0].name}: {actual!r}\n"
                    f"  oracle: {expected!r}"
                )
        return result

    # Compare all successful backends pairwise
    reference_name = successful[0].name
    reference_out = _normalize_output(successful[0].stdout)  # type: ignore[arg-type]
    for other in successful[1:]:
        other_out = _normalize_output(other.stdout)  # type: ignore[arg-type]
        if reference_out != other_out:
            result.match = False
            result.error = (
                f"Output mismatch between {reference_name} and {other.name}.\n"
                f"  {reference_name}: {reference_out!r}\n"
                f"  {other.name}: {other_out!r}"
            )
            return result

    # If oracle is available, compare against it too
    if oracle is not None:
        expected = _normalize_output(oracle)
        if reference_out != expected:
            result.match = False
            result.error = (
                f"All backends agree but differ from oracle.\n"
                f"  backends: {reference_out!r}\n"
                f"  oracle: {expected!r}"
            )
            return result

    return result
