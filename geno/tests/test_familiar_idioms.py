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
    result = geno.check(_main("let m = 1\nprint(qux([m]))"))
    assert "Did you mean" not in result.diagnostics[0].message


# ---------------------------------------------------------------------------
# The probe (proposal 0012): idioms models write from other languages. Each
# one is written as `print(<idiom>)` after these bindings.
# ---------------------------------------------------------------------------

_PROBE_BINDINGS = (
    "let xs: List[Int] = [3, 1, 2]",
    "let ys: List[Int] = [4, 5]",
    'let words: List[String] = ["kiwi", "fig"]',
    'let s: String = "Hello, World"',
    'let m: Map[String, Int] = {"a": 1}',
    "let opt: Option[Int] = Some(3)",
    "let r: Result[Int, String] = Ok(3)",
    "let f: Float = 2.5",
    "let n: Int = 7",
    'let c: String = "a"',
)

ACCEPTED_IDIOMS = [
    "xs.sum()",
    "sum(xs)",
    "xs.max()",
    "max(xs)",
    "xs.min()",
    "min(xs)",
    "min(1, 2)",
    "max(1, 2)",
    "xs.is_empty()",
    "is_empty(xs)",
    "xs.first()",
    "xs.last()",
    "xs.reverse()",
    "reverse(xs)",
    "xs.contains(2)",
    "xs.index_of(2)",
    "xs.sort()",
    "xs.sort_by(fn(x) -> 0 - x)",
    "xs.fold(0, fn(a, x) -> a + x)",
    "xs.any(fn(x) -> x > 1)",
    "xs.all(fn(x) -> x > 1)",
    "xs.count(fn(x) -> x > 1)",
    "xs.find(fn(x) -> x > 1)",
    "xs.enumerate()",
    "xs.zip(ys)",
    "xs.take(2)",
    "xs.drop(1)",
    "xs.flat_map(fn(x) -> [x, x])",
    "[[1], [2]].flatten()",
    "xs.unique()",
    "xs.append(4)",
    'words.join(", ")',
    "xs.get(0)",
    "xs.slice(1, 2)",
    "xs.length()",
    "range(0, 5)",
    "range(5)",
    "xs.map(fn(x) -> x * 2).sum()",
    "xs.chunk(2)",
    "xs + ys",
    "xs.concat(ys)",
    "xs + [4]",
    "xs[0]",
    "xs[-1]",
    "xs.filter(fn(x) -> x > 1).length()",
    "xs.sort().reverse()",
    "xs.group_by(fn(x) -> x % 2)",
    "xs.find_index(fn(x) -> x > 1)",
    "[1.5, 2.5].sum()",
    "words.sort()",
    "xs.intersperse(0)",
    "s.to_chars()",
    's.split(",")',
    "s.trim()",
    "s.to_upper()",
    "s.to_lower()",
    "s.length()",
    's.contains("lo")',
    's.starts_with("He")',
    's.ends_with("d")',
    's.replace("l", "L")',
    's.index_of("o")',
    "s.reverse()",
    "s.repeat(3)",
    "s[0]",
    "s.substring(0, 3)",
    "s.is_empty()",
    "s.lines()",
    "s.words()",
    "s.char_at(0)",
    "c.is_digit()",
    "c.is_alpha()",
    "c.is_alphanumeric()",
    "c.is_whitespace()",
    "c.is_upper()",
    "c.is_lower()",
    'parse_int("3")',
    '"3".parse_int()',
    "char_code(c)",
    "from_char_code(97)",
    "s.trim_start()",
    's.pad_left(20, " ")',
    "s + to_string(n)",
    'parse_float("2.5")',
    '"abc" < "abd"',
    's.split("")',
    "abs(n)",
    "n.abs()",
    "2 ** 3",
    "sqrt(f)",
    "floor(f)",
    "ceil(f)",
    "round(f)",
    "f.floor()",
    "f.round()",
    "n.to_float()",
    "f.to_int()",
    "n.to_string()",
    "to_string(n)",
    "min(1.5, f)",
    "max(f, 1.0)",
    "n / 2",
    "n % 2",
    "f / 2.0",
    "n.clamp(0, 5)",
    "n.max(3)",
    "n.min(3)",
    "f.sqrt()",
    "math_sqrt(f)",
    "n.to_float() / 2.0",
    'm.get("a")',
    'm["a"]',
    'm.contains_key("a")',
    "m.keys()",
    "m.values()",
    'm.insert("b", 2)',
    'm.insert(key: "b", value: 2)',
    "m.entries()",
    'm.get("a").unwrap_or(0)',
    'm.get_or("a", 0)',
    'm.remove("a")',
    "m.length()",
    "m.is_empty()",
    "m.size()",
    "opt.unwrap_or(0)",
    "opt.is_some()",
    "opt.is_none()",
    "opt.map(fn(x) -> x + 1)",
    "opt.unwrap()",
    "opt.and_then(fn(x) -> Some(x))",
    'opt.expect("no")',
    "opt == None",
    "r.unwrap_or(0)",
    "r.is_ok()",
    "r.is_err()",
    "r.map(fn(x) -> x + 1)",
    "r.unwrap()",
    'r.map_err(fn(e) -> e + "!")',
    "r.and_then(fn(x) -> Ok(x))",
    'r.expect("no")',
]

# Rejected, with a fix that makes the program check: another language's name
# for a Geno operation, or arguments that need names.
FIXED_IDIOMS = [
    "xs.skip(1)",
    "xs.distinct()",
    "xs.dedup()",
    "xs.position(fn(x) -> x > 1)",
    "xs.rev()",
    "xs.len()",
    "s.chars()",
    '"3".to_int()',
    "ord(c)",
    "chr(97)",
    "s.split_whitespace()",
    "s.strip()",
    's.find("o")',
    "to_float(n)",
    "to_int(f)",
    "int(f)",
    "float(n)",
    "clamp(n, 0, 5)",
    "m.items()",
    'm.get_or_default("a", 0)',
    'm.has("a")',
    'm.contains("a")',
    'm.set("b", 2)',
    'm.put("b", 2)',
    "opt.get_or(0)",
    "opt.flat_map(fn(x) -> Some(x))",
]


def _probe(idiom: str) -> str:
    return _main("\n".join(_PROBE_BINDINGS) + f"\nprint({idiom})")


@pytest.mark.parametrize("idiom", ACCEPTED_IDIOMS)
def test_an_everyday_idiom_type_checks(idiom: str) -> None:
    result = geno.check(_probe(idiom))
    assert result.ok, [str(d) for d in result.diagnostics]


@pytest.mark.parametrize("idiom", FIXED_IDIOMS)
def test_another_languages_idiom_gets_a_fix_that_checks(idiom: str) -> None:
    source = _probe(idiom)
    diagnostic = geno.check(source).diagnostics[0]
    assert diagnostic.fixes, diagnostic.message
    fixed = geno.check(apply_fix(source, diagnostic.fixes[0]))
    assert fixed.ok, [str(d) for d in fixed.diagnostics]


class TestEverydayBuiltins:
    def test_new_builtins_agree_on_every_backend(self) -> None:
        _assert_parity(
            _main(
                """
let xs = [3, 1, 2, 3]
let fs = [1.5, 2.5]
let none_fs: List[Float] = []
let words = ["kiwi", "fig", "kiwi"]
let counts = {"a": 1}
print(min(4, 2))
print(min(1, 2.5))
print(xs.sum())
print(sum(fs))
print(none_fs.sum())
print(max(xs))
print(xs.min())
print(words.max())
print(none_fs.max())
print(xs.is_empty())
print("".is_empty())
print(counts.is_empty())
print(xs.index_of(3))
print(xs.index_of(9))
print(xs.count(fn(x) -> x > 1))
print(xs.unique())
print([[1], [1], [2]].unique())
print("héllo".reverse())
print("a\\nb\\r\\nc\\n".lines())
print("  two  words\\there ".words())
print("7".is_digit())
print("".is_digit())
print("aZ".is_alpha())
print("a1".is_alphanumeric())
print(" \\t".is_whitespace())
print("AB".is_upper())
print("aB".is_lower())
print(range(3))
print(counts.get("a", 0) + counts.get("z", 0))
print(counts.get("a"))
"""
            ),
            "2\n1.0\n9\n4.0\n0.0\nSome(value: 3)\nSome(value: 1)\n"
            'Some(value: "kiwi")\nNone\nfalse\ntrue\nfalse\nSome(value: 0)\n'
            'None\n3\n[3, 1, 2]\n[[1], [2]]\nolléh\n["a", "b", "c"]\n'
            '["two", "words", "here"]\ntrue\nfalse\ntrue\ntrue\ntrue\ntrue\n'
            "false\n[0, 1, 2]\n1\nSome(value: 1)\n",
        )

    def test_unique_follows_geno_equality_on_every_backend(self) -> None:
        """Maps and functions are not merged by how they serialize."""
        _assert_parity(
            _main(
                """
print([{"a": 1}, {"a": 2}, {"a": 1}].unique())
let inc = fn(x: Int) -> x + 1
let dbl = fn(x: Int) -> x * 2
print([inc, dbl, inc].unique().length())
print([Some(1), None, Some(1), Some(2)].unique())
print([(1, "a"), (1, "b"), (1, "a")].unique())
print(["1", "1", "2"].unique())
"""
            ),
            '[{"a": 1}, {"a": 2}]\n2\n[Some(value: 1), None, Some(value: 2)]\n'
            '[(1, "a"), (1, "b")]\n["1", "2"]\n',
        )

    def test_sum_of_a_list_of_ints_stays_an_int(self) -> None:
        result = geno.check(_main("let total: Int = [1, 2].sum()\nprint(total)"))
        assert result.ok

    def test_sum_needs_numbers(self) -> None:
        result = geno.check(_main('print(["a"].sum())'))
        assert not result.ok

    def test_a_program_may_define_the_new_names(self) -> None:
        _assert_parity(
            _main(
                "print(sum(1, 2))",
                before=(
                    "func sum(a: Int, b: Int) -> Int\n    example 1, 2 -> 3\n"
                    "    return a + b\nend func\n\n"
                ),
            ),
            "3\n",
        )


@pytest.mark.parametrize(
    ("call", "message"),
    [
        ('substring("a")', "'substring' expects 3 argument(s), but 1 were provided"),
        ("max([1], 2, 3)", "'max' expects 2 argument(s), but 3 were provided"),
    ],
)
def test_a_rejected_call_resolves_as_a_method_only_with_matching_arity(
    call: str, message: str
) -> None:
    """`max(xs)` is `xs.max()`; a call no method takes keeps its own error."""
    messages = [d.message for d in geno.check(_main(f"print({call})")).diagnostics]
    assert messages[0] == message
    assert not any("list_max" in m or "string_substring" in m for m in messages)
