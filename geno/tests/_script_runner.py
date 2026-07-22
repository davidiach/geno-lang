from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence


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
