"""Where an Int sits in a Float slot, and how deep (#137).

Geno accepts an ``Int`` wherever a ``Float`` is expected.  The interpreter
and compiled Python must then hold a real float, or the value prints as an
Int (``7`` instead of ``7.0``) where JS, which formats by static type, does
not.  ``float_shape`` turns an expected type into a small description of the
Float positions inside it, for both backends to promote along:

* ``"F"``: the value itself is a Float;
* ``("L", s)``: a list whose elements have shape ``s``;
* ``("O", s)``: an Option whose ``Some`` value has shape ``s``;
* ``("R", ok, err)``: a Result (either part may be ``None``);
* ``("T", (s, ...))``: a tuple, one shape (or ``None``) per element;
* ``("M", key, value)``: a map.

``None`` means nothing inside needs promotion.
"""

from __future__ import annotations

from typing import Any

from .ast_nodes import SimpleType
from .types import FloatType, ListType, MapType, OptionType, ResultType, TupleType


def float_shape(expected: Any) -> Any:
    """Return the Float shape of an expected type, or ``None``."""
    if isinstance(expected, FloatType):
        return "F"
    if isinstance(expected, ListType):
        return _wrap("L", float_shape(expected.element_type))
    if isinstance(expected, OptionType):
        return _wrap("O", float_shape(expected.value_type))
    if isinstance(expected, ResultType):
        return _pair("R", float_shape(expected.ok_type), float_shape(expected.err_type))
    if isinstance(expected, MapType):
        return _pair(
            "M", float_shape(expected.key_type), float_shape(expected.value_type)
        )
    if isinstance(expected, TupleType):
        return _tuple(float_shape(item) for item in expected.element_types)
    if isinstance(expected, SimpleType):
        return _annotation_shape(expected)
    return None


def _annotation_shape(annotation: SimpleType) -> Any:
    params = annotation.type_params
    name = annotation.name
    if name == "Float" and not params:
        return "F"
    if name == "List" and len(params) == 1:
        return _wrap("L", float_shape(params[0]))
    if name == "Option" and len(params) == 1:
        return _wrap("O", float_shape(params[0]))
    if name == "Result" and len(params) == 2:
        return _pair("R", float_shape(params[0]), float_shape(params[1]))
    if name == "Map" and len(params) == 2:
        return _pair("M", float_shape(params[0]), float_shape(params[1]))
    if name == "Tuple" and params:
        return _tuple(float_shape(item) for item in params)
    return None


def _wrap(kind: str, inner: Any) -> Any:
    return None if inner is None else (kind, inner)


def _pair(kind: str, first: Any, second: Any) -> Any:
    if first is None and second is None:
        return None
    return (kind, first, second)


def _tuple(items: Any) -> Any:
    shapes = tuple(items)
    if all(shape is None for shape in shapes):
        return None
    return ("T", shapes)


def promotion_shape(expected: Any, actual: Any) -> Any:
    """Return the Float shape of ``expected`` where ``actual`` may hold an Int.

    Positions whose static type is already ``Float`` are left out, so a value
    that needs no widening is not copied.  An unknown ``actual`` falls back to
    the full ``float_shape``.
    """
    if isinstance(actual, FloatType):
        return None
    if isinstance(expected, ListType) and isinstance(actual, ListType):
        return _wrap("L", promotion_shape(expected.element_type, actual.element_type))
    if isinstance(expected, OptionType) and isinstance(actual, OptionType):
        return _wrap("O", promotion_shape(expected.value_type, actual.value_type))
    if isinstance(expected, ResultType) and isinstance(actual, ResultType):
        return _pair(
            "R",
            promotion_shape(expected.ok_type, actual.ok_type),
            promotion_shape(expected.err_type, actual.err_type),
        )
    if isinstance(expected, MapType) and isinstance(actual, MapType):
        return _pair(
            "M",
            promotion_shape(expected.key_type, actual.key_type),
            promotion_shape(expected.value_type, actual.value_type),
        )
    if (
        isinstance(expected, TupleType)
        and isinstance(actual, TupleType)
        and len(expected.element_types) == len(actual.element_types)
    ):
        return _tuple(
            promotion_shape(item, actual_item)
            for item, actual_item in zip(expected.element_types, actual.element_types)
        )
    return float_shape(expected)
