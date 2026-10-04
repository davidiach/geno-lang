"""An index, field or `?` after a pipeline stage applies to its result (#138)."""

from __future__ import annotations

import pytest

from geno.ast_nodes import FieldAccess, FunctionCall, IndexAccess, Pipeline
from geno.parser import parse
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

SOURCE = """
type Box = Box(x: Int)

func mk(n: Int) -> Box
    example 1 -> Box(1)
    return Box(n)
end func

func first_ok(xs: List[Int]) -> Result[Int, String]
    example [1] -> Ok(1)
    if length(xs) == 0 then
        return Err("empty")
    end if
    return Ok(xs[0])
end func

func doubled_first(xs: List[Int]) -> Result[Int, String]
    example [2] -> Ok(4)
    let n = xs |> first_ok(_)?
    return Ok(n * 2)
end func

func main() -> Unit
    let x = [1, 2] |> map(_, fn(n: Int) -> n + 1)[0]
    print(x)
    print(5 |> mk(_).x)
    let y = [3, 4] |> map(_, fn(n: Int) -> n * 2)[1] |> mk(_)
    print(y)
    print(doubled_first([3]))
    print(doubled_first([]))
    let z = [10] |> map(_, fn(n: Int) -> fn(k: Int) -> k + n)[0](5)
    print(z)
    return ()
end func
"""

EXPECTED = '2\n5\nBox(x: 8)\nOk(value: 6)\nErr(error: "empty")\n15\n'


def _value_of_first_let(source: str):
    main = parse(source).definitions[-1]
    return main.body[0].value


def test_trailing_index_wraps_the_pipeline() -> None:
    value = _value_of_first_let(
        "func main() -> Unit\n    let x = [1] |> map(_, fn(n: Int) -> n)[0]\nend func\n"
    )
    assert isinstance(value, IndexAccess)
    assert isinstance(value.target, Pipeline)


def test_trailing_field_then_next_stage() -> None:
    value = _value_of_first_let(
        "func main() -> Unit\n    let x = 5 |> mk(_).x |> show(_)\nend func\n"
    )
    assert isinstance(value, Pipeline)
    assert isinstance(value.initial, FieldAccess)
    assert isinstance(value.initial.target, Pipeline)
    assert len(value.stages) == 1


def test_call_after_stage_postfix_stays_attached() -> None:
    value = _value_of_first_let(
        "func main() -> Unit\n    let x = 1 |> make(_).handler()\nend func\n"
    )
    assert isinstance(value, FunctionCall)
    assert isinstance(value.function, FieldAccess)
    assert isinstance(value.function.target, Pipeline)


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
def test_stage_postfix_runs_on_every_backend() -> None:
    _assert_expected_backend_outputs(
        label="pipeline stage postfix",
        context=SOURCE,
        expected=EXPECTED,
        interp_out=_interpreter_output(SOURCE),
        py_out=_compiled_python_output(SOURCE),
        js_out=_compiled_js_output(SOURCE),
    )


def test_selfhost_parser_applies_the_index(tmp_path) -> None:
    from geno.tests.test_cli import _run_selfhost_cli

    program = tmp_path / "p.geno"
    program.write_text(
        "func pair(n: Int) -> List[Int]\n"
        "    example 1 -> [1, 2]\n"
        "    return [n, n + 1]\n"
        "end func\n"
        "\n"
        "func adders(n: Int) -> List[(Int) -> Int]\n"
        "    example 1 -> [fn(k: Int) -> k + 1]\n"
        "    return [fn(k: Int) -> k + n]\n"
        "end func\n"
        "\n"
        "func main() -> Unit\n"
        "    let x: Int = 5 |> pair[1]\n"
        "    print(x)\n"
        "    let y: Int = 5 |> adders[0](1)\n"
        "    print(y)\n"
        "end func\n"
    )
    result = _run_selfhost_cli("run", str(program))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-2:] == ["6", "6"]
