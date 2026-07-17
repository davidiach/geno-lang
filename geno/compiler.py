"""
Geno Compiler
=================

Compiles Geno source code to Python.
"""

import ast
import keyword
from contextlib import contextmanager
from functools import lru_cache
from io import StringIO
from typing import TYPE_CHECKING, Collection, Iterator, Optional, cast

from ._backend_fastpath import (
    APPEND_FAST_PATH_BUILTIN,
    LENGTH_FAST_PATH_BUILTIN,
    STRING_AFFIX_FAST_PATH_BUILTINS,
    STRING_CHAR_AT_FAST_PATH_BUILTIN,
    SUBSTRING_FAST_PATH_BUILTINS,
    has_len_fast_path,
    is_int_type,
    is_list_type,
    is_numeric_type,
    is_simple_fast_path_expr,
    is_string_type,
)
from ._definition_index import collect_definitions
from .ast_nodes import (  # Types; Expressions; Patterns; Statements; Definitions; Program
    AssignStatement,
    ASTVisitor,
    AwaitExpr,
    BinaryOp,
    BooleanLiteral,
    BreakStatement,
    CallArg,
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
    FunctionType,
    Identifier,
    IfStatement,
    ImplDef,
    ImportStatement,
    IndexAccess,
    IndexAssignStatement,
    IntegerLiteral,
    LambdaExpr,
    LetStatement,
    ListComprehension,
    ListLiteral,
    ListPattern,
    LiteralPattern,
    MatchExpr,
    MatchStatement,
    Pattern,
    Pipeline,
    PlaceholderExpr,
    Program,
    PropagateExpr,
    RestPattern,
    ReturnStatement,
    SimpleType,
    Statement,
    StringLiteral,
    ThrowExpression,
    TryStatement,
    TupleDestructureStatement,
    TupleExpr,
    TypeAnnotation,
    TypeDef,
    TypedHole,
    TypeIdentifier,
    TypeVariant,
    UnaryOp,
    VariablePattern,
    VarStatement,
    WhileStatement,
    WildcardPattern,
    WithExpr,
)

if TYPE_CHECKING:
    from .target_profile import TargetProfile
from .base_compiler import BaseCompiler
from .builtin_registry import (
    all_builtin_names,
    python_backend_builtin_helper_names,
    python_backend_builtin_name_map,
)
from .runtime_prelude import RUNTIME_PRELUDE
from .types import FloatType, UserType


# Names defined in the runtime prelude that must not be shadowed by user code.
def _python_prelude_binding_names(source: str) -> frozenset[str]:
    """Return every module-scope name bound by the generated Python prelude."""
    names: set[str] = set()

    def add_target(target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                add_target(element)

    def visit_statements(statements: list[ast.stmt]) -> None:
        for statement in statements:
            if isinstance(
                statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                names.add(statement.name)
            elif isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                )
                for target in targets:
                    add_target(target)
            elif isinstance(statement, (ast.Import, ast.ImportFrom)):
                for alias in statement.names:
                    names.add(alias.asname or alias.name.split(".", 1)[0])
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                add_target(statement.target)
                visit_statements(statement.body)
                visit_statements(statement.orelse)
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                for item in statement.items:
                    if item.optional_vars is not None:
                        add_target(item.optional_vars)
                visit_statements(statement.body)
            elif isinstance(statement, ast.If):
                visit_statements(statement.body)
                visit_statements(statement.orelse)
            elif isinstance(statement, (ast.While, ast.Try)):
                visit_statements(statement.body)
                visit_statements(statement.orelse)
                if isinstance(statement, ast.Try):
                    visit_statements(statement.finalbody)
                    for handler in statement.handlers:
                        if handler.name:
                            names.add(handler.name)
                        visit_statements(handler.body)
            elif isinstance(statement, ast.Match):
                for case in statement.cases:
                    visit_statements(case.body)

    visit_statements(ast.parse(source).body)
    return frozenset(names)


# Shadowing these could disable safety mechanisms (get_field, _safe_div) or
# corrupt core type definitions (Constructor, Some, Ok, Err).
_PYTHON_LOCAL_RESERVED_NAMES = (
    frozenset(
        {
            # Security-critical functions
            "get_field",
            "_safe_index",
            "_safe_index_set",
            "_safe_div",
            "_safe_sub",
            "_safe_mod",
            "_numeric_mod",
            "_int_div",
            "_int_mod",
            "_float_div",
            "_div_zero",
            "_int_oob",
            "_check_int_bits",
            "_MAX_INTEGER_BITS",
            "_list_size_exceeded",
            "_safe_add",
            "_safe_mul",
            "_safe_pow",
            "_safe_bitand",
            "_safe_bitxor",
            "_safe_lshift",
            "_safe_rshift",
            "_safe_invert",
            "_require_safe_js_int",
            "_require_int_bit_limit",
            "_check_collection_size",
            "_check_collection_kind",
            "_MAX_COLLECTION_SIZE",
            "_geno_run_async",
            "match_constructor",
            "_BLOCKED_FIELD_NAMES",
            # Core type infrastructure
            "_GenoArray",
            "Constructor",
            "Some",
            "_None",
            "None_",
            "Ok",
            "Err",
            # Imported modules used by prelude
            "math",
            "deepcopy",
            "_geno_deepcopy",
            "dataclass",
            "_dataclasses_fields",
            "_dataclasses_replace",
            "_runtime_codecs",
            "_runtime_posixpath",
            "_runtime_random",
            "_runtime_time",
            # Typed hole helper
            "_typed_hole",
            # Mutable collection types
            "_GenoSet",
            # ? operator support
            "_propagate",
            "_PropagateReturn",
            # throw/catch support
            "_GenoThrow",
            "_GenoContractViolation",
            "_GENO_MISSING",
            "_geno_throw",
        }
    )
    | python_backend_builtin_helper_names()
)


RESERVED_PRELUDE_NAMES = (
    _python_prelude_binding_names(RUNTIME_PRELUDE) - frozenset(all_builtin_names())
) | _PYTHON_LOCAL_RESERVED_NAMES
# Python keywords that are valid Geno identifiers but would produce invalid
# Python if emitted verbatim.  We mangle them by appending "_kw".
_PYTHON_KEYWORDS: frozenset[str] = frozenset(keyword.kwlist) | frozenset(
    getattr(keyword, "softkwlist", [])
)

# ---------------------------------------------------------------------------
# Integer-limit contract of the compiled Python backend
#
# The compiled backend enforces the runtime-configurable integer bit
# ceiling (_GENO_MAX_INTEGER_BITS) with one DELIBERATE, documented
# relaxation chosen for performance (see benchmarks/RESULTS.md):
#
#   Values produced by Int +/- with a small constant addend, and integer
#   literals at or below _RAW_INT_LITERAL_MAX_BITS, are not re-checked at
#   every evaluation. Under a host-tightened limit they can therefore
#   exceed the configured ceiling by one bit per operation (the
#   interpreter and the generic _safe_* helpers raise instead).
#
# This cannot become an integer bomb: constant addends grow a value
# linearly, so its bit length grows logarithmically — from any checked
# value, exceeding the default 33,219-bit ceiling would take ~2**33187
# operations. Every other producer (var+var arithmetic, *, **, shifts,
# bitwise ops, all runtime helpers and collections) checks the configured
# limit exactly, so relaxed values re-trip the limit at the next guarded
# use. Under the default limit the relaxation is unobservable.
# ---------------------------------------------------------------------------

# Integer literals at or below this bit length compile to raw Python
# constants with no runtime size check: they are provably below every
# integer-bits limit configured in practice (the runtime default is 33,219
# bits and the tightest sandbox override exercised is 64), and a constant
# cannot grow at runtime (the JS backend likewise emits literals raw under
# a compile-time bound). Literals above this keep the runtime guard.
_RAW_INT_LITERAL_MAX_BITS = 63

# Int + and - skip the integer-bits guard entirely when one operand is an
# integer literal at or below this bit length (the relaxation described
# above; * always keeps its guard because it doubles the bit length per
# operation, a real growth vector).
_GROWTH_FREE_ADDEND_MAX_BITS = 32


class CompileError(Exception):
    """Raised when compilation detects an unsafe or invalid program."""


class Compiler(BaseCompiler, ASTVisitor):
    """
    Compiles Geno AST to Python source code.

    Example:
        compiler = Compiler()
        python_code = compiler.compile(program)
    """

    def __init__(self):
        super().__init__()
        self._active_loop_vars: list[str] = []
        self._name_overrides: list[dict[str, str]] = []
        # Depth counter: >0 while compiling a comprehension's iterable
        # expression, where Python forbids assignment expressions, so the
        # inline integer guard must fall back to its call form.
        self._in_comprehension_iterable = 0
        # Depth counter: >0 while compiling statements inside a try block.
        # Statement-form integer guards assign before checking, which a
        # same-function catch could observe, so try bodies keep the
        # expression-form guard (target stays unassigned on failure).
        self._try_depth = 0

    def _compiled_identifier_name(self, name: str) -> str:
        """Resolve an identifier, honoring scoped capture/shadow overrides."""
        for overrides in reversed(self._name_overrides):
            resolved = overrides.get(name)
            if resolved is not None:
                return resolved
        return self._mangle_name(name)

    @contextmanager
    def _with_name_overrides(self, overrides: dict[str, str]) -> Iterator[None]:
        self._name_overrides.append(overrides)
        try:
            yield
        finally:
            self._name_overrides.pop()

    @contextmanager
    def _with_shadowed_bindings(self, names: list[str]) -> Iterator[None]:
        shadowed = {
            name: self._mangle_name(name)
            for name in names
            if any(name in scope for scope in self._name_overrides)
        }
        if shadowed:
            with self._with_name_overrides(shadowed):
                yield
        else:
            yield

    def _pattern_bound_names(self, pattern: Pattern) -> set[str]:
        if isinstance(pattern, VariablePattern):
            return {pattern.name}
        if isinstance(pattern, RestPattern):
            return {pattern.name} if pattern.name is not None else set()
        if isinstance(pattern, ConstructorPattern):
            bound: set[str] = set()
            for subpattern in pattern.subpatterns:
                bound.update(self._pattern_bound_names(subpattern))
            return bound
        if isinstance(pattern, ListPattern):
            list_bound: set[str] = set()
            for element in pattern.elements:
                list_bound.update(self._pattern_bound_names(element))
            return list_bound
        return set()

    def _collect_loop_var_refs_in_value(
        self,
        value: object,
        bound: set[str],
        referenced: set[str],
        active: set[str],
    ) -> None:
        if isinstance(value, Expression):
            self._collect_loop_var_refs_in_expr(value, bound, referenced, active)
            return
        if isinstance(value, Statement):
            self._collect_loop_var_refs_in_statements(
                [value], bound, referenced, active
            )
            return
        if isinstance(value, Pattern):
            return
        if isinstance(value, list):
            for item in value:
                self._collect_loop_var_refs_in_value(item, bound, referenced, active)
            return
        if isinstance(value, tuple):
            for item in value:
                self._collect_loop_var_refs_in_value(item, bound, referenced, active)
            return
        if hasattr(value, "__dict__"):
            for nested in vars(value).values():
                self._collect_loop_var_refs_in_value(nested, bound, referenced, active)

    def _collect_loop_var_refs_in_expr(
        self,
        expr: Expression | None,
        bound: set[str],
        referenced: set[str],
        active: set[str],
    ) -> None:
        if expr is None:
            return
        if isinstance(expr, Identifier):
            if expr.name in active and expr.name not in bound:
                referenced.add(expr.name)
            return
        if isinstance(expr, LambdaExpr):
            lambda_bound = set(bound)
            lambda_bound.update(param.name for param in expr.params)
            if expr.block_body is not None:
                self._collect_loop_var_refs_in_statements(
                    expr.block_body, lambda_bound, referenced, active
                )
            elif expr.body is not None:
                self._collect_loop_var_refs_in_expr(
                    expr.body, lambda_bound, referenced, active
                )
            return
        if isinstance(expr, ListComprehension):
            self._collect_loop_var_refs_in_expr(
                expr.iterable, bound, referenced, active
            )
            comp_bound = set(bound)
            comp_bound.add(expr.variable)
            self._collect_loop_var_refs_in_expr(
                expr.element_expr, comp_bound, referenced, active
            )
            self._collect_loop_var_refs_in_expr(
                expr.condition, comp_bound, referenced, active
            )
            return

        for value in vars(expr).values():
            self._collect_loop_var_refs_in_value(value, bound, referenced, active)

    def _collect_loop_var_refs_in_statements(
        self,
        statements: list[Statement],
        bound: set[str],
        referenced: set[str],
        active: set[str],
    ) -> None:
        scope = set(bound)
        for stmt in statements:
            if isinstance(stmt, LetStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.value, scope, referenced, active
                )
                scope.add(stmt.name)
                continue
            if isinstance(stmt, VarStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.value, scope, referenced, active
                )
                scope.add(stmt.name)
                continue
            if isinstance(stmt, TupleDestructureStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.value, scope, referenced, active
                )
                scope.update(stmt.names)
                continue
            if isinstance(stmt, AssignStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.value, scope, referenced, active
                )
                continue
            if isinstance(stmt, IndexAssignStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.target, scope, referenced, active
                )
                self._collect_loop_var_refs_in_expr(
                    stmt.index, scope, referenced, active
                )
                self._collect_loop_var_refs_in_expr(
                    stmt.value, scope, referenced, active
                )
                continue
            if isinstance(stmt, FieldAssignStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.target, scope, referenced, active
                )
                self._collect_loop_var_refs_in_expr(
                    stmt.value, scope, referenced, active
                )
                continue
            if isinstance(stmt, ExpressionStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.expression, scope, referenced, active
                )
                continue
            if isinstance(stmt, ReturnStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.value, scope, referenced, active
                )
                continue
            if isinstance(stmt, IfStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.condition, scope, referenced, active
                )
                self._collect_loop_var_refs_in_statements(
                    stmt.then_body, set(scope), referenced, active
                )
                self._collect_loop_var_refs_in_statements(
                    stmt.else_body, set(scope), referenced, active
                )
                continue
            if isinstance(stmt, WhileStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.condition, scope, referenced, active
                )
                self._collect_loop_var_refs_in_statements(
                    stmt.body, set(scope), referenced, active
                )
                continue
            if isinstance(stmt, ForStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.iterable, scope, referenced, active
                )
                loop_scope = set(scope)
                loop_scope.add(stmt.variable)
                self._collect_loop_var_refs_in_statements(
                    stmt.body, loop_scope, referenced, active
                )
                continue
            if isinstance(stmt, MatchStatement):
                self._collect_loop_var_refs_in_expr(
                    stmt.scrutinee, scope, referenced, active
                )
                for arm in stmt.arms:
                    arm_scope = set(scope)
                    arm_scope.update(self._pattern_bound_names(arm.pattern))
                    self._collect_loop_var_refs_in_expr(
                        arm.guard, arm_scope, referenced, active
                    )
                    self._collect_loop_var_refs_in_statements(
                        arm.body, arm_scope, referenced, active
                    )
                continue
            if isinstance(stmt, TryStatement):
                self._collect_loop_var_refs_in_statements(
                    stmt.try_body, set(scope), referenced, active
                )
                catch_scope = set(scope)
                catch_scope.add(stmt.catch_clause.variable)
                self._collect_loop_var_refs_in_statements(
                    stmt.catch_clause.body, catch_scope, referenced, active
                )
                continue

            for value in vars(stmt).values():
                self._collect_loop_var_refs_in_value(value, scope, referenced, active)

    def _captured_loop_vars_in_lambda(self, expr: LambdaExpr) -> list[str]:
        active_loop_vars: list[str] = []
        seen: set[str] = set()
        for name in self._active_loop_vars:
            if name not in seen:
                active_loop_vars.append(name)
                seen.add(name)
        if not active_loop_vars:
            return []

        referenced: set[str] = set()
        lambda_bound = {param.name for param in expr.params}
        if expr.block_body is not None:
            self._collect_loop_var_refs_in_statements(
                expr.block_body, lambda_bound, referenced, set(active_loop_vars)
            )
        elif expr.body is not None:
            self._collect_loop_var_refs_in_expr(
                expr.body, lambda_bound, referenced, set(active_loop_vars)
            )
        return [name for name in active_loop_vars if name in referenced]

    # Geno builtin names that map to suffixed Python names to avoid
    # colliding with Python builtins.
    _BUILTIN_NAME_MAP: dict[str, str] = python_backend_builtin_name_map()

    @staticmethod
    def _mangle_name(name: str) -> str:
        """Mangle a Geno identifier so it is a valid, non-keyword Python name."""
        mapped = Compiler._BUILTIN_NAME_MAP.get(name)
        if mapped is not None:
            return mapped
        if name in _PYTHON_KEYWORDS:
            return f"{name}_kw"
        return name

    @staticmethod
    def _emit_python_string_literal(value: str) -> str:
        """Escape *value* and wrap it in double quotes for Python source."""
        import re as _re

        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace("\0", "\\0")
        )
        escaped = _re.sub(
            r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]",
            lambda m: f"\\x{ord(m.group()):02x}",
            escaped,
        )
        return f'"{escaped}"'

    def compile(self, program: Program) -> str:
        """
        Compile a Geno program to Python.

        Args:
            program: The program AST

        Returns:
            Python source code as a string
        """
        self.output = StringIO()
        self.indent_level = 0
        self._reset_definition_state()
        self._reserve_user_temp_names(program)

        # Write runtime prelude
        self.output.write(RUNTIME_PRELUDE)

        # First pass: collect type, function, trait, and impl definitions
        collect_definitions(program, into=self._definition_index)

        self._validate_runtime_name_collisions(
            program,
            RESERVED_PRELUDE_NAMES,
            _PYTHON_LOCAL_RESERVED_NAMES,
            CompileError,
        )

        # Compile all definitions
        for defn in program.definitions:
            if isinstance(defn, TypeDef):
                self._compile_type_def(defn)
            elif isinstance(defn, FunctionDef):
                self._compile_function_def(defn)
            elif isinstance(defn, ImplDef):
                self._compile_impl_def(defn)
            elif isinstance(defn, ImportStatement):
                if defn.alias:
                    self.output.write(f"{defn.alias} = {defn.module_name}\n")
            # TestBlock definitions are skipped in compiled output

        # Emit trait dispatch wrapper functions
        self._emit_trait_dispatchers()

        # Add main call if 'main' was defined
        main_defn = None
        for d in program.definitions:
            if isinstance(d, FunctionDef) and d.name == "main":
                main_defn = d
                break
        if main_defn is not None:
            self.output.write("\n\nif __name__ == '__main__':\n")
            if main_defn.is_async:
                self.output.write("    import asyncio\n")
                self.output.write("    result = asyncio.run(main())\n")
            else:
                self.output.write("    result = main()\n")
            self.output.write("    if result is not None:\n")
            self.output.write("        print(result)\n")

        return str(self.output.getvalue())

    @staticmethod
    def _module_runtime_exports(program: Program) -> tuple[list[str], list[str]]:
        """Return public and impl helper names a compiled module must expose."""
        public_exports: list[str] = []
        impl_exports: list[str] = []
        for defn in program.definitions:
            if isinstance(defn, FunctionDef):
                public_exports.append(defn.name)
            elif isinstance(defn, TypeDef):
                public_exports.extend(variant.name for variant in defn.variants)
            elif isinstance(defn, ImplDef):
                impl_exports.extend(
                    f"{defn.trait_name}_{method.name}_{defn.target_type}"
                    for method in defn.methods
                )
        return public_exports, impl_exports

    def compile_project(self, dep_graph) -> str:
        """Compile all modules in a DependencyGraph to a single Python file.

        Modules are emitted in topological order (dependencies first).
        The runtime prelude is written once at the top.
        """
        self.output = StringIO()
        self.indent_level = 0
        self._reset_definition_state()
        for program in dep_graph.parsed.values():
            self._reserve_user_temp_names(program)

            self._validate_runtime_name_collisions(
                program,
                RESERVED_PRELUDE_NAMES,
                _PYTHON_LOCAL_RESERVED_NAMES,
                CompileError,
            )
        for mod_name in dep_graph.sorted_modules:
            if mod_name in RESERVED_PRELUDE_NAMES:
                raise CompileError(f"'{mod_name}' is a reserved runtime module name")
        # Write runtime prelude once
        self.output.write(RUNTIME_PRELUDE)

        # Find the entrypoint module (last in topo order, typically has main())
        entrypoint = dep_graph.project.entrypoint or dep_graph.sorted_modules[-1]
        main_defn = None
        module_public_exports: dict[str, list[str]] = {}
        module_impl_exports: dict[str, list[str]] = {}
        module_runtime_exports: dict[str, list[str]] = {}

        for mod_name in dep_graph.sorted_modules:
            program = dep_graph.parsed[mod_name]
            self._register_module_param_names(mod_name, program)
            public_exports, impl_exports = self._module_runtime_exports(program)
            module_public_exports[mod_name] = public_exports
            module_impl_exports[mod_name] = impl_exports
            module_runtime_exports[mod_name] = public_exports + impl_exports

        # Compile each module in topological order
        for mod_name in dep_graph.sorted_modules:
            program = dep_graph.parsed[mod_name]
            runtime_exports = module_runtime_exports[mod_name]
            own_export_names = set(runtime_exports)
            imported_runtime_names: dict[str, str] = {}
            ambiguous_imported_names: set[str] = set()

            for defn in program.definitions:
                if not isinstance(defn, ImportStatement):
                    continue
                for export_name in module_runtime_exports.get(defn.module_name, []):
                    if export_name in own_export_names:
                        continue
                    if export_name in imported_runtime_names:
                        ambiguous_imported_names.add(export_name)
                    else:
                        imported_runtime_names[export_name] = defn.module_name

            for ambiguous in ambiguous_imported_names:
                imported_runtime_names.pop(ambiguous, None)

            self.output.write(f"\n\n# === Module: {mod_name} ===\n")
            factory_name = f"_geno_module_{self._mangle_name(mod_name)}"
            self._writeln(f"def {factory_name}():")
            self._indent()

            for defn in program.definitions:
                if isinstance(defn, ImportStatement) and defn.alias:
                    self._writeln(
                        f"{self._mangle_name(defn.alias)} = {defn.module_name}"
                    )

            for export_name, imported_module in sorted(imported_runtime_names.items()):
                self._writeln(
                    f"{self._mangle_name(export_name)} = "
                    f"getattr({imported_module}, {export_name!r})"
                )

            # First pass: collect definitions
            self._register_import_alias_param_names(program)
            collect_definitions(program, into=self._definition_index)

            # Compile definitions
            for defn in program.definitions:
                if isinstance(defn, TypeDef):
                    self._compile_type_def(defn)
                elif isinstance(defn, FunctionDef):
                    self._compile_function_def(defn)
                    if defn.name == "main" and mod_name == entrypoint:
                        main_defn = defn
                elif isinstance(defn, ImplDef):
                    self._compile_impl_def(defn)
                # TestBlock is skipped

            # Emit module namespace object for qualified access
            attrs = ", ".join(f"{n!r}: {self._mangle_name(n)}" for n in runtime_exports)
            self._writeln(
                f"return _SimpleNamespace(**{{{attrs}}})"
                if attrs
                else "return _SimpleNamespace()"
            )
            self._dedent()
            self._writeln(f"{mod_name} = {factory_name}()")

        for mod_name in dep_graph.sorted_modules:
            for impl_name in module_impl_exports[mod_name]:
                self._writeln(
                    f"{self._mangle_name(impl_name)} = getattr({mod_name}, {impl_name!r})"
                )

        for export_name in module_public_exports.get(entrypoint, []):
            self._writeln(
                f"{self._mangle_name(export_name)} = getattr({entrypoint}, {export_name!r})"
            )

        # Emit trait dispatchers
        self._emit_trait_dispatchers()

        # Add main call
        if main_defn is not None:
            self.output.write("\n\nif __name__ == '__main__':\n")
            if main_defn.is_async:
                self.output.write("    import asyncio\n")
                self.output.write("    result = asyncio.run(main())\n")
            else:
                self.output.write("    result = main()\n")
            self.output.write("    if result is not None:\n")
            self.output.write("        print(result)\n")

        return str(self.output.getvalue())

    def _emit_source_location(self, location) -> None:
        """Emit a source mapping comment: # geno:<filename>:<line>."""
        if location and location.filename:
            filename = str(location.filename).encode("unicode_escape").decode("ascii")
            self._writeln(f"# geno:{filename}:{location.line}")

    # -- Syntax token hooks (BaseCompiler) ------------------------------------

    def _if_open(self, cond: str) -> str:
        return f"if {cond}:"

    def _else_open(self) -> str:
        return "else:"

    def _while_open(self, cond: str) -> str:
        return f"while {cond}:"

    def _for_open(self, var: str, iterable: str) -> str:
        return f"for {var} in {iterable}:"

    def _return_stmt(self, value: str) -> str:
        return f"return {value}"

    def _statement_terminator(self) -> str:
        return ""

    def _tuple_destructure_stmt(self, names_csv: str, value: str, mutable: bool) -> str:
        # Python has no ``let``/``const`` distinction at binding time —
        # tuple unpacking is the same for both ``let`` and ``var``.
        return f"({names_csv}) = {value}"

    def _emit_empty_block(self) -> None:
        self._writeln("pass")

    # =========================================================================
    # Type Definitions
    # =========================================================================

    def _compile_type_def(self, defn: TypeDef) -> None:
        """Compile a type definition to Python dataclasses."""
        self._writeln()
        self._emit_source_location(defn.location)
        self._writeln(f"# Type: {defn.name}")

        for variant in defn.variants:
            self._compile_variant(defn.name, defn.type_params, variant)

        # Emit a Union type alias so the type name resolves in annotations
        variant_names = [v.name for v in defn.variants]
        if len(variant_names) == 1:
            self._writeln(f"{defn.name} = {variant_names[0]}")
        else:
            self._writeln(f"{defn.name} = Union[{', '.join(variant_names)}]")
        self._writeln()

    def _compile_variant(
        self, type_name: str, type_params: list[str], variant: TypeVariant
    ) -> None:
        """Compile a type variant to a Python dataclass with __slots__."""
        # repr=False so Constructor.__repr__ (matching interpreter format) is used
        self._writeln("@dataclass(frozen=True, repr=False)")
        self._writeln(f"class {variant.name}(Constructor):")
        self._indent()

        # Emit __slots__ for memory efficiency
        if variant.fields:
            slot_names = ", ".join(f"'{f[0]}'" for f in variant.fields)
            self._writeln(f"__slots__ = ({slot_names},)")
            for field_name, field_type in variant.fields:
                py_type = self._compile_type_annotation(field_type)
                # Quote annotations to support recursive/self-referential ADTs.
                # @dataclass only reads field names from __annotations__, not types.
                self._writeln(f"{field_name}: '{py_type}'")
        else:
            self._writeln("__slots__ = ()")
            self._writeln("pass")

        self._dedent()
        self._writeln()

    def _compile_type_annotation(self, type_annot: TypeAnnotation) -> str:
        """Compile a type annotation to Python type hint."""
        if isinstance(type_annot, SimpleType):
            type_map = {
                "Int": "int",
                "Float": "float",
                "Bool": "bool",
                "String": "str",
                "Unit": "None",
            }

            if type_annot.name in type_map and not type_annot.type_params:
                return type_map[type_annot.name]

            if type_annot.name == "List":
                if type_annot.type_params:
                    elem = self._compile_type_annotation(type_annot.type_params[0])
                    return f"list[{elem}]"
                return "list"

            if type_annot.name == "Array":
                return "_GenoArray"

            if type_annot.name == "Vec":
                return "_GenoVec"

            if type_annot.name == "Option":
                if type_annot.type_params:
                    val = self._compile_type_annotation(type_annot.type_params[0])
                    return f"Optional[{val}]"
                return "Optional"

            if type_annot.name == "Map":
                if len(type_annot.type_params) >= 2:
                    k = self._compile_type_annotation(type_annot.type_params[0])
                    v = self._compile_type_annotation(type_annot.type_params[1])
                    return f"dict[{k}, {v}]"
                return "dict"

            if type_annot.name == "Tuple":
                if type_annot.type_params:
                    params = ", ".join(
                        self._compile_type_annotation(p) for p in type_annot.type_params
                    )
                    return f"tuple[{params}]"
                return "tuple"

            if type_annot.name == "Result":
                if len(type_annot.type_params) >= 2:
                    ok = self._compile_type_annotation(type_annot.type_params[0])
                    err = self._compile_type_annotation(type_annot.type_params[1])
                    return f"Union[Ok[{ok}], Err[{err}]]"
                return "Union[Ok, Err]"

            if type_annot.type_params:
                params = ", ".join(
                    self._compile_type_annotation(p) for p in type_annot.type_params
                )
                return f"{type_annot.name}[{params}]"

            return str(type_annot.name)

        elif isinstance(type_annot, FunctionType):
            params = ", ".join(
                self._compile_type_annotation(p) for p in type_annot.param_types
            )
            ret = self._compile_type_annotation(type_annot.return_type)
            return f"Callable[[{params}], {ret}]"

        return "Any"

    # =========================================================================
    # Function Definitions
    # =========================================================================

    # ``_compile_impl_def`` lives on ``BaseCompiler`` — shared loop plus
    # the ``_record_impl_def_location`` hook (which defaults to a no-op
    # for Python).  #622 slice 3.

    # ``_emit_trait_dispatchers`` lives on ``BaseCompiler`` — both
    # backends share the iterate-and-chain scaffolding.  This subclass
    # only provides the Python-specific emission hooks below (#622).

    def _open_dispatcher(self, method_name: str) -> None:
        self._writeln(f"def {self._mangle_name(method_name)}(self_arg, *_args):")

    def _emit_dispatcher_arm(
        self,
        *,
        is_first: bool,
        constructor_names: tuple[str, ...],
        mangled: str,
    ) -> None:
        cond = "if" if is_first else "elif"
        self._writeln(f"{cond} type(self_arg).__name__ in {constructor_names!r}:")
        self._indent()
        self._writeln(f"return {mangled}(self_arg, *_args)")
        self._dedent()

    def _emit_dispatcher_else(self, first_trait: str) -> None:
        self._writeln("else:")
        self._indent()
        self._writeln(
            f"raise RuntimeError("
            f"f\"No implementation of trait '{first_trait}' "
            f'for type {{type(self_arg).__name__}}")'
        )
        self._dedent()

    def _close_dispatcher(self) -> None:
        # Python dispatcher body ends with a dedent — there's no
        # explicit closing punctuation.
        self._dedent()

    def _compile_function_def(self, defn: FunctionDef) -> None:
        """Compile a function definition to Python."""
        self._writeln()
        self._emit_source_location(defn.location)

        # Function signature with default values.
        # Annotations are quoted to support forward references to user-defined
        # types (recursive ADTs, types defined later in module order).
        param_parts = []
        for p in defn.params:
            ann = self._compile_type_annotation(p.param_type)
            part = f"{self._mangle_name(p.name)}: '{ann}'"
            if p.default_value is not None:
                part += " = _GENO_MISSING"
            param_parts.append(part)
        params = ", ".join(param_parts)
        ret_type = self._compile_type_annotation(defn.return_type)
        func_name = self._mangle_name(defn.name)
        async_prefix = "async " if defn.is_async else ""
        self._writeln(f"{async_prefix}def {func_name}({params}) -> '{ret_type}':")

        self._indent()

        # Docstring with specs
        if defn.specs.requires or defn.specs.ensures or defn.specs.examples:
            self._writeln('"""')
            for req in defn.specs.requires:
                self._writeln(f"Requires: {self._compile_expr(req.condition)}")
            for ens in defn.specs.ensures:
                self._writeln(f"Ensures: {self._compile_expr(ens.condition)}")
            for ex in defn.specs.examples:
                self._writeln(
                    f"Example: {self._compile_expr(ex.input_expr)} -> {self._compile_expr(ex.output_expr)}"
                )
            self._writeln('"""')

        for p in defn.params:
            if p.default_value is None:
                continue
            param_name = self._mangle_name(p.name)
            default_value = self._compile_expr(p.default_value)
            self._writeln(f"if {param_name} is _GENO_MISSING:")
            self._indent()
            self._writeln(f"{param_name} = {default_value}")
            self._dedent()

        # Generate requires checks at function entry
        for req in defn.specs.requires:
            if isinstance(req.condition, BooleanLiteral):
                if req.condition.value:
                    continue
                raise CompileError(
                    f"`requires false` on {defn.name} makes the function uncallable"
                )
            cond = self._compile_expr(req.condition)
            self._writeln(f"if not ({cond}):")
            self._indent()
            self._writeln(
                f'raise _GenoContractViolation("Precondition failed for {defn.name}: requires clause evaluated to false")'
            )
            self._dedent()

        # If we have ensures clauses, we need to wrap the body
        has_ensures = any(
            not isinstance(ens.condition, BooleanLiteral) or not ens.condition.value
            for ens in defn.specs.ensures
        )

        if has_ensures:
            # Use a helper function to capture result and check ensures.
            # When the enclosing function is async the helper must also be
            # async — otherwise an `await` inside the body compiles to
            # invalid Python (issue #666).
            helper_prefix = "async " if defn.is_async else ""
            self._writeln(f"{helper_prefix}def _body_{defn.name}():")
            self._indent()

        # Only wrap in try/except for ? operator propagation when the
        # function actually uses the ? operator. This avoids exception-
        # setup overhead in hot loops for leaf functions.
        uses_propagate = self._uses_propagate(defn)

        if uses_propagate:
            self._writeln("try:")
            self._indent()

        if not defn.body:
            self._writeln("pass")
        else:
            for stmt in defn.body:
                self._compile_statement(stmt)

        if uses_propagate:
            self._dedent()
            self._writeln("except _PropagateReturn as __geno_pr__:")
            self._indent()
            self._writeln("return __geno_pr__.value")
            self._dedent()

        if has_ensures:
            self._dedent()
            # Call the body and capture result. For async enclosing
            # functions the helper is async too, so the call must be awaited.
            call_prefix = "await " if defn.is_async else ""
            self._writeln(f"result = {call_prefix}_body_{defn.name}()")
            if self._expected_runtime_type_is_float(defn.return_type):
                self._writeln("result = _promote_int_to_float(result)")
            # Check ensures clauses
            for ens in defn.specs.ensures:
                if isinstance(ens.condition, BooleanLiteral):
                    if ens.condition.value:
                        continue
                    raise CompileError(
                        f"`ensures false` on {defn.name} makes the function unusable"
                    )
                cond = self._compile_expr(ens.condition)
                self._writeln(f"if not ({cond}):")
                self._indent()
                self._writeln(
                    f'raise _GenoContractViolation(f"Postcondition failed for {defn.name}: ensures clause evaluated to false (result was {{result}})")'
                )
                self._dedent()
            self._writeln("return result")

        self._dedent()

    # =========================================================================
    # Statements
    # =========================================================================

    def _compile_statement(self, stmt: Statement) -> None:
        """Compile a statement to Python."""
        stmt_type = type(stmt)

        # Emit source mapping comment for control-flow and binding statements
        if stmt_type in (
            LetStatement,
            VarStatement,
            IfStatement,
            WhileStatement,
            ForStatement,
            MatchStatement,
            ReturnStatement,
            TryStatement,
        ) or isinstance(
            stmt,
            (
                LetStatement,
                VarStatement,
                IfStatement,
                WhileStatement,
                ForStatement,
                MatchStatement,
                ReturnStatement,
                TryStatement,
            ),
        ):
            self._emit_source_location(stmt.location)

        if stmt_type is LetStatement:
            self._compile_let_statement(cast(LetStatement, stmt))
        elif stmt_type is VarStatement:
            self._compile_var_statement(cast(VarStatement, stmt))
        elif stmt_type is TupleDestructureStatement:
            self._compile_tuple_destructure(cast(TupleDestructureStatement, stmt))
        elif stmt_type is AssignStatement:
            self._compile_assign_statement(cast(AssignStatement, stmt))
        elif stmt_type is IndexAssignStatement:
            self._compile_index_assign_statement(cast(IndexAssignStatement, stmt))
        elif stmt_type is FieldAssignStatement:
            self._compile_field_assign_statement(cast(FieldAssignStatement, stmt))
        elif stmt_type is IfStatement:
            self._compile_if_statement(cast(IfStatement, stmt))
        elif stmt_type is WhileStatement:
            self._compile_while_statement(cast(WhileStatement, stmt))
        elif stmt_type is ForStatement:
            self._compile_for_statement(cast(ForStatement, stmt))
        elif stmt_type is MatchStatement:
            self._compile_match_statement(cast(MatchStatement, stmt))
        elif stmt_type is ReturnStatement:
            self._compile_return_statement(cast(ReturnStatement, stmt))
        elif stmt_type is BreakStatement:
            self._writeln("break")
        elif stmt_type is ContinueStatement:
            self._writeln("continue")
        elif stmt_type is TryStatement:
            self._compile_try_statement(cast(TryStatement, stmt))
        elif stmt_type is ExpressionStatement:
            expr_stmt = cast(ExpressionStatement, stmt)
            self._writeln(self._compile_expr(expr_stmt.expression))
        else:
            self._compile_statement_slowpath(stmt)

    def _compile_statement_slowpath(self, stmt: Statement) -> None:
        """Compatibility fallback for statement subclasses."""
        if isinstance(stmt, LetStatement):
            self._compile_let_statement(stmt)
        elif isinstance(stmt, VarStatement):
            self._compile_var_statement(stmt)
        elif isinstance(stmt, TupleDestructureStatement):
            self._compile_tuple_destructure(stmt)
        elif isinstance(stmt, AssignStatement):
            self._compile_assign_statement(stmt)
        elif isinstance(stmt, IndexAssignStatement):
            self._compile_index_assign_statement(stmt)
        elif isinstance(stmt, FieldAssignStatement):
            self._compile_field_assign_statement(stmt)
        elif isinstance(stmt, IfStatement):
            self._compile_if_statement(stmt)
        elif isinstance(stmt, WhileStatement):
            self._compile_while_statement(stmt)
        elif isinstance(stmt, ForStatement):
            self._compile_for_statement(stmt)
        elif isinstance(stmt, MatchStatement):
            self._compile_match_statement(stmt)
        elif isinstance(stmt, ReturnStatement):
            self._compile_return_statement(stmt)
        elif isinstance(stmt, BreakStatement):
            self._writeln("break")
        elif isinstance(stmt, ContinueStatement):
            self._writeln("continue")
        elif isinstance(stmt, TryStatement):
            self._compile_try_statement(stmt)
        elif isinstance(stmt, ExpressionStatement):
            self._writeln(self._compile_expr(stmt.expression))
        else:
            raise CompileError(f"Unsupported statement node: {type(stmt).__name__}")

    def _compile_field_assign_statement(self, stmt: FieldAssignStatement) -> None:
        """Compile field assignment against frozen Python constructor dataclasses."""
        target = self._compile_expr(stmt.target)
        value = self._compile_expr(stmt.value)
        self._writeln(f"_object_setattr({target}, {stmt.field_name!r}, {value})")

    def _compile_try_statement(self, stmt: TryStatement) -> None:
        """Compile a try/catch statement to Python try/except."""
        catch_type_annot = stmt.catch_clause.type_annotation
        catch_type_name = getattr(catch_type_annot, "name", "String")
        is_string_catch = catch_type_name == "String"

        self._writeln("try:")
        self._indent()
        self._try_depth += 1
        try:
            if stmt.try_body:
                for s in stmt.try_body:
                    self._compile_statement(s)
            else:
                self._writeln("pass")
        finally:
            self._try_depth -= 1
        self._dedent()
        var_name = self._mangle_name(stmt.catch_clause.variable)
        if is_string_catch:
            self._writeln(
                "except (_GenoThrow, RuntimeError, IndexError) as __geno_err__:"
            )
            self._indent()
            self._writeln("if isinstance(__geno_err__, _GenoThrow):")
            self._indent()
            self._writeln(f"{var_name} = str(__geno_err__.value)")
            self._dedent()
            self._writeln("elif type(__geno_err__) in (RuntimeError, IndexError):")
            self._indent()
            self._writeln(f"{var_name} = str(__geno_err__)")
            self._dedent()
            self._writeln("else: raise")
        else:
            self._writeln("except _GenoThrow as __geno_err__:")
            self._indent()
            self._writeln(f"{var_name} = __geno_err__.value")
        if stmt.catch_clause.body:
            for s in stmt.catch_clause.body:
                self._compile_statement(s)
        else:
            self._writeln("pass")
        self._dedent()

    def _compile_for_statement(self, stmt: ForStatement) -> None:
        """Compile for loops while preserving per-iteration lambda captures."""
        iterable = self._compile_expr(stmt.iterable)
        var = self._mangle_name(stmt.variable)
        self._writeln(self._for_open(var, iterable))
        self._indent()
        self._active_loop_vars.append(stmt.variable)
        try:
            with self._with_shadowed_bindings([stmt.variable]):
                if stmt.body:
                    for s in stmt.body:
                        self._compile_statement(s)
                else:
                    self._writeln("pass")
        finally:
            self._active_loop_vars.pop()
        self._dedent()
        self._emit_block_close()

    def _compile_let_statement(self, stmt: LetStatement) -> None:
        """Compile a let statement.

        Match interpreter semantics: shallow copy for collections (list/dict),
        no copy for immutable primitives (Int, Float, Bool, String, Unit).
        """
        name = self._mangle_name(stmt.name)
        type_annot = stmt.type_annotation
        # Determine copy strategy from the declared type
        type_name = (
            getattr(type_annot, "name", None)
            if isinstance(type_annot, SimpleType)
            else None
        )
        expected_let_type = getattr(stmt, "_expected_runtime_type", type_annot)
        if type_name not in (
            "List",
            "Map",
        ) and not self._expected_runtime_type_is_float(expected_let_type):
            stmt_form = self._try_stmt_form_int_rhs(stmt.value)
            if stmt_form is not None:
                raw, needs_check = stmt_form
                if type_annot is not None:
                    ann = self._compile_type_annotation(type_annot)
                    self._writeln(f"{name}: '{ann}' = {raw}")
                else:
                    self._writeln(f"{name} = {raw}")
                if needs_check:
                    self._writeln_int_bits_check(name)
                return
        value = self._compile_expr(stmt.value)
        rhs = f"_geno_deepcopy({value})"
        rhs = self._promote_expr_to_expected_float(
            rhs, getattr(stmt, "_expected_runtime_type", type_annot)
        )
        if type_annot is not None:
            ann = self._compile_type_annotation(type_annot)
            # Quote annotations to match function signatures and ADT fields,
            # preventing forward-reference issues with user-defined types.
            self._writeln(f"{name}: '{ann}' = {rhs}")
        else:
            self._writeln(f"{name} = {rhs}")

    def _compile_var_statement(self, stmt: VarStatement) -> None:
        """Compile a var statement.

        Copy semantics must match let — var only makes the binding mutable,
        not the value.  Array is a reference type and is never copied.
        """
        name = self._mangle_name(stmt.name)
        type_annot = stmt.type_annotation
        type_name = (
            getattr(type_annot, "name", None)
            if isinstance(type_annot, SimpleType)
            else None
        )
        expected_type = getattr(stmt, "_expected_runtime_type", type_annot)
        if type_name not in (
            "List",
            "Map",
        ) and not self._expected_runtime_type_is_float(expected_type):
            stmt_form = self._try_stmt_form_int_rhs(stmt.value)
            if stmt_form is not None:
                raw, needs_check = stmt_form
                if type_annot is not None:
                    ann = self._compile_type_annotation(type_annot)
                    self._writeln(f"{name}: '{ann}' = {raw}")
                else:
                    self._writeln(f"{name} = {raw}")
                if needs_check:
                    self._writeln_int_bits_check(name)
                return
        value = self._compile_expr(stmt.value)
        rhs = f"_geno_deepcopy({value})"
        rhs = self._promote_expr_to_expected_float(rhs, expected_type)
        if type_annot is not None:
            ann = self._compile_type_annotation(type_annot)
            # Quote annotations to match function signatures and ADT fields,
            # preventing forward-reference issues with user-defined types.
            self._writeln(f"{name}: '{ann}' = {rhs}")
        else:
            self._writeln(f"{name} = {rhs}")

    @staticmethod
    def _needs_constructor_copy(value: Expression) -> bool:
        """Return whether binding *value* must snapshot a user record."""
        resolved_type = getattr(value, "_resolved_type", None)
        return isinstance(resolved_type, UserType) and not isinstance(
            value, (ConstructorCall, TypeIdentifier, WithExpr)
        )

    # ``_compile_tuple_destructure`` lives on ``BaseCompiler``; Python's
    # emission goes through ``_tuple_destructure_stmt`` below.  #622 slice.

    def _compile_return_statement(self, stmt: ReturnStatement) -> None:
        value = self._compile_expr(stmt.value)
        value = self._promote_expr_to_expected_float(
            value, getattr(stmt, "_expected_runtime_type", None)
        )
        self._writeln(self._return_stmt(value))

    def _try_stmt_form_int_rhs(self, value_expr: Expression) -> tuple[str, bool] | None:
        """Raw RHS for an Int-arithmetic assignment plus whether the target
        needs a post-assignment bits check.

        The statement form (assign raw, then check the target name) saves
        the assignment-expression temp of the inline guard. It is skipped
        inside try blocks, where a same-function catch could observe the
        over-limit value already bound to the target.
        """
        if self._try_depth or self._in_comprehension_iterable:
            return None
        if self._is_int_addsub(value_expr):
            binary = cast(BinaryOp, value_expr)
            left = self._compile_addsub_operand(binary.left)
            right = self._compile_addsub_operand(binary.right)
            raw = f"(({left}) {binary.operator} ({right}))"
            operand_is_chain = self._is_transient_chain_node(
                binary.left
            ) or self._is_transient_chain_node(binary.right)
            needs_check = operand_is_chain or not self._has_growth_free_addend(binary)
            return raw, needs_check
        if self._is_growth_free_mul(value_expr) or (
            isinstance(value_expr, BinaryOp)
            and value_expr.operator == "*"
            and is_int_type(value_expr.left)
            and is_int_type(value_expr.right)
        ):
            binary = cast(BinaryOp, value_expr)
            left = self._compile_addsub_operand(binary.left)
            right = self._compile_addsub_operand(binary.right)
            return f"(({left}) * ({right}))", True
        return None

    def _writeln_int_bits_check(self, name: str) -> None:
        """Emit the statement-form integer-bits check for *name*."""
        self._writeln(f"if {name}.bit_length() > _MAX_INTEGER_BITS: _int_oob({name})")

    def _compile_assign_statement(self, stmt: AssignStatement) -> None:
        expected_type = getattr(stmt, "_expected_runtime_type", None)
        if not self._expected_runtime_type_is_float(expected_type):
            stmt_form = self._try_stmt_form_int_rhs(stmt.value)
            if stmt_form is not None:
                raw, needs_check = stmt_form
                name = self._mangle_name(stmt.target)
                self._writeln(f"{name} = {raw}")
                if needs_check:
                    self._writeln_int_bits_check(name)
                return
        value = self._compile_expr(stmt.value)
        value = self._promote_expr_to_expected_float(value, expected_type)
        self._writeln(f"{self._mangle_name(stmt.target)} = {value}")

    # ``_compile_{index_assign,field_assign}_statement`` live on
    # ``BaseCompiler`` — both differ between Python and JS only in the
    # per-statement terminator (none vs ``;``), supplied by
    # ``_statement_terminator`` below.  #622 slice 2.

    def _compile_match_statement(self, stmt: MatchStatement) -> None:
        """Compile a match statement using if-elif chain (with flag for guarded arms)."""
        scrutinee = self._compile_expr(stmt.scrutinee)
        scrutinee_var = self._fresh_temp()
        self._writeln(f"{scrutinee_var} = {scrutinee}")

        has_guards = any(arm.guard is not None for arm in stmt.arms)

        if has_guards:
            # Use a flag-based approach when guards are present, since a
            # pattern can match but a guard can fail, requiring fallthrough.
            matched_var = self._fresh_temp()
            self._writeln(f"{matched_var} = False")
            for arm in stmt.arms:
                cond, bindings = self._compile_pattern_condition(
                    arm.pattern, scrutinee_var
                )
                self._writeln(f"if not {matched_var} and {cond}:")
                self._indent()
                for var_name, expr in bindings:
                    self._writeln(f"{var_name} = {expr}")
                if arm.guard is not None:
                    guard_code = self._compile_expr(arm.guard)
                    self._writeln(f"if {guard_code}:")
                    self._indent()
                    if arm.body:
                        for s in arm.body:
                            self._compile_statement(s)
                    self._writeln(f"{matched_var} = True")
                    self._dedent()
                else:
                    if arm.body:
                        for s in arm.body:
                            self._compile_statement(s)
                    self._writeln(f"{matched_var} = True")
                self._dedent()
            self._writeln(f"if not {matched_var}:")
            self._indent()
            self._writeln('raise RuntimeError("No matching pattern")')
            self._dedent()
        else:
            # Simple if-elif chain when no guards are present
            first = True
            for arm in stmt.arms:
                keyword = "if" if first else "elif"
                first = False
                cond, bindings = self._compile_pattern_condition(
                    arm.pattern, scrutinee_var
                )
                self._writeln(f"{keyword} {cond}:")
                self._indent()
                for var_name, expr in bindings:
                    self._writeln(f"{var_name} = {expr}")
                if arm.body:
                    for s in arm.body:
                        self._compile_statement(s)
                else:
                    self._writeln("pass")
                self._dedent()
            self._writeln("else:")
            self._indent()
            self._writeln('raise RuntimeError("No matching pattern")')
            self._dedent()

    def _compile_pattern_condition(
        self, pattern: Pattern, scrutinee: str
    ) -> tuple[str, list[tuple[str, str]]]:
        """
        Compile a pattern to a condition and list of bindings.

        Returns:
            (condition_string, [(var_name, expr_string), ...])
        """
        bindings: list[tuple[str, str]] = []

        if isinstance(pattern, WildcardPattern):
            return ("True", bindings)

        if isinstance(pattern, VariablePattern):
            bindings.append((self._mangle_name(pattern.name), scrutinee))
            return ("True", bindings)

        if isinstance(pattern, LiteralPattern):
            value = pattern.value
            if isinstance(value, str):
                # Must reuse the same escaping as StringLiteral expression
                # codegen — a naive f'"{value}"' produces invalid Python
                # for any pattern containing a quote or backslash.
                value = self._emit_python_string_literal(value)
            elif isinstance(value, bool):
                value = "True" if value else "False"
            elif isinstance(value, int):
                # Pattern literals keep the runtime guard unconditionally:
                # tightened _GENO_MAX_INTEGER_BITS limits must reject them at
                # match time (see test_compiled_int_literal_pattern_honors_bit_limit).
                value = f"_check_collection_size({value})"
            return (f"{scrutinee} == {value}", bindings)

        if isinstance(pattern, ConstructorPattern):
            # Check constructor name
            if pattern.constructor == "None":
                cond = f"isinstance({scrutinee}, _None)"
            else:
                cond = f"isinstance({scrutinee}, {pattern.constructor})"

            # Handle subpatterns
            for i, subpat in enumerate(pattern.subpatterns):
                # Get field name from type definition
                field_name = self._get_constructor_field_name(pattern.constructor, i)
                field_access = f"get_field({scrutinee}, {field_name!r})"

                sub_cond, sub_bindings = self._compile_pattern_condition(
                    subpat, field_access
                )
                if sub_cond != "True":
                    cond = f"({cond}) and ({sub_cond})"
                bindings.extend(sub_bindings)

            return (cond, bindings)

        if isinstance(pattern, ListPattern):
            # Check for rest pattern
            rest_index = None
            for i, elem_pat in enumerate(pattern.elements):
                if isinstance(elem_pat, RestPattern):
                    rest_index = i
                    break

            if rest_index is not None:
                fixed_before = rest_index
                fixed_after = len(pattern.elements) - rest_index - 1
                min_required = fixed_before + fixed_after
                cond = f"isinstance({scrutinee}, list) and len({scrutinee}) >= {min_required}"

                # Elements before rest
                for i in range(fixed_before):
                    elem_access = f"{scrutinee}[{i}]"
                    sub_cond, sub_bindings = self._compile_pattern_condition(
                        pattern.elements[i], elem_access
                    )
                    if sub_cond != "True":
                        cond = f"({cond}) and ({sub_cond})"
                    bindings.extend(sub_bindings)

                # Rest binding
                rest_pat = pattern.elements[rest_index]
                if isinstance(rest_pat, RestPattern) and rest_pat.name is not None:
                    if fixed_after > 0:
                        bindings.append(
                            (
                                rest_pat.name,
                                f"{scrutinee}[{fixed_before}:{-fixed_after}]",
                            )
                        )
                    else:
                        bindings.append(
                            (rest_pat.name, f"{scrutinee}[{fixed_before}:]")
                        )

                # Elements after rest
                for i in range(fixed_after):
                    pat_idx = rest_index + 1 + i
                    elem_access = f"{scrutinee}[{-(fixed_after - i)}]"
                    sub_cond, sub_bindings = self._compile_pattern_condition(
                        pattern.elements[pat_idx], elem_access
                    )
                    if sub_cond != "True":
                        cond = f"({cond}) and ({sub_cond})"
                    bindings.extend(sub_bindings)
            else:
                cond = f"isinstance({scrutinee}, list) and len({scrutinee}) == {len(pattern.elements)}"

                for i, elem_pat in enumerate(pattern.elements):
                    elem_access = f"{scrutinee}[{i}]"
                    sub_cond, sub_bindings = self._compile_pattern_condition(
                        elem_pat, elem_access
                    )
                    if sub_cond != "True":
                        cond = f"({cond}) and ({sub_cond})"
                    bindings.extend(sub_bindings)

            return (cond, bindings)

        return ("True", bindings)

    # =========================================================================
    # Expressions
    # =========================================================================

    def _compile_expr(self, expr: Expression) -> str:
        """Compile an expression to a Python expression string."""
        expr_type = type(expr)

        if expr_type is IntegerLiteral:
            int_expr = cast(IntegerLiteral, expr)
            return self._compile_int_literal(int_expr.value)

        if expr_type is FloatLiteral:
            float_expr = cast(FloatLiteral, expr)
            return str(float_expr.value)

        if expr_type is StringLiteral:
            string_expr = cast(StringLiteral, expr)
            return self._compile_string_literal(string_expr.value)

        if expr_type is FStringExpr:
            return self._compile_fstring_expr(cast(FStringExpr, expr))

        if expr_type is BooleanLiteral:
            bool_expr = cast(BooleanLiteral, expr)
            return "True" if bool_expr.value else "False"

        if expr_type is Identifier:
            identifier = cast(Identifier, expr)
            return self._compiled_identifier_name(identifier.name)

        if expr_type is TypeIdentifier:
            type_identifier = cast(TypeIdentifier, expr)
            if type_identifier.name == "None":
                return "None_"
            # Check if this is a nullary constructor (type variant with no fields)
            variant = self._constructor_to_variant.get(type_identifier.name)
            if variant is not None and not variant.fields:
                return f"{type_identifier.name}()"
            return str(type_identifier.name)

        if expr_type is ListLiteral:
            list_expr = cast(ListLiteral, expr)
            elements = ", ".join(self._compile_expr(e) for e in list_expr.elements)
            return f"_check_collection_size([{elements}])"

        if expr_type is BinaryOp:
            return self._compile_binary_op(cast(BinaryOp, expr))

        if expr_type is UnaryOp:
            return self._compile_unary_op(cast(UnaryOp, expr))

        if expr_type is FunctionCall:
            return self._compile_function_call(cast(FunctionCall, expr))

        if expr_type is IndexAccess:
            index_expr = cast(IndexAccess, expr)
            target = self._compile_expr(index_expr.target)
            index = self._compile_expr(index_expr.index)
            return f"_safe_index({target}, {index})"

        if expr_type is FieldAccess:
            field_access = cast(FieldAccess, expr)
            target = self._compile_expr(field_access.target)
            return f"get_field({target}, {field_access.field_name!r})"

        if expr_type is Pipeline:
            return self._compile_pipeline(cast(Pipeline, expr))

        if expr_type is LambdaExpr:
            return self._compile_lambda(cast(LambdaExpr, expr))

        if expr_type is ConstructorCall:
            return self._compile_constructor_call(cast(ConstructorCall, expr))

        if expr_type is TupleExpr:
            tuple_expr = cast(TupleExpr, expr)
            if not tuple_expr.elements:
                return "None"
            elements = ", ".join(self._compile_expr(e) for e in tuple_expr.elements)
            if len(tuple_expr.elements) == 1:
                return f"_check_collection_size(({elements},))"
            return f"_check_collection_size(({elements}))"

        if expr_type is MatchExpr:
            return self._compile_match_expr(cast(MatchExpr, expr))

        if expr_type is TypedHole:
            typed_hole = cast(TypedHole, expr)
            escaped_name = typed_hole.name.replace("\\", "\\\\").replace('"', '\\"')
            return f'_typed_hole("{escaped_name}")'

        if expr_type is PropagateExpr:
            propagate_expr = cast(PropagateExpr, expr)
            operand = self._compile_expr(propagate_expr.operand)
            return f"_propagate({operand})"

        if expr_type is WithExpr:
            return cast(str, self._compile_with_expr(cast(WithExpr, expr)))

        if expr_type is ListComprehension:
            list_comp = cast(ListComprehension, expr)
            var = self._mangle_name(list_comp.variable)
            iterable = self._compile_comprehension_iterable(list_comp.iterable)
            with self._with_shadowed_bindings([list_comp.variable]):
                elem = self._compile_expr(list_comp.element_expr)
                if list_comp.condition is not None:
                    cond = self._compile_expr(list_comp.condition)
                    return f"_check_collection_size([{elem} for {var} in {iterable} if {cond}])"
                return f"_check_collection_size([{elem} for {var} in {iterable}])"

        if expr_type is ThrowExpression:
            throw_expr = cast(ThrowExpression, expr)
            value = self._compile_expr(throw_expr.value)
            return f"_geno_throw({value})"

        if expr_type is AwaitExpr:
            await_expr = cast(AwaitExpr, expr)
            inner = self._compile_expr(await_expr.expr)
            return f"(await {inner})"

        return self._compile_expr_slowpath(expr)

    def _compile_int_literal(self, value: int) -> str:
        """Emit an integer literal, skipping the runtime guard when provably safe.

        A constant cannot grow at runtime, so the integer-bits guard only
        matters for literals large enough to plausibly exceed a configured
        limit. Small literals compile to plain Python constants (the JS
        backend already emits all literals raw under a compile-time bound);
        larger literals keep the runtime check so tightened
        _GENO_MAX_INTEGER_BITS limits are still honored. Pattern literals
        are guarded unconditionally in _compile_pattern_condition.
        """
        if value.bit_length() <= _RAW_INT_LITERAL_MAX_BITS:
            return str(value)
        return f"_check_collection_size({value})"

    def _compile_string_literal(self, value: str) -> str:
        """Emit a string literal with its size check inlined.

        The length is known at compile time, so the runtime
        _check_collection_size call reduces to a constant comparison against
        the (runtime-configurable) _MAX_COLLECTION_SIZE; the cold branch
        delegates to _check_collection_kind, which raises the identical
        \"String size exceeds limit\" error. Behavior under tightened limits
        is unchanged: the literal still raises when (and only when) it is
        evaluated.
        """
        literal = self._emit_python_string_literal(value)
        size = len(value)
        if size == 0:
            # The empty string can never exceed a size limit.
            return literal
        return (
            f"({literal} if {size} <= _MAX_COLLECTION_SIZE"
            f' else _check_collection_kind("String", {size}))'
        )

    def _compile_expr_slowpath(self, expr: Expression) -> str:
        """Compatibility fallback for expression subclasses."""
        if isinstance(expr, IntegerLiteral):
            return self._compile_int_literal(expr.value)

        if isinstance(expr, FloatLiteral):
            return str(expr.value)

        if isinstance(expr, StringLiteral):
            return self._compile_string_literal(expr.value)

        if isinstance(expr, FStringExpr):
            return self._compile_fstring_expr(expr)

        if isinstance(expr, BooleanLiteral):
            return "True" if expr.value else "False"

        if isinstance(expr, Identifier):
            return self._mangle_name(expr.name)

        if isinstance(expr, TypeIdentifier):
            if expr.name == "None":
                return "None_"
            variant = self._constructor_to_variant.get(expr.name)
            if variant is not None and not variant.fields:
                return f"{expr.name}()"
            return str(expr.name)

        if isinstance(expr, ListLiteral):
            elements = ", ".join(self._compile_expr(e) for e in expr.elements)
            return f"_check_collection_size([{elements}])"

        if isinstance(expr, BinaryOp):
            return self._compile_binary_op(expr)

        if isinstance(expr, UnaryOp):
            return self._compile_unary_op(expr)

        if isinstance(expr, FunctionCall):
            return self._compile_function_call(expr)

        if isinstance(expr, IndexAccess):
            target = self._compile_expr(expr.target)
            index = self._compile_expr(expr.index)
            return f"_safe_index({target}, {index})"

        if isinstance(expr, FieldAccess):
            target = self._compile_expr(expr.target)
            return f"get_field({target}, {expr.field_name!r})"

        if isinstance(expr, Pipeline):
            return self._compile_pipeline(expr)

        if isinstance(expr, LambdaExpr):
            return self._compile_lambda(expr)

        if isinstance(expr, ConstructorCall):
            return self._compile_constructor_call(expr)

        if isinstance(expr, TupleExpr):
            if not expr.elements:
                return "None"
            elements = ", ".join(self._compile_expr(e) for e in expr.elements)
            if len(expr.elements) == 1:
                return f"_check_collection_size(({elements},))"
            return f"_check_collection_size(({elements}))"

        if isinstance(expr, MatchExpr):
            return self._compile_match_expr(expr)

        if isinstance(expr, TypedHole):
            escaped_name = expr.name.replace("\\", "\\\\").replace('"', '\\"')
            return f'_typed_hole("{escaped_name}")'

        if isinstance(expr, PropagateExpr):
            operand = self._compile_expr(expr.operand)
            return f"_propagate({operand})"

        if isinstance(expr, WithExpr):
            return cast(str, self._compile_with_expr(expr))

        if isinstance(expr, ListComprehension):
            elem = self._compile_expr(expr.element_expr)
            var = self._mangle_name(expr.variable)
            iterable = self._compile_comprehension_iterable(expr.iterable)
            if expr.condition is not None:
                cond = self._compile_expr(expr.condition)
                return f"_check_collection_size([{elem} for {var} in {iterable} if {cond}])"
            return f"_check_collection_size([{elem} for {var} in {iterable}])"

        if isinstance(expr, ThrowExpression):
            value = self._compile_expr(expr.value)
            return f"_geno_throw({value})"

        if isinstance(expr, AwaitExpr):
            inner = self._compile_expr(expr.expr)
            return f"(await {inner})"

        raise CompileError(f"Unsupported expression node: {type(expr).__name__}")

    @staticmethod
    def _uses_propagate(defn) -> bool:
        """Check if a function body contains a ? (propagate) expression."""
        from .ast_nodes import PropagateExpr

        field_cache: dict[type, tuple[str, ...]] = {}
        stack: list[object] = list(defn.body or [])

        while stack:
            node = stack.pop()
            node_type = type(node)
            if isinstance(node, PropagateExpr):
                return True
            if node_type is list:
                stack.extend(reversed(cast(list[object], node)))
                continue
            if node_type is tuple:
                stack.extend(reversed(cast(tuple[object, ...], node)))
                continue
            if not hasattr(node, "__dict__"):
                continue

            field_names = field_cache.get(node_type)
            if field_names is None:
                field_names = tuple(
                    name
                    for name in vars(node)
                    if not name.startswith("_")
                    and name
                    not in {
                        "location",
                        "type_annotation",
                        "param_type",
                        "return_type",
                        "var_type",
                        "hole_type",
                    }
                )
                field_cache[node_type] = field_names

            for field_name in reversed(field_names):
                child = getattr(node, field_name)
                if child is not None:
                    stack.append(child)

        return False

    @staticmethod
    def _is_float_type(expr: Expression) -> bool:
        """Check if a typechecked expression has Float type."""
        from .types import FloatType

        t = getattr(expr, "_resolved_type", None)
        return isinstance(t, FloatType)

    @staticmethod
    def _expected_runtime_type_is_float(expected_type: object) -> bool:
        if isinstance(expected_type, FloatType):
            return True
        return (
            isinstance(expected_type, SimpleType)
            and expected_type.name == "Float"
            and not expected_type.type_params
        )

    def _promote_expr_to_expected_float(self, value: str, expected_type: object) -> str:
        if self._expected_runtime_type_is_float(expected_type):
            return f"_promote_int_to_float({value})"
        return value

    @staticmethod
    def _is_indexable_type(expr: Expression) -> bool:
        """Check if a typechecked expression has List or String type."""
        from .types import ListType, StringType

        t = getattr(expr, "_resolved_type", None)
        return isinstance(t, (ListType, StringType))

    def _compile_comprehension_iterable(self, iterable: Expression) -> str:
        """Compile a comprehension's iterable with assignment expressions
        disabled, since Python rejects them in that position."""
        self._in_comprehension_iterable += 1
        try:
            return self._compile_expr(iterable)
        finally:
            self._in_comprehension_iterable -= 1

    def _compile_int_arith_guarded(self, left: str, op: str, right: str) -> str:
        """Inline the integer-bits guard for statically Int + / - / *.

        Static Int operands are runtime ints (Int-where-Float only widens the
        other direction), so the str/list size pre-checks in the generic
        _safe_* helpers cannot apply; the only required guard is the integer
        bit-length ceiling. Emitting it inline keeps the exact raise
        condition and message (via the _int_oob cold path, which reads the
        runtime-configured _MAX_INTEGER_BITS) while avoiding a Python
        function call on every arithmetic operation.

        Python forbids assignment expressions inside a comprehension's
        iterable, so those positions use the equivalent guarded call form.
        """
        if self._in_comprehension_iterable:
            return f"_check_int_bits(({left}) {op} ({right}))"
        t = self._fresh_temp()
        return (
            f"({t} if ({t} := ({left}) {op} ({right})).bit_length()"
            f" <= _MAX_INTEGER_BITS else _int_oob({t}))"
        )

    def _compile_int_divmod_by_literal(
        self, expr: BinaryOp, left: str, py_op: str
    ) -> str | None:
        """Inline Int / and % when the divisor is a positive integer literal.

        With a positive constant divisor, division by zero is impossible and
        the result magnitude never exceeds the operands', so no runtime guard
        is needed; only Python's floor semantics must be corrected to Geno's
        truncation-toward-zero for negative dividends:
        ``a % 10`` -> ``(a % 10 if a >= 0 else -(-a % 10))``.

        Returns None when the divisor is not a positive small literal, where
        the dividend would need an assignment expression in a position that
        forbids one, or for ``**``-style growth cases — callers then fall
        back to the guarded helper call.
        """
        divisor = expr.right
        if (
            not isinstance(divisor, IntegerLiteral)
            or divisor.value <= 0
            or divisor.value.bit_length() > _RAW_INT_LITERAL_MAX_BITS
        ):
            return None
        lit = divisor.value
        if isinstance(expr.left, (Identifier, IntegerLiteral)):
            # Side-effect free and cheap to re-evaluate: no temp needed.
            return f"(({left}) {py_op} {lit} if ({left}) >= 0 else -(-({left}) {py_op} {lit}))"
        if self._in_comprehension_iterable:
            return None
        t = self._fresh_temp()
        return (
            f"({t} {py_op} {lit} if ({t} := ({left})) >= 0 else -(-{t} {py_op} {lit}))"
        )

    def _compile_int_divmod_simple(
        self, expr: BinaryOp, left: str, right: str, py_op: str
    ) -> str | None:
        """Inline Int / and % for side-effect-free operands.

        Python's floor // and % agree with Geno's truncation-toward-zero
        semantics whenever the dividend is non-negative and the divisor is
        positive — the overwhelmingly common case — so emit the raw operator
        behind that runtime test and fall back to the guarded helper (which
        also produces the division-by-zero error) otherwise. Restricted to
        identifier/literal operands so re-evaluating them is free and no
        assignment expression is needed.
        """
        if not isinstance(expr.left, (Identifier, IntegerLiteral)) or not isinstance(
            expr.right, (Identifier, IntegerLiteral)
        ):
            return None
        helper = "_int_div" if py_op == "//" else "_int_mod"
        return (
            f"(({left}) {py_op} ({right}) if ({left}) >= 0 < ({right})"
            f" else {helper}(({left}), ({right})))"
        )

    @staticmethod
    def _has_growth_free_addend(expr: BinaryOp) -> bool:
        """True when either operand of an Int +/- is a small integer literal.

        See _GROWTH_FREE_ADDEND_MAX_BITS: adding/subtracting a bounded
        constant cannot produce integer-bomb growth, so the bits guard is
        skipped for these operations.
        """
        for operand in (expr.left, expr.right):
            if (
                type(operand) is IntegerLiteral
                and operand.value.bit_length() <= _GROWTH_FREE_ADDEND_MAX_BITS
            ):
                return True
        return False

    @staticmethod
    def _is_int_addsub(expr: Expression) -> bool:
        """True for an Int +/- node (both operands statically Int)."""
        return (
            isinstance(expr, BinaryOp)
            and expr.operator in ("+", "-")
            and is_int_type(expr.left)
            and is_int_type(expr.right)
        )

    @staticmethod
    def _is_growth_free_mul(expr: Expression) -> bool:
        """True for an Int * node with a small integer literal operand.

        Multiplying by a bounded constant adds at most ~33 bits per chain
        level — additive in compile-time expression depth, unlike var*var
        whose bit lengths add and can amplify exponentially with depth — so
        such nodes may compile raw in transient positions where a consuming
        root guard (or a comparison's bool result) bounds what escapes.
        """
        if not (
            isinstance(expr, BinaryOp)
            and expr.operator == "*"
            and is_int_type(expr.left)
            and is_int_type(expr.right)
        ):
            return False
        return any(
            type(operand) is IntegerLiteral
            and operand.value.bit_length() <= _GROWTH_FREE_ADDEND_MAX_BITS
            for operand in (expr.left, expr.right)
        )

    def _is_transient_chain_node(self, expr: Expression) -> bool:
        """True for nodes that may compile raw under a guarded consumer."""
        return self._is_int_addsub(expr) or self._is_growth_free_mul(expr)

    def _try_compile_raw_len(self, operand: Expression) -> str | None:
        """Emit raw len() for a length() call in a transient position.

        Mirrors the length() builtin fast path's gating. Only used where the
        result does not escape (comparison operands and operands of guarded
        Int +/- roots): a materialized container's length is bounded by
        process memory, and any over-limit arithmetic combining it is still
        caught by the consuming root guard.
        """
        if (
            isinstance(operand, FunctionCall)
            and isinstance(operand.function, Identifier)
            and operand.function.name == LENGTH_FAST_PATH_BUILTIN
            and len(operand.arguments) == 1
            and operand.arguments[0].name is None
        ):
            value_expr = operand.arguments[0].value
            if has_len_fast_path(value_expr):
                return f"len({self._compile_expr(value_expr)})"
        return None

    def _compile_addsub_operand(self, operand: Expression) -> str:
        """Compile an operand consumed directly by a guarded Int +/-.

        Within a pure Int +/- chain only the root needs the bits guard: each
        +/- level can raise the bit length by at most one, the chain depth
        is fixed at compile time, and the consuming operation re-checks the
        combined result, so intermediates cannot bomb and every escaping
        value is still validated. Multiplication and other growth vectors
        are not part of such chains and keep their own guards. length()
        calls compile to raw len() here for the same reason.
        """
        if self._is_transient_chain_node(operand):
            binary = cast(BinaryOp, operand)
            left = self._compile_addsub_operand(binary.left)
            right = self._compile_addsub_operand(binary.right)
            return f"(({left}) {binary.operator} ({right}))"
        if type(operand) is StringLiteral:
            # A literal compared (bool result) or combined under a guarded
            # root cannot escape this expression, so its size check is
            # unnecessary here.
            return self._emit_python_string_literal(operand.value)
        raw_len = self._try_compile_raw_len(operand)
        if raw_len is not None:
            return raw_len
        return self._compile_expr(operand)

    def _compile_binary_op(self, expr: BinaryOp) -> str:
        """Compile a binary operation."""
        # Numeric operands still use guarded helpers for +, *, and - because
        # Geno permits Int values where Float is expected, so runtime values may
        # be Python ints even when the static operand type is Float.
        both_numeric = is_numeric_type(expr.left) and is_numeric_type(expr.right)
        both_int = is_int_type(expr.left) and is_int_type(expr.right)

        # Int +/- compiles its operands through the chain-aware path, so it
        # must dispatch before the generic operand compilation below (which
        # can have write side effects and must run exactly once).
        if expr.operator in ("+", "-") and both_int:
            op = expr.operator
            operand_is_chain = self._is_transient_chain_node(
                expr.left
            ) or self._is_transient_chain_node(expr.right)
            left = self._compile_addsub_operand(expr.left)
            right = self._compile_addsub_operand(expr.right)
            if not operand_is_chain and self._has_growth_free_addend(expr):
                # A checked value +/- a bounded constant cannot bomb;
                # raw-chain operands instead require the root guard.
                return f"(({left}) {op} ({right}))"
            return self._compile_int_arith_guarded(left, op, right)

        # Int * roots guard their own result; operands may use the raw
        # transient forms since the root guard re-checks the product.
        if expr.operator == "*" and both_int:
            left = self._compile_addsub_operand(expr.left)
            right = self._compile_addsub_operand(expr.right)
            return self._compile_int_arith_guarded(left, "*", right)

        # Comparison results are transient bools, so operands may use the
        # unguarded chain/len forms; over-limit values cannot escape a
        # comparison. Dispatched early for the same single-compile reason.
        if expr.operator in ("==", "!=", "<", ">", "<=", ">="):
            left = self._compile_addsub_operand(expr.left)
            right = self._compile_addsub_operand(expr.right)
            return f"({left} {expr.operator} {right})"

        left = self._compile_expr(expr.left)
        right = self._compile_expr(expr.right)

        # Division: emit specialized calls when types are known to avoid isinstance
        if expr.operator == "/":
            if both_int:
                inline = self._compile_int_divmod_by_literal(expr, left, "//")
                if inline is not None:
                    return inline
                inline = self._compile_int_divmod_simple(expr, left, right, "//")
                if inline is not None:
                    return inline
                return f"_int_div({left}, {right})"
            if both_numeric:
                return f"_float_div({left}, {right})"
            return f"_safe_div({left}, {right})"

        # Addition / subtraction on non-Int operands keep the generic
        # helpers (string/list size guards, Int-where-Float runtime ints).
        if expr.operator == "+":
            return f"_safe_add({left}, {right})"
        if expr.operator == "-":
            return f"_safe_sub({left}, {right})"

        # Multiplication on non-Int operands keeps the generic helper
        # (string/list repeat size guards, Int-where-Float runtime ints).
        if expr.operator == "*":
            return f"_safe_mul({left}, {right})"

        # Modulo: typed path uses helper to match truncation-toward-zero semantics
        if expr.operator == "%":
            if both_int:
                inline = self._compile_int_divmod_by_literal(expr, left, "%")
                if inline is not None:
                    return inline
                inline = self._compile_int_divmod_simple(expr, left, right, "%")
                if inline is not None:
                    return inline
                return f"_int_mod({left}, {right})"
            if both_numeric:
                return f"_numeric_mod({left}, {right})"
            return f"_safe_mod({left}, {right})"

        # Short-circuit operators must return bool to match interpreter semantics
        # (Python's `and`/`or` return operand values, not necessarily bool).
        if expr.operator in ("and", "or"):
            return f"bool({left} {expr.operator} {right})"

        # Bitwise and exponentiation operators need integer size guards:
        # with negative operands & and ^ can grow the result by one bit
        # (e.g. -5 & -4 == -8, -1 ^ 7 == -8), so they keep their helpers
        # to honor tightened bit limits exactly.
        if expr.operator == "**":
            return f"_safe_pow({left}, {right})"
        if expr.operator == "&":
            return f"_safe_bitand({left}, {right})"
        if expr.operator == "^":
            return f"_safe_bitxor({left}, {right})"
        if expr.operator == "<<":
            return f"_safe_lshift({left}, {right})"
        if expr.operator == ">>":
            return f"_safe_rshift({left}, {right})"

        op_map = {
            "==": "==",
            "!=": "!=",
            "<": "<",
            ">": ">",
            "<=": "<=",
            ">=": ">=",
        }

        py_op = op_map.get(expr.operator, expr.operator)
        return f"({left} {py_op} {right})"

    def _compile_unary_op(self, expr: UnaryOp) -> str:
        """Compile a unary operation."""
        operand = self._compile_expr(expr.operand)

        if expr.operator == "-":
            return f"(-{operand})"
        elif expr.operator == "not":
            return f"(not {operand})"
        elif expr.operator == "~":
            # ~a == -a - 1 can exceed the bit limit by one (e.g. ~7 == -8),
            # so the guarded helper stays even for statically Int operands.
            return f"_safe_invert({operand})"

        return operand

    def _compile_function_call(self, expr: FunctionCall) -> str:
        """Compile a function call."""
        has_named_args = any(arg.name for arg in expr.arguments)
        func_name = (
            expr.function.name if isinstance(expr.function, Identifier) else None
        )

        param_names = self._param_names_for_function_call(expr, func_name)

        if has_named_args and param_names is not None:
            ordered_args = self._reorder_call_args_for_compile(
                expr.arguments, param_names
            )
        else:
            ordered_args = list(expr.arguments)

        concrete_args = [arg for arg in ordered_args if arg is not None]
        if len(concrete_args) == len(ordered_args):
            fast_path = self._compile_builtin_fast_path(func_name, concrete_args)
            if fast_path is not None:
                return fast_path

        func = self._compile_expr(expr.function)
        args = ", ".join(
            "_GENO_MISSING" if arg is None else self._compile_expr(arg.value)
            for arg in ordered_args
        )

        return f"{func}({args})"

    def _compile_builtin_fast_path(
        self, func_name: str | None, call_args: list[CallArg]
    ) -> str | None:
        """Inline hot builtin calls when types are statically known."""
        if func_name is None:
            return None

        if func_name == LENGTH_FAST_PATH_BUILTIN and len(call_args) == 1:
            value_expr = call_args[0].value
            if has_len_fast_path(value_expr):
                # len() of a materialized List/String/Array is bounded by
                # process memory, far below the JavaScript safe-integer
                # range, so that part of _require_safe_js_int can never
                # fire. The integer-bits check stays (inline) because
                # tightened _GENO_MAX_INTEGER_BITS limits must still reject
                # length results.
                value = self._compile_expr(value_expr)
                if self._in_comprehension_iterable:
                    return f"_require_int_bit_limit(len({value}))"
                t = self._fresh_temp()
                return (
                    f"({t} if ({t} := len({value})).bit_length()"
                    f" <= _MAX_INTEGER_BITS else _int_oob({t}))"
                )

        if func_name == "bit_or" and len(call_args) == 2:
            a_expr = call_args[0].value
            b_expr = call_args[1].value
            if is_int_type(a_expr) and is_int_type(b_expr):
                # The compiled bit_or helper is a bare `a | b` (| cannot
                # increase the larger operand's bit length), so inlining the
                # operator is exactly equivalent minus the call.
                a = self._compile_expr(a_expr)
                b = self._compile_expr(b_expr)
                return f"(({a}) | ({b}))"

        if func_name == STRING_CHAR_AT_FAST_PATH_BUILTIN and len(call_args) == 2:
            text_expr = call_args[0].value
            index_expr = call_args[1].value
            if is_string_type(text_expr) and is_int_type(index_expr):
                all_simple = is_simple_fast_path_expr(
                    text_expr
                ) and is_simple_fast_path_expr(index_expr)
                if not all_simple and self._in_comprehension_iterable:
                    # The non-simple form needs assignment expressions, which
                    # are illegal in this position; use the helper call.
                    return None
                text = self._compile_expr(text_expr)
                index = self._compile_expr(index_expr)
                if all_simple:
                    return f'({text}[{index}] if 0 <= {index} < len({text}) else "")'
                text_temp = self._fresh_temp()
                index_temp = self._fresh_temp()
                return (
                    f"(((({text_temp} := {text}) or True)"
                    f" and 0 <= ({index_temp} := {index}) < len({text_temp})"
                    f' and {text_temp}[{index_temp}]) or "")'
                )

        if func_name in SUBSTRING_FAST_PATH_BUILTINS and len(call_args) == 3:
            text_expr = call_args[0].value
            start_expr = call_args[1].value
            stop_expr = call_args[2].value
            if (
                is_string_type(text_expr)
                and is_int_type(start_expr)
                and is_int_type(stop_expr)
            ):
                all_simple = (
                    is_simple_fast_path_expr(text_expr)
                    and is_simple_fast_path_expr(start_expr)
                    and is_simple_fast_path_expr(stop_expr)
                )
                if not all_simple and self._in_comprehension_iterable:
                    return None
                text = self._compile_expr(text_expr)
                if (
                    type(start_expr) is IntegerLiteral
                    and type(stop_expr) is IntegerLiteral
                    and start_expr.value >= 0
                    and stop_expr.value >= 0
                    and start_expr.value.bit_length() <= _RAW_INT_LITERAL_MAX_BITS
                    and stop_expr.value.bit_length() <= _RAW_INT_LITERAL_MAX_BITS
                ):
                    # Python slicing clamps non-negative bounds natively, so
                    # the max/min dance is redundant for literal bounds.
                    return f"{text}[{start_expr.value}:{stop_expr.value}]"
                start = self._compile_expr(start_expr)
                stop = self._compile_expr(stop_expr)
                if all_simple:
                    return f"{text}[max(0, {start}):min(len({text}), {stop})]"
                text_temp = self._fresh_temp()
                start_temp = self._fresh_temp()
                stop_temp = self._fresh_temp()
                return (
                    f"(((({text_temp} := {text}) or True)"
                    f" and (({start_temp} := {start}) or True)"
                    f" and (({stop_temp} := {stop}) or True)"
                    f" and {text_temp}[max(0, {start_temp}):min(len({text_temp}), {stop_temp})])"
                    f' or "")'
                )

        if func_name in STRING_AFFIX_FAST_PATH_BUILTINS and len(call_args) == 2:
            text_expr = call_args[0].value
            affix_expr = call_args[1].value
            if is_string_type(text_expr) and is_string_type(affix_expr):
                all_simple = is_simple_fast_path_expr(
                    text_expr
                ) and is_simple_fast_path_expr(affix_expr)
                if not all_simple and self._in_comprehension_iterable:
                    return None
                text = self._compile_expr(text_expr)
                affix = self._compile_expr(affix_expr)
                method = "startswith" if func_name == "starts_with" else "endswith"
                if all_simple:
                    return f"{text}.{method}({affix})"
                text_temp = self._fresh_temp()
                affix_temp = self._fresh_temp()
                return (
                    f"(((({text_temp} := {text}) or True)"
                    f" and (({affix_temp} := {affix}) or True)"
                    f" and {text_temp}.{method}({affix_temp})))"
                )

        if func_name == APPEND_FAST_PATH_BUILTIN and len(call_args) == 2:
            list_expr = call_args[0].value
            item_expr = call_args[1].value
            if is_list_type(list_expr):
                all_simple = is_simple_fast_path_expr(
                    list_expr
                ) and is_simple_fast_path_expr(item_expr)
                if not all_simple and self._in_comprehension_iterable:
                    return None
                list_value = self._compile_expr(list_expr)
                item_value = self._compile_expr(item_expr)
                if all_simple:
                    return (
                        f"(({list_value} + [{item_value}])"
                        f" if len({list_value}) + 1 <= _MAX_COLLECTION_SIZE"
                        f" else _list_size_exceeded(len({list_value}) + 1))"
                    )
                list_temp = self._fresh_temp()
                item_temp = self._fresh_temp()
                return (
                    f"(((({list_temp} := {list_value}) or True)"
                    f" and (({item_temp} := {item_value}) or True)"
                    f" and len({list_temp}) + 1 <= _MAX_COLLECTION_SIZE"
                    f" and ({list_temp} + [{item_temp}]))"
                    f" or _list_size_exceeded(len({list_temp}) + 1))"
                )

        return None

    def _reorder_args_for_compile(
        self, call_args: list[CallArg], param_names: list[str]
    ) -> list[CallArg]:
        """Reorder call arguments to match parameter order for compilation."""
        result: list[CallArg | None] = [None] * len(param_names)
        used_positions: set[int] = set()
        positional_index = 0

        for arg in call_args:
            if arg.name is not None:
                # Named argument
                if arg.name in param_names:
                    pos = param_names.index(arg.name)
                    result[pos] = arg
                    used_positions.add(pos)
                else:
                    raise ValueError(f"Unknown parameter name: {arg.name}")
            else:
                # Positional argument
                while positional_index in used_positions:
                    positional_index += 1
                if positional_index < len(param_names):
                    result[positional_index] = arg
                    used_positions.add(positional_index)
                    positional_index += 1

        # Return only non-None args in order
        return [arg for arg in result if arg is not None]

    def _compile_pipeline(self, expr: Pipeline) -> str:
        """Compile a pipeline expression."""
        current = self._compile_expr(expr.initial)

        for stage in expr.stages:
            current_temp = self._fresh_temp()
            func = self._compile_expr(stage.function)

            # Build arguments, handling placeholders
            args = []
            placeholder_found = False

            for arg_expr in stage.arguments:
                if isinstance(arg_expr, PlaceholderExpr):
                    args.append(current_temp)
                    placeholder_found = True
                else:
                    args.append(self._compile_expr(arg_expr))

            if not placeholder_found:
                args = [current_temp] + args

            arg_str = ", ".join(args)
            current = f"(lambda {current_temp}: {func}({arg_str}))({current})"

        return current

    def _compile_lambda(self, expr: LambdaExpr) -> str:
        """Compile a lambda expression."""
        captured_loop_vars = self._captured_loop_vars_in_lambda(expr)
        if captured_loop_vars:
            capture_names = {name: self._fresh_temp() for name in captured_loop_vars}
            factory_name = self._fresh_temp()
            capture_params = ", ".join(
                f"{alias}={self._compiled_identifier_name(name)}"
                for name, alias in capture_names.items()
            )
            self._writeln(f"def {factory_name}({capture_params}):")
            self._indent()
            with self._with_name_overrides(capture_names):
                lambda_value = self._compile_lambda_direct(expr)
                self._writeln(f"return {lambda_value}")
            self._dedent()
            return f"{factory_name}()"

        return self._compile_lambda_direct(expr)

    def _compile_lambda_direct(self, expr: LambdaExpr) -> str:
        """Compile a lambda without adding loop-capture wrappers."""
        params = ", ".join(self._mangle_name(p.name) for p in expr.params)

        if expr.block_body is not None:
            func_name = self._fresh_temp()
            self._writeln(f"def {func_name}({params}):")
            self._indent()
            with self._with_shadowed_bindings([p.name for p in expr.params]):
                if not expr.block_body:
                    self._writeln("pass")
                else:
                    for stmt in expr.block_body:
                        self._compile_statement(stmt)
            self._dedent()
            return cast(str, func_name)
        else:
            assert expr.body is not None
            with self._with_shadowed_bindings([p.name for p in expr.params]):
                body = self._compile_expr(expr.body)
            return f"(lambda {params}: {body})"

    def _compile_constructor_call(self, expr: ConstructorCall) -> str:
        """Compile a constructor call."""
        if expr.constructor == "None":
            return "None_"

        args = ", ".join(self._compile_expr(arg) for arg in expr.arguments)
        return f"{expr.constructor}({args})"

    def _compile_fstring_expr(self, expr: FStringExpr) -> str:
        """Compile an f-string expression to a Python f-string."""
        import re as _re

        segments: list[str] = []
        for part in expr.parts:
            if isinstance(part, str):
                escaped = (
                    part.replace("\\", "\\\\")
                    .replace('"', '\\"')
                    .replace("\n", "\\n")
                    .replace("\r", "\\r")
                    .replace("\t", "\\t")
                    .replace("\0", "\\0")
                )
                escaped = _re.sub(
                    r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]",
                    lambda m: f"\\x{ord(m.group()):02x}",
                    escaped,
                )
                escaped = escaped.replace("{", "{{").replace("}", "}}")
                segments.append(escaped)
            else:
                compiled = self._compile_expr(part)
                segments.append("{" + f"_geno_format({compiled})" + "}")
        return '_check_collection_size(f"' + "".join(segments) + '")'

    # ``_compile_with_expr`` lives on ``BaseCompiler``; Python's
    # emission goes through ``_with_expr_emit`` below.  #622 slice.

    def _with_expr_emit(self, target: str, updates: list[tuple[str, str]]) -> str:
        updates_str = ", ".join(f"{name}={value}" for name, value in updates)
        return f"_dataclasses_replace({target}, {updates_str})"

    def _compile_match_expr(self, expr: MatchExpr) -> str:
        """Compile a match expression (inline)."""
        # For inline match, we use a helper function
        scrutinee = self._compile_expr(expr.scrutinee)
        match_var = self._fresh_temp()

        # Build a conditional expression chain
        result_parts = []
        for arm in expr.arms:
            cond, bindings = self._compile_pattern_condition(arm.pattern, match_var)

            if len(arm.body) != 1 or not isinstance(arm.body[0], ReturnStatement):
                raise CompileError(
                    "MatchExpr arm body must be exactly one return statement"
                )

            return_stmt = arm.body[0]
            body_expr = self._compile_expr(return_stmt.value)
            # Wrap with nested lambdas for all bindings (in reverse order)
            for var_name, var_expr in reversed(bindings):
                body_expr = f"(lambda {var_name}: {body_expr})({var_expr})"

            if arm.guard is not None:
                guard_code = self._compile_expr(arm.guard)
                if bindings:
                    # Need bindings available for guard evaluation too
                    guard_with_bindings = guard_code
                    for var_name, var_expr in reversed(bindings):
                        guard_with_bindings = (
                            f"(lambda {var_name}: {guard_with_bindings})({var_expr})"
                        )
                    cond = f"{cond} and {guard_with_bindings}"
                else:
                    cond = f"{cond} and {guard_code}"

            result_parts.append((cond, body_expr))

        # Build nested ternary. The fallback must be a valid Python expression
        # that raises at runtime — a trailing `# no match` comment turns the
        # surrounding ternary into a truncated line and produces a SyntaxError
        # (see issue #657).
        result = '_geno_throw("Non-exhaustive match expression")'
        for cond, body in reversed(result_parts):
            result = f"({body} if {cond} else {result})"

        match_result = f"(lambda {match_var}: {result})({scrutinee})"
        return self._promote_expr_to_expected_float(
            match_result, getattr(expr, "_resolved_type", None)
        )


# =============================================================================
# Convenience Functions
# =============================================================================


def compile_to_python(
    source: str,
    filename: str = "<stdin>",
    typecheck: bool = True,
    target_profile: Optional["TargetProfile"] = None,
) -> str:
    """
    Compile Geno source code to Python.

    Args:
        source: Geno source code
        filename: Filename for error messages
        typecheck: Whether to run typechecker (default True)
        target_profile: Optional target profile for availability checks

    Returns:
        Python source code
    """
    from .lexer import Lexer
    from .parser import Parser
    from .target_profile import TargetProfile
    from .typechecker import TypeChecker

    lexer = Lexer(source, filename)
    tokens = lexer.tokenize()

    parser = Parser(tokens)
    program = parser.parse_program()

    # Type check by default (matches CLI behavior)
    if typecheck:
        profile = target_profile or TargetProfile.load("python-cli")
        checker = TypeChecker(target_profile=profile)
        checker.check_program(program)

    compiler = Compiler()
    return compiler.compile(program)


def _drop_source_prefix(source: str, lineno: int, col_offset: int) -> str:
    """Return ``source`` after the AST node ending at ``lineno``/``col_offset``."""
    if lineno <= 0:
        return source

    lines = source.splitlines(keepends=True)
    if lineno > len(lines):
        return ""

    line = lines[lineno - 1]
    remainder = line[col_offset:]
    while remainder.startswith((" ", "\t", ";")):
        remainder = remainder[1:]
    lines[lineno - 1] = remainder
    return "".join(lines[lineno - 1 :])


# Line windows probed before falling back to a full parse in
# _strip_leading_docstring_and_imports. The leading docstring/import block
# of the ~4k-line runtime prelude sits in its first few dozen lines, but a
# full ast.parse of the whole text cost ~20-30 ms on every CLI invocation.
_STRIP_PROBE_LINE_WINDOWS = (160, 800)


def _line_window(source: str, line_count: int) -> str | None:
    """First *line_count* lines of *source*, or None if it has no more."""
    end = -1
    for _ in range(line_count):
        end = source.find("\n", end + 1)
        if end == -1:
            return None
    return source[: end + 1]


def _has_top_level_non_import(tree: ast.Module) -> bool:
    body = tree.body
    start = 0
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        start = 1
    return any(
        not isinstance(node, (ast.Import, ast.ImportFrom)) for node in body[start:]
    )


def _strip_using_tree(source: str, tree: ast.Module) -> str:
    """Strip the leading docstring/import block located by *tree*."""
    body = list(tree.body)
    skipped_docstring = False
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
        skipped_docstring = True

    first_body_idx = 0
    for idx, node in enumerate(body):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            first_body_idx = idx
            break
    else:
        return ""

    skipped = body[:first_body_idx]
    if skipped:
        last_skipped = skipped[-1]
    elif skipped_docstring:
        last_skipped = tree.body[0]
    else:
        return source

    end_lineno = getattr(last_skipped, "end_lineno", None)
    end_col_offset = getattr(last_skipped, "end_col_offset", None)
    if end_lineno is None or end_col_offset is None:
        return source
    return _drop_source_prefix(source, end_lineno, end_col_offset)


def _strip_leading_docstring_and_imports(source: str) -> str:
    """Drop leading module docstring/import nodes without line-based scanning.

    The result depends only on the leading docstring/import nodes and the
    existence of a first non-import statement, so a small line window is
    parsed first: a window that parses and contains a top-level non-import
    statement fully determines the answer. Windows that cut a statement in
    half fail to parse (parenthesised multi-line imports included) and
    widen; anything inconclusive falls back to the exact full parse.
    """
    for window in _STRIP_PROBE_LINE_WINDOWS:
        prefix = _line_window(source, window)
        if prefix is None:
            break  # source fits inside the window; the full parse is cheap
        try:
            tree = ast.parse(prefix)
        except SyntaxError:
            continue
        if _has_top_level_non_import(tree):
            return _strip_using_tree(source, tree)

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    return _strip_using_tree(source, tree)


def _compiled_runtime_sections(python_code: str) -> tuple[str, str] | None:
    """Return the compiler-owned runtime prelude and generated body, if present."""
    if python_code.startswith(RUNTIME_PRELUDE):
        return RUNTIME_PRELUDE, python_code[len(RUNTIME_PRELUDE) :]
    return None


@lru_cache(maxsize=4)
def _stripped_prelude_for(prelude: str) -> str:
    """Strip a prelude's docstring/import prefix (memoized by text).

    The cache exists because the strip requires an ast.parse of the ~4k-line
    runtime prelude, which CLI runs would otherwise repeat several times per
    invocation. Keying by the text keeps it correct if RUNTIME_PRELUDE is
    replaced (tests monkeypatch it).
    """
    return _strip_leading_docstring_and_imports(prelude)


def _stripped_runtime_prelude() -> str:
    """Return the compiler-owned runtime prelude as seen by the process worker."""
    return _stripped_prelude_for(RUNTIME_PRELUDE)


@lru_cache(maxsize=4)
def _line_count_for(text: str) -> int:
    """Line count of *text* (memoized for the stripped runtime prelude)."""
    return len(text.splitlines())


def _strip_runtime_prelude_imports(python_code: str) -> str:
    """Drop the generated prelude's leading docstring/import block.

    Sandboxed compiled execution pre-injects the runtime symbols the prelude
    needs. Keeping the generated imports would route back through the sandbox's
    import gate and break otherwise safe compiled execution.
    """
    sections = _compiled_runtime_sections(python_code)
    if sections is None:
        return _strip_leading_docstring_and_imports(python_code)

    _prelude, body = sections
    return _stripped_runtime_prelude() + body


def _trusted_runtime_prelude_line_count(python_code: str) -> int:
    """Return the line count of the trusted runtime-support prefix.

    ``python_code`` should already have had leading runtime imports stripped so
    its line numbers match the process worker's AST parse.  If the code does
    not structurally start with the compiler-owned runtime prelude, fail closed
    by trusting no prefix.
    """
    stripped_prelude = _stripped_runtime_prelude()
    if stripped_prelude and python_code.startswith(stripped_prelude):
        return _line_count_for(stripped_prelude)
    return 0


def _compiled_runtime_capability_assignment(
    capabilities: Collection[str] | None,
) -> str:
    """Return code that grants explicit caps to embedded compiled runtime code."""
    caps = sorted(set(capabilities or ()))
    if caps:
        values = ", ".join(repr(cap) for cap in caps)
        if len(caps) == 1:
            values += ","
        caps_literal = f"{{{values}}}"
    else:
        caps_literal = "set()"
    return f"\n\n_GENO_CAPS = {caps_literal}\n"


def _insert_compiled_runtime_capability_assignment(
    python_code: str,
    capabilities: Collection[str] | None,
) -> str:
    """Install explicit runtime caps before generated compiled program code."""
    assignment = _compiled_runtime_capability_assignment(capabilities)
    sections = _compiled_runtime_sections(python_code)
    if sections is not None:
        prelude, body = sections
        return prelude + assignment + body

    stripped_prelude = _stripped_runtime_prelude()
    if stripped_prelude and python_code.startswith(stripped_prelude):
        return stripped_prelude + assignment + python_code[len(stripped_prelude) :]
    return assignment + python_code


def _compiled_main_result_capture(
    is_async: bool,
    *,
    catch_name_error: bool = False,
) -> str:
    call = "_geno_run_async(main())" if is_async else "main()"
    assignment = f"__result__ = {call}"
    if catch_name_error:
        return f"\n\ntry:\n    {assignment}\nexcept NameError:\n    pass\n"
    return f"\n\n{assignment}\n"


def compile_and_exec(
    source: str,
    filename: str = "<stdin>",
    typecheck: bool = True,
    sandboxed: bool = True,
    timeout: float | None = 30.0,
    capabilities: Collection[str] | None = None,
) -> dict:
    """
    Compile Geno source to Python and execute it.

    This is a build-time / trusted-caller convenience.  For untrusted code,
    use ``geno.api.run()`` which enforces cooperative timeouts via the
    in-process interpreter, or the hosted server which adds a wall-clock
    budget via a process pool.

    Args:
        source: Geno source code
        filename: Filename for error messages
        typecheck: Whether to run typechecker (default True)
        sandboxed: Whether to run in sandbox mode (default True)
        timeout: Execution timeout in seconds (default 30).  When set and
                 *sandboxed* is True, execution is routed through
                 :class:`~geno.sandbox.ProcessSandbox` for hard timeout
                 enforcement.  The return dict will only contain
                 ``__result__`` and ``__output__`` (not full globals).
                 Pass ``None`` to exec in-process with no timeout
                 (returns full globals dict).
        capabilities: Capability names to grant to compiled runtime helpers.
                      ``None`` means no grants.

    Returns:
        Dictionary of global variables from execution.
        When *timeout* is not None, only ``__result__`` and ``__output__``
        are populated.

    Note:
        When sandboxed=True, dangerous operations (eval, exec, open, etc.)
        are blocked. The sandbox is enforced BEFORE execution to prevent
        escape attempts during initialization.

        With ``sandboxed=True`` and a non-None ``timeout``, if the sandbox
        worker's result channel is lost (it exits 0 without emitting a
        parseable result), ``geno.sandbox.SandboxError`` propagates out of
        this function rather than the usual ``RuntimeError``. ``SandboxError``
        does not subclass ``RuntimeError``, so callers that catch execution
        failures should catch both (or ``SandboxError``'s common base).
    """
    python_code = compile_to_python(source, filename, typecheck=typecheck)

    if sandboxed:
        if timeout is not None:
            # Use ProcessSandbox for hard timeout enforcement (can kill
            # runaway processes).  The worker pre-injects the runtime
            # prelude symbols, so strip the generated import block first.
            from .sandbox import ProcessSandbox, ProcessSandboxConfig

            exec_code = _strip_runtime_prelude_imports(python_code)
            trusted_prelude_line_count = _trusted_runtime_prelude_line_count(exec_code)
            exec_code = _insert_compiled_runtime_capability_assignment(
                exec_code, capabilities
            )
            # Replace the __name__ guard with a direct __result__
            # assignment so the process sandbox captures the return value.
            _MAIN_GUARD = (
                "\n\nif __name__ == '__main__':\n"
                "    result = main()\n"
                "    if result is not None:\n"
                "        print(result)\n"
            )
            _ASYNC_MAIN_GUARD = (
                "\n\nif __name__ == '__main__':\n"
                "    import asyncio\n"
                "    result = asyncio.run(main())\n"
                "    if result is not None:\n"
                "        print(result)\n"
            )
            if _ASYNC_MAIN_GUARD in exec_code:
                exec_code = exec_code.replace(
                    _ASYNC_MAIN_GUARD,
                    _compiled_main_result_capture(is_async=True),
                )
            elif _MAIN_GUARD in exec_code:
                exec_code = exec_code.replace(
                    _MAIN_GUARD,
                    _compiled_main_result_capture(is_async=False),
                )

            process_config = ProcessSandboxConfig(
                timeout=timeout,
                strict=False,
                compiled_runtime_prelude=True,
                trusted_prelude_line_count=trusted_prelude_line_count,
            )
            sandbox = ProcessSandbox(process_config)
            result, output, error = sandbox.execute(exec_code)

            if error is not None:
                raise RuntimeError(error)

            return {"__result__": result, "__output__": output}
        else:
            # Trusted-caller path: exec in-process with no timeout.
            # Returns the full globals dict so callers can inspect
            # compiled functions by name.
            import codecs as _runtime_codecs
            import math
            import posixpath as _runtime_posixpath
            import random as _runtime_random
            import re as _re
            import time as _runtime_time
            from copy import deepcopy
            from dataclasses import dataclass
            from dataclasses import fields as _dataclasses_fields
            from dataclasses import replace as _dataclasses_replace
            from typing import Any, Callable, Generic, TypeVar, Union
            from typing import Optional as Opt

            from .sandbox import SandboxConfig, create_safe_globals

            output_buffer: list[str] = []
            config = SandboxConfig(timeout=None, allow_print=True)
            globals_dict = create_safe_globals(config, output_buffer)

            # Pre-inject modules required by the runtime prelude so it
            # doesn't need __import__ (which is blocked by the sandbox)
            # Inject __build_class__ for the runtime prelude's @dataclass
            # class definitions.  This is NOT in SAFE_BUILTINS (to prevent
            # user-authored class definitions in the thread sandbox) but is
            # needed here for compiler-generated code.
            _build_class = (
                __builtins__["__build_class__"]
                if isinstance(__builtins__, dict)
                else __builtins__.__build_class__
            )
            globals_dict["__builtins__"]["__build_class__"] = _build_class

            globals_dict.update(
                {
                    "dataclass": dataclass,
                    "Any": Any,
                    "Callable": Callable,
                    "TypeVar": TypeVar,
                    "Generic": Generic,
                    "Optional": Opt,
                    "Union": Union,
                    "deepcopy": deepcopy,
                    "math": math,
                    "_runtime_codecs": _runtime_codecs,
                    "_runtime_posixpath": _runtime_posixpath,
                    "_runtime_random": _runtime_random,
                    "_runtime_time": _runtime_time,
                    "cmp_to_key": __import__("functools").cmp_to_key,
                    "_dataclasses_fields": _dataclasses_fields,
                    "_dataclasses_replace": _dataclasses_replace,
                    "_re": _re,
                    # Configurable limits picked up by the runtime prelude
                    "_GENO_MAX_COLLECTION_SIZE": config.max_collection_size,
                }
            )

            python_code = _strip_runtime_prelude_imports(python_code)
            python_code = _insert_compiled_runtime_capability_assignment(
                python_code, capabilities
            )

            # Python 3.9's dataclasses._is_type does
            #   sys.modules.get(cls.__module__).__dict__
            # Classes created via exec() get __module__ = '__sandbox__' from
            # the globals dict, but that name is not in sys.modules.
            # Register a fake module so the lookup doesn't fail on None.
            import sys as _sys
            import types as _types_mod

            _sandbox_mod_name = globals_dict.get("__name__", "__sandbox__")
            _fake_mod = _types_mod.ModuleType(_sandbox_mod_name)
            _fake_mod.__dict__.update(globals_dict)
            _sys.modules[_sandbox_mod_name] = _fake_mod
            try:
                exec(python_code, globals_dict)  # nosec B102
                globals_dict["_GENO_CAPS"] = set(capabilities or ())
                return cast(dict[Any, Any], globals_dict)
            finally:
                _sys.modules.pop(_sandbox_mod_name, None)
    else:
        # Unsafe mode - no sandbox
        unsafe_globals: dict = {}
        python_code = _insert_compiled_runtime_capability_assignment(
            python_code, capabilities
        )
        exec(python_code, unsafe_globals)  # nosec B102
        unsafe_globals["_GENO_CAPS"] = set(capabilities or ())
        return unsafe_globals
