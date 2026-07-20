"""Entrypoint discovery and declared return-type helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .ast_nodes import (
    FunctionDef,
    ImportStatement,
    Program,
    SimpleType,
    TypeAlias,
    TypeAnnotation,
    TypeDef,
)
from .types import IntType

_BUILTIN_TYPE_NAMES = frozenset(
    {
        "Array",
        "Async",
        "Bool",
        "Float",
        "Int",
        "List",
        "Map",
        "MutableMap",
        "Option",
        "Result",
        "Set",
        "String",
        "Unit",
        "Vec",
    }
)


def find_entrypoint_main(program: Program) -> FunctionDef | None:
    """Return the program's own ``main`` definition, excluding imports."""
    return next(
        (
            defn
            for defn in program.definitions
            if isinstance(defn, FunctionDef) and defn.name == "main"
        ),
        None,
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


@dataclass(frozen=True)
class _ScopedTypeAlias:
    """A type alias paired with the aliases visible where it was declared."""

    definition: TypeAlias
    scope: Mapping[str, _ScopedTypeAlias]


def _visible_scoped_type_aliases(
    program: Program,
    modules: Mapping[str, Program] | None,
) -> Mapping[str, _ScopedTypeAlias]:
    """Build the entry alias environment without losing defining scopes."""
    module_programs = modules or {}
    scope_cache: dict[int, dict[str, _ScopedTypeAlias]] = {}

    def exported_aliases(current: Program) -> list[TypeAlias]:
        has_exports = any(
            isinstance(defn, (FunctionDef, TypeAlias, TypeDef)) and defn.exported
            for defn in current.definitions
        )
        return [
            defn
            for defn in current.definitions
            if isinstance(defn, TypeAlias) and (not has_exports or defn.exported)
        ]

    def scope_for(current: Program) -> dict[str, _ScopedTypeAlias]:
        scope_key = id(current)
        cached = scope_cache.get(scope_key)
        if cached is not None:
            return cached

        scope: dict[str, _ScopedTypeAlias] = {}
        scope_cache[scope_key] = scope
        resolved: set[str] = set()

        def resolve_import(import_stmt: ImportStatement) -> None:
            module_name = import_stmt.module_name
            imported_program = module_programs.get(module_name)
            if module_name not in resolved:
                resolved.add(module_name)
                if imported_program is not None:
                    for defn in imported_program.definitions:
                        if isinstance(defn, ImportStatement):
                            resolve_import(defn)
            if import_stmt.alias is None and imported_program is not None:
                imported_scope = scope_for(imported_program)
                for alias_def in exported_aliases(imported_program):
                    scope[alias_def.name] = _ScopedTypeAlias(
                        alias_def,
                        imported_scope,
                    )

        for defn in current.definitions:
            if isinstance(defn, ImportStatement):
                resolve_import(defn)
        for defn in current.definitions:
            if isinstance(defn, TypeAlias):
                scope[defn.name] = _ScopedTypeAlias(defn, scope)
        return scope

    return scope_for(program)


def _scoped_annotation_resolves_to_int(
    annotation: TypeAnnotation,
    aliases: Mapping[str, _ScopedTypeAlias],
    bindings: Mapping[str, TypeAnnotation] | None = None,
    seen_aliases: frozenset[int] = frozenset(),
    seen_bindings: frozenset[str] = frozenset(),
) -> bool:
    if not isinstance(annotation, SimpleType):
        return False

    active_bindings = bindings or {}
    if (
        not annotation.type_params
        and annotation.name in active_bindings
        and annotation.name not in seen_bindings
    ):
        return _scoped_annotation_resolves_to_int(
            active_bindings[annotation.name],
            aliases,
            active_bindings,
            seen_aliases,
            seen_bindings | {annotation.name},
        )
    if annotation.name == "Int" and not annotation.type_params:
        return True
    if annotation.name == "Tuple" and annotation.type_params:
        return False
    if annotation.name in _BUILTIN_TYPE_NAMES:
        return False

    scoped_alias = aliases.get(annotation.name)
    if scoped_alias is None or id(scoped_alias.definition) in seen_aliases:
        return False
    resolved_args = [
        _resolve_bound_annotation(type_param, active_bindings)
        for type_param in annotation.type_params
    ]
    alias_bindings = dict(active_bindings)
    alias_bindings.update(
        zip(scoped_alias.definition.type_params, resolved_args, strict=False)
    )
    return _scoped_annotation_resolves_to_int(
        scoped_alias.definition.target_type,
        scoped_alias.scope,
        alias_bindings,
        seen_aliases | {id(scoped_alias.definition)},
        frozenset(),
    )


def _resolve_bound_annotation(
    annotation: TypeAnnotation,
    bindings: Mapping[str, TypeAnnotation],
    seen_names: frozenset[str] = frozenset(),
) -> TypeAnnotation:
    """Substitute enclosing alias bindings throughout a simple type."""
    if not isinstance(annotation, SimpleType):
        return annotation
    if not annotation.type_params:
        if annotation.name not in bindings or annotation.name in seen_names:
            return annotation
        return _resolve_bound_annotation(
            bindings[annotation.name],
            bindings,
            seen_names | {annotation.name},
        )
    return SimpleType(
        location=annotation.location,
        name=annotation.name,
        type_params=[
            _resolve_bound_annotation(type_param, bindings, seen_names)
            for type_param in annotation.type_params
        ],
    )


def entrypoint_returns_int(
    program: Program,
    modules: Mapping[str, Program] | None = None,
) -> bool:
    """Return whether the program's own ``main`` has declared type ``Int``."""
    main_def = find_entrypoint_main(program)
    if main_def is None:
        return False
    resolved_return_type = main_def.__dict__.get("_resolved_return_type")
    if resolved_return_type is not None:
        return isinstance(resolved_return_type, IntType)
    return _scoped_annotation_resolves_to_int(
        main_def.return_type,
        _visible_scoped_type_aliases(program, modules),
    )
