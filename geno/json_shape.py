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
    ("ref", key, scope)             # a user type, described in ``types``

and ``types`` maps each user type's key (its written form, ``Box[Int]``) to
one of::

    ("record", constructor, ((field, shape, wire, has_default), ...), reject_unknown)
    ("enum", ((constructor, wire), ...))
    ("union", ((constructor, wire, ((field, shape, wire, has_default), ...)), ...), reject_unknown)

A user type refers to itself through its key, so a recursive type is finite.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .types import (
    AnyType,
    ArrayType,
    AsyncType,
    BoolType,
    BytesType,
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
    map_type,
    type_children,
)

Shape = tuple[Any, ...]

# The key a multi-constructor type's object names its constructor under.
# `type` is a Geno keyword, so no field can have this name.
DISCRIMINATOR = "type"
RESULT_OK = "ok"
RESULT_ERR = "err"

VALID_JSON_RENAME_ALL = (
    "camelCase",
    "snake_case",
    "kebab-case",
    "PascalCase",
    "SCREAMING_SNAKE_CASE",
)
VALID_JSON_UNKNOWN = ("ignore", "reject")

_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+")


def rename_wire_name(name: str, style: str) -> str:
    """Convert a Geno field or enum variant name to the requested JSON wire case."""
    words = _WORD_RE.findall(name)
    if not words:
        return name
    if style == "camelCase":
        return str(
            words[0].lower() + "".join(w[:1].upper() + w[1:].lower() for w in words[1:])
        )
    if style == "PascalCase":
        return "".join(w[:1].upper() + w[1:].lower() for w in words)
    if style == "snake_case":
        return "_".join(w.lower() for w in words)
    if style == "kebab-case":
        return "-".join(w.lower() for w in words)
    if style == "SCREAMING_SNAKE_CASE":
        return "_".join(w.upper() for w in words)
    return name


def _variant_wire_name(info: TypeDefInfo, constructor: str, *, is_enum: bool) -> str:
    explicit = info.variant_json_names.get(constructor)
    if explicit is not None:
        return explicit
    if is_enum and info.json_rename_all is not None:
        return rename_wire_name(constructor, info.json_rename_all)
    return constructor


def _field_wire_name(info: TypeDefInfo, constructor: str, field_name: str) -> str:
    explicit = info.field_json_names.get((constructor, field_name))
    if explicit is not None:
        return explicit
    if info.json_rename_all is not None:
        return rename_wire_name(field_name, info.json_rename_all)
    return field_name


def validate_type_json_mapping(info: TypeDefInfo) -> str | None:
    """Return why *info*'s ``@json`` wire mapping is invalid, or ``None``."""
    if not info.variants:
        return None
    is_enum = all(not fields for fields in info.variants.values())
    is_union = not is_enum and len(info.variants) > 1

    if is_enum or is_union:
        seen_tags: dict[str, str] = {}
        for constructor in info.variants:
            wire_tag = _variant_wire_name(info, constructor, is_enum=is_enum)
            if wire_tag in seen_tags:
                return (
                    f"variants '{seen_tags[wire_tag]}' and '{constructor}' both map "
                    f'to JSON tag "{wire_tag}"'
                )
            seen_tags[wire_tag] = constructor

    if not is_enum:
        for constructor, fields in info.variants.items():
            seen_fields: dict[str, str] = {}
            for field_name, _ in fields:
                wire = _field_wire_name(info, constructor, field_name)
                if is_union and wire == DISCRIMINATOR:
                    return (
                        f"field '{field_name}' of {constructor} maps to JSON name "
                        f'"{DISCRIMINATOR}", which conflicts with the union discriminator'
                    )
                if wire in seen_fields:
                    return (
                        f"fields '{seen_fields[wire]}' and '{field_name}' of "
                        f'{constructor} both map to JSON name "{wire}"'
                    )
                seen_fields[wire] = field_name
    return None


# Keep expanded generic instantiations and serialized codec literals bounded.
# Even before an ADT can be looked up, its structural key may repeat part of
# its type arguments many times (for example, Nest[(T, T)]).
_MAX_CODEC_NODES = 4096
_MAX_CODEC_DEPTH = 128


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
    return map_type(type_, lambda child: _substitute(child, bindings))


def _qualified_type(type_: Type) -> Type:
    """Use defining scopes in codec keys, including inside type arguments."""
    if isinstance(type_, UserType):
        return UserType(
            type_.definition_scope or type_.name,
            tuple(_qualified_type(arg) for arg in type_.type_args),
        )
    return map_type(type_, _qualified_type)


class _Builder:
    def __init__(self, type_defs: Mapping[str, TypeDefInfo], encoding: bool) -> None:
        self.type_defs = type_defs
        self.encoding = encoding
        self.types: dict[str, Shape] = {}
        self.depth = 0
        self.nodes = 0

    def fail(self, type_: Type, reason: str, where: tuple[str, ...]) -> JsonTypeError:
        inside = f" (in {' of '.join(reversed(where))})" if where else ""
        return JsonTypeError(f"{type_} has no JSON form: {reason}{inside}")

    def shape(self, type_: Type, where: tuple[str, ...]) -> Shape:
        self.depth += 1
        self.nodes += 1
        if self.depth > _MAX_CODEC_DEPTH or self.nodes > _MAX_CODEC_NODES:
            raise JsonTypeError("its type is too complex to describe")
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
            self.check_type_size(type_)
            return self.user_type(type_, where)
        if isinstance(type_, FuncType):
            raise self.fail(type_, "a function is not data", where)
        if isinstance(type_, AsyncType):
            raise self.fail(type_, "an async computation is not data", where)
        if isinstance(type_, MutableMapType):
            raise self.fail(type_, "use a Map[String, V]", where)
        if isinstance(type_, BytesType):
            raise self.fail(
                type_,
                "use bytes_to_base64 or bytes_to_hex to represent Bytes in JSON",
                where,
            )
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

    def check_type_size(self, type_: Type) -> None:
        """Count expanded type nodes before constructing a printable key."""
        pending = [(type_, 1)]
        while pending:
            current, depth = pending.pop()
            self.nodes += 1
            if depth > _MAX_CODEC_DEPTH or self.nodes > _MAX_CODEC_NODES:
                raise JsonTypeError("its type is too complex to describe")
            parts = type_children(current)
            if parts is not None:
                pending.extend((child, depth + 1) for child in parts[0])

    def user_type(self, type_: UserType, where: tuple[str, ...]) -> Shape:
        if type_.name == "JsonValue":
            return ("json",)
        info = self.type_defs.get(type_.definition_scope or type_.name)
        if info is None or not info.variants:
            raise self.fail(type_, "it is not a data type", where)
        key = str(_qualified_type(type_))
        reference = ("ref", key, info.json_constructor_scope)
        if key in self.types:
            return reference
        bindings = dict(zip(info.type_params, type_.type_args))
        if len(type_.type_args) != len(info.type_params):
            raise self.fail(type_, "its type arguments are not known", where)
        mapping_error = validate_type_json_mapping(info)
        if mapping_error is not None:
            raise self.fail(type_, mapping_error, where)
        # Reserve the key first: a recursive field refers back to it.
        self.types[key] = ()
        is_enum = all(not fields for fields in info.variants.values())
        reject_unknown = info.json_unknown == "reject"
        if is_enum:
            self.types[key] = (
                "enum",
                tuple(
                    (constructor, _variant_wire_name(info, constructor, is_enum=True))
                    for constructor in info.variants
                ),
            )
            return reference
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
                        _field_wire_name(info, constructor, name),
                        (constructor, name) in info.field_defaults,
                    )
                )
            variants.append(
                (
                    constructor,
                    _variant_wire_name(info, constructor, is_enum=False),
                    tuple(described),
                )
            )
        if len(variants) == 1:
            self.types[key] = (
                "record",
                variants[0][0],
                variants[0][2],
                reject_unknown,
            )
        else:
            self.types[key] = ("union", tuple(variants), reject_unknown)
        return reference


def build_codec(
    type_: Type, type_defs: Mapping[str, TypeDefInfo], *, encoding: bool = False
) -> JsonCodec:
    """The codec of *type_*; raises ``JsonTypeError`` if it has no JSON form.

    With *encoding*, an inference variable nothing settled (the element type
    of an empty list literal) is allowed: no value has that type.
    """
    builder = _Builder(type_defs, encoding)
    builder.check_type_size(type_)
    shape = builder.shape(type_, ())
    return JsonCodec(shape, builder.types)
