"""
Geno Parser — Statement parsing mixin
=======================================

Contains StatementParserMixin with all statement parsing methods.
"""

from __future__ import annotations

import copy

from .ast_nodes import (
    AssignStatement,
    BinaryOp,
    BooleanLiteral,
    BreakStatement,
    ContinueStatement,
    Expression,
    ExpressionStatement,
    FieldAccess,
    FieldAssignStatement,
    FloatLiteral,
    ForStatement,
    Identifier,
    IfStatement,
    IndexAccess,
    IndexAssignStatement,
    InferredType,
    IntegerLiteral,
    LetStatement,
    MatchArm,
    MatchStatement,
    Pattern,
    ReturnStatement,
    Statement,
    StringLiteral,
    TupleDestructureStatement,
    TupleExpr,
    TypeAnnotation,
    UnaryOp,
    VarStatement,
    WhileStatement,
    WildcardPattern,
)
from .diagnostics import ErrorCode, replace_fix
from .parser_base import REMOVED_TRY, ParseError, ParserBase
from .tokens import SourceLocation, TokenType


class StatementParserMixin(ParserBase):
    """Mixin providing statement parsing methods for the Parser."""

    def _parse_statement_list(self) -> list[Statement]:
        """Parse statements until we hit a block terminator."""
        statements: list[Statement] = []
        while not self._check(
            TokenType.END, TokenType.ELSE, TokenType.BAR, TokenType.EOF
        ):
            statements.append(self._parse_statement())
        return statements

    def _parse_statement(self) -> Statement:
        """Parse a single statement."""
        current = self._current()
        if (
            current.type is TokenType.IDENTIFIER
            and current.value == "elif"
            and self._peek(1).type
            not in (
                TokenType.ASSIGN,
                TokenType.LPAREN,
                TokenType.DOT,
                TokenType.LBRACKET,
            )
        ):
            raise ParseError(
                "Geno writes 'else if', not 'elif'",
                current.location,
                ErrorCode.PARSE_INVALID_SYNTAX,
                (replace_fix(current.location, "elif", "else if"),),
            )
        if self._check(TokenType.LET):
            return self._parse_let_statement()
        elif self._check(TokenType.VAR):
            return self._parse_var_statement()
        elif self._check(TokenType.IF, TokenType.WHILE, TokenType.FOR, TokenType.MATCH):
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
                else:
                    return self._parse_match_statement()
            finally:
                self._nesting_depth -= 1
        elif self._check(TokenType.TRY, TokenType.CATCH):
            raise self._error(REMOVED_TRY, ErrorCode.PARSE_REMOVED_SYNTAX)
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
        let_token = self._expect(TokenType.LET)
        if self._check(TokenType.LPAREN):
            return self._parse_tuple_destructure(location, mutable=False)
        self._reject_let_mut(let_token)
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
        type_annotation: TypeAnnotation = InferredType(location=location)
        if self._match(TokenType.COLON):
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
        """Parse an assignment to an index or field (``arr[i] = v``,
        ``obj.f = v``), a compound assignment (``x += v``), or an expression
        statement."""
        location = self._current().location
        expr = self._parse_expression()

        operator = self._compound_assign_operator()
        if operator is not None:
            return self._parse_compound_assignment(location, expr, operator)

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

    def _parse_compound_assignment(
        self, location: SourceLocation, target: Expression, operator: str
    ) -> Statement:
        """Parse the rest of ``target op= value`` as ``target = target op value``.

        The target is read twice, so it may not call a function: a call could
        have an effect, or return a different value the second time.
        """
        operator_location = self._current().location
        self._advance()  # the operator
        self._advance()  # `=`
        if not isinstance(target, Identifier | IndexAccess | FieldAccess):
            raise ParseError(
                f"Invalid assignment target for '{operator}=': only variables, "
                "fields (x.field), and indices (x[i]) can be assigned to",
                operator_location,
                ErrorCode.PARSE_INVALID_SYNTAX,
            )
        if not _repeatable(target):
            raise ParseError(
                f"The target of '{operator}=' is read and then written, so it "
                "cannot call a function; bind the call's result with 'let' "
                "first, or write the assignment out in full",
                operator_location,
                ErrorCode.PARSE_INVALID_SYNTAX,
            )
        read = copy.deepcopy(target)
        # Lets the checker word an error about reading the target in terms of
        # the compound assignment the program wrote.
        read._compound_operator = operator  # type: ignore[union-attr]
        value = BinaryOp(
            location=target.location,
            operator=operator,
            left=read,
            right=self._parse_expression(),
        )
        if isinstance(target, Identifier):
            return AssignStatement(location=location, target=target.name, value=value)
        if isinstance(target, IndexAccess):
            return IndexAssignStatement(
                location=location, target=target.target, index=target.index, value=value
            )
        return FieldAssignStatement(
            location=location,
            target=target.target,
            field_name=target.field_name,
            value=value,
        )

    def _parse_if_statement(
        self, consume_end: bool = True
    ) -> IfStatement | MatchStatement:
        """Parse: if cond then ... [else ...] end if [label]

        ``if let PATTERN = value then ...`` is the two-arm match
        ``match value with | PATTERN -> ... | _ -> <else branch> end match``,
        which every later pass and backend already handles.
        """
        location = self._current().location
        self._expect(TokenType.IF)
        if_let: tuple[list[Pattern], Expression] | None = None
        condition: Expression | None = None
        if self._match(TokenType.LET):
            patterns = self._parse_arm_patterns()
            if not self._check(TokenType.ASSIGN):
                raise self._error(
                    "Expected '=' after the pattern: if let PATTERN = value then"
                )
            self._advance()
            if_let = (patterns, self._parse_expression())
            if self._check(TokenType.IDENTIFIER) and self._current().value == "when":
                raise self._error(
                    "if let takes no guard. Test the condition inside the branch, "
                    "or write a match, whose arms take `when`"
                )
        else:
            condition = self._parse_expression()
        self._expect(TokenType.THEN)
        then_body = self._parse_statement_list()

        else_location = self._current().location
        else_body: list[Statement] = []
        if self._check(TokenType.ELSE):
            else_token = self._advance()
            self._reject_trailing_colon("else")
            # A chain may be written as ``else if`` or wrapped with ``if`` at
            # the same indentation.  A more-indented ``if`` remains an
            # ordinary nested statement with its own ``end if``.
            is_else_if = self._check(TokenType.IF) and (
                self._current().location.line == else_token.location.line
                or self._current().location.column <= else_token.location.column
            )
            if is_else_if:
                else_body = [self._parse_if_statement(consume_end=False)]
            else:
                else_body = self._parse_statement_list()

        if consume_end:
            self._expect_end(TokenType.IF)

            label = None
            if self._check(TokenType.STRING):
                label = self._advance().value
        else:
            label = None

        if if_let is not None:
            patterns, scrutinee = if_let
            # An arm needs a statement; with no else branch it does nothing.
            nothing: list[Statement] = [
                ExpressionStatement(
                    location=else_location,
                    expression=TupleExpr(location=else_location, elements=[]),
                )
            ]
            arms = self._or_pattern_arms(
                patterns, None, then_body or copy.deepcopy(nothing), location
            )
            arms.append(
                MatchArm(
                    pattern=WildcardPattern(location=else_location),
                    body=else_body or nothing,
                    location=else_location,
                )
            )
            return MatchStatement(
                location=location, scrutinee=scrutinee, arms=arms, label=label
            )

        assert condition is not None
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
        self._expect_end(TokenType.WHILE)

        label = None
        if self._check(TokenType.STRING):
            label = self._advance().value

        return WhileStatement(
            location=location, condition=condition, body=body, label=label
        )

    def _parse_for_statement(self) -> ForStatement:
        """Parse: for x: Type in items do ... end for [label]

        ``for (a, b) in pairs`` unpacks each element (``_parse_loop_binding``).
        """
        location = self._current().location
        self._expect(TokenType.FOR)
        variable, var_type, pattern = self._parse_loop_binding()
        self._expect(TokenType.IN)
        iterable = self._parse_expression()
        self._expect(TokenType.DO)
        body = self._parse_statement_list()
        self._expect_end(TokenType.FOR)
        if pattern is not None:
            body = self._unpack_loop_body(variable, pattern, body)

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

    def _parse_match_statement(self) -> MatchStatement:
        """Parse: match expr with | pattern -> body ... end match [label]"""
        location = self._current().location
        self._expect(TokenType.MATCH)
        scrutinee = self._parse_expression()
        self._expect(TokenType.WITH)

        arms: list[MatchArm] = []
        while self._match(TokenType.BAR):
            arm_location = self._current().location
            patterns = self._parse_arm_patterns()
            guard = None
            if self._check(TokenType.IDENTIFIER) and self._current().value == "when":
                self._advance()  # consume 'when'
                guard = self._parse_expression()
            self._expect(TokenType.ARROW)
            body = self._parse_statement_list()
            if not body:
                raise self._error("Match arm body must contain at least one statement")
            arms.extend(self._or_pattern_arms(patterns, guard, body, arm_location))

        if not arms:
            raise self._error(
                "Match must have at least one arm (each arm starts with '|')"
            )

        self._expect_end(TokenType.MATCH)

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
        # Bare `return` is equivalent to `return ()`.  Since Geno does not
        # emit newline tokens, use source locations to keep a statement on the
        # following line from being silently consumed as the return value.
        terminators = (
            TokenType.END,
            TokenType.ELSE,
            TokenType.BAR,
            TokenType.EOF,
        )
        if self._check(*terminators) or self._current().location.line > location.line:
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

    def _parse_expression_statement(self) -> Statement:
        """Parse an expression statement, or a compound assignment ``x += v``."""
        location = self._current().location
        expr = self._parse_expression()
        operator = self._compound_assign_operator()
        if operator is not None:
            return self._parse_compound_assignment(location, expr, operator)
        return ExpressionStatement(location=location, expression=expr)


# Expressions a compound assignment's target may contain: reading them twice
# gives the same value and has no effect.
_REPEATABLE_LEAVES = (
    Identifier,
    IntegerLiteral,
    FloatLiteral,
    StringLiteral,
    BooleanLiteral,
)


def _repeatable(expr: Expression) -> bool:
    """Whether ``expr`` can be evaluated twice with the same result."""
    if isinstance(expr, _REPEATABLE_LEAVES):
        return True
    if isinstance(expr, FieldAccess):
        return _repeatable(expr.target)
    if isinstance(expr, IndexAccess):
        return _repeatable(expr.target) and _repeatable(expr.index)
    if isinstance(expr, BinaryOp):
        return _repeatable(expr.left) and _repeatable(expr.right)
    if isinstance(expr, UnaryOp):
        return _repeatable(expr.operand)
    return False
