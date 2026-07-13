"""Regression tests for type-soundness issues blocking the first public release."""

import pytest

from geno.parser import parse
from geno.typechecker import TypeChecker
from geno.types import TypeError as GenoTypeError
from geno.types import TypeErrors


def _check(source: str) -> TypeChecker:
    checker = TypeChecker()
    checker.check_program(parse(source))
    return checker


def _check_error(source: str) -> str:
    with pytest.raises((GenoTypeError, TypeErrors)) as exc_info:
        _check(source)
    return str(exc_info.value)


def test_named_call_cannot_skip_a_required_parameter() -> None:
    error = _check_error(
        """
func add(a: Int, b: Int, c: Int = 0) -> Int
    example (1, 2, 0) -> 3
    return a + b + c
end func

func main() -> Int
    return add(a: 1, c: 2)
end func
"""
    )
    assert "Missing argument for parameter 'b'" in error


def test_direct_generic_record_field_is_invariant() -> None:
    error = _check_error(
        """
type Box[T] = Box(v: T)

func main() -> Int
    var ints: Box[Int] = Box(1)
    var floats: Box[Float] = ints
    floats.v = 2.5
    return ints.v & 1
end func
"""
    )
    assert "Box" in error


def test_indexed_callee_propagates_its_effects() -> None:
    error = _check_error(
        """
func main() -> Unit with mutation
    let writers: List[() -> Unit with io] = [fn() do
        print("hello")
        return ()
    end fn]
    writers[0]()
    return ()
end func
"""
    )
    assert "undeclared effects" in error.lower()
    assert "io" in error


def test_malformed_constructor_pattern_reports_error_without_internal_crash() -> None:
    error = _check_error(
        """
func main() -> Int
    let value: Option[Int] = Some(1)
    match value with
    | Some -> return 1
    | None -> return 0
    end match
end func
"""
    )
    assert "Some" in error
    assert "expects 1 fields" in error


def test_duplicate_top_level_functions_are_rejected() -> None:
    error = _check_error(
        """
func value() -> Int
    example () -> 1
    return 1
end func

func value() -> Int
    example () -> 2
    return 2
end func

func main() -> Int
    return value()
end func
"""
    )
    assert "Duplicate function definition: 'value'" in error


@pytest.mark.parametrize("arguments", ["1, 2.5", "2.5, 1"])
def test_numeric_generic_inference_widens_to_float_in_either_order(
    arguments: str,
) -> None:
    _check(
        f"""
func main() -> Float
    return max({arguments})
end func
"""
    )
