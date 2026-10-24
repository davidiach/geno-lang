"""Types inferred from context: lambda parameters, loop variables, destructuring.

Proposal 0007.
"""

from __future__ import annotations

import pytest

from geno.ast_nodes import InferredType
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


def _check(source: str) -> None:
    TypeChecker().check_program(parse(source))


def _errors(source: str) -> list[GenoTypeError]:
    with pytest.raises(GenoTypeError) as info:
        _check(source)
    return list(getattr(info.value, "errors", [info.value]))


def _main(body: str) -> str:
    return "func main() -> Unit\n" + body + "end func\n"


def _assert_parity(source: str, expected: str) -> None:
    interp = _interpreter_output(source)
    py = _compiled_python_output(source)
    js = _compiled_js_output(source) if HAS_NODE else expected
    _assert_expected_backend_outputs(
        label="contextual inference",
        context=source,
        expected=expected,
        interp_out=interp,
        py_out=py,
        js_out=js,
    )


def test_omitted_types_parse_as_inferred() -> None:
    body = (
        parse(
            _main(
                "    let f = fn(x, y: Int) -> y\n"
                "    for x in [1] do\n"
                "        print(x)\n"
                "    end for\n"
                "    let ys = [x for x in [1]]\n"
                "    let (a, b) = (1, 2)\n"
            )
        )
        .definitions[0]
        .body
    )
    assert isinstance(body[0].value.params[0].param_type, InferredType)
    assert not isinstance(body[0].value.params[1].param_type, InferredType)
    assert isinstance(body[1].var_type, InferredType)
    assert isinstance(body[2].value.var_type, InferredType)
    assert isinstance(body[3].type_annotation, InferredType)


def test_function_parameters_still_require_types() -> None:
    with pytest.raises(Exception, match="Expected ':'"):
        parse("func f(x) -> Int\n    example 1 -> 1\n    return x\nend func\n")


@pytest.mark.parametrize(
    "statement",
    [
        "print(map([1, 2], fn(x) -> x * 2))",
        "print([1, 2].map(fn(x) -> x * 2).filter(fn(x) -> x > 2))",
        "print([1, 2].fold(initial: 0, reducer: fn(acc, x) -> acc + x))",
        "let inc: (Int) -> Int = fn(x) -> x + 1",
        "var dec: (Int) -> Int = fn(x) -> x - 1",
        "print(sort_by([3, 1], fn(x) -> 0 - x))",
        # Pipeline stages: the piped value fixes the lambda's types.
        "print([1, 2] |> filter(_, fn(x) -> x > 1))",
        "print([1, 2] |> map(fn(x) -> x * 2) |> map(_, fn(y) -> to_string(y)))",
        "print([1, 2] |> fold(_, 0, fn(acc, x) -> acc + x))",
    ],
)
def test_lambda_parameters_take_their_type_from_context(statement: str) -> None:
    _check(_main(f"    {statement}\n"))


def test_lambda_is_checked_after_the_arguments_that_fix_its_types() -> None:
    _check(
        "func apply[A, B](f: (A) -> B, x: A) -> B\n"
        "    example fn(v: Int) -> v + 1, 1 -> 2\n"
        "    return f(x)\n"
        "end func\n" + _main("    print(apply(fn(x) -> x * 2, 21))\n")
    )


def test_returned_lambda_takes_the_declared_return_type() -> None:
    _check(
        '@untested("returns a closure")\n'
        "func adder(n: Int) -> (Int) -> Int\n"
        "    return fn(x) -> x + n\n"
        "end func\n"
    )


def test_constructor_field_types_a_lambda() -> None:
    _check(
        "type Handler = Handler(name: String, run: (Int) -> Int)\n"
        + _main('    let h = Handler("x", fn(v) -> v + 1)\n    print(h.run(1))\n')
    )


def test_inferred_parameter_is_really_typed() -> None:
    errors = _errors(_main('    print(map([1, 2], fn(x) -> x + "a"))\n'))
    assert "Cannot apply '+' to Int and String" in errors[0].message


@pytest.mark.parametrize(
    "statement",
    ["let f = fn(x) -> x + 1", "print(map([], fn(y) -> y))"],
)
def test_lambda_without_context_reports_one_error(statement: str) -> None:
    errors = _errors(_main(f"    {statement}\n"))
    assert len(errors) == 1
    assert errors[0].error_code == ErrorCode.TYPE_GENERIC_INFERENCE
    assert "annotate it, as in" in errors[0].message


def test_for_and_comprehension_variables_take_the_element_type() -> None:
    errors = _errors(
        _main(
            '    for x in ["a"] do\n'
            "        print(x + 1)\n"
            "    end for\n"
            '    print([y * 2 for y in ["b"]])\n'
        )
    )
    assert len(errors) == 2
    assert all("String and Int" in error.message for error in errors)


def test_destructuring_takes_the_tuple_types() -> None:
    errors = _errors(_main('    let (a, b) = (1, "two")\n    print(a + b)\n'))
    assert "Cannot apply '+' to Int and String" in errors[0].message


def test_destructuring_a_non_tuple_is_rejected() -> None:
    errors = _errors(_main("    let (a, b) = [1, 2]\n"))
    assert "requires a tuple type, got List[Int]" in errors[0].message


def test_inferred_programs_run_identically_on_every_backend() -> None:
    _assert_parity(
        "func apply[A, B](f: (A) -> B, x: A) -> B\n"
        "    example fn(v: Int) -> v + 1, 1 -> 2\n"
        "    return f(x)\n"
        "end func\n"
        + _main(
            "    let xs = [3, 1, 2]\n"
            "    print(xs.map(fn(x) -> x * 10).filter(fn(x) -> x > 10))\n"
            "    print(map(xs, fn(x) -> x * 1.5))\n"
            "    print(xs.fold(initial: 0, reducer: fn(acc, x) -> acc + x))\n"
            "    print(apply(fn(x) -> x * 2, 21))\n"
            "    var total = 0\n"
            "    for x in xs do\n"
            "        total = total + x\n"
            "    end for\n"
            "    print(total)\n"
            "    print([x * x for x in xs if x > 1])\n"
            '    let (a, b) = (1, "two")\n'
            "    print(b + to_string(a))\n"
            "    print(xs.map(fn(x) do\n"
            "        if x > 1 then\n"
            '            return "big"\n'
            "        end if\n"
            '        return "small"\n'
            "    end fn))\n"
        ),
        '[30, 20]\n[4.5, 1.5, 3.0]\n6\n42\n6\n[9, 4]\ntwo1\n["big", "small", "big"]\n',
    )
