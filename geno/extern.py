"""Extern functions: typed, capability-gated host interop (proposal 0025).

An ``extern func`` declares a Geno signature and, per target, the host
function that implements it::

    extern func sqrt(x: Float) -> Float with ffi
        python "math:sqrt"
        js "Math.sqrt"
    end func

This module owns the rules every layer shares: which binding strings are
valid, how a binding splits into what a runtime loads, and which types may
cross the host boundary. A type that may is described by a *shape*, the same
tuples typed JSON uses (``geno/json_shape.py``), restricted to::

    ("int",) ("float",) ("string",) ("bool",) ("unit",) ("json",)
    ("list", item) ("tuple", (item, ...)) ("option", item) ("map", value)

Each backend's runtime converts arguments to host values and validates the
host's result against these shapes, so the backends agree on the mapping.
"""

from __future__ import annotations

import re

from .types import (
    BoolType,
    FloatType,
    IntType,
    ListType,
    MapType,
    OptionType,
    StringType,
    TupleType,
    Type,
    UnitType,
    UserType,
)

Shape = tuple

# The effect an extern performs and the capability that lets it run.
FFI = "ffi"

# Binding targets in the order a declaration usually lists them.
EXTERN_TARGETS = ("python", "js")

_PY_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_PY_DOTTED = rf"{_PY_NAME}(?:\.{_PY_NAME})*"
_PYTHON_BINDING = re.compile(rf"({_PY_DOTTED}):({_PY_DOTTED})")
_JS_NAME = r"[A-Za-z_$][A-Za-z0-9_$]*"
_JS_DOTTED = rf"{_JS_NAME}(?:\.{_JS_NAME})*"
_JS_MODULE_BINDING = re.compile(rf"([^#\s]+)#({_JS_DOTTED})")
_JS_GLOBAL_BINDING = re.compile(_JS_DOTTED)

_BINDING_FORMS = {
    "python": '"module:function", such as "math:sqrt"',
    "js": (
        '"module#export", such as "node:crypto#randomUUID", or a global '
        'path, such as "Math.sqrt"'
    ),
}


class ExternTypeError(Exception):
    """A type, or a component of it, cannot cross the host boundary."""


def binding_form(target: str) -> str:
    """How a binding for *target* is written, for error messages."""
    return _BINDING_FORMS[target]


def binding_error(target: str, spec: str) -> str | None:
    """Why *spec* is not a valid binding for *target*, or ``None``."""
    if target == "python":
        if _PYTHON_BINDING.fullmatch(spec):
            return None
    elif target == "js":
        if _JS_MODULE_BINDING.fullmatch(spec) or _JS_GLOBAL_BINDING.fullmatch(spec):
            return None
    else:  # pragma: no cover - the parser only passes known targets
        return f"unknown binding target '{target}'"
    return f"a {target} binding is written {binding_form(target)}, not {spec!r}"


def python_binding(spec: str) -> tuple[str, tuple[str, ...]]:
    """The module a Python binding imports and the attribute path it reads."""
    match = _PYTHON_BINDING.fullmatch(spec)
    assert match is not None, spec
    return match.group(1), tuple(match.group(2).split("."))


def js_binding(spec: str) -> tuple[str | None, tuple[str, ...]]:
    """The module a JS binding requires (``None`` for a global) and its path."""
    match = _JS_MODULE_BINDING.fullmatch(spec)
    if match is not None:
        return match.group(1), tuple(match.group(2).split("."))
    return None, tuple(spec.split("."))


def marshal_shape(type_: Type) -> Shape:
    """The shape of *type_* at the host boundary.

    Raises ``ExternTypeError`` naming the component that cannot cross.
    """
    if isinstance(type_, IntType):
        return ("int",)
    if isinstance(type_, FloatType):
        return ("float",)
    if isinstance(type_, StringType):
        return ("string",)
    if isinstance(type_, BoolType):
        return ("bool",)
    if isinstance(type_, UnitType):
        return ("unit",)
    if isinstance(type_, ListType):
        return ("list", marshal_shape(type_.element_type))
    if isinstance(type_, TupleType):
        return ("tuple", tuple(marshal_shape(item) for item in type_.element_types))
    if isinstance(type_, OptionType):
        return ("option", marshal_shape(type_.value_type))
    if isinstance(type_, MapType):
        if not isinstance(type_.key_type, StringType):
            raise ExternTypeError(
                f"{type_} cannot cross the host boundary: a Map's keys must be String"
            )
        return ("map", marshal_shape(type_.value_type))
    if isinstance(type_, UserType) and type_.name == "JsonValue":
        return ("json",)
    raise ExternTypeError(
        f"{type_} cannot cross the host boundary: an extern takes and returns "
        "Int, Float, String, Bool, Unit, List, tuples, Option, "
        "Map[String, V] and JsonValue"
    )
