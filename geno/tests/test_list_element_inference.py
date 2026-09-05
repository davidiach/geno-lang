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

from geno.api import RunConfig, run
from geno.compiler import compile_to_python
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


@pytest.mark.parametrize(
    ("literal", "return_type", "expected"),
    [
        ("[-1, 2.5]", "List[Float]", [-1.0, 2.5]),
        ("[n, 2.5]", "List[Float]", [1.0, 2.5]),
        ("[1 + 1, 2.5]", "List[Float]", [2.0, 2.5]),
        (
            "[[n, -1], [2.5, 3]]",
            "List[List[Float]]",
            [[1.0, -1.0], [2.5, 3.0]],
        ),
        ("[ints, [2.5]]", "List[List[Float]]", [[1.0], [2.5]]),
    ],
)
def test_inferred_float_lists_materialize_all_int_expressions(
    literal: str, return_type: str, expected: list
) -> None:
    source = (
        f"func main() -> {return_type}\n"
        "    let n = 1\n"
        "    let ints = [n]\n"
        f"    let xs = {literal}\n"
        "    return xs\n"
        "end func\n"
    )
    interpreted = run(source)
    assert interpreted.ok, interpreted.diagnostics
    namespace = {"__name__": "__test__"}
    code = compile(compile_to_python(source), "<geno>", "exec", dont_inherit=True)
    exec(code, namespace)
    compiled = namespace["main"]()

    for value in (interpreted.value_raw, compiled):
        assert value == expected
        leaves = (
            value
            if return_type == "List[Float]"
            else [element for row in value for element in row]
        )
        assert all(type(element) is float for element in leaves)


def test_nested_list_widening_preserves_the_source_int_list() -> None:
    source = """
func main() -> (List[List[Float]], List[Int])
    let ints = [1]
    let widened = [ints, [2.5]]
    return (widened, ints)
end func
"""
    interpreted = run(source)
    assert interpreted.ok, interpreted.diagnostics
    namespace = {"__name__": "__test__"}
    code = compile(compile_to_python(source), "<geno>", "exec", dont_inherit=True)
    exec(code, namespace)

    for widened, original in (interpreted.value_raw, namespace["main"]()):
        assert widened == [[1.0], [2.5]]
        assert type(widened[0][0]) is float
        assert original == [1]
        assert type(original[0]) is int
        assert widened[0] is not original


@pytest.mark.parametrize("value,max_bits", [(8, 3), (18446744073709551616, 64)])
def test_float_literal_promotion_keeps_integer_bit_limit(
    value: int, max_bits: int
) -> None:
    source = f"func main() -> Float\n    return {value}\nend func\n"
    interpreted = run(source, config=RunConfig(max_integer_bits=max_bits))
    assert not interpreted.ok
    assert any(
        "Integer exceeds maximum size" in diagnostic.message
        for diagnostic in interpreted.diagnostics
    )
    namespace = {"__name__": "__test__", "_GENO_MAX_INTEGER_BITS": max_bits}
    code = compile(compile_to_python(source), "<geno>", "exec", dont_inherit=True)
    exec(code, namespace)
    with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
        namespace["main"]()
