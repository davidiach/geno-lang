"""
Geno Type System
===================

Type representations used by the type checker.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

from .tokens import SourceLocation


class GenoTypeError(Exception):
    """Exception raised for Geno type errors.

    Named ``GenoTypeError`` to avoid shadowing Python's built-in ``TypeError``.
    The old name ``TypeError`` is kept as an alias for backward compatibility.
    """

    def __init__(self, message: str, location: SourceLocation, error_code=None):
        self.message = message
        self.location = location
        self.error_code = error_code
        super().__init__(f"{location}: Type Error: {message}")


class TypeErrors(GenoTypeError):
    """Exception containing multiple Geno type errors."""

    def __init__(self, errors: list[GenoTypeError]) -> None:
        if not errors:
            raise ValueError("TypeErrors requires at least one error")
        self.errors = errors
        first = errors[0]
        super().__init__(first.message, first.location, first.error_code)

    def __str__(self) -> str:
        return "\n".join(str(error) for error in self.errors)


# Backward-compatibility alias
TypeError = GenoTypeError


# =============================================================================
# Type Representation
# =============================================================================


@dataclass(frozen=True)
class Type:
    """Base class for internal type representation."""

    pass


@dataclass(frozen=True)
class IntType(Type):
    def __str__(self) -> str:
        return "Int"


@dataclass(frozen=True)
class FloatType(Type):
    def __str__(self) -> str:
        return "Float"


@dataclass(frozen=True)
class BoolType(Type):
    def __str__(self) -> str:
        return "Bool"


@dataclass(frozen=True)
class StringType(Type):
    def __str__(self) -> str:
        return "String"


@dataclass(frozen=True)
class UnitType(Type):
    def __str__(self) -> str:
        return "Unit"


@dataclass(frozen=True)
class ListType(Type):
    element_type: Type

    def __str__(self) -> str:
        return f"List[{self.element_type}]"


@dataclass(frozen=True)
class ArrayType(Type):
    element_type: Type

    def __str__(self) -> str:
        return f"Array[{self.element_type}]"


@dataclass(frozen=True)
class OptionType(Type):
    value_type: Type

    def __str__(self) -> str:
        return f"Option[{self.value_type}]"


@dataclass(frozen=True)
class ResultType(Type):
    ok_type: Type
    err_type: Type

    def __str__(self) -> str:
        return f"Result[{self.ok_type}, {self.err_type}]"


@dataclass(frozen=True)
class TupleType(Type):
    element_types: tuple[Type, ...]

    def __str__(self) -> str:
        types = ", ".join(str(t) for t in self.element_types)
        return f"({types})"


@dataclass(frozen=True)
class FuncType(Type):
    param_types: tuple[Type, ...]
    return_type: Type
    effects: frozenset[str] = frozenset()

    def __str__(self) -> str:
        params = ", ".join(str(t) for t in self.param_types)
        base = f"({params}) -> {self.return_type}"
        if self.effects:
            return f"{base} with {', '.join(sorted(self.effects))}"
        return base


@dataclass(frozen=True)
class MapType(Type):
    key_type: Type
    value_type: Type

    def __str__(self) -> str:
        return f"Map[{self.key_type}, {self.value_type}]"


@dataclass(frozen=True)
class MutableMapType(Type):
    key_type: Type
    value_type: Type

    def __str__(self) -> str:
        return f"MutableMap[{self.key_type}, {self.value_type}]"


@dataclass(frozen=True)
class VecType(Type):
    element_type: Type

    def __str__(self) -> str:
        return f"Vec[{self.element_type}]"


@dataclass(frozen=True)
class SetType(Type):
    element_type: Type

    def __str__(self) -> str:
        return f"Set[{self.element_type}]"


@dataclass(frozen=True)
class TypeVar(Type):
    """Type variable for polymorphism."""

    name: str

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class UserType(Type):
    """User-defined type."""

    name: str
    type_args: tuple[Type, ...] = ()

    def __str__(self) -> str:
        if self.type_args:
            args = ", ".join(str(t) for t in self.type_args)
            return f"{self.name}[{args}]"
        return self.name


@dataclass(frozen=True)
class AnyType(Type):
    """Type for holes and unknown types."""

    def __str__(self) -> str:
        return "Any"


@dataclass(frozen=True)
class NeverType(Type):
    """Bottom type for divergent expressions (throw, unreachable branches).

    Assignable TO any type (a divergent expression can appear anywhere a
    value is expected), but no value can be assigned FROM NeverType.
    """

    def __str__(self) -> str:
        return "Never"


@dataclass(frozen=True)
class ModuleType(Type):
    """Type representing an imported module namespace."""

    module_name: str

    def __str__(self) -> str:
        return f"Module({self.module_name})"


@dataclass(frozen=True)
class AsyncType(Type):
    """Async[T] — the type of an async computation returning T."""

    result_type: Type

    def __str__(self) -> str:
        return f"Async[{self.result_type}]"


# =============================================================================
# Structural type traversal
# =============================================================================


def type_children(
    t: Type,
) -> tuple[tuple[Type, ...], Callable[[tuple[Type, ...]], Type]] | None:
    """Decompose a compound type into (children, rebuild).

    Returns ``None`` for leaf types.  For compound types the *rebuild*
    callable accepts a tuple of new children (same length as the
    returned *children*) and produces the same type constructor with
    updated children.

    This is the single extension point for structural type traversals:
    adding a new ``Type`` subclass with child types requires only one
    branch here.
    """
    if isinstance(t, (ListType, ArrayType, SetType, VecType)):
        container_cls = type(t)
        return (t.element_type,), lambda cs, _c=container_cls: _c(cs[0])
    if isinstance(t, OptionType):
        return (t.value_type,), lambda cs: OptionType(cs[0])
    if isinstance(t, AsyncType):
        return (t.result_type,), lambda cs: AsyncType(cs[0])
    if isinstance(t, ResultType):
        return (t.ok_type, t.err_type), lambda cs: ResultType(cs[0], cs[1])
    if isinstance(t, (MapType, MutableMapType)):
        map_cls = type(t)
        return (t.key_type, t.value_type), lambda cs, _c=map_cls: _c(cs[0], cs[1])
    if isinstance(t, TupleType):
        return tuple(t.element_types), lambda cs: TupleType(cs)
    if isinstance(t, FuncType):
        n = len(t.param_types)
        children = (*t.param_types, t.return_type)
        eff = t.effects
        return children, lambda cs, _n=n, _e=eff: FuncType(cs[:_n], cs[_n], _e)
    if isinstance(t, UserType) and t.type_args:
        nm = t.name
        return tuple(t.type_args), lambda cs, _n=nm: UserType(_n, cs)
    return None


def map_type(t: Type, f: Callable[[Type], Type]) -> Type:
    """Apply *f* to every child type and reconstruct the parent."""
    parts = type_children(t)
    if parts is None:
        return t
    children, rebuild = parts
    return rebuild(tuple(f(c) for c in children))


def any_child(t: Type, pred: Callable[[Type], bool]) -> bool:
    """Return ``True`` if *pred* holds for any child type of *t*."""
    parts = type_children(t)
    if parts is None:
        return False
    children, _ = parts
    return any(pred(c) for c in children)


# =============================================================================
# Type Environment
# =============================================================================


@dataclass
class TypeEnv:
    """Type environment mapping names to types."""

    bindings: dict[str, Type] = field(default_factory=dict)
    parent: Optional["TypeEnv"] = None
    mutable_vars: set[str] = field(default_factory=set)

    def lookup(self, name: str) -> Type | None:
        """Look up a name in the environment."""
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def bind(self, name: str, type_: Type, mutable: bool = False) -> None:
        """Bind a name to a type."""
        self.bindings[name] = type_
        if mutable:
            self.mutable_vars.add(name)

    def is_mutable(self, name: str) -> bool:
        """Check whether the nearest enclosing binding of ``name`` is mutable.

        Scope resolution must stop at the first scope that binds ``name`` —
        otherwise a child `let` that shadows a parent `var` would still be
        treated as mutable (see issue #656, F-0001).
        """
        if name in self.bindings:
            return name in self.mutable_vars
        if self.parent:
            return self.parent.is_mutable(name)
        return False

    def child(self) -> "TypeEnv":
        """Create a child environment."""
        return TypeEnv(parent=self)


@dataclass
class TypeDefInfo:
    """Information about a user-defined type."""

    name: str
    type_params: list[str]
    variants: dict[str, list[tuple[str, Type]]]  # variant_name -> [(field_name, type)]
    invariant_params: frozenset[int] = field(default_factory=frozenset)
