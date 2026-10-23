"""
Geno Parser — Base class
=========================

Contains the ParserBase class with __init__, helper methods, class constants,
and the ParseError/ParseErrors exception classes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from .diagnostics import ErrorCode, Fix, TextEdit, insert_fix
from .tokens import SourceLocation, Token, TokenType, token_type_to_str

if TYPE_CHECKING:
    from .ast_nodes import (
        Expression,
        FunctionDef,
        MatchArm,
        Parameter,
        Pattern,
        Statement,
        TypeAnnotation,
    )


class ParseError(Exception):
    """Exception raised for parsing errors."""

    def __init__(
        self, message: str, location: SourceLocation, error_code=None, fixes=()
    ):
        self.message = message
        self.location = location
        self.error_code = error_code
        # Edits that resolve the error, when the correction is unambiguous
        # (geno.diagnostics.Fix).
        self.fixes = tuple(fixes)
        self.partial_program: object | None = (
            None  # Set by parse_program for LSP recovery
        )
        super().__init__(f"{location}: {message}")


class ParseErrors(Exception):
    """Exception containing multiple parse errors."""

    def __init__(self, errors: list[ParseError]):
        self.errors = errors
        self.partial_program: object | None = (
            None  # Set by parse_program for LSP recovery
        )
        messages = [str(e) for e in errors]
        super().__init__("\n".join(messages))

    def __str__(self) -> str:
        return "\n".join(str(e) for e in self.errors)


T = TypeVar("T")


# Geno 0.6 removed exceptions (proposal 0009). The keywords stay reserved so
# that 0.5 code gets one error naming the replacement instead of a cascade.
REMOVED_THROW = (
    "'throw' was removed in Geno 0.6. Return Err(...) from a function that "
    'returns Result, or call panic("...") for a bug'
)
REMOVED_TRY = (
    "'try'/'catch' was removed in Geno 0.6. Call a function that returns "
    "Result and handle Err with match, or pass it on with ?"
)
REMOVED_THROW_EFFECT = (
    "The 'throw' effect was removed in Geno 0.6; delete it from the effect list"
)


# The keyword that must follow an `if` condition or a loop header.
_CLAUSE_KEYWORDS = {TokenType.THEN: "then", TokenType.DO: "do"}


class ParserBase:
    """
    Base class for the Geno parser.

    Provides token navigation helpers, error handling, and recovery logic.
    """

    # Token types that mark recovery points (start of new definitions/statements)
    RECOVERY_TOKENS = {
        TokenType.FUNC,
        TokenType.TYPE,
        TokenType.IMPORT,
        TokenType.TRAIT,
        TokenType.IMPL,
        TokenType.TEST,
        TokenType.END,
        TokenType.EOF,
    }

    MAX_NESTING_DEPTH = 50
    _INFIX_PRECEDENCE = {
        TokenType.EQ: 1,
        TokenType.NEQ: 1,
        TokenType.LT: 1,
        TokenType.GT: 1,
        TokenType.LTE: 1,
        TokenType.GTE: 1,
        TokenType.CARET: 2,
        TokenType.AMPERSAND: 3,
        TokenType.LSHIFT: 4,
        TokenType.RSHIFT: 4,
        TokenType.PLUS: 5,
        TokenType.MINUS: 5,
        TokenType.STAR: 6,
        TokenType.SLASH: 6,
        TokenType.PERCENT: 6,
        TokenType.DOUBLESTAR: 7,
    }
    _INFIX_OPERATOR_TEXT = {
        TokenType.EQ: "==",
        TokenType.NEQ: "!=",
        TokenType.LT: "<",
        TokenType.GT: ">",
        TokenType.LTE: "<=",
        TokenType.GTE: ">=",
        TokenType.CARET: "^",
        TokenType.AMPERSAND: "&",
        TokenType.LSHIFT: "<<",
        TokenType.RSHIFT: ">>",
        TokenType.PLUS: "+",
        TokenType.MINUS: "-",
        TokenType.STAR: "*",
        TokenType.SLASH: "/",
        TokenType.PERCENT: "%",
        TokenType.DOUBLESTAR: "**",
    }
    _COMPARISON_TOKENS = frozenset(
        {
            TokenType.EQ,
            TokenType.NEQ,
            TokenType.LT,
            TokenType.GT,
            TokenType.LTE,
            TokenType.GTE,
        }
    )
    _RIGHT_ASSOCIATIVE_TOKENS = frozenset({TokenType.DOUBLESTAR})
    _LOGICAL_PRECEDENCE = {
        TokenType.OR: 1,
        TokenType.AND: 2,
    }
    _LOGICAL_OPERATOR_TEXT = {
        TokenType.OR: "or",
        TokenType.AND: "and",
    }

    def __init__(self, tokens: list[Token], source: str | None = None):
        """
        Initialize the parser.

        Args:
            tokens: List of tokens from the lexer (must end with EOF)
            source: The text the tokens came from, when known; some errors
                then carry a fix placed relative to it
        """
        self._source_lines = source.split("\n") if source is not None else None
        self.tokens = tokens
        self._tokens_len = len(tokens)
        self.pos = 0
        self._eof_token = tokens[-1]
        self._current_token = tokens[0]
        self._current_type = self._current_token.type
        self.errors: list[ParseError] = []
        self._panic_mode = False
        self._nesting_depth = 0

    def _current(self) -> Token:
        """Get the current token."""
        return self._current_token

    def _peek(self, offset: int = 0) -> Token:
        """Look ahead at a token without consuming it."""
        pos = self.pos + offset
        tokens = self.tokens
        if pos >= self._tokens_len:
            return self._eof_token
        return tokens[pos]

    def _advance(self) -> Token:
        """Consume and return the current token."""
        token = self._current_token
        pos = self.pos
        if pos < self._tokens_len - 1:  # Don't advance past EOF
            pos += 1
            self.pos = pos
            next_token = self.tokens[pos]
            self._current_token = next_token
            self._current_type = next_token.type
        return token

    def _check(self, *types: TokenType) -> bool:
        """Check if current token matches any of the given types."""
        current_type = self._current_type
        if len(types) == 1:
            return current_type is types[0]
        if len(types) == 2:
            return current_type is types[0] or current_type is types[1]
        if len(types) == 3:
            return (
                current_type is types[0]
                or current_type is types[1]
                or current_type is types[2]
            )
        return current_type in types

    def _match(self, *types: TokenType) -> Token | None:
        """Consume token if it matches, return it or None."""
        token = self._current_token
        current_type = self._current_type
        if len(types) == 1:
            matches = current_type is types[0]
        elif len(types) == 2:
            matches = current_type is types[0] or current_type is types[1]
        elif len(types) == 3:
            matches = (
                current_type is types[0]
                or current_type is types[1]
                or current_type is types[2]
            )
        else:
            matches = current_type in types
        if matches:
            pos = self.pos
            if pos < self._tokens_len - 1:
                pos += 1
                self.pos = pos
                next_token = self.tokens[pos]
                self._current_token = next_token
                self._current_type = next_token.type
            return token
        return None

    def _expect(self, token_type: TokenType, message: str | None = None) -> Token:
        """Consume token of expected type or raise error."""
        if self._check(token_type):
            return self._advance()
        if message is None:
            message = f"Expected {token_type_to_str(token_type)}, got {token_type_to_str(self._current().type)}"
        fixes: tuple[Fix, ...] = ()
        keyword = _CLAUSE_KEYWORDS.get(token_type)
        if (
            keyword is not None
            and self.pos > 0
            and self._current().location.line > self.tokens[self.pos - 1].location.line
        ):
            # `if c` / `while c` / `for x in xs` missing its `then` or `do`.
            end = self._line_end(self.tokens[self.pos - 1].location.line)
            if end is not None:
                fixes = (insert_fix(f"Add '{keyword}'", [(end, f" {keyword}")]),)
        raise ParseError(
            message,
            self._current().location,
            ErrorCode.PARSE_EXPECTED_TOKEN,
            fixes,
        )

    def _reject_let_mut(self, let_token: Token) -> None:
        """`let mut x = ...` is Rust; Geno declares a mutable binding with `var`."""
        current = self._current()
        if not (
            current.type is TokenType.IDENTIFIER
            and current.value == "mut"
            and self._peek(1).type is TokenType.IDENTIFIER
        ):
            return
        start, mut = let_token.location, current.location
        raise ParseError(
            "Geno has no 'let mut'; declare a mutable variable with 'var'",
            start,
            ErrorCode.PARSE_INVALID_SYNTAX,
            (
                Fix(
                    "Use 'var'",
                    (
                        TextEdit(
                            start.line, start.column, mut.line, mut.column + 3, "var"
                        ),
                    ),
                ),
            ),
        )

    def _line_end(self, line: int) -> SourceLocation | None:
        """Where the code on ``line`` ends, before any trailing comment."""
        if self._source_lines is None or not 0 < line <= len(self._source_lines):
            return None
        text = self._source_lines[line - 1]
        in_string = False
        end = len(text)
        index = 0
        while index < len(text):
            char = text[index]
            if in_string:
                if char == "\\":
                    index += 1
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif text.startswith("//", index):
                end = index
                break
            index += 1
        return SourceLocation(
            line, len(text[:end].rstrip()) + 1, self._eof_token.location.filename
        )

    def _error(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.PARSE_INVALID_SYNTAX,
    ) -> ParseError:
        """Create a categorized parse error at the current location."""
        return ParseError(message, self._current().location, error_code)

    def _record_error(self, error: ParseError) -> None:
        """Record an error for later reporting."""
        # Avoid duplicate errors at the same location
        if not self.errors or self.errors[-1].location != error.location:
            self.errors.append(error)
        self._panic_mode = True

    def _synchronize_to_definition(self) -> None:
        """Synchronize to the start of the next top-level definition."""
        self._panic_mode = False

        while not self._check(TokenType.EOF):
            # `end func` closes the definition that failed; stopping at its
            # `func` would report "Expected function name" as a second error.
            if self._current().type in {
                TokenType.FUNC,
                TokenType.TYPE,
                TokenType.IMPORT,
                TokenType.TRAIT,
                TokenType.IMPL,
                TokenType.TEST,
            } and (
                self.pos == 0 or self.tokens[self.pos - 1].type is not TokenType.END
            ):
                return
            self._advance()

    # Cross-mixin parse hooks. Concrete implementations live in the parser
    # mixins / Parser class, but declaring them here keeps mypy aware that
    # every parser instance exposes the full parsing surface.

    def _parse_expression(self) -> Expression:
        raise NotImplementedError

    def _parse_statement_list(self) -> list[Statement]:
        raise NotImplementedError

    def _parse_type(self) -> TypeAnnotation:
        raise NotImplementedError

    def _parse_pattern(self) -> Pattern:
        raise NotImplementedError

    def _parse_arm_patterns(self) -> list[Pattern]:
        raise NotImplementedError

    @staticmethod
    def _or_pattern_arms(
        patterns: list[Pattern],
        guard: Expression | None,
        body: list[Statement],
        location: SourceLocation,
    ) -> list[MatchArm]:
        raise NotImplementedError

    def _parse_parameter_list(
        self, *, allow_inferred_types: bool = False
    ) -> list[Parameter]:
        raise NotImplementedError

    def _parse_effect_list(self) -> list[str]:
        """Parse a comma-separated list of effect names."""
        effects: list[str] = []
        while True:
            tok = self._current()
            if tok.type is TokenType.THROW:
                raise self._error(REMOVED_THROW_EFFECT, ErrorCode.PARSE_REMOVED_SYNTAX)
            if tok.type is not TokenType.IDENTIFIER:
                after = "','" if effects else "'with'"
                raise self._error(f"Expected effect name after {after}")
            self._advance()
            effects.append(tok.value)
            if not self._match(TokenType.COMMA):
                return effects

    def _parse_function_def(self, is_async: bool = False) -> FunctionDef:
        raise NotImplementedError
