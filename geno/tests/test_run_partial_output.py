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


SPIN_PROGRAM = """func spin(n: Int) -> Int
    example spin(-1) -> -1
    var i = n
    while i >= 0 do
        i = i + 1
    end while
    return i
end func

func main() -> Int
    print("before")
    return spin(1)
end func
"""


def test_output_before_process_timeout_is_printed(tmp_path: Path):
    program = tmp_path / "prog.geno"
    program.write_text(SPIN_PROGRAM, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", "--timeout", "2", str(program)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == "before\n"
    assert "timed out" in result.stderr


FAILING_EXAMPLE_PROGRAM = """func noisy(n: Int) -> Int
    example noisy(1) -> 99
    print("from example")
    return n
end func

func main() -> Int
    return noisy(1)
end func
"""


def test_failed_example_output_is_not_program_output():
    from geno.api import RunConfig, run

    result = run(FAILING_EXAMPLE_PROGRAM, config=RunConfig(capabilities={"print"}))
    assert not result.ok
    assert result.output == ""
