"""Shared defaults for cooperative Geno execution limits."""

import contextvars
import sys
import threading
from typing import Callable, TypeVar, cast

# Enough for ordinary recursive programs: walking a 10,000-element list
# recursively, or fib(30)-sized call trees (proposal 0011). The hosted server
# keeps its own, smaller limits.
DEFAULT_MAX_RECURSION_DEPTH = 10_000
DEFAULT_INTERPRETER_MAX_STEPS = 20_000_000

# XNU accounts large reserved VM-map regions and Python compilation needs more
# growth headroom than other supported hosts. This remains a finite kernel-
# enforced budget; callers can explicitly choose a smaller or larger value.
DEFAULT_PROCESS_MAX_MEMORY_BYTES = (
    512 * 1024 * 1024 if sys.platform == "darwin" else 256 * 1024 * 1024
)

# geno test defaults; defined here (a leaf module) so the CLI parser can
# reference them without importing the interpreter-backed test runner.
DEFAULT_TEST_TIMEOUT = 5.0
DEFAULT_TEST_MAX_STEPS = DEFAULT_INTERPRETER_MAX_STEPS

# Python recursion a caller's own thread can hold without exhausting its C
# stack. CPython 3.10 spends up to ~1 KiB of C stack per Python frame, and
# 3.11 still does for recursion through C slots such as `__eq__`; an 8 MiB
# stack holds this many with room to spare. Deeper runs get their own thread.
# From 3.12, Python-to-Python calls use no C stack and C recursion has its
# own limit, so any depth runs on the caller's thread.
INLINE_RECURSION_LIMIT = 6_100
C_STACK_FREE_RECURSION = sys.version_info >= (3, 12)
STACK_BYTES_PER_FRAME = 2048
MIN_DEEP_STACK_BYTES = 32 * 1024 * 1024
MAX_DEEP_STACK_BYTES = 1024 * 1024 * 1024

T = TypeVar("T")

_stack_size_lock = threading.Lock()


def call_with_recursion_room(
    frames: int,
    body: Callable[[int], T],
    *,
    interrupt: Callable[[], None] | None = None,
) -> T:
    """Run ``body(limit)`` where Python may recurse ``limit`` frames deep.

    ``limit`` is ``frames`` when the run fits the current thread or gets a
    thread whose stack holds that many frames; it falls back to
    ``INLINE_RECURSION_LIMIT`` when no such thread can be started (a tight
    address-space limit, say), so a deep run then fails with a recursion
    error instead of overflowing the C stack. ``interrupt`` is called when
    the waiting caller is interrupted (Ctrl-C) so the run can stop.
    """
    if frames <= INLINE_RECURSION_LIMIT or C_STACK_FREE_RECURSION:
        return body(frames)

    stack_bytes = min(
        max(frames * STACK_BYTES_PER_FRAME, MIN_DEEP_STACK_BYTES),
        MAX_DEEP_STACK_BYTES,
    )
    limit = min(frames, stack_bytes // STACK_BYTES_PER_FRAME)
    context = contextvars.copy_context()
    outcome: list[tuple[bool, object]] = []

    def target() -> None:
        try:
            outcome.append((True, context.run(body, limit)))
        except BaseException as exc:  # re-raised on the calling thread
            outcome.append((False, exc))

    thread: threading.Thread | None = threading.Thread(
        target=target, name="geno-run", daemon=True
    )
    with _stack_size_lock:
        previous = threading.stack_size()
        try:
            threading.stack_size(stack_bytes)
            assert thread is not None
            thread.start()
        except (RuntimeError, ValueError, MemoryError):
            thread = None
        finally:
            threading.stack_size(previous)
    if thread is None:
        return body(INLINE_RECURSION_LIMIT)

    try:
        thread.join()
    except BaseException:
        if interrupt is not None:
            interrupt()
        raise
    succeeded, value = outcome[0]
    if succeeded:
        return cast(T, value)
    assert isinstance(value, BaseException)
    raise value
