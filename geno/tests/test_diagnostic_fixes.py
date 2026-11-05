"""Diagnostics carry the fix a tool can apply (milestone M9, pillar P7)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import geno
from geno.diagnostics import Fix, TextEdit, apply_fix, apply_fixes

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _program(body: str) -> str:
    return (
        "type Point = Pt(x: Int)\n"
        "func main() -> Unit\n"
        "    let x = 2\n"
        f"    {body}\n"
        "end func\n"
    )


@pytest.mark.parametrize(
    ("body", "message", "title"),
    [
        ("let mut y = 1", "no 'let mut'", "Use 'var'"),
        ("let y = True", "Booleans are lowercase", "Change 'True' to 'true'"),
        ('let s = "a" ++ "b"', "'++' is not an operator", "Change '++' to '+'"),
        ("if x > 1\n        print(1)\n    end if", "Expected 'then'", "Add 'then'"),
        ("for z in [1]\n        print(z)\n    end for", "Expected 'do'", "Add 'do'"),
        (
            "if x > 1 then\n        print(1)\n    elif x < 0 then\n"
            "        print(2)\n    end if",
            "'else if', not 'elif'",
            "Change 'elif' to 'else if'",
        ),
        (
            'print(substring("abc", 0, 2))',
            "use named arguments",
            "Name the arguments",
        ),
        ("let n = lenght([1])", "Did you mean 'length'?", "Change 'lenght'"),
        ("let n = [1].lenght()", "Did you mean 'length'?", "Change 'lenght'"),
        ('let t: Strin = "a"', "Did you mean 'String'?", "Change 'Strin'"),
        ("let p = Pnt(1)", "Did you mean 'Pt'?", "Change 'Pnt'"),
        ("print(lengthh)", "Did you mean 'length'?", "Change 'lengthh'"),
        ("print(Pt(1).width)", "Unknown field 'width'", None),
        ("print(Pt(1).xx)", "Did you mean 'x'?", "Change 'xx' to 'x'"),
        (
            'print(contains_substring("ab", "b"))',
            "was removed in Geno 0.6",
            "Change 'contains_substring' to 'string_contains'",
        ),
        ("print(x > 1 && x < 3)", "Geno writes 'and', not '&&'", "Change '&&'"),
        ("print(x > 1 || x < 3)", "Geno writes 'or', not '||'", "Change '||'"),
        ("print(!(x > 1))", "Geno writes 'not', not '!'", "Change '!' to 'not'"),
        ("# a note", "Geno writes '//', not '#'", "Change '#' to '//'"),
        (
            "let n = match Some(x) with\n        | Some(v) => v\n"
            "        | None -> 0\n    end match",
            "Geno writes '->', not '=>'",
            "Change '=>' to '->'",
        ),
        ("let y: Option[Int] = null", "Geno has no 'null'", "Change 'null' to 'None'"),
        ("if x > 1:\n        print(1)\n    end if", "Expected 'then'", "Change ':'"),
        ("for z in [1]:\n        print(z)\n    end for", "Expected 'do'", "Change ':'"),
        (
            "if x > 1 then\n        print(1)\n    else:\n        print(2)\n    end if",
            "'else' with no ':'",
            "Remove ':'",
        ),
        ("if x > 1 then\n        print(1)\n    end", "after 'end'", "Write 'end if'"),
        (
            "print([1].map(lambda v: v + 1))",
            "not 'lambda x: body'",
            "Write 'fn(v) ->'",
        ),
        ("print(len([1]))", "Did you mean 'length'?", "Change 'len' to 'length'"),
        ("print([1].size())", "Did you mean 'length'?", "Change 'size' to 'length'"),
        ("print(str(x))", "Did you mean 'to_string'?", "Change 'str'"),
        ('print("a".upper())', "Did you mean 'to_upper'?", "Change 'upper'"),
        ("print(xx + 1)", "Did you mean 'x'?", "Change 'xx' to 'x'"),
    ],
)
def test_a_diagnostic_carries_a_fix_that_resolves_it(body, message, title) -> None:
    source = _program(body)
    result = geno.check(source)
    assert not result.ok
    diagnostic = result.diagnostics[0]
    assert message in diagnostic.message
    if title is None:
        assert diagnostic.fixes == ()
        return
    assert diagnostic.fixes[0].title.startswith(title)
    fixed = geno.check(apply_fix(source, diagnostic.fixes[0]))
    assert fixed.ok, [d.message for d in fixed.diagnostics]


def test_a_misspelled_method_is_reported_at_its_name() -> None:
    diagnostic = geno.check(_program("print([1].lenght())")).diagnostics[0]
    assert (diagnostic.location.line, diagnostic.location.column) == (4, 15)


def test_fixes_serialize_with_their_diagnostic() -> None:
    diagnostic = geno.check(_program("let n = lenght([1])")).diagnostics[0]
    assert diagnostic.to_dict()["fixes"] == [
        {
            "title": "Change 'lenght' to 'length'",
            "edits": [
                {
                    "start": {"line": 4, "column": 13},
                    "end": {"line": 4, "column": 19},
                    "text": "length",
                }
            ],
        }
    ]


def test_a_mixed_named_and_positional_call_gets_no_fix() -> None:
    diagnostic = geno.check(
        _program('print(substring("abc", start: 0, 2))')
    ).diagnostics[0]
    assert diagnostic.fixes == ()


def test_overlapping_fixes_are_applied_once() -> None:
    first = Fix("a", (TextEdit(1, 1, 1, 4, "xyz"),))
    overlapping = Fix("b", (TextEdit(1, 2, 1, 3, "q"),))
    later = Fix("c", (TextEdit(1, 5, 1, 5, "!"),))
    assert apply_fixes("abc def", [first, overlapping, later]) == ("xyz !def", 2)


def test_geno_check_fix_applies_every_fix(tmp_path: Path) -> None:
    path = tmp_path / "fixme.geno"
    path.write_text(
        "type Point = Pt(x: Int)\n\n"
        "func main() -> Unit\n"
        "    let mut total = 0\n"
        "    let ok = True\n"
        "    if total > 1\n"
        '        print(substring("abc", 0, 2))\n'
        "    end if\n"
        "    print([1].lenght())\n"
        "    total = total + Pnt(1).x\n"
        "end func\n",
        encoding="utf-8",
    )

    def geno_check(*flags: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "geno", "check", *flags, str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            cwd=_REPO_ROOT,
        )

    before = geno_check()
    assert before.returncode == 1
    assert "fix: Use 'var' (`geno check --fix` applies it)" in before.stderr

    fixed = geno_check("--fix")
    assert fixed.returncode == 0, fixed.stderr
    assert "Applied 6 fixes" in fixed.stdout
    assert "Type check passed" in fixed.stdout
    assert path.read_text(encoding="utf-8") == (
        "type Point = Pt(x: Int)\n\n"
        "func main() -> Unit\n"
        "    var total = 0\n"
        "    let ok = true\n"
        "    if total > 1 then\n"
        '        print(substring(text: "abc", start: 0, stop: 2))\n'
        "    end if\n"
        "    print([1].length())\n"
        "    total = total + Pt(1).x\n"
        "end func\n"
    )


def test_a_missing_keyword_is_reported_where_it_belongs() -> None:
    """At the end of the `if` line, not at the first token of the next."""
    diagnostic = geno.check(
        _program("if x > 1\n        print(1)\n    end if")
    ).diagnostics[0]
    assert (diagnostic.location.line, diagnostic.location.column) == (4, 13)


def test_a_bare_end_is_reported_at_the_end() -> None:
    source = _program("if x > 1 then\n        print(1)\n    end")
    diagnostic = geno.check(source).diagnostics[0]
    assert diagnostic.message == (
        "Expected 'if' after 'end': this block closes with 'end if'"
    )
    assert (diagnostic.location.line, diagnostic.location.column) == (6, 5)


def test_end_and_its_keyword_share_a_line() -> None:
    """A bare `end` before a line that starts with `if` is not `end if`."""
    source = _program(
        "if x > 1 then\n        print(1)\n    end\n    if x > 2 then\n"
        "        print(2)\n    end if"
    )
    diagnostic = geno.check(source).diagnostics[0]
    assert "after 'end'" in diagnostic.message
    assert geno.check(apply_fix(source, diagnostic.fixes[0])).ok


def test_a_missing_return_type_is_reported_on_the_signature() -> None:
    source = "func double(n: Int)\n    example 2 -> 4\n    return n * 2\nend func\n"
    diagnostic = geno.check(source).diagnostics[0]
    assert "Function 'double' needs a return type" in diagnostic.message
    assert (diagnostic.location.line, diagnostic.location.column) == (1, 20)
    assert diagnostic.fixes == ()


def test_an_implicit_return_gets_a_return() -> None:
    source = "func double(n: Int) -> Int\n    example 2 -> 4\n    n * 2\nend func\n"
    diagnostic = geno.check(source).diagnostics[0]
    assert "ends with a value it does not return" in diagnostic.message
    assert diagnostic.location.line == 3
    fixed = apply_fix(source, diagnostic.fixes[0])
    assert "    return n * 2\n" in fixed
    assert geno.check(fixed).ok


def test_a_final_expression_of_the_wrong_type_gets_no_return_fix() -> None:
    source = 'func f(n: Int) -> Int\n    example 2 -> 4\n    print("x")\nend func\n'
    diagnostic = geno.check(source).diagnostics[0]
    assert "may not return a value on all paths" in diagnostic.message
    assert diagnostic.fixes == ()


@pytest.mark.parametrize(
    ("body", "hint"),
    [
        # Two parameters one edit away: the hint names one, no fix guesses.
        ("print(ab)", "Did you mean"),
        # Two edits away from a four-letter name: too far to apply blindly.
        ("print(nulx)", ""),
    ],
)
def test_an_ambiguous_correction_carries_no_fix(body: str, hint: str) -> None:
    source = (
        "func f(aa: Int, bb: Int) -> Int\n    example 1, 2 -> 1\n    return aa\n"
        f"end func\n\nfunc main() -> Unit\n    let aa = 1\n    let bb = 2\n    {body}\n"
        "end func\n"
    )
    diagnostic = geno.check(source).diagnostics[0]
    assert hint in diagnostic.message
    assert diagnostic.fixes == ()


def test_a_transposition_is_one_edit() -> None:
    """`lenght` is nearer `length` than `height`, though both are two
    substitutions away."""
    source = (
        "func height(n: Int) -> Int\n    example 1 -> 1\n    return n\nend func\n\n"
        "func main() -> Unit\n    print(lenght([1]))\nend func\n"
    )
    diagnostic = geno.check(source).diagnostics[0]
    assert "Did you mean 'length'?" in diagnostic.message
    assert diagnostic.fixes[0].title == "Change 'lenght' to 'length'"


def test_geno_fix_applies_fixes_until_none_is_left() -> None:
    source = _program("let mut y = True && x > 1\n    print(y)")
    result = geno.fix(source)
    assert result.check.ok, [d.message for d in result.check.diagnostics]
    assert result.applied == 3
    assert "    var y = true and x > 1\n" in result.source


def test_geno_fix_leaves_a_program_without_fixes_alone() -> None:
    source = _program("print(undefined_thing_here)")
    result = geno.fix(source)
    assert (result.source, result.applied, result.check.ok) == (source, 0, False)


def test_a_method_call_writing_three_arguments_names_them() -> None:
    source = (
        "func span(xs: List[Int], start: Int, stop: Int, step: Int) -> List[Int]\n"
        "    example [1, 2], 0, 2, 1 -> [1, 2]\n"
        "    return xs.slice(start, stop)\n"
        "end func\n\n"
        "func main() -> Unit\n"
        "    print([1, 2, 3].span(0, 2, 1))\n"
        "end func\n"
    )
    diagnostic = geno.check(source).diagnostics[0]
    assert "name them" in diagnostic.message
    fixed = apply_fix(source, diagnostic.fixes[0])
    assert "[1, 2, 3].span(start: 0, stop: 2, step: 1)" in fixed
    assert geno.check(fixed).ok


@pytest.mark.parametrize(
    "call",
    [
        'print("abc".substring(0, 2))',
        'print("abc".replace("a", "b"))',
        "print([1, 2].fold(0, fn(acc, x) -> acc + x))",
        'print({"a": 1}.insert("b", 2))',
    ],
)
def test_two_arguments_after_a_receiver_stay_positional(call: str) -> None:
    result = geno.check(_program(call))
    assert result.ok, [d.message for d in result.diagnostics]


def test_geno_fix_leaves_errors_in_modules_alone() -> None:
    """A module's fix holds positions in the module, not in the source."""
    main = (
        "import Util\n\nfunc main() -> Unit\n"
        "    print(123456789)\n    print(f(1))\nend func\n"
    )
    util = (
        "export func f(n: Int) -> Int\n    example 1 -> 1\n"
        "    return lenght([n])\nend func\n"
    )
    result = geno.fix(main, modules={"Util": util})
    assert (result.source, result.applied) == (main, 0)
    assert "lenght" in result.check.diagnostics[0].message


# Panic examples (proposal 0017, 0.7 draft): an expected value written as a
# panic is E330, whose fix writes the `panics` form.


def _divider(example: str, preamble: str = "") -> str:
    return (
        f"{preamble}func safe_div(a: Int, b: Int) -> Int\n"
        "    example 6, 3 -> 2\n"
        f"    {example}\n"
        "    return a / b\n"
        "end func\n"
    )


def _usage(example: str) -> str:
    return (
        "func usage() -> String\n"
        f"    {example}\n"
        '    panic("not implemented")\n'
        "end func\n"
    )


@pytest.mark.parametrize(
    ("source", "written", "title", "fixed_line"),
    [
        (
            _divider("example 1, 0 -> panics"),
            "example 1, 0 panics",
            "Write 'panics'",
            "    example 1, 0 panics",
        ),
        (
            _divider('example 1, 0 -> panics "division"'),
            'example 1, 0 panics "division"',
            "Write 'panics \"division\"'",
            '    example 1, 0 panics "division"',
        ),
        (
            _divider("example 1, 0 -> panic"),
            "example 1, 0 panics",
            "Write 'panics'",
            "    example 1, 0 panics",
        ),
        (
            _divider('example 1, 0 -> panic("division")'),
            'example 1, 0 panics "division"',
            "Write 'panics \"division\"'",
            '    example 1, 0 panics "division"',
        ),
        (
            _divider('example 1, 0 -> panic(message: "division")'),
            'example 1, 0 panics "division"',
            "Write 'panics \"division\"'",
            '    example 1, 0 panics "division"',
        ),
        (
            # Spacing around the arrow does not matter: one space is left.
            _divider('example 1, 0->panic("division")'),
            'example 1, 0 panics "division"',
            "Write 'panics \"division\"'",
            '    example 1, 0 panics "division"',
        ),
        (
            _divider('example 1, 0   ->   panic("division")'),
            'example 1, 0 panics "division"',
            "Write 'panics \"division\"'",
            '    example 1, 0 panics "division"',
        ),
        (
            _usage("example -> panics"),
            "example () panics",
            "Write '() panics'",
            "    example () panics",
        ),
        (
            _usage('example -> panic("not implemented")'),
            'example () panics "not implemented"',
            "Write '() panics \"not implemented\"'",
            '    example () panics "not implemented"',
        ),
        (
            _usage('example () -> panic("not implemented")'),
            'example () panics "not implemented"',
            "Write 'panics \"not implemented\"'",
            '    example () panics "not implemented"',
        ),
    ],
)
def test_an_expected_panic_gets_a_fix_to_the_panics_form(
    source: str, written: str, title: str, fixed_line: str
) -> None:
    result = geno.check(source)
    [diagnostic] = result.diagnostics
    assert diagnostic.code is geno.ErrorCode.TYPE_EXAMPLE_PANIC_FORM
    assert diagnostic.message == (
        f"An example that expects a panic is written without an arrow: {written}"
    )
    # Reported at the arrow.
    example_line = source.split("\n")[diagnostic.location.line - 1]
    assert example_line.lstrip().startswith("example") and "panic" in example_line
    assert diagnostic.location.column == example_line.index("->") + 1
    assert [fix.title for fix in diagnostic.fixes] == [title]
    fixed = apply_fix(source, diagnostic.fixes[0])
    assert fixed_line in fixed.split("\n")
    assert geno.check(fixed).ok

    # geno.fix() applies it and the result checks clean.
    result = geno.fix(source)
    assert result.check.ok, [d.message for d in result.check.diagnostics]
    assert (result.source, result.applied) == (fixed, 1)


def test_the_e330_fix_edits_serialize() -> None:
    source = _divider('example 1, 0 -> panic("division")')
    [diagnostic] = geno.check(source).diagnostics
    assert diagnostic.to_dict()["fixes"] == [
        {
            "title": "Write 'panics \"division\"'",
            "edits": [
                {
                    "start": {"line": 3, "column": 17},
                    "end": {"line": 3, "column": 27},
                    "text": " panics ",
                },
                {
                    "start": {"line": 3, "column": 37},
                    "end": {"line": 3, "column": 38},
                    "text": "",
                },
            ],
        }
    ]


def test_the_old_panic_typo_fix_is_never_offered() -> None:
    """0.6 offered E301 "Did you mean 'panic'?" with a fix to `panic`."""
    result = geno.check(_divider("example 1, 0 -> panics"))
    assert [d.code for d in result.diagnostics] == [
        geno.ErrorCode.TYPE_EXAMPLE_PANIC_FORM
    ]
    titles = [fix.title for d in result.diagnostics for fix in d.fixes]
    assert "Change 'panics' to 'panic'" not in titles


def test_a_non_literal_panic_message_gets_no_fix() -> None:
    source = _divider('example 1, 0 -> panic(to_string(1) + " left")')
    [diagnostic] = geno.check(source).diagnostics
    assert diagnostic.code is geno.ErrorCode.TYPE_EXAMPLE_PANIC_FORM
    assert diagnostic.fixes == ()
    assert "example 1, 0 panics" in diagnostic.message
    assert "string literal" in diagnostic.message
    assert geno.fix(source).applied == 0


def test_an_arrow_starting_its_line_gets_no_fix() -> None:
    """Removing it would put `panics` on another line than the inputs."""
    source = _divider('example 1, 0\n        -> panic("division")')
    [diagnostic] = geno.check(source).diagnostics
    assert diagnostic.code is geno.ErrorCode.TYPE_EXAMPLE_PANIC_FORM
    assert diagnostic.fixes == ()


@pytest.mark.parametrize(
    "preamble, example",
    [
        # The program's own `panic` is a function like any other.
        (
            "func panic(code: Int) -> Int\n    example 1 -> 1\n    return code\n"
            "end func\n\n",
            "example 6, 0 -> panic(3)",
        ),
        (
            'func panic(text: String) -> Int\n    example "a" -> 1\n'
            "    return 1\nend func\n\n",
            'example 6, 0 -> panic("a")',
        ),
        # A `panics` value in scope keeps `-> panics` a value example.
        ("let panics = 0\n\n", "example 0, 1 -> panics"),
        (
            "func panics() -> Int\n    example () -> 0\n    return 0\nend func\n\n",
            "example 0, 1 -> panics()",
        ),
    ],
)
def test_a_programs_own_panic_or_panics_is_not_diagnosed(
    preamble: str, example: str
) -> None:
    result = geno.check(_divider(example, preamble))
    assert result.ok, [d.message for d in result.diagnostics]


def test_a_message_after_a_panics_value_is_still_e330() -> None:
    """A value example has no message, so `-> panics "m"` is always the form."""
    source = _divider('example 0, 1 -> panics "m"', "let panics = 0\n\n")
    [diagnostic] = geno.check(source).diagnostics
    assert diagnostic.code is geno.ErrorCode.TYPE_EXAMPLE_PANIC_FORM
    assert 'example 0, 1 panics "m"' in diagnostic.message


def test_geno_check_fix_rewrites_every_arrow_form(tmp_path: Path) -> None:
    path = tmp_path / "panics.geno"
    path.write_text(
        "func safe_div(a: Int, b: Int) -> Int\n"
        "    example 6, 3 -> 2\n"
        '    example 1, 0 -> panic("division")\n'
        "    example 2, 0 -> panic\n"
        '    example 3, 0 -> panics "zero"\n'
        "    example 4, 0 -> panics\n"
        "    return a / b\n"
        "end func\n\n"
        "func usage() -> String\n"
        "    example -> panics\n"
        '    panic("not implemented")\n'
        "end func\n",
        encoding="utf-8",
    )
    fixed = subprocess.run(
        [sys.executable, "-m", "geno", "check", "--fix", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert fixed.returncode == 0, fixed.stderr
    assert "Applied 5 fixes" in fixed.stdout
    assert path.read_text(encoding="utf-8") == (
        "func safe_div(a: Int, b: Int) -> Int\n"
        "    example 6, 3 -> 2\n"
        '    example 1, 0 panics "division"\n'
        "    example 2, 0 panics\n"
        '    example 3, 0 panics "zero"\n'
        "    example 4, 0 panics\n"
        "    return a / b\n"
        "end func\n\n"
        "func usage() -> String\n"
        "    example () panics\n"
        '    panic("not implemented")\n'
        "end func\n"
    )


@pytest.mark.parametrize(
    ("source", "code", "title", "fixed_line"),
    [
        (
            _usage("example panics"),
            geno.ErrorCode.PARSE_EXPECTED_TOKEN,
            "Write 'example () panics'",
            "    example () panics",
        ),
        (
            _usage('example panics "not implemented"'),
            geno.ErrorCode.PARSE_EXPECTED_TOKEN,
            "Write 'example () panics'",
            '    example () panics "not implemented"',
        ),
        (
            _divider('example 1, 0 panics ""'),
            geno.ErrorCode.PARSE_INVALID_SYNTAX,
            "Remove the empty message",
            "    example 1, 0 panics",
        ),
    ],
)
def test_a_misspelled_panics_example_gets_a_parse_fix(
    source: str, code: geno.ErrorCode, title: str, fixed_line: str
) -> None:
    [diagnostic] = geno.check(source).diagnostics
    assert diagnostic.code is code
    assert diagnostic.fixes[0].title == title
    result = geno.fix(source)
    assert result.check.ok, [d.message for d in result.check.diagnostics]
    assert fixed_line in result.source.split("\n")
