"""The operations models reach for (proposal 0012, milestone M11).

Membership, list concatenation, two-argument method calls without names,
`Int`/`Float` conversion methods, and fixes that rename another language's
spelling to Geno's, the same on every backend.
"""

from __future__ import annotations

import pytest

import geno
from geno.diagnostics import apply_fix
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)


def _main(body: str, before: str = "") -> str:
    lines = "".join(f"    {line}\n" for line in body.strip("\n").split("\n"))
    return before + "func main() -> Unit\n" + lines + "end func\n"


def _assert_parity(source: str, expected: str) -> None:
    assert geno.check(source).ok, [str(d) for d in geno.check(source).diagnostics]
    _assert_expected_backend_outputs(
        label="familiar idioms",
        context=source,
        expected=expected,
        interp_out=_interpreter_output(source),
        py_out=_compiled_python_output(source),
        js_out=_compiled_js_output(source) if HAS_NODE else expected,
    )


class TestMembership:
    def test_in_and_not_in_on_every_container(self) -> None:
        _assert_parity(
            _main(
                """
let xs = [1, 2]
let m = {"a": 1}
var mm: MutableMap[String, Int] = mutable_map_new()
mm.set("k", 1)
print(2 in xs)
print(9 not in xs)
print("ell" in "hello")
print("a" in m)
print("z" not in m)
print("x" in set_from_list(["x"]))
print("k" in mm)
print([x for x in [1, 2, 3, 4] if x not in xs])
print([1, 2] in [[1, 2], [3]])
print(not 2 in xs)
"""
            ),
            "true\ntrue\ntrue\ntrue\ntrue\ntrue\ntrue\n[3, 4]\ntrue\nfalse\n",
        )

    def test_membership_reaches_the_builtin_a_program_shadows(self) -> None:
        _assert_parity(
            _main(
                "print(2 in [1, 2])\nprint(contains(5))",
                before=(
                    "func contains(n: Int) -> Int\n    example 1 -> 1\n"
                    "    return n\nend func\n\n"
                ),
            ),
            "true\n5\n",
        )

    def test_operands_are_evaluated_left_to_right(self) -> None:
        _assert_parity(
            _main(
                "print(say(1) in [say(2)])",
                before=(
                    "func say(n: Int) -> Int with io\n    example 1 -> 1\n"
                    "    print(n)\n    return n\nend func\n\n"
                ),
            ),
            "1\n2\nfalse\n",
        )

    @pytest.mark.parametrize(
        ("expression", "message"),
        [
            ('"a" in [1]', "'in' on List[Int] needs Int, got String"),
            ('1 in {"a": 1}', "'in' on Map[String, Int] needs String, got Int"),
            ("1 in 2", "Cannot test membership in Int"),
        ],
    )
    def test_membership_is_type_checked(self, expression: str, message: str) -> None:
        result = geno.check(_main(f"print({expression})"))
        assert message in result.diagnostics[0].message

    def test_membership_does_not_chain(self) -> None:
        result = geno.check(_main("print(1 in [1] == true)"))
        assert "Comparison chaining is not supported" in result.diagnostics[0].message


class TestListConcatenation:
    def test_plus_concatenates_lists(self) -> None:
        _assert_parity(
            _main(
                """
var acc: List[Int] = []
for x in [1, 2] do
    acc = acc + [x * 10]
end for
print(acc)
print([] + [1])
print(["a"] + ["b", "c"])
print([[1]] + [[2, 3]])
"""
            ),
            '[10, 20]\n[1]\n["a", "b", "c"]\n[[1], [2, 3]]\n',
        )

    def test_an_int_list_joins_a_float_list_as_floats(self) -> None:
        _assert_parity(
            _main(
                """
let fs = [1.5]
let ns = [2]
print(fs + ns)
print(ns + fs)
print(fs + [3])
"""
            ),
            "[1.5, 2.0]\n[2.0, 1.5]\n[1.5, 3.0]\n",
        )

    def test_lists_of_different_types_do_not_concatenate(self) -> None:
        result = geno.check(_main('print([1] + ["a"])'))
        assert result.diagnostics[0].message == (
            "Cannot concatenate List[Int] and List[String]: "
            "both lists need the same element type"
        )


class TestMethodCalls:
    def test_two_arguments_after_the_receiver_stay_positional(self) -> None:
        _assert_parity(
            _main(
                """
print([3, 1, 2].fold(0, fn(acc, x) -> acc + x))
print("Hello".replace("l", "L"))
print([1, 2, 3].slice(1, 3))
print({"a": 1}.insert("b", 2))
print("ab".pad_left(5, "*"))
print(7.clamp(0, 5))
"""
            ),
            '6\nHeLLo\n[2, 3]\n{"a": 1, "b": 2}\n***ab\n5\n',
        )

    def test_int_and_float_convert_with_methods(self) -> None:
        _assert_parity(
            _main("print(7.to_float() / 2.0)\nprint(2.5.to_int())"),
            "3.5\n2\n",
        )


@pytest.mark.parametrize(
    ("expression", "title"),
    [
        ('"abc".chars()', "Change 'chars' to 'to_chars'"),
        ('ord("a")', "Change 'ord' to 'char_code'"),
        ("chr(97)", "Change 'chr' to 'from_char_code'"),
        ("[1, 2].skip(1)", "Change 'skip' to 'drop'"),
        ("[1, 2].rev()", "Change 'rev' to 'reverse'"),
        ('{"a": 1}.items()', "Change 'items' to 'entries'"),
        ('{"a": 1}.has("a")', "Change 'has' to 'contains_key'"),
        ('{"a": 1}.contains("a")', "Change 'contains' to 'contains_key'"),
        ('{"a": 1}.put("b", 2)', "Change 'put' to 'insert'"),
        ("Some(1).flat_map(fn(x) -> Some(x))", "Change 'flat_map' to 'and_then'"),
        ("Some(1).get_or(0)", "Change 'get_or' to 'unwrap_or'"),
        ('"3".to_int()', "Change 'to_int' to 'parse_int'"),
        ("float(3)", "Change 'float' to 'int_to_float'"),
        ('"a b".lstrip()', "Change 'lstrip' to 'trim_start'"),
    ],
)
def test_another_languages_name_is_fixed_for_the_receiver(
    expression: str, title: str
) -> None:
    source = _main(f"print({expression})")
    diagnostic = geno.check(source).diagnostics[0]
    assert diagnostic.fixes[0].title == title
    assert geno.check(apply_fix(source, diagnostic.fixes[0])).ok


@pytest.mark.parametrize(
    ("body", "hint"),
    [
        # A changed letter in a short name is another word, not a typo.
        ("let bat = 1\nprint(bad)", "Did you mean 'bat'?"),
        # Two changed letters: `to_valid` is not a slip of `is_valid`.
        ("print(to_valid(1))", "Did you mean 'is_valid'?"),
    ],
)
def test_a_different_word_gets_a_hint_but_no_fix(body: str, hint: str) -> None:
    source = _main(
        body,
        before=(
            "func is_valid(n: Int) -> Bool\n    example 1 -> true\n"
            "    return n > 0\nend func\n\n"
        ),
    )
    diagnostic = geno.check(source).diagnostics[0]
    assert hint in diagnostic.message
    assert diagnostic.fixes == ()


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("pow(2, 3)", "Geno writes powers with '**': a ** b"),
        ('"ab" * 3', "To repeat a string, write s.repeat(n)"),
        ('", ".join(["a"])', "Geno joins from the list: write parts.join(separator)"),
    ],
)
def test_habits_without_a_rename_get_a_hint(expression: str, message: str) -> None:
    result = geno.check(_main(f"print({expression})"))
    assert message in result.diagnostics[0].message


def test_a_short_name_gets_no_far_fetched_hint() -> None:
    result = geno.check(_main("let m = 1\nprint(sum([m]))"))
    assert "Did you mean" not in result.diagnostics[0].message
