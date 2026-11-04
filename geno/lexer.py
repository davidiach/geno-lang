"""
Geno Lexer
==============

Lexical analyzer that converts source code into a stream of tokens.
"""

import re
from collections.abc import Sequence
from typing import Iterator

from .diagnostics import ErrorCode, Fix, replace_fix
from .tokens import KEYWORDS, SourceLocation, Token, TokenType

_WHITESPACE = " \t\r\n"
_STRING_ESCAPE_MAP = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}
_NUMBER_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SINGLE_CHAR_TOKENS = {
    "+": TokenType.PLUS,
    "-": TokenType.MINUS,
    "*": TokenType.STAR,
    "/": TokenType.SLASH,
    "%": TokenType.PERCENT,
    "<": TokenType.LT,
    ">": TokenType.GT,
    "=": TokenType.ASSIGN,
    "|": TokenType.BAR,
    "&": TokenType.AMPERSAND,
    "^": TokenType.CARET,
    "~": TokenType.TILDE,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    "[": TokenType.LBRACKET,
    "]": TokenType.RBRACKET,
    "{": TokenType.LBRACE,
    "}": TokenType.RBRACE,
    ",": TokenType.COMMA,
    ":": TokenType.COLON,
    ";": TokenType.SEMICOLON,
    ".": TokenType.DOT,
    "_": TokenType.UNDERSCORE,
    "?": TokenType.QUESTION,
    "@": TokenType.AT,
}


def _is_ascii_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _is_ascii_alpha(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def _is_ascii_alnum(ch: str) -> bool:
    return _is_ascii_digit(ch) or _is_ascii_alpha(ch)


# How deeply string literals may nest inside f-string interpolations, as in
# f"{f"{f"..."}"}": deeper nesting is an error rather than a recursion limit.
MAX_FSTRING_NESTING = 50


def _starts_fstring(chars: list[str]) -> bool:
    """Whether a quote written after ``chars`` opens an f-string (``f"``)."""
    if not chars or chars[-1] != "f":
        return False
    return len(chars) < 2 or not (chars[-2].isalnum() or chars[-2] == "_")


def string_literal_end(text: str, start: int) -> int:
    """The index just past the string literal whose opening quote is
    ``text[start]``, or ``len(text)`` when it does not close in ``text``.

    The quote opens an f-string when an ``f`` starting a token precedes it;
    a string literal inside one of its interpolations does not close it.
    Triple-quoted strings are not recognized.
    """
    fstring = _starts_fstring(list(text[max(0, start - 2) : start]))
    index = start + 1
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
        elif char == '"':
            return index + 1
        elif fstring and char == "{":
            index += 1
            while index < length and text[index] != "}":
                if text[index] == '"':
                    index = string_literal_end(text, index)
                else:
                    index += 1
            index += 1
        else:
            index += 1
    return length


class LexerError(Exception):
    """Exception raised for lexical analysis errors."""

    def __init__(
        self,
        message: str,
        location: SourceLocation,
        error_code=None,
        fixes: Sequence[Fix] = (),
    ):
        self.message = message
        self.location = location
        self.fixes = tuple(fixes)
        if error_code is None:
            if message.startswith("Unterminated block comment"):
                error_code = ErrorCode.LEX_UNTERMINATED_COMMENT
            elif message.startswith("Unterminated"):
                error_code = ErrorCode.LEX_UNTERMINATED_STRING
            elif message.startswith("Invalid escape sequence"):
                error_code = ErrorCode.LEX_INVALID_ESCAPE
        self.error_code = error_code
        super().__init__(f"{location}: {message}")


# Other languages' spellings of an operator, arrow or comment: what was
# written, and how Geno writes it.
_FOREIGN_OPERATORS = {
    "&&": ("&&", "and"),
    "||": ("||", "or"),
    "!": ("!", "not "),
    "=>": ("=>", "->"),
    "#": ("#", "//"),
}


class Lexer:
    """
    Lexical analyzer for Geno source code.

    Converts a string of source code into a sequence of tokens.
    Handles all Geno lexical elements including:
    - Keywords and identifiers
    - Integer and float literals
    - String literals (single and multi-line)
    - Operators and delimiters
    - Comments (single-line // and multi-line /* */)

    Example:
        lexer = Lexer("let x: Int = 5")
        tokens = list(lexer.tokenize())
    """

    def __init__(self, source: str, filename: str = "<stdin>"):
        """
        Initialize the lexer.

        Args:
            source: The source code to tokenize
            filename: Name of the source file (for error messages)
        """
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.column = 1
        self._source_len = len(source)
        self.tokens: list[Token] = []

    def _current_location(self) -> SourceLocation:
        """Get the current source location."""
        return SourceLocation(self.line, self.column, self.filename)

    def _peek(self, offset: int = 0) -> str:
        """Look at a character without consuming it."""
        pos = self.pos + offset
        if pos >= self._source_len:
            return "\0"
        return self.source[pos]

    def _advance(self) -> str:
        """Consume and return the current character."""
        if self.pos >= self._source_len:
            return "\0"
        char = self.source[self.pos]
        self.pos += 1
        if char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char

    def _read_string(self) -> Token:
        """Read a string literal."""
        start_loc = self._current_location()
        self._advance()  # consume opening quote

        # Check for multi-line string """
        if self._peek() == '"' and self._peek(1) == '"':
            self._advance()
            self._advance()
            return self._read_multiline_string(start_loc)

        chars: list[str] = []
        while True:
            char = self._peek()
            if char == "\0" or char == "\n":
                raise LexerError("Unterminated string literal", start_loc)
            if char == '"':
                self._advance()
                break
            if char == "\\":
                self._advance()
                escaped = self._advance()
                if escaped in _STRING_ESCAPE_MAP:
                    chars.append(_STRING_ESCAPE_MAP[escaped])
                else:
                    raise LexerError(
                        f"Invalid escape sequence: \\{escaped}",
                        self._current_location(),
                    )
            else:
                chars.append(self._advance())

        return Token(TokenType.STRING, "".join(chars), start_loc)

    def _read_multiline_string(self, start_loc: SourceLocation) -> Token:
        """Read a multi-line string literal enclosed in triple quotes."""
        chars: list[str] = []
        while True:
            if self._peek() == "\0":
                raise LexerError("Unterminated multi-line string", start_loc)
            if self._peek() == '"' and self._peek(1) == '"' and self._peek(2) == '"':
                self._advance()
                self._advance()
                self._advance()
                break
            chars.append(self._advance())

        return Token(TokenType.STRING, "".join(chars), start_loc)

    def _read_fstring(self) -> Token:
        """Read an f-string literal: f"text {expr} text".

        The token's value is ``(text, locations, spans)``: the text with its
        escapes decoded and each interpolation's source copied unchanged,
        where each interpolation starts in the source, and the
        ``(start, end)`` of each interpolation's expression within the text.
        """
        start_loc = self._current_location()
        self._advance()  # consume opening "

        chars: list[str] = []
        interp_locations: list[SourceLocation] = []
        interp_spans: list[tuple[int, int]] = []
        in_expr = False
        expr_start = 0
        while True:
            char = self._peek()
            if char == "\0":
                if in_expr:
                    location = interp_locations[-1] if interp_locations else start_loc
                    raise LexerError("Unterminated f-string expression", location)
                raise LexerError("Unterminated f-string literal", start_loc)
            if char == "\n" and not in_expr:
                raise LexerError("Unterminated f-string literal", start_loc)
            if char == "{" and not in_expr:
                in_expr = True
                chars.append(self._advance())
                expr_start = len(chars)
                # Record the absolute source location of the first character of
                # the interpolation so the parser can report errors inside it at
                # their true offset rather than at the f-string's start.
                interp_locations.append(self._current_location())
                continue
            if char == "}" and in_expr:
                in_expr = False
                interp_spans.append((expr_start, len(chars)))
                chars.append(self._advance())
                continue
            if char == "{" and in_expr:
                raise LexerError(
                    "Nested braces are not allowed in f-string expressions",
                    self._current_location(),
                )
            if char == '"':
                if in_expr:
                    # A string literal in the interpolation, copied unchanged
                    # for the parser to read with the rest of the expression.
                    chars.extend(
                        self._read_string_source(
                            fstring=_starts_fstring(chars), depth=1
                        )
                    )
                    continue
                self._advance()  # consume closing "
                break
            if char == "\\" and not in_expr:
                self._advance()
                escaped = self._advance()
                if escaped in _STRING_ESCAPE_MAP:
                    chars.append(_STRING_ESCAPE_MAP[escaped])
                else:
                    raise LexerError(
                        f"Invalid escape sequence: \\{escaped}",
                        self._current_location(),
                    )
                continue
            chars.append(self._advance())

        return Token(
            TokenType.FSTRING,
            ("".join(chars), tuple(interp_locations), tuple(interp_spans)),
            start_loc,
        )

    def _read_string_source(self, *, fstring: bool, depth: int) -> str:
        """Read a one-line string literal inside an f-string interpolation and
        return its source text unchanged, quotes included.

        A nested f-string's own interpolations may hold string literals too;
        ``depth`` counts how many interpolations this one is inside.
        """
        start_loc = self._current_location()
        if depth > MAX_FSTRING_NESTING:
            raise LexerError(
                f"F-strings are nested too deeply (more than {MAX_FSTRING_NESTING} "
                "levels); build the inner strings with let first",
                start_loc,
            )
        chars = [self._advance()]  # opening quote
        in_expr = False
        while True:
            char = self._peek()
            if char == "\0" or char == "\n":
                raise LexerError("Unterminated string literal", start_loc)
            if char == "\\" and not in_expr:
                chars.append(self._advance())
                if self._peek() in ("\0", "\n"):
                    raise LexerError("Unterminated string literal", start_loc)
                chars.append(self._advance())
                continue
            if fstring and char == "{" and not in_expr:
                in_expr = True
            elif fstring and char == "}" and in_expr:
                in_expr = False
            elif char == '"':
                if not in_expr:
                    chars.append(self._advance())
                    return "".join(chars)
                chars.extend(
                    self._read_string_source(
                        fstring=_starts_fstring(chars), depth=depth + 1
                    )
                )
                continue
            chars.append(self._advance())

    def tokenize(self) -> list[Token]:
        """
        Tokenize the entire source code.

        Returns:
            List of tokens, ending with EOF token.

        Raises:
            LexerError: If an invalid token is encountered.
        """
        tokens: list[Token] = []
        append_token = tokens.append

        source = self.source
        source_len = self._source_len
        filename = self.filename
        identifier_match = _IDENTIFIER_RE.match
        keyword_get = KEYWORDS.get
        number_match = _NUMBER_RE.match
        single_char_tokens = _SINGLE_CHAR_TOKENS
        token_cls = Token
        source_location_cls = SourceLocation
        pos = self.pos
        line = self.line
        column = self.column

        read_fstring = self._read_fstring
        read_string = self._read_string

        while pos < source_len:
            char = source[pos]
            while char == " " or char == "\t" or char == "\r" or char == "\n":
                if char == "\n":
                    line += 1
                    column = 1
                else:
                    column += 1
                pos += 1
                if pos >= source_len:
                    self.pos = pos
                    self.line = line
                    self.column = column
                    append_token(
                        token_cls(
                            TokenType.EOF,
                            None,
                            source_location_cls(line, column, filename),
                        )
                    )
                    return tokens
                char = source[pos]

            next_pos = pos + 1
            next_char = source[next_pos] if next_pos < source_len else "\0"

            # Skip comments.
            if char == "/" and next_char == "/":
                newline_pos = source.find("\n", next_pos + 1)
                if newline_pos == -1:
                    column += source_len - pos
                    pos = source_len
                    break
                column += newline_pos - pos
                pos = newline_pos
                continue
            if char == "/" and next_char == "*":
                start_loc = source_location_cls(line, column, filename)
                pos += 2
                column += 2
                while True:
                    if pos >= source_len:
                        raise LexerError("Unterminated block comment", start_loc)
                    char = source[pos]
                    if char == "*" and pos + 1 < source_len and source[pos + 1] == "/":
                        pos += 2
                        column += 2
                        break
                    if char == "\n":
                        line += 1
                        column = 1
                        pos += 1
                    else:
                        pos += 1
                        column += 1
                continue

            start_loc = source_location_cls(line, column, filename)

            # F-string literals: f"...".  Multiline f-strings are not part of
            # Geno's grammar; without this guard, f"""...""" was tokenized as
            # an empty f-string followed by an unrelated string literal.
            if char == "f" and next_char == '"':
                if source.startswith('f"""', pos):
                    raise LexerError(
                        "Triple-quoted f-strings are not supported", start_loc
                    )
                self.pos = pos + 1
                self.line = line
                self.column = column + 1
                append_token(read_fstring())
                pos = self.pos
                line = self.line
                column = self.column
                continue

            # String literals.
            if char == '"':
                self.pos = pos
                self.line = line
                self.column = column
                append_token(read_string())
                pos = self.pos
                line = self.line
                column = self.column
                continue

            # Numbers.
            if "0" <= char <= "9":
                match = number_match(source, pos)
                assert match is not None
                start_pos = pos
                text = match.group(0)
                pos = match.end()

                # A numeric literal cannot run directly into an identifier.
                # Treating ``123abc`` as two independent tokens lets the
                # parser silently reinterpret a typo as a different program.
                # Keep malformed import names tokenized separately so the
                # parser can synchronize and report later definitions too.
                adjacent_identifier = pos < source_len and (
                    _is_ascii_alpha(source[pos]) or source[pos] == "_"
                )
                follows_import = bool(tokens) and tokens[-1].type is TokenType.IMPORT
                if adjacent_identifier and not follows_import:
                    raise LexerError(
                        "Expected a separator between numeric literal and identifier",
                        source_location_cls(line, column + pos - start_pos, filename),
                    )

                int_part_end = text.find(".")
                if int_part_end == -1:
                    if len(text) > 1000:
                        raise LexerError(
                            "Integer literal too long (max 1000 digits)", start_loc
                        )
                    append_token(token_cls(TokenType.INTEGER, int(text), start_loc))
                else:
                    int_digits = int_part_end
                    frac_digits = len(text) - int_part_end - 1
                    if int_digits > 1000 or frac_digits > 1000:
                        raise LexerError(
                            "Float literal too long (max 1000 digits per part)",
                            start_loc,
                        )
                    append_token(token_cls(TokenType.FLOAT, float(text), start_loc))
                column += pos - start_pos
                continue

            # Identifiers and keywords.
            if ("a" <= char <= "z") or ("A" <= char <= "Z") or char == "_":
                match = identifier_match(source, pos)
                assert match is not None
                name = match.group(0)
                start_pos = pos
                pos = match.end()
                column += pos - start_pos

                keyword_type = keyword_get(name)
                if keyword_type is not None:
                    append_token(token_cls(keyword_type, name, start_loc))
                elif name == "_":
                    append_token(token_cls(TokenType.UNDERSCORE, name, start_loc))
                elif "A" <= name[0] <= "Z":
                    append_token(token_cls(TokenType.TYPE_IDENTIFIER, name, start_loc))
                else:
                    append_token(token_cls(TokenType.IDENTIFIER, name, start_loc))
                continue

            # Operators and delimiters.
            if char == "-" and next_char == ">":
                pos += 2
                column += 2
                append_token(token_cls(TokenType.ARROW, "->", start_loc))
                continue
            if char == "|" and next_char == ">":
                pos += 2
                column += 2
                append_token(token_cls(TokenType.PIPE, "|>", start_loc))
                continue
            if char == "=" and next_char == "=":
                pos += 2
                column += 2
                append_token(token_cls(TokenType.EQ, "==", start_loc))
                continue
            if char == "!" and next_char == "=":
                pos += 2
                column += 2
                append_token(token_cls(TokenType.NEQ, "!=", start_loc))
                continue
            if char == "*" and next_char == "*":
                pos += 2
                column += 2
                append_token(token_cls(TokenType.DOUBLESTAR, "**", start_loc))
                continue
            if char == "<" and next_char == "<":
                pos += 2
                column += 2
                append_token(token_cls(TokenType.LSHIFT, "<<", start_loc))
                continue
            if char == "<" and next_char == "=":
                pos += 2
                column += 2
                append_token(token_cls(TokenType.LTE, "<=", start_loc))
                continue
            if char == ">" and next_char == ">":
                pos += 2
                column += 2
                append_token(token_cls(TokenType.RSHIFT, ">>", start_loc))
                continue
            if char == ">" and next_char == "=":
                pos += 2
                column += 2
                append_token(token_cls(TokenType.GTE, ">=", start_loc))
                continue

            if char in "&|!=#" and (
                foreign := _FOREIGN_OPERATORS.get(char + next_char)
                or _FOREIGN_OPERATORS.get(char)
            ):
                written, geno_word = foreign
                raise LexerError(
                    f"Geno writes '{geno_word.strip()}', not '{written}'",
                    start_loc,
                    ErrorCode.LEX_UNEXPECTED_CHAR,
                    (
                        replace_fix(
                            start_loc,
                            written,
                            geno_word,
                            f"Change '{written}' to '{geno_word.strip()}'",
                        ),
                    ),
                )

            token_type = single_char_tokens.get(char)
            if token_type is not None:
                pos += 1
                column += 1
                append_token(token_cls(token_type, char, start_loc))
                continue

            raise LexerError(f"Unexpected character: {char!r}", start_loc)

        self.pos = pos
        self.line = line
        self.column = column
        append_token(
            token_cls(TokenType.EOF, None, source_location_cls(line, column, filename))
        )
        return tokens

    def tokenize_iter(self) -> Iterator[Token]:
        """
        Iterate over tokens from a cached tokenize() call.

        .. note::
            This is **not** lazy — it materializes all tokens first via
            ``tokenize()`` then yields them.  Retained for backward
            compatibility; prefer ``tokenize()`` directly.

        Yields:
            Tokens one at a time.
        """
        yield from self.tokenize()


def tokenize(source: str, filename: str = "<stdin>") -> list[Token]:
    """
    Convenience function to tokenize source code.

    Args:
        source: Source code string
        filename: Filename for error messages

    Returns:
        List of tokens
    """
    return Lexer(source, filename).tokenize()
