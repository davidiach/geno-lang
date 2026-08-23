"""The default ``geno run`` path must not import the frontend in the parent.

``geno run`` compiles and executes inside the process-isolated worker, so the
worker imports lexer/parser/typechecker/dependency-graph itself. Importing them
in the parent as well pays for the whole frontend twice per run -- 17.2% of the
wall-clock time of running a small program, measured paired -- for exception
names the process path cannot raise, because the worker reports every frontend
failure back as pre-formatted text.

This asserts the footprint rather than the timing, and that is what makes it
the real guard for this regression. 17.2% sits under the perf ratchet's 20%
headroom, which cannot safely be tightened below roughly four times the ~5%
measurement noise, so ``make perf-ratchets`` would let a reintroduction
through. The import footprint is exact on any machine at any load.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Modules the worker owns on the process-isolated run path. ``geno.sandbox`` is
# deliberately absent: the parent has to spawn and supervise the worker.
FRONTEND_MODULES = (
    "geno.compiler",
    "geno.dependency_graph",
    "geno.interpreter",
    "geno.lexer",
    "geno.parser",
    "geno.project_resolution",
    "geno.typechecker",
    "geno.values",
)

_PROBE = """
import json, runpy, sys

sys.argv = ["geno", *sys.argv[1:]]
status = 0
try:
    runpy.run_module("geno", run_name="__main__")
except SystemExit as exc:
    status = exc.code if isinstance(exc.code, int) else 1
print(
    "\\x1ePROBE\\x1e"
    + json.dumps(
        {
            "status": status,
            "loaded": sorted(m for m in %(modules)r if m in sys.modules),
        }
    )
)
"""


def _loaded_modules(source: Path, *args: str) -> dict[str, object]:
    """Run the CLI in-process and report which frontend modules it imported."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _PROBE % {"modules": FRONTEND_MODULES},
            *args,
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    marker = completed.stdout.rpartition("\x1ePROBE\x1e")
    assert marker[1], (
        f"probe produced no result: {completed.stdout}\n{completed.stderr}"
    )
    return json.loads(marker[2])


@pytest.fixture
def program(tmp_path: Path) -> Path:
    source = tmp_path / "Main.geno"
    source.write_text(
        """
func double(n: Int) -> Int
    example 2 -> 4

    return n * 2
end func

func main() -> Int
    return double(21)
end func
""".lstrip(),
        encoding="utf-8",
    )
    return source


def test_process_run_does_not_import_the_frontend_in_the_parent(program: Path) -> None:
    result = _loaded_modules(program, "run")

    assert result["status"] == 0
    assert result["loaded"] == []


def test_unsafe_run_still_imports_the_frontend_in_the_parent(program: Path) -> None:
    # The --unsafe path runs the interpreter in this process, so the frontend
    # genuinely belongs here. This guards the fix against being "extended" into
    # the path that needs those imports.
    result = _loaded_modules(program, "run", "--unsafe")

    assert result["status"] == 0
    assert "geno.interpreter" in result["loaded"]
    assert "geno.typechecker" in result["loaded"]
