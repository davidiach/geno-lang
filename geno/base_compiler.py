"""
Base Compiler
================

Shared infrastructure for the Python and JavaScript compiler backends.
Contains the output buffer, indentation management, definition state,
and target-independent compilation methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import fields, is_dataclass
from io import StringIO

from ._definition_index import DefinitionIndex
from .ast_nodes import (
    AssignStatement,
    CallArg,
    ConstructorPattern,
    Expression,
    FieldAccess,
    FieldAssignStatement,
    ForStatement,
    FunctionCall,
    FunctionDef,
    Identifier,
    IfStatement,
    ImplDef,
    ImportStatement,
    IndexAssignStatement,
    LambdaExpr,
    LetStatement,
    ListComprehension,
    ListPattern,
    MatchArm,
    Parameter,
    Pattern,
    Program,
    RestPattern,
    ReturnStatement,
    SimpleType,
    Statement,
    TraitDef,
    TraitMethodSig,
    TryStatement,
    TupleDestructureStatement,
    TypeAlias,
    TypeDef,
    TypeIdentifier,
    TypeVariant,
    VariablePattern,
    VarStatement,
    WhileStatement,
    WithExpr,
)
from .builtin_registry import all_builtin_names, builtin_param_name_lists
from .tokens import SourceLocation

# Built-in type variants that must be registered for dynamic field lookup.
# These mirror the type definitions in the typechecker but are expressed as
# TypeVariant objects so _get_constructor_field_name can resolve them.
_BUILTIN_LOC = SourceLocation(line=0, column=0, filename="<builtin>")
_DUMMY_TYPE = SimpleType(name="Any", location=_BUILTIN_LOC)


def _builtin_variant(name: str, field_names: list[str]) -> TypeVariant:
    """Create a TypeVariant for a built-in constructor with the given field names."""
    return TypeVariant(
        name=name,
        fields=[(f, _DUMMY_TYPE) for f in field_names],
        location=_BUILTIN_LOC,
    )


_BUILTIN_VARIANTS: list[TypeVariant] = [
    # Option
    _builtin_variant("Some", ["value"]),
    _builtin_variant("None", []),
    # Result
    _builtin_variant("Ok", ["value"]),
    _builtin_variant("Err", ["error"]),
    # JsonValue
    _builtin_variant("JsonString", ["value"]),
    _builtin_variant("JsonInt", ["value"]),
    _builtin_variant("JsonFloat", ["value"]),
    _builtin_variant("JsonBool", ["value"]),
    _builtin_variant("JsonNull", []),
    _builtin_variant("JsonArray", ["items"]),
    _builtin_variant("JsonObject", ["entries"]),
    # HttpResponse
    _builtin_variant("HttpResponse", ["status", "body", "headers"]),
    # HttpRequest
    _builtin_variant("HttpRequest", ["method", "path", "query", "headers", "body"]),
    # ProcessResult
    _builtin_variant("ProcessResult", ["exit_code", "stdout", "stderr"]),
    # FileKind
    _builtin_variant("FileKindFile", []),
    _builtin_variant("FileKindDirectory", []),
    _builtin_variant("FileKindSymlink", []),
    _builtin_variant("FileKindOther", []),
    # FileMetadata
    _builtin_variant("FileMetadata", ["kind", "size", "modified_ms"]),
]


class BaseCompiler(ABC):
    """Abstract base class shared by the Python and JS compiler backends.

    Provides:
    - Output buffer and indentation management (_write, _writeln, _indent, _dedent)
    - Definition state (type_defs, func_param_names, trait/impl support)
    - Target-independent helper methods (_fresh_temp, _get_constructor_field_name)
    - Shared control-flow compilation (_compile_if_statement, etc.)

    Subclasses MUST implement the abstract methods that provide target-specific
    syntax tokens and code generation.
    """

    def __init__(self) -> None:
        self.output = StringIO()
        self.indent_level = 0
        self.temp_counter = 0

        # Definition state — shared across backends
        self.type_defs: dict[str, TypeDef] = {}
        self.type_aliases: dict[str, TypeAlias] = {}
        self.func_param_names: dict[str, list[str]] = {}
        self.trait_defs: dict[str, TraitDef] = {}
        self.impl_defs: dict[tuple[str, str], ImplDef] = {}
        self.trait_dispatch: dict[str, list[tuple[str, str]]] = {}
        self._module_param_names: dict[str, dict[str, list[str]]] = {}
        self._constructor_to_variant: dict[str, TypeVariant] = {}
        self._reserved_temp_names: set[str] = set()
        self._definition_index = DefinitionIndex(
            type_defs=self.type_defs,
            type_aliases=self.type_aliases,
            func_param_names=self.func_param_names,
            trait_defs=self.trait_defs,
            impl_defs=self.impl_defs,
            trait_dispatch=self.trait_dispatch,
            constructor_to_variant=self._constructor_to_variant,
        )

        self.func_param_names.update(builtin_param_name_lists())

        # Register built-in type variants for dynamic field lookup
        for variant in _BUILTIN_VARIANTS:
            self._constructor_to_variant[variant.name] = variant

    # =========================================================================
    # Output helpers
    # =========================================================================

    def _write(self, text: str) -> None:
        """Write text to output buffer."""
        self.output.write(text)

    def _writeln(self, text: str = "") -> None:
        """Write an indented line to the output buffer."""
        if text:
            self._write("    " * self.indent_level + text + "\n")
        else:
            self._write("\n")

    def _indent(self) -> None:
        """Increase indentation level."""
        self.indent_level += 1

    def _dedent(self) -> None:
        """Decrease indentation level."""
        self.indent_level = max(0, self.indent_level - 1)

    def _fresh_temp(self) -> str:
        """Generate a fresh temporary variable name."""
        while True:
            self.temp_counter += 1
            name = f"_temp_{self.temp_counter}"
            if name not in self._reserved_temp_names:
                self._reserved_temp_names.add(name)
                return name

    def _reset_definition_state(self) -> None:
        """Clear per-program definition state while preserving shared dict aliases."""
        self.temp_counter = 0
        self.type_defs.clear()
        self.type_aliases.clear()
        self.func_param_names.clear()
        self.func_param_names.update(builtin_param_name_lists())
        self.trait_defs.clear()
        self.impl_defs.clear()
        self.trait_dispatch.clear()
        self._module_param_names.clear()
        self._constructor_to_variant.clear()
        self._reserved_temp_names.clear()
        for variant in _BUILTIN_VARIANTS:
            self._constructor_to_variant[variant.name] = variant

    def _reserve_temp_name(self, name: str) -> None:
        """Reserve a user-visible target identifier from generated temps."""
        if not name.isidentifier():
            return
        self._reserved_temp_names.add(name)
        self._reserved_temp_names.add(self._mangle_name(name))

    def _reserve_user_temp_names(self, node: object) -> None:
        """Reserve identifiers that appear in the AST before generating temps."""
        seen: set[int] = set()

        def visit(value: object) -> None:
            if isinstance(value, str):
                self._reserve_temp_name(value)
                return
            if value is None or isinstance(value, (bool, int, float, bytes)):
                return

            value_id = id(value)
            if value_id in seen:
                return

            if isinstance(value, (list, tuple, set, frozenset)):
                seen.add(value_id)
                for item in value:
                    visit(item)
                return

            if isinstance(value, dict):
                seen.add(value_id)
                for key, item in value.items():
                    visit(key)
                    visit(item)
                return

            if is_dataclass(value) and not isinstance(value, type):
                seen.add(value_id)
                for field_info in fields(value):
                    visit(getattr(value, field_info.name))

        visit(node)

    def _validate_runtime_name_collisions(
        self,
        program: Program,
        global_reserved_names: Collection[str],
        local_reserved_names: Collection[str],
        error_type: type[Exception],
        top_level_dispatchers_share_scope: bool = True,
        direct_reference_reserved_names: Collection[str] | None = None,
    ) -> None:
        """Reject every user binding that can overwrite emitted runtime state."""

        def reject(name: str | None, kind: str) -> None:
            if name is not None and name in global_reserved_names:
                raise error_type(
                    f"'{name}' is a reserved runtime name and cannot be used "
                    f"as a {kind} name"
                )

        for defn in program.definitions:
            if isinstance(defn, FunctionDef):
                reject(defn.name, "function")
            elif isinstance(defn, TypeAlias):
                reject(defn.name, "type")
            elif isinstance(defn, TypeDef):
                reject(defn.name, "type")
                for variant in defn.variants:
                    reject(variant.name, "constructor")
            elif isinstance(defn, TraitDef):
                reject(defn.name, "trait")
                # Trait dispatchers are emitted as top-level functions named
                # after the method, so their names are global bindings too.
                for trait_method in defn.methods:
                    reject(trait_method.name, "trait method")
            elif isinstance(defn, ImplDef):
                for impl_method in defn.methods:
                    reject(
                        f"{defn.trait_name}_{impl_method.name}_{defn.target_type}",
                        "implementation helper",
                    )
            elif isinstance(defn, ImportStatement) and defn.alias:
                reject(defn.alias, "import alias")

        reference_reserved_names = (
            global_reserved_names
            if direct_reference_reserved_names is None
            else direct_reference_reserved_names
        )
        seen_references: set[int] = set()
        if top_level_dispatchers_share_scope:
            function_names = {
                defn.name
                for defn in program.definitions
                if isinstance(defn, FunctionDef)
            }
            if collisions := function_names & self.trait_dispatch.keys():
                name = min(collisions)
                raise error_type(f"Function '{name}' conflicts with a trait dispatcher")

        def validate_reference(value: object) -> None:
            if isinstance(value, Identifier):
                if (
                    value.name in reference_reserved_names
                    and value._resolved_builtin_name is None
                    and value.name not in all_builtin_names()
                ):
                    raise error_type(
                        f"'{value.name}' is a reserved runtime name and cannot "
                        "be referenced directly"
                    )
                return
            if value is None or isinstance(value, (str, bool, int, float, bytes)):
                return
            value_id = id(value)
            if value_id in seen_references:
                return
            if isinstance(value, (list, tuple, set, frozenset)):
                seen_references.add(value_id)
                for item in value:
                    validate_reference(item)
                return
            if isinstance(value, dict):
                seen_references.add(value_id)
                for key, item in value.items():
                    validate_reference(key)
                    validate_reference(item)
                return
            if is_dataclass(value) and not isinstance(value, type):
                seen_references.add(value_id)
                for field_info in fields(value):
                    validate_reference(getattr(value, field_info.name))

        validate_reference(program)

        # Only helpers emitted directly inside user scopes need local-name
        # protection. Global prelude functions resolve their dependencies in
        # the module/global scope and do not justify banning ordinary locals.
        self._validate_reserved_local_names(
            program,
            local_reserved_names,
            error_type,
        )

    def _validate_reserved_local_names(
        self,
        program: Program,
        reserved_names: Collection[str],
        error_type: type[Exception],
    ) -> None:
        """Reject local user bindings that would shadow backend runtime helpers."""

        def check_name(name: str | None, kind: str) -> None:
            if name is not None and name in reserved_names:
                raise error_type(
                    f"'{name}' is a reserved runtime name and cannot be used "
                    f"as a {kind} name"
                )

        def check_params(params: list[Parameter], owner: str) -> None:
            for param in params:
                check_name(param.name, f"{owner} parameter")
                if param.default_value is not None:
                    visit_expr(param.default_value)

        def check_pattern(pattern: Pattern) -> None:
            if isinstance(pattern, (VariablePattern, RestPattern)):
                check_name(pattern.name, "pattern binding")
            elif isinstance(pattern, ConstructorPattern):
                for subpattern in pattern.subpatterns:
                    check_pattern(subpattern)
            elif isinstance(pattern, ListPattern):
                for element in pattern.elements:
                    check_pattern(element)

        def visit_value(value: object) -> None:
            if value is None:
                return
            if isinstance(value, Expression):
                visit_expr(value)
            elif isinstance(value, Statement):
                visit_stmt(value)
            elif isinstance(value, MatchArm):
                check_pattern(value.pattern)
                visit_expr(value.guard)
                visit_statements(value.body)
            elif isinstance(value, CallArg):
                visit_expr(value.value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    visit_value(item)

        def visit_expr(expr: Expression | None) -> None:
            if expr is None:
                return
            if isinstance(expr, LambdaExpr):
                check_params(expr.params, "lambda")
            elif isinstance(expr, ListComprehension):
                check_name(expr.variable, "list comprehension binding")
            if is_dataclass(expr) and not isinstance(expr, type):
                for field_info in fields(expr):
                    visit_value(getattr(expr, field_info.name))

        def visit_stmt(stmt: Statement) -> None:
            if isinstance(stmt, (LetStatement, VarStatement)):
                check_name(stmt.name, "local binding")
            elif isinstance(stmt, TupleDestructureStatement):
                for name in stmt.names:
                    check_name(name, "local binding")
            elif isinstance(stmt, ForStatement):
                check_name(stmt.variable, "for-loop binding")
            elif isinstance(stmt, TryStatement):
                check_name(stmt.catch_clause.variable, "catch binding")
                visit_statements(stmt.catch_clause.body)

            if is_dataclass(stmt) and not isinstance(stmt, type):
                for field_info in fields(stmt):
                    visit_value(getattr(stmt, field_info.name))

        def visit_statements(statements: list[Statement]) -> None:
            for stmt in statements:
                visit_stmt(stmt)

        def visit_function(defn: FunctionDef) -> None:
            check_params(defn.params, "function")
            for requires_clause in defn.specs.requires:
                visit_expr(requires_clause.condition)
            for ensures_clause in defn.specs.ensures:
                visit_expr(ensures_clause.condition)
            for example_clause in defn.specs.examples:
                visit_expr(example_clause.input_expr)
                visit_expr(example_clause.output_expr)
            visit_statements(defn.body)

        def visit_trait_method(method: TraitMethodSig) -> None:
            check_params(method.params, "trait method")

        for defn in program.definitions:
            if isinstance(defn, FunctionDef):
                visit_function(defn)
            elif isinstance(defn, ImplDef):
                for impl_method in defn.methods:
                    visit_function(impl_method)
            elif isinstance(defn, TraitDef):
                for trait_method in defn.methods:
                    visit_trait_method(trait_method)

    def _register_module_param_names(self, module_name: str, program) -> None:
        """Record function parameter names exported by one module."""
        self._module_param_names[module_name] = {
            defn.name: [param.name for param in defn.params]
            for defn in program.definitions
            if isinstance(defn, FunctionDef)
        }

    def _register_import_alias_param_names(self, program) -> None:
        """Record local import aliases for qualified calls in the current module."""
        for defn in program.definitions:
            if not isinstance(defn, ImportStatement) or defn.alias is None:
                continue
            imported = self._module_param_names.get(defn.module_name)
            if imported is not None:
                self._module_param_names[defn.alias] = imported

    def _param_names_for_function_call(
        self, expr: FunctionCall, func_name: str | None
    ) -> list[str] | None:
        """Return known parameter names for a function call expression."""
        has_named_args = any(arg.name for arg in expr.arguments)
        if not has_named_args:
            return None

        if func_name is not None:
            if func_name in self.trait_dispatch:
                trait_params = self._trait_dispatch_param_names_for_call(
                    func_name, expr.arguments
                )
                if trait_params is not None:
                    return trait_params
            return self.func_param_names.get(func_name)

        if isinstance(expr.function, FieldAccess) and isinstance(
            expr.function.target, (Identifier, TypeIdentifier)
        ):
            module_params = self._module_param_names.get(expr.function.target.name, {})
            return module_params.get(expr.function.field_name)

        return None

    @staticmethod
    def _reorder_call_args_for_compile(
        call_args: list[CallArg], param_names: list[str]
    ) -> list[CallArg | None]:
        """Reorder named call arguments and preserve skipped middle defaults."""
        result: list[CallArg | None] = [None] * len(param_names)
        used_positions: set[int] = set()
        positional_index = 0

        for arg in call_args:
            if arg.name is not None:
                if arg.name not in param_names:
                    raise ValueError(f"Unknown parameter name: {arg.name}")
                pos = param_names.index(arg.name)
                result[pos] = arg
                used_positions.add(pos)
            else:
                while positional_index in used_positions:
                    positional_index += 1
                if positional_index < len(param_names):
                    result[positional_index] = arg
                    used_positions.add(positional_index)
                    positional_index += 1

        while result and result[-1] is None:
            result.pop()
        return result

    @staticmethod
    def _call_args_match_param_names(call_args: list, param_names: list[str]) -> bool:
        """Return whether call args can be reordered against ``param_names``."""
        used_positions: set[int] = set()
        positional_index = 0

        for arg in call_args:
            arg_name = getattr(arg, "name", None)
            if arg_name is not None:
                if arg_name not in param_names:
                    return False
                pos = param_names.index(arg_name)
                if pos in used_positions:
                    return False
                used_positions.add(pos)
            else:
                while positional_index in used_positions:
                    positional_index += 1
                if positional_index >= len(param_names):
                    return False
                used_positions.add(positional_index)
                positional_index += 1

        return True

    def _trait_dispatch_param_names_for_call(
        self, method_name: str, call_args: list
    ) -> list[str] | None:
        """Return the matching trait-dispatch param order for a named call."""
        seen_traits: set[str] = set()
        for trait_name, _type_name in self.trait_dispatch.get(method_name, []):
            if trait_name in seen_traits:
                continue
            seen_traits.add(trait_name)
            trait_def = self.trait_defs.get(trait_name)
            if trait_def is None:
                continue
            for method in trait_def.methods:
                if method.name != method_name:
                    continue
                param_names = [param.name for param in method.params]
                if self._call_args_match_param_names(call_args, param_names):
                    return param_names
        return None

    # =========================================================================
    # Target-specific abstract methods
    # =========================================================================

    @abstractmethod
    def _compile_statement(self, stmt: Statement) -> None:
        """Dispatch and compile a single statement."""

    @abstractmethod
    def _compile_expr(self, expr: Expression) -> str:
        """Compile an expression and return the target-language string."""

    @staticmethod
    @abstractmethod
    def _mangle_name(name: str) -> str:
        """Mangle a Geno identifier to a valid target-language name."""

    @abstractmethod
    def _emit_source_location(self, location) -> None:
        """Emit a source mapping (comment or source-map record)."""

    # =========================================================================
    # Shared constructor field resolution
    # =========================================================================

    def _get_constructor_field_name(self, constructor: str, index: int) -> str:
        """Get the field name for a constructor at a given index.

        This logic is identical across backends.  Built-in type variants are
        registered in ``_constructor_to_variant`` at init time so they are
        resolved dynamically — no hardcoded field maps needed.
        """
        variant = self._constructor_to_variant.get(constructor)
        if variant is not None and index < len(variant.fields):
            return str(variant.fields[index][0])
        return f"field_{index}"

    # =========================================================================
    # Shared control-flow compilation
    # =========================================================================

    def _compile_if_statement(self, stmt: IfStatement) -> None:
        """Compile an if statement — shared control-flow logic."""
        cond = self._compile_expr(stmt.condition)
        self._writeln(self._if_open(cond))
        self._indent()
        if stmt.then_body:
            for s in stmt.then_body:
                self._compile_statement(s)
        else:
            self._emit_empty_block()
        self._dedent()

        if stmt.else_body:
            self._writeln(self._else_open())
            self._indent()
            for s in stmt.else_body:
                self._compile_statement(s)
            self._dedent()
        self._emit_block_close()

    def _compile_while_statement(self, stmt: WhileStatement) -> None:
        """Compile a while loop — shared control-flow logic."""
        cond = self._compile_expr(stmt.condition)
        self._writeln(self._while_open(cond))
        self._indent()
        if stmt.body:
            for s in stmt.body:
                self._compile_statement(s)
        else:
            self._emit_empty_block()
        self._dedent()
        self._emit_block_close()

    def _compile_for_statement(self, stmt: ForStatement) -> None:
        """Compile a for loop — shared control-flow logic."""
        iterable = self._compile_expr(stmt.iterable)
        var = self._mangle_name(stmt.variable)
        self._writeln(self._for_open(var, iterable))
        self._indent()
        if stmt.body:
            for s in stmt.body:
                self._compile_statement(s)
        else:
            self._emit_empty_block()
        self._dedent()
        self._emit_block_close()

    def _compile_return_statement(self, stmt: ReturnStatement) -> None:
        """Compile a return statement — shared logic."""
        value = self._compile_expr(stmt.value)
        self._writeln(self._return_stmt(value))

    def _compile_assign_statement(self, stmt: AssignStatement) -> None:
        """Compile ``target = value`` — identical across Python and JS
        except for the trailing per-statement terminator (#622 slice)."""
        value = self._compile_expr(stmt.value)
        self._writeln(
            f"{self._mangle_name(stmt.target)} = {value}{self._statement_terminator()}"
        )

    def _compile_index_assign_statement(self, stmt: IndexAssignStatement) -> None:
        """Compile ``target[index] = value`` via the shared runtime
        safety wrapper.  Both backends expose ``_safe_index_set`` with
        identical semantics — Python in ``_runtime_support.py`` and
        JS in ``_js_runtime_support.js`` — so the call emitted here is
        identical across targets except for the trailing terminator."""
        target = self._compile_expr(stmt.target)
        index = self._compile_expr(stmt.index)
        value = self._compile_expr(stmt.value)
        self._writeln(
            f"_safe_index_set({target}, {index}, {value}){self._statement_terminator()}"
        )

    def _compile_field_assign_statement(self, stmt: FieldAssignStatement) -> None:
        """Compile ``target.field = value``."""
        target = self._compile_expr(stmt.target)
        value = self._compile_expr(stmt.value)
        self._writeln(
            f"{target}.{stmt.field_name} = {value}{self._statement_terminator()}"
        )

    def _compile_tuple_destructure(self, stmt: TupleDestructureStatement) -> None:
        """Compile ``let/var (a, b, ...) = value``.

        The shared logic is: compile the RHS, mangle each target name,
        and hand off to the target-specific ``_tuple_destructure_stmt``
        hook — which knows whether to emit tuple-unpack (Python) or
        array-destructure (JS), and whether the binding is mutable.
        Before this hoist both backends duplicated the compile/mangle
        boilerplate (#622 slice).
        """
        value = self._compile_expr(stmt.value)
        names = ", ".join(self._mangle_name(n) for n in stmt.names)
        self._writeln(self._tuple_destructure_stmt(names, value, stmt.mutable))

    # =========================================================================
    # Top-level definition compilation
    # =========================================================================

    def _compile_impl_def(self, defn: ImplDef) -> None:
        """Compile an impl block by emitting each method as a mangled
        top-level function.

        The ``ImplDef`` AST node stores a list of ``FunctionDef``-shaped
        methods; we rewrite each with a mangled ``{trait}_{method}_{target}``
        name and hand off to the target-specific ``_compile_function_def``.
        Both backends agree on the mangling scheme and the shape of the
        rewrite — only the source-map bookkeeping differs (JS records a
        mapping at the impl level for finer precision; Python lets the
        nested function-def compilation emit its own ``# geno:`` comment).
        That asymmetry is hidden behind ``_record_impl_def_location``
        below.  #622 slice 3.
        """
        self._record_impl_def_location(defn)
        for method in defn.methods:
            mangled = f"{defn.trait_name}_{method.name}_{defn.target_type}"
            mangled_def = FunctionDef(
                location=method.location,
                name=mangled,
                params=method.params,
                return_type=method.return_type,
                specs=method.specs,
                body=method.body,
                closing_name=None,
                effects=method.effects,
            )
            self._compile_function_def(mangled_def)

    def _record_impl_def_location(self, defn: ImplDef) -> None:
        """Record the impl block's source location.  No-op by default
        — Python does not emit an impl-level source marker since the
        nested function definitions emit their own.  JS overrides to
        drop a source-map entry at the impl level for finer source
        mapping precision."""

    @abstractmethod
    def _compile_function_def(self, defn: FunctionDef) -> None:
        """Compile a single top-level function definition to the
        target-specific output.  Called from the shared
        ``_compile_impl_def`` above; backends' existing concrete
        implementations already exist — this abstract declaration just
        pins the signature ``BaseCompiler`` relies on."""

    # =========================================================================
    # Expression compilation (partial — incremental #622 hoist)
    # =========================================================================

    def _compile_with_expr(self, expr: WithExpr) -> str:
        """Compile ``target with (field = value, ...)``.

        The outer shape is identical across backends — compile the
        target expression, compile each update's value expression —
        but the final emission differs:

        Python: ``_dataclasses_replace({target}, name=value, ...)``
                (keyword-argument call into a helper that clones the
                frozen dataclass).
        JS:     ``Object.freeze({...{target}, name: value, ...})``
                (object spread + freeze).

        The target-specific formatter lives in
        ``_with_expr_emit`` below.  #622 slice.
        """
        target = self._compile_expr(expr.target)
        updates = [(name, self._compile_expr(value)) for name, value in expr.updates]
        return self._with_expr_emit(target, updates)

    @abstractmethod
    def _with_expr_emit(self, target: str, updates: list[tuple[str, str]]) -> str:
        """Emit the final ``with`` expression given a pre-compiled
        *target* string and a list of ``(field_name, compiled_value)``
        pairs.  See ``_compile_with_expr`` for the per-backend shape."""

    # =========================================================================
    # Syntax token hooks — override in subclasses
    # =========================================================================

    @abstractmethod
    def _if_open(self, cond: str) -> str:
        """Return the opening line for an if statement.
        Python: 'if {cond}:'   JS: 'if ({cond}) {'
        """

    @abstractmethod
    def _else_open(self) -> str:
        """Return the else line.
        Python: 'else:'   JS: '} else {'
        """

    @abstractmethod
    def _while_open(self, cond: str) -> str:
        """Return the opening line for a while loop.
        Python: 'while {cond}:'   JS: 'while ({cond}) {'
        """

    @abstractmethod
    def _for_open(self, var: str, iterable: str) -> str:
        """Return the opening line for a for loop.
        Python: 'for {var} in {iterable}:'   JS: 'for (const {var} of {iterable}) {'
        """

    @abstractmethod
    def _return_stmt(self, value: str) -> str:
        """Return a return statement string.
        Python: 'return {value}'   JS: 'return {value};'
        """

    @abstractmethod
    def _tuple_destructure_stmt(self, names_csv: str, value: str, mutable: bool) -> str:
        """Return the full line that destructures *value* into *names_csv*.

        Python: ``"({names_csv}) = {value}"`` (tuple unpacking; both
        ``let`` and ``var`` share the same emission — Python has no
        equivalent of the JS ``let``/``const`` distinction at binding
        time).

        JS: ``"{'let' if mutable else 'const'} [{names_csv}] = {value};"``
        (array destructuring; the ``mutable`` flag selects the binding
        keyword).
        """

    @abstractmethod
    def _statement_terminator(self) -> str:
        """Per-statement terminator appended to simple expression
        statements.  Python emits nothing (newlines terminate
        statements); JS emits ``;``.  Used by shared simple-statement
        compilers hoisted into ``BaseCompiler`` (#622).  Future
        incremental slices (augmented assigns, ``break`` / ``continue``,
        etc.) can reuse the same hook without growing the surface."""

    def _emit_empty_block(self) -> None:
        """Emit a placeholder for an empty block (Python: pass, JS: nothing)."""

    def _emit_block_close(self) -> None:
        """Emit block-closing syntax (Python: nothing, JS: '}')."""

    # =========================================================================
    # Trait-dispatcher emission — shared shape, target-specific tokens
    #
    # Both backends previously kept a full, parallel copy of
    # ``_emit_trait_dispatchers``.  The outer scaffolding (iterate
    # ``trait_dispatch`` → for each method iterate impls → chain of
    # ``if`` / ``elif`` dispatching on ``self_arg``'s constructor →
    # trailing error branch) is target-independent, so it lives here.
    # Subclasses provide the four emission hooks below for their
    # specific syntax.  First slice of the #622 BaseCompiler hoist —
    # subsequent iterations can lift more of the overlapping visitor
    # methods using the same pattern.
    # =========================================================================

    def _emit_trait_dispatchers(self) -> None:
        """Emit dispatch wrapper functions for trait methods."""
        for method_name, impls in self.trait_dispatch.items():
            self._writeln()
            self._open_dispatcher(method_name)
            self._indent()

            for i, (trait_name, type_name) in enumerate(impls):
                mangled = f"{trait_name}_{method_name}_{type_name}"
                type_def = self.type_defs.get(type_name)
                if type_def:
                    constructor_names = tuple(v.name for v in type_def.variants)
                else:
                    constructor_names = (type_name,)
                self._emit_dispatcher_arm(
                    is_first=(i == 0),
                    constructor_names=constructor_names,
                    mangled=mangled,
                )

            first_trait = impls[0][0] if impls else "Unknown"
            self._emit_dispatcher_else(first_trait)
            self._close_dispatcher()

    @abstractmethod
    def _open_dispatcher(self, method_name: str) -> None:
        """Emit the opening of the dispatcher function for *method_name*.

        Python: ``def {mangle(method_name)}(self_arg, *_args):``
        JS:     ``function {mangle(method_name)}(self_arg, ...args) {``

        Responsible for writing the declaration line; the caller then
        calls ``_indent()`` so subsequent arm emissions are nested.
        """

    @abstractmethod
    def _emit_dispatcher_arm(
        self,
        *,
        is_first: bool,
        constructor_names: tuple[str, ...],
        mangled: str,
    ) -> None:
        """Emit one ``if`` / ``elif`` arm of the dispatcher.

        Python form::

            if/elif type(self_arg).__name__ in (...):
                return {mangled}(self_arg, *_args)

        JS form::

            if/} else if ([...].includes(self_arg._tag)) {
                return {mangled}(self_arg, ...args);

        Subclasses manage arm-local block-open / dedent punctuation;
        Python closes each arm with a dedent while JS leaves the brace
        open for the next ``else if`` and relies on
        ``_emit_dispatcher_else`` / ``_close_dispatcher`` to finalise.
        """

    @abstractmethod
    def _emit_dispatcher_else(self, first_trait: str) -> None:
        """Emit the final ``else`` branch that raises / throws."""

    @abstractmethod
    def _close_dispatcher(self) -> None:
        """Close the dispatcher function (emit trailing brace / dedent)."""
