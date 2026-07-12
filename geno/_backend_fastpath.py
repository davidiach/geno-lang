"""
Shared backend helpers for compiler builtin fast paths.
"""

from .ast_nodes import (
    BooleanLiteral,
    Expression,
    FloatLiteral,
    Identifier,
    IntegerLiteral,
    StringLiteral,
)
from .types import ArrayType, FloatType, IntType, ListType, StringType

LENGTH_FAST_PATH_BUILTIN = "length"
STRING_CHAR_AT_FAST_PATH_BUILTIN = "string_char_at"
SUBSTRING_FAST_PATH_BUILTINS = frozenset({"substring", "string_substring"})
STRING_AFFIX_FAST_PATH_BUILTINS = frozenset({"starts_with", "ends_with"})
APPEND_FAST_PATH_BUILTIN = "append"


def is_numeric_type(expr: Expression) -> bool:
    """Check if a typechecked expression has Int or Float type."""
    resolved_type = getattr(expr, "_resolved_type", None)
    return isinstance(resolved_type, (IntType, FloatType))


def is_int_type(expr: Expression) -> bool:
    """Check if a typechecked expression has Int type."""
    resolved_type = getattr(expr, "_resolved_type", None)
    return isinstance(resolved_type, IntType)


def is_string_type(expr: Expression) -> bool:
    """Check if a typechecked expression has String type."""
    resolved_type = getattr(expr, "_resolved_type", None)
    return isinstance(resolved_type, StringType)


def is_list_type(expr: Expression) -> bool:
    """Check if a typechecked expression has List type."""
    resolved_type = getattr(expr, "_resolved_type", None)
    return isinstance(resolved_type, ListType)


def has_len_fast_path(expr: Expression) -> bool:
    """Check if a typechecked expression can use raw length access."""
    resolved_type = getattr(expr, "_resolved_type", None)
    return isinstance(resolved_type, (ArrayType, ListType, StringType))


def is_simple_fast_path_expr(expr: Expression) -> bool:
    """Check if reusing a compiled expression is side-effect free."""
    return isinstance(
        expr,
        (BooleanLiteral, FloatLiteral, Identifier, IntegerLiteral, StringLiteral),
    )
