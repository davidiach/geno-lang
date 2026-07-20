from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

_NODE_MAIN_EXIT_GUARD = (
    "if (typeof process === 'object' && process !== null && "
    "process.release && process.release.name === 'node') {"
)


def display_main_result_for_test(js_code: str) -> str:
    """Route an Int main through the browser display branch in value tests."""
    return js_code.replace(_NODE_MAIN_EXIT_GUARD, "if (false) {")


def display_python_main_result_for_test(python_code: str) -> str:
    """Keep compiled-Python value tests independent of script exit policy."""
    return python_code.replace(
        "    raise SystemExit(result % 256)",
        "    print(result)",
    )


def _run_generated_script(
    executable: str,
    code: str,
    *,
    suffix: str,
    args: Sequence[str] = (),
    timeout: float | None = 10,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run generated source from a temp file to avoid Windows argv limits."""
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    path = Path(tmp_path)
    try:
        path.write_text(code, encoding="utf-8", newline="\n")
        return subprocess.run(
            [executable, str(path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def run_node_code(
    js_code: str,
    *,
    node_executable: str = "node",
    args: Sequence[str] = (),
    timeout: float | None = 10,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run_generated_script(
        node_executable,
        js_code,
        suffix=".js",
        args=args,
        timeout=timeout,
        cwd=cwd,
    )


def run_python_code(
    python_code: str,
    *,
    python_executable: str,
    args: Sequence[str] = (),
    timeout: float | None = 10,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run_generated_script(
        python_executable,
        python_code,
        suffix=".py",
        args=args,
        timeout=timeout,
        cwd=cwd,
    )
