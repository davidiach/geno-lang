"""F-string format specs: the ``.2f`` of ``f"{price:.2f}"``.

A spec is a portable subset of Python's format mini-language::

    [[fill]align][sign][0][width][,][.precision][type]

``align`` is ``<``, ``>`` or ``^``; ``sign`` is ``+``, ``-`` or a space; and
``type`` is one of ``d x X o b`` (an Int), ``f e %`` (an Int or a Float) or
``s`` (any value, as ``to_string`` writes it). Every backend formats a value
the way Python's ``format`` does for the same spec, so the output does not
depend on the backend.

This module only reads and checks a spec. Formatting lives in each runtime:
``_geno_format_spec`` in the Python prelude, which the interpreter shares, and
``_genoFormatSpec`` in the JavaScript prelude.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

INT_TYPES = frozenset("dxXob")
FLOAT_TYPES = frozenset("fe%")
MAX_WIDTH = 1000
MAX_PRECISION = 100

_SPEC_RE = re.compile(
    r"(?:(?P<fill>.)?(?P<align>[<>^]))?"
    r"(?P<sign>[-+ ])?"
    r"(?P<zero>0)?"
    r"(?P<width>[1-9][0-9]*)?"
    r"(?P<comma>,)?"
    r"(?:\.(?P<precision>[0-9]+))?"
    r"(?P<type>[a-zA-Z%])?",
    re.DOTALL,
)


class FormatSpecError(ValueError):
    """A format spec that is malformed or does not fit the value's type."""


@dataclass(frozen=True)
class FormatSpec:
    """One parsed spec. ``type`` is ``""`` when the spec names none."""

    fill: str
    align: str
    sign: str
    zero: bool
    width: int
    comma: bool
    precision: int | None
    type: str


def parse_format_spec(text: str) -> FormatSpec:
    """Parse ``text``, the part of an interpolation after its ``:``.

    Raises FormatSpecError for anything outside the subset, with a message
    that says what to write instead.
    """
    match = _SPEC_RE.fullmatch(text)
    if match is None:
        raise FormatSpecError(
            f"Invalid format spec '{text}': a spec is "
            "[[fill]align][sign][0][width][,][.precision][type], as in "
            "'.2f', '>8', '05d' or ',d'"
        )
    type_ = match["type"] or ""
    if type_ and type_ not in INT_TYPES | FLOAT_TYPES | {"s"}:
        hint = " Use 'f' or 'e' for a Float." if type_ in "gGnEF" else ""
        raise FormatSpecError(
            f"Unknown format type '{type_}' in spec '{text}': the types are "
            f"d, x, X, o, b, f, e, % and s.{hint}"
        )
    width = int(match["width"] or 0)
    precision = int(match["precision"]) if match["precision"] is not None else None
    if width > MAX_WIDTH:
        raise FormatSpecError(
            f"Format width {width} in spec '{text}' is over the limit of {MAX_WIDTH}"
        )
    if precision is not None and precision > MAX_PRECISION:
        raise FormatSpecError(
            f"Format precision {precision} in spec '{text}' is over the limit "
            f"of {MAX_PRECISION}"
        )
    spec = FormatSpec(
        fill=match["fill"] or "",
        align=match["align"] or "",
        sign=match["sign"] or "",
        zero=match["zero"] is not None,
        width=width,
        comma=match["comma"] is not None,
        precision=precision,
        type=type_,
    )
    if spec.zero and spec.align:
        raise FormatSpecError(
            f"Spec '{text}' has both an alignment and a 0 flag: write the fill "
            f"with the alignment instead, as in '0{spec.align}{width or 8}'"
        )
    if spec.zero and spec.comma:
        raise FormatSpecError(
            f"Spec '{text}' combines the 0 flag with ',': pad a grouped number "
            "with a fill and an alignment instead"
        )
    return spec


def value_kind(spec: FormatSpec, static_kind: str, text: str) -> str:
    """How a value of ``static_kind`` is formatted under ``spec``.

    ``static_kind`` is ``"int"``, ``"float"`` or ``"text"`` (anything else).
    Returns the same three names for what the runtime does: format an
    integer, format a float, or pad what ``to_string`` writes. Raises
    FormatSpecError when the spec does not fit the type.
    """
    type_ = spec.type
    numeric_only = spec.sign or spec.zero or spec.comma
    if type_ in INT_TYPES:
        if static_kind != "int":
            raise FormatSpecError(
                f"Format type '{type_}' in spec '{text}' needs an Int"
                + ("; use 'f' or 'e' for a Float" if static_kind == "float" else "")
            )
        if spec.precision is not None:
            raise FormatSpecError(
                f"Spec '{text}' gives a precision to an integer type; "
                "precision goes with f, e, % or s"
            )
        if spec.comma and type_ != "d":
            raise FormatSpecError(
                f"Spec '{text}' groups digits with ',' under type '{type_}'; "
                "',' goes with d or f"
            )
        return "int"
    if type_ in FLOAT_TYPES:
        if static_kind not in ("int", "float"):
            raise FormatSpecError(
                f"Format type '{type_}' in spec '{text}' needs an Int or a Float"
            )
        if spec.comma and type_ != "f":
            raise FormatSpecError(
                f"Spec '{text}' groups digits with ',' under type '{type_}'; "
                "',' goes with d or f"
            )
        return "float"
    if type_ == "s":
        if numeric_only:
            raise FormatSpecError(
                f"Spec '{text}' uses a sign, 0 or ',' with type 's'; those go "
                "with the number types"
            )
        return "text"
    # No type: an Int formats like 'd'; anything else is padded text.
    if static_kind == "int":
        if spec.precision is not None:
            raise FormatSpecError(
                f"Spec '{text}' gives a precision to an Int; write "
                f"'.{spec.precision}f' to show decimals"
            )
        return "int"
    if static_kind == "float" and (numeric_only or spec.precision is not None):
        raise FormatSpecError(
            f"Spec '{text}' needs a type for a Float: add f, e or %, as in '{text}f'"
        )
    if numeric_only:
        raise FormatSpecError(
            f"Spec '{text}' uses a sign, 0 or ',' on a value that is not a "
            "number; a fill, an alignment, a width and a precision apply"
        )
    # A precision cuts the text to that many characters.
    return "text"


def runtime_kind(text: str, value: object) -> str:
    """The kind for a value whose program skipped type checking."""
    type_ = text[-1:]
    if type_ in INT_TYPES:
        return "int"
    if type_ in FLOAT_TYPES:
        return "float"
    if type_ != "s" and type(value) is int:
        return "int"
    return "text"


def format_value(value: object, text: str, kind: str) -> str:
    """Format ``value`` under the checked spec ``text``.

    Python's own ``format`` is the definition of the subset, so the Python
    backends call it. ``value`` is a ``str`` already for the ``"text"`` kind.
    """
    if kind == "int":
        if type(value) is not int:
            raise ValueError(f"Format spec '{text}' needs an Int")
        return format(value, text)
    if kind == "float":
        if type(value) not in (int, float):
            raise ValueError(f"Format spec '{text}' needs an Int or a Float")
        return format(float(value), text)  # type: ignore[arg-type]
    if not isinstance(value, str):
        raise ValueError(f"Format spec '{text}' needs a String here")
    return format(value, text)
