"""
Geno Diagnostics
=================

Structured error codes and diagnostic types for machine-readable error reporting.
"""

from dataclasses import dataclass
from enum import Enum, auto

from .tokens import SourceLocation


class Severity(Enum):
    """Severity level for diagnostics."""

    ERROR = auto()
    WARNING = auto()
    INFO = auto()


class ErrorCode(Enum):
    """
    Canonical error codes for all Geno error types.

    Naming convention: <PHASE>_<CATEGORY>.

    Codes are stable once published. The first code in each phase (E100,
    E200, E300) and ``RUNTIME_UNKNOWN`` (E499) are intentional fallbacks for
    errors that do not yet have a more specific category. The other codes are
    reserved for the named condition and should be attached at the point where
    that condition is recognized. This lets tools depend on granular codes
    without requiring every possible error to have its own category.
    """

    # Lexer errors (1xx)
    LEX_UNEXPECTED_CHAR = "E100"
    LEX_UNTERMINATED_STRING = "E101"
    LEX_UNTERMINATED_COMMENT = "E102"
    LEX_INVALID_ESCAPE = "E103"

    # Parse errors (2xx)
    PARSE_UNEXPECTED_TOKEN = "E200"
    PARSE_EXPECTED_TOKEN = "E201"
    PARSE_INVALID_SYNTAX = "E202"
    PARSE_RECOVERY = "E203"
    PROJECT_RESOLUTION_ERROR = "E204"

    # Type errors (3xx)
    TYPE_MISMATCH = "E300"
    TYPE_UNDEFINED_VAR = "E301"
    TYPE_UNDEFINED_FUNC = "E302"
    TYPE_UNDEFINED_TYPE = "E303"
    TYPE_WRONG_ARITY = "E304"
    TYPE_NOT_CALLABLE = "E305"
    TYPE_IMMUTABLE_ASSIGN = "E306"
    TYPE_UNKNOWN_FIELD = "E307"
    TYPE_PATTERN_MISMATCH = "E308"
    TYPE_DUPLICATE_DEFINITION = "E309"

    # Effect errors (31x)
    EFFECT_VIOLATION = "E310"  # Function performs undeclared effect
    EFFECT_UNKNOWN = "E311"  # Unrecognized effect name in annotation
    EFFECT_MISMATCH = "E312"  # Callback has incompatible effects

    # Warnings (W3xx): reported with Severity.WARNING once a program passes
    # the type checker; the program is still accepted.
    WARN_REDECLARED_VAR = "W300"  # let/var redeclares a mutable var in scope

    # Runtime errors (4xx)
    RUNTIME_DIVISION_BY_ZERO = "E400"
    RUNTIME_INDEX_OUT_OF_BOUNDS = "E401"
    RUNTIME_KEY_NOT_FOUND = "E402"
    RUNTIME_UNDEFINED_VAR = "E403"
    RUNTIME_WRONG_ARITY = "E404"
    RUNTIME_NOT_CALLABLE = "E405"
    RUNTIME_NO_MATCH = "E406"
    RUNTIME_PRECONDITION_FAILED = "E407"
    RUNTIME_POSTCONDITION_FAILED = "E408"
    RUNTIME_EXAMPLE_FAILED = "E409"
    RUNTIME_TYPE_ERROR = "E410"
    RUNTIME_UNFILLED_HOLE = "E411"
    RUNTIME_CAPABILITY_DENIED = "E412"
    RUNTIME_HOST_CALLBACK_MISSING = "E413"
    RUNTIME_UNKNOWN = "E499"

    # Sandbox / resource errors (5xx)
    SANDBOX_SECURITY_VIOLATION = "E500"
    SANDBOX_TIMEOUT = "E501"
    SANDBOX_RECURSION_LIMIT = "E502"
    SANDBOX_STEP_LIMIT = "E503"
    SANDBOX_RESOURCE_LIMIT = "E504"
    SANDBOX_OUTPUT_LIMIT = "E505"


def runtime_error_code_for_message(message: str) -> ErrorCode | None:
    """Classify standardized interpreter errors that predate structured codes.

    Runtime error sites should pass a code directly when practical. This
    compatibility classifier keeps common errors stable while those older
    call sites are migrated; an unrecognized message deliberately returns
    ``None`` so the API can use ``RUNTIME_UNKNOWN``.
    """

    if message.startswith("Division by zero"):
        return ErrorCode.RUNTIME_DIVISION_BY_ZERO
    if message.startswith("Index ") and " out of bounds " in message:
        return ErrorCode.RUNTIME_INDEX_OUT_OF_BOUNDS
    if message.startswith("Key not found:"):
        return ErrorCode.RUNTIME_KEY_NOT_FOUND
    return None


def error_parts(error: object) -> list[tuple[SourceLocation | None, str]]:
    """Split a toolchain error into ``(location, message)`` parts.

    ``str()`` of some errors already carries a phase label (a type error renders
    as ``"<loc>: Type Error: <msg>"``), so callers that print their own label
    must build the line from these structured parts instead of ``str(error)``.
    Aggregate errors (``TypeErrors``) yield one part per contained error; an
    error without a ``message`` falls back to its ``str()``.
    """
    errors = getattr(error, "errors", None) or [error]
    parts: list[tuple[SourceLocation | None, str]] = []
    for err in errors:
        message = getattr(err, "message", None)
        if isinstance(message, str):
            parts.append((getattr(err, "location", None), message))
        else:
            parts.append((None, str(err)))
    return parts


def format_labeled_error(label: str, error: object) -> list[str]:
    """Render ``"<label>: <loc>: <message>"`` lines, one per contained error."""
    lines = []
    for location, message in error_parts(error):
        where = f"{location}: " if location is not None else ""
        lines.append(f"{label}: {where}{message}")
    return lines


@dataclass(frozen=True)
class Diagnostic:
    """
    A structured diagnostic message.

    Designed for machine consumption: error codes are stable identifiers
    that agents/tools can match on without parsing human-readable text.
    """

    code: ErrorCode
    message: str
    severity: Severity
    location: SourceLocation | None = None

    def __str__(self) -> str:
        parts = [f"[{self.code.value}]"]
        if self.location:
            parts.append(str(self.location))
        parts.append(self.message)
        return " ".join(parts)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        d: dict = {
            "code": self.code.value,
            "message": self.message,
            "severity": self.severity.name.lower(),
        }
        if self.location:
            d["location"] = {
                "line": self.location.line,
                "column": self.location.column,
                "filename": self.location.filename,
            }
        return d
