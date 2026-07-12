"""
Geno Runtime Values
======================

Runtime value types used by the interpreter and builtins.
Extracted to avoid circular imports between interpreter and builtins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Optional

from .ast_nodes import Parameter, SpecBlock, Statement
from .tokens import SourceLocation


class GenoRuntimeError(Exception):
    """Exception raised for Geno runtime errors.

    Named ``GenoRuntimeError`` to avoid shadowing Python's built-in
    ``RuntimeError``.
    """

    def __init__(
        self,
        message: str,
        location: SourceLocation | None = None,
        error_code: Any | None = None,
    ) -> None:
        self.message = message
        self.location = location
        self.error_code = error_code
        if location:
            super().__init__(f"{location}: Runtime Error: {message}")
        else:
            super().__init__(f"Runtime Error: {message}")


class ContractViolationError(GenoRuntimeError):
    """Uncatchable contract violation from requires/ensures clauses."""


class GenoThrowError(Exception):
    """Exception raised by 'throw' expressions carrying a typed value."""

    def __init__(self, value: Any, location: SourceLocation | None = None) -> None:
        self.thrown_value = value
        self.location = location
        super().__init__(f"Uncaught throw: {value}")


# Backward-compatible alias — existing code can still import RuntimeError
# from this module.  New code should use GenoRuntimeError.
RuntimeError = GenoRuntimeError


class ReturnException(BaseException):
    """Used to implement return statement control flow."""

    def __init__(self, value: Any) -> None:
        self.value = value


class BreakException(BaseException):
    """Used to implement break statement control flow."""

    pass


class ContinueException(BaseException):
    """Used to implement continue statement control flow."""

    pass


class PropagateException(BaseException):
    """Used to implement ? operator early-return control flow."""

    def __init__(self, value: Any) -> None:
        self.value = value


# =============================================================================
# Runtime Values
# =============================================================================


@dataclass
class Closure:
    """A function closure."""

    params: list[Parameter]
    body: list[Statement]
    env: Environment
    name: str | None = None
    specs: SpecBlock | None = None  # requires/ensures clauses
    is_async: bool = False

    def __repr__(self) -> str:
        return f"<function {self.name or 'anonymous'}>"


class AsyncValue:
    """Represents a pending async computation (lazy evaluation in interpreter)."""

    def __init__(self, closure: Closure, args: list[Any]) -> None:
        self.closure = closure
        self.args = args

    def __repr__(self) -> str:
        return f"<async {self.closure.name or 'anonymous'}>"


class ConstructorValue:
    """A value constructed from a type variant.

    Fields are stored in a read-only ``MappingProxyType`` so that
    interpreter-side constructors are effectively immutable, matching the
    ``@dataclass(frozen=True)`` used on the compiler side.
    """

    __slots__ = ("_fields", "constructor")
    constructor: str
    _fields: MappingProxyType[str, Any]

    def __init__(self, constructor: str, fields: dict[str, Any]):
        object.__setattr__(self, "constructor", constructor)
        from types import MappingProxyType

        object.__setattr__(self, "_fields", MappingProxyType(dict(fields)))

    @property
    def fields(self) -> MappingProxyType[str, Any]:
        return self._fields

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"ConstructorValue is immutable — cannot set '{name}'")

    def __repr__(self) -> str:
        if not self._fields:
            return self.constructor
        field_strs = ", ".join(f"{k}: {v!r}" for k, v in self._fields.items())
        return f"{self.constructor}({field_strs})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ConstructorValue):
            return False
        return self.constructor == other.constructor and dict(self._fields) == dict(
            other._fields
        )

    def __hash__(self) -> int:
        try:
            return hash((self.constructor, tuple(sorted(self._fields.items()))))
        except TypeError:
            # Fields contain unhashable values (list, dict)
            raise TypeError(
                f"unhashable type: '{self.constructor}' contains mutable fields"
            ) from None


@dataclass
class BuiltinFunction:
    """A built-in function."""

    name: str
    func: Callable[..., Any]
    arity: int
    param_names: list[str]

    def __repr__(self) -> str:
        return f"<builtin {self.name}>"


# =============================================================================
# Environment
# =============================================================================

# Sentinel for "variable not found" — distinct from None (Geno's Unit value).
_UNBOUND = object()


class Environment:
    """Runtime environment for variable bindings.

    Slotted plain class rather than a dataclass: one environment is
    created per call frame and block scope, so construction, attribute
    access, and the lookup walk are hot paths.
    """

    __slots__ = ("bindings", "mutable", "parent")

    def __init__(
        self,
        bindings: dict[str, Any] | None = None,
        parent: Environment | None = None,
        mutable: set[str] | None = None,
    ) -> None:
        self.bindings: dict[str, Any] = {} if bindings is None else bindings
        self.parent = parent
        # Names bound mutable; allocated lazily on the first mutable bind.
        self.mutable = mutable

    def lookup(self, name: str) -> Any:
        """Look up a variable value.  Returns _UNBOUND if not found."""
        # Local frame checked before the parent walk: most lookups hit the
        # current frame, and the walk's loop scaffolding costs on that path.
        bindings = self.bindings
        if name in bindings:
            return bindings[name]
        env = self.parent
        while env is not None:
            bindings = env.bindings
            if name in bindings:
                return bindings[name]
            env = env.parent
        return _UNBOUND

    def bind(self, name: str, value: Any, mutable: bool = False) -> None:
        """Bind a variable to a value."""
        self.bindings[name] = value
        if mutable:
            if self.mutable is None:
                self.mutable = set()
            self.mutable.add(name)

    def assign(self, name: str, value: Any) -> bool:
        """Assign to an existing mutable variable."""
        # Local frame checked before the parent walk; see lookup.
        bindings = self.bindings
        if name in bindings:
            mutable = self.mutable
            if mutable is not None and name in mutable:
                bindings[name] = value
                return True
            return False
        env = self.parent
        while env is not None:
            bindings = env.bindings
            if name in bindings:
                mutable = env.mutable
                if mutable is not None and name in mutable:
                    bindings[name] = value
                    return True
                return False
            env = env.parent
        return False

    def child(self) -> Environment:
        """Create a child environment."""
        return Environment(parent=self)


# =============================================================================
# Mutable Array
# =============================================================================


class ArrayValue:
    """A mutable fixed-size array.

    Reference type — assignment shares the underlying storage so that
    ``array_set`` mutations are visible through all aliases.
    """

    __slots__ = ("_elements",)
    _elements: list[Any]

    def __init__(self, elements: list[Any]) -> None:
        object.__setattr__(self, "_elements", elements)

    def __len__(self) -> int:
        return len(self._elements)

    def __getitem__(self, index: int) -> Any:
        return self._elements[index]

    def __setitem__(self, index: int, value: Any) -> None:
        self._elements[index] = value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ArrayValue):
            return False
        return self._elements == other._elements

    def __repr__(self) -> str:
        return f"Array({self._elements!r})"


# =============================================================================
# Mutable Map
# =============================================================================


class MutableMapValue:
    """A mutable hash map. Reference type like ArrayValue."""

    __slots__ = ("_data",)
    _data: dict[Any, Any]

    def __init__(self) -> None:
        object.__setattr__(self, "_data", {})

    def __repr__(self) -> str:
        return f"MutableMap({self._data!r})"

    def __len__(self) -> int:
        return len(self._data)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MutableMapValue):
            return False
        return self._data == other._data


# =============================================================================
# Growable List (Vec)
# =============================================================================


class VecValue:
    """A growable mutable list. Reference type like ArrayValue."""

    __slots__ = ("_elements",)
    _elements: list[Any]

    def __init__(self) -> None:
        object.__setattr__(self, "_elements", [])

    def __len__(self) -> int:
        return len(self._elements)

    def __repr__(self) -> str:
        return f"Vec({self._elements!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VecValue):
            return False
        return self._elements == other._elements


# =============================================================================
# Set
# =============================================================================


class SetValue:
    """A mutable set. Reference type like ArrayValue."""

    __slots__ = ("_data",)
    _data: set[Any]

    def __init__(self) -> None:
        object.__setattr__(self, "_data", set())

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"Set({self._data!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SetValue):
            return False
        return self._data == other._data


# =============================================================================
# JSON Serialization
# =============================================================================


def value_to_json(value: Any) -> Any:
    """
    Convert a Geno runtime value to a JSON-serializable Python object.

    Mapping:
        Int, Float, Bool, String -> int, float, bool, str
        Unit (None)              -> None
        List[T]                  -> list
        Tuple                    -> list (tagged: {"_tuple": [...]})
        Map[K,V]                 -> dict (keys converted to strings)
        ConstructorValue         -> {"_constructor": name, "fields": {...}}
        Closure / BuiltinFunction -> {"_function": name}
    """
    return _value_to_json(value, seen=set())


def _value_to_json(value: Any, seen: set[int]) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        # NaN/Infinity are not valid JSON — convert to string representation
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, (int, str)):
        return value

    if isinstance(
        value,
        (
            ArrayValue,
            MutableMapValue,
            VecValue,
            SetValue,
            list,
            tuple,
            dict,
            ConstructorValue,
        ),
    ):
        value_id = id(value)
        if value_id in seen:
            return "[Circular]"
        seen.add(value_id)
        try:
            if isinstance(value, ArrayValue):
                return {
                    "_array": [_value_to_json(item, seen) for item in value._elements]
                }
            if isinstance(value, MutableMapValue):
                return {
                    "_mutable_map": {
                        str(k): _value_to_json(v, seen) for k, v in value._data.items()
                    }
                }
            if isinstance(value, VecValue):
                return {
                    "_vec": [_value_to_json(item, seen) for item in value._elements]
                }
            if isinstance(value, SetValue):
                return {
                    "_set": sorted(
                        [_value_to_json(item, seen) for item in value._data], key=str
                    )
                }
            if isinstance(value, list):
                return [_value_to_json(item, seen) for item in value]
            if isinstance(value, tuple):
                return {"_tuple": [_value_to_json(item, seen) for item in value]}
            if isinstance(value, dict):
                return {str(k): _value_to_json(v, seen) for k, v in value.items()}
            if isinstance(value, ConstructorValue):
                return {
                    "_constructor": value.constructor,
                    "fields": {
                        k: _value_to_json(v, seen) for k, v in value.fields.items()
                    },
                }
        finally:
            seen.remove(value_id)
    if isinstance(value, Closure):
        return {"_function": value.name or "anonymous"}
    if isinstance(value, BuiltinFunction):
        return {"_function": value.name}
    # Fallback: string representation
    return str(value)
