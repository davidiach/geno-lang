"""
Geno Parser
===============

Recursive descent parser for the Geno language.
Produces an Abstract Syntax Tree from a token stream.
"""

from __future__ import annotations

from .ast_nodes import (
    AssertStatement,
    Definition,
    EnsuresClause,
    ExampleClause,
    Expression,
    FunctionDef,
    ImplDef,
    ImportStatement,
    Parameter,
    Program,
    RequiresClause,
    SpecBlock,
    Statement,
    TestBlock,
    TraitDef,
    TraitMethodSig,
    TupleExpr,
)
from .parser_base import (
    ParseError,
    ParseErrors,
)
from .parser_expressions import ExpressionParserMixin
from .parser_patterns import PatternParserMixin
from .parser_statements import StatementParserMixin
from .parser_types import TypeParserMixin
from .tokens import TokenType, token_type_to_str


class Parser(
    ExpressionParserMixin,
    StatementParserMixin,
    TypeParserMixin,
    PatternParserMixin,
):
    """
    Recursive descent parser for Geno.

    Implements an LL(2) parser that converts a token stream into an AST.
    Features comprehensive error messages with source locations.
    Supports error recovery to report multiple errors in a single pass.

    Example:
        parser = Parser(tokens)
        program = parser.parse_program()
    """

    # =========================================================================
    # Program and Definitions
    # =========================================================================

    def parse_program(self) -> Program:
        """
        Parse a complete program.

        Uses error recovery to continue parsing after errors,
        collecting all errors for reporting at the end.
        """
        location = self._current().location
        definitions: list[Definition] = []

        while not self._check(TokenType.EOF):
            try:
                definitions.append(self._parse_definition())
            except ParseError as e:
                self._record_error(e)
                self._synchronize_to_definition()

        # If we collected any errors, attach partial results and raise
        if self.errors:
            partial = Program(location=location, definitions=definitions)
            if len(self.errors) == 1:
                self.errors[0].partial_program = partial
                raise self.errors[0]
            err = ParseErrors(self.errors)
            err.partial_program = partial
            raise err

        return Program(location=location, definitions=definitions)

    def _parse_definition(self) -> Definition:
        """Parse a top-level definition (func, type, trait, impl, export, or import)."""
        # Check for @untested annotation
        untested_reason = None
        if self._check(TokenType.AT):
            untested_reason = self._parse_untested_annotation()

        # Check for export keyword
        is_exported = False
        if self._check(TokenType.EXPORT):
            if untested_reason is not None:
                raise self._error("@untested must come after 'export', not before")
            self._advance()  # consume 'export'
            is_exported = True
            # Allow @untested after export
            if self._check(TokenType.AT):
                untested_reason = self._parse_untested_annotation()

        if self._check(TokenType.ASYNC):
            self._advance()  # consume 'async'
            defn = self._parse_function_def(is_async=True)
            defn.untested_reason = untested_reason
            defn.exported = is_exported
            return defn
        if self._check(TokenType.FUNC):
            defn = self._parse_function_def()
            defn.untested_reason = untested_reason
            defn.exported = is_exported
            return defn
        elif self._check(TokenType.TYPE):
            if untested_reason is not None:
                raise self._error("@untested annotation is only valid on functions")
            type_defn = self._parse_type_def()
            type_defn.exported = is_exported
            return type_defn
        elif self._check(TokenType.IMPORT):
            if untested_reason is not None:
                raise self._error("@untested annotation is only valid on functions")
            if is_exported:
                raise self._error("'export' cannot be used with 'import'")
            return self._parse_import()
        elif self._check(TokenType.TRAIT):
            if untested_reason is not None:
                raise self._error("@untested annotation is only valid on functions")
            if is_exported:
                raise self._error("'export' on traits is not yet supported")
            return self._parse_trait_def()
        elif self._check(TokenType.IMPL):
            if untested_reason is not None:
                raise self._error("@untested annotation is only valid on functions")
            if is_exported:
                raise self._error("'export' cannot be used with 'impl'")
            return self._parse_impl_def()
        elif self._check(TokenType.TEST):
            if untested_reason is not None:
                raise self._error("@untested annotation is only valid on functions")
            if is_exported:
                raise self._error("'export' cannot be used with 'test'")
            return self._parse_test_block()
        else:
            raise self._error(
                f"Expected 'func', 'type', 'trait', 'impl', 'test', 'export', or 'import', got {token_type_to_str(self._current().type)}"
            )

    def _parse_untested_annotation(self) -> str:
        """Parse: @untested("reason string")"""
        self._expect(TokenType.AT)
        name = self._expect(TokenType.IDENTIFIER, "Expected annotation name after '@'")
        if name.value != "untested":
            raise self._error(
                f"Unknown annotation '@{name.value}', expected '@untested'"
            )
        self._expect(TokenType.LPAREN, "Expected '(' after '@untested'")
        reason = self._expect(TokenType.STRING, "Expected reason string in @untested()")
        if not reason.value:
            raise self._error("@untested reason string must not be empty")
        self._expect(TokenType.RPAREN, "Expected ')' after reason string")
        return str(reason.value)

    def _parse_import(self) -> ImportStatement:
        """Parse: import ModuleName [as Alias]"""
        location = self._current().location
        self._expect(TokenType.IMPORT)
        name = self._expect(
            TokenType.TYPE_IDENTIFIER, "Expected module name (PascalCase)"
        )
        alias = None
        if self._check(TokenType.IDENTIFIER) and self._current().value == "as":
            self._advance()  # consume 'as'
            alias_tok = self._expect(
                TokenType.TYPE_IDENTIFIER, "Expected alias name (PascalCase) after 'as'"
            )
            alias = alias_tok.value
        return ImportStatement(location=location, module_name=name.value, alias=alias)

    def _parse_test_block(self) -> TestBlock:
        """Parse: test "name" ... end test"""
        location = self._current().location
        self._expect(TokenType.TEST)
        name_token = self._expect(TokenType.STRING, "Expected test name string")
        name = name_token.value

        body = self._parse_test_body()

        self._expect(TokenType.END, "Expected 'end' to close test block")
        self._expect(TokenType.TEST, "Expected 'test' after 'end'")

        return TestBlock(location=location, name=name, body=body)

    def _parse_test_body(self) -> list[Statement]:
        """Parse statements inside a test block, including assert statements."""
        statements: list[Statement] = []
        while not self._check(TokenType.END, TokenType.EOF):
            if self._check(TokenType.ASSERT):
                statements.append(self._parse_assert_statement())
            else:
                statements.append(self._parse_statement())
        return statements

    def _parse_assert_statement(self) -> AssertStatement:
        """Parse: assert expression"""
        location = self._current().location
        self._expect(TokenType.ASSERT)
        expr = self._parse_expression()
        return AssertStatement(location=location, expression=expr)

    def _parse_trait_def(self) -> TraitDef:
        """Parse a trait definition.

        trait TraitName
            func method_name(params) -> ReturnType
            ...
        end trait
        """
        location = self._current().location
        self._expect(TokenType.TRAIT)

        name_token = self._expect(
            TokenType.TYPE_IDENTIFIER, "Expected trait name (PascalCase)"
        )
        trait_name = name_token.value

        # Parse method signatures until 'end trait'
        methods: list[TraitMethodSig] = []
        while not (self._check(TokenType.END) or self._check(TokenType.EOF)):
            method_loc = self._current().location
            self._expect(TokenType.FUNC)
            method_name_token = self._expect(
                TokenType.IDENTIFIER, "Expected method name"
            )
            method_name = method_name_token.value

            # Parameters
            self._expect(TokenType.LPAREN)
            params = self._parse_parameter_list()
            self._expect(TokenType.RPAREN)

            # Return type
            self._expect(TokenType.ARROW)
            return_type = self._parse_type()

            methods.append(
                TraitMethodSig(
                    name=method_name,
                    params=params,
                    return_type=return_type,
                    location=method_loc,
                )
            )

        # Closing: end trait
        self._expect(TokenType.END)
        self._expect(TokenType.TRAIT)

        return TraitDef(
            location=location,
            name=trait_name,
            methods=methods,
        )

    def _parse_impl_def(self) -> ImplDef:
        """Parse an impl block.

        impl TraitName for TypeName
            func method_name(params) -> ReturnType
                ...
            end func
        end impl
        """
        location = self._current().location
        self._expect(TokenType.IMPL)

        trait_name_token = self._expect(
            TokenType.TYPE_IDENTIFIER, "Expected trait name (PascalCase)"
        )
        trait_name = trait_name_token.value

        self._expect(TokenType.FOR)

        type_name_token = self._expect(
            TokenType.TYPE_IDENTIFIER, "Expected type name (PascalCase)"
        )
        target_type = type_name_token.value

        # Parse full function definitions until 'end impl'
        methods: list[FunctionDef] = []
        while not (self._check(TokenType.END) or self._check(TokenType.EOF)):
            methods.append(self._parse_function_def())

        # Closing: end impl
        self._expect(TokenType.END)
        self._expect(TokenType.IMPL)

        return ImplDef(
            location=location,
            trait_name=trait_name,
            target_type=target_type,
            methods=methods,
        )

    def _parse_function_def(self, is_async: bool = False) -> FunctionDef:
        """Parse a function definition."""
        location = self._current().location
        self._expect(TokenType.FUNC)

        name_token = self._expect(TokenType.IDENTIFIER, "Expected function name")
        name = name_token.value

        # Parameters
        self._expect(TokenType.LPAREN)
        params = self._parse_parameter_list()
        self._expect(TokenType.RPAREN)

        # Return type
        self._expect(TokenType.ARROW)
        return_type = self._parse_type()

        # Optional effect annotations: with fs, io, http
        effects: list[str] = []
        if self._match(TokenType.WITH):
            effects = self._parse_effect_list()

        # Specification block
        specs = self._parse_spec_block()

        # Body
        body = self._parse_statement_list()

        # Closing: end func [name]
        self._expect(TokenType.END)
        self._expect(TokenType.FUNC)
        closing_name = None
        if self._check(TokenType.IDENTIFIER):
            closing_name = self._advance().value
            if closing_name != name:
                raise self._error(
                    f"Function closing name '{closing_name}' doesn't match '{name}'"
                )

        return FunctionDef(
            location=location,
            name=name,
            params=params,
            return_type=return_type,
            specs=specs,
            body=body,
            closing_name=closing_name,
            is_async=is_async,
            effects=effects,
        )

    def _parse_parameter_list(self) -> list[Parameter]:
        """Parse function parameters."""
        params: list[Parameter] = []

        if self._check(TokenType.RPAREN):
            return params

        params.append(self._parse_parameter())
        while self._match(TokenType.COMMA):
            params.append(self._parse_parameter())

        # Validate: required params must come before optional params
        seen_default = False
        for p in params:
            if p.default_value is not None:
                seen_default = True
            elif seen_default:
                self._record_error(
                    ParseError(
                        f"Required parameter '{p.name}' cannot follow a parameter with a default value",
                        p.location,
                    )
                )

        return params

    def _parse_parameter(self) -> Parameter:
        """Parse a single parameter: name: Type or name: Type = default"""
        location = self._current().location
        name_token = self._expect(TokenType.IDENTIFIER, "Expected parameter name")
        self._expect(TokenType.COLON)
        param_type = self._parse_type()
        default_value = None
        if self._match(TokenType.ASSIGN):
            default_value = self._parse_expression()
        return Parameter(
            name=name_token.value,
            param_type=param_type,
            location=location,
            default_value=default_value,
        )

    def _parse_spec_block(self) -> SpecBlock:
        """Parse specification clauses (requires, ensures, example)."""
        specs = SpecBlock()

        while True:
            if self._check(TokenType.REQUIRES):
                specs.requires.append(self._parse_requires_clause())
            elif self._check(TokenType.ENSURES):
                specs.ensures.append(self._parse_ensures_clause())
            elif self._check(TokenType.EXAMPLE):
                specs.examples.append(self._parse_example_clause())
            else:
                break

        return specs

    def _parse_requires_clause(self) -> RequiresClause:
        """Parse: requires condition"""
        location = self._current().location
        self._expect(TokenType.REQUIRES)
        condition = self._parse_expression()
        return RequiresClause(location=location, condition=condition)

    def _parse_ensures_clause(self) -> EnsuresClause:
        """Parse: ensures condition"""
        location = self._current().location
        self._expect(TokenType.ENSURES)
        condition = self._parse_expression()
        return EnsuresClause(location=location, condition=condition)

    def _parse_example_clause(self) -> ExampleClause:
        """Parse: example input -> output or example input1, input2, ... -> output"""
        location = self._current().location
        self._expect(TokenType.EXAMPLE)

        # Parse comma-separated input expressions until we hit ARROW
        inputs: list[Expression] = []
        inputs.append(self._parse_expression())
        while self._match(TokenType.COMMA):
            inputs.append(self._parse_expression())

        self._expect(TokenType.ARROW)
        output_expr = self._parse_expression()

        # If there's only one input, use it directly; otherwise wrap in tuple
        if len(inputs) == 1:
            input_expr = inputs[0]
        else:
            input_expr = TupleExpr(location=location, elements=inputs)

        return ExampleClause(
            location=location, input_expr=input_expr, output_expr=output_expr
        )


# =============================================================================
# Convenience Functions
# =============================================================================


def parse(source: str, filename: str = "<stdin>") -> Program:
    """
    Parse Geno source code into an AST.

    Args:
        source: Source code string
        filename: Filename for error messages

    Returns:
        Program AST node

    Raises:
        ParseError: If the source code is invalid
    """
    from .lexer import Lexer

    lexer = Lexer(source, filename)
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    return parser.parse_program()
