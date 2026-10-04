"""Awaiting one Async value twice runs it once on every backend (#136)."""

from __future__ import annotations

import pytest

from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

SOURCE = """
async func load_value(n: Int) -> Int
    print("loading")
    return n
end func

async func fail(n: Int) -> Int
    print("failing")
    if n > 0 then
        throw "boom"
    end if
    return n
end func

func main() -> Unit
    let a = load_value(20)
    let x = await a
    let y = await a
    print(x + y)
    let p = 5 |> load_value(_)
    print((await p) + (await p))
    let f = fail(1)
    try
        print(await f)
    catch e: String
        print("caught " + e)
    end try
    try
        print(await f)
    catch e: String
        print("caught " + e)
    end try
    return ()
end func
"""

EXPECTED = "loading\n40\nloading\n10\nfailing\ncaught boom\ncaught boom\n"


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
def test_second_await_reuses_the_first_result() -> None:
    _assert_expected_backend_outputs(
        label="await twice",
        context=SOURCE,
        expected=EXPECTED,
        interp_out=_interpreter_output(SOURCE),
        py_out=_compiled_python_output(SOURCE),
        js_out=_compiled_js_output(SOURCE),
    )


def test_default_geno_run_awaits_twice(tmp_path) -> None:
    import subprocess
    import sys
    from pathlib import Path

    program = tmp_path / "prog.geno"
    program.write_text(SOURCE, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", str(program)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.stdout == EXPECTED, result.stderr[-400:]
