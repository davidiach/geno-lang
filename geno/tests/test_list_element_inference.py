"""List element inference must not depend on the order of the elements.

``_check_list_literal`` anchored the element type on ``elements[0]`` and checked
the rest against it.  Int widens to Float but not the reverse, so the anchor
decided the outcome: ``[2.5, 1, 3]`` inferred ``List[Float]`` while ``[1, 2.5,
3]`` -- the same list -- was rejected with "expected Int, got Float".  An
explicit ``List[Float]`` annotation did not help, because the error was raised
during inference, before the annotation was consulted.

The fix widens to the type every element is compatible with, and records that
type on each element so the backends promote the Int literals (the
``_expected_runtime_type`` path) rather than emitting a mixed list.
"""

from __future__ import annotations

import pytest

from geno.lexer import Lexer
from geno.parser import Parser
from geno.typechecker import TypeChecker
from geno.types import GenoTypeError


def _check(source: str) -> list[str]:
    """Type check *source*, returning the error messages it produced.

    ``check_program`` raises on the first error rather than accumulating, so
    the raise is caught and the checker's own error list is read back.
    """
    program = Parser(Lexer(source).tokenize()).parse_program()
    checker = TypeChecker()
    try:
        checker.check_program(program)
    except GenoTypeError:
        pass
    return [str(e) for e in checker.errors]


def _in_main(body: str) -> str:
    return f"func main() -> Unit\n{body}\n    return ()\nend func main\n"


# Lists that must type check, in every element order.
ACCEPTED = [
    pytest.param("[1, 2.5, 3]", id="int_first"),
    pytest.param("[2.5, 1, 3]", id="float_first"),
    pytest.param("[1, 3, 2.5]", id="float_last"),
    pytest.param("[2.5, 3.5]", id="all_float"),
    pytest.param("[1, 2, 3]", id="all_int"),
]


@pytest.mark.parametrize("literal", ACCEPTED)
def test_annotated_float_list_accepts_int_literals_in_any_position(
    literal: str,
) -> None:
    assert _check(_in_main(f"    let xs: List[Float] = {literal}")) == []


@pytest.mark.parametrize("literal", ACCEPTED)
def test_unannotated_list_infers_without_regard_to_order(literal: str) -> None:
    assert _check(_in_main(f"    let xs = {literal}")) == []


def test_int_and_float_orders_agree_on_the_element_type() -> None:
    """The two orderings are the same list and must settle on the same type."""
    from geno.types import FloatType

    def element_types(literal: str) -> list[object]:
        source = _in_main(f"    let xs = {literal}")
        program = Parser(Lexer(source).tokenize()).parse_program()
        checker = TypeChecker()
        checker.check_program(program)
        assert checker.errors == []
        elements = program.definitions[0].body[0].value.elements
        return [getattr(e, "_expected_runtime_type", None) for e in elements]

    int_first = element_types("[1, 2.5, 3]")
    float_first = element_types("[2.5, 1, 3]")
    assert all(isinstance(t, FloatType) for t in int_first), int_first
    assert all(isinstance(t, FloatType) for t in float_first), float_first


@pytest.mark.parametrize(
    "literal",
    [
        pytest.param('[1, "a"]', id="int_then_string"),
        pytest.param('["a", 1]', id="string_then_int"),
        pytest.param('[1, 2.5, "a"]', id="widened_then_string"),
        pytest.param("[true, 1]", id="bool_then_int"),
        pytest.param("[1, true]", id="int_then_bool"),
    ],
)
def test_genuinely_mixed_lists_are_still_rejected(literal: str) -> None:
    """Widening must not turn a real type error into a silent accept."""
    errors = _check(_in_main(f"    let xs = {literal}"))
    assert errors, f"{literal} should not type check"
    assert any("List element type mismatch" in e for e in errors)


def test_int_literals_carry_the_widened_type_to_the_backends() -> None:
    """Without this the inferred List[Float] would render as `[1, 2.5, 3]`."""
    from geno.types import FloatType

    source = _in_main("    let xs = [1, 2.5, 3]")
    program = Parser(Lexer(source).tokenize()).parse_program()
    checker = TypeChecker()
    checker.check_program(program)
    assert checker.errors == []
    elements = program.definitions[0].body[0].value.elements
    for element in elements:
        expected = getattr(element, "_expected_runtime_type", None)
        assert isinstance(expected, FloatType), f"{element} lost the widened type"


# A fresh type variable must not anchor the list either.  `None` types as
# `Option[fresh]`, which is compatible with `Option[Int]` in both directions,
# so a fold that only moves toward a strictly wider type never left it --
# making the Option case order-dependent in exactly the way the Int/Float
# case had been.  Ties are now broken toward the more concrete type.
OPTION_ORDERS = [
    pytest.param("[Some(1), None]", id="concrete_first"),
    pytest.param("[None, Some(1)]", id="type_var_first"),
    pytest.param("[None, Some(1), None]", id="type_var_outside"),
]


@pytest.mark.parametrize("literal", OPTION_ORDERS)
def test_option_element_inference_is_order_independent(literal: str) -> None:
    assert _check(_in_main(f"    let xs = {literal}")) == []


def test_a_list_of_only_type_variables_still_cannot_be_inferred() -> None:
    """Tie-breaking must not invent a concrete type that is not there."""
    errors = _check(_in_main("    let xs = [None, None]"))
    assert errors, "an all-None list has no concrete element type"
