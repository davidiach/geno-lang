"""
Geno Parser — Expression parsing mixin
========================================

Contains ExpressionParserMixin with all expression parsing methods.
"""

from __future__ import annotations

from typing import Union

from .ast_nodes import (
    AwaitExpr,
    BinaryOp,
    BooleanLiteral,
    CallArg,
    ConstructorCall,
    Expression,
    FieldAccess,
    FloatLiteral,
    FStringExpr,
    FunctionCall,
    Identifier,
    IndexAccess,
    InferredType,
    IntegerLiteral,
    LambdaExpr,
    ListComprehension,
    ListLiteral,
    LiteralPattern,
    MapEntry,
    MapLiteral,
    MatchArm,
    MatchExpr,
    Pipeline,
    PipelineStage,
    PlaceholderExpr,
    PropagateExpr,
    ReturnStatement,
    Statement,
    StringLiteral,
    TupleExpr,
    TypeAnnotation,
    TypedHole,
    TypeIdentifier,
    UnaryOp,
    WithExpr,
)
from .diagnostics import ErrorCode, replace_fix
from .lexer import Lexer, LexerError
from .parser_base import REMOVED_THROW, ParseError, ParserBase
from .tokens import SourceLocation, TokenType, token_type_to_str


class ExpressionParserMixin(ParserBase):
    """Mixin providing expression parsing methods for the Parser."""

    def _parse_expression(self) -> Expression:
        """Parse an expression."""
        self._nesting_depth += 1
        if self._nesting_depth > self.MAX_NESTING_DEPTH:
            raise ParseError(
                f"Expression nesting too deep (>{self.MAX_NESTING_DEPTH})",
                self._current().location,
            )
        try:
            return self._parse_pipeline()
        finally:
            self._nesting_depth -= 1

    def _parse_pipeline(self) -> Expression:
        """Parse pipeline expression: expr |> f |> g"""
        left = self._parse_logical_expr(1)

        if self._current_type is TokenType.PIPE:
            stages: list[PipelineStage] = []
            while self._current_type is TokenType.PIPE:
                self._advance()
                stage_location = self._current().location
                func, args = self._parse_pipeline_stage()
                stages.append(
                    PipelineStage(
                        function=func, arguments=args, location=stage_location
                    )
                )
                if self._current_type in self._PIPELINE_POSTFIX:
                    # `xs |> map(_, f)[0]` indexes the stage's result rather
                    # than leaving `[0]` behind as a statement of its own
                    # (#138); later stages continue from the indexed value.
                    left = self._parse_pipeline_postfix(
                        Pipeline(location=left.location, initial=left, stages=stages)
                    )
                    stages = []
            if not stages:
                return left
            return Pipeline(location=left.location, initial=left, stages=stages)

        return left

    _PIPELINE_POSTFIX = frozenset(
        {TokenType.LBRACKET, TokenType.DOT, TokenType.QUESTION}
    )

    def _parse_pipeline_postfix(self, expr: Expression) -> Expression:
        """Apply the postfixes (index, field, `?`, call) that follow a stage."""
        return self._parse_postfix_chain(expr)

    def _parse_pipeline_stage(self) -> tuple[Expression, list[Expression]]:
        """Parse a single pipeline stage: f or f(args)"""
        func = self._parse_pipeline_stage_function()
        args: list[Expression] = []

        if self._current_type is TokenType.LPAREN:
            self._advance()
            if self._current_type is not TokenType.RPAREN:
                args.append(self._parse_pipeline_arg())
                while self._current_type is TokenType.COMMA:
                    self._advance()
                    args.append(self._parse_pipeline_arg())
            self._expect(TokenType.RPAREN)

        return func, args

    def _parse_pipeline_stage_function(self) -> Expression:
        """Parse a pipeline stage callable, allowing qualified names."""
        func = self._parse_primary()
        while self._current_type is TokenType.DOT:
            self._advance()
            name = self._expect(TokenType.IDENTIFIER)
            func = FieldAccess(
                location=func.location,
                target=func,
                field_name=name.value,
                name_location=name.location,
            )
        return func

    def _parse_pipeline_arg(self) -> Expression:
        """Parse a pipeline argument (may include placeholder _)."""
        if self._current_type is TokenType.UNDERSCORE:
            location = self._current().location
            self._advance()
            return PlaceholderExpr(location=location)
        return self._parse_expression()

    def _parse_logical_expr(self, min_precedence: int) -> Expression:
        """Parse logical `or` / `and` expressions using precedence climbing."""
        left = self._parse_not_expr()
        precedence_map = self._LOGICAL_PRECEDENCE
        operator_text = self._LOGICAL_OPERATOR_TEXT

        while True:
            current_type = self._current_type
            precedence = precedence_map.get(current_type)
            if precedence is None or precedence < min_precedence:
                return left

            location = left.location
            self._advance()
            right = self._parse_logical_expr(precedence + 1)
            left = BinaryOp(
                location=location,
                operator=operator_text[current_type],
                left=left,
                right=right,
            )

    def _parse_not_expr(self) -> Expression:
        """Parse: not expr"""
        if self._current_type is TokenType.NOT:
            self._advance()
            self._nesting_depth += 1
            if self._nesting_depth > self.MAX_NESTING_DEPTH:
                raise ParseError(
                    f"Expression nesting too deep (>{self.MAX_NESTING_DEPTH})",
                    self._current().location,
                )
            try:
                location = self._current().location
                operand = self._parse_not_expr()
                return UnaryOp(location=location, operator="not", operand=operand)
            finally:
                self._nesting_depth -= 1
        return self._parse_infix_expr(1)

    def _parse_infix_expr(self, min_precedence: int) -> Expression:
        """Parse infix operators using precedence climbing."""
        left = self._parse_unary()
        precedence_map = self._INFIX_PRECEDENCE
        operator_text = self._INFIX_OPERATOR_TEXT
        comparison_tokens = self._COMPARISON_TOKENS
        right_associative_tokens = self._RIGHT_ASSOCIATIVE_TOKENS

        while True:
            current_type = self._current_type
            precedence = precedence_map.get(current_type)
            if precedence is None or precedence < min_precedence:
                return left

            location = left.location
            self._advance()
            operator = operator_text[current_type]

            if current_type in comparison_tokens:
                right = self._parse_nested_infix_expr(precedence + 1)
                if self._current_type in comparison_tokens:
                    raise self._error(
                        "Comparison chaining is not supported; "
                        "write 'a < b and b < c' instead"
                    )
                return BinaryOp(
                    location=location, operator=operator, left=left, right=right
                )

            next_precedence = (
                precedence
                if current_type in right_associative_tokens
                else precedence + 1
            )
            right = self._parse_nested_infix_expr(next_precedence)
            left = BinaryOp(
                location=location, operator=operator, left=left, right=right
            )

    def _parse_nested_infix_expr(self, min_precedence: int) -> Expression:
        """Parse a recursive infix operand while enforcing expression depth."""
        self._nesting_depth += 1
        if self._nesting_depth > self.MAX_NESTING_DEPTH:
            raise ParseError(
                f"Expression nesting too deep (>{self.MAX_NESTING_DEPTH})",
                self._current().location,
            )
        try:
            return self._parse_infix_expr(min_precedence)
        finally:
            self._nesting_depth -= 1

    def _parse_unary(self) -> Expression:
        """Parse: -expr or ~expr"""
        if self._current_type is TokenType.TILDE:
            self._advance()
            self._nesting_depth += 1
            if self._nesting_depth > self.MAX_NESTING_DEPTH:
                raise ParseError(
                    f"Expression nesting too deep (>{self.MAX_NESTING_DEPTH})",
                    self._current().location,
                )
            try:
                location = self._current().location
                operand = self._parse_unary()
                return UnaryOp(location=location, operator="~", operand=operand)
            finally:
                self._nesting_depth -= 1
        if self._current_type is TokenType.MINUS:
            self._advance()
            self._nesting_depth += 1
            if self._nesting_depth > self.MAX_NESTING_DEPTH:
                raise ParseError(
                    f"Expression nesting too deep (>{self.MAX_NESTING_DEPTH})",
                    self._current().location,
                )
            try:
                location = self._current().location
                operand = self._parse_unary()
                return UnaryOp(location=location, operator="-", operand=operand)
            finally:
                self._nesting_depth -= 1
        return self._parse_call()

    def _parse_call(self) -> Expression:
        """Parse function calls and index access: f(x), arr[i]"""
        expr = self._parse_primary()
        current_type = self._current_type
        tokens = self.tokens
        tokens_len = self._tokens_len

        if (
            current_type is not TokenType.LPAREN
            and current_type is not TokenType.LBRACKET
            and current_type is not TokenType.DOT
            and current_type is not TokenType.QUESTION
        ):
            if current_type is not TokenType.WITH:
                return expr
            next_pos = self.pos + 1
            if next_pos >= tokens_len or tokens[next_pos].type is not TokenType.LPAREN:
                return expr
            return self._parse_with_expr(expr)

        expr = self._parse_postfix_chain(expr)
        current_type = self._current_type

        # Check for `with` expression: expr with (field: val, ...)
        # Only parse if `with` is followed by `(` to avoid conflict with `match ... with`
        if current_type is TokenType.WITH:
            next_pos = self.pos + 1
            if next_pos < tokens_len and tokens[next_pos].type is TokenType.LPAREN:
                return self._parse_with_expr(expr)

        return expr

    def _parse_postfix_chain(self, expr: Expression) -> Expression:
        """Apply call, index, field and `?` postfixes to ``expr``."""
        tokens = self.tokens
        tokens_len = self._tokens_len
        while True:
            current_type = self._current_type
            if current_type is TokenType.LPAREN:
                self._advance()
                args: list[CallArg] = []
                if self._current_type is not TokenType.RPAREN:
                    while True:
                        next_pos = self.pos + 1
                        if (
                            self._current_type is TokenType.IDENTIFIER
                            and next_pos < tokens_len
                            and tokens[next_pos].type is TokenType.COLON
                        ):
                            name = self._advance().value
                            self._expect(TokenType.COLON)
                            value = self._parse_expression()
                            args.append(CallArg(value=value, name=name))
                        else:
                            args.append(CallArg(value=self._parse_expression()))
                        if self._current_type is not TokenType.COMMA:
                            break
                        self._advance()
                self._expect(TokenType.RPAREN)
                expr = FunctionCall(
                    location=expr.location, function=expr, arguments=args
                )
            elif current_type is TokenType.LBRACKET:
                self._advance()
                # Index access
                index = self._parse_expression()
                self._expect(TokenType.RBRACKET)
                expr = IndexAccess(location=expr.location, target=expr, index=index)
            elif current_type is TokenType.DOT:
                self._advance()
                if self._check(TokenType.INTEGER):
                    # Tuple field: pair.0 is the index access pair[0].
                    index_token = self._advance()
                    expr = IndexAccess(
                        location=expr.location,
                        target=expr,
                        index=IntegerLiteral(
                            location=index_token.location, value=index_token.value
                        ),
                    )
                    continue
                if self._check(TokenType.FLOAT):
                    raise self._error(
                        "Write a nested tuple field with parentheses, as in (t.0).1"
                    )
                # Field access
                name = self._expect(TokenType.IDENTIFIER)
                expr = FieldAccess(
                    location=expr.location,
                    target=expr,
                    field_name=name.value,
                    name_location=name.location,
                )
            elif current_type is TokenType.QUESTION:
                self._advance()
                # Propagation operator: expr?
                expr = PropagateExpr(location=expr.location, operand=expr)
            else:
                break
        return expr

    def _parse_with_expr(self, target: Expression) -> WithExpr:
        """Parse: expr with (field1: val1, field2: val2, ...)"""
        self._expect(TokenType.WITH)
        self._expect(TokenType.LPAREN)
        updates: list[tuple[str, Expression]] = []
        if not self._check(TokenType.RPAREN):
            field_name = self._expect(TokenType.IDENTIFIER).value
            self._expect(TokenType.COLON)
            value = self._parse_expression()
            updates.append((field_name, value))
            while self._match(TokenType.COMMA):
                field_name = self._expect(TokenType.IDENTIFIER).value
                self._expect(TokenType.COLON)
                value = self._parse_expression()
                updates.append((field_name, value))
        self._expect(TokenType.RPAREN)
        return WithExpr(location=target.location, target=target, updates=updates)

    def _parse_fstring(self) -> FStringExpr:
        """Parse an f-string token into an FStringExpr node."""
        token = self._advance()
        location = token.location
        raw, interp_locations = token.value

        parts: list[Union[str, Expression]] = []
        i = 0
        interp_index = 0
        while i < len(raw):
            brace_start = raw.find("{", i)
            if brace_start == -1:
                parts.append(raw[i:])
                break
            if brace_start > i:
                parts.append(raw[i:brace_start])
            brace_end = raw.find("}", brace_start + 1)
            if brace_end == -1:
                raise ParseError("Unterminated expression in f-string", location)
            expr_location = interp_locations[interp_index]
            interp_index += 1
            expr_text = raw[brace_start + 1 : brace_end]
            if not expr_text.strip():
                raise ParseError("Empty expression in f-string", expr_location)
            expr = self._parse_fstring_expr(expr_text, expr_location)
            parts.append(expr)
            i = brace_end + 1

        if not parts:
            parts.append("")

        return FStringExpr(location=location, parts=parts)

    @staticmethod
    def _fstring_inner_location(
        base: SourceLocation, inner: SourceLocation
    ) -> SourceLocation:
        """Map a location relative to an interpolation onto absolute source coords.

        ``inner`` is reported by the sub-lexer/sub-parser with line/column relative
        to the interpolation text (line 1, column 1 == ``base``). Only the first
        line is column-shifted; later lines already start at column 1.
        """
        if inner.line == 1:
            return SourceLocation(
                line=base.line,
                column=base.column + (inner.column - 1),
                filename=base.filename,
            )
        return SourceLocation(
            line=base.line + (inner.line - 1),
            column=inner.column,
            filename=base.filename,
        )

    def _parse_fstring_expr(
        self, expr_text: str, base_location: SourceLocation
    ) -> Expression:
        """Tokenize and parse a single expression from an f-string."""
        lexer = Lexer(expr_text, base_location.filename)
        try:
            tokens = lexer.tokenize()
        except LexerError as e:
            raise ParseError(
                f"Invalid expression in f-string: {e.message}",
                self._fstring_inner_location(base_location, e.location),
            ) from e
        # Import here to avoid circular import; at runtime the final Parser class exists.
        from .parser import Parser

        sub_parser = Parser(tokens)
        try:
            expr = sub_parser._parse_expression()
        except ParseError as e:
            raise ParseError(
                e.message,
                self._fstring_inner_location(base_location, e.location),
            ) from e
        if not sub_parser._check(TokenType.EOF):
            current = sub_parser._current()
            raise ParseError(
                f"Unexpected token in f-string expression: {current}",
                self._fstring_inner_location(base_location, current.location),
            )
        return expr

    def _parse_primary(self) -> Expression:
        """Parse primary expression (literals, identifiers, grouping)."""
        current = self._current()
        current_type = current.type
        location = current.location

        if current_type is TokenType.IDENTIFIER:
            return Identifier(location=location, name=self._advance().value)

        if current_type is TokenType.TYPE_IDENTIFIER:
            name = self._advance().value
            if self._current_type is TokenType.LPAREN:
                self._advance()
                args: list[Expression] = []
                arg_names: list[str | None] = []
                if self._current_type is not TokenType.RPAREN:
                    while True:
                        if (
                            self._current_type is TokenType.IDENTIFIER
                            and self._peek(1).type is TokenType.COLON
                        ):
                            arg_names.append(self._advance().value)
                            self._expect(TokenType.COLON)
                        else:
                            arg_names.append(None)
                        args.append(self._parse_expression())
                        if self._current_type is not TokenType.COMMA:
                            break
                        self._advance()
                self._expect(TokenType.RPAREN)
                return ConstructorCall(
                    location=location,
                    constructor=name,
                    arguments=args,
                    argument_names=(
                        arg_names if any(n is not None for n in arg_names) else None
                    ),
                )
            # Known zero-argument constructors are constructor calls without parens
            if name in ("None",):
                return ConstructorCall(
                    location=location, constructor=name, arguments=[]
                )
            return TypeIdentifier(location=location, name=name)

        # Literals
        if current_type is TokenType.INTEGER:
            return IntegerLiteral(location=location, value=self._advance().value)
        if current_type is TokenType.FLOAT:
            return FloatLiteral(location=location, value=self._advance().value)
        if current_type is TokenType.STRING:
            return StringLiteral(location=location, value=self._advance().value)
        if current_type is TokenType.FSTRING:
            return self._parse_fstring()
        if current_type is TokenType.TRUE:
            self._advance()
            return BooleanLiteral(location=location, value=True)
        if current_type is TokenType.FALSE:
            self._advance()
            return BooleanLiteral(location=location, value=False)

        if current_type is TokenType.THROW:
            raise self._error(REMOVED_THROW, ErrorCode.PARSE_REMOVED_SYNTAX)

        # await expression
        if current_type is TokenType.AWAIT:
            self._advance()
            expr = self._parse_expression()
            return AwaitExpr(location=location, expr=expr)

        # List literal or list comprehension
        if current_type is TokenType.LBRACE:
            return self._parse_map_literal()

        if current_type is TokenType.LBRACKET:
            self._advance()
            if self._current_type is TokenType.RBRACKET:
                self._advance()
                return ListLiteral(location=location, elements=[])

            first_expr = self._parse_expression()

            # Check for list comprehension: [expr for var: Type in iterable]
            if self._current_type is TokenType.FOR:
                self._advance()  # consume 'for'
                variable_token = self._expect(
                    TokenType.IDENTIFIER, "Expected variable name"
                )
                variable = variable_token.value
                var_type: TypeAnnotation = InferredType(
                    location=variable_token.location
                )
                if self._match(TokenType.COLON):
                    var_type = self._parse_type()
                self._expect(TokenType.IN)
                iterable = self._parse_expression()
                condition = None
                if self._current_type is TokenType.IF:
                    self._advance()
                    condition = self._parse_expression()
                self._expect(TokenType.RBRACKET)
                return ListComprehension(
                    location=location,
                    element_expr=first_expr,
                    variable=variable,
                    var_type=var_type,
                    iterable=iterable,
                    condition=condition,
                )

            # Regular list literal
            elements: list[Expression] = [first_expr]
            while self._current_type is TokenType.COMMA:
                self._advance()
                elements.append(self._parse_expression())
            self._expect(TokenType.RBRACKET)
            return ListLiteral(location=location, elements=elements)

        # Lambda: fn(params) -> expr  OR  fn(params) do ... end fn
        if current_type is TokenType.FN:
            self._advance()
            self._expect(TokenType.LPAREN)
            params = self._parse_parameter_list(allow_inferred_types=True)
            self._expect(TokenType.RPAREN)
            return_type = None

            if self._current_type is TokenType.DO:
                self._advance()
                # Block lambda: fn(params) do ... end fn
                block_body = self._parse_statement_list()
                self._expect(TokenType.END)
                self._expect(TokenType.FN)
                return LambdaExpr(
                    location=location,
                    params=params,
                    return_type=return_type,
                    block_body=block_body,
                )
            else:
                # Expression lambda: fn(params) -> expr
                self._expect(
                    TokenType.ARROW,
                    "Expected '->' or 'do' after lambda parameter list",
                )
                body = self._parse_expression()
                return LambdaExpr(
                    location=location,
                    params=params,
                    return_type=return_type,
                    body=body,
                )

        # Typed hole: ?name: Type
        if current_type is TokenType.QUESTION:
            self._advance()
            name = self._expect(TokenType.IDENTIFIER).value
            self._expect(TokenType.COLON)
            hole_type = self._parse_type()
            constraint = None
            if self._current_type is TokenType.WHERE:
                self._advance()
                constraint = self._parse_expression()
            return TypedHole(
                location=location, name=name, hole_type=hole_type, constraint=constraint
            )

        # Match expression (inline): match expr with ... end match
        if current_type is TokenType.IF:
            return self._parse_if_expression()

        if current_type is TokenType.MATCH:
            return self._parse_match_expression()

        # Grouping or tuple: (expr) or (expr, expr, ...)
        if current_type is TokenType.LPAREN:
            self._advance()
            if self._current_type is TokenType.RPAREN:
                # Empty tuple / unit
                self._expect(TokenType.RPAREN)
                return TupleExpr(location=location, elements=[])

            first = self._parse_expression()
            if self._current_type is TokenType.COMMA:
                self._advance()
                # Tuple (possibly single-element via trailing comma: (x,))
                elements = [first]
                if self._current_type is not TokenType.RPAREN:
                    elements.append(self._parse_expression())
                    while self._current_type is TokenType.COMMA:
                        self._advance()
                        if self._current_type is TokenType.RPAREN:
                            break
                        elements.append(self._parse_expression())
                self._expect(TokenType.RPAREN)
                return TupleExpr(location=location, elements=elements)
            else:
                # Grouping
                self._expect(TokenType.RPAREN)
                return first

        current = self._current()
        previous = self.tokens[self.pos - 1] if self.pos > 0 else None
        if (
            current.type is TokenType.PLUS
            and previous is not None
            and previous.type is TokenType.PLUS
            and previous.location.line == current.location.line
            and previous.location.column + 1 == current.location.column
        ):
            raise ParseError(
                "Geno concatenates with '+'; '++' is not an operator",
                previous.location,
                ErrorCode.PARSE_INVALID_SYNTAX,
                (replace_fix(previous.location, "++", "+"),),
            )
        raise self._error(
            f"Expected expression (e.g., a value, variable, or function call), "
            f"got {token_type_to_str(self._current().type)}"
        )

    def _parse_map_literal(self) -> MapLiteral:
        """Parse `{key: value, ...}`; `{}` is the empty map."""
        location = self._current().location
        self._expect(TokenType.LBRACE)
        entries: list[MapEntry] = []
        while not self._check(TokenType.RBRACE):
            key = self._parse_expression()
            self._expect(
                TokenType.COLON, "Expected ':' between a map key and its value"
            )
            value = self._parse_expression()
            entries.append(MapEntry(key=key, value=value))
            if not self._match(TokenType.COMMA):
                break
        self._expect(TokenType.RBRACE, "Expected ',' or '}' in a map literal")
        return MapLiteral(location=location, entries=entries)

    def _parse_if_expression(self) -> MatchExpr:
        """Parse `if c then a else if d then b else e end if` as a value.

        It is the match `match c with | true -> a | false -> ... end match`,
        which every later pass and backend already handles.
        """
        expr = self._parse_if_expression_chain()
        self._expect(TokenType.END)
        self._expect(TokenType.IF)
        return expr

    def _parse_if_expression_chain(self) -> MatchExpr:
        location = self._current().location
        self._expect(TokenType.IF)
        condition = self._parse_expression()
        self._expect(TokenType.THEN)
        then_value = self._parse_expression()
        if not self._match(TokenType.ELSE):
            raise self._error(
                "An if expression needs an else branch, because it must produce "
                "a value: if condition then a else b end if"
            )
        if self._check(TokenType.IF):
            else_value: Expression = self._parse_if_expression_chain()
        else:
            else_value = self._parse_expression()
        arms = [
            MatchArm(
                pattern=LiteralPattern(location=then_value.location, value=True),
                body=[ReturnStatement(location=then_value.location, value=then_value)],
                location=then_value.location,
            ),
            MatchArm(
                pattern=LiteralPattern(location=else_value.location, value=False),
                body=[ReturnStatement(location=else_value.location, value=else_value)],
                location=else_value.location,
            ),
        ]
        expr = MatchExpr(location=location, scrutinee=condition, arms=arms)
        expr.from_if = True
        return expr

    def _parse_match_expression(self) -> MatchExpr:
        """Parse inline match expression."""
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
            # For expression context, the body is a single expression
            body_expr = self._parse_expression()
            body: list[Statement] = [
                ReturnStatement(location=body_expr.location, value=body_expr)
            ]
            arms.extend(self._or_pattern_arms(patterns, guard, body, arm_location))

        if not arms:
            raise self._error(
                "Match must have at least one arm (each arm starts with '|')"
            )

        self._expect(TokenType.END)
        self._expect(TokenType.MATCH)

        return MatchExpr(location=location, scrutinee=scrutinee, arms=arms)
