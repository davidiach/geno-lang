"""Runtime failure reports: shared limits and contract-failure wording.

Proposal 0018 makes every runtime failure as precise as a compile error.
This module holds the parts every backend must agree on byte for byte. The
compiled runtimes cannot import it (generated code stays standalone), so the
Python prelude (``geno/_runtime_support.py``) and the JavaScript prelude
(``geno/_js_runtime_support.js``) carry copies of the constants and of
``contract_message``; ``geno/tests/test_runtime_diagnostics.py`` pins that
the copies agree.

The module is deliberately light: it imports nothing from the toolchain, so
any lane (including the default ``geno run`` parent) can use it.
"""

from __future__ import annotations

from collections.abc import Sequence

# A runtime stack longer than STACK_HEAD + STACK_TAIL frames keeps its first
# STACK_HEAD and last STACK_TAIL frames and says how many it left out.
STACK_HEAD = 10
STACK_TAIL = 10

# The longest rendering of one argument or result value in a contract
# failure, in code points; a longer one keeps its first
# ARGUMENT_DISPLAY_LIMIT - 3 code points and gains "...".
ARGUMENT_DISPLAY_LIMIT = 64

# The longest clause text a contract failure quotes, in code points, with the
# same truncation rule.
CLAUSE_TEXT_LIMIT = 200

_ELLIPSIS = "..."

PROPAGATED_NOTE = "'?' propagated None/Err"


def truncate_display(text: str, limit: int) -> str:
    """*text* if it fits in *limit* code points, else its head and ``...``."""
    if len(text) <= limit:
        return text
    return text[: max(limit - len(_ELLIPSIS), 0)] + _ELLIPSIS


def contract_message(
    *,
    clause: str,
    reason: str,
    function: str,
    clause_text: str | None,
    clause_index: int,
    arguments: Sequence[tuple[str, str]],
    result: str | None = None,
    checked_values: Sequence[tuple[str, str]] = (),
) -> str:
    """The message of a contract failure (proposal 0018, section 6).

    ``Precondition failed: name(a: 1, b: 0) requires b != 0``, or
    ``Postcondition failed: name(x: -1) ensures result >= 0 (result was -1)``.
    The argument and result values arrive already rendered and bounded.
    *clause_text* falls back to ``requires clause #N`` (1-based) when the
    source text is unknown. *checked_values* lists the parameters an
    ``ensures`` saw rebound to a different value than the call passed.
    """
    label = "Precondition failed" if clause == "requires" else "Postcondition failed"
    shown_arguments = ", ".join(f"{name}: {value}" for name, value in arguments)
    shown_clause = clause_text if clause_text else f"clause #{clause_index + 1}"
    message = f"{label}: {function}({shown_arguments}) {clause} {shown_clause}"
    notes: list[str] = []
    if reason == "propagate":
        notes.append(PROPAGATED_NOTE)
    if result is not None:
        notes.append(f"result was {result}")
    if checked_values:
        rebound = ", ".join(f"{name} was {value}" for name, value in checked_values)
        notes.append(f"{rebound} when checked")
    if notes:
        message += f" ({'; '.join(notes)})"
    return message
