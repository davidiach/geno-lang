"""Exact JSON integers without changing Python's global decimal safety limit."""

from __future__ import annotations

import json
import math
from typing import Any, Iterator, cast


def int_to_decimal(value: int) -> str:
    """Render an already resource-checked integer in small decimal chunks.

    Callers own size limits: the interpreter checks integer bits, and the
    hosted encoder checks its response budget before invoking this helper.
    No conversion here exceeds Python's minimum configurable digit limit.
    """
    if value.bit_length() < 2_000:
        return int.__repr__(value)
    negative = value < 0
    magnitude = -value if negative else value
    chunks: list[int] = []
    while magnitude:
        magnitude, remainder = divmod(magnitude, 1_000_000_000)
        chunks.append(remainder)
    return (
        ("-" if negative else "")
        + str(chunks[-1])
        + "".join(f"{chunk:09d}" for chunk in reversed(chunks[:-1]))
    )


def safe_repr(value: Any) -> str:
    """Keep Python's existing key/sort representation for large integers too."""
    return _safe_repr(value, set())


def _safe_repr(value: Any, active: set[int]) -> str:
    if id(value) in active:
        if isinstance(value, list):
            return "[...]"
        return "(...)" if isinstance(value, tuple) else "{...}"
    try:
        return repr(value)
    except ValueError:
        # Python's decimal ceiling can be reached at any depth of a map key
        # or the JSON-normalized objects used to sort a Geno Set.
        if isinstance(value, int):
            return int_to_decimal(value)
        if not isinstance(value, (list, tuple, dict)):
            raise
    active.add(id(value))
    try:
        if isinstance(value, dict):
            return (
                "{"
                + ", ".join(
                    f"{_safe_repr(k, active)}: {_safe_repr(v, active)}"
                    for k, v in value.items()
                )
                + "}"
            )
        contents = ", ".join(_safe_repr(item, active) for item in value)
        if isinstance(value, tuple):
            return "(" + contents + ("," if len(value) == 1 else "") + ")"
        return "[" + contents + "]"
    finally:
        active.remove(id(value))


class GenoJSONEncoder(json.JSONEncoder):
    """Use the standard JSON traversal with a local integer renderer.

    The server previously owned this implementation. Sharing it keeps CLI and
    hosted numeric tokens identical without loosening process-global limits.
    """

    _intstr = staticmethod(int_to_decimal)

    def iterencode(self, value: Any, _one_shot: bool = False) -> Iterator[str]:
        markers: dict[int, Any] | None = {} if self.check_circular else None
        string_encoder = (
            json.encoder.encode_basestring_ascii
            if self.ensure_ascii
            else json.encoder.encode_basestring
        )

        def float_text(number: float) -> str:
            if math.isfinite(number):
                return float.__repr__(number)
            if not self.allow_nan:
                raise ValueError(
                    "Out of range float values are not JSON compliant: " + repr(number)
                )
            if math.isnan(number):
                return "NaN"
            return "Infinity" if number > 0 else "-Infinity"

        # Python 3.13 moved this normalization out of _make_iterencode.
        # Passing its normalized form also preserves earlier Python behavior.
        indent = self.indent
        if indent is not None and not isinstance(indent, str):
            indent = " " * indent
        make_iterencode = json.encoder.__dict__["_make_iterencode"]
        iterator = make_iterencode(
            markers,
            self.default,
            string_encoder,
            indent,
            float_text,
            self.key_separator,
            self.item_separator,
            self.sort_keys,
            self.skipkeys,
            _one_shot,
            _intstr=self._intstr,
        )
        return cast(Iterator[str], iterator(value, 0))
