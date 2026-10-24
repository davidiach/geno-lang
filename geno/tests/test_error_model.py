"""One error model: Result, ``?`` and ``panic`` (proposal 0009)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from geno.api import RunConfig, run
from geno.diagnostics import ErrorCode
from geno.parser import ParseError, ParseErrors, parse
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)
from geno.typechecker import TypeChecker
from geno.types import TypeError as GenoTypeError


def _check(source: str) -> None:
    TypeChecker().check_program(parse(source))


def _parse_errors(source: str) -> list[ParseError]:
    with pytest.raises((ParseError, ParseErrors)) as info:
        parse(source)
    return list(getattr(info.value, "errors", [info.value]))


def _main(body: str, returns: str = "Unit") -> str:
    return f"func main() -> {returns}\n" + body + "end func\n"


def _assert_parity(source: str, expected: str) -> None:
    _assert_expected_backend_outputs(
        label="error model",
        context=source,
        expected=expected,
        interp_out=_interpreter_output(source),
        py_out=_compiled_python_output(source),
        js_out=_compiled_js_output(source) if HAS_NODE else expected,
    )


def _failure_messages(source: str) -> tuple[str, str, str]:
    """The failure text of *source* on the interpreter, Python and JS."""
    result = run(source, config=RunConfig(timeout=10.0, capabilities={"print"}))
    assert not result.ok
    interp = " ".join(d.message for d in result.diagnostics)
    with pytest.raises(RuntimeError) as py_failure:
        _compiled_python_output(source)
    js = ""
    if HAS_NODE:
        with pytest.raises(RuntimeError) as js_failure:
            _compiled_js_output(source)
        js = str(js_failure.value)
    return interp, str(py_failure.value), js


# ---------------------------------------------------------------------------
# Removed forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        (_main('    throw "boom"\n'), "'throw' was removed in Geno 0.6"),
        (
            _main(
                "    try\n"
                "        print(1)\n"
                "    catch e: String\n"
                "        print(e)\n"
                "    end try\n"
            ),
            "'try'/'catch' was removed in Geno 0.6",
        ),
        (
            "func f() -> Unit with throw\n    return ()\nend func\n",
            "The 'throw' effect was removed in Geno 0.6",
        ),
    ],
)
def test_removed_forms_report_one_error_naming_the_replacement(
    source: str, fragment: str
) -> None:
    errors = _parse_errors(source + _main("    print(2)\n").replace("main", "other"))
    assert len(errors) == 1
    assert fragment in errors[0].message
    assert errors[0].error_code == ErrorCode.PARSE_REMOVED_SYNTAX


def test_recovery_skips_the_func_of_end_func() -> None:
    errors = _parse_errors(
        "func a() -> Int\n"
        "    let = 1\n"
        "end func\n"
        "func b() -> Int\n"
        "    let = 2\n"
        "end func\n"
    )
    assert [error.location.line for error in errors] == [2, 5]


# ---------------------------------------------------------------------------
# panic
# ---------------------------------------------------------------------------


def test_panic_fits_any_type_and_ends_a_path() -> None:
    _check(
        "func pick(xs: List[Int]) -> Int\n"
        "    example [1] -> 1\n"
        "    match xs.first() with\n"
        "        | Some(x) -> return x\n"
        '        | None -> panic("empty")\n'
        "    end match\n"
        "end func\n"
        "func half(n: Int) -> Int\n"
        "    example 4 -> 2\n"
        "    if n % 2 == 0 then\n"
        "        return n / 2\n"
        "    end if\n"
        '    panic("odd")\n'
        "end func\n"
        + _main('    let x: String = if true then "a" else panic("no") end if\n')
    )


def test_panic_takes_a_string() -> None:
    with pytest.raises(GenoTypeError, match="type mismatch"):
        _check(_main("    panic(42)\n"))


def test_panic_message_is_the_failure_on_every_backend() -> None:
    source = _main('    print("before")\n    panic("disk on fire")\n')
    for message in _failure_messages(source):
        assert "disk on fire" in message
    result = run(source, config=RunConfig(capabilities={"print"}))
    assert result.output == "before\n"
    assert result.diagnostics[0].code == ErrorCode.RUNTIME_PANIC
    assert result.diagnostics[0].message == "disk on fire"


# ---------------------------------------------------------------------------
# Total accessors and explicit unwrapping
# ---------------------------------------------------------------------------


def test_total_accessors_and_unwrapping_agree_across_backends() -> None:
    _assert_parity(
        _main(
            "    let xs = [10, 20, 30]\n"
            "    let empty: List[Int] = []\n"
            "    print(xs.get(1))\n"
            "    print(xs.get(-1))\n"
            "    print(xs.get(3))\n"
            "    print(get_or(xs, 9))\n"
            "    print(xs.first())\n"
            "    print(xs.last())\n"
            "    print(empty.first())\n"
            "    print(empty.last())\n"
            "    let ok: Result[Int, String] = Ok(7)\n"
            "    print(ok.unwrap())\n"
            '    print(ok.expect("needed"))\n'
            '    print(Some(3).expect("needed"))\n'
        ).replace(
            "func main",
            "func get_or(xs: List[Int], i: Int) -> Int\n"
            "    example [1], 0 -> 1\n"
            "    return xs.get(i).unwrap_or(-1)\n"
            "end func\n"
            "func main",
        ),
        "Some(value: 20)\nSome(value: 30)\nNone\n-1\nSome(value: 10)\n"
        "Some(value: 30)\nNone\nNone\n7\n7\n3\n",
    )


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        (
            'let r: Result[Int, String] = Err("disk full")\n    print(r.unwrap())',
            "unwrap called on Err: disk full",
        ),
        (
            'let r: Result[Int, String] = Err("disk full")\n'
            '    print(r.expect("saving"))',
            "saving: disk full",
        ),
        (
            'let o: Option[Int] = None\n    print(o.expect("no config"))',
            "no config",
        ),
    ],
)
def test_unwrapping_a_failure_panics_with_its_message(
    statement: str, message: str
) -> None:
    for failure in _failure_messages(_main(f"    {statement}\n")):
        if failure:
            assert message in failure


# ---------------------------------------------------------------------------
# Result-returning host builtins
# ---------------------------------------------------------------------------

READ_CONFIG = """
@untested("reads the filesystem")
func load(path: String) -> Result[Int, String] with fs
    let text = fs_read_text(path)?
    return Ok(length(text))
end func

func main() -> Unit with fs, io
    match load("present.txt") with
        | Ok(n) -> print(n)
        | Err(e) -> print("unexpected: " + e)
    end match
    match load("missing.txt") with
        | Ok(n) -> print(n)
        | Err(_) -> print("missing")
    end match
    print(fs_write_text("out.txt", "written").is_ok())
end func
"""


def test_fs_builtins_return_results_in_the_interpreter(tmp_path: Path) -> None:
    import subprocess
    import sys

    (tmp_path / "present.txt").write_text("hello")
    (tmp_path / "prog.geno").write_text(READ_CONFIG)
    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", "--unsafe", "--cap", "fs,print"]
        + ["prog.geno"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2])},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["5", "missing", "true"]
    assert (tmp_path / "out.txt").read_text() == "written"


def test_fs_builtins_return_results_in_the_compiled_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from geno import _runtime_support as rs

    monkeypatch.setattr(rs, "_GENO_CAPS", {"fs"})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "present.txt").write_text("hello")
    assert rs.fs_read_text("present.txt") == rs.Ok("hello")
    assert isinstance(rs.fs_read_text("missing.txt"), rs.Err)
    assert rs.fs_write_text("out.txt", "written") == rs.Ok(None)
    assert (tmp_path / "out.txt").read_text() == "written"


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
def test_fs_builtins_return_results_in_compiled_javascript(tmp_path: Path) -> None:
    import subprocess

    from geno.js_compiler import compile_to_js

    (tmp_path / "present.txt").write_text("hello")
    script = tmp_path / "prog.js"
    js = compile_to_js(READ_CONFIG)
    assert isinstance(js, str)
    script.write_text(js)
    result = subprocess.run(
        ["node", str(script), "--cap", "fs,print"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["5", "missing", "true"]


def test_io_builtins_have_result_types() -> None:
    with pytest.raises(GenoTypeError, match="Result\\[String, String\\]"):
        _check(
            "func f(p: String) -> String with fs\n"
            '    example "x" -> "x"\n'
            "    return fs_read_text(p)\n"
            "end func\n"
        )
