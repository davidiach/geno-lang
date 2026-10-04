"""`geno run` keeps output printed before an uncaught runtime error (#130)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PROGRAM = """func main() -> Unit
    print("before")
    let xs = [1, 2]
    print(xs[5])
end func
"""


@pytest.mark.parametrize(
    "flags",
    [[], ["--unsafe"], ["--unsafe", "--cap", "print"]],
    ids=["process-sandbox", "unsafe", "unsafe-cap-print"],
)
def test_output_before_runtime_error_is_printed(tmp_path: Path, flags: list[str]):
    program = tmp_path / "prog.geno"
    program.write_text(PROGRAM, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", *flags, str(program)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == "before\n"
    assert "out of bounds" in result.stderr


def test_json_envelope_keeps_output_before_runtime_error(tmp_path: Path):
    import json

    program = tmp_path / "prog.geno"
    program.write_text(PROGRAM, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", "--json", "--cap", "print", str(program)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is False
    assert envelope["output"] == "before\n"
