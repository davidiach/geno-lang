"""Regression coverage for RC typing and runtime conversion defects (#189, #191)."""

import pytest

from geno.api import check
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

BACKENDS = [
    pytest.param(_interpreter_output, id="interpreter"),
    pytest.param(_compiled_python_output, id="python"),
    pytest.param(
        _compiled_js_output,
        id="javascript",
        marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
    ),
]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "callee",
    ["half", "fn(x: Float) -> x / 2", "fn(x: Float) do\nreturn x / 2\nend fn"],
)
def test_contravariant_alias_uses_actual_parameter_type(backend, callee):
    source = f"""
func half(x: Float) -> Float
    example 3.0 -> 1.5
    return x / 2
end func

func main() -> Unit
    let f: (Int) -> Float = {callee}
    print(f(3))
    print(half(3))
    print(map([3], f))
    return ()
end func
"""
    assert backend(source) == "1.5\n1.5\n[1.5]\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_contravariant_alias_of_inferred_float_lambda(backend):
    source = """
func main() -> Unit
    let half: (Float) -> Float = fn(x) -> x / 2
    let f: (Int) -> Float = half
    print(f(3))
    return ()
end func
"""
    assert backend(source) == "1.5\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_contravariant_container_arguments_do_not_change_caller_values(backend):
    source = """
func half(xs: List[Float]) -> Float
    example [3.0] -> 1.5
    return xs[0] / 2
end func

func main() -> Unit
    let f: (List[Int]) -> Float = half
    let xs = [3]
    print(f(xs))
    print(xs)
    return ()
end func
"""
    assert backend(source) == "1.5\n[3]\n"


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    ("alias", "argument_type", "argument", "expression"),
    [
        ("Float", "Int", "3", "x / 2"),
        ("List[Float]", "List[Int]", "[3]", "x[0] / 2"),
    ],
)
@pytest.mark.parametrize("lambda_callee", [False, True], ids=["named", "lambda"])
def test_contravariant_alias_resolves_parameter_type_aliases(
    backend, alias, argument_type, argument, expression, lambda_callee
):
    callee = f"fn(x: Real) -> {expression}" if lambda_callee else "half"
    source = f"""
type Real = {alias}

@untested("parameter alias regression")
func half(x: Real) -> Float
    return {expression}
end func

func main() -> Unit
    let f: ({argument_type}) -> Float = {callee}
    print(f({argument}))
    return ()
end func
"""
    assert backend(source) == "1.5\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_contravariant_async_arguments_are_promoted_before_execution(backend):
    source = """
async func half(x: Float) -> Float
    return x / 2
end func

func main() -> Unit
    let f: (Int) -> Async[Float] = half
    print(await f(3))
    return ()
end func
"""
    assert backend(source) == "1.5\n"


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("annotated", [True, False], ids=["contextual", "inferred"])
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
@pytest.mark.parametrize(
    ("return_type", "first", "second", "expected"),
    [
        ("Option[Int]", "None", "Some(1)", "None\nSome(value: 1)\n"),
        ("Float", "1", "2.5", "1.0\n2.5\n"),
        (
            "Result[Float, String]",
            "Ok(1)",
            'Err("bad")',
            'Ok(value: 1.0)\nErr(error: "bad")\n',
        ),
    ],
)
def test_block_lambda_return_order(
    backend, annotated, reverse, return_type, first, second, expected
):
    # An inferred Result with only Ok(Int) has no Float context to widen it.
    if not annotated and return_type == "Result[Float, String]":
        expected = expected.replace("1.0", "1")
    condition = "not flag" if reverse else "flag"
    arms = (second, first) if reverse else (first, second)
    annotation = f": (Bool) -> {return_type}" if annotated else ""
    source = f"""
func main() -> Unit
    let f{annotation} = fn(flag: Bool) do
        if {condition} then
            return {arms[0]}
        else
            return {arms[1]}
        end if
    end fn
    print(f(true))
    print(f(false))
    return ()
end func
"""
    assert backend(source) == expected


@pytest.mark.parametrize("reverse", [False, True])
def test_block_lambda_still_rejects_incompatible_return_types(reverse):
    arms = ('"bad"', "1") if reverse else ("1", '"bad"')
    source = f"""
func main() -> Unit
    let f = fn(flag: Bool) do
        if flag then
            return {arms[0]}
        else
            return {arms[1]}
        end if
    end fn
    return ()
end func
"""
    result = check(source)
    assert not result.ok
    assert any("Inconsistent return types" in d.message for d in result.diagnostics)


@pytest.mark.parametrize("backend", BACKENDS)
def test_block_lambda_join_preserves_never_and_named_function_control(backend):
    source = """
func choose(flag: Bool) -> Option[Int]
    example true -> None
    example false -> Some(1)
    if flag then
        return None
    end if
    return Some(1)
end func

func main() -> Unit
    let f = fn(flag: Bool) do
        if flag then
            return panic("unreachable")
        end if
        if not flag then
            return Some(1)
        end if
        return None
    end fn
    print(f(false))
    print(choose(false))
    return ()
end func
"""
    assert backend(source) == "Some(value: 1)\nSome(value: 1)\n"
