"""Forms models write without Geno's prompt rules (proposal 0017).

Compound assignment, tuple patterns in loops, string literals in f-string
interpolations and an untyped method receiver are accepted, the same on every
backend. Other languages' record syntax, a `match` without `with`, foreign
conversion names and a missing return type are rejected with a fix that
`geno check --fix` applies.
"""

from __future__ import annotations

import pytest

import geno
from geno.diagnostics import ErrorCode
from geno.formatter import format_source
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
    result = geno.check(source)
    assert result.ok, [str(d) for d in result.diagnostics]
    _assert_expected_backend_outputs(
        label="cold-write forms",
        context=source,
        expected=expected,
        interp_out=_interpreter_output(source),
        py_out=_compiled_python_output(source),
        js_out=_compiled_js_output(source) if HAS_NODE else expected,
    )


def _errors(source: str) -> list[geno.Diagnostic]:
    result = geno.check(source)
    assert not result.ok
    return list(result.diagnostics)


def _fixed(source: str) -> str:
    """``source`` after ``geno check --fix``, which must leave it checking."""
    fixed = geno.fix(source)
    assert fixed.applied >= 1
    assert fixed.check.ok, [str(d) for d in fixed.check.diagnostics]
    return fixed.source


_POINT = "type Point = Point(x: Int, y: Int)\n\n"


class TestCompoundAssignment:
    def test_every_operator_on_a_variable(self) -> None:
        _assert_parity(
            _main(
                """
var n = 100
n -= 1
n *= 2
n /= 4
n %= 7
n += 10
var f = 1.5
f *= 2.0
var s = "a"
s += "b"
var xs = [1]
xs += [2, 3]
print(n)
print(f)
print(s)
print(xs)
"""
            ),
            "10\n3.0\nab\n[1, 2, 3]\n",
        )

    def test_index_and_field_targets(self) -> None:
        _assert_parity(
            _main(
                """
var arr = array_from_list([10, 20, 30])
let i = 1
arr[i + 1] += 5
arr[0] -= 1
var p = Point(x: 1, y: 2)
p.x += 40
print(array_to_list(arr))
print(p.x)
""",
                before=_POINT,
            ),
            "[9, 20, 35]\n41\n",
        )

    @pytest.mark.parametrize(
        ("declaration", "update", "hint"),
        [
            (
                "var m: MutableMap[String, Int] = mutable_map_new()",
                'm["a"] += 2',
                "write m[key] = m.get(key).unwrap_or(0) + value",
            ),
            (
                "var v: Vec[Int] = vec_from_list([1])",
                "v[0] *= 3",
                "write v[i] = v.get(i) * value",
            ),
            (
                'var m = {"a": 1}',
                'm["a"] += 1',
                "build a new one, as in m = m.insert(key, value)",
            ),
        ],
    )
    def test_a_container_it_cannot_read_says_what_to_write(
        self, declaration: str, update: str, hint: str
    ) -> None:
        (error,) = _errors(_main(f"{declaration}\n{update}"))
        assert hint in error.message

    def test_in_a_loop_and_a_block_lambda(self) -> None:
        _assert_parity(
            _main(
                """
var total = 0
for x in [1, 2, 3] do
    total += x
end for
let add_all = fn(xs: List[Int]) do
    var sum = 0
    for x in xs do
        sum += x * 2
    end for
    return sum
end fn
print(total)
print(add_all([1, 2]))
"""
            ),
            "6\n6\n",
        )

    def test_is_the_assignment_written_out(self) -> None:
        program = geno.parse(_main("var t = 0\nt += 2"))
        written_out = geno.parse(_main("var t = 0\nt = t + 2"))
        statement = program.definitions[0].body[1]
        expected = written_out.definitions[0].body[1]
        assert type(statement) is type(expected)
        assert statement.target == expected.target == "t"
        assert statement.value.operator == "+"

    def test_a_let_gets_the_fix_that_declares_it_with_var(self) -> None:
        source = _main("let total = 0\ntotal += 1\nprint(total)")
        (error,) = _errors(source)
        assert error.code is ErrorCode.TYPE_IMMUTABLE_ASSIGN
        assert error.fixes[0].title == "Declare 'total' with 'var'"
        assert "var total = 0" in _fixed(source)

    def test_index_and_field_on_a_let_get_the_fix_too(self) -> None:
        source = _main("let p = Point(x: 1, y: 2)\np.x += 1\nprint(p.x)", before=_POINT)
        (error,) = _errors(source)
        assert error.fixes[0].title == "Declare 'p' with 'var'"
        assert "var p = Point" in _fixed(source)

    def test_a_parameter_gets_no_fix(self) -> None:
        source = (
            "func f(n: Int) -> Int\n    example 1 -> 2\n    n += 1\n"
            "    return n\nend func\n"
        )
        (error,) = _errors(source)
        assert error.code is ErrorCode.TYPE_IMMUTABLE_ASSIGN
        assert error.fixes == ()

    def test_a_target_that_calls_a_function_is_rejected(self) -> None:
        source = _main(
            "var xs = array_from_list([1])\nxs[zero()] += 1",
            before="func zero() -> Int\n    example () -> 0\n    return 0\nend func\n\n",
        )
        (error,) = _errors(source)
        assert "cannot call a function" in error.message
        assert error.code is ErrorCode.PARSE_INVALID_SYNTAX

    def test_an_operator_written_apart_from_its_equals_sign_is_not_one(self) -> None:
        source = _main("var t = 0\nt + = 1")
        assert not geno.check(source).ok

    def test_is_a_statement_not_an_expression(self) -> None:
        source = _main("var a = 1\nlet b = a += 1")
        assert not geno.check(source).ok

    def test_the_formatter_keeps_it(self) -> None:
        source = _main("var t = 0\nt  +=  1\nt-=1")
        assert "t += 1\n    t -= 1\n" in format_source(source)


class TestTupleLoopPatterns:
    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            (" for (a,b) in [(1,2)] do\n print(a+b)\n end for\n", "3\n"),
            ("    for   (a,b) in [(1,2)] do\n        print(a+b)\n    end for\n", "3\n"),
            (" let xs=[a+b for (a,b) in [(1,2)] if a>0]\n print(xs)\n", "[3]\n"),
            (
                " for (a,b) in [(1,2)] do\n"
                " for (c,(d,_)) in [(3,(4,5))] do\n"
                " print(a+b+c+d)\n end for\n end for\n",
                "10\n",
            ),
            (
                " print([[a+c for (c,d) in [(3,4)]] for (a,b) in [(1,2)]])\n",
                "[[4]]\n",
            ),
            (
                ' print(f"{[a for (a,b) in [(1,2)]]} {[c for (c,d) in [(3,4)]]}")\n',
                "[1] [3]\n",
            ),
        ],
    )
    def test_formatter_preserves_tuple_bindings(self, body: str, expected: str) -> None:
        source = "func main() -> Unit\n" + body + "end func\n"
        _assert_parity(source, expected)
        formatted = format_source(source)
        assert formatted != source
        assert format_source(formatted) == formatted
        _assert_parity(formatted, expected)

    @pytest.mark.parametrize("name", ["_geno_item_0", "_geno_item_1", "_geno_item_3_9"])
    def test_generated_bindings_do_not_shadow_user_names(self, name: str) -> None:
        source = _main(
            f"let {name} = 10\n"
            f"for (a,b) in [(1,2)] do\n    print({name})\nend for\n"
            f'print(f"{{[f"{{{name}}}" for (c,d) in [(3,4)]]}}")'
        )
        _assert_parity(source, '10\n["10"]\n')
        _assert_parity(format_source(source), '10\n["10"]\n')

    def test_generated_bindings_reserve_names_inside_fstrings(self) -> None:
        source = _main(
            'print(f"{[[_geno_item_1 + a for (a,b) in [(1,2)]] '
            'for (_geno_item_1,c) in [(10,20)]]}")'
        )
        _assert_parity(source, "[[11]]\n")
        _assert_parity(format_source(source), "[[11]]\n")

    def test_for_loops_unpack_each_element(self) -> None:
        _assert_parity(
            _main(
                """
for (i, word) in ["a", "b"].enumerate() do
    print(f"{i}:{word}")
end for
for (a, (b, _)) in [(1, (2, 3)), (4, (5, 6))] do
    print(a + b)
end for
for (k, v): (String, Int) in {"x": 1}.entries() do
    print(f"{k}={v}")
end for
"""
            ),
            "0:a\n1:b\n3\n9\nx=1\n",
        )

    def test_break_and_continue_leave_the_loop(self) -> None:
        _assert_parity(
            _main(
                """
var out = ""
for (n, s) in [(1, "a"), (2, "b"), (3, "c"), (4, "d")] do
    if n == 2 then
        continue
    end if
    if n == 4 then
        break
    end if
    out += s
end for
print(out)
"""
            ),
            "ac\n",
        )

    def test_comprehensions_unpack_in_the_element_and_the_condition(self) -> None:
        _assert_parity(
            _main(
                """
let pairs = [(1, "a"), (2, "b"), (3, "c")]
print([f"{s}{n}" for (n, s) in pairs if n != 2])
print([a * b for (a, b) in [(2, 3), (4, 5)]])
"""
            ),
            '["a1", "c3"]\n[6, 20]\n',
        )

    def test_a_nested_loop_unpacks_each_level(self) -> None:
        _assert_parity(
            _main(
                """
var total = 0
for (a, b) in [(1, 2)] do
    for (c, d) in [(10, 20)] do
        total += a * c + b * d
    end for
end for
print(total)
"""
            ),
            "50\n",
        )

    @pytest.mark.parametrize(
        ("source", "formatted"),
        [
            (
                "func main() -> Unit\n for (a,b) in [(1,2)] do\n print(a+b)\n"
                " end for\nend func\n",
                "func main() -> Unit\n    for (a, b) in [(1, 2)] do\n"
                "        print(a + b)\n    end for\nend func\n",
            ),
            (
                "func main() -> Unit\n let xs = [a+b for (a,b) in [(1,2)] if a>0]\n"
                " print(xs)\nend func\n",
                "func main() -> Unit\n    let xs = [a + b for (a, b) in [(1, 2)] if a > 0]\n"
                "    print(xs)\nend func\n",
            ),
            (
                "func main() -> Unit\n        for (k,  (a, _))  in [(1, (2, 3))] do\n"
                "                        print(k + a)\n        end for\nend func\n",
                "func main() -> Unit\n    for (k, (a, _)) in [(1, (2, 3))] do\n"
                "        print(k + a)\n    end for\nend func\n",
            ),
        ],
    )
    def test_formatting_keeps_the_program(self, source: str, formatted: str) -> None:
        assert geno.check(source).ok
        assert format_source(source) == formatted
        assert format_source(formatted) == formatted
        assert geno.check(formatted).ok

    def test_names_stay_unique_inside_fstring_interpolations(self) -> None:
        _assert_parity(
            _main(
                """
for (a, b) in [(1, 2)] do
    print(f"{[x + y for (x, y) in [(a, b), (b, a)]]} {a}")
end for
"""
            ),
            "[3, 3] 1\n",
        )
        program = geno.parse(
            _main(
                'for (a, b) in [(1, 2)] do\n    print(f"{[x for (x, y) in [(a, b)]]}")\nend for'
            )
        )
        loop = program.definitions[0].body[0]
        comprehension = (
            loop.body[0].arms[0].body[0].expression.arguments[0].value.parts[0]
        )
        assert loop.variable != comprehension.variable

    def test_pythons_unparenthesized_names_get_parentheses(self) -> None:
        source = _main("for i, x in [5, 6].enumerate() do\n    print(i * x)\nend for")
        (error,) = _errors(source)
        assert "in parentheses" in error.message
        assert "for (i, x) in" in _fixed(source)

    def test_a_pattern_that_can_fail_is_rejected(self) -> None:
        (error,) = _errors(_main("print([a for (a, 1) in [(1, 1)]])"))
        assert "unpacks each element into names" in error.message

    def test_a_non_tuple_element_is_one_error_without_a_rename(self) -> None:
        (error,) = _errors(
            _main("var t = 0\nfor (a, b) in [1, 2] do\n    t += a\nend for")
        )
        assert error.code is ErrorCode.TYPE_PATTERN_MISMATCH
        assert error.fixes == ()

    @pytest.mark.parametrize(
        "comprehension",
        [
            "[await twice(a) for (a, b) in [(1, 2)]]",
            "[a for (a, b) in [(1, 2)] if (await twice(b)) > 1]",
        ],
    )
    def test_a_comprehension_that_unpacks_cannot_await(
        self, comprehension: str
    ) -> None:
        source = (
            "async func twice(n: Int) -> Int\n    return n * 2\nend func\n\n"
            f"func main() -> String\n    let xs = {comprehension}\n"
            '    return f"{xs}"\nend func\n'
        )
        (error,) = _errors(source)
        assert "cannot use await; write a for loop" in error.message
        line = source.split("\n")[5]
        assert error.location.column == line.index("await") + 1

    def test_a_loop_that_unpacks_can_await(self) -> None:
        _assert_parity(
            "async func twice(n: Int) -> Int\n    return n * 2\nend func\n\n"
            + _main(
                "var total = 0\nfor (a, b) in [(1, 2), (3, 4)] do\n"
                "    total += await twice(a + b)\nend for\nprint(total)"
            ),
            "20\n",
        )


class TestStringLiteralsInFStrings:
    def test_literals_braces_quotes_and_nested_fstrings(self) -> None:
        _assert_parity(
            _main(
                r"""
let m = {"the": 2}
let s = "a}b{c"
print(f"the={m.get("the", 0)} none={m.get("none", 0)}")
print(f"{s.replace("}", "")} {"a,b".replace(",", "-")}")
print(f"{f"[{"x"}]"} {"\"q\""} {"//"}")
"""
            ),
            'the=2 none=0\nab{c a-b\n[x] "q" //\n',
        )

    def test_errors_inside_an_interpolation_are_located_where_written(self) -> None:
        source = 'func main() -> String\n    let n = 5\n    return f"v: {n + "x"} {nope}"\nend func\n'
        locations = [(d.location.line, d.location.column) for d in _errors(source)]
        line = source.split("\n")[2]
        assert locations == [
            (3, line.index('n + "x"') + 1),
            (3, line.index("nope") + 1),
        ]

    def test_nesting_is_bounded(self) -> None:
        def nested(depth: int) -> str:
            expr = '"x"'
            for _level in range(depth):
                expr = 'f"{' + expr + '}"'
            return f"func main() -> String\n    return {expr}\nend func\n"

        assert geno.check(nested(20)).ok
        for depth in (60, 1200):
            (error,) = _errors(nested(depth))
            assert "nested too deeply" in error.message

    def test_an_unterminated_literal_inside_is_a_lexer_error(self) -> None:
        source = 'func main() -> String\n    return f"{"x}"\nend func\n'
        assert not geno.check(source).ok

    def test_the_formatter_reads_a_comment_marker_in_a_nested_literal(self) -> None:
        source = _main('let s = "a//b"\nprint(f"{s.replace("//",    "")}")  // note')
        formatted = format_source(source)
        assert 'print(f"{s.replace("//",    "")}")  // note' in formatted
        assert geno.check(formatted).ok


class TestBareSelf:
    SOURCE = """trait Describe
    func describe(self) -> String
    func scaled(self, k: Int) -> Int
end trait

type Point = Point(x: Int, y: Int)

impl Describe for Point
    func describe(self) -> String
        example Point(1, 2) -> "(1, 2)"
        return f"({self.x}, {self.y})"
    end func

    func scaled(self, k: Int) -> Int
        example (Point(1, 2), 3) -> 9
        return (self.x + self.y) * k
    end func
end impl

func show[T: Describe](x: T) -> String
    example Point(0, 0) -> "(0, 0)"
    return x.describe()
end func

"""

    def test_means_self_typed_by_the_trait_and_the_impl(self) -> None:
        _assert_parity(
            self.SOURCE
            + "func main() -> Unit\n    let p = Point(x: 3, y: 4)\n"
            + "    print(p.describe())\n    print(p.scaled(2))\n"
            + "    print(show(p))\nend func\n",
            "(3, 4)\n14\n(3, 4)\n",
        )

    def test_is_the_annotated_receiver(self) -> None:
        program = geno.parse(self.SOURCE)
        trait, _point, impl, _show = program.definitions
        assert str(trait.methods[0].params[0].param_type) == "Self"
        assert str(impl.methods[0].params[0].param_type) == "Point"

    def test_a_free_function_still_types_its_parameters(self) -> None:
        source = "func f(self) -> Int\n    example 1 -> 1\n    return 1\nend func\n"
        assert not geno.check(source).ok


class TestFixes:
    @pytest.mark.parametrize(
        ("header", "repaired"),
        [("match n", "match n with"), ("match n:", "match n with")],
    )
    def test_a_match_without_with(self, header: str, repaired: str) -> None:
        source = (
            f"func f(n: Int) -> Int\n    example 1 -> 1\n    {header}\n"
            "        | 1 -> return 1\n        | _ -> return 0\n    end match\nend func\n"
        )
        assert f"    {repaired}\n" in _fixed(source)

    @pytest.mark.parametrize(
        ("declaration", "repaired"),
        [
            ("type Point = { x: Int, y: Int }", "type Point = Point(x: Int, y: Int)"),
            ("type Point = {x: Int; y: Int;}", "type Point = Point(x: Int, y: Int)"),
            (
                "type Point = {\n    x: Int,\n    y: List[Int],\n}",
                "type Point = Point(\n    x: Int,\n    y: List[Int])",
            ),
        ],
    )
    def test_a_record_type_in_braces(self, declaration: str, repaired: str) -> None:
        source = (
            declaration
            + "\n\nfunc f(p: Point) -> Int\n    example Point(1, 2) -> 1\n    return p.x\nend func\n"
        )
        if "List" in declaration:
            source = source.replace("Point(1, 2)", "Point(1, [2])")
        (error,) = _errors(source)
        assert error.code is ErrorCode.PARSE_INVALID_SYNTAX
        assert "one constructor" in error.message
        assert _fixed(source).startswith(repaired + "\n")

    def test_a_generic_record_type_in_braces(self) -> None:
        source = (
            "type Box[T] = { value: T }\n\nfunc get(b: Box[Int]) -> Int\n"
            "    example Box(1) -> 1\n    return b.value\nend func\n"
        )
        assert _fixed(source).startswith("type Box[T] = Box(value: T)\n")

    @pytest.mark.parametrize(
        "update",
        ["{ p with x = p.x + 4 }", "{ p with x: p.x + 4 }", "{ ...p, x: p.x + 4 }"],
    )
    def test_a_record_update_in_braces(self, update: str) -> None:
        source = (
            _POINT
            + "func f(p: Point) -> Point\n    example Point(1, 2) -> Point(5, 2)\n"
            + f"    return {update}\nend func\n"
        )
        (error,) = _errors(source)
        assert "with (field: new_value)" in error.message
        assert "    return p with (x: p.x + 4)\n" in _fixed(source)

    @pytest.mark.parametrize(
        ("literal", "repaired"),
        [
            ("Point { x: n, y }", "Point(x: n, y: y)"),
            ("Point { x: n, y: 2 }", "Point(x: n, y: 2)"),
            ("Point { x: n, ..p }", "p with (x: n)"),
        ],
    )
    def test_a_struct_literal(self, literal: str, repaired: str) -> None:
        source = (
            _POINT
            + "func f(n: Int, p: Point) -> Point\n"
            + "    example (1, Point(0, 2)) -> Point(1, 2)\n"
            + f"    let y = 2\n    return {literal}\nend func\n"
        )
        (error,) = _errors(source)
        assert "calling its constructor" in error.message
        assert f"    return {repaired}\n" in _fixed(source)

    @pytest.mark.parametrize(
        "expression",
        [
            "Point { x: n, y: 2 // note\n    }",
            "Point { x: n, /* note */ y: 2 }",
            "{ p with x = n // note\n    }",
            "{ ...p, x: n // note\n    }",
        ],
    )
    def test_a_rewrite_that_would_lose_a_comment_is_not_offered(
        self, expression: str
    ) -> None:
        source = (
            _POINT
            + "func f(n: Int, p: Point) -> Point\n"
            + "    example (1, Point(0, 2)) -> Point(1, 2)\n"
            + f"    return {expression}\nend func\n"
        )
        (error,) = _errors(source)
        assert error.fixes == ()

    @pytest.mark.parametrize(
        ("declaration", "repaired"),
        [
            (
                "type Point = {\n    x: Int,\n    y: Int // last\n}",
                "type Point = Point(\n    x: Int,\n    y: Int // last\n)",
            ),
            (
                "type Point = {\n    x: Int;\n    y: Int; // last\n}",
                "type Point = Point(\n    x: Int,\n    y: Int // last\n)",
            ),
            (
                "type Point = { /* a point */ x: Int, y: Int }",
                "type Point = Point( /* a point */ x: Int, y: Int)",
            ),
        ],
    )
    def test_a_record_type_fix_keeps_comments(
        self, declaration: str, repaired: str
    ) -> None:
        source = (
            declaration
            + "\n\nfunc f(p: Point) -> Int\n    example Point(1, 2) -> 1\n"
            + "    return p.x\nend func\n"
        )
        assert _fixed(source).startswith(repaired + "\n")

    def test_a_generic_trait_is_one_error(self) -> None:
        source = "trait Container[T]\n    func size(self: Self) -> Int\nend trait\n"
        (error,) = _errors(source)
        assert "cannot take type parameters" in error.message

    @pytest.mark.parametrize(
        ("call", "repaired"),
        [
            ("int_to_string(n)", "to_string(n)"),
            ("n.int_to_string()", "n.to_string()"),
            ("string_of_int(n)", "to_string(n)"),
            (
                'to_string(parseInt("4").unwrap_or(n))',
                'to_string(parse_int("4").unwrap_or(n))',
            ),
        ],
    )
    def test_another_languages_conversion_name(self, call: str, repaired: str) -> None:
        source = f'func f(n: Int) -> String\n    example 4 -> "4"\n    return {call}\nend func\n'
        assert f"    return {repaired}\n" in _fixed(source)


class TestInferredReturnType:
    @pytest.mark.parametrize(
        ("function", "signature"),
        [
            (
                "func fib(n: Int)\n    example 10 -> 55\n    if n < 2 then\n"
                "        return n\n    end if\n    return fib(n - 1) + fib(n - 2)\nend func\n",
                "func fib(n: Int) -> Int",
            ),
            (
                "func pairs(xs: List[Int])\n    example [1] -> [(1, 1.0)]\n"
                "    return [(x, x.to_float()) for x in xs]\nend func\n",
                "func pairs(xs: List[Int]) -> List[(Int, Float)]",
            ),
            (
                "func head_of[T](xs: List[T])\n    example [1] -> Some(1)\n"
                "    return xs.first()\nend func\n",
                "func head_of[T](xs: List[T]) -> Option[T]",
            ),
            (
                "func note(x: Int)\n    example 1 -> ()\n    let y = x\nend func\n",
                "func note(x: Int) -> Unit",
            ),
            (
                "type Shape = Circle(r: Float) | Square(s: Float)\n\n"
                "func area(s: Shape)\n    example Circle(1.0) -> 3.0\n    match s with\n"
                "        | Circle(r) -> return 3.0 * r * r\n"
                "        | Square(x) -> return x * x\n    end match\nend func\n",
                "func area(s: Shape) -> Float",
            ),
        ],
    )
    def test_is_the_type_its_returns_have(self, function: str, signature: str) -> None:
        (error,) = _errors(function)
        assert "needs a return type" in error.message
        assert f"{signature}\n" in _fixed(function)

    def test_an_impl_method(self) -> None:
        source = (
            "trait Area\n    func area(self) -> Float\nend trait\n\n"
            "type Sq = Sq(s: Float)\n\nimpl Area for Sq\n    func area(self)\n"
            "        example Sq(2.0) -> 4.0\n        return self.s * self.s\n"
            "    end func\nend impl\n"
        )
        assert "    func area(self) -> Float\n        example" in _fixed(source)

    def test_returns_that_disagree_get_no_fix(self) -> None:
        source = (
            "func f(x: Int)\n    example 1 -> 1\n    if x > 0 then\n        return 1\n"
            '    end if\n    return "no"\nend func\n'
        )
        (error,) = _errors(source)
        assert error.fixes == ()

    def test_a_function_that_only_calls_itself_gets_no_fix(self) -> None:
        source = "func loop(n: Int)\n    example 1 -> 1\n    return loop(n)\nend func\n"
        (error,) = _errors(source)
        assert error.fixes == ()

    def test_every_function_missing_one_is_reported(self) -> None:
        source = (
            'func a()\n    example () -> "a"\n    return "a"\nend func\n\n'
            "func b()\n    example () -> 2\n    return 2\nend func\n"
        )
        errors = _errors(source)
        assert [e.fixes[0].title for e in errors] == ["Add '-> String'", "Add '-> Int'"]
