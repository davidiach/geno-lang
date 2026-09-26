from __future__ import annotations

import os
import re
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


def declares_int_main(source: str) -> bool:
    """Whether ``source`` declares a zero-argument ``main`` returning ``Int``.

    A literal check, deliberately: it exists only to tell the test helpers below
    which channel a compiled artifact reports its result on. The real
    classification is `geno.entrypoint.classify_entrypoint_result`, which also
    handles an aliased return type and needs a parsed program; tests *about* the
    exit contract use that instead, through the CLI or the compiler.
    """
    return re.search(r"func\s+main\s*\(\s*\)\s*->\s*Int\b", source) is not None


def entrypoint_observation(
    completed: subprocess.CompletedProcess[str],
    *,
    int_main: bool,
) -> str:
    """What a standalone artifact reported for its ``main`` result, as text.

    From Geno 0.5 an `Int` result is the process exit status and is not printed
    (docs/spec/v0.5.md 4.1.1), while every other displayed kind still arrives on
    stdout. Many tests predate that and use `main() -> Int` purely to observe a
    computed value; passing ``int_main`` lets them keep asserting on one string.

    The status channel only carries 0 to 255, so a test whose value falls
    outside that range must print it instead of returning it -- `print` accepts
    an `Int` directly and emits the same digits the backend used to.
    """
    printed = completed.stdout.strip()
    if not int_main:
        return printed
    # An `Int` main prints nothing, so anything on stdout came from the program
    # itself and is the more specific observation.
    return printed if printed else str(completed.returncode)
