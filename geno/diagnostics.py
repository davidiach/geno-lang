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
    PARSE_REMOVED_SYNTAX = "E205"  # Form removed in this series; names its replacement

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

    # Generic errors (32x)
    TYPE_GENERIC_PARAMS = "E320"  # Invalid type parameter list on a function
    TYPE_GENERIC_INFERENCE = "E321"  # Generic function value's types not inferred
    TYPE_UNKNOWN_BOUND = "E322"  # Type parameter bound names no bound or trait
    TYPE_UNSATISFIED_BOUND = "E323"  # Type argument misses a parameter's bound

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
    RUNTIME_PANIC = "E414"  # The program called panic(...)
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
class TextEdit:
    """Replace the text from ``line``:``column`` up to ``end_line``:``end_column``.

    Positions are 1-based and the end is exclusive, so an insertion has the
    same start and end.
    """

    line: int
    column: int
    end_line: int
    end_column: int
    text: str

    def to_dict(self) -> dict:
        return {
            "start": {"line": self.line, "column": self.column},
            "end": {"line": self.end_line, "column": self.end_column},
            "text": self.text,
        }


@dataclass(frozen=True)
class Fix:
    """A change that resolves a diagnostic, applied as all of its edits."""

    title: str
    edits: tuple[TextEdit, ...]

    def to_dict(self) -> dict:
        return {"title": self.title, "edits": [edit.to_dict() for edit in self.edits]}


def replace_fix(location: SourceLocation, old: str, new: str, title: str = "") -> Fix:
    """A fix that replaces the ``old`` text written at ``location`` with ``new``."""
    return Fix(
        title or f"Change '{old}' to '{new}'",
        (
            TextEdit(
                location.line,
                location.column,
                location.line,
                location.column + len(old),
                new,
            ),
        ),
    )


def insert_fix(title: str, insertions: list[tuple[SourceLocation, str]]) -> Fix:
    """A fix that inserts each text before its location."""
    return Fix(
        title,
        tuple(
            TextEdit(loc.line, loc.column, loc.line, loc.column, text)
            for loc, text in insertions
        ),
    )


def apply_fix(source: str, fix: Fix) -> str:
    """Return ``source`` with every edit of ``fix`` applied."""
    return apply_fixes(source, [fix])[0]


def apply_fixes(source: str, fixes: list[Fix]) -> tuple[str, int]:
    """Apply each fix whose edits do not overlap an earlier one's.

    Returns the new text and how many fixes were applied.
    """
    lines = source.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)

    def offset(line: int, column: int) -> int:
        return offsets[min(line, len(lines)) - 1] + column - 1

    taken: list[tuple[int, int]] = []
    edits: list[tuple[int, int, str]] = []
    applied = 0
    for fix in fixes:
        spans = [
            (offset(e.line, e.column), offset(e.end_line, e.end_column), e.text)
            for e in fix.edits
        ]
        if any(
            (start < other_end and other_start < end)
            or (start == end == other_start == other_end)
            for start, end, _text in spans
            for other_start, other_end in taken
        ):
            continue
        taken.extend((start, end) for start, end, _text in spans)
        edits.extend(spans)
        applied += 1
    result = source
    for start, end, text in sorted(edits, reverse=True):
        result = result[:start] + text + result[end:]
    return result, applied


@dataclass(frozen=True)
class Diagnostic:
    """
    A structured diagnostic message.

    Designed for machine consumption: error codes are stable identifiers
    that agents/tools can match on without parsing human-readable text, and
    ``fixes`` are edits a tool can apply when the correction is unambiguous.
    """

    code: ErrorCode
    message: str
    severity: Severity
    location: SourceLocation | None = None
    fixes: tuple[Fix, ...] = ()

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
        if self.fixes:
            d["fixes"] = [fix.to_dict() for fix in self.fixes]
        return d
