"""
Geno Parser — Pattern parsing mixin
======================================

Contains PatternParserMixin with pattern parsing methods.
"""

from __future__ import annotations

from .ast_nodes import (
    ConstructorPattern,
    ListPattern,
    LiteralPattern,
    Pattern,
    RestPattern,
    VariablePattern,
    WildcardPattern,
)
from .parser_base import ParseError, ParserBase
from .tokens import TokenType, token_type_to_str


class PatternParserMixin(ParserBase):
    """Mixin providing pattern parsing methods for the Parser."""

    def _parse_pattern_element(self) -> Pattern:
        """Parse a pattern element, including rest patterns (...)."""
        location = self._current().location
        # Rest pattern: ...name or ...
        if (
            self._check(TokenType.DOT)
            and self._peek(1).type == TokenType.DOT
            and self._peek(2).type == TokenType.DOT
        ):
            self._advance()  # consume first .
            self._advance()  # consume second .
            self._advance()  # consume third .
            name = None
            if self._check(TokenType.IDENTIFIER):
                name = self._advance().value
            return RestPattern(location=location, name=name)
        return self._parse_pattern()

    def _parse_pattern(self) -> Pattern:
        """Parse a pattern for matching."""
        self._nesting_depth += 1
        try:
            if self._nesting_depth > self.MAX_NESTING_DEPTH:
                raise ParseError(
                    f"Pattern nesting too deep (>{self.MAX_NESTING_DEPTH})",
                    self._current().location,
                )
            return self._parse_pattern_unchecked()
        finally:
            self._nesting_depth -= 1

    def _parse_pattern_unchecked(self) -> Pattern:
        """Parse a pattern after depth has already been checked."""
        location = self._current().location

        # Wildcard: _
        if self._match(TokenType.UNDERSCORE):
            return WildcardPattern(location=location)

        # List pattern: [], [x, ...rest]
        if self._match(TokenType.LBRACKET):
            elements: list[Pattern] = []
            if not self._check(TokenType.RBRACKET):
                elements.append(self._parse_pattern_element())
                while self._match(TokenType.COMMA):
                    elements.append(self._parse_pattern_element())
            self._expect(TokenType.RBRACKET)
            # Spec §8.6: rest pattern may appear at most once per list.
            if sum(isinstance(e, RestPattern) for e in elements) > 1:
                raise self._error(
                    "List pattern may contain at most one rest ('...') element"
                )
            return ListPattern(location=location, elements=elements)

        # Constructor pattern: SomeConstructor or SomeConstructor(p1, p2)
        if self._check(TokenType.TYPE_IDENTIFIER):
            constructor = self._advance().value
            subpatterns: list[Pattern] = []
            if self._match(TokenType.LPAREN):
                if not self._check(TokenType.RPAREN):
                    subpatterns.append(self._parse_pattern())
                    while self._match(TokenType.COMMA):
                        subpatterns.append(self._parse_pattern())
                self._expect(TokenType.RPAREN)
            return ConstructorPattern(
                location=location, constructor=constructor, subpatterns=subpatterns
            )

        # Variable pattern: x
        if self._check(TokenType.IDENTIFIER):
            name = self._advance().value
            return VariablePattern(location=location, name=name)

        # Literal patterns
        if self._check(TokenType.INTEGER):
            value = self._advance().value
            return LiteralPattern(location=location, value=value)
        if self._check(TokenType.FLOAT):
            value = self._advance().value
            return LiteralPattern(location=location, value=value)
        if self._check(TokenType.STRING):
            value = self._advance().value
            return LiteralPattern(location=location, value=value)
        if self._check(TokenType.TRUE, TokenType.FALSE):
            value = self._advance().type == TokenType.TRUE
            return LiteralPattern(location=location, value=value)

        raise self._error(
            f"Expected match pattern (e.g., a literal, variable name, constructor, or '_' wildcard), "
            f"got {token_type_to_str(self._current().type)}"
        )
