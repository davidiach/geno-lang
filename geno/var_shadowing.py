"""Warn when a declaration redeclares a mutable variable (W300).

Inside a nested block, ``var y = f(y)`` where an enclosing ``y`` is a ``var``
declares a second ``y`` instead of updating the first one, and the new value
is lost when the block ends. In the same block, redeclaration rebinds the
existing variable instead. This pass distinguishes those cases in a warning
once the program passes the type checker.

The rule is syntactic: a ``let`` or ``var`` warns when the nearest binding of
its name, in the same block or an enclosing one of the same function body, is
a ``var``. Lambda bodies are included because they may assign the ``var`` they
capture. Parameters, loop variables, catch variables and pattern bindings are
immutable, so declarations that shadow them do not warn.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .ast_nodes import (
    Expression,
    ForStatement,
    FunctionDef,
    IfStatement,
    ImplDef,
    LambdaExpr,
    LetStatement,
    ListComprehension,
    MatchArm,
    MatchExpr,
    MatchStatement,
    Parameter,
    Pattern,
    Program,
    RestPattern,
    Statement,
    TestBlock,
    TupleDestructureStatement,
    TypeAnnotation,
    VariablePattern,
    VarStatement,
    WhileStatement,
)
from .diagnostics import Diagnostic, ErrorCode, Severity
from .tokens import SourceLocation

_STD_DIR = (Path(__file__).parent / "std").resolve()


class _Scope:
    """Names bound in one block: name -> declaration line of a ``var``, or None."""

    def __init__(self, parent: _Scope | None = None) -> None:
        self.parent = parent
        self.names: dict[str, int | None] = {}

    def child(self) -> _Scope:
        return _Scope(self)

    def nearest(self, name: str) -> tuple[bool, int | None]:
        """Return (found, var line) for the nearest binding of ``name``."""
        scope: _Scope | None = self
        while scope is not None:
            if name in scope.names:
                return True, scope.names[name]
            scope = scope.parent
        return False, None


class _Finder:
    def __init__(self) -> None:
        self.diagnostics: list[Diagnostic] = []

    # -- bindings ---------------------------------------------------------

    def declare(
        self, name: str, mutable: bool, location: SourceLocation, scope: _Scope
    ) -> None:
        if name == "_":
            return
        found, var_line = scope.nearest(name)
        if found and var_line is not None:
            keyword = "var" if mutable else "let"
            if name in scope.names:
                message = (
                    f"'{keyword} {name}' rebinds the mutable '{name}' from "
                    f"line {var_line} in the same block. Existing closures "
                    f"observe its new value. Write '{name} = ...' to update "
                    "it without redeclaring, or choose a different name"
                )
            else:
                message = (
                    f"'{keyword} {name}' declares a new variable that hides "
                    f"the mutable '{name}' from line {var_line}, which keeps "
                    f"its old value. Write '{name} = ...' to update it, or "
                    "choose a different name"
                )
            self.diagnostics.append(
                Diagnostic(
                    code=ErrorCode.WARN_REDECLARED_VAR,
                    message=message,
                    severity=Severity.WARNING,
                    location=location,
                )
            )
        scope.names[name] = location.line if mutable else None

    @staticmethod
    def bind_immutable(name: str | None, scope: _Scope) -> None:
        if name and name != "_":
            scope.names[name] = None

    def bind_pattern(self, pattern: Pattern, scope: _Scope) -> None:
        if isinstance(pattern, (VariablePattern, RestPattern)):
            self.bind_immutable(pattern.name, scope)
            return
        for value in _field_values(pattern):
            for item in _flatten(value):
                if isinstance(item, Pattern):
                    self.bind_pattern(item, scope)

    # -- traversal --------------------------------------------------------

    def function(self, params: list[Parameter], body: list[Statement]) -> None:
        scope = _Scope()
        for param in params:
            # Defaults are checked in global scope, outside the function's
            # parameters and locals. Their lambdas still need their own walk.
            self.expression(param.default_value, _Scope())
            self.bind_immutable(param.name, scope)
        self.block(body, scope)

    def block(self, statements: list[Statement], scope: _Scope) -> None:
        for statement in statements:
            self.statement(statement, scope)

    def arm(self, arm: MatchArm, scope: _Scope) -> None:
        arm_scope = scope.child()
        self.bind_pattern(arm.pattern, arm_scope)
        if arm.guard is not None:
            self.expression(arm.guard, arm_scope)
        self.block(arm.body, arm_scope)

    def statement(self, stmt: Statement, scope: _Scope) -> None:
        if isinstance(stmt, (LetStatement, VarStatement)):
            self.expression(stmt.value, scope)
            self.declare(
                stmt.name, isinstance(stmt, VarStatement), stmt.location, scope
            )
        elif isinstance(stmt, TupleDestructureStatement):
            self.expression(stmt.value, scope)
            for name in stmt.names:
                self.declare(name, stmt.mutable, stmt.location, scope)
        elif isinstance(stmt, IfStatement):
            self.expression(stmt.condition, scope)
            self.block(stmt.then_body, scope.child())
            if stmt.else_body:
                self.block(stmt.else_body, scope.child())
        elif isinstance(stmt, WhileStatement):
            self.expression(stmt.condition, scope)
            self.block(stmt.body, scope.child())
        elif isinstance(stmt, ForStatement):
            self.expression(stmt.iterable, scope)
            body_scope = scope.child()
            self.bind_immutable(stmt.variable, body_scope)
            self.block(stmt.body, body_scope)
        elif isinstance(stmt, MatchStatement):
            self.expression(stmt.scrutinee, scope)
            for arm in stmt.arms:
                self.arm(arm, scope)
        else:
            self.children(stmt, scope)

    def expression(self, expr: Expression | None, scope: _Scope) -> None:
        if expr is None:
            return
        if isinstance(expr, LambdaExpr):
            body_scope = scope.child()
            for param in expr.params:
                self.bind_immutable(param.name, body_scope)
            if expr.block_body is not None:
                self.block(expr.block_body, body_scope)
            else:
                self.expression(expr.body, body_scope)
        elif isinstance(expr, MatchExpr):
            self.expression(expr.scrutinee, scope)
            for arm in expr.arms:
                self.arm(arm, scope)
        elif isinstance(expr, ListComprehension):
            self.expression(expr.iterable, scope)
            body_scope = scope.child()
            self.bind_immutable(expr.variable, body_scope)
            self.expression(expr.element_expr, body_scope)
            self.expression(expr.condition, body_scope)
        else:
            self.children(expr, scope)

    def children(self, node: Any, scope: _Scope) -> None:
        """Visit the expressions and nested statements of any other node."""
        for value in _field_values(node):
            for item in _flatten(value):
                if isinstance(item, Expression):
                    self.expression(item, scope)
                elif isinstance(item, Statement):
                    self.statement(item, scope)


def _field_values(node: Any) -> Iterator[Any]:
    for f in dataclasses.fields(node):
        if f.name == "location" or f.name.startswith("_"):
            continue
        yield getattr(node, f.name)


def _flatten(value: Any) -> Iterator[Any]:
    if isinstance(value, (Expression, Statement, Pattern)):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _flatten(item)
    elif (
        dataclasses.is_dataclass(value)
        and not isinstance(value, type)
        and not isinstance(value, TypeAnnotation)
    ):
        # Helper records such as named arguments hold expressions too.
        for item in _field_values(value):
            yield from _flatten(item)


def find_redeclared_vars(program: Program) -> list[Diagnostic]:
    """Return W300 warnings for one parsed program."""
    finder = _Finder()
    for defn in program.definitions:
        if isinstance(defn, FunctionDef):
            finder.function(defn.params, defn.body)
        elif isinstance(defn, ImplDef):
            for method in defn.methods:
                finder.function(method.params, method.body)
        elif isinstance(defn, TestBlock):
            finder.function([], defn.body)
    return finder.diagnostics


def is_std_module_path(path: str | Path) -> bool:
    """True when ``path`` is a standard-library module shipped with Geno."""
    if not str(path) or str(path).startswith("<"):
        # In-memory sources such as "<module:Math>" are never shipped files.
        return False
    try:
        return Path(path).resolve().is_relative_to(_STD_DIR)
    except (OSError, ValueError):
        return False


def project_warnings(dep_graph: Any) -> list[Diagnostic]:
    """Return W300 warnings for a project's own modules.

    Standard-library modules and installed dependencies are skipped: their
    authors, not the project, can act on the warning.
    """
    warnings: list[Diagnostic] = []
    for name in dep_graph.sorted_modules:
        resolved = dep_graph.file_map.get(name)
        if resolved is None or resolved.is_dependency:
            continue
        if is_std_module_path(resolved.path):
            continue
        warnings.extend(find_redeclared_vars(dep_graph.parsed[name]))
    return warnings
