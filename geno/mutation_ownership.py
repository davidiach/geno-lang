"""Which collections a function or lambda owns (proposal 0019, section 3).

A unit is a function body or a lambda body. A name ``n`` is *owned* in a unit
when every collection it ever holds was allocated by the unit itself and none
of them can be reached through any other storage while the unit runs. The
effect checker masks writes into an owned collection's top-level slots
(``n[i] = e``), because no caller can observe them.

The test is flow-insensitive and keyed by name. ``n`` is owned when:

- **O0.** the unit declares ``var n = e`` (or ``var n: T = e``) with ``e`` a
  fresh expression, outside every nested lambda;
- **O1.** ``n`` is not a parameter of the unit;
- **O2.** every declaration of ``n`` in the unit, in any block, is such a fresh
  ``var`` (a ``let``, a destructure, or a ``for``, pattern or comprehension
  variable named ``n`` disqualifies it);
- **O3.** every plain assignment ``n = e`` has ``e`` a fresh expression;
- **O4.** ``n`` does not occur in any lambda nested in the unit, as an
  identifier, an assignment target, a declaration or a parameter;
- **O5.** every other occurrence of ``n`` is in a permitted position:

  - **P1.** the root of a write statement's target (``n[i] = e``,
    ``n[i].f = e``);
  - **P2.** the target of an index read ``n[i]``;
  - **P3.** an argument a builtin only borrows (``BORROWED_ARGUMENT_NAMES``),
    matched by position or by parameter name; a pipeline passes its value as
    an argument of the stage call (spec 7.7), so ``n |> array_to_list`` and
    ``n |> array_get(_, 0)`` put ``n`` in the parameter it fills;
  - **P4.** the container operand of ``in`` (and so of ``not in``);
  - **P5.** the iterable of a ``for`` statement or a list comprehension;
  - **P6.** a return position: the operand of a ``return`` that ends the unit,
    the body of an expression-bodied lambda, and recursively an element of a
    tuple or list literal, an argument of a constructor call, or a branch
    value of a ``match`` or ``if`` expression that is itself in a return
    position.

  Every other occurrence is an escape: an argument to a user function or to a
  builtin parameter that is not borrowed, an initializer, the right side of an
  assignment, a value stored by a write, an element of a literal or
  constructor that is not returned, a pipeline value that fills a parameter
  that is not borrowed (``n |> helper``), a match scrutinee, an
  f-string interpolation, an operand of any other operator, of ``?`` or of
  ``await``, a field access target, a callee, and so on.

A *fresh expression* is a call that the checker resolved to one of the
``FRESH_COLLECTION_BUILTINS`` (``FunctionCall._resolved_builtin_name``). So the
analysis runs after the unit has been type-checked: a user function or a local
binding that shadows ``array_new`` is not fresh, and method spellings the
checker rewrote (``v.push(x)`` into ``vec_push(v, x)``) are plain builtin
calls by then.

The parser represents each branch of a ``match`` or ``if`` expression as a
``ReturnStatement`` holding the branch value. Those are not returns from the
unit: their value goes wherever the expression goes, so they take the
expression's position (P6 only when the expression itself is returned).

The analysis is conservative by construction. Merging every binding that
shares a name can only remove names, and so can every escape. Each
``Statement``, ``Expression`` and ``Pattern`` class in ``geno.ast_nodes`` has
an explicit handler (``geno/tests/test_mutation_ownership.py`` enumerates
them); a node of any other class makes the whole unit own nothing.

``owned_names`` reports a name only if O0 holds, so a name the unit merely
captures or reads from module scope is never owned. It is a standalone,
pure-AST query: the checker still requires a masked write's root to be
unit-local by its own scope chain.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .ast_nodes import (
    AssertStatement,
    AssignStatement,
    AwaitExpr,
    BinaryOp,
    BooleanLiteral,
    BreakStatement,
    ConstructorCall,
    ConstructorPattern,
    ContinueStatement,
    Expression,
    ExpressionStatement,
    FieldAccess,
    FieldAssignStatement,
    FloatLiteral,
    ForStatement,
    FStringExpr,
    FunctionCall,
    FunctionDef,
    Identifier,
    IfStatement,
    IndexAccess,
    IndexAssignStatement,
    IntegerLiteral,
    LambdaExpr,
    LetStatement,
    ListComprehension,
    ListLiteral,
    ListPattern,
    LiteralPattern,
    MapLiteral,
    MatchArm,
    MatchExpr,
    MatchStatement,
    Parameter,
    Pattern,
    Pipeline,
    PipelineStage,
    PlaceholderExpr,
    PropagateExpr,
    RestPattern,
    ReturnStatement,
    Statement,
    StringLiteral,
    TupleDestructureStatement,
    TupleExpr,
    TuplePattern,
    TypedHole,
    TypeIdentifier,
    UnaryOp,
    VariablePattern,
    VarStatement,
    WhileStatement,
    WildcardPattern,
    WithExpr,
)
from .builtin_registry import (
    BORROWED_ARGUMENT_NAMES,
    BORROWED_ARGUMENT_POSITIONS,
    FRESH_COLLECTION_BUILTINS,
)

__all__ = [
    "function_owned_names",
    "is_fresh_expression",
    "lambda_owned_names",
    "owned_names",
]


def owned_names(
    body: Sequence[Statement] | Expression, params: Iterable[Parameter]
) -> frozenset[str]:
    """Return the names the unit with this body and these parameters owns.

    *body* is a function's or block lambda's statements, or an
    expression-bodied lambda's expression. Run it after the unit has been
    type-checked, so that builtin calls are resolved.
    """
    walker = _OwnershipWalker()
    for param in params:
        walker.disqualify(param.name)
    if isinstance(body, Expression):
        walker.expression(body, returned=True)
    else:
        walker.block(body, returned=True)
    return walker.result()


def function_owned_names(defn: FunctionDef) -> frozenset[str]:
    """Return the names a function (top-level, impl method, async) owns.

    Parameter defaults run in the declaration's global scope, not in the
    function body, so they take no part.
    """
    return owned_names(defn.body, defn.params)


def lambda_owned_names(expr: LambdaExpr) -> frozenset[str]:
    """Return the names a lambda owns, treating its body as its own unit."""
    if expr.block_body is not None:
        return owned_names(expr.block_body, expr.params)
    if expr.body is not None:
        return owned_names(expr.body, expr.params)
    return frozenset()


def is_fresh_expression(expr: Expression) -> bool:
    """Is *expr* a call the checker resolved to an allocating builtin?"""
    if not isinstance(expr, FunctionCall):
        return False
    return _resolved_builtin(expr) in FRESH_COLLECTION_BUILTINS


def _resolved_builtin(expr: FunctionCall) -> str | None:
    """The builtin *expr* calls, if the checker resolved its callee to one.

    The callee must still be the identifier the checker resolved: a call it
    re-targeted afterwards (proposal 0010 overloads) keeps no stale name.
    """
    name: str | None = expr._resolved_builtin_name
    callee = expr.function
    if name is None or not isinstance(callee, Identifier) or callee.name != name:
        return None
    return name


def _stage_builtin(stage: PipelineStage) -> str | None:
    """The builtin a pipeline stage calls, if the checker resolved it to one."""
    callee = stage.function
    if not isinstance(callee, Identifier):
        return None
    name: str | None = callee._resolved_builtin_name
    if name is None or callee.name != name:
        return None
    return name


def _stage_borrowed_positions(stage: PipelineStage) -> frozenset[int]:
    """The parameter positions of a stage's builtin that only borrow."""
    builtin = _stage_builtin(stage)
    if builtin is None:
        return frozenset()
    positions: frozenset[int] = BORROWED_ARGUMENT_POSITIONS.get(builtin, frozenset())
    return positions


def _stage_placeholders(stage: PipelineStage) -> frozenset[int]:
    """The parameter positions a stage's ``_`` placeholders fill."""
    return frozenset(
        index
        for index, argument in enumerate(stage.arguments)
        if isinstance(argument, PlaceholderExpr)
    )


def _stage_borrows_piped_value(stage: PipelineStage) -> bool:
    """Does every parameter the piped value fills only borrow it?"""
    borrowed = _stage_borrowed_positions(stage)
    filled = _stage_placeholders(stage) or frozenset({0})
    return bool(borrowed) and filled <= borrowed


class _OwnershipWalker:
    """One pass over a unit, collecting fresh ``var`` names and disqualified names.

    ``returned`` says whether a value in this position is the unit's result
    (P6). For statements it says whether a ``ReturnStatement`` there returns
    from the unit, which is false inside a ``match`` expression that is not
    itself returned.
    """

    def __init__(self) -> None:
        self._fresh_vars: set[str] = set()
        self._disqualified: set[str] = set()
        self._lambda_depth = 0
        self._incomplete = False

    def result(self) -> frozenset[str]:
        if self._incomplete:
            return frozenset()
        return frozenset(self._fresh_vars - self._disqualified)

    # -- names ------------------------------------------------------------

    def disqualify(self, name: str | None) -> None:
        if name is not None:
            self._disqualified.add(name)

    def _declare_var(self, name: str, value: Expression) -> None:
        if self._lambda_depth == 0 and is_fresh_expression(value):
            self._fresh_vars.add(name)
        else:
            self.disqualify(name)

    def _reassign(self, name: str, value: Expression) -> None:
        if self._lambda_depth > 0 or not is_fresh_expression(value):
            self.disqualify(name)

    # -- positions --------------------------------------------------------

    def _value(self, expr: Expression | None) -> None:
        """Visit an expression whose value may go anywhere (an escape)."""
        if expr is not None:
            self.expression(expr, returned=False)

    def _permitted(self, expr: Expression) -> None:
        """Visit an operand in a permitted position (P1 to P5).

        Only a bare name is permitted there; any other expression is visited
        as an escape, so `array_length(if c then a else b end if)` still
        counts `a` and `b` as escaping.
        """
        if isinstance(expr, Identifier) and self._lambda_depth == 0:
            return
        self._value(expr)

    def _write_target(self, target: Expression) -> None:
        """Visit a write's container chain `r s1 ... sk`, whose root is P1."""
        node = target
        while True:
            if isinstance(node, IndexAccess):
                self._value(node.index)
                node = node.target
            elif isinstance(node, FieldAccess):
                node = node.target
            else:
                break
        self._permitted(node)

    # -- dispatch ---------------------------------------------------------

    def block(self, statements: Sequence[Statement], returned: bool) -> None:
        for statement in statements:
            self.statement(statement, returned)

    def statement(self, stmt: Statement, returned: bool) -> None:
        handler = _STATEMENT_HANDLERS.get(type(stmt))
        if handler is None:
            self._incomplete = True
            return
        handler(self, stmt, returned)

    def expression(self, expr: Expression, returned: bool) -> None:
        handler = _EXPRESSION_HANDLERS.get(type(expr))
        if handler is None:
            self._incomplete = True
            return
        handler(self, expr, returned)

    def pattern(self, pattern: Pattern) -> None:
        handler = _PATTERN_HANDLERS.get(type(pattern))
        if handler is None:
            self._incomplete = True
            return
        handler(self, pattern)

    def _arm(self, arm: MatchArm, returned: bool) -> None:
        self.pattern(arm.pattern)
        self._value(arm.guard)
        self.block(arm.body, returned)

    # -- statements -------------------------------------------------------

    def _let_statement(self, stmt: LetStatement, _returned: bool) -> None:
        self._value(stmt.value)
        self.disqualify(stmt.name)

    def _var_statement(self, stmt: VarStatement, _returned: bool) -> None:
        self._value(stmt.value)
        self._declare_var(stmt.name, stmt.value)

    def _tuple_destructure(
        self, stmt: TupleDestructureStatement, _returned: bool
    ) -> None:
        self._value(stmt.value)
        for name in stmt.names:
            self.disqualify(name)

    def _assign_statement(self, stmt: AssignStatement, _returned: bool) -> None:
        self._value(stmt.value)
        self._reassign(stmt.target, stmt.value)

    def _index_assign_statement(
        self, stmt: IndexAssignStatement, _returned: bool
    ) -> None:
        self._write_target(stmt.target)
        self._value(stmt.index)
        self._value(stmt.value)

    def _field_assign_statement(
        self, stmt: FieldAssignStatement, _returned: bool
    ) -> None:
        self._write_target(stmt.target)
        self._value(stmt.value)

    def _if_statement(self, stmt: IfStatement, returned: bool) -> None:
        self._value(stmt.condition)
        self.block(stmt.then_body, returned)
        self.block(stmt.else_body, returned)

    def _while_statement(self, stmt: WhileStatement, returned: bool) -> None:
        self._value(stmt.condition)
        self.block(stmt.body, returned)

    def _for_statement(self, stmt: ForStatement, returned: bool) -> None:
        self._permitted(stmt.iterable)
        self.disqualify(stmt.variable)
        self.block(stmt.body, returned)

    def _match_statement(self, stmt: MatchStatement, returned: bool) -> None:
        self._value(stmt.scrutinee)
        for arm in stmt.arms:
            self._arm(arm, returned)

    def _return_statement(self, stmt: ReturnStatement, returned: bool) -> None:
        if stmt.value is not None:
            self.expression(stmt.value, returned)

    def _no_operands(self, _node: Any, _returned: bool) -> None:
        """Break, continue, literals and placeholders mention no name."""

    def _expression_statement(self, stmt: ExpressionStatement, _returned: bool) -> None:
        self._value(stmt.expression)

    def _assert_statement(self, stmt: AssertStatement, _returned: bool) -> None:
        self._value(stmt.expression)

    # -- expressions ------------------------------------------------------

    def _identifier(self, expr: Identifier | TypeIdentifier, returned: bool) -> None:
        if self._lambda_depth > 0 or not returned:
            self.disqualify(expr.name)

    def _fstring(self, expr: FStringExpr, _returned: bool) -> None:
        for part in expr.parts:
            if not isinstance(part, str):
                self._value(part)

    def _sequence(self, expr: ListLiteral | TupleExpr, returned: bool) -> None:
        for element in expr.elements:
            self.expression(element, returned)

    def _map_literal(self, expr: MapLiteral, _returned: bool) -> None:
        for entry in expr.entries:
            self._value(entry.key)
            self._value(entry.value)

    def _list_comprehension(self, expr: ListComprehension, _returned: bool) -> None:
        self._permitted(expr.iterable)
        self.disqualify(expr.variable)
        self._value(expr.element_expr)
        self._value(expr.condition)

    def _await(self, expr: AwaitExpr, _returned: bool) -> None:
        self._value(expr.expr)

    def _binary_op(self, expr: BinaryOp, _returned: bool) -> None:
        self._value(expr.left)
        if expr.operator == "in":
            self._permitted(expr.right)
        else:
            self._value(expr.right)

    def _unary_op(self, expr: UnaryOp, _returned: bool) -> None:
        self._value(expr.operand)

    def _function_call(self, expr: FunctionCall, _returned: bool) -> None:
        self._value(expr.function)
        builtin = _resolved_builtin(expr)
        borrowed_names = BORROWED_ARGUMENT_NAMES.get(builtin or "", frozenset())
        borrowed_positions = BORROWED_ARGUMENT_POSITIONS.get(builtin or "", frozenset())
        named_seen = False
        for index, arg in enumerate(expr.arguments):
            if arg.name is not None:
                named_seen = True
                borrowed = arg.name in borrowed_names
            else:
                # A positional argument after a named one fills the first
                # free slot; rather than repeat that rule, treat it as an
                # escape.
                borrowed = not named_seen and index in borrowed_positions
            if borrowed:
                self._permitted(arg.value)
            else:
                self._value(arg.value)

    def _index_access(self, expr: IndexAccess, _returned: bool) -> None:
        self._permitted(expr.target)
        self._value(expr.index)

    def _field_access(self, expr: FieldAccess, _returned: bool) -> None:
        self._value(expr.target)

    def _pipeline(self, expr: Pipeline, _returned: bool) -> None:
        # `x |> f(args)` passes `x` as an argument of the stage call (spec
        # 7.7): at each `_` placeholder, or else as the first argument. So the
        # initial value and every written argument take the position of the
        # parameter they fill, as in a call. Later stages receive the
        # previous stage's result, which is no name of this unit.
        stages = expr.stages
        if stages and _stage_borrows_piped_value(stages[0]):
            self._permitted(expr.initial)
        else:
            self._value(expr.initial)
        for stage in stages:
            self._value(stage.function)
            borrowed = _stage_borrowed_positions(stage)
            piped_first = not _stage_placeholders(stage)
            for index, argument in enumerate(stage.arguments):
                position = index + 1 if piped_first else index
                if position in borrowed:
                    self._permitted(argument)
                else:
                    self._value(argument)

    def _lambda(self, expr: LambdaExpr, _returned: bool) -> None:
        # O4: every name a nested lambda mentions or declares is disqualified,
        # which the depth makes every handler do.
        self._lambda_depth += 1
        try:
            for param in expr.params:
                self.disqualify(param.name)
                self._value(param.default_value)
            if expr.block_body is not None:
                self.block(expr.block_body, returned=False)
            self._value(expr.body)
        finally:
            self._lambda_depth -= 1

    def _match_expr(self, expr: MatchExpr, returned: bool) -> None:
        self._value(expr.scrutinee)
        for arm in expr.arms:
            # Each arm's `ReturnStatement` is the branch value, not a return
            # from the unit: it takes this expression's position.
            self._arm(arm, returned)

    def _constructor_call(self, expr: ConstructorCall, returned: bool) -> None:
        for argument in expr.arguments:
            self.expression(argument, returned)

    def _typed_hole(self, expr: TypedHole, _returned: bool) -> None:
        self._value(expr.constraint)

    def _propagate(self, expr: PropagateExpr, _returned: bool) -> None:
        self._value(expr.operand)

    def _with_expr(self, expr: WithExpr, _returned: bool) -> None:
        self._value(expr.target)
        for _field_name, update in expr.updates:
            self._value(update)

    # -- patterns ---------------------------------------------------------

    def _no_bindings(self, _pattern: Pattern) -> None:
        """Wildcard and literal patterns bind nothing."""

    def _variable_pattern(self, pattern: VariablePattern) -> None:
        self.disqualify(pattern.name)

    def _rest_pattern(self, pattern: RestPattern) -> None:
        self.disqualify(pattern.name)

    def _constructor_pattern(self, pattern: ConstructorPattern) -> None:
        for subpattern in pattern.subpatterns:
            self.pattern(subpattern)

    def _sequence_pattern(self, pattern: ListPattern | TuplePattern) -> None:
        for element in pattern.elements:
            self.pattern(element)


_NodeHandler = Callable[[_OwnershipWalker, Any, bool], None]

_STATEMENT_HANDLERS: dict[type[Statement], _NodeHandler] = {
    LetStatement: _OwnershipWalker._let_statement,
    VarStatement: _OwnershipWalker._var_statement,
    TupleDestructureStatement: _OwnershipWalker._tuple_destructure,
    AssignStatement: _OwnershipWalker._assign_statement,
    IndexAssignStatement: _OwnershipWalker._index_assign_statement,
    FieldAssignStatement: _OwnershipWalker._field_assign_statement,
    IfStatement: _OwnershipWalker._if_statement,
    WhileStatement: _OwnershipWalker._while_statement,
    ForStatement: _OwnershipWalker._for_statement,
    MatchStatement: _OwnershipWalker._match_statement,
    ReturnStatement: _OwnershipWalker._return_statement,
    BreakStatement: _OwnershipWalker._no_operands,
    ContinueStatement: _OwnershipWalker._no_operands,
    ExpressionStatement: _OwnershipWalker._expression_statement,
    AssertStatement: _OwnershipWalker._assert_statement,
}

_EXPRESSION_HANDLERS: dict[type[Expression], _NodeHandler] = {
    IntegerLiteral: _OwnershipWalker._no_operands,
    FloatLiteral: _OwnershipWalker._no_operands,
    StringLiteral: _OwnershipWalker._no_operands,
    BooleanLiteral: _OwnershipWalker._no_operands,
    FStringExpr: _OwnershipWalker._fstring,
    Identifier: _OwnershipWalker._identifier,
    # Constructor names are capitalised, so this cannot name a `var`; it is
    # treated as an identifier anyway, which can only remove names.
    TypeIdentifier: _OwnershipWalker._identifier,
    ListLiteral: _OwnershipWalker._sequence,
    MapLiteral: _OwnershipWalker._map_literal,
    ListComprehension: _OwnershipWalker._list_comprehension,
    AwaitExpr: _OwnershipWalker._await,
    BinaryOp: _OwnershipWalker._binary_op,
    UnaryOp: _OwnershipWalker._unary_op,
    FunctionCall: _OwnershipWalker._function_call,
    IndexAccess: _OwnershipWalker._index_access,
    FieldAccess: _OwnershipWalker._field_access,
    Pipeline: _OwnershipWalker._pipeline,
    PlaceholderExpr: _OwnershipWalker._no_operands,
    LambdaExpr: _OwnershipWalker._lambda,
    MatchExpr: _OwnershipWalker._match_expr,
    ConstructorCall: _OwnershipWalker._constructor_call,
    TupleExpr: _OwnershipWalker._sequence,
    TypedHole: _OwnershipWalker._typed_hole,
    PropagateExpr: _OwnershipWalker._propagate,
    WithExpr: _OwnershipWalker._with_expr,
}

_PATTERN_HANDLERS: dict[type[Pattern], Callable[[_OwnershipWalker, Any], None]] = {
    WildcardPattern: _OwnershipWalker._no_bindings,
    VariablePattern: _OwnershipWalker._variable_pattern,
    ConstructorPattern: _OwnershipWalker._constructor_pattern,
    LiteralPattern: _OwnershipWalker._no_bindings,
    ListPattern: _OwnershipWalker._sequence_pattern,
    TuplePattern: _OwnershipWalker._sequence_pattern,
    RestPattern: _OwnershipWalker._rest_pattern,
}
