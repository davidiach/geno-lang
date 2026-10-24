"""
Geno Parser — Pattern parsing mixin
======================================

Contains PatternParserMixin with pattern parsing methods.
"""

from __future__ import annotations

import copy

from .ast_nodes import (
    ConstructorPattern,
    Expression,
    ListPattern,
    LiteralPattern,
    MatchArm,
    Pattern,
    RestPattern,
    Statement,
    TuplePattern,
    VariablePattern,
    WildcardPattern,
)
from .parser_base import ParseError, ParserBase
from .tokens import SourceLocation, TokenType, token_type_to_str


def pattern_bound_names(pattern: Pattern) -> set[str]:
    """The variable names *pattern* binds."""
    if isinstance(pattern, VariablePattern):
        return {pattern.name}
    if isinstance(pattern, RestPattern):
        return {pattern.name} if pattern.name else set()
    if isinstance(pattern, ConstructorPattern):
        children: list[Pattern] = pattern.subpatterns
    elif isinstance(pattern, (ListPattern, TuplePattern)):
        children = pattern.elements
    else:
        return set()
    names: set[str] = set()
    for child in children:
        names |= pattern_bound_names(child)
    return names


class PatternParserMixin(ParserBase):
    """Mixin providing pattern parsing methods for the Parser."""

    def _parse_arm_patterns(self) -> list[Pattern]:
        """Parse a match arm's pattern, or its `|`-separated alternatives.

        `| Red | Green -> ...` matches either; every alternative must bind
        the same names, so the arm body sees the same variables either way.
        """
        patterns = [self._parse_pattern()]
        while self._check(TokenType.BAR):
            self._advance()
            patterns.append(self._parse_pattern())
        expected = pattern_bound_names(patterns[0])
        for alternative in patterns[1:]:
            names = pattern_bound_names(alternative)
            if names != expected:
                raise ParseError(
                    "Every alternative of an or-pattern must bind the same names: "
                    f"the first binds {_describe_names(expected)}, this one binds "
                    f"{_describe_names(names)}",
                    alternative.location,
                )
        return patterns

    @staticmethod
    def _or_pattern_arms(
        patterns: list[Pattern],
        guard: Expression | None,
        body: list[Statement],
        location: SourceLocation,
    ) -> list[MatchArm]:
        """One arm per alternative, each with its own copy of guard and body."""
        arms = [
            MatchArm(pattern=patterns[0], body=body, location=location, guard=guard)
        ]
        for alternative in patterns[1:]:
            arms.append(
                MatchArm(
                    pattern=alternative,
                    body=copy.deepcopy(body),
                    location=location,
                    guard=copy.deepcopy(guard),
                )
            )
        return arms

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

        # Tuple pattern: (a, b), (x, _, z)
        if self._match(TokenType.LPAREN):
            tuple_elements: list[Pattern] = []
            if not self._check(TokenType.RPAREN):
                tuple_elements.append(self._parse_pattern())
                while self._match(TokenType.COMMA):
                    tuple_elements.append(self._parse_pattern())
            self._expect(TokenType.RPAREN)
            if len(tuple_elements) < 2:
                raise self._error(
                    "Tuple pattern needs at least two elements (e.g. '(a, b)'); "
                    "bind the whole value with a plain name instead"
                )
            return TuplePattern(location=location, elements=tuple_elements)

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


def _describe_names(names: set[str]) -> str:
    return ", ".join(f"'{name}'" for name in sorted(names)) if names else "nothing"
