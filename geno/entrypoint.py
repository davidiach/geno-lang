"""Type aliases visible from a selected entry program."""

from __future__ import annotations

from collections.abc import Mapping

from .ast_nodes import FunctionDef, ImportStatement, Program, TypeAlias, TypeDef


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
