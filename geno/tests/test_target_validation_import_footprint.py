"""Target checks should load only the compiler backend they validate.

The default check and test paths validate Python lowering. Importing the
JavaScript compiler solely for its error class adds startup cost to both.
A subprocess gives each probe an uncontaminated import table, so this guards
that cost without depending on machine speed or benchmark timing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_PROBE = """
import json, runpy, sys

sys.argv = ["geno", *sys.argv[1:]]
status = 0
try:
    runpy.run_module("geno", run_name="__main__")
except SystemExit as exc:
    status = 0 if exc.code is None else (exc.code if isinstance(exc.code, int) else 1)
print(
    "\\x1ePROBE\\x1e"
    + json.dumps(
        {
            "status": status,
            "loaded": sorted(
                name for name in ("geno.compiler", "geno.js_compiler")
                if name in sys.modules
            ),
        }
    )
)
"""


@pytest.mark.parametrize(
    ("command", "target", "compiler"),
    [
        ("check", None, "geno.compiler"),
        ("check", "python-cli", "geno.compiler"),
        ("check", "python-hosted", "geno.compiler"),
        ("check", "node-cli", "geno.js_compiler"),
        ("check", "browser", "geno.js_compiler"),
        ("test", None, "geno.compiler"),
    ],
)
def test_target_validation_imports_only_selected_backend(
    tmp_path: Path, command: str, target: str | None, compiler: str
) -> None:
    source = tmp_path / "Main.geno"
    source.write_text(
        "func double(n: Int) -> Int\n"
        "    example 2 -> 4\n"
        "    return n * 2\n"
        "end func\n"
        "func main() -> Int\n"
        "    return 0\n"
        "end func\n",
        encoding="utf-8",
    )
    args = [command, str(source)]
    if target is not None:
        args.extend(["--target", target])
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE, *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    marker = completed.stdout.rpartition("\x1ePROBE\x1e")
    assert marker[1], (
        f"probe produced no result: {completed.stdout}\n{completed.stderr}"
    )
    result = json.loads(marker[2])
    assert result["status"] == 0, completed.stderr
    assert result["loaded"] == [compiler]
