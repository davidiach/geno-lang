"""How a call ended, for ``example ... panics`` (proposal 0017).

One classifier decides what an exception means, and every evaluator of
examples uses it: ``Interpreter._verify_examples`` (``geno run``, the
embedding API and the hosted server), ``geno test`` and the harness runners.

A *panic* is a call of ``panic(...)`` or any other runtime error, including a
failed ``requires`` or ``ensures``. A *limit* is the host's resource limit
running out (steps, time, recursion depth, memory, collection size, integer
size, output); it never satisfies a panics example, so an example cannot turn
an input that exhausts the host into a passing test. A *refusal* is the host
declining an operation (a capability denial, a missing host callback, a
sandbox security violation); whether it occurs depends on what the host
grants, not on the input, so by default it does not satisfy one either.

Limits are identified by exception type, never by the Geno-facing message, so
``panic("List size exceeds limit")`` stays a panic. The one exception is
compiled Python, whose runtime raises plain ``RuntimeError`` for its limits:
``classify_compiled_failure`` recognizes those by the fixed messages of
``geno/_runtime_support.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Literal

from .diagnostics import ErrorCode
from .sandbox import (
    RecursionLimitError,
    ResourceLimitExceeded,
    SandboxError,
    SecurityViolation,
    StepLimitExceeded,
)
from .sandbox import TimeoutError as SandboxTimeout
from .values import GenoRuntimeError, RuntimeLimitError

FailureKind = Literal["panic", "limit", "refused"]

# What an evaluator catches around the call of a panics example: every
# Geno-facing way a call can end other than returning. Control-flow signals
# (ReturnException, PropagateException) and KeyboardInterrupt are
# BaseExceptions and pass through.
CALL_FAILURES: tuple[type[BaseException], ...] = (
    GenoRuntimeError,
    SandboxError,
    RecursionError,
    MemoryError,
)

# Host refusals: the codes a refused operation reports, plus SecurityViolation.
_REFUSAL_CODES = frozenset(
    {
        ErrorCode.RUNTIME_CAPABILITY_DENIED,
        ErrorCode.RUNTIME_HOST_CALLBACK_MISSING,
    }
)

# Messages of the limits compiled Python enforces with a plain RuntimeError
# (geno/_runtime_support.py and the sandbox's output guard), with the limit
# each one names. Matched from the start of the message, after an optional
# "<builtin>: " prefix.
_COMPILED_LIMIT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\w+ size exceeds limit \("), "collection size limit"),
    (re.compile(r"result would exceed collection size limit"), "collection size limit"),
    (re.compile(r"Integer exceeds maximum size \("), "integer size limit"),
    (
        re.compile(r"Exponentiation result too large \(estimated "),
        "integer size limit",
    ),
    (re.compile(r"Exponentiation result too large$"), "numeric range limit"),
    (re.compile(r"(Left|Right) shift amount too large \("), "integer size limit"),
    (re.compile(r"Output limit exceeded \("), "output limit"),
)
_COMPILED_PREFIX = re.compile(r"^(?:\w+: )?")


@dataclass(frozen=True)
class CallFailure:
    """A call that ended without returning."""

    kind: FailureKind
    # The Geno-facing message, without a location or "Runtime Error:" label.
    message: str
    # For a limit: what ran out ("step limit", "timeout", ...).
    limit: str | None = None


def _refusal_satisfies_panics() -> bool:
    """Whether a host refusal (E412, E413, E500) satisfies a panics example.

    The one place this is decided (proposal 0017, Unresolved Questions). A
    refusal depends on the host's grants rather than on the input: in
    ``geno test`` every ``env`` read is denied, so a function that reads the
    environment would otherwise pass its panics example vacuously.
    """
    return False


def _message_of(exc: BaseException) -> str:
    message = getattr(exc, "message", None)
    if isinstance(message, str):
        return message
    return str(exc) or type(exc).__name__


def _limit(exc: BaseException, limit: str) -> CallFailure:
    return CallFailure("limit", _message_of(exc), limit)


def _refused_or_panic(exc: BaseException) -> CallFailure:
    kind: FailureKind = "panic" if _refusal_satisfies_panics() else "refused"
    return CallFailure(kind, _message_of(exc))


def classify_call_failure(exc: BaseException) -> CallFailure | None:
    """Classify an exception an interpreted call raised.

    Returns ``None`` for an exception that is not Geno-facing (an internal
    ``TypeError``, say); the caller treats it as it treated it before.
    """
    if isinstance(exc, StepLimitExceeded):
        return _limit(exc, "step limit")
    if isinstance(exc, SandboxTimeout):
        return _limit(exc, "timeout")
    if isinstance(exc, RecursionLimitError):
        return _limit(exc, "recursion depth limit")
    if isinstance(exc, ResourceLimitExceeded):
        return _limit(exc, "resource limit")
    if isinstance(exc, SecurityViolation):
        return _refused_or_panic(exc)
    if isinstance(exc, SandboxError):
        return _refused_or_panic(exc)
    if isinstance(exc, RecursionError):
        return _limit(exc, "host recursion limit")
    if isinstance(exc, MemoryError):
        return _limit(exc, "memory limit")
    if isinstance(exc, RuntimeLimitError):
        return _limit(exc, f"{exc.limit} limit")
    if isinstance(exc, GenoRuntimeError):
        if exc.error_code in _REFUSAL_CODES:
            return _refused_or_panic(exc)
        return CallFailure("panic", exc.message)
    return None


def compiled_limit(message: str) -> str | None:
    """The limit a compiled-Python runtime message reports, if it is one."""
    text = _COMPILED_PREFIX.sub("", message, count=1)
    for pattern, limit in _COMPILED_LIMIT_PATTERNS:
        if pattern.match(text) or pattern.match(message):
            return limit
    return None


def classify_compiled_failure(exc: BaseException) -> CallFailure | None:
    """Classify an exception a compiled-Python call raised.

    The compiled runtime raises plain ``RuntimeError``, ``ArithmeticError``
    and ``LookupError`` for panics and for most of its limits alike, so its
    limits are recognized by their fixed messages; anything the sandbox raises
    is classified as for the interpreter.
    """
    interpreted = classify_call_failure(exc)
    if interpreted is not None:
        return interpreted
    if type(exc).__name__ == "_GenoOutputLimitError":
        return _limit(exc, "output limit")
    if isinstance(exc, (RuntimeError, ArithmeticError, LookupError, ValueError)):
        message = str(exc)
        limit = compiled_limit(message)
        if limit is not None:
            return CallFailure("limit", message, limit)
        return CallFailure("panic", message)
    return None


def panic_message_matches(message: str, expected: str) -> bool:
    """Whether a panic's *message* contains *expected*, ignoring case."""
    return expected.casefold() in message.casefold()


def quote_panic_message(text: str) -> str:
    """Render a panics example's message as a double-quoted string."""
    return json.dumps(text, ensure_ascii=False)


def evaluate_panics_call(
    call: Callable[[], object],
    expected: str | None,
    *,
    render: Callable[[object], str],
    reraise_unsatisfying: bool = False,
) -> str | None:
    """Make an interpreted panics example's call; ``None`` when it passes.

    The example's inputs are evaluated before *call*: an input that panics is
    a broken example, not a satisfied one. With *reraise_unsatisfying*, a
    limit or a refusal propagates as the original exception instead of
    becoming a reason, so a caller that reports errors by code keeps it.
    """
    try:
        returned = call()
    except CALL_FAILURES as exc:
        failure = classify_call_failure(exc)
        if failure is None or (reraise_unsatisfying and failure.kind != "panic"):
            raise
        return check_panics_example(failure, expected)
    return check_panics_example(None, expected, returned=render(returned))


def check_panics_example(
    failure: CallFailure | None,
    expected: str | None,
    *,
    returned: str | None = None,
) -> str | None:
    """Decide a panics example: ``None`` when it passes, else the reason.

    *failure* is how the call ended (``None`` when it returned, in which case
    *returned* is the rendered value). *expected* is the example's message
    substring, if it has one.
    """
    if failure is None:
        shown = returned if returned is not None else "a value"
        return f"expected a panic, but it returned {shown}"
    if failure.kind == "limit":
        return (
            f"expected a panic, but the call hit the {failure.limit} "
            "(a resource limit is not a panic)"
        )
    if failure.kind == "refused":
        return f"expected a panic, but the call was refused: {failure.message}"
    if expected is not None and not panic_message_matches(failure.message, expected):
        return (
            f"expected a panic containing {quote_panic_message(expected)}, "
            f"but it panicked with: {failure.message}"
        )
    return None
