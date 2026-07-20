"""
Geno Type Checker
=====================

Static type checking for Geno programs.
Verifies type annotations and catches type errors before runtime.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator, Mapping, Union, cast

if TYPE_CHECKING:
    from .target_profile import TargetProfile

from .ast_nodes import (  # Types; Expressions; Patterns; Statements; Definitions; Program; Visitor
    AssertStatement,
    AssignStatement,
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
    MatchArm,
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
    TestBlock,
    ThrowExpression,
    TraitDef,
    TryStatement,
    TupleDestructureStatement,
    TupleExpr,
    TypeAlias,
    TypeAnnotation,
    TypeDef,
    TypedHole,
    TypeIdentifier,
    UnaryOp,
    VariablePattern,
    VarStatement,
    WhileStatement,
    WildcardPattern,
    WithExpr,
)
from .builtin_registry import VALID_EFFECTS, source_builtin_specs
from .diagnostics import ErrorCode
from .exhaustiveness import ExhaustivenessMixin
from .tokens import SourceLocation
from .typechecker_calls import resolve_call_parameter_info
from .types import (
    AnyType,
    ArrayType,
    AsyncType,
    BoolType,
    FloatType,
    FuncType,
    IntType,
    ListType,
    MapType,
    ModuleType,
    MutableMapType,
    NeverType,
    OptionType,
    ResultType,
    SetType,
    StringType,
    TupleType,
    Type,
    TypeDefInfo,
    TypeEnv,
    TypeError,
    TypeErrors,
    TypeVar,
    UnitType,
    UserType,
    VecType,
    any_child,
    map_type,
    type_children,
)

_ANY_TYPE = AnyType()
_INT_TYPE = IntType()
_FLOAT_TYPE = FloatType()
_BOOL_TYPE = BoolType()
_STRING_TYPE = StringType()
_UNIT_TYPE = UnitType()
_NUMERIC_TYPEVAR_NAMES = frozenset({"Num"})
_BUILTIN_SIMPLE_TYPES = {
    "Int": _INT_TYPE,
    "Float": _FLOAT_TYPE,
    "Bool": _BOOL_TYPE,
    "String": _STRING_TYPE,
    "Unit": _UNIT_TYPE,
}
_PARAMETERIZED_BUILTIN_ARITY = {
    "List": 1,
    "Array": 1,
    "Option": 1,
    "Result": 2,
    "Map": 2,
    "MutableMap": 2,
    "Vec": 1,
    "Set": 1,
    "Async": 1,
}

_STDLIB_FORWARDER_BUILTINS: dict[str, dict[str, str]] = {
    "List": {
        "zip": "list_zip",
        "enumerate": "list_enumerate",
        "flatten": "list_flatten",
        "chunk": "list_chunk",
        "take": "list_take",
        "drop": "list_drop",
        "find": "list_find",
        "find_index": "list_find_index",
        "all": "list_all",
        "any": "list_any",
        "fold_right": "list_fold_right",
        "intersperse": "list_intersperse",
        "group_by": "list_group_by",
        "length": "list_length",
        "map": "list_map",
        "filter": "list_filter",
    },
    "Map": {
        "from_list": "map_from_list",
        "merge": "map_merge",
        "filter_map": "map_filter_map",
        "map_values": "map_map_values",
        "entries": "map_entries",
        "from_entries": "map_from_entries",
        "get": "map_get",
        "insert": "map_insert",
    },
    "Option": {
        "map": "option_map",
        "and_then": "option_and_then",
        "unwrap_or": "option_unwrap_or",
        "is_some": "option_is_some",
        "is_none": "option_is_none",
        "flatten": "option_flatten",
        "to_result": "option_to_result",
    },
    "Result": {
        "map": "result_map",
        "map_err": "result_map_err",
        "and_then": "result_and_then",
        "unwrap_or": "result_unwrap_or",
        "is_ok": "result_is_ok",
        "is_err": "result_is_err",
        "to_option": "result_to_option",
    },
    "Math": {
        "abs": "math_abs",
        "min": "math_min",
        "max": "math_max",
        "clamp": "math_clamp",
    },
}

# =============================================================================
# Project import summaries
# =============================================================================


@dataclass(frozen=True)
class _ModuleImportSummary:
    """Compact import-facing view of a checked module."""

    imports: tuple[ImportStatement, ...]
    type_aliases: dict[str, tuple[list[str], TypeAnnotation]]
    type_defs: dict[str, TypeDefInfo]
    functions: dict[str, FuncType]
    func_param_names: dict[str, list[str]]
    func_default_counts: dict[str, int]
    module_symbols: dict[str, Type]
    module_param_names: dict[str, list[str]]
    module_default_counts: dict[str, int]


@dataclass(frozen=True)
class _BuiltinCheckerState:
    """Reusable builtin state for newly constructed type checkers."""

    type_defs: dict[str, TypeDefInfo]
    constructor_to_type: dict[str, str]
    builtin_types: dict[str, tuple[FuncType, tuple[str, ...]]]
    global_env_bindings: dict[str, Type]
    func_param_names: dict[str, tuple[str, ...]]


_DEFAULT_BUILTIN_CHECKER_STATE: _BuiltinCheckerState | None = None


def _clone_type_def_info(info: TypeDefInfo) -> TypeDefInfo:
    """Return an isolated copy of a TypeDefInfo entry."""
    return TypeDefInfo(
        name=info.name,
        type_params=list(info.type_params),
        variants={
            variant_name: list(fields) for variant_name, fields in info.variants.items()
        },
        invariant_params=frozenset(info.invariant_params),
    )


# =============================================================================
# Suggestion helpers
# =============================================================================


def _levenshtein(a: str, b: str) -> int:
    """Compute the Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _suggest_name(name: str, candidates: Iterable[str], max_dist: int = 2) -> str:
    """Return ' Did you mean: X?' suffix if a close match exists, else ''."""
    best, best_dist = None, max_dist + 1
    for c in candidates:
        d = _levenshtein(name, c)
        if d < best_dist:
            best, best_dist = c, d
    if best is not None and best_dist <= max_dist:
        return f" Did you mean '{best}'?"
    return ""


# =============================================================================
# Type Checker
# =============================================================================


class TypeChecker(ExhaustivenessMixin):
    """
    Type checker for Geno programs.

    Performs static type checking to verify that:
    - All expressions have valid types
    - Type annotations match actual types
    - Variables are defined before use
    - Assignments respect mutability

    Example:
        checker = TypeChecker()
        checker.check_program(program)  # Raises TypeError on failure
    """

    def __init__(self, target_profile: TargetProfile | None = None) -> None:
        self.global_env = TypeEnv()
        self.type_defs: dict[str, TypeDefInfo] = {}
        self.type_aliases: dict[str, tuple[list[str], TypeAnnotation]] = {}
        self.func_param_names: dict[
            str, list[str]
        ] = {}  # Store param names for named arg support
        self.func_default_counts: dict[
            str, int
        ] = {}  # Number of default params per function
        self.current_return_type: Type | None = None
        self._in_async_function: bool = False
        self._in_main_function: bool = False
        self._lambda_return_types: list[Type] | None = None
        self.errors: list[TypeError] = []
        self._user_defined_names: set[str] = set()
        self._constructor_to_type: dict[str, str] = {}
        self._loop_depth: int = 0
        self._is_app_mode: bool = False
        self._resolving_aliases: set[str] = set()
        self._resolved_type_cache: dict[tuple[int, tuple[str, ...]], Type] = {}
        self._declared_user_types: list[dict[str, tuple[str, ...]]] = []
        self._trait_self_type_depth = 0
        self._target_profile = target_profile
        self._target_rejected: dict[str, str] = {}
        self._fresh_tv_counter: int = 0

        # Module namespace for qualified imports: alias/name → {symbol: Type}
        self._module_exports: dict[str, dict[str, Type]] = {}
        # Module param names for qualified calls: alias/name → {func: [param_names]}
        self._module_param_names: dict[str, dict[str, list[str]]] = {}
        self._module_default_counts: dict[str, dict[str, int]] = {}

        # Trait support
        self.trait_defs: dict[str, TraitDef] = {}
        # (trait_name, type_name) -> {method_name: FuncType}
        self.impl_registry: dict[tuple[str, str], dict[str, FuncType]] = {}
        # method_name -> [(trait_name, TraitDef)]
        self.trait_methods: dict[str, list[tuple[str, TraitDef]]] = {}

        # Initialize built-in types
        self._init_builtins()

    def _init_builtins(self) -> None:
        """Initialize built-in types and functions."""
        global _DEFAULT_BUILTIN_CHECKER_STATE

        cached = _DEFAULT_BUILTIN_CHECKER_STATE
        if cached is not None:
            self.type_defs = {
                name: _clone_type_def_info(info)
                for name, info in cached.type_defs.items()
            }
            self._constructor_to_type = dict(cached.constructor_to_type)
            self.builtin_types = {
                name: (func_type, list(param_names))
                for name, (func_type, param_names) in cached.builtin_types.items()
            }
            self._target_rejected = {}

            if self._target_profile is None:
                self.global_env.bindings.update(cached.global_env_bindings)
                self.func_param_names.update(
                    {
                        name: list(param_names)
                        for name, param_names in cached.func_param_names.items()
                    }
                )
                return

            for name, type_ in cached.global_env_bindings.items():
                if not self._target_profile.is_available(name):
                    self._target_rejected[name] = (
                        self._target_profile.rejection_message(name)
                    )
                    continue
                self.global_env.bindings[name] = type_
                self.func_param_names[name] = list(cached.func_param_names[name])
            return

        # Built-in type constructors
        self.type_defs["Option"] = TypeDefInfo(
            name="Option",
            type_params=["T"],
            variants={
                "Some": [("value", TypeVar("T"))],
                "None": [],
            },
        )
        self.type_defs["Result"] = TypeDefInfo(
            name="Result",
            type_params=["T", "E"],
            variants={
                "Ok": [("value", TypeVar("T"))],
                "Err": [("error", TypeVar("E"))],
            },
        )
        self.type_defs["JsonValue"] = TypeDefInfo(
            name="JsonValue",
            type_params=[],
            variants={
                "JsonString": [("value", StringType())],
                "JsonInt": [("value", IntType())],
                "JsonFloat": [("value", FloatType())],
                "JsonBool": [("value", BoolType())],
                "JsonNull": [],
                "JsonArray": [("items", ListType(UserType("JsonValue")))],
                "JsonObject": [
                    (
                        "entries",
                        ListType(TupleType((StringType(), UserType("JsonValue")))),
                    )
                ],
            },
        )

        self.type_defs["HttpResponse"] = TypeDefInfo(
            name="HttpResponse",
            type_params=[],
            variants={
                "HttpResponse": [
                    ("status", IntType()),
                    ("body", StringType()),
                    ("headers", ListType(TupleType((StringType(), StringType())))),
                ],
            },
        )

        self.type_defs["HttpRequest"] = TypeDefInfo(
            name="HttpRequest",
            type_params=[],
            variants={
                "HttpRequest": [
                    ("method", StringType()),
                    ("path", StringType()),
                    ("query", StringType()),
                    ("headers", ListType(TupleType((StringType(), StringType())))),
                    ("body", StringType()),
                ],
            },
        )

        self.type_defs["ProcessResult"] = TypeDefInfo(
            name="ProcessResult",
            type_params=[],
            variants={
                "ProcessResult": [
                    ("exit_code", IntType()),
                    ("stdout", StringType()),
                    ("stderr", StringType()),
                ],
            },
        )

        self.type_defs["FileKind"] = TypeDefInfo(
            name="FileKind",
            type_params=[],
            variants={
                "FileKindFile": [],
                "FileKindDirectory": [],
                "FileKindSymlink": [],
                "FileKindOther": [],
            },
        )

        self.type_defs["FileMetadata"] = TypeDefInfo(
            name="FileMetadata",
            type_params=[],
            variants={
                "FileMetadata": [
                    ("kind", UserType("FileKind")),
                    ("size", IntType()),
                    ("modified_ms", IntType()),
                ],
            },
        )

        # Build constructor→type reverse index for built-in types
        for type_name, type_info in self.type_defs.items():
            for constructor_name in type_info.variants:
                self._constructor_to_type[constructor_name] = type_name

        # Built-in functions: source name -> FuncType
        resolved_builtin_types = {
            name: (spec.signature, list(spec.source_param_names))
            for name, spec in source_builtin_specs().items()
            if spec.signature is not None
        }

        self.builtin_types = resolved_builtin_types
        # Builtins rejected by target profile (name -> rejection message)
        self._target_rejected = {}

        for name, (type_, param_names) in resolved_builtin_types.items():
            if (
                self._target_profile is not None
                and not self._target_profile.is_available(name)
            ):
                self._target_rejected[name] = self._target_profile.rejection_message(
                    name
                )
                continue
            self.global_env.bind(name, type_)
            self.func_param_names[name] = param_names

        if self._target_profile is None and _DEFAULT_BUILTIN_CHECKER_STATE is None:
            _DEFAULT_BUILTIN_CHECKER_STATE = _BuiltinCheckerState(
                type_defs={
                    name: _clone_type_def_info(info)
                    for name, info in self.type_defs.items()
                },
                constructor_to_type=dict(self._constructor_to_type),
                builtin_types={
                    name: (func_type, tuple(param_names))
                    for name, (func_type, param_names) in resolved_builtin_types.items()
                },
                global_env_bindings=dict(self.global_env.bindings),
                func_param_names={
                    name: tuple(param_names)
                    for name, param_names in self.func_param_names.items()
                },
            )

    def _error(
        self,
        message: str,
        location: SourceLocation,
        error_code: ErrorCode | None = ErrorCode.TYPE_MISMATCH,
    ) -> TypeError:
        """Create and record a type error."""
        error = TypeError(message, location, error_code)
        self.errors.append(error)
        return error

    @contextmanager
    def _with_declared_type_defs(self, type_defs: Iterable[TypeDef]) -> Iterator[None]:
        """Temporarily expose in-scope user type names before full collection."""
        declared = {defn.name: tuple(defn.type_params) for defn in type_defs}
        if not declared:
            yield
            return

        self._declared_user_types.append(declared)
        self._resolved_type_cache.clear()
        try:
            yield
        finally:
            self._declared_user_types.pop()
            self._resolved_type_cache.clear()

    def _declared_user_type_params(self, name: str) -> tuple[str, ...] | None:
        """Return declared type parameters for a known user-defined type."""
        for declared in reversed(self._declared_user_types):
            if name in declared:
                return declared[name]
        type_info = self.type_defs.get(name)
        if type_info is not None:
            return tuple(type_info.type_params)
        return None

    @staticmethod
    def _raise_type_arity_error(
        name: str, expected: int, actual: int, location: SourceLocation
    ) -> None:
        """Raise a consistent wrong-arity error for a type annotation."""
        if actual == 0:
            raise TypeError(
                f"Type '{name}' expects {expected} type parameter(s) "
                f"but none were provided",
                location,
                ErrorCode.TYPE_WRONG_ARITY,
            )
        raise TypeError(
            f"Type '{name}' expects {expected} type parameter(s) but got {actual}",
            location,
            ErrorCode.TYPE_WRONG_ARITY,
        )

    def _validate_type_aliases(self, aliases: Iterable[TypeAlias]) -> None:
        """Resolve alias targets eagerly so invalid aliases fail even if unused."""
        for alias in aliases:
            self._resolve_type(alias.target_type, alias.type_params)

    @staticmethod
    def _type_annotation_names(type_annot: TypeAnnotation) -> set[str]:
        """Return simple type names referenced by a type annotation."""
        if isinstance(type_annot, SimpleType):
            simple_names = {type_annot.name}
            for param in type_annot.type_params:
                simple_names.update(TypeChecker._type_annotation_names(param))
            return simple_names
        if isinstance(type_annot, FunctionType):
            function_names: set[str] = set()
            for param in type_annot.param_types:
                function_names.update(TypeChecker._type_annotation_names(param))
            function_names.update(
                TypeChecker._type_annotation_names(type_annot.return_type)
            )
            return function_names
        return set()

    def _hidden_exported_type_names(
        self,
        annotations: Iterable[TypeAnnotation],
        *,
        visible_names: set[str],
        type_params: Iterable[str] = (),
        local_aliases: dict[str, TypeAlias] | None = None,
    ) -> list[str]:
        """Return non-visible type names referenced by exported annotations."""
        local_type_params = set(type_params)
        alias_map = local_aliases or {}
        hidden_names: set[str] = set()
        for annotation in annotations:
            hidden_names.update(
                self._hidden_exported_type_annotation_names(
                    annotation,
                    visible_names=visible_names,
                    type_params=local_type_params,
                    local_aliases=alias_map,
                )
            )
        return sorted(hidden_names)

    def _hidden_exported_type_annotation_names(
        self,
        type_annot: TypeAnnotation,
        *,
        visible_names: set[str],
        type_params: set[str],
        local_aliases: dict[str, TypeAlias],
        seen_aliases: frozenset[str] | None = None,
    ) -> set[str]:
        """Return hidden type names, expanding transparent local aliases."""
        seen_aliases = seen_aliases or frozenset()
        if isinstance(type_annot, SimpleType):
            hidden_names: set[str] = set()
            name = type_annot.name
            if name not in visible_names and name not in type_params:
                alias = local_aliases.get(name)
                if alias is not None and name not in seen_aliases:
                    hidden_names.update(
                        self._hidden_exported_type_annotation_names(
                            alias.target_type,
                            visible_names=visible_names,
                            type_params=set(alias.type_params),
                            local_aliases=local_aliases,
                            seen_aliases=seen_aliases | {name},
                        )
                    )
                else:
                    hidden_names.add(name)
            for param in type_annot.type_params:
                hidden_names.update(
                    self._hidden_exported_type_annotation_names(
                        param,
                        visible_names=visible_names,
                        type_params=type_params,
                        local_aliases=local_aliases,
                        seen_aliases=seen_aliases,
                    )
                )
            return hidden_names
        if isinstance(type_annot, FunctionType):
            hidden_names = set()
            for param in type_annot.param_types:
                hidden_names.update(
                    self._hidden_exported_type_annotation_names(
                        param,
                        visible_names=visible_names,
                        type_params=type_params,
                        local_aliases=local_aliases,
                        seen_aliases=seen_aliases,
                    )
                )
            hidden_names.update(
                self._hidden_exported_type_annotation_names(
                    type_annot.return_type,
                    visible_names=visible_names,
                    type_params=type_params,
                    local_aliases=local_aliases,
                    seen_aliases=seen_aliases,
                )
            )
            return hidden_names
        return set()

    def _raise_hidden_exported_type_error(
        self,
        *,
        kind: str,
        name: str,
        hidden_names: list[str],
        location: SourceLocation,
    ) -> None:
        """Raise a consistent diagnostic for exported private type exposure."""
        hidden = ", ".join(hidden_names)
        plural = "s" if len(hidden_names) != 1 else ""
        raise TypeError(
            f"Exported {kind} '{name}' references non-exported type{plural}: {hidden}",
            location,
        )

    def _validate_exported_type_surface(
        self,
        aliases: Iterable[TypeAlias],
        type_defs: Iterable[TypeDef],
        functions: Iterable[FunctionDef],
        *,
        has_exports: bool,
    ) -> None:
        """Reject exported APIs that expose non-exported module-local types."""
        if not has_exports:
            return

        alias_list = list(aliases)
        type_def_list = list(type_defs)
        function_list = list(functions)
        local_aliases = {alias.name: alias for alias in alias_list}
        current_type_names = {defn.name for defn in type_def_list} | {
            alias.name for alias in alias_list
        }
        exported_current_names = {
            defn.name
            for defn in type_def_list
            if bool(getattr(defn, "exported", False))
        } | {
            alias.name
            for alias in alias_list
            if bool(getattr(alias, "exported", False))
        }
        externally_visible_names = (
            set(self.type_defs) | set(self.type_aliases)
        ) - current_type_names
        visible_names = (
            set(_BUILTIN_SIMPLE_TYPES)
            | set(_PARAMETERIZED_BUILTIN_ARITY)
            | externally_visible_names
            | exported_current_names
        )

        for alias in alias_list:
            if not bool(getattr(alias, "exported", False)):
                continue
            hidden_names = self._hidden_exported_type_names(
                [alias.target_type],
                visible_names=visible_names,
                type_params=alias.type_params,
                local_aliases=local_aliases,
            )
            if hidden_names:
                self._raise_hidden_exported_type_error(
                    kind="type alias",
                    name=alias.name,
                    hidden_names=hidden_names,
                    location=alias.location,
                )

        for type_def in type_def_list:
            if not bool(getattr(type_def, "exported", False)):
                continue
            field_annotations = [
                field_type
                for variant in type_def.variants
                for _field_name, field_type in variant.fields
            ]
            hidden_names = self._hidden_exported_type_names(
                field_annotations,
                visible_names=visible_names,
                type_params=type_def.type_params,
                local_aliases=local_aliases,
            )
            if hidden_names:
                self._raise_hidden_exported_type_error(
                    kind="type",
                    name=type_def.name,
                    hidden_names=hidden_names,
                    location=type_def.location,
                )

        for function_def in function_list:
            if not bool(getattr(function_def, "exported", False)):
                continue
            annotations = [param.param_type for param in function_def.params] + [
                function_def.return_type
            ]
            hidden_names = self._hidden_exported_type_names(
                annotations,
                visible_names=visible_names,
                local_aliases=local_aliases,
            )
            if hidden_names:
                self._raise_hidden_exported_type_error(
                    kind="function",
                    name=function_def.name,
                    hidden_names=hidden_names,
                    location=function_def.location,
                )

    def _is_hashable_type(
        self, type_: Type, seen_user_types: frozenset[str] = frozenset()
    ) -> bool:
        """Return whether values of *type_* can be used as stable hash keys."""
        if isinstance(type_, (AnyType, NeverType, TypeVar)):
            return True
        if isinstance(type_, (IntType, FloatType, BoolType, StringType, UnitType)):
            return True
        if isinstance(type_, TupleType):
            return all(
                self._is_hashable_type(elem_type, seen_user_types)
                for elem_type in type_.element_types
            )
        if isinstance(type_, OptionType):
            return self._is_hashable_type(type_.value_type, seen_user_types)
        if isinstance(type_, ResultType):
            return self._is_hashable_type(
                type_.ok_type, seen_user_types
            ) and self._is_hashable_type(type_.err_type, seen_user_types)
        if isinstance(type_, UserType):
            type_info = self.type_defs.get(type_.name)
            if type_info is None:
                return True
            if type_.name in seen_user_types:
                return True
            substitutions = {
                param: arg for param, arg in zip(type_info.type_params, type_.type_args)
            }
            next_seen = seen_user_types | {type_.name}
            for fields in type_info.variants.values():
                for _field_name, field_type in fields:
                    resolved_field_type = self._apply_substitutions(
                        field_type, substitutions
                    )
                    if not self._is_hashable_type(resolved_field_type, next_seen):
                        return False
            return True
        return False

    def _validate_hashable_key_type(
        self, collection_name: str, role: str, key_type: Type, location: SourceLocation
    ) -> None:
        if not self._is_hashable_type(key_type):
            self._error(
                f"{collection_name} {role} type must be hashable, got {key_type}",
                location,
            )

    def _validate_keyed_collection_type(
        self, type_: Type, location: SourceLocation
    ) -> None:
        """Validate hash-key contracts inside maps and sets."""
        if isinstance(type_, MapType):
            self._validate_hashable_key_type("Map", "key", type_.key_type, location)
        elif isinstance(type_, MutableMapType):
            self._validate_hashable_key_type(
                "MutableMap", "key", type_.key_type, location
            )
        elif isinstance(type_, SetType):
            self._validate_hashable_key_type(
                "Set", "element", type_.element_type, location
            )

        parts = type_children(type_)
        if parts is None:
            return
        children, _rebuild = parts
        for child in children:
            self._validate_keyed_collection_type(child, location)

    @contextmanager
    def _allow_trait_self_type(self) -> Iterator[None]:
        """Temporarily allow `Self` while resolving trait method signatures."""
        self._trait_self_type_depth += 1
        self._resolved_type_cache.clear()
        try:
            yield
        finally:
            self._trait_self_type_depth -= 1
            self._resolved_type_cache.clear()

    def check_program(
        self,
        program: Program,
        modules: Mapping[str, Union[Program, _ModuleImportSummary]] | None = None,
        *,
        is_entrypoint: bool = True,
    ) -> None:
        """
        Type check a complete program.

        Args:
            program: The main program AST.
            modules: Optional map of module names to parsed Program ASTs or
                precomputed import summaries.

        Raises:
            TypeError: If any type errors are found
        """
        self.errors = []
        self._resolved_type_cache.clear()

        # Resolve imports: load type defs and function sigs from modules
        if modules is not None:
            resolved: set[str] = set()
            import_summaries: dict[str, _ModuleImportSummary] = {}
            for defn in program.definitions:
                if isinstance(defn, ImportStatement):
                    self._resolve_import(defn, modules, resolved, import_summaries)

        # Detect app mode: init/update/render present without main
        func_names = {d.name for d in program.definitions if isinstance(d, FunctionDef)}
        self._is_app_mode = {"init", "update", "render"}.issubset(
            func_names
        ) and "main" not in func_names

        type_defs = [defn for defn in program.definitions if isinstance(defn, TypeDef)]
        type_aliases = [
            defn for defn in program.definitions if isinstance(defn, TypeAlias)
        ]
        function_defs = [
            defn for defn in program.definitions if isinstance(defn, FunctionDef)
        ]
        self._validate_exported_type_surface(
            type_aliases,
            type_defs,
            function_defs,
            has_exports=self._program_has_explicit_exports(program),
        )

        # First pass: collect type aliases, type definitions, and trait definitions
        with self._with_declared_type_defs(type_defs):
            for defn in type_aliases:
                self._collect_type_alias(defn)
            for defn in type_defs:
                self._collect_type_def(defn)
            self._refresh_type_def_variance(
                {
                    defn.name: self.type_defs[defn.name]
                    for defn in type_defs
                    if defn.name in self.type_defs
                }
            )
            self._validate_type_aliases(type_aliases)
        for defn in program.definitions:
            if isinstance(defn, TraitDef):
                self._collect_trait_def(defn)

        # Second pass: collect function signatures and process impl blocks.
        # Keep the first signature when a name is repeated so later passes do
        # not silently type-check calls against whichever definition happened
        # to appear last.
        seen_function_names: set[str] = set()
        for defn in program.definitions:
            if isinstance(defn, FunctionDef):
                if defn.name in seen_function_names:
                    self._error(
                        f"Duplicate function definition: '{defn.name}'",
                        defn.location,
                        ErrorCode.TYPE_DUPLICATE_DEFINITION,
                    )
                    continue
                seen_function_names.add(defn.name)
                self._collect_function_sig(defn)
        for defn in program.definitions:
            if isinstance(defn, ImplDef):
                self._check_impl_def(defn)

        if is_entrypoint:
            self._validate_browser_entrypoint_lifecycle(program)

        # Collect function names tested by test blocks so they can be
        # exempted from the example requirement.
        self._tested_by_block: set[str] = set()
        for defn in program.definitions:
            if isinstance(defn, TestBlock):
                self._collect_tested_names(defn.body)

        # Third pass: type-check function bodies before final effect validation.
        for defn in program.definitions:
            if isinstance(defn, FunctionDef):
                self._check_function_def(
                    defn, update_signature=False, validate_effects=False
                )
        for defn in program.definitions:
            if isinstance(defn, ImplDef):
                for method in defn.methods:
                    self._check_function_def(
                        method, update_signature=False, validate_effects=False
                    )

        if not self.errors:
            self._stabilize_function_effects(program)

            for defn in program.definitions:
                if isinstance(defn, FunctionDef):
                    self._check_function_def(defn)
            for defn in program.definitions:
                if isinstance(defn, ImplDef):
                    for method in defn.methods:
                        self._check_function_def(method, update_signature=False)

        # Fourth pass: check test blocks
        for defn in program.definitions:
            if isinstance(defn, TestBlock):
                self._check_test_block(defn)

        if self.errors:
            if len(self.errors) == 1:
                raise self.errors[0]
            raise TypeErrors(self.errors)

    def check_project_graph(self, dep_graph) -> dict[str, Program]:
        """Typecheck all modules in a DependencyGraph in topological order.

        Returns a dict of module_name -> parsed program AST, all typechecked.
        Type info carries across module boundaries: only exported symbols
        (or all symbols if no exports) from earlier modules are available.
        """
        checked: dict[str, Program] = {}
        module_summaries: dict[str, _ModuleImportSummary] = {}

        for mod_name in dep_graph.sorted_modules:
            program = dep_graph.parsed[mod_name]  # type: ignore[assignment]
            # Each module gets a fresh TypeChecker; import resolution
            # via _resolve_import applies export visibility filtering.
            mod_checker = TypeChecker(target_profile=self._target_profile)
            modules = module_summaries or None
            mod_checker.check_program(
                program,
                modules=modules,
                is_entrypoint=mod_name == dep_graph.project.entrypoint,
            )
            checked[mod_name] = program
            module_summaries[mod_name] = mod_checker._build_module_import_summary(
                program
            )

        return checked

    def _validate_browser_entrypoint_lifecycle(self, program: Program) -> None:
        """Reject stale or partial browser app lifecycles on entrypoints."""
        if self._target_profile is None or self._target_profile.target != "browser":
            return

        func_defs = {
            defn.name: defn
            for defn in program.definitions
            if isinstance(defn, FunctionDef)
        }
        if "main" in func_defs:
            return

        func_names = set(func_defs)
        lifecycle_names = {"init", "update", "render"}
        stale_names = {"init_model", "view"}
        hinted_names = func_names & (lifecycle_names | stale_names)
        if not hinted_names:
            return

        if "init_model" in func_defs:
            self._error(
                "Browser apps without `main()` must use `init()` instead of the "
                "stale `init_model()` lifecycle name",
                func_defs["init_model"].location,
            )
            return

        if "view" in func_defs and "render" not in func_defs:
            self._error(
                "Browser apps without `main()` must use `render(model) -> Unit` "
                "instead of the stale `view(...)` lifecycle function",
                func_defs["view"].location,
            )
            return

        missing = sorted(lifecycle_names - func_names)
        if missing:
            missing_text = ", ".join(f"`{name}`" for name in missing)
            self._error(
                "Browser apps without `main()` must define the canonical "
                "`init()`, `update(model, dt: Float)`, and `render(model) -> Unit` "
                f"lifecycle; missing {missing_text}",
                program.location,
            )
            return

        init_def = func_defs["init"]
        update_def = func_defs["update"]
        render_def = func_defs["render"]

        if init_def.params:
            self._error(
                "Browser `init()` must not take parameters",
                init_def.location,
            )
            return

        if len(update_def.params) != 2:
            self._error(
                "Browser `update()` must have signature "
                "`update(model, dt: Float) -> Model`",
                update_def.location,
            )
            return

        update_dt_type = self._resolve_type(update_def.params[1].param_type)
        if not self._types_strictly_compatible(_FLOAT_TYPE, update_dt_type):
            self._error(
                "Browser `update()` must have signature "
                "`update(model, dt: Float) -> Model`",
                update_def.params[1].location,
            )
            return

        if len(render_def.params) != 1:
            self._error(
                "Browser `render()` must have signature `render(model) -> Unit`",
                render_def.location,
            )
            return

        render_return_type = self._resolve_type(render_def.return_type)
        if not self._types_strictly_compatible(_UNIT_TYPE, render_return_type):
            self._error(
                "Browser `render()` must have signature `render(model) -> Unit`",
                render_def.location,
            )
            return

    def _resolve_import(
        self,
        import_stmt: ImportStatement,
        modules: Mapping[str, Union[Program, _ModuleImportSummary]],
        resolved: set[str],
        import_summaries: dict[str, _ModuleImportSummary],
    ) -> None:
        """Resolve an import statement by loading module definitions."""
        name = import_stmt.module_name
        if name in resolved:
            if name in import_summaries:
                self._apply_module_import_summary(
                    module_name=name,
                    alias=import_stmt.alias,
                    summary=import_summaries[name],
                )
            return  # Already imported
        if name not in modules:
            raise TypeError(
                f"Unknown module: '{name}'",
                import_stmt.location,
            )

        resolved.add(name)
        mod_entry = modules[name]

        if isinstance(mod_entry, _ModuleImportSummary):
            for nested_import in mod_entry.imports:
                imported_name = nested_import.module_name
                if imported_name == name:
                    raise TypeError(
                        f"Circular import detected: module '{name}' imports itself",
                        nested_import.location,
                    )
                if imported_name not in modules:
                    raise TypeError(
                        f"Unknown module: '{imported_name}'",
                        nested_import.location,
                    )
                self._resolve_import(nested_import, modules, resolved, import_summaries)
            import_summaries[name] = mod_entry
            self._apply_module_import_summary(
                module_name=name,
                alias=import_stmt.alias,
                summary=mod_entry,
            )
            return

        mod_program = mod_entry

        # Check for circular imports: if a module also imports, resolve recursively
        for defn in mod_program.definitions:
            if isinstance(defn, ImportStatement):
                if defn.module_name == name:
                    raise TypeError(
                        f"Circular import detected: module '{name}' imports itself",
                        defn.location,
                    )
                self._resolve_import(defn, modules, resolved, import_summaries)

        # Determine if module uses explicit exports
        has_exports = any(
            (isinstance(d, (FunctionDef, TypeAlias, TypeDef)) and d.exported)
            for d in mod_program.definitions
        )

        # Track exported symbols for qualified access
        module_symbols: dict[str, Type] = {}
        is_aliased = import_stmt.alias is not None

        # Collect type aliases and definitions from module
        module_type_defs = [
            defn for defn in mod_program.definitions if isinstance(defn, TypeDef)
        ]
        module_aliases = [
            defn for defn in mod_program.definitions if isinstance(defn, TypeAlias)
        ]
        module_functions = [
            defn for defn in mod_program.definitions if isinstance(defn, FunctionDef)
        ]
        self._validate_exported_type_surface(
            module_aliases,
            module_type_defs,
            module_functions,
            has_exports=has_exports,
        )
        temporary_alias_names = {
            defn.name
            for defn in module_aliases
            if is_aliased or (has_exports and not defn.exported)
        }
        previous_temporary_aliases = {
            alias_name: self.type_aliases[alias_name]
            for alias_name in temporary_alias_names
            if alias_name in self.type_aliases
        }
        try:
            with self._with_declared_type_defs(module_type_defs):
                for defn in module_aliases:
                    self._collect_type_alias(defn)
                collected_type_defs: dict[str, TypeDefInfo] = {}
                for defn in module_type_defs:
                    if has_exports and not defn.exported:
                        continue  # Not exported — skip
                    if not is_aliased:
                        self._collect_type_def(defn)
                        if defn.name in self.type_defs:
                            collected_type_defs[defn.name] = self.type_defs[defn.name]
                    # Track for qualified access
                    for variant in defn.variants:
                        fields = []
                        for fn, ft in variant.fields:
                            fields.append(
                                (fn, self._resolve_type(ft, defn.type_params))
                            )
                        func_type = FuncType(
                            tuple(t for _, t in fields),
                            UserType(defn.name),
                        )
                        module_symbols[variant.name] = func_type
                self._refresh_type_def_variance(collected_type_defs)
                self._validate_type_aliases(module_aliases)

            # Collect function signatures from module
            module_param_names: dict[str, list[str]] = {}
            module_default_counts: dict[str, int] = {}
            stdlib_module_name = self._stdlib_module_name_for_program(mod_program)
            for defn in mod_program.definitions:
                if isinstance(defn, FunctionDef):
                    if has_exports and not defn.exported:
                        continue  # Not exported — skip
                    func_type, param_names, num_defaults = (
                        self._function_signature_from_def(defn)
                    )
                    func_type = (
                        self._stdlib_forwarder_type(stdlib_module_name, defn.name)
                        or func_type
                    )
                    if not is_aliased:
                        self._register_function_signature(
                            defn.name,
                            func_type,
                            param_names,
                            num_defaults,
                        )
                    # Track for qualified access
                    module_symbols[defn.name] = func_type
                    module_param_names[defn.name] = param_names
                    if num_defaults > 0:
                        module_default_counts[defn.name] = num_defaults
            import_summaries[name] = self._build_module_import_summary(mod_program)
        finally:
            # Restore temporary-alias state even if signature resolution raised,
            # so failed or aliased imports cannot leak names into the importer.
            if temporary_alias_names:
                for alias_name in temporary_alias_names:
                    if alias_name in previous_temporary_aliases:
                        self.type_aliases[alias_name] = previous_temporary_aliases[
                            alias_name
                        ]
                    else:
                        self.type_aliases.pop(alias_name, None)
                # Cached resolutions may have referenced hidden aliases while
                # resolving exported signatures above; drop the cache so later
                # lookups do not observe private names.
                self._resolved_type_cache.clear()

        # Register module namespace for qualified access (Foo.symbol)
        ns_name = import_stmt.alias or name
        self._module_exports[ns_name] = module_symbols
        self._module_param_names[ns_name] = module_param_names
        self._module_default_counts[ns_name] = module_default_counts
        self.global_env.bind(ns_name, ModuleType(name))

    @staticmethod
    def _program_has_explicit_exports(program: Program) -> bool:
        """Return True when a module uses explicit export markers."""
        return any(
            isinstance(defn, (FunctionDef, TypeAlias, TypeDef)) and defn.exported
            for defn in program.definitions
        )

    @staticmethod
    def _stdlib_module_name_for_program(program: Program) -> str | None:
        """Return the std module name for a program loaded from geno/std."""
        for defn in program.definitions:
            filename = getattr(defn.location, "filename", None)
            if not filename:
                continue
            path = Path(filename)
            if (
                path.suffix == ".geno"
                and path.parent.name == "std"
                and path.parent.parent.name == "geno"
            ):
                return path.stem
        return None

    def _stdlib_forwarder_type(
        self,
        stdlib_module_name: str | None,
        function_name: str,
    ) -> FuncType | None:
        """Return the generic builtin signature for known std thin wrappers."""
        if stdlib_module_name is None:
            return None
        builtin_name = _STDLIB_FORWARDER_BUILTINS.get(stdlib_module_name, {}).get(
            function_name
        )
        if builtin_name is None:
            return None
        builtin = self.builtin_types.get(builtin_name)
        if builtin is None:
            return None
        return builtin[0]

    def _build_module_import_summary(self, program: Program) -> _ModuleImportSummary:
        """Precompute the import-facing shape of a checked module."""
        has_exports = self._program_has_explicit_exports(program)
        stdlib_module_name = self._stdlib_module_name_for_program(program)
        imports: list[ImportStatement] = []
        type_aliases: dict[str, tuple[list[str], TypeAnnotation]] = {}
        type_defs: dict[str, TypeDefInfo] = {}
        functions: dict[str, FuncType] = {}
        func_param_names: dict[str, list[str]] = {}
        func_default_counts: dict[str, int] = {}
        module_symbols: dict[str, Type] = {}
        module_param_names: dict[str, list[str]] = {}
        module_default_counts: dict[str, int] = {}
        module_type_defs = [
            defn for defn in program.definitions if isinstance(defn, TypeDef)
        ]
        module_aliases = [
            defn for defn in program.definitions if isinstance(defn, TypeAlias)
        ]
        module_functions = [
            defn for defn in program.definitions if isinstance(defn, FunctionDef)
        ]
        self._validate_exported_type_surface(
            module_aliases,
            module_type_defs,
            module_functions,
            has_exports=has_exports,
        )

        with self._with_declared_type_defs(module_type_defs):
            for defn in program.definitions:
                if isinstance(defn, ImportStatement):
                    imports.append(defn)
                elif isinstance(defn, TypeAlias):
                    if has_exports and not defn.exported:
                        continue
                    type_aliases[defn.name] = (
                        list(defn.type_params),
                        defn.target_type,
                    )
                elif isinstance(defn, TypeDef):
                    if has_exports and not defn.exported:
                        continue
                    type_defs[defn.name] = self._build_type_def_info(defn)
                    for variant in defn.variants:
                        fields = []
                        for field_name, field_type in variant.fields:
                            fields.append(
                                (
                                    field_name,
                                    self._resolve_type(field_type, defn.type_params),
                                )
                            )
                        module_symbols[variant.name] = FuncType(
                            tuple(field_type for _, field_type in fields),
                            UserType(defn.name),
                        )
                elif isinstance(defn, FunctionDef):
                    if has_exports and not defn.exported:
                        continue
                    func_type, param_names, num_defaults = (
                        self._function_signature_from_def(defn)
                    )
                    func_type = (
                        self._stdlib_forwarder_type(stdlib_module_name, defn.name)
                        or func_type
                    )
                    functions[defn.name] = func_type
                    func_param_names[defn.name] = param_names
                    if num_defaults > 0:
                        func_default_counts[defn.name] = num_defaults
                        module_default_counts[defn.name] = num_defaults
                    module_symbols[defn.name] = func_type
                    module_param_names[defn.name] = param_names
        self._refresh_type_def_variance(type_defs)

        return _ModuleImportSummary(
            imports=tuple(imports),
            type_aliases=type_aliases,
            type_defs=type_defs,
            functions=functions,
            func_param_names=func_param_names,
            func_default_counts=func_default_counts,
            module_symbols=module_symbols,
            module_param_names=module_param_names,
            module_default_counts=module_default_counts,
        )

    def _apply_module_import_summary(
        self,
        module_name: str,
        alias: str | None,
        summary: _ModuleImportSummary,
    ) -> None:
        """Merge a precomputed module summary into the current checker."""
        if alias is None:
            for alias_name, alias_info in summary.type_aliases.items():
                self.type_aliases[alias_name] = (list(alias_info[0]), alias_info[1])
            for type_name, type_info in summary.type_defs.items():
                # Remove stale constructor entries if type is being redefined
                old_info = self.type_defs.get(type_name)
                if old_info is not None:
                    for old_ctor in old_info.variants:
                        self._constructor_to_type.pop(old_ctor, None)
                self.type_defs[type_name] = type_info
                for constructor_name in type_info.variants:
                    self._constructor_to_type[constructor_name] = type_name
            for func_name, func_type in summary.functions.items():
                self._register_function_signature(
                    func_name,
                    func_type,
                    summary.func_param_names[func_name],
                    summary.func_default_counts.get(func_name, 0),
                )

        ns_name = alias or module_name
        self._module_exports[ns_name] = dict(summary.module_symbols)
        self._module_param_names[ns_name] = {
            name: list(param_names)
            for name, param_names in summary.module_param_names.items()
        }
        self._module_default_counts[ns_name] = dict(summary.module_default_counts)
        self.global_env.bind(ns_name, ModuleType(module_name))

    def _collect_type_alias(self, alias: TypeAlias) -> None:
        """Collect a type alias."""
        self.type_aliases[alias.name] = (alias.type_params, alias.target_type)

    def _build_type_def_info(self, defn: TypeDef) -> TypeDefInfo:
        """Build resolved type information for a type definition."""
        variants: dict[str, list[tuple[str, Type]]] = {}

        for variant in defn.variants:
            fields: list[tuple[str, Type]] = []
            for field_name, field_type_annot in variant.fields:
                field_type = self._resolve_type(field_type_annot, defn.type_params)
                fields.append((field_name, field_type))
            variants[variant.name] = fields

        return TypeDefInfo(
            name=defn.name,
            type_params=defn.type_params,
            variants=variants,
        )

    def _refresh_type_def_variance(self, type_defs: dict[str, TypeDefInfo]) -> None:
        """Recompute variance until nested user-type relationships stabilize."""
        if not type_defs:
            return

        lookup = dict(self.type_defs)
        lookup.update(type_defs)

        changed = True
        while changed:
            changed = False
            for info in type_defs.values():
                invariant = self._compute_invariant_params(
                    info.type_params, info.variants, lookup
                )
                if invariant != info.invariant_params:
                    info.invariant_params = invariant
                    changed = True

    @staticmethod
    def _compute_invariant_params(
        type_params: list[str],
        variants: dict[str, list[tuple[str, Type]]],
        type_defs: dict[str, TypeDefInfo],
    ) -> frozenset[int]:
        """Determine which type parameters require invariant checking.

        A parameter is invariant if it is stored directly in an assignable
        user-type field, appears inside a mutable container (Array, Vec, Set,
        MutableMap), or occurs in a contravariant function-parameter position.
        """
        invariant: set[int] = set()
        for i, param in enumerate(type_params):
            for fields in variants.values():
                for _fname, ftype in fields:
                    if (
                        isinstance(ftype, TypeVar) and ftype.name == param
                    ) or TypeChecker._in_noncovariant_position(ftype, param, type_defs):
                        invariant.add(i)
                        break
                else:
                    continue
                break
        return frozenset(invariant)

    @staticmethod
    def _in_noncovariant_position(
        t: Type, param: str, type_defs: dict[str, TypeDefInfo]
    ) -> bool:
        """Return True if TypeVar *param* appears in an invariant or contravariant position."""
        if isinstance(t, TypeVar):
            return False
        if isinstance(t, (ArrayType, VecType, SetType)):
            return TypeChecker._occurs_in(param, t.element_type)
        if isinstance(t, MutableMapType):
            return TypeChecker._occurs_in(param, t.key_type) or TypeChecker._occurs_in(
                param, t.value_type
            )
        if isinstance(t, FuncType):
            if any(TypeChecker._occurs_in(param, p) for p in t.param_types):
                return True
            return TypeChecker._in_noncovariant_position(
                t.return_type, param, type_defs
            )
        if isinstance(t, ListType):
            return TypeChecker._in_noncovariant_position(
                t.element_type, param, type_defs
            )
        if isinstance(t, OptionType):
            return TypeChecker._in_noncovariant_position(t.value_type, param, type_defs)
        if isinstance(t, ResultType):
            return TypeChecker._in_noncovariant_position(
                t.ok_type, param, type_defs
            ) or TypeChecker._in_noncovariant_position(t.err_type, param, type_defs)
        if isinstance(t, TupleType):
            return any(
                TypeChecker._in_noncovariant_position(e, param, type_defs)
                for e in t.element_types
            )
        if isinstance(t, MapType):
            return TypeChecker._in_noncovariant_position(
                t.key_type, param, type_defs
            ) or TypeChecker._in_noncovariant_position(t.value_type, param, type_defs)
        if isinstance(t, AsyncType):
            return TypeChecker._in_noncovariant_position(
                t.result_type, param, type_defs
            )
        if isinstance(t, UserType):
            type_info = type_defs.get(t.name)
            if type_info is None:
                return any(TypeChecker._occurs_in(param, a) for a in t.type_args)
            return any(
                TypeChecker._occurs_in(param, arg)
                if i in type_info.invariant_params
                else TypeChecker._in_noncovariant_position(arg, param, type_defs)
                for i, arg in enumerate(t.type_args)
            )
        return False

    def _collect_type_def(self, defn: TypeDef) -> None:
        """Collect a type definition."""
        # Remove stale constructor entries if this type name is being redefined
        old_info = self.type_defs.get(defn.name)
        if old_info is not None:
            for old_ctor in old_info.variants:
                self._constructor_to_type.pop(old_ctor, None)

        info = self._build_type_def_info(defn)
        self.type_defs[defn.name] = info
        for constructor_name in info.variants:
            existing_type = self._constructor_to_type.get(constructor_name)
            if existing_type is not None and existing_type != defn.name:
                self._error(
                    f"Constructor '{constructor_name}' is already defined "
                    f"by type '{existing_type}'",
                    defn.location,
                    ErrorCode.TYPE_DUPLICATE_DEFINITION,
                )
            self._constructor_to_type[constructor_name] = defn.name

    def _function_signature_from_def(
        self, defn: FunctionDef
    ) -> tuple[FuncType, list[str], int]:
        """Build a function signature and named-arg metadata."""
        param_types = tuple(self._resolve_type(p.param_type) for p in defn.params)
        return_type = self._resolve_type(defn.return_type)
        defn.__dict__["_resolved_return_type"] = return_type
        for param_type in param_types:
            self._validate_keyed_collection_type(param_type, defn.location)
        self._validate_keyed_collection_type(return_type, defn.location)

        if defn.is_async:
            return_type = AsyncType(return_type)

        effects = frozenset(defn.effects) if defn.effects else frozenset()
        func_type = FuncType(param_types, return_type, effects)
        param_names = [p.name for p in defn.params]
        num_defaults = sum(1 for p in defn.params if p.default_value is not None)
        return func_type, param_names, num_defaults

    def _qualified_module_function_type_from_def(self, defn: FunctionDef) -> FuncType:
        """Build the qualified-call type used for module namespace access."""
        return self._function_signature_from_def(defn)[0]

    def _register_function_signature(
        self,
        name: str,
        func_type: FuncType,
        param_names: list[str],
        num_defaults: int,
    ) -> None:
        """Register a function signature into the global environment."""
        self.global_env.bind(name, func_type)
        self._user_defined_names.add(name)
        self.func_param_names[name] = list(param_names)
        if num_defaults > 0:
            self.func_default_counts[name] = num_defaults

    def _collect_function_sig(self, defn: FunctionDef) -> None:
        """Collect a function signature into the environment."""
        func_type, param_names, num_defaults = self._function_signature_from_def(defn)
        self._register_function_signature(
            defn.name, func_type, param_names, num_defaults
        )

    def _collect_trait_def(self, defn: TraitDef) -> None:
        """Collect a trait definition."""
        if defn.name in self.trait_defs:
            self._error(
                f"Duplicate trait definition: '{defn.name}'",
                defn.location,
                ErrorCode.TYPE_DUPLICATE_DEFINITION,
            )
            return
        self.trait_defs[defn.name] = defn

        with self._allow_trait_self_type():
            for method_sig in defn.methods:
                for param in method_sig.params:
                    self._resolve_type(param.param_type)
                self._resolve_type(method_sig.return_type)

        # Register each method name for trait dispatch lookup
        for method_sig in defn.methods:
            if method_sig.name not in self.trait_methods:
                self.trait_methods[method_sig.name] = []
            self.trait_methods[method_sig.name].append((defn.name, defn))

    def _trait_method_param_names(
        self, trait_name: str, method_name: str
    ) -> list[str] | None:
        """Return parameter names for ``trait_name.method_name``."""
        trait_def = self.trait_defs.get(trait_name)
        if trait_def is None:
            return None
        for method_sig in trait_def.methods:
            if method_sig.name == method_name:
                return [param.name for param in method_sig.params]
        return None

    @staticmethod
    def _substitute_trait_self(t: Type, replacement: Type) -> Type:
        """Replace `Self` in a trait signature with the concrete impl type."""
        if isinstance(t, UserType) and t.name == "Self" and not t.type_args:
            return replacement
        return map_type(
            t, lambda child: TypeChecker._substitute_trait_self(child, replacement)
        )

    def _check_impl_def(self, defn: ImplDef) -> None:
        """Verify and register an impl block."""
        # Verify trait exists
        if defn.trait_name not in self.trait_defs:
            self._error(
                f"Unknown trait: '{defn.trait_name}'",
                defn.location,
            )
            return

        # Verify target type exists
        if defn.target_type not in self.type_defs:
            hint = _suggest_name(defn.target_type, self.type_defs.keys())
            self._error(
                f"Unknown type: '{defn.target_type}'{hint}",
                defn.location,
                ErrorCode.TYPE_UNDEFINED_TYPE,
            )
            return

        # Check for duplicate impl (before signature validation to avoid noisy errors)
        if (defn.trait_name, defn.target_type) in self.impl_registry:
            self._error(
                f"Duplicate implementation of trait '{defn.trait_name}' "
                f"for type '{defn.target_type}'",
                defn.location,
                ErrorCode.TYPE_DUPLICATE_DEFINITION,
            )
            return

        trait_def = self.trait_defs[defn.trait_name]
        impl_self_type = UserType(defn.target_type)

        # Build a map of implemented method names to their FunctionDef nodes
        impl_method_map: dict[str, FunctionDef] = {}
        for method in defn.methods:
            impl_method_map[method.name] = method

        # Verify all required methods are implemented and signatures match
        for sig in trait_def.methods:
            if sig.name not in impl_method_map:
                self._error(
                    f"Missing implementation of method '{sig.name}' "
                    f"for trait '{defn.trait_name}' on type '{defn.target_type}'",
                    defn.location,
                )
                continue

            impl_method = impl_method_map[sig.name]

            # Verify parameter count matches
            if len(impl_method.params) != len(sig.params):
                self._error(
                    f"Method '{sig.name}' in impl '{defn.trait_name}' for "
                    f"'{defn.target_type}' has {len(impl_method.params)} parameters, "
                    f"expected {len(sig.params)}",
                    impl_method.location,
                )
                continue

            # Verify return type matches
            with self._allow_trait_self_type():
                expected_ret = self._resolve_type(sig.return_type)
            expected_ret = self._substitute_trait_self(expected_ret, impl_self_type)
            actual_ret = self._resolve_type(impl_method.return_type)
            if not self._types_strictly_compatible(expected_ret, actual_ret):
                self._error(
                    f"Method '{sig.name}' return type mismatch: "
                    f"expected {expected_ret}, got {actual_ret}",
                    impl_method.location,
                )

            # Verify parameter types match (skip 'self' name check, check types)
            for i, (sig_param, impl_param) in enumerate(
                zip(sig.params, impl_method.params)
            ):
                with self._allow_trait_self_type():
                    sig_type = self._resolve_type(sig_param.param_type)
                expected_type = self._substitute_trait_self(sig_type, impl_self_type)
                impl_type = self._resolve_type(impl_param.param_type)
                if not self._types_strictly_compatible(expected_type, impl_type):
                    self._error(
                        f"Method '{sig.name}' parameter {i + 1} type mismatch: "
                        f"expected {expected_type}, got {impl_type}",
                        impl_param.location,
                    )

        # Register the implementation
        method_types: dict[str, FuncType] = {}
        for method in defn.methods:
            param_types = tuple(self._resolve_type(p.param_type) for p in method.params)
            return_type = self._resolve_type(method.return_type)
            effects = frozenset(method.effects) if method.effects else frozenset()
            method_types[method.name] = FuncType(param_types, return_type, effects)

        self.impl_registry[(defn.trait_name, defn.target_type)] = method_types

        # Register trait method dispatch function in the type environment.
        # This allows calls like `describe(c)` to resolve.
        for sig in trait_def.methods:
            with self._allow_trait_self_type():
                sig_param_types = tuple(
                    self._resolve_type(p.param_type) for p in sig.params
                )
                sig_return_type = self._resolve_type(sig.return_type)
            # Only bind if not already bound (first impl registers the dispatch name)
            if sig.name not in self._user_defined_names:
                dispatch_type = FuncType(sig_param_types, sig_return_type)
                self.global_env.bind(sig.name, dispatch_type)
                self._user_defined_names.add(sig.name)
                self.func_param_names[sig.name] = [p.name for p in sig.params]

    def _check_test_block(self, test_block: TestBlock) -> None:
        """Type check a test block's body."""
        env = self.global_env.child()
        for stmt in test_block.body:
            self._check_statement(stmt, env)

    def _collect_tested_names(self, stmts) -> None:
        """Walk statements to find function names called in test blocks."""
        for node in stmts:
            for child in vars(node).values():
                if isinstance(child, list):
                    for item in child:
                        if hasattr(item, "__dict__"):
                            self._collect_tested_names([item])
                elif hasattr(child, "__dict__"):
                    self._collect_tested_names([child])
            if isinstance(node, FunctionCall):
                if isinstance(node.function, Identifier):
                    self._tested_by_block.add(node.function.name)

    def _check_function_def(
        self,
        defn: FunctionDef,
        *,
        update_signature: bool = True,
        validate_effects: bool = True,
    ) -> None:
        """Type check a function definition."""
        # Enforce example requirement - every function must have at least one example
        # Exception: 'main' and app lifecycle functions don't require examples
        # In app mode, all functions are exempt (game helpers can't easily have examples)
        # Functions called from test blocks are also exempt
        _EXEMPT_FROM_EXAMPLES = {"main", "init", "update", "render"}
        tested_by_block: set = getattr(self, "_tested_by_block", set())
        if (
            not defn.specs.examples
            and defn.name not in _EXEMPT_FROM_EXAMPLES
            and not self._is_app_mode
            and not defn.is_async
            and defn.untested_reason is None
            and defn.name not in tested_by_block
        ):
            self._error(
                f"Function '{defn.name}' must have at least one example clause",
                defn.location,
            )

        # Validate effect annotations
        for effect_name in defn.effects:
            if effect_name not in VALID_EFFECTS:
                self._error(
                    f"Unknown effect '{effect_name}'. "
                    f"Valid effects are: {', '.join(sorted(VALID_EFFECTS))}",
                    defn.location,
                    ErrorCode.EFFECT_UNKNOWN,
                )

        # Validate no duplicate parameter names
        seen_params = set()
        for param in defn.params:
            if param.name in seen_params:
                self._error(
                    f"Duplicate parameter name '{param.name}'",
                    param.location,
                    ErrorCode.TYPE_DUPLICATE_DEFINITION,
                )
            seen_params.add(param.name)

        self._validate_ensures_result_binding_collisions(defn)

        # Create local environment with parameters
        local_env = self.global_env.child()

        for param in defn.params:
            param_type = self._resolve_type(param.param_type)
            local_env.bind(param.name, param_type)

        # Type-check default value expressions
        for param in defn.params:
            if param.default_value is not None:
                default_type = self._check_expression(
                    param.default_value, self.global_env
                )
                expected_type = self._resolve_type(param.param_type)
                if not self._types_strictly_compatible(expected_type, default_type):
                    self._error(
                        f"Default value type mismatch for '{param.name}': "
                        f"expected {expected_type}, got {default_type}",
                        param.default_value.location,
                    )

        # Set current return type for return statement checking
        # For async functions, the actual return type (not wrapped in AsyncType)
        self.current_return_type = self._resolve_type(defn.return_type)

        # Track async and main context
        prev_async = self._in_async_function
        self._in_async_function = defn.is_async
        prev_main = self._in_main_function
        self._in_main_function = defn.name == "main"

        # Check specification clauses
        self._check_specs(defn, local_env)

        # Check body
        for stmt in defn.body:
            self._check_statement(stmt, local_env)

        # Check return-path coverage for non-Unit functions
        if not isinstance(self.current_return_type, UnitType):
            if not self._definitely_returns(defn.body):
                self._error(
                    f"Function '{defn.name}' may not return a value on all paths",
                    defn.location,
                )

        if validate_effects:
            inferred = self._infer_function_effects(
                defn, self._effect_env_for_function(defn)
            )
            declared = frozenset(defn.effects) if defn.effects else None

            if declared is not None:
                # Check for invalid declared effects (already validated above)
                # Check that body doesn't exceed declared effects
                undeclared = inferred - declared
                if undeclared:
                    self._error(
                        f"Function '{defn.name}' performs undeclared effects: "
                        f"{', '.join(sorted(undeclared))}. "
                        f"Declared effects: {', '.join(sorted(declared)) if declared else '(pure)'}",
                        defn.location,
                        ErrorCode.EFFECT_VIOLATION,
                    )
                # Use declared effects as the function's signature effects
                final_effects = declared
            else:
                # No explicit annotation — use inferred effects
                final_effects = inferred

            # Update the function's type in the environment with inferred/declared effects
            if update_signature:
                existing_type = self.global_env.lookup(defn.name)
                if (
                    isinstance(existing_type, FuncType)
                    and existing_type.effects != final_effects
                ):
                    updated = FuncType(
                        existing_type.param_types,
                        existing_type.return_type,
                        final_effects,
                    )
                    self.global_env.bind(defn.name, updated)

        self.current_return_type = None
        self._in_async_function = prev_async
        self._in_main_function = prev_main

    def _validate_ensures_result_binding_collisions(self, defn: FunctionDef) -> None:
        """Reject user bindings that collide with `ensures result`."""
        if not defn.specs.ensures:
            return

        for param in defn.params:
            if param.name == "result":
                self._error_result_binding_collision("parameter", param.location)

        for stmt in defn.body:
            self._validate_no_result_binding_in_statement(stmt)

    def _error_result_binding_collision(
        self, binding_kind: str, location: SourceLocation
    ) -> None:
        self._error(
            "`result` is reserved in functions with ensures clauses; "
            f"rename this {binding_kind} so `ensures result` unambiguously "
            "refers to the return value",
            location,
        )

    def _validate_no_result_binding_in_statement(self, stmt: Statement) -> None:
        if isinstance(stmt, (LetStatement, VarStatement)):
            if stmt.name == "result":
                self._error_result_binding_collision("binding", stmt.location)
            self._validate_no_result_binding_in_expr(stmt.value)
        elif isinstance(stmt, TupleDestructureStatement):
            if "result" in stmt.names:
                self._error_result_binding_collision(
                    "destructured binding", stmt.location
                )
            self._validate_no_result_binding_in_expr(stmt.value)
        elif isinstance(stmt, AssignStatement):
            self._validate_no_result_binding_in_expr(stmt.value)
        elif isinstance(stmt, IndexAssignStatement):
            self._validate_no_result_binding_in_expr(stmt.target)
            self._validate_no_result_binding_in_expr(stmt.index)
            self._validate_no_result_binding_in_expr(stmt.value)
        elif isinstance(stmt, FieldAssignStatement):
            self._validate_no_result_binding_in_expr(stmt.target)
            self._validate_no_result_binding_in_expr(stmt.value)
        elif isinstance(stmt, IfStatement):
            self._validate_no_result_binding_in_expr(stmt.condition)
            self._validate_no_result_binding_in_statements(stmt.then_body)
            self._validate_no_result_binding_in_statements(stmt.else_body)
        elif isinstance(stmt, WhileStatement):
            self._validate_no_result_binding_in_expr(stmt.condition)
            self._validate_no_result_binding_in_statements(stmt.body)
        elif isinstance(stmt, ForStatement):
            if stmt.variable == "result":
                self._error_result_binding_collision("loop variable", stmt.location)
            self._validate_no_result_binding_in_expr(stmt.iterable)
            self._validate_no_result_binding_in_statements(stmt.body)
        elif isinstance(stmt, MatchStatement):
            self._validate_no_result_binding_in_expr(stmt.scrutinee)
            self._validate_no_result_binding_in_match_arms(stmt.arms)
        elif isinstance(stmt, ReturnStatement):
            self._validate_no_result_binding_in_expr(stmt.value)
        elif isinstance(stmt, TryStatement):
            self._validate_no_result_binding_in_statements(stmt.try_body)
            if stmt.catch_clause.variable == "result":
                self._error_result_binding_collision(
                    "catch binding", stmt.catch_clause.location
                )
            self._validate_no_result_binding_in_statements(stmt.catch_clause.body)
        elif isinstance(stmt, (ExpressionStatement, AssertStatement)):
            self._validate_no_result_binding_in_expr(stmt.expression)

    def _validate_no_result_binding_in_statements(self, stmts: list[Statement]) -> None:
        for stmt in stmts:
            self._validate_no_result_binding_in_statement(stmt)

    def _validate_no_result_binding_in_match_arms(self, arms: list[MatchArm]) -> None:
        for arm in arms:
            self._validate_no_result_binding_in_pattern(arm.pattern)
            if arm.guard is not None:
                self._validate_no_result_binding_in_expr(arm.guard)
            self._validate_no_result_binding_in_statements(arm.body)

    def _validate_no_result_binding_in_pattern(self, pattern: Pattern) -> None:
        if isinstance(pattern, VariablePattern):
            if pattern.name == "result":
                self._error_result_binding_collision(
                    "pattern binding", pattern.location
                )
        elif isinstance(pattern, RestPattern):
            if pattern.name == "result":
                self._error_result_binding_collision(
                    "rest pattern binding", pattern.location
                )
        elif isinstance(pattern, ConstructorPattern):
            for subpattern in pattern.subpatterns:
                self._validate_no_result_binding_in_pattern(subpattern)
        elif isinstance(pattern, ListPattern):
            for element in pattern.elements:
                self._validate_no_result_binding_in_pattern(element)

    def _validate_no_result_binding_in_expr(self, expr: Expression | None) -> None:
        if expr is None:
            return
        if isinstance(expr, FStringExpr):
            for part in expr.parts:
                if not isinstance(part, str):
                    self._validate_no_result_binding_in_expr(part)
        elif isinstance(expr, ListLiteral):
            for element in expr.elements:
                self._validate_no_result_binding_in_expr(element)
        elif isinstance(expr, ListComprehension):
            if expr.variable == "result":
                self._error_result_binding_collision(
                    "list comprehension variable", expr.location
                )
            self._validate_no_result_binding_in_expr(expr.iterable)
            self._validate_no_result_binding_in_expr(expr.condition)
            self._validate_no_result_binding_in_expr(expr.element_expr)
        elif isinstance(expr, ThrowExpression):
            self._validate_no_result_binding_in_expr(expr.value)
        elif isinstance(expr, AwaitExpr):
            self._validate_no_result_binding_in_expr(expr.expr)
        elif isinstance(expr, BinaryOp):
            self._validate_no_result_binding_in_expr(expr.left)
            self._validate_no_result_binding_in_expr(expr.right)
        elif isinstance(expr, UnaryOp):
            self._validate_no_result_binding_in_expr(expr.operand)
        elif isinstance(expr, FunctionCall):
            self._validate_no_result_binding_in_expr(expr.function)
            for call_arg in expr.arguments:
                self._validate_no_result_binding_in_expr(call_arg.value)
        elif isinstance(expr, IndexAccess):
            self._validate_no_result_binding_in_expr(expr.target)
            self._validate_no_result_binding_in_expr(expr.index)
        elif isinstance(expr, FieldAccess):
            self._validate_no_result_binding_in_expr(expr.target)
        elif isinstance(expr, Pipeline):
            self._validate_no_result_binding_in_expr(expr.initial)
            for stage in expr.stages:
                self._validate_no_result_binding_in_expr(stage.function)
                for stage_arg in stage.arguments:
                    self._validate_no_result_binding_in_expr(stage_arg)
        elif isinstance(expr, LambdaExpr):
            for param in expr.params:
                if param.name == "result":
                    self._error_result_binding_collision(
                        "lambda parameter", param.location
                    )
            self._validate_no_result_binding_in_expr(expr.body)
            if expr.block_body is not None:
                self._validate_no_result_binding_in_statements(expr.block_body)
        elif isinstance(expr, MatchExpr):
            self._validate_no_result_binding_in_expr(expr.scrutinee)
            self._validate_no_result_binding_in_match_arms(expr.arms)
        elif isinstance(expr, ConstructorCall):
            for ctor_arg in expr.arguments:
                self._validate_no_result_binding_in_expr(ctor_arg)
        elif isinstance(expr, TupleExpr):
            for element in expr.elements:
                self._validate_no_result_binding_in_expr(element)
        elif isinstance(expr, TypedHole):
            self._validate_no_result_binding_in_expr(expr.constraint)
        elif isinstance(expr, PropagateExpr):
            self._validate_no_result_binding_in_expr(expr.operand)
        elif isinstance(expr, WithExpr):
            self._validate_no_result_binding_in_expr(expr.target)
            for _field_name, value in expr.updates:
                self._validate_no_result_binding_in_expr(value)

    def _effect_env_for_function(self, defn: FunctionDef) -> TypeEnv:
        """Build the local environment used for effect inference."""
        local_env = self.global_env.child()
        for param in defn.params:
            param_type = self._resolve_type(param.param_type)
            local_env.bind(param.name, param_type)
        return local_env

    def _stable_effects_for_function(self, defn: FunctionDef) -> frozenset[str]:
        """Return the final effect set for a function definition."""
        local_env = self._effect_env_for_function(defn)
        inferred = self._infer_function_effects(defn, local_env)
        if defn.effects:
            return frozenset(defn.effects)
        return inferred

    def _stabilize_function_effects(self, program: Program) -> None:
        """Iteratively infer effects for functions until signatures stabilize."""
        functions: list[FunctionDef] = []
        impl_methods: list[tuple[ImplDef, FunctionDef]] = []

        for defn in program.definitions:
            if isinstance(defn, FunctionDef):
                functions.append(defn)
            elif isinstance(defn, ImplDef):
                for method in defn.methods:
                    impl_methods.append((defn, method))

        while True:
            changed = False

            for defn in functions:
                existing_type = self.global_env.lookup(defn.name)
                if not isinstance(existing_type, FuncType):
                    continue
                final_effects = self._stable_effects_for_function(defn)
                if existing_type.effects != final_effects:
                    self.global_env.bind(
                        defn.name,
                        FuncType(
                            existing_type.param_types,
                            existing_type.return_type,
                            final_effects,
                        ),
                    )
                    changed = True

            for impl_def, method in impl_methods:
                method_types = self.impl_registry.get(
                    (impl_def.trait_name, impl_def.target_type)
                )
                if method_types is None or method.name not in method_types:
                    continue
                existing_type = method_types[method.name]
                final_effects = self._stable_effects_for_function(method)
                if existing_type.effects != final_effects:
                    method_types[method.name] = FuncType(
                        existing_type.param_types,
                        existing_type.return_type,
                        final_effects,
                    )
                    changed = True

            if not changed:
                break

    def _infer_body_effects(
        self, stmts: list[Statement], env: TypeEnv
    ) -> frozenset[str]:
        """Infer effects from a list of statements."""
        effects: set[str] = set()
        for stmt in stmts:
            effects |= self._infer_stmt_effects(stmt, env)
        return frozenset(effects)

    def _infer_function_effects(
        self, defn: FunctionDef, env: TypeEnv
    ) -> frozenset[str]:
        """Infer effects from specs plus the function body."""
        effects: set[str] = set(self._infer_specs_effects(defn, env))
        effects |= self._infer_body_effects(defn.body, env)
        return frozenset(effects)

    def _infer_specs_effects(self, defn: FunctionDef, env: TypeEnv) -> frozenset[str]:
        """Infer effects from requires and ensures clauses."""
        effects: set[str] = set()
        for req in defn.specs.requires:
            effects |= self._infer_expr_effects(req.condition, env)

        ensures_env = env.child()
        ensures_env.bind("result", self._resolve_type(defn.return_type))
        for ens in defn.specs.ensures:
            effects |= self._infer_expr_effects(ens.condition, ensures_env)
        return frozenset(effects)

    def _infer_stmt_effects(self, stmt: Statement, env: TypeEnv) -> set[str]:
        """Infer effects from a single statement."""
        effects: set[str] = set()

        if isinstance(stmt, ExpressionStatement):
            effects |= self._infer_expr_effects(stmt.expression, env)
        elif isinstance(stmt, LetStatement):
            effects |= self._infer_expr_effects(stmt.value, env)
            binding_type = (
                self._resolve_type(stmt.type_annotation)
                if stmt.type_annotation is not None
                else self._check_expression(stmt.value, env)
            )
            env.bind(stmt.name, binding_type)
        elif isinstance(
            stmt,
            (AssignStatement, FieldAssignStatement, IndexAssignStatement),
        ):
            effects.add("mutation")
            effects |= self._infer_expr_effects(stmt.value, env)
        elif isinstance(stmt, VarStatement):
            effects.add("mutation")
            effects |= self._infer_expr_effects(stmt.value, env)
            binding_type = (
                self._resolve_type(stmt.type_annotation)
                if stmt.type_annotation is not None
                else self._check_expression(stmt.value, env)
            )
            env.bind(stmt.name, binding_type, mutable=True)
        elif isinstance(stmt, ReturnStatement):
            if stmt.value is not None:
                effects |= self._infer_expr_effects(stmt.value, env)
        elif isinstance(stmt, IfStatement):
            effects |= self._infer_expr_effects(stmt.condition, env)
            effects |= self._infer_body_effects(stmt.then_body, env.child())
            if stmt.else_body:
                effects |= self._infer_body_effects(stmt.else_body, env.child())
        elif isinstance(stmt, WhileStatement):
            effects |= self._infer_expr_effects(stmt.condition, env)
            effects |= self._infer_body_effects(stmt.body, env.child())
        elif isinstance(stmt, ForStatement):
            effects |= self._infer_expr_effects(stmt.iterable, env)
            body_env = env.child()
            body_env.bind(stmt.variable, self._resolve_type(stmt.var_type))
            effects |= self._infer_body_effects(stmt.body, body_env)
        elif isinstance(stmt, TryStatement):
            body_effects = self._infer_body_effects(stmt.try_body, env.child())
            # try/catch masks the "throw" effect from the try body
            effects |= body_effects - {"throw"}
            catch_env = env.child()
            catch_env.bind(
                stmt.catch_clause.variable,
                self._resolve_type(stmt.catch_clause.type_annotation),
            )
            effects |= self._infer_body_effects(stmt.catch_clause.body, catch_env)
        elif isinstance(stmt, MatchStatement):
            effects |= self._infer_expr_effects(stmt.scrutinee, env)
            scrutinee_type = self._check_expression(stmt.scrutinee, env)
            for arm in stmt.arms:
                arm_env = env.child()
                self._bind_pattern_effect_env(arm.pattern, scrutinee_type, arm_env)
                if arm.guard is not None:
                    effects |= self._infer_expr_effects(arm.guard, arm_env)
                effects |= self._infer_body_effects(arm.body, arm_env)
        elif isinstance(stmt, AssertStatement):
            effects |= self._infer_expr_effects(stmt.expression, env)
        elif isinstance(stmt, TupleDestructureStatement):
            effects |= self._infer_expr_effects(stmt.value, env)
            tuple_type = self._resolve_type(stmt.type_annotation)
            if isinstance(tuple_type, TupleType):
                for name, element_type in zip(stmt.names, tuple_type.element_types):
                    env.bind(name, element_type, mutable=stmt.mutable)

        return effects

    def _trait_dispatch_effects(
        self, expr: FunctionCall, env: TypeEnv
    ) -> frozenset[str]:
        """Resolve effects for a trait-dispatch call from the first argument type."""
        callee = expr.function
        if not isinstance(callee, Identifier) or callee.name not in self.trait_methods:
            return frozenset()
        if not expr.arguments:
            return frozenset()

        first_arg_type = self._check_expression(expr.arguments[0].value, env)
        if not isinstance(first_arg_type, UserType):
            return frozenset()

        resolved_type_name = self._constructor_to_type.get(
            first_arg_type.name, first_arg_type.name
        )
        effects: set[str] = set()
        for trait_name, _trait_def in self.trait_methods[callee.name]:
            method_types = self.impl_registry.get((trait_name, resolved_type_name))
            if method_types is None or callee.name not in method_types:
                continue
            effects |= method_types[callee.name].effects
        return frozenset(effects)

    def _infer_expr_effects(self, expr: Expression, env: TypeEnv) -> set[str]:
        """Infer effects from an expression."""
        effects: set[str] = set()

        if isinstance(expr, FunctionCall):
            # Infer effects from arguments first
            for arg in expr.arguments:
                effects |= self._infer_expr_effects(arg.value, env)

            # Calling any expression invokes the effects carried by its
            # function type. This includes indexed/computed callees, not only
            # identifiers and inline lambdas.
            callee = expr.function
            effects |= self._infer_expr_effects(callee, env)
            callee_type = self._check_expression(callee, env)
            if isinstance(callee_type, FuncType):
                effects |= callee_type.effects

            if isinstance(callee, Identifier):
                effects |= self._trait_dispatch_effects(expr, env)
            elif isinstance(callee, FieldAccess):
                # Module-qualified call: mod.func(...)
                if isinstance(callee.target, Identifier):
                    mod_exports = self._module_exports.get(callee.target.name)
                    if mod_exports:
                        module_callee_type = mod_exports.get(callee.field_name)
                        if isinstance(module_callee_type, FuncType):
                            effects |= module_callee_type.effects
        elif isinstance(expr, ThrowExpression):
            effects.add("throw")
            effects |= self._infer_expr_effects(expr.value, env)

        elif isinstance(expr, BinaryOp):
            effects |= self._infer_expr_effects(expr.left, env)
            effects |= self._infer_expr_effects(expr.right, env)

        elif isinstance(expr, UnaryOp):
            effects |= self._infer_expr_effects(expr.operand, env)

        elif isinstance(expr, IfStatement):
            # If-expression
            effects |= self._infer_expr_effects(expr.condition, env)
            effects |= self._infer_body_effects(expr.then_body, env)
            if expr.else_body:
                effects |= self._infer_body_effects(expr.else_body, env)

        elif isinstance(expr, MatchExpr):
            effects |= self._infer_expr_effects(expr.scrutinee, env)
            scrutinee_type = self._check_expression(expr.scrutinee, env)
            for arm in expr.arms:
                arm_env = env.child()
                self._bind_pattern_effect_env(arm.pattern, scrutinee_type, arm_env)
                if arm.guard is not None:
                    effects |= self._infer_expr_effects(arm.guard, arm_env)
                effects |= self._infer_body_effects(arm.body, arm_env)

        elif isinstance(expr, Pipeline):
            effects |= self._infer_expr_effects(expr.initial, env)
            for stage in expr.stages:
                effects |= self._infer_expr_effects(stage.function, env)
                callee_type = self._check_expression(stage.function, env)
                if isinstance(callee_type, FuncType):
                    effects |= callee_type.effects
                for stage_arg in stage.arguments:
                    effects |= self._infer_expr_effects(stage_arg, env)

        elif isinstance(expr, ConstructorCall):
            for ctor_arg in expr.arguments:
                effects |= self._infer_expr_effects(ctor_arg, env)

        elif isinstance(expr, FieldAccess):
            effects |= self._infer_expr_effects(expr.target, env)

        elif isinstance(expr, IndexAccess):
            effects |= self._infer_expr_effects(expr.target, env)
            effects |= self._infer_expr_effects(expr.index, env)

        elif isinstance(expr, LambdaExpr):
            # Lambda effects are not propagated to the enclosing function —
            # they only matter when the lambda is actually called
            pass

        elif isinstance(expr, ListLiteral):
            for elem in expr.elements:
                effects |= self._infer_expr_effects(elem, env)

        elif isinstance(expr, ListComprehension):
            effects |= self._infer_expr_effects(expr.iterable, env)
            comp_env = env.child()
            comp_env.bind(expr.variable, self._resolve_type(expr.var_type))
            effects |= self._infer_expr_effects(expr.element_expr, comp_env)
            if expr.condition:
                effects |= self._infer_expr_effects(expr.condition, comp_env)

        elif isinstance(expr, TupleExpr):
            for elem in expr.elements:
                effects |= self._infer_expr_effects(elem, env)

        elif isinstance(expr, FStringExpr):
            for part in expr.parts:
                if not isinstance(part, str):
                    effects |= self._infer_expr_effects(part, env)

        elif isinstance(expr, AwaitExpr):
            effects |= self._infer_expr_effects(expr.expr, env)

        elif isinstance(expr, PropagateExpr):
            effects |= self._infer_expr_effects(expr.operand, env)

        elif isinstance(expr, WithExpr):
            for _field_name, update_expr in expr.updates:
                effects |= self._infer_expr_effects(update_expr, env)
            effects |= self._infer_expr_effects(expr.target, env)

        return effects

    def _definitely_returns(self, stmts: list[Statement]) -> bool:
        """Check if a list of statements definitely returns on all paths."""
        return any(self._stmt_definitely_returns(stmt) for stmt in stmts)

    def _stmt_definitely_returns(self, stmt: Statement) -> bool:
        """Check if a single statement definitely returns."""
        if isinstance(stmt, ReturnStatement):
            return True

        if isinstance(stmt, ExpressionStatement) and isinstance(
            stmt.expression, ThrowExpression
        ):
            return True

        if isinstance(stmt, IfStatement):
            # Both branches must definitely return
            then_returns = self._definitely_returns(stmt.then_body)
            else_returns = self._definitely_returns(stmt.else_body)
            return then_returns and else_returns

        if isinstance(stmt, MatchStatement):
            # All arms must definitely return
            if not stmt.arms:
                return False
            return all(self._definitely_returns(arm.body) for arm in stmt.arms)

        if isinstance(stmt, TryStatement):
            # Both try body and catch body must definitely return
            try_returns = self._definitely_returns(stmt.try_body)
            catch_returns = self._definitely_returns(stmt.catch_clause.body)
            return try_returns and catch_returns

        # While/for loops don't guarantee return (might not execute)
        # Let/var/assign statements don't return
        return False

    def _check_specs(self, defn: FunctionDef, env: TypeEnv) -> None:
        """Type check specification clauses (requires, ensures, examples)."""
        # Check requires clauses - must be Bool
        for req in defn.specs.requires:
            req_type = self._check_expression(req.condition, env)
            if not isinstance(req_type, BoolType):
                self._error(
                    f"Requires clause must be Bool, got {req_type}",
                    req.condition.location,
                )

        # Check ensures clauses - must be Bool, can use 'result' keyword
        ensures_env = env.child()
        ensures_env.bind("result", self.current_return_type)  # type: ignore[arg-type]
        for ens in defn.specs.ensures:
            ens_type = self._check_expression(ens.condition, ensures_env)
            if not isinstance(ens_type, BoolType):
                self._error(
                    f"Ensures clause must be Bool, got {ens_type}",
                    ens.condition.location,
                )

        # Check example clauses - input types must match params, output must match return
        param_types = [self._resolve_type(p.param_type) for p in defn.params]
        return_type = self.current_return_type
        required_count = sum(1 for p in defn.params if p.default_value is None)
        total_count = len(defn.params)

        for ex in defn.specs.examples:
            # Check input expression type
            input_type = self._check_expression(ex.input_expr, self.global_env)

            if total_count == 0:
                if not (
                    isinstance(input_type, UnitType)
                    or (
                        isinstance(input_type, TupleType)
                        and len(input_type.element_types) == 0
                    )
                ):
                    self._error(
                        f"Example expects 0 inputs, got {input_type}",
                        ex.input_expr.location,
                        ErrorCode.TYPE_WRONG_ARITY,
                    )
            elif total_count == 1:
                if required_count == 0 and (
                    isinstance(input_type, UnitType)
                    or (
                        isinstance(input_type, TupleType)
                        and len(input_type.element_types) == 0
                    )
                ):
                    pass
                elif not self._types_strictly_compatible(param_types[0], input_type):
                    self._error(
                        f"Example input type mismatch: expected {param_types[0]}, got {input_type}",
                        ex.input_expr.location,
                    )
            elif total_count > 1:
                # The example may supply any arity between `required_count`
                # and `total_count`; omitted trailing parameters use defaults.
                if isinstance(input_type, TupleType):
                    arity = len(input_type.element_types)
                    if arity < required_count or arity > total_count:
                        self._error(
                            f"Example expects {required_count}..{total_count} "
                            f"inputs, got {arity}",
                            ex.input_expr.location,
                            ErrorCode.TYPE_WRONG_ARITY,
                        )
                    else:
                        for i, (expected, actual) in enumerate(
                            zip(param_types[:arity], input_type.element_types)
                        ):
                            if not self._types_strictly_compatible(expected, actual):
                                self._error(
                                    f"Example input {i + 1} type mismatch: expected {expected}, got {actual}",
                                    ex.input_expr.location,
                                )
                else:
                    # Scalar input: only valid when exactly one positional
                    # argument is expected (i.e., the first parameter is
                    # required and every other parameter has a default).
                    if required_count <= 1 <= total_count:
                        if not self._types_strictly_compatible(
                            param_types[0], input_type
                        ):
                            self._error(
                                f"Example input type mismatch: expected {param_types[0]}, got {input_type}",
                                ex.input_expr.location,
                            )
                    else:
                        self._error(
                            f"Example input for a {total_count}-parameter "
                            f"function (with {required_count} required) must be "
                            f"a tuple, got {input_type}",
                            ex.input_expr.location,
                        )

            # Check output expression type
            output_type = self._check_expression(ex.output_expr, self.global_env)
            if not self._types_strictly_compatible(return_type, output_type):  # type: ignore[arg-type]
                self._error(
                    f"Example output type mismatch: expected {return_type}, got {output_type}",
                    ex.output_expr.location,
                )

    def _check_statement(self, stmt: Statement, env: TypeEnv) -> None:
        """Type check a statement."""
        stmt_type = type(stmt)
        if stmt_type is LetStatement:
            self._check_let_statement(cast(LetStatement, stmt), env)
        elif stmt_type is VarStatement:
            self._check_var_statement(cast(VarStatement, stmt), env)
        elif stmt_type is TupleDestructureStatement:
            self._check_tuple_destructure(cast(TupleDestructureStatement, stmt), env)
        elif stmt_type is AssignStatement:
            self._check_assign_statement(cast(AssignStatement, stmt), env)
        elif stmt_type is IndexAssignStatement:
            self._check_index_assign_statement(cast(IndexAssignStatement, stmt), env)
        elif stmt_type is FieldAssignStatement:
            self._check_field_assign_statement(cast(FieldAssignStatement, stmt), env)
        elif stmt_type is IfStatement:
            self._check_if_statement(cast(IfStatement, stmt), env)
        elif stmt_type is WhileStatement:
            self._check_while_statement(cast(WhileStatement, stmt), env)
        elif stmt_type is ForStatement:
            self._check_for_statement(cast(ForStatement, stmt), env)
        elif stmt_type is MatchStatement:
            self._check_match_statement(cast(MatchStatement, stmt), env)
        elif stmt_type is ReturnStatement:
            self._check_return_statement(cast(ReturnStatement, stmt), env)
        elif stmt_type is BreakStatement:
            if self._loop_depth == 0:
                self._error("'break' outside of loop", stmt.location)
        elif stmt_type is ContinueStatement:
            if self._loop_depth == 0:
                self._error("'continue' outside of loop", stmt.location)
        elif stmt_type is TryStatement:
            self._check_try_statement(cast(TryStatement, stmt), env)
        elif stmt_type is ExpressionStatement:
            expr_stmt = cast(ExpressionStatement, stmt)
            self._check_expression(expr_stmt.expression, env)
        elif stmt_type is AssertStatement:
            assert_stmt = cast(AssertStatement, stmt)
            expr_type = self._check_expression(assert_stmt.expression, env)
            if expr_type != _BOOL_TYPE and not isinstance(expr_type, TypeVar):
                self._error(
                    f"Assert expression must be Bool, got {expr_type}",
                    stmt.location,
                )

    def _check_try_statement(self, stmt: TryStatement, env: TypeEnv) -> None:
        """Type check a try/catch statement."""
        try_env = env.child()
        for s in stmt.try_body:
            self._check_statement(s, try_env)

        catch = stmt.catch_clause
        declared_type = self._resolve_type(catch.type_annotation)
        if not isinstance(declared_type, (StringType, UserType)):
            self._error(
                f"Catch variable must be String or a user-defined type, got {declared_type}",
                catch.location,
            )

        catch_env = env.child()
        catch_env.bind(catch.variable, declared_type)
        for s in catch.body:
            self._check_statement(s, catch_env)

    def _check_let_statement(self, stmt: LetStatement, env: TypeEnv) -> None:
        """Type check a let statement."""
        errors_before = len(self.errors)
        actual_type = self._check_expression(stmt.value, env)

        if stmt.type_annotation is not None:
            declared_type = self._resolve_type(stmt.type_annotation)
            stmt._expected_runtime_type = declared_type
            self._record_expected_runtime_type(stmt.value, declared_type)
            if not self._types_strictly_compatible(declared_type, actual_type):
                self._error(
                    f"Type mismatch in 'let {stmt.name}': declared type {declared_type}, "
                    f"but the value has type {actual_type}",
                    stmt.location,
                )
            env.bind(stmt.name, declared_type, mutable=False)
        else:
            if len(self.errors) == errors_before and (
                self._contains_any(actual_type) or self._has_type_vars(actual_type)
            ):
                self._error(
                    f"Cannot infer a concrete type for 'let {stmt.name}' from "
                    f"{actual_type}; add an explicit type annotation",
                    stmt.location,
                )
            # Type inference: use the type of the RHS expression
            env.bind(stmt.name, actual_type, mutable=False)

    def _check_var_statement(self, stmt: VarStatement, env: TypeEnv) -> None:
        """Type check a var statement."""
        errors_before = len(self.errors)
        actual_type = self._check_expression(stmt.value, env)

        if stmt.type_annotation is not None:
            declared_type = self._resolve_type(stmt.type_annotation)
            stmt._expected_runtime_type = declared_type
            self._record_expected_runtime_type(stmt.value, declared_type)
            if not self._types_strictly_compatible(declared_type, actual_type):
                self._error(
                    f"Type mismatch in 'var {stmt.name}': declared type {declared_type}, "
                    f"but the value has type {actual_type}",
                    stmt.location,
                )
            env.bind(stmt.name, declared_type, mutable=True)
        else:
            if len(self.errors) == errors_before and (
                self._contains_any(actual_type) or self._has_type_vars(actual_type)
            ):
                self._error(
                    f"Cannot infer a concrete type for 'var {stmt.name}' from "
                    f"{actual_type}; add an explicit type annotation",
                    stmt.location,
                )
            # Type inference: use the type of the RHS expression
            env.bind(stmt.name, actual_type, mutable=True)

    def _check_tuple_destructure(
        self, stmt: TupleDestructureStatement, env: TypeEnv
    ) -> None:
        """Type check a tuple destructuring statement."""
        declared_type = self._resolve_type(stmt.type_annotation)
        actual_type = self._check_expression(stmt.value, env)
        self._record_expected_runtime_type(stmt.value, declared_type)

        if not self._types_strictly_compatible(declared_type, actual_type):
            self._error(
                f"Type mismatch in tuple destructuring: declared type {declared_type}, "
                f"but the value has type {actual_type}",
                stmt.location,
            )

        if not isinstance(declared_type, TupleType):
            self._error(
                f"Tuple destructuring requires a tuple type, got {declared_type}",
                stmt.location,
            )
            return

        if len(stmt.names) != len(declared_type.element_types):
            self._error(
                f"Tuple has {len(declared_type.element_types)} elements, "
                f"but destructuring has {len(stmt.names)} names",
                stmt.location,
            )
            return

        for name, elem_type in zip(stmt.names, declared_type.element_types):
            env.bind(name, elem_type, mutable=stmt.mutable)

    def _check_assign_statement(self, stmt: AssignStatement, env: TypeEnv) -> None:
        """Type check an assignment statement."""
        var_type = env.lookup(stmt.target)
        if var_type is None:
            hint = _suggest_name(stmt.target, self._env_names(env))
            self._error(
                f"Undefined variable: {stmt.target}{hint}",
                stmt.location,
                ErrorCode.TYPE_UNDEFINED_VAR,
            )
            return

        if not env.is_mutable(stmt.target):
            self._error(
                f"Cannot assign to immutable variable: {stmt.target}",
                stmt.location,
                ErrorCode.TYPE_IMMUTABLE_ASSIGN,
            )
            return

        value_type = self._check_expression(stmt.value, env)
        stmt._expected_runtime_type = var_type
        self._record_expected_runtime_type(stmt.value, var_type)
        if not self._types_strictly_compatible(var_type, value_type):
            self._error(
                f"Type mismatch in assignment: expected {var_type}, got {value_type}",
                stmt.location,
            )

    def _check_index_assign_statement(
        self, stmt: IndexAssignStatement, env: TypeEnv
    ) -> None:
        """Type check an index assignment: arr[i] = value."""
        target_type = self._check_expression(stmt.target, env)
        index_type = self._check_expression(stmt.index, env)
        value_type = self._check_expression(stmt.value, env)

        root_name = self._assignment_root_name(stmt.target)
        if (
            root_name is not None
            and env.lookup(root_name) is not None
            and not env.is_mutable(root_name)
        ):
            self._error(
                f"Cannot assign index on immutable variable: {root_name}",
                stmt.location,
                ErrorCode.TYPE_IMMUTABLE_ASSIGN,
            )
            return

        if isinstance(target_type, ArrayType):
            if not isinstance(index_type, IntType):
                self._error(f"Array index must be Int, got {index_type}", stmt.location)
            self._record_expected_runtime_type(stmt.value, target_type.element_type)
            if not self._types_strictly_compatible(
                target_type.element_type, value_type
            ):
                self._error(
                    f"Type mismatch: expected {target_type.element_type}, got {value_type}",
                    stmt.location,
                )
        elif isinstance(target_type, VecType):
            if not isinstance(index_type, IntType):
                self._error(f"Vec index must be Int, got {index_type}", stmt.location)
            self._record_expected_runtime_type(stmt.value, target_type.element_type)
            if not self._types_strictly_compatible(
                target_type.element_type, value_type
            ):
                self._error(
                    f"Type mismatch: expected {target_type.element_type}, got {value_type}",
                    stmt.location,
                )
        elif isinstance(target_type, MutableMapType):
            if not self._types_strictly_compatible(target_type.key_type, index_type):
                self._error(
                    f"Map key type mismatch: expected {target_type.key_type}, got {index_type}",
                    stmt.location,
                )
            self._record_expected_runtime_type(stmt.value, target_type.value_type)
            if not self._types_strictly_compatible(target_type.value_type, value_type):
                self._error(
                    f"Map value type mismatch: expected {target_type.value_type}, got {value_type}",
                    stmt.location,
                )
        else:
            self._error(
                f"Cannot use index assignment on {target_type} (only Array, Vec, MutableMap)",
                stmt.location,
            )

    def _check_field_assign_statement(
        self, stmt: FieldAssignStatement, env: TypeEnv
    ) -> None:
        """Type check a field assignment: obj.field = value."""
        target_type = self._check_expression(stmt.target, env)

        if not isinstance(target_type, UserType):
            self._error(
                f"Cannot assign field '{stmt.field_name}' on type {target_type}",
                stmt.location,
            )
            return

        root_name = self._assignment_root_name(stmt.target)
        if (
            root_name is not None
            and env.lookup(root_name) is not None
            and not env.is_mutable(root_name)
        ):
            self._error(
                f"Cannot assign field on immutable variable: {root_name}",
                stmt.location,
                ErrorCode.TYPE_IMMUTABLE_ASSIGN,
            )
            return

        value_type = self._check_expression(stmt.value, env)

        field_types, missing_variants = self._resolved_variant_field_types(
            target_type,
            stmt.field_name,
        )
        variant_count = len(field_types) + len(missing_variants)
        if missing_variants and variant_count > 1:
            self._error(
                f"Field '{stmt.field_name}' does not exist on all variants "
                f"of '{target_type.name}' (missing on: "
                f"{', '.join(missing_variants)}); use pattern matching "
                f"to update variant-specific fields",
                stmt.location,
                ErrorCode.TYPE_UNKNOWN_FIELD,
            )
            return
        if not field_types:
            self._error(
                f"No field '{stmt.field_name}' on type {target_type.name}",
                stmt.location,
                ErrorCode.TYPE_UNKNOWN_FIELD,
            )
            return

        expected_field_type = self._consistent_field_type(
            target_type,
            stmt.field_name,
            field_types,
            stmt.location,
        )
        if expected_field_type is None:
            return

        self._record_expected_runtime_type(stmt.value, expected_field_type)
        if not self._types_strictly_compatible(expected_field_type, value_type):
            self._error(
                f"Type mismatch: field '{stmt.field_name}' expects "
                f"{expected_field_type}, got {value_type}",
                stmt.location,
            )

    def _assignment_root_name(self, expr: Expression) -> str | None:
        """Return the root binding name for chained mutation targets."""
        if isinstance(expr, Identifier):
            return cast(str, expr.name)
        if isinstance(expr, FieldAccess):
            return self._assignment_root_name(expr.target)
        if isinstance(expr, IndexAccess):
            return self._assignment_root_name(expr.target)
        return None

    def _check_if_statement(self, stmt: IfStatement, env: TypeEnv) -> None:
        """Type check an if statement."""
        cond_type = self._check_expression(stmt.condition, env)
        if not isinstance(cond_type, BoolType):
            self._error(f"If condition must be Bool, got {cond_type}", stmt.location)

        then_env = env.child()
        for s in stmt.then_body:
            self._check_statement(s, then_env)

        else_env = env.child()
        for s in stmt.else_body:
            self._check_statement(s, else_env)

    def _check_while_statement(self, stmt: WhileStatement, env: TypeEnv) -> None:
        """Type check a while statement."""
        cond_type = self._check_expression(stmt.condition, env)
        if not isinstance(cond_type, BoolType):
            self._error(f"While condition must be Bool, got {cond_type}", stmt.location)

        body_env = env.child()
        self._loop_depth += 1
        for s in stmt.body:
            self._check_statement(s, body_env)
        self._loop_depth -= 1

    def _check_for_statement(self, stmt: ForStatement, env: TypeEnv) -> None:
        """Type check a for statement."""
        iterable_type = self._check_expression(stmt.iterable, env)

        if not isinstance(iterable_type, (ListType, ArrayType)):
            self._error(
                f"For loop requires List or Array, got {iterable_type}", stmt.location
            )
            elem_type: Type = AnyType()
        else:
            elem_type = iterable_type.element_type

        declared_type = self._resolve_type(stmt.var_type)
        if not self._types_strictly_compatible(declared_type, elem_type):
            self._error(
                f"For variable type {declared_type} doesn't match list element type {elem_type}",
                stmt.location,
            )

        body_env = env.child()
        body_env.bind(stmt.variable, declared_type)

        self._loop_depth += 1
        for s in stmt.body:
            self._check_statement(s, body_env)
        self._loop_depth -= 1

    def _check_match_statement(self, stmt: MatchStatement, env: TypeEnv) -> None:
        """Type check a match statement."""
        scrutinee_type = self._check_expression(stmt.scrutinee, env)

        for arm in stmt.arms:
            arm_env = env.child()
            self._check_pattern(arm.pattern, scrutinee_type, arm_env, arm.location)

            if arm.guard is not None:
                guard_type = self._check_expression(arm.guard, arm_env)
                if not isinstance(guard_type, BoolType):
                    self._error(
                        f"Match guard must be Bool, got {guard_type}", arm.location
                    )

            for s in arm.body:
                self._check_statement(s, arm_env)

        # Check pattern exhaustiveness
        self._check_pattern_exhaustiveness(scrutinee_type, stmt.arms, stmt.location)

    def _check_return_statement(self, stmt: ReturnStatement, env: TypeEnv) -> None:
        """Type check a return statement."""
        if self.current_return_type is None:
            self._error("Return outside of function", stmt.location)
            return

        value_type = self._check_expression(stmt.value, env)
        if self._lambda_return_types is not None:
            # Collecting return types for block lambda inference
            self._lambda_return_types.append(value_type)
            return

        stmt._expected_runtime_type = self.current_return_type
        self._record_expected_runtime_type(stmt.value, self.current_return_type)
        if not self._types_strictly_compatible(self.current_return_type, value_type):
            self._error(
                f"Return type mismatch: expected {self.current_return_type}, got {value_type}",
                stmt.location,
            )

    def _record_expected_runtime_type(self, expr: Expression, expected: Type) -> None:
        """Attach concrete contextual types used by backend lowering."""
        expr._expected_runtime_type = expected

        if isinstance(expr, ListLiteral) and isinstance(expected, ListType):
            for element in expr.elements:
                self._record_expected_runtime_type(element, expected.element_type)
            return

        if isinstance(expr, TupleExpr) and isinstance(expected, TupleType):
            for element, element_type in zip(expr.elements, expected.element_types):
                self._record_expected_runtime_type(element, element_type)
            return

        if isinstance(expr, ListComprehension) and isinstance(expected, ListType):
            self._record_expected_runtime_type(expr.element_expr, expected.element_type)
            return

        if isinstance(expr, FunctionCall):
            function_type = getattr(expr.function, "_resolved_type", None)
            output_type = getattr(expr, "_resolved_type", None)
            if isinstance(function_type, FuncType) and isinstance(output_type, Type):
                substitutions: dict[str, Type] = {}
                if self._types_compatible_with_subs(
                    output_type, expected, substitutions
                ):
                    for call_argument in expr.arguments:
                        argument_expected = getattr(
                            call_argument.value, "_expected_runtime_type", None
                        )
                        if isinstance(argument_expected, Type):
                            self._record_expected_runtime_type(
                                call_argument.value,
                                self._apply_substitutions(
                                    argument_expected, substitutions
                                ),
                            )
            return
        if isinstance(expr, MatchExpr):
            for arm in expr.arms:
                if len(arm.body) == 1 and isinstance(arm.body[0], ReturnStatement):
                    self._record_expected_runtime_type(arm.body[0].value, expected)
            return

        if isinstance(expr, ConstructorCall):
            if isinstance(expected, OptionType) and expr.constructor == "Some":
                if expr.arguments:
                    self._record_expected_runtime_type(
                        expr.arguments[0], expected.value_type
                    )
                return
            if isinstance(expected, ResultType) and expr.arguments:
                if expr.constructor == "Ok":
                    self._record_expected_runtime_type(
                        expr.arguments[0], expected.ok_type
                    )
                elif expr.constructor == "Err":
                    self._record_expected_runtime_type(
                        expr.arguments[0], expected.err_type
                    )
                return
            if isinstance(expected, UserType):
                type_info = self.type_defs.get(expected.name)
                if type_info is None:
                    return
                fields = type_info.variants.get(expr.constructor)
                if fields is None:
                    return
                substitutions = dict(zip(type_info.type_params, expected.type_args))
                for argument, (_name, field_type) in zip(expr.arguments, fields):
                    self._record_expected_runtime_type(
                        argument,
                        self._apply_substitutions(field_type, substitutions),
                    )
            return

        if isinstance(expr, WithExpr) and isinstance(expected, UserType):
            type_info = self.type_defs.get(expected.name)
            if type_info is None:
                return
            substitutions = dict(zip(type_info.type_params, expected.type_args))
            field_types = {
                name: self._apply_substitutions(field_type, substitutions)
                for fields in type_info.variants.values()
                for name, field_type in fields
            }
            for field_name, value in expr.updates:
                update_field_type = field_types.get(field_name)
                if update_field_type is not None:
                    self._record_expected_runtime_type(value, update_field_type)

    def _check_expression(self, expr: Expression, env: TypeEnv) -> Type:
        """Type check an expression and return its type."""
        result = self._check_expression_inner(expr, env)
        expr._resolved_type = result
        return result

    def _check_expression_inner(self, expr: Expression, env: TypeEnv) -> Type:
        """Inner dispatch for expression type checking."""
        expr_type = type(expr)
        if expr_type is IntegerLiteral:
            return _INT_TYPE
        elif expr_type is FloatLiteral:
            return _FLOAT_TYPE
        elif expr_type is StringLiteral:
            return _STRING_TYPE
        elif expr_type is FStringExpr:
            fstring_expr = cast(FStringExpr, expr)
            for part in fstring_expr.parts:
                if not isinstance(part, str):
                    self._check_expression(part, env)
            return _STRING_TYPE
        elif expr_type is BooleanLiteral:
            return _BOOL_TYPE
        elif expr_type is Identifier:
            return self._check_identifier(cast(Identifier, expr), env)
        elif expr_type is TypeIdentifier:
            return self._check_type_identifier(cast(TypeIdentifier, expr), env)
        elif expr_type is ListLiteral:
            return self._check_list_literal(cast(ListLiteral, expr), env)
        elif expr_type is BinaryOp:
            return self._check_binary_op(cast(BinaryOp, expr), env)
        elif expr_type is UnaryOp:
            return self._check_unary_op(cast(UnaryOp, expr), env)
        elif expr_type is FunctionCall:
            return self._check_function_call(cast(FunctionCall, expr), env)
        elif expr_type is IndexAccess:
            return self._check_index_access(cast(IndexAccess, expr), env)
        elif expr_type is FieldAccess:
            return self._check_field_access(cast(FieldAccess, expr), env)
        elif expr_type is Pipeline:
            return self._check_pipeline(cast(Pipeline, expr), env)
        elif expr_type is LambdaExpr:
            return self._check_lambda(cast(LambdaExpr, expr), env)
        elif expr_type is ConstructorCall:
            return self._check_constructor_call(cast(ConstructorCall, expr), env)
        elif expr_type is TupleExpr:
            return self._check_tuple_expr(cast(TupleExpr, expr), env)
        elif expr_type is MatchExpr:
            return self._check_match_expr(cast(MatchExpr, expr), env)
        elif expr_type is TypedHole:
            typed_hole = cast(TypedHole, expr)
            return self._resolve_type(typed_hole.hole_type)
        elif expr_type is WithExpr:
            return self._check_with_expr(cast(WithExpr, expr), env)
        elif expr_type is PropagateExpr:
            return self._check_propagate_expr(cast(PropagateExpr, expr), env)
        elif expr_type is ListComprehension:
            return self._check_list_comprehension(cast(ListComprehension, expr), env)
        elif expr_type is ThrowExpression:
            return self._check_throw_expression(cast(ThrowExpression, expr), env)
        elif expr_type is AwaitExpr:
            return self._check_await_expr(cast(AwaitExpr, expr), env)
        else:
            self._error(
                f"Unknown expression type: {type(expr).__name__}", expr.location
            )
            return _ANY_TYPE

    @staticmethod
    def _env_names(env: TypeEnv) -> set[str]:
        """Collect all names visible in the type environment chain."""
        names: set[str] = set()
        cur: TypeEnv | None = env
        while cur is not None:
            names.update(cur.bindings.keys())
            cur = cur.parent
        return names

    def _all_constructors(self) -> set[str]:
        """Collect all known constructor names."""
        names = set(self._constructor_to_type.keys())
        names.update(self._module_exports.keys())
        return names

    def _check_identifier(self, expr: Identifier, env: TypeEnv) -> Type:
        """Type check an identifier."""
        type_ = env.lookup(expr.name)
        if type_ is None:
            if expr.name in self._target_rejected:
                self._error(self._target_rejected[expr.name], expr.location)
                return AnyType()
            hint = _suggest_name(expr.name, self._env_names(env))
            self._error(
                f"Undefined variable: {expr.name}{hint}",
                expr.location,
                ErrorCode.TYPE_UNDEFINED_VAR,
            )
            return AnyType()
        if self._identifier_resolves_to_builtin(expr.name, env):
            expr._resolved_builtin_name = expr.name
        return type_

    def _identifier_resolves_to_builtin(self, name: str, env: TypeEnv) -> bool:
        """Return True when an identifier still refers to a builtin function."""
        if name not in self.builtin_types or name in self._user_defined_names:
            return False

        scope: TypeEnv | None = env
        while scope is not None and scope is not self.global_env:
            if name in scope.bindings:
                return False
            scope = scope.parent

        return name in self.global_env.bindings

    def _check_type_identifier(self, expr: TypeIdentifier, env: TypeEnv) -> Type:
        """Type check a type identifier (constructor with no args or module ref)."""
        # Check if it's a nullary constructor (O(1) lookup via reverse index)
        type_name = self._constructor_to_type.get(expr.name)
        if type_name is not None:
            type_info = self.type_defs[type_name]
            fields = type_info.variants[expr.name]
            if len(fields) == 0:
                # Return the parent type (e.g., Option for None). For a
                # generic ADT, emit a fresh TypeVar per parameter so later
                # unification at a call/assignment site can bind them —
                # using AnyType here would silently unify with any
                # instantiation.
                if type_info.type_params:
                    return UserType(
                        type_name,
                        tuple(self._fresh_type_var(p) for p in type_info.type_params),
                    )
                return UserType(type_name)

        # Check if it's a module namespace (for qualified access)
        if expr.name in self._module_exports:
            return ModuleType(expr.name)

        hint = _suggest_name(expr.name, self._all_constructors())
        self._error(f"Unknown constructor: {expr.name}{hint}", expr.location)
        return _ANY_TYPE

    def _check_list_literal(self, expr: ListLiteral, env: TypeEnv) -> Type:
        """Type check a list literal."""
        if not expr.elements:
            # Empty list: use a fresh TypeVar for the element so a later
            # unification (call-site or let/var with explicit annotation)
            # binds it. Using AnyType here would silently unify with any
            # element type and launder through generic identity calls.
            return ListType(self._fresh_type_var("elem"))

        elem_types = [self._check_expression(e, env) for e in expr.elements]
        if any(isinstance(t, NeverType) for t in elem_types):
            return NeverType()
        first_type = elem_types[0]

        for i, t in enumerate(elem_types[1:], 1):
            if not self._types_compatible(first_type, t):
                self._error(
                    f"List element type mismatch: expected {first_type}, got {t}",
                    expr.elements[i].location,
                )

        return ListType(first_type)

    def _check_binary_op(self, expr: BinaryOp, env: TypeEnv) -> Type:
        """Type check a binary operation."""
        left_type = self._check_expression(expr.left, env)
        right_type = self._check_expression(expr.right, env)

        # Bitwise operators (Int only)
        if expr.operator in ("&", "^", "<<", ">>"):
            if not isinstance(left_type, IntType):
                self._error(
                    f"Left operand of '{expr.operator}' must be Int, got {left_type}",
                    expr.location,
                )
            if not isinstance(right_type, IntType):
                self._error(
                    f"Right operand of '{expr.operator}' must be Int, got {right_type}",
                    expr.location,
                )
            return _INT_TYPE

        # Exponentiation
        if expr.operator == "**":
            if isinstance(left_type, IntType) and isinstance(right_type, IntType):
                return _INT_TYPE
            elif isinstance(left_type, (IntType, FloatType)) and isinstance(
                right_type, (IntType, FloatType)
            ):
                return _FLOAT_TYPE
            else:
                self._error(
                    f"Cannot apply '**' to {left_type} and {right_type}: "
                    f"both operands must be numeric (Int or Float)",
                    expr.location,
                )
                return _ANY_TYPE

        # Arithmetic operators
        if expr.operator in ("+", "-", "*", "/", "%"):
            if (
                expr.operator == "+"
                and isinstance(left_type, StringType)
                and isinstance(right_type, StringType)
            ):
                return _STRING_TYPE
            if isinstance(left_type, IntType) and isinstance(right_type, IntType):
                return _INT_TYPE
            elif (
                isinstance(left_type, FloatType) and isinstance(right_type, FloatType)
            ) or (
                isinstance(left_type, (IntType, FloatType))
                and isinstance(right_type, (IntType, FloatType))
            ):
                return _FLOAT_TYPE
            else:
                self._error(
                    f"Cannot apply '{expr.operator}' to {left_type} and {right_type}: "
                    f"both operands must be numeric (Int or Float)"
                    f"{' or both String for concatenation' if expr.operator == '+' else ''}",
                    expr.location,
                )
                return _ANY_TYPE

        # Equality operators
        if expr.operator in ("==", "!="):
            if not (
                self._types_compatible(left_type, right_type)
                or self._types_compatible(right_type, left_type)
            ):
                self._error(
                    f"Cannot compare {left_type} with {right_type}", expr.location
                )
            return _BOOL_TYPE

        # Ordering operators
        if expr.operator in ("<", ">", "<=", ">="):
            if not self._types_orderable(left_type, right_type):
                self._error(
                    f"Cannot apply '{expr.operator}' to {left_type} and {right_type}: "
                    "operands must both be numeric (Int or Float) or both String",
                    expr.location,
                )
            return _BOOL_TYPE

        # Logical operators
        if expr.operator in ("and", "or"):
            if not isinstance(left_type, BoolType):
                self._error(
                    f"Left operand of '{expr.operator}' must be Bool, got {left_type}",
                    expr.location,
                )
            if not isinstance(right_type, BoolType):
                self._error(
                    f"Right operand of '{expr.operator}' must be Bool, got {right_type}",
                    expr.location,
                )
            return _BOOL_TYPE

        self._error(f"Unknown operator: {expr.operator}", expr.location)
        return _ANY_TYPE

    def _types_orderable(self, left_type: Type, right_type: Type) -> bool:
        """Return whether the pair supports Geno ordering operators."""
        if isinstance(left_type, AnyType) or isinstance(right_type, AnyType):
            return True
        if isinstance(left_type, (IntType, FloatType)) and isinstance(
            right_type, (IntType, FloatType)
        ):
            return True
        return isinstance(left_type, StringType) and isinstance(right_type, StringType)

    def _check_unary_op(self, expr: UnaryOp, env: TypeEnv) -> Type:
        """Type check a unary operation."""
        operand_type = self._check_expression(expr.operand, env)

        if expr.operator == "-":
            if isinstance(operand_type, IntType):
                return _INT_TYPE
            elif isinstance(operand_type, FloatType):
                return _FLOAT_TYPE
            else:
                self._error(
                    f"Cannot negate type {operand_type}: "
                    f"the '-' operator requires Int or Float",
                    expr.location,
                )
                return _ANY_TYPE

        if expr.operator == "not":
            if not isinstance(operand_type, BoolType):
                self._error(f"'not' requires Bool, got {operand_type}", expr.location)
            return _BOOL_TYPE

        if expr.operator == "~":
            if not isinstance(operand_type, IntType):
                self._error(f"'~' requires Int, got {operand_type}", expr.location)
            return _INT_TYPE

        self._error(f"Unknown unary operator: {expr.operator}", expr.location)
        return _ANY_TYPE

    def _check_function_call(self, expr: FunctionCall, env: TypeEnv) -> Type:
        """Type check a function call."""
        func_expr = expr.function
        func_expr_type = type(func_expr)
        identifier_func = (
            cast(Identifier, func_expr) if func_expr_type is Identifier else None
        )
        arguments = expr.arguments
        builtin_name = None
        if identifier_func is not None and self._identifier_resolves_to_builtin(
            identifier_func.name, env
        ):
            builtin_name = identifier_func.name
            expr._resolved_builtin_name = builtin_name

        # Special case: range accepts 2 or 3 arguments
        if builtin_name == "range" and len(arguments) in (2, 3):
            if any(arg.name is not None for arg in arguments):
                self._validate_special_builtin_named_args(
                    arguments, ["start", "end"], expr.location
                )
            for arg in arguments:
                arg_type = self._check_expression(arg.value, env)
                if not isinstance(arg_type, IntType):
                    self._error(
                        f"range expects Int arguments, got {arg_type}",
                        arg.value.location,
                    )
            return ListType(_INT_TYPE)

        if builtin_name == "length" and len(arguments) == 1:
            self._validate_special_builtin_named_args(
                arguments, ["list"], expr.location
            )
            arg_type = self._check_expression(arguments[0].value, env)
            if not isinstance(arg_type, (ListType, StringType, ArrayType)):
                self._error(
                    f"Argument 1 type mismatch: expected List[T], String, or Array[T], got {arg_type}",
                    arguments[0].value.location,
                )
            return _INT_TYPE

        # Trait method dispatch: resolve based on first argument's concrete type
        if (
            identifier_func is not None
            and identifier_func.name in self.trait_methods
            and arguments
        ):
            method_name = identifier_func.name
            has_named_args = any(arg.name for arg in arguments)
            for trait_name, _trait_def in self.trait_methods[method_name]:
                trait_param_names = (
                    self._trait_method_param_names(trait_name, method_name) or []
                )
                if has_named_args:
                    ordered_trait_args = self._try_reorder_call_args(
                        arguments, trait_param_names
                    )
                    if ordered_trait_args is None:
                        continue
                else:
                    ordered_trait_args = list(arguments)

                if not ordered_trait_args or ordered_trait_args[0] is None:
                    continue

                first_arg_type = self._check_expression(
                    ordered_trait_args[0].value, env
                )

                # Find the concrete type name from the first argument
                resolved_type_name: str | None = None
                if isinstance(first_arg_type, UserType):
                    resolved_type_name = first_arg_type.name
                    # If this is a constructor name, find its parent type
                    parent = self._constructor_to_type.get(resolved_type_name)
                    if parent is not None:
                        resolved_type_name = parent

                if resolved_type_name is None:
                    continue

                key = (trait_name, resolved_type_name)
                if (
                    key not in self.impl_registry
                    or method_name not in self.impl_registry[key]
                ):
                    continue

                impl_func_type = self.impl_registry[key][method_name]
                positional_count = sum(1 for arg in arguments if arg.name is None)
                if len(impl_func_type.param_types) >= 3 and positional_count > 0:
                    self._error(
                        f"Function '{method_name}' has {len(impl_func_type.param_types)} parameters; use named arguments for clarity (e.g., param_name: value)",
                        expr.location,
                    )

                if len(arguments) != len(impl_func_type.param_types):
                    self._error(
                        f"'{method_name}' expects {len(impl_func_type.param_types)} argument(s), but {len(arguments)} were provided",
                        expr.location,
                        ErrorCode.TYPE_WRONG_ARITY,
                    )
                for i, expected_type in enumerate(impl_func_type.param_types):
                    call_arg: CallArg | None = (
                        ordered_trait_args[i] if i < len(ordered_trait_args) else None
                    )
                    if call_arg is None:
                        self._error(
                            f"Missing argument for parameter '{trait_param_names[i]}'",
                            expr.location,
                            ErrorCode.TYPE_WRONG_ARITY,
                        )
                        continue
                    arg_type = self._check_expression(call_arg.value, env)
                    self._record_expected_runtime_type(call_arg.value, expected_type)
                    if not self._types_compatible(expected_type, arg_type):
                        self._error(
                            f"Argument {i + 1} type mismatch: expected {expected_type}, got {arg_type}",
                            call_arg.value.location,
                        )
                return impl_func_type.return_type

        if (
            identifier_func is not None
            and env.lookup(identifier_func.name) is None
            and identifier_func.name not in self._target_rejected
        ):
            hint = _suggest_name(identifier_func.name, self._env_names(env))
            self._error(
                f"Undefined function: {identifier_func.name}{hint}",
                expr.location,
                ErrorCode.TYPE_UNDEFINED_FUNC,
            )
            return _ANY_TYPE

        func_type = self._check_expression(func_expr, env)

        if not isinstance(func_type, FuncType):
            self._error(
                f"Cannot call non-function type: {func_type}",
                expr.location,
                ErrorCode.TYPE_NOT_CALLABLE,
            )
            return _ANY_TYPE

        # Check argument count (with default parameter support)
        call_param_info = resolve_call_parameter_info(
            func_expr,
            self.func_param_names,
            self._module_param_names,
            self.func_default_counts,
            self._module_default_counts,
        )
        func_name_for_defaults = call_param_info.default_lookup_name
        num_defaults = call_param_info.default_count
        min_args = len(func_type.param_types) - num_defaults
        max_args = len(func_type.param_types)

        if not (min_args <= len(arguments) <= max_args):
            callee_desc = (
                f"'{func_name_for_defaults}'" if func_name_for_defaults else "function"
            )
            if min_args == max_args:
                self._error(
                    f"{callee_desc} expects {max_args} argument(s), "
                    f"but {len(arguments)} were provided",
                    expr.location,
                    ErrorCode.TYPE_WRONG_ARITY,
                )
            else:
                self._error(
                    f"{callee_desc} expects {min_args} to {max_args} arguments, "
                    f"but {len(arguments)} were provided",
                    expr.location,
                    ErrorCode.TYPE_WRONG_ARITY,
                )

        # Get parameter names if available (for named argument support)
        param_names = list(call_param_info.param_names)

        # For LLM-friendliness: require named arguments for functions with 3+ required parameters
        required_param_count = max(len(arguments), min_args)
        if required_param_count >= 3:
            positional_count = sum(1 for arg in arguments if arg.name is None)
            if positional_count > 0:
                func_name = (
                    identifier_func.name if identifier_func is not None else "function"
                )
                self._error(
                    f"Function '{func_name}' has {len(func_type.param_types)} parameters; "
                    f"use named arguments for clarity (e.g., param_name: value)",
                    expr.location,
                )

        # Handle named arguments by reordering to match parameter order
        has_named_args = any(arg.name for arg in arguments)

        # Named arguments are only supported for direct function calls (identifiers)
        # where we know the parameter names. For lambdas/function values, we can't
        # reliably determine parameter names at compile time.
        if has_named_args and not param_names:
            self._error(
                "Named arguments are only supported for direct function calls, "
                "not for lambda expressions or function values",
                expr.location,
            )

        # Determine whether we need TypeVar substitution tracking.
        # When the signature contains TypeVars, we collect bindings so that
        # e.g. append([1,2,3], "oops") is rejected (T bound to Int, then
        # String doesn't match).
        use_subs = any(
            self._has_type_vars(pt) for pt in func_type.param_types
        ) or self._has_type_vars(func_type.return_type)
        subs: dict[str, Type] = {}
        has_never_arg = False

        if has_named_args and param_names:
            ordered_args = self._reorder_call_args(
                arguments, param_names, func_type.param_types, expr.location
            )
            for i in range(min_args):
                if ordered_args[i] is None:
                    self._error(
                        f"Missing argument for parameter '{param_names[i]}'",
                        expr.location,
                        ErrorCode.TYPE_WRONG_ARITY,
                    )
            for i, (maybe_arg, expected_type) in enumerate(
                zip(ordered_args, func_type.param_types)
            ):
                if maybe_arg is not None:
                    ca = maybe_arg
                    arg_type = self._check_expression(ca.value, env)
                    has_never_arg = has_never_arg or isinstance(arg_type, NeverType)
                    if self._has_type_vars(arg_type):
                        arg_type = self._freshen_type_vars(arg_type, f"__arg{i}_")
                    if use_subs:
                        if not self._types_compatible_with_subs(
                            expected_type, arg_type, subs
                        ):
                            resolved = self._apply_substitutions(expected_type, subs)
                            self._error(
                                f"Argument '{param_names[i]}' type mismatch: expected {resolved}, got {arg_type}",
                                ca.value.location,
                            )
                    else:
                        if not self._types_compatible(expected_type, arg_type):
                            self._error(
                                f"Argument '{param_names[i]}' type mismatch: expected {expected_type}, got {arg_type}",
                                ca.value.location,
                            )
                    self._record_expected_runtime_type(
                        ca.value,
                        self._apply_substitutions(expected_type, subs),
                    )
        else:
            # Check argument types positionally
            for i, (arg, expected_type) in enumerate(
                zip(arguments, func_type.param_types)
            ):
                arg_type = self._check_expression(arg.value, env)
                has_never_arg = has_never_arg or isinstance(arg_type, NeverType)
                if self._has_type_vars(arg_type):
                    arg_type = self._freshen_type_vars(arg_type, f"__arg{i}_")
                if use_subs:
                    if not self._types_compatible_with_subs(
                        expected_type, arg_type, subs
                    ):
                        resolved = self._apply_substitutions(expected_type, subs)
                        self._error(
                            f"Argument {i + 1} type mismatch: expected {resolved}, got {arg_type}",
                            arg.value.location,
                        )
                else:
                    if not self._types_compatible(expected_type, arg_type):
                        self._error(
                            f"Argument {i + 1} type mismatch: expected {expected_type}, got {arg_type}",
                            arg.value.location,
                        )
                self._record_expected_runtime_type(
                    arg.value,
                    self._apply_substitutions(expected_type, subs),
                )

        if has_never_arg:
            return NeverType()

        # Apply substitutions to the return type for a concrete result.
        if use_subs and subs:
            result_type = self._apply_substitutions(func_type.return_type, subs)
        else:
            result_type = func_type.return_type
        self._validate_keyed_collection_type(result_type, expr.location)
        return result_type

    def _validate_special_builtin_named_args(
        self,
        call_args: list[CallArg],
        param_names: list[str],
        location: SourceLocation,
    ) -> None:
        """Validate names for builtin calls that bypass normal call checking."""
        used_positions: set[int] = set()
        positional_index = 0

        for arg in call_args:
            if arg.name is not None:
                if arg.name not in param_names:
                    self._error(f"Unknown parameter name: {arg.name}", location)
                    continue
                pos = param_names.index(arg.name)
                if pos in used_positions:
                    self._error(
                        f"Duplicate argument for parameter: {arg.name}", location
                    )
                    continue
                used_positions.add(pos)
            else:
                while positional_index in used_positions:
                    positional_index += 1
                if positional_index >= len(param_names):
                    self._error("Too many positional arguments", location)
                    continue
                used_positions.add(positional_index)
                positional_index += 1

    def _reorder_call_args(
        self,
        call_args: list[CallArg],
        param_names: list[str],
        param_types: tuple[Type, ...],
        location: SourceLocation,
    ) -> list[CallArg | None]:
        """Reorder call arguments to match parameter order."""
        result: list[CallArg | None] = [None] * len(param_names)
        used_positions: set[int] = set()
        positional_index = 0

        for arg in call_args:
            if arg.name is not None:
                # Named argument
                if arg.name not in param_names:
                    self._error(f"Unknown parameter name: {arg.name}", location)
                    continue
                pos = param_names.index(arg.name)
                if pos in used_positions:
                    self._error(
                        f"Duplicate argument for parameter: {arg.name}", location
                    )
                    continue
                result[pos] = arg
                used_positions.add(pos)
            else:
                # Positional argument
                while positional_index in used_positions:
                    positional_index += 1
                if positional_index < len(param_names):
                    result[positional_index] = arg
                    used_positions.add(positional_index)
                    positional_index += 1

        return result

    def _try_reorder_call_args(
        self, call_args: list[CallArg], param_names: list[str]
    ) -> list[CallArg | None] | None:
        """Best-effort reordering used while selecting overloaded trait methods."""
        result: list[CallArg | None] = [None] * len(param_names)
        used_positions: set[int] = set()
        positional_index = 0

        for arg in call_args:
            if arg.name is not None:
                if arg.name not in param_names:
                    return None
                pos = param_names.index(arg.name)
                if pos in used_positions:
                    return None
                result[pos] = arg
                used_positions.add(pos)
            else:
                while positional_index in used_positions:
                    positional_index += 1
                if positional_index >= len(param_names):
                    return None
                result[positional_index] = arg
                used_positions.add(positional_index)
                positional_index += 1

        return result

    def _check_index_access(self, expr: IndexAccess, env: TypeEnv) -> Type:
        """Type check index access."""
        target_type = self._check_expression(expr.target, env)
        index_type = self._check_expression(expr.index, env)

        if isinstance(target_type, ListType):
            if not isinstance(index_type, IntType):
                self._error(f"List index must be Int, got {index_type}", expr.location)
            return target_type.element_type
        elif isinstance(target_type, ArrayType):
            if not isinstance(index_type, IntType):
                self._error(f"Array index must be Int, got {index_type}", expr.location)
            return target_type.element_type
        elif isinstance(target_type, StringType):
            if not isinstance(index_type, IntType):
                self._error(
                    f"String index must be Int, got {index_type}", expr.location
                )
            return StringType()
        elif isinstance(target_type, MapType):
            if not self._types_compatible(target_type.key_type, index_type):
                self._error(
                    f"Map key type mismatch: expected {target_type.key_type}, got {index_type}",
                    expr.location,
                )
            return target_type.value_type
        else:
            self._error(f"Cannot index into {target_type}", expr.location)
            return AnyType()

    def _check_field_access(self, expr: FieldAccess, env: TypeEnv) -> Type:
        """Type check field access.

        For multi-variant types, the field must exist on ALL variants
        to be safely accessible without pattern matching.
        Handles qualified module access (Foo.symbol) when target is a module.
        """
        target_type = self._check_expression(expr.target, env)

        # Qualified module access: Foo.symbol
        if isinstance(target_type, ModuleType):
            # Find the namespace (could be alias or module name)
            from .ast_nodes import Identifier

            ns_name = (
                expr.target.name
                if isinstance(expr.target, Identifier)
                else target_type.module_name
            )
            if ns_name in self._module_exports:
                sym_type = self._module_exports[ns_name].get(expr.field_name)
                if sym_type is not None:
                    return sym_type
            self._error(
                f"Module '{ns_name}' has no exported symbol '{expr.field_name}'",
                expr.location,
                ErrorCode.TYPE_UNKNOWN_FIELD,
            )
            return AnyType()

        if isinstance(target_type, UserType):
            field_types, missing_variants = self._resolved_variant_field_types(
                target_type,
                expr.field_name,
            )
            variant_count = len(field_types) + len(missing_variants)
            if missing_variants and variant_count > 1:
                self._error(
                    f"Field '{expr.field_name}' does not exist on all variants "
                    f"of '{target_type.name}' (missing on: "
                    f"{', '.join(missing_variants)}); use pattern matching "
                    f"to access variant-specific fields",
                    expr.location,
                    ErrorCode.TYPE_UNKNOWN_FIELD,
                )
                return AnyType()
            if field_types:
                field_type = self._consistent_field_type(
                    target_type,
                    expr.field_name,
                    field_types,
                    expr.location,
                )
                if field_type is not None:
                    return field_type

        self._error(
            f"Unknown field '{expr.field_name}' on type {target_type}",
            expr.location,
            ErrorCode.TYPE_UNKNOWN_FIELD,
        )
        return AnyType()

    def _resolved_variant_field_types(
        self, target_type: UserType, field_name: str
    ) -> tuple[list[tuple[str, Type]], list[str]]:
        """Return resolved field types per variant plus any missing variants."""
        type_info = self.type_defs.get(target_type.name)
        if type_info is None:
            return [], []

        substitutions = {
            param: arg
            for param, arg in zip(type_info.type_params, target_type.type_args)
        }
        field_types: list[tuple[str, Type]] = []
        missing_variants: list[str] = []

        for variant_name, variant_fields in type_info.variants.items():
            for candidate_name, candidate_type in variant_fields:
                if candidate_name == field_name:
                    field_types.append(
                        (
                            variant_name,
                            self._apply_substitutions(candidate_type, substitutions),
                        )
                    )
                    break
            else:
                missing_variants.append(variant_name)

        return field_types, missing_variants

    def _consistent_field_type(
        self,
        target_type: UserType,
        field_name: str,
        field_types: list[tuple[str, Type]],
        location: SourceLocation,
    ) -> Type | None:
        """Return the shared field type when every variant agrees on it."""
        first_variant, expected_type = field_types[0]
        mismatched = [
            (variant_name, variant_type)
            for variant_name, variant_type in field_types[1:]
            if variant_type != expected_type
        ]
        if not mismatched:
            return expected_type

        details = ", ".join(
            [f"{first_variant}: {expected_type}"]
            + [
                f"{variant_name}: {variant_type}"
                for variant_name, variant_type in mismatched
            ]
        )
        self._error(
            f"Field '{field_name}' has inconsistent types across variants "
            f"of '{target_type.name}' ({details}); use pattern matching "
            f"to access variant-specific fields",
            location,
        )
        return None

    def _check_propagate_expr(self, expr: PropagateExpr, env: TypeEnv) -> Type:
        """Type check the ? propagation operator."""
        operand_type = self._check_expression(expr.operand, env)

        if self.current_return_type is None:
            self._error("'?' operator used outside of a function", expr.location)
            return AnyType()

        if isinstance(operand_type, OptionType):
            if not isinstance(self.current_return_type, OptionType):
                self._error(
                    f"'?' on {operand_type} requires enclosing function "
                    f"to return Option type, but it returns {self.current_return_type}",
                    expr.location,
                )
            return operand_type.value_type

        elif isinstance(operand_type, ResultType):
            if not isinstance(self.current_return_type, ResultType):
                self._error(
                    f"'?' on {operand_type} requires enclosing function "
                    f"to return Result type, but it returns {self.current_return_type}",
                    expr.location,
                )
            elif not self._types_strictly_compatible(
                self.current_return_type.err_type, operand_type.err_type
            ):
                self._error(
                    f"Error type mismatch in '?': function returns "
                    f"Result[_, {self.current_return_type.err_type}] "
                    f"but '?' propagates {operand_type.err_type}",
                    expr.location,
                )
            return operand_type.ok_type

        else:
            self._error(
                f"'?' operator requires Option or Result type, got {operand_type}",
                expr.location,
            )
            return AnyType()

    def _check_with_expr(self, expr: WithExpr, env: TypeEnv) -> Type:
        """Type check a with expression: expr with (field: val, ...)"""
        target_type = self._check_expression(expr.target, env)

        if not isinstance(target_type, UserType):
            self._error(
                f"'with' expression requires a user-defined type, got {target_type}",
                expr.location,
            )
            return AnyType()

        type_info = self.type_defs.get(target_type.name)
        if not type_info:
            hint = _suggest_name(target_type.name, self.type_defs.keys())
            self._error(
                f"Unknown type '{target_type.name}' in 'with' expression{hint}",
                expr.location,
            )
            return AnyType()

        # Only allow `with` on single-variant types where fields are unambiguous
        if len(type_info.variants) != 1:
            self._error(
                f"'with' expression requires a single-variant type, "
                f"but '{target_type.name}' has {len(type_info.variants)} variants",
                expr.location,
            )
            return AnyType()

        substitutions = {
            param: arg
            for param, arg in zip(type_info.type_params, target_type.type_args)
        }
        all_fields: dict[str, Type] = {}
        for variant_fields in type_info.variants.values():
            for field_name, field_type in variant_fields:
                all_fields[field_name] = self._apply_substitutions(
                    field_type, substitutions
                )

        for field_name, value_expr in expr.updates:
            if field_name not in all_fields:
                self._error(
                    f"Unknown field '{field_name}' on type {target_type}",
                    value_expr.location,
                    ErrorCode.TYPE_UNKNOWN_FIELD,
                )
                continue
            value_type = self._check_expression(value_expr, env)
            expected_type = all_fields[field_name]
            self._record_expected_runtime_type(value_expr, expected_type)
            if not self._types_strictly_compatible(expected_type, value_type):
                self._error(
                    f"Field '{field_name}' expects {expected_type}, got {value_type}",
                    value_expr.location,
                )

        return target_type

    def _check_list_comprehension(self, expr: ListComprehension, env: TypeEnv) -> Type:
        """Type check a list comprehension: [expr for var: Type in iterable if cond]."""
        iterable_type = self._check_expression(expr.iterable, env)
        if not isinstance(iterable_type, ListType):
            self._error(
                f"List comprehension requires List iterable, got {iterable_type}",
                expr.iterable.location,
            )
            elem_type: Type = AnyType()
        else:
            elem_type = iterable_type.element_type

        # Bind loop variable in a child env
        comp_env = env.child()
        var_type = self._resolve_type(expr.var_type)
        if not self._types_strictly_compatible(var_type, elem_type):
            self._error(
                f"Loop variable type mismatch: declared {var_type}, iterable has {elem_type}",
                expr.iterable.location,
            )
        comp_env.bind(expr.variable, var_type)

        # Check condition if present
        if expr.condition is not None:
            cond_type = self._check_expression(expr.condition, comp_env)
            if not isinstance(cond_type, BoolType):
                self._error(
                    f"Comprehension condition must be Bool, got {cond_type}",
                    expr.condition.location,
                )

        # Check element expression
        result_type = self._check_expression(expr.element_expr, comp_env)
        return ListType(result_type)

    def _check_throw_expression(self, expr: ThrowExpression, env: TypeEnv) -> Type:
        """Type check a throw expression."""
        value_type = self._check_expression(expr.value, env)
        if not isinstance(value_type, (StringType, UserType)):
            self._error(
                f"throw requires String or user-defined type, got {value_type}",
                expr.value.location,
            )
        return NeverType()

    def _check_await_expr(self, expr: AwaitExpr, env: TypeEnv) -> Type:
        """Type check an await expression."""
        if not self._in_async_function and not self._in_main_function:
            self._error(
                "'await' can only be used inside an async function or main",
                expr.location,
            )
        inner_type = self._check_expression(expr.expr, env)
        if isinstance(inner_type, AsyncType):
            return inner_type.result_type
        # Permit TypeVar (unresolved at this stage) and AnyType (already in
        # an error state) so we don't stack cascading errors on top of a
        # prior one.
        if isinstance(inner_type, (TypeVar, AnyType)):
            return inner_type
        self._error(
            f"'await' expects an Async[T] value, got {inner_type}",
            expr.location,
        )
        return AnyType()

    def _check_pipeline(self, expr: Pipeline, env: TypeEnv) -> Type:
        """Type check a pipeline expression."""
        current_type = self._check_expression(expr.initial, env)

        for stage in expr.stages:
            func_type = self._check_expression(stage.function, env)
            if not isinstance(func_type, FuncType):
                self._error(
                    f"Pipeline stage must be function, got {func_type}", stage.location
                )
                continue

            if len(func_type.param_types) == 0:
                self._error(
                    "Pipeline function must take at least one argument", stage.location
                )
                continue

            use_subs = any(
                self._has_type_vars(pt) for pt in func_type.param_types
            ) or self._has_type_vars(func_type.return_type)
            subs: dict[str, Type] = {}

            placeholder_positions = {
                i
                for i, arg_expr in enumerate(stage.arguments)
                if isinstance(arg_expr, PlaceholderExpr)
            }

            # Build expected argument count based on whether placeholders
            # consume the piped value or whether it is prepended.
            if placeholder_positions:
                expected_arg_count = len(stage.arguments)
            else:
                expected_arg_count = len(stage.arguments) + 1

            if expected_arg_count != len(func_type.param_types):
                self._error(
                    f"Pipeline stage expects {len(func_type.param_types)} arguments, got {expected_arg_count}",
                    stage.location,
                    ErrorCode.TYPE_WRONG_ARITY,
                )
                current_type = func_type.return_type
                continue

            if placeholder_positions:
                for param_index in sorted(placeholder_positions):
                    expected_piped_type = func_type.param_types[param_index]
                    if use_subs:
                        if not self._types_compatible_with_subs(
                            expected_piped_type, current_type, subs
                        ):
                            resolved = self._apply_substitutions(
                                expected_piped_type, subs
                            )
                            self._error(
                                f"Pipeline type mismatch: function expects {resolved}, got {current_type}",
                                stage.location,
                            )
                    elif not self._types_compatible(expected_piped_type, current_type):
                        self._error(
                            f"Pipeline type mismatch: function expects {expected_piped_type}, got {current_type}",
                            stage.location,
                        )
            else:
                expected_piped_type = func_type.param_types[0]
                if use_subs:
                    if not self._types_compatible_with_subs(
                        expected_piped_type, current_type, subs
                    ):
                        resolved = self._apply_substitutions(expected_piped_type, subs)
                        self._error(
                            f"Pipeline type mismatch: function expects {resolved}, got {current_type}",
                            stage.location,
                        )
                elif not self._types_compatible(expected_piped_type, current_type):
                    self._error(
                        f"Pipeline type mismatch: function expects {expected_piped_type}, got {current_type}",
                        stage.location,
                    )

            for i, arg_expr in enumerate(stage.arguments):
                if i in placeholder_positions:
                    continue

                param_index = i if placeholder_positions else i + 1
                arg_type = self._check_expression(arg_expr, env)
                if self._has_type_vars(arg_type):
                    arg_type = self._freshen_type_vars(
                        arg_type, f"__pipe{param_index}_"
                    )
                expected_type = func_type.param_types[param_index]
                if use_subs:
                    if not self._types_compatible_with_subs(
                        expected_type, arg_type, subs
                    ):
                        resolved = self._apply_substitutions(expected_type, subs)
                        self._error(
                            f"Pipeline argument type mismatch: expected {resolved}, got {arg_type}",
                            stage.location,
                        )
                elif not self._types_compatible(expected_type, arg_type):
                    self._error(
                        f"Pipeline argument type mismatch: expected {expected_type}, got {arg_type}",
                        stage.location,
                    )

            current_type = (
                self._apply_substitutions(func_type.return_type, subs)
                if use_subs and subs
                else func_type.return_type
            )

        return current_type

    def _check_lambda(self, expr: LambdaExpr, env: TypeEnv) -> Type:
        """Type check a lambda expression (expression or block form)."""
        lambda_env = env.child()
        param_types: list[Type] = []

        for param in expr.params:
            param_type = self._resolve_type(param.param_type)
            param_types.append(param_type)
            lambda_env.bind(param.name, param_type)

        if expr.block_body is not None:
            # Block lambda: type-check body, collecting return types
            old_return_type = self.current_return_type
            old_collecting = self._lambda_return_types

            # Use a dummy return type so _check_return_statement doesn't
            # error with "Return outside of function", and collect actual types
            self.current_return_type = UnitType()
            self._lambda_return_types = []

            for stmt in expr.block_body:
                self._check_statement(stmt, lambda_env)

            return_types = self._lambda_return_types
            self._lambda_return_types = old_collecting
            self.current_return_type = old_return_type

            if not return_types:
                return_type: Type = UnitType()
            else:
                return_type = return_types[0]
                for rt in return_types[1:]:
                    if isinstance(return_type, NeverType):
                        return_type = rt
                        continue
                    if isinstance(rt, NeverType):
                        continue
                    if not self._types_strictly_compatible(return_type, rt):
                        self._error(
                            f"Inconsistent return types in block lambda: "
                            f"{return_type} vs {rt}",
                            expr.location,
                        )
                if not self._definitely_returns(expr.block_body):
                    self._error(
                        "Block lambda may not return a value on all paths",
                        expr.location,
                    )

            lambda_effects = self._infer_body_effects(expr.block_body, lambda_env)
            return FuncType(tuple(param_types), return_type, lambda_effects)
        else:
            assert expr.body is not None
            body_type = self._check_expression(expr.body, lambda_env)
            lambda_effects = frozenset(self._infer_expr_effects(expr.body, lambda_env))
            return FuncType(tuple(param_types), body_type, lambda_effects)

    def _check_constructor_call(self, expr: ConstructorCall, env: TypeEnv) -> Type:
        """Type check a constructor call."""
        type_name = self._constructor_to_type.get(expr.constructor)
        if type_name is not None:
            type_info = self.type_defs[type_name]
            fields = type_info.variants[expr.constructor]

            if len(expr.arguments) != len(fields):
                self._error(
                    f"Constructor {expr.constructor} expects {len(fields)} arguments, got {len(expr.arguments)}",
                    expr.location,
                    ErrorCode.TYPE_WRONG_ARITY,
                )

            # Infer type parameters from arguments
            type_param_bindings: dict[str, Type] = {}
            has_never_arg = False
            for i, (arg, (_field_name, field_type)) in enumerate(
                zip(expr.arguments, fields)
            ):
                arg_type = self._check_expression(arg, env)
                has_never_arg = has_never_arg or isinstance(arg_type, NeverType)
                if self._has_type_vars(arg_type):
                    arg_type = self._freshen_type_vars(arg_type, f"__ctor_arg{i}_")

                if self._has_type_vars(field_type):
                    if not self._types_compatible_with_subs(
                        field_type, arg_type, type_param_bindings
                    ):
                        expected_type = self._apply_substitutions(
                            field_type, type_param_bindings
                        )
                        self._error(
                            f"Constructor argument {i + 1} type mismatch: "
                            f"expected {expected_type}, got {arg_type}",
                            arg.location,
                        )
                elif not self._types_strictly_compatible(field_type, arg_type):
                    self._error(
                        f"Constructor argument {i + 1} type mismatch: "
                        f"expected {field_type}, got {arg_type}",
                        arg.location,
                    )
                self._record_expected_runtime_type(
                    arg,
                    self._apply_substitutions(field_type, type_param_bindings),
                )

            if has_never_arg:
                return NeverType()

            # Type parameters that weren't bound from arguments (phantom
            # params, constructors with zero positional args, etc.) get a
            # fresh TypeVar rather than AnyType. A fresh TypeVar still lets
            # later unification at a call/assignment site bind it, but unlike
            # AnyType it does not silently coerce to an unrelated concrete
            # type downstream. Mirrors the nullary-constructor path in
            # ``_check_type_identifier`` and the empty-list path in
            # ``_check_list_literal``.
            def _bind_or_fresh(param: str) -> Type:
                bound = type_param_bindings.get(param)
                return bound if bound is not None else self._fresh_type_var(param)

            # Special handling for built-in Option type
            if type_name == "Option":
                return OptionType(_bind_or_fresh("T"))

            # Special handling for built-in Result type
            if type_name == "Result":
                return ResultType(_bind_or_fresh("T"), _bind_or_fresh("E"))

            if type_info.type_params:
                # Return type with inferred type parameters
                inferred_params = tuple(
                    _bind_or_fresh(param) for param in type_info.type_params
                )
                return UserType(type_name, inferred_params)
            return UserType(type_name)

        hint = _suggest_name(expr.constructor, self._all_constructors())
        self._error(f"Unknown constructor: {expr.constructor}{hint}", expr.location)
        return AnyType()

    def _check_tuple_expr(self, expr: TupleExpr, env: TypeEnv) -> Type:
        """Type check a tuple expression."""
        if not expr.elements:
            return UnitType()

        elem_types = [self._check_expression(e, env) for e in expr.elements]
        if any(isinstance(t, NeverType) for t in elem_types):
            return NeverType()
        return TupleType(tuple(elem_types))

    def _check_match_expr(self, expr: MatchExpr, env: TypeEnv) -> Type:
        """Type check a match expression."""
        scrutinee_type = self._check_expression(expr.scrutinee, env)

        result_type: Type | None = None
        for arm in expr.arms:
            arm_env = env.child()
            self._check_pattern(arm.pattern, scrutinee_type, arm_env, arm.location)

            if arm.guard is not None:
                guard_type = self._check_expression(arm.guard, arm_env)
                if not isinstance(guard_type, BoolType):
                    self._error(
                        f"Match guard must be Bool, got {guard_type}", arm.location
                    )

            # Parser-produced match expressions lower each arm expression to
            # exactly one ReturnStatement. Reject malformed constructed ASTs
            # instead of silently ignoring statements.
            if len(arm.body) != 1 or not isinstance(arm.body[0], ReturnStatement):
                self._error(
                    "Match expression arm body must be exactly one return statement",
                    arm.location,
                )
                continue

            return_stmt = arm.body[0]
            arm_type = self._check_expression(return_stmt.value, arm_env)
            if result_type is None or isinstance(result_type, NeverType):
                result_type = arm_type
            elif isinstance(arm_type, NeverType):
                continue
            else:
                merged_type = self._merge_match_expr_result_types(result_type, arm_type)
                if merged_type is None:
                    self._error(
                        f"Match arm type mismatch: expected {result_type}, got {arm_type}",
                        arm.location,
                    )
                else:
                    result_type = merged_type

        # Check pattern exhaustiveness
        self._check_pattern_exhaustiveness(scrutinee_type, expr.arms, expr.location)

        return result_type if result_type else AnyType()

    def _merge_match_expr_result_types(self, left: Type, right: Type) -> Type | None:
        """Return an order-independent result type for compatible match arms."""
        if self._types_strictly_compatible(left, right):
            return left
        if self._types_strictly_compatible(right, left):
            return right
        return None

    def _bind_pattern_effect_env(
        self,
        pattern: Pattern,
        expected_type: Type,
        env: TypeEnv,
    ) -> None:
        """Bind pattern variables for effect inference after typechecking succeeds."""
        if isinstance(pattern, WildcardPattern):
            return
        if isinstance(pattern, VariablePattern):
            env.bind(pattern.name, expected_type)
            return
        if isinstance(pattern, LiteralPattern):
            return
        if isinstance(pattern, RestPattern):
            if pattern.name is not None:
                env.bind(pattern.name, expected_type)
            return
        if isinstance(pattern, ListPattern):
            if not isinstance(expected_type, ListType):
                return
            for elem_pattern in pattern.elements:
                if isinstance(elem_pattern, RestPattern):
                    if elem_pattern.name is not None:
                        env.bind(elem_pattern.name, expected_type)
                else:
                    self._bind_pattern_effect_env(
                        elem_pattern,
                        expected_type.element_type,
                        env,
                    )
            return
        if not isinstance(pattern, ConstructorPattern):
            return

        type_info: TypeDefInfo | None = None
        if isinstance(expected_type, UserType):
            type_info = self.type_defs.get(expected_type.name)
        elif isinstance(expected_type, OptionType):
            type_info = self.type_defs.get("Option")
        elif isinstance(expected_type, ResultType):
            type_info = self.type_defs.get("Result")

        if type_info is None or pattern.constructor not in type_info.variants:
            return

        type_param_map: dict[str, Type] = {}
        if isinstance(expected_type, OptionType) and type_info.type_params:
            type_param_map["T"] = expected_type.value_type
        elif isinstance(expected_type, ResultType) and type_info.type_params:
            type_param_map["T"] = expected_type.ok_type
            type_param_map["E"] = expected_type.err_type
        elif isinstance(expected_type, UserType) and expected_type.type_args:
            for param, arg in zip(type_info.type_params, expected_type.type_args):
                type_param_map[param] = arg

        fields = type_info.variants[pattern.constructor]
        for subpat, (_field_name, field_type) in zip(pattern.subpatterns, fields):
            self._bind_pattern_effect_env(
                subpat,
                self._substitute_type_vars(field_type, type_param_map),
                env,
            )

    def _check_pattern(
        self,
        pattern: Pattern,
        expected_type: Type,
        env: TypeEnv,
        location: SourceLocation,
    ) -> None:
        """Type check a pattern and bind variables."""
        if isinstance(pattern, WildcardPattern):
            pass
        elif isinstance(pattern, VariablePattern):
            env.bind(pattern.name, expected_type)
        elif isinstance(pattern, LiteralPattern):
            literal_type = self._literal_type(pattern.value)
            if not self._types_strictly_compatible(expected_type, literal_type):
                self._error(
                    f"Pattern type mismatch: expected {expected_type}, got {literal_type}",
                    pattern.location,
                    ErrorCode.TYPE_PATTERN_MISMATCH,
                )
        elif isinstance(pattern, ConstructorPattern):
            type_name: str | None = None
            type_info: TypeDefInfo | None = None

            if isinstance(expected_type, UserType):
                type_name = expected_type.name
                type_info = self.type_defs.get(type_name)
            elif isinstance(expected_type, OptionType):
                type_name = "Option"
                type_info = self.type_defs.get(type_name)
            elif isinstance(expected_type, ResultType):
                type_name = "Result"
                type_info = self.type_defs.get(type_name)

            if type_info is None:
                self._error(
                    f"Constructor pattern {pattern.constructor} is not valid for type {expected_type}",
                    pattern.location,
                    ErrorCode.TYPE_PATTERN_MISMATCH,
                )
                return

            if pattern.constructor not in type_info.variants:
                self._error(
                    f"Constructor {pattern.constructor} does not belong to type {type_name}",
                    pattern.location,
                    ErrorCode.TYPE_PATTERN_MISMATCH,
                )
                return

            # Build type parameter substitution map
            type_param_map: dict[str, Type] = {}
            if isinstance(expected_type, OptionType) and type_info.type_params:
                type_param_map["T"] = expected_type.value_type
            elif isinstance(expected_type, ResultType) and type_info.type_params:
                type_param_map["T"] = expected_type.ok_type
                type_param_map["E"] = expected_type.err_type
            elif isinstance(expected_type, UserType) and expected_type.type_args:
                for param, arg in zip(type_info.type_params, expected_type.type_args):
                    type_param_map[param] = arg

            fields = type_info.variants[pattern.constructor]
            if len(pattern.subpatterns) != len(fields):
                self._error(
                    f"Constructor {pattern.constructor} expects {len(fields)} fields, got {len(pattern.subpatterns)}",
                    pattern.location,
                    ErrorCode.TYPE_PATTERN_MISMATCH,
                )
            for subpat, (_field_name, field_type) in zip(pattern.subpatterns, fields):
                resolved = self._substitute_type_vars(field_type, type_param_map)
                self._check_pattern(subpat, resolved, env, pattern.location)
        elif isinstance(pattern, ListPattern):
            if isinstance(expected_type, ListType):
                for elem_pattern in pattern.elements:
                    if isinstance(elem_pattern, RestPattern):
                        # Rest pattern binds to List[T]
                        if elem_pattern.name is not None:
                            env.bind(elem_pattern.name, expected_type)
                    else:
                        self._check_pattern(
                            elem_pattern,
                            expected_type.element_type,
                            env,
                            pattern.location,
                        )
            else:
                self._error(
                    f"List pattern used with non-list type: {expected_type}",
                    pattern.location,
                    ErrorCode.TYPE_PATTERN_MISMATCH,
                )
        elif isinstance(pattern, RestPattern):
            # Rest pattern at top level (shouldn't happen, but handle gracefully)
            if pattern.name is not None:
                env.bind(pattern.name, expected_type)

    def _literal_type(self, value) -> Type:
        """Get the type of a literal value."""
        if isinstance(value, bool):
            return BoolType()
        elif isinstance(value, int):
            return IntType()
        elif isinstance(value, float):
            return FloatType()
        elif isinstance(value, str):
            return StringType()
        return AnyType()

    def _resolve_type(
        self, type_annot: TypeAnnotation, type_params: list[str] | None = None
    ) -> Type:
        """Resolve a type annotation to an internal type."""
        cache_key = (
            id(type_annot),
            tuple(type_params) if type_params is not None else (),
        )
        cached = self._resolved_type_cache.get(cache_key)
        if cached is not None:
            return cached
        if type_params is None:
            type_params = []

        if isinstance(type_annot, SimpleType):
            name = type_annot.name

            # Check if it's a type parameter
            if name in type_params:
                if type_annot.type_params:
                    raise TypeError(
                        f"Type parameter '{name}' does not take type arguments",
                        type_annot.location,
                    )
                resolved: Type = TypeVar(name)
                self._resolved_type_cache[cache_key] = resolved
                return resolved

            if name == "Self":
                if self._trait_self_type_depth == 0:
                    raise TypeError(
                        "Type 'Self' is only valid in trait method signatures",
                        type_annot.location,
                    )
                if type_annot.type_params:
                    raise TypeError(
                        "Type 'Self' does not take type parameters",
                        type_annot.location,
                    )
                resolved = UserType("Self")
                self._resolved_type_cache[cache_key] = resolved
                return resolved

            # Built-in types
            if name in _BUILTIN_SIMPLE_TYPES:
                if type_annot.type_params:
                    raise TypeError(
                        f"Type '{name}' does not take type parameters",
                        type_annot.location,
                    )
                resolved = _BUILTIN_SIMPLE_TYPES[name]
                self._resolved_type_cache[cache_key] = resolved
                return resolved

            # Parameterized built-in types
            builtin_arity = _PARAMETERIZED_BUILTIN_ARITY.get(name)
            if (
                builtin_arity is not None
                and len(type_annot.type_params) != builtin_arity
            ):
                self._raise_type_arity_error(
                    name,
                    builtin_arity,
                    len(type_annot.type_params),
                    type_annot.location,
                )
            if name == "List" and len(type_annot.type_params) == 1:
                elem_type = self._resolve_type(type_annot.type_params[0], type_params)
                resolved = ListType(elem_type)
                self._resolved_type_cache[cache_key] = resolved
                return resolved
            if name == "Array" and len(type_annot.type_params) == 1:
                elem_type = self._resolve_type(type_annot.type_params[0], type_params)
                resolved = ArrayType(elem_type)
                self._resolved_type_cache[cache_key] = resolved
                return resolved
            if name == "Option" and len(type_annot.type_params) == 1:
                value_type = self._resolve_type(type_annot.type_params[0], type_params)
                resolved = OptionType(value_type)
                self._resolved_type_cache[cache_key] = resolved
                return resolved
            if name == "Result" and len(type_annot.type_params) == 2:
                ok_type = self._resolve_type(type_annot.type_params[0], type_params)
                err_type = self._resolve_type(type_annot.type_params[1], type_params)
                resolved = ResultType(ok_type, err_type)
                self._resolved_type_cache[cache_key] = resolved
                return resolved
            if name == "Map" and len(type_annot.type_params) == 2:
                key_type = self._resolve_type(type_annot.type_params[0], type_params)
                value_type = self._resolve_type(type_annot.type_params[1], type_params)
                resolved = MapType(key_type, value_type)
                self._validate_hashable_key_type(
                    "Map", "key", key_type, type_annot.location
                )
                self._resolved_type_cache[cache_key] = resolved
                return resolved
            if name == "MutableMap" and len(type_annot.type_params) == 2:
                key_type = self._resolve_type(type_annot.type_params[0], type_params)
                value_type = self._resolve_type(type_annot.type_params[1], type_params)
                resolved = MutableMapType(key_type, value_type)
                self._validate_hashable_key_type(
                    "MutableMap", "key", key_type, type_annot.location
                )
                self._resolved_type_cache[cache_key] = resolved
                return resolved
            if name == "Vec" and len(type_annot.type_params) == 1:
                elem_type = self._resolve_type(type_annot.type_params[0], type_params)
                resolved = VecType(elem_type)
                self._resolved_type_cache[cache_key] = resolved
                return resolved
            if name == "Set" and len(type_annot.type_params) == 1:
                elem_type = self._resolve_type(type_annot.type_params[0], type_params)
                resolved = SetType(elem_type)
                self._validate_hashable_key_type(
                    "Set", "element", elem_type, type_annot.location
                )
                self._resolved_type_cache[cache_key] = resolved
                return resolved
            if name == "Async" and len(type_annot.type_params) == 1:
                result_type = self._resolve_type(type_annot.type_params[0], type_params)
                resolved = AsyncType(result_type)
                self._resolved_type_cache[cache_key] = resolved
                return resolved

            # Tuple types.  The parser desugars ``(T1, T2, ...)`` to the
            # canonical name ``Tuple``; there is no ``Tuple2`` / ``Tuple3``
            # surface.  Using ``startswith`` previously let a
            # user-defined type such as ``TupleFoo[T]`` be silently
            # classified as a builtin tuple — F-0033 in #665.  Require
            # an exact match on the canonical name.
            if name == "Tuple" and type_annot.type_params:
                elem_types = tuple(
                    self._resolve_type(t, type_params) for t in type_annot.type_params
                )
                resolved = TupleType(elem_types)
                self._resolved_type_cache[cache_key] = resolved
                return resolved

            # Type aliases — resolve transparently
            if name in self.type_aliases:
                if name in self._resolving_aliases:
                    raise TypeError(
                        f"Recursive type alias '{name}'",
                        type_annot.location,
                    )
                self._resolving_aliases.add(name)
                try:
                    alias_params, alias_target = self.type_aliases[name]
                    if alias_params:
                        if not type_annot.type_params:
                            raise TypeError(
                                f"Type alias '{name}' expects "
                                f"{len(alias_params)} type parameter(s) "
                                f"but none were provided",
                                type_annot.location,
                                ErrorCode.TYPE_WRONG_ARITY,
                            )
                        if len(alias_params) != len(type_annot.type_params):
                            raise TypeError(
                                f"Type alias '{name}' expects "
                                f"{len(alias_params)} type parameter(s) "
                                f"but got {len(type_annot.type_params)}",
                                type_annot.location,
                                ErrorCode.TYPE_WRONG_ARITY,
                            )
                        sub_params = list(type_params or [])
                        for ap, _arg in zip(alias_params, type_annot.type_params):
                            sub_params.append(ap)
                        resolved = self._resolve_type(alias_target, sub_params)
                        subs: dict[str, Type] = {}
                        for ap, arg in zip(alias_params, type_annot.type_params):
                            subs[ap] = self._resolve_type(arg, type_params)
                        resolved = self._apply_substitutions(resolved, subs)
                        self._validate_keyed_collection_type(
                            resolved, type_annot.location
                        )
                        self._resolved_type_cache[cache_key] = resolved
                        return resolved
                    resolved = self._resolve_type(alias_target, type_params)
                    self._validate_keyed_collection_type(resolved, type_annot.location)
                    self._resolved_type_cache[cache_key] = resolved
                    return resolved
                finally:
                    self._resolving_aliases.discard(name)

            # User-defined types
            declared_type_params = self._declared_user_type_params(name)
            if declared_type_params is not None:
                if len(type_annot.type_params) != len(declared_type_params):
                    self._raise_type_arity_error(
                        name,
                        len(declared_type_params),
                        len(type_annot.type_params),
                        type_annot.location,
                    )
                type_args = tuple(
                    self._resolve_type(t, type_params) for t in type_annot.type_params
                )
                resolved = UserType(name, type_args)
                self._resolved_type_cache[cache_key] = resolved
                return resolved

            known_types = (
                set(_BUILTIN_SIMPLE_TYPES)
                | set(_PARAMETERIZED_BUILTIN_ARITY)
                | set(self.type_aliases)
                | set(self.type_defs)
            )
            for declared in self._declared_user_types:
                known_types.update(declared)
            hint = _suggest_name(name, known_types)
            raise TypeError(
                f"Unknown type: '{name}'{hint}",
                type_annot.location,
                ErrorCode.TYPE_UNDEFINED_TYPE,
            )

        elif isinstance(type_annot, FunctionType):
            param_types = tuple(
                self._resolve_type(t, type_params) for t in type_annot.param_types
            )
            return_type = self._resolve_type(type_annot.return_type, type_params)
            effects = (
                frozenset(type_annot.effects) if type_annot.effects else frozenset()
            )
            resolved = FuncType(param_types, return_type, effects)
            self._resolved_type_cache[cache_key] = resolved
            return resolved

        self._resolved_type_cache[cache_key] = _ANY_TYPE
        return _ANY_TYPE

    def _substitute_type_vars(self, t: Type, subs: dict[str, Type]) -> Type:
        """Replace TypeVars in a type according to a substitution map."""
        if isinstance(t, TypeVar):
            return subs.get(t.name, t)
        return map_type(t, lambda c: self._substitute_type_vars(c, subs))

    def _types_compatible(self, expected: Type, actual: Type) -> bool:
        """Call-site compatibility, allowing TypeVars to behave as wildcards."""
        return self._types_structurally_compatible(
            expected, actual, allow_typevar_wildcards=True
        )

    def _types_strictly_compatible(self, expected: Type, actual: Type) -> bool:
        """Compatibility for declarations/returns/assignments without TypeVar wildcards."""
        if self._types_structurally_compatible(
            expected, actual, allow_typevar_wildcards=False
        ):
            return True

        # Declarations and return annotations should still accept valid
        # monomorphic instantiations of polymorphic values, such as:
        #   let f: (Int) -> String = to_string
        if self._has_type_vars(actual) and not self._has_type_vars(expected):
            return self._types_compatible_with_subs(
                expected,
                self._freshen_type_vars(actual, "__actual_"),
                {},
            )

        return False

    def _types_structurally_compatible(
        self,
        expected: Type,
        actual: Type,
        *,
        allow_typevar_wildcards: bool,
        in_recovery: bool = True,
    ) -> bool:
        """Structural type compatibility with configurable TypeVar handling.

        ``in_recovery`` controls whether ``AnyType`` is treated as universally
        compatible.  The current default (``True``) preserves the long-standing
        cascade-suppression behaviour — an expression that already produced a
        diagnostic and fell back to ``AnyType`` does not generate a second
        error on downstream checks.  A future hardening pass can tighten this
        by passing ``in_recovery=False`` at strategic call sites.  Introducing
        the flag here (and in ``_types_compatible_with_subs``) gives that work
        a single gated seam rather than having to audit every producer.
        """
        if isinstance(expected, AnyType) or isinstance(actual, AnyType):
            return in_recovery
        # NeverType (bottom) is assignable to any type — a divergent
        # expression can appear wherever a value is expected.
        if isinstance(actual, NeverType):
            return True
        if isinstance(expected, TypeVar) or isinstance(actual, TypeVar):
            if allow_typevar_wildcards:
                return True
            return bool(expected == actual)

        def matches(expected_inner: Type, actual_inner: Type) -> bool:
            return self._types_structurally_compatible(
                expected_inner,
                actual_inner,
                allow_typevar_wildcards=allow_typevar_wildcards,
                in_recovery=in_recovery,
            )

        def matches_invariant(expected_inner: Type, actual_inner: Type) -> bool:
            """Mutual compatibility check for mutable-container element
            types, where covariant Int→Float promotion would be unsound
            (the same slot is used for reads and writes).
            """
            return matches(expected_inner, actual_inner) and matches(
                actual_inner, expected_inner
            )

        # Same type class
        if type(expected) != type(actual):
            # Int/Float coercion
            return isinstance(expected, FloatType) and isinstance(actual, IntType)

        # Recursive checks for compound types
        if isinstance(expected, ListType) and isinstance(actual, ListType):
            return matches(expected.element_type, actual.element_type)

        if isinstance(expected, ArrayType) and isinstance(actual, ArrayType):
            # Mutable: invariant in element type.
            return matches_invariant(expected.element_type, actual.element_type)

        if isinstance(expected, TupleType) and isinstance(actual, TupleType):
            if len(expected.element_types) != len(actual.element_types):
                return False
            return all(
                matches(e, a)
                for e, a in zip(expected.element_types, actual.element_types)
            )

        if isinstance(expected, FuncType) and isinstance(actual, FuncType):
            if len(expected.param_types) != len(actual.param_types):
                return False
            # Contravariant in parameters, covariant in return
            params_ok = all(
                matches(a, e)  # Note: reversed for contravariance
                for e, a in zip(expected.param_types, actual.param_types)
            )
            ret_ok = matches(expected.return_type, actual.return_type)
            # Effect subtyping: actual effects must be a subset of expected effects
            # (a function that does less is acceptable where more is expected)
            effects_ok = actual.effects <= expected.effects
            return params_ok and ret_ok and effects_ok

        if isinstance(expected, UserType) and isinstance(actual, UserType):
            if expected.name != actual.name:
                return False
            if len(expected.type_args) != len(actual.type_args):
                return False
            type_info = self.type_defs.get(expected.name)
            inv = type_info.invariant_params if type_info else frozenset()
            return all(
                matches_invariant(e, a) if i in inv else matches(e, a)
                for i, (e, a) in enumerate(zip(expected.type_args, actual.type_args))
            )

        if isinstance(expected, OptionType) and isinstance(actual, OptionType):
            return matches(expected.value_type, actual.value_type)

        if isinstance(expected, ResultType) and isinstance(actual, ResultType):
            return matches(expected.ok_type, actual.ok_type) and matches(
                expected.err_type, actual.err_type
            )

        if isinstance(expected, MapType) and isinstance(actual, MapType):
            return matches(expected.key_type, actual.key_type) and matches(
                expected.value_type, actual.value_type
            )

        if isinstance(expected, MutableMapType) and isinstance(actual, MutableMapType):
            # Mutable: invariant in key and value types.
            return matches_invariant(
                expected.key_type, actual.key_type
            ) and matches_invariant(expected.value_type, actual.value_type)

        if isinstance(expected, VecType) and isinstance(actual, VecType):
            # Mutable: invariant in element type.
            return matches_invariant(expected.element_type, actual.element_type)

        if isinstance(expected, SetType) and isinstance(actual, SetType):
            # Mutable: invariant in element type.
            return matches_invariant(expected.element_type, actual.element_type)

        if isinstance(expected, AsyncType) and isinstance(actual, AsyncType):
            return matches(expected.result_type, actual.result_type)

        # Primitive types (IntType, FloatType, BoolType, StringType, UnitType)
        # are singletons — same class means same type.
        # Unknown type pair — reject rather than silently accepting
        return type(expected) in (IntType, FloatType, BoolType, StringType, UnitType)

    # -----------------------------------------------------------------
    # TypeVar substitution tracking for generic function calls
    # -----------------------------------------------------------------

    @staticmethod
    def _has_type_vars(t: Type) -> bool:
        """Return True if the type contains any TypeVar."""
        if isinstance(t, TypeVar):
            return True
        return cast(bool, any_child(t, TypeChecker._has_type_vars))

    @staticmethod
    def _contains_any(t: Type) -> bool:
        """Return True if the type contains Any anywhere inside it."""
        if isinstance(t, AnyType):
            return True
        return cast(bool, any_child(t, TypeChecker._contains_any))

    @staticmethod
    def _apply_substitutions(
        t: Type, subs: dict[str, Type], seen: set[str] | None = None
    ) -> Type:
        """Replace TypeVars in *t* using the substitution map."""
        if isinstance(t, TypeVar):
            replacement = subs.get(t.name)
            if replacement is None:
                return t
            if seen is not None and t.name in seen:
                return t
            next_seen = {t.name} if seen is None else seen | {t.name}
            return TypeChecker._apply_substitutions(replacement, subs, next_seen)
        return map_type(t, lambda c: TypeChecker._apply_substitutions(c, subs, seen))

    @staticmethod
    def _occurs_in(name: str, t: Type) -> bool:
        """Return True if the TypeVar *name* appears anywhere inside *t*."""
        if isinstance(t, TypeVar):
            return cast(bool, t.name == name)
        return cast(bool, any_child(t, lambda c: TypeChecker._occurs_in(name, c)))

    def _fresh_type_var(self, hint: str = "T") -> TypeVar:
        """Generate a unique fresh TypeVar for inference placeholders.

        Used when a type parameter needs to be introduced without a
        user-provided name (empty list literal, nullary constructor of a
        generic ADT, etc.). The result is later unified at the call site.
        """
        self._fresh_tv_counter += 1
        return TypeVar(f"__fresh_{hint}_{self._fresh_tv_counter}")

    @staticmethod
    def _freshen_type_vars(
        t: Type, prefix: str, mapping: dict[str, TypeVar] | None = None
    ) -> Type:
        """Rename TypeVars in *t* so they don't collide with outer substitutions."""
        if mapping is None:
            mapping = {}
        if isinstance(t, TypeVar):
            if t.name not in mapping:
                mapping[t.name] = TypeVar(f"{prefix}{t.name}")
            return mapping[t.name]
        return map_type(t, lambda c: TypeChecker._freshen_type_vars(c, prefix, mapping))

    @staticmethod
    def _typevar_accepts(type_var: TypeVar, actual: Type) -> bool:
        if type_var.name in _NUMERIC_TYPEVAR_NAMES:
            return isinstance(actual, (IntType, FloatType, TypeVar))
        return True

    def _types_compatible_with_subs(
        self,
        expected: Type,
        actual: Type,
        subs: dict[str, Type],
        *,
        in_recovery: bool = True,
    ) -> bool:
        """Like _types_compatible but tracks TypeVar bindings in *subs*.

        When *expected* is a TypeVar:
          - If already bound in *subs*, check compatibility with the bound type.
          - If unbound, bind it to *actual*.
        When *actual* is a TypeVar (for higher-order generics and function values):
          - Bind it too, after freshening actual-side TypeVars to avoid collisions.

        ``in_recovery`` mirrors the flag on ``_types_structurally_compatible``
        (see that method's docstring).
        """
        # Numeric generics use their common widened type regardless of
        # argument order: Num=Int followed by Float must widen to Float just
        # as Num=Float followed by Int already does.
        if (
            isinstance(expected, TypeVar)
            and expected.name in _NUMERIC_TYPEVAR_NAMES
            and isinstance(subs.get(expected.name), (IntType, FloatType))
            and isinstance(actual, (IntType, FloatType))
        ):
            if isinstance(actual, FloatType):
                subs[expected.name] = _FLOAT_TYPE
            return True
        expected = self._apply_substitutions(expected, subs)
        actual = self._apply_substitutions(actual, subs)

        if isinstance(expected, AnyType) or isinstance(actual, AnyType):
            return in_recovery
        # Never is bottom: it should satisfy any expected type without
        # constraining generic substitutions to Never.
        if isinstance(actual, NeverType):
            return True

        # Local recursion helper that propagates the in_recovery flag so a
        # caller passing in_recovery=False is not silently widened back to the
        # default when we recurse into compound types.
        def recurse(e: Type, a: Type, s: dict[str, Type] | None = None) -> bool:
            return self._types_compatible_with_subs(
                e, a, subs if s is None else s, in_recovery=in_recovery
            )

        def matches_invariant(expected_inner: Type, actual_inner: Type) -> bool:
            trial_subs = dict(subs)
            if not recurse(expected_inner, actual_inner, trial_subs):
                return False
            if not recurse(actual_inner, expected_inner, trial_subs):
                return False
            subs.clear()
            subs.update(trial_subs)
            return True

        if isinstance(expected, TypeVar):
            if expected.name in subs:
                # Already bound — check the actual type against the bound type
                return recurse(subs[expected.name], actual)
            # Unbound — bind it, but refuse to create a cyclic binding
            # (occurs check). Without this, binding T = List[T] produces
            # an infinite type.
            if not self._typevar_accepts(expected, actual):
                return False
            if self._occurs_in(expected.name, actual):
                return False
            subs[expected.name] = actual
            return True

        if isinstance(actual, TypeVar):
            if actual.name in subs:
                return recurse(expected, subs[actual.name])
            if self._occurs_in(actual.name, expected):
                return False
            subs[actual.name] = expected
            return True

        if type(expected) != type(actual):
            return isinstance(expected, FloatType) and isinstance(actual, IntType)

        if isinstance(expected, ListType) and isinstance(actual, ListType):
            return recurse(expected.element_type, actual.element_type)

        if isinstance(expected, ArrayType) and isinstance(actual, ArrayType):
            return matches_invariant(expected.element_type, actual.element_type)

        if isinstance(expected, TupleType) and isinstance(actual, TupleType):
            if len(expected.element_types) != len(actual.element_types):
                return False
            return all(
                recurse(e, a)
                for e, a in zip(expected.element_types, actual.element_types)
            )

        if isinstance(expected, FuncType) and isinstance(actual, FuncType):
            if len(expected.param_types) != len(actual.param_types):
                return False
            # Contravariant in parameters (reversed), covariant in return
            params_ok = all(
                recurse(a, e) for e, a in zip(expected.param_types, actual.param_types)
            )
            ret_ok = recurse(expected.return_type, actual.return_type)
            # Effect subtyping: actual effects must be subset of expected
            effects_ok = actual.effects <= expected.effects
            return params_ok and ret_ok and effects_ok

        if isinstance(expected, UserType) and isinstance(actual, UserType):
            if expected.name != actual.name:
                return False
            if len(expected.type_args) != len(actual.type_args):
                return False
            type_info = self.type_defs.get(expected.name)
            inv = type_info.invariant_params if type_info else frozenset()
            return all(
                matches_invariant(e, a) if i in inv else recurse(e, a)
                for i, (e, a) in enumerate(zip(expected.type_args, actual.type_args))
            )

        if isinstance(expected, OptionType) and isinstance(actual, OptionType):
            return recurse(expected.value_type, actual.value_type)

        if isinstance(expected, ResultType) and isinstance(actual, ResultType):
            return recurse(expected.ok_type, actual.ok_type) and recurse(
                expected.err_type, actual.err_type
            )

        if isinstance(expected, MapType) and isinstance(actual, MapType):
            return recurse(expected.key_type, actual.key_type) and recurse(
                expected.value_type, actual.value_type
            )

        if isinstance(expected, MutableMapType) and isinstance(actual, MutableMapType):
            return matches_invariant(
                expected.key_type, actual.key_type
            ) and matches_invariant(expected.value_type, actual.value_type)

        if isinstance(expected, VecType) and isinstance(actual, VecType):
            return matches_invariant(expected.element_type, actual.element_type)

        if isinstance(expected, SetType) and isinstance(actual, SetType):
            return matches_invariant(expected.element_type, actual.element_type)

        if isinstance(expected, AsyncType) and isinstance(actual, AsyncType):
            return recurse(expected.result_type, actual.result_type)

        return type(expected) in (IntType, FloatType, BoolType, StringType, UnitType)


def type_check(program: Program) -> None:
    """
    Type check a Geno program.

    Args:
        program: The program AST to check

    Raises:
        TypeError: If any type errors are found
    """
    checker = TypeChecker()
    checker.check_program(program)
