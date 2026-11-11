"""Regression tests for reference apps in examples/apps.

These run the apps' own Geno code: the tetris piece tables through the
embedding API, and geno-form's render through the interpreter with the draw
builtins recorded.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

import geno
from geno import RunConfig
from geno import builtins as geno_builtins
from geno.__main__ import main as geno_main

APPS = Path(__file__).resolve().parents[2] / "examples" / "apps"

Cells = frozenset[tuple[int, int]]


def _normalize(cells: set[tuple[int, int]]) -> Cells:
    mx = min(x for x, _ in cells)
    my = min(y for _, y in cells)
    return frozenset((x - mx, y - my) for x, y in cells)


def _ccw(cells: Cells) -> Cells:
    width = max(x for x, _ in cells) + 1
    return _normalize({(y, width - 1 - x) for x, y in cells})


def _shape(rows: str) -> Cells:
    return frozenset(
        (x, y)
        for y, row in enumerate(rows.split("/"))
        for x, ch in enumerate(row)
        if ch == "#"
    )


# Rotation 0 of each piece index in tetris.geno; ArrowUp turns counter-clockwise.
TETRIS_ROT0 = {
    0: _shape("####"),  # I
    1: _shape("##/##"),  # O
    2: _shape("###/.#."),  # T
    3: _shape(".##/##."),  # S
    4: _shape("##./.##"),  # Z
    5: _shape("..#/###"),  # L
    6: _shape("#../###"),  # J
}


def _tetris_pieces() -> dict[tuple[int, int], Cells]:
    source = (APPS / "tetris.geno").read_text(encoding="utf-8")
    start = source.index("func piece_dx")
    end = source.index("end func piece_dy") + len("end func piece_dy")
    harness = source[start:end] + (
        '\n\n@untested("test harness")\n'
        "func main() -> Unit\n"
        "    for p: Int in range(0, 7) do\n"
        "        for r: Int in range(0, 4) do\n"
        "            for c: Int in range(0, 4) do\n"
        "                let dx = piece_dx(piece: p, rot: r, cell: c)\n"
        "                let dy = piece_dy(piece: p, rot: r, cell: c)\n"
        '                print(f"{p} {r} {dx} {dy}")\n'
        "            end for\n"
        "        end for\n"
        "    end for\n"
        "    return ()\n"
        "end func\n"
    )
    result = geno.run(harness, config=RunConfig(capabilities={"print"}))
    assert result.ok, result.diagnostics
    pieces: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for line in result.output.splitlines():
        p, r, dx, dy = map(int, line.split())
        pieces.setdefault((p, r), set()).add((dx, dy))
    return {key: frozenset(cells) for key, cells in pieces.items()}


def test_tetris_pieces_are_tetrominoes_that_rotate_consistently():
    pieces = _tetris_pieces()
    for p, rot0 in TETRIS_ROT0.items():
        assert len(pieces[(p, 0)]) == 4, f"piece {p} rotation 0"
        assert _normalize(set(pieces[(p, 0)])) == rot0, f"piece {p} rotation 0"
        for r in range(4):
            cells = pieces[(p, r)]
            assert len(cells) == 4, f"piece {p} rotation {r} has {len(cells)} cells"
            turned = _normalize(set(pieces[(p, (r + 1) % 4)]))
            assert turned == _ccw(_normalize(set(cells))), f"piece {p} rotation {r + 1}"


def test_tetris_board_and_score_fit_the_default_canvas():
    source = (APPS / "tetris.geno").read_text(encoding="utf-8")
    cell = re.search(r"y: 20 \+ y \* (\d+), width: \d+, height: (\d+)", source)
    assert cell, "grid drawing call not found"
    pitch, size = int(cell.group(1)), int(cell.group(2))
    assert 20 + 19 * pitch + size <= 600
    score = re.search(r'format\("Score: \{\}".*?y: (\d+)', source)
    assert score, "score drawing call not found"
    assert int(score.group(1)) <= 600


def test_geno_form_stacks_its_fields(tmp_path, monkeypatch, capsys):
    project = tmp_path / "geno-form"
    shutil.copytree(APPS / "geno-form", project)
    main = project / "Main.geno"
    main.write_text(
        main.read_text(encoding="utf-8") + '\n@untested("test harness")\n'
        "func main() -> Unit\n"
        "    render(init())\n"
        "    return ()\n"
        "end func\n",
        encoding="utf-8",
    )
    boxes: list[tuple[int, int]] = []

    def record_outline(x, y, width, height, color):
        boxes.append((y, width))

    monkeypatch.setattr(geno_builtins, "builtin_draw_rect_outline", record_outline)
    try:
        geno_main(["run", "--unsafe", str(main)])
    except SystemExit as exc:
        assert not exc.code, capsys.readouterr()
    field_rows = [y for y, width in boxes if width in (320, 18)]
    # Registration: four text fields and the terms checkbox, one under another.
    assert len(field_rows) == 5, boxes
    assert field_rows == sorted(set(field_rows)), field_rows


def _readme_lane_problems(text: str) -> list[str]:
    """Return each `geno run --cap` line that names the wrong lane.

    The default process-isolated lane grants the capabilities in
    ``ISOLATED_RUN_CAPABILITIES`` (proposal 0013), so a command granting only
    those must not leave it for `--unsafe`, and one granting anything else
    (`process`, `serve`, ...) must say `--unsafe` or `--json`.
    """
    from geno.capabilities import ISOLATED_RUN_CAPABILITIES

    problems: list[str] = []
    for line in text.splitlines():
        command = re.search(r"\bgeno run\b.*", line)
        if command is None or not re.search(r"--cap\b", command.group(0)):
            continue
        command_text = command.group(0).split("`")[0]
        granted = {
            name
            for value in re.findall(r"--cap[= ]+([A-Za-z_,]+)", command_text)
            for name in value.split(",")
            if name
        }
        isolated = granted <= ISOLATED_RUN_CAPABILITIES
        other_lane = "--unsafe" in command_text or "--json" in command_text
        if isolated and "--unsafe" in command_text:
            problems.append(f"leaves process isolation needlessly: {line}")
        elif not isolated and not other_lane:
            problems.append(f"the default lane refuses this grant: {line}")
    return problems


@pytest.mark.parametrize(
    "readme", sorted(APPS.glob("*/README.md")), ids=lambda p: p.parent.name
)
def test_app_readmes_grant_capabilities_in_a_supported_lane(readme):
    assert _readme_lane_problems(readme.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        ("geno run --cap serve examples/apps/x", "refuses this grant"),
        ("geno run --cap print,process x.geno", "refuses this grant"),
        ("geno run --unsafe --cap fs,print x.geno", "needlessly"),
    ],
)
def test_the_readme_lane_rule_flags_a_wrong_lane(line: str, fragment: str) -> None:
    (problem,) = _readme_lane_problems(f"```bash\n{line}\n```\n")

    assert fragment in problem


@pytest.mark.parametrize(
    "line",
    [
        "geno run --cap env,fs,print examples/apps/x -- input.csv",
        "geno run --unsafe --cap serve,env examples/apps/x",
        "geno run --json --cap process x.geno",
        "`geno run --cap print,env x` also works; `--unsafe` is for trusted code.",
    ],
)
def test_the_readme_lane_rule_accepts_a_supported_lane(line: str) -> None:
    assert _readme_lane_problems(line) == []
