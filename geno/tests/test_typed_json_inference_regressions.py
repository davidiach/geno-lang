"""Regression tests for typed JSON parsing and contextual inference."""

import pytest

from geno.tests.test_typed_json import BACKENDS


@pytest.mark.parametrize("backend", BACKENDS)
def test_shadowed_json_decode_can_still_be_indexed(backend):
    source = """
type Key = One | Two

func from_list() -> Int
    example () -> 42
    let json_decode = [42]
    return json_decode[(0)]
end func

func from_map() -> Int
    example () -> 43
    let json_decode = {One: 43}
    return json_decode[One]
end func

func from_function_map() -> Int
    example () -> 3
    let json_decode = {One: fn(text: String) -> length(text)}
    return json_decode[One]("abc")
end func

func main() -> Unit with io
    print(from_list())
    print(from_map())
    print(from_function_map())
    print(json_decode[Int]("44"))
end func
"""

    assert backend(source) == "42\n43\n3\nOk(value: 44)\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_later_generic_argument_supplies_json_decode_type(backend):
    source = """
func pick[T](decoded: Result[T, String], fallback: T) -> T
    example (Ok(7), 0) -> 7
    match decoded with
        | Ok(value) -> return value
        | Err(_) -> return fallback
    end match
end func

func pick_wrapped[T](decoded: List[Result[T, String]], fallback: T) -> T
    example ([Ok(7)], 0) -> 7
    return pick(decoded[0], fallback)
end func

func main() -> Unit with io
    print(pick(json_decode("1"), 2))
    print(pick(decoded: json_decode("3"), fallback: 4))
    print(pick_wrapped([json_decode("5")], 6))
    print(8 |> pick(json_decode("7"), _))
end func
"""

    assert backend(source) == "1\n3\n5\n7\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_generic_call_result_supplies_nested_json_decode_type(backend):
    source = """
func identity[T](value: T) -> T
    example 0 -> 0
    return value
end func

func main() -> Unit with io
    let result: Result[Int, String] = identity(json_decode("1"))
    print(result)
end func
"""

    assert backend(source) == "Ok(value: 1)\n"
