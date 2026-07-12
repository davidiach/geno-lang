"""Shared error-formatting utilities for CLI subcommands."""

from __future__ import annotations

import sys
from pathlib import Path

from ..diagnostics import ErrorCode
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


def _print_error(label: str, error, file=sys.stderr):
    """Print an error with source line snippet if available."""
    loc = getattr(error, "location", None)
    snippet = _format_source_snippet(loc)
    print(f"{label}: {error}{snippet}", file=file)


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


def write_text_output(path: str | Path, content: str) -> None:
    """Write generated output, reporting write failures clearly.

    A failure writing the *output* file (missing directory, permission denied,
    disk full) is reported as an output error and exits 1 — never as a raw
    traceback and never mislabeled as the input 'File not found' (M-08).
    """
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
    except OSError as exc:
        print(f"Error: cannot write output file {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def _print_runtime_error(error, file=sys.stderr):
    """Print a runtime error with stack trace if available."""
    msg = getattr(error, "message", str(error))
    loc = getattr(error, "location", None)
    snippet = _format_source_snippet(loc)
    stack = getattr(error, "_geno_stack", None)
    if stack:
        print("Stack trace (most recent call last):", file=file)
        for name, frame_loc in stack:
            if frame_loc:
                line = f"  in {name}, {frame_loc}"
            else:
                line = f"  in {name}"
            print(line, file=file)
    print(f"Runtime Error: {msg}{snippet}", file=file)


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
