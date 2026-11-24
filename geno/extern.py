"""Extern functions: typed, capability-gated host interop (proposal 0025).

An ``extern func`` declares a Geno signature and, per target, the host
function that implements it::

    extern func sqrt(x: Float) -> Float with ffi
        python "math:sqrt"
        js "Math.sqrt"
    end func

An ``async extern func`` binds an async host function (Python coroutine or
JS Promise-returning function) and returns ``Async[T]`` in Geno. A top-level
``Result[T, String]`` return type turns host exceptions / rejections into
``Err(message)`` instead of panicking.

This module owns the rules every layer shares: which binding strings are
valid, how a binding splits into what a runtime loads, and which types may
cross the host boundary. A type that may is described by a *shape*, using
the same tuples as typed JSON (``geno/json_shape.py``)::

    ("int",) ("float",) ("string",) ("bool",) ("unit",) ("json",)
    ("list", item) ("tuple", (item, ...)) ("option", item) ("map", value)
    ("ref", key, scope)             # a user record, enum, or tagged union
    ("extern_result", ok_shape)     # top-level Result[T, String] return only

Each backend's runtime converts arguments to host values and validates the
host's result against these shapes, so the backends agree on the mapping.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .json_shape import (
    _MAX_CODEC_DEPTH,
    _MAX_CODEC_NODES,
    _qualified_type,
    _substitute,
)
from .types import (
    BoolType,
    FloatType,
    IntType,
    ListType,
    MapType,
    OptionType,
    ResultType,
    StringType,
    TupleType,
    Type,
    TypeDefInfo,
    UnitType,
    UserType,
    type_children,
)

Shape = tuple[Any, ...]

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


@dataclass(frozen=True)
class ExternCodec:
    """Shapes and reachable user types for one extern function."""

    params: tuple[Shape, ...]
    result: Shape
    types: Mapping[str, Shape]


class ExternShapeBuilder:
    """Build boundary shapes and reachable user-type descriptors for an extern."""

    def __init__(self, type_defs: Mapping[str, TypeDefInfo]) -> None:
        self.type_defs = type_defs
        self.types: dict[str, Shape] = {}
        self.depth = 0
        self.nodes = 0

    def fail(self, type_: Type, reason: str, where: tuple[str, ...]) -> ExternTypeError:
        inside = f" (in {' of '.join(reversed(where))})" if where else ""
        return ExternTypeError(
            f"{type_} cannot cross the host boundary: {reason}{inside}"
        )

    def param_shape(self, type_: Type) -> Shape:
        """Shape for a parameter type."""
        saved = dict(self.types)
        try:
            self._check_type_size(type_)
            return self.shape(type_, ())
        except ExternTypeError:
            self.types = saved
            raise

    def return_shape(self, type_: Type) -> Shape:
        """Shape for a return type, allowing top-level ``Result[T, String]``."""
        saved = dict(self.types)
        try:
            self._check_type_size(type_)
            if isinstance(type_, ResultType):
                if not isinstance(type_.err_type, StringType):
                    raise self.fail(
                        type_,
                        "an extern returning Result must use String as its error "
                        "type (Result[T, String])",
                        (),
                    )
                return ("extern_result", self.shape(type_.ok_type, ()))
            return self.shape(type_, ())
        except ExternTypeError:
            self.types = saved
            raise

    def shape(self, type_: Type, where: tuple[str, ...]) -> Shape:
        self.depth += 1
        self.nodes += 1
        if self.depth > _MAX_CODEC_DEPTH or self.nodes > _MAX_CODEC_NODES:
            raise ExternTypeError("its type is too complex to cross the host boundary")
        try:
            return self._shape(type_, where)
        finally:
            self.depth -= 1

    def _shape(self, type_: Type, where: tuple[str, ...]) -> Shape:
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
            return ("list", self.shape(type_.element_type, where))
        if isinstance(type_, TupleType):
            return (
                "tuple",
                tuple(self.shape(item, where) for item in type_.element_types),
            )
        if isinstance(type_, OptionType):
            return ("option", self.shape(type_.value_type, where))
        if isinstance(type_, MapType):
            if not isinstance(type_.key_type, StringType):
                raise self.fail(type_, "a Map's keys must be String", where)
            return ("map", self.shape(type_.value_type, where))
        if isinstance(type_, ResultType):
            raise self.fail(
                type_,
                "Result[T, String] is only allowed as the top-level return type "
                "of an extern function",
                where,
            )
        if isinstance(type_, UserType):
            self._check_type_size(type_)
            return self._user_type(type_, where)
        raise self.fail(
            type_,
            "an extern takes and returns Int, Float, String, Bool, Unit, List, "
            "tuples, Option, Map[String, V], JsonValue, and records/enums/unions "
            "built from them (or returns Result[T, String])",
            where,
        )

    def _check_type_size(self, type_: Type) -> None:
        pending = [(type_, 1)]
        while pending:
            current, depth = pending.pop()
            self.nodes += 1
            if depth > _MAX_CODEC_DEPTH or self.nodes > _MAX_CODEC_NODES:
                raise ExternTypeError(
                    "its type is too complex to cross the host boundary"
                )
            parts = type_children(current)
            if parts is not None:
                pending.extend((child, depth + 1) for child in parts[0])

    def _user_type(self, type_: UserType, where: tuple[str, ...]) -> Shape:
        if type_.name == "JsonValue":
            return ("json",)
        info = self.type_defs.get(type_.definition_scope or type_.name)
        if info is None or not info.variants:
            raise self.fail(type_, "it is not a data type", where)
        key = str(_qualified_type(type_))
        reference = ("ref", key, info.json_constructor_scope)
        if key in self.types:
            return reference
        if len(type_.type_args) != len(info.type_params):
            raise self.fail(type_, "its type arguments are not known", where)
        bindings = dict(zip(info.type_params, type_.type_args))
        # Reserve the key first so a recursive field refers back to it.
        self.types[key] = ()
        variants = []
        for constructor, fields in info.variants.items():
            described = []
            for name, field_type in fields:
                described.append(
                    (
                        name,
                        self.shape(
                            _substitute(field_type, bindings),
                            (*where, f"field '{name}' of {constructor}"),
                        ),
                    )
                )
            variants.append((constructor, tuple(described)))
        if all(not fields for _, fields in variants):
            self.types[key] = ("enum", tuple(name for name, _ in variants))
        elif len(variants) == 1:
            self.types[key] = ("record", variants[0][0], variants[0][1])
        else:
            self.types[key] = ("union", tuple(variants))
        return reference


def marshal_shape(
    type_: Type, type_defs: Mapping[str, TypeDefInfo] | None = None
) -> Shape:
    """The shape of *type_* at the host boundary.

    Raises ``ExternTypeError`` naming the component that cannot cross.
    """
    return ExternShapeBuilder(type_defs or {}).param_shape(type_)
