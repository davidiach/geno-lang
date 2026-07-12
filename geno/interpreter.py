"""
Geno Interpreter
====================

Runtime interpreter for Geno programs.
Executes AST nodes and manages program state.
"""

import logging
import math
import sys
import threading
import time
from collections.abc import Collection, Generator
from contextlib import contextmanager
from functools import partial
from types import MappingProxyType
from typing import Any, cast

from . import builtins as _builtins
from .ast_nodes import (  # Types; Expressions; Patterns; Statements; Specifications; Definitions; Program
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
    TypeAlias,
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
from .builtin_registry import (
    ALWAYS_AVAILABLE_BUILTINS,
    DEFAULT_ALLOWED_CAPABILITIES,
    allowed_gated_builtins,
    interpreter_builtin_param_name_lists,
)
from .diagnostics import ErrorCode
from .harness import example_call_args
from .sandbox import (
    RecursionLimitError,
    SandboxConfig,
    SandboxError,
    StepLimitExceeded,
)
from .sandbox import TimeoutError as SandboxTimeout
from .tokens import SourceLocation
from .types import FloatType

# Re-export runtime value types for backward compatibility
from .values import (
    _UNBOUND,
    ArrayValue,
    AsyncValue,
    BreakException,
    BuiltinFunction,
    Closure,
    ConstructorValue,
    ContinueException,
    ContractViolationError,
    Environment,
    GenoRuntimeError,
    GenoThrowError,
    MutableMapValue,
    PropagateException,
    ReturnException,
    RuntimeError,
    SetValue,
    VecValue,
)

# =============================================================================
# Constants
# =============================================================================

logger = logging.getLogger(__name__)

# sys.setrecursionlimit is process-global. geno.run() is a documented embedding
# API that may be invoked from multiple threads; a per-run raise/restore races
# — one run's finally could lower the limit while another still needs it, or
# leave it permanently raised. Coordinate with a lock + reference count: raise
# to the max any active run needs, and restore the original only when the last
# active run exits.
_recursion_limit_lock = threading.Lock()
_recursion_limit_refcount = 0
_recursion_limit_original: int | None = None


@contextmanager
def _raised_recursion_limit(needed: int) -> Generator[None, None, None]:
    global _recursion_limit_refcount, _recursion_limit_original
    with _recursion_limit_lock:
        if _recursion_limit_refcount == 0:
            _recursion_limit_original = sys.getrecursionlimit()
        _recursion_limit_refcount += 1
        if needed > sys.getrecursionlimit():
            sys.setrecursionlimit(needed)
    try:
        yield
    finally:
        with _recursion_limit_lock:
            _recursion_limit_refcount -= 1
            if _recursion_limit_refcount == 0 and _recursion_limit_original is not None:
                sys.setrecursionlimit(_recursion_limit_original)
                _recursion_limit_original = None


# Sentinel for unfilled argument slots (not None — that's Geno's Unit value)
_UNFILLED = object()

# These builtins mutate a validated reference container in place. Their own
# implementations enforce top-level growth; the interpreter only needs to
# inspect the non-target arguments and result for nested resource limits.
_INCREMENTAL_MUTATION_BUILTINS = frozenset(
    {
        "array_set",
        "array_fill",
        "mutable_map_set",
        "mutable_map_delete",
        "vec_push",
        "vec_set",
        "vec_pop",
        "set_add",
        "set_remove",
    }
)
_UNSPECIFIED_CAPABILITIES = object()

# Exception types a builtin may raise as an ordinary, user-facing Geno error
# (bad argument, arithmetic, etc.). Anything outside this set reaching the
# builtin-call catch-all is an internal toolchain defect, not user error, so
# it is logged with its traceback before being surfaced. Base classes only —
# subclasses (KeyError/IndexError ⊂ LookupError, ZeroDivisionError ⊂
# ArithmeticError) are covered transitively; RuntimeError subclasses such as
# NotImplementedError are handled by the earlier RuntimeError re-raise.
_EXPECTED_BUILTIN_ERRORS = (
    ValueError,
    TypeError,
    ArithmeticError,
    AttributeError,
    LookupError,
    OSError,
)


class _ModuleNamespace:
    """Sentinel for module namespace access in the interpreter."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name


def _int_trunc_divmod(a: int, b: int) -> tuple[int, int]:
    """Integer division/modulo with truncation-toward-zero semantics."""
    if b == 0:
        raise ZeroDivisionError("division by zero")
    quotient = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        quotient = -quotient
    remainder = a - (b * quotient)
    return quotient, remainder


def _numeric_mod(a: Any, b: Any) -> Any:
    """Numeric remainder paired with truncation-toward-zero division."""
    if b == 0:
        raise ZeroDivisionError("division by zero")
    if isinstance(a, int) and isinstance(b, int):
        return _int_trunc_divmod(a, b)[1]
    return a - (b * math.trunc(a / b))


def _expected_runtime_type_is_float(expected_type: Any) -> bool:
    if isinstance(expected_type, FloatType):
        return True
    return (
        isinstance(expected_type, SimpleType)
        and expected_type.name == "Float"
        and not expected_type.type_params
    )


def _promote_int_to_expected_float(value: Any, expected_type: Any) -> Any:
    """Materialise Geno's Int-to-Float compatibility in runtime values."""
    if type(value) is int and _expected_runtime_type_is_float(expected_type):
        return float(value)
    return value


# =============================================================================
# Interpreter
# =============================================================================


class Interpreter:
    """
    Interpreter for Geno programs.

    Executes a Geno AST and returns the result.
    Manages the runtime environment and handles all language constructs.

    Example:
        interp = Interpreter()
        result = interp.run(program)
    """

    # Maximum recursion depth to prevent stack overflow
    # Set conservatively because each Geno call uses multiple Python stack frames
    # Python's default limit is ~1000, and each Geno call uses ~4-5 Python frames
    MAX_RECURSION_DEPTH = 500

    def __init__(
        self,
        check_examples: bool = True,
        max_recursion_depth: int | None = None,
        sandbox_config: SandboxConfig | None = None,
        capabilities: Collection[str] | None | object = _UNSPECIFIED_CAPABILITIES,
    ):
        """
        Initialize the interpreter.

        Args:
            check_examples: Whether to verify example clauses at runtime
            max_recursion_depth: Maximum call stack depth (default: 100)
            sandbox_config: Sandbox configuration (None = use defaults)
            capabilities: Granted runtime capabilities. Omitted uses the CLI-style
                direct interpreter default; None is fail-closed.
        """
        self.global_env = Environment()
        self.type_defs: dict[str, TypeDef] = {}
        self.functions: dict[str, FunctionDef] = {}
        self._module_namespaces: dict[str, dict[str, Closure]] = {}
        self.check_examples = check_examples
        self.sandbox_config = sandbox_config or SandboxConfig()
        self.max_recursion_depth = (
            max_recursion_depth
            or self.sandbox_config.max_recursion_depth
            or self.MAX_RECURSION_DEPTH
        )
        self.call_depth = 0
        self._call_stack: list[tuple[str, SourceLocation | None]] = []

        # Step counter for execution budgeting
        self.steps: int = 0
        self.max_steps: int | None = self.sandbox_config.max_steps
        # Comparison-friendly form of max_steps for the per-step fast path
        # (None means unlimited; float("inf") compares correctly with ints).
        self._step_limit: int | float = (
            self.max_steps if self.max_steps is not None else float("inf")
        )

        # Output capture for sandboxed execution
        self.output_buffer: list[str] = []
        self._output_length = 0
        self._deadline: float | None = None

        # Reverse index: constructor name -> parent type name (O(1) lookup)
        self._constructor_to_type: dict[str, str] = {}

        # Trait support: (trait_name, type_name) -> {method_name: Closure}
        self.trait_impls: dict[tuple[str, str], dict[str, Closure]] = {}
        # Set of method names that are trait methods (for dispatch detection)
        self.trait_method_names: set[str] = set()
        self.trait_method_param_names: dict[tuple[str, str], list[str]] = {}

        # Propagate the configured cap to the module-level limit consulted
        # by builtin pre-checks (set_at, concat, range, array_new, set_*,
        # repeat_string, etc.) so a tightened sandbox limit is enforced
        # before those builtins allocate their output.
        _builtins.set_max_collection_size(self.sandbox_config.max_collection_size)

        self._register_builtin_types()
        self._init_builtins()
        capability_grants = (
            DEFAULT_ALLOWED_CAPABILITIES
            if capabilities is _UNSPECIFIED_CAPABILITIES
            else cast(Collection[str] | None, capabilities)
        )
        self.apply_capabilities(capability_grants)

    def apply_capabilities(self, capabilities: Collection[str] | None) -> None:
        """Replace disallowed gated builtins with capability-denied stubs."""
        allowed_gated = allowed_gated_builtins(capabilities)

        def _make_denied_stub(builtin_name: str):
            def denied(*args):
                raise RuntimeError(
                    f"Capability denied: '{builtin_name}' requires a capability "
                    f"that was not granted",
                    error_code=ErrorCode.RUNTIME_CAPABILITY_DENIED,
                )

            return denied

        to_deny: list[str] = []
        for name, value in self.global_env.bindings.items():
            if isinstance(value, BuiltinFunction):
                if name not in ALWAYS_AVAILABLE_BUILTINS and name not in allowed_gated:
                    to_deny.append(name)

        for name in to_deny:
            original = self.global_env.bindings[name]
            self.global_env.bindings[name] = BuiltinFunction(
                name=name,
                func=_make_denied_stub(name),
                arity=original.arity,
                param_names=original.param_names,
            )

    def _register_builtin_types(self) -> None:
        """Register built-in type definitions so the generic constructor path handles them."""
        loc = SourceLocation(line=0, column=0, filename="<builtin>")
        dummy = SimpleType(name="Any", location=loc)

        def _variant(name: str, field_names: list[str]) -> TypeVariant:
            return TypeVariant(
                name=name, fields=[(f, dummy) for f in field_names], location=loc
            )

        builtin_types: list[TypeDef] = [
            TypeDef(
                name="Option",
                type_params=["T"],
                variants=[
                    _variant("Some", ["value"]),
                    _variant("None", []),
                ],
                location=loc,
            ),
            TypeDef(
                name="Result",
                type_params=["T", "E"],
                variants=[
                    _variant("Ok", ["value"]),
                    _variant("Err", ["error"]),
                ],
                location=loc,
            ),
            TypeDef(
                name="JsonValue",
                type_params=[],
                variants=[
                    _variant("JsonString", ["value"]),
                    _variant("JsonInt", ["value"]),
                    _variant("JsonFloat", ["value"]),
                    _variant("JsonBool", ["value"]),
                    _variant("JsonNull", []),
                    _variant("JsonArray", ["items"]),
                    _variant("JsonObject", ["entries"]),
                ],
                location=loc,
            ),
            TypeDef(
                name="HttpResponse",
                type_params=[],
                variants=[
                    _variant("HttpResponse", ["status", "body", "headers"]),
                ],
                location=loc,
            ),
            TypeDef(
                name="HttpRequest",
                type_params=[],
                variants=[
                    _variant(
                        "HttpRequest", ["method", "path", "query", "headers", "body"]
                    ),
                ],
                location=loc,
            ),
            TypeDef(
                name="ProcessResult",
                type_params=[],
                variants=[
                    _variant("ProcessResult", ["exit_code", "stdout", "stderr"]),
                ],
                location=loc,
            ),
        ]

        for type_def in builtin_types:
            self.type_defs[type_def.name] = type_def
            for variant in type_def.variants:
                self._constructor_to_type[variant.name] = type_def.name

    def _init_builtins(self) -> None:
        """Initialize built-in functions."""
        collection_limited_builtins = {
            "append",
            "concat",
            "split",
            "join",
            "replace",
            "range",
            "format",
            "repeat_string",
            "string_pad_left",
            "string_pad_right",
            "string_repeat",
            "string_split",
            "string_join",
            "string_replace",
            "json_parse",
            "json_stringify",
            "json_stringify_pretty",
            "json_to_string",
            "csv_parse",
            "csv_parse_with_headers",
            "toml_parse",
            "regex_find_all",
            "regex_replace",
            "env_get",
            "env_get_or",
            "cli_args",
            "list_flatten",
            "list_intersperse",
            "array_new",
            "array_from_list",
            "map_from_list",
            "map_insert",
            "map_merge",
            "map_entries",
            "map_from_entries",
            "set_from_list",
            "set_add",
            "set_union",
        }

        # Format: (name, func, arity, fallback_param_names)
        builtins = [
            # List operations
            ("length", _builtins.builtin_length, 1, ["list"]),
            ("head", _builtins.builtin_head, 1, ["list"]),
            ("tail", _builtins.builtin_tail, 1, ["list"]),
            ("append", _builtins.builtin_append, 2, ["list", "element"]),
            ("concat", _builtins.builtin_concat, 2, ["list1", "list2"]),
            ("set_at", _builtins.builtin_set_at, 3, ["list", "index", "value"]),
            ("slice", _builtins.builtin_slice, 3, ["list", "start", "stop"]),
            ("filter", self._builtin_filter, 2, ["list", "predicate"]),
            ("map", self._builtin_map, 2, ["list", "transform"]),
            ("fold", self._builtin_fold, 3, ["list", "initial", "reducer"]),
            ("contains", _builtins.builtin_contains, 2, ["list", "element"]),
            ("take_while", self._builtin_take_while, 2, ["list", "predicate"]),
            ("all", self._builtin_all, 2, ["list", "predicate"]),
            ("sort", self._builtin_sort, 2, ["list", "comparator"]),
            ("sort_by", self._builtin_sort_by, 2, ["list", "key_fn"]),
            ("zip", _builtins.builtin_zip, 2, ["list1", "list2"]),
            ("enumerate", _builtins.builtin_enumerate, 1, ["list"]),
            ("flat_map", self._builtin_flat_map, 2, ["list", "fn"]),
            # String operations
            ("split", _builtins.builtin_split, 2, ["text", "delimiter"]),
            ("join", _builtins.builtin_join, 2, ["parts", "separator"]),
            ("trim", _builtins.builtin_trim, 1, ["text"]),
            ("to_lower", _builtins.builtin_to_lower, 1, ["text"]),
            ("to_upper", _builtins.builtin_to_upper, 1, ["text"]),
            ("replace", _builtins.builtin_replace, 3, ["text", "old", "new"]),
            ("ends_with", _builtins.builtin_ends_with, 2, ["text", "suffix"]),
            ("split_once", _builtins.builtin_split_once, 2, ["text", "delimiter"]),
            ("starts_with", _builtins.builtin_starts_with, 2, ["text", "prefix"]),
            ("to_chars", _builtins.builtin_to_chars, 1, ["text"]),
            ("sort_strings", _builtins.builtin_sort_strings, 1, ["items"]),
            (
                "contains_substring",
                _builtins.builtin_contains_substring,
                2,
                ["text", "substring"],
            ),
            ("repeat_string", _builtins.builtin_repeat_string, 2, ["text", "count"]),
            ("string_trim", _builtins.builtin_string_trim, 1, ["text"]),
            ("string_trim_start", _builtins.builtin_string_trim_start, 1, ["text"]),
            ("string_trim_end", _builtins.builtin_string_trim_end, 1, ["text"]),
            (
                "string_pad_left",
                _builtins.builtin_string_pad_left,
                3,
                ["text", "width", "fill_char"],
            ),
            (
                "string_pad_right",
                _builtins.builtin_string_pad_right,
                3,
                ["text", "width", "fill_char"],
            ),
            ("string_char_at", _builtins.builtin_string_char_at, 2, ["text", "index"]),
            (
                "string_index_of",
                _builtins.builtin_string_index_of,
                2,
                ["text", "substring"],
            ),
            (
                "string_last_index_of",
                _builtins.builtin_string_last_index_of,
                2,
                ["text", "substring"],
            ),
            ("string_repeat", _builtins.builtin_string_repeat, 2, ["text", "count"]),
            (
                "string_substring",
                _builtins.builtin_string_substring,
                3,
                ["text", "start", "stop"],
            ),
            ("string_split", _builtins.builtin_string_split, 2, ["text", "delimiter"]),
            ("string_join", _builtins.builtin_string_join, 2, ["parts", "separator"]),
            (
                "string_replace",
                _builtins.builtin_string_replace,
                3,
                ["text", "old", "new"],
            ),
            ("string_to_upper", _builtins.builtin_string_to_upper, 1, ["text"]),
            ("string_to_lower", _builtins.builtin_string_to_lower, 1, ["text"]),
            (
                "string_starts_with",
                _builtins.builtin_string_starts_with,
                2,
                ["text", "prefix"],
            ),
            (
                "string_ends_with",
                _builtins.builtin_string_ends_with,
                2,
                ["text", "suffix"],
            ),
            (
                "string_contains",
                _builtins.builtin_string_contains,
                2,
                ["text", "substring"],
            ),
            (
                "string_split_once",
                _builtins.builtin_string_split_once,
                2,
                ["text", "delimiter"],
            ),
            # List stdlib wrappers
            ("list_length", _builtins.builtin_list_length, 1, ["list"]),
            ("list_map", self._builtin_list_map, 2, ["list", "transform"]),
            ("list_filter", self._builtin_list_filter, 2, ["list", "predicate"]),
            # Math stdlib builtins
            ("math_abs", _builtins.builtin_math_abs, 1, ["value"]),
            ("math_min", _builtins.builtin_math_min, 2, ["a", "b"]),
            ("math_max", _builtins.builtin_math_max, 2, ["a", "b"]),
            ("math_clamp", _builtins.builtin_math_clamp, 3, ["value", "lo", "hi"]),
            ("math_floor", _builtins.builtin_math_floor, 1, ["value"]),
            ("math_ceil", _builtins.builtin_math_ceil, 1, ["value"]),
            ("math_round", _builtins.builtin_math_round, 1, ["value"]),
            ("math_sqrt", _builtins.builtin_math_sqrt, 1, ["value"]),
            ("math_log", _builtins.builtin_math_log, 1, ["value"]),
            ("math_sin", _builtins.builtin_math_sin, 1, ["value"]),
            ("math_cos", _builtins.builtin_math_cos, 1, ["value"]),
            ("math_pi", _builtins.builtin_math_pi, 0, []),
            ("math_e", _builtins.builtin_math_e, 0, []),
            ("math_random_int", _builtins.builtin_math_random_int, 2, ["lo", "hi"]),
            ("math_random_float", _builtins.builtin_math_random_float, 0, []),
            # Map stdlib builtins
            ("map_from_list", _builtins.builtin_map_from_list, 1, ["pairs"]),
            ("map_merge", _builtins.builtin_map_merge, 2, ["map1", "map2"]),
            ("map_filter_map", self._builtin_map_filter_map, 2, ["map", "predicate"]),
            ("map_map_values", self._builtin_map_map_values, 2, ["map", "transform"]),
            ("map_entries", _builtins.builtin_map_entries, 1, ["map"]),
            ("map_from_entries", _builtins.builtin_map_from_entries, 1, ["entries"]),
            # Result stdlib builtins
            ("result_map", self._builtin_result_map, 2, ["result", "f"]),
            ("result_map_err", self._builtin_result_map_err, 2, ["result", "f"]),
            ("result_and_then", self._builtin_result_and_then, 2, ["result", "f"]),
            (
                "result_unwrap_or",
                _builtins.builtin_result_unwrap_or,
                2,
                ["result", "default"],
            ),
            ("result_is_ok", _builtins.builtin_result_is_ok, 1, ["result"]),
            ("result_is_err", _builtins.builtin_result_is_err, 1, ["result"]),
            ("result_to_option", _builtins.builtin_result_to_option, 1, ["result"]),
            # Option stdlib builtins
            ("option_map", self._builtin_option_map, 2, ["option", "f"]),
            ("option_and_then", self._builtin_option_and_then, 2, ["option", "f"]),
            (
                "option_unwrap_or",
                _builtins.builtin_option_unwrap_or,
                2,
                ["option", "default"],
            ),
            ("option_is_some", _builtins.builtin_option_is_some, 1, ["option"]),
            ("option_is_none", _builtins.builtin_option_is_none, 1, ["option"]),
            ("option_flatten", _builtins.builtin_option_flatten, 1, ["option"]),
            (
                "option_to_result",
                _builtins.builtin_option_to_result,
                2,
                ["option", "err"],
            ),
            # Path stdlib builtins
            ("path_join", _builtins.builtin_path_join, 2, ["base", "child"]),
            ("path_parent", _builtins.builtin_path_parent, 1, ["path"]),
            ("path_filename", _builtins.builtin_path_filename, 1, ["path"]),
            ("path_extension", _builtins.builtin_path_extension, 1, ["path"]),
            ("path_is_absolute", _builtins.builtin_path_is_absolute, 1, ["path"]),
            # DateTime stdlib builtins
            ("datetime_now", _builtins.builtin_datetime_now, 0, []),
            (
                "datetime_format",
                _builtins.builtin_datetime_format,
                2,
                ["timestamp", "fmt"],
            ),
            ("datetime_parse", _builtins.builtin_datetime_parse, 2, ["text", "fmt"]),
            (
                "datetime_elapsed",
                _builtins.builtin_datetime_elapsed,
                2,
                ["start", "end_time"],
            ),
            # List stdlib builtins
            ("list_zip", _builtins.builtin_list_zip, 2, ["list1", "list2"]),
            ("list_enumerate", _builtins.builtin_list_enumerate, 1, ["list"]),
            ("list_all", self._builtin_list_all, 2, ["list", "predicate"]),
            ("list_flatten", _builtins.builtin_list_flatten, 1, ["lists"]),
            ("list_chunk", _builtins.builtin_list_chunk, 2, ["list", "size"]),
            ("list_take", _builtins.builtin_list_take, 2, ["list", "count"]),
            ("list_drop", _builtins.builtin_list_drop, 2, ["list", "count"]),
            ("list_find", self._builtin_list_find, 2, ["list", "predicate"]),
            (
                "list_find_index",
                self._builtin_list_find_index,
                2,
                ["list", "predicate"],
            ),
            ("list_any", self._builtin_list_any, 2, ["list", "predicate"]),
            (
                "list_fold_right",
                self._builtin_list_fold_right,
                3,
                ["list", "init", "f"],
            ),
            (
                "list_intersperse",
                _builtins.builtin_list_intersperse,
                2,
                ["list", "separator"],
            ),
            ("list_group_by", self._builtin_list_group_by, 2, ["list", "key_fn"]),
            # Math operations
            ("add", lambda a, b: a + b, 2, ["a", "b"]),
            ("subtract", lambda a, b: a - b, 2, ["a", "b"]),
            ("multiply", lambda a, b: a * b, 2, ["a", "b"]),
            ("divide", _builtins.builtin_divide, 2, ["a", "b"]),
            ("sqrt", _builtins.builtin_sqrt, 1, ["value"]),
            ("floor", _builtins.builtin_floor, 1, ["value"]),
            ("ceil", _builtins.builtin_ceil, 1, ["value"]),
            ("round", _builtins.builtin_round, 1, ["value"]),
            ("max", _builtins.builtin_max, 2, ["a", "b"]),
            ("clamp", _builtins.builtin_clamp, 3, ["value", "min", "max"]),
            ("abs", lambda x: abs(x), 1, ["value"]),
            ("square", lambda x: x * x, 1, ["value"]),
            # Type predicates
            ("is_sorted", _builtins.builtin_is_sorted, 1, ["list"]),
            ("is_positive", lambda x: x > 0, 1, ["value"]),
            ("is_numeric_string", _builtins.builtin_is_numeric_string, 1, ["text"]),
            ("is_permutation", _builtins.builtin_is_permutation, 2, ["list1", "list2"]),
            # Conversions
            ("parse_int", _builtins.builtin_parse_int, 1, ["text"]),
            ("parse_float", _builtins.builtin_parse_float, 1, ["text"]),
            (
                "to_string",
                _builtins.stringify_value,
                1,
                ["value"],
            ),
            ("float_to_int", _builtins.builtin_float_to_int, 1, ["value"]),
            ("int_to_float", lambda x: float(x), 1, ["value"]),
            # List extras
            ("reverse", _builtins.builtin_reverse, 1, ["list"]),
            ("bit_or", _builtins.builtin_bit_or, 2, ["a", "b"]),
            ("range", _builtins.builtin_range, -1, ["start", "end"]),
            # String extras
            ("substring", _builtins.builtin_substring, 3, ["text", "start", "stop"]),
            ("format", _builtins.builtin_format, 2, ["template", "values"]),
            # Char code operations
            ("char_code", _builtins.builtin_char_code, 1, ["text"]),
            ("from_char_code", _builtins.builtin_from_char_code, 1, ["code"]),
            # Option operations
            ("is_some", _builtins.builtin_is_some, 1, ["option"]),
            ("is_none", _builtins.builtin_is_none, 1, ["option"]),
            ("unwrap", _builtins.builtin_unwrap, 1, ["option"]),
            ("unwrap_or", _builtins.builtin_unwrap_or, 2, ["option", "default"]),
            # Map operations
            ("map_insert", _builtins.builtin_map_insert, 3, ["map", "key", "value"]),
            ("map_get", _builtins.builtin_map_get, 2, ["map", "key"]),
            # IO (for REPL)
            ("print", self._builtin_print, 1, ["value"]),
            # Clock / random
            ("clock_now", _builtins.builtin_clock_now, 0, []),
            ("clock_format", _builtins.builtin_clock_format, 2, ["timestamp", "fmt"]),
            ("clock_parse", _builtins.builtin_clock_parse, 2, ["text", "fmt"]),
            (
                "clock_elapsed",
                _builtins.builtin_clock_elapsed,
                2,
                ["start", "end_time"],
            ),
            ("random_int", _builtins.builtin_random_int, 2, ["min", "max"]),
            ("random_float", _builtins.builtin_random_float, 0, []),
            # sleep_ms is gated behind a host callback (installed by
            # install_clock_callbacks) so RunConfig.capabilities can deny it.
            ("sleep_ms", self._stub_host_callback("sleep_ms"), 1, ["ms"]),
            # Host-callback builtins (stubs until replaced by host)
            ("fs_read_text", self._stub_host_callback("fs_read_text"), 1, ["path"]),
            (
                "fs_write_text",
                self._stub_host_callback("fs_write_text"),
                2,
                ["path", "content"],
            ),
            ("fs_list_dir", self._stub_host_callback("fs_list_dir"), 1, ["path"]),
            ("fs_exists", self._stub_host_callback("fs_exists"), 1, ["path"]),
            ("http_fetch", self._stub_host_callback("http_fetch"), 1, ["url"]),
            ("http_post", self._stub_host_callback("http_post"), 2, ["url", "body"]),
            (
                "http_request",
                self._stub_host_callback("http_request"),
                4,
                ["method", "url", "headers", "body"],
            ),
            # Serve builtins (HOF — needs interpreter self)
            ("http_listen", self._stub_host_callback("http_listen"), 1, ["port"]),
            (
                "http_route",
                self._stub_host_callback("http_route"),
                3,
                ["method", "path", "handler"],
            ),
            (
                "http_respond",
                _builtins.builtin_http_respond,
                3,
                ["status", "headers", "body"],
            ),
            # JSON builtins
            ("json_parse", _builtins.builtin_json_parse, 1, ["text"]),
            ("json_stringify", _builtins.builtin_json_stringify, 1, ["value"]),
            (
                "json_stringify_pretty",
                _builtins.builtin_json_stringify_pretty,
                2,
                ["value", "indent"],
            ),
            ("json_to_string", _builtins.builtin_json_to_string, 1, ["value"]),
            ("csv_parse", _builtins.builtin_csv_parse, 1, ["text"]),
            (
                "csv_parse_with_headers",
                _builtins.builtin_csv_parse_with_headers,
                1,
                ["text"],
            ),
            ("toml_parse", _builtins.builtin_toml_parse, 1, ["text"]),
            # Process execution (capability-gated, host callback stubs)
            ("exec", self._stub_host_callback("exec"), 1, ["command"]),
            (
                "exec_with_input",
                self._stub_host_callback("exec_with_input"),
                2,
                ["command", "stdin"],
            ),
            ("spawn", self._stub_host_callback("spawn"), 2, ["program", "args"]),
            (
                "spawn_with_input",
                self._stub_host_callback("spawn_with_input"),
                3,
                ["program", "args", "stdin"],
            ),
            # Stdin (capability-gated, host callback stub)
            (
                "stdin_read_all",
                self._stub_host_callback("stdin_read_all"),
                0,
                [],
            ),
            # Environment variable builtins (capability-gated)
            ("env_get", _builtins.builtin_env_get, 1, ["name"]),
            ("env_get_or", _builtins.builtin_env_get_or, 2, ["name", "default"]),
            ("cli_args", _builtins.builtin_cli_args, 0, []),
            # Graphics builtins (stubs in interpreter mode)
            ("clear_screen", _builtins.builtin_clear_screen, 1, ["color"]),
            (
                "draw_rect",
                _builtins.builtin_draw_rect,
                5,
                ["x", "y", "width", "height", "color"],
            ),
            (
                "draw_rect_outline",
                _builtins.builtin_draw_rect_outline,
                5,
                ["x", "y", "width", "height", "color"],
            ),
            (
                "draw_circle",
                _builtins.builtin_draw_circle,
                4,
                ["x", "y", "radius", "color"],
            ),
            (
                "draw_line",
                _builtins.builtin_draw_line,
                5,
                ["x1", "y1", "x2", "y2", "color"],
            ),
            (
                "draw_text",
                _builtins.builtin_draw_text,
                5,
                ["text", "x", "y", "size", "color"],
            ),
            ("screen_width", _builtins.builtin_screen_width, 0, []),
            ("screen_height", _builtins.builtin_screen_height, 0, []),
            # Input builtins (stubs in interpreter mode)
            ("is_key_down", _builtins.builtin_is_key_down, 1, ["key"]),
            ("is_key_pressed", _builtins.builtin_is_key_pressed, 1, ["key"]),
            # Mouse input builtins (stubs in interpreter mode)
            ("mouse_x", _builtins.builtin_mouse_x, 0, []),
            ("mouse_y", _builtins.builtin_mouse_y, 0, []),
            ("is_mouse_down", _builtins.builtin_is_mouse_down, 0, []),
            ("is_mouse_clicked", _builtins.builtin_is_mouse_clicked, 0, []),
            # Text input builtins (stubs in interpreter mode)
            ("get_text_input", _builtins.builtin_get_text_input, 0, []),
            ("clear_text_input", _builtins.builtin_clear_text_input, 0, []),
            # Array operations
            ("array_new", _builtins.builtin_array_new, 2, ["size", "default"]),
            ("array_from_list", _builtins.builtin_array_from_list, 1, ["list"]),
            ("array_get", _builtins.builtin_array_get, 2, ["array", "index"]),
            ("array_set", _builtins.builtin_array_set, 3, ["array", "index", "value"]),
            ("array_length", _builtins.builtin_array_length, 1, ["array"]),
            ("array_to_list", _builtins.builtin_array_to_list, 1, ["array"]),
            ("array_fill", _builtins.builtin_array_fill, 2, ["array", "value"]),
            ("array_copy", _builtins.builtin_array_copy, 1, ["array"]),
            # MutableMap operations
            ("mutable_map_new", _builtins.builtin_mutable_map_new, 0, []),
            (
                "mutable_map_set",
                _builtins.builtin_mutable_map_set,
                3,
                ["map", "key", "value"],
            ),
            ("mutable_map_get", _builtins.builtin_mutable_map_get, 2, ["map", "key"]),
            (
                "mutable_map_contains",
                _builtins.builtin_mutable_map_contains,
                2,
                ["map", "key"],
            ),
            (
                "mutable_map_delete",
                _builtins.builtin_mutable_map_delete,
                2,
                ["map", "key"],
            ),
            ("mutable_map_size", _builtins.builtin_mutable_map_size, 1, ["map"]),
            ("mutable_map_keys", _builtins.builtin_mutable_map_keys, 1, ["map"]),
            # Vec operations
            ("vec_new", _builtins.builtin_vec_new, 0, []),
            ("vec_push", _builtins.builtin_vec_push, 2, ["vec", "item"]),
            ("vec_get", _builtins.builtin_vec_get, 2, ["vec", "index"]),
            ("vec_set", _builtins.builtin_vec_set, 3, ["vec", "index", "value"]),
            ("vec_length", _builtins.builtin_vec_length, 1, ["vec"]),
            ("vec_pop", _builtins.builtin_vec_pop, 1, ["vec"]),
            ("vec_to_list", _builtins.builtin_vec_to_list, 1, ["vec"]),
            ("vec_from_list", _builtins.builtin_vec_from_list, 1, ["list"]),
            # Set operations
            ("set_new", _builtins.builtin_set_new, 0, []),
            ("set_from_list", _builtins.builtin_set_from_list, 1, ["list"]),
            ("set_add", _builtins.builtin_set_add, 2, ["set", "item"]),
            ("set_remove", _builtins.builtin_set_remove, 2, ["set", "item"]),
            ("set_contains", _builtins.builtin_set_contains, 2, ["set", "item"]),
            ("set_size", _builtins.builtin_set_size, 1, ["set"]),
            ("set_to_list", _builtins.builtin_set_to_list, 1, ["set"]),
            ("set_union", _builtins.builtin_set_union, 2, ["a", "b"]),
            ("set_intersection", _builtins.builtin_set_intersection, 2, ["a", "b"]),
            # Regex operations (capability-gated)
            ("regex_match", _builtins.builtin_regex_match, 2, ["pattern", "text"]),
            (
                "regex_find_all",
                _builtins.builtin_regex_find_all,
                2,
                ["pattern", "text"],
            ),
            (
                "regex_replace",
                _builtins.builtin_regex_replace,
                3,
                ["pattern", "replacement", "text"],
            ),
        ]

        shared_param_names = interpreter_builtin_param_name_lists()
        for name, func, arity, param_names in builtins:
            if name in collection_limited_builtins:
                func = partial(
                    func,
                    max_collection_size=self.sandbox_config.max_collection_size,
                )
            resolved_param_names = shared_param_names.get(name, param_names)
            self.global_env.bind(
                name,
                BuiltinFunction(name, func, arity, resolved_param_names.copy()),
            )

    # =========================================================================
    # Host Callback Stubs
    # =========================================================================

    def _stub_host_callback(self, name: str) -> Any:
        """Return a stub function that raises RUNTIME_HOST_CALLBACK_MISSING."""
        from .diagnostics import ErrorCode

        def stub(*args):
            raise RuntimeError(
                f"Host callback not provided for '{name}'. "
                f"Provide a callback via RunConfig.host_callbacks.",
                error_code=ErrorCode.RUNTIME_HOST_CALLBACK_MISSING,
            )

        return stub

    # =========================================================================
    # Built-in Function Implementations (callback-dependent)
    # =========================================================================

    def _builtin_filter(self, lst: Any, pred: Any) -> list:
        if not isinstance(lst, list):
            raise RuntimeError(f"filter expects list, got {type(lst).__name__}")
        result: list[Any] = []
        for item in lst:
            if self._call_function(pred, [item]):
                result.append(item)
        return result

    def _builtin_map(self, lst: Any, func: Any) -> list:
        if not isinstance(lst, list):
            raise RuntimeError(f"map expects list, got {type(lst).__name__}")
        return [self._call_function(func, [item]) for item in lst]

    def _builtin_list_map(self, lst: Any, func: Any) -> list:
        if not isinstance(lst, list):
            raise RuntimeError(f"list_map expects list, got {type(lst).__name__}")
        return [self._call_function(func, [item]) for item in lst]

    def _builtin_list_filter(self, lst: Any, pred: Any) -> list:
        if not isinstance(lst, list):
            raise RuntimeError(f"list_filter expects list, got {type(lst).__name__}")
        result: list[Any] = []
        for item in lst:
            if self._call_function(pred, [item]):
                result.append(item)
        return result

    def _builtin_flat_map(self, lst: Any, func: Any) -> list:
        if not isinstance(lst, list):
            raise RuntimeError(f"flat_map expects list, got {type(lst).__name__}")
        limit = self.sandbox_config.max_collection_size
        result: list[Any] = []
        for item in lst:
            mapped = self._call_function(func, [item])
            if not isinstance(mapped, list):
                raise RuntimeError("flat_map: function must return a list")
            expected = len(result) + len(mapped)
            if expected > limit:
                raise RuntimeError(f"List size exceeds limit ({expected} > {limit})")
            result.extend(mapped)
        return result

    def _builtin_fold(self, lst: Any, init: Any, func: Any) -> Any:
        if not isinstance(lst, list):
            raise RuntimeError(f"fold expects list, got {type(lst).__name__}")
        acc = init
        limit = self.sandbox_config.max_collection_size
        for item in lst:
            acc = self._call_function(func, [acc, item])
            # Match compiler semantics: check intermediate accumulator size
            if isinstance(acc, (str, list)):
                if len(acc) > limit:
                    kind = "String" if isinstance(acc, str) else "List"
                    raise RuntimeError(
                        f"{kind} size exceeds limit ({len(acc)} > {limit})"
                    )
        return acc

    def _builtin_take_while(self, lst: Any, pred: Any) -> list:
        if not isinstance(lst, list):
            raise RuntimeError(f"take_while expects list, got {type(lst).__name__}")
        result = []
        for item in lst:
            if self._call_function(pred, [item]):
                result.append(item)
            else:
                break
        return result

    def _builtin_all(self, lst: Any, pred: Any) -> bool:
        if not isinstance(lst, list):
            raise RuntimeError(f"all expects list, got {type(lst).__name__}")
        return all(self._call_function(pred, [item]) for item in lst)

    def _builtin_list_find(self, lst: Any, pred: Any) -> Any:
        if not isinstance(lst, list):
            raise RuntimeError(f"list_find expects list, got {type(lst).__name__}")
        from .values import ConstructorValue

        for item in lst:
            if self._call_function(pred, [item]):
                return ConstructorValue("Some", {"value": item})
        return ConstructorValue("None", {})

    def _builtin_list_find_index(self, lst: Any, pred: Any) -> Any:
        if not isinstance(lst, list):
            raise RuntimeError(
                f"list_find_index expects list, got {type(lst).__name__}"
            )
        from .values import ConstructorValue

        for i, item in enumerate(lst):
            if self._call_function(pred, [item]):
                return ConstructorValue("Some", {"value": i})
        return ConstructorValue("None", {})

    def _builtin_list_all(self, lst: Any, pred: Any) -> bool:
        if not isinstance(lst, list):
            raise RuntimeError(f"list_all expects list, got {type(lst).__name__}")
        return all(self._call_function(pred, [item]) for item in lst)

    def _builtin_list_any(self, lst: Any, pred: Any) -> bool:
        if not isinstance(lst, list):
            raise RuntimeError(f"list_any expects list, got {type(lst).__name__}")
        return any(self._call_function(pred, [item]) for item in lst)

    def _builtin_list_fold_right(self, lst: Any, init: Any, f: Any) -> Any:
        if not isinstance(lst, list):
            raise RuntimeError(
                f"list_fold_right expects list, got {type(lst).__name__}"
            )
        acc = init
        for item in reversed(lst):
            acc = self._call_function(f, [item, acc])
        return acc

    def _builtin_list_group_by(self, lst: Any, key_fn: Any) -> list:
        if not isinstance(lst, list):
            raise RuntimeError(f"list_group_by expects list, got {type(lst).__name__}")
        groups: list[tuple[Any, list[Any]]] = []
        for item in lst:
            k = self._call_function(key_fn, [item])
            for existing_key, items in groups:
                if self._values_equal(existing_key, k):
                    items.append(item)
                    break
            else:
                groups.append((k, [item]))
        return groups

    def _builtin_map_filter_map(self, m: Any, pred: Any) -> dict:
        if not isinstance(m, dict):
            raise RuntimeError(f"map_filter_map expects map, got {type(m).__name__}")
        result = {}
        for k, v in m.items():
            if self._call_function(pred, [k, v]):
                result[k] = v
        return result

    def _builtin_map_map_values(self, m: Any, f: Any) -> dict:
        if not isinstance(m, dict):
            raise RuntimeError(f"map_map_values expects map, got {type(m).__name__}")
        return {k: self._call_function(f, [v]) for k, v in m.items()}

    def _builtin_result_map(self, result: Any, f: Any) -> Any:
        from .values import ConstructorValue

        if isinstance(result, ConstructorValue) and result.constructor == "Ok":
            return ConstructorValue(
                "Ok", {"value": self._call_function(f, [result.fields["value"]])}
            )
        return result

    def _builtin_result_map_err(self, result: Any, f: Any) -> Any:
        from .values import ConstructorValue

        if isinstance(result, ConstructorValue) and result.constructor == "Err":
            return ConstructorValue(
                "Err", {"error": self._call_function(f, [result.fields["error"]])}
            )
        return result

    def _builtin_result_and_then(self, result: Any, f: Any) -> Any:
        from .values import ConstructorValue

        if isinstance(result, ConstructorValue) and result.constructor == "Ok":
            return self._call_function(f, [result.fields["value"]])
        return result

    def _builtin_option_map(self, option: Any, f: Any) -> Any:
        from .values import ConstructorValue

        if isinstance(option, ConstructorValue) and option.constructor == "Some":
            return ConstructorValue(
                "Some", {"value": self._call_function(f, [option.fields["value"]])}
            )
        # Match compiled behaviour: any non-Some (including ill-typed inputs)
        # collapses to a fresh None.
        return ConstructorValue("None", {})

    def _builtin_option_and_then(self, option: Any, f: Any) -> Any:
        from .values import ConstructorValue

        if isinstance(option, ConstructorValue) and option.constructor == "Some":
            return self._call_function(f, [option.fields["value"]])
        return ConstructorValue("None", {})

    def _builtin_sort(self, lst: Any, cmp: Any) -> list:
        if not isinstance(lst, list):
            raise RuntimeError(f"sort expects list, got {type(lst).__name__}")
        import functools

        def comparator(a, b):
            return self._call_function(cmp, [a, b])

        return sorted(lst, key=functools.cmp_to_key(comparator))

    def _builtin_sort_by(self, lst: Any, key_fn: Any) -> list:
        if not isinstance(lst, list):
            raise RuntimeError(f"sort_by expects list, got {type(lst).__name__}")
        return sorted(
            lst,
            key=lambda x: _builtins._geno_sort_key(self._call_function(key_fn, [x])),
        )

    def _builtin_print(self, value: Any) -> None:
        """Print a value, capturing to output buffer only.

        Output is NOT written to host stdout — callers that want visible
        output (e.g. the REPL) should read ``get_output()`` and print it
        themselves.  This avoids duplicate output in ``--unsafe`` mode and
        keeps ``--json`` output clean.
        """
        output = (value if isinstance(value, str) else self._format_value(value)) + "\n"

        # Check output limit
        self._output_length += len(output)
        if self._output_length > self.sandbox_config.max_output_length:
            raise RuntimeError(
                f"Output limit exceeded ({self.sandbox_config.max_output_length} characters)"
            )

        self.output_buffer.append(output)
        return None

    def _format_value(self, value: Any) -> str:
        """Format a value for display."""
        return str(_builtins.format_value(value))

    # =========================================================================
    # Program Execution
    # =========================================================================

    def run(
        self,
        program: Program,
        modules: dict[str, "Program"] | None = None,
        execute_main: bool = True,
    ) -> Any:
        """
        Execute a Geno program.

        Args:
            program: The program AST
            modules: Optional map of module names to parsed Program ASTs.
            execute_main: Whether to invoke `main` after loading definitions.

        Returns:
            The result of the main function, or None

        Raises:
            SandboxTimeout: If execution exceeds timeout
            RuntimeError: For runtime errors
        """
        # Resolve imports first
        if modules is not None:
            resolved: set[str] = set()
            module_imports: dict[
                str, tuple[dict[str, Closure], dict[str, FunctionDef]]
            ] = {}
            for defn in program.definitions:
                if isinstance(defn, ImportStatement):
                    self._resolve_module_import(defn, modules, resolved, module_imports)

        # First pass: collect type and function definitions
        for defn in program.definitions:
            if isinstance(defn, TypeDef):
                # Remove stale constructor entries if type is being redefined
                old_def = self.type_defs.get(defn.name)
                if old_def is not None:
                    for v in old_def.variants:
                        self._constructor_to_type.pop(v.name, None)
                self.type_defs[defn.name] = defn
                for variant in defn.variants:
                    self._constructor_to_type[variant.name] = defn.name
            elif isinstance(defn, FunctionDef):
                self.functions[defn.name] = defn
                closure = Closure(
                    params=defn.params,
                    body=defn.body,
                    env=self.global_env,
                    name=defn.name,
                    specs=defn.specs,
                    is_async=defn.is_async,
                )
                self.global_env.bind(defn.name, closure)

        # Second pass: process trait impls
        for defn in program.definitions:
            if isinstance(defn, ImplDef):
                method_closures: dict[str, Closure] = {}
                for method in defn.methods:
                    method_closures[method.name] = Closure(
                        params=method.params,
                        body=method.body,
                        env=self.global_env,
                        name=method.name,
                        specs=method.specs,
                    )
                self.trait_impls[(defn.trait_name, defn.target_type)] = method_closures
                # Register each trait method name for dispatch
                for method in defn.methods:
                    self.trait_method_names.add(method.name)
                    self.trait_method_param_names[(defn.trait_name, method.name)] = [
                        param.name for param in method.params
                    ]

        # Ensure Python's recursion limit can accommodate the Geno depth.
        # A bare Geno call uses ~4-5 Python frames, but try/catch and match
        # paths chain ~8 (issue #650). 12 gives headroom on every path so
        # the Geno-level check fires before Python's RecursionError.
        needed = self.max_recursion_depth * 12 + 100
        with _raised_recursion_limit(needed):
            with self._execution_deadline(self.sandbox_config.timeout):
                # Verify examples if enabled. Steps spent verifying examples
                # count against the same max_steps budget as `main` so the
                # advertised bound is a single honest global cap — otherwise
                # adversarial example clauses could do up to max_steps work
                # in verification *and* another max_steps in `main`.
                if self.check_examples:
                    self._verify_examples()
                    self.output_buffer.clear()
                    self._output_length = 0

                # Look for a main function
                if execute_main and "main" in self.functions:
                    main_func = self.global_env.lookup("main")
                    return self._call_function(main_func, [])

                return None

    def _resolve_module_import(
        self,
        import_stmt: ImportStatement,
        modules: dict[str, "Program"],
        resolved: set[str],
        module_imports: dict[str, tuple[dict[str, Closure], dict[str, FunctionDef]]],
    ) -> None:
        """Resolve an import statement by loading module definitions."""
        name = import_stmt.module_name
        if name in resolved:
            if name in module_imports:
                self._apply_module_import(import_stmt, module_imports[name])
            return
        if name not in modules:
            raise RuntimeError(
                f"Unknown module: '{name}'",
                import_stmt.location,
            )

        resolved.add(name)
        mod_program = modules[name]

        # Recursively resolve imports within the module
        for defn in mod_program.definitions:
            if isinstance(defn, ImportStatement):
                if defn.module_name == name:
                    raise RuntimeError(
                        f"Circular import detected: module '{name}' imports itself",
                        defn.location,
                    )
                self._resolve_module_import(defn, modules, resolved, module_imports)

        # Determine if module uses explicit exports
        has_exports = any(
            (isinstance(d, (TypeAlias, TypeDef, FunctionDef)) and d.exported)
            for d in mod_program.definitions
        )

        # Register type definitions from module
        for defn in mod_program.definitions:
            if isinstance(defn, TypeDef):
                if has_exports and not defn.exported:
                    continue
                old_def = self.type_defs.get(defn.name)
                if old_def is not None:
                    for v in old_def.variants:
                        self._constructor_to_type.pop(v.name, None)
                self.type_defs[defn.name] = defn
                for variant in defn.variants:
                    self._constructor_to_type[variant.name] = defn.name

        # Register function definitions from module
        module_ns: dict[str, Closure] = {}
        module_functions: dict[str, FunctionDef] = {}
        for defn in mod_program.definitions:
            if isinstance(defn, FunctionDef):
                if has_exports and not defn.exported:
                    continue
                closure = Closure(
                    params=defn.params,
                    body=defn.body,
                    env=self.global_env,
                    name=defn.name,
                    specs=defn.specs,
                    is_async=defn.is_async,
                )
                module_ns[defn.name] = closure
                module_functions[defn.name] = defn
        module_import = (module_ns, module_functions)
        module_imports[name] = module_import
        self._apply_module_import(import_stmt, module_import)

    def _apply_module_import(
        self,
        import_stmt: ImportStatement,
        module_import: tuple[dict[str, Closure], dict[str, FunctionDef]],
    ) -> None:
        """Apply an already loaded module using this import statement's shape."""
        module_ns, module_functions = module_import
        ns_name = import_stmt.alias or import_stmt.module_name
        self._module_namespaces[ns_name] = dict(module_ns)
        if import_stmt.alias is None:
            for func_name, closure in module_ns.items():
                self.functions[func_name] = module_functions[func_name]
                self.global_env.bind(func_name, closure)

    @contextmanager
    def _execution_deadline(self, timeout: float | None) -> Generator[None, None, None]:
        """Apply a cooperative execution deadline for the current call chain."""
        previous_deadline = self._deadline

        if timeout is None:
            try:
                yield
            finally:
                self._deadline = previous_deadline
            return

        deadline = time.perf_counter() + timeout
        if previous_deadline is not None:
            deadline = min(deadline, previous_deadline)

        self._deadline = deadline
        self._check_timeout()
        try:
            yield
            self._check_timeout()
        finally:
            self._deadline = previous_deadline

    def _check_timeout(self, location: SourceLocation | None = None) -> None:
        """Raise when cooperative execution exceeds the configured deadline."""
        if self._deadline is None:
            return
        if time.perf_counter() <= self._deadline:
            return

        loc_str = f" at {location}" if location else ""
        raise SandboxTimeout(
            f"Execution timed out after {self.sandbox_config.timeout} seconds{loc_str}"
        )

    def call_function(
        self,
        func: Any,
        args: list[Any],
        location: SourceLocation | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Call a function with an optional cooperative timeout."""
        with self._execution_deadline(timeout):
            return self._call_function(func, args, location)

    def get_output(self) -> str:
        """Get captured output."""
        return "".join(self.output_buffer)

    def clear_output(self) -> None:
        """Clear the output buffer."""
        self.output_buffer.clear()
        self._output_length = 0

    def _verify_examples(self) -> None:
        """Verify all example clauses in function definitions."""
        for name, func_def in self.functions.items():
            for example in func_def.specs.examples:
                try:
                    # Evaluate input
                    input_val = self.eval_expr(example.input_expr, self.global_env)
                    expected = self.eval_expr(example.output_expr, self.global_env)

                    # Call function with input
                    func = self.global_env.lookup(name)
                    actual = self._call_function(
                        func,
                        example_call_args(
                            input_val,
                            param_count=len(func_def.params),
                            required_count=sum(
                                1
                                for param in func_def.params
                                if param.default_value is None
                            ),
                        ),
                    )

                    # Compare
                    if not self._values_equal(
                        actual, expected, approximate_floats=True
                    ):
                        raise RuntimeError(
                            f"Example verification failed for {name}: "
                            f"expected {self._format_value(expected)}, "
                            f"got {self._format_value(actual)}",
                            example.location,
                        )
                except ReturnException:
                    raise RuntimeError(
                        "Unexpected return in example expression",
                        example.location,
                    )

    def _values_equal(
        self,
        a: Any,
        b: Any,
        _visited: set | None = None,
        *,
        approximate_floats: bool = False,
    ) -> bool:
        """Check if two values are equal.

        Each recursive element comparison counts as a step so that large
        structure comparisons respect the step budget and cooperative timeout.
        A *_visited* set of ``(id(a), id(b))`` pairs prevents infinite
        recursion on circular structures.
        Set *approximate_floats* for example verification, where users expect
        decimal arithmetic examples to tolerate binary floating-point noise.
        """
        self._step()
        # Normalise Unit representations: None and () are both Unit
        if a is None:
            a = ()
        if b is None:
            b = ()
        if type(a) in (int, float) and type(b) in (int, float):
            if approximate_floats:
                return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
            # Int is a subtype of Float in Geno. Equality therefore compares
            # numeric values rather than host-language storage types.
            return bool(a == b)
        if type(a) != type(b):
            return False
        if approximate_floats and isinstance(a, float):
            return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)

        # Guard against circular references
        if isinstance(
            a,
            (
                list,
                dict,
                ConstructorValue,
                ArrayValue,
                MutableMapValue,
                SetValue,
                VecValue,
            ),
        ):
            pair = (id(a), id(b))
            if _visited is None:
                _visited = set()
            if pair in _visited:
                return True  # assume equal for cycles
            _visited.add(pair)

        if isinstance(a, ArrayValue):
            if len(a) != len(b):
                return False
            return all(
                self._values_equal(
                    a[i], b[i], _visited, approximate_floats=approximate_floats
                )
                for i in range(len(a))
            )
        if isinstance(a, VecValue):
            if len(a) != len(b):
                return False
            return all(
                self._values_equal(
                    x, y, _visited, approximate_floats=approximate_floats
                )
                for x, y in zip(a._elements, b._elements)
            )
        if isinstance(a, list):
            if len(a) != len(b):
                return False
            return all(
                self._values_equal(
                    x, y, _visited, approximate_floats=approximate_floats
                )
                for x, y in zip(a, b)
            )
        if isinstance(a, tuple):
            if len(a) != len(b):
                return False
            return all(
                self._values_equal(
                    x, y, _visited, approximate_floats=approximate_floats
                )
                for x, y in zip(a, b)
            )
        if isinstance(a, dict):
            if a.keys() != b.keys():
                return False
            return all(
                self._values_equal(
                    a[k], b[k], _visited, approximate_floats=approximate_floats
                )
                for k in a
            )
        if isinstance(a, MutableMapValue):
            if a._data.keys() != b._data.keys():
                return False
            return all(
                self._values_equal(
                    a._data[k],
                    b._data[k],
                    _visited,
                    approximate_floats=approximate_floats,
                )
                for k in a._data
            )
        if isinstance(a, SetValue):
            if len(a) != len(b):
                return False
            if not approximate_floats:
                return bool(a == b)
            left_items = list(a._data)
            right_items = list(b._data)
            edges: list[list[int]] = []
            for item in left_items:
                candidates = []
                for index, candidate in enumerate(right_items):
                    candidate_visited = set(_visited) if _visited is not None else None
                    if self._values_equal(
                        item,
                        candidate,
                        candidate_visited,
                        approximate_floats=True,
                    ):
                        candidates.append(index)
                edges.append(candidates)

            matched_left_by_right: list[int | None] = [None] * len(right_items)

            def find_match(left_index: int, seen: set[int]) -> bool:
                self._step()
                for right_index in edges[left_index]:
                    if right_index in seen:
                        continue
                    seen.add(right_index)
                    matched_left = matched_left_by_right[right_index]
                    if matched_left is None or find_match(matched_left, seen):
                        matched_left_by_right[right_index] = left_index
                        return True
                return False

            for left_index in range(len(left_items)):
                if not find_match(left_index, set()):
                    return False
            return True
        if isinstance(a, ConstructorValue):
            return a.constructor == b.constructor and self._values_equal(
                list(a.fields.values()),
                list(b.fields.values()),
                _visited,
                approximate_floats=approximate_floats,
            )
        return bool(a == b)

    # =========================================================================
    # Step Budget
    # =========================================================================

    # How often (in steps) the cooperative deadline is polled on the step
    # fast path. Checking time.perf_counter() on every step dominated step
    # cost; every 256th step bounds the extra latency past the deadline to
    # ~256 step durations, and function-call/builtin boundaries still check
    # the deadline on every call (_call_function, call_function).
    _TIMEOUT_POLL_MASK = 0xFF

    def _step(self, location: SourceLocation | None = None) -> None:
        """Increment step counter and check budget."""
        steps = self.steps + 1
        self.steps = steps
        if steps > self._step_limit or not (steps & self._TIMEOUT_POLL_MASK):
            self._step_checks(location)

    def _step_checks(self, location: SourceLocation | None = None) -> None:
        """Step slow path: poll the cooperative deadline, enforce the budget.

        The timeout check stays ahead of the step-limit raise so an expired
        deadline reports as a timeout, matching the pre-fast-path order.
        """
        self._check_timeout(location)
        if self.max_steps is not None and self.steps > self.max_steps:
            loc_str = f" at {location}" if location else ""
            raise StepLimitExceeded(
                f"Step limit exceeded ({self.max_steps} steps){loc_str}"
            )

    # =========================================================================
    # Expression Evaluation
    # =========================================================================

    def eval_expr(self, expr: Expression, env: Environment) -> Any:
        """Evaluate an expression and return its value."""
        # Inlined _step fast path: the location lookup and slow-path call
        # are only needed when the budget is exhausted or the deadline poll
        # is due.
        steps = self.steps + 1
        self.steps = steps
        if steps > self._step_limit or not (steps & self._TIMEOUT_POLL_MASK):
            self._step_checks(getattr(expr, "location", None))

        # Recursion depth is checked in _eval_function_call where it
        # matters (before each call).  Checking here on every expression
        # was redundant and added overhead for non-call expressions.

        # Exact-type dispatch: AST nodes inherit from an ABC, so isinstance
        # chains pay ABCMeta.__instancecheck__ per test; a type-keyed table
        # is O(1). Subclasses of node types resolve through the MRO fallback.
        handler = _EXPR_DISPATCH.get(expr.__class__)
        if handler is None:
            handler = _dispatch_via_mro(_EXPR_DISPATCH, expr)
            if handler is None:
                raise RuntimeError(
                    f"Unknown expression type: {type(expr).__name__}", expr.location
                )
        return handler(self, expr, env)

    def _eval_integer_literal(self, expr: IntegerLiteral, env: Environment) -> Any:
        self._check_integer_bits(expr.value, expr.location)
        return expr.value

    def _eval_float_literal(self, expr: FloatLiteral, env: Environment) -> Any:
        return expr.value

    def _eval_string_literal(self, expr: StringLiteral, env: Environment) -> Any:
        self._check_collection_size("String", len(expr.value), expr.location)
        return expr.value

    def _eval_boolean_literal(self, expr: BooleanLiteral, env: Environment) -> Any:
        return expr.value

    def _eval_fstring(self, expr: FStringExpr, env: Environment) -> str:
        parts = []
        for part in expr.parts:
            if isinstance(part, str):
                parts.append(part)
            else:
                value = self.eval_expr(part, env)
                parts.append(_builtins.stringify_value(value))
        result = "".join(parts)
        limit = self.sandbox_config.max_collection_size
        if len(result) > limit:
            raise RuntimeError(
                f"String size exceeds limit ({len(result)} > {limit})",
                expr.location,
            )
        return result

    def _eval_list_literal(self, expr: ListLiteral, env: Environment) -> list:
        self._check_collection_size("List", len(expr.elements), expr.location)
        list_result = [self.eval_expr(e, env) for e in expr.elements]
        self._check_collection_limits([list_result], expr.location)
        return list_result

    def _eval_typed_hole(self, expr: TypedHole, env: Environment) -> Any:
        raise RuntimeError(f"Encountered unfilled hole: ?{expr.name}", expr.location)

    def _eval_identifier(self, expr: Identifier, env: Environment) -> Any:
        """Evaluate an identifier."""
        value = env.lookup(expr.name)
        if value is _UNBOUND:
            raise RuntimeError(f"Undefined variable: {expr.name}", expr.location)
        return value

    def _eval_type_identifier(self, expr: TypeIdentifier, env: Environment) -> Any:
        """Evaluate a type identifier (nullary constructor)."""
        # Check if it's a nullary constructor
        for type_def in self.type_defs.values():
            for variant in type_def.variants:
                if variant.name == expr.name and not variant.fields:
                    return ConstructorValue(expr.name, {})

        # Check if it's a module namespace
        if expr.name in self._module_namespaces:
            return _ModuleNamespace(expr.name)

        raise RuntimeError(f"Unknown constructor: {expr.name}", expr.location)

    def _eval_binary_op(self, expr: BinaryOp, env: Environment) -> Any:
        """Evaluate a binary operation."""
        operator = expr.operator
        if operator == "and":
            left = self.eval_expr(expr.left, env)
            if not left:
                return False
            return bool(self.eval_expr(expr.right, env))

        if operator == "or":
            left = self.eval_expr(expr.left, env)
            if left:
                return True
            return bool(self.eval_expr(expr.right, env))

        left = self.eval_expr(expr.left, env)
        right = self.eval_expr(expr.right, env)

        op_func = _BINOP_FUNCS.get(operator)
        if op_func is None:
            raise RuntimeError(f"Unknown operator: {operator}", expr.location)

        try:
            result = op_func(self, expr, left, right)
        except ZeroDivisionError:
            raise RuntimeError(
                f"Division by zero: cannot compute {self._format_value(left)} {operator} {self._format_value(right)}",
                expr.location,
            )
        except OverflowError as e:
            message = "Exponentiation result too large" if operator == "**" else str(e)
            raise RuntimeError(message, expr.location)
        except (TypeError, ValueError) as e:
            raise RuntimeError(str(e), expr.location)

        # Guard against integer arithmetic bombs (e.g. acc * acc on huge ints)
        max_bits = self.sandbox_config.max_integer_bits
        if isinstance(result, complex):
            raise RuntimeError(
                "Exponentiation result is not a real number",
                expr.location,
            )
        if isinstance(result, int) and result.bit_length() > max_bits:
            raise RuntimeError(
                f"Integer exceeds maximum size ({result.bit_length()} bits)",
                expr.location,
            )

        return result

    def _eval_unary_op(self, expr: UnaryOp, env: Environment) -> Any:
        """Evaluate a unary operation."""
        operand = self.eval_expr(expr.operand, env)

        if expr.operator == "-":
            return -operand
        elif expr.operator == "not":
            return not operand
        elif expr.operator == "~":
            return ~operand

        raise RuntimeError(f"Unknown unary operator: {expr.operator}", expr.location)

    def _eval_function_call(self, expr: FunctionCall, env: Environment) -> Any:
        """Evaluate a function call."""
        # Trait method dispatch: check if this is a trait method call.
        # When dispatch matches we call the impl directly. When it does not
        # (first positional argument is not an ADT), we fall through to the
        # generic path while reusing the already-evaluated first argument so
        # its side effects run only once.
        trait_first_arg: Any = _UNFILLED
        trait_named_evaluated_args: list[tuple[str | None, Any]] | None = None
        # trait_method_names is empty for most programs — test it first so
        # the common case skips the (ABC-slow) isinstance probe.
        if (
            self.trait_method_names
            and expr.arguments
            and isinstance(expr.function, Identifier)
            and expr.function.name in self.trait_method_names
        ):
            method_name = expr.function.name
            has_named_args = any(arg.name for arg in expr.arguments)
            if has_named_args:
                trait_named_evaluated_args = [
                    (arg.name, self.eval_expr(arg.value, env)) for arg in expr.arguments
                ]
                resolved_type_name: str | None = None
                for (trait_name, target_type), methods in self.trait_impls.items():
                    if method_name not in methods:
                        continue
                    param_names = self.trait_method_param_names.get(
                        (trait_name, method_name)
                    )
                    if param_names is None:
                        continue
                    try:
                        dispatch_args = self._reorder_evaluated_args(
                            trait_named_evaluated_args,
                            param_names,
                            expr.location,
                        )
                    except RuntimeError:
                        continue
                    if dispatch_args and isinstance(dispatch_args[0], ConstructorValue):
                        # Find which type this constructor belongs to (O(1) lookup)
                        resolved_type_name = self._constructor_to_type.get(
                            dispatch_args[0].constructor
                        )
                        if resolved_type_name == target_type:
                            impl_closure = methods[method_name]
                            return self._call_function(
                                impl_closure, dispatch_args, expr.location
                            )
                if resolved_type_name is not None:
                    raise RuntimeError(
                        f"No trait implementation of '{method_name}' "
                        f"for type '{resolved_type_name}'",
                        expr.location,
                    )
            else:
                type_name: str | None = None
                trait_first_arg = self.eval_expr(expr.arguments[0].value, env)
                if isinstance(trait_first_arg, ConstructorValue):
                    # Find which type this constructor belongs to (O(1) lookup)
                    type_name = self._constructor_to_type.get(
                        trait_first_arg.constructor
                    )

                if type_name is not None:
                    # Search trait_impls for a matching implementation
                    for (_tn, tt), methods in self.trait_impls.items():
                        if tt == type_name and method_name in methods:
                            impl_closure = methods[method_name]
                            remaining_args = [
                                self.eval_expr(arg.value, env)
                                for arg in expr.arguments[1:]
                            ]
                            return self._call_function(
                                impl_closure,
                                [trait_first_arg] + remaining_args,
                                expr.location,
                            )
                    raise RuntimeError(
                        f"No trait implementation of '{method_name}' "
                        f"for type '{type_name}'",
                        expr.location,
                    )

        func = self.eval_expr(expr.function, env)

        # Handle named arguments by reordering them to match parameter order
        # (plain loop: a generator expression here costs a frame per call)
        has_named_args = False
        for call_arg in expr.arguments:
            if call_arg.name:
                has_named_args = True
                break
        if has_named_args:
            if isinstance(func, Closure):
                param_names = [p.name for p in func.params]
                if trait_named_evaluated_args is not None:
                    args = self._reorder_evaluated_args(
                        trait_named_evaluated_args,
                        param_names,
                        expr.location,
                    )
                else:
                    args = self._reorder_named_args(
                        expr.arguments, param_names, env, expr.location
                    )
            elif isinstance(func, BuiltinFunction):
                param_names = func.param_names
                if trait_named_evaluated_args is not None:
                    args = self._reorder_evaluated_args(
                        trait_named_evaluated_args,
                        param_names,
                        expr.location,
                    )
                else:
                    args = self._reorder_named_args(
                        expr.arguments, param_names, env, expr.location
                    )
            else:
                # Named args not supported for other callable types
                raise RuntimeError(
                    "Named arguments are only supported for direct function calls, "
                    "not for lambda expressions or function values",
                    expr.location,
                )
        elif trait_first_arg is not _UNFILLED:
            # Trait dispatch was probed but did not match. Reuse the
            # already-evaluated first argument so its side effects run once.
            args = [trait_first_arg] + [
                self.eval_expr(arg.value, env) for arg in expr.arguments[1:]
            ]
        else:
            args = [self.eval_expr(arg.value, env) for arg in expr.arguments]

        return self._call_function(func, args, expr.location)

    def _reorder_named_args(
        self,
        call_args: list[CallArg],
        param_names: list[str],
        env: Environment,
        location: SourceLocation | None,
    ) -> list[Any]:
        """Reorder arguments based on named parameters.

        Returns the full-length result array with _UNFILLED sentinels for
        positions that were not provided — _call_function fills those from
        default values, preserving correct positional mapping.
        """
        evaluated = [(arg.name, self.eval_expr(arg.value, env)) for arg in call_args]
        return self._reorder_evaluated_args(evaluated, param_names, location)

    def _reorder_evaluated_args(
        self,
        evaluated_args: list[tuple[str | None, Any]],
        param_names: list[str],
        location: SourceLocation | None,
    ) -> list[Any]:
        """Reorder already-evaluated arguments based on named parameters."""
        result: list[Any] = [_UNFILLED] * len(param_names)
        used_positions: set[int] = set()
        positional_index = 0

        for arg_name, arg_value in evaluated_args:
            if arg_name is not None:
                if arg_name not in param_names:
                    raise RuntimeError(f"Unknown parameter name: {arg_name}", location)
                pos = param_names.index(arg_name)
                if pos in used_positions:
                    raise RuntimeError(
                        f"Duplicate argument for parameter: {arg_name}", location
                    )
                result[pos] = arg_value
                used_positions.add(pos)
            else:
                while positional_index in used_positions:
                    positional_index += 1
                if positional_index >= len(param_names):
                    raise RuntimeError("Too many positional arguments", location)
                result[positional_index] = arg_value
                used_positions.add(positional_index)
                positional_index += 1

        return result

    def _check_collection_limits(
        self, roots: list[Any], location: SourceLocation | None
    ) -> None:
        """Enforce ``max_collection_size`` on every reachable container.

        The previous implementation only inspected the top-level builtin
        arguments and return value, so nested containers (e.g. a small outer
        list holding a million-element inner list) could exceed the sandbox
        limit. Walk the reachable graph depth-first, tracking visited object
        ids to handle cyclic structures in ``Vec`` / ``MutableMap`` / etc.

        Regression for #661 / F-0026.

        Scalar-only root sets (the common case for function arguments and
        return values) are handled without allocating the visited set and
        work stack; any container or unrecognised root falls through to
        the full reachability walk.
        """
        max_bits = self.sandbox_config.max_integer_bits
        size_limit = self.sandbox_config.max_collection_size
        for value in roots:
            t = type(value)
            if t is int:
                if value.bit_length() > max_bits:
                    self._check_integer_bits(value, location)
            elif t is str:
                if len(value) > size_limit:
                    self._check_collection_size("String", len(value), location)
            elif not (t is bool or t is float or value is None):
                break
        else:
            return

        visited: set[int] = set()
        stack: list[Any] = list(roots)
        while stack:
            value = stack.pop()
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                self._check_integer_bits(value, location)
                continue

            value_id = id(value)
            if value_id in visited:
                continue

            if isinstance(value, MutableMapValue):
                visited.add(value_id)
                size = len(value)
                kind = "MutableMap"
            elif isinstance(value, dict):
                visited.add(value_id)
                size = len(value)
                kind = "Map"
            elif isinstance(value, SetValue):
                visited.add(value_id)
                size = len(value)
                kind = "Set"
            elif isinstance(value, VecValue):
                visited.add(value_id)
                size = len(value)
                kind = "Vec"
            elif isinstance(value, ArrayValue):
                visited.add(value_id)
                size = len(value)
                kind = "Array"
            elif isinstance(value, list):
                visited.add(value_id)
                size = len(value)
                kind = "List"
            elif isinstance(value, tuple):
                visited.add(value_id)
                size = len(value)
                kind = "Tuple"
            elif isinstance(value, ConstructorValue):
                visited.add(value_id)
                stack.extend(value.fields.values())
                continue
            elif isinstance(value, str):
                visited.add(value_id)
                size = len(value)
                kind = "String"
            else:
                continue

            self._check_collection_size(kind, size, location)

            if isinstance(value, (list, tuple)):
                stack.extend(value)
            elif isinstance(value, (ArrayValue, VecValue)):
                stack.extend(value._elements)
            elif isinstance(value, SetValue):
                stack.extend(value._data)
            elif isinstance(value, MutableMapValue):
                stack.extend(value._data.keys())
                stack.extend(value._data.values())
            elif isinstance(value, dict):
                stack.extend(value.keys())
                stack.extend(value.values())

    def _check_collection_size(
        self, kind: str, size: int, location: SourceLocation | None
    ) -> None:
        """Raise when a known collection size exceeds the sandbox limit."""
        limit = self.sandbox_config.max_collection_size
        if size > limit:
            raise RuntimeError(
                f"{kind} size exceeds limit ({size} > {limit})",
                location,
            )

    def _check_integer_bits(self, value: int, location: SourceLocation | None) -> None:
        """Raise when an integer value exceeds the sandbox bit-length limit."""
        limit = self.sandbox_config.max_integer_bits
        if value.bit_length() > limit:
            raise RuntimeError(
                f"Integer exceeds maximum size ({value.bit_length()} bits)",
                location,
            )

    def _call_function(
        self, func: Any, args: list[Any], location: SourceLocation | None = None
    ) -> Any:
        """Call a function with arguments.

        Tracks recursion depth so that calls originating from builtins
        (map, filter, fold, etc.) are counted towards the depth limit.
        """
        self._check_timeout(location)
        if isinstance(func, Closure):
            if self.call_depth >= self.max_recursion_depth:
                # RecursionLimitError (a SandboxError) is intentionally not
                # GenoRuntimeError so user-level try/catch cannot swallow
                # the limit and continue executing (issue #650).
                raise RecursionLimitError(
                    f"Maximum recursion depth exceeded ({self.max_recursion_depth}). "
                    f"Check for infinite recursion or increase the limit."
                )
            self.call_depth += 1
            try:
                # Fill in default values while this invocation counts toward the
                # recursion limit. Recursive defaults should raise a Geno error
                # instead of escaping as a host-language RecursionError.
                if len(args) < len(func.params):
                    # Trailing args omitted — append defaults
                    filled_args = list(args)
                    for i in range(len(args), len(func.params)):
                        if func.params[i].default_value is not None:
                            default = func.params[i].default_value
                            assert default is not None
                            filled_args.append(self.eval_expr(default, func.env))
                        else:
                            break
                    args = filled_args
                # Handle _UNFILLED sentinels from named-arg reordering
                for i, arg in enumerate(args):
                    if arg is _UNFILLED:
                        if func.params[i].default_value is not None:
                            default = func.params[i].default_value
                            assert default is not None
                            args[i] = self.eval_expr(default, func.env)
                        else:
                            raise RuntimeError(
                                f"Missing argument for parameter '{func.params[i].name}'",
                                location,
                            )

                if len(args) != len(func.params):
                    raise RuntimeError(
                        f"Expected {len(func.params)} arguments, got {len(args)}",
                        location,
                    )

                self._check_collection_limits(args, location)

                # For async functions, return an AsyncValue instead of executing
                if func.is_async:
                    return AsyncValue(func, list(args))

                self._call_stack.append((func.name or "<anonymous>", location))
                try:
                    # Create new environment with parameters bound
                    # (immutable binds are plain dict entries, so build the
                    # frame directly instead of child() plus bind() per param)
                    call_env = Environment(
                        {param.name: arg for param, arg in zip(func.params, args)},
                        parent=func.env,
                    )

                    # Check preconditions (requires clauses). An empty
                    # SpecBlock is truthy — gate on the clause list so
                    # spec-free functions skip the check call entirely.
                    specs = func.specs
                    if specs is not None and specs.requires:
                        self._check_requires(func, call_env)

                    # Execute body
                    try:
                        for stmt in func.body:
                            self.exec_stmt(stmt, call_env)
                        result = None  # No explicit return
                    except ReturnException as ret:
                        result = ret.value
                    except PropagateException as prop:
                        result = prop.value
                    except BreakException:
                        raise RuntimeError("'break' outside of loop", location)
                    except ContinueException:
                        raise RuntimeError("'continue' outside of loop", location)

                    # Check postconditions (ensures clauses); gated on the
                    # clause list — _check_ensures allocates an environment
                    # even when there is nothing to check.
                    if specs is not None and specs.ensures:
                        self._check_ensures(func, call_env, result)

                    self._check_collection_limits([result], location)
                    self._check_timeout(location)
                    return result
                except RuntimeError as e:
                    if not hasattr(e, "_geno_stack"):
                        e._geno_stack = list(self._call_stack)  # type: ignore[attr-defined]
                    raise
                finally:
                    self._call_stack.pop()
            finally:
                self.call_depth -= 1

        elif isinstance(func, BuiltinFunction):
            if func.arity >= 0 and len(args) != func.arity:
                raise RuntimeError(
                    f"Builtin {func.name} expects {func.arity} arguments, got {len(args)}",
                    location,
                )
            incremental_mutation = func.name in _INCREMENTAL_MUTATION_BUILTINS
            checked_args = args[1:] if incremental_mutation else args
            self._check_collection_limits(checked_args, location)
            self._step()
            try:
                result = func.func(*args)
            except SandboxError:
                raise
            except (RuntimeError, GenoRuntimeError):
                raise
            except _EXPECTED_BUILTIN_ERRORS as e:
                # Ordinary user-facing error from a builtin (bad argument,
                # arithmetic, lookup): surface as a Geno runtime error with no
                # Python traceback, as before.
                raise RuntimeError(str(e), location) from None
            except Exception as e:
                # An unexpected exception type reaching here is an internal
                # builtin/interpreter defect, not a user-program error. It is
                # still collapsed to a user-style error (unchanged surface
                # behavior), but log it with its traceback first so the bug is
                # diagnosable instead of vanishing silently (M-12).
                logger.exception(
                    "Internal error in builtin %r at %s: %s",
                    func.name,
                    location,
                    e,
                )
                raise RuntimeError(str(e), location) from None
            result_roots = [result] if incremental_mutation else [result, *args]
            self._check_collection_limits(result_roots, location)
            self._check_timeout(location)
            return result

        else:
            raise RuntimeError(
                f"Cannot call non-function: {type(func).__name__}", location
            )

    def _check_requires(self, func: Closure, env: Environment) -> None:
        """Check all requires (precondition) clauses."""
        assert func.specs is not None
        for req in func.specs.requires:
            try:
                condition = self.eval_expr(req.condition, env)
            except PropagateException:
                raise ContractViolationError(
                    f"Precondition failed for {func.name or 'function'}: "
                    f"requires clause cannot use '?' to propagate None/Err",
                    req.condition.location,
                ) from None
            if not condition:
                raise ContractViolationError(
                    f"Precondition failed for {func.name or 'function'}: "
                    f"requires clause evaluated to false",
                    req.condition.location,
                )

    def _check_ensures(self, func: Closure, env: Environment, result: Any) -> None:
        """Check all ensures (postcondition) clauses."""
        assert func.specs is not None
        # Create environment with 'result' bound to the return value
        ensures_env = env.child()
        ensures_env.bind("result", result)

        for ens in func.specs.ensures:
            try:
                condition = self.eval_expr(ens.condition, ensures_env)
            except PropagateException:
                raise ContractViolationError(
                    f"Postcondition failed for {func.name or 'function'}: "
                    f"ensures clause cannot use '?' to propagate None/Err",
                    ens.condition.location,
                ) from None
            if not condition:
                raise ContractViolationError(
                    f"Postcondition failed for {func.name or 'function'}: "
                    f"ensures clause evaluated to false (result was {self._format_value(result)})",
                    ens.condition.location,
                )

    def _eval_index_access(self, expr: IndexAccess, env: Environment) -> Any:
        """Evaluate index access."""
        target = self.eval_expr(expr.target, env)
        index = self.eval_expr(expr.index, env)

        if isinstance(target, list):
            if not isinstance(index, int):
                raise RuntimeError("List index must be integer", expr.location)
            if index < 0:
                index += len(target)
            if index < 0 or index >= len(target):
                raise RuntimeError(
                    f"Index {index} out of bounds for list of length {len(target)}",
                    expr.location,
                )
            return target[index]
        elif isinstance(target, ArrayValue):
            if not isinstance(index, int):
                raise RuntimeError("Array index must be integer", expr.location)
            if index < 0:
                index += len(target)
            if index < 0 or index >= len(target):
                raise RuntimeError(
                    f"Index {index} out of bounds for array of length {len(target)}",
                    expr.location,
                )
            return target[index]
        elif isinstance(target, str):
            if not isinstance(index, int):
                raise RuntimeError("String index must be integer", expr.location)
            if index < 0:
                index += len(target)
            if index < 0 or index >= len(target):
                raise RuntimeError(
                    f"Index {index} out of bounds for string of length {len(target)}",
                    expr.location,
                )
            return target[index]
        elif isinstance(target, dict):
            if index not in target:
                raise RuntimeError(f"Key not found: {index}", expr.location)
            return target[index]
        else:
            raise RuntimeError(
                f"Cannot index into {type(target).__name__}", expr.location
            )

    def _eval_field_access(self, expr: FieldAccess, env: Environment) -> Any:
        """Evaluate field access."""
        target = self.eval_expr(expr.target, env)

        if isinstance(target, _ModuleNamespace):
            ns = self._module_namespaces.get(target.name, {})
            if expr.field_name in ns:
                return ns[expr.field_name]
            raise RuntimeError(
                f"Module '{target.name}' has no symbol '{expr.field_name}'",
                expr.location,
            )

        if isinstance(target, ConstructorValue):
            if expr.field_name in target.fields:
                return target.fields[expr.field_name]
            raise RuntimeError(
                f"No field '{expr.field_name}' on {target.constructor}", expr.location
            )

        raise RuntimeError(
            f"Cannot access field on {type(target).__name__}", expr.location
        )

    def _eval_propagate(self, expr: PropagateExpr, env: Environment) -> Any:
        """Evaluate the ? propagation operator."""
        value = self.eval_expr(expr.operand, env)
        if not isinstance(value, ConstructorValue):
            raise RuntimeError(
                f"'?' requires Option or Result value, got {type(value).__name__}",
                expr.location,
            )
        if value.constructor == "Some":
            return value.fields["value"]
        elif value.constructor == "None":
            raise PropagateException(ConstructorValue("None", {}))
        elif value.constructor == "Ok":
            return value.fields["value"]
        elif value.constructor == "Err":
            raise PropagateException(value)
        else:
            raise RuntimeError(
                f"'?' requires Option or Result value, got {value.constructor}(...)",
                expr.location,
            )

    def _eval_with_expr(self, expr: WithExpr, env: Environment) -> Any:
        """Evaluate a with expression: create a copy with updated fields."""
        target = self.eval_expr(expr.target, env)

        if not isinstance(target, ConstructorValue):
            raise RuntimeError(
                f"'with' requires a constructor value, got {type(target).__name__}",
                expr.location,
            )

        new_fields = dict(target.fields)
        for field_name, value_expr in expr.updates:
            new_fields[field_name] = self.eval_expr(value_expr, env)

        return ConstructorValue(target.constructor, new_fields)

    def _eval_list_comprehension(
        self, expr: ListComprehension, env: Environment
    ) -> Any:
        """Evaluate a list comprehension."""
        iterable = self.eval_expr(expr.iterable, env)
        if not isinstance(iterable, list):
            raise RuntimeError(
                f"List comprehension requires a list, got {type(iterable).__name__}",
                expr.iterable.location,
            )
        result: list[Any] = []
        for item in iterable:
            comp_env = env.child()
            comp_env.bind(expr.variable, item)
            if expr.condition is not None:
                cond = self.eval_expr(expr.condition, comp_env)
                if not cond:
                    continue
            if len(result) >= self.sandbox_config.max_collection_size:
                self._check_collection_size("List", len(result) + 1, expr.location)
            item = self.eval_expr(expr.element_expr, comp_env)
            self._check_collection_limits([item], expr.element_expr.location)
            result.append(item)
        return result

    def _eval_throw(self, expr: ThrowExpression, env: Environment) -> Any:
        """Evaluate a throw expression."""
        value = self.eval_expr(expr.value, env)
        if isinstance(value, str):
            raise GenoRuntimeError(value, expr.location)
        raise GenoThrowError(value, expr.location)

    def _eval_await(self, expr: AwaitExpr, env: Environment) -> Any:
        """Evaluate an await expression — execute the async value synchronously."""
        value = self.eval_expr(expr.expr, env)
        if isinstance(value, AsyncValue):
            # Execute the async closure's body directly (bypass is_async check)
            func = value.closure
            call_env = func.env.child()
            for param, arg in zip(func.params, value.args):
                call_env.bind(param.name, arg)
            if func.specs:
                self._check_requires(func, call_env)
            if self.call_depth >= self.max_recursion_depth:
                # See _call_function — RecursionLimitError is uncatchable
                # by user try/catch (issue #650).
                raise RecursionLimitError(
                    f"Maximum recursion depth exceeded ({self.max_recursion_depth}). "
                    f"Check for infinite recursion or increase the limit."
                )
            self.call_depth += 1
            try:
                try:
                    for stmt in func.body:
                        self.exec_stmt(stmt, call_env)
                    result = None
                except ReturnException as ret:
                    result = ret.value
                except PropagateException as prop:
                    result = prop.value
                except BreakException:
                    raise RuntimeError("'break' outside of loop", expr.location)
                except ContinueException:
                    raise RuntimeError("'continue' outside of loop", expr.location)
                if func.specs:
                    self._check_ensures(func, call_env, result)
                self._check_collection_limits([result], expr.location)
                return result
            finally:
                self.call_depth -= 1
        # If not an AsyncValue, return as-is (identity await)
        return value

    def _eval_pipeline(self, expr: Pipeline, env: Environment) -> Any:
        """Evaluate a pipeline expression."""
        current = self.eval_expr(expr.initial, env)

        for stage in expr.stages:
            func = self.eval_expr(stage.function, env)

            # Build argument list, replacing placeholders with current value
            args = []
            placeholder_found = False
            for arg_expr in stage.arguments:
                if isinstance(arg_expr, PlaceholderExpr):
                    args.append(current)
                    placeholder_found = True
                else:
                    args.append(self.eval_expr(arg_expr, env))

            # If no placeholder, prepend current value
            if not placeholder_found:
                args = [current] + args

            current = self._call_function(func, args, stage.location)

        return current

    def _eval_lambda(self, expr: LambdaExpr, env: Environment) -> Closure:
        """Evaluate a lambda expression."""
        if expr.block_body is not None:
            body = expr.block_body
        else:
            assert expr.body is not None
            body = [ReturnStatement(location=expr.location, value=expr.body)]
        return Closure(
            params=expr.params,
            body=body,
            env=env,
        )

    def _eval_constructor_call(
        self, expr: ConstructorCall, env: Environment
    ) -> ConstructorValue:
        """Evaluate a constructor call.

        Built-in type variants (Option, Result, JsonValue, HttpResponse,
        HttpRequest, ProcessResult) are registered in ``type_defs`` at init
        time so they are resolved dynamically through the same path as
        user-defined types.
        """
        parent_type = self._constructor_to_type.get(expr.constructor)
        if parent_type is not None:
            type_def = self.type_defs[parent_type]
            for variant in type_def.variants:
                if variant.name == expr.constructor:
                    if len(expr.arguments) != len(variant.fields):
                        raise RuntimeError(
                            f"Constructor {expr.constructor} expects "
                            f"{len(variant.fields)} arguments, got {len(expr.arguments)}",
                            expr.location,
                        )

                    fields = {}
                    for arg, (field_name, _) in zip(expr.arguments, variant.fields):
                        fields[field_name] = self.eval_expr(arg, env)

                    return ConstructorValue(expr.constructor, fields)

        raise RuntimeError(f"Unknown constructor: {expr.constructor}", expr.location)

    def _eval_tuple(self, expr: TupleExpr, env: Environment) -> tuple:
        """Evaluate a tuple expression."""
        if not expr.elements:
            return ()  # Unit
        self._check_collection_size("Tuple", len(expr.elements), expr.location)
        result = tuple(self.eval_expr(e, env) for e in expr.elements)
        self._check_collection_limits([result], expr.location)
        return result

    def _eval_match_expr(self, expr: MatchExpr, env: Environment) -> Any:
        """Evaluate a match expression."""
        scrutinee = self.eval_expr(expr.scrutinee, env)

        for arm in expr.arms:
            bindings = self._match_pattern(arm.pattern, scrutinee)
            if bindings is not None:
                arm_env = env.child()
                for name, value in bindings.items():
                    arm_env.bind(name, value)

                # Check guard condition if present
                if arm.guard is not None:
                    guard_result = self.eval_expr(arm.guard, arm_env)
                    if not guard_result:
                        continue

                # Execute arm body
                try:
                    for stmt in arm.body:
                        self.exec_stmt(stmt, arm_env)
                    return None
                except ReturnException as ret:
                    return _promote_int_to_expected_float(
                        ret.value, getattr(expr, "_resolved_type", None)
                    )

        raise RuntimeError(
            f"No matching pattern in match expression for value {self._format_value(scrutinee)}",
            expr.location,
        )

    # =========================================================================
    # Pattern Matching
    # =========================================================================

    def _match_pattern(self, pattern: Pattern, value: Any) -> dict[str, Any] | None:
        """
        Try to match a pattern against a value.

        Each recursive call counts as a step so that deeply nested pattern
        matching respects the step budget and cooperative timeout.

        Returns:
            Dictionary of bindings if match succeeds, None otherwise
        """
        self._step()
        if isinstance(pattern, WildcardPattern):
            return {}

        if isinstance(pattern, VariablePattern):
            return {pattern.name: value}

        if isinstance(pattern, LiteralPattern):
            if type(pattern.value) is int:
                self._check_integer_bits(pattern.value, pattern.location)
            if self._values_equal(pattern.value, value):
                return {}
            return None

        if isinstance(pattern, ConstructorPattern):
            if not isinstance(value, ConstructorValue):
                return None
            if value.constructor != pattern.constructor:
                return None
            if len(pattern.subpatterns) != len(value.fields):
                return None

            bindings = {}
            field_values = list(value.fields.values())
            for subpat, field_val in zip(pattern.subpatterns, field_values):
                sub_bindings = self._match_pattern(subpat, field_val)
                if sub_bindings is None:
                    return None
                bindings.update(sub_bindings)

            return bindings

        if isinstance(pattern, ListPattern):
            if not isinstance(value, list):
                return None

            # Check if pattern contains a rest element
            rest_index = None
            for i, elem_pat in enumerate(pattern.elements):
                if isinstance(elem_pat, RestPattern):
                    rest_index = i
                    break

            if rest_index is not None:
                # Pattern has a rest element
                fixed_before = rest_index
                fixed_after = len(pattern.elements) - rest_index - 1
                min_required = fixed_before + fixed_after
                if len(value) < min_required:
                    return None

                bindings = {}
                # Match elements before rest
                for i in range(fixed_before):
                    sub_bindings = self._match_pattern(pattern.elements[i], value[i])
                    if sub_bindings is None:
                        return None
                    bindings.update(sub_bindings)

                # Bind rest
                rest_pat = pattern.elements[rest_index]
                rest_values = value[
                    fixed_before : len(value) - fixed_after
                    if fixed_after > 0
                    else len(value)
                ]
                if isinstance(rest_pat, RestPattern) and rest_pat.name is not None:
                    bindings[rest_pat.name] = rest_values

                # Match elements after rest
                for i in range(fixed_after):
                    pat_idx = rest_index + 1 + i
                    val_idx = len(value) - fixed_after + i
                    sub_bindings = self._match_pattern(
                        pattern.elements[pat_idx], value[val_idx]
                    )
                    if sub_bindings is None:
                        return None
                    bindings.update(sub_bindings)
            else:
                # No rest element - exact length match required
                if len(pattern.elements) != len(value):
                    return None

                bindings = {}
                for elem_pat, elem_val in zip(pattern.elements, value):
                    sub_bindings = self._match_pattern(elem_pat, elem_val)
                    if sub_bindings is None:
                        return None
                    bindings.update(sub_bindings)

            return bindings

        return None

    # =========================================================================
    # Statement Execution
    # =========================================================================

    def exec_stmt(self, stmt: Statement, env: Environment) -> None:
        """Execute a statement."""
        # Inlined _step fast path; see eval_expr.
        steps = self.steps + 1
        self.steps = steps
        if steps > self._step_limit or not (steps & self._TIMEOUT_POLL_MASK):
            self._step_checks(getattr(stmt, "location", None))

        # Exact-type dispatch; see eval_expr for rationale.
        handler = _STMT_DISPATCH.get(stmt.__class__)
        if handler is None:
            handler = _dispatch_via_mro(_STMT_DISPATCH, stmt)
            if handler is None:
                raise RuntimeError(
                    f"Unknown statement type: {type(stmt).__name__}", stmt.location
                )
        handler(self, stmt, env)

    def _exec_break(self, stmt: BreakStatement, env: Environment) -> None:
        raise BreakException()

    def _exec_continue(self, stmt: ContinueStatement, env: Environment) -> None:
        raise ContinueException()

    def _exec_expression_stmt(
        self, stmt: ExpressionStatement, env: Environment
    ) -> None:
        self.eval_expr(stmt.expression, env)

    def _exec_assert(self, stmt: AssertStatement, env: Environment) -> None:
        result = self.eval_expr(stmt.expression, env)
        if not result:
            raise RuntimeError("Assertion failed", stmt.location)

    @staticmethod
    def _deep_copy_value(value: Any, memo: dict[int, Any] | None = None) -> Any:
        """Deep-copy value containers while preserving explicit reference types."""
        if memo is None:
            memo = {}

        if isinstance(value, list):
            value_id = id(value)
            if value_id in memo:
                return memo[value_id]
            copied_list: list[Any] = []
            memo[value_id] = copied_list
            copied_list.extend(Interpreter._deep_copy_value(v, memo) for v in value)
            return copied_list
        if isinstance(value, dict):
            value_id = id(value)
            if value_id in memo:
                return memo[value_id]
            copied_dict: dict[Any, Any] = {}
            memo[value_id] = copied_dict
            for key, nested_value in value.items():
                copied_dict[key] = Interpreter._deep_copy_value(nested_value, memo)
            return copied_dict
        if isinstance(value, tuple):
            return tuple(Interpreter._deep_copy_value(v, memo) for v in value)
        if isinstance(value, ConstructorValue):
            value_id = id(value)
            if value_id in memo:
                return memo[value_id]
            copied_ctor = ConstructorValue(value.constructor, {})
            memo[value_id] = copied_ctor
            new_fields = {
                k: Interpreter._deep_copy_value(v, memo)
                for k, v in value.fields.items()
            }
            object.__setattr__(copied_ctor, "_fields", MappingProxyType(new_fields))
            return copied_ctor
        # Scalars and explicit reference types (Array/Vec/MutableMap/Set/Closure/etc.)
        # intentionally preserve identity across bindings.
        return value

    def _exec_let(self, stmt: LetStatement, env: Environment) -> None:
        """Execute a let statement."""
        value = self.eval_expr(stmt.value, env)
        value = _promote_int_to_expected_float(
            value, getattr(stmt, "_expected_runtime_type", stmt.type_annotation)
        )
        value = self._deep_copy_value(value)
        env.bind(stmt.name, value, mutable=False)

    def _exec_var(self, stmt: VarStatement, env: Environment) -> None:
        """Execute a var statement."""
        value = self.eval_expr(stmt.value, env)
        value = _promote_int_to_expected_float(
            value, getattr(stmt, "_expected_runtime_type", stmt.type_annotation)
        )
        value = self._deep_copy_value(value)
        env.bind(stmt.name, value, mutable=True)

    def _exec_tuple_destructure(
        self, stmt: TupleDestructureStatement, env: Environment
    ) -> None:
        """Execute a tuple destructuring statement."""
        value = self.eval_expr(stmt.value, env)
        if not isinstance(value, tuple) or len(value) != len(stmt.names):
            raise RuntimeError(
                f"Expected {len(stmt.names)}-element tuple, got {type(value).__name__}",
                stmt.location,
            )
        for name, elem in zip(stmt.names, value):
            env.bind(name, elem, mutable=stmt.mutable)

    def _exec_assign(self, stmt: AssignStatement, env: Environment) -> None:
        """Execute an assignment."""
        value = self.eval_expr(stmt.value, env)
        value = _promote_int_to_expected_float(
            value, getattr(stmt, "_expected_runtime_type", None)
        )
        if not env.assign(stmt.target, value):
            raise RuntimeError(
                f"Cannot assign to '{stmt.target}': not mutable or not defined",
                stmt.location,
            )

    def _exec_index_assign(self, stmt: IndexAssignStatement, env: Environment) -> None:
        """Execute an index assignment: arr[i] = value."""
        target = self.eval_expr(stmt.target, env)
        index = self.eval_expr(stmt.index, env)
        value = self.eval_expr(stmt.value, env)
        self._check_collection_limits([index, value], stmt.location)

        if isinstance(target, ArrayValue):
            if not isinstance(index, int):
                raise RuntimeError("Array index must be integer", stmt.location)
            if index < 0 or index >= len(target):
                raise RuntimeError(
                    f"Index {index} out of bounds for array of length {len(target)}",
                    stmt.location,
                )
            target[index] = value
        elif isinstance(target, VecValue):
            if not isinstance(index, int):
                raise RuntimeError("Vec index must be integer", stmt.location)
            if index < 0 or index >= len(target._elements):
                raise RuntimeError(
                    f"Index {index} out of bounds for vec of length {len(target._elements)}",
                    stmt.location,
                )
            target._elements[index] = value
        elif isinstance(target, MutableMapValue):
            try:
                is_new_key = index not in target._data
            except TypeError:
                raise RuntimeError(
                    f"MutableMap key must be hashable, got {type(index).__name__}",
                    stmt.location,
                ) from None
            if is_new_key:
                self._check_collection_size(
                    "MutableMap", len(target._data) + 1, stmt.location
                )
            target._data[index] = value
        else:
            raise RuntimeError(
                f"Cannot use index assignment on {type(target).__name__}",
                stmt.location,
            )

    def _exec_field_assign(self, stmt: FieldAssignStatement, env: Environment) -> None:
        """Execute a field assignment: obj.field = value."""
        target = self.eval_expr(stmt.target, env)
        value = self.eval_expr(stmt.value, env)

        if isinstance(target, ConstructorValue):
            if stmt.field_name in target.fields:
                # Mutate by creating a new fields dict and replacing _fields
                from types import MappingProxyType

                new_fields = dict(target.fields)
                new_fields[stmt.field_name] = value
                object.__setattr__(target, "_fields", MappingProxyType(new_fields))
            else:
                raise RuntimeError(
                    f"No field '{stmt.field_name}' on {target.constructor}",
                    stmt.location,
                )
        else:
            raise RuntimeError(
                f"Cannot assign field on {type(target).__name__}", stmt.location
            )

    def _exec_if(self, stmt: IfStatement, env: Environment) -> None:
        """Execute an if statement."""
        condition = self.eval_expr(stmt.condition, env)
        if condition:
            block_env = env.child()
            for s in stmt.then_body:
                self.exec_stmt(s, block_env)
        else:
            block_env = env.child()
            for s in stmt.else_body:
                self.exec_stmt(s, block_env)

    def _exec_while(self, stmt: WhileStatement, env: Environment) -> None:
        """Execute a while loop."""
        while self.eval_expr(stmt.condition, env):
            body_env = env.child()
            try:
                for s in stmt.body:
                    self.exec_stmt(s, body_env)
            except BreakException:
                break
            except ContinueException:
                continue

    def _exec_for(self, stmt: ForStatement, env: Environment) -> None:
        """Execute a for loop."""
        iterable = self.eval_expr(stmt.iterable, env)
        if not isinstance(iterable, (list, ArrayValue)):
            raise RuntimeError(
                f"Cannot iterate over {type(iterable).__name__}", stmt.location
            )

        # Snapshot array elements so mutations during iteration are safe
        items = (
            list(iterable._elements) if isinstance(iterable, ArrayValue) else iterable
        )
        for item in items:
            body_env = env.child()
            body_env.bind(stmt.variable, item)
            try:
                for s in stmt.body:
                    self.exec_stmt(s, body_env)
            except BreakException:
                break
            except ContinueException:
                continue

    def _exec_match(self, stmt: MatchStatement, env: Environment) -> None:
        """Execute a match statement."""
        scrutinee = self.eval_expr(stmt.scrutinee, env)

        for arm in stmt.arms:
            bindings = self._match_pattern(arm.pattern, scrutinee)
            if bindings is not None:
                arm_env = env.child()
                for name, value in bindings.items():
                    arm_env.bind(name, value)

                # Check guard condition if present
                if arm.guard is not None:
                    guard_result = self.eval_expr(arm.guard, arm_env)
                    if not guard_result:
                        continue

                for s in arm.body:
                    self.exec_stmt(s, arm_env)
                return

        raise RuntimeError(
            f"No matching pattern in match statement for value {self._format_value(scrutinee)}",
            stmt.location,
        )

    def _constructor_matches_type(self, constructor_name: str, type_name: str) -> bool:
        """Check if a constructor belongs to the given type."""
        if constructor_name == type_name:
            return True  # Single-variant type where constructor == type name
        type_def = self.type_defs.get(type_name)
        if type_def is None:
            return False
        return any(v.name == constructor_name for v in type_def.variants)

    def _exec_try(self, stmt: TryStatement, env: Environment) -> None:
        """Execute a try/catch statement."""
        catch_type_annot = stmt.catch_clause.type_annotation
        catch_type_name = getattr(catch_type_annot, "name", "String")

        try:
            block_env = env.child()
            for s in stmt.try_body:
                self.exec_stmt(s, block_env)
        except GenoThrowError as e:
            # Structured error — catch expects a user-defined type
            if catch_type_name == "String":
                # String catch: convert thrown value to string
                catch_env = env.child()
                catch_env.bind(
                    stmt.catch_clause.variable, str(e.thrown_value), mutable=False
                )
                for s in stmt.catch_clause.body:
                    self.exec_stmt(s, catch_env)
            elif isinstance(e.thrown_value, ConstructorValue):
                # Verify the constructor belongs to the declared catch type
                if not self._constructor_matches_type(
                    e.thrown_value.constructor, catch_type_name
                ):
                    raise  # Re-throw: constructor doesn't match catch type
                catch_env = env.child()
                catch_env.bind(
                    stmt.catch_clause.variable, e.thrown_value, mutable=False
                )
                for s in stmt.catch_clause.body:
                    self.exec_stmt(s, catch_env)
            else:
                raise
        except ContractViolationError:
            raise
        except GenoRuntimeError as e:
            if catch_type_name != "String":
                raise  # Non-string catch doesn't handle runtime errors
            catch_env = env.child()
            catch_env.bind(stmt.catch_clause.variable, e.message, mutable=False)
            for s in stmt.catch_clause.body:
                self.exec_stmt(s, catch_env)

    def _exec_return(self, stmt: ReturnStatement, env: Environment) -> None:
        """Execute a return statement."""
        value = self.eval_expr(stmt.value, env)
        value = _promote_int_to_expected_float(
            value, getattr(stmt, "_expected_runtime_type", None)
        )
        raise ReturnException(value)


# =============================================================================
# Binary Operator Functions
# =============================================================================

# One function per operator, built once at import. The previous
# implementation rebuilt an 18-lambda dict on every binary operation.
# Operator-specific allocation/bomb pre-checks live with their operator so
# the common path pays only a dict lookup and a call; the caller
# (_eval_binary_op) applies the shared result guards (complex rejection,
# integer bit-length) to every operator's result.


def _binop_add(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    # Pre-check collection size before allocation to prevent OOM
    if isinstance(a, (str, list)) and isinstance(b, type(a)):
        expected = len(a) + len(b)
        limit = interp.sandbox_config.max_collection_size
        if expected > limit:
            kind = "String" if isinstance(a, str) else "List"
            raise RuntimeError(
                f"{kind} size exceeds limit ({expected} > {limit})",
                expr.location,
            )
    return a + b


def _binop_sub(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    return a - b


def _binop_mul(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    # Pre-check collection size before allocation to prevent OOM
    if isinstance(a, (str, list)) and isinstance(b, int):
        expected = len(a) * max(b, 0)
        limit = interp.sandbox_config.max_collection_size
        if expected > limit:
            kind = "String" if isinstance(a, str) else "List"
            raise RuntimeError(
                f"{kind} size exceeds limit ({expected} > {limit})",
                expr.location,
            )
    elif isinstance(b, (str, list)) and isinstance(a, int):
        expected = len(b) * max(a, 0)
        limit = interp.sandbox_config.max_collection_size
        if expected > limit:
            kind = "String" if isinstance(b, str) else "List"
            raise RuntimeError(
                f"{kind} size exceeds limit ({expected} > {limit})",
                expr.location,
            )
    return a * b


def _binop_div(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    if isinstance(a, int) and isinstance(b, int):
        return _int_trunc_divmod(a, b)[0]
    return a / b


def _binop_mod(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    return _numeric_mod(a, b)


def _binop_eq(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> bool:
    return interp._values_equal(a, b)


def _binop_ne(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> bool:
    return not interp._values_equal(a, b)


def _binop_lt(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    return a < b


def _binop_gt(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    return a > b


def _binop_le(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    return a <= b


def _binop_ge(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    return a >= b


def _binop_pow(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    # Guard against exponentiation bombs
    if isinstance(b, int):
        if b < 0:
            pass  # Negative exponent produces float, not a bomb
        elif isinstance(a, int) and a != 0 and b > 0:
            # Estimate result bits: roughly |a|.bit_length() * b
            est_bits = max(a.bit_length(), 1) * b
            if est_bits > interp.sandbox_config.max_integer_bits:
                raise RuntimeError(
                    f"Exponentiation result too large (estimated {est_bits} bits)",
                    expr.location,
                )
    return a**b


def _binop_bitand(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    return a & b


def _binop_bitxor(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    return a ^ b


def _check_shift_amount(
    interp: "Interpreter", expr: BinaryOp, b: Any, direction: str
) -> None:
    # Guard against shift bombs
    if isinstance(b, int):
        if b < 0:
            raise RuntimeError("Negative shift count", expr.location)
        if b > interp.sandbox_config.max_integer_bits:
            raise RuntimeError(
                f"{direction} shift amount too large ({b} bits)",
                expr.location,
            )


def _binop_lshift(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    _check_shift_amount(interp, expr, b, "Left")
    return a << b


def _binop_rshift(interp: "Interpreter", expr: BinaryOp, a: Any, b: Any) -> Any:
    _check_shift_amount(interp, expr, b, "Right")
    return a >> b


# "and"/"or" are handled by short-circuit branches in _eval_binary_op and
# never reach this table.
_BINOP_FUNCS: dict[str, Any] = {
    "+": _binop_add,
    "-": _binop_sub,
    "*": _binop_mul,
    "/": _binop_div,
    "%": _binop_mod,
    "==": _binop_eq,
    "!=": _binop_ne,
    "<": _binop_lt,
    ">": _binop_gt,
    "<=": _binop_le,
    ">=": _binop_ge,
    "**": _binop_pow,
    "&": _binop_bitand,
    "^": _binop_bitxor,
    "<<": _binop_lshift,
    ">>": _binop_rshift,
}


# =============================================================================
# Dispatch Tables
# =============================================================================

# Exact node type -> unbound handler. Built once at import; eval_expr and
# exec_stmt look up type(node) directly instead of walking isinstance chains
# (AST nodes inherit from an ABC, making isinstance disproportionately slow).
_EXPR_DISPATCH: dict[type, Any] = {
    IntegerLiteral: Interpreter._eval_integer_literal,
    FloatLiteral: Interpreter._eval_float_literal,
    StringLiteral: Interpreter._eval_string_literal,
    FStringExpr: Interpreter._eval_fstring,
    BooleanLiteral: Interpreter._eval_boolean_literal,
    Identifier: Interpreter._eval_identifier,
    TypeIdentifier: Interpreter._eval_type_identifier,
    ListLiteral: Interpreter._eval_list_literal,
    BinaryOp: Interpreter._eval_binary_op,
    UnaryOp: Interpreter._eval_unary_op,
    FunctionCall: Interpreter._eval_function_call,
    IndexAccess: Interpreter._eval_index_access,
    FieldAccess: Interpreter._eval_field_access,
    Pipeline: Interpreter._eval_pipeline,
    LambdaExpr: Interpreter._eval_lambda,
    ConstructorCall: Interpreter._eval_constructor_call,
    TupleExpr: Interpreter._eval_tuple,
    MatchExpr: Interpreter._eval_match_expr,
    TypedHole: Interpreter._eval_typed_hole,
    WithExpr: Interpreter._eval_with_expr,
    PropagateExpr: Interpreter._eval_propagate,
    ListComprehension: Interpreter._eval_list_comprehension,
    ThrowExpression: Interpreter._eval_throw,
    AwaitExpr: Interpreter._eval_await,
}

_STMT_DISPATCH: dict[type, Any] = {
    LetStatement: Interpreter._exec_let,
    VarStatement: Interpreter._exec_var,
    TupleDestructureStatement: Interpreter._exec_tuple_destructure,
    AssignStatement: Interpreter._exec_assign,
    IndexAssignStatement: Interpreter._exec_index_assign,
    FieldAssignStatement: Interpreter._exec_field_assign,
    IfStatement: Interpreter._exec_if,
    WhileStatement: Interpreter._exec_while,
    ForStatement: Interpreter._exec_for,
    MatchStatement: Interpreter._exec_match,
    ReturnStatement: Interpreter._exec_return,
    BreakStatement: Interpreter._exec_break,
    ContinueStatement: Interpreter._exec_continue,
    TryStatement: Interpreter._exec_try,
    ExpressionStatement: Interpreter._exec_expression_stmt,
    AssertStatement: Interpreter._exec_assert,
}


def _dispatch_via_mro(table: dict[type, Any], node: Any) -> Any:
    """Resolve a handler for subclasses of registered node types.

    Exact-type lookup misses for subclasses (the old isinstance chains
    accepted them), so walk the MRO to the nearest registered base.
    """
    for klass in type(node).__mro__[1:]:
        handler = table.get(klass)
        if handler is not None:
            return handler
    return None


# =============================================================================
# Convenience Functions
# =============================================================================


def interpret(
    source: str,
    filename: str = "<stdin>",
    check_examples: bool = True,
    max_recursion_depth: int | None = None,
) -> Any:
    """
    Parse and execute Geno source code.

    Args:
        source: Source code string
        filename: Filename for error messages
        check_examples: Whether to verify example clauses
        max_recursion_depth: Maximum call stack depth (default: 100)

    Returns:
        Result of main function, or None
    """
    from .lexer import Lexer
    from .parser import Parser
    from .typechecker import TypeChecker

    # Lex
    lexer = Lexer(source, filename)
    tokens = lexer.tokenize()

    # Parse
    parser = Parser(tokens)
    program = parser.parse_program()

    # Type check
    checker = TypeChecker()
    checker.check_program(program)

    # Interpret
    interpreter = Interpreter(
        check_examples=check_examples, max_recursion_depth=max_recursion_depth
    )
    return interpreter.run(program)
