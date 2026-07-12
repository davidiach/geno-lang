"""
CLI formatting utilities and code formatter command.

Provides ANSI color helpers for terminal output and the ``geno fmt``
command implementation.

Extracted from ``__main__.py`` to keep the CLI module focused on
argument parsing and dispatch.
"""

import os
import sys
from pathlib import Path

# =============================================================================
# Terminal color helpers
# =============================================================================


def supports_color() -> bool:
    """Check if the terminal supports ANSI color codes."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def color(text: str, code: str) -> str:
    """Wrap text in ANSI color codes if supported."""
    if not supports_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def green(text: str) -> str:
    return color(text, "32")


def red(text: str) -> str:
    return color(text, "31")


def yellow(text: str) -> str:
    return color(text, "33")


def dim(text: str) -> str:
    return color(text, "2")


# =============================================================================
# Code formatter command
# =============================================================================


def format_files(path: str, check: bool = False, diff: bool = False):
    """Format Geno source files."""
    from .formatter import format_source
    from .test_runner import discover_files

    target = Path(path)
    if not target.exists():
        print(f"Error: '{path}' not found", file=sys.stderr)
        sys.exit(1)

    if target.is_file():
        files = [target]
    else:
        files = discover_files(target)

    if not files:
        print(f"No .geno files found in '{path}'", file=sys.stderr)
        sys.exit(1)

    any_changed = False
    had_error = False
    for filepath in sorted(files):
        # Per-file error boundary: a single unreadable/non-UTF-8/unwritable file
        # must not abort the whole run and skip every file after it. Report it and
        # carry on, then exit non-zero so the failure is not silent.
        try:
            source = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error: cannot read {filepath}: {exc}", file=sys.stderr)
            had_error = True
            continue

        formatted = format_source(source)

        if source == formatted:
            continue

        any_changed = True

        if check:
            print(f"Would reformat: {filepath}")
        elif diff:
            import difflib

            diff_lines = difflib.unified_diff(
                source.splitlines(keepends=True),
                formatted.splitlines(keepends=True),
                fromfile=str(filepath),
                tofile=str(filepath),
            )
            print("".join(diff_lines), end="")
        else:
            try:
                filepath.write_text(formatted, encoding="utf-8")
            except OSError as exc:
                print(f"Error: cannot write {filepath}: {exc}", file=sys.stderr)
                had_error = True
                continue
            print(f"Formatted: {filepath}")

    if check and any_changed:
        print("\nSome files need formatting. Run `geno fmt` to fix.")
    elif not any_changed and not had_error:
        print("All files are properly formatted.")

    if had_error or (check and any_changed):
        sys.exit(1)
