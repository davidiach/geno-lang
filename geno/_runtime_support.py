"""
Geno Runtime Support
========================
Auto-generated Python code from Geno source.
"""

import math
import re as _re
from dataclasses import dataclass
from dataclasses import fields as _dataclasses_fields
from dataclasses import replace as _dataclasses_replace
from functools import cmp_to_key
from typing import Any, Callable, Generic, Optional, TypeVar, Union

_builtin_zip = zip
_builtin_enumerate = enumerate
_builtin_min = min
_builtin_max = max
_GENO_OBJECT = ().__class__.__mro__[-1]
_GENO_MISSING = _GENO_OBJECT()
_DECIMAL_INT_RE = _re.compile(r"^-?[0-9]+$")
_MAX_SAFE_JS_INT = 2**53 - 1
_MIN_SAFE_JS_INT = -_MAX_SAFE_JS_INT


def _require_safe_js_int(value: int, context: str) -> int:
    if value < _MIN_SAFE_JS_INT or value > _MAX_SAFE_JS_INT:
        raise RuntimeError(f"{context} exceeds JavaScript safe integer range")
    if value.bit_length() > _MAX_INTEGER_BITS:
        raise RuntimeError(f"Integer exceeds maximum size ({value.bit_length()} bits)")
    return value


def _require_int_bit_limit(value: int) -> int:
    if value.bit_length() > _MAX_INTEGER_BITS:
        raise RuntimeError(f"Integer exceeds maximum size ({value.bit_length()} bits)")
    return value


def _require_int(name: str, value: Any, label: str) -> None:
    if not isinstance(value, int):
        raise RuntimeError(f"{name} {label} must be an integer")


def _require_str(name: str, value: Any, label: str | None = None) -> None:
    if not isinstance(value, str):
        if label:
            raise RuntimeError(
                f"{name} {label} must be a string, got {type(value).__name__}"
            )
        raise RuntimeError(f"{name} expects string, got {type(value).__name__}")


def _promote_int_to_float(value: Any) -> Any:
    """Materialise Geno's Int-to-Float compatibility in compiled output."""
    if type(value) is int:
        _require_int_bit_limit(value)
        return float(value)
    return value


def _object_getattribute(obj: Any, name: str) -> Any:
    """Call object.__getattribute__ without relying on exposed builtins."""
    return _GENO_OBJECT.__getattribute__(obj, name)  # type: ignore[call-arg]


def _object_setattr(obj: Any, name: str, value: Any) -> None:
    """Call object.__setattr__ without relying on exposed builtins."""
    _GENO_OBJECT.__setattr__(obj, name, value)  # type: ignore[arg-type, call-arg]


class _SimpleNamespace:
    """Lightweight namespace for module-qualified access in compiled code."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


T = TypeVar("T")
U = TypeVar("U")
K = TypeVar("K")
V = TypeVar("V")
E = TypeVar("E")


# =============================================================================
# Geno-canonical value formatting
# =============================================================================


def _geno_sort_key(value: Any, _seen: set[int] | None = None) -> tuple[Any, ...]:
    if _seen is None:
        _seen = set()

    if isinstance(
        value,
        (list, dict, tuple, _GenoArray, _GenoMutableMap, _GenoVec, _GenoSet),
    ):
        obj_id = id(value)
        if obj_id in _seen:
            return (98, "cycle")
        _seen = _seen | {obj_id}

    if value is None:
        return (0,)
    if isinstance(value, bool):
        return (1, value)
    if isinstance(value, (int, float)):
        return (2, value)
    if isinstance(value, str):
        return (4, value)
    if isinstance(value, tuple):
        return (5, tuple(_geno_sort_key(item, _seen) for item in value))
    if isinstance(value, Constructor):
        constructor_name = type(value).__name__
        if constructor_name == "_None":
            constructor_name = "None"
        fields = tuple(
            (field.name, _geno_sort_key(getattr(value, field.name), _seen))
            for field in _dataclasses_fields(value)
        )
        return (6, constructor_name, fields)
    if isinstance(value, list):
        return (7, tuple(_geno_sort_key(item, _seen) for item in value))
    if isinstance(value, _GenoArray):
        return (8, tuple(_geno_sort_key(item, _seen) for item in value._elements))
    if isinstance(value, _GenoMutableMap):
        entries = sorted(
            (
                (_geno_sort_key(key, _seen), _geno_sort_key(item, _seen))
                for key, item in value._data.items()
            ),
            key=lambda entry: entry[0],
        )
        return (9, tuple(entries))
    if isinstance(value, dict):
        entries = sorted(
            (
                (_geno_sort_key(key, _seen), _geno_sort_key(item, _seen))
                for key, item in value.items()
            ),
            key=lambda entry: entry[0],
        )
        return (10, tuple(entries))
    if isinstance(value, _GenoSet):
        return (
            11,
            tuple(sorted(_geno_sort_key(item, _seen) for item in value._data)),
        )
    if isinstance(value, _GenoVec):
        return (12, tuple(_geno_sort_key(item, _seen) for item in value._elements))
    return (99, type(value).__name__, repr(value))


def _geno_format(
    value: Any,
    _seen: set[int] | None = None,
    *,
    _top_level: bool = True,
) -> str:
    if _seen is None:
        _seen = set()

    container_types = (list, dict, tuple)
    runtime_container_types = (
        _GenoArray,
        _GenoMutableMap,
        _GenoVec,
        _GenoSet,
    )
    if isinstance(value, container_types + runtime_container_types):
        obj_id = id(value)
        if obj_id in _seen:
            if isinstance(value, _GenoArray):
                return "Array([...])"
            if isinstance(value, _GenoMutableMap):
                return "MutableMap({...})"
            if isinstance(value, _GenoVec):
                return "Vec([...])"
            if isinstance(value, _GenoSet):
                return "Set({...})"
            if isinstance(value, dict):
                return "{...}"
            return "[...]" if isinstance(value, list) else "(...)"
        _seen = _seen | {obj_id}

    if value is None:
        return "()"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value if _top_level else repr(value)
    if isinstance(value, Constructor):
        fields = _dataclasses_fields(value)
        if not fields:
            return type(value).__name__
        field_strs = ", ".join(
            f"{field.name}: "
            f"{_geno_format(getattr(value, field.name), _seen, _top_level=False)}"
            for field in fields
        )
        return f"{type(value).__name__}({field_strs})"
    if isinstance(value, _GenoArray):
        elements = ", ".join(
            _geno_format(item, _seen, _top_level=False) for item in value._elements
        )
        return f"Array([{elements}])"
    if isinstance(value, _GenoMutableMap):
        items = ", ".join(
            f"{_geno_format(k, _seen, _top_level=False)}: "
            f"{_geno_format(v, _seen, _top_level=False)}"
            for k, v in value._data.items()
        )
        return f"MutableMap({{{items}}})"
    if isinstance(value, _GenoVec):
        elements = ", ".join(
            _geno_format(item, _seen, _top_level=False) for item in value._elements
        )
        return f"Vec([{elements}])"
    if isinstance(value, _GenoSet):
        elements = ", ".join(
            _geno_format(item, _seen, _top_level=False)
            for item in sorted(value._data, key=_geno_sort_key)
        )
        return f"Set({{{elements}}})"
    if isinstance(value, list):
        elements = ", ".join(
            _geno_format(item, _seen, _top_level=False) for item in value
        )
        return f"[{elements}]"
    if isinstance(value, tuple):
        if not value:
            return "()"
        elements = ", ".join(
            _geno_format(item, _seen, _top_level=False) for item in value
        )
        suffix = "," if len(value) == 1 else ""
        return f"({elements}{suffix})"
    if isinstance(value, dict):
        items = ", ".join(
            f"{_geno_format(k, _seen, _top_level=False)}: "
            f"{_geno_format(v, _seen, _top_level=False)}"
            for k, v in value.items()
        )
        return f"{{{items}}}"
    return str(value)


# =============================================================================
# Constructor Base
# =============================================================================


@dataclass(frozen=True)
class Constructor:
    """Base class for algebraic data type constructors."""

    __slots__ = ()

    def __repr__(self) -> str:
        """Match interpreter's ConstructorValue repr: Name(field: val, ...)."""
        import dataclasses as _dc

        fields = _dc.fields(self)
        if not fields:
            return type(self).__name__
        field_strs = ", ".join(f"{f.name}: {getattr(self, f.name)!r}" for f in fields)
        return f"{type(self).__name__}({field_strs})"


# =============================================================================
# Built-in Types
# =============================================================================


@dataclass(frozen=True, repr=False)
class Some(Constructor, Generic[T]):
    __slots__ = ("value",)
    value: T


@dataclass(frozen=True, repr=False)
class _None(Constructor):
    __slots__ = ()
    pass


# Use None_ to avoid conflict with Python None
None_ = _None()


@dataclass(frozen=True, repr=False)
class Ok(Constructor, Generic[T]):
    __slots__ = ("value",)
    value: T


@dataclass(frozen=True, repr=False)
class Err(Constructor, Generic[E]):
    __slots__ = ("error",)
    error: E


@dataclass(frozen=True, repr=False)
class HttpRequest(Constructor):
    __slots__ = ("body", "headers", "method", "path", "query")
    method: str
    path: str
    query: str
    headers: list
    body: str


@dataclass(frozen=True, repr=False)
class HttpResponse(Constructor):
    __slots__ = ("body", "headers", "status")
    status: int
    body: str
    headers: list


@dataclass(frozen=True, repr=False)
class ProcessResult(Constructor):
    __slots__ = ("exit_code", "stderr", "stdout")
    exit_code: int
    stdout: str
    stderr: str


class _PropagateReturn(Exception):
    """Internal: raised by _propagate() to trigger early return from ?."""

    def __init__(self, value: Any) -> None:
        self.value = value


class _GenoThrow(Exception):
    """Internal: raised by throw expressions to carry a typed error value."""

    def __init__(self, value: Any) -> None:
        self.value = value
        super().__init__(str(value))


class _GenoContractViolation(RuntimeError):
    """Internal: uncatchable requires/ensures failure."""


def _geno_throw(value: Any) -> None:
    """Runtime support for throw expressions."""
    if isinstance(value, str):
        raise RuntimeError(value)
    raise _GenoThrow(value)


def _propagate(val: Any) -> Any:
    """Runtime support for the ? operator."""
    if isinstance(val, Some):
        return val.value
    if isinstance(val, _None):
        raise _PropagateReturn(val)
    if isinstance(val, Ok):
        return val.value
    if isinstance(val, Err):
        raise _PropagateReturn(val)
    raise RuntimeError(
        f"? operator requires Option or Result, got {type(val).__name__}"
    )


# =============================================================================
# Runtime Functions
# =============================================================================


def length(lst: list) -> int:
    return _require_safe_js_int(len(lst), "length result")


def head(lst: list[Any]) -> Any:
    if not lst:
        raise RuntimeError("head of empty list")
    return lst[0]


def tail(lst: list) -> list:
    if not lst:
        raise RuntimeError("tail of empty list")
    return lst[1:]


def append(lst: list[Any], item: Any) -> list[Any]:
    new_len = len(lst) + 1
    if new_len > _MAX_COLLECTION_SIZE:
        raise RuntimeError(
            f"List size exceeds limit ({new_len} > {_MAX_COLLECTION_SIZE})"
        )
    result = [*lst, item]
    return result


def concat(lst1: list, lst2: list) -> list:
    new_len = len(lst1) + len(lst2)
    if new_len > _MAX_COLLECTION_SIZE:
        raise RuntimeError(
            f"List size exceeds limit ({new_len} > {_MAX_COLLECTION_SIZE})"
        )
    return lst1 + lst2


def set_at(lst: list[Any], index: int, value: Any) -> list[Any]:
    if not isinstance(index, int):
        raise RuntimeError("set_at index must be integer")
    if index < 0 or index >= len(lst):
        raise RuntimeError("set_at index out of range")
    updated = list(lst)
    updated[index] = value
    return updated


def slice_(lst: list, start: int, stop: int) -> list:
    start = max(0, start)
    stop = min(len(lst), stop)
    return lst[start:stop]


def filter_(lst: list, pred: Callable) -> list:
    return [x for x in lst if pred(x)]


def map_(lst: list, func: Callable) -> list:
    result = []
    for x in lst:
        y = func(x)
        # Per-element check keeps the int-bit ceiling enforced even if a
        # closure's _safe_* wrappers were ever optimized out.
        _check_collection_size(y)
        result.append(y)
    _check_collection_size(result)
    return result


def fold(lst: list[Any], init: Any, func: Callable[..., Any]) -> Any:
    acc = init
    for x in lst:
        acc = func(acc, x)
        _check_collection_size(acc)
    return acc


def contains(lst: list[Any], item: Any) -> bool:
    return item in lst


def take_while(lst: list, pred: Callable) -> list:
    result = []
    for x in lst:
        if pred(x):
            result.append(x)
        else:
            break
    return result


def all_(lst: list, pred: Callable) -> bool:
    return all(pred(x) for x in lst)


def sort(lst: list, cmp: Callable) -> list:
    return sorted(lst, key=cmp_to_key(cmp))


def sort_by(lst: list, key_fn: Callable) -> list:
    return sorted(lst, key=lambda x: _geno_sort_key(key_fn(x)))


def split(s: str, sep: str) -> list:
    _check_collection_kind("List", _split_result_count("split", s, sep))
    result = s.split(sep)
    return result


def join(lst: list, sep: str) -> str:
    return _join_strings_under_limit("join", lst, sep)


def trim(s: str) -> str:
    return s.strip()


def to_lower(s: str) -> str:
    result = s.lower()
    _check_string_result_size("to_lower", len(result))
    return result


def to_upper(s: str) -> str:
    result = s.upper()
    _check_string_result_size("to_upper", len(result))
    return result


def replace(text: str, old: str, new: str) -> str:
    _check_string_result_size("replace", _replace_result_size(text, old, new))
    return text.replace(old, new)


def ends_with(text: str, suffix: str) -> bool:
    return text.endswith(suffix)


def split_once(s: str, sep: str) -> Any:
    if sep == "":
        raise RuntimeError("split_once: delimiter cannot be empty")
    if sep in s:
        parts = s.split(sep, 1)
        return Some((parts[0], parts[1]))
    return None_


def starts_with(s: str, prefix: str) -> bool:
    return s.startswith(prefix)


def to_chars(s: str) -> list:
    result = list(s)
    _check_collection_size(result)
    return result


def sort_strings(values: list) -> list:
    if len(values) > 100_000:
        raise RuntimeError("sort_strings: list too large (max 100,000 elements)")
    if not all(isinstance(v, str) for v in values):
        raise RuntimeError("sort_strings expects a list of strings")
    return sorted(values)


def divide(a: Any, b: Any) -> Any:
    if b == 0:
        raise RuntimeError("Division by zero")
    if isinstance(a, int) and isinstance(b, int):
        return _int_trunc_divmod(a, b)[0]
    return a / b


def sqrt(x: float) -> float:
    if x < 0:
        raise RuntimeError("sqrt of negative number")
    return math.sqrt(x)


def floor_(x: float) -> int:
    return _require_safe_js_int(math.floor(x), "floor result")


def ceil_(x: float) -> int:
    return _require_safe_js_int(math.ceil(x), "ceil result")


def round_(x: float) -> int:
    return _require_safe_js_int(int(math.floor(x + 0.5)), "round result")


def max_(a: Any, b: Any) -> Any:
    return a if a >= b else b


def is_sorted(lst: list) -> bool:
    return all(lst[i] <= lst[i + 1] for i in range(len(lst) - 1))


def is_positive(x: Any) -> bool:
    return bool(x > 0)


def is_numeric_string(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    return _DECIMAL_INT_RE.fullmatch(s.strip()) is not None


def is_permutation(lst1: list, lst2: list) -> bool:
    if len(lst1) > 100_000 or len(lst2) > 100_000:
        raise RuntimeError("is_permutation: list too large (max 100,000 elements)")
    if len(lst1) != len(lst2):
        return False
    try:
        return sorted(lst1) == sorted(lst2)
    except TypeError:
        used = [False] * len(lst2)
        for left in lst1:
            for index, right in enumerate(lst2):
                if not used[index] and left == right:
                    used[index] = True
                    break
            else:
                return False
        return True


def parse_int(s: str) -> Any:
    if not isinstance(s, str):
        raise RuntimeError(f"parse_int expects string, got {type(s).__name__}")
    if len(s) > 1000:
        raise RuntimeError("parse_int: input string too long (max 1000 characters)")
    trimmed = s.strip()
    if _DECIMAL_INT_RE.fullmatch(trimmed) is None:
        return None_
    try:
        value = int(trimmed)
    except ValueError:
        return None_
    if value < _MIN_SAFE_JS_INT or value > _MAX_SAFE_JS_INT:
        return None_
    return Some(_check_collection_size(value))


def _is_valid_float_str(s: str) -> bool:
    """Check if string matches pattern: -?(digits.?digits? | .digits)"""
    i = 0
    n = len(s)
    if n == 0:
        return False
    if s[i] == "-":
        i += 1
    if i >= n:
        return False
    has_digits_before = False
    has_dot = False
    has_digits_after = False
    while i < n and s[i].isdigit():
        has_digits_before = True
        i += 1
    if i < n and s[i] == ".":
        has_dot = True
        i += 1
    while i < n and s[i].isdigit():
        has_digits_after = True
        i += 1
    if i != n:
        return False
    return has_digits_before or (has_dot and has_digits_after)


def parse_float(s: str) -> Any:
    if not isinstance(s, str):
        raise RuntimeError(f"parse_float expects string, got {type(s).__name__}")
    if len(s) > 1000:
        raise RuntimeError("parse_float: input string too long (max 1000 characters)")
    trimmed = s.strip()
    if not _is_valid_float_str(trimmed):
        return None_
    try:
        value = float(trimmed)
    except ValueError:
        return None_
    if not math.isfinite(value):
        return None_
    return Some(value)


def format_(template: Any, values: Any) -> str:
    if not isinstance(template, str):
        raise RuntimeError(f"format expects string, got {type(template).__name__}")
    if not isinstance(values, list):
        raise RuntimeError(f"format expects list, got {type(values).__name__}")
    parts = template.split("{}")
    if len(parts) - 1 != len(values):
        raise RuntimeError(
            f"format: expected {len(parts) - 1} values, got {len(values)}"
        )
    value_parts = []
    for val in values:
        # Reject huge ints before str-ing them, so a 10-million-digit
        # int doesn't get converted to string just to trip the next
        # collection-size check.
        _check_collection_size(val)
        value_parts.append(str(val))
    size = sum(len(part) for part in parts) + sum(len(part) for part in value_parts)
    _check_string_result_size("format", size)
    result = parts[0]
    for i, val in enumerate(value_parts):
        result += val + parts[i + 1]
    return result


def to_string(x: Any) -> str:
    result = _geno_format(x)
    _check_string_result_size("to_string", len(result))
    return result


def square(x: Any) -> Any:
    return x * x


def add(a: Any, b: Any) -> Any:
    return a + b


def subtract(a: Any, b: Any) -> Any:
    return a - b


def multiply(a: Any, b: Any) -> Any:
    return a * b


def abs_(x: Any) -> Any:
    return abs(x)


def reverse(lst: list) -> list:
    return lst[::-1]


def bit_or(a: int, b: int) -> int:
    return a | b


def range_(*args: int) -> list[int]:
    if len(args) == 2:
        start, end = args
        step = 1
    elif len(args) == 3:
        start, end, step = args
    else:
        raise RuntimeError(f"range expects 2 or 3 arguments, got {len(args)}")
    _require_int("range", start, "start")
    _require_int("range", end, "end")
    _require_int("range", step, "step")
    if step == 0:
        raise RuntimeError("range step cannot be zero")
    if step > 0 and start >= end:
        return []
    if step < 0 and start <= end:
        return []
    size = abs((end - start + step - (1 if step > 0 else -1)) // step)
    if size > _MAX_COLLECTION_SIZE:
        raise RuntimeError(f"List size exceeds limit ({size} > {_MAX_COLLECTION_SIZE})")
    return [
        _require_safe_js_int(value, "range result") for value in range(start, end, step)
    ]


def substring(s: str, start: int, stop: int) -> str:
    _require_str("substring", s)
    _require_int("substring", start, "start")
    _require_int("substring", stop, "stop")
    start = max(0, start)
    stop = min(len(s), stop)
    return s[start:stop]


def char_code(s: Any) -> int:
    if not isinstance(s, str):
        raise RuntimeError("char_code expects string")
    if len(s) == 0:
        raise RuntimeError("char_code: empty string")
    return _require_safe_js_int(ord(s[0]), "char_code result")


def from_char_code(n: Any) -> str:
    if not isinstance(n, int):
        raise RuntimeError("from_char_code expects integer")
    if n < 0 or n > 0x10FFFF:
        raise RuntimeError(f"from_char_code: code point {n} out of range")
    return chr(n)


def float_to_int(x: float) -> int:
    return _require_safe_js_int(int(x), "float_to_int result")


def int_to_float(x: int) -> float:
    return float(x)


def is_some(opt: Any) -> bool:
    return isinstance(opt, Some)


def is_none(opt: Any) -> bool:
    if isinstance(opt, _None):
        return True
    return opt is None


def unwrap(opt: Any) -> Any:
    if isinstance(opt, Some):
        return opt.value
    if isinstance(opt, _None) or opt is None:
        raise RuntimeError("unwrap called on None")
    raise RuntimeError(f"unwrap expects Option, got {type(opt).__name__}")


def unwrap_or(opt: Any, default: Any) -> Any:
    if isinstance(opt, Some):
        return opt.value
    if isinstance(opt, _None) or opt is None:
        return default
    raise RuntimeError(f"unwrap_or expects Option, got {type(opt).__name__}")


def map_insert(m: dict[Any, Any], key: Any, value: Any) -> dict[Any, Any]:
    if key not in m:
        _check_collection_kind("Map", len(m) + 1)
    new_map = dict(m)
    new_map[key] = value
    return new_map


def map_get(m: dict[Any, Any], key: Any) -> Any:
    if key in m:
        return Some(m[key])
    return None_


def print_(value: Any) -> None:
    _require_cap("print", "print")
    print(_geno_format(value))
    return None


def _int_trunc_divmod(a: int, b: int) -> tuple[int, int]:
    """Integer division/modulo with truncation-toward-zero semantics."""
    if b == 0:
        raise RuntimeError("Division by zero")
    quotient = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        quotient = -quotient
    remainder = a - (b * quotient)
    return quotient, remainder


def _numeric_mod(a: Any, b: Any) -> Any:
    """Numeric remainder paired with truncation-toward-zero division."""
    if b == 0:
        raise RuntimeError("Division by zero")
    if isinstance(a, int) and isinstance(b, int):
        return _int_trunc_divmod(a, b)[1]
    return a - (b * math.trunc(a / b))


def _safe_div(a: Any, b: Any) -> Any:
    """Division that uses integer division for int/int, true division otherwise."""
    if b == 0:
        raise RuntimeError("Division by zero")
    if isinstance(a, int) and isinstance(b, int):
        return _int_trunc_divmod(a, b)[0]
    return a / b


def _int_div(a: int, b: int) -> int:
    """Fast truncation-toward-zero division (types known at compile time)."""
    if b == 0:
        raise RuntimeError("Division by zero")
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _int_mod(a: int, b: int) -> int:
    """Fast remainder paired with truncation-toward-zero division
    (types known at compile time)."""
    if b == 0:
        raise RuntimeError("Division by zero")
    r = a % b
    if r != 0 and (a < 0) != (b < 0):
        r -= b
    return r


def _float_div(a: float, b: float) -> float:
    """Fast float division (types known at compile time)."""
    if b == 0:
        raise RuntimeError("Division by zero")
    return a / b


def _safe_sub(a: Any, b: Any) -> Any:
    """Subtraction with integer overflow guard."""
    result = a - b
    if isinstance(result, int) and result.bit_length() > _MAX_INTEGER_BITS:
        raise RuntimeError(f"Integer exceeds maximum size ({result.bit_length()} bits)")
    return result


def _div_zero() -> None:
    """Cold path: raise Geno's standard division-by-zero error."""
    raise RuntimeError("Division by zero")


def _int_oob(value: int) -> int:
    """Cold path: raise Geno's standard integer-size error.

    Backs the inline integer-bits guards the compiler emits for statically
    Int arithmetic; message must match _check_int_bits / _safe_add.
    """
    raise RuntimeError(f"Integer exceeds maximum size ({value.bit_length()} bits)")


def _list_size_exceeded(size: int) -> None:
    """Cold path: raise Geno's standard list-size error."""
    raise RuntimeError(f"List size exceeds limit ({size} > {_MAX_COLLECTION_SIZE})")


def _safe_mod(a: Any, b: Any) -> Any:
    """Modulo with division-by-zero guard."""
    return _numeric_mod(a, b)


def _check_int_bits(result: Any) -> Any:
    """Enforce integer bit-length limits."""
    if isinstance(result, int) and result.bit_length() > _MAX_INTEGER_BITS:
        raise RuntimeError(f"Integer exceeds maximum size ({result.bit_length()} bits)")
    return result


def _check_pow_result(result: Any) -> Any:
    """Enforce Geno's real-number exponentiation contract."""
    if type(result).__name__ == "complex":
        raise RuntimeError("Exponentiation result is not a real number")
    return _check_int_bits(result)


def _safe_pow(a, b):
    """Exponentiation with pre-check and integer overflow guard."""
    if isinstance(a, int) and isinstance(b, int) and a != 0 and b > 0:
        est_bits = max(a.bit_length(), 1) * b
        if est_bits > _MAX_INTEGER_BITS:
            raise RuntimeError(
                f"Exponentiation result too large (estimated {est_bits} bits)"
            )
    try:
        result = a**b
    except Exception as exc:
        if type(exc).__name__ == "OverflowError":
            raise RuntimeError("Exponentiation result too large") from exc
        if type(exc).__name__ == "ZeroDivisionError":
            raise RuntimeError("Division by zero") from exc
        raise
    return _check_pow_result(result)


def _safe_bitand(a, b):
    """Bitwise-and with integer overflow guard."""
    return _check_int_bits(a & b)


def _safe_bitor(a, b):
    """Bitwise-or with integer overflow guard."""
    return _check_int_bits(a | b)


def _safe_bitxor(a, b):
    """Bitwise-xor with integer overflow guard."""
    return _check_int_bits(a ^ b)


def _safe_lshift(a, b):
    """Left-shift with shift-size pre-check and integer overflow guard."""
    if isinstance(b, int) and b < 0:
        raise RuntimeError("Negative shift count")
    if isinstance(b, int) and b > _MAX_INTEGER_BITS:
        raise RuntimeError(f"Left shift amount too large ({b} bits)")
    return _check_int_bits(a << b)


def _safe_rshift(a, b):
    """Right-shift with shift-size pre-check and integer overflow guard."""
    if isinstance(b, int) and b < 0:
        raise RuntimeError("Negative shift count")
    if isinstance(b, int) and b > _MAX_INTEGER_BITS:
        raise RuntimeError(f"Right shift amount too large ({b} bits)")
    return _check_int_bits(a >> b)


def _safe_invert(a):
    """Bitwise inversion with integer overflow guard."""
    return _check_int_bits(~a)


# =============================================================================
# Pattern Matching Helper
# =============================================================================


def match_constructor(value, constructor_name: str):
    """Check if value matches a constructor name."""
    if isinstance(value, Constructor):
        return type(value).__name__ == constructor_name or (
            constructor_name == "None" and isinstance(value, _None)
        )
    return False


_BLOCKED_FIELD_NAMES = frozenset(
    {
        "__class__",
        "__bases__",
        "__mro__",
        "__subclasses__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__dict__",
        "__self__",
        "__func__",
        "__closure__",
        "__annotations__",
        "__kwdefaults__",
        "__defaults__",
        "__module__",
        "__qualname__",
        "__wrapped__",
        "__init_subclass__",
        "__set_name__",
        "__getattribute__",
        "__subclasshook__",
        # str.format() / str.format_map() perform C-level attribute traversal.
        "format",
        "format_map",
        "gi_frame",
        "gi_code",
        "cr_frame",
        "cr_code",
        "ag_frame",
        "ag_code",
        "f_globals",
        "f_locals",
        "f_builtins",
        "f_code",
        # Traceback/exception chain attributes
        "__traceback__",
        "__cause__",
        "__context__",
        "__suppress_context__",
        "tb_frame",
        "tb_next",
        "tb_lineno",
    }
)


def get_field(value, field_name: str):
    """Get a field from a constructor value.

    Security: Rejects private attributes and blocked attribute names
    to prevent sandbox escape via attribute access.
    """
    if field_name in _BLOCKED_FIELD_NAMES or field_name.startswith("_"):
        raise RuntimeError(
            f"Access to field '{field_name}' is not allowed (private attribute)"
        )
    import dataclasses as _dc

    try:
        value_dict = _object_getattribute(value, "__dict__")
    except AttributeError:
        value_dict = None
    if isinstance(value_dict, dict) and field_name in value_dict:
        return value_dict[field_name]

    try:
        dataclass_fields = _dc.fields(value)
    except TypeError:
        dataclass_fields = ()
    for field in dataclass_fields:
        if field.name == field_name:
            return _object_getattribute(value, field_name)

    for cls in type(value).__mro__:
        if field_name not in cls.__dict__:
            continue
        attr = cls.__dict__[field_name]
        if type(attr).__name__ in {"member_descriptor", "getset_descriptor"}:
            return _object_getattribute(value, field_name)
        type_name = type(value).__name__
        raise RuntimeError(f"'{type_name}' has no field '{field_name}'")

    type_name = type(value).__name__
    raise RuntimeError(f"'{type_name}' has no field '{field_name}'")


# Default collection size limit; can be overridden by injecting
# _GENO_MAX_COLLECTION_SIZE into globals before exec (compile_and_exec)
# or via the GENO_MAX_COLLECTION_SIZE env var (ProcessSandbox worker).
try:
    _MAX_COLLECTION_SIZE = _GENO_MAX_COLLECTION_SIZE  # type: ignore[name-defined]
except NameError:
    _MAX_COLLECTION_SIZE = 10_000_000


# Default integer bit-length ceiling (~10,000 decimal digits); can be
# overridden by injecting _GENO_MAX_INTEGER_BITS into globals before exec
# or via the GENO_MAX_INTEGER_BITS env var forwarded by ProcessSandbox.
try:
    _MAX_INTEGER_BITS = _GENO_MAX_INTEGER_BITS  # type: ignore[name-defined]
except NameError:
    _MAX_INTEGER_BITS = 33_219


def _check_collection_size(result):
    """Raise if a reachable value exceeds configured runtime size limits.

    This backs generated-code expression checks as well as helper return
    checks. Walk nested compiled-runtime containers so a small outer list or
    constructor cannot hide an over-limit inner value.
    """
    stack: list[Any] = [result]
    visited: list[Any] = []
    while stack:
        value = stack.pop()
        if isinstance(value, bool):
            # bool is an int subclass; don't apply the bit-length check to it.
            continue
        if isinstance(value, int):
            if value.bit_length() > _MAX_INTEGER_BITS:
                raise RuntimeError(
                    f"Integer exceeds maximum size ({value.bit_length()} bits)"
                )
            continue
        if isinstance(value, str):
            _check_collection_kind("String", len(value))
            continue

        if any(seen is value for seen in visited):
            continue

        if isinstance(value, list):
            visited.append(value)
            _check_collection_kind("List", len(value))
            stack.extend(value)
            continue
        if isinstance(value, tuple):
            visited.append(value)
            _check_collection_kind("Tuple", len(value))
            stack.extend(value)
            continue
        if isinstance(value, dict):
            visited.append(value)
            _check_collection_kind("Map", len(value))
            stack.extend(value.keys())
            stack.extend(value.values())
            continue

        if isinstance(value, _GenoArray):
            visited.append(value)
            _check_collection_kind("Array", len(value))
            stack.extend(value._elements)
            continue

        if isinstance(value, _GenoVec):
            visited.append(value)
            _check_collection_kind("Vec", len(value))
            stack.extend(value._elements)
            continue

        if isinstance(value, _GenoSet):
            visited.append(value)
            _check_collection_kind("Set", len(value))
            stack.extend(value._data)
            continue

        if isinstance(value, _GenoMutableMap):
            visited.append(value)
            _check_collection_kind("MutableMap", len(value._data))
            stack.extend(value._data.keys())
            stack.extend(value._data.values())
            continue

        if isinstance(value, Constructor):
            visited.append(value)
            stack.extend(
                getattr(value, field.name) for field in _dataclasses_fields(value)
            )
    return result


def _check_collection_kind(kind: str, size: int) -> None:
    if size > _MAX_COLLECTION_SIZE:
        raise RuntimeError(
            f"{kind} size exceeds limit ({size} > {_MAX_COLLECTION_SIZE})"
        )


def _check_string_result_size(func_name: str, size: int) -> None:
    try:
        _check_collection_kind("String", size)
    except RuntimeError as exc:
        raise RuntimeError(f"{func_name}: {exc}") from exc


def _split_result_count(func_name: str, text: str, delimiter: str) -> int:
    if delimiter == "":
        raise RuntimeError(f"{func_name}: delimiter cannot be empty")
    return text.count(delimiter) + 1


def _join_strings_under_limit(func_name: str, parts: list, separator: str) -> str:
    string_parts = [str(part) for part in parts]
    size = sum(len(part) for part in string_parts)
    if len(string_parts) > 1:
        size += len(separator) * (len(string_parts) - 1)
    _check_string_result_size(func_name, size)
    return separator.join(string_parts)


def _replace_result_size(text: str, old: str, new: str) -> int:
    if old == "":
        return len(text) + (len(text) + 1) * len(new)
    return len(text) + text.count(old) * (len(new) - len(old))


def _safe_add(a, b):
    """Addition that enforces collection size limits on str/list results."""
    # Pre-check size before allocation to prevent OOM on huge operands
    if isinstance(a, (str, list)) and isinstance(b, type(a)):
        expected = len(a) + len(b)
        if expected > _MAX_COLLECTION_SIZE:
            kind = "String" if isinstance(a, str) else "List"
            raise RuntimeError(
                f"{kind} size exceeds limit ({expected} > {_MAX_COLLECTION_SIZE})"
            )
    result = a + b
    if isinstance(result, int) and result.bit_length() > _MAX_INTEGER_BITS:
        raise RuntimeError(f"Integer exceeds maximum size ({result.bit_length()} bits)")
    return result


def _safe_mul(a, b):
    """Multiplication that enforces collection size limits on str/list results."""
    # Pre-check size before allocation to prevent OOM on huge multipliers
    if isinstance(a, (str, list)) and isinstance(b, int):
        expected = len(a) * max(b, 0)
        if expected > _MAX_COLLECTION_SIZE:
            kind = "String" if isinstance(a, str) else "List"
            raise RuntimeError(
                f"{kind} size exceeds limit ({expected} > {_MAX_COLLECTION_SIZE})"
            )
    elif isinstance(b, (str, list)) and isinstance(a, int):
        expected = len(b) * max(a, 0)
        if expected > _MAX_COLLECTION_SIZE:
            kind = "String" if isinstance(b, str) else "List"
            raise RuntimeError(
                f"{kind} size exceeds limit ({expected} > {_MAX_COLLECTION_SIZE})"
            )
    result = a * b
    if isinstance(result, int) and result.bit_length() > _MAX_INTEGER_BITS:
        raise RuntimeError(f"Integer exceeds maximum size ({result.bit_length()} bits)")
    return result


class _GenoArray:
    """Mutable fixed-size array (reference type)."""

    __slots__ = ("_elements",)

    def __init__(self, elements):
        self._elements = elements

    def __len__(self):
        return len(self._elements)

    def __getitem__(self, index):
        return self._elements[index]

    def __setitem__(self, index, value):
        self._elements[index] = value

    def __eq__(self, other):
        if not isinstance(other, _GenoArray):
            return False
        return self._elements == other._elements

    def __iter__(self):
        # Snapshot to match interpreter semantics: mutations during iteration
        # must not affect the current loop.
        return iter(list(self._elements))

    def __repr__(self):
        return f"Array({self._elements!r})"


def array_new(size, default):
    if not isinstance(size, int):
        raise RuntimeError("array_new size must be an integer")
    if size < 0:
        raise RuntimeError(f"array_new size must be non-negative, got {size}")
    if size > _MAX_COLLECTION_SIZE:
        raise RuntimeError(
            f"Array size exceeds limit ({size} > {_MAX_COLLECTION_SIZE})"
        )
    return _GenoArray([default] * size)


def array_from_list(lst):
    if not isinstance(lst, list):
        raise RuntimeError(f"array_from_list expects list, got {type(lst).__name__}")
    if len(lst) > _MAX_COLLECTION_SIZE:
        raise RuntimeError(
            f"Array size exceeds limit ({len(lst)} > {_MAX_COLLECTION_SIZE})"
        )
    return _GenoArray(list(lst))


def array_get(arr, index):
    if not isinstance(arr, _GenoArray):
        raise RuntimeError(f"array_get expects array, got {type(arr).__name__}")
    if not isinstance(index, int):
        raise RuntimeError("array_get index must be an integer")
    if index < 0 or index >= len(arr):
        raise RuntimeError(f"array_get index {index} out of bounds (length {len(arr)})")
    return arr[index]


def array_set(arr, index, value):
    if not isinstance(arr, _GenoArray):
        raise RuntimeError(f"array_set expects array, got {type(arr).__name__}")
    if not isinstance(index, int):
        raise RuntimeError("array_set index must be an integer")
    if index < 0 or index >= len(arr):
        raise RuntimeError(f"array_set index {index} out of bounds (length {len(arr)})")
    arr[index] = value
    return None


def array_length(arr):
    if not isinstance(arr, _GenoArray):
        raise RuntimeError(f"array_length expects array, got {type(arr).__name__}")
    return _require_safe_js_int(len(arr), "array_length result")


def array_to_list(arr):
    if not isinstance(arr, _GenoArray):
        raise RuntimeError(f"array_to_list expects array, got {type(arr).__name__}")
    return list(arr._elements)


def array_fill(arr, value):
    if not isinstance(arr, _GenoArray):
        raise RuntimeError(f"array_fill expects array, got {type(arr).__name__}")
    for i in range(len(arr._elements)):
        arr._elements[i] = value
    return None


def array_copy(arr):
    if not isinstance(arr, _GenoArray):
        raise RuntimeError(f"array_copy expects array, got {type(arr).__name__}")
    return _GenoArray(list(arr._elements))


# =============================================================================
# MutableMap
# =============================================================================


class _GenoMutableMap:
    __slots__ = ("_data",)
    _data: dict

    def __init__(self):
        _object_setattr(self, "_data", {})

    def __eq__(self, other):
        if not isinstance(other, _GenoMutableMap):
            return False
        return self._data == other._data


def mutable_map_new():
    return _GenoMutableMap()


def mutable_map_set(m, key, value):
    if not isinstance(m, _GenoMutableMap):
        raise RuntimeError("mutable_map_set expects MutableMap")
    if key not in m._data:
        _check_collection_kind("MutableMap", len(m._data) + 1)
    m._data[key] = value
    return None


def mutable_map_get(m, key):
    if not isinstance(m, _GenoMutableMap):
        raise RuntimeError("mutable_map_get expects MutableMap")
    if key in m._data:
        return Some(m._data[key])
    return None_


def mutable_map_contains(m, key):
    if not isinstance(m, _GenoMutableMap):
        raise RuntimeError("mutable_map_contains expects MutableMap")
    return key in m._data


def mutable_map_delete(m, key):
    if not isinstance(m, _GenoMutableMap):
        raise RuntimeError("mutable_map_delete expects MutableMap")
    m._data.pop(key, None)
    return None


def mutable_map_size(m):
    if not isinstance(m, _GenoMutableMap):
        raise RuntimeError("mutable_map_size expects MutableMap")
    return _require_safe_js_int(len(m._data), "mutable_map_size result")


def mutable_map_keys(m):
    if not isinstance(m, _GenoMutableMap):
        raise RuntimeError("mutable_map_keys expects MutableMap")
    return list(m._data.keys())


# =============================================================================
# Vec (Growable List)
# =============================================================================


class _GenoVec:
    __slots__ = ("_elements",)
    _elements: list

    def __init__(self, elements=None):
        _object_setattr(self, "_elements", elements if elements is not None else [])

    def __len__(self):
        return len(self._elements)

    def __eq__(self, other):
        if not isinstance(other, _GenoVec):
            return False
        return self._elements == other._elements


def vec_new():
    return _GenoVec()


def vec_push(v, item):
    if not isinstance(v, _GenoVec):
        raise RuntimeError("vec_push expects Vec")
    _check_collection_kind("Vec", len(v) + 1)
    v._elements.append(item)
    return None


def vec_get(v, index):
    if not isinstance(v, _GenoVec):
        raise RuntimeError("vec_get expects Vec")
    if not isinstance(index, int):
        raise RuntimeError("vec_get index must be integer")
    if index < 0 or index >= len(v):
        raise RuntimeError(f"vec_get index {index} out of bounds")
    return v._elements[index]


def vec_set(v, index, value):
    if not isinstance(v, _GenoVec):
        raise RuntimeError("vec_set expects Vec")
    if not isinstance(index, int):
        raise RuntimeError("vec_set index must be integer")
    if index < 0 or index >= len(v):
        raise RuntimeError(f"vec_set index {index} out of bounds")
    v._elements[index] = value
    return None


def vec_length(v):
    if not isinstance(v, _GenoVec):
        raise RuntimeError("vec_length expects Vec")
    return _require_safe_js_int(len(v), "vec_length result")


def vec_pop(v):
    if not isinstance(v, _GenoVec):
        raise RuntimeError("vec_pop expects Vec")
    if len(v) == 0:
        return None_
    return Some(v._elements.pop())


def vec_to_list(v):
    if not isinstance(v, _GenoVec):
        raise RuntimeError("vec_to_list expects Vec")
    _check_collection_kind("List", len(v))
    return list(v._elements)


def vec_from_list(lst):
    if not isinstance(lst, list):
        raise RuntimeError("vec_from_list expects list")
    _check_collection_kind("Vec", len(lst))
    return _GenoVec(list(lst))


# =============================================================================
# Set
# =============================================================================


class _GenoSet:
    def __init__(self, data=None):
        self._data = data if data is not None else set()

    def __len__(self):
        return len(self._data)

    def __eq__(self, other):
        if not isinstance(other, _GenoSet):
            return False
        return self._data == other._data


def set_new():
    return _GenoSet()


def set_from_list(lst):
    if not isinstance(lst, list):
        raise RuntimeError("set_from_list expects list")
    data: set[Any] = set()
    for item in lst:
        if item not in data:
            _check_collection_kind("Set", len(data) + 1)
        data.add(item)
    return _GenoSet(data)


def set_add(s, item):
    if not isinstance(s, _GenoSet):
        raise RuntimeError("set_add expects Set")
    if item not in s._data:
        _check_collection_kind("Set", len(s._data) + 1)
    s._data.add(item)
    return None


def set_remove(s, item):
    if not isinstance(s, _GenoSet):
        raise RuntimeError("set_remove expects Set")
    s._data.discard(item)
    return None


def set_contains(s, item):
    if not isinstance(s, _GenoSet):
        raise RuntimeError("set_contains expects Set")
    return item in s._data


def set_size(s):
    if not isinstance(s, _GenoSet):
        raise RuntimeError("set_size expects Set")
    return _require_safe_js_int(len(s._data), "set_size result")


def set_to_list(s):
    if not isinstance(s, _GenoSet):
        raise RuntimeError("set_to_list expects Set")
    _check_collection_kind("List", len(s._data))
    return sorted(s._data, key=_geno_sort_key)


def set_union(a, b):
    if not isinstance(a, _GenoSet) or not isinstance(b, _GenoSet):
        raise RuntimeError("set_union expects two Sets")
    expected = len(a._data)
    for item in b._data:
        if item not in a._data:
            expected += 1
    _check_collection_kind("Set", expected)
    data = a._data | b._data
    return _GenoSet(data)


def set_intersection(a, b):
    if not isinstance(a, _GenoSet) or not isinstance(b, _GenoSet):
        raise RuntimeError("set_intersection expects two Sets")
    return _GenoSet(a._data & b._data)


# =============================================================================
# Graphics (stubs for compiled Python mode)
# =============================================================================


def clear_screen(color):
    return None


def draw_rect(x, y, w, h, color):
    return None


def draw_rect_outline(x, y, w, h, color):
    return None


def draw_circle(x, y, radius, color):
    return None


def draw_line(x1, y1, x2, y2, color):
    return None


def draw_text(text, x, y, size, color):
    return None


def screen_width():
    return _require_safe_js_int(800, "screen_width result")


def screen_height():
    return _require_safe_js_int(600, "screen_height result")


# =============================================================================
# Input (stubs for compiled Python mode)
# =============================================================================


def is_key_down(key):
    return False


def is_key_pressed(key):
    return False


def mouse_x():
    return _require_safe_js_int(0, "mouse_x result")


def mouse_y():
    return _require_safe_js_int(0, "mouse_y result")


def is_mouse_down():
    return False


def is_mouse_clicked():
    return False


def get_text_input():
    return ""


def clear_text_input():
    return None


# =============================================================================
# Clock / Random (lazy imports to avoid sandbox violations)
# =============================================================================


def clock_now():
    _require_cap("clock", "clock_now")
    import time

    return _require_safe_js_int(int(time.time()), "clock_now result")


def sleep_ms(ms):
    """Block for ``ms`` milliseconds. ``ms == 0`` is a yield; negative raises."""
    _require_cap("clock", "sleep_ms")
    if not isinstance(ms, int) or isinstance(ms, bool):
        raise RuntimeError(
            f"sleep_ms: expected Int milliseconds, got {type(ms).__name__}"
        )
    if ms < 0:
        raise RuntimeError(f"sleep_ms: negative duration not allowed ({ms})")
    if ms == 0:
        return None
    import time

    time.sleep(ms / 1000.0)
    return None


def random_int(lo, hi):
    _require_cap("random", "random_int")
    import random

    lo = _require_safe_js_int(lo, "random_int lower bound")
    hi = _require_safe_js_int(hi, "random_int upper bound")
    result = random.randint(lo, hi)  # noqa: S311 — non-crypto use, general-purpose RNG
    return _require_safe_js_int(result, "random_int result")


def random_float():
    _require_cap("random", "random_float")
    import random

    return random.random()  # noqa: S311 — non-crypto use, general-purpose RNG


def _safe_index(target, index):
    """Index into a list/string/array/dict with bounds checking.

    Uses Python's native [] on the fast path (no isinstance checks)
    and translates IndexError/KeyError to RuntimeError.
    """
    try:
        return target[index]
    except IndexError:
        raise RuntimeError(f"Index {index} out of bounds") from None
    except KeyError:
        raise RuntimeError(f"Key not found: {index}") from None
    except TypeError:
        raise RuntimeError(
            f"Cannot index {type(target).__name__} with {type(index).__name__}"
        ) from None


def _safe_index_set(target, index, value):
    """Assign to an index with bounds checking."""
    _check_collection_size(index)
    _check_collection_size(value)

    if isinstance(target, _GenoArray):
        if not isinstance(index, int):
            raise RuntimeError("Array index must be integer")
        if index < 0 or index >= len(target):
            raise RuntimeError(f"Index {index} out of bounds for assignment")
        target._elements[index] = value
        return None

    if isinstance(target, _GenoVec):
        if not isinstance(index, int):
            raise RuntimeError("Vec index must be integer")
        if index < 0 or index >= len(target):
            raise RuntimeError(f"Index {index} out of bounds for assignment")
        target._elements[index] = value
        return None

    if isinstance(target, _GenoMutableMap):
        try:
            is_new_key = index not in target._data
        except TypeError:
            raise RuntimeError(
                f"MutableMap key must be hashable, got {type(index).__name__}"
            ) from None
        if is_new_key:
            _check_collection_kind("MutableMap", len(target._data) + 1)
        target._data[index] = value
        return None

    if isinstance(target, dict):
        try:
            is_new_key = index not in target
        except TypeError:
            raise RuntimeError(
                f"Map key must be hashable, got {type(index).__name__}"
            ) from None
        if is_new_key:
            _check_collection_kind("Map", len(target) + 1)
        target[index] = value
        return None

    try:
        target[index] = value
    except IndexError:
        raise RuntimeError(f"Index {index} out of bounds for assignment") from None
    except TypeError:
        raise RuntimeError(
            f"Cannot assign to index of {type(target).__name__}"
        ) from None


# =============================================================================
# Recursion Limit
# =============================================================================

# Match the interpreter's default max_recursion_depth (100) with headroom
# for internal Python frames.  The limit is set by the execution host:
# - compile_and_exec injects _GENO_RECURSION_LIMIT into globals
# - ProcessSandbox worker calls sys.setrecursionlimit before exec
# The runtime prelude itself cannot import sys (blocked by sandbox).

# =============================================================================
# Typed Holes
# =============================================================================


def _typed_hole(name: str):
    """Halt execution when a typed hole is reached at runtime."""
    raise RuntimeError(f"Typed hole '?{name}' has not been filled")


# =============================================================================
# Clock Builtins
# =============================================================================


def _ts_to_utc_parts(ts):
    """Convert Unix timestamp to (Y, M, D, h, m, s) in UTC — no imports."""
    if ts < 0:
        raise RuntimeError(
            "clock_format: negative timestamps (pre-1970) are not supported"
        )
    ts = int(ts)
    s = ts % 60
    ts //= 60
    mi = ts % 60
    ts //= 60
    h = ts % 24
    days = ts // 24
    # Days since 1970-01-01
    y = 1970
    while True:
        yd = 366 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 365
        if days < yd:
            break
        days -= yd
        y += 1
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    mdays = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    mo = 0
    while mo < 12 and days >= mdays[mo]:
        days -= mdays[mo]
        mo += 1
    return (y, mo + 1, days + 1, h, mi, s)


def _utc_parts_to_ts(y, mo, d, h, mi, s):
    """Convert UTC (Y, M, D, h, m, s) to Unix timestamp — no imports."""
    days = 0
    if y >= 1970:
        for yr in range(1970, y):
            days += 366 if (yr % 4 == 0 and (yr % 100 != 0 or yr % 400 == 0)) else 365
    else:
        for yr in range(y, 1970):
            days -= 366 if (yr % 4 == 0 and (yr % 100 != 0 or yr % 400 == 0)) else 365
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    mdays = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    for m in range(mo - 1):
        days += mdays[m]
    days += d - 1
    return float(days * 86400 + h * 3600 + mi * 60 + s)


# Directives supported by the narrow clock/datetime format/parse contract.
# See geno/std/DateTime.geno for the documented subset.
_CLOCK_DIRECTIVES = frozenset({"Y", "m", "d", "H", "M", "S", "%"})


def _validate_clock_fmt(func_name: str, fmt: str) -> None:
    """Reject any ``%X`` directive outside the documented subset so the
    compiled Python backend matches the compiled JS backend and the
    interpreter."""
    if not isinstance(fmt, str):
        raise RuntimeError(f"{func_name}: fmt must be a string")
    i = 0
    n = len(fmt)
    while i < n:
        if fmt[i] == "%":
            if i + 1 >= n:
                raise RuntimeError(
                    f"{func_name}: trailing '%' with no directive in format string"
                )
            nxt = fmt[i + 1]
            if nxt not in _CLOCK_DIRECTIVES:
                raise RuntimeError(
                    f"{func_name}: unsupported format directive '%{nxt}' "
                    "(supported: %Y %m %d %H %M %S %%)"
                )
            i += 2
        else:
            i += 1


def _is_valid_utc_parts(y: int, mo: int, d: int, h: int, mi: int, s: int) -> bool:
    if not 1 <= y <= 9999:
        return False
    if not 1 <= mo <= 12:
        return False
    if not 0 <= h <= 23:
        return False
    if not 0 <= mi <= 59:
        return False
    if not 0 <= s <= 59:
        return False
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    mdays = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return 1 <= d <= mdays[mo - 1]


def clock_format(timestamp: float, fmt: str) -> str:
    """Format Unix timestamp to string."""
    _require_cap("clock", "clock_format")
    if not isinstance(timestamp, (int, float)):
        raise RuntimeError("clock_format: timestamp must be a number")
    _validate_clock_fmt("clock_format", fmt)
    y, mo, d, h, mi, s = _ts_to_utc_parts(float(timestamp))

    def p(n, w=2):
        return str(n).zfill(w)

    # Handle %% (literal percent) first via sentinel to avoid double-replacement
    _sentinel = "\x00PCT\x00"
    result = (
        fmt.replace("%%", _sentinel)
        .replace("%Y", p(y, 4))
        .replace("%m", p(mo))
        .replace("%d", p(d))
        .replace("%H", p(h))
        .replace("%M", p(mi))
        .replace("%S", p(s))
        .replace(_sentinel, "%")
    )
    _check_string_result_size("clock_format", len(result))
    return result


def clock_parse(text: str, fmt: str):
    """Parse date string to Unix timestamp or None_."""
    _require_cap("clock", "clock_parse")
    if not isinstance(text, str):
        raise RuntimeError("clock_parse: text must be a string")
    _validate_clock_fmt("clock_parse", fmt)
    # Replace directives with placeholders, escape the rest, restore placeholders
    _directives = {
        "%Y": "(?P<Y>\\d{4})",
        "%m": "(?P<m>\\d{2})",
        "%d": "(?P<d>\\d{2})",
        "%H": "(?P<H>\\d{2})",
        "%M": "(?P<M>\\d{2})",
        "%S": "(?P<S>\\d{2})",
    }
    parts = []
    i = 0
    while i < len(fmt):
        if i + 1 < len(fmt) and fmt[i : i + 2] == "%%":
            parts.append("%")
            i += 2
        elif i + 1 < len(fmt) and fmt[i : i + 2] in _directives:
            parts.append(_directives[fmt[i : i + 2]])
            i += 2
        else:
            parts.append(_re.escape(fmt[i]))
            i += 1
    pattern = "".join(parts)
    m = _re.match("^" + pattern + "$", text)
    if m is None:
        return None_
    g = m.groupdict()
    y = int(g.get("Y", "1970"))
    mo = int(g.get("m", "1"))
    d = int(g.get("d", "1"))
    h = int(g.get("H", "0"))
    mi = int(g.get("M", "0"))
    s = int(g.get("S", "0"))
    if not _is_valid_utc_parts(y, mo, d, h, mi, s):
        return None_
    return Some(_utc_parts_to_ts(y, mo, d, h, mi, s))


def clock_elapsed(start: float, end_time: float) -> float:
    """Difference in seconds."""
    _require_cap("clock", "clock_elapsed")
    return float(end_time) - float(start)


# =============================================================================
# Regex Builtins
# =============================================================================


_MAX_REGEX_PATTERN_LEN = 1000
_MAX_REGEX_TEXT_LEN = 10_000
_BACKREF_RE = _re.compile(r"\\[1-9]")


def _has_nested_quantifier(pattern: str) -> bool:
    n = len(pattern)
    i = 0
    while i < n:
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == ")":
            j = i + 1
            while j < n and pattern[j] in (" ", "\t"):
                j += 1
            if j < n and pattern[j] in ("+", "*", "{"):
                depth = 1
                k = i - 1
                while k >= 0 and depth > 0:
                    if pattern[k] == ")" and (k == 0 or pattern[k - 1] != "\\"):
                        depth += 1
                    elif pattern[k] == "(" and (k == 0 or pattern[k - 1] != "\\"):
                        depth -= 1
                    k -= 1
                group_start = k + 2
                m = group_start
                while m < i:
                    if pattern[m] == "\\":
                        m += 2
                        continue
                    if pattern[m] in ("+", "*"):
                        return True
                    if pattern[m] == "{" and m + 1 < i and pattern[m + 1].isdigit():
                        return True
                    m += 1
        i += 1
    return False


def _has_overlapping_alternation(pattern: str) -> bool:
    def _split_top_level_alternatives(group_text: str) -> list[str]:
        branches: list[str] = []
        depth = 0
        in_char_class = False
        start = 0
        i = 0
        while i < len(group_text):
            ch = group_text[i]
            if ch == "\\":
                i += 2
                continue
            if in_char_class:
                if ch == "]":
                    in_char_class = False
                i += 1
                continue
            if ch == "[":
                in_char_class = True
            elif ch == "(":
                depth += 1
            elif ch == ")" and depth > 0:
                depth -= 1
            elif ch == "|" and depth == 0:
                branches.append(group_text[start:i].strip())
                start = i + 1
            i += 1
        branches.append(group_text[start:].strip())
        return branches

    def _branches_overlap(left: str, right: str) -> bool:
        if not left or not right:
            return True
        return left == right or left.startswith(right) or right.startswith(left)

    n = len(pattern)
    i = 0
    while i < n:
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] != "(":
            i += 1
            continue

        group_start = i
        depth = 1
        j = i + 1
        split_at: int | None = None
        while j < n and depth > 0:
            if pattern[j] == "\\":
                j += 2
                continue
            if pattern[j] == "(":
                depth += 1
            elif pattern[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            elif pattern[j] == "|" and depth == 1 and split_at is None:
                split_at = j
            j += 1

        if depth != 0:
            return False

        quant_idx = j + 1
        while quant_idx < n and pattern[quant_idx] in (" ", "\t"):
            quant_idx += 1
        if (
            split_at is not None
            and quant_idx < n
            and pattern[quant_idx] in ("+", "*", "{")
        ):
            branches = _split_top_level_alternatives(pattern[group_start + 1 : j])
            for idx, left in enumerate(branches):
                for right in branches[idx + 1 :]:
                    if _branches_overlap(left, right):
                        return True

        i = j + 1

    return False


def _regex_char_class_end(pattern: str, start: int) -> int | None:
    i = start + 1
    while i < len(pattern):
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == "]":
            return i
        i += 1
    return None


def _regex_group_end(pattern: str, start: int) -> int | None:
    depth = 1
    i = start + 1
    while i < len(pattern):
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == "[":
            class_end = _regex_char_class_end(pattern, i)
            if class_end is None:
                return None
            i = class_end + 1
            continue
        if pattern[i] == "(":
            depth += 1
        elif pattern[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _regex_quantifier_end(pattern: str, start: int) -> tuple[int, bool] | None:
    if start >= len(pattern):
        return None
    if pattern[start] in ("*", "+"):
        end = start + 1
        if end < len(pattern) and pattern[end] == "?":
            end += 1
        return end, True
    if pattern[start] != "{" or start + 1 >= len(pattern):
        return None

    i = start + 1
    while i < len(pattern) and pattern[i].isdigit():
        i += 1
    has_comma = i < len(pattern) and pattern[i] == ","
    if has_comma:
        i += 1
        while i < len(pattern) and pattern[i].isdigit():
            i += 1
    if i >= len(pattern) or pattern[i] != "}":
        return None
    end = i + 1
    if end < len(pattern) and pattern[end] == "?":
        end += 1
    return end, has_comma


def _regex_char_class_key(pattern: str, start: int, end: int) -> tuple[str, str]:
    content = pattern[start + 1 : end]
    if len(content) == 1:
        return ("literal", content)
    if len(content) == 2 and content[0] == "\\":
        return ("literal", content[1])
    return ("class", content)


def _has_sequential_quantified_atoms(pattern: str) -> bool:
    previous_key: tuple[str, str] | None = None
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "|":
            previous_key = None
            i += 1
            continue
        if ch == "\\":
            if i + 1 >= len(pattern):
                return False
            key = ("escape", pattern[i + 1])
            atom_end = i + 2
        elif ch == "[":
            class_end = _regex_char_class_end(pattern, i)
            if class_end is None:
                return False
            key = _regex_char_class_key(pattern, i, class_end)
            atom_end = class_end + 1
        elif ch == "(":
            group_end = _regex_group_end(pattern, i)
            if group_end is None:
                return False
            if _has_sequential_quantified_atoms(pattern[i + 1 : group_end]):
                return True
            key = ("group", pattern[i + 1 : group_end])
            atom_end = group_end + 1
        elif ch in (")", "^", "$"):
            i += 1
            continue
        else:
            key = ("literal", ch)
            atom_end = i + 1

        quantifier = _regex_quantifier_end(pattern, atom_end)
        if quantifier is None:
            previous_key = None
            i = atom_end
            continue

        quantifier_end, is_ambiguous = quantifier
        if is_ambiguous:
            if previous_key == key:
                return True
            previous_key = key
        else:
            previous_key = None
        i = quantifier_end

    return False


def _validate_regex_pattern(pattern: str, func_name: str) -> None:
    if not isinstance(pattern, str):
        raise RuntimeError(f"{func_name}: pattern must be a string")
    if len(pattern) > _MAX_REGEX_PATTERN_LEN:
        raise RuntimeError(
            f"{func_name}: pattern too long (max {_MAX_REGEX_PATTERN_LEN} chars)"
        )
    if _BACKREF_RE.search(pattern):
        raise RuntimeError(f"{func_name}: backreferences are not supported for safety")
    if _has_nested_quantifier(pattern):
        raise RuntimeError(
            f"{func_name}: nested quantifiers are not supported for safety"
        )
    if _has_overlapping_alternation(pattern):
        raise RuntimeError(
            f"{func_name}: overlapping alternation branches are not supported for safety"
        )
    if _has_sequential_quantified_atoms(pattern):
        raise RuntimeError(
            f"{func_name}: adjacent repeated atoms are not supported for safety"
        )


def _validate_regex_text(text: str, func_name: str, arg_name: str = "text") -> None:
    if not isinstance(text, str):
        raise RuntimeError(f"{func_name}: {arg_name} must be a string")
    if len(text) > _MAX_REGEX_TEXT_LEN:
        raise RuntimeError(
            f"{func_name}: {arg_name} too long (max {_MAX_REGEX_TEXT_LEN} chars)"
        )


def regex_match(pattern: str, text: str):
    """Return first match or None_."""
    _require_cap("regex", "regex_match")
    _validate_regex_pattern(pattern, "regex_match")
    _validate_regex_text(text, "regex_match")
    try:
        m = _re.search(pattern, text)
    except _re.error as e:
        raise RuntimeError(f"regex_match: invalid pattern: {e}")
    if m is None:
        return None_
    return Some(m.group())


def regex_find_all(pattern: str, text: str) -> list:
    """Return all matches."""
    _require_cap("regex", "regex_find_all")
    _validate_regex_pattern(pattern, "regex_find_all")
    _validate_regex_text(text, "regex_find_all")
    try:
        result: list[str] = []
        for match in _re.finditer(pattern, text):
            value = match.group()
            _check_string_result_size("regex_find_all", len(value))
            _check_collection_kind("List", len(result) + 1)
            result.append(value)
        return result
    except _re.error as e:
        raise RuntimeError(f"regex_find_all: invalid pattern: {e}")


def regex_replace(pattern: str, replacement: str, text: str) -> str:
    """Replace all matches."""
    _require_cap("regex", "regex_replace")
    _validate_regex_pattern(pattern, "regex_replace")
    _validate_regex_text(replacement, "regex_replace", "replacement")
    _validate_regex_text(text, "regex_replace")
    try:
        result = _re.sub(pattern, replacement, text)
        _check_string_result_size("regex_replace", len(result))
        return result
    except _re.error as e:
        raise RuntimeError(f"regex_replace: invalid pattern: {e}")


# =============================================================================
# JSON Builtins
# =============================================================================


@dataclass(frozen=True, repr=False)
class JsonString(Constructor):
    __slots__ = ("value",)
    value: str


@dataclass(frozen=True, repr=False)
class JsonInt(Constructor):
    __slots__ = ("value",)
    value: int


@dataclass(frozen=True, repr=False)
class JsonFloat(Constructor):
    __slots__ = ("value",)
    value: float


@dataclass(frozen=True, repr=False)
class JsonBool(Constructor):
    __slots__ = ("value",)
    value: bool


@dataclass(frozen=True, repr=False)
class JsonNull(Constructor):
    __slots__ = ()
    pass


@dataclass(frozen=True, repr=False)
class JsonArray(Constructor):
    __slots__ = ("items",)
    items: list


@dataclass(frozen=True, repr=False)
class JsonObject(Constructor):
    __slots__ = ("entries",)
    entries: list


def _python_to_json_value(obj):
    """Convert a Python object (from json.loads) to a compiled JsonValue."""
    if obj is None:
        return JsonNull()
    if isinstance(obj, bool):
        return JsonBool(obj)
    if isinstance(obj, int):
        return JsonInt(obj)
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("json_parse: non-finite JSON number")
        return JsonFloat(obj)
    if isinstance(obj, str):
        _check_collection_kind("String", len(obj))
        return JsonString(obj)
    if isinstance(obj, list):
        _check_collection_kind("List", len(obj))
        return JsonArray([_python_to_json_value(item) for item in obj])
    if isinstance(obj, dict):
        _check_collection_kind("Map", len(obj))
        return JsonObject(
            [
                (_check_collection_size(k), _python_to_json_value(v))
                for k, v in obj.items()
            ]
        )
    raise RuntimeError(f"json_parse: unsupported JSON value type: {type(obj).__name__}")


def _json_value_to_python(value):
    """Convert a compiled JsonValue back to a Python object for json.dumps."""
    if isinstance(value, JsonNull):
        return None
    if isinstance(value, JsonBool):
        return value.value
    if isinstance(value, JsonInt):
        return value.value
    if isinstance(value, JsonFloat):
        if not math.isfinite(value.value):
            raise RuntimeError("json_stringify: JsonFloat must be finite")
        return value.value
    if isinstance(value, JsonString):
        return value.value
    if isinstance(value, JsonArray):
        return [_json_value_to_python(item) for item in value.items]
    if isinstance(value, JsonObject):
        return {k: _json_value_to_python(v) for k, v in value.entries}
    raise RuntimeError(
        f"json_stringify: expected JsonValue, got {type(value).__name__}"
    )


def _reject_json_constant(name: str) -> None:
    raise ValueError(f"Invalid JSON constant: {name}")


def json_parse(text: str):
    """Parse a JSON string into a JsonValue."""
    import json as _json

    try:
        obj = _json.loads(text, parse_constant=_reject_json_constant)
        value = _python_to_json_value(obj)
    except ValueError as e:
        return _check_collection_size(Err(str(e)))
    return _check_collection_size(Ok(value))


def json_stringify(value) -> str:
    """Convert a JsonValue to a JSON string."""
    import json as _json

    _check_collection_size(value)
    obj = _json_value_to_python(value)
    result = _json.dumps(obj, separators=(",", ":"), allow_nan=False)
    _check_string_result_size("json_stringify", len(result))
    return result


def json_stringify_pretty(value, indent) -> str:
    """Pretty-print a JsonValue. ``indent <= 0`` returns compact form.

    Object key ordering matches JsonObject insertion order (no sorting).
    """
    import json as _json

    if not isinstance(indent, int) or isinstance(indent, bool):
        raise RuntimeError(
            f"json_stringify_pretty: indent must be Int, got {type(indent).__name__}"
        )
    _check_collection_size(value)
    obj = _json_value_to_python(value)
    if indent <= 0:
        result = _json.dumps(
            obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    else:
        result = _json.dumps(obj, indent=indent, ensure_ascii=False, allow_nan=False)
    _check_string_result_size("json_stringify_pretty", len(result))
    return result


def _geno_value_to_python(value):
    """Convert any Geno value to a Python object for JSON serialization."""
    # JsonValue constructors
    if isinstance(
        value,
        (JsonNull, JsonBool, JsonInt, JsonFloat, JsonString, JsonArray, JsonObject),
    ):
        return _json_value_to_python(value)
    # Option: None/Some
    if isinstance(value, type(None_)):
        return None
    if hasattr(value, "_tag") and getattr(value, "_tag", None) == "Some":
        return _geno_value_to_python(value.value)
    # Result: Ok/Err
    if isinstance(value, Ok):
        return _geno_value_to_python(value.value)
    if isinstance(value, Err):
        return {"error": _geno_value_to_python(value.error)}
    # Generic compiled ADTs are dataclasses that inherit Constructor.
    if isinstance(value, Constructor):
        result = {"_tag": type(value).__name__}
        for field in _dataclasses_fields(value):
            result[field.name] = _geno_value_to_python(getattr(value, field.name))
        return result
    # Runtime-only containers fall back to their Geno display representation.
    if isinstance(value, (_GenoArray, _GenoMutableMap, _GenoVec, _GenoSet)):
        return _geno_format(value)
    # Generic ADT with _tag
    if hasattr(value, "_tag"):
        result = {"_tag": value._tag}
        for k, v in vars(value).items():
            if k != "_tag":
                result[k] = _geno_value_to_python(v)
        return result
    # Dict (Map)
    if isinstance(value, dict):
        return {str(k): _geno_value_to_python(v) for k, v in value.items()}
    # List/tuple
    if isinstance(value, (list, tuple)):
        return [_geno_value_to_python(item) for item in value]
    # Primitives
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RuntimeError("json_to_string: Float must be finite")
        return value
    if isinstance(value, (bool, int, str)):
        return value
    if value is None:
        return None
    return str(value)


def json_to_string(value) -> str:
    """Convert any Geno value to a JSON string."""
    import json as _json

    _check_collection_size(value)
    obj = _geno_value_to_python(value)
    result = _json.dumps(obj, separators=(",", ":"), allow_nan=False)
    _check_string_result_size("json_to_string", len(result))
    return result


# =============================================================================
# CSV/TOML Builtins
# =============================================================================


def csv_parse(text: str) -> list:
    """Parse CSV text into a list of rows."""
    import csv as _csv
    import io as _io

    reader = _csv.reader(_io.StringIO(text, newline=""))
    rows: list[list[str]] = []
    for row in reader:
        _check_collection_kind("List", len(rows) + 1)
        checked_row = list(row)
        _check_collection_kind("List", len(checked_row))
        for field in checked_row:
            _check_string_result_size("csv_parse", len(field))
        rows.append(checked_row)
    return rows


def _csv_header_row_to_map(headers: list[str], row: list[str]) -> dict[str, str]:
    checked_row: dict[str, str] = {}
    for index, key in enumerate(headers):
        _check_string_result_size("csv_parse_with_headers", len(key))
        value = row[index] if index < len(row) else ""
        _check_string_result_size("csv_parse_with_headers", len(value))
        if key not in checked_row:
            _check_collection_kind("Map", len(checked_row) + 1)
        checked_row[key] = value
    return checked_row


def csv_parse_with_headers(text: str) -> list:
    """Parse CSV with first row as headers, returning list of maps."""
    import csv as _csv
    import io as _io

    reader = _csv.reader(_io.StringIO(text, newline=""))
    try:
        headers = list(next(reader))
    except StopIteration:
        return []

    rows: list[dict[str, str]] = []
    for row in reader:
        _check_collection_kind("List", len(rows) + 1)
        rows.append(_csv_header_row_to_map(headers, list(row)))
    return rows


def toml_parse(text: str):
    """Parse a TOML string into a JsonValue."""
    try:
        import tomllib as _tomllib
    except ImportError:
        try:
            import tomli as _tomllib  # type: ignore[no-redef]
        except ImportError:
            return _check_collection_size(
                Err("TOML parsing not available (install tomli for Python <3.11)")
            )
    try:
        obj = _tomllib.loads(text)
    except ValueError as e:
        return _check_collection_size(Err(str(e)))
    return _check_collection_size(Ok(_python_to_json_value(obj)))


# =============================================================================
# Extended Collection Builtins
# =============================================================================


def zip(list1, list2):
    """Combine two lists pairwise into a list of tuples."""
    return [(a, b) for a, b in _builtin_zip(list1, list2)]


def enumerate(lst):
    """Return a list of (index, element) tuples."""
    return _check_collection_size([(i, v) for i, v in _builtin_enumerate(lst)])


def flat_map(lst, fn):
    """Map a function over a list and flatten the results."""
    result: list[Any] = []
    for item in lst:
        mapped = fn(item)
        if not isinstance(mapped, list):
            raise RuntimeError("flat_map: function must return a list")
        _check_collection_kind("List", len(result) + len(mapped))
        result.extend(mapped)
    return result


def contains_substring(text, substring):
    """Check if a string contains a substring."""
    return substring in text


def repeat_string(text, count):
    """Repeat a string a given number of times."""
    if count < 0:
        raise RuntimeError("repeat_string: count must be non-negative")
    if len(text) * count > _MAX_COLLECTION_SIZE:
        raise RuntimeError("repeat_string: result would exceed collection size limit")
    return text * count


def string_trim(text):
    return text.strip()


def string_trim_start(text):
    return text.lstrip()


def string_trim_end(text):
    return text.rstrip()


def _validate_pad_fill(func_name, fill_char):
    if not isinstance(fill_char, str):
        raise RuntimeError(f"{func_name}: fill_char must be a string")
    if len(fill_char) != 1:
        raise RuntimeError(f"{func_name}: fill_char must be a single character")


def string_pad_left(text, width, fill_char):
    _validate_pad_fill("string_pad_left", fill_char)
    _check_string_result_size("string_pad_left", max(len(text), width))
    return text.rjust(width, fill_char)


def string_pad_right(text, width, fill_char):
    _validate_pad_fill("string_pad_right", fill_char)
    _check_string_result_size("string_pad_right", max(len(text), width))
    return text.ljust(width, fill_char)


def string_char_at(text, index):
    if index < 0 or index >= len(text):
        return ""
    return text[index]


def string_index_of(text, sub):
    return _require_safe_js_int(text.find(sub), "string_index_of result")


def string_last_index_of(text, sub):
    return _require_safe_js_int(text.rfind(sub), "string_last_index_of result")


def string_repeat(text, count):
    if count < 0:
        raise RuntimeError("string_repeat: count must be non-negative")
    if len(text) * count > _MAX_COLLECTION_SIZE:
        raise RuntimeError("string_repeat: result would exceed collection size limit")
    return text * count


def string_substring(text, start, stop):
    _require_str("string_substring", text, "text")
    _require_int("string_substring", start, "start")
    _require_int("string_substring", stop, "stop")
    start = max(0, start)
    stop = min(len(text), stop)
    return text[start:stop]


def string_split(text, delimiter):
    _check_collection_kind("List", _split_result_count("string_split", text, delimiter))
    result = text.split(delimiter)
    return result


def string_join(parts, separator):
    return _join_strings_under_limit("string_join", parts, separator)


def string_replace(text, old, new):
    _check_string_result_size("string_replace", _replace_result_size(text, old, new))
    return text.replace(old, new)


def string_to_upper(text):
    result = text.upper()
    _check_string_result_size("string_to_upper", len(result))
    return result


def string_to_lower(text):
    result = text.lower()
    _check_string_result_size("string_to_lower", len(result))
    return result


def string_starts_with(text, prefix):
    return text.startswith(prefix)


def string_ends_with(text, suffix):
    return text.endswith(suffix)


def string_contains(text, substring):
    return substring in text


def string_split_once(text, delimiter):
    if delimiter == "":
        raise RuntimeError("string_split_once: delimiter cannot be empty")
    if delimiter in text:
        parts = text.split(delimiter, 1)
        return Some((parts[0], parts[1]))
    return None_


def math_abs(x):
    return abs(x)


def math_min(a, b):
    return _builtin_min(a, b)


def math_max(a, b):
    return _builtin_max(a, b)


def math_clamp(value, lo, hi):
    return _builtin_max(lo, _builtin_min(hi, value))


def math_floor(x):
    return _require_safe_js_int(math.floor(x), "math_floor result")


def math_ceil(x):
    return _require_safe_js_int(math.ceil(x), "math_ceil result")


def math_round(x):
    return _require_safe_js_int(int(math.floor(x + 0.5)), "math_round result")


def math_sqrt(x):
    if not isinstance(x, (int, float)):
        raise RuntimeError(f"math_sqrt: expected number, got {type(x).__name__}")
    if x < 0:
        raise RuntimeError("math_sqrt: argument must be non-negative")
    return math.sqrt(x)


def math_log(x):
    if not isinstance(x, (int, float)):
        raise RuntimeError(f"math_log: expected number, got {type(x).__name__}")
    if x <= 0:
        raise RuntimeError("math_log: argument must be positive")
    return math.log(x)


def math_sin(x):
    return math.sin(x)


def math_cos(x):
    return math.cos(x)


def math_pi():
    return math.pi


def math_e():
    return math.e


def math_random_int(lo, hi):
    _require_cap("random", "math_random_int")
    import random as _random

    lo = _require_safe_js_int(lo, "math_random_int lower bound")
    hi = _require_safe_js_int(hi, "math_random_int upper bound")
    result = _random.randint(lo, hi)  # noqa: S311
    return _require_safe_js_int(result, "math_random_int result")


def math_random_float():
    _require_cap("random", "math_random_float")
    import random as _random

    return _random.random()  # noqa: S311


# =============================================================================
# Result stdlib
# =============================================================================


def result_map(result, f):
    if isinstance(result, Ok):
        return Ok(f(result.value))
    return result


def result_map_err(result, f):
    if isinstance(result, Err):
        return Err(f(result.error))
    return result


def result_and_then(result, f):
    if isinstance(result, Ok):
        return f(result.value)
    return result


def result_unwrap_or(result, default):
    if isinstance(result, Ok):
        return result.value
    return default


def result_is_ok(result):
    return isinstance(result, Ok)


def result_is_err(result):
    return isinstance(result, Err)


def result_to_option(result):
    if isinstance(result, Ok):
        return Some(result.value)
    return None_


# =============================================================================
# Option stdlib
# =============================================================================


def option_map(option, f):
    if isinstance(option, Some):
        return Some(f(option.value))
    return None_


def option_and_then(option, f):
    if isinstance(option, Some):
        return f(option.value)
    return None_


def option_unwrap_or(option, default):
    if isinstance(option, Some):
        return option.value
    return default


def option_is_some(option):
    return isinstance(option, Some)


def option_is_none(option):
    return isinstance(option, _None)


def option_flatten(option):
    if isinstance(option, Some) and isinstance(option.value, (Some, _None)):
        return option.value
    return None_


def option_to_result(option, err):
    if isinstance(option, Some):
        return Ok(option.value)
    return Err(err)


# =============================================================================
# Path stdlib
# =============================================================================


def path_join(base, child):
    import posixpath as _pp

    result = _pp.join(base, child)
    _check_string_result_size("path_join", len(result))
    return result


def path_parent(path):
    import posixpath as _pp

    return _pp.dirname(path)


def path_filename(path):
    import posixpath as _pp

    return _pp.basename(path)


def path_extension(path):
    import posixpath as _pp

    _, ext = _pp.splitext(path)
    return ext


def path_is_absolute(path):
    import posixpath as _pp

    return _pp.isabs(path)


# =============================================================================
# DateTime stdlib
# =============================================================================


def datetime_now():
    _require_cap("clock", "datetime_now")
    import time as _t

    return _require_safe_js_int(int(_t.time()), "datetime_now result")


def datetime_format(timestamp, fmt):
    _require_cap("clock", "datetime_format")
    # Delegate to clock_format so the JS and Python compiled backends, plus
    # the interpreter, all honor the same narrow directive contract
    # (%Y %m %d %H %M %S %%).
    return clock_format(timestamp, fmt)


def datetime_parse(text, fmt):
    _require_cap("clock", "datetime_parse")
    result = clock_parse(text, fmt)
    if isinstance(result, Some):
        return Some(_require_safe_js_int(int(result.value), "datetime_parse result"))
    return None_


def datetime_elapsed(start, end_time):
    _require_cap("clock", "datetime_elapsed")
    return _require_safe_js_int(
        int(end_time) - int(start),
        "datetime_elapsed result",
    )


# =============================================================================
# Serve stdlib
# =============================================================================

_http_routes: list[tuple[str, str, Callable[..., Any]]] = []


def http_respond(status, headers, body):
    _require_cap("serve", "http_respond")
    return HttpResponse(status=status, body=body, headers=headers)


_HTTP_HEADER_NAME_RE = _re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def _validate_http_response_headers(headers: Any) -> list:
    """Reject header names/values that could inject extra response headers.

    Mirrors the interpreter serve path (geno/_serve.py): a handler that
    reflects a client-supplied value into a response header must not be able
    to smuggle CRLF and split the response.
    """
    if headers is None:
        return []
    validated = []
    for header in headers:
        try:
            name, value = header
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Invalid response header entry") from exc
        if not isinstance(name, str) or not _HTTP_HEADER_NAME_RE.fullmatch(name):
            raise RuntimeError(f"Invalid response header name: {name!r}")
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise RuntimeError(f"Invalid response header value for {name!r}")
        validated.append((name, value))
    return validated


def http_route(method, path, handler):
    _require_cap("serve", "http_route")
    # Bound the route registry like the interpreter path does, so a program
    # cannot register unbounded routes.
    _check_collection_kind("Route registry", len(_http_routes) + 1)
    _http_routes.append((method.upper(), path, handler))


def http_listen(port):
    _require_cap("serve", "http_listen")
    import sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    def _plain(handler: Any, status: int, body: str) -> None:
        encoded = body.encode("utf-8", errors="replace")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(encoded)))
        handler.end_headers()
        handler.wfile.write(encoded)

    class Handler(BaseHTTPRequestHandler):
        # Per-connection socket read timeout (StreamRequestHandler applies this
        # to the accepted connection). Stops a slow/stuck client from wedging
        # the single-threaded server indefinitely.
        timeout = 30

        def _handle(self):
            raw = self.headers.get("Content-Length", "0")
            try:
                content_length = int(raw)
            except ValueError:
                _plain(self, 400, "Invalid Content-Length header")
                return
            if content_length < 0:
                _plain(self, 400, "Invalid Content-Length: must not be negative")
                return
            if content_length > 1_048_576:
                _plain(self, 413, "Request body too large")
                return
            if content_length:
                # Invalid UTF-8 in the body must be a 400, not an uncaught
                # UnicodeDecodeError that drops the connection.
                try:
                    body = self.rfile.read(content_length).decode("utf-8")
                except UnicodeDecodeError:
                    _plain(self, 400, "Request body must be valid UTF-8")
                    return
            else:
                body = ""
            path_parts = self.path.split("?", 1)
            path = path_parts[0]
            query = path_parts[1] if len(path_parts) > 1 else ""
            headers = [(k, v) for k, v in self.headers.items()]
            request = HttpRequest(
                method=self.command, path=path, query=query, headers=headers, body=body
            )
            for r_method, r_path, handler in _http_routes:
                if r_method == self.command and r_path == path:
                    # A handler exception (or malformed response) must not drop
                    # the connection or leak a traceback to the client; log it
                    # server-side and return a generic 500.
                    try:
                        response = handler(request)
                        status = response.status
                        # Validate the status inside the guard (parity with
                        # _serve.py): send_response runs outside the try, so a
                        # non-int status would otherwise drop the connection
                        # with an uncaught traceback.
                        if not isinstance(status, int) or isinstance(status, bool):
                            raise RuntimeError("Invalid response status")
                        response_headers = _validate_http_response_headers(
                            response.headers
                        )
                        response_body = response.body.encode("utf-8")
                    except Exception:
                        import traceback

                        traceback.print_exc(file=sys.stderr)
                        _plain(self, 500, "Internal Server Error")
                        return
                    self.send_response(status)
                    for hk, hv in response_headers:
                        self.send_header(hk, hv)
                    self.end_headers()
                    self.wfile.write(response_body)
                    return
            _plain(self, 404, "Not Found")

        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def do_PUT(self):
            self._handle()

        def do_DELETE(self):
            self._handle()

        def log_message(self, format, *args):
            pass

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise RuntimeError(f"http_listen: failed to bind port {port}: {exc}") from exc
    print(f"Listening on http://127.0.0.1:{port}")
    server.serve_forever()


def map_from_list(pairs):
    result: dict[Any, Any] = {}
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise RuntimeError(
                "map_from_list: each element must be a (key, value) pair"
            )
        if pair[0] not in result:
            _check_collection_kind("Map", len(result) + 1)
        result[pair[0]] = pair[1]
    return result


def map_merge(m1, m2):
    expected = len(m1)
    for key in m2:
        if key not in m1:
            expected += 1
    _check_collection_kind("Map", expected)
    result = dict(m1)
    result.update(m2)
    return result


def map_filter_map(m, pred):
    return {k: v for k, v in m.items() if pred(k, v)}


def map_map_values(m, f):
    return {k: f(v) for k, v in m.items()}


def map_entries(m):
    _check_collection_kind("List", len(m))
    return [(k, v) for k, v in m.items()]


def map_from_entries(entries):
    result: dict[Any, Any] = {}
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise RuntimeError(
                "map_from_entries: each element must be a (key, value) pair"
            )
        if entry[0] not in result:
            _check_collection_kind("Map", len(result) + 1)
        result[entry[0]] = entry[1]
    return result


def list_zip(xs, ys):
    return [(a, b) for a, b in _builtin_zip(xs, ys)]


def list_enumerate(xs):
    return _check_collection_size([(i, x) for i, x in _builtin_enumerate(xs)])


def list_all(xs, pred):
    return all(pred(x) for x in xs)


def list_flatten(xss):
    result: list[Any] = []
    for xs in xss:
        _check_collection_kind("List", len(result) + len(xs))
        result.extend(xs)
    return result


def list_chunk(xs, n):
    if n <= 0:
        raise RuntimeError("list_chunk: chunk size must be positive")
    return [xs[i : i + n] for i in range(0, len(xs), n)]


def list_take(xs, n):
    return xs[: max(0, n)]


def list_drop(xs, n):
    return xs[max(0, n) :]


def list_find(xs, pred):
    for x in xs:
        if pred(x):
            return Some(x)
    return None_


def list_find_index(xs, pred):
    for i, x in _builtin_enumerate(xs):
        if pred(x):
            return _check_collection_size(Some(i))
    return None_


def list_any(xs, pred):
    return any(pred(x) for x in xs)


def list_fold_right(xs, init, f):
    acc = init
    for x in reversed(xs):
        acc = f(x, acc)
    return acc


def list_intersperse(xs, sep):
    if not xs:
        return []
    _check_collection_kind("List", len(xs) * 2 - 1)
    result = [xs[0]]
    for x in xs[1:]:
        result.append(sep)
        result.append(x)
    return result


def list_group_by(xs, key_fn):
    groups: list[tuple[Any, list[Any]]] = []
    for x in xs:
        k = key_fn(x)
        for existing_key, items in groups:
            if existing_key == k:
                items.append(x)
                break
        else:
            groups.append((k, [x]))
    return groups


def list_length(xs):
    return _require_safe_js_int(len(xs), "list_length result")


def list_map(xs, transform):
    result = [transform(x) for x in xs]
    _check_collection_size(result)
    return result


def list_filter(xs, predicate):
    return [x for x in xs if predicate(x)]


def clamp(value, min_val, max_val):
    """Clamp a numeric value to a range."""
    return _builtin_max(min_val, _builtin_min(max_val, value))


# =============================================================================
# Capability Parsing (for standalone compiled output)
# =============================================================================


def _geno_parse_caps():
    """Parse --cap flags from sys.argv when running as standalone compiled output.

    Returns a set of granted capability names.  An empty set means no
    capabilities were granted — all capability-gated builtins will be denied.
    This ensures standalone compiled output defaults to deny-all, matching
    the interpreter's fail-closed behaviour.
    """
    try:
        import sys as _sys

        argv = _sys.argv
    except Exception:  # sandbox may block sys access
        return set()
    caps: set = set()
    i = 1
    while i < len(argv):
        if argv[i] == "--cap" and i + 1 < len(argv):
            caps.update(argv[i + 1].split(","))
            i += 2
        else:
            i += 1
    return caps


_GENO_CAPS = _geno_parse_caps()


def _require_cap(cap_name: str, builtin_name: str) -> None:
    """Raise if the capability is not granted."""
    if cap_name not in _GENO_CAPS:
        raise RuntimeError(
            f"Capability denied: '{builtin_name}' requires '--cap {cap_name}'"
        )


def _minimal_process_env():
    import os

    env = {}
    for key in (
        "PATH",
        "Path",
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "windir",
        "COMSPEC",
        "ComSpec",
        "PATHEXT",
    ):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _process_env():
    if "env" in _GENO_CAPS:
        return None
    return _minimal_process_env()


def _geno_env_truthy(name: str) -> bool:
    import os as _os

    return _os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _geno_env_list(name: str):
    import os as _os

    raw = _os.environ.get(name)
    if not raw:
        return None
    return [item for item in raw.split(_os.pathsep) if item]


def _fs_cli_arg_roots():
    import os as _os
    import sys as _sys

    roots = []
    after_separator = False
    for arg in _sys.argv[1:]:
        if arg == "--":
            after_separator = True
            continue
        if not after_separator or not isinstance(arg, str) or "\x00" in arg:
            continue
        if _os.path.isabs(arg):
            candidate = _os.path.realpath(arg)
        elif _os.sep in arg or (_os.altsep and _os.altsep in arg):
            candidate = _os.path.realpath(_os.path.abspath(arg))
        else:
            continue
        roots.append(
            candidate if _os.path.isdir(candidate) else _os.path.dirname(candidate)
        )
    return roots


def _fs_policy_roots() -> list[str]:
    import os as _os

    roots = _geno_env_list("GENO_FS_ROOTS")
    if roots is None:
        roots = [_os.getcwd(), *_fs_cli_arg_roots()]
    return [_os.path.realpath(root) for root in roots]


def _is_under_root(path: str, root: str) -> bool:
    import os as _os

    try:
        return _os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def _resolve_fs_path(path: str, fn_name: str) -> str:
    import os as _os

    if not isinstance(path, str):
        raise RuntimeError(f"{fn_name}: path must be String")
    roots = _fs_policy_roots()
    if _os.path.isabs(path):
        resolved = _os.path.realpath(path)
        if _geno_env_truthy("GENO_FS_ALLOW_ABSOLUTE"):
            absolute_roots = roots
        elif _geno_env_list("GENO_FS_ROOTS") is None:
            absolute_roots = [_os.path.realpath(root) for root in _fs_cli_arg_roots()]
        else:
            absolute_roots = []
        if any(_is_under_root(resolved, root) for root in absolute_roots):
            return resolved
        if any(_is_under_root(resolved, root) for root in roots):
            raise RuntimeError(f"{fn_name}: absolute paths are not allowed")
        raise RuntimeError(f"{fn_name}: path escapes configured filesystem roots")

    for root in roots:
        resolved = _os.path.realpath(_os.path.join(root, path))
        if _is_under_root(resolved, root):
            return resolved
    raise RuntimeError(f"{fn_name}: path escapes configured filesystem roots")


def _fs_writes_allowed() -> bool:
    return not _geno_env_truthy("GENO_FS_READ_ONLY")


class _GenoOutputLimitError(RuntimeError):
    """Internal: host output exceeded the configured collection limit."""


def _check_limited_text_size(fn_name: str, size: int, limit_error_type) -> None:
    try:
        _check_string_result_size(fn_name, size)
    except RuntimeError as exc:
        if limit_error_type is RuntimeError:
            raise
        raise limit_error_type(str(exc)) from exc


def _read_limited_utf8_stream(
    reader, fn_name: str, *, limit_error_type=RuntimeError
) -> str:
    import codecs as _codecs

    decoder = _codecs.getincrementaldecoder("utf-8")(errors="replace")
    parts: list[str] = []
    total = 0
    while True:
        chunk = reader.read(8192)
        if not chunk:
            break
        text = decoder.decode(chunk)
        if not text:
            continue
        total += len(text)
        _check_limited_text_size(fn_name, total, limit_error_type)
        parts.append(text)
    tail = decoder.decode(b"", final=True)
    if tail:
        total += len(tail)
        _check_limited_text_size(fn_name, total, limit_error_type)
        parts.append(tail)
    return "".join(parts)


def _read_limited_text_stream(
    reader, fn_name: str, *, limit_error_type=RuntimeError
) -> str:
    parts: list[str] = []
    total = 0
    while True:
        text = reader.read(8192)
        if not text:
            break
        total += len(text)
        _check_limited_text_size(fn_name, total, limit_error_type)
        parts.append(text)
    return "".join(parts)


def _validate_http_target(url: str, fn_name: str) -> None:
    from urllib.parse import urlparse as _urlparse

    parsed = _urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise RuntimeError(
            f"{fn_name}: scheme '{scheme}' is not allowed, only http and https"
        )
    if not parsed.hostname:
        raise RuntimeError(f"{fn_name}: URL must include a hostname")
    if _geno_env_truthy("GENO_HTTP_ALLOW_PRIVATE"):
        return

    _resolve_validated_http_addresses(
        parsed.hostname,
        parsed.port or (443 if scheme == "https" else 80),
        fn_name,
    )


def _validate_http_address(host: str, fn_name: str) -> None:
    if _geno_env_truthy("GENO_HTTP_ALLOW_PRIVATE"):
        return
    import ipaddress as _ipaddress

    try:
        address = _ipaddress.ip_address(host)
    except ValueError as exc:
        raise RuntimeError(
            f"{fn_name}: cannot validate resolved host {host!r}"
        ) from exc
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise RuntimeError(
            f"{fn_name}: private, local, or reserved network targets are not allowed"
        )


def _resolve_validated_http_addresses(
    hostname: str,
    port: int,
    fn_name: str,
) -> list[Any]:
    import socket as _socket

    try:
        infos = _socket.getaddrinfo(hostname, port, type=_socket.SOCK_STREAM)
    except OSError as exc:
        raise RuntimeError(f"{fn_name}: cannot resolve host {hostname!r}") from exc
    for info in infos:
        resolved_host = info[4][0]
        if not isinstance(resolved_host, str):
            raise RuntimeError(f"{fn_name}: cannot validate resolved host")
        _validate_http_address(resolved_host, fn_name)
    return infos


def _create_validated_http_connection(
    hostname: str,
    port: int,
    timeout: Any,
    source_address: Any,
    fn_name: str,
) -> Any:
    import socket as _socket

    infos = _resolve_validated_http_addresses(hostname, port, fn_name)
    last_error = None
    global_default_timeout = getattr(_socket, "_GLOBAL_DEFAULT_TIMEOUT", object())
    for family, socktype, proto, _canonname, sockaddr in infos:
        sock = _socket.socket(family, socktype, proto)
        try:
            if timeout is not global_default_timeout:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"{fn_name}: cannot resolve host {hostname!r}")


def _read_limited_temp_text(handle, fn_name: str) -> str:
    handle.seek(0)
    return _read_limited_utf8_stream(
        handle, fn_name, limit_error_type=_GenoOutputLimitError
    )


def _process_allowlist():
    import os as _os

    raw = _geno_env_list("GENO_PROCESS_EXECUTABLES")
    if raw is None:
        return None
    return {_os.path.realpath(path) for path in raw}


def _validate_process_argv(argv, fn_name: str, *, process_env):
    import os as _os
    import shutil as _shutil

    if not argv:
        return None, f"{fn_name}: command must not be empty"
    program = argv[0]
    if not isinstance(program, str):
        return None, f"{fn_name}: program must be String"
    allow_path_search = _geno_env_truthy("GENO_PROCESS_ALLOW_PATH_SEARCH")
    if not allow_path_search and not _os.path.isabs(program):
        return None, f"{fn_name}: executable must be an absolute path"

    resolved = program
    if _os.path.isabs(program):
        resolved = _os.path.realpath(program)
    elif allow_path_search:
        path_env = None if process_env is None else process_env.get("PATH")
        found = _shutil.which(program, path=path_env)
        if found is None:
            return None, f"{fn_name}: executable not found: {program}"
        resolved = _os.path.realpath(found)

    allowlist = _process_allowlist()
    if allowlist is not None and resolved not in allowlist:
        return None, f"{fn_name}: executable is not in the configured allowlist"
    return [resolved, *argv[1:]], None


def _run_limited_process(argv, *, input_text=None):
    import subprocess as _subprocess
    import tempfile as _tempfile

    env = _process_env()
    argv, err = _validate_process_argv(argv, "process", process_env=env)
    if err is not None:
        raise ValueError(err)
    stdin_payload = input_text.encode("utf-8") if input_text is not None else None
    with _tempfile.TemporaryFile() as stdout_file:
        with _tempfile.TemporaryFile() as stderr_file:
            result = _subprocess.run(  # noqa: S603
                argv,
                input=stdin_payload,
                stdout=stdout_file,
                stderr=stderr_file,
                env=env,
                timeout=30,
            )
            return _subprocess.CompletedProcess(
                argv,
                result.returncode,
                _read_limited_temp_text(stdout_file, "process"),
                _read_limited_temp_text(stderr_file, "process"),
            )


# =============================================================================
# File I/O Builtins (capability-gated: --cap fs)
# =============================================================================


def fs_read_text(path: str) -> str:
    """Read a text file and return its contents."""
    _require_cap("fs", "fs_read_text")
    path = _resolve_fs_path(path, "fs_read_text")
    with open(path, encoding="utf-8") as f:
        result = _read_limited_text_stream(f, "fs_read_text")
    return result


def fs_write_text(path: str, content: str):
    """Write text to a file."""
    _require_cap("fs", "fs_write_text")
    if not _fs_writes_allowed():
        raise RuntimeError("fs_write_text: filesystem writes are not allowed")
    path = _resolve_fs_path(path, "fs_write_text")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return None


def fs_list_dir(path: str):
    """List directory entries, returning Result[List[String], String]."""
    _require_cap("fs", "fs_list_dir")
    import os

    try:
        path = _resolve_fs_path(path, "fs_list_dir")
        entries = sorted(os.listdir(path))
    except (OSError, RuntimeError) as e:
        return Err(str(e))
    return _check_collection_size(Ok(entries))


def fs_exists(path: str) -> bool:
    """Check if a path exists."""
    _require_cap("fs", "fs_exists")
    import os.path

    path = _resolve_fs_path(path, "fs_exists")
    return os.path.exists(path)


# =============================================================================
# HTTP Builtins (capability-gated: --cap http)
# =============================================================================


def _validate_http_scheme(url: str, fn_name: str) -> None:
    _validate_http_target(url, fn_name)


def _open_http_url(request, *, timeout: int, fn_name: str):
    import http.client as _http_client
    import ssl as _ssl
    from urllib.parse import urljoin
    from urllib.request import (
        HTTPHandler,
        HTTPRedirectHandler,
        HTTPSHandler,
        ProxyHandler,
        build_opener,
    )

    class _ValidatedHTTPConnection(_http_client.HTTPConnection):
        def connect(self) -> None:
            self.sock = _create_validated_http_connection(
                self.host,
                self.port,
                self.timeout,
                getattr(self, "source_address", None),
                fn_name,
            )

    class _ValidatedHTTPSConnection(_http_client.HTTPSConnection):
        def connect(self) -> None:
            self.sock = _create_validated_http_connection(
                self.host,
                self.port,
                self.timeout,
                getattr(self, "source_address", None),
                fn_name,
            )
            context = getattr(self, "_context", None) or _ssl.create_default_context()
            self.sock = context.wrap_socket(
                self.sock,
                server_hostname=self.host,
            )

    class _ValidatedHTTPHandler(HTTPHandler):
        def http_open(self, req: Any) -> Any:
            return self.do_open(_ValidatedHTTPConnection, req)

    class _ValidatedHTTPSHandler(HTTPSHandler):
        def https_open(self, req: Any) -> Any:
            return self.do_open(_ValidatedHTTPSConnection, req)

    class _HttpOnlyRedirectHandler(HTTPRedirectHandler):
        def redirect_request(
            self,
            req: Any,
            fp: Any,
            code: int,
            msg: str,
            headers: Any,
            newurl: str,
        ) -> Any:
            _validate_http_scheme(urljoin(req.full_url, newurl), fn_name)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return build_opener(
        ProxyHandler({}),
        _ValidatedHTTPHandler,
        _ValidatedHTTPSHandler,
        _HttpOnlyRedirectHandler,
    ).open(request, timeout=timeout)


def http_fetch(url: str) -> str:
    """Fetch a URL and return the response body as a string."""
    _require_cap("http", "http_fetch")
    _validate_http_scheme(url, "http_fetch")
    from urllib.request import Request

    try:
        with _open_http_url(
            Request(url),  # noqa: S310
            timeout=30,
            fn_name="http_fetch",
        ) as resp:
            result = _read_limited_utf8_stream(resp, "http_fetch")
    except (OSError, ValueError, RuntimeError) as e:
        raise RuntimeError(f"http_fetch: {e}")
    _check_string_result_size("http_fetch", len(result))
    return result


def http_post(url: str, body: str) -> str:
    """POST to a URL and return the response body as a string."""
    _require_cap("http", "http_post")
    _validate_http_scheme(url, "http_post")
    from urllib.request import Request

    try:
        req = Request(url, data=body.encode("utf-8"), method="POST")  # noqa: S310
        req.add_header("Content-Type", "application/json")
        with _open_http_url(req, timeout=30, fn_name="http_post") as resp:
            result = _read_limited_utf8_stream(resp, "http_post")
    except (OSError, ValueError, RuntimeError) as e:
        raise RuntimeError(f"http_post: {e}")
    _check_string_result_size("http_post", len(result))
    return result


def http_request(method: str, url: str, headers, body):
    """Make an HTTP request, returning Result[HttpResponse, String]."""
    _require_cap("http", "http_request")
    from urllib.error import URLError
    from urllib.request import Request

    try:
        _validate_http_scheme(url, "http_request")
        data = body.encode("utf-8") if body is not None else None
        req = Request(url, data=data, method=method)  # noqa: S310
        for key, value in headers:
            req.add_header(key, value)
        with _open_http_url(req, timeout=30, fn_name="http_request") as resp:
            resp_headers = [(k, v) for k, v in resp.getheaders()]
            body_text = _read_limited_utf8_stream(resp, "http_request")
            response = HttpResponse(resp.status, body_text, resp_headers)
    except URLError as e:
        return Err(str(e))
    except (OSError, ValueError, TypeError, RuntimeError) as e:
        return Err(str(e))
    return _check_collection_size(Ok(response))


# =============================================================================
# Process Execution Builtins (capability-gated)
# =============================================================================


def exec_(command: str):
    """Execute a command and return Result[ProcessResult, String]."""
    _require_cap("process", "exec")
    import shlex as _shlex
    import subprocess as _subprocess

    try:
        if not isinstance(command, str):
            return Err("exec: command must be String")
        result = _run_limited_process(_shlex.split(command))
    except _GenoOutputLimitError:
        raise
    except _subprocess.TimeoutExpired:
        return Err("Process timed out")
    except (
        OSError,
        ValueError,
        TypeError,
        UnicodeError,
        _subprocess.SubprocessError,
    ) as e:
        return Err(str(e))
    return _check_collection_size(
        Ok(ProcessResult(result.returncode, result.stdout, result.stderr))
    )


def exec_with_input(command: str, stdin: str):
    """Execute a command with stdin and return Result[ProcessResult, String]."""
    _require_cap("process", "exec_with_input")
    import shlex as _shlex
    import subprocess as _subprocess

    try:
        if not isinstance(command, str):
            return Err("exec_with_input: command must be String")
        if not isinstance(stdin, str):
            return Err("exec_with_input: stdin must be String")
        result = _run_limited_process(_shlex.split(command), input_text=stdin)
    except _GenoOutputLimitError:
        raise
    except _subprocess.TimeoutExpired:
        return Err("Process timed out")
    except (
        OSError,
        ValueError,
        TypeError,
        UnicodeError,
        _subprocess.SubprocessError,
    ) as e:
        return Err(str(e))
    return _check_collection_size(
        Ok(ProcessResult(result.returncode, result.stdout, result.stderr))
    )


def spawn(program, args):
    """Spawn a program with a typed argv list — no shell, no caller quoting."""
    _require_cap("process", "spawn")
    import subprocess as _subprocess

    if not isinstance(program, str):
        return Err(f"spawn: program must be String, got {type(program).__name__}")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return Err("spawn: args must be List[String]")
    try:
        result = _run_limited_process([program, *args])
    except _GenoOutputLimitError:
        raise
    except _subprocess.TimeoutExpired:
        return Err("Process timed out")
    except (
        OSError,
        ValueError,
        TypeError,
        UnicodeError,
        _subprocess.SubprocessError,
    ) as e:
        return Err(str(e))
    return _check_collection_size(
        Ok(ProcessResult(result.returncode, result.stdout, result.stderr))
    )


def spawn_with_input(program, args, stdin):
    """Spawn a program with argv list and stdin text."""
    _require_cap("process", "spawn_with_input")
    import subprocess as _subprocess

    if not isinstance(program, str):
        return Err(
            f"spawn_with_input: program must be String, got {type(program).__name__}"
        )
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return Err("spawn_with_input: args must be List[String]")
    if not isinstance(stdin, str):
        return Err(
            f"spawn_with_input: stdin must be String, got {type(stdin).__name__}"
        )
    try:
        result = _run_limited_process([program, *args], input_text=stdin)
    except _GenoOutputLimitError:
        raise
    except _subprocess.TimeoutExpired:
        return Err("Process timed out")
    except (
        OSError,
        ValueError,
        TypeError,
        UnicodeError,
        _subprocess.SubprocessError,
    ) as e:
        return Err(str(e))
    return _check_collection_size(
        Ok(ProcessResult(result.returncode, result.stdout, result.stderr))
    )


def stdin_read_all():
    """Read stdin to EOF as UTF-8. Non-UTF-8 input returns Err."""
    _require_cap("stdin", "stdin_read_all")
    import sys as _sys

    try:
        buffered = getattr(_sys.stdin, "buffer", None)
        data = buffered.read() if buffered is not None else _sys.stdin.read()
    except OSError as e:
        return Err(str(e))
    except UnicodeDecodeError as e:
        return Err(f"stdin is not valid UTF-8: {e}")
    if isinstance(data, bytes):
        try:
            return _check_collection_size(Ok(data.decode("utf-8")))
        except UnicodeDecodeError as e:
            return Err(f"stdin is not valid UTF-8: {e}")
    if isinstance(data, str):
        return _check_collection_size(Ok(data))
    return Err(f"stdin returned unsupported type: {type(data).__name__}")


# =============================================================================
# Environment Variable Builtins (lazy import to avoid sandbox violations)
# =============================================================================


def env_get(name: str):
    """Return the value of an environment variable, or None if unset."""
    _require_cap("env", "env_get")
    import os

    value = os.environ.get(name)
    if value is None:
        return None_
    return _check_collection_size(Some(value))


def env_get_or(name: str, default: str) -> str:
    """Return the value of an environment variable, or a default if unset."""
    _require_cap("env", "env_get_or")
    import os

    result = os.environ.get(name, default)
    _check_string_result_size("env_get_or", len(result))
    return result


def cli_args():
    """Return command-line arguments passed after '--' to the program."""
    _require_cap("env", "cli_args")
    import json as _json
    import os
    import sys

    # In sandbox mode, args are forwarded via env var
    env_args = os.environ.get("GENO_CLI_ARGS")
    if env_args:
        return _check_collection_size(_json.loads(env_args))
    # In standalone compiled mode, read sys.argv directly
    argv = sys.argv
    if "--" in argv:
        idx = argv.index("--")
        return _check_collection_size(list(argv[idx + 1 :]))
    return []


# =============================================================================
# Generated Code Follows
# =============================================================================
