"""The JSON form of a Geno type, for ``json_decode`` and ``json_encode``.

Typed JSON (proposal 0021) is type-directed: the checker knows the type a
value is decoded to or encoded from, and turns it into a *codec*, a small
description of that type's JSON form. Each backend's runtime walks a value
and a codec together. The codec is the one place the mapping is decided, so
the interpreter, compiled Python and compiled JavaScript cannot disagree
about it.

A shape is a tuple whose first item names its kind::

    ("int",) ("float",) ("string",) ("bool",) ("unit",) ("json",)
    ("list", item) ("array", item) ("vec", item) ("set", item)
    ("tuple", (item, ...))
    ("option", item)
    ("result", ok, err)
    ("map", value)                  # Map[String, V]
    ("unknown",)                    # encoding only: a type no value can have
    ("ref", key)                    # a user type, described in ``types``

and ``types`` maps each user type's key (its written form, ``Box[Int]``) to
one of::

    ("record", constructor, ((field, shape), ...))   # one constructor with fields
    ("enum", (constructor, ...))                      # every constructor nullary
    ("union", ((constructor, ((field, shape), ...)), ...))

A user type refers to itself through its key, so a recursive type is finite.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .types import (
    AnyType,
    ArrayType,
    AsyncType,
    BoolType,
    FloatType,
    FuncType,
    IntType,
    ListType,
    MapType,
    MutableMapType,
    NeverType,
    OptionType,
    ResultType,
    SetType,
    StringType,
    TupleType,
    Type,
    TypeDefInfo,
    TypeParam,
    TypeVar,
    UnitType,
    UserType,
    VecType,
)

Shape = tuple[Any, ...]

# The key a multi-constructor type's object names its constructor under.
# `type` is a Geno keyword, so no field can have this name.
DISCRIMINATOR = "type"
RESULT_OK = "ok"
RESULT_ERR = "err"


class JsonTypeError(Exception):
    """A type, or a component of it, has no JSON form."""


@dataclass(frozen=True)
class JsonCodec:
    """The JSON form of one type: its shape and the user types it reaches."""

    shape: Shape
    types: Mapping[str, Shape]


def _substitute(type_: Type, bindings: Mapping[str, Type]) -> Type:
    """*type_* with a user type's parameters replaced by its arguments."""
    if isinstance(type_, (TypeVar, TypeParam)):
        return bindings.get(type_.name, type_)
    if isinstance(type_, (ListType, ArrayType, VecType, SetType)):
        return type(type_)(_substitute(type_.element_type, bindings))
    if isinstance(type_, OptionType):
        return OptionType(_substitute(type_.value_type, bindings))
    if isinstance(type_, ResultType):
        return ResultType(
            _substitute(type_.ok_type, bindings), _substitute(type_.err_type, bindings)
        )
    if isinstance(type_, TupleType):
        return TupleType(tuple(_substitute(t, bindings) for t in type_.element_types))
    if isinstance(type_, (MapType, MutableMapType)):
        return type(type_)(
            _substitute(type_.key_type, bindings),
            _substitute(type_.value_type, bindings),
        )
    if isinstance(type_, UserType) and type_.type_args:
        return UserType(
            type_.name, tuple(_substitute(arg, bindings) for arg in type_.type_args)
        )
    if isinstance(type_, AsyncType):
        return AsyncType(_substitute(type_.result_type, bindings))
    return type_


class _Builder:
    def __init__(self, type_defs: Mapping[str, TypeDefInfo], encoding: bool) -> None:
        self.type_defs = type_defs
        self.encoding = encoding
        self.types: dict[str, Shape] = {}

    def fail(self, type_: Type, reason: str, where: tuple[str, ...]) -> JsonTypeError:
        inside = f" (in {' of '.join(reversed(where))})" if where else ""
        return JsonTypeError(f"{type_} has no JSON form: {reason}{inside}")

    def shape(self, type_: Type, where: tuple[str, ...]) -> Shape:
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
        if isinstance(type_, ArrayType):
            return ("array", self.shape(type_.element_type, where))
        if isinstance(type_, VecType):
            return ("vec", self.shape(type_.element_type, where))
        if isinstance(type_, SetType):
            return ("set", self.shape(type_.element_type, where))
        if isinstance(type_, TupleType):
            return (
                "tuple",
                tuple(self.shape(item, where) for item in type_.element_types),
            )
        if isinstance(type_, OptionType):
            return ("option", self.shape(type_.value_type, where))
        if isinstance(type_, ResultType):
            return (
                "result",
                self.shape(type_.ok_type, where),
                self.shape(type_.err_type, where),
            )
        if isinstance(type_, MapType):
            if not isinstance(type_.key_type, StringType):
                raise self.fail(
                    type_, "a JSON object's keys are strings, so a Map's must be", where
                )
            return ("map", self.shape(type_.value_type, where))
        if isinstance(type_, UserType):
            return self.user_type(type_, where)
        if isinstance(type_, FuncType):
            raise self.fail(type_, "a function is not data", where)
        if isinstance(type_, AsyncType):
            raise self.fail(type_, "an async computation is not data", where)
        if isinstance(type_, MutableMapType):
            raise self.fail(type_, "use a Map[String, V]", where)
        if isinstance(type_, TypeVar) and self.encoding:
            # The element type of `[]` or of `None` that nothing settled: no
            # value has it, so an encoder never reaches this shape.
            return ("unknown",)
        if isinstance(type_, (TypeVar, TypeParam)):
            raise self.fail(
                type_, "its type is not known where the call is written", where
            )
        if isinstance(type_, (AnyType, NeverType)):
            raise self.fail(type_, "its type is not known", where)
        raise self.fail(type_, "it is not a data type", where)

    def user_type(self, type_: UserType, where: tuple[str, ...]) -> Shape:
        if type_.name == "JsonValue":
            return ("json",)
        key = str(type_)
        if key in self.types:
            return ("ref", key)
        info = self.type_defs.get(type_.name)
        if info is None or not info.variants:
            raise self.fail(type_, "it is not a data type", where)
        bindings = dict(zip(info.type_params, type_.type_args))
        if len(type_.type_args) != len(info.type_params):
            raise self.fail(type_, "its type arguments are not known", where)
        # Reserve the key first: a recursive field refers back to it.
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
        return ("ref", key)


def build_codec(
    type_: Type, type_defs: Mapping[str, TypeDefInfo], *, encoding: bool = False
) -> JsonCodec:
    """The codec of *type_*; raises ``JsonTypeError`` if it has no JSON form.

    With *encoding*, an inference variable nothing settled (the element type
    of an empty list literal) is allowed: no value has that type.
    """
    builder = _Builder(type_defs, encoding)
    shape = builder.shape(type_, ())
    return JsonCodec(shape, builder.types)
