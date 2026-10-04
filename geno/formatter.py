"""
Line-based auto-formatter for Geno source files.

Uses keyword-based indentation tracking rather than AST parsing,
so comments and blank lines are preserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, is_dataclass

from .lexer import Lexer, LexerError
from .tokens import SourceLocation, TokenType


class FormatError(ValueError):
    """Raised when formatting would change what the program means."""


def format_source(source: str) -> str:
    """Format Geno source code. Returns the formatted string.

    Raises:
        FormatError: if ``source`` parses but the formatted text would not
            parse to the same program.  Formatting only changes whitespace,
            so this guard catches indentation-sensitive constructs the
            formatter misjudged instead of writing a broken file (#131).
    """
    formatted = _format_source_text(source)
    if formatted != source and not _same_program(source, formatted):
        raise FormatError(
            "formatting would change the meaning of this file; it was left unchanged"
        )
    return formatted


def _same_program(source: str, formatted: str) -> bool:
    """Return whether ``formatted`` parses to the same program as ``source``.

    A ``source`` that does not parse (an editor buffer mid-edit) has nothing
    to compare, so formatting it stays best effort.
    """
    from .parser import ParseError, ParseErrors, parse

    unparsable = (LexerError, ParseError, ParseErrors, RecursionError)
    try:
        original = parse(source)
    except unparsable:
        return True
    try:
        reformatted = parse(formatted)
    except unparsable:
        return False
    return _ast_key(original) == _ast_key(reformatted)


def _ast_key(node: object) -> object:
    """Structural key of an AST that ignores source locations."""
    if isinstance(node, list | tuple):
        return tuple(_ast_key(item) for item in node)
    if isinstance(node, dict):
        return tuple((key, _ast_key(value)) for key, value in node.items())
    if is_dataclass(node) and not isinstance(node, type):
        return (
            type(node).__name__,
            tuple(
                (item.name, _ast_key(getattr(node, item.name)))
                for item in fields(node)
                if item.compare and item.name != "location"
            ),
        )
    if isinstance(node, SourceLocation):
        return None
    return node


def _format_source_text(source: str) -> str:
    """Format ``source`` without the parse-preservation guard."""
    if '"""' not in source:
        return _format_lines(source)
    try:
        tokens = Lexer(source).tokenize()
    except LexerError:
        # An incomplete literal has no safe closing span. Keep editor buffers
        # unchanged until the lexer can identify their contents unambiguously.
        return source

    line_offsets = [0]
    for line in source.split("\n"):
        line_offsets.append(line_offsets[-1] + len(line) + 1)
    prefix = "__geno_format_literal_"
    while prefix in source:
        prefix += "_"
    literals: list[tuple[str, str]] = []
    parts: list[str] = []
    cursor = 0
    for token in tokens:
        if token.type != TokenType.STRING:
            continue
        start = line_offsets[token.location.line - 1] + token.location.column - 1
        if not source.startswith('"""', start):
            continue
        # Triple strings contain raw text: the token value has exactly the
        # source length between the opening and closing three-quote delimiters.
        end = start + len(token.value) + 6
        placeholder = f'"{prefix}{len(literals)}__"'
        parts.extend((source[cursor:start], placeholder))
        literals.append((placeholder, source[start:end]))
        cursor = end
    parts.append(source[cursor:])
    formatted = _format_lines("".join(parts))
    for placeholder, literal in literals:
        formatted = formatted.replace(placeholder, literal)
    return formatted


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\|>|->|\S")
_OPEN_BRACKETS = {"(": ")", "[": "]", "{": "}"}
_CLOSE_BRACKETS = {")", "]", "}"}
# Keywords that always open a block closed by ``end <kw>``.
_ALWAYS_OPENS = {"while", "match", "try", "trait", "impl", "test"}


@dataclass
class _Frame:
    """One open block or bracket and the indent its contents use."""

    kind: str
    inner: int
    is_lambda_params: bool = False


def _format_lines(source: str) -> str:
    """Apply indentation with multiline literal contents protected by the caller.

    Indentation follows the code's tokens: a stack holds every open block
    (``func`` ... ``end func``, ``fn(...) do`` ... ``end fn``, a match
    expression, ...) and every open bracket.  A line is indented one step past
    the line that opened the innermost frame, so match expressions, block
    lambdas and multi-line call arguments keep their structure (#131, #145).
    """
    lines = source.split("\n")
    codes = _line_codes(lines)
    else_if_lines = _else_if_continuation_lines(lines, codes)
    result: list[str] = []
    stack: list[_Frame] = []
    in_block_comment = False
    block_comment_depth = 0
    in_type_def = False

    for index, line in enumerate(lines):
        stripped = line.strip()

        # Handle block comments
        if in_block_comment:
            result.append(_indent(block_comment_depth) + stripped if stripped else "")
            if "*/" in stripped:
                in_block_comment = False
            continue

        depth = stack[-1].inner if stack else 0

        if _starts_block_comment(stripped):
            block_comment_depth = (
                depth + 1
                if in_type_def and _next_significant_token(lines, index + 1) == "|"
                else depth
            )
            result.append(_indent(block_comment_depth) + stripped)
            in_block_comment = True
            continue

        # Blank lines (preserve in_type_def across blank lines so | variants
        # after a blank line are still indented correctly)
        if not stripped:
            result.append("")
            continue

        tokens = _TOKEN_RE.findall(codes[index])
        first_token = tokens[0] if tokens else ""
        if first_token == "export" and len(tokens) > 1:  # noqa: S105
            first_token = tokens[1]

        # End a multi-line type def when we hit a non-| line
        comment_continues_type_def = (
            in_type_def
            and _is_comment_line(stripped)
            and _next_significant_token(lines, index + 1) == "|"
        )
        if in_type_def and first_token != "|" and not comment_continues_type_def:  # noqa: S105
            in_type_def = False

        line_depth = depth
        if index in else_if_lines:
            # ``if`` wrapped onto the line after ``else`` at the same column
            # continues the chain; it must stay level with ``else``.
            line_depth = max(0, depth - 1)
        elif first_token == "end" and stack:  # noqa: S105
            line_depth = _closing_depth(stack, _end_kind(tokens, 0))
        elif first_token in _CLOSE_BRACKETS and stack:
            line_depth = _closing_depth(stack, first_token)
        elif first_token in {"else", "catch"}:
            line_depth = max(0, depth - 1)
        elif first_token == "|>":  # noqa: S105
            line_depth = depth + 1
        elif comment_continues_type_def or (first_token == "|" and in_type_def):  # noqa: S105
            # Type variant continuation lines indent under the type keyword
            line_depth = depth + 1

        result.append(_indent(line_depth) + stripped)

        if first_token == "type" and "=" in tokens:  # noqa: S105
            # Type definitions use leading ``|`` variant lines, not ``end``.
            in_type_def = True
        _apply_line_tokens(
            stack, tokens, line_depth + 1, skip_first_if=index in else_if_lines
        )

    # Ensure file ends with a single newline
    text = "\n".join(result)
    if not text or text.isspace():
        return "\n"
    if not text.endswith("\n"):
        text += "\n"
    while text.endswith("\n\n"):
        text = text[:-1]
    return text


def _line_codes(lines: list[str]) -> list[str]:
    """Return each line's code, blank for lines inside a block comment."""
    codes: list[str] = []
    in_block_comment = False
    for line in lines:
        stripped = line.strip()
        if in_block_comment:
            in_block_comment = "*/" not in stripped
            codes.append("")
            continue
        if _starts_block_comment(stripped):
            in_block_comment = True
            codes.append("")
            continue
        codes.append(_line_code(stripped))
    return codes


def _line_code(stripped: str) -> str:
    """Return a line's code with strings and comments blanked out."""
    if stripped.startswith("#"):
        # Corpus headers such as ``# EXPECT: E502`` are prose, not code.
        return ""
    return _strip_strings_and_line_comments(stripped)


def _end_kind(tokens: list[str], index: int) -> str:
    """Return the block keyword named after the ``end`` at ``index``."""
    return tokens[index + 1] if index + 1 < len(tokens) else ""


def _closing_depth(stack: list[_Frame], kind: str) -> int:
    """Indent for a line that starts by closing the frame named ``kind``."""
    for frame in reversed(stack):
        if frame.kind == kind:
            return max(0, frame.inner - 1)
    return max(0, stack[-1].inner - 1)


def _apply_line_tokens(
    stack: list[_Frame], tokens: list[str], inner: int, *, skip_first_if: bool
) -> None:
    """Push and pop the frames one line's tokens open and close."""
    lead = tokens[1:2] if tokens[:1] == ["export"] else tokens[:1]
    starts_with_impl = lead == ["impl"]
    previous = ""
    last_closed_lambda_params = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        top = stack[-1] if stack else None
        in_bracket = top is not None and top.kind in _CLOSE_BRACKETS
        if token == "end":  # noqa: S105
            kind = _end_kind(tokens, index)
            _pop_block(stack, kind)
            index += 2
            previous = kind
            continue
        if token in _OPEN_BRACKETS:
            stack.append(
                _Frame(
                    _OPEN_BRACKETS[token],
                    inner,
                    is_lambda_params=token == "(" and previous == "fn",  # noqa: S105
                )
            )
        elif token in _CLOSE_BRACKETS:
            last_closed_lambda_params = False
            if top is not None and top.kind == token:
                last_closed_lambda_params = top.is_lambda_params
                stack.pop()
        elif token in _ALWAYS_OPENS:
            stack.append(_Frame(token, inner))
        elif token == "func":  # noqa: S105
            # Trait bodies hold signatures only; they never get ``end func``.
            if top is None or top.kind != "trait":
                stack.append(_Frame(token, inner))
        elif token == "if":  # noqa: S105
            chained = previous == "else" or (index == 0 and skip_first_if)
            # A comprehension's ``if`` filter has no ``end if``.
            if not chained and not in_bracket:
                stack.append(_Frame(token, inner))
        elif token == "for":  # noqa: S105
            # Neither a comprehension's ``for`` nor ``impl T for U`` opens a block.
            if not in_bracket and not starts_with_impl:
                stack.append(_Frame(token, inner))
        elif token == "do":  # noqa: S105
            if previous == ")" and last_closed_lambda_params:
                stack.append(_Frame("fn", inner))
        previous = token
        index += 1


def _pop_block(stack: list[_Frame], kind: str) -> None:
    """Close the innermost block named ``kind`` and any brackets left inside it."""
    for position in range(len(stack) - 1, -1, -1):
        if stack[position].kind == kind:
            del stack[position:]
            return
        if stack[position].kind not in _CLOSE_BRACKETS:
            break
    if stack:
        stack.pop()


def _else_if_continuation_lines(lines: list[str], codes: list[str]) -> set[int]:
    """Lines whose leading ``if`` continues the chain of the ``else`` above.

    The parser treats an ``if`` on a later line as ``else if`` when it sits at
    or left of the ``else`` column, so the formatter must keep both level.
    """
    chained: set[int] = set()
    for index, code in enumerate(codes):
        tokens = _TOKEN_RE.findall(code)
        if not tokens or tokens[-1] != "else":
            continue
        else_column = _token_column(lines[index], code, len(tokens) - 1)
        for next_index in range(index + 1, len(codes)):
            next_tokens = _TOKEN_RE.findall(codes[next_index])
            if not next_tokens:
                continue
            if next_tokens[0] == "if":
                next_line = lines[next_index]
                if_column = len(next_line) - len(next_line.lstrip())
                if if_column <= else_column:
                    chained.add(next_index)
            break
    return chained


def _token_column(line: str, code: str, token_index: int) -> int:
    """Zero-based column of the ``token_index``-th token of ``line``."""
    leading = len(line) - len(line.lstrip())
    matches = list(_TOKEN_RE.finditer(code))
    return leading + matches[token_index].start()


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
