"""Match-expression arms may build different variants of a generic type (#134)."""

from __future__ import annotations

import pytest

from geno.api import RunConfig, run
from geno.parser import parse
from geno.typechecker import TypeChecker
from geno.types import TypeError as GenoTypeError


def _check(source: str) -> None:
    TypeChecker().check_program(parse(source))


@pytest.mark.parametrize(
    ("arms", "binding"),
    [
        ('| 0 -> Err("zero")\n        | _ -> Ok(n)', "let r: Result[Int, String] ="),
        ('| 0 -> Err("zero")\n        | _ -> Ok(n)', "let r ="),
        ('| 0 -> Ok(n)\n        | _ -> Err("zero")', "let r ="),
        ("| 0 -> Ok(0)\n        | _ -> Ok(n)", "let r: Result[Int, String] ="),
        ('| 0 -> Err("a")\n        | 1 -> Ok(1)\n        | _ -> Err("b")', "let r ="),
    ],
)
def test_result_arms_unify(arms: str, binding: str) -> None:
    _check(
        "func classify(n: Int) -> Result[Int, String]\n"
        "    example 1 -> Ok(1)\n"
        f"    {binding} match n with\n"
        f"        {arms}\n"
        "    end match\n"
        "    return r\n"
        "end func\n"
    )


def test_returned_option_arms_unify_and_run() -> None:
    source = (
        "func classify(n: Int) -> Result[Int, String]\n"
        "    example 1 -> Ok(1)\n"
        "    return match n with\n"
        '        | 0 -> Err("zero")\n'
        "        | _ -> Ok(n)\n"
        "    end match\n"
        "end func\n"
        "\n"
        "func opt(n: Int) -> Option[Int]\n"
        "    example 1 -> Some(1)\n"
        "    return match n with\n"
        "        | 0 -> None\n"
        "        | _ -> Some(n)\n"
        "    end match\n"
        "end func\n"
        "\n"
        "func main() -> Unit\n"
        "    print(classify(0))\n"
        "    print(opt(0))\n"
        "end func\n"
    )
    result = run(source, config=RunConfig(capabilities={"print"}))
    assert result.ok, [d.message for d in result.diagnostics]
    assert result.output == 'Err(error: "zero")\nNone\n'


@pytest.mark.parametrize(
    ("arms", "message"),
    [
        (
            "| 0 -> Ok(1)\n        | _ -> Some(2)",
            "expected Result[Int, _], got Option[Int]",
        ),
        (
            '| 0 -> Err("a")\n        | _ -> Err(2)',
            "expected Result[_, String], got Result[_, Int]",
        ),
    ],
)
def test_real_mismatches_are_still_reported_without_internal_names(
    arms: str, message: str
) -> None:
    source = (
        "func f(n: Int) -> Int\n"
        "    example 1 -> 1\n"
        "    let r = match n with\n"
        f"        {arms}\n"
        "    end match\n"
        "    return 1\n"
        "end func\n"
    )
    with pytest.raises(GenoTypeError) as info:
        _check(source)
    assert message in str(info.value)
    assert "__fresh" not in str(info.value)


@pytest.mark.parametrize(
    "arms",
    [
        '| 0 -> Ok(1)\n        | 1 -> Err("x")\n        | _ -> Ok(2.5)',
        '| 0 -> Ok(2.5)\n        | 1 -> Err("x")\n        | _ -> Ok(1)',
        '| 0 -> Err("x")\n        | 1 -> Ok(1)\n        | _ -> Ok(2.5)',
    ],
    ids=["int-first", "float-first", "err-first"],
)
def test_numeric_widening_does_not_depend_on_arm_order(arms: str) -> None:
    _check(
        "func classify(n: Int) -> Result[Float, String]\n"
        "    example 0 -> Ok(1.0)\n"
        "    let r = match n with\n"
        f"        {arms}\n"
        "    end match\n"
        "    return r\n"
        "end func\n"
    )


WIDENED_PROGRAM = """func classify(n: Int) -> Result[Float, String]
    example 2 -> Ok(2.5)
    let r = match n with
        | 0 -> Ok(1)
        | 1 -> Err("x")
        | _ -> Ok(2.5)
    end match
    return r
end func

func main() -> Unit
    print(classify(0))
end func
"""


def test_widened_arm_builds_a_float_on_every_backend(tmp_path) -> None:
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from geno.js_compiler import compile_to_js

    repo_root = Path(__file__).resolve().parents[2]
    program = tmp_path / "prog.geno"
    program.write_text(WIDENED_PROGRAM, encoding="utf-8")
    expected = "Ok(value: 1.0)\n"

    result = run(WIDENED_PROGRAM, config=RunConfig(capabilities={"print"}))
    assert result.output == expected
    sandboxed = subprocess.run(
        [sys.executable, "-m", "geno", "run", str(program)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert sandboxed.stdout == expected
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    js_path = tmp_path / "prog.js"
    js_code = compile_to_js(WIDENED_PROGRAM)
    assert isinstance(js_code, str)
    js_path.write_text(js_code, encoding="utf-8")
    node_run = subprocess.run(
        [node, str(js_path), "--cap", "print"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert node_run.stdout == expected
