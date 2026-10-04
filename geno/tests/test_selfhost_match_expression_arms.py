"""Self-hosted match-expression arms yield their expression (#140)."""

from __future__ import annotations

from geno.tests.test_cli import _run_selfhost_cli

PROGRAM = """type Shape = Circle(r: Int) | Rect(w: Int, h: Int)
func area(s: Shape) -> Int
    example Circle(1) -> 3
    return match s with
        | Circle(r) -> 3 * r * r
        | Rect(w, h) -> w * h
    end match
end func
func main() -> Unit
    print(area(Rect(2, 3)))
    print(area(Circle(2)))
end func
"""


def test_selfhost_run_and_test_use_the_arm_value(tmp_path) -> None:
    program = tmp_path / "sm.geno"
    program.write_text(PROGRAM, encoding="utf-8")
    run = _run_selfhost_cli("run", str(program))
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.split() == ["6", "12"]
    tested = _run_selfhost_cli("test", str(program))
    assert tested.returncode == 0, tested.stdout + tested.stderr


def test_selfhost_check_rejects_a_mistyped_arm(tmp_path) -> None:
    program = tmp_path / "bad.geno"
    program.write_text(
        PROGRAM.replace("| Circle(r) -> 3 * r * r", '| Circle(r) -> "x"'),
        encoding="utf-8",
    )
    result = _run_selfhost_cli("check", str(program))
    assert result.returncode != 0
    assert "Match arm type mismatch" in result.stdout + result.stderr
