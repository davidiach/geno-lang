"""Runtime failure reports: Geno stacks, locations and contract wording.

Proposal 0018 makes every runtime failure as precise as a compile error.
This module holds the parts every lane and backend must agree on: the stack
bound, the report text, the ``std/`` path display, the budgeted fitting of a
report into a size limit, the bounded source-snippet reader, and the
contract-failure wording. The compiled runtimes cannot import it (generated
code stays standalone), so the Python prelude (``geno/_runtime_support.py``)
and the JavaScript prelude (``geno/_js_runtime_support.js``) carry copies of
the constants and of ``contract_message``; ``geno/tests/test_runtime_diagnostics.py``
pins that the copies agree.

The module is deliberately light: it imports only ``geno.diagnostics`` and
``geno.tokens``, so any lane (including the default ``geno run`` parent,
which must not load the frontend) can use it.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .diagnostics import GenoStack, StackFrame
from .tokens import SourceLocation

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

STACK_HEADER = "Stack trace (most recent call last):"

# A source snippet is read from at most this many bytes of a file, and a line
# longer than SNIPPET_LINE_LIMIT code points is not shown.
SNIPPET_READ_LIMIT = 1 << 20
SNIPPET_LINE_LIMIT = 1000

# Where the installed standard library lives; its files display as
# ``std/<Name>.geno`` on every lane.
STDLIB_DIR = (Path(__file__).parent / "std").resolve()

# The origin pseudo-frame of a failure raised while verifying an example.
EXAMPLE_ORIGIN = "<example>"

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


# ---------------------------------------------------------------------------
# Stacks
# ---------------------------------------------------------------------------


class CapturedStack:
    """The raw stack an interpreter captured as a failure first left a frame.

    ``entries`` is a copy of the interpreter's call stack, outermost first:
    one ``(display name, call site)`` pair per activation, where the name is
    ``None`` for a lambda and the call site is where that activation was
    started (``None`` when no Geno position exists, as for ``main`` or a
    callback a builtin has not been located for yet). ``location`` is the
    failure location. ``origin`` names an outer pseudo-frame (the
    ``<example>`` of example verification), positioned at the outermost
    entry's call site.

    Nothing is bounded or renamed here: ``build_stack`` does that when a
    report is made, so the handlers the failure unwinds through can still
    correct an entry (``locate_entry``).
    """

    __slots__ = ("entries", "location", "origin")

    def __init__(
        self,
        entries: list[tuple[str | None, SourceLocation | None]],
        location: SourceLocation | None,
        origin: str | None = None,
    ) -> None:
        self.entries = entries
        self.location = location
        self.origin = origin

    def locate_entry(self, depth: int, location: SourceLocation | None) -> None:
        """Give the entry at *depth* the call site *location* if it has none."""
        if location is None or not 0 <= depth < len(self.entries):
            return
        name, site = self.entries[depth]
        if site is None:
            self.entries[depth] = (name, location)

    def __repr__(self) -> str:
        return (
            f"CapturedStack(entries={self.entries!r}, location={self.location!r}, "
            f"origin={self.origin!r})"
        )


def bound_frames(frames: Sequence[StackFrame]) -> GenoStack:
    """*frames* bounded to ``STACK_HEAD`` + ``STACK_TAIL`` frames."""
    total = len(frames)
    if total <= STACK_HEAD + STACK_TAIL:
        return GenoStack(tuple(frames))
    kept = tuple(frames[:STACK_HEAD]) + tuple(frames[total - STACK_TAIL :])
    return GenoStack(kept, total - STACK_HEAD - STACK_TAIL)


def build_stack(captured: CapturedStack) -> GenoStack | None:
    """The bounded Geno stack of *captured*; ``None`` when it has no frames.

    Lambdas are not frames: each named activation is positioned at the call
    site of the next named activation (a call inside a lambda body, or a
    builtin's call for a callback it started), and the innermost one at the
    failure location.
    """
    named: list[tuple[str, SourceLocation | None]] = [
        (name, site) for name, site in captured.entries if name is not None
    ]
    if not named:
        return None
    if captured.origin is not None:
        named.insert(0, (captured.origin, None))
    frames = [
        StackFrame(
            name,
            named[index + 1][1] if index + 1 < len(named) else captured.location,
        )
        for index, (name, _site) in enumerate(named)
    ]
    return bound_frames(frames)


def stack_of(error: object) -> GenoStack | None:
    """The bounded Geno stack *error* carries, if any."""
    captured = getattr(error, "geno_stack", None)
    if isinstance(captured, CapturedStack):
        return build_stack(captured)
    if isinstance(captured, GenoStack):
        return captured if captured.frames else None
    return None


# ---------------------------------------------------------------------------
# Paths and positions
# ---------------------------------------------------------------------------


def _stdlib_file(filename: str) -> Path | None:
    """The installed standard-library file *filename* names, if it is one."""
    if not filename or filename.startswith("<"):
        return None
    if filename.startswith("std/"):
        name = filename[len("std/") :]
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        return STDLIB_DIR / name
    try:
        path = Path(filename)
        if not path.name.endswith(".geno"):
            return None
        if path.parent.resolve() == STDLIB_DIR:
            return STDLIB_DIR / path.name
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def display_path(filename: str) -> str:
    """*filename* as reports show it: ``std/<Name>.geno`` for the stdlib."""
    stdlib = _stdlib_file(filename)
    if stdlib is not None:
        return f"std/{stdlib.name}"
    return filename


def format_position(location: SourceLocation) -> str:
    """``file:line:col`` with the ``std/`` display."""
    return f"{display_path(location.filename)}:{location.line}:{location.column}"


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def omitted_line(count: int) -> str:
    noun = "frame" if count == 1 else "frames"
    return f"  ... {count} {noun} omitted ..."


_OMITTED_LINE = re.compile(r"^  \.\.\. (\d+) frames? omitted \.\.\.$")


def frame_line(frame: StackFrame) -> str:
    if frame.location is None:
        return f"  in {frame.function}"
    return f"  {format_position(frame.location)} in {frame.function}"


def snippet_lines(source_line: str, column: int) -> list[str]:
    """The source line and caret shown under the last frame.

    The line is shown without its indentation, four spaces in from the frame
    lines, with the caret under *column* (1-based).
    """
    text = source_line.lstrip(" \t")
    offset = max(column - 1 - (len(source_line) - len(text)), 0)
    offset = min(offset, len(text))
    caret = "".join("\t" if ch == "\t" else " " for ch in text[:offset])
    return [f"      {text}", f"      {caret}^"]


def stack_lines(stack: GenoStack | None, omitted_at: int | None = None) -> list[str]:
    """The frame lines of *stack*, with the elision line in place.

    The omitted frames lie after the first ``STACK_HEAD`` kept frames, or
    after the first *omitted_at* frames when a budget dropped frames next to
    the elision point (the default lane's worker payload).
    """
    if stack is None or not stack.frames:
        return []
    frames = list(stack.frames)
    lines = [frame_line(frame) for frame in frames]
    if stack.omitted > 0:
        at = min(STACK_HEAD if omitted_at is None else omitted_at, len(lines))
        lines.insert(max(at, 0), omitted_line(stack.omitted))
    return lines


def report_lines(
    label: str,
    message: str,
    *,
    stack: GenoStack | None = None,
    location: SourceLocation | None = None,
    source_line: str | None = None,
    omitted_at: int | None = None,
) -> list[str]:
    """The lines of a runtime report (proposal 0018, section 4).

    The frame block (or ``  at <location>`` when no stack is known), then the
    snippet for *source_line* when given, then ``<label>: <message>`` last.
    *source_line* is the text of the line the innermost frame (or
    *location*) points at.
    """
    lines: list[str] = []
    where = snippet_location(stack, location)
    if stack is not None and stack.frames:
        lines.append(STACK_HEADER)
        lines.extend(stack_lines(stack, omitted_at))
    elif location is not None:
        lines.append(f"  at {format_position(location)}")
    if source_line is not None and where is not None:
        lines.extend(snippet_lines(source_line, where.column))
    lines.append(f"{label}: {message}")
    return lines


def snippet_location(
    stack: GenoStack | None, location: SourceLocation | None
) -> SourceLocation | None:
    """Where a report's snippet points: its innermost frame, or *location*."""
    if stack is not None and stack.frames:
        return stack.frames[-1].location
    return location


def render(
    label: str,
    message: str,
    *,
    stack: GenoStack | None = None,
    location: SourceLocation | None = None,
    source_line: str | None = None,
    omitted_at: int | None = None,
) -> str:
    """``report_lines`` joined with newlines (no trailing newline)."""
    return "\n".join(
        report_lines(
            label,
            message,
            stack=stack,
            location=location,
            source_line=source_line,
            omitted_at=omitted_at,
        )
    )


# ---------------------------------------------------------------------------
# Fitting a report into a budget
# ---------------------------------------------------------------------------


def fit_report_text(lines: Sequence[str], limit: int) -> str:
    """The report *lines* joined, shortened to at most *limit* characters.

    A report that must travel as one bounded string is never cut at its
    head, which would lose the final ``<Label>: <message>`` line. Instead
    the snippet goes first, then frame lines from the middle (the elision
    count grows), then the frame block, and as a last resort the message
    line's own text is truncated. The final line is always present and
    always last.
    """
    lines = list(lines)
    if not lines:
        return ""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    message = lines[-1]
    body = lines[:-1]
    header: list[str] = []
    if body and body[0] == STACK_HEADER:
        header = [body.pop(0)]
    frames: list[str] = []
    omitted = 0
    omitted_index: int | None = None
    for line in body:
        if line.startswith("   "):
            continue  # snippet lines: dropped first
        match = _OMITTED_LINE.match(line)
        if match is not None:
            omitted += int(match.group(1))
            omitted_index = len(frames)
            continue
        frames.append(line)

    def joined() -> str:
        block = list(frames)
        if omitted:
            index = omitted_index if omitted_index is not None else len(block) // 2
            block.insert(index, omitted_line(omitted))
        return "\n".join([*header, *block, message])

    # Drop the frames next to the elision point, alternating sides so that
    # the head and the tail shrink evenly.
    take_head = True
    while frames and len(joined()) > limit:
        if omitted_index is None:
            omitted_index = len(frames) // 2
        if (take_head and omitted_index > 0) or omitted_index >= len(frames):
            del frames[omitted_index - 1]
            omitted_index -= 1
        else:
            del frames[omitted_index]
        take_head = not take_head
        omitted += 1
    if frames:
        return joined()
    if len(message) <= limit:
        return message
    if limit <= len(_ELLIPSIS):
        return message[: max(limit, 0)]
    return message[: limit - len(_ELLIPSIS)] + _ELLIPSIS


# ---------------------------------------------------------------------------
# The default lane's worker payload
# ---------------------------------------------------------------------------
#
# In the default `geno run` lane, `main` runs as compiled Python inside the
# process-isolated worker. The worker maps the failure through the program's
# trace table (the prelude's `_geno_failure_report`) and sends the result to
# the parent as one compact `trace` object in its error payload:
#
#   {"files": [path, ...], "location": [file, line, col] | null,
#    "stack": [[name, file, line, col] | [name], ...], "omitted": n,
#    "omitted_at": k}
#
# The object has a fixed serialized budget of its own, and the parent's
# stderr capture window grows by the same reserved amount
# (`ProcessSandbox._STDERR_TRACE_OVERHEAD`), so the message's share and its
# truncation are what they were in 0.6. The parent treats the object as
# untrusted: `decode_trace_payload` validates every field and rejects the
# whole object when anything is malformed.

TRACE_PAYLOAD_LIMIT = 3072
TRACE_NAME_LIMIT = 128
TRACE_PATH_LIMIT = 512
# Over budget, paths longer than this are replaced by their basename.
TRACE_SHORT_PATH_LIMIT = 128

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

_Position = tuple[str, int, int]


def _payload_size(payload: dict[str, Any]) -> int:
    import json

    return len(json.dumps(payload))


def _short_path(path: str) -> str:
    if len(path) <= TRACE_SHORT_PATH_LIMIT:
        return path
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    return truncate_display(base, TRACE_SHORT_PATH_LIMIT)


def _build_trace_payload(
    frames: Sequence[tuple[str, _Position | None]],
    omitted: int,
    omitted_at: int,
    location: _Position | None,
    *,
    short_paths: bool,
    with_stack: bool,
) -> dict[str, Any]:
    files: list[str] = []
    indexes: dict[str, int] = {}

    def encode(position: _Position | None) -> list[Any] | None:
        if position is None:
            return None
        path = truncate_display(position[0], TRACE_PATH_LIMIT)
        if short_paths:
            path = _short_path(path)
        index = indexes.get(path)
        if index is None:
            index = indexes[path] = len(files)
            files.append(path)
        return [index, position[1], position[2]]

    payload: dict[str, Any] = {"files": files, "location": encode(location)}
    if with_stack:
        stack: list[list[Any]] = []
        for name, position in frames:
            shown = truncate_display(name, TRACE_NAME_LIMIT)
            encoded = encode(position)
            stack.append([shown] if encoded is None else [shown, *encoded])
        payload["stack"] = stack
        payload["omitted"] = omitted
        if omitted:
            payload["omitted_at"] = omitted_at
    return payload


def encode_trace_payload(
    frames: Sequence[tuple[str, _Position | None]],
    omitted: int,
    location: _Position | None,
    limit: int = TRACE_PAYLOAD_LIMIT,
) -> dict[str, Any] | None:
    """The worker's ``trace`` object for a mapped failure, within *limit*.

    *frames* (outermost first, already bounded), *omitted* and *location*
    come from the prelude's ``_geno_failure_report``. While the serialized
    object is over *limit* bytes, frames next to the elision point are
    dropped (``omitted`` grows), then long paths are replaced by their
    basenames, then the stack is dropped (keeping ``location``); ``None``
    when even that does not fit. The message never pays for any of it.
    """
    kept = list(frames)
    at = min(STACK_HEAD, len(kept)) if omitted else len(kept) // 2
    take_head = True
    while True:
        payload = _build_trace_payload(
            kept, omitted, at, location, short_paths=False, with_stack=True
        )
        if _payload_size(payload) <= limit or len(kept) <= 2:
            break
        # Drop the frames next to the elision point, alternating sides, but
        # keep the outermost and innermost frames.
        if take_head and at > 1:
            del kept[at - 1]
            at -= 1
        elif at < len(kept) - 1:
            del kept[at]
        else:
            del kept[at - 1]
            at -= 1
        take_head = not take_head
        omitted += 1
    for short_paths, with_stack in ((False, True), (True, True), (True, False)):
        payload = _build_trace_payload(
            kept,
            omitted,
            at,
            location,
            short_paths=short_paths,
            with_stack=with_stack,
        )
        if _payload_size(payload) <= limit:
            return payload
    return None


def _payload_int(value: Any, minimum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def decode_trace_payload(
    payload: Any,
) -> tuple[SourceLocation | None, GenoStack | None, int | None] | None:
    """Validate the worker's ``trace`` object: ``(location, stack, omitted_at)``.

    The object is untrusted. Anything malformed (a wrong type, an index out
    of range, a non-positive line, an oversized or control-character name or
    path, more frames than a bounded stack holds) rejects all of it, and
    the caller then reports the message alone.
    """
    if not isinstance(payload, dict) or not set(payload) <= {
        "files",
        "location",
        "stack",
        "omitted",
        "omitted_at",
    }:
        return None
    files = payload.get("files")
    if not isinstance(files, list) or len(files) > 2 * (STACK_HEAD + STACK_TAIL) + 1:
        return None
    for path in files:
        if (
            not isinstance(path, str)
            or not path
            or len(path) > TRACE_PATH_LIMIT
            or _CONTROL_CHARACTERS.search(path)
        ):
            return None

    def position(fields: Sequence[Any]) -> SourceLocation | None:
        if len(fields) != 3:
            return None
        index = _payload_int(fields[0], 0)
        line = _payload_int(fields[1], 1)
        column = _payload_int(fields[2], 0)
        if index is None or line is None or column is None or index >= len(files):
            return None
        return SourceLocation(line=line, column=column, filename=files[index])

    raw_location = payload.get("location")
    location: SourceLocation | None = None
    if raw_location is not None:
        if not isinstance(raw_location, list):
            return None
        location = position(raw_location)
        if location is None:
            return None
    raw_stack = payload.get("stack", [])
    if not isinstance(raw_stack, list) or len(raw_stack) > STACK_HEAD + STACK_TAIL:
        return None
    frames: list[StackFrame] = []
    for entry in raw_stack:
        if not isinstance(entry, list) or len(entry) not in (1, 4):
            return None
        name = entry[0]
        if (
            not isinstance(name, str)
            or not name
            or len(name) > TRACE_NAME_LIMIT
            or _CONTROL_CHARACTERS.search(name)
        ):
            return None
        site: SourceLocation | None = None
        if len(entry) == 4:
            site = position(entry[1:])
            if site is None:
                return None
        frames.append(StackFrame(name, site))
    omitted = _payload_int(payload.get("omitted", 0), 0)
    if omitted is None:
        return None
    omitted_at: int | None = None
    if "omitted_at" in payload:
        omitted_at = _payload_int(payload["omitted_at"], 0)
        if omitted_at is None or omitted_at > len(frames):
            return None
    if omitted and not frames:
        return None
    stack = GenoStack(tuple(frames), omitted) if frames else None
    return location, stack, omitted_at


# ---------------------------------------------------------------------------
# Trace tables (the compile side)
# ---------------------------------------------------------------------------
#
# A compiled program with a `main` embeds `_GENO_TRACE`, a table that maps
# host lines back to Geno source, next to an anchor function whose definition
# line is the table's host line 0 (`_geno_trace_anchor` in Python,
# `_genoTraceAnchor` in JavaScript). The runtime preludes read it only when a
# failure escapes `main`, so a call costs nothing extra. Both compilers write
# the same format, through `TraceRecorder`. Records are tab-separated lines:
#
#   G 1                          format and version
#   F idx path                   a source file
#   D start end host display     a Geno function's host line range [start, end)
#   T id file line col           a Geno statement's location
#   S host_line id               from this host line on, lines belong to id
#                                (-1: no statement)
#   C id key line col            a candidate site in statement id: the host
#                                callee `key` called there, at line:col
#   E host_line                  the end of the generated body
#
# Neither compiler writes its output linearly (block lambdas compile into a
# scratch buffer that is spliced in later), so the records that need a host
# line are written as in-band `\0geno-trace:N\0` markers and resolved once,
# over the final text, by counting the newlines before each marker. Geno
# string literals escape NUL on both backends, so a marker cannot collide
# with user text.

TRACE_TABLE_VERSION = 1
TRACE_MARKER_PATTERN = re.compile("\0geno-trace:([0-9]+)\0")
# The placeholder the compiler writes where the table's literal goes.
TRACE_TABLE_PLACEHOLDER = "\0geno-trace-table\0"
# Any trace marker or the placeholder (to strip them from scanned text).
TRACE_ANY_MARKER = re.compile("\0geno-trace(?::[0-9]+|-table)\0")
# The candidate key of an `await` (proposal 0018, section 2: async frames).
TRACE_AWAIT_KEY = "<await>"


def trace_field(text: str) -> str:
    """Escape one text field of the trace table."""
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


class TraceRecorder:
    """Collects the trace table of one compilation (proposal 0018)."""

    def __init__(self, entry_filename: str | None, *, absolute_paths: bool) -> None:
        entry_dir = "."
        if entry_filename and not entry_filename.startswith("<"):
            entry_dir = os.path.dirname(entry_filename) or "."
        self._entry_dir = entry_dir
        self._absolute_paths = absolute_paths
        self._files: dict[str, int] = {}
        self._paths: list[str] = []
        self._statements: list[tuple[int, int, int]] = []
        self._candidates: dict[tuple[int, str, int, int], None] = {}
        self._functions: list[list[int | str]] = []
        self._marks: list[tuple[str, int]] = []
        # Statements being compiled, innermost last.
        self.stack: list[int] = []

    def marker(self, kind: str, value: int) -> str:
        self._marks.append((kind, value))
        return f"\0geno-trace:{len(self._marks) - 1}\0"

    def _shown_path(self, filename: str) -> str:
        if filename.startswith("<"):
            return filename
        shown = display_path(filename)
        if shown != filename or self._absolute_paths:
            return shown
        # A compiled artifact must not carry the build machine's layout.
        try:
            relative = os.path.relpath(filename, self._entry_dir)
        except ValueError:
            return os.path.basename(filename)
        if (
            os.path.isabs(relative)
            or relative == os.pardir
            or relative.startswith(os.pardir + os.sep)
        ):
            return os.path.basename(filename)
        return relative.replace(os.sep, "/")

    def _file_index(self, filename: str) -> int:
        index = self._files.get(filename)
        if index is None:
            index = len(self._paths)
            self._files[filename] = index
            self._paths.append(self._shown_path(filename))
        return index

    def statement(self, location: SourceLocation | None) -> int:
        """Register a statement at *location*; -1 when it has none."""
        if location is None or not isinstance(location.filename, str):
            return -1
        file_index = self._file_index(location.filename)
        self._statements.append((file_index, location.line, location.column))
        return len(self._statements) - 1

    def candidate(self, key: str, location: SourceLocation | None) -> None:
        """Record that the current statement calls *key* at *location*."""
        if location is None or not self.stack or self.stack[-1] < 0:
            return
        self._candidates[(self.stack[-1], key, location.line, location.column)] = None

    def function(self, host_name: str, display_name: str) -> int:
        self._functions.append([0, 0, host_name, display_name])
        return len(self._functions) - 1

    def resolve(self, text: str, anchor_at: int, literal: Any = repr) -> str:
        """Remove the markers from *text* and fill in the table.

        *anchor_at* is where the anchor's definition line starts in *text*;
        *literal* renders the table as a string literal of the target
        language (``repr`` for Python, ``json.dumps`` for JavaScript).
        """
        pieces = [text[:anchor_at]]
        line = 0
        position = anchor_at
        statement_lines: list[tuple[int, int]] = []
        end_line = 0
        for match in TRACE_MARKER_PATTERN.finditer(text, anchor_at):
            start = match.start()
            line += text.count("\n", position, start)
            pieces.append(text[position:start])
            position = match.end()
            kind, value = self._marks[int(match.group(1))]
            if kind == "S":
                if statement_lines and statement_lines[-1][0] == line:
                    statement_lines.pop()
                if not statement_lines or statement_lines[-1][1] != value:
                    statement_lines.append((line, value))
            elif kind == "D0":
                self._functions[value][0] = line
            elif kind == "D1":
                self._functions[value][1] = line
            elif kind == "E":
                end_line = line
        pieces.append(text[position:])
        table = self._encode(statement_lines, end_line)
        return "".join(pieces).replace(TRACE_TABLE_PLACEHOLDER, literal(table), 1)

    def _encode(self, statement_lines: list[tuple[int, int]], end_line: int) -> str:
        records = [f"G\t{TRACE_TABLE_VERSION}"]
        records.extend(
            f"F\t{index}\t{trace_field(path)}" for index, path in enumerate(self._paths)
        )
        records.extend(
            f"D\t{start}\t{end}\t{trace_field(str(host))}\t{trace_field(str(shown))}"
            for start, end, host, shown in self._functions
        )
        records.extend(
            f"T\t{index}\t{file_index}\t{line}\t{column}"
            for index, (file_index, line, column) in enumerate(self._statements)
        )
        records.extend(f"S\t{line}\t{index}" for line, index in statement_lines)
        records.extend(
            f"C\t{index}\t{trace_field(key)}\t{line}\t{column}"
            for index, key, line, column in self._candidates
        )
        records.append(f"E\t{end_line}")
        return "\n".join(records)


# ---------------------------------------------------------------------------
# Source snippets
# ---------------------------------------------------------------------------


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def read_snippet_line(
    location: Any,
    *,
    allowed_roots: Sequence[str | os.PathLike[str]] | None = None,
) -> str | None:
    """The text of the source line *location* points at, or ``None``.

    The read is bounded: it opens only a regular file (never a FIFO, device
    or directory, without blocking), stops at the target line or after
    ``SNIPPET_READ_LIMIT`` bytes, and refuses a line longer than
    ``SNIPPET_LINE_LIMIT`` code points or one that does not decode.

    With *allowed_roots* the location is untrusted (it came from a worker
    payload): the path must end in ``.geno`` and resolve, symlinks followed,
    inside one of the roots or the installed standard library, and the final
    component is opened without following a symlink where the platform
    allows it. Without it the location comes from this process's own
    frontend. ``std/<Name>.geno`` names the standard library in both modes.
    """
    filename = getattr(location, "filename", None)
    line = getattr(location, "line", None)
    if (
        not isinstance(filename, str)
        or not isinstance(line, int)
        or isinstance(line, bool)
        or line < 1
        or not filename
        or filename.startswith("<")
        or "\0" in filename
    ):
        return None
    stdlib = _stdlib_file(filename)
    path = str(stdlib) if stdlib is not None else filename
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        if allowed_roots is not None:
            if not path.endswith(".geno"):
                return None
            resolved = os.path.realpath(path)
            roots = [os.path.realpath(os.fspath(root)) for root in allowed_roots]
            roots.append(str(STDLIB_DIR))
            if not any(_inside(resolved, root) for root in roots):
                return None
            path = resolved
            flags |= getattr(os, "O_NOFOLLOW", 0)
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        descriptor = os.open(path, flags)
    except (OSError, ValueError):
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return None
        handle = os.fdopen(descriptor, "rb")
    except OSError:
        os.close(descriptor)
        return None
    try:
        with handle:
            remaining = SNIPPET_READ_LIMIT
            raw = b""
            for _number in range(line):
                if remaining <= 0:
                    return None
                raw = handle.readline(remaining)
                if not raw:
                    return None
                remaining -= len(raw)
            if not raw.endswith(b"\n") and remaining <= 0:
                return None  # the line runs past the read limit
        text = raw.decode("utf-8").rstrip("\r\n")
    except (OSError, UnicodeDecodeError):
        return None
    if len(text) > SNIPPET_LINE_LIMIT:
        return None
    return text
