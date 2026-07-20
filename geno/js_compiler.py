"""
Geno JavaScript Compiler
============================

Compiles Geno source code to JavaScript.
"""

import base64 as _base64
import html as _html
import json as _json
import re as _re
from io import StringIO
from typing import TYPE_CHECKING, Optional, Tuple, Union, cast

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
from .ast_nodes import (
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
from .entrypoint import entrypoint_returns_int, visible_type_aliases

if TYPE_CHECKING:
    from .target_profile import TargetProfile
from .ast_nodes import (
    FunctionType as ASTFunctionType,
)
from .base_compiler import BaseCompiler
from .builtin_registry import (
    all_builtin_names,
    js_backend_builtin_helper_names,
    js_backend_builtin_name_map,
)
from .js_runtime_prelude import JS_RUNTIME_PRELUDE
from .manifest import validate_module_name
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
    MutableMapType,
    OptionType,
    ResultType,
    SetType,
    StringType,
    TupleType,
    Type,
    TypeVar,
    UnitType,
    UserType,
    VecType,
)

# ---------------------------------------------------------------------------
# Prelude section-based tree-shaking
# ---------------------------------------------------------------------------

# Each section is delimited by "// ===" headers in the JS prelude.
# A section is included if any of its defined names are referenced.

_PRELUDE_SECTIONS: list | None = None  # list of (names, source)
_PRELUDE_ALL_NAMES: set | None = None
_PRELUDE_SECTION_DEPS: list | None = None  # list of set of dependency names
_PRELUDE_NAME_TO_SECTIONS: dict[str, tuple[int, ...]] | None = None
_PRELUDE_SECTION_CLOSURES: list[frozenset[int]] | None = None
_PRELUDE_IDENTIFIER_RE = _re.compile(r"\b[A-Za-z_]\w*\b")
_PRELUDE_NAME_DEF_RE = _re.compile(
    r"^(?:function\s+(\w+)|class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=)"
)
_MAX_SAFE_JS_INT = 9_007_199_254_740_991

# Sections whose names should always be included
_CORE_NAMES = (
    frozenset(
        {
            "_checkCollectionSize",
            "_MAX_COLLECTION_SIZE",
            "_MAX_INTEGER_BITS",
            "_checkIntegerBits",
            "_integerBitLength",
            "isConstructor",
            "Some",
            "None_",
            "Ok",
            "Err",
            "_valuesEqual",
            "_formatValue",
            "_formatFloat",
            "_roundNearest",
            "_reprString",
            "_stringifyValue",
            "_withGenoFormatter",
            "_GENO_FORMATTER",
            "_deepCopy",
            "get_field",
            "_divZero",
            "_safe_div",
            "_float_div",
            "_float_power",
            "_safe_add",
            "_safe_sub",
            "_safe_mul",
            "_safe_mod",
            "_safe_power",
            "_safe_lshift",
            "_safe_rshift",
            "_safe_bitor",
            "_safe_bitand",
            "_safe_bitxor",
            "_safe_neg",
            "_safe_bitnot",
            "_safe_index",
            "_PropagateReturn",
            "_propagate",
            "_GenoThrow",
            "_GenoContractViolation",
        }
    )
    | js_backend_builtin_helper_names()
)


def _browser_capability_bootstrap() -> str:
    """Return JS that grants capabilities declared by the browser target."""
    from .target_profile import TargetProfile

    caps = sorted(TargetProfile.load("browser").capabilities)
    return f"globalThis.__GENO_CAPS = {_json.dumps(caps)};\n"


def _offset_source_map_lines(source_map_json: str, line_delta: int) -> str:
    """Offset generated source-map lines by prepending empty mapping lines."""
    if line_delta <= 0:
        return source_map_json

    source_map = _json.loads(source_map_json)
    mappings = source_map.get("mappings")
    if isinstance(mappings, str) and mappings:
        source_map["mappings"] = ";" * line_delta + mappings
    return _json.dumps(source_map)


def _coerce_canvas_dimension(value: object, name: str) -> int:
    """Coerce a canvas dimension before embedding it in HTML attributes."""
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        raise ValueError(f"{name} must be an integer canvas dimension")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer canvas dimension") from exc


def _init_prelude_sections() -> None:
    """Parse prelude into sections for tree-shaking."""
    global _PRELUDE_SECTIONS, _PRELUDE_ALL_NAMES, _PRELUDE_SECTION_DEPS
    global _PRELUDE_NAME_TO_SECTIONS, _PRELUDE_SECTION_CLOSURES

    # Split on "// ===" section headers
    sections: list[tuple[set, str]] = []
    all_names: set[str] = set()
    current_lines: list[str] = []
    current_names: set[str] = set()

    for line in JS_RUNTIME_PRELUDE.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("// ===") and current_lines:
            # Close previous section
            sections.append((current_names, "".join(current_lines)))
            all_names.update(current_names)
            current_lines = [line]
            current_names = set()
        else:
            current_lines.append(line)
            m = _PRELUDE_NAME_DEF_RE.match(stripped)
            if m:
                name = m.group(1) or m.group(2) or m.group(3)
                if name:
                    current_names.add(name)

    if current_lines:
        sections.append((current_names, "".join(current_lines)))
        all_names.update(current_names)

    # Build per-section dependency sets and section lookup tables.
    section_deps: list[set] = []
    name_to_sections: dict[str, list[int]] = {}
    for index, (names, _source) in enumerate(sections):
        for name in names:
            name_to_sections.setdefault(name, []).append(index)

    for _names, source in sections:
        deps = (set(_PRELUDE_IDENTIFIER_RE.findall(source)) & all_names) - _names
        section_deps.append(deps)

    section_closures: list[frozenset[int]] = []
    for index, deps in enumerate(section_deps):
        closure = {index}
        stack = [
            dep_index
            for dep_name in deps
            for dep_index in name_to_sections.get(dep_name, ())
        ]
        while stack:
            dep_index = stack.pop()
            if dep_index in closure:
                continue
            closure.add(dep_index)
            stack.extend(
                next_index
                for dep_name in section_deps[dep_index]
                for next_index in name_to_sections.get(dep_name, ())
            )
        section_closures.append(frozenset(closure))

    _PRELUDE_SECTIONS = sections
    _PRELUDE_ALL_NAMES = all_names
    _PRELUDE_SECTION_DEPS = section_deps
    _PRELUDE_NAME_TO_SECTIONS = {
        name: tuple(indices) for name, indices in name_to_sections.items()
    }
    _PRELUDE_SECTION_CLOSURES = section_closures


def _tree_shake_prelude(user_code: str) -> str:
    """Return only the prelude sections referenced by user_code."""
    if _PRELUDE_SECTIONS is None:
        _init_prelude_sections()

    assert _PRELUDE_SECTIONS is not None
    assert _PRELUDE_ALL_NAMES is not None
    assert _PRELUDE_NAME_TO_SECTIONS is not None
    assert _PRELUDE_SECTION_CLOSURES is not None

    referenced_names = set(_PRELUDE_IDENTIFIER_RE.findall(user_code))
    needed_sections: set[int] = set()
    for name in _CORE_NAMES | (referenced_names & _PRELUDE_ALL_NAMES):
        for section_index in _PRELUDE_NAME_TO_SECTIONS.get(name, ()):
            needed_sections.update(_PRELUDE_SECTION_CLOSURES[section_index])

    # Include sections that define any needed name (or define no names — preamble)
    parts: list[str] = []
    for index, (names, source) in enumerate(_PRELUDE_SECTIONS):
        if not names or index in needed_sections:
            parts.append(source)
    return "".join(parts)


_JS_RESERVED = frozenset(
    {
        "break",
        "case",
        "catch",
        "continue",
        "debugger",
        "default",
        "delete",
        "do",
        "else",
        "finally",
        "for",
        "function",
        "if",
        "in",
        "instanceof",
        "new",
        "return",
        "switch",
        "this",
        "throw",
        "try",
        "typeof",
        "var",
        "void",
        "while",
        "with",
        "class",
        "const",
        "enum",
        "export",
        "extends",
        "import",
        "super",
        "implements",
        "interface",
        "let",
        "package",
        "private",
        "protected",
        "public",
        "static",
        "yield",
        "await",
        "async",
    }
)

_JS_RECORD_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "_tag",
        "__proto__",
        "constructor",
        "prototype",
    }
)

_JS_EMITTED_LOCAL_HELPER_NAMES = frozenset(
    {
        "_GENO_MAP",
        "_GENO_STRING",
        "_GenoContractViolation",
        "_compareGenoValues",
        "_compareOrderedValues",
        "_jsonFloatToString",
        "_jsonStringLiteral",
        "_mapSet",
        "_safe_bitnot",
        "_safe_neg",
        "_safe_sub",
        "_stringCharAt",
        "_stringLength",
        "_stringSubstring",
    }
)
_JS_HOST_INTRINSIC_NAMES = frozenset(
    {
        "Array",
        "BigInt",
        "Date",
        "Error",
        "fetch",
        "document",
        "JSON",
        "Map",
        "Math",
        "Number",
        "Object",
        "Promise",
        "parseFloat",
        "parseInt",
        "RegExp",
        "Set",
        "String",
        "Symbol",
        "console",
        "globalThis",
        "require",
        "process",
        "undefined",
        "requestAnimationFrame",
    }
)
_JS_FIXED_GLOBAL_NAMES = frozenset(
    {
        "_geno_canvas",
        "_geno_ctx",
        "_geno_entry_main",
        "_geno_frame",
        "_geno_last_ts",
        "_geno_state",
        "_GENO_CREATE_REQUIRE",
        "_main_result",
    }
)


_JS_LOCAL_RESERVED_NAMES = (
    frozenset(
        {
            "isConstructor",
            "Some",
            "None_",
            "Ok",
            "Err",
            "get_field",
            "_divZero",
            "_safe_index",
            "_safe_index_set",
            "_safe_div",
            "_float_div",
            "_float_power",
            "_safe_add",
            "_safe_mul",
            "_safe_mod",
            "_safe_power",
            "_safe_lshift",
            "_safe_rshift",
            "_safe_bitor",
            "_safe_bitand",
            "_safe_bitxor",
            "_checkCollectionSize",
            "_MAX_COLLECTION_SIZE",
            "_MAX_INTEGER_BITS",
            "_checkIntegerBits",
            "_integerBitLength",
            "_deepCopy",
            "_valuesEqual",
            "_formatValue",
            "_formatFloat",
            "_roundNearest",
            "_reprString",
            "_stringifyValue",
            "_withGenoFormatter",
            "_GENO_FORMATTER",
            "GenoArray",
            "array_new",
            "array_from_list",
            "array_get",
            "array_set",
            "array_length",
            "array_to_list",
            "floor_",
            "ceil_",
            "format_",
            "GenoSet",
            "set_new",
            "set_from_list",
            "set_add",
            "set_remove",
            "set_contains",
            "set_size",
            "set_to_list",
            "set_union",
            "set_intersection",
            "_propagate",
            "_PropagateReturn",
            "_GenoThrow",
        }
    )
    | _JS_EMITTED_LOCAL_HELPER_NAMES
    | _JS_HOST_INTRINSIC_NAMES
    | js_backend_builtin_helper_names()
)

_JS_PRELUDE_BINDING_RE = _re.compile(
    r"^(?:(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    r"|class\s+([A-Za-z_$][A-Za-z0-9_$]*)"
    r"|(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*))\b",
    _re.MULTILINE,
)
JS_RESERVED_PRELUDE_NAMES = (
    (
        frozenset(
            next(name for name in match.groups() if name is not None)
            for match in _JS_PRELUDE_BINDING_RE.finditer(JS_RUNTIME_PRELUDE)
        )
        - frozenset(all_builtin_names())
    )
    | _JS_LOCAL_RESERVED_NAMES
    | _JS_FIXED_GLOBAL_NAMES
)
JS_DIRECT_REFERENCE_RESERVED_NAMES = (
    JS_RESERVED_PRELUDE_NAMES - _JS_HOST_INTRINSIC_NAMES
)


class JSCompileError(Exception):
    """Raised when JS compilation detects an unsafe or invalid program."""


def _validate_js_record_field_name(
    field_name: str, *, context: str, variant_name: str | None = None
) -> None:
    """Reject field names that are not inert JS data properties."""
    if field_name not in _JS_RECORD_FORBIDDEN_FIELD_NAMES:
        return

    where = f" in variant '{variant_name}'" if variant_name is not None else context
    if field_name == "_tag":
        reason = "conflicts with the JS runtime discriminator"
    else:
        reason = "is prototype-sensitive in JavaScript record output"
    raise JSCompileError(f"Field name '{field_name}' {where} {reason}")


# =========================================================================
# Source Map V3 helpers
# =========================================================================

_VLQ_BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _vlq_encode(value: int) -> str:
    """Encode a single integer as a VLQ base64 string."""
    vlq = (-value << 1) + 1 if value < 0 else value << 1
    encoded = ""
    while True:
        digit = vlq & 0x1F
        vlq >>= 5
        if vlq > 0:
            digit |= 0x20
        encoded += _VLQ_BASE64[digit]
        if vlq == 0:
            break
    return encoded


class JSCompiler(BaseCompiler):
    """Compiles Geno AST to JavaScript source code."""

    def __init__(self, track_source_map: bool = True):
        super().__init__()
        self._track_source_map = track_source_map

        # Source map tracking
        self._out_line = 0  # current output line (0-based)
        self._source_files: list[str] = []  # ordered source filenames
        self._source_index: dict[str, int] = {}  # filename -> index
        # mappings: list of (out_line, out_col, src_idx, src_line, src_col)
        self._mappings: list[tuple[int, int, int, int, int]] = []
        self._emit_function_assignments = False
        self._active_module_bindings: dict[str, str] = {}

    _BUILTIN_NAME_MAP: dict[str, str] = js_backend_builtin_name_map()

    @staticmethod
    def _mangle_name(name: str) -> str:
        mapped = JSCompiler._BUILTIN_NAME_MAP.get(name)
        if mapped is not None:
            return mapped
        if name in _JS_RESERVED:
            return f"{name}_kw"
        return name

    def _emit_main_result(
        self,
        main_def: FunctionDef | None,
        *,
        type_aliases: dict[str, TypeAlias] | None = None,
        main_returns_int: bool | None = None,
    ) -> None:
        """Emit host-boundary handling for a completed main call."""
        resolved_return_type = (
            main_def.__dict__.get("_resolved_return_type")
            if main_def is not None
            else None
        )
        main_result_type = (
            resolved_return_type
            if isinstance(resolved_return_type, Type)
            else self._annotation_to_type(
                main_def.return_type, type_aliases=type_aliases
            )
            if main_def is not None
            else None
        )
        rendered_main = self._compile_formatted_value(
            "_main_result",
            main_result_type,
            mode="display",
            top_level=True,
        )
        uses_exit_status = (
            isinstance(main_result_type, IntType)
            if main_returns_int is None
            else main_returns_int
        )
        if uses_exit_status:
            self._writeln(
                "if (typeof process === 'object' && process !== null && "
                "process.release && process.release.name === 'node') {"
            )
            self._indent()
            self._writeln("process.exitCode = ((_main_result % 256) + 256) % 256;")
            self._dedent()
            self._writeln("} else {")
            self._indent()
            self._writeln(f"console.log({rendered_main});")
            self._dedent()
            self._writeln("}")
            return

        self._writeln("if (_main_result !== null && _main_result !== undefined) {")
        self._indent()
        self._writeln(f"console.log({rendered_main});")
        self._dedent()
        self._writeln("}")

    def _open_esm_entrypoint_guard(self) -> None:
        """Guard ESM main execution so importing the module is side-effect safe."""
        file_url_to_path = self._fresh_temp()
        create_require = self._fresh_temp()
        realpath_sync = self._fresh_temp()
        entry_require = self._fresh_temp()
        is_eval = self._fresh_temp()
        fallback_main = self._fresh_temp()
        self._writeln(
            f'import {{ fileURLToPath as {file_url_to_path} }} from "node:url";'
        )
        self._writeln(
            f'import {{ createRequire as {create_require} }} from "node:module";'
        )
        self._writeln(f'import {{ realpathSync as {realpath_sync} }} from "node:fs";')
        self._writeln(f"const {entry_require} = {create_require}(import.meta.url);")
        self._writeln(
            f"const {is_eval} = process.execArgv.some(arg => "
            "arg === '--eval' || arg.startsWith('--eval=') || "
            "(arg.startsWith('-e') && !arg.startsWith('--')) || "
            "arg === '--print' || arg.startsWith('--print=') || "
            "(arg.startsWith('-p') && !arg.startsWith('--')));"
        )
        self._writeln(f"let {fallback_main} = false;")
        self._writeln(f"if (!{is_eval} && process.argv[1]) {{")
        self._indent()
        self._writeln("try {")
        self._indent()
        self._writeln(
            f"{fallback_main} = "
            f"{realpath_sync}({file_url_to_path}(import.meta.url)) === "
            f"{realpath_sync}({entry_require}.resolve(process.argv[1]));"
        )
        self._dedent()
        self._writeln("} catch {")
        self._indent()
        self._writeln(f"{fallback_main} = false;")
        self._dedent()
        self._writeln("}")
        self._dedent()
        self._writeln("}")
        self._writeln(
            "if (typeof process === 'object' && process !== null && "
            "(import.meta.main === true || "
            "(typeof import.meta.main !== 'boolean' && "
            f"{fallback_main}))) {{"
        )
        self._indent()

    # =========================================================================
    # Core compilation
    # =========================================================================

    def compile(
        self,
        program: Program,
        tree_shake: bool = True,
        *,
        esm: bool = False,
    ) -> str:
        # Compile user code into a separate buffer first
        self.output = StringIO()
        self.indent_level = 0
        self._reset_definition_state()
        self._active_module_bindings = {}
        self._reserve_user_temp_names(program)
        self._out_line = 0
        self._source_files = []
        self._source_index = {}
        self._mappings = []
        self._emit_function_assignments = False

        # First pass: collect type, function, trait, and impl definitions
        collect_definitions(program, into=self._definition_index)

        # Validate no user names shadow prelude names
        self._validate_runtime_name_collisions(
            program,
            JS_RESERVED_PRELUDE_NAMES,
            _JS_LOCAL_RESERVED_NAMES,
            JSCompileError,
            direct_reference_reserved_names=JS_DIRECT_REFERENCE_RESERVED_NAMES,
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
                # Aliased imports (import Foo as F) emit a const binding.
                # Non-aliased imports are resolved by the project compile path
                # which concatenates modules in dependency order, so no codegen
                # is needed here for the single-program path.
                if defn.alias:
                    self._write(f"const {defn.alias} = {defn.module_name};\n")
            # TestBlock definitions are skipped in compiled output

        # Emit trait dispatch wrapper functions
        self._emit_trait_dispatchers()

        # Detect app mode: init/update/render lifecycle functions
        func_names = {d.name for d in program.definitions if isinstance(d, FunctionDef)}
        is_app_mode = {"init", "update", "render"}.issubset(
            func_names
        ) and "main" not in func_names

        if is_app_mode:
            # App mode: emit requestAnimationFrame loop
            self._writeln()
            self._writeln("// App mode: game loop")
            self._writeln("let _geno_state = init();")
            self._writeln("let _geno_last_ts = 0;")
            self._writeln("function _geno_frame(ts) {")
            self._indent()
            self._writeln(
                "const dt = _geno_last_ts === 0 ? 0.016 : (ts - _geno_last_ts) / 1000;"
            )
            self._writeln("_geno_last_ts = ts;")
            self._writeln("_geno_state = update(_geno_state, dt);")
            self._writeln("render(_geno_state);")
            self._writeln("_geno_clear_pressed_keys();")
            self._writeln("_geno_clear_mouse_clicked();")
            self._writeln("requestAnimationFrame(_geno_frame);")
            self._dedent()
            self._writeln("}")
            self._writeln("requestAnimationFrame(_geno_frame);")
        else:
            # Standard mode: call main
            has_main = "main" in func_names
            main_def = next(
                (
                    d
                    for d in program.definitions
                    if isinstance(d, FunctionDef) and d.name == "main"
                ),
                None,
            )
            main_is_async = any(
                isinstance(d, FunctionDef) and d.name == "main" and d.is_async
                for d in program.definitions
            )
            if has_main:
                self._writeln()
                if esm:
                    self._open_esm_entrypoint_guard()
                if main_is_async:
                    self._writeln("(async () => {")
                    self._indent()
                    self._writeln("const _main_result = await main();")
                else:
                    self._writeln("const _main_result = main();")
                self._emit_main_result(main_def)
                if main_is_async:
                    self._dedent()
                    self._writeln(
                        "})().catch(e => { console.error(e); process.exitCode = 1; });"
                    )
                if esm:
                    self._dedent()
                    self._writeln("}")

        user_code = str(self.output.getvalue())

        # Prepend the prelude (tree-shaken or full)
        if tree_shake:
            prelude = _tree_shake_prelude(user_code)
        else:
            prelude = JS_RUNTIME_PRELUDE

        # Re-build output with prelude + user code, updating line tracking
        self.output = StringIO()
        self._out_line = 0
        self._write(prelude)
        self._write("\n")

        # Offset all recorded source mappings by the prelude line count
        if self._track_source_map:
            prelude_lines = self._out_line
            self._mappings = [
                (out_line + prelude_lines, out_col, src_idx, src_line, src_col)
                for out_line, out_col, src_idx, src_line, src_col in self._mappings
            ]

        self._write(user_code)
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

    def compile_project(
        self,
        dep_graph,
        tree_shake: bool = True,
        *,
        esm: bool = False,
    ) -> str:
        """Compile all modules in a DependencyGraph to a single JS file.

        Modules are emitted in topological order (dependencies first).
        The runtime prelude is written once at the top.
        """
        self.output = StringIO()
        self.indent_level = 0
        self._reset_definition_state()
        self._active_module_bindings = {}
        for program in dep_graph.parsed.values():
            self._reserve_user_temp_names(program)
            self._validate_runtime_name_collisions(
                program,
                JS_RESERVED_PRELUDE_NAMES,
                _JS_LOCAL_RESERVED_NAMES,
                JSCompileError,
                top_level_dispatchers_share_scope=False,
                direct_reference_reserved_names=JS_DIRECT_REFERENCE_RESERVED_NAMES,
            )
        for mod_name in dep_graph.sorted_modules:
            try:
                validate_module_name(mod_name)
            except ValueError as exc:
                raise JSCompileError(str(exc)) from exc
            if mod_name in _JS_RESERVED:
                raise JSCompileError(
                    f"Invalid JavaScript module name '{mod_name}': reserved keyword"
                )
            if (
                mod_name in JS_RESERVED_PRELUDE_NAMES
                and mod_name not in _JS_HOST_INTRINSIC_NAMES
            ):
                raise JSCompileError(f"'{mod_name}' is a reserved runtime module name")
        module_bindings = {
            mod_name: self._fresh_temp() for mod_name in dep_graph.sorted_modules
        }
        self._out_line = 0
        self._source_files = []
        self._source_index = {}
        self._mappings = []

        entrypoint = dep_graph.project.entrypoint or dep_graph.sorted_modules[-1]
        self._emit_function_assignments = False
        module_impl_exports: dict[str, list[str]] = {}
        module_runtime_exports: dict[str, list[str]] = {}
        for mod_name in dep_graph.sorted_modules:
            program = dep_graph.parsed[mod_name]
            self._register_module_param_names(mod_name, program)
            public_exports, impl_exports = self._module_runtime_exports(program)
            module_impl_exports[mod_name] = impl_exports
            module_runtime_exports[mod_name] = public_exports + impl_exports

        # Compile each module in topological order
        for mod_name in dep_graph.sorted_modules:
            program = dep_graph.parsed[mod_name]
            runtime_exports = module_runtime_exports[mod_name]
            own_export_names = set(runtime_exports)
            imported_runtime_names: dict[str, str] = {}
            ambiguous_imported_names: set[str] = set()
            active_module_bindings = {
                (defn.alias or defn.module_name): module_bindings[defn.module_name]
                for defn in program.definitions
                if isinstance(defn, ImportStatement)
                and defn.module_name in module_bindings
            }
            export_collisions = own_export_names & active_module_bindings.keys()
            if export_collisions:
                collision = min(export_collisions)
                raise JSCompileError(
                    f"Imported module '{collision}' conflicts with a local export"
                )
            self._validate_reserved_local_names(
                program,
                active_module_bindings.keys(),
                JSCompileError,
            )
            self._active_module_bindings = active_module_bindings

            for defn in program.definitions:
                if not isinstance(defn, ImportStatement) or defn.alias:
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

            self._write(f"\n\n// === Module: {mod_name} ===\n")
            self._writeln(f"const {module_bindings[mod_name]} = (() => {{")
            self._indent()

            for export_name, imported_module in sorted(imported_runtime_names.items()):
                self._writeln(
                    f"const {self._mangle_name(export_name)} = "
                    f"{module_bindings[imported_module]}[{export_name!r}];"
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
                elif isinstance(defn, ImplDef):
                    self._compile_impl_def(defn)
                # TestBlock is skipped

            attrs = ", ".join(f"{n}: {self._mangle_name(n)}" for n in runtime_exports)
            self._writeln(f"return {{{attrs}}};" if attrs else "return {};")
            self._dedent()
            self._writeln("})();")
            self._active_module_bindings = {}

        for mod_name in dep_graph.sorted_modules:
            for impl_name in module_impl_exports[mod_name]:
                self._writeln(
                    f"const {self._mangle_name(impl_name)} = "
                    f"{module_bindings[mod_name]}[{impl_name!r}];"
                )

        # Emit trait dispatchers
        self._emit_trait_dispatchers()

        # Add main call from entrypoint module
        entry_binding = module_bindings[entrypoint]
        func_names = set()
        main_is_async = False
        ep_program = dep_graph.parsed.get(entrypoint)
        entrypoint_type_aliases: dict[str, TypeAlias] = {}
        entrypoint_main_returns_int: bool | None = None
        if ep_program:
            entrypoint_type_aliases = visible_type_aliases(ep_program, dep_graph.parsed)
            entrypoint_main_returns_int = entrypoint_returns_int(
                ep_program, dep_graph.parsed
            )
            func_names = {
                d.name for d in ep_program.definitions if isinstance(d, FunctionDef)
            }
            main_def = next(
                (
                    d
                    for d in ep_program.definitions
                    if isinstance(d, FunctionDef) and d.name == "main"
                ),
                None,
            )
            main_is_async = any(
                isinstance(d, FunctionDef) and d.name == "main" and d.is_async
                for d in ep_program.definitions
            )

        is_app_mode = {"init", "update", "render"}.issubset(
            func_names
        ) and "main" not in func_names

        if is_app_mode:
            # App mode: emit requestAnimationFrame loop
            self._writeln()
            self._writeln("// App mode: game loop")
            self._writeln(f"let _geno_state = {entry_binding}['init']();")
            self._writeln("let _geno_last_ts = 0;")
            self._writeln("function _geno_frame(ts) {")
            self._indent()
            self._writeln(
                "const dt = _geno_last_ts === 0 ? 0.016 : (ts - _geno_last_ts) / 1000;"
            )
            self._writeln("_geno_last_ts = ts;")
            self._writeln(f"_geno_state = {entry_binding}['update'](_geno_state, dt);")
            self._writeln(f"{entry_binding}['render'](_geno_state);")
            self._writeln("_geno_clear_pressed_keys();")
            self._writeln("_geno_clear_mouse_clicked();")
            self._writeln("requestAnimationFrame(_geno_frame);")
            self._dedent()
            self._writeln("}")
            self._writeln("requestAnimationFrame(_geno_frame);")
        elif "main" in func_names:
            self._writeln()
            if esm:
                self._open_esm_entrypoint_guard()
            if main_is_async:
                self._writeln("(async () => {")
                self._indent()
                self._writeln(f"const _main_result = await {entry_binding}['main']();")
            else:
                self._writeln(f"const _main_result = {entry_binding}['main']();")
            self._emit_main_result(
                main_def,
                type_aliases=entrypoint_type_aliases,
                main_returns_int=entrypoint_main_returns_int,
            )
            if main_is_async:
                self._dedent()
                self._writeln(
                    "})().catch(e => { console.error(e); process.exitCode = 1; });"
                )
            if esm:
                self._dedent()
                self._writeln("}")

        user_code = str(self.output.getvalue())

        if tree_shake:
            prelude = _tree_shake_prelude(user_code)
        else:
            prelude = JS_RUNTIME_PRELUDE

        self.output = StringIO()
        self._out_line = 0
        self._write(prelude)
        self._write("\n")

        if self._track_source_map:
            prelude_lines = self._out_line
            self._mappings = [
                (out_line + prelude_lines, out_col, src_idx, src_line, src_col)
                for out_line, out_col, src_idx, src_line, src_col in self._mappings
            ]

        self._write(user_code)
        self._emit_function_assignments = False
        return str(self.output.getvalue())

    # =========================================================================
    # Output helpers
    # =========================================================================

    def _write(self, text: str) -> None:
        """Override to track line count for source maps."""
        super()._write(text)
        self._out_line += text.count("\n")

    def _emit_source_location(self, location) -> None:
        """Record a source map mapping for the current output line."""
        self._record_mapping(location)

    # -- Syntax token hooks (BaseCompiler) ------------------------------------

    def _if_open(self, cond: str) -> str:
        return f"if ({cond}) {{"

    def _else_open(self) -> str:
        return "} else {"

    def _while_open(self, cond: str) -> str:
        return f"while ({cond}) {{"

    def _for_open(self, var: str, iterable: str) -> str:
        return f"for (const {var} of {iterable}) {{"

    def _return_stmt(self, value: str) -> str:
        return f"return {value};"

    def _statement_terminator(self) -> str:
        return ";"

    def _tuple_destructure_stmt(self, names_csv: str, value: str, mutable: bool) -> str:
        keyword = "let" if mutable else "const"
        return f"{keyword} [{names_csv}] = {value};"

    def _emit_block_close(self) -> None:
        self._writeln("}")

    # ----- Source map helpers ------------------------------------------------

    def _record_mapping(self, location) -> None:
        """Record a source map mapping for the current output line."""
        if not self._track_source_map or location is None or not location.filename:
            return
        fname = location.filename
        if fname not in self._source_index:
            self._source_index[fname] = len(self._source_files)
            self._source_files.append(fname)
        src_idx = self._source_index[fname]
        # out_col 0, source line/col are 0-based in source maps
        self._mappings.append(
            (self._out_line, 0, src_idx, location.line - 1, location.column - 1)
        )

    def generate_source_map(
        self,
        out_file: str = "output.js",
        sources_content: dict[str, str] | None = None,
    ) -> str:
        """Generate a V3 source map JSON string."""
        if not self._track_source_map:
            raise RuntimeError(
                "Source map tracking is disabled; instantiate JSCompiler with "
                "track_source_map=True to generate a source map"
            )
        # Sort mappings by output line
        sorted_maps = sorted(self._mappings, key=lambda m: (m[0], m[1]))

        # Build the mappings string
        prev_out_line = 0
        prev_src_idx = 0
        prev_src_line = 0
        prev_src_col = 0
        prev_out_col = 0
        segments_by_line: dict[int, list[str]] = {}

        for out_line, out_col, src_idx, src_line, src_col in sorted_maps:
            if out_line not in segments_by_line:
                segments_by_line[out_line] = []

            # Reset out_col tracking per line
            if out_line != prev_out_line:
                prev_out_col = 0

            seg = _vlq_encode(out_col - prev_out_col)
            seg += _vlq_encode(src_idx - prev_src_idx)
            seg += _vlq_encode(src_line - prev_src_line)
            seg += _vlq_encode(src_col - prev_src_col)

            segments_by_line[out_line].append(seg)
            prev_out_line = out_line
            prev_out_col = out_col
            prev_src_idx = src_idx
            prev_src_line = src_line
            prev_src_col = src_col

        # Build the full mappings string (semicolons separate lines)
        max_line = max(segments_by_line.keys()) if segments_by_line else 0
        mapping_parts: list[str] = []
        for line_no in range(max_line + 1):
            segs = segments_by_line.get(line_no, [])
            mapping_parts.append(",".join(segs))
        mappings_str = ";".join(mapping_parts)

        # Build sources content array
        contents: list[str | None] = []
        if sources_content:
            for fname in self._source_files:
                contents.append(sources_content.get(fname))
        else:
            contents = [None] * len(self._source_files)

        source_map = {
            "version": 3,
            "file": out_file,
            "sources": self._source_files,
            "sourcesContent": contents,
            "mappings": mappings_str,
        }
        return _json.dumps(source_map)

    # =========================================================================
    # Type Definitions
    # =========================================================================

    def _compile_type_def(self, defn: TypeDef) -> None:
        self._writeln()
        self._record_mapping(defn.location)
        self._writeln(f"// Type: {defn.name}")
        formatter_names = {
            variant.name: self._fresh_temp() for variant in defn.variants
        }
        for variant in defn.variants:
            self._compile_variant(variant, formatter_names[variant.name])
        for variant in defn.variants:
            self._compile_variant_formatter(
                defn, variant, formatter_names[variant.name]
            )
        self._writeln()

    def _compile_variant(self, variant: TypeVariant, formatter_name: str) -> None:
        for field_name, _ in variant.fields:
            if field_name in _JS_RESERVED or field_name == "_withGenoFormatter":
                raise JSCompileError(
                    f"Record field '{field_name}' cannot be represented safely "
                    "by the JavaScript backend"
                )
            _validate_js_record_field_name(
                field_name,
                context="variant field",
                variant_name=variant.name,
            )
        if variant.fields:
            params = ", ".join(name for name, _ in variant.fields)
            self._writeln(f"function {variant.name}({params}) {{")
            self._indent()
            self._writeln(
                f"return _withGenoFormatter("
                f"{{ _tag: '{variant.name}', {params} }}, {formatter_name});"
            )
            self._dedent()
            self._writeln("}")
        else:
            self._writeln(
                f"function {variant.name}() {{ "
                f"return _withGenoFormatter("
                f"{{ _tag: '{variant.name}' }}, {formatter_name}); "
                "}"
            )

    def _compile_variant_formatter(
        self, defn: TypeDef, variant: TypeVariant, formatter_name: str
    ) -> None:
        self._writeln(f"function {formatter_name}(value, seen, topLevel) {{")
        self._indent()
        if not variant.fields:
            self._writeln(f"return {self._js_string_literal(variant.name)};")
            self._dedent()
            self._writeln("}")
            return

        bindings: dict[str, Type] = {name: TypeVar(name) for name in defn.type_params}
        active_type = UserType(
            defn.name,
            tuple(TypeVar(name) for name in defn.type_params),
        )
        field_parts: list[str] = []
        for field_name, field_type in variant.fields:
            resolved_field_type = self._annotation_to_type(field_type, bindings)
            field_value = self._compile_formatted_value(
                f"value.{field_name}",
                resolved_field_type,
                mode="stringify",
                top_level=False,
                active_user_types=frozenset({active_type}),
                seen_ref="seen",
            )
            field_parts.append(
                f"{self._js_string_literal(field_name + ': ')} + {field_value}"
            )

        joined_fields = (
            f" + {self._js_string_literal(', ')} + ".join(field_parts)
            if len(field_parts) > 1
            else field_parts[0]
        )
        self._writeln(
            f"return {self._js_string_literal(variant.name + '(')} + "
            f"{joined_fields} + {self._js_string_literal(')')};"
        )
        self._dedent()
        self._writeln("}")

    # =========================================================================
    # Function Definitions
    # =========================================================================

    # ``_compile_impl_def`` lives on ``BaseCompiler`` — we override only
    # the source-map hook below to record a mapping at the impl level
    # (Python does not, by design).  #622 slice 3.

    def _record_impl_def_location(self, defn: ImplDef) -> None:
        self._record_mapping(defn.location)

    # ``_emit_trait_dispatchers`` lives on ``BaseCompiler`` — both
    # backends share the iterate-and-chain scaffolding.  This subclass
    # only provides the JS-specific emission hooks below (#622).

    def _open_dispatcher(self, method_name: str) -> None:
        self._writeln(
            f"function {self._mangle_name(method_name)}(self_arg, ...args) {{"
        )

    def _emit_dispatcher_arm(
        self,
        *,
        is_first: bool,
        constructor_names: tuple[str, ...],
        mangled: str,
    ) -> None:
        names_js = ", ".join(f"{cn!r}" for cn in constructor_names)
        cond = "if" if is_first else "} else if"
        self._writeln(f"{cond} ([{names_js}].includes(self_arg._tag)) {{")
        self._indent()
        self._writeln(f"return {mangled}(self_arg, ...args);")
        self._dedent()

    def _emit_dispatcher_else(self, first_trait: str) -> None:
        self._writeln("} else {")
        self._indent()
        self._writeln(
            f"throw new Error("
            f"`No implementation of trait '{first_trait}' "
            f"for type ${{self_arg._tag}}`);"
        )
        self._dedent()
        self._writeln("}")

    def _close_dispatcher(self) -> None:
        # Trailing ``}`` closes the JS ``function`` body; the chain's
        # own closing ``}`` was already written by
        # ``_emit_dispatcher_else``.
        self._dedent()
        self._writeln("}")

    def _compile_function_def(self, defn: FunctionDef) -> None:
        self._writeln()
        self._record_mapping(defn.location)
        param_parts = []
        for p in defn.params:
            part = self._mangle_name(p.name)
            if p.default_value is not None:
                part += f" = {self._compile_expr(p.default_value)}"
            param_parts.append(part)
        params = ", ".join(param_parts)
        func_name = self._mangle_name(defn.name)
        async_prefix = "async " if defn.is_async else ""
        if self._emit_function_assignments:
            self._writeln(
                f"var {func_name} = {async_prefix}function {func_name}({params}) {{"
            )
        else:
            self._writeln(f"{async_prefix}function {func_name}({params}) {{")
        self._indent()

        # Requires checks
        for req in defn.specs.requires:
            if isinstance(req.condition, BooleanLiteral):
                if req.condition.value:
                    continue
                raise JSCompileError(
                    f"`requires false` on {defn.name} makes the function uncallable"
                )
            cond = self._compile_expr(req.condition)
            self._writeln(f"if (!({cond})) {{")
            self._indent()
            self._writeln(
                f'throw new _GenoContractViolation("Precondition failed for {defn.name}: '
                f'requires clause evaluated to false");'
            )
            self._dedent()
            self._writeln("}")

        has_ensures = any(
            not isinstance(ens.condition, BooleanLiteral) or not ens.condition.value
            for ens in defn.specs.ensures
        )

        body_helper = self._fresh_temp() if has_ensures else ""
        if has_ensures:
            self._writeln(f"{async_prefix}function {body_helper}() {{")
            self._indent()

        self._writeln("try {")
        self._indent()
        if defn.body:
            for stmt in defn.body:
                self._compile_statement(stmt)
        self._dedent()
        self._writeln("} catch (__geno_pr__) {")
        self._indent()
        self._writeln(
            "if (__geno_pr__ instanceof _PropagateReturn) return __geno_pr__.value;"
        )
        self._writeln("throw __geno_pr__;")
        self._dedent()
        self._writeln("}")

        if has_ensures:
            self._dedent()
            self._writeln("}")
            await_body = "await " if defn.is_async else ""
            self._writeln(f"const result = {await_body}{body_helper}();")
            for ens in defn.specs.ensures:
                if isinstance(ens.condition, BooleanLiteral):
                    if ens.condition.value:
                        continue
                    raise JSCompileError(
                        f"`ensures false` on {defn.name} makes the function unusable"
                    )
                cond = self._compile_expr(ens.condition)
                self._writeln(f"if (!({cond})) {{")
                self._indent()
                self._writeln(
                    f"throw new _GenoContractViolation("
                    f"`Postcondition failed for {defn.name}: "
                    f"ensures clause evaluated to false "
                    f"(result was ${{_formatValue(result)}})`);"
                )
                self._dedent()
                self._writeln("}")
            self._writeln("return result;")

        self._dedent()
        self._writeln("};" if self._emit_function_assignments else "}")

    # =========================================================================
    # Statements
    # =========================================================================

    def _compile_statement(self, stmt: Statement) -> None:
        self._record_mapping(stmt.location)
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
            self._writeln("break;")
        elif isinstance(stmt, ContinueStatement):
            self._writeln("continue;")
        elif isinstance(stmt, TryStatement):
            self._compile_try_statement(stmt)
        elif isinstance(stmt, ExpressionStatement):
            self._writeln(f"{self._compile_expr(stmt.expression)};")
        else:
            raise JSCompileError(f"Unsupported statement type: {type(stmt).__name__}")

    def _compile_try_statement(self, stmt: TryStatement) -> None:
        """Compile a try/catch statement to JavaScript try/catch."""
        catch_type_annot = stmt.catch_clause.type_annotation
        catch_type_name = getattr(catch_type_annot, "name", "String")
        is_string_catch = catch_type_name == "String"

        self._writeln("try {")
        self._indent()
        for s in stmt.try_body:
            self._compile_statement(s)
        self._dedent()
        var_name = self._mangle_name(stmt.catch_clause.variable)
        self._writeln("} catch (__geno_err__) {")
        self._indent()
        if is_string_catch:
            self._writeln("if (__geno_err__ instanceof _GenoThrow) {")
            self._indent()
            self._writeln(f"const {var_name} = String(__geno_err__.value);")
            for s in stmt.catch_clause.body:
                self._compile_statement(s)
            self._dedent()
            self._writeln(
                "} else if ((__geno_err__ instanceof Error) "
                "&& __geno_err__.constructor === Error) {"
            )
            self._indent()
            self._writeln(f"const {var_name} = __geno_err__.message;")
            for s in stmt.catch_clause.body:
                self._compile_statement(s)
            self._dedent()
            self._writeln("} else { throw __geno_err__; }")
        else:
            self._writeln(
                "if (!(__geno_err__ instanceof _GenoThrow)) throw __geno_err__;"
            )
            self._writeln(f"const {var_name} = __geno_err__.value;")
            for s in stmt.catch_clause.body:
                self._compile_statement(s)
        self._dedent()
        self._writeln("}")

    # Types that are immutable in JS and never need deep copying
    _IMMUTABLE_TYPES = frozenset(
        {
            "Int",
            "Float",
            "Bool",
            "String",
            "Unit",
        }
    )

    @classmethod
    def _is_alias_free_expr(cls, expr: Expression) -> bool:
        """Return True when *expr* cannot carry mutable aliases from existing
        bindings into a freshly allocated container value."""
        if isinstance(
            expr,
            (IntegerLiteral, FloatLiteral, StringLiteral, BooleanLiteral),
        ):
            return True

        resolved_type = getattr(expr, "_resolved_type", None)
        type_name = (
            getattr(resolved_type, "name", None)
            if isinstance(resolved_type, SimpleType)
            else None
        )
        if type_name in cls._IMMUTABLE_TYPES or type_name == "Array":
            return True

        if isinstance(expr, ListLiteral):
            return all(cls._is_alias_free_expr(element) for element in expr.elements)
        if isinstance(expr, ListComprehension):
            return cls._is_alias_free_expr(expr.element_expr)
        if isinstance(expr, ConstructorCall):
            return all(cls._is_alias_free_expr(arg) for arg in expr.arguments)
        if isinstance(expr, TupleExpr):
            return all(cls._is_alias_free_expr(element) for element in expr.elements)
        return False

    def _needs_deep_copy(self, stmt_value: Expression, type_annot: object) -> bool:
        type_name = (
            getattr(type_annot, "name", None)
            if isinstance(type_annot, SimpleType)
            else None
        )
        if type_name in self._IMMUTABLE_TYPES or type_name == "Array":
            return False
        return not self._is_alias_free_expr(stmt_value)

    def _js_string_literal(self, value: str) -> str:
        return f'"{self._escape_js_string(value)}"'

    def _annotation_to_type(
        self,
        type_annot: object,
        bindings: dict[str, Type] | None = None,
        type_aliases: dict[str, TypeAlias] | None = None,
    ) -> Type | None:
        bindings = bindings or {}
        aliases = self.type_aliases if type_aliases is None else type_aliases

        if isinstance(type_annot, SimpleType):
            if not type_annot.type_params and type_annot.name in bindings:
                return bindings[type_annot.name]

            params = [
                converted if converted is not None else AnyType()
                for converted in (
                    self._annotation_to_type(param, bindings, aliases)
                    for param in type_annot.type_params
                )
            ]

            if type_annot.name == "Int" and not params:
                return IntType()
            if type_annot.name == "Float" and not params:
                return FloatType()
            if type_annot.name == "Bool" and not params:
                return BoolType()
            if type_annot.name == "String" and not params:
                return StringType()
            if type_annot.name == "Unit" and not params:
                return UnitType()
            if type_annot.name == "List" and params:
                return ListType(params[0])
            if type_annot.name == "Array" and params:
                return ArrayType(params[0])
            if type_annot.name == "Option" and params:
                return OptionType(params[0])
            if type_annot.name == "Result" and len(params) >= 2:
                return ResultType(params[0], params[1])
            if type_annot.name == "Tuple" and params:
                return TupleType(tuple(params))
            if type_annot.name == "Async" and params:
                return AsyncType(params[0])
            if type_annot.name == "Map" and len(params) >= 2:
                return MapType(params[0], params[1])
            if type_annot.name == "MutableMap" and len(params) >= 2:
                return MutableMapType(params[0], params[1])
            if type_annot.name == "Vec" and params:
                return VecType(params[0])
            if type_annot.name == "Set" and params:
                return SetType(params[0])

            alias_def = aliases.get(type_annot.name)
            if alias_def is not None:
                alias_bindings = dict(bindings)
                alias_bindings.update(
                    {
                        name: params[index]
                        for index, name in enumerate(alias_def.type_params)
                        if index < len(params)
                    }
                )
                return self._annotation_to_type(
                    alias_def.target_type, alias_bindings, aliases
                )

            if not type_annot.type_params:
                return UserType(type_annot.name)
            return UserType(type_annot.name, tuple(params))

        if isinstance(type_annot, ASTFunctionType):
            return None

        return None

    def _variant_field_types(
        self, resolved_type: Type
    ) -> dict[str, list[tuple[str, Type | None]]] | None:
        if isinstance(resolved_type, OptionType):
            return {
                "Some": [("value", resolved_type.value_type)],
                "None": [],
            }

        if isinstance(resolved_type, ResultType):
            return {
                "Ok": [("value", resolved_type.ok_type)],
                "Err": [("error", resolved_type.err_type)],
            }

        if isinstance(resolved_type, UserType):
            type_def = self.type_defs.get(resolved_type.name)
            if type_def is None:
                return None

            bindings = {
                name: resolved_type.type_args[index]
                for index, name in enumerate(type_def.type_params)
                if index < len(resolved_type.type_args)
            }

            variants: dict[str, list[tuple[str, Type | None]]] = {}
            for variant in type_def.variants:
                variants[variant.name] = [
                    (field_name, self._annotation_to_type(field_type, bindings))
                    for field_name, field_type in variant.fields
                ]
            return variants

        return None

    def _compile_runtime_formatted_value(
        self,
        value_ref: str,
        *,
        mode: str,
        top_level: bool,
        seen_ref: str | None = None,
    ) -> str:
        if seen_ref is not None:
            top_level_js = "true" if top_level else "false"
            return f"_stringifyValue({value_ref}, {seen_ref}, {top_level_js})"

        if mode == "display":
            if top_level:
                return (
                    f'((typeof {value_ref} === "string") ? '
                    f"{value_ref} : _formatValue({value_ref}))"
                )
            return f"_formatValue({value_ref})"

        if top_level:
            return f"to_string({value_ref})"

        return f"_stringifyValue({value_ref}, undefined, false)"

    def _compile_variant_formatted_value(
        self,
        value_ref: str,
        variants: dict[str, list[tuple[str, Type | None]]],
        *,
        mode: str,
        active_user_types: frozenset[UserType],
        seen_ref: str | None = None,
    ) -> str:
        cases: list[str] = []
        for variant_name, fields in variants.items():
            if not fields:
                rendered = f"_formatValue({value_ref})"
            else:
                field_parts = []
                for field_name, field_type in fields:
                    field_value = self._compile_formatted_value(
                        f"{value_ref}.{field_name}",
                        field_type,
                        mode=mode,
                        top_level=False,
                        active_user_types=active_user_types,
                        seen_ref=seen_ref,
                    )
                    field_parts.append(
                        f"{self._js_string_literal(field_name + ': ')} + {field_value}"
                    )
                joined_fields = (
                    f" + {self._js_string_literal(', ')} + ".join(field_parts)
                    if len(field_parts) > 1
                    else field_parts[0]
                )
                rendered = (
                    f"{self._js_string_literal(variant_name + '(')} + "
                    f"{joined_fields} + "
                    f"{self._js_string_literal(')')}"
                )
            cases.append(
                f"case {self._js_string_literal(variant_name)}: return {rendered};"
            )

        fallback = self._compile_runtime_formatted_value(
            value_ref, mode=mode, top_level=False, seen_ref=seen_ref
        )
        return (
            "(() => { "
            f"switch ({value_ref}._tag) {{ "
            + " ".join(cases)
            + f" default: return {fallback}; "
            + "} })()"
        )

    def _compile_formatted_value(
        self,
        value_ref: str,
        resolved_type: Type | None,
        *,
        mode: str,
        top_level: bool,
        active_user_types: frozenset[UserType] = frozenset(),
        seen_ref: str | None = None,
    ) -> str:
        if resolved_type is None or isinstance(resolved_type, (AnyType, TypeVar)):
            return self._compile_runtime_formatted_value(
                value_ref, mode=mode, top_level=top_level, seen_ref=seen_ref
            )

        if isinstance(resolved_type, AsyncType):
            return self._compile_formatted_value(
                value_ref,
                resolved_type.result_type,
                mode=mode,
                top_level=top_level,
                active_user_types=active_user_types,
                seen_ref=seen_ref,
            )

        if isinstance(resolved_type, UnitType):
            return self._js_string_literal("()")

        if isinstance(resolved_type, BoolType):
            return f'({value_ref} ? "true" : "false")'

        if isinstance(resolved_type, StringType):
            if mode == "display" and top_level:
                return value_ref
            if mode == "stringify" and top_level:
                return value_ref
            if mode == "stringify":
                return f"_reprString({value_ref})"
            return f"_formatValue({value_ref})"

        if isinstance(resolved_type, IntType):
            return f"_GENO_STRING({value_ref})"

        if isinstance(resolved_type, FloatType):
            return f"_formatFloat({value_ref})"

        if isinstance(resolved_type, ListType):
            item_var = self._fresh_temp()
            item_fmt = self._compile_formatted_value(
                item_var,
                resolved_type.element_type,
                mode=mode,
                top_level=False,
                active_user_types=active_user_types,
                seen_ref=seen_ref,
            )
            return (
                f"{self._js_string_literal('[')} + "
                f"{value_ref}.map(({item_var}) => {item_fmt}).join("
                f"{self._js_string_literal(', ')}) + "
                f"{self._js_string_literal(']')}"
            )

        if isinstance(resolved_type, VecType):
            item_var = self._fresh_temp()
            item_fmt = self._compile_formatted_value(
                item_var,
                resolved_type.element_type,
                mode=mode,
                top_level=False,
                active_user_types=active_user_types,
                seen_ref=seen_ref,
            )
            return (
                f"{self._js_string_literal('Vec([')} + "
                f"{value_ref}._elements.map(({item_var}) => {item_fmt}).join("
                f"{self._js_string_literal(', ')}) + "
                f"{self._js_string_literal('])')}"
            )

        if isinstance(resolved_type, ArrayType):
            item_var = self._fresh_temp()
            item_fmt = self._compile_formatted_value(
                item_var,
                resolved_type.element_type,
                mode=mode,
                top_level=False,
                active_user_types=active_user_types,
                seen_ref=seen_ref,
            )
            return (
                f"{self._js_string_literal('Array([')} + "
                f"{value_ref}._elements.map(({item_var}) => {item_fmt}).join("
                f"{self._js_string_literal(', ')}) + "
                f"{self._js_string_literal('])')}"
            )

        if isinstance(resolved_type, SetType):
            item_var = self._fresh_temp()
            item_fmt = self._compile_formatted_value(
                item_var,
                resolved_type.element_type,
                mode=mode,
                top_level=False,
                active_user_types=active_user_types,
                seen_ref=seen_ref,
            )
            return (
                f"{self._js_string_literal('Set({')} + "
                f"[...{value_ref}._data.values()].sort(_compareGenoValues)"
                f".map(({item_var}) => {item_fmt}).join("
                f"{self._js_string_literal(', ')}) + "
                f"{self._js_string_literal('})')}"
            )

        if isinstance(resolved_type, MapType):
            entry_var = self._fresh_temp()
            key_fmt = self._compile_formatted_value(
                f"{entry_var}[0]",
                resolved_type.key_type,
                mode=mode,
                top_level=False,
                active_user_types=active_user_types,
                seen_ref=seen_ref,
            )
            value_fmt = self._compile_formatted_value(
                f"{entry_var}[1]",
                resolved_type.value_type,
                mode=mode,
                top_level=False,
                active_user_types=active_user_types,
                seen_ref=seen_ref,
            )
            return (
                f"{self._js_string_literal('{')} + "
                f"[...{value_ref}.entries()].map(({entry_var}) => "
                f"{key_fmt} + {self._js_string_literal(': ')} + {value_fmt}"
                f").join({self._js_string_literal(', ')}) + "
                f"{self._js_string_literal('}')}"
            )

        if isinstance(resolved_type, MutableMapType):
            entry_var = self._fresh_temp()
            key_fmt = self._compile_formatted_value(
                f"{entry_var}[0]",
                resolved_type.key_type,
                mode=mode,
                top_level=False,
                active_user_types=active_user_types,
                seen_ref=seen_ref,
            )
            value_fmt = self._compile_formatted_value(
                f"{entry_var}[1]",
                resolved_type.value_type,
                mode=mode,
                top_level=False,
                active_user_types=active_user_types,
                seen_ref=seen_ref,
            )
            return (
                f"{self._js_string_literal('MutableMap({')} + "
                f"[...{value_ref}._data.entries()].map(({entry_var}) => "
                f"{key_fmt} + {self._js_string_literal(': ')} + {value_fmt}"
                f").join({self._js_string_literal(', ')}) + "
                f"{self._js_string_literal('})')}"
            )

        if isinstance(resolved_type, TupleType):
            if not resolved_type.element_types:
                return self._js_string_literal("()")
            elements = [
                self._compile_formatted_value(
                    f"{value_ref}[{index}]",
                    element_type,
                    mode=mode,
                    top_level=False,
                    active_user_types=active_user_types,
                    seen_ref=seen_ref,
                )
                for index, element_type in enumerate(resolved_type.element_types)
            ]
            if len(elements) == 1:
                return (
                    f"{self._js_string_literal('(')} + "
                    f"{elements[0]} + "
                    f"{self._js_string_literal(',)')}"
                )
            return (
                f"{self._js_string_literal('(')} + "
                + f" + {self._js_string_literal(', ')} + ".join(elements)
                + f" + {self._js_string_literal(')')}"
            )

        variant_active_user_types = active_user_types
        if isinstance(resolved_type, UserType):
            if resolved_type in active_user_types:
                return self._compile_runtime_formatted_value(
                    value_ref, mode=mode, top_level=top_level, seen_ref=seen_ref
                )
            variant_active_user_types = active_user_types | {resolved_type}

        variants = self._variant_field_types(resolved_type)
        if variants is not None:
            return self._compile_variant_formatted_value(
                value_ref,
                variants,
                mode=mode,
                active_user_types=variant_active_user_types,
                seen_ref=seen_ref,
            )

        return self._compile_runtime_formatted_value(
            value_ref, mode=mode, top_level=top_level, seen_ref=seen_ref
        )

    def _compile_formatted_expr(self, expr: Expression, *, mode: str) -> str:
        value = self._compile_expr(expr)
        temp = self._fresh_temp()
        resolved_type = getattr(expr, "_resolved_type", None)
        if not isinstance(resolved_type, Type):
            resolved_type = None
        formatted = self._compile_formatted_value(
            temp,
            resolved_type,
            mode=mode,
            top_level=True,
        )
        return f"(({temp}) => {formatted})({value})"

    def _compile_json_object_key_value(
        self, value_ref: str, resolved_type: Type | None
    ) -> str | None:
        if resolved_type is None or isinstance(resolved_type, (AnyType, TypeVar)):
            return None

        if isinstance(resolved_type, AsyncType):
            return self._compile_json_object_key_value(
                value_ref, resolved_type.result_type
            )

        if isinstance(resolved_type, UnitType):
            return self._js_string_literal("None")

        if isinstance(resolved_type, BoolType):
            return f"({value_ref} ? 'True' : 'False')"

        if isinstance(resolved_type, StringType):
            return value_ref

        if isinstance(resolved_type, IntType):
            return f"_GENO_STRING({value_ref})"

        if isinstance(resolved_type, FloatType):
            return f"_formatFloat({value_ref})"

        return None

    def _compile_json_display_sequence_string_value(
        self,
        values_ref: str,
        element_type: Type,
        *,
        prefix: str,
        suffix: str,
    ) -> str:
        item_var = self._fresh_temp()
        item_display = self._compile_formatted_value(
            item_var,
            element_type,
            mode="stringify",
            top_level=False,
        )
        display = (
            f"{self._js_string_literal(prefix)} + "
            f"{values_ref}.map(({item_var}) => {item_display}).join("
            f"{self._js_string_literal(', ')}) + "
            f"{self._js_string_literal(suffix)}"
        )
        return f"_jsonStringLiteral({display})"

    def _compile_json_display_mutable_map_string_value(
        self,
        value_ref: str,
        resolved_type: MutableMapType,
    ) -> str:
        key_var = self._fresh_temp()
        value_var = self._fresh_temp()
        key_display = self._compile_formatted_value(
            key_var,
            resolved_type.key_type,
            mode="stringify",
            top_level=False,
        )
        value_display = self._compile_formatted_value(
            value_var,
            resolved_type.value_type,
            mode="stringify",
            top_level=False,
        )
        entry_display = (
            f"{key_display} + {self._js_string_literal(': ')} + {value_display}"
        )
        display = (
            f"{self._js_string_literal('MutableMap({')} + "
            f"[...{value_ref}._data.entries()].map(([{key_var}, {value_var}]) => "
            f"{entry_display}).join({self._js_string_literal(', ')}) + "
            f"{self._js_string_literal('})')}"
        )
        return f"_jsonStringLiteral({display})"

    def _compile_json_variant_value(
        self,
        value_ref: str,
        variants: dict[str, list[tuple[str, Type | None]]],
        active_user_types: frozenset[str],
    ) -> str | None:
        cases: list[str] = []
        for variant_name, fields in variants.items():
            parts = [
                self._js_string_literal(
                    f"{_json.dumps('_tag')}:{_json.dumps(variant_name)}"
                )
            ]
            for field_name, field_type in fields:
                field_json = self._compile_json_string_value(
                    f"{value_ref}.{field_name}",
                    field_type,
                    active_user_types,
                )
                if field_json is None:
                    return None
                parts.append(
                    f"{self._js_string_literal(',' + _json.dumps(field_name) + ':')} "
                    f"+ {field_json}"
                )
            rendered = (
                f"{self._js_string_literal('{')} + "
                + " + ".join(parts)
                + f" + {self._js_string_literal('}')}"
            )
            cases.append(
                f"case {self._js_string_literal(variant_name)}: return {rendered};"
            )

        return (
            "(() => { "
            f"switch ({value_ref}._tag) {{ "
            + " ".join(cases)
            + f" default: return json_to_string({value_ref}); "
            + "} })()"
        )

    def _compile_json_string_value(
        self,
        value_ref: str,
        resolved_type: Type | None,
        active_user_types: frozenset[str] = frozenset(),
    ) -> str | None:
        if resolved_type is None or isinstance(resolved_type, (AnyType, TypeVar)):
            return None

        if isinstance(resolved_type, AsyncType):
            return self._compile_json_string_value(
                value_ref, resolved_type.result_type, active_user_types
            )

        if isinstance(resolved_type, UnitType):
            return self._js_string_literal("null")

        if isinstance(resolved_type, BoolType):
            return f'({value_ref} ? "true" : "false")'

        if isinstance(resolved_type, StringType):
            return f"_jsonStringLiteral({value_ref})"

        if isinstance(resolved_type, IntType):
            return f"_GENO_STRING({value_ref})"

        if isinstance(resolved_type, FloatType):
            return (
                f"_jsonFloatToString({value_ref}, "
                f"{self._js_string_literal('json_to_string: Float must be finite')})"
            )

        if isinstance(resolved_type, ArrayType):
            return self._compile_json_display_sequence_string_value(
                f"{value_ref}._elements",
                resolved_type.element_type,
                prefix="Array([",
                suffix="])",
            )

        if isinstance(resolved_type, VecType):
            return self._compile_json_display_sequence_string_value(
                f"{value_ref}._elements",
                resolved_type.element_type,
                prefix="Vec([",
                suffix="])",
            )

        if isinstance(resolved_type, SetType):
            return self._compile_json_display_sequence_string_value(
                f"[...{value_ref}._data.values()].sort(_compareGenoValues)",
                resolved_type.element_type,
                prefix="Set({",
                suffix="})",
            )

        if isinstance(resolved_type, MutableMapType):
            return self._compile_json_display_mutable_map_string_value(
                value_ref, resolved_type
            )

        if isinstance(resolved_type, ListType):
            item_var = self._fresh_temp()
            item_json = self._compile_json_string_value(
                item_var, resolved_type.element_type, active_user_types
            )
            if item_json is None:
                return None
            return (
                f"{self._js_string_literal('[')} + "
                f"{value_ref}.map(({item_var}) => {item_json}).join("
                f"{self._js_string_literal(',')}) + "
                f"{self._js_string_literal(']')}"
            )

        if isinstance(resolved_type, TupleType):
            if not resolved_type.element_types:
                return self._js_string_literal("null")
            elements = [
                self._compile_json_string_value(
                    f"{value_ref}[{index}]", element_type, active_user_types
                )
                for index, element_type in enumerate(resolved_type.element_types)
            ]
            if any(element is None for element in elements):
                return None
            return (
                f"{self._js_string_literal('[')} + "
                + f" + {self._js_string_literal(',')} + ".join(
                    element for element in elements if element is not None
                )
                + f" + {self._js_string_literal(']')}"
            )

        if isinstance(resolved_type, MapType):
            key_var = self._fresh_temp()
            value_var = self._fresh_temp()
            key_string = self._compile_json_object_key_value(
                key_var, resolved_type.key_type
            )
            value_json = self._compile_json_string_value(
                value_var, resolved_type.value_type, active_user_types
            )
            if key_string is None or value_json is None:
                return None
            return (
                f"{self._js_string_literal('{')} + "
                f"[...{value_ref}.entries()].map(([{key_var}, {value_var}]) => "
                f"_jsonStringLiteral({key_string}) + {self._js_string_literal(':')} + "
                f"{value_json}).join({self._js_string_literal(',')}) + "
                f"{self._js_string_literal('}')}"
            )

        if isinstance(resolved_type, OptionType):
            some_json = self._compile_json_string_value(
                f"{value_ref}.value", resolved_type.value_type, active_user_types
            )
            if some_json is None:
                return None
            return f"({value_ref}._tag === 'Some' ? {some_json} : 'null')"

        if isinstance(resolved_type, ResultType):
            ok_json = self._compile_json_string_value(
                f"{value_ref}.value", resolved_type.ok_type, active_user_types
            )
            err_json = self._compile_json_string_value(
                f"{value_ref}.error", resolved_type.err_type, active_user_types
            )
            if ok_json is None or err_json is None:
                return None
            error_prefix = self._js_string_literal('{"error":')
            object_suffix = self._js_string_literal("}")
            return (
                f"({value_ref}._tag === 'Ok' ? {ok_json} : "
                f"{error_prefix} + {err_json} + {object_suffix})"
            )

        if isinstance(resolved_type, UserType) and resolved_type.name == "JsonValue":
            return f"json_to_string({value_ref})"

        if isinstance(resolved_type, UserType):
            if resolved_type.name in active_user_types:
                return None
            variants = self._variant_field_types(resolved_type)
            if variants is None:
                return None
            return self._compile_json_variant_value(
                value_ref, variants, active_user_types | frozenset({resolved_type.name})
            )

        return None

    def _compile_json_string_expr(self, expr: Expression) -> str | None:
        resolved_type = getattr(expr, "_resolved_type", None)
        if not isinstance(resolved_type, Type):
            return None
        value = self._compile_expr(expr)
        temp = self._fresh_temp()
        result = self._fresh_temp()
        json_string = self._compile_json_string_value(temp, resolved_type)
        if json_string is None:
            return None
        return (
            f"(({temp}) => (_checkCollectionSize({temp}), "
            f"(({result}) => _checkCollectionSize({result}))"
            f"({json_string})))({value})"
        )

    def _compile_let_statement(self, stmt: LetStatement) -> None:
        value = self._compile_expr(stmt.value)
        name = self._mangle_name(stmt.name)
        if self._needs_deep_copy(stmt.value, stmt.type_annotation):
            self._writeln(f"const {name} = _deepCopy({value});")
        else:
            self._writeln(f"const {name} = {value};")

    def _compile_var_statement(self, stmt: VarStatement) -> None:
        value = self._compile_expr(stmt.value)
        name = self._mangle_name(stmt.name)
        if self._needs_deep_copy(stmt.value, stmt.type_annotation):
            self._writeln(f"let {name} = _deepCopy({value});")
        else:
            self._writeln(f"let {name} = {value};")

    # ``_compile_tuple_destructure`` lives on ``BaseCompiler``; JS's
    # emission goes through ``_tuple_destructure_stmt`` below.  #622 slice.

    # ``_compile_{assign,index_assign}_statement`` live on ``BaseCompiler`` —
    # JS differs from Python only in the trailing ``;`` terminator, supplied
    # by ``_statement_terminator`` below.
    # #622 slice 2.

    def _compile_field_assign_statement(self, stmt: FieldAssignStatement) -> None:
        _validate_js_record_field_name(
            stmt.field_name,
            context="in field assignment",
        )
        super()._compile_field_assign_statement(stmt)

    def _compile_match_statement(self, stmt: MatchStatement) -> None:
        scrutinee = self._compile_expr(stmt.scrutinee)
        scrutinee_var = self._fresh_temp()
        self._writeln(f"const {scrutinee_var} = {scrutinee};")

        has_guards = any(arm.guard is not None for arm in stmt.arms)

        if has_guards:
            # Use flag-based approach when guards are present
            matched_var = self._fresh_temp()
            self._writeln(f"let {matched_var} = false;")
            for arm in stmt.arms:
                cond, bindings = self._compile_pattern_condition(
                    arm.pattern, scrutinee_var
                )
                self._writeln(f"if (!{matched_var} && {cond}) {{")
                self._indent()
                for var_name, expr in bindings:
                    self._writeln(f"const {var_name} = {expr};")
                if arm.guard is not None:
                    guard_code = self._compile_expr(arm.guard)
                    self._writeln(f"if ({guard_code}) {{")
                    self._indent()
                    if arm.body:
                        for s in arm.body:
                            self._compile_statement(s)
                    self._writeln(f"{matched_var} = true;")
                    self._dedent()
                    self._writeln("}")
                else:
                    if arm.body:
                        for s in arm.body:
                            self._compile_statement(s)
                    self._writeln(f"{matched_var} = true;")
                self._dedent()
                self._writeln("}")
            self._writeln(f"if (!{matched_var}) {{")
            self._indent()
            self._writeln('throw new Error("No matching pattern");')
            self._dedent()
            self._writeln("}")
        else:
            # Simple if-else chain when no guards
            first = True
            for arm in stmt.arms:
                cond, bindings = self._compile_pattern_condition(
                    arm.pattern, scrutinee_var
                )
                if first:
                    self._writeln(f"if ({cond}) {{")
                    first = False
                else:
                    self._writeln(f"}} else if ({cond}) {{")
                self._indent()
                for var_name, expr in bindings:
                    self._writeln(f"const {var_name} = {expr};")
                if arm.body:
                    for s in arm.body:
                        self._compile_statement(s)
                self._dedent()
            self._writeln("} else {")
            self._indent()
            self._writeln('throw new Error("No matching pattern");')
            self._dedent()
            self._writeln("}")

    # =========================================================================
    # Pattern matching
    # =========================================================================

    def _compile_pattern_condition(
        self, pattern: Pattern, scrutinee: str
    ) -> tuple[str, list[tuple[str, str]]]:
        bindings: list[tuple[str, str]] = []

        if isinstance(pattern, WildcardPattern):
            return ("true", bindings)

        if isinstance(pattern, VariablePattern):
            bindings.append((self._mangle_name(pattern.name), scrutinee))
            return ("true", bindings)

        if isinstance(pattern, LiteralPattern):
            value = pattern.value
            if isinstance(value, str):
                escaped = self._escape_js_string(value)
                value_str = f'"{escaped}"'
            elif isinstance(value, bool):
                value_str = "true" if value else "false"
            elif isinstance(value, int):
                if abs(value) > _MAX_SAFE_JS_INT:
                    raise JSCompileError(
                        "JavaScript backend only supports Int pattern literals "
                        "within the safe integer range"
                    )
                value_str = f"_checkCollectionSize({value})"
            else:
                value_str = str(value)
            return (f"_valuesEqual({scrutinee}, {value_str})", bindings)

        if isinstance(pattern, ConstructorPattern):
            if pattern.constructor == "None":
                cond = f"{scrutinee}._tag === 'None'"
            else:
                cond = f"{scrutinee}._tag === '{pattern.constructor}'"

            for i, subpat in enumerate(pattern.subpatterns):
                field_name = self._get_constructor_field_name(pattern.constructor, i)
                field_access = f"get_field({scrutinee}, {field_name!r})"
                sub_cond, sub_bindings = self._compile_pattern_condition(
                    subpat, field_access
                )
                if sub_cond != "true":
                    cond = f"({cond}) && ({sub_cond})"
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
                cond = (
                    f"Array.isArray({scrutinee}) && "
                    f"{scrutinee}.length >= {min_required}"
                )

                for i in range(fixed_before):
                    elem_access = f"{scrutinee}[{i}]"
                    sub_cond, sub_bindings = self._compile_pattern_condition(
                        pattern.elements[i], elem_access
                    )
                    if sub_cond != "true":
                        cond = f"({cond}) && ({sub_cond})"
                    bindings.extend(sub_bindings)

                rest_pat = pattern.elements[rest_index]
                if isinstance(rest_pat, RestPattern) and rest_pat.name is not None:
                    if fixed_after > 0:
                        bindings.append(
                            (
                                rest_pat.name,
                                f"{scrutinee}.slice({fixed_before}, {scrutinee}.length - {fixed_after})",
                            )
                        )
                    else:
                        bindings.append(
                            (rest_pat.name, f"{scrutinee}.slice({fixed_before})")
                        )

                for i in range(fixed_after):
                    pat_idx = rest_index + 1 + i
                    elem_access = f"{scrutinee}[{scrutinee}.length - {fixed_after - i}]"
                    sub_cond, sub_bindings = self._compile_pattern_condition(
                        pattern.elements[pat_idx], elem_access
                    )
                    if sub_cond != "true":
                        cond = f"({cond}) && ({sub_cond})"
                    bindings.extend(sub_bindings)
            else:
                cond = (
                    f"Array.isArray({scrutinee}) && "
                    f"{scrutinee}.length === {len(pattern.elements)}"
                )
                for i, elem_pat in enumerate(pattern.elements):
                    elem_access = f"{scrutinee}[{i}]"
                    sub_cond, sub_bindings = self._compile_pattern_condition(
                        elem_pat, elem_access
                    )
                    if sub_cond != "true":
                        cond = f"({cond}) && ({sub_cond})"
                    bindings.extend(sub_bindings)
            return (cond, bindings)

        raise JSCompileError(f"Unsupported pattern type: {type(pattern).__name__}")

    # =========================================================================
    # Expressions
    # =========================================================================

    def _compile_expr(self, expr: Expression) -> str:
        if isinstance(expr, IntegerLiteral):
            if abs(expr.value) > _MAX_SAFE_JS_INT:
                raise JSCompileError(
                    "JavaScript backend only supports Int literals within the "
                    "safe integer range"
                )
            return f"_checkCollectionSize({expr.value})"

        if isinstance(expr, FloatLiteral):
            return str(expr.value)

        if isinstance(expr, StringLiteral):
            escaped = self._escape_js_string(expr.value)
            return f'_checkCollectionSize("{escaped}")'

        if isinstance(expr, FStringExpr):
            return self._compile_fstring_expr(expr)

        if isinstance(expr, BooleanLiteral):
            return "true" if expr.value else "false"

        if isinstance(expr, Identifier):
            if expr.name in self._active_module_bindings:
                return self._active_module_bindings[expr.name]
            expected_type = getattr(expr, "_expected_runtime_type", None)
            if expr._resolved_builtin_name == "divide" and isinstance(
                expected_type, FuncType
            ):
                params = expected_type.param_types
                if len(params) == 2 and all(isinstance(p, IntType) for p in params):
                    return "_safe_div"
                if len(params) == 2 and all(
                    isinstance(p, (IntType, FloatType)) for p in params
                ):
                    return "_float_div"
                raise JSCompileError(
                    "First-class divide requires a concrete Int or Float function type"
                )
            if expr._resolved_builtin_name == "divide":
                raise JSCompileError(
                    "First-class divide requires a concrete Int or Float function type"
                )
            return self._mangle_name(expr.name)

        if isinstance(expr, TypeIdentifier):
            if expr.name in self._active_module_bindings:
                return self._active_module_bindings[expr.name]
            if expr.name == "None":
                return "None_"
            variant = self._constructor_to_variant.get(expr.name)
            if variant is not None and not variant.fields:
                return f"{expr.name}()"
            return cast(str, expr.name)

        if isinstance(expr, ListLiteral):
            elements = ", ".join(self._compile_expr(e) for e in expr.elements)
            return f"_checkCollectionSize([{elements}])"

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
                return "null"
            elements = ", ".join(self._compile_expr(e) for e in expr.elements)
            return f'_checkCollectionSize([{elements}], "Tuple")'

        if isinstance(expr, MatchExpr):
            return self._compile_match_expr(expr)

        if isinstance(expr, TypedHole):
            return f"null /* HOLE: ?{expr.name} */"

        if isinstance(expr, PropagateExpr):
            operand = self._compile_expr(expr.operand)
            return f"_propagate({operand})"

        if isinstance(expr, WithExpr):
            return cast(str, self._compile_with_expr(expr))

        if isinstance(expr, ListComprehension):
            var = self._mangle_name(expr.variable)
            iterable = self._compile_expr(expr.iterable)
            iter_temp = self._fresh_temp()
            result_temp = self._fresh_temp()
            item_temp = self._fresh_temp()
            if expr.condition is not None:
                cond = self._compile_expr(expr.condition)
                elem = self._compile_expr(expr.element_expr)
                return (
                    f"(({iter_temp}) => {{ const {result_temp} = []; "
                    f"for (const {var} of {iter_temp}) {{ "
                    f"if ({cond}) {{ const {item_temp} = {elem}; "
                    f"if ({result_temp}.length + 1 > _MAX_COLLECTION_SIZE) {{ "
                    f'throw new Error("List size exceeds limit (" + '
                    f"({result_temp}.length + 1) + "
                    f'" > " + _MAX_COLLECTION_SIZE + ")"); }} '
                    f"{result_temp}.push({item_temp}); }} }} "
                    f"return _checkCollectionSize({result_temp}); }})({iterable})"
                )
            elem = self._compile_expr(expr.element_expr)
            return f"_checkCollectionSize(({iterable}).map(({var}) => {elem}))"

        if isinstance(expr, ThrowExpression):
            value = self._compile_expr(expr.value)
            return f"(() => {{ throw new _GenoThrow({value}); }})()"

        if isinstance(expr, AwaitExpr):
            inner = self._compile_expr(expr.expr)
            return f"(await {inner})"

        raise JSCompileError(f"Unsupported expression type: {type(expr).__name__}")

    def _compile_binary_op(self, expr: BinaryOp) -> str:
        left = self._compile_expr(expr.left)
        right = self._compile_expr(expr.right)
        both_int = is_int_type(expr.left) and is_int_type(expr.right)
        both_numeric = is_numeric_type(expr.left) and is_numeric_type(expr.right)

        if expr.operator == "/":
            if both_int:
                return f"_safe_div({left}, {right})"
            if both_numeric:
                return f"_float_div({left}, {right})"
            return f"_safe_div({left}, {right})"
        if expr.operator == "+":
            if both_numeric and not both_int:
                return f"({left} + {right})"
            return f"_safe_add({left}, {right})"
        if expr.operator == "*":
            if both_numeric and not both_int:
                return f"({left} * {right})"
            return f"_safe_mul({left}, {right})"
        if expr.operator == "%":
            if both_int:
                return f"_safe_mod({left}, {right})"
            if is_numeric_type(expr.left) and is_numeric_type(expr.right):
                if is_simple_fast_path_expr(expr.left) and is_simple_fast_path_expr(
                    expr.right
                ):
                    if (
                        isinstance(expr.right, (FloatLiteral, IntegerLiteral))
                        and expr.right.value != 0
                    ):
                        return f"({left} % {right})"
                    return f"(({right} !== 0) ? ({left} % {right}) : _divZero())"
                lhs = self._fresh_temp()
                rhs = self._fresh_temp()
                return (
                    f"(({lhs}, {rhs}) => "
                    f"(({rhs} !== 0) ? ({lhs} % {rhs}) : _divZero()))"
                    f"({left}, {right})"
                )
            return f"_safe_mod({left}, {right})"
        if expr.operator == "==":
            return f"_valuesEqual({left}, {right})"
        if expr.operator == "!=":
            return f"!_valuesEqual({left}, {right})"

        # Bitwise and exponentiation use safe wrappers
        if expr.operator == "**":
            if both_numeric and not both_int:
                return f"_float_power({left}, {right})"
            return f"_safe_power({left}, {right})"
        if expr.operator == "<<":
            return f"_safe_lshift({left}, {right})"
        if expr.operator == ">>":
            return f"_safe_rshift({left}, {right})"
        if expr.operator == "&":
            return f"_safe_bitand({left}, {right})"
        if expr.operator == "^":
            return f"_safe_bitxor({left}, {right})"
        if expr.operator == "-" and is_int_type(expr.left) and is_int_type(expr.right):
            return f"_safe_sub({left}, {right})"
        if expr.operator in ("<", ">", "<=", ">="):
            cmp = f"_compareOrderedValues({left}, {right})"
            cmp_op = {
                "<": "<",
                ">": ">",
                "<=": "<=",
                ">=": ">=",
            }[expr.operator]
            threshold = "0"
            return f"({cmp} {cmp_op} {threshold})"

        op_map = {
            "-": "-",
            "and": "&&",
            "or": "||",
        }
        js_op = op_map.get(expr.operator, expr.operator)
        return f"({left} {js_op} {right})"

    def _compile_unary_op(self, expr: UnaryOp) -> str:
        operand = self._compile_expr(expr.operand)
        if expr.operator == "-":
            if is_int_type(expr.operand):
                return f"_safe_neg({operand})"
            return f"(-{operand})"
        if expr.operator == "not":
            return f"(!{operand})"
        if expr.operator == "~":
            if is_int_type(expr.operand):
                return f"_safe_bitnot({operand})"
            return f"(~{operand})"
        return operand

    def _compile_function_call(self, expr: FunctionCall) -> str:
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
            fast_path = self._compile_builtin_fast_path(expr, concrete_args)
            if fast_path is not None:
                return fast_path

        func = self._compile_expr(expr.function)
        args = ", ".join(
            "undefined" if arg is None else self._compile_expr(arg.value)
            for arg in ordered_args
        )

        return f"{func}({args})"

    def _compile_builtin_fast_path(
        self, expr: FunctionCall, call_args: list[CallArg]
    ) -> str | None:
        """Inline hot builtin calls when types are statically known."""
        builtin_name = getattr(expr, "_resolved_builtin_name", None)
        if builtin_name is None:
            return None

        if builtin_name == "to_string" and len(call_args) == 1:
            formatted = self._compile_formatted_expr(
                call_args[0].value, mode="stringify"
            )
            return f"_checkCollectionSize({formatted})"

        if builtin_name == "json_to_string" and len(call_args) == 1:
            json_fast_path = self._compile_json_string_expr(call_args[0].value)
            if json_fast_path is not None:
                return json_fast_path

        if builtin_name == "print" and len(call_args) == 1:
            formatted = self._compile_formatted_expr(call_args[0].value, mode="display")
            return f"print_({formatted})"

        if builtin_name == "divide" and len(call_args) == 2:
            left_expr = call_args[0].value
            right_expr = call_args[1].value
            left = self._compile_expr(left_expr)
            right = self._compile_expr(right_expr)
            if is_numeric_type(left_expr) and is_numeric_type(right_expr):
                if is_int_type(left_expr) and is_int_type(right_expr):
                    return f"_safe_div({left}, {right})"
                return f"_float_div({left}, {right})"

        if (
            builtin_name in {"map", "list_map"}
            and len(call_args) == 2
            and isinstance(call_args[1].value, Identifier)
            and call_args[1].value.name == "to_string"
        ):
            list_type = getattr(call_args[0].value, "_resolved_type", None)
            if isinstance(list_type, ListType):
                value = self._compile_expr(call_args[0].value)
                item_var = self._fresh_temp()
                formatted = self._compile_formatted_value(
                    item_var,
                    list_type.element_type,
                    mode="stringify",
                    top_level=True,
                )
                formatted = f"_checkCollectionSize({formatted})"
                return (
                    f"_checkCollectionSize(({value}).map(({item_var}) => {formatted}))"
                )

        if (
            builtin_name == "list_group_by"
            and len(call_args) == 2
            and isinstance(call_args[1].value, Identifier)
            and call_args[1].value.name == "to_string"
        ):
            list_type = getattr(call_args[0].value, "_resolved_type", None)
            if isinstance(list_type, ListType):
                value = self._compile_expr(call_args[0].value)
                groups_var = self._fresh_temp()
                item_var = self._fresh_temp()
                key_var = self._fresh_temp()
                group_var = self._fresh_temp()
                bucket_var = self._fresh_temp()
                formatted_key = self._compile_formatted_value(
                    item_var,
                    list_type.element_type,
                    mode="stringify",
                    top_level=True,
                )
                formatted_key = f"_checkCollectionSize({formatted_key})"
                return (
                    "(() => { "
                    f"const {groups_var} = []; "
                    f"for (const {item_var} of ({value})) {{ "
                    f"const {key_var} = {formatted_key}; "
                    f"let {bucket_var} = null; "
                    f"for (const {group_var} of {groups_var}) {{ "
                    f"if (_valuesEqual({group_var}[0], {key_var})) {{ "
                    f"{bucket_var} = {group_var}; break; "
                    "} "
                    "} "
                    f"if ({bucket_var} === null) {{ "
                    f"{groups_var}.push([{key_var}, [{item_var}]]); "
                    "} else { "
                    f"{bucket_var}[1].push({item_var}); "
                    "} "
                    "} "
                    f"return _checkCollectionSize({groups_var}); "
                    "})()"
                )

        if (
            builtin_name == "option_map"
            and len(call_args) == 2
            and isinstance(call_args[1].value, Identifier)
            and call_args[1].value.name == "to_string"
        ):
            option_type = getattr(call_args[0].value, "_resolved_type", None)
            if isinstance(option_type, OptionType):
                value = self._compile_expr(call_args[0].value)
                option_var = self._fresh_temp()
                formatted = self._compile_formatted_value(
                    f"{option_var}.value",
                    option_type.value_type,
                    mode="stringify",
                    top_level=True,
                )
                formatted = f"_checkCollectionSize({formatted})"
                return (
                    f"(({option_var}) => "
                    f"({option_var}._tag === 'Some' ? "
                    f"Some({formatted}) : None_))({value})"
                )

        if (
            builtin_name in {"result_map", "result_map_err"}
            and len(call_args) == 2
            and isinstance(call_args[1].value, Identifier)
            and call_args[1].value.name == "to_string"
        ):
            result_type = getattr(call_args[0].value, "_resolved_type", None)
            if isinstance(result_type, ResultType):
                value = self._compile_expr(call_args[0].value)
                result_var = self._fresh_temp()
                if builtin_name == "result_map":
                    active_tag = "Ok"
                    active_field = "value"
                    mapped_type = result_type.ok_type
                    constructor = "Ok"
                else:
                    active_tag = "Err"
                    active_field = "error"
                    mapped_type = result_type.err_type
                    constructor = "Err"
                formatted = self._compile_formatted_value(
                    f"{result_var}.{active_field}",
                    mapped_type,
                    mode="stringify",
                    top_level=True,
                )
                formatted = f"_checkCollectionSize({formatted})"
                return (
                    f"(({result_var}) => "
                    f"({result_var}._tag === '{active_tag}' ? "
                    f"{constructor}({formatted}) : {result_var}))({value})"
                )

        if (
            builtin_name == "map_map_values"
            and len(call_args) == 2
            and isinstance(call_args[1].value, Identifier)
            and call_args[1].value.name == "to_string"
        ):
            map_type = getattr(call_args[0].value, "_resolved_type", None)
            if isinstance(map_type, MapType):
                value = self._compile_expr(call_args[0].value)
                result_var = self._fresh_temp()
                key_var = self._fresh_temp()
                value_var = self._fresh_temp()
                formatted = self._compile_formatted_value(
                    value_var,
                    map_type.value_type,
                    mode="stringify",
                    top_level=True,
                )
                formatted = f"_checkCollectionSize({formatted})"
                return (
                    "(() => { "
                    f"const {result_var} = new _GENO_MAP(); "
                    f"for (const [{key_var}, {value_var}] of ({value})) "
                    f"_mapSet({result_var}, {key_var}, {formatted}); "
                    f"return {result_var}; "
                    "})()"
                )

        if builtin_name == LENGTH_FAST_PATH_BUILTIN and len(call_args) == 1:
            value_expr = call_args[0].value
            if has_len_fast_path(value_expr):
                value = self._compile_expr(value_expr)
                if is_string_type(value_expr):
                    return f"_checkIntegerBits(_stringLength({value}))"
                return f"_checkIntegerBits(({value}).length)"

        if builtin_name == STRING_CHAR_AT_FAST_PATH_BUILTIN and len(call_args) == 2:
            text_expr = call_args[0].value
            index_expr = call_args[1].value
            if is_string_type(text_expr) and is_int_type(index_expr):
                text = self._compile_expr(text_expr)
                index = self._compile_expr(index_expr)
                return f"_stringCharAt({text}, {index})"

        if builtin_name in SUBSTRING_FAST_PATH_BUILTINS and len(call_args) == 3:
            text_expr = call_args[0].value
            start_expr = call_args[1].value
            stop_expr = call_args[2].value
            if (
                is_string_type(text_expr)
                and is_int_type(start_expr)
                and is_int_type(stop_expr)
            ):
                text = self._compile_expr(text_expr)
                start = self._compile_expr(start_expr)
                stop = self._compile_expr(stop_expr)
                return f"_stringSubstring({text}, {start}, {stop})"

        if builtin_name in STRING_AFFIX_FAST_PATH_BUILTINS and len(call_args) == 2:
            text_expr = call_args[0].value
            affix_expr = call_args[1].value
            if is_string_type(text_expr) and is_string_type(affix_expr):
                text = self._compile_expr(text_expr)
                affix = self._compile_expr(affix_expr)
                method = "startsWith" if builtin_name == "starts_with" else "endsWith"
                if is_simple_fast_path_expr(text_expr) and is_simple_fast_path_expr(
                    affix_expr
                ):
                    return f"{text}.{method}({affix})"
                text_temp = self._fresh_temp()
                affix_temp = self._fresh_temp()
                return (
                    f"(({text_temp}, {affix_temp}) => "
                    f"{text_temp}.{method}({affix_temp}))"
                    f"({text}, {affix})"
                )

        if builtin_name == APPEND_FAST_PATH_BUILTIN and len(call_args) == 2:
            list_expr = call_args[0].value
            item_expr = call_args[1].value
            if is_list_type(list_expr):
                list_value = self._compile_expr(list_expr)
                item_value = self._compile_expr(item_expr)
                if is_simple_fast_path_expr(list_expr) and is_simple_fast_path_expr(
                    item_expr
                ):
                    return (
                        f"((({list_value}.length + 1) <= _MAX_COLLECTION_SIZE) "
                        f"? [...{list_value}, {item_value}] "
                        f': (() => {{ throw new Error("List size exceeds limit (" + '
                        f'({list_value}.length + 1) + " > " + _MAX_COLLECTION_SIZE + ")"); }})())'
                    )
                list_temp = self._fresh_temp()
                item_temp = self._fresh_temp()
                return (
                    f"(({list_temp}, {item_temp}) => "
                    f"((({list_temp}.length + 1) <= _MAX_COLLECTION_SIZE) "
                    f"? [...{list_temp}, {item_temp}] "
                    f': (() => {{ throw new Error("List size exceeds limit (" + '
                    f'({list_temp}.length + 1) + " > " + _MAX_COLLECTION_SIZE + ")"); }})()))'
                    f"({list_value}, {item_value})"
                )

        return None

    def _reorder_args(
        self, call_args: list[CallArg], param_names: list[str]
    ) -> list[CallArg]:
        result: list[CallArg | None] = [None] * len(param_names)
        used_positions: set[int] = set()
        positional_index = 0

        for arg in call_args:
            if arg.name is not None:
                if arg.name in param_names:
                    pos = param_names.index(arg.name)
                    result[pos] = arg
                    used_positions.add(pos)
                else:
                    raise ValueError(f"Unknown parameter name: {arg.name}")
            else:
                while positional_index in used_positions:
                    positional_index += 1
                if positional_index < len(param_names):
                    result[positional_index] = arg
                    used_positions.add(positional_index)
                    positional_index += 1

        return [arg for arg in result if arg is not None]

    def _compile_pipeline(self, expr: Pipeline) -> str:
        current = self._compile_expr(expr.initial)

        for stage in expr.stages:
            current_temp = self._fresh_temp()
            func = self._compile_expr(stage.function)
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
            current = f"(({current_temp}) => {func}({arg_str}))({current})"

        return current

    def _compile_lambda(self, expr: LambdaExpr) -> str:
        params = ", ".join(self._mangle_name(p.name) for p in expr.params)

        if expr.block_body is not None:
            func_name = self._fresh_temp()
            self._writeln(f"const {func_name} = (({params}) => {{")
            self._indent()
            for stmt in expr.block_body:
                self._compile_statement(stmt)
            self._dedent()
            self._writeln("});")
            return cast(str, func_name)
        else:
            assert expr.body is not None
            body = self._compile_expr(expr.body)
            return f"(({params}) => {body})"

    def _compile_constructor_call(self, expr: ConstructorCall) -> str:
        if expr.constructor == "None":
            return "None_"
        args = ", ".join(self._compile_expr(arg) for arg in expr.arguments)
        return f"{expr.constructor}({args})"

    def _compile_fstring_expr(self, expr: FStringExpr) -> str:
        """Compile an f-string expression to a JavaScript template literal."""
        segments: list[str] = []
        for part in expr.parts:
            if isinstance(part, str):
                escaped = (
                    part.replace("\\", "\\\\")
                    .replace("`", "\\`")
                    .replace("${", "\\${")
                    .replace("\n", "\\n")
                    .replace("\r", "\\r")
                    .replace("\t", "\\t")
                    .replace("<", "\\x3C")
                )
                segments.append(escaped)
            else:
                formatted = self._compile_formatted_expr(part, mode="stringify")
                segments.append("${" + formatted + "}")
        return "_checkCollectionSize(`" + "".join(segments) + "`)"

    # ``_compile_with_expr`` lives on ``BaseCompiler``; JS's emission
    # goes through ``_with_expr_emit`` below.  #622 slice.

    def _with_expr_emit(self, target: str, updates: list[tuple[str, str]]) -> str:
        for field_name, _ in updates:
            _validate_js_record_field_name(
                field_name,
                context="in with expression",
            )
        updates_str = ", ".join(f"{name}: {value}" for name, value in updates)
        if _re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", target):
            return (
                f"_withGenoFormatter(({{...{target}, {updates_str}}}), "
                f"{target}[_GENO_FORMATTER])"
            )
        temp = self._fresh_temp()
        return (
            f"(({temp}) => _withGenoFormatter("
            f"{{...{temp}, {updates_str}}}, {temp}[_GENO_FORMATTER]))({target})"
        )

    def _compile_match_expr(self, expr: MatchExpr) -> str:
        scrutinee = self._compile_expr(expr.scrutinee)
        match_var = self._fresh_temp()

        result_parts = []
        for arm in expr.arms:
            cond, bindings = self._compile_pattern_condition(arm.pattern, match_var)
            if len(arm.body) != 1 or not isinstance(arm.body[0], ReturnStatement):
                raise JSCompileError(
                    "MatchExpr arm body must be exactly one return statement"
                )

            return_stmt = arm.body[0]
            body_expr = self._compile_expr(return_stmt.value)
            for var_name, var_expr in reversed(bindings):
                body_expr = f"(({var_name}) => {body_expr})({var_expr})"

            if arm.guard is not None:
                guard_code = self._compile_expr(arm.guard)
                if bindings:
                    guard_with_bindings = guard_code
                    for var_name, var_expr in reversed(bindings):
                        guard_with_bindings = (
                            f"(({var_name}) => {guard_with_bindings})({var_expr})"
                        )
                    cond = f"{cond} && {guard_with_bindings}"
                else:
                    cond = f"{cond} && {guard_code}"

            result_parts.append((cond, body_expr))

        # Fallback must raise at runtime rather than silently returning null.
        # Mirrors the Python backend via _geno_throw — see issue #657.
        result = (
            '(() => { throw new _GenoThrow("Non-exhaustive match expression"); })()'
        )
        for cond, body in reversed(result_parts):
            result = f"({cond} ? {body} : {result})"

        return f"(({match_var}) => {result})({scrutinee})"

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _escape_js_string(s: str) -> str:
        escaped = (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
            .replace("\0", "\\0")
            .replace("`", "\\`")
            # Keep inline <script> wrappers intact when compiled JS is embedded
            # into single-file HTML output.
            .replace("<", "\\x3C")
        )
        escaped = _re.sub(
            r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]",
            lambda m: f"\\x{ord(m.group()):02x}",
            escaped,
        )
        return escaped


# =============================================================================
# Public API
# =============================================================================


def compile_to_js(
    source: str,
    filename: str = "<stdin>",
    typecheck: bool = True,
    source_map: bool = False,
    source_map_file: str | None = None,
    esm: bool = False,
    target_profile: Optional["TargetProfile"] = None,
) -> Union[str, Tuple[str, str]]:
    """Compile Geno source code to JavaScript.

    When *source_map* is True, returns ``(js_code, source_map_json)``
    instead of just ``js_code``.
    When *esm* is True, the output is an ES module with exported functions.
    """
    from .lexer import Lexer
    from .parser import Parser
    from .target_profile import TargetProfile
    from .typechecker import TypeChecker

    lexer = Lexer(source, filename)
    tokens = lexer.tokenize()

    parser = Parser(tokens)
    program = parser.parse_program()

    profile = target_profile or TargetProfile.load("node-cli")
    if typecheck:
        checker = TypeChecker(target_profile=profile)
        checker.check_program(program)

    compiler = JSCompiler(track_source_map=source_map)
    js_code = compiler.compile(program, esm=esm and profile.target != "browser")

    if esm:
        js_code = _to_esm(js_code, program)

    if source_map:
        out_file = source_map_file or "output.js"
        sm_json = compiler.generate_source_map(
            out_file=out_file,
            sources_content={filename: source},
        )
        if esm:
            sm_json = _offset_source_map_lines(sm_json, _ESM_SOURCE_MAP_LINE_DELTA)
        return js_code, sm_json

    return js_code


def generate_dts(program: Program) -> str:
    """Generate TypeScript declaration file (.d.ts) from a Geno program."""
    from .ast_nodes import FunctionDef, TypeDef

    lines: list[str] = []

    for defn in program.definitions:
        if isinstance(defn, TypeDef):
            # Emit interface for each variant
            for variant in defn.variants:
                if variant.fields:
                    lines.append(f"export interface {variant.name} {{")
                    lines.append(f"  readonly _tag: '{variant.name}';")
                    for fname, ftype in variant.fields:
                        ts_type = _geno_type_to_ts(ftype)
                        lines.append(f"  readonly {fname}: {ts_type};")
                    lines.append("}")
                else:
                    lines.append(f"export interface {variant.name} {{")
                    lines.append(f"  readonly _tag: '{variant.name}';")
                    lines.append("}")
                # Constructor function
                if variant.fields:
                    params = ", ".join(
                        f"{fn}: {_geno_type_to_ts(ft)}" for fn, ft in variant.fields
                    )
                    lines.append(
                        f"export declare function {variant.name}({params}): {variant.name};"
                    )
                else:
                    lines.append(
                        f"export declare function {variant.name}(): {variant.name};"
                    )
                lines.append("")

            # Union type
            variant_names = [v.name for v in defn.variants]
            if len(variant_names) > 1:
                lines.append(f"export type {defn.name} = {' | '.join(variant_names)};")
                lines.append("")

        elif isinstance(defn, FunctionDef):
            params = ", ".join(
                f"{p.name}: {_geno_type_to_ts(p.param_type)}" for p in defn.params
            )
            ret = _geno_type_to_ts(defn.return_type)
            mangled = JSCompiler._mangle_name(defn.name)
            lines.append(f"export declare function {mangled}({params}): {ret};")
            lines.append("")

    return "\n".join(lines) + "\n" if lines else ""


def _geno_type_to_ts(type_annot) -> str:
    """Map a Geno type annotation to a TypeScript type string."""
    from .ast_nodes import FunctionType, SimpleType

    if type_annot is None:
        return "void"
    if isinstance(type_annot, SimpleType):
        name = type_annot.name
        params = type_annot.type_params
        type_map = {
            "Int": "number",
            "Float": "number",
            "Bool": "boolean",
            "String": "string",
            "Unit": "void",
        }
        if name in type_map and not params:
            return type_map[name]
        if name == "List":
            if params:
                return f"Array<{_geno_type_to_ts(params[0])}>"
            return "Array<unknown>"
        if name == "Tuple":
            if params:
                elems = ", ".join(_geno_type_to_ts(t) for t in params)
                return f"[{elems}]"
            return "[unknown, unknown]"
        if name == "Option":
            if params:
                inner = _geno_type_to_ts(params[0])
                return f"{inner} | null"
            return "unknown | null"
        if name == "Result":
            return "{ _tag: 'Ok'; value: unknown } | { _tag: 'Err'; error: unknown }"
        if name == "Map":
            if len(params) >= 2:
                k = _geno_type_to_ts(params[0])
                v = _geno_type_to_ts(params[1])
                return f"Map<{k}, {v}>"
            return "Map<unknown, unknown>"
        # User-defined type or type variable
        return cast(str, name)
    if isinstance(type_annot, FunctionType):
        fn_params = ", ".join(
            f"arg{i}: {_geno_type_to_ts(t)}"
            for i, t in enumerate(type_annot.param_types)
        )
        ret = _geno_type_to_ts(type_annot.return_type)
        return f"({fn_params}) => {ret}"
    return "unknown"


_ESM_NODE_PREAMBLE = (
    'import { createRequire as _GENO_CREATE_REQUIRE } from "node:module";\n'
    "const require = _GENO_CREATE_REQUIRE(import.meta.url);\n"
)
_ESM_SOURCE_MAP_LINE_DELTA = _ESM_NODE_PREAMBLE.count("\n") - 1


def _to_esm(js_code: str, program: Program) -> str:
    """Convert compiled JS to ES module format.

    Removes "use strict" (implicit in ESM), and appends export statements
    for all user-defined functions and type constructors.
    """
    from .ast_nodes import FunctionDef, TypeDef

    # ESM is implicitly strict
    js_code = js_code.replace('"use strict";\n', "", 1)
    js_code = _ESM_NODE_PREAMBLE + js_code

    # Collect exported names
    exports: list[str] = []
    for defn in program.definitions:
        if isinstance(defn, FunctionDef):
            exports.append(JSCompiler._mangle_name(defn.name))
        elif isinstance(defn, TypeDef):
            for variant in defn.variants:
                exports.append(variant.name)

    if exports:
        export_line = "export { " + ", ".join(exports) + " };\n"
        js_code += "\n" + export_line

    return js_code


def _wrap_html(
    js_code: str,
    width: int = 800,
    height: int = 600,
    title: str = "Geno App",
    source_map_json: str | None = None,
) -> str:
    """Wrap compiled JS code in a self-contained HTML shell with Canvas."""
    safe_title = _html.escape(title)
    safe_width = _coerce_canvas_dimension(width, "width")
    safe_height = _coerce_canvas_dimension(height, "height")
    browser_bootstrap = _browser_capability_bootstrap()
    script_prefix = (
        "const _geno_canvas = document.getElementById('geno-canvas');\n"
        "const _geno_ctx = _geno_canvas.getContext('2d');\n"
        f"{browser_bootstrap}"
    )
    sm_comment = ""
    if source_map_json:
        source_map_json = _offset_source_map_lines(
            source_map_json, script_prefix.count("\n")
        )
        b64 = _base64.b64encode(source_map_json.encode()).decode()
        sm_comment = (
            f"\n//# sourceMappingURL=data:application/json;charset=utf-8;base64,{b64}\n"
        )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>
body {{ margin: 0; background: #111; display: flex; justify-content: center; align-items: center; height: 100vh; }}
canvas {{ border: 1px solid #333; }}
</style>
</head>
<body>
<canvas id="geno-canvas" width="{safe_width}" height="{safe_height}"></canvas>
<script>{script_prefix}{js_code}{sm_comment}</script>
</body>
</html>"""


def compile_to_html(
    source: str,
    filename: str = "<stdin>",
    typecheck: bool = True,
    width: int = 800,
    height: int = 600,
    title: str = "Geno App",
    source_map: bool = False,
) -> str:
    """Compile Geno source code to a self-contained HTML file with Canvas."""
    from .lexer import Lexer
    from .parser import Parser
    from .target_profile import TargetProfile
    from .typechecker import TypeChecker

    lexer = Lexer(source, filename)
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    program = parser.parse_program()

    if typecheck:
        checker = TypeChecker(target_profile=TargetProfile.load("browser"))
        checker.check_program(program)

    compiler = JSCompiler(track_source_map=source_map)
    js_code = compiler.compile(program)
    sm_json = None
    if source_map:
        sm_json = compiler.generate_source_map(
            out_file="app.js",
            sources_content={filename: source},
        )
    return _wrap_html(
        js_code,
        width=width,
        height=height,
        title=title,
        source_map_json=sm_json,
    )


def compile_project_to_html(
    dep_graph,
    width: int = 800,
    height: int = 600,
    title: str = "Geno App",
    source_map: bool = False,
) -> str:
    """Compile a multi-module project to a self-contained HTML file."""
    compiler = JSCompiler(track_source_map=source_map)
    js_code = compiler.compile_project(dep_graph)

    # Collect source contents from the dependency graph
    sources_content: dict[str, str] = {}
    if source_map:
        for mod_name in dep_graph.sorted_modules:
            rf = dep_graph.file_map.get(mod_name)
            if rf:
                sources_content[str(rf.path)] = dep_graph.original_sources[mod_name]

    sm_json = None
    if source_map:
        sm_json = compiler.generate_source_map(
            out_file="app.js",
            sources_content=sources_content,
        )
    return _wrap_html(
        js_code,
        width=width,
        height=height,
        title=title,
        source_map_json=sm_json,
    )


__all__ = [
    "JS_RESERVED_PRELUDE_NAMES",
    "JSCompileError",
    "JSCompiler",
    "compile_project_to_html",
    "compile_to_html",
    "compile_to_js",
]
