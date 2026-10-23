"""``main() -> Result[T, E]`` at every executable boundary (proposal 0009).

``Err(e)`` writes ``Error: <e>`` to stderr and exits 1, so ``?`` reaches the
process boundary; ``Ok(v)`` behaves as a ``main`` returning ``v`` would.
Embedding APIs keep returning the raw value.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from geno.api import RunConfig, run
from geno.compiler import compile_to_python
from geno.entrypoint import EntrypointResultKind, classify_entrypoint_result
from geno.js_compiler import compile_to_js
from geno.parser import parse
from geno.typechecker import TypeChecker

_NODE = shutil.which("node")

PARSE = """
func parse_port(text: String) -> Result[Int, String]
    example "80" -> Ok(80)
    match parse_int(text) with
        | Some(n) -> return Ok(n)
        | None -> return Err(f"not a port: {text}")
    end match
end func
"""


def _program(main: str) -> str:
    return PARSE + main


FAILING = _program(
    "func main() -> Result[Unit, String]\n"
    '    print("starting")\n'
    '    let port = parse_port("http")?\n'
    "    print(port)\n"
    "    return Ok(())\n"
    "end func\n"
)
SUCCEEDING = _program(
    "func main() -> Result[Unit, String]\n"
    '    let port = parse_port("8080")?\n'
    "    print(port)\n"
    "    return Ok(())\n"
    "end func\n"
)
STATUS = _program(
    "func main() -> Result[Int, String]\n"
    '    let port = parse_port("258")?\n'
    "    return Ok(port)\n"
    "end func\n"
)
DISPLAYED = _program(
    'func main() -> Result[String, Int]\n    return Ok("shown")\nend func\n'
)
STRUCTURED_ERROR = (
    "type Problem = Problem(code: Int, reason: String)\n"
    "func main() -> Result[Unit, Problem]\n"
    '    return Err(Problem(7, "bad input"))\n'
    "end func\n"
)

_MODES = [
    pytest.param((), id="process"),
    pytest.param(("--unsafe",), id="direct"),
]


def _geno_run(
    tmp_path: Path, source: str, *mode_args: str
) -> subprocess.CompletedProcess[str]:
    app = tmp_path / "App.geno"
    app.write_text(source, encoding="utf-8")
    caps = ("--cap", "print") if "--unsafe" in mode_args else ()
    return subprocess.run(
        [sys.executable, "-m", "geno", "run", *mode_args, *caps, str(app)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _kind(source: str) -> EntrypointResultKind:
    program = parse(source)
    TypeChecker().check_program(program)
    return classify_entrypoint_result(program)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        (FAILING, EntrypointResultKind.RESULT_UNIT),
        (STATUS, EntrypointResultKind.RESULT_INT),
        (DISPLAYED, EntrypointResultKind.RESULT_OTHER),
        (
            "type Outcome = Result[Int, String]\n"
            "func main() -> Outcome\n    return Ok(1)\nend func\n",
            EntrypointResultKind.RESULT_INT,
        ),
        (
            "async func main() -> Result[Unit, String]\n    return Ok(())\nend func\n",
            EntrypointResultKind.RESULT_UNIT,
        ),
    ],
)
def test_a_result_main_is_classified_by_its_ok_type(
    source: str, kind: EntrypointResultKind
) -> None:
    assert _kind(source) is kind
    assert kind.is_result
    # Compilation without type checking reads the annotation directly.
    assert classify_entrypoint_result(parse(source)) is kind


def test_result_kinds_unwrap_to_the_plain_kinds() -> None:
    assert EntrypointResultKind.RESULT_UNIT.ok_kind is EntrypointResultKind.UNIT
    assert EntrypointResultKind.RESULT_INT.ok_kind is EntrypointResultKind.INT
    assert EntrypointResultKind.RESULT_OTHER.ok_kind is EntrypointResultKind.OTHER
    assert EntrypointResultKind.INT.ok_kind is EntrypointResultKind.INT
    assert not EntrypointResultKind.OTHER.is_result


# ---------------------------------------------------------------------------
# geno run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode_args", _MODES)
def test_an_err_from_main_is_reported_and_exits_one(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    result = _geno_run(tmp_path, FAILING, *mode_args)
    assert result.returncode == 1
    assert result.stdout == "starting\n"
    assert result.stderr == "Error: not a port: http\n"


@pytest.mark.parametrize("mode_args", _MODES)
def test_an_ok_from_main_behaves_like_its_value(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    succeeded = _geno_run(tmp_path, SUCCEEDING, *mode_args)
    assert (succeeded.returncode, succeeded.stdout, succeeded.stderr) == (
        0,
        "8080\n",
        "",
    )
    status = _geno_run(tmp_path, STATUS, *mode_args)
    assert (status.returncode, status.stdout, status.stderr) == (2, "", "")
    shown = _geno_run(tmp_path, DISPLAYED, *mode_args)
    assert (shown.returncode, shown.stdout, shown.stderr) == (0, "=> shown\n", "")


@pytest.mark.parametrize("mode_args", _MODES)
def test_a_structured_error_is_displayed(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    result = _geno_run(tmp_path, STRUCTURED_ERROR, *mode_args)
    assert result.returncode == 1
    assert result.stderr == 'Error: Problem(code: 7, reason: "bad input")\n'


def test_json_keeps_the_raw_err_and_exits_one(tmp_path: Path) -> None:
    result = _geno_run(tmp_path, FAILING, "--json", "--cap", "print")
    assert result.returncode == 1
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["output"] == "starting\n"
    assert envelope["diagnostics"] == []
    assert "not a port: http" in json.dumps(envelope["value"])


def test_embedding_returns_the_raw_result() -> None:
    result = run(FAILING, config=RunConfig(capabilities={"print"}))
    assert result.ok
    assert result.value_raw.constructor == "Err"
    assert result.output == "starting\n"


# ---------------------------------------------------------------------------
# Standalone artifacts
# ---------------------------------------------------------------------------


def _python_artifact(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "app.py"
    script.write_text(compile_to_python(source), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script), "--cap", "print"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _node_artifact(
    tmp_path: Path, source: str, *, target: str | None = None
) -> subprocess.CompletedProcess[str]:
    assert _NODE is not None
    script = tmp_path / "app.mjs"
    js = compile_to_js(source) if target is None else compile_to_js(source, target)
    assert isinstance(js, str)
    script.write_text(js, encoding="utf-8")
    return subprocess.run(
        [_NODE, str(script), "--cap", "print"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (FAILING, (1, "starting\n", "Error: not a port: http\n")),
        (SUCCEEDING, (0, "8080\n", "")),
        (STATUS, (2, "", "")),
        (DISPLAYED, (0, "shown\n", "")),
    ],
)
def test_compiled_python_reports_a_result_main(
    tmp_path: Path, source: str, expected: tuple[int, str, str]
) -> None:
    result = _python_artifact(tmp_path, source)
    assert (result.returncode, result.stdout, result.stderr) == expected


@pytest.mark.skipif(_NODE is None, reason="Node.js not available")
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (FAILING, (1, "starting\n", "Error: not a port: http\n")),
        (SUCCEEDING, (0, "8080\n", "")),
        (STATUS, (2, "", "")),
        (DISPLAYED, (0, "shown\n", "")),
        (STRUCTURED_ERROR, (1, "", 'Error: Problem(code: 7, reason: "bad input")\n')),
    ],
)
def test_compiled_node_reports_a_result_main(
    tmp_path: Path, source: str, expected: tuple[int, str, str]
) -> None:
    result = _node_artifact(tmp_path, source)
    assert (result.returncode, result.stdout, result.stderr) == expected
