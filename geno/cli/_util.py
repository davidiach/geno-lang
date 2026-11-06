"""Shared error-formatting utilities for CLI subcommands."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TextIO

from ..diagnostics import Diagnostic, ErrorCode, error_parts
from ..tokens import SourceLocation
from ..version_support import is_supported_python, unsupported_python_message


def _format_source_snippet(location) -> str:
    """Return a source-line + caret snippet for an error location, or ''."""
    if location is None or not hasattr(location, "filename"):
        return ""
    try:
        with open(location.filename) as f:
            lines = f.readlines()
        if location.line < 1 or location.line > len(lines):
            return ""
        source_line = lines[location.line - 1].rstrip("\n")
        col = max(location.column - 1, 0)
        caret = " " * col + "^"
        return f"\n  {source_line}\n  {caret}"
    except (OSError, UnicodeDecodeError):
        return ""


def _format_error(label: str, error) -> str:
    """Format an error under ``label`` with source snippets if available.

    The label appears exactly once per error: each line is built from the
    error's structured location and message, never its ``str()``, which may
    already carry a phase label of its own (type errors do).
    """
    lines = []
    for err in getattr(error, "errors", None) or [error]:
        fix_notes = "".join(
            f"\n  fix: {fix.title} (`geno check --fix` applies it)"
            for fix in getattr(err, "fixes", ())[:1]
        )
        for location, message in error_parts(err):
            where = f"{location}: " if location is not None else ""
            snippet = _format_source_snippet(location)
            lines.append(f"{label}: {where}{message}{snippet}{fix_notes}")
    return "\n".join(lines)


def _print_error(label: str, error, file=sys.stderr):
    """Print an error under ``label`` with a source snippet if available."""
    print(_format_error(label, error), file=file)


def _print_warning(diagnostic: Diagnostic, file: TextIO | None = None) -> None:
    """Print a warning diagnostic with its code and a source snippet."""
    loc = diagnostic.location
    where = f"{loc.filename}:{loc.line}:{loc.column}: " if loc else ""
    snippet = _format_source_snippet(loc)
    print(
        f"Warning: {where}{diagnostic.message} [{diagnostic.code.value}]{snippet}",
        file=file or sys.stderr,
    )


def report_deep_nesting_error(filename: str) -> None:
    """Report a RecursionError from an extremely deeply-nested program cleanly.

    A valid but very deeply-nested expression (e.g. a long left-associative
    operator chain) can exceed Python's recursion limit while the toolchain
    walks/serializes the AST. Every CLI command that type-checks or serializes
    should report this uniformly instead of dumping a raw Python traceback
    (H-08). Callers invoke this from an ``except RecursionError`` handler.
    """
    print(
        f"Error: expression nesting is too deep to process in {filename} "
        "(exceeded the interpreter's recursion limit). Simplify deeply nested "
        "expressions such as very long operator chains.",
        file=sys.stderr,
    )
    sys.exit(1)


def write_text_output(
    path: str | Path, content: str, *, create_parents: bool = False
) -> None:
    """Write generated output, reporting write failures clearly.

    A failure writing the *output* file (missing directory, permission denied,
    disk full) is reported as an output error and exits 1 — never as a raw
    traceback and never mislabeled as the input 'File not found' (M-08).

    ``create_parents`` opts a caller into creating the output file's missing
    parent directories first, the way directory-producing commands already do.
    It stays opt-in so commands that treat a missing directory as a user error
    keep reporting it. Parent creation failures (permission denied, a parent
    that is a regular file) are reported through the same output-error path.
    """
    if create_parents:
        parent = Path(path).parent
        try:
            if str(parent):
                parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"Error: cannot write output file {path}: {exc}", file=sys.stderr)
            sys.exit(1)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
    except OSError as exc:
        print(f"Error: cannot write output file {path}: {exc}", file=sys.stderr)
        sys.exit(1)


HOST_TRACEBACK_ENV = "GENO_TRACEBACK"


def host_traceback_requested() -> bool:
    """True when ``GENO_TRACEBACK=host`` asks for the raw host traceback.

    The escape hatch is for debugging the toolchain itself (proposal 0018):
    the CLI lanes print the host traceback after the Geno report. Envelopes,
    the embedding API and the hosted server never read it.
    """
    return os.environ.get(HOST_TRACEBACK_ENV) == "host"


def runtime_report_lines(error, label: str = "Runtime Error") -> list[str]:
    """The lines of the Geno runtime report for *error* (proposal 0018).

    The Geno stack the interpreter captured (most recent call last), a
    snippet of the failing line when this process can read the program's
    source, then ``<label>: <message>`` last. The message is the error's own
    ``message`` (never its ``str()``, which may carry a location and a label
    of its own); with neither stack nor location this is exactly the single
    message line of 0.6.
    """
    from ..runtime_report import (
        read_snippet_line,
        report_lines,
        snippet_location,
        stack_of,
    )

    message = getattr(error, "message", None)
    if not isinstance(message, str):
        message = str(error)
    location = getattr(error, "location", None)
    if not isinstance(location, SourceLocation):
        location = None
    stack = stack_of(error)
    where = snippet_location(stack, location)
    source_line = read_snippet_line(where) if where is not None else None
    return report_lines(
        label, message, stack=stack, location=location, source_line=source_line
    )


def _print_runtime_error(
    error,
    file=sys.stderr,
    *,
    label: str = "Runtime Error",
    host_traceback: bool = False,
):
    """Print the Geno runtime report for *error*.

    With *host_traceback* (``GENO_TRACEBACK=host``) the host traceback of
    the error follows the report.
    """
    print("\n".join(runtime_report_lines(error, label)), file=file)
    trace = getattr(error, "__traceback__", None)
    if host_traceback and trace is not None:
        import traceback

        print(f"Host traceback ({HOST_TRACEBACK_ENV}=host):", file=file)
        print(
            "".join(traceback.format_exception(type(error), error, trace)).rstrip(),
            file=file,
        )


def _emit_unsupported_python_error(
    command: str | None,
    *,
    json_output: bool = False,
    path: str | None = None,
) -> None:
    """Emit an unsupported-Python error in the command's native format."""
    message = unsupported_python_message()

    if json_output:
        import json as json_mod

        if command == "run":
            print(
                json_mod.dumps(
                    {
                        "ok": False,
                        "value": None,
                        "output": "",
                        "diagnostics": [
                            {
                                "code": ErrorCode.RUNTIME_UNKNOWN.value,
                                "message": message,
                                "severity": "error",
                            }
                        ],
                        "timing": {
                            "total_ms": 0.0,
                            "lex_ms": 0.0,
                            "parse_ms": 0.0,
                            "typecheck_ms": 0.0,
                            "run_ms": 0.0,
                        },
                        "steps_used": 0,
                    },
                    indent=2,
                    allow_nan=False,
                )
            )
        elif command == "constrain":
            print(
                json_mod.dumps(
                    {
                        "valid": False,
                        "error": message,
                        "unclosed_blocks": [],
                        "allowed_next": {
                            "keywords": [],
                            "allow_identifier": False,
                            "allow_type_identifier": False,
                            "allow_int": False,
                            "allow_float": False,
                            "allow_string": False,
                            "allow_bool": False,
                            "allow_punct": [],
                            "expected_end_stack": [],
                        },
                    },
                    indent=2,
                    allow_nan=False,
                )
            )
        elif command == "test":
            print(
                json_mod.dumps(
                    {
                        "files": [
                            {
                                "path": path or ".",
                                "error": message,
                                "elapsed_ms": 0.0,
                            }
                        ],
                        "total": 0,
                        "passed": 0,
                        "failed": 0,
                        "errors": 1,
                        "success": False,
                    },
                    indent=2,
                    allow_nan=False,
                )
            )
        else:
            print(json_mod.dumps({"error": message}, indent=2, allow_nan=False))
    else:
        print(f"Error: {message}", file=sys.stderr)

    sys.exit(1)


def _check_python_version(
    command: str | None = None,
    *,
    json_output: bool = False,
    path: str | None = None,
) -> None:
    """Fail fast if running on an unsupported Python version."""
    if not is_supported_python():
        _emit_unsupported_python_error(command, json_output=json_output, path=path)
