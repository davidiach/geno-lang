"""A coherent standard library and ``geno migrate`` (proposal 0010)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from geno.api import RunConfig, run
from geno.diagnostics import ErrorCode
from geno.migrate import MigrationError, migrate_source
from geno.parser import parse
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_js_project_output,
    _compiled_python_output,
    _compiled_python_project_output,
    _interpreter_output,
    _interpreter_project_output,
)
from geno.typechecker import TypeChecker
from geno.types import TypeError as GenoTypeError

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _main(body: str, imports: str = "") -> str:
    return imports + "func main() -> Unit\n" + body + "end func\n"


def _errors(source: str) -> list[GenoTypeError]:
    with pytest.raises(GenoTypeError) as info:
        TypeChecker().check_program(parse(source))
    return list(getattr(info.value, "errors", [info.value]))


def _assert_parity(source: str, expected: str) -> None:
    _assert_expected_backend_outputs(
        label="coherent stdlib",
        context=source,
        expected=expected,
        interp_out=_interpreter_output(source),
        py_out=_compiled_python_output(source),
        js_out=_compiled_js_output(source) if HAS_NODE else expected,
    )


# ---------------------------------------------------------------------------
# Standard-library imports and free calls
# ---------------------------------------------------------------------------


def test_importing_the_stdlib_keeps_builtins_and_qualified_names() -> None:
    """Standard-library modules resolve from `geno/std`, as `geno run` does."""
    source = _main(
        '    print(length("abc"))\n'
        "    print(length([1, 2]))\n"
        "    print(List.length([1, 2, 3]))\n"
        '    print(trim("  a "))\n'
        '    print(String.trim("  b "))\n'
        "    print(map([1, 2], fn(x) -> x + 1))\n",
        imports="import List\nimport String\nimport Option\n",
    )
    expected = "3\n2\n3\na\nb\n[2, 3]\n"
    _assert_expected_backend_outputs(
        label="stdlib imports",
        context=source,
        expected=expected,
        interp_out=_interpreter_project_output(source, {}),
        py_out=_compiled_python_project_output(source, {}),
        js_out=_compiled_js_project_output(source, {}) if HAS_NODE else expected,
    )
    # The embedding API resolves the standard library too.
    assert _interpreter_output(source) == expected


def test_a_free_call_resolves_like_the_method_it_spells() -> None:
    _assert_parity(
        _main(
            "    let o = Some(2)\n"
            "    let r: Result[Int, String] = Ok(1)\n"
            "    print(map(o, fn(x) -> x * 10))\n"
            "    print(map(r, fn(x) -> x + 1))\n"
            "    print(unwrap_or(r, 0))\n"
            '    print(length({"a": 1}))\n'
            "    print(length(set_from_list([1, 1, 2])))\n"
        ),
        "Some(value: 20)\nOk(value: 2)\n1\n1\n2\n",
    )


def test_a_program_that_checked_before_keeps_its_meaning() -> None:
    """A user function of a builtin's name is still called when it accepts."""
    _assert_parity(
        "func length(xs: List[Int]) -> Int\n"
        "    example [1] -> 100\n"
        "    return 100\n"
        "end func\n" + _main("    print(length([1, 2]))\n"),
        "100\n",
    )


def test_a_rejected_free_call_still_reports_the_mismatch() -> None:
    errors = _errors(_main('    print(map("text", fn(x: Int) -> x))\n'))
    assert "Argument 1 type mismatch" in errors[0].message


def test_errors_inside_the_first_argument_are_reported() -> None:
    errors = _errors(_main("    print(length([1].lenght()))\n"))
    assert any("has no method 'lenght'" in error.message for error in errors)


# ---------------------------------------------------------------------------
# Uniform collection API
# ---------------------------------------------------------------------------


def test_every_collection_has_length_and_maps_are_complete() -> None:
    _assert_parity(
        _main(
            '    let ages = {"ada": 36, "alan": 41}\n'
            "    print(ages.length())\n"
            '    print(ages.contains_key("ada"))\n'
            '    print(ages.contains_key("bob"))\n'
            '    print(ages.remove("ada"))\n'
            "    print(ages)\n"
            "    print(ages.keys())\n"
            "    print(ages.values())\n"
            "    print(set_from_list([1, 2, 2]).length())\n"
            "    let v: Vec[Int] = vec_from_list([1, 2, 3])\n"
            "    print(v.length())\n"
            "    let m: MutableMap[String, Int] = mutable_map_new()\n"
            '    m.set(key: "k", value: 1)\n'
            "    print(m.length())\n"
        ),
        '2\ntrue\nfalse\n{"alan": 41}\n{"ada": 36, "alan": 41}\n'
        '["ada", "alan"]\n[36, 41]\n2\n3\n1\n',
    )


def test_index_of_returns_an_option() -> None:
    _assert_parity(
        _main(
            '    print("hello world".index_of("world"))\n'
            '    print("hello".index_of("z"))\n'
            '    print("abcabc".last_index_of("bc"))\n'
            '    print(string_index_of("abc", "c").unwrap_or(-1))\n'
        ),
        "Some(value: 6)\nNone\nSome(value: 4)\n2\n",
    )


def test_sort_without_a_comparator_uses_the_natural_order() -> None:
    _assert_parity(
        _main(
            "    print([3, 1, 2].sort())\n"
            '    print(sort(["pear", "apple", "Fig"]))\n'
            "    print([3, 1, 2].sort(fn(a, b) -> b - a))\n"
            '    print([(2, "b"), (1, "z"), (2, "a")].sort())\n'
            "    print([1.5, 0.5].sort())\n"
        ),
        '[1, 2, 3]\n["Fig", "apple", "pear"]\n[3, 2, 1]\n'
        '[(1, "z"), (2, "a"), (2, "b")]\n[0.5, 1.5]\n',
    )


def test_sort_without_a_comparator_needs_a_list() -> None:
    errors = _errors(_main("    print(sort(3))\n"))
    assert errors[0].message == "Argument 1 type mismatch: expected List[T], got Int"


@pytest.mark.parametrize(
    ("call", "replacement"),
    [
        ("square(3)", "write x * x"),
        ('repeat_string("a", 2)', "write text.repeat(count)"),
        ('contains_substring("ab", "b")', "write text.contains(substring)"),
        ('sort_strings(["b"])', "write items.sort()"),
        ("is_sorted([1])", "write xs == xs.sort()"),
        ('is_numeric_string("1")', "write parse_int(text).is_some()"),
        ("is_permutation([1], [1])", "write a.sort() == b.sort()"),
        ("is_positive(1)", "write n > 0"),
    ],
)
def test_removed_builtins_name_their_replacement(call: str, replacement: str) -> None:
    errors = _errors(_main(f"    print({call})\n"))
    assert errors[0].error_code == ErrorCode.TYPE_UNDEFINED_FUNC
    assert "was removed in Geno 0.6" in errors[0].message
    assert replacement in errors[0].message


def test_a_program_may_define_a_removed_builtins_name() -> None:
    source = (
        "func square(x: Int) -> Int\n"
        "    example 3 -> 9\n"
        "    return x * x\n"
        "end func\n" + _main("    print(square(4))\n")
    )
    assert run(source, config=RunConfig(capabilities={"print"})).output == "16\n"


# ---------------------------------------------------------------------------
# geno migrate
# ---------------------------------------------------------------------------

LEGACY = """import String

func load(path: String) -> String with fs, throw
    if path == "" then
        throw "empty path" // keep this comment
    end if
    let text: String = fs_read_text(path)
    fs_write_text(path: "out.txt", content: text)
    return text
end func

func risky() -> Int with throw
    example () -> 1
    throw AppError("bad")
end func

func find(s: String) -> Int
    example "abc" -> 1
    let a = string_index_of(s, "b")
    let b = String.last_index_of(s, "c")
    return a + b
end func

func main() -> Unit
    try
        print(load("x"))
    catch e: String
        print(e)
    end try
    print(square(3))
    print(square(1 + 2))
    print(is_positive(n - 1))
    print(repeat_string("ab", 2))
    print(is_permutation([1, 2], [2, 1]))
    print(map([1], square))
    print(is_numeric_string("42"))
end func
"""

MIGRATED = """import String

func load(path: String) -> String with fs
    if path == "" then
        panic("empty path") // keep this comment
    end if
    let text: String = fs_read_text(path).unwrap()
    fs_write_text(path: "out.txt", content: text).unwrap()
    return text
end func

func risky() -> Int
    example () -> 1
    throw AppError("bad")
end func

func find(s: String) -> Int
    example "abc" -> 1
    let a = string_index_of(s, "b").unwrap_or(-1)
    let b = String.last_index_of(s, "c").unwrap_or(-1)
    return a + b
end func

func main() -> Unit
    try
        print(load("x"))
    catch e: String
        print(e)
    end try
    print((3 * 3))
    print(square(1 + 2))
    print(((n - 1) > 0))
    print(string_repeat("ab", 2))
    print((sort([1, 2]) == sort([2, 1])))
    print(map([1], square))
    print(is_some(parse_int("42")))
end func
"""


def test_migrate_rewrites_what_is_mechanical() -> None:
    result = migrate_source(LEGACY)
    assert result.source == MIGRATED
    assert len(result.changes) == 12


def test_migrate_reports_what_needs_a_person() -> None:
    manual = migrate_source(LEGACY).manual
    assert [note.line for note in manual] == [14, 25, 31, 35]
    assert "try/catch" in manual[1].message
    assert "Err(...)" in manual[0].message


def test_migrate_is_idempotent() -> None:
    once = migrate_source(LEGACY).source
    assert migrate_source(once).source == once


@pytest.mark.parametrize(
    "line",
    [
        "    match fs_read_text(p) with\n        | Ok(t) -> return 1\n"
        "        | Err(_) -> return 0\n    end match\n",
        "    let r: Result[String, String] = fs_read_text(p)\n    return 1\n",
        '    let i: Option[Int] = string_index_of(p, "x")\n    return 1\n',
    ],
)
def test_migrate_leaves_results_that_are_already_handled(line: str) -> None:
    source = "func f(p: String) -> Int with fs\n" + line + "end func\n"
    assert migrate_source(source).source == source


def test_migrate_leaves_a_function_returning_the_result() -> None:
    source = (
        "func f(p: String) -> Result[String, String] with fs\n"
        "    return fs_read_text(p)\n"
        "end func\n"
    )
    assert migrate_source(source).source == source


def test_migrated_code_checks_under_0_6() -> None:
    source = (
        "func f(p: String) -> String with fs, throw\n"
        '    example "a" -> "a"\n'
        '    if p == "" then\n'
        '        throw "empty"\n'
        "    end if\n"
        "    return fs_read_text(p)\n"
        "end func\n"
    )
    TypeChecker().check_program(parse(migrate_source(source).source))


def test_migrate_refuses_source_it_cannot_tokenize() -> None:
    with pytest.raises(MigrationError):
        migrate_source('func f() -> Unit\n    print("unterminated)\nend func\n')


def test_migrate_command(tmp_path: Path) -> None:
    app = tmp_path / "App.geno"
    app.write_text(LEGACY, encoding="utf-8")

    def geno_migrate(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "geno", "migrate", *args, str(app)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            cwd=_REPO_ROOT,
        )

    checked = geno_migrate("--check")
    assert checked.returncode == 1
    assert "Would migrate" in checked.stdout
    assert app.read_text(encoding="utf-8") == LEGACY

    shown = geno_migrate("--diff")
    assert '+        panic("empty path") // keep this comment' in shown.stdout
    assert app.read_text(encoding="utf-8") == LEGACY

    migrated = geno_migrate()
    assert migrated.returncode == 0
    assert f"{app}:25: needs a manual rewrite: try/catch" in migrated.stdout
    assert app.read_text(encoding="utf-8") == MIGRATED
