"""W300: a let/var that redeclares a mutable var warns but still passes."""

from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

import geno
from geno.__main__ import main as geno_main
from geno.diagnostics import ErrorCode, Severity
from geno.lexer import Lexer
from geno.parser import Parser
from geno.project_graph import ResolvedFile
from geno.var_shadowing import find_redeclared_vars, project_warnings

STD_DIR = Path(geno.__file__).resolve().parent / "std"


def _warnings(source: str):
    """Check ``source`` and return its W300 diagnostics; it must pass."""
    result = geno.check(textwrap.dedent(source))
    assert result.ok, [str(d) for d in result.diagnostics]
    return [d for d in result.diagnostics if d.code is ErrorCode.WARN_REDECLARED_VAR]


def _parse(source: str, filename: str = "<test>"):
    tokens = Lexer(textwrap.dedent(source), filename).tokenize()
    return Parser(tokens).parse_program()


# The shape of the geno-form bug: a var redeclared inside a loop body.
LOOP_SOURCE = """\
    func total(xs: List[Int]) -> Int
        example [1, 2, 3] -> 0
        var sum: Int = 0
        for x: Int in xs do
            var sum: Int = sum + x
        end for
        return sum
    end func
    """

WARNING_CASES = {
    "var in a loop body": (LOOP_SOURCE, [5]),
    "var in an if branch": (
        """\
        func pick(active: Bool) -> String
            example true -> "border"
            var color: String = "border"
            if active then
                var color: String = "button"
            end if
            return color
        end func
        """,
        [5],
    ),
    "let in a while body": (
        """\
        func steps(n: Int) -> Int
            example 3 -> 0
            var count: Int = 0
            var i: Int = 0
            while i < n do
                let count: Int = count + 1
                i = i + 1
            end while
            return count
        end func
        """,
        [6],
    ),
    "var twice in one block": (
        """\
        func twice() -> Int
            example () -> 2
            var x: Int = 1
            var x: Int = 2
            return x
        end func
        """,
        [4],
    ),
    "var in an else-if branch": (
        """\
        func grade(n: Int) -> Int
            example 1 -> 0
            var r: Int = 0
            if n > 5 then
                r = 1
            else if n > 2 then
                var r: Int = 2
            end if
            return r
        end func
        """,
        [7],
    ),
    "var in a match arm": (
        """\
        func unwrap(o: Option[Int]) -> Int
            example Some(4) -> 0
            var total: Int = 0
            match o with
                | Some(v) ->
                    var total: Int = v
                | None -> total = 1
            end match
            return total
        end func
        """,
        [6],
    ),
    "var in try and catch bodies": (
        """\
        func safe(n: Int) -> Int
            example 2 -> 0
            var out: Int = 0
            try
                var out: Int = 10 / n
            catch e: String
                var out: Int = -1
            end try
            return out
        end func
        """,
        [5, 7],
    ),
    "var in a block lambda": (
        """\
        func seen(xs: List[Int]) -> Int
            example [1] -> 0
            var last: Int = 0
            let f = fn(x: Int) do
                var last: Int = x
                return last
            end fn
            return last + f(0)
        end func
        """,
        [5],
    ),
    "tuple destructuring": (
        """\
        func pair() -> Int
            example () -> 3
            var a: Int = 1
            if true then
                var (a, b): (Int, Int) = (2, 1)
            end if
            return a + 2
        end func
        """,
        [5],
    ),
    "test block": (
        """\
        func one() -> Int
            example () -> 1
            return 1
        end func

        test "redeclares"
            var n: Int = one()
            if n > 0 then
                var n: Int = 2
            end if
            assert n == 1
        end test
        """,
        [9],
    ),
}


@pytest.mark.parametrize("name", sorted(WARNING_CASES))
def test_redeclared_var_warns(name):
    source, lines = WARNING_CASES[name]
    warnings = _warnings(source)
    assert [w.location.line for w in warnings] == lines
    for warning in warnings:
        assert warning.severity is Severity.WARNING
        assert warning.code.value == "W300"


def test_message_names_the_original_declaration():
    (warning,) = _warnings(LOOP_SOURCE)
    assert warning.message == (
        "'var sum' declares a new variable that hides the mutable 'sum' from "
        "line 3, which keeps its old value. Write 'sum = ...' to update it, "
        "or choose a different name"
    )
    assert warning.location.column == 9


@pytest.mark.parametrize(
    ("declaration", "keyword"),
    [
        ("var x: Int = 2", "var"),
        ("let x: Int = 2", "let"),
        ("var (x, y): (Int, Int) = (2, 0)", "var"),
        ("let (x, y): (Int, Int) = (2, 0)", "let"),
    ],
)
def test_same_block_message_describes_live_rebinding(declaration, keyword):
    source = (
        "func main() -> Int\n"
        "    var x: Int = 1\n"
        "    let read = fn() -> x\n"
        f"    {declaration}\n"
        "    return read()\n"
        "end func\n"
    )
    (warning,) = _warnings(source)
    assert warning.message == (
        f"'{keyword} x' rebinds the mutable 'x' from line 2 in the same block. "
        "Existing closures observe its new value. Write 'x = ...' to update "
        "it without redeclaring, or choose a different name"
    )
    assert warning.location.line == 4
    result = geno.run(source)
    assert result.ok, result.diagnostics
    assert result.value == 2


def test_default_parameter_lambda_warns_once():
    source = """\
func use(f: () -> Int with mutation = fn() do
    var x: Int = 0
    if true then
        var x: Int = 1
    end if
    return x
end fn) -> Int
    example () -> 2
    var x: Int = 2
    return f() + x
end func
func main() -> Int
    return use()
end func
"""
    (warning,) = _warnings(source)
    assert warning.location.line == 4
    assert "from line 2, which keeps its old value" in warning.message
    result = geno.run(source)
    assert result.ok, result.diagnostics
    assert result.value == 2


QUIET_CASES = {
    "let rebinding a parameter": """\
        func bump(n: Int) -> Int
            example 1 -> 2
            let n: Int = n + 1
            return n
        end func
        """,
    "let shadowing a let": """\
        func inner(flag: Bool) -> Int
            example true -> 1
            let x: Int = 1
            if flag then
                let x: Int = 2
                print(to_string(x))
            end if
            return x
        end func
        """,
    "var shadowing a let": """\
        func frozen(flag: Bool) -> Int
            example true -> 1
            let x: Int = 1
            if flag then
                var x: Int = 2
                x = x + 1
            end if
            return x
        end func
        """,
    "sibling blocks": """\
        func sides(flag: Bool) -> Int
            example true -> 1
            if flag then
                var t: Int = 1
                return t
            else
                var t: Int = 2
                return t
            end if
        end func
        """,
    "assignment updates the var": """\
        func total(xs: List[Int]) -> Int
            example [1, 2, 3] -> 6
            var sum: Int = 0
            for x: Int in xs do
                sum = sum + x
            end for
            return sum
        end func
        """,
    "let shadowing the loop variable": """\
        func shifted(xs: List[Int]) -> Int
            example [1] -> 0
            var x: Int = 0
            for x: Int in xs do
                let x: Int = x + 1
                print(to_string(x))
            end for
            return x
        end func
        """,
    "let shadowing a pattern binding": """\
        func value(o: Option[Int]) -> Int
            example Some(1) -> 2
            var v: Int = 0
            match o with
                | Some(v) ->
                    let v: Int = v + 1
                    return v
                | None -> return v
            end match
        end func
        """,
    "same name in different functions": """\
        func a() -> Int
            example () -> 1
            var n: Int = 1
            return n
        end func

        func b() -> Int
            example () -> 2
            var n: Int = 2
            return n
        end func
        """,
}


@pytest.mark.parametrize("name", sorted(QUIET_CASES))
def test_other_shadowing_is_quiet(name):
    assert _warnings(QUIET_CASES[name]) == []


def test_impl_methods_are_checked():
    program = _parse(
        """\
        trait Sized
            func size(self: Self) -> Int
        end trait

        type Box = Box(n: Int)

        impl Sized for Box
            func size(self: Box) -> Int
                example Box(1) -> 1
                var s: Int = 1
                if true then
                    var s: Int = 2
                end if
                return s
            end func
        end impl
        """
    )
    warnings = find_redeclared_vars(program)
    assert [w.location.line for w in warnings if w.location] == [12]
    assert len(warnings) == 1


def test_type_errors_suppress_warnings():
    result = geno.check(
        textwrap.dedent(
            """\
            func total(xs: List[Int]) -> Int
                example [1, 2, 3] -> 0
                var sum: Int = 0
                for x: Int in xs do
                    var sum: Int = sum + x
                end for
                return "sum"
            end func
            """
        )
    )
    assert not result.ok
    assert all(d.severity is Severity.ERROR for d in result.diagnostics)


def test_project_warnings_skip_std_and_dependencies(tmp_path):
    buggy = textwrap.dedent(LOOP_SOURCE)
    paths = {
        "Main": tmp_path / "Main.geno",
        "Dep": tmp_path / "deps" / "Dep.geno",
        "list": STD_DIR / "list.geno",
    }
    graph = SimpleNamespace(
        sorted_modules=["list", "Dep", "Main"],
        file_map={
            "Main": ResolvedFile(module_name="Main", path=paths["Main"]),
            "Dep": ResolvedFile(
                module_name="Dep",
                path=paths["Dep"],
                is_dependency=True,
                package_name="dep",
            ),
            "list": ResolvedFile(module_name="list", path=paths["list"]),
        },
        parsed={name: _parse(buggy, str(path)) for name, path in paths.items()},
    )
    warnings = project_warnings(graph)
    assert [w.location.filename for w in warnings if w.location] == [str(paths["Main"])]
    assert len(warnings) == 1


def _write_project(root: Path) -> Path:
    (root / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main", "Utils"]\n')
    (root / "Main.geno").write_text(
        "import Utils\nfunc main() -> Int\n    return total(xs: [1, 2])\nend func\n"
    )
    (root / "Utils.geno").write_text("export " + textwrap.dedent(LOOP_SOURCE))
    return root / "Utils.geno"


def test_check_path_reports_project_module_warnings(tmp_path):
    utils = _write_project(tmp_path)
    result = geno.check_path(str(tmp_path / "Main.geno"))
    assert result.ok, [str(d) for d in result.diagnostics]
    assert [
        (d.code.value, d.location.filename, d.location.line) for d in result.diagnostics
    ] == [("W300", str(utils), 5)]


def test_cli_check_prints_warning_and_passes(tmp_path, capsys):
    path = tmp_path / "loop.geno"
    path.write_text(textwrap.dedent(LOOP_SOURCE))
    try:
        geno_main(["check", str(path)])
    except SystemExit as exc:
        assert exc.code in (0, None)
    out, err = capsys.readouterr()
    assert f"Warning: {path}:5:9: 'var sum' declares a new variable" in err
    assert err.rstrip().endswith("^")
    assert "[W300]" in err
    assert "        var sum: Int = sum + x" in err
    assert "Type check passed: " in out
    assert out.rstrip().endswith("1 warning")


def test_cli_check_without_warnings_is_unchanged(tmp_path, capsys):
    path = tmp_path / "clean.geno"
    path.write_text(textwrap.dedent(QUIET_CASES["assignment updates the var"]))
    try:
        geno_main(["check", str(path)])
    except SystemExit as exc:
        assert exc.code in (0, None)
    out, err = capsys.readouterr()
    assert err == ""
    assert out.splitlines()[-1] == "  1 definitions, 1 modules"


def test_caller_module_named_like_std_still_warns():
    """A supplied module is skipped only when it is the shipped std source."""
    entry = "func main() -> Int\n    return 0\nend func\n"
    buggy = "export " + textwrap.dedent(LOOP_SOURCE)
    result = geno.check(entry, modules={"Math": buggy})
    assert result.ok, [str(d) for d in result.diagnostics]
    assert [
        (d.code.value, d.location.filename if d.location else None)
        for d in result.diagnostics
    ] == [("W300", "<module:Math>")]

    shipped = (STD_DIR / "Math.geno").read_text(encoding="utf-8")
    module = geno.api._parse_modules({"Math": shipped})["Math"]
    assert module.location.filename == str(STD_DIR / "Math.geno")


def test_std_path_check_ignores_in_memory_names():
    from geno.var_shadowing import is_std_module_path

    assert is_std_module_path(STD_DIR / "Math.geno")
    assert not is_std_module_path("<module:Math>")
    assert not is_std_module_path("")
