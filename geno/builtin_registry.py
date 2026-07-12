"""
Shared builtin metadata for capabilities, completion, and named arguments.

``BUILTIN_PARAM_NAMES``, ``CAPABILITY_MAP``, and ``ALWAYS_AVAILABLE_BUILTINS``
are **derived** from the canonical manifest in ``builtin_manifest``.  Do not
edit them directly — update ``builtin_manifest.BUILTIN_MANIFEST`` instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection

from .builtin_manifest import (
    manifest_always_available,
    manifest_capability_map,
    manifest_param_names,
)
from .capabilities import (
    DEFAULT_ALLOWED_CAPABILITIES as DEFAULT_ALLOWED_CAPABILITIES,
)
from .types import (
    ArrayType,
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
    TypeVar,
    UnitType,
    UserType,
    VecType,
)

SOURCE_BUILTIN_PARAM_OVERRIDES: dict[str, list[str]] = {
    # The interpreter exposes ``print`` directly, while the compilers target
    # the prelude helper ``print_``.
    "print": ["value"],
    # ``range`` accepts an optional third positional argument, so the
    # source language cannot use a full 3-name list for named-arg reordering.
    "range": ["start", "end"],
}

SOURCE_BUILTIN_NAME_OVERRIDES: dict[str, str] = {
    # The source language exposes ``print`` while the runtime helper name is
    # ``print_`` in the compiler preludes.
    "print_": "print",
}

# ---------------------------------------------------------------------------
# Derived from the manifest — do not edit directly
# ---------------------------------------------------------------------------
BUILTIN_PARAM_NAMES: dict[str, list[str]] = manifest_param_names()

CAPABILITY_MAP: dict[str, list[str]] = manifest_capability_map(
    SOURCE_BUILTIN_NAME_OVERRIDES
)

ALWAYS_AVAILABLE_BUILTINS: frozenset[str] = manifest_always_available(
    SOURCE_BUILTIN_NAME_OVERRIDES
)

# DEFAULT_ALLOWED_CAPABILITIES now lives in geno.capabilities (kept light
# for CLI startup) and is re-exported above for existing importers.

# Maps runtime capability strings to semantic effect names used in the type system.
CAPABILITY_TO_EFFECT: dict[str, str] = {
    "print": "io",
    "fs": "fs",
    "http": "http",
    "serve": "http",
    "process": "process",
    "env": "env",
    "clock": "clock",
    "random": "random",
    "regex": "regex",
    "stdin": "stdin",
}

# All valid effect names that can appear in `with` annotations.
VALID_EFFECTS: frozenset[str] = frozenset(
    {
        "io",
        "fs",
        "http",
        "process",
        "env",
        "clock",
        "random",
        "regex",
        "stdin",
        "mutation",
        "throw",
    }
)

PYTHON_BACKEND_BUILTIN_NAME_OVERRIDES: dict[str, str] = {
    "filter": "filter_",
    "map": "map_",
    "all": "all_",
    "abs": "abs_",
    "print": "print_",
    "max": "max_",
    "slice": "slice_",
    "floor": "floor_",
    "ceil": "ceil_",
    "round": "round_",
    "format": "format_",
    "range": "range_",
    "exec": "exec_",
}

JS_BACKEND_BUILTIN_NAME_OVERRIDES: dict[str, str] = {
    "filter": "filter_",
    "map": "map_",
    "all": "all_",
    "abs": "abs_",
    "print": "print_",
    "max": "max_",
    "slice": "slice_",
    "round": "round_",
    "range": "range_",
}

_BUILTIN_SIGNATURES: dict[str, FuncType] = {
    "length": FuncType((ListType(TypeVar("T")),), IntType()),
    "head": FuncType((ListType(TypeVar("T")),), TypeVar("T")),
    "tail": FuncType((ListType(TypeVar("T")),), ListType(TypeVar("T"))),
    "append": FuncType((ListType(TypeVar("T")), TypeVar("T")), ListType(TypeVar("T"))),
    "concat": FuncType(
        (ListType(TypeVar("T")), ListType(TypeVar("T"))), ListType(TypeVar("T"))
    ),
    "set_at": FuncType(
        (ListType(TypeVar("T")), IntType(), TypeVar("T")), ListType(TypeVar("T"))
    ),
    "slice": FuncType(
        (ListType(TypeVar("T")), IntType(), IntType()), ListType(TypeVar("T"))
    ),
    "filter": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), BoolType())),
        ListType(TypeVar("T")),
    ),
    "map": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), TypeVar("U"))),
        ListType(TypeVar("U")),
    ),
    "fold": FuncType(
        (
            ListType(TypeVar("T")),
            TypeVar("U"),
            FuncType((TypeVar("U"), TypeVar("T")), TypeVar("U")),
        ),
        TypeVar("U"),
    ),
    "contains": FuncType((ListType(TypeVar("T")), TypeVar("T")), BoolType()),
    "split": FuncType((StringType(), StringType()), ListType(StringType())),
    "join": FuncType((ListType(StringType()), StringType()), StringType()),
    "trim": FuncType((StringType(),), StringType()),
    "to_lower": FuncType((StringType(),), StringType()),
    "to_upper": FuncType((StringType(),), StringType()),
    "replace": FuncType((StringType(), StringType(), StringType()), StringType()),
    "ends_with": FuncType((StringType(), StringType()), BoolType()),
    "split_once": FuncType(
        (StringType(), StringType()),
        OptionType(TupleType((StringType(), StringType()))),
    ),
    "starts_with": FuncType((StringType(), StringType()), BoolType()),
    "to_chars": FuncType((StringType(),), ListType(StringType())),
    "sort_strings": FuncType((ListType(StringType()),), ListType(StringType())),
    "substring": FuncType((StringType(), IntType(), IntType()), StringType()),
    "format": FuncType((StringType(), ListType(StringType())), StringType()),
    "char_code": FuncType((StringType(),), IntType()),
    "from_char_code": FuncType((IntType(),), StringType()),
    "add": FuncType((TypeVar("Num"), TypeVar("Num")), TypeVar("Num")),
    "subtract": FuncType((TypeVar("Num"), TypeVar("Num")), TypeVar("Num")),
    "multiply": FuncType((TypeVar("Num"), TypeVar("Num")), TypeVar("Num")),
    "divide": FuncType((TypeVar("Num"), TypeVar("Num")), TypeVar("Num")),
    "sqrt": FuncType((FloatType(),), FloatType()),
    "floor": FuncType((FloatType(),), IntType()),
    "ceil": FuncType((FloatType(),), IntType()),
    "round": FuncType((FloatType(),), IntType()),
    "max": FuncType((TypeVar("Num"), TypeVar("Num")), TypeVar("Num")),
    "abs": FuncType((TypeVar("Num"),), TypeVar("Num")),
    "is_sorted": FuncType((ListType(IntType()),), BoolType()),
    "is_positive": FuncType((IntType(),), BoolType()),
    "is_numeric_string": FuncType((StringType(),), BoolType()),
    "parse_int": FuncType((StringType(),), OptionType(IntType())),
    "parse_float": FuncType((StringType(),), OptionType(FloatType())),
    "to_string": FuncType((TypeVar("T"),), StringType()),
    "float_to_int": FuncType((FloatType(),), IntType()),
    "int_to_float": FuncType((IntType(),), FloatType()),
    "square": FuncType((TypeVar("Num"),), TypeVar("Num")),
    "is_permutation": FuncType(
        (ListType(TypeVar("T")), ListType(TypeVar("T"))), BoolType()
    ),
    "reverse": FuncType((ListType(TypeVar("T")),), ListType(TypeVar("T"))),
    "bit_or": FuncType((IntType(), IntType()), IntType()),
    "range": FuncType((IntType(), IntType()), ListType(IntType())),
    "is_some": FuncType((OptionType(TypeVar("T")),), BoolType()),
    "is_none": FuncType((OptionType(TypeVar("T")),), BoolType()),
    "unwrap": FuncType((OptionType(TypeVar("T")),), TypeVar("T")),
    "unwrap_or": FuncType((OptionType(TypeVar("T")), TypeVar("T")), TypeVar("T")),
    "map_insert": FuncType(
        (MapType(TypeVar("K"), TypeVar("V")), TypeVar("K"), TypeVar("V")),
        MapType(TypeVar("K"), TypeVar("V")),
    ),
    "map_get": FuncType(
        (MapType(TypeVar("K"), TypeVar("V")), TypeVar("K")),
        OptionType(TypeVar("V")),
    ),
    "take_while": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), BoolType())),
        ListType(TypeVar("T")),
    ),
    "all": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), BoolType())), BoolType()
    ),
    "sort": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"), TypeVar("T")), IntType())),
        ListType(TypeVar("T")),
    ),
    "sort_by": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), TypeVar("U"))),
        ListType(TypeVar("T")),
    ),
    "zip": FuncType(
        (ListType(TypeVar("A")), ListType(TypeVar("B"))),
        ListType(TupleType((TypeVar("A"), TypeVar("B")))),
    ),
    "enumerate": FuncType(
        (ListType(TypeVar("T")),), ListType(TupleType((IntType(), TypeVar("T"))))
    ),
    "flat_map": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), ListType(TypeVar("U")))),
        ListType(TypeVar("U")),
    ),
    "contains_substring": FuncType((StringType(), StringType()), BoolType()),
    "repeat_string": FuncType((StringType(), IntType()), StringType()),
    "string_trim": FuncType((StringType(),), StringType()),
    "string_trim_start": FuncType((StringType(),), StringType()),
    "string_trim_end": FuncType((StringType(),), StringType()),
    "string_pad_left": FuncType((StringType(), IntType(), StringType()), StringType()),
    "string_pad_right": FuncType((StringType(), IntType(), StringType()), StringType()),
    "string_char_at": FuncType((StringType(), IntType()), StringType()),
    "string_index_of": FuncType((StringType(), StringType()), IntType()),
    "string_last_index_of": FuncType((StringType(), StringType()), IntType()),
    "string_repeat": FuncType((StringType(), IntType()), StringType()),
    "string_substring": FuncType((StringType(), IntType(), IntType()), StringType()),
    "string_split": FuncType((StringType(), StringType()), ListType(StringType())),
    "string_join": FuncType((ListType(StringType()), StringType()), StringType()),
    "string_replace": FuncType(
        (StringType(), StringType(), StringType()), StringType()
    ),
    "string_to_upper": FuncType((StringType(),), StringType()),
    "string_to_lower": FuncType((StringType(),), StringType()),
    "string_starts_with": FuncType((StringType(), StringType()), BoolType()),
    "string_ends_with": FuncType((StringType(), StringType()), BoolType()),
    "string_contains": FuncType((StringType(), StringType()), BoolType()),
    "string_split_once": FuncType(
        (StringType(), StringType()),
        OptionType(TupleType((StringType(), StringType()))),
    ),
    "list_length": FuncType((ListType(TypeVar("T")),), IntType()),
    "list_map": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), TypeVar("U"))),
        ListType(TypeVar("U")),
    ),
    "list_filter": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), BoolType())),
        ListType(TypeVar("T")),
    ),
    "math_abs": FuncType((TypeVar("Num"),), TypeVar("Num")),
    "math_min": FuncType((TypeVar("Num"), TypeVar("Num")), TypeVar("Num")),
    "math_max": FuncType((TypeVar("Num"), TypeVar("Num")), TypeVar("Num")),
    "math_clamp": FuncType(
        (TypeVar("Num"), TypeVar("Num"), TypeVar("Num")), TypeVar("Num")
    ),
    "math_floor": FuncType((FloatType(),), IntType()),
    "math_ceil": FuncType((FloatType(),), IntType()),
    "math_round": FuncType((FloatType(),), IntType()),
    "math_sqrt": FuncType((FloatType(),), FloatType()),
    "math_log": FuncType((FloatType(),), FloatType()),
    "math_sin": FuncType((FloatType(),), FloatType()),
    "math_cos": FuncType((FloatType(),), FloatType()),
    "math_pi": FuncType((), FloatType()),
    "math_e": FuncType((), FloatType()),
    "math_random_int": FuncType((IntType(), IntType()), IntType()),
    "math_random_float": FuncType((), FloatType()),
    "map_from_list": FuncType(
        (ListType(TupleType((TypeVar("K"), TypeVar("V")))),),
        MapType(TypeVar("K"), TypeVar("V")),
    ),
    "map_merge": FuncType(
        (MapType(TypeVar("K"), TypeVar("V")), MapType(TypeVar("K"), TypeVar("V"))),
        MapType(TypeVar("K"), TypeVar("V")),
    ),
    "map_filter_map": FuncType(
        (
            MapType(TypeVar("K"), TypeVar("V")),
            FuncType((TypeVar("K"), TypeVar("V")), BoolType()),
        ),
        MapType(TypeVar("K"), TypeVar("V")),
    ),
    "map_map_values": FuncType(
        (
            MapType(TypeVar("K"), TypeVar("V")),
            FuncType((TypeVar("V"),), TypeVar("U")),
        ),
        MapType(TypeVar("K"), TypeVar("U")),
    ),
    "map_entries": FuncType(
        (MapType(TypeVar("K"), TypeVar("V")),),
        ListType(TupleType((TypeVar("K"), TypeVar("V")))),
    ),
    "map_from_entries": FuncType(
        (ListType(TupleType((TypeVar("K"), TypeVar("V")))),),
        MapType(TypeVar("K"), TypeVar("V")),
    ),
    "result_map": FuncType(
        (
            ResultType(TypeVar("T"), TypeVar("E")),
            FuncType((TypeVar("T"),), TypeVar("U")),
        ),
        ResultType(TypeVar("U"), TypeVar("E")),
    ),
    "result_map_err": FuncType(
        (
            ResultType(TypeVar("T"), TypeVar("E")),
            FuncType((TypeVar("E"),), TypeVar("F")),
        ),
        ResultType(TypeVar("T"), TypeVar("F")),
    ),
    "result_and_then": FuncType(
        (
            ResultType(TypeVar("T"), TypeVar("E")),
            FuncType((TypeVar("T"),), ResultType(TypeVar("U"), TypeVar("E"))),
        ),
        ResultType(TypeVar("U"), TypeVar("E")),
    ),
    "result_unwrap_or": FuncType(
        (ResultType(TypeVar("T"), TypeVar("E")), TypeVar("T")), TypeVar("T")
    ),
    "result_is_ok": FuncType((ResultType(TypeVar("T"), TypeVar("E")),), BoolType()),
    "result_is_err": FuncType((ResultType(TypeVar("T"), TypeVar("E")),), BoolType()),
    "result_to_option": FuncType(
        (ResultType(TypeVar("T"), TypeVar("E")),), OptionType(TypeVar("T"))
    ),
    "option_map": FuncType(
        (OptionType(TypeVar("T")), FuncType((TypeVar("T"),), TypeVar("U"))),
        OptionType(TypeVar("U")),
    ),
    "option_and_then": FuncType(
        (
            OptionType(TypeVar("T")),
            FuncType((TypeVar("T"),), OptionType(TypeVar("U"))),
        ),
        OptionType(TypeVar("U")),
    ),
    "option_unwrap_or": FuncType(
        (OptionType(TypeVar("T")), TypeVar("T")), TypeVar("T")
    ),
    "option_is_some": FuncType((OptionType(TypeVar("T")),), BoolType()),
    "option_is_none": FuncType((OptionType(TypeVar("T")),), BoolType()),
    "option_flatten": FuncType(
        (OptionType(OptionType(TypeVar("T"))),), OptionType(TypeVar("T"))
    ),
    "option_to_result": FuncType(
        (OptionType(TypeVar("T")), TypeVar("E")),
        ResultType(TypeVar("T"), TypeVar("E")),
    ),
    "path_join": FuncType((StringType(), StringType()), StringType()),
    "path_parent": FuncType((StringType(),), StringType()),
    "path_filename": FuncType((StringType(),), StringType()),
    "path_extension": FuncType((StringType(),), StringType()),
    "path_is_absolute": FuncType((StringType(),), BoolType()),
    "datetime_now": FuncType((), IntType()),
    "datetime_format": FuncType((IntType(), StringType()), StringType()),
    "datetime_parse": FuncType((StringType(), StringType()), OptionType(IntType())),
    "datetime_elapsed": FuncType((IntType(), IntType()), IntType()),
    "list_zip": FuncType(
        (ListType(TypeVar("T")), ListType(TypeVar("U"))),
        ListType(TupleType((TypeVar("T"), TypeVar("U")))),
    ),
    "list_enumerate": FuncType(
        (ListType(TypeVar("T")),), ListType(TupleType((IntType(), TypeVar("T"))))
    ),
    "list_all": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), BoolType())), BoolType()
    ),
    "list_flatten": FuncType(
        (ListType(ListType(TypeVar("T"))),), ListType(TypeVar("T"))
    ),
    "list_chunk": FuncType(
        (ListType(TypeVar("T")), IntType()), ListType(ListType(TypeVar("T")))
    ),
    "list_take": FuncType((ListType(TypeVar("T")), IntType()), ListType(TypeVar("T"))),
    "list_drop": FuncType((ListType(TypeVar("T")), IntType()), ListType(TypeVar("T"))),
    "list_find": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), BoolType())),
        OptionType(TypeVar("T")),
    ),
    "list_find_index": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), BoolType())),
        OptionType(IntType()),
    ),
    "list_any": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), BoolType())), BoolType()
    ),
    "list_fold_right": FuncType(
        (
            ListType(TypeVar("T")),
            TypeVar("U"),
            FuncType((TypeVar("T"), TypeVar("U")), TypeVar("U")),
        ),
        TypeVar("U"),
    ),
    "list_intersperse": FuncType(
        (ListType(TypeVar("T")), TypeVar("T")), ListType(TypeVar("T"))
    ),
    "list_group_by": FuncType(
        (ListType(TypeVar("T")), FuncType((TypeVar("T"),), TypeVar("K"))),
        ListType(TupleType((TypeVar("K"), ListType(TypeVar("T"))))),
    ),
    "clamp": FuncType((TypeVar("Num"), TypeVar("Num"), TypeVar("Num")), TypeVar("Num")),
    "print": FuncType((TypeVar("T"),), UnitType()),
    "clock_now": FuncType((), IntType()),
    "clock_format": FuncType((FloatType(), StringType()), StringType()),
    "clock_parse": FuncType((StringType(), StringType()), OptionType(FloatType())),
    "clock_elapsed": FuncType((FloatType(), FloatType()), FloatType()),
    "sleep_ms": FuncType((IntType(),), UnitType()),
    "random_int": FuncType((IntType(), IntType()), IntType()),
    "random_float": FuncType((), FloatType()),
    "fs_read_text": FuncType((StringType(),), StringType()),
    "fs_write_text": FuncType((StringType(), StringType()), UnitType()),
    "fs_list_dir": FuncType(
        (StringType(),), ResultType(ListType(StringType()), StringType())
    ),
    "fs_exists": FuncType((StringType(),), BoolType()),
    "http_fetch": FuncType((StringType(),), StringType()),
    "http_post": FuncType((StringType(), StringType()), StringType()),
    "http_request": FuncType(
        (
            StringType(),
            StringType(),
            ListType(TupleType((StringType(), StringType()))),
            StringType(),
        ),
        ResultType(UserType("HttpResponse"), StringType()),
    ),
    "http_listen": FuncType((IntType(),), UnitType()),
    "http_route": FuncType(
        (
            StringType(),
            StringType(),
            FuncType(
                (UserType("HttpRequest"),),
                UserType("HttpResponse"),
                frozenset({"http"}),
            ),
        ),
        UnitType(),
    ),
    "http_respond": FuncType(
        (
            IntType(),
            ListType(TupleType((StringType(), StringType()))),
            StringType(),
        ),
        UserType("HttpResponse"),
    ),
    "json_parse": FuncType(
        (StringType(),), ResultType(UserType("JsonValue"), StringType())
    ),
    "json_stringify": FuncType((UserType("JsonValue"),), StringType()),
    "json_stringify_pretty": FuncType((UserType("JsonValue"), IntType()), StringType()),
    "json_to_string": FuncType((TypeVar("T"),), StringType()),
    "csv_parse": FuncType((StringType(),), ListType(ListType(StringType()))),
    "csv_parse_with_headers": FuncType(
        (StringType(),), ListType(MapType(StringType(), StringType()))
    ),
    "toml_parse": FuncType(
        (StringType(),), ResultType(UserType("JsonValue"), StringType())
    ),
    "exec": FuncType(
        (StringType(),), ResultType(UserType("ProcessResult"), StringType())
    ),
    "exec_with_input": FuncType(
        (StringType(), StringType()),
        ResultType(UserType("ProcessResult"), StringType()),
    ),
    "spawn": FuncType(
        (StringType(), ListType(StringType())),
        ResultType(UserType("ProcessResult"), StringType()),
    ),
    "spawn_with_input": FuncType(
        (StringType(), ListType(StringType()), StringType()),
        ResultType(UserType("ProcessResult"), StringType()),
    ),
    "stdin_read_all": FuncType((), ResultType(StringType(), StringType())),
    "env_get": FuncType((StringType(),), OptionType(StringType())),
    "env_get_or": FuncType((StringType(), StringType()), StringType()),
    "cli_args": FuncType((), ListType(StringType())),
    "regex_match": FuncType((StringType(), StringType()), OptionType(StringType())),
    "regex_find_all": FuncType((StringType(), StringType()), ListType(StringType())),
    "regex_replace": FuncType((StringType(), StringType(), StringType()), StringType()),
    "clear_screen": FuncType((StringType(),), UnitType()),
    "draw_rect": FuncType(
        (IntType(), IntType(), IntType(), IntType(), StringType()), UnitType()
    ),
    "draw_rect_outline": FuncType(
        (IntType(), IntType(), IntType(), IntType(), StringType()), UnitType()
    ),
    "draw_circle": FuncType(
        (IntType(), IntType(), IntType(), StringType()), UnitType()
    ),
    "draw_line": FuncType(
        (IntType(), IntType(), IntType(), IntType(), StringType()), UnitType()
    ),
    "draw_text": FuncType(
        (StringType(), IntType(), IntType(), IntType(), StringType()), UnitType()
    ),
    "screen_width": FuncType((), IntType()),
    "screen_height": FuncType((), IntType()),
    "is_key_down": FuncType((StringType(),), BoolType()),
    "is_key_pressed": FuncType((StringType(),), BoolType()),
    "mouse_x": FuncType((), IntType()),
    "mouse_y": FuncType((), IntType()),
    "is_mouse_down": FuncType((), BoolType()),
    "is_mouse_clicked": FuncType((), BoolType()),
    "get_text_input": FuncType((), StringType()),
    "clear_text_input": FuncType((), UnitType()),
    "array_new": FuncType((IntType(), TypeVar("T")), ArrayType(TypeVar("T"))),
    "array_from_list": FuncType((ListType(TypeVar("T")),), ArrayType(TypeVar("T"))),
    "array_get": FuncType((ArrayType(TypeVar("T")), IntType()), TypeVar("T")),
    "array_set": FuncType(
        (ArrayType(TypeVar("T")), IntType(), TypeVar("T")), UnitType()
    ),
    "array_length": FuncType((ArrayType(TypeVar("T")),), IntType()),
    "array_to_list": FuncType((ArrayType(TypeVar("T")),), ListType(TypeVar("T"))),
    "array_fill": FuncType((ArrayType(TypeVar("T")), TypeVar("T")), UnitType()),
    "array_copy": FuncType((ArrayType(TypeVar("T")),), ArrayType(TypeVar("T"))),
    "mutable_map_new": FuncType((), MutableMapType(TypeVar("K"), TypeVar("V"))),
    "mutable_map_set": FuncType(
        (MutableMapType(TypeVar("K"), TypeVar("V")), TypeVar("K"), TypeVar("V")),
        UnitType(),
    ),
    "mutable_map_get": FuncType(
        (MutableMapType(TypeVar("K"), TypeVar("V")), TypeVar("K")),
        OptionType(TypeVar("V")),
    ),
    "mutable_map_contains": FuncType(
        (MutableMapType(TypeVar("K"), TypeVar("V")), TypeVar("K")), BoolType()
    ),
    "mutable_map_delete": FuncType(
        (MutableMapType(TypeVar("K"), TypeVar("V")), TypeVar("K")), UnitType()
    ),
    "mutable_map_size": FuncType(
        (MutableMapType(TypeVar("K"), TypeVar("V")),), IntType()
    ),
    "mutable_map_keys": FuncType(
        (MutableMapType(TypeVar("K"), TypeVar("V")),), ListType(TypeVar("K"))
    ),
    "vec_new": FuncType((), VecType(TypeVar("T"))),
    "vec_push": FuncType((VecType(TypeVar("T")), TypeVar("T")), UnitType()),
    "vec_get": FuncType((VecType(TypeVar("T")), IntType()), TypeVar("T")),
    "vec_set": FuncType((VecType(TypeVar("T")), IntType(), TypeVar("T")), UnitType()),
    "vec_length": FuncType((VecType(TypeVar("T")),), IntType()),
    "vec_pop": FuncType((VecType(TypeVar("T")),), OptionType(TypeVar("T"))),
    "vec_to_list": FuncType((VecType(TypeVar("T")),), ListType(TypeVar("T"))),
    "vec_from_list": FuncType((ListType(TypeVar("T")),), VecType(TypeVar("T"))),
    "set_new": FuncType((), SetType(TypeVar("T"))),
    "set_from_list": FuncType((ListType(TypeVar("T")),), SetType(TypeVar("T"))),
    "set_add": FuncType((SetType(TypeVar("T")), TypeVar("T")), UnitType()),
    "set_remove": FuncType((SetType(TypeVar("T")), TypeVar("T")), UnitType()),
    "set_contains": FuncType((SetType(TypeVar("T")), TypeVar("T")), BoolType()),
    "set_size": FuncType((SetType(TypeVar("T")),), IntType()),
    "set_to_list": FuncType((SetType(TypeVar("T")),), ListType(TypeVar("T"))),
    "set_union": FuncType(
        (SetType(TypeVar("T")), SetType(TypeVar("T"))), SetType(TypeVar("T"))
    ),
    "set_intersection": FuncType(
        (SetType(TypeVar("T")), SetType(TypeVar("T"))), SetType(TypeVar("T"))
    ),
}


def _effect_for_source_builtin(source_name: str) -> frozenset[str]:
    """Look up the effect set for a builtin by its source name."""
    cap_by_builtin = _build_capability_by_builtin()
    cap = cap_by_builtin.get(source_name)
    if cap is None:
        return frozenset()
    effect = CAPABILITY_TO_EFFECT.get(cap)
    if effect is None:
        return frozenset()
    return frozenset({effect})


def build_builtin_signatures() -> dict[str, FuncType]:
    """Return builtin source-name -> FuncType signatures with effects populated."""
    result: dict[str, FuncType] = {}
    for name, sig in _BUILTIN_SIGNATURES.items():
        effects = _effect_for_source_builtin(name)
        if effects:
            sig = FuncType(sig.param_types, sig.return_type, effects)
        result[name] = sig
    return result


@dataclass(frozen=True)
class BuiltinSpec:
    """Unified builtin metadata for source, runtime, and backend views."""

    runtime_name: str
    source_name: str
    runtime_param_names: tuple[str, ...]
    source_param_names: tuple[str, ...]
    signature: FuncType | None
    capability: str | None
    always_available: bool
    python_backend_name: str
    js_backend_name: str


def _build_capability_by_builtin() -> dict[str, str]:
    capability_by_builtin: dict[str, str] = {}
    for capability, builtin_names in CAPABILITY_MAP.items():
        for builtin_name in builtin_names:
            capability_by_builtin[builtin_name] = capability
    return capability_by_builtin


def _build_builtin_registry() -> dict[str, BuiltinSpec]:
    capability_by_builtin = _build_capability_by_builtin()
    sigs_with_effects = build_builtin_signatures()
    registry: dict[str, BuiltinSpec] = {}

    for runtime_name, runtime_param_names in BUILTIN_PARAM_NAMES.items():
        source_name = SOURCE_BUILTIN_NAME_OVERRIDES.get(runtime_name, runtime_name)
        source_param_names = SOURCE_BUILTIN_PARAM_OVERRIDES.get(
            source_name, runtime_param_names
        )
        registry[runtime_name] = BuiltinSpec(
            runtime_name=runtime_name,
            source_name=source_name,
            runtime_param_names=tuple(runtime_param_names),
            source_param_names=tuple(source_param_names),
            signature=sigs_with_effects.get(source_name),
            capability=capability_by_builtin.get(source_name),
            always_available=source_name in ALWAYS_AVAILABLE_BUILTINS,
            python_backend_name=PYTHON_BACKEND_BUILTIN_NAME_OVERRIDES.get(
                source_name, source_name
            ),
            js_backend_name=JS_BACKEND_BUILTIN_NAME_OVERRIDES.get(
                source_name, source_name
            ),
        )

    missing_sources = set(_BUILTIN_SIGNATURES) - {
        spec.source_name for spec in registry.values()
    }
    if missing_sources:
        raise ValueError(
            "Builtin registry is missing source builtins: "
            + ", ".join(sorted(missing_sources))
        )

    return registry


BUILTIN_REGISTRY = _build_builtin_registry()


def source_builtin_specs() -> dict[str, BuiltinSpec]:
    """Return a fresh source-builtin -> spec mapping."""
    return {spec.source_name: spec for spec in BUILTIN_REGISTRY.values()}


def builtin_param_name_lists() -> dict[str, list[str]]:
    """Return a fresh builtin-name -> param-name mapping."""
    return {
        name: list(spec.runtime_param_names) for name, spec in BUILTIN_REGISTRY.items()
    }


def source_builtin_param_name_lists() -> dict[str, list[str]]:
    """Return builtin param names as exposed in source-level Geno code."""
    return {
        name: list(spec.source_param_names)
        for name, spec in source_builtin_specs().items()
    }


def interpreter_builtin_param_name_lists() -> dict[str, list[str]]:
    """Return builtin param names as exposed by the interpreter surface."""
    return source_builtin_param_name_lists()


def python_backend_builtin_name_map() -> dict[str, str]:
    """Return source builtin names remapped for the Python backend."""
    return {
        spec.source_name: spec.python_backend_name
        for spec in BUILTIN_REGISTRY.values()
        if spec.python_backend_name != spec.source_name
    }


def js_backend_builtin_name_map() -> dict[str, str]:
    """Return source builtin names remapped for the JavaScript backend."""
    return {
        spec.source_name: spec.js_backend_name
        for spec in BUILTIN_REGISTRY.values()
        if spec.js_backend_name != spec.source_name
    }


def python_backend_builtin_helper_names() -> frozenset[str]:
    """Return helper identifiers reserved by the Python backend prelude."""
    return frozenset(python_backend_builtin_name_map().values())


def js_backend_builtin_helper_names() -> frozenset[str]:
    """Return helper identifiers reserved by the JavaScript backend prelude."""
    return frozenset(js_backend_builtin_name_map().values())


def allowed_gated_builtins(capabilities: Collection[str] | None) -> set[str]:
    """Return gated builtin names allowed by the provided capabilities."""
    if capabilities is None:
        return set()

    return {
        spec.source_name
        for spec in BUILTIN_REGISTRY.values()
        if spec.capability in capabilities
    }


def all_builtin_names() -> list[str]:
    """Return the sorted public builtin name list used for completion."""
    return sorted({spec.source_name for spec in BUILTIN_REGISTRY.values()})


__all__ = [
    "ALWAYS_AVAILABLE_BUILTINS",
    "BUILTIN_PARAM_NAMES",
    "BUILTIN_REGISTRY",
    "CAPABILITY_MAP",
    "DEFAULT_ALLOWED_CAPABILITIES",
    "JS_BACKEND_BUILTIN_NAME_OVERRIDES",
    "PYTHON_BACKEND_BUILTIN_NAME_OVERRIDES",
    "SOURCE_BUILTIN_NAME_OVERRIDES",
    "SOURCE_BUILTIN_PARAM_OVERRIDES",
    "BuiltinSpec",
    "all_builtin_names",
    "allowed_gated_builtins",
    "build_builtin_signatures",
    "builtin_param_name_lists",
    "interpreter_builtin_param_name_lists",
    "js_backend_builtin_helper_names",
    "js_backend_builtin_name_map",
    "python_backend_builtin_helper_names",
    "python_backend_builtin_name_map",
    "source_builtin_param_name_lists",
    "source_builtin_specs",
]
