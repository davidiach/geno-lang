"""
Geno Parser — Type parsing mixin
==================================

Contains TypeParserMixin with type definition and type annotation parsing methods.
"""

from __future__ import annotations

from .ast_nodes import (
    FunctionType,
    SimpleType,
    TypeAlias,
    TypeAnnotation,
    TypeDef,
    TypeVariant,
)
from .parser_base import ParseError, ParserBase
from .tokens import TokenType, token_type_to_str


class TypeParserMixin(ParserBase):
    """Mixin providing type parsing methods for the Parser."""

    def _parse_type_def(self) -> TypeAlias | TypeDef:
        """Parse a type definition or type alias."""
        location = self._current().location
        self._expect(TokenType.TYPE)

        name_token = self._expect(TokenType.TYPE_IDENTIFIER, "Expected type name")
        name = name_token.value

        # Optional type parameters
        type_params: list[str] = []
        if self._match(TokenType.LBRACKET):
            type_params.append(self._expect(TokenType.TYPE_IDENTIFIER).value)
            while self._match(TokenType.COMMA):
                type_params.append(self._expect(TokenType.TYPE_IDENTIFIER).value)
            self._expect(TokenType.RBRACKET)

        self._expect(TokenType.ASSIGN)

        # Disambiguate: type alias vs algebraic data type
        # Alias if RHS starts with '(' (function type) or 'TypeId[' (parameterized type)
        if self._check(TokenType.LPAREN):
            # Function type: type Predicate = (Int) -> Bool
            target_type = self._parse_type()
            return TypeAlias(
                location=location,
                name=name,
                type_params=type_params,
                target_type=target_type,
            )
        if (
            self._check(TokenType.TYPE_IDENTIFIER)
            and self._peek(1).type == TokenType.LBRACKET
        ):
            # Parameterized type: type Coord = Tuple[Int, Int]
            target_type = self._parse_type()
            return TypeAlias(
                location=location,
                name=name,
                type_params=type_params,
                target_type=target_type,
            )
        if (
            self._check(TokenType.TYPE_IDENTIFIER)
            and self._peek(1).type != TokenType.LPAREN
            and self._peek(1).type != TokenType.BAR
        ):
            # Bare type alias: type Name = String
            target_type = self._parse_type()
            return TypeAlias(
                location=location,
                name=name,
                type_params=type_params,
                target_type=target_type,
            )

        # Otherwise: algebraic data type with variants
        variants = [self._parse_type_variant()]
        while self._match(TokenType.BAR):
            variants.append(self._parse_type_variant())

        return TypeDef(
            location=location,
            name=name,
            type_params=type_params,
            variants=variants,
        )

    def _parse_type_variant(self) -> TypeVariant:
        """Parse a type variant: Name or Name(field: Type, ...)"""
        location = self._current().location
        name_token = self._expect(TokenType.TYPE_IDENTIFIER, "Expected variant name")
        name = name_token.value

        fields: list[tuple[str, TypeAnnotation]] = []
        if self._match(TokenType.LPAREN):
            if not self._check(TokenType.RPAREN):
                # Parse first field
                field_name = self._expect(TokenType.IDENTIFIER).value
                self._expect(TokenType.COLON)
                field_type = self._parse_type()
                fields.append((field_name, field_type))

                while self._match(TokenType.COMMA):
                    field_name = self._expect(TokenType.IDENTIFIER).value
                    self._expect(TokenType.COLON)
                    field_type = self._parse_type()
                    fields.append((field_name, field_type))

            self._expect(TokenType.RPAREN)

        return TypeVariant(name=name, fields=fields, location=location)

    def _parse_type(self) -> TypeAnnotation:
        """Parse a type annotation."""
        self._nesting_depth += 1
        try:
            if self._nesting_depth > self.MAX_NESTING_DEPTH:
                raise ParseError(
                    f"Type annotation nesting too deep (>{self.MAX_NESTING_DEPTH})",
                    self._current().location,
                )
            return self._parse_type_unchecked()
        finally:
            self._nesting_depth -= 1

    def _parse_type_unchecked(self) -> TypeAnnotation:
        """Parse a type annotation after depth has already been checked."""
        location = self._current().location

        # Function type: (T1, T2) -> R  or  Tuple type: (T1, T2)
        if self._check(TokenType.LPAREN):
            return self._parse_paren_type()

        # Simple type: Name or Name[T1, T2]
        if self._check(TokenType.TYPE_IDENTIFIER):
            name = self._advance().value
            type_params: list[TypeAnnotation] = []

            if self._match(TokenType.LBRACKET):
                type_params.append(self._parse_type())
                while self._match(TokenType.COMMA):
                    type_params.append(self._parse_type())
                self._expect(TokenType.RBRACKET)

            return SimpleType(location=location, name=name, type_params=type_params)

        raise self._error(
            f"Expected type annotation (e.g., Int, String, List[T], or a user-defined type), "
            f"got {token_type_to_str(self._current().type)}"
        )

    def _parse_paren_type(self) -> TypeAnnotation:
        """Parse (T1, T2) -> R (function type) or (T1, T2) (tuple type)."""
        location = self._current().location
        self._expect(TokenType.LPAREN)

        inner_types: list[TypeAnnotation] = []
        if not self._check(TokenType.RPAREN):
            inner_types.append(self._parse_type())
            while self._match(TokenType.COMMA):
                inner_types.append(self._parse_type())

        self._expect(TokenType.RPAREN)

        if self._match(TokenType.ARROW):
            # Function type: (T1, T2) -> R [with effect1, effect2]
            return_type = self._parse_type()
            effects: list[str] = []
            if self._match(TokenType.WITH):
                effects = self._parse_effect_list()
            return FunctionType(
                location=location,
                param_types=inner_types,
                return_type=return_type,
                effects=effects,
            )

        # Tuple type: (T1, T2) — desugar to Tuple[T1, T2]
        return SimpleType(location=location, name="Tuple", type_params=inner_types)
