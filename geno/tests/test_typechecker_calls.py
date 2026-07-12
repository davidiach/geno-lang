"""Tests for extracted typechecker call-resolution helpers."""

from __future__ import annotations

from geno.ast_nodes import FieldAccess, Identifier, IntegerLiteral
from geno.tokens import SourceLocation
from geno.typechecker_calls import resolve_call_parameter_info

LOC = SourceLocation(1, 1, "<test>")


def ident(name: str) -> Identifier:
    return Identifier(location=LOC, name=name)


def test_identifier_call_resolves_default_and_parameter_metadata() -> None:
    info = resolve_call_parameter_info(
        ident("blend"),
        {"blend": ["red", "green", "blue"]},
        {},
        {"blend": 1},
    )

    assert info.default_lookup_name == "blend"
    assert info.default_count == 1
    assert info.param_names == ("red", "green", "blue")


def test_qualified_module_call_resolves_named_argument_metadata() -> None:
    info = resolve_call_parameter_info(
        FieldAccess(location=LOC, target=ident("Math"), field_name="clamp"),
        {},
        {"Math": {"clamp": ["value", "lo", "hi"]}},
        module_default_counts={"Math": {"clamp": 2}},
    )

    assert info.default_lookup_name is None
    assert info.default_count == 2
    assert info.param_names == ("value", "lo", "hi")


def test_unknown_or_dynamic_call_target_has_no_named_argument_metadata() -> None:
    info = resolve_call_parameter_info(
        IntegerLiteral(location=LOC, value=1),
        {"blend": ["red", "green", "blue"]},
        {"Math": {"clamp": ["value", "lo", "hi"]}},
    )

    assert info.default_lookup_name is None
    assert info.default_count == 0
    assert info.param_names == ()
