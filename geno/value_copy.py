"""Geno value snapshots shared by the interpreter and pure builtin helpers."""

from types import MappingProxyType
from typing import Any

from .values import ConstructorValue


def copy_value(value: Any, memo: dict[int, Any] | None = None) -> Any:
    """Deep-copy value containers while preserving explicit reference types."""
    if memo is None:
        memo = {}

    if isinstance(value, list):
        value_id = id(value)
        if value_id in memo:
            return memo[value_id]
        copied_list: list[Any] = []
        memo[value_id] = copied_list
        copied_list.extend(copy_value(v, memo) for v in value)
        return copied_list
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in memo:
            return memo[value_id]
        copied_dict: dict[Any, Any] = {}
        memo[value_id] = copied_dict
        for key, nested_value in value.items():
            copied_dict[copy_value(key, memo)] = copy_value(nested_value, memo)
        return copied_dict
    if isinstance(value, tuple):
        return tuple(copy_value(v, memo) for v in value)
    if isinstance(value, ConstructorValue):
        value_id = id(value)
        if value_id in memo:
            return memo[value_id]
        copied_ctor = ConstructorValue(value.constructor, {})
        memo[value_id] = copied_ctor
        new_fields = {k: copy_value(v, memo) for k, v in value.fields.items()}
        object.__setattr__(copied_ctor, "_fields", MappingProxyType(new_fields))
        return copied_ctor
    # Scalars and explicit reference types (Array/Vec/MutableMap/Set/Closure/etc.)
    # intentionally preserve identity across bindings.
    return value
