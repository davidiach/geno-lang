"""
Line-based auto-formatter for Geno source files.

Uses keyword-based indentation tracking rather than AST parsing,
so comments and blank lines are preserved.
"""

from __future__ import annotations

# Keywords that open a new indentation block (followed by "end <kw>")
_BLOCK_OPENERS = {"func", "if", "while", "for", "match", "try", "trait", "impl", "test"}

# "end" decreases depth before the line
_BLOCK_CLOSERS = {"end"}

# These decrease depth for themselves, then increase for their body
_MID_BLOCK = {"else", "catch"}


def format_source(source: str) -> str:
    """Format Geno source code. Returns the formatted string."""
    lines = source.split("\n")
    result: list[str] = []
    depth = 0
    in_block_comment = False
    block_comment_depth = 0
    in_type_def = False
    # Track if we're directly inside a trait block (not inside a func within an impl)
    in_trait_body = False

    for index, line in enumerate(lines):
        stripped = line.strip()

        # Handle block comments
        if in_block_comment:
            result.append(_indent(block_comment_depth) + stripped if stripped else "")
            if "*/" in stripped:
                in_block_comment = False
            continue

        if _starts_block_comment(stripped):
            block_comment_depth = (
                depth + 1
                if in_type_def and _next_significant_token(lines, index + 1) == "|"
                else depth
            )
            formatted = _indent(block_comment_depth) + stripped
            result.append(formatted)
            in_block_comment = True
            continue

        # Blank lines (preserve in_type_def across blank lines so | variants
        # after a blank line are still indented correctly)
        if not stripped:
            result.append("")
            continue

        # Get the first token for indent decisions.  Lines starting with
        # ``export`` (e.g. ``export func foo`` / ``export type Foo = ...``
        # / ``export async func foo``) must be classified by the token
        # AFTER ``export`` — otherwise they bypass the block-opener /
        # alias handling below and get the wrong depth.  F-0020 in #663.
        # ``classification_line`` mirrors ``stripped`` with the leading
        # ``export`` (if any) peeled off; every subsequent check that
        # wants to pattern-match on a prefix (e.g. ``async func``) uses
        # it instead of ``stripped``.
        first_token = _first_token(stripped)
        classification_line = stripped
        if first_token == "export":  # noqa: S105
            remainder = stripped[len("export") :].lstrip()
            if remainder:
                classification_line = remainder
                first_token = _first_token(remainder)

        # End a multi-line type def when we hit a non-| line
        comment_continues_type_def = (
            in_type_def
            and _is_comment_line(stripped)
            and _next_significant_token(lines, index + 1) == "|"
        )
        if in_type_def and first_token != "|" and not comment_continues_type_def:  # noqa: S105
            in_type_def = False

        # Determine indent level for this line
        line_depth = depth

        if first_token in _BLOCK_CLOSERS:
            depth = max(0, depth - 1)
            line_depth = depth
            # Check if we're closing a trait
            if "end trait" in stripped:
                in_trait_body = False
        elif first_token in _MID_BLOCK:
            line_depth = max(0, depth - 1)
        elif comment_continues_type_def:
            line_depth = depth + 1
        elif first_token == "|" and in_type_def:  # noqa: S105
            # Type variant continuation lines indent under the type keyword
            line_depth = depth + 1

        # Apply indent
        result.append(_indent(line_depth) + stripped)

        # Adjust depth for subsequent lines
        if first_token == "async" and classification_line.startswith("async func"):  # noqa: S105
            # async func opens a block just like func
            depth += 1
        elif first_token == "func" and in_trait_body:  # noqa: S105
            # Trait method signatures don't open blocks
            pass
        elif first_token == "type" and "=" in classification_line:  # noqa: S105
            # Type definitions: if this has | on continuation lines, track it
            # But type doesn't use end/end type, so no depth change
            in_type_def = True
        elif first_token in _BLOCK_OPENERS and _has_inline_block_close(
            stripped, first_token
        ):
            # Block opened and closed on same line (e.g., "if x then return y end if")
            pass
        elif first_token in _BLOCK_OPENERS:
            depth += 1
            if first_token == "trait":  # noqa: S105
                in_trait_body = True

    # Ensure file ends with a single newline
    text = "\n".join(result)
    if not text or text.isspace():
        return "\n"
    if not text.endswith("\n"):
        text += "\n"
    while text.endswith("\n\n"):
        text = text[:-1]
    return text


def _indent(depth: int) -> str:
    """Return indentation string for the given depth."""
    return "    " * depth


def _starts_block_comment(stripped: str) -> bool:
    """Check if line starts a block comment, ignoring /* inside string literals."""
    if "/*" not in stripped or "*/" in stripped:
        return False
    # Walk the line tracking whether we're inside a string literal
    in_string = False
    i = 0
    while i < len(stripped) - 1:
        ch = stripped[i]
        if ch == "\\" and in_string:
            i += 2  # skip escape sequence
            continue
        if ch == '"':
            in_string = not in_string
        elif ch == "/" and stripped[i + 1] == "*" and not in_string:
            return True
        i += 1
    return False


def _has_inline_block_close(line: str, block_name: str) -> bool:
    """Return whether ``end <block_name>`` appears in code on the same line."""
    code = _strip_strings_and_line_comments(line)
    return f"end {block_name}" in code


def _strip_strings_and_line_comments(line: str) -> str:
    """Remove string literal contents and line comments from one source line."""
    chars: list[str] = []
    in_string = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_string:
            if ch == "\\":
                chars.append(" ")
                if i + 1 < len(line):
                    chars.append(" ")
                    i += 2
                    continue
            elif ch == '"':
                in_string = False
            chars.append(" ")
            i += 1
            continue

        if line.startswith("//", i) or line.startswith("/*", i):
            break
        if ch == '"':
            in_string = True
            chars.append(" ")
        else:
            chars.append(ch)
        i += 1
    return "".join(chars)


def _first_token(line: str) -> str:
    """Extract the first whitespace-delimited token from a line, ignoring comments."""
    if line.startswith("//"):
        return ""
    for i, ch in enumerate(line):
        if ch in (" ", "\t", "(", ":", "["):
            return line[:i]
    return line


def _is_comment_line(stripped: str) -> bool:
    """Return True for lines that contain only a Geno comment."""
    return stripped.startswith("//") or stripped.startswith("/*")


def _next_significant_token(lines: list[str], start: int) -> str:
    """Return the first token after comments and blank lines."""
    in_block_comment = False
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        if in_block_comment:
            if "*/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("//"):
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block_comment = True
            continue
        return _first_token(stripped)
    return ""
