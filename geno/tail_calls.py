"""Self tail calls (proposal 0022): which calls they are, and how to run them.

A function's call to itself in tail position runs in constant stack on every
backend. This module is the one definition of "tail position" the interpreter
and both compilers share, so a program is as deep on one as on another.

A call is a self tail call when all of these hold:

* It is in a top-level, non-async function, and its callee is that function's
  own name, written as a plain name the function does not rebind.
* It is the whole value of a ``return``; or the value of a branch of an ``if``
  or ``match`` expression that is itself in such a position; or, in a function
  that returns ``Unit``, the last statement of the body, through the last
  statement of an ``if`` or ``match`` statement.
* It is not inside a lambda.

Everything else is an ordinary call: mutual recursion, a call through a
function value, a pipeline stage, ``f(x)?``, a call whose result is used.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, fields, is_dataclass
from typing import Literal

from .ast_nodes import (
    BooleanLiteral,
    ExpressionStatement,
    ForStatement,
    FunctionCall,
    FunctionDef,
    Identifier,
    IfStatement,
    LambdaExpr,
    LetStatement,
    ListComprehension,
    MatchExpr,
    MatchStatement,
    Pipeline,
    RestPattern,
    ReturnStatement,
    SimpleType,
    Statement,
    TupleDestructureStatement,
    VariablePattern,
    VarStatement,
    WhileStatement,
)
from .entrypoint import is_async_execution_form


@dataclass(frozen=True)
class TailCallPlan:
    """How a function with self tail calls is run.

    ``form`` is for the compilers. ``"loop"`` rewrites the body as a loop that
    assigns the parameters; it is used when nothing can observe that the
    parameters are reused. ``"trampoline"`` runs each logical call as its own
    call of an inner function, so every call has its own bindings; it is used
    when the function creates a closure, has an ``ensures`` clause, or makes a
    tail call from inside a loop or an expression.

    ``snapshots`` says each logical call's ``ensures`` check has to run: a
    clause reads a parameter, so the checks differ, or calls a function, which
    may have an effect. Each call's bindings are then kept until the result is
    known. Otherwise every check would compute the same thing from the same
    result, and the last call's check stands for all of them.
    """

    calls: tuple[FunctionCall, ...]
    form: Literal["loop", "trampoline"]
    snapshots: bool


def _children(node: object) -> Iterator[object]:
    if isinstance(node, (list, tuple)):
        yield from node
    elif is_dataclass(node) and not isinstance(node, type):
        for item in fields(node):
            if not item.name.startswith("_") and item.name != "location":
                yield getattr(node, item.name)


def _walk(root: object) -> Iterator[object]:
    """Every node under *root*, lambdas and their bodies included."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node is None or isinstance(node, (str, int, float, bool)):
            continue
        yield node
        stack.extend(_children(node))


def _bound_names(defn: FunctionDef) -> set[str]:
    """Every name *defn* binds anywhere in its contracts, defaults and body."""
    names = {param.name for param in defn.params}
    for node in _walk([defn.specs, defn.body]):
        if isinstance(node, (LetStatement, VarStatement)):
            names.add(node.name)
        elif isinstance(node, TupleDestructureStatement):
            names.update(node.names)
        elif isinstance(node, (ForStatement, ListComprehension)):
            names.add(node.variable)
        elif isinstance(node, (VariablePattern, RestPattern)):
            if node.name is not None:
                names.add(node.name)
        elif isinstance(node, LambdaExpr):
            names.update(param.name for param in node.params)
    return names


def _returns_unit(defn: FunctionDef) -> bool:
    return isinstance(defn.return_type, SimpleType) and defn.return_type.name == "Unit"


class _Finder:
    """Collects the self tail calls of one function."""

    def __init__(self, defn: FunctionDef) -> None:
        self.name = defn.name
        self.calls: list[FunctionCall] = []
        # A tail call the loop form cannot express: inside a loop, or the
        # value of an `if`/`match` expression.
        self.needs_trampoline = False

    def expression(self, expr: object, direct: bool) -> None:
        if isinstance(expr, FunctionCall):
            callee = expr.function
            if isinstance(callee, Identifier) and callee.name == self.name:
                self.calls.append(expr)
                if not direct:
                    self.needs_trampoline = True
        elif isinstance(expr, MatchExpr):
            # An arm's value is the `return` that ends its body.
            for arm in expr.arms:
                if arm.body and isinstance(arm.body[-1], ReturnStatement):
                    self.expression(arm.body[-1].value, False)

    def returns(self, statements: list[Statement], in_loop: bool) -> None:
        """Every `return` under *statements*, however deeply nested."""
        for stmt in statements:
            if isinstance(stmt, ReturnStatement):
                self.expression(stmt.value, not in_loop)
            elif isinstance(stmt, IfStatement):
                self.returns(stmt.then_body, in_loop)
                self.returns(stmt.else_body, in_loop)
            elif isinstance(stmt, MatchStatement):
                for arm in stmt.arms:
                    self.returns(arm.body, in_loop)
            elif isinstance(stmt, (ForStatement, WhileStatement)):
                self.returns(stmt.body, True)

    def last_statement(self, statements: list[Statement]) -> None:
        """The call a `Unit` function ends with, when it ends with one."""
        if not statements:
            return
        last = statements[-1]
        if isinstance(last, ExpressionStatement):
            self.expression(last.expression, True)
        elif isinstance(last, IfStatement):
            self.last_statement(last.then_body)
            self.last_statement(last.else_body)
        elif isinstance(last, MatchStatement):
            for arm in last.arms:
                self.last_statement(arm.body)


def plan_tail_calls(defn: FunctionDef) -> TailCallPlan | None:
    """The plan for top-level function *defn*, or ``None`` without a tail call."""
    if is_async_execution_form(defn) or not defn.body:
        return None
    finder = _Finder(defn)
    finder.returns(defn.body, False)
    if _returns_unit(defn):
        finder.last_statement(defn.body)
    if not finder.calls:
        return None
    # A parameter or local of the function's own name hides the function.
    # Scopes are ignored: a name bound anywhere in the function rules it out.
    if defn.name in _bound_names(defn):
        return None

    checked = [
        clause.condition
        for clause in defn.specs.ensures
        if not isinstance(clause.condition, BooleanLiteral)
        or not clause.condition.value
    ]
    params = {param.name for param in defn.params}
    snapshots = any(
        (isinstance(node, Identifier) and node.name in params)
        or isinstance(node, (FunctionCall, Pipeline))
        for node in _walk(checked)
    )
    creates_closure = any(
        isinstance(node, LambdaExpr)
        for node in _walk(
            [defn.specs, defn.body, [param.default_value for param in defn.params]]
        )
    )
    trampoline = bool(checked) or creates_closure or finder.needs_trampoline
    return TailCallPlan(
        calls=tuple(finder.calls),
        form="trampoline" if trampoline else "loop",
        snapshots=snapshots,
    )


def mark_self_tail_calls(defn: FunctionDef) -> TailCallPlan | None:
    """Mark *defn*'s self tail calls for the interpreter; return the plan."""
    plan = plan_tail_calls(defn)
    if plan is not None:
        for call in plan.calls:
            call._self_tail_call = True
    return plan
