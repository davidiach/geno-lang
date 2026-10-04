"""Geno value snapshots shared by the interpreter and pure builtin helpers."""

from types import MappingProxyType
from typing import Any

from .values import ConstructorValue


def copy_value(
    value: Any, memo: dict[int, Any] | None = None, *, share: bool = False
) -> Any:
    """Deep-copy value containers while preserving explicit reference types.

    By default ``memo`` only holds the containers on the current recursion
    path: a cycle still terminates, but two siblings that referenced one
    object get independent copies.  A snapshot that may later be written in
    place (``var``, assignment, a field or element store) needs that, or a
    nested write through one sibling shows up in the other (#129).

    ``share=True`` keeps one copy per original object instead, so a value
    built from shared parts (a tree whose children are one node) stays
    linear in size.  Only use it for snapshots nothing can write through,
    such as ``let`` bindings and module constants.
    """
    if memo is None:
        memo = {}

    if isinstance(value, list):
        value_id = id(value)
        if value_id in memo:
            return memo[value_id]
        copied_list: list[Any] = []
        memo[value_id] = copied_list
        try:
            copied_list.extend(copy_value(v, memo, share=share) for v in value)
        finally:
            if not share:
                del memo[value_id]
        return copied_list
    if isinstance(value, dict):
        value_id = id(value)
        if value_id in memo:
            return memo[value_id]
        copied_dict: dict[Any, Any] = {}
        memo[value_id] = copied_dict
        try:
            for key, nested_value in value.items():
                copied_dict[copy_value(key, memo, share=share)] = copy_value(
                    nested_value, memo, share=share
                )
        finally:
            if not share:
                del memo[value_id]
        return copied_dict
    if isinstance(value, tuple):
        return tuple(copy_value(v, memo, share=share) for v in value)
    if isinstance(value, ConstructorValue):
        value_id = id(value)
        if value_id in memo:
            return memo[value_id]
        copied_ctor = ConstructorValue(value.constructor, {})
        memo[value_id] = copied_ctor
        try:
            new_fields = {
                k: copy_value(v, memo, share=share) for k, v in value.fields.items()
            }
        finally:
            if not share:
                del memo[value_id]
        object.__setattr__(copied_ctor, "_fields", MappingProxyType(new_fields))
        return copied_ctor
    # Scalars and explicit reference types (Array/Vec/MutableMap/Set/Closure/etc.)
    # intentionally preserve identity across bindings.
    return value
