"""``json_decode`` and ``json_encode`` on the interpreter (proposal 0021).

The walk over a value and its codec is the compiled Python runtime's
(``geno/_runtime_support.py``); this module supplies the interpreter's value
representation to it, so the two cannot decode or encode differently.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from . import _runtime_support as _runtime
from .json_shape import JsonCodec
from .values import ConstructorValue

_NODE_FIELDS: dict[str, tuple[str, ...]] = {
    "JsonString": ("value",),
    "JsonInt": ("value",),
    "JsonFloat": ("value",),
    "JsonBool": ("value",),
    "JsonNull": (),
    "JsonArray": ("items",),
    "JsonObject": ("entries",),
}


def _constructor_fields(
    types: Mapping[str, tuple],
) -> dict[tuple[str, str], tuple[str, ...]]:
    """Each user constructor's field names, by type and constructor name."""
    fields: dict[tuple[str, str], tuple[str, ...]] = {}
    for described in types.values():
        owner = described[1]
        if described[0] == "enum":
            for constructor in described[2]:
                fields[(owner, constructor)] = ()
        elif described[0] == "record":
            fields[(owner, described[2])] = tuple(name for name, _ in described[3])
        else:
            for constructor, variant_fields in described[2]:
                fields[(owner, constructor)] = tuple(n for n, _ in variant_fields)
    return fields


class InterpreterJsonOps:
    """The interpreter's values, for the shared typed-JSON walk.

    *builtins* are the interpreter's own bound builtins, which carry its
    collection limit.
    """

    def __init__(
        self, builtins: Mapping[str, Callable[..., Any]], types: Mapping[str, tuple]
    ) -> None:
        self._builtins = builtins
        self._fields = _constructor_fields(types)

    def tag(self, node: ConstructorValue) -> str:
        return node.constructor

    def get(self, value: ConstructorValue, name: str) -> Any:
        return value.fields[name]

    def node(self, tag: str, *values: Any) -> ConstructorValue:
        return ConstructorValue(tag, dict(zip(_NODE_FIELDS[tag], values)))

    def unit(self) -> Any:
        return ()

    def none(self) -> ConstructorValue:
        return ConstructorValue("None", {})

    def some(self, value: Any) -> ConstructorValue:
        return ConstructorValue("Some", {"value": value})

    def ok(self, value: Any) -> ConstructorValue:
        return ConstructorValue("Ok", {"value": value})

    def err(self, value: Any) -> ConstructorValue:
        return ConstructorValue("Err", {"error": value})

    def array(self, items: list[Any]) -> Any:
        return self._builtins["array_from_list"](items)

    def vec(self, items: list[Any]) -> Any:
        return self._builtins["vec_from_list"](items)

    def set(self, items: list[Any]) -> Any:
        return self._builtins["set_from_list"](items)

    def map(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        return dict(pairs)

    def make(self, owner: str, constructor: str, values: list[Any]) -> ConstructorValue:
        return ConstructorValue(
            constructor, dict(zip(self._fields[(owner, constructor)], values))
        )

    def is_variant(
        self, value: Any, constructor: str, owner: str | None = None
    ) -> bool:
        return isinstance(value, ConstructorValue) and value.constructor == constructor

    def array_items(self, value: Any) -> list[Any]:
        return list(self._builtins["array_to_list"](value))

    def vec_items(self, value: Any) -> list[Any]:
        return list(self._builtins["vec_to_list"](value))

    def set_items(self, value: Any) -> list[Any]:
        return list(self._builtins["set_to_list"](value))

    def map_pairs(self, value: dict[str, Any]) -> list[tuple[str, Any]]:
        return list(value.items())


def decode(builtins: Mapping[str, Callable[..., Any]], text: str, codec: JsonCodec):
    """``json_decode(text)`` for the type *codec* describes."""
    return _runtime.json_decode(
        text,
        codec.shape,
        codec.types,
        InterpreterJsonOps(builtins, codec.types),
        builtins["json_parse"],
    )


def encode(
    builtins: Mapping[str, Callable[..., Any]],
    value: Any,
    codec: JsonCodec,
    indent: int | None = None,
) -> str:
    """``json_encode(value)``, or ``json_encode_pretty(value, indent)``."""
    ops = InterpreterJsonOps(builtins, codec.types)
    if indent is None:
        return str(
            _runtime.json_encode(
                value, codec.shape, codec.types, ops, builtins["json_stringify"]
            )
        )
    return str(
        _runtime.json_encode_pretty(
            value,
            indent,
            codec.shape,
            codec.types,
            ops,
            builtins["json_stringify_pretty"],
        )
    )
