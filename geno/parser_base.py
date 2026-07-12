"""
Geno Parser — Base class
=========================

Contains the ParserBase class with __init__, helper methods, class constants,
and the ParseError/ParseErrors exception classes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, TypeVar

from .tokens import SourceLocation, Token, TokenType, token_type_to_str

if TYPE_CHECKING:
    from .ast_nodes import (
        Expression,
        FunctionDef,
        Parameter,
        Pattern,
        Statement,
        TypeAnnotation,
    )


class ParseError(Exception):
    """Exception raised for parsing errors."""

    def __init__(self, message: str, location: SourceLocation, error_code=None):
        self.message = message
        self.location = location
        self.error_code = error_code
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

    def __init__(self, tokens: list[Token]):
        """
        Initialize the parser.

        Args:
            tokens: List of tokens from the lexer (must end with EOF)
        """
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
        raise ParseError(message, self._current().location)

    def _error(self, message: str) -> ParseError:
        """Create a parse error at current location."""
        return ParseError(message, self._current().location)

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
            if self._current().type in {
                TokenType.FUNC,
                TokenType.TYPE,
                TokenType.IMPORT,
                TokenType.TRAIT,
                TokenType.IMPL,
                TokenType.TEST,
            }:
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

    def _parse_parameter_list(self) -> list[Parameter]:
        raise NotImplementedError

    def _parse_effect_list(self) -> list[str]:
        """Parse a comma-separated list of effect names.

        Effect names are typically identifiers (fs, io, http, etc.) but
        'throw' is also valid and is a keyword token.
        """
        _EFFECT_TOKENS = (TokenType.IDENTIFIER, TokenType.THROW)

        effects: list[str] = []
        tok = self._current()
        if tok.type not in _EFFECT_TOKENS:
            raise self._error("Expected effect name after 'with'")
        self._advance()
        effects.append(tok.value)
        while self._match(TokenType.COMMA):
            tok = self._current()
            if tok.type not in _EFFECT_TOKENS:
                raise self._error("Expected effect name after ','")
            self._advance()
            effects.append(tok.value)
        return effects

    def _parse_function_def(self, is_async: bool = False) -> FunctionDef:
        raise NotImplementedError
