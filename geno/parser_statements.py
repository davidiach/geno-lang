"""
Geno Parser — Statement parsing mixin
=======================================

Contains StatementParserMixin with all statement parsing methods.
"""

from __future__ import annotations

from .ast_nodes import (
    AssignStatement,
    BreakStatement,
    CatchClause,
    ContinueStatement,
    Expression,
    ExpressionStatement,
    FieldAccess,
    FieldAssignStatement,
    ForStatement,
    IfStatement,
    IndexAccess,
    IndexAssignStatement,
    LetStatement,
    MatchArm,
    MatchStatement,
    ReturnStatement,
    Statement,
    TryStatement,
    TupleDestructureStatement,
    TupleExpr,
    VarStatement,
    WhileStatement,
)
from .parser_base import ParseError, ParserBase
from .tokens import SourceLocation, TokenType, token_type_to_str


class StatementParserMixin(ParserBase):
    """Mixin providing statement parsing methods for the Parser."""

    def _parse_statement_list(self) -> list[Statement]:
        """Parse statements until we hit a block terminator."""
        statements: list[Statement] = []
        while not self._check(
            TokenType.END, TokenType.ELSE, TokenType.BAR, TokenType.CATCH, TokenType.EOF
        ):
            statements.append(self._parse_statement())
        return statements

    def _parse_statement(self) -> Statement:
        """Parse a single statement."""
        if self._check(TokenType.LET):
            return self._parse_let_statement()
        elif self._check(TokenType.VAR):
            return self._parse_var_statement()
        elif self._check(
            TokenType.IF, TokenType.WHILE, TokenType.FOR, TokenType.MATCH, TokenType.TRY
        ):
            # Track nesting depth for compound statements to prevent
            # deeply nested code from causing Python RecursionError.
            self._nesting_depth += 1
            if self._nesting_depth > self.MAX_NESTING_DEPTH:
                raise ParseError(
                    f"Statement nesting too deep (>{self.MAX_NESTING_DEPTH})",
                    self._current().location,
                )
            try:
                if self._check(TokenType.IF):
                    return self._parse_if_statement()
                elif self._check(TokenType.WHILE):
                    return self._parse_while_statement()
                elif self._check(TokenType.FOR):
                    return self._parse_for_statement()
                elif self._check(TokenType.TRY):
                    return self._parse_try_statement()
                else:
                    return self._parse_match_statement()
            finally:
                self._nesting_depth -= 1
        elif self._check(TokenType.RETURN):
            return self._parse_return_statement()
        elif self._check(TokenType.BREAK):
            return self._parse_break_statement()
        elif self._check(TokenType.CONTINUE):
            return self._parse_continue_statement()
        elif (
            self._check(TokenType.IDENTIFIER) and self._peek(1).type == TokenType.ASSIGN
        ):
            return self._parse_assign_statement()
        elif self._check(TokenType.IDENTIFIER) and self._peek(1).type in (
            TokenType.LBRACKET,
            TokenType.DOT,
        ):
            return self._parse_compound_assign_or_expr()
        else:
            return self._parse_expression_statement()

    def _parse_let_statement(self) -> Statement:
        """Parse: let x: Type = value  or  let x = value  or  let (x, y): (Int, Int) = value"""
        location = self._current().location
        self._expect(TokenType.LET)
        if self._check(TokenType.LPAREN):
            return self._parse_tuple_destructure(location, mutable=False)
        name = self._expect(TokenType.IDENTIFIER).value
        type_annotation = None
        if self._match(TokenType.COLON):
            type_annotation = self._parse_type()
        self._expect(TokenType.ASSIGN)
        value = self._parse_expression()
        return LetStatement(
            location=location, name=name, type_annotation=type_annotation, value=value
        )

    def _parse_var_statement(self) -> Statement:
        """Parse: var x: Type = value  or  var x = value  or  var (x, y): (Int, Int) = value"""
        location = self._current().location
        self._expect(TokenType.VAR)
        if self._check(TokenType.LPAREN):
            return self._parse_tuple_destructure(location, mutable=True)
        name = self._expect(TokenType.IDENTIFIER).value
        type_annotation = None
        if self._match(TokenType.COLON):
            type_annotation = self._parse_type()
        self._expect(TokenType.ASSIGN)
        value = self._parse_expression()
        return VarStatement(
            location=location, name=name, type_annotation=type_annotation, value=value
        )

    def _parse_tuple_destructure(
        self, location: SourceLocation, mutable: bool
    ) -> TupleDestructureStatement:
        """Parse: (x, y, ...): (Type, Type, ...) = value"""
        self._expect(TokenType.LPAREN)
        names = [self._expect(TokenType.IDENTIFIER, "Expected variable name").value]
        while self._match(TokenType.COMMA):
            names.append(
                self._expect(TokenType.IDENTIFIER, "Expected variable name").value
            )
        self._expect(TokenType.RPAREN)
        self._expect(TokenType.COLON)
        type_annotation = self._parse_type()
        self._expect(TokenType.ASSIGN)
        value = self._parse_expression()
        return TupleDestructureStatement(
            location=location,
            names=names,
            type_annotation=type_annotation,
            value=value,
            mutable=mutable,
        )

    def _parse_assign_statement(self) -> AssignStatement:
        """Parse: x = value"""
        location = self._current().location
        name = self._expect(TokenType.IDENTIFIER).value
        self._expect(TokenType.ASSIGN)
        value = self._parse_expression()
        return AssignStatement(location=location, target=name, value=value)

    def _parse_compound_assign_or_expr(self) -> Statement:
        """Parse index/field assignment (arr[i] = v, obj.f = v) or expression statement."""
        location = self._current().location
        expr = self._parse_expression()

        if self._match(TokenType.ASSIGN):
            value = self._parse_expression()
            if isinstance(expr, IndexAccess):
                return IndexAssignStatement(
                    location=location, target=expr.target, index=expr.index, value=value
                )
            elif isinstance(expr, FieldAccess):
                return FieldAssignStatement(
                    location=location,
                    target=expr.target,
                    field_name=expr.field_name,
                    value=value,
                )
            else:
                raise self._error(
                    "Invalid assignment target: only variables, fields (x.field), "
                    "and indices (x[i]) can be assigned to"
                )

        return ExpressionStatement(location=location, expression=expr)

    def _parse_if_statement(self, consume_end: bool = True) -> IfStatement:
        """Parse: if cond then ... [else ...] end if [label]"""
        location = self._current().location
        self._expect(TokenType.IF)
        condition = self._parse_expression()
        self._expect(TokenType.THEN)
        then_body = self._parse_statement_list()

        else_body: list[Statement] = []
        if self._check(TokenType.ELSE):
            else_line = self._current().location.line
            self._advance()
            if self._check(TokenType.IF) and self._current().location.line == else_line:
                else_body = [self._parse_if_statement(consume_end=False)]
            else:
                else_body = self._parse_statement_list()

        if consume_end:
            self._expect(TokenType.END)
            self._expect(TokenType.IF)

            label = None
            if self._check(TokenType.STRING):
                label = self._advance().value
        else:
            label = None

        return IfStatement(
            location=location,
            condition=condition,
            then_body=then_body,
            else_body=else_body,
            label=label,
        )

    def _parse_while_statement(self) -> WhileStatement:
        """Parse: while cond do ... end while [label]"""
        location = self._current().location
        self._expect(TokenType.WHILE)
        condition = self._parse_expression()
        self._expect(TokenType.DO)
        body = self._parse_statement_list()
        self._expect(TokenType.END)
        self._expect(TokenType.WHILE)

        label = None
        if self._check(TokenType.STRING):
            label = self._advance().value

        return WhileStatement(
            location=location, condition=condition, body=body, label=label
        )

    def _parse_for_statement(self) -> ForStatement:
        """Parse: for x: Type in items do ... end for [label]"""
        location = self._current().location
        self._expect(TokenType.FOR)
        variable = self._expect(TokenType.IDENTIFIER).value
        self._expect(TokenType.COLON)
        var_type = self._parse_type()
        self._expect(TokenType.IN)
        iterable = self._parse_expression()
        self._expect(TokenType.DO)
        body = self._parse_statement_list()
        self._expect(TokenType.END)
        self._expect(TokenType.FOR)

        label = None
        if self._check(TokenType.STRING):
            label = self._advance().value

        return ForStatement(
            location=location,
            variable=variable,
            var_type=var_type,
            iterable=iterable,
            body=body,
            label=label,
        )

    def _parse_try_statement(self) -> TryStatement:
        """Parse: try ... catch e: Type ... end try"""
        location = self._current().location
        self._expect(TokenType.TRY)
        try_body = self._parse_statement_list()

        catch_loc = self._current().location
        self._expect(TokenType.CATCH)
        var_name = self._expect(TokenType.IDENTIFIER).value
        self._expect(TokenType.COLON)
        type_annotation = self._parse_type()
        catch_body = self._parse_statement_list()

        catch_clause = CatchClause(
            variable=var_name,
            type_annotation=type_annotation,
            body=catch_body,
            location=catch_loc,
        )

        self._expect(TokenType.END)
        self._expect(TokenType.TRY)

        return TryStatement(
            location=location,
            try_body=try_body,
            catch_clause=catch_clause,
        )

    def _parse_match_statement(self) -> MatchStatement:
        """Parse: match expr with | pattern -> body ... end match [label]"""
        location = self._current().location
        self._expect(TokenType.MATCH)
        scrutinee = self._parse_expression()
        self._expect(TokenType.WITH)

        arms: list[MatchArm] = []
        while self._match(TokenType.BAR):
            arm_location = self._current().location
            pattern = self._parse_pattern()
            guard = None
            if self._check(TokenType.IDENTIFIER) and self._current().value == "when":
                self._advance()  # consume 'when'
                guard = self._parse_expression()
            self._expect(TokenType.ARROW)
            body = self._parse_statement_list()
            if not body:
                raise self._error("Match arm body must contain at least one statement")
            arms.append(
                MatchArm(pattern=pattern, body=body, location=arm_location, guard=guard)
            )

        if not arms:
            raise self._error(
                "Match must have at least one arm (each arm starts with '|')"
            )

        self._expect(TokenType.END)
        self._expect(TokenType.MATCH)

        label = None
        if self._check(TokenType.STRING):
            label = self._advance().value

        return MatchStatement(
            location=location, scrutinee=scrutinee, arms=arms, label=label
        )

    def _parse_return_statement(self) -> ReturnStatement:
        """Parse: return [expr]  (expr is optional, defaults to ())"""
        location = self._current().location
        self._expect(TokenType.RETURN)
        # Spec grammar: return_stmt = "return" expr?
        # Bare `return` is equivalent to `return ()`.
        terminators = (
            TokenType.END,
            TokenType.ELSE,
            TokenType.BAR,
            TokenType.CATCH,
            TokenType.EOF,
        )
        if self._check(*terminators):
            value: Expression = TupleExpr(location=location, elements=[])
        else:
            value = self._parse_expression()
        return ReturnStatement(location=location, value=value)

    def _parse_break_statement(self) -> BreakStatement:
        """Parse: break"""
        location = self._current().location
        self._expect(TokenType.BREAK)
        return BreakStatement(location=location)

    def _parse_continue_statement(self) -> ContinueStatement:
        """Parse: continue"""
        location = self._current().location
        self._expect(TokenType.CONTINUE)
        return ContinueStatement(location=location)

    def _parse_expression_statement(self) -> ExpressionStatement:
        """Parse expression as statement."""
        location = self._current().location
        expr = self._parse_expression()
        return ExpressionStatement(location=location, expression=expr)
