"""
Symbol table for scope-aware rename and find-references.

Walks the AST and builds a mapping from each identifier usage to its
definition site, enabling semantic (not text-based) symbol resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .ast_nodes import (
    AssertStatement,
    AssignStatement,
    CallArg,
    ConstructorCall,
    ConstructorPattern,
    Expression,
    ExpressionStatement,
    FieldAccess,
    FieldAssignStatement,
    ForStatement,
    FunctionCall,
    FunctionDef,
    FunctionType,
    Identifier,
    IfStatement,
    ImplDef,
    ImportStatement,
    IndexAssignStatement,
    LambdaExpr,
    LetStatement,
    ListPattern,
    MatchArm,
    MatchExpr,
    MatchStatement,
    Parameter,
    Pattern,
    Program,
    RestPattern,
    ReturnStatement,
    SimpleType,
    Statement,
    TestBlock,
    TraitDef,
    TryStatement,
    TupleDestructureStatement,
    TypeAlias,
    TypeAnnotation,
    TypeDef,
    TypeIdentifier,
    VariablePattern,
    VarStatement,
    WhileStatement,
)
from .tokens import SourceLocation


@dataclass(frozen=True)
class SymbolDef:
    """A symbol definition site."""

    name: str
    location: SourceLocation
    kind: str  # "function", "variable", "parameter", "type", "trait", "constructor", "module"
    module_name: str | None = None


@dataclass(frozen=True)
class SymbolRef:
    """A reference to a symbol."""

    name: str
    location: SourceLocation
    definition: SymbolDef


class Scope:
    """A lexical scope that maps names to definitions."""

    def __init__(self, parent: Scope | None = None):
        self.parent = parent
        self.bindings: dict[str, SymbolDef] = {}

    def bind(self, name: str, defn: SymbolDef) -> None:
        self.bindings[name] = defn

    def lookup(self, name: str) -> SymbolDef | None:
        if name in self.bindings:
            return self.bindings[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def child(self) -> Scope:
        return Scope(parent=self)


@dataclass
class SymbolTable:
    """Maps identifier locations to their definitions."""

    # All definitions found
    definitions: list[SymbolDef] = field(default_factory=list)
    # All references found (each mapped to its definition)
    references: list[SymbolRef] = field(default_factory=list)
    # Module namespace bindings (for qualified access like Module.func)
    module_scopes: dict[str, Scope] = field(default_factory=dict)

    def refs_for_def(self, defn: SymbolDef) -> list[SymbolRef]:
        """Find all references that point to a given definition."""
        return [r for r in self.references if r.definition == defn]

    def def_at(self, filename: str, line: int, col: int) -> SymbolDef | None:
        """Find the definition at the given location."""
        for d in self.definitions:
            if (
                d.location.filename == filename
                and d.location.line == line
                and d.location.column <= col < d.location.column + len(d.name)
            ):
                return d
        return None

    def ref_at(self, filename: str, line: int, col: int) -> SymbolRef | None:
        """Find the reference at the given location."""
        for r in self.references:
            if (
                r.location.filename == filename
                and r.location.line == line
                and r.location.column <= col < r.location.column + len(r.name)
            ):
                return r
        return None

    def symbol_at(
        self, filename: str, line: int, col: int, name: str | None = None
    ) -> SymbolDef | None:
        """Find the definition that the symbol at (line, col) refers to.

        Works whether the cursor is on a definition or a reference.
        If name is provided, uses it for name-based fallback lookup.
        """
        # Check if cursor is on a definition
        d = self.def_at(filename, line, col)
        if d:
            return d
        # Check if cursor is on a reference
        r = self.ref_at(filename, line, col)
        if r:
            return r.definition
        # Fallback: find definition by name on the same line
        if name:
            for d in self.definitions:
                if (
                    d.name == name
                    and d.location.filename == filename
                    and d.location.line == line
                ):
                    return d
            # Check references by name on same line
            for r in self.references:
                if (
                    r.name == name
                    and r.location.filename == filename
                    and r.location.line == line
                ):
                    return r.definition
        return None

    def all_locations(self, defn: SymbolDef) -> list[SourceLocation]:
        """Return definition + all reference locations for a symbol."""
        locs = [defn.location]
        for r in self.references:
            if r.definition == defn:
                locs.append(r.location)
        return locs


class SymbolTableBuilder:
    """Walk an AST and build a SymbolTable."""

    def __init__(self) -> None:
        self.table = SymbolTable()
        self._module_export_scopes: dict[str, Scope] = {}
        self._module_local_scopes: dict[str, Scope] = {}

    def build(
        self,
        program: Program,
        filename: str = "<unknown>",
        modules: dict[str, Program] | None = None,
    ) -> SymbolTable:
        """Build a symbol table from a program and its imported modules."""
        global_scope = Scope()
        self._module_export_scopes = {}
        self._module_local_scopes = {}

        # First pass: collect module export scopes for qualified/member access.
        if modules:
            for mod_name, mod_program in modules.items():
                export_scope = Scope()
                self._collect_definitions(
                    mod_program,
                    export_scope,
                    mod_name,
                    exported_only=True,
                    record=False,
                )
                self._module_export_scopes[mod_name] = export_scope
                self.table.module_scopes[mod_name] = export_scope

        # Second pass: build local module scopes with import visibility.
        if modules:
            for mod_name, mod_program in modules.items():
                mod_scope = Scope()
                self._bind_imports(
                    mod_program,
                    mod_scope,
                    modules,
                    resolved=set(),
                    import_stack={mod_name},
                )
                local_export_scope: Scope | None = self._module_export_scopes.get(
                    mod_name
                )
                if local_export_scope is not None:
                    # Reuse export-scope SymbolDef objects so semantic locations
                    # do not depend on duplicate dataclass construction staying
                    # bit-identical across passes.
                    for sym in local_export_scope.bindings.values():
                        mod_scope.bind(sym.name, sym)
                        self.table.definitions.append(sym)
                self._collect_definitions(
                    mod_program,
                    mod_scope,
                    mod_name,
                    private_only=True,
                )
                self._module_local_scopes[mod_name] = mod_scope

        # Third pass: bind imports and definitions in the focused program.
        if modules:
            self._bind_imports(
                program,
                global_scope,
                modules,
                resolved=set(),
                import_stack=set(),
            )
        else:
            self._bind_imports(
                program,
                global_scope,
                {},
                resolved=set(),
                import_stack=set(),
            )
        self._collect_definitions(program, global_scope, filename)

        # Fourth pass: resolve references in all modules.
        if modules:
            for mod_name, mod_program in modules.items():
                mod_scope = self._module_local_scopes.get(mod_name, Scope())
                self._resolve_program(mod_program, mod_scope)

        # Resolve references in main program
        self._resolve_program(program, global_scope)

        return self.table

    @staticmethod
    def _program_has_explicit_exports(program: Program) -> bool:
        """Return whether a module uses explicit export markers."""
        return any(
            isinstance(defn, (FunctionDef, TypeDef, TypeAlias))
            and bool(getattr(defn, "exported", False))
            for defn in program.definitions
        )

    def _bind_imports(
        self,
        program: Program,
        scope: Scope,
        modules: dict[str, Program],
        *,
        resolved: set[str],
        import_stack: set[str],
    ) -> None:
        """Bind names made visible by import statements into one scope."""
        for defn in program.definitions:
            if isinstance(defn, ImportStatement):
                self._bind_import(
                    defn,
                    scope,
                    modules,
                    resolved=resolved,
                    import_stack=import_stack,
                )

    def _bind_import(
        self,
        import_stmt: ImportStatement,
        scope: Scope,
        modules: dict[str, Program],
        *,
        resolved: set[str],
        import_stack: set[str],
    ) -> None:
        """Bind one import statement using the same visibility rules as typecheck."""
        module_name = import_stmt.module_name
        if module_name in resolved:
            return

        module_def = SymbolDef(
            import_stmt.alias or module_name,
            import_stmt.location,
            "module",
            module_name=module_name,
        )
        scope.bind(module_def.name, module_def)
        self.table.definitions.append(module_def)

        mod_program = modules.get(module_name)
        if mod_program is None:
            resolved.add(module_name)
            return

        if module_name not in import_stack:
            import_stack.add(module_name)
            self._bind_imports(
                mod_program,
                scope,
                modules,
                resolved=resolved,
                import_stack=import_stack,
            )
            import_stack.remove(module_name)

        if import_stmt.alias is None:
            export_scope = self._module_export_scopes.get(module_name)
            if export_scope is not None:
                for name, defn in export_scope.bindings.items():
                    scope.bind(name, defn)

        resolved.add(module_name)

    def _collect_definitions(
        self,
        program: Program,
        scope: Scope,
        filename: str,
        *,
        exported_only: bool = False,
        private_only: bool = False,
        record: bool = True,
    ) -> None:
        """First pass: collect function, type, and constructor definitions."""
        has_explicit_exports = self._program_has_explicit_exports(program)
        for defn in program.definitions:
            if isinstance(defn, FunctionDef):
                if exported_only and has_explicit_exports and not defn.exported:
                    continue
                if private_only and (not has_explicit_exports or defn.exported):
                    continue
                sym = SymbolDef(defn.name, defn.location, "function")
                scope.bind(defn.name, sym)
                if record:
                    self.table.definitions.append(sym)
            elif isinstance(defn, TypeDef):
                if exported_only and has_explicit_exports and not defn.exported:
                    continue
                if private_only and (not has_explicit_exports or defn.exported):
                    continue
                sym = SymbolDef(defn.name, defn.location, "type")
                scope.bind(defn.name, sym)
                if record:
                    self.table.definitions.append(sym)
                for variant in defn.variants:
                    vsym = SymbolDef(variant.name, variant.location, "constructor")
                    scope.bind(variant.name, vsym)
                    if record:
                        self.table.definitions.append(vsym)
            elif isinstance(defn, TypeAlias):
                exported = bool(getattr(defn, "exported", False))
                if exported_only and has_explicit_exports and not exported:
                    continue
                if private_only and (not has_explicit_exports or exported):
                    continue
                sym = SymbolDef(defn.name, defn.location, "type")
                scope.bind(defn.name, sym)
                if record:
                    self.table.definitions.append(sym)
            elif isinstance(defn, TraitDef):
                sym = SymbolDef(defn.name, defn.location, "trait")
                scope.bind(defn.name, sym)
                if record:
                    self.table.definitions.append(sym)

    def _resolve_program(self, program: Program, scope: Scope) -> None:
        """Resolve all identifier references in a program."""
        for defn in program.definitions:
            if isinstance(defn, FunctionDef):
                self._resolve_function(defn, scope)
            elif isinstance(defn, TypeAlias):
                self._resolve_type_annotation(defn.target_type, scope)
            elif isinstance(defn, TypeDef):
                for variant in defn.variants:
                    for _field_name, field_type in variant.fields:
                        self._resolve_type_annotation(field_type, scope)
            elif isinstance(defn, TraitDef):
                self._resolve_trait_def(defn, scope)
            elif isinstance(defn, ImplDef):
                self._resolve_impl_def(defn, scope)
            elif isinstance(defn, TestBlock):
                # Walk the test-block body so ``assert`` expressions and
                # any other statements in the block get their references
                # resolved.  Without this, the ``AssertStatement`` case
                # added for F-0018 would be unreachable from the public
                # ``build_symbol_table`` entry point.
                test_scope = scope.child()
                self._resolve_statements(defn.body, test_scope)

    def _resolve_function(self, func: FunctionDef, parent_scope: Scope) -> None:
        """Resolve references within a function body."""
        func_scope = parent_scope.child()

        # Bind parameters
        for param in func.params:
            sym = SymbolDef(param.name, param.location, "parameter")
            func_scope.bind(param.name, sym)
            self.table.definitions.append(sym)
            self._resolve_type_annotation(param.param_type, parent_scope)
            if param.default_value is not None:
                self._resolve_expr(param.default_value, func_scope)

        self._resolve_type_annotation(func.return_type, parent_scope)

        # Resolve body
        self._resolve_statements(func.body, func_scope)

    def _resolve_trait_def(self, trait: TraitDef, parent_scope: Scope) -> None:
        """Resolve references within a trait definition."""
        for method in trait.methods:
            method_scope = parent_scope.child()
            for param in method.params:
                sym = SymbolDef(param.name, param.location, "parameter")
                method_scope.bind(param.name, sym)
                self.table.definitions.append(sym)
                self._resolve_type_annotation(param.param_type, parent_scope)
                if param.default_value is not None:
                    self._resolve_expr(param.default_value, method_scope)
            self._resolve_type_annotation(method.return_type, parent_scope)

    def _resolve_impl_def(self, impl: ImplDef, parent_scope: Scope) -> None:
        """Resolve references within an impl block."""
        trait_def = parent_scope.lookup(impl.trait_name)
        if trait_def:
            self.table.references.append(
                SymbolRef(impl.trait_name, impl.location, trait_def)
            )

        target_def = parent_scope.lookup(impl.target_type)
        if target_def:
            self.table.references.append(
                SymbolRef(impl.target_type, impl.location, target_def)
            )

        for method in impl.methods:
            self._resolve_function(method, parent_scope)

    def _resolve_type_annotation(
        self, type_annot: TypeAnnotation, scope: Scope
    ) -> None:
        """Resolve symbol references that appear inside a type annotation."""
        if isinstance(type_annot, SimpleType):
            defn = scope.lookup(type_annot.name)
            if defn:
                self.table.references.append(
                    SymbolRef(type_annot.name, type_annot.location, defn)
                )
            for param in type_annot.type_params:
                self._resolve_type_annotation(param, scope)
        elif isinstance(type_annot, FunctionType):
            for param_type in type_annot.param_types:
                self._resolve_type_annotation(param_type, scope)
            self._resolve_type_annotation(type_annot.return_type, scope)

    def _resolve_statements(self, stmts: list[Statement], scope: Scope) -> None:
        for stmt in stmts:
            self._resolve_statement(stmt, scope)

    def _resolve_statement(self, stmt: Statement, scope: Scope) -> None:
        if isinstance(stmt, (LetStatement, VarStatement)):
            if stmt.type_annotation is not None:
                self._resolve_type_annotation(stmt.type_annotation, scope)
            self._resolve_expr(stmt.value, scope)
            sym = SymbolDef(stmt.name, stmt.location, "variable")
            scope.bind(stmt.name, sym)
            self.table.definitions.append(sym)

        elif isinstance(stmt, TupleDestructureStatement):
            self._resolve_type_annotation(stmt.type_annotation, scope)
            self._resolve_expr(stmt.value, scope)
            for name in stmt.names:
                sym = SymbolDef(name, stmt.location, "variable")
                scope.bind(name, sym)
                self.table.definitions.append(sym)

        elif isinstance(stmt, AssignStatement):
            # The target is a reference to an existing variable
            target_def = scope.lookup(stmt.target)
            if target_def:
                self.table.references.append(
                    SymbolRef(stmt.target, stmt.location, target_def)
                )
            self._resolve_expr(stmt.value, scope)

        elif isinstance(stmt, IndexAssignStatement):
            # ``arr[i] = value``: all three sub-expressions contain
            # identifier uses we want to record so rename / find-references
            # see them.  Before #663 these references were silently
            # dropped.
            self._resolve_expr(stmt.target, scope)
            self._resolve_expr(stmt.index, scope)
            self._resolve_expr(stmt.value, scope)

        elif isinstance(stmt, FieldAssignStatement):
            # ``obj.field = value``: the target expression walks through
            # the normal expression resolver (which records the ``obj``
            # reference); the RHS is resolved separately.  The bare
            # field name does not correspond to a binding, so nothing
            # to record for it.
            self._resolve_expr(stmt.target, scope)
            self._resolve_expr(stmt.value, scope)

        elif isinstance(stmt, AssertStatement):
            # ``assert cond`` inside a ``test`` block: the condition
            # expression may reference bindings that need resolving
            # (and renaming) just like any other expression.
            self._resolve_expr(stmt.expression, scope)

        elif isinstance(stmt, ReturnStatement):
            if stmt.value:
                self._resolve_expr(stmt.value, scope)

        elif isinstance(stmt, ExpressionStatement):
            self._resolve_expr(stmt.expression, scope)

        elif isinstance(stmt, IfStatement):
            self._resolve_expr(stmt.condition, scope)
            self._resolve_statements(stmt.then_body, scope.child())
            if stmt.else_body:
                self._resolve_statements(stmt.else_body, scope.child())
            for elif_cond, elif_body in getattr(stmt, "elif_clauses", []):
                self._resolve_expr(elif_cond, scope)
                self._resolve_statements(elif_body, scope.child())

        elif isinstance(stmt, WhileStatement):
            self._resolve_expr(stmt.condition, scope)
            self._resolve_statements(stmt.body, scope.child())

        elif isinstance(stmt, ForStatement):
            self._resolve_type_annotation(stmt.var_type, scope)
            self._resolve_expr(stmt.iterable, scope)
            for_scope = scope.child()
            sym = SymbolDef(stmt.variable, stmt.location, "variable")
            for_scope.bind(stmt.variable, sym)
            self.table.definitions.append(sym)
            self._resolve_statements(stmt.body, for_scope)

        elif isinstance(stmt, MatchStatement):
            self._resolve_expr(stmt.scrutinee, scope)
            for arm in stmt.arms:
                self._resolve_match_arm(arm, scope)

        elif isinstance(stmt, TryStatement):
            self._resolve_statements(stmt.try_body, scope.child())
            catch = stmt.catch_clause
            catch_scope = scope.child()
            self._resolve_type_annotation(catch.type_annotation, scope)
            sym = SymbolDef(catch.variable, catch.location, "variable")
            catch_scope.bind(catch.variable, sym)
            self.table.definitions.append(sym)
            self._resolve_statements(catch.body, catch_scope)

    def _resolve_match_arm(self, arm: MatchArm, scope: Scope) -> None:
        arm_scope = scope.child()
        self._bind_pattern(arm.pattern, arm_scope)
        if arm.guard:
            self._resolve_expr(arm.guard, arm_scope)
        self._resolve_statements(arm.body, arm_scope)

    def _bind_pattern(self, pattern: Pattern, scope: Scope) -> None:
        """Bind variables introduced by a match pattern."""
        if isinstance(pattern, VariablePattern):
            if pattern.name != "_":
                sym = SymbolDef(pattern.name, pattern.location, "variable")
                scope.bind(pattern.name, sym)
                self.table.definitions.append(sym)
        elif isinstance(pattern, ConstructorPattern):
            # The constructor name is a reference
            ctor_def = scope.lookup(pattern.constructor)
            if ctor_def:
                self.table.references.append(
                    SymbolRef(pattern.constructor, pattern.location, ctor_def)
                )
            for sub in pattern.subpatterns:
                self._bind_pattern(sub, scope)
        elif isinstance(pattern, ListPattern):
            # ``[x, y, ...rest]``: each element is itself a pattern and
            # may bind variables into the arm scope.  Previously the
            # resolver ignored list patterns entirely and the bindings
            # were invisible to rename / go-to-definition (F-0019).
            for elem in pattern.elements:
                self._bind_pattern(elem, scope)
        elif isinstance(pattern, RestPattern):
            # ``...rest`` binds ``rest`` to the tail slice; anonymous
            # ``...`` has ``name is None`` and binds nothing.
            if pattern.name is not None:
                sym = SymbolDef(pattern.name, pattern.location, "variable")
                scope.bind(pattern.name, sym)
                self.table.definitions.append(sym)

    def _resolve_expr(self, expr: Expression, scope: Scope) -> None:
        """Resolve identifier references in an expression."""
        if expr is None:
            return

        if isinstance(expr, (Identifier, TypeIdentifier)):
            defn = scope.lookup(expr.name)
            if defn:
                self.table.references.append(SymbolRef(expr.name, expr.location, defn))

        elif isinstance(expr, FunctionCall):
            self._resolve_expr(expr.function, scope)
            for arg in expr.arguments:
                if isinstance(arg, CallArg):
                    self._resolve_expr(arg.value, scope)
                elif isinstance(arg, Expression):
                    self._resolve_expr(arg, scope)

        elif isinstance(expr, FieldAccess):
            # Module.member or record.field
            self._resolve_expr(expr.target, scope)
            # If target is a module identifier, resolve the member
            if isinstance(expr.target, (Identifier, TypeIdentifier)):
                target_def = scope.lookup(expr.target.name)
                mod_scope = None
                if target_def and target_def.kind == "module":
                    mod_scope = self._module_export_scopes.get(
                        target_def.module_name or target_def.name
                    )
                if mod_scope is None:
                    mod_scope = self.table.module_scopes.get(expr.target.name)
                if mod_scope:
                    member_def = mod_scope.lookup(expr.field_name)
                    if member_def:
                        self.table.references.append(
                            SymbolRef(
                                expr.field_name,
                                SourceLocation(
                                    expr.location.line,
                                    expr.location.column + len(expr.target.name) + 1,
                                    expr.location.filename,
                                ),
                                member_def,
                            )
                        )

        elif isinstance(expr, LambdaExpr):
            lam_scope = scope.child()
            for param in expr.params:
                sym = SymbolDef(param.name, param.location, "parameter")
                lam_scope.bind(param.name, sym)
                self.table.definitions.append(sym)
                self._resolve_type_annotation(param.param_type, scope)
                if param.default_value is not None:
                    self._resolve_expr(param.default_value, lam_scope)
            if expr.return_type is not None:
                self._resolve_type_annotation(expr.return_type, scope)
            if expr.body:
                self._resolve_expr(expr.body, lam_scope)
            if expr.block_body:
                self._resolve_statements(expr.block_body, lam_scope)

        elif isinstance(expr, MatchExpr):
            self._resolve_expr(expr.scrutinee, scope)
            for arm in expr.arms:
                self._resolve_match_arm(arm, scope)

        elif isinstance(expr, ConstructorCall):
            ctor_def = scope.lookup(expr.constructor)
            if ctor_def:
                self.table.references.append(
                    SymbolRef(expr.constructor, expr.location, ctor_def)
                )
            for ctor_arg in expr.arguments:
                self._resolve_expr(ctor_arg, scope)

        else:
            # For other expression types, recurse into sub-expressions
            self._resolve_subexpressions(expr, scope)

    def _resolve_subexpressions(self, expr: Expression, scope: Scope) -> None:
        """Generic handler: resolve all Expression-typed fields."""
        for attr_name in vars(expr):
            if attr_name.startswith("_"):
                continue
            val = getattr(expr, attr_name)
            if isinstance(val, Expression):
                self._resolve_expr(val, scope)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, Expression):
                        self._resolve_expr(item, scope)
                    elif isinstance(item, Statement):
                        self._resolve_statement(item, scope)


def build_symbol_table(
    program: Program,
    filename: str = "<unknown>",
    modules: dict[str, Program] | None = None,
) -> SymbolTable:
    """Convenience function to build a symbol table."""
    builder = SymbolTableBuilder()
    return builder.build(program, filename, modules)
