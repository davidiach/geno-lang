"""``geno migrate``: rewrite Geno 0.5 source for Geno 0.6.

Every rewrite preserves the 0.5 program's behavior, and each is local to a few
tokens, so it works on source that the 0.6 parser rejects. What cannot be
rewritten mechanically (a ``try``/``catch``, a ``throw`` of a user type, a
removed builtin passed as a value) is reported with its location instead.

Rules (proposals 0009 and 0010):

- ``throw "text"`` (a string or f-string) becomes ``panic("text")``.
- ``throw`` is deleted from ``with`` effect lists.
- A call of ``fs_read_text``, ``fs_write_text``, ``http_fetch`` or ``http_post``
  gains ``.unwrap()``: 0.6 returns a ``Result`` where 0.5 raised.
- ``string_index_of``/``string_last_index_of`` (and ``String.index_of``,
  ``String.last_index_of``) gain ``.unwrap_or(-1)``: 0.6 returns an ``Option``.
- Removed builtins are rewritten to what replaces them: ``repeat_string`` ->
  ``string_repeat``, ``contains_substring`` -> ``string_contains``,
  ``sort_strings(xs)`` -> ``sort(xs)``, ``is_positive(x)`` -> ``(x > 0)``,
  ``is_numeric_string(s)`` -> ``is_some(parse_int(s))``,
  ``is_permutation(a, b)`` -> ``(sort(a) == sort(b))``, and, for a simple
  argument, ``square(x)`` -> ``(x * x)`` and ``is_sorted(xs)`` ->
  ``(xs == sort(xs))``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .lexer import _IDENTIFIER_RE, _NUMBER_RE, Lexer, LexerError
from .tokens import Token, TokenType


@dataclass(frozen=True)
class Note:
    """A change made, or a construct left for a person, at a source line."""

    line: int
    message: str


@dataclass
class MigrationResult:
    """The migrated source and what happened to it."""

    source: str
    changes: list[Note] = field(default_factory=list)
    manual: list[Note] = field(default_factory=list)


class MigrationError(Exception):
    """The source could not be tokenized, so nothing was rewritten."""


_IO_BUILTINS = frozenset({"fs_read_text", "fs_write_text", "http_fetch", "http_post"})
_INDEX_BUILTINS = frozenset({"string_index_of", "string_last_index_of"})
_STRING_INDEX_FUNCTIONS = frozenset({"index_of", "last_index_of"})
_RENAMED_BUILTINS = {
    "repeat_string": "string_repeat",
    "contains_substring": "string_contains",
    "sort_strings": "sort",
}
_OPENERS = {TokenType.LPAREN, TokenType.LBRACKET, TokenType.LBRACE}
_CLOSERS = {TokenType.RPAREN, TokenType.RBRACKET, TokenType.RBRACE}


class _Source:
    """Token spans in a source text."""

    def __init__(self, source: str) -> None:
        self.text = source
        try:
            self.tokens = Lexer(source).tokenize()
        except LexerError as exc:
            raise MigrationError(str(exc)) from exc
        self._line_starts = [0]
        for match in re.finditer("\n", source):
            self._line_starts.append(match.end())

    def start(self, token: Token) -> int:
        location = token.location
        return self._line_starts[location.line - 1] + location.column - 1

    def end(self, token: Token) -> int:
        start = self.start(token)
        kind = token.type
        if kind is TokenType.EOF:
            return start
        if kind is TokenType.STRING:
            lexer = Lexer(self.text)
            lexer.pos = start
            lexer._read_string()
            return lexer.pos
        if kind is TokenType.FSTRING:
            lexer = Lexer(self.text)
            lexer.pos = start + 1  # past the `f`
            lexer._read_fstring()
            return lexer.pos
        if kind in (TokenType.INTEGER, TokenType.FLOAT):
            match = _NUMBER_RE.match(self.text, start)
            if match:
                return match.end()
        match = _IDENTIFIER_RE.match(self.text, start)
        if match and str(token.value).isidentifier():
            return match.end()
        return start + len(str(token.value))

    def span_text(self, first: int, last: int) -> str:
        """The source text of tokens ``first`` through ``last``."""
        return self.text[self.start(self.tokens[first]) : self.end(self.tokens[last])]

    def matching_close(self, open_index: int) -> int | None:
        depth = 0
        for index in range(open_index, len(self.tokens)):
            kind = self.tokens[index].type
            if kind in _OPENERS:
                depth += 1
            elif kind in _CLOSERS:
                depth -= 1
                if depth == 0:
                    return index
        return None

    def call_arguments(
        self, open_index: int, close_index: int
    ) -> list[tuple[int, int]]:
        """Token index ranges of the arguments between a call's parentheses."""
        args: list[tuple[int, int]] = []
        depth = 0
        first = open_index + 1
        for index in range(open_index + 1, close_index):
            kind = self.tokens[index].type
            if kind in _OPENERS:
                depth += 1
            elif kind in _CLOSERS:
                depth -= 1
            elif kind is TokenType.COMMA and depth == 0:
                args.append((first, index - 1))
                first = index + 1
        if first <= close_index - 1:
            args.append((first, close_index - 1))
        return args


def migrate_source(source: str) -> MigrationResult:
    """Rewrite *source* from Geno 0.5 to Geno 0.6 where that is mechanical."""
    src = _Source(source)
    tokens = src.tokens
    edits: list[tuple[int, int, str]] = []
    result = MigrationResult(source)

    def change(token: Token, message: str) -> None:
        result.changes.append(Note(token.location.line, message))

    def manual(token: Token, message: str) -> None:
        result.manual.append(Note(token.location.line, message))

    defined = {
        tokens[i + 1].value
        for i in range(len(tokens) - 1)
        if tokens[i].type is TokenType.FUNC
        and tokens[i + 1].type is TokenType.IDENTIFIER
    }
    imports_string = any(
        tokens[i].type is TokenType.IMPORT
        and tokens[i + 1].value == "String"
        and tokens[i + 2].value != "as"
        for i in range(len(tokens) - 2)
    )

    def is_call(index: int) -> bool:
        """Whether the identifier at *index* is a plain call `name(...)`."""
        if index + 1 >= len(tokens) or tokens[index + 1].type is not TokenType.LPAREN:
            return False
        previous = tokens[index - 1].type if index > 0 else None
        return previous not in (TokenType.DOT, TokenType.FUNC)

    def used_as(index: int, type_name: str) -> bool:
        """Whether the call at *index* already treats its value as *type_name*.

        Keeps a second run, or a file already written for 0.6, unchanged:
        `match fs_read_text(p) with`, `let r: Result[...] = fs_read_text(p)`
        and `return fs_read_text(p)` from a function returning `Result`.
        """
        if index == 0:
            return False
        previous = tokens[index - 1]
        if previous.type is TokenType.MATCH:
            return True
        line = tokens[index].location.line
        cursor = index - 1
        while cursor >= 0 and tokens[cursor].location.line == line:
            if tokens[cursor].value == type_name:
                return True
            cursor -= 1
        if previous.type is not TokenType.RETURN:
            return False
        func = index - 1
        while func >= 0 and tokens[func].type is not TokenType.FUNC:
            func -= 1
        if func < 0:
            return False
        signature_line = tokens[func].location.line
        for cursor in range(func + 1, index):
            if tokens[cursor].location.line != signature_line:
                break
            if tokens[cursor].type is TokenType.ARROW:
                return bool(tokens[cursor + 1].value == type_name)
        return False

    def append_after_call(open_index: int, suffix: str) -> bool:
        close = src.matching_close(open_index)
        if close is None:
            return False
        following = tokens[close + 1] if close + 1 < len(tokens) else None
        if following is not None and following.type in (
            TokenType.QUESTION,
            TokenType.DOT,
        ):
            return False
        position = src.end(tokens[close])
        edits.append((position, position, suffix))
        return True

    try_depth = 0
    for index, token in enumerate(tokens):
        kind = token.type
        if kind is TokenType.TRY:
            if index > 0 and tokens[index - 1].type is TokenType.END:
                continue  # `end try`
            try_depth += 1
            manual(
                token,
                "try/catch: return a Result from the code that can fail and "
                "handle it with match or ?; a runtime error can no longer be caught",
            )
            continue
        if kind is TokenType.END and index + 1 < len(tokens):
            if tokens[index + 1].type is TokenType.TRY:
                try_depth = max(0, try_depth - 1)
            continue
        if kind is TokenType.THROW:
            _migrate_throw(src, index, try_depth, edits, change, manual)
            continue
        if kind is not TokenType.IDENTIFIER:
            continue
        name = token.value
        if name in defined and not (
            index > 0 and tokens[index - 1].type is TokenType.DOT
        ):
            continue  # the program's own function of that name

        if name in _IO_BUILTINS and is_call(index):
            if used_as(index, "Result"):
                continue
            if append_after_call(index + 1, ".unwrap()"):
                change(token, f"{name}(...) returns a Result: added .unwrap()")
            continue

        string_index = name in _STRING_INDEX_FUNCTIONS and (
            (
                index > 1
                and tokens[index - 1].type is TokenType.DOT
                and tokens[index - 2].value == "String"
            )
            or (imports_string and is_call(index))
        )
        if (name in _INDEX_BUILTINS and is_call(index)) or string_index:
            first = index - 2 if string_index and not is_call(index) else index
            if used_as(first, "Option"):
                continue
            if index + 1 < len(tokens) and tokens[index + 1].type is TokenType.LPAREN:
                if append_after_call(index + 1, ".unwrap_or(-1)"):
                    change(
                        token, f"{name}(...) returns an Option: added .unwrap_or(-1)"
                    )
            continue

        if name in _REMOVED_REWRITES or name in _RENAMED_BUILTINS:
            if not is_call(index):
                if index > 0 and tokens[index - 1].type is TokenType.DOT:
                    continue
                manual(
                    token,
                    f"'{name}' was removed in Geno 0.6; replace this use by hand",
                )
                continue
            if name in _RENAMED_BUILTINS:
                edits.append(
                    (src.start(token), src.end(token), _RENAMED_BUILTINS[name])
                )
                change(token, f"{name} -> {_RENAMED_BUILTINS[name]}")
                continue
            close = src.matching_close(index + 1)
            if close is None:
                continue
            args = [
                src.span_text(a, b) for a, b in src.call_arguments(index + 1, close)
            ]
            simple = [a == b for a, b in src.call_arguments(index + 1, close)]
            replacement = _REMOVED_REWRITES[name](args, simple)
            if replacement is None:
                manual(
                    token,
                    f"'{name}' was removed in Geno 0.6; rewrite this call by hand",
                )
                continue
            edits.append((src.start(token), src.end(tokens[close]), replacement))
            change(token, f"{name}(...) -> {replacement}")

    result.source = _apply(source, edits)
    return result


def _migrate_throw(
    src: _Source,
    index: int,
    try_depth: int,
    edits: list[tuple[int, int, str]],
    change: Callable[[Token, str], None],
    manual: Callable[[Token, str], None],
) -> None:
    tokens = src.tokens
    token = tokens[index]
    previous = tokens[index - 1] if index > 0 else None
    # `with throw` / `with io, throw`: an effect, not an expression.
    effect_chain = index - 1
    while (
        effect_chain >= 0
        and tokens[effect_chain].type in (TokenType.COMMA, TokenType.IDENTIFIER)
        and tokens[effect_chain].location.line == token.location.line
    ):
        effect_chain -= 1
    if (
        previous is not None
        and previous.type in (TokenType.WITH, TokenType.COMMA)
        and effect_chain >= 0
        and tokens[effect_chain].type is TokenType.WITH
    ):
        _remove_throw_effect(src, index, edits)
        change(token, "removed the throw effect")
        return

    # The thrown expression runs to the end of its line, or further while a
    # bracket is open.
    last = index
    depth = 0
    for cursor in range(index + 1, len(tokens)):
        candidate = tokens[cursor]
        if candidate.type is TokenType.EOF:
            break
        if depth == 0 and candidate.location.line != tokens[last].location.line:
            break
        if candidate.type in _OPENERS:
            depth += 1
        elif candidate.type in _CLOSERS:
            depth -= 1
        last = cursor
    if last == index:
        return
    first = tokens[index + 1]
    if try_depth > 0 or first.type not in (TokenType.STRING, TokenType.FSTRING):
        manual(
            token,
            "throw: return Err(...) from a function that returns Result, "
            'or call panic("...") if this is a bug',
        )
        return
    edits.append((src.start(token), src.start(first), "panic("))
    position = src.end(tokens[last])
    edits.append((position, position, ")"))
    change(token, 'throw "..." -> panic("...")')


def _remove_throw_effect(
    src: _Source, index: int, edits: list[tuple[int, int, str]]
) -> None:
    tokens = src.tokens
    previous = tokens[index - 1]
    following = tokens[index + 1] if index + 1 < len(tokens) else None
    same_line_comma = (
        following is not None
        and following.type is TokenType.COMMA
        and following.location.line == tokens[index].location.line
    )
    if previous.type is TokenType.WITH and not same_line_comma:
        # `-> T with throw`: drop ` with throw`.
        start = src.end(tokens[index - 2])
        edits.append((start, src.end(tokens[index]), ""))
    elif previous.type is TokenType.WITH:
        # `with throw, io`: drop `throw, `.
        assert following is not None
        edits.append((src.start(tokens[index]), src.start(tokens[index + 2]), ""))
    else:
        # `with io, throw`: drop `, throw`.
        edits.append((src.end(tokens[index - 2]), src.end(tokens[index]), ""))


def _wrap(text: str, simple: bool) -> str:
    return text if simple else f"({text})"


_REMOVED_REWRITES: dict[str, Callable[[list[str], list[bool]], str | None]] = {
    "square": lambda args, simple: (
        f"({args[0]} * {args[0]})" if len(args) == 1 and simple[0] else None
    ),
    "is_positive": lambda args, simple: (
        f"({_wrap(args[0], simple[0])} > 0)" if len(args) == 1 else None
    ),
    "is_sorted": lambda args, simple: (
        f"({args[0]} == sort({args[0]}))" if len(args) == 1 and simple[0] else None
    ),
    "is_permutation": lambda args, simple: (
        f"(sort({args[0]}) == sort({args[1]}))" if len(args) == 2 else None
    ),
    "is_numeric_string": lambda args, simple: (
        f"is_some(parse_int({args[0]}))" if len(args) == 1 else None
    ),
}


def _apply(source: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply non-overlapping ``(start, end, text)`` edits."""
    out: list[str] = []
    position = 0
    for start, end, text in sorted(edits, key=lambda edit: (edit[0], edit[1])):
        if start < position:
            continue  # overlapping edit: keep the first
        out.append(source[position:start])
        out.append(text)
        position = end
    out.append(source[position:])
    return "".join(out)
