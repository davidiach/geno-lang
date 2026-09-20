"""Entrypoint execution form and type aliases visible from an entry program."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .ast_nodes import (
    AwaitExpr,
    FunctionDef,
    ImportStatement,
    LambdaExpr,
    Program,
    TypeAlias,
    TypeDef,
)


def visible_type_aliases(
    program: Program,
    modules: Mapping[str, Program] | None = None,
) -> dict[str, TypeAlias]:
    """Rebuild aliases visible to *program* using source import order."""
    module_programs = modules or {}
    exported_aliases: dict[str, dict[str, TypeAlias]] = {}
    for module_name, module_program in module_programs.items():
        has_exports = any(
            isinstance(defn, (FunctionDef, TypeAlias, TypeDef)) and defn.exported
            for defn in module_program.definitions
        )
        exported_aliases[module_name] = {
            defn.name: defn
            for defn in module_program.definitions
            if isinstance(defn, TypeAlias) and (not has_exports or defn.exported)
        }

    aliases: dict[str, TypeAlias] = {}
    resolved: set[str] = set()

    def resolve_import(import_stmt: ImportStatement) -> None:
        module_name = import_stmt.module_name
        if module_name not in resolved:
            resolved.add(module_name)
            imported_program = module_programs.get(module_name)
            if imported_program is not None:
                for defn in imported_program.definitions:
                    if isinstance(defn, ImportStatement):
                        resolve_import(defn)
        if import_stmt.alias is None:
            aliases.update(exported_aliases.get(module_name, {}))

    for defn in program.definitions:
        if isinstance(defn, ImportStatement):
            resolve_import(defn)
    for defn in program.definitions:
        if isinstance(defn, TypeAlias):
            aliases[defn.name] = defn
    return aliases


# Annotation fields carry type syntax, never an expression, so the walk below
# skips them the way the compiler's own body scans do.
_NON_EXPRESSION_FIELDS = frozenset(
    {
        "location",
        "type_annotation",
        "param_type",
        "return_type",
        "var_type",
        "hole_type",
    }
)


def _awaits_directly(body: Sequence[Any] | None) -> bool:
    """Return whether *body* awaits outside any nested function scope."""
    field_cache: dict[type, tuple[str, ...]] = {}
    stack: list[Any] = list(body or [])

    while stack:
        node = stack.pop()
        if isinstance(node, AwaitExpr):
            return True
        # A lambda or nested function owns its own async scope, so an `await`
        # inside one says nothing about the function being classified.
        if isinstance(node, (LambdaExpr, FunctionDef)):
            continue
        node_type = type(node)
        if node_type is list or node_type is tuple:
            stack.extend(node)
            continue
        if not hasattr(node, "__dict__"):
            continue

        field_names = field_cache.get(node_type)
        if field_names is None:
            field_names = tuple(
                name
                for name in vars(node)
                if not name.startswith("_") and name not in _NON_EXPRESSION_FIELDS
            )
            field_cache[node_type] = field_names

        for field_name in field_names:
            child = getattr(node, field_name)
            if child is not None:
                stack.append(child)

    return False


def is_async_execution_form(defn: FunctionDef) -> bool:
    """Return whether *defn* must be lowered and awaited as an async function.

    A function declared ``async`` is one.  So is a synchronous ``main`` that
    awaits in its own body: ``TypeChecker._check_await_expr`` accepts ``await``
    directly inside any ``main`` without an ``async`` modifier, and lowering
    such a ``main`` synchronously emits ``await`` outside an async function,
    which neither backend accepts.
    """
    if defn.is_async:
        return True
    if defn.name != "main":
        # `await` anywhere else is already a type error, so no other function
        # can become an asynchronous form without the modifier.
        return False
    return _awaits_directly(defn.body)
