"""Method-call syntax: ``receiver.name(args)`` (proposal 0006)."""

from __future__ import annotations

import pytest

from geno.ast_nodes import FunctionCall, Identifier
from geno.diagnostics import ErrorCode
from geno.parser import parse
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)
from geno.typechecker import TypeChecker
from geno.types import TypeError as GenoTypeError

POINT = """type Point = Point(x: Int, y: Int)

func norm1(p: Point) -> Int
    example Point(1, -2) -> 3
    return abs(p.x) + abs(p.y)
end func

trait Describe
    func describe(self: Self) -> String
end trait

impl Describe for Point
    func describe(self: Point) -> String
        example Point(1, 2) -> "(1, 2)"
        return f"({self.x}, {self.y})"
    end func
end impl

"""


def _check(source: str) -> None:
    TypeChecker().check_program(parse(source))


def _errors(source: str) -> list[GenoTypeError]:
    with pytest.raises(GenoTypeError) as info:
        _check(source)
    return list(getattr(info.value, "errors", [info.value]))


def _assert_parity(source: str, expected: str) -> None:
    interp = _interpreter_output(source)
    py = _compiled_python_output(source)
    js = _compiled_js_output(source) if HAS_NODE else expected
    _assert_expected_backend_outputs(
        label="method call",
        context=source,
        expected=expected,
        interp_out=interp,
        py_out=py,
        js_out=js,
    )


def _main_call(source: str) -> FunctionCall:
    """Type-check *source* and return the first call in `main`'s first statement."""
    program = parse(source)
    TypeChecker().check_program(program)
    main = next(d for d in program.definitions if getattr(d, "name", "") == "main")
    call = main.body[0].expression.arguments[0].value
    assert isinstance(call, FunctionCall)
    return call


@pytest.mark.parametrize(
    ("method_call", "callee"),
    [
        ("[3, 1].length()", "length"),
        ('"a b".length()', "length"),
        ('" a ".trim()', "string_trim"),
        ("Some(1).unwrap_or(0)", "option_unwrap_or"),
        ("[1, 2].map(fn(x: Int) -> x * 2)", "list_map"),
        ("[1, 2].contains(2)", "contains"),
        ("Point(1, 2).norm1()", "norm1"),
        ("Point(1, 2).describe()", "describe"),
    ],
)
def test_method_call_becomes_an_ordinary_call(method_call: str, callee: str) -> None:
    call = _main_call(
        POINT + f"func main() -> Unit\n    print({method_call})\nend func\n"
    )
    assert isinstance(call.function, Identifier)
    assert call.function.name == callee
    assert call._method_call == method_call.split(".")[-1].split("(")[0]


def test_field_of_function_type_wins_over_a_function_of_that_name() -> None:
    _assert_parity(
        "type Handler = Handler(name: String, run: (Int) -> Int)\n"
        "func run(h: Handler, x: Int) -> Int\n"
        '    example Handler("a", fn(v: Int) -> v), 1 -> 0\n'
        "    return 0\n"
        "end func\n"
        "func main() -> Unit\n"
        '    let h = Handler("x", fn(v: Int) -> v + 1)\n'
        "    print(h.run(2))\n"
        "end func\n",
        "3\n",
    )


def test_module_member_calls_are_not_methods() -> None:
    lib = parse(
        "export func double(x: Int) -> Int\n"
        "    example 2 -> 4\n"
        "    return x * 2\n"
        "end func\n"
    )
    program = parse(
        "import Lib\nfunc main() -> Unit\n    print(Lib.double(2))\nend func\n"
    )
    TypeChecker().check_program(program, modules={"Lib": lib})
    main = program.definitions[1]
    call = main.body[0].expression.arguments[0].value
    assert call._method_call is None
    assert type(call.function).__name__ == "FieldAccess"


def test_methods_run_identically_on_every_backend() -> None:
    _assert_parity(
        POINT + "func shout(s: String) -> String\n"
        '    example "a" -> "A!"\n'
        '    return s.to_upper() + "!"\n'
        "end func\n"
        "func main() -> Unit\n"
        "    let xs = [3, 1, 2]\n"
        "    print(xs.map(fn(x: Int) -> x * 10).filter(fn(x: Int) -> x > 10))\n"
        "    print(xs.length())\n"
        "    print(xs.reverse().head())\n"
        '    print("  hi  ".trim().shout())\n'
        '    print("a,b,c".split(",").join("-"))\n'
        "    print(Some(4).map(fn(x: Int) -> x + 1).unwrap_or(0))\n"
        '    let r: Result[Int, String] = Err("bad")\n'
        "    print(r.map_err(fn(e: String) -> e.length()))\n"
        '    let m = map_from_list([("k", 1)])\n'
        '    print(m.get("k"))\n'
        "    let v: Vec[Int] = vec_new()\n"
        "    v.push(5)\n"
        "    v.push(6)\n"
        "    print(v.length())\n"
        "    print(Point(3, -4).norm1())\n"
        "    print(Point(1, 2).describe())\n"
        '    print("hello".replace(old: "l", new: "L"))\n'
        "    print(xs.fold(initial: 0, reducer: fn(acc: Int, x: Int) -> acc + x))\n"
        "    print(2.5.abs())\n"
        "end func\n",
        "[30, 20]\n3\n2\nHI!\na-b-c\n5\nErr(error: 3)\nSome(value: 1)\n2\n7\n"
        "(1, 2)\nheLLo\n6\n2.5\n",
    )


def test_generic_and_bounded_functions_are_methods_too() -> None:
    _assert_parity(
        "func first_or[T](xs: List[T], fallback: T) -> T\n"
        "    example [1], 0 -> 1\n"
        "    match xs with\n"
        "        | [] -> return fallback\n"
        "        | [x, ...] -> return x\n"
        "    end match\n"
        "end func\n"
        "trait Shape\n"
        "    func area(self: Self) -> Float\n"
        "end trait\n"
        "type Sq = Sq(side: Float)\n"
        "impl Shape for Sq\n"
        "    func area(self: Sq) -> Float\n"
        "        example Sq(2.0) -> 4.0\n"
        "        return self.side * self.side\n"
        "    end func\n"
        "end impl\n"
        "func biggest[T: Shape](xs: List[T]) -> Float\n"
        "    example [Sq(1.0)] -> 1.0\n"
        "    var best = 0.0\n"
        "    for s: T in xs do\n"
        "        best = max(best, s.area())\n"
        "    end for\n"
        "    return best\n"
        "end func\n"
        "func main() -> Unit\n"
        "    print([2.0].first_or(0.0))\n"
        "    print([Sq(1.0), Sq(3.0)].biggest())\n"
        "end func\n",
        "2.0\n9.0\n",
    )


def test_unknown_method_suggests_a_close_name() -> None:
    errors = _errors("func main() -> Unit\n    print([1, 2].lenght())\nend func\n")
    assert len(errors) == 1
    assert errors[0].message == (
        "Type List[Int] has no method 'lenght'. Did you mean 'length'?"
    )
    assert errors[0].error_code == ErrorCode.TYPE_UNKNOWN_FIELD


def test_unknown_method_inside_a_lambda_is_reported_once() -> None:
    errors = _errors(
        "func main() -> Unit\n    print([1].map(fn(x: Int) -> x.nope()))\nend func\n"
    )
    assert len(errors) == 1


def test_named_argument_rule_counts_arguments_after_the_receiver() -> None:
    """Two arguments after the receiver stay positional (proposal 0012)."""
    TypeChecker().check_program(
        parse('func main() -> Unit\n    print("hi".replace("h", "H"))\nend func\n')
    )
    errors = _errors(
        "func span(xs: List[Int], start: Int, stop: Int, step: Int) -> List[Int]\n"
        "    example [1], 0, 1, 1 -> [1]\n"
        "    return xs\n"
        "end func\n\n"
        "func main() -> Unit\n    print([1].span(0, 1, 1))\nend func\n"
    )
    assert errors[0].message == (
        "Method 'span' takes 3 arguments after the receiver; "
        "name them: .span(start: ..., stop: ..., step: ...)"
    )


def test_argument_numbers_skip_the_receiver() -> None:
    errors = _errors('func main() -> Unit\n    print([1].take("x"))\nend func\n')
    assert errors[0].message.startswith("Argument 1 type mismatch")


def test_ambiguous_imports_fall_back_to_the_receiver_types_builtin() -> None:
    def module(kind: str) -> object:
        return parse(
            f"export func map(x: {kind}[Int], f: (Int) -> Int) -> {kind}[Int]\n"
            f"    example {'[1]' if kind == 'List' else 'Some(1)'}, fn(v: Int) -> v -> "
            f"{'[1]' if kind == 'List' else 'Some(1)'}\n"
            "    return x\n"
            "end func\n"
        )

    program = parse(
        "import Lists\n"
        "import Options\n"
        "func main() -> Unit\n"
        "    print([1, 2].map(fn(x: Int) -> x * 2))\n"
        "    print(Some(1).map(fn(x: Int) -> x * 2))\n"
        "end func\n"
    )
    TypeChecker().check_program(
        program, modules={"Lists": module("List"), "Options": module("Option")}
    )
    main = program.definitions[2]
    callees = [stmt.expression.arguments[0].value.function.name for stmt in main.body]
    assert callees == ["list_map", "option_map"]
