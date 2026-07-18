"""
Geno Built-in Functions
==========================

Pure built-in function implementations for the Geno interpreter.
These functions have no dependency on interpreter state.
"""

import math
import os
import random
import re
import time
from typing import Any, Callable

from .values import (
    ArrayValue,
    BuiltinFunction,
    Closure,
    ConstructorValue,
    MutableMapValue,
    RuntimeError,
    SetValue,
    VecValue,
)

_DECIMAL_INT_RE = re.compile(r"^-?[0-9]+$")

DEFAULT_MAX_COLLECTION_SIZE = 10_000_000
_MAX_SAFE_JS_INT = 2**53 - 1
_MIN_SAFE_JS_INT = -_MAX_SAFE_JS_INT

# Module-level collection-size cap used by builtin pre-checks.
# The Interpreter calls ``set_max_collection_size`` during init with the
# value from ``SandboxConfig.max_collection_size`` so tightened limits are
# enforced *before* a builtin allocates its output, not only after
# (the ``_call_function`` post-check runs after allocation).
_MAX_COLLECTION_SIZE: int = DEFAULT_MAX_COLLECTION_SIZE


def set_max_collection_size(n: int) -> None:
    """Set the module-level collection-size cap used by builtin pre-checks."""
    global _MAX_COLLECTION_SIZE
    if not isinstance(n, int) or n < 0:
        raise RuntimeError(
            f"set_max_collection_size expects non-negative int, got {n!r}"
        )
    _MAX_COLLECTION_SIZE = n


def get_max_collection_size() -> int:
    """Return the current module-level collection-size cap."""
    return _MAX_COLLECTION_SIZE


def _effective_max_collection_size(max_collection_size: int) -> int:
    """Honor both the caller-supplied limit and the module-level cap."""
    return min(max_collection_size, _MAX_COLLECTION_SIZE)


def _check_collection_size(kind: str, size: int, limit: int) -> None:
    if size > limit:
        raise RuntimeError(f"{kind} size exceeds limit ({size} > {limit})")


def _check_result_string_size(
    func_name: str, size: int, max_collection_size: int
) -> None:
    limit = _effective_max_collection_size(max_collection_size)
    try:
        _check_collection_size("String", size, limit)
    except RuntimeError as exc:
        raise RuntimeError(f"{func_name}: {exc}") from exc


def _require_safe_js_int(value: int, context: str) -> int:
    if value < _MIN_SAFE_JS_INT or value > _MAX_SAFE_JS_INT:
        raise RuntimeError(f"{context} exceeds JavaScript safe integer range")
    return value


def _join_strings_under_limit(
    func_name: str,
    parts: list[Any],
    separator: str,
    max_collection_size: int,
) -> str:
    string_parts = [str(part) for part in parts]
    size = sum(len(part) for part in string_parts)
    if len(string_parts) > 1:
        size += len(separator) * (len(string_parts) - 1)
    _check_result_string_size(func_name, size, max_collection_size)
    return separator.join(string_parts)


def _split_result_count(func_name: str, text: str, delimiter: str) -> int:
    if delimiter == "":
        raise RuntimeError(f"{func_name}: delimiter cannot be empty")
    return text.count(delimiter) + 1


def _replace_result_size(text: str, old: str, new: str) -> int:
    if old == "":
        return len(text) + (len(text) + 1) * len(new)
    return len(text) + text.count(old) * (len(new) - len(old))


def _require_list(name: str, value: Any) -> None:
    if not isinstance(value, list):
        raise RuntimeError(f"{name} expects list, got {type(value).__name__}")


def _require_str(name: str, value: Any, label: str | None = None) -> None:
    if not isinstance(value, str):
        if label:
            raise RuntimeError(
                f"{name} {label} must be a string, got {type(value).__name__}"
            )
        raise RuntimeError(f"{name} expects string, got {type(value).__name__}")


def _require_int(name: str, value: Any, label: str) -> None:
    if not isinstance(value, int):
        raise RuntimeError(f"{name} {label} must be an integer")


def _require_pair(name: str, value: Any) -> tuple[Any, Any]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise RuntimeError(f"{name}: each element must be a (key, value) pair")
    return value[0], value[1]


def _trunc_int_div(a: int, b: int) -> int:
    """Integer division with truncation-toward-zero semantics."""
    quotient = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        quotient = -quotient
    return quotient


def builtin_length(lst: list[Any] | str | ArrayValue) -> int:
    if not isinstance(lst, (list, str, ArrayValue)):
        raise RuntimeError(
            f"length expects list, string, or array, got {type(lst).__name__}"
        )
    return len(lst)


def builtin_head(lst: list[Any]) -> Any:
    _require_list("head", lst)
    if not lst:
        raise RuntimeError("head of empty list")
    return lst[0]


def builtin_tail(lst: list[Any]) -> list[Any]:
    _require_list("tail", lst)
    if not lst:
        raise RuntimeError("tail of empty list")
    return lst[1:]


def builtin_append(
    lst: list[Any], item: Any, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> list[Any]:
    _require_list("append", lst)
    limit = _effective_max_collection_size(max_collection_size)
    _check_collection_size("List", len(lst) + 1, limit)
    return lst + [item]


def builtin_concat(
    lst1: list[Any],
    lst2: list[Any],
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> list[Any]:
    if not isinstance(lst1, list) or not isinstance(lst2, list):
        raise RuntimeError("concat expects two lists")
    # Pre-check to avoid allocating a huge list before the interpreter's
    # post-call size check can fire.
    expected = len(lst1) + len(lst2)
    limit = _effective_max_collection_size(max_collection_size)
    if expected > limit:
        raise RuntimeError(f"List size exceeds limit ({expected} > {limit})")
    return lst1 + lst2


def builtin_contains(lst: list[Any], item: Any) -> bool:
    _require_list("contains", lst)
    return item in lst


def builtin_split(
    s: str, sep: str, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> list[str]:
    if not isinstance(s, str) or not isinstance(sep, str):
        raise RuntimeError("split expects strings")
    limit = _effective_max_collection_size(max_collection_size)
    _check_collection_size("List", _split_result_count("split", s, sep), limit)
    return s.split(sep)


def builtin_join(
    lst: list[Any], sep: str, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> str:
    if not isinstance(lst, list) or not isinstance(sep, str):
        raise RuntimeError("join expects list and string")
    return _join_strings_under_limit("join", lst, sep, max_collection_size)


def builtin_trim(s: str) -> str:
    _require_str("trim", s)
    return s.strip()


def builtin_to_lower(s: str) -> str:
    _require_str("to_lower", s)
    return s.lower()


def builtin_to_upper(s: str) -> str:
    _require_str("to_upper", s)
    return s.upper()


def builtin_replace(
    text: str,
    old: str,
    new: str,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> str:
    _require_str("replace", text)
    _require_str("replace", old, "old")
    _require_str("replace", new, "new")
    _check_result_string_size(
        "replace", _replace_result_size(text, old, new), max_collection_size
    )
    return text.replace(old, new)


def builtin_ends_with(text: str, suffix: str) -> bool:
    _require_str("ends_with", text)
    _require_str("ends_with", suffix, "suffix")
    return text.endswith(suffix)


def builtin_split_once(s: str, sep: str) -> ConstructorValue:
    if not isinstance(s, str) or not isinstance(sep, str):
        raise RuntimeError("split_once expects strings")
    if sep == "":
        raise RuntimeError("split_once: delimiter cannot be empty")
    if sep in s:
        parts = s.split(sep, 1)
        return ConstructorValue("Some", {"value": (parts[0], parts[1])})
    return ConstructorValue("None", {})


def builtin_divide(a: int | float, b: int | float) -> int | float:
    if b == 0:
        raise RuntimeError("Division by zero")
    if isinstance(a, int) and isinstance(b, int):
        return _trunc_int_div(a, b)
    return a / b


def builtin_sqrt(x: int | float) -> float:
    if x < 0:
        raise RuntimeError("sqrt of negative number")
    return math.sqrt(x)


def builtin_floor(x: int | float) -> int:
    return _require_safe_js_int(math.floor(x), "floor result")


def builtin_ceil(x: int | float) -> int:
    return _require_safe_js_int(math.ceil(x), "ceil result")


def builtin_round(x: int | float) -> int:
    base = math.floor(x)
    rounded = base + (1 if x - base >= 0.5 else 0)
    return _require_safe_js_int(rounded, "round result")


def builtin_float_to_int(x: int | float) -> int:
    return _require_safe_js_int(int(x), "float_to_int result")


def builtin_is_sorted(lst: list[Any]) -> bool:
    _require_list("is_sorted", lst)
    return all(lst[i] <= lst[i + 1] for i in range(len(lst) - 1))


def builtin_is_numeric_string(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    return _DECIMAL_INT_RE.fullmatch(s.strip()) is not None


def builtin_is_permutation(lst1: list[Any], lst2: list[Any]) -> bool:
    if not isinstance(lst1, list) or not isinstance(lst2, list):
        raise RuntimeError("is_permutation expects lists")
    if len(lst1) > 100_000 or len(lst2) > 100_000:
        raise RuntimeError("is_permutation: list too large (max 100,000 elements)")
    if len(lst1) != len(lst2):
        return False
    return sorted(_geno_sort_key(item) for item in lst1) == sorted(
        _geno_sort_key(item) for item in lst2
    )


def builtin_parse_int(s: str) -> ConstructorValue:
    if not isinstance(s, str):
        raise RuntimeError(f"parse_int expects string, got {type(s).__name__}")
    if len(s) > 1000:
        raise RuntimeError("parse_int: input string too long (max 1000 characters)")
    trimmed = s.strip()
    if _DECIMAL_INT_RE.fullmatch(trimmed) is None:
        return ConstructorValue("None", {})
    try:
        value = int(trimmed)
    except ValueError:
        return ConstructorValue("None", {})
    if value < _MIN_SAFE_JS_INT or value > _MAX_SAFE_JS_INT:
        return ConstructorValue("None", {})
    return ConstructorValue("Some", {"value": value})


def builtin_parse_float(s: str) -> ConstructorValue:
    if not isinstance(s, str):
        raise RuntimeError(f"parse_float expects string, got {type(s).__name__}")
    if len(s) > 1000:
        raise RuntimeError("parse_float: input string too long (max 1000 characters)")
    if not re.match(r"^-?(\d+\.?\d*|\.\d+)$", s.strip()):
        return ConstructorValue("None", {})
    try:
        value = float(s)
    except ValueError:
        return ConstructorValue("None", {})
    if not math.isfinite(value):
        return ConstructorValue("None", {})
    return ConstructorValue("Some", {"value": value})


def builtin_reverse(lst: list[Any]) -> list[Any]:
    _require_list("reverse", lst)
    return lst[::-1]


def builtin_bit_or(a: int, b: int) -> int:
    _require_int("bit_or", a, "a")
    _require_int("bit_or", b, "b")
    return a | b


def builtin_range(
    *args: int, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> list[int]:
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
    limit = _effective_max_collection_size(max_collection_size)
    if size > limit:
        raise RuntimeError(f"List size exceeds limit ({size} > {limit})")
    return list(range(start, end, step))


def builtin_substring(s: str, start: int, stop: int) -> str:
    _require_str("substring", s)
    _require_int("substring", start, "start")
    _require_int("substring", stop, "stop")
    start = max(0, start)
    stop = min(len(s), stop)
    return s[start:stop]


def builtin_format(
    template: str,
    values: list[Any],
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> str:
    _require_str("format", template)
    _require_list("format", values)
    parts = template.split("{}")
    if len(parts) - 1 != len(values):
        raise RuntimeError(
            f"format: expected {len(parts) - 1} values, got {len(values)}"
        )
    value_parts = [str(value) for value in values]
    size = sum(len(part) for part in parts) + sum(len(part) for part in value_parts)
    _check_result_string_size("format", size, max_collection_size)
    result = parts[0]
    for i, val in enumerate(value_parts):
        result += val + parts[i + 1]
    return result


def builtin_char_code(s: str) -> int:
    _require_str("char_code", s)
    if len(s) == 0:
        raise RuntimeError("char_code: empty string")
    return ord(s[0])


def builtin_from_char_code(n: int) -> str:
    _require_int("from_char_code", n, "code")
    if n < 0 or n > 0x10FFFF:
        raise RuntimeError(f"from_char_code: code point {n} out of range")
    return chr(n)


def builtin_set_at(lst: list[Any], index: int, value: Any) -> list[Any]:
    _require_list("set_at", lst)
    _require_int("set_at", index, "index")
    if index < 0 or index >= len(lst):
        raise RuntimeError("set_at index out of range")
    updated = list(lst)
    updated[index] = value
    return updated


def builtin_slice(lst: list[Any], start: int, end: int) -> list[Any]:
    _require_list("slice", lst)
    _require_int("slice", start, "start")
    _require_int("slice", end, "end")
    start = max(0, start)
    end = min(len(lst), end)
    return lst[start:end]


def builtin_max(a: Any, b: Any) -> Any:
    return a if a >= b else b


def builtin_starts_with(s: str, prefix: str) -> bool:
    if not isinstance(s, str) or not isinstance(prefix, str):
        raise RuntimeError("starts_with expects strings")
    return s.startswith(prefix)


def builtin_to_chars(s: str) -> list[str]:
    _require_str("to_chars", s)
    return list(s)


def builtin_sort_strings(values: list[str]) -> list[str]:
    _require_list("sort_strings", values)
    if len(values) > 100_000:
        raise RuntimeError("sort_strings: list too large (max 100,000 elements)")
    if not all(isinstance(value, str) for value in values):
        raise RuntimeError("sort_strings expects a list of strings")
    return sorted(values)


def builtin_is_some(opt: Any) -> bool:
    if isinstance(opt, ConstructorValue):
        return bool(opt.constructor == "Some")
    return False


def builtin_is_none(opt: Any) -> bool:
    if isinstance(opt, ConstructorValue):
        return bool(opt.constructor == "None")
    return opt is None


def builtin_unwrap(opt: Any) -> Any:
    if isinstance(opt, ConstructorValue):
        if opt.constructor == "Some":
            if "value" not in opt.fields:
                raise RuntimeError("unwrap: malformed Some missing 'value' field")
            return opt.fields["value"]
        elif opt.constructor == "None":
            raise RuntimeError("unwrap called on None")
    if opt is None:
        raise RuntimeError("unwrap called on None")
    raise RuntimeError(f"unwrap expects Option, got {type(opt).__name__}")


def builtin_unwrap_or(opt: Any, default: Any) -> Any:
    if isinstance(opt, ConstructorValue):
        if opt.constructor == "Some":
            return opt.fields.get("value")
        elif opt.constructor == "None":
            return default
    if opt is None:
        return default
    raise RuntimeError(f"unwrap_or expects Option, got {type(opt).__name__}")


def builtin_map_insert(
    m: dict[Any, Any],
    key: Any,
    value: Any,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> dict[Any, Any]:
    if not isinstance(m, dict):
        raise RuntimeError(f"map_insert expects map, got {type(m).__name__}")
    limit = _effective_max_collection_size(max_collection_size)
    if key not in m:
        _check_collection_size("Map", len(m) + 1, limit)
    new_map = dict(m)
    new_map[key] = value
    return new_map


def builtin_map_get(m: dict[Any, Any], key: Any) -> ConstructorValue:
    if not isinstance(m, dict):
        raise RuntimeError(f"map_get expects map, got {type(m).__name__}")
    if key in m:
        return ConstructorValue("Some", {"value": m[key]})
    return ConstructorValue("None", {})


def builtin_clock_now() -> int:
    return int(time.time())


# Directives supported by the narrow clock/datetime format/parse contract.
# See geno/std/DateTime.geno for the documented subset.
_CLOCK_DIRECTIVES: frozenset[str] = frozenset({"Y", "m", "d", "H", "M", "S", "%"})


def _validate_clock_fmt(func_name: str, fmt: str) -> None:
    """Reject any ``%X`` directive outside the documented subset.

    Guarantees that the interpreter honors the same narrow contract as the
    compiled Python and JS runtimes, so ``clock_format("%j", ...)`` no longer
    silently differs across backends.
    """
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


_CLOCK_PARSE_DIRECTIVES: dict[str, str] = {
    "%Y": r"(?P<Y>\d{4})",
    "%m": r"(?P<m>\d{2})",
    "%d": r"(?P<d>\d{2})",
    "%H": r"(?P<H>\d{2})",
    "%M": r"(?P<M>\d{2})",
    "%S": r"(?P<S>\d{2})",
}


def _clock_parse_pattern(fmt: str) -> str:
    parts: list[str] = []
    i = 0
    while i < len(fmt):
        if i + 1 < len(fmt) and fmt[i : i + 2] == "%%":
            parts.append("%")
            i += 2
        elif i + 1 < len(fmt) and fmt[i : i + 2] in _CLOCK_PARSE_DIRECTIVES:
            parts.append(_CLOCK_PARSE_DIRECTIVES[fmt[i : i + 2]])
            i += 2
        else:
            parts.append(re.escape(fmt[i]))
            i += 1
    return "^" + "".join(parts) + "$"


def builtin_clock_format(timestamp: int | float, fmt: str) -> str:
    """Format Unix timestamp to string."""
    from datetime import datetime, timezone

    if not isinstance(timestamp, (int, float)):
        raise RuntimeError("clock_format: timestamp must be a number")
    if not isinstance(fmt, str):
        raise RuntimeError("clock_format: fmt must be a string")
    if float(timestamp) < 0:
        raise RuntimeError(
            "clock_format: negative timestamps (pre-1970) are not supported"
        )
    _validate_clock_fmt("clock_format", fmt)
    try:
        dt = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
        return dt.strftime(fmt)
    except (OSError, ValueError, OverflowError) as e:
        raise RuntimeError(f"clock_format: {e}")


def builtin_clock_parse(text: str, fmt: str) -> ConstructorValue:
    """Parse date string to Unix timestamp or None."""
    from datetime import datetime, timezone

    if not isinstance(text, str):
        raise RuntimeError("clock_parse: text must be a string")
    if not isinstance(fmt, str):
        raise RuntimeError("clock_parse: fmt must be a string")
    _validate_clock_fmt("clock_parse", fmt)
    match = re.match(_clock_parse_pattern(fmt), text)
    if match is None:
        return ConstructorValue("None", {})
    groups = match.groupdict()
    try:
        dt = datetime(
            int(groups.get("Y", "1970")),
            int(groups.get("m", "1")),
            int(groups.get("d", "1")),
            int(groups.get("H", "0")),
            int(groups.get("M", "0")),
            int(groups.get("S", "0")),
            tzinfo=timezone.utc,
        )
        return ConstructorValue("Some", {"value": dt.timestamp()})
    except ValueError:
        return ConstructorValue("None", {})


def builtin_clock_elapsed(start: int | float, end_time: int | float) -> float:
    """Difference in seconds between two timestamps."""
    if not isinstance(start, (int, float)):
        raise RuntimeError("clock_elapsed: start must be a number")
    if not isinstance(end_time, (int, float)):
        raise RuntimeError("clock_elapsed: end_time must be a number")
    return float(end_time) - float(start)


def builtin_random_int(min_val: int, max_val: int) -> int:
    if not isinstance(min_val, int) or not isinstance(max_val, int):
        raise RuntimeError("random_int expects integer arguments")
    return random.randint(min_val, max_val)


def builtin_random_float() -> float:
    return random.random()


def builtin_array_new(
    size: int, default: Any, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> ArrayValue:
    if not isinstance(size, int):
        raise RuntimeError("array_new size must be an integer")
    if size < 0:
        raise RuntimeError(f"array_new size must be non-negative, got {size}")
    limit = _effective_max_collection_size(max_collection_size)
    if size > limit:
        raise RuntimeError(f"Array size exceeds limit ({size} > {limit})")
    return ArrayValue([default] * size)


def builtin_array_from_list(
    lst: list[Any], *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> ArrayValue:
    _require_list("array_from_list", lst)
    limit = _effective_max_collection_size(max_collection_size)
    if len(lst) > limit:
        raise RuntimeError(f"Array size exceeds limit ({len(lst)} > {limit})")
    return ArrayValue(list(lst))


def builtin_array_get(arr: ArrayValue, index: int) -> Any:
    if not isinstance(arr, ArrayValue):
        raise RuntimeError(f"array_get expects array, got {type(arr).__name__}")
    if not isinstance(index, int):
        raise RuntimeError("array_get index must be an integer")
    if index < 0 or index >= len(arr):
        raise RuntimeError(f"array_get index {index} out of bounds (length {len(arr)})")
    return arr[index]


def builtin_array_set(arr: ArrayValue, index: int, value: Any) -> None:
    if not isinstance(arr, ArrayValue):
        raise RuntimeError(f"array_set expects array, got {type(arr).__name__}")
    if not isinstance(index, int):
        raise RuntimeError("array_set index must be an integer")
    if index < 0 or index >= len(arr):
        raise RuntimeError(f"array_set index {index} out of bounds (length {len(arr)})")
    arr[index] = value
    return None


def builtin_array_length(arr: ArrayValue) -> int:
    if not isinstance(arr, ArrayValue):
        raise RuntimeError(f"array_length expects array, got {type(arr).__name__}")
    return len(arr)


def builtin_array_to_list(arr: ArrayValue) -> list[Any]:
    if not isinstance(arr, ArrayValue):
        raise RuntimeError(f"array_to_list expects array, got {type(arr).__name__}")
    return list(arr._elements)


def _geno_sort_key(value: Any, _seen: set[int] | None = None) -> tuple[Any, ...]:
    if _seen is None:
        _seen = set()

    if isinstance(
        value, (list, dict, tuple, ArrayValue, MutableMapValue, SetValue, VecValue)
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
    if isinstance(value, ConstructorValue):
        fields = tuple(
            (field_name, _geno_sort_key(field_value, _seen))
            for field_name, field_value in value.fields.items()
        )
        return (6, value.constructor, fields)
    if isinstance(value, list):
        return (7, tuple(_geno_sort_key(item, _seen) for item in value))
    if isinstance(value, ArrayValue):
        return (8, tuple(_geno_sort_key(item, _seen) for item in value._elements))
    if isinstance(value, MutableMapValue):
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
    if isinstance(value, SetValue):
        return (
            11,
            tuple(sorted(_geno_sort_key(item, _seen) for item in value._data)),
        )
    if isinstance(value, VecValue):
        return (12, tuple(_geno_sort_key(item, _seen) for item in value._elements))
    return (99, type(value).__name__, repr(value))


def _format_string_literal(value: str) -> str:
    """Return Geno's canonical double-quoted string representation."""
    backslash = chr(92)
    escapes = {
        backslash: backslash + backslash,
        '"': backslash + '"',
        chr(8): backslash + "b",
        chr(12): backslash + "f",
        chr(10): backslash + "n",
        chr(13): backslash + "r",
        chr(9): backslash + "t",
    }
    parts = ['"']
    for char in value:
        escaped = escapes.get(char)
        if escaped is not None:
            parts.append(escaped)
        elif ord(char) < 0x20:
            parts.append(backslash + "u" + f"{ord(char):04x}")
        else:
            parts.append(char)
    parts.append('"')
    return "".join(parts)


def format_value(value: Any, _seen: set[int] | None = None) -> str:
    """Format a value for display.

    Uses a *_seen* set (of ``id``s) to detect cycles in nested structures.
    """
    if _seen is None:
        _seen = set()

    obj_id = id(value)
    if isinstance(
        value, (list, dict, tuple, ArrayValue, MutableMapValue, SetValue, VecValue)
    ):
        if obj_id in _seen:
            if isinstance(value, ArrayValue):
                return "Array([...])"
            if isinstance(value, MutableMapValue):
                return "MutableMap({...})"
            if isinstance(value, SetValue):
                return "Set({...})"
            if isinstance(value, VecValue):
                return "Vec([...])"
            return "[...]" if isinstance(value, list) else "{...}"
        _seen = _seen | {obj_id}

    if isinstance(value, SetValue):
        elements = ", ".join(
            format_value(e, _seen) for e in sorted(value._data, key=_geno_sort_key)
        )
        return f"Set({{{elements}}})"
    if isinstance(value, ArrayValue):
        elements = ", ".join(format_value(e, _seen) for e in value._elements)
        return f"Array([{elements}])"
    if isinstance(value, MutableMapValue):
        items = ", ".join(
            f"{format_value(k, _seen)}: {format_value(v, _seen)}"
            for k, v in value._data.items()
        )
        return f"MutableMap({{{items}}})"
    if isinstance(value, VecValue):
        elements = ", ".join(format_value(e, _seen) for e in value._elements)
        return f"Vec([{elements}])"
    if isinstance(value, list):
        elements = ", ".join(format_value(e, _seen) for e in value)
        return f"[{elements}]"
    if isinstance(value, tuple):
        elements = ", ".join(format_value(e, _seen) for e in value)
        suffix = "," if len(value) == 1 else ""
        return f"({elements}{suffix})"
    if isinstance(value, dict):
        items = ", ".join(
            f"{format_value(k, _seen)}: {format_value(v, _seen)}"
            for k, v in value.items()
        )
        return f"{{{items}}}"
    if isinstance(value, ConstructorValue):
        if not value.fields:
            return str(value.constructor)
        field_strs = ", ".join(
            f"{key}: {format_value(field_value, _seen)}"
            for key, field_value in value.fields.items()
        )
        return f"{value.constructor}({field_strs})"
    if isinstance(value, Closure):
        return repr(value)
    if isinstance(value, BuiltinFunction):
        return repr(value)
    if isinstance(value, str):
        return _format_string_literal(value)
    if value is None:
        return "()"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def stringify_value(value: Any, _seen: set[int] | None = None) -> str:
    """Format a value for ``to_string`` without quoting a top-level string."""
    writer = _StringifyWriter("to_string", _MAX_COLLECTION_SIZE)
    _write_stringify_value(value, writer, _seen or set(), top_level=True)
    return writer.result()


def _stringify_value(
    value: Any, _seen: set[int] | None = None, *, top_level: bool = False
) -> str:
    writer = _StringifyWriter("to_string", _MAX_COLLECTION_SIZE)
    _write_stringify_value(value, writer, _seen or set(), top_level=top_level)
    return writer.result()


class _StringifyWriter:
    def __init__(self, func_name: str, limit: int):
        self.func_name = func_name
        self.limit = limit
        self.size = 0
        self.parts: list[str] = []

    def append(self, text: str) -> None:
        next_size = self.size + len(text)
        if next_size > self.limit:
            raise RuntimeError(
                f"{self.func_name}: String size exceeds limit "
                f"({next_size} > {self.limit})"
            )
        self.size = next_size
        self.parts.append(text)

    def result(self) -> str:
        return "".join(self.parts)


def _write_stringify_value(
    value: Any,
    writer: _StringifyWriter,
    _seen: set[int],
    *,
    top_level: bool = False,
) -> None:
    seen = _seen

    obj_id = id(value)
    if isinstance(value, (list, dict, tuple, ArrayValue, SetValue, VecValue)):
        if obj_id in seen:
            if isinstance(value, ArrayValue):
                writer.append("Array([...])")
                return
            if isinstance(value, SetValue):
                writer.append("Set({...})")
                return
            if isinstance(value, VecValue):
                writer.append("Vec([...])")
                return
            if isinstance(value, dict):
                writer.append("{...}")
                return
            writer.append("[...]" if isinstance(value, list) else "(...)")
            return
        seen = seen | {obj_id}

    if isinstance(value, str):
        writer.append(value if top_level else _format_string_literal(value))
        return
    if value is None:
        writer.append("()")
        return
    if isinstance(value, bool):
        writer.append("true" if value else "false")
        return
    if isinstance(value, SetValue):
        writer.append("Set({")
        for index, element in enumerate(sorted(value._data, key=_geno_sort_key)):
            if index:
                writer.append(", ")
            _write_stringify_value(element, writer, seen)
        writer.append("})")
        return
    if isinstance(value, ArrayValue):
        writer.append("Array([")
        for index, element in enumerate(value._elements):
            if index:
                writer.append(", ")
            _write_stringify_value(element, writer, seen)
        writer.append("])")
        return
    if isinstance(value, VecValue):
        writer.append("Vec([")
        for index, element in enumerate(value._elements):
            if index:
                writer.append(", ")
            _write_stringify_value(element, writer, seen)
        writer.append("])")
        return
    if isinstance(value, list):
        writer.append("[")
        for index, element in enumerate(value):
            if index:
                writer.append(", ")
            _write_stringify_value(element, writer, seen)
        writer.append("]")
        return
    if isinstance(value, tuple):
        if not value:
            writer.append("()")
            return
        writer.append("(")
        for index, element in enumerate(value):
            if index:
                writer.append(", ")
            _write_stringify_value(element, writer, seen)
        if len(value) == 1:
            writer.append(",")
        writer.append(")")
        return
    if isinstance(value, MutableMapValue):
        writer.append("MutableMap({")
        for index, (key, item) in enumerate(value._data.items()):
            if index:
                writer.append(", ")
            _write_stringify_value(key, writer, seen)
            writer.append(": ")
            _write_stringify_value(item, writer, seen)
        writer.append("})")
        return
    if isinstance(value, dict):
        writer.append("{")
        for index, (key, item) in enumerate(value.items()):
            if index:
                writer.append(", ")
            _write_stringify_value(key, writer, seen)
            writer.append(": ")
            _write_stringify_value(item, writer, seen)
        writer.append("}")
        return
    if isinstance(value, ConstructorValue):
        if not value.fields:
            writer.append(value.constructor)
            return
        writer.append(value.constructor)
        writer.append("(")
        for index, (key, field_value) in enumerate(value.fields.items()):
            if index:
                writer.append(", ")
            writer.append(f"{key}: ")
            _write_stringify_value(field_value, writer, seen)
        writer.append(")")
        return
    if isinstance(value, Closure):
        writer.append(repr(value))
        return
    if isinstance(value, BuiltinFunction):
        writer.append(repr(value))
        return
    writer.append(str(value))


# =============================================================================
# Array Helpers
# =============================================================================


def builtin_array_fill(arr: ArrayValue, value: Any) -> None:
    if not isinstance(arr, ArrayValue):
        raise RuntimeError("array_fill expects array")
    for i in range(len(arr)):
        arr[i] = value
    return None


def builtin_array_copy(arr: ArrayValue) -> ArrayValue:
    if not isinstance(arr, ArrayValue):
        raise RuntimeError("array_copy expects array")
    return ArrayValue(list(arr._elements))


# =============================================================================
# MutableMap Builtins
# =============================================================================


def builtin_mutable_map_new() -> MutableMapValue:
    return MutableMapValue()


def builtin_mutable_map_set(
    m: MutableMapValue,
    key: Any,
    value: Any,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> None:
    if not isinstance(m, MutableMapValue):
        raise RuntimeError("mutable_map_set expects MutableMap")
    _require_hashable("mutable_map_set", key, "key")
    if key not in m._data:
        limit = _effective_max_collection_size(max_collection_size)
        _check_collection_size("MutableMap", len(m._data) + 1, limit)
    m._data[key] = value
    return None


def builtin_mutable_map_get(m: MutableMapValue, key: Any) -> ConstructorValue:
    if not isinstance(m, MutableMapValue):
        raise RuntimeError("mutable_map_get expects MutableMap")
    if key in m._data:
        return ConstructorValue("Some", {"value": m._data[key]})
    return ConstructorValue("None", {})


def builtin_mutable_map_contains(m: MutableMapValue, key: Any) -> bool:
    if not isinstance(m, MutableMapValue):
        raise RuntimeError("mutable_map_contains expects MutableMap")
    return key in m._data


def builtin_mutable_map_delete(m: MutableMapValue, key: Any) -> None:
    if not isinstance(m, MutableMapValue):
        raise RuntimeError("mutable_map_delete expects MutableMap")
    m._data.pop(key, None)
    return None


def builtin_mutable_map_size(m: MutableMapValue) -> int:
    if not isinstance(m, MutableMapValue):
        raise RuntimeError("mutable_map_size expects MutableMap")
    return len(m._data)


def builtin_mutable_map_keys(m: MutableMapValue) -> list[Any]:
    if not isinstance(m, MutableMapValue):
        raise RuntimeError("mutable_map_keys expects MutableMap")
    return list(m._data.keys())


# =============================================================================
# Vec Builtins
# =============================================================================


def builtin_vec_new() -> VecValue:
    return VecValue()


def builtin_vec_push(
    v: VecValue, item: Any, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> None:
    if not isinstance(v, VecValue):
        raise RuntimeError("vec_push expects Vec")
    limit = _effective_max_collection_size(max_collection_size)
    _check_collection_size("Vec", len(v) + 1, limit)
    v._elements.append(item)
    return None


def builtin_vec_get(v: VecValue, index: int) -> Any:
    if not isinstance(v, VecValue):
        raise RuntimeError("vec_get expects Vec")
    if not isinstance(index, int):
        raise RuntimeError("vec_get index must be integer")
    if index < 0 or index >= len(v):
        raise RuntimeError(f"vec_get index {index} out of bounds (length {len(v)})")
    return v._elements[index]


def builtin_vec_set(v: VecValue, index: int, value: Any) -> None:
    if not isinstance(v, VecValue):
        raise RuntimeError("vec_set expects Vec")
    if not isinstance(index, int):
        raise RuntimeError("vec_set index must be integer")
    if index < 0 or index >= len(v):
        raise RuntimeError(f"vec_set index {index} out of bounds (length {len(v)})")
    v._elements[index] = value
    return None


def builtin_vec_length(v: VecValue) -> int:
    if not isinstance(v, VecValue):
        raise RuntimeError("vec_length expects Vec")
    return len(v)


def builtin_vec_pop(v: VecValue) -> ConstructorValue:
    if not isinstance(v, VecValue):
        raise RuntimeError("vec_pop expects Vec")
    if len(v) == 0:
        return ConstructorValue("None", {})
    return ConstructorValue("Some", {"value": v._elements.pop()})


def builtin_vec_to_list(v: VecValue) -> list[Any]:
    if not isinstance(v, VecValue):
        raise RuntimeError("vec_to_list expects Vec")
    return list(v._elements)


def builtin_vec_from_list(lst: list[Any]) -> VecValue:
    if not isinstance(lst, list):
        raise RuntimeError("vec_from_list expects list")
    v = VecValue()
    object.__setattr__(v, "_elements", list(lst))
    return v


# =============================================================================
# Set Builtins
# =============================================================================


def builtin_set_new() -> SetValue:
    return SetValue()


def _require_hashable(name: str, value: Any, label: str = "value") -> None:
    try:
        hash(value)
    except TypeError:
        raise RuntimeError(
            f"{name} {label} must be hashable, got {type(value).__name__}"
        )


def builtin_set_from_list(
    lst: list[Any], *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> SetValue:
    if not isinstance(lst, list):
        raise RuntimeError("set_from_list expects list")
    s = SetValue()
    for item in lst:
        _require_hashable("set_from_list", item, "items")
    data = set(lst)
    limit = _effective_max_collection_size(max_collection_size)
    if len(data) > limit:
        raise RuntimeError(f"Set size exceeds limit ({len(data)} > {limit})")
    object.__setattr__(s, "_data", data)
    return s


def builtin_set_add(
    s: SetValue, item: Any, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> None:
    if not isinstance(s, SetValue):
        raise RuntimeError("set_add expects Set")
    _require_hashable("set_add", item)
    if item not in s._data:
        limit = _effective_max_collection_size(max_collection_size)
        _check_collection_size("Set", len(s._data) + 1, limit)
    s._data.add(item)
    return None


def builtin_set_remove(s: SetValue, item: Any) -> None:
    if not isinstance(s, SetValue):
        raise RuntimeError("set_remove expects Set")
    _require_hashable("set_remove", item)
    s._data.discard(item)
    return None


def builtin_set_contains(s: SetValue, item: Any) -> bool:
    if not isinstance(s, SetValue):
        raise RuntimeError("set_contains expects Set")
    _require_hashable("set_contains", item)
    return item in s._data


def builtin_set_size(s: SetValue) -> int:
    if not isinstance(s, SetValue):
        raise RuntimeError("set_size expects Set")
    return len(s._data)


def builtin_set_to_list(s: SetValue) -> list[Any]:
    if not isinstance(s, SetValue):
        raise RuntimeError("set_to_list expects Set")
    return sorted(s._data, key=_geno_sort_key)


def builtin_set_union(
    a: SetValue, b: SetValue, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> SetValue:
    if not isinstance(a, SetValue) or not isinstance(b, SetValue):
        raise RuntimeError("set_union expects two Sets")
    data = a._data | b._data
    limit = _effective_max_collection_size(max_collection_size)
    if len(data) > limit:
        raise RuntimeError(f"Set size exceeds limit ({len(data)} > {limit})")
    result = SetValue()
    object.__setattr__(result, "_data", data)
    return result


def builtin_set_intersection(a: SetValue, b: SetValue) -> SetValue:
    if not isinstance(a, SetValue) or not isinstance(b, SetValue):
        raise RuntimeError("set_intersection expects two Sets")
    result = SetValue()
    object.__setattr__(result, "_data", a._data & b._data)
    return result


# =============================================================================
# Graphics Builtins (stubs for interpreter mode)
# =============================================================================


def builtin_clear_screen(color: Any) -> None:
    """No-op in interpreter mode."""
    return None


def builtin_draw_rect(x: Any, y: Any, w: Any, h: Any, color: Any) -> None:
    """No-op in interpreter mode."""
    return None


def builtin_draw_rect_outline(x: Any, y: Any, w: Any, h: Any, color: Any) -> None:
    """No-op in interpreter mode."""
    return None


def builtin_draw_circle(x: Any, y: Any, radius: Any, color: Any) -> None:
    """No-op in interpreter mode."""
    return None


def builtin_draw_line(x1: Any, y1: Any, x2: Any, y2: Any, color: Any) -> None:
    """No-op in interpreter mode."""
    return None


def builtin_draw_text(text: Any, x: Any, y: Any, size: Any, color: Any) -> None:
    """No-op in interpreter mode."""
    return None


def builtin_screen_width() -> int:
    """Returns default canvas width in interpreter mode."""
    return 800


def builtin_screen_height() -> int:
    """Returns default canvas height in interpreter mode."""
    return 600


# =============================================================================
# Input Builtins (stubs for interpreter mode)
# =============================================================================


def builtin_is_key_down(key: str) -> bool:
    """Always returns false in interpreter mode."""
    return False


def builtin_is_key_pressed(key: str) -> bool:
    """Always returns false in interpreter mode."""
    return False


# =============================================================================
# Mouse Input Builtins (stubs for interpreter mode)
# =============================================================================


def builtin_mouse_x() -> int:
    """Always returns 0 in interpreter mode."""
    return 0


def builtin_mouse_y() -> int:
    """Always returns 0 in interpreter mode."""
    return 0


def builtin_is_mouse_down() -> bool:
    """Always returns false in interpreter mode."""
    return False


def builtin_is_mouse_clicked() -> bool:
    """Always returns false in interpreter mode."""
    return False


# =============================================================================
# Text Input Builtins (stubs for interpreter mode)
# =============================================================================


def builtin_get_text_input() -> str:
    """Always returns empty string in interpreter mode."""
    return ""


def builtin_clear_text_input() -> None:
    """No-op in interpreter mode."""
    return None


# =============================================================================
# Regex Builtins (capability-gated: --cap regex)
# =============================================================================


_MAX_REGEX_PATTERN_LEN = 1000
_MAX_REGEX_TEXT_LEN = 10_000
_MAX_REGEX_REPEAT = _MAX_REGEX_TEXT_LEN
_MAX_REGEX_GROUP_DEPTH = 128
_BACKREF_RE = re.compile(r"\\[1-9]|\(\?P=[A-Za-z_][A-Za-z0-9_]*\)")

_PORTABLE_REGEX_LITERAL_ESCAPES = frozenset(r"\.^$|?*+()[]{}")
_PORTABLE_REGEX_CLASS_ESCAPES = frozenset(r"\^-]")


def _is_ascii_regex_digit(character: str) -> bool:
    return "0" <= character <= "9"


def _regex_group_depth_exceeds_limit(pattern: str) -> bool:
    """Check nesting iteratively before recursive regex safety scans."""
    depth = 0
    in_class = False
    i = 0
    while i < len(pattern):
        character = pattern[i]
        if character == "\\":
            i += 2
            continue
        if character == "[" and not in_class:
            in_class = True
        elif character == "]" and in_class:
            in_class = False
        elif not in_class and character == "(":
            depth += 1
            if depth > _MAX_REGEX_GROUP_DEPTH:
                return True
        elif not in_class and character == ")" and depth:
            depth -= 1
        i += 1
    return False


def _portable_regex_quantifier_end(pattern: str, start: int) -> int | None:
    """Return the end of a bounded portable quantifier, or ``None``."""
    i = start + 1
    lower_start = i
    while i < len(pattern) and _is_ascii_regex_digit(pattern[i]):
        i += 1
    if i == lower_start:
        return None
    lower = int(pattern[lower_start:i])
    upper: int | None = lower
    if i < len(pattern) and pattern[i] == ",":
        i += 1
        upper_start = i
        while i < len(pattern) and _is_ascii_regex_digit(pattern[i]):
            i += 1
        upper = int(pattern[upper_start:i]) if i > upper_start else None
    if i >= len(pattern) or pattern[i] != "}":
        return None
    if lower > _MAX_REGEX_REPEAT or (
        upper is not None and (upper > _MAX_REGEX_REPEAT or lower > upper)
    ):
        return None
    end = i + 1
    if end < len(pattern) and pattern[end] == "?":
        end += 1
    return end


def _has_unsupported_regex_construct(pattern: str) -> bool:
    """Reject syntax whose meaning differs across the Python and JS engines."""
    i = 0
    in_class = False
    has_alternation = False
    has_quantifier = False
    has_start_anchor = False
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            if i + 1 >= len(pattern):
                return True
            escaped = pattern[i + 1]
            allowed = (
                _PORTABLE_REGEX_CLASS_ESCAPES
                if in_class
                else _PORTABLE_REGEX_LITERAL_ESCAPES
            )
            if escaped not in allowed:
                return True
            i += 2
            continue
        if ch == "[" and not in_class:
            member = i + 1
            if member < len(pattern) and pattern[member] == "^":
                member += 1
            if member < len(pattern) and pattern[member] == "]":
                return True
            in_class = True
            i += 1
            continue
        if ch == "]" and in_class:
            in_class = False
            i += 1
            continue
        if not in_class and ch == "$":
            return True
        if not in_class and ch == "^":
            has_start_anchor = True
        if not in_class and ch == "|":
            if i == 0 or i + 1 == len(pattern):
                return True
            if pattern[i - 1] in "(|" or pattern[i + 1] in ")|":
                return True
            has_alternation = True
        if not in_class and ch == "{":
            end = _portable_regex_quantifier_end(pattern, i)
            if end is None:
                return True
            has_quantifier = True
            i = end
            continue
        if not in_class and ch == "}":
            return True
        if ch == "." and not in_class:
            return True
        if ch == "(" and not in_class:
            if i + 1 < len(pattern) and pattern[i + 1] in ("?", ")"):
                return True
        if not in_class and ch in ("*", "+", "?"):
            has_quantifier = True
            if i + 1 < len(pattern) and pattern[i + 1] == "+":
                return True
        i += 1
    return in_class or (has_alternation and (has_quantifier or has_start_anchor))


def _count_variable_regex_quantifiers(pattern: str) -> int:
    """Count variable quantifiers without interpreting escaped/class text."""
    count = 0
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            class_end = _regex_char_class_end(pattern, i)
            if class_end is None:
                return count
            i = class_end + 1
            continue
        if ch in ("*", "+", "?"):
            count += 1
            i += 1
            if i < len(pattern) and pattern[i] == "?":
                i += 1
            continue
        if ch == "{" and i + 1 < len(pattern) and _is_ascii_regex_digit(pattern[i + 1]):
            end = i + 2
            while end < len(pattern) and _is_ascii_regex_digit(pattern[end]):
                end += 1
            if end < len(pattern) and pattern[end] == ",":
                end += 1
                while end < len(pattern) and _is_ascii_regex_digit(pattern[end]):
                    end += 1
                if end < len(pattern) and pattern[end] == "}":
                    count += 1
                    i = end + 1
                    if i < len(pattern) and pattern[i] == "?":
                        i += 1
                    continue
        i += 1
    return count


def _count_regex_alternation_sites(pattern: str) -> int:
    """Count distinct nesting levels that contain alternation."""
    sites: set[int] = set()
    stack = [0]
    next_group = 1
    i = 0
    while i < len(pattern):
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == "[":
            class_end = _regex_char_class_end(pattern, i)
            if class_end is None:
                return len(sites)
            i = class_end + 1
            continue
        if pattern[i] == "(":
            stack.append(next_group)
            next_group += 1
        elif pattern[i] == ")" and len(stack) > 1:
            stack.pop()
        elif pattern[i] == "|":
            sites.add(stack[-1])
        i += 1
    return len(sites)


def _has_nested_quantifier(pattern: str) -> bool:
    """Detect quantified groups that contain inner quantifiers (ReDoS risk).

    Handles arbitrary nesting depth — e.g. ``((a+))+`` is caught even
    though the inner ``+`` is two levels deep.
    """
    n = len(pattern)
    i = 0
    while i < n:
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == ")":
            # Check if this ')' is followed by a quantifier
            j = i + 1
            while j < n and pattern[j] in (" ", "\t"):
                j += 1
            if j < n and pattern[j] in ("+", "*", "?", "{"):
                # Walk backwards to find the matching '('
                depth = 1
                k = i - 1
                while k >= 0 and depth > 0:
                    if pattern[k] == ")" and (k == 0 or pattern[k - 1] != "\\"):
                        depth += 1
                    elif pattern[k] == "(" and (k == 0 or pattern[k - 1] != "\\"):
                        depth -= 1
                    k -= 1
                group_start = k + 2  # first char after '('
                # Scan interior for any unescaped quantifier
                m = group_start
                while m < i:
                    if pattern[m] == "\\":
                        m += 2
                        continue
                    if pattern[m] in ("+", "*"):
                        return True
                    if pattern[m] == "?" and m > group_start and pattern[m - 1] != "(":
                        return True
                    if (
                        pattern[m] == "{"
                        and m + 1 < i
                        and _is_ascii_regex_digit(pattern[m + 1])
                    ):
                        return True
                    m += 1
        i += 1
    return False


def _has_overlapping_alternation(pattern: str) -> bool:
    """Conservatively reject alternation anywhere inside a repeated group.

    Source-level branch comparison is not sufficient because equivalent atoms can
    be spelled differently (for example ``a`` and ``[a]``). Repeated alternation
    is therefore outside Geno's safe regex subset, even when branches appear
    distinct.
    """

    n = len(pattern)
    i = 0
    while i < n:
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == "[":
            i += 1
            while i < n:
                if pattern[i] == "\\":
                    i += 2
                    continue
                if pattern[i] == "]":
                    i += 1
                    break
                i += 1
            continue
        if pattern[i] != "(":
            i += 1
            continue

        group_start = i
        depth = 1
        has_alternation = False
        in_char_class = False
        j = i + 1
        while j < n and depth > 0:
            ch = pattern[j]
            if ch == "\\":
                j += 2
                continue
            if in_char_class:
                if ch == "]":
                    in_char_class = False
                j += 1
                continue
            if ch == "[":
                in_char_class = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            elif ch == "|":
                has_alternation = True
            j += 1

        if depth != 0:
            return False

        quant_idx = j + 1
        while quant_idx < n and pattern[quant_idx] in (" ", "\t"):
            quant_idx += 1
        if (
            has_alternation
            and quant_idx < n
            and pattern[quant_idx] in ("+", "*", "?", "{")
        ):
            return True

        # Continue inside this group so a quantified nested group is checked even
        # when its parent group is not itself repeated.
        i = group_start + 1

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


def _regex_quantifier_end(
    pattern: str, start: int
) -> tuple[int, bool, bool, bool] | None:
    if start >= len(pattern):
        return None
    if pattern[start] in ("*", "+", "?"):
        marker = pattern[start]
        end = start + 1
        if end < len(pattern) and pattern[end] == "?":
            end += 1
        return end, True, True, marker == "+"
    if pattern[start] != "{" or start + 1 >= len(pattern):
        return None

    i = start + 1
    while i < len(pattern) and _is_ascii_regex_digit(pattern[i]):
        i += 1
    has_comma = i < len(pattern) and pattern[i] == ","
    minimum_text = pattern[start + 1 : i].lstrip("0")
    maximum_text = minimum_text
    if has_comma:
        i += 1
        maximum_start = i
        while i < len(pattern) and _is_ascii_regex_digit(pattern[i]):
            i += 1
        maximum_text = pattern[maximum_start:i].lstrip("0")
    if i >= len(pattern) or pattern[i] != "}":
        return None
    end = i + 1
    if end < len(pattern) and pattern[end] == "?":
        end += 1
    required = minimum_text not in ("", "0")
    can_consume = required or (has_comma and maximum_text != "0")
    return end, has_comma, can_consume, required


def _regex_char_class_key(pattern: str, start: int, end: int) -> tuple[str, str]:
    content = pattern[start + 1 : end]
    if len(content) == 1:
        return ("literal", content)
    if len(content) == 2 and content[0] == "\\":
        return ("literal", content[1])
    return ("class", content)


def _regex_quantified_atoms_overlap(
    left: tuple[str, str], right: tuple[str, str]
) -> bool:
    if left == right:
        return True
    if left == ("literal", ".") or right == ("literal", "."):
        return True
    return left[0] != "literal" or right[0] != "literal"


def _has_sequential_quantified_atoms(pattern: str) -> bool:
    """Detect a variable repetition followed by a potentially overlapping atom.

    Even an unquantified or exact-count suffix can multiply backtracking work.
    """
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
            quantifier_end = atom_end
            is_ambiguous = False
            can_consume = True
            required = True
        else:
            quantifier_end, is_ambiguous, can_consume, required = quantifier
        if (
            previous_key is not None
            and can_consume
            and _regex_quantified_atoms_overlap(previous_key, key)
        ):
            return True
        if previous_key is not None and required:
            previous_key = None
        if is_ambiguous and can_consume:
            previous_key = key
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
    if _regex_group_depth_exceeds_limit(pattern):
        raise RuntimeError(
            f"{func_name}: group nesting too deep (max {_MAX_REGEX_GROUP_DEPTH})"
        )
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
    if _count_variable_regex_quantifiers(pattern) > 1:
        raise RuntimeError(
            f"{func_name}: multiple variable quantifiers are not supported for safety"
        )
    if _count_regex_alternation_sites(pattern) > 1:
        raise RuntimeError(
            f"{func_name}: multiple alternation sites are not supported for safety"
        )
    if _has_unsupported_regex_construct(pattern):
        raise RuntimeError(
            f"{func_name}: advanced or encoded regex constructs are not supported "
            "for safety"
        )


def _validate_regex_text(text: str, func_name: str, arg_name: str = "text") -> None:
    if not isinstance(text, str):
        raise RuntimeError(f"{func_name}: {arg_name} must be a string")
    if len(text) > _MAX_REGEX_TEXT_LEN:
        raise RuntimeError(
            f"{func_name}: {arg_name} too long (max {_MAX_REGEX_TEXT_LEN} chars)"
        )


def builtin_regex_match(pattern: str, text: str) -> ConstructorValue:
    """Return first match or None."""
    _validate_regex_pattern(pattern, "regex_match")
    _validate_regex_text(text, "regex_match")
    try:
        m = re.search(pattern, text)
    except (re.error, OverflowError) as e:
        raise RuntimeError(f"regex_match: invalid pattern: {e}")
    if m is None:
        return ConstructorValue("None", {})
    return ConstructorValue("Some", {"value": m.group()})


def builtin_regex_find_all(
    pattern: str, text: str, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> list[str]:
    """Return all matches."""
    _validate_regex_pattern(pattern, "regex_find_all")
    _validate_regex_text(text, "regex_find_all")
    limit = _effective_max_collection_size(max_collection_size)
    try:
        result: list[str] = []
        for match in re.finditer(pattern, text):
            value = match.group()
            _check_result_string_size("regex_find_all", len(value), limit)
            _check_collection_size("List", len(result) + 1, limit)
            result.append(value)
        return result
    except (re.error, OverflowError) as e:
        raise RuntimeError(f"regex_find_all: invalid pattern: {e}")


def _expand_regex_replacement(
    replacement: str,
    match: Any,
    total_before: int,
    max_collection_size: int,
) -> tuple[str, int]:
    pieces: list[str] = []
    literal: list[str] = []
    added = 0

    def flush_literal() -> None:
        nonlocal added
        if not literal:
            return
        value = "".join(literal)
        _check_result_string_size(
            "regex_replace", total_before + added + len(value), max_collection_size
        )
        pieces.append(value)
        added += len(value)
        literal.clear()

    i = 0
    while i < len(replacement):
        if (
            replacement[i] == "\\"
            and i + 1 < len(replacement)
            and replacement[i + 1] in "123456789"
        ):
            flush_literal()
            end = i + 2
            while end < len(replacement) and _is_ascii_regex_digit(replacement[end]):
                end += 1
            group_text = replacement[i + 1 : end]
            max_group_text = str(len(match.groups()))
            if len(group_text) > len(max_group_text) or (
                len(group_text) == len(max_group_text) and group_text > max_group_text
            ):
                raise RuntimeError("regex_replace: invalid replacement group reference")
            group_index = int(group_text)
            try:
                value = match.group(group_index) or ""
            except IndexError as exc:
                raise RuntimeError(
                    "regex_replace: invalid replacement group reference"
                ) from exc
            _check_result_string_size(
                "regex_replace", total_before + added + len(value), max_collection_size
            )
            pieces.append(value)
            added += len(value)
            i = end
            continue
        literal.append(replacement[i])
        i += 1
    flush_literal()
    return "".join(pieces), added


def _bounded_regex_replace(
    pattern: str,
    replacement: str,
    text: str,
    max_collection_size: int,
) -> str:
    compiled = re.compile(pattern)
    pieces: list[str] = []
    total = 0
    last_end = 0
    for match in compiled.finditer(text):
        prefix_size = match.start() - last_end
        _check_result_string_size(
            "regex_replace", total + prefix_size, max_collection_size
        )
        expanded, expanded_size = _expand_regex_replacement(
            replacement,
            match,
            total + prefix_size,
            max_collection_size,
        )
        total += prefix_size + expanded_size
        pieces.append(text[last_end : match.start()])
        pieces.append(expanded)
        last_end = match.end()
    tail_size = len(text) - last_end
    _check_result_string_size("regex_replace", total + tail_size, max_collection_size)
    pieces.append(text[last_end:])
    return "".join(pieces)


def builtin_regex_replace(
    pattern: str,
    replacement: str,
    text: str,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> str:
    """Replace all matches."""
    _validate_regex_pattern(pattern, "regex_replace")
    _validate_regex_text(replacement, "regex_replace", "replacement")
    _validate_regex_text(text, "regex_replace")
    try:
        limit = _effective_max_collection_size(max_collection_size)
        return _bounded_regex_replace(pattern, replacement, text, limit)
    except (re.error, OverflowError) as e:
        raise RuntimeError(f"regex_replace: invalid pattern: {e}")


# =============================================================================
# JSON Builtins
# =============================================================================

_MAX_JSON_NESTING_DEPTH = 128


def _validate_json_nesting(text: str) -> None:
    """Reject excessive JSON nesting without relying on host recursion limits."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > _MAX_JSON_NESTING_DEPTH:
                raise ValueError(
                    "json_parse: input nested too deeply "
                    f"(max {_MAX_JSON_NESTING_DEPTH})"
                )
        elif char in "]}" and depth:
            depth -= 1


def _python_to_json_value(obj: Any, limit: int | None = None) -> ConstructorValue:
    """Convert a Python object (from json.loads) to a Geno JsonValue."""
    if obj is None:
        return ConstructorValue("JsonNull", {})
    if isinstance(obj, bool):
        return ConstructorValue("JsonBool", {"value": obj})
    if isinstance(obj, int):
        return ConstructorValue("JsonInt", {"value": obj})
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("json_parse: non-finite JSON number")
        return ConstructorValue("JsonFloat", {"value": obj})
    if isinstance(obj, str):
        if limit is not None:
            _check_collection_size("String", len(obj), limit)
        return ConstructorValue("JsonString", {"value": obj})
    if isinstance(obj, list):
        if limit is not None:
            _check_collection_size("List", len(obj), limit)
        items = [_python_to_json_value(item, limit) for item in obj]
        return ConstructorValue("JsonArray", {"items": items})
    if isinstance(obj, dict):
        if limit is not None:
            _check_collection_size("Map", len(obj), limit)
        entries = [_python_to_json_value_entry(k, v, limit) for k, v in obj.items()]
        return ConstructorValue("JsonObject", {"entries": entries})
    raise RuntimeError(f"json_parse: unsupported JSON value type: {type(obj).__name__}")


def _python_to_json_value_entry(
    key: str, value: Any, limit: int | None = None
) -> tuple[str, ConstructorValue]:
    """Convert a dict entry to a (String, JsonValue) tuple."""
    if limit is not None:
        _check_collection_size("String", len(key), limit)
    return (key, _python_to_json_value(value, limit))


def _json_value_to_python(value: Any, limit: int | None = None) -> Any:
    """Convert a Geno JsonValue back to a Python object for json.dumps."""
    if not isinstance(value, ConstructorValue):
        raise RuntimeError(
            f"json_stringify: expected JsonValue, got {type(value).__name__}"
        )
    tag = value.constructor
    if tag == "JsonNull":
        return None
    if tag == "JsonBool":
        return value.fields["value"]
    if tag == "JsonInt":
        return value.fields["value"]
    if tag == "JsonFloat":
        float_value = value.fields["value"]
        if not math.isfinite(float_value):
            raise RuntimeError("json_stringify: JsonFloat must be finite")
        return float_value
    if tag == "JsonString":
        if limit is not None:
            _check_collection_size("String", len(value.fields["value"]), limit)
        return value.fields["value"]
    if tag == "JsonArray":
        items = value.fields["items"]
        if limit is not None:
            _check_collection_size("List", len(items), limit)
        return [_json_value_to_python(item, limit) for item in items]
    if tag == "JsonObject":
        entries = value.fields["entries"]
        if limit is not None:
            _check_collection_size("Map", len(entries), limit)
        result = {}
        for entry in entries:
            key, val = entry
            if limit is not None and isinstance(key, str):
                _check_collection_size("String", len(key), limit)
            result[key] = _json_value_to_python(val, limit)
        return result
    raise RuntimeError(f"json_stringify: unknown JsonValue constructor: {tag}")


def _reject_json_constant(name: str) -> None:
    raise ValueError(f"Invalid JSON constant: {name}")


def builtin_json_parse(
    text: str, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> ConstructorValue:
    """Parse a JSON string into a JsonValue."""
    if not isinstance(text, str):
        raise RuntimeError("json_parse: text must be a string")
    import json

    limit = _effective_max_collection_size(max_collection_size)
    try:
        _validate_json_nesting(text)
        obj = json.loads(text, parse_constant=_reject_json_constant)
        value = _python_to_json_value(obj, limit)
    except (ValueError, RecursionError) as e:
        return ConstructorValue("Err", {"error": str(e)})
    return ConstructorValue("Ok", {"value": value})


def builtin_json_stringify(
    value: Any, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> str:
    """Convert a JsonValue to a JSON string."""
    import json

    limit = _effective_max_collection_size(max_collection_size)
    obj = _json_value_to_python(value, limit)
    result = json.dumps(obj, separators=(",", ":"), allow_nan=False)
    _check_result_string_size("json_stringify", len(result), max_collection_size)
    return result


def builtin_json_stringify_pretty(
    value: Any,
    indent: Any,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> str:
    """Pretty-print a JsonValue. ``indent <= 0`` returns compact form.

    Object key ordering preserves JsonObject insertion order.
    """
    import json

    if not isinstance(indent, int) or isinstance(indent, bool):
        raise RuntimeError("json_stringify_pretty: indent must be Int")
    limit = _effective_max_collection_size(max_collection_size)
    obj = _json_value_to_python(value, limit)
    if indent <= 0:
        result = json.dumps(
            obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    else:
        result = json.dumps(obj, indent=indent, ensure_ascii=False, allow_nan=False)
    _check_result_string_size("json_stringify_pretty", len(result), max_collection_size)
    return result


def _geno_value_to_python(value: Any, limit: int | None = None) -> Any:
    """Convert any Geno value to a Python object for JSON serialization."""
    if isinstance(value, ConstructorValue):
        tag = value.constructor
        # JsonValue constructors
        if tag in (
            "JsonNull",
            "JsonBool",
            "JsonInt",
            "JsonFloat",
            "JsonString",
            "JsonArray",
            "JsonObject",
        ):
            return _json_value_to_python(value, limit)
        # Option
        if tag == "None":
            return None
        if tag == "Some":
            return _geno_value_to_python(value.fields["value"], limit)
        # Ok/Err
        if tag == "Ok":
            return _geno_value_to_python(value.fields["value"], limit)
        if tag == "Err":
            return {"error": _geno_value_to_python(value.fields["error"], limit)}
        # Generic ADT: serialize as {"_tag": ..., ...fields}
        result = {"_tag": tag}
        for k, v in value.fields.items():
            result[k] = _geno_value_to_python(v, limit)
        if limit is not None:
            _check_collection_size("Map", len(result), limit)
        return result
    if isinstance(value, (ArrayValue, MutableMapValue, VecValue, SetValue)):
        return format_value(value)
    if isinstance(value, dict):
        if limit is not None:
            _check_collection_size("Map", len(value), limit)
        return {str(k): _geno_value_to_python(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if limit is not None:
            kind = "Tuple" if isinstance(value, tuple) else "List"
            _check_collection_size(kind, len(value), limit)
        return [_geno_value_to_python(item, limit) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RuntimeError("json_to_string: Float must be finite")
        return value
    if isinstance(value, str):
        if limit is not None:
            _check_collection_size("String", len(value), limit)
        return value
    if value is None:
        return None
    return str(value)


def builtin_json_to_string(
    value: Any, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> str:
    """Convert any Geno value to a JSON string."""
    import json

    limit = _effective_max_collection_size(max_collection_size)
    obj = _geno_value_to_python(value, limit)
    result = json.dumps(obj, separators=(",", ":"), allow_nan=False)
    _check_result_string_size("json_to_string", len(result), max_collection_size)
    return result


# =============================================================================
# Environment Variable Builtins (capability-gated: --cap env)
# =============================================================================


def _check_csv_field(func_name: str, field: str, limit: int) -> str:
    _check_result_string_size(func_name, len(field), limit)
    return field


def _check_csv_row(func_name: str, row: list[str], limit: int) -> list[str]:
    _check_collection_size("List", len(row), limit)
    return [_check_csv_field(func_name, field, limit) for field in row]


def builtin_csv_parse(
    text: str, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> list[list[str]]:
    """Parse CSV text into a list of rows (each row is a list of strings)."""
    if not isinstance(text, str):
        raise RuntimeError("csv_parse: text must be a string")
    import csv
    import io

    reader = csv.reader(io.StringIO(text, newline=""))
    limit = _effective_max_collection_size(max_collection_size)
    rows: list[list[str]] = []
    for row in reader:
        _check_collection_size("List", len(rows) + 1, limit)
        rows.append(_check_csv_row("csv_parse", list(row), limit))
    return rows


def _csv_header_row_to_map(
    headers: list[str], row: list[str], func_name: str, limit: int
) -> dict[str, str]:
    checked: dict[str, str] = {}
    for index, key in enumerate(headers):
        _check_csv_field(func_name, key, limit)
        value = row[index] if index < len(row) else ""
        _check_csv_field(func_name, value, limit)
        if key not in checked:
            _check_collection_size("Map", len(checked) + 1, limit)
        checked[key] = value
    return checked


def builtin_csv_parse_with_headers(
    text: str, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> list[dict[str, str]]:
    """Parse CSV with first row as headers, returning list of maps."""
    if not isinstance(text, str):
        raise RuntimeError("csv_parse_with_headers: text must be a string")
    import csv
    import io

    reader = csv.reader(io.StringIO(text, newline=""))
    limit = _effective_max_collection_size(max_collection_size)
    try:
        headers = list(next(reader))
    except StopIteration:
        return []

    rows: list[dict[str, str]] = []
    for row in reader:
        _check_collection_size("List", len(rows) + 1, limit)
        rows.append(
            _csv_header_row_to_map(headers, list(row), "csv_parse_with_headers", limit)
        )
    return rows


def builtin_toml_parse(
    text: str, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> ConstructorValue:
    """Parse a TOML string into a JsonValue."""
    if not isinstance(text, str):
        raise RuntimeError("toml_parse: text must be a string")
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return ConstructorValue(
                "Err",
                {
                    "error": "TOML parsing not available (install tomli for Python <3.11)"
                },
            )
    try:
        obj = tomllib.loads(text)
        limit = _effective_max_collection_size(max_collection_size)
        return ConstructorValue("Ok", {"value": _python_to_json_value(obj, limit)})
    except ValueError as e:
        return ConstructorValue("Err", {"error": str(e)})


def builtin_env_get(
    name: str, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> ConstructorValue:
    """Return the value of an environment variable, or None if unset."""
    if not isinstance(name, str):
        raise RuntimeError("env_get: name must be a string")
    value = os.environ.get(name)
    if value is None:
        return ConstructorValue("None", {})
    _check_result_string_size("env_get", len(value), max_collection_size)
    return ConstructorValue("Some", {"value": value})


def builtin_env_get_or(
    name: str,
    default: str,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> str:
    """Return the value of an environment variable, or a default if unset."""
    if not isinstance(name, str):
        raise RuntimeError("env_get_or: name must be a string")
    if not isinstance(default, str):
        raise RuntimeError("env_get_or: default must be a string")
    result = os.environ.get(name, default)
    _check_result_string_size("env_get_or", len(result), max_collection_size)
    return result


def builtin_cli_args(
    *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> list[str]:
    """Return command-line arguments passed after '--' to the program."""
    limit = _effective_max_collection_size(max_collection_size)
    env_args = os.environ.get("GENO_CLI_ARGS")
    if env_args is not None:
        import json

        try:
            parsed = json.loads(env_args)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"cli_args: invalid GENO_CLI_ARGS JSON: {exc}") from exc
        if not isinstance(parsed, list) or not all(
            isinstance(arg, str) for arg in parsed
        ):
            raise RuntimeError(
                "cli_args: GENO_CLI_ARGS must be a JSON array of strings"
            )
        _check_collection_size("List", len(parsed), limit)
        for arg in parsed:
            _check_collection_size("String", len(arg), limit)
        return parsed

    import sys

    argv = sys.argv
    if "--" in argv:
        idx = argv.index("--")
        result = list(argv[idx + 1 :])
        _check_collection_size("List", len(result), limit)
        for arg in result:
            _check_collection_size("String", len(arg), limit)
        return result
    return []


# =============================================================================
# Collection Builtins
# =============================================================================


def builtin_zip(list1: list[Any], list2: list[Any]) -> list[tuple[Any, Any]]:
    """Combine two lists pairwise into a list of tuples."""
    _require_list("zip", list1)
    _require_list("zip", list2)
    return [(a, b) for a, b in zip(list1, list2)]


def builtin_enumerate(lst: list[Any]) -> list[tuple[int, Any]]:
    """Return a list of (index, element) tuples."""
    _require_list("enumerate", lst)
    return [(i, v) for i, v in enumerate(lst)]


def builtin_flat_map(
    lst: list[Any],
    fn: Callable[..., Any],
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> list[Any]:
    """Map a function over a list and flatten the results."""
    _require_list("flat_map", lst)
    limit = _effective_max_collection_size(max_collection_size)
    result: list[Any] = []
    for item in lst:
        mapped = fn(item)
        if not isinstance(mapped, list):
            raise RuntimeError("flat_map: function must return a list")
        _check_collection_size("List", len(result) + len(mapped), limit)
        result.extend(mapped)
    return result


# =============================================================================
# String Builtins (extended)
# =============================================================================


def builtin_contains_substring(text: str, sub: str) -> bool:
    """Check if a string contains a substring."""
    _require_str("contains_substring", text, "text")
    _require_str("contains_substring", sub, "substring")
    return sub in text


def builtin_repeat_string(
    text: str, count: int, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> str:
    """Repeat a string a given number of times."""
    _require_str("repeat_string", text, "text")
    _require_int("repeat_string", count, "count")
    if count < 0:
        raise RuntimeError("repeat_string: count must be non-negative")
    limit = _effective_max_collection_size(max_collection_size)
    if len(text) * count > limit:
        raise RuntimeError(
            "repeat_string: result would exceed collection size limit "
            f"({len(text) * count} > {limit})"
        )
    return text * count


def builtin_string_trim(text: str) -> str:
    """Trim whitespace from both ends (stdlib wrapper)."""
    _require_str("string_trim", text, "text")
    return text.strip()


def builtin_string_trim_start(text: str) -> str:
    """Remove leading whitespace from a string."""
    _require_str("string_trim_start", text, "text")
    return text.lstrip()


def builtin_string_trim_end(text: str) -> str:
    """Remove trailing whitespace from a string."""
    _require_str("string_trim_end", text, "text")
    return text.rstrip()


def builtin_string_pad_left(
    text: str,
    width: int,
    fill_char: str,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> str:
    """Pad a string on the left to the given width."""
    _require_str("string_pad_left", text, "text")
    _require_int("string_pad_left", width, "width")
    _require_str("string_pad_left", fill_char, "fill_char")
    if len(fill_char) != 1:
        raise RuntimeError("string_pad_left: fill_char must be a single character")
    _check_result_string_size(
        "string_pad_left", max(len(text), width), max_collection_size
    )
    return text.rjust(width, fill_char)


def builtin_string_pad_right(
    text: str,
    width: int,
    fill_char: str,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> str:
    """Pad a string on the right to the given width."""
    _require_str("string_pad_right", text, "text")
    _require_int("string_pad_right", width, "width")
    _require_str("string_pad_right", fill_char, "fill_char")
    if len(fill_char) != 1:
        raise RuntimeError("string_pad_right: fill_char must be a single character")
    _check_result_string_size(
        "string_pad_right", max(len(text), width), max_collection_size
    )
    return text.ljust(width, fill_char)


def builtin_string_char_at(text: str, index: int) -> str:
    """Get the character at the given index, or empty string if out of bounds."""
    _require_str("string_char_at", text, "text")
    _require_int("string_char_at", index, "index")
    if index < 0 or index >= len(text):
        return ""
    return text[index]


def builtin_string_index_of(text: str, sub: str) -> int:
    """Find the first index of a substring, or -1 if not found."""
    _require_str("string_index_of", text, "text")
    _require_str("string_index_of", sub, "substring")
    return text.find(sub)


def builtin_string_last_index_of(text: str, sub: str) -> int:
    """Find the last index of a substring, or -1 if not found."""
    _require_str("string_last_index_of", text, "text")
    _require_str("string_last_index_of", sub, "substring")
    return text.rfind(sub)


def builtin_string_repeat(
    text: str, count: int, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> str:
    """Repeat a string (stdlib wrapper)."""
    _require_str("string_repeat", text, "text")
    _require_int("string_repeat", count, "count")
    if count < 0:
        raise RuntimeError("string_repeat: count must be non-negative")
    limit = _effective_max_collection_size(max_collection_size)
    if len(text) * count > limit:
        raise RuntimeError(
            "string_repeat: result would exceed collection size limit "
            f"({len(text) * count} > {limit})"
        )
    return text * count


def builtin_string_substring(text: str, start: int, stop: int) -> str:
    """Substring extraction (stdlib wrapper)."""
    _require_str("string_substring", text, "text")
    _require_int("string_substring", start, "start")
    _require_int("string_substring", stop, "stop")
    start = max(0, start)
    stop = min(len(text), stop)
    return text[start:stop]


def builtin_string_split(
    text: str,
    delimiter: str,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> list[str]:
    """Split a string by delimiter (stdlib wrapper)."""
    _require_str("string_split", text, "text")
    _require_str("string_split", delimiter, "delimiter")
    limit = _effective_max_collection_size(max_collection_size)
    _check_collection_size(
        "List", _split_result_count("string_split", text, delimiter), limit
    )
    return text.split(delimiter)


def builtin_string_join(
    parts: list[Any],
    separator: str,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> str:
    """Join a list of strings with a separator (stdlib wrapper)."""
    _require_list("string_join", parts)
    _require_str("string_join", separator, "separator")
    return _join_strings_under_limit(
        "string_join", parts, separator, max_collection_size
    )


def builtin_string_replace(
    text: str,
    old: str,
    new: str,
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> str:
    """Replace all occurrences of old with new (stdlib wrapper)."""
    _require_str("string_replace", text, "text")
    _require_str("string_replace", old, "old")
    _require_str("string_replace", new, "new")
    _check_result_string_size(
        "string_replace", _replace_result_size(text, old, new), max_collection_size
    )
    return text.replace(old, new)


def builtin_string_to_upper(text: str) -> str:
    """Convert string to uppercase (stdlib wrapper)."""
    _require_str("string_to_upper", text, "text")
    return text.upper()


def builtin_string_to_lower(text: str) -> str:
    """Convert string to lowercase (stdlib wrapper)."""
    _require_str("string_to_lower", text, "text")
    return text.lower()


def builtin_string_starts_with(text: str, prefix: str) -> bool:
    """Check if string starts with prefix (stdlib wrapper)."""
    _require_str("string_starts_with", text, "text")
    _require_str("string_starts_with", prefix, "prefix")
    return text.startswith(prefix)


def builtin_string_ends_with(text: str, suffix: str) -> bool:
    """Check if string ends with suffix (stdlib wrapper)."""
    _require_str("string_ends_with", text, "text")
    _require_str("string_ends_with", suffix, "suffix")
    return text.endswith(suffix)


def builtin_string_contains(text: str, substring: str) -> bool:
    """Check if string contains substring (stdlib wrapper)."""
    _require_str("string_contains", text, "text")
    _require_str("string_contains", substring, "substring")
    return substring in text


def builtin_string_split_once(text: str, delimiter: str) -> ConstructorValue:
    """Split string at first occurrence of delimiter (stdlib wrapper)."""
    _require_str("string_split_once", text, "text")
    _require_str("string_split_once", delimiter, "delimiter")
    if delimiter == "":
        raise RuntimeError("string_split_once: delimiter cannot be empty")
    if delimiter in text:
        parts = text.split(delimiter, 1)
        return ConstructorValue("Some", {"value": (parts[0], parts[1])})
    return ConstructorValue("None", {})


# =============================================================================
# List Builtins (stdlib wrappers)
# =============================================================================


def builtin_list_length(lst: list[Any]) -> int:
    """Return the length of a list (stdlib wrapper)."""
    _require_list("list_length", lst)
    return len(lst)


def builtin_list_map(lst: list[Any], func: Callable[..., Any]) -> list[Any]:
    """Apply a function to each element (stdlib wrapper)."""
    _require_list("list_map", lst)
    return [func(x) for x in lst]


def builtin_list_filter(lst: list[Any], pred: Callable[..., Any]) -> list[Any]:
    """Keep elements matching a predicate (stdlib wrapper)."""
    _require_list("list_filter", lst)
    return [x for x in lst if pred(x)]


# =============================================================================
# Map Builtins (stdlib wrappers)
# =============================================================================


def builtin_map_from_list(
    pairs: list[Any], *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> dict[Any, Any]:
    """Build a map from a list of (key, value) pairs."""
    _require_list("map_from_list", pairs)
    limit = _effective_max_collection_size(max_collection_size)
    result: dict[Any, Any] = {}
    for pair in pairs:
        key, value = _require_pair("map_from_list", pair)
        if key not in result:
            _check_collection_size("Map", len(result) + 1, limit)
        result[key] = value
    return result


def builtin_map_merge(
    m1: dict[Any, Any],
    m2: dict[Any, Any],
    *,
    max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE,
) -> dict[Any, Any]:
    """Merge two maps, with m2 values taking precedence."""
    if not isinstance(m1, dict):
        raise RuntimeError(
            f"map_merge: first argument must be a map, got {type(m1).__name__}"
        )
    if not isinstance(m2, dict):
        raise RuntimeError(
            f"map_merge: second argument must be a map, got {type(m2).__name__}"
        )
    limit = _effective_max_collection_size(max_collection_size)
    merged_size = len(m1) + sum(1 for key in m2 if key not in m1)
    _check_collection_size("Map", merged_size, limit)
    result = dict(m1)
    result.update(m2)
    return result


def builtin_map_entries(
    m: dict[Any, Any], *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> list[tuple[Any, Any]]:
    """Get all key-value pairs from a map as a list of (key, value) tuples."""
    if not isinstance(m, dict):
        raise RuntimeError(f"map_entries: expected map, got {type(m).__name__}")
    limit = _effective_max_collection_size(max_collection_size)
    _check_collection_size("List", len(m), limit)
    return [(k, v) for k, v in m.items()]


def builtin_map_from_entries(
    entries: list[Any], *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> dict[Any, Any]:
    """Build a map from a list of (key, value) pairs."""
    _require_list("map_from_entries", entries)
    limit = _effective_max_collection_size(max_collection_size)
    result: dict[Any, Any] = {}
    for entry in entries:
        key, value = _require_pair("map_from_entries", entry)
        if key not in result:
            _check_collection_size("Map", len(result) + 1, limit)
        result[key] = value
    return result


# =============================================================================
# List Builtins (stdlib wrappers)
# =============================================================================


def builtin_list_zip(xs: list[Any], ys: list[Any]) -> list[tuple[Any, Any]]:
    """Zip two lists together into a list of tuples."""
    return builtin_zip(xs, ys)


def builtin_list_enumerate(xs: list[Any]) -> list[tuple[int, Any]]:
    """Enumerate a list, returning a list of (index, element) tuples."""
    return builtin_enumerate(xs)


def builtin_list_all(xs: list[Any], pred: Callable[..., Any]) -> bool:
    """Check if all elements match a predicate."""
    _require_list("list_all", xs)
    return all(pred(x) for x in xs)


def builtin_list_flatten(
    xss: list[list[Any]], *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> list[Any]:
    """Flatten a list of lists into a single list."""
    _require_list("list_flatten", xss)
    limit = _effective_max_collection_size(max_collection_size)
    result: list[Any] = []
    for xs in xss:
        if not isinstance(xs, list):
            raise RuntimeError("list_flatten: expected list of lists")
        _check_collection_size("List", len(result) + len(xs), limit)
        result.extend(xs)
    return result


def builtin_list_chunk(xs: list[Any], n: int) -> list[list[Any]]:
    """Split a list into chunks of size n."""
    _require_list("list_chunk", xs)
    _require_int("list_chunk", n, "n")
    if n <= 0:
        raise RuntimeError("list_chunk: chunk size must be positive")
    return [xs[i : i + n] for i in range(0, len(xs), n)]


def builtin_list_take(xs: list[Any], n: int) -> list[Any]:
    """Take the first n elements."""
    _require_list("list_take", xs)
    _require_int("list_take", n, "n")
    return xs[: max(0, n)]


def builtin_list_drop(xs: list[Any], n: int) -> list[Any]:
    """Drop the first n elements."""
    _require_list("list_drop", xs)
    _require_int("list_drop", n, "n")
    return xs[max(0, n) :]


def builtin_list_find(xs: list[Any], pred: Callable[..., Any]) -> ConstructorValue:
    """Find the first element matching a predicate, or None."""
    _require_list("list_find", xs)
    from .values import ConstructorValue

    for x in xs:
        if pred(x):
            return ConstructorValue("Some", {"value": x})
    return ConstructorValue("None", {})


def builtin_list_find_index(
    xs: list[Any], pred: Callable[..., Any]
) -> ConstructorValue:
    """Find the index of the first matching element, wrapped in Option."""
    _require_list("list_find_index", xs)
    from .values import ConstructorValue

    for i, x in enumerate(xs):
        if pred(x):
            return ConstructorValue("Some", {"value": i})
    return ConstructorValue("None", {})


def builtin_list_any(xs: list[Any], pred: Callable[..., Any]) -> bool:
    """Check if any element matches a predicate."""
    _require_list("list_any", xs)
    return any(pred(x) for x in xs)


def builtin_list_fold_right(xs: list[Any], init: Any, f: Callable[..., Any]) -> Any:
    """Fold a list from right to left."""
    _require_list("list_fold_right", xs)
    acc = init
    for x in reversed(xs):
        acc = f(x, acc)
    return acc


def builtin_list_intersperse(
    xs: list[Any], sep: Any, *, max_collection_size: int = DEFAULT_MAX_COLLECTION_SIZE
) -> list[Any]:
    """Insert a separator between each element."""
    _require_list("list_intersperse", xs)
    if not xs:
        return []
    limit = _effective_max_collection_size(max_collection_size)
    _check_collection_size("List", len(xs) * 2 - 1, limit)
    result = [xs[0]]
    for x in xs[1:]:
        result.append(sep)
        result.append(x)
    return result


def builtin_list_group_by(
    xs: list[Any], key_fn: Callable[..., Any]
) -> list[tuple[Any, list[Any]]]:
    """Group elements by a key function. Returns list of [key, [elements]] pairs."""
    _require_list("list_group_by", xs)
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


# =============================================================================
# Math Builtins (extended)
# =============================================================================


def builtin_math_min(a: Any, b: Any) -> Any:
    """Return the smaller of two numbers."""
    return min(a, b)


def builtin_math_max(a: Any, b: Any) -> Any:
    """Return the larger of two numbers."""
    return max(a, b)


def builtin_math_log(x: int | float) -> float:
    """Natural logarithm."""
    import math

    if not isinstance(x, (int, float)):
        raise RuntimeError(f"math_log: expected number, got {type(x).__name__}")
    if x <= 0:
        raise RuntimeError("math_log: argument must be positive")
    return math.log(x)


def builtin_math_sin(x: int | float) -> float:
    """Sine function (radians)."""
    import math

    if not isinstance(x, (int, float)):
        raise RuntimeError(f"math_sin: expected number, got {type(x).__name__}")
    return math.sin(x)


def builtin_math_cos(x: int | float) -> float:
    """Cosine function (radians)."""
    import math

    if not isinstance(x, (int, float)):
        raise RuntimeError(f"math_cos: expected number, got {type(x).__name__}")
    return math.cos(x)


def builtin_math_abs(x: int | float) -> int | float:
    """Absolute value (stdlib wrapper)."""
    if not isinstance(x, (int, float)):
        raise RuntimeError(f"math_abs: expected number, got {type(x).__name__}")
    return abs(x)


def builtin_math_clamp(value: Any, lo: Any, hi: Any) -> Any:
    """Clamp to range (stdlib wrapper)."""
    return max(lo, min(hi, value))


def builtin_math_floor(x: int | float) -> int:
    """Floor (stdlib wrapper)."""
    import math

    return _require_safe_js_int(math.floor(x), "math_floor result")


def builtin_math_ceil(x: int | float) -> int:
    """Ceil (stdlib wrapper)."""
    import math

    return _require_safe_js_int(math.ceil(x), "math_ceil result")


def builtin_math_round(x: int | float) -> int:
    """Round (stdlib wrapper)."""
    base = math.floor(x)
    rounded = base + (1 if x - base >= 0.5 else 0)
    return _require_safe_js_int(rounded, "math_round result")


def builtin_math_sqrt(x: int | float) -> float:
    """Square root (stdlib wrapper)."""
    import math

    if not isinstance(x, (int, float)):
        raise RuntimeError(f"math_sqrt: expected number, got {type(x).__name__}")
    if x < 0:
        raise RuntimeError("math_sqrt: argument must be non-negative")
    return math.sqrt(x)


def builtin_math_pi() -> float:
    """Return pi."""
    import math

    return math.pi


def builtin_math_e() -> float:
    """Return e."""
    import math

    return math.e


def builtin_math_random_int(lo: int, hi: int) -> int:
    """Random int in [lo, hi] (stdlib wrapper)."""
    import random

    return random.randint(lo, hi)


def builtin_math_random_float() -> float:
    """Random float in [0, 1) (stdlib wrapper)."""
    import random

    return random.random()


# =============================================================================
# Result stdlib builtins
# =============================================================================


def builtin_result_unwrap_or(result: Any, default: Any) -> Any:
    """Return the Ok value, or default if Err."""
    from .values import ConstructorValue

    if isinstance(result, ConstructorValue):
        if result.constructor == "Ok":
            return result.fields["value"]
        return default
    raise RuntimeError(
        f"result_unwrap_or: expected Result, got {type(result).__name__}"
    )


def builtin_result_is_ok(result: Any) -> bool:
    """True if Result is Ok."""
    from .values import ConstructorValue

    if isinstance(result, ConstructorValue):
        return bool(result.constructor == "Ok")
    raise RuntimeError(f"result_is_ok: expected Result, got {type(result).__name__}")


def builtin_result_is_err(result: Any) -> bool:
    """True if Result is Err."""
    from .values import ConstructorValue

    if isinstance(result, ConstructorValue):
        return bool(result.constructor == "Err")
    raise RuntimeError(f"result_is_err: expected Result, got {type(result).__name__}")


def builtin_result_to_option(result: Any) -> ConstructorValue:
    """Convert Ok(v) to Some(v), Err(_) to None."""
    from .values import ConstructorValue

    if isinstance(result, ConstructorValue):
        if result.constructor == "Ok":
            return ConstructorValue("Some", {"value": result.fields["value"]})
        return ConstructorValue("None", {})
    raise RuntimeError(
        f"result_to_option: expected Result, got {type(result).__name__}"
    )


# =============================================================================
# Option stdlib builtins
# =============================================================================


def builtin_option_unwrap_or(option: Any, default: Any) -> Any:
    """Return the Some value, or default if None."""
    from .values import ConstructorValue

    if isinstance(option, ConstructorValue):
        if option.constructor == "Some":
            return option.fields["value"]
        return default
    raise RuntimeError(
        f"option_unwrap_or: expected Option, got {type(option).__name__}"
    )


def builtin_option_is_some(option: Any) -> bool:
    """True if Option is Some."""
    from .values import ConstructorValue

    if isinstance(option, ConstructorValue):
        return bool(option.constructor == "Some")
    raise RuntimeError(f"option_is_some: expected Option, got {type(option).__name__}")


def builtin_option_is_none(option: Any) -> bool:
    """True if Option is None."""
    from .values import ConstructorValue

    if isinstance(option, ConstructorValue):
        return bool(option.constructor == "None")
    raise RuntimeError(f"option_is_none: expected Option, got {type(option).__name__}")


def builtin_option_flatten(option: Any) -> ConstructorValue:
    """Flatten Option[Option[T]] to Option[T]."""
    from .values import ConstructorValue

    if isinstance(option, ConstructorValue):
        if option.constructor == "Some":
            inner = option.fields["value"]
            if isinstance(inner, ConstructorValue) and inner.constructor in (
                "Some",
                "None",
            ):
                return inner
        return ConstructorValue("None", {})
    raise RuntimeError(f"option_flatten: expected Option, got {type(option).__name__}")


def builtin_option_to_result(option: Any, err: Any) -> ConstructorValue:
    """Convert Some(v) to Ok(v), None to Err(err)."""
    from .values import ConstructorValue

    if isinstance(option, ConstructorValue):
        if option.constructor == "Some":
            return ConstructorValue("Ok", {"value": option.fields["value"]})
        return ConstructorValue("Err", {"error": err})
    raise RuntimeError(
        f"option_to_result: expected Option, got {type(option).__name__}"
    )


# =============================================================================
# Path stdlib builtins
# =============================================================================


def builtin_path_join(base: str, child: str) -> str:
    """Join two path segments."""
    import posixpath

    return posixpath.join(base, child)


def builtin_path_parent(path: str) -> str:
    """Return the parent directory of a path."""
    import posixpath

    return posixpath.dirname(path)


def builtin_path_filename(path: str) -> str:
    """Return the filename portion of a path."""
    import posixpath

    return posixpath.basename(path)


def builtin_path_extension(path: str) -> str:
    """Return the file extension (including dot)."""
    import posixpath

    _, ext = posixpath.splitext(path)
    return ext


def builtin_path_is_absolute(path: str) -> bool:
    """Return whether a path is absolute."""
    import posixpath

    return posixpath.isabs(path)


# =============================================================================
# DateTime stdlib builtins
# =============================================================================


def builtin_datetime_now() -> int:
    """Current Unix timestamp as Int."""
    return int(time.time())


def builtin_datetime_format(timestamp: int | float, fmt: str) -> str:
    """Format Unix timestamp to string."""
    return builtin_clock_format(timestamp, fmt)


def builtin_datetime_parse(text: str, fmt: str) -> ConstructorValue:
    """Parse date string to Unix timestamp (Option[Int])."""
    result = builtin_clock_parse(text, fmt)
    if isinstance(result, ConstructorValue) and result.constructor == "Some":
        return ConstructorValue("Some", {"value": int(result.fields["value"])})
    return result


def builtin_datetime_elapsed(start: int | float, end_time: int | float) -> int:
    """Difference in seconds between two timestamps."""
    return int(builtin_clock_elapsed(start, end_time))


def builtin_http_respond(
    status: int, headers: list[Any], body: str
) -> ConstructorValue:
    """Construct an HttpResponse value."""
    return ConstructorValue(
        "HttpResponse",
        {"status": status, "body": body, "headers": headers},
    )


def builtin_clamp(
    value: int | float, min_val: int | float, max_val: int | float
) -> int | float:
    """Clamp a numeric value to a range."""
    if not isinstance(value, (int, float)):
        raise RuntimeError("clamp: value must be a number")
    if not isinstance(min_val, (int, float)):
        raise RuntimeError("clamp: min must be a number")
    if not isinstance(max_val, (int, float)):
        raise RuntimeError("clamp: max must be a number")
    return max(min_val, min(max_val, value))
