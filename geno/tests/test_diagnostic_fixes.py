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
        ('print("abc".substring(0, 2))', "name them", "Name the arguments"),
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
