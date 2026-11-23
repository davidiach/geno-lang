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


class InterpreterJsonOps:
    """The interpreter's values, for the shared typed-JSON walk.

    *builtins* are the interpreter's own bound builtins, which carry its
    collection limit.
    """

    def __init__(self, builtins: Mapping[str, Callable[..., Any]]) -> None:
        self._builtins = builtins

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

    def make(
        self, scope: str, constructor: str, fields: tuple, values: list[Any]
    ) -> ConstructorValue:
        return ConstructorValue(
            constructor, {name: value for (name, _), value in zip(fields, values)}
        )

    def is_variant(self, value: Any, constructor: str, scope: str = "") -> bool:
        return isinstance(value, ConstructorValue) and value.constructor == constructor

    def array_items(self, value: Any) -> list[Any]:
        return list(self._builtins["array_to_list"](value))

    def vec_items(self, value: Any) -> list[Any]:
        return list(self._builtins["vec_to_list"](value))

    def set_items(self, value: Any) -> list[Any]:
        return list(self._builtins["set_to_list"](value))

    def map_pairs(self, value: dict[str, Any]) -> list[tuple[str, Any]]:
        return list(value.items())


def decode(
    builtins: Mapping[str, Callable[..., Any]],
    text: str,
    codec: JsonCodec,
    *,
    check_size: Callable[[int], None],
    step: Callable[[], None],
):
    """``json_decode(text)`` for the type *codec* describes."""
    return _runtime.json_decode(
        text,
        codec.shape,
        codec.types,
        InterpreterJsonOps(builtins),
        builtins["json_parse"],
        check_size,
        step,
    )


def encode(
    builtins: Mapping[str, Callable[..., Any]],
    value: Any,
    codec: JsonCodec,
    indent: int | None = None,
    *,
    check_size: Callable[[int], None],
    step: Callable[[], None],
) -> str:
    """``json_encode(value)``, or ``json_encode_pretty(value, indent)``."""
    ops = InterpreterJsonOps(builtins)
    if indent is None:
        return str(
            _runtime.json_encode(
                value,
                codec.shape,
                codec.types,
                ops,
                lambda node: builtins["json_stringify"](node, _keep_last=True),
                check_size,
                step,
            )
        )
    return str(
        _runtime.json_encode_pretty(
            value,
            indent,
            codec.shape,
            codec.types,
            ops,
            lambda node, width: builtins["json_stringify_pretty"](
                node, width, _keep_last=True
            ),
            check_size,
            step,
        )
    )
