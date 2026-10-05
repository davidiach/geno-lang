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


def test_concurrent_awaiters_share_one_run() -> None:
    import asyncio

    from geno._runtime_support import _GenoAsync

    runs = []

    async def work() -> int:
        runs.append(1)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return 7

    shared = _GenoAsync(work())

    async def consume() -> int:
        return await shared

    async def both() -> list[int]:
        # Separate coroutines: gather() would de-duplicate the same awaitable.
        return list(await asyncio.gather(consume(), consume(), consume()))

    assert asyncio.run(both()) == [7, 7, 7]
    assert len(runs) == 1


def test_concurrent_awaiters_share_one_error() -> None:
    import asyncio

    from geno._runtime_support import _GenoAsync

    runs = []

    async def work() -> int:
        runs.append(1)
        await asyncio.sleep(0)
        raise RuntimeError("boom")

    shared = _GenoAsync(work())

    async def consume() -> int:
        return await shared

    async def both() -> list[object]:
        return list(await asyncio.gather(consume(), consume(), return_exceptions=True))

    outcomes = asyncio.run(both())
    assert [str(o) for o in outcomes] == ["boom", "boom"]
    assert len(runs) == 1


def test_cancelled_run_does_not_strand_other_awaiters() -> None:
    import asyncio

    from geno._runtime_support import _GenoAsync

    async def work() -> int:
        await asyncio.sleep(10)
        return 1

    shared = _GenoAsync(work())

    async def consume() -> int:
        return await shared

    async def scenario() -> str:
        first = asyncio.ensure_future(consume())
        second = asyncio.ensure_future(consume())
        await asyncio.sleep(0)
        first.cancel()
        try:
            await asyncio.wait_for(second, timeout=5)
        except RuntimeError as exc:
            return str(exc)
        return "no error"

    assert asyncio.run(scenario()) == "Async value did not finish"
