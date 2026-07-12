"""
Single source of truth for builtin capability classification.

Every builtin function is declared exactly once.  ``BUILTIN_PARAM_NAMES``,
``CAPABILITY_MAP``, and ``ALWAYS_AVAILABLE_BUILTINS`` in ``builtin_registry``
are all **derived** from this manifest — no independent classification exists
elsewhere.

Convention:
    capability = None   →  always available (pure computation, no side effects)
    capability = "fs"   →  requires the ``fs`` capability grant
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------
# Key   = runtime builtin name (the key used in BUILTIN_PARAM_NAMES).
# Value = (param_names, capability_or_none).
#
# Ordering matches the historical BUILTIN_PARAM_NAMES grouping so diffs are
# easy to review.
# ---------------------------------------------------------------------------

BUILTIN_MANIFEST: dict[str, tuple[list[str], str | None]] = {
    # --- List operations (always available) --------------------------------
    "length": (["list"], None),
    "head": (["list"], None),
    "tail": (["list"], None),
    "append": (["list", "element"], None),
    "concat": (["list1", "list2"], None),
    "set_at": (["list", "index", "value"], None),
    "slice": (["list", "start", "stop"], None),
    "filter": (["list", "predicate"], None),
    "map": (["list", "transform"], None),
    "fold": (["list", "initial", "reducer"], None),
    "contains": (["list", "element"], None),
    "take_while": (["list", "predicate"], None),
    "all": (["list", "predicate"], None),
    "sort": (["list", "comparator"], None),
    "sort_by": (["list", "key_fn"], None),
    # --- String operations (always available) ------------------------------
    "split": (["text", "delimiter"], None),
    "join": (["parts", "separator"], None),
    "trim": (["text"], None),
    "to_lower": (["text"], None),
    "split_once": (["text", "delimiter"], None),
    "starts_with": (["text", "prefix"], None),
    "to_chars": (["text"], None),
    "sort_strings": (["items"], None),
    "contains_substring": (["text", "substring"], None),
    "repeat_string": (["text", "count"], None),
    # --- Stdlib string wrappers (always available) -------------------------
    "string_trim": (["text"], None),
    "string_trim_start": (["text"], None),
    "string_trim_end": (["text"], None),
    "string_pad_left": (["text", "width", "fill_char"], None),
    "string_pad_right": (["text", "width", "fill_char"], None),
    "string_char_at": (["text", "index"], None),
    "string_index_of": (["text", "substring"], None),
    "string_last_index_of": (["text", "substring"], None),
    "string_repeat": (["text", "count"], None),
    "string_substring": (["text", "start", "stop"], None),
    "string_split": (["text", "delimiter"], None),
    "string_join": (["parts", "separator"], None),
    "string_replace": (["text", "old", "new"], None),
    "string_to_upper": (["text"], None),
    "string_to_lower": (["text"], None),
    "string_starts_with": (["text", "prefix"], None),
    "string_ends_with": (["text", "suffix"], None),
    "string_contains": (["text", "substring"], None),
    "string_split_once": (["text", "delimiter"], None),
    # --- Stdlib list wrappers (always available) ---------------------------
    "list_length": (["list"], None),
    "list_map": (["list", "transform"], None),
    "list_filter": (["list", "predicate"], None),
    # --- Stdlib math wrappers (always available) ---------------------------
    "math_abs": (["value"], None),
    "math_min": (["a", "b"], None),
    "math_max": (["a", "b"], None),
    "math_clamp": (["value", "lo", "hi"], None),
    "math_floor": (["value"], None),
    "math_ceil": (["value"], None),
    "math_round": (["value"], None),
    "math_sqrt": (["value"], None),
    "math_log": (["value"], None),
    "math_sin": (["value"], None),
    "math_cos": (["value"], None),
    "math_pi": ([], None),
    "math_e": ([], None),
    # --- Stdlib math random wrappers (gated by random) ---------------------
    "math_random_int": (["lo", "hi"], "random"),
    "math_random_float": ([], "random"),
    # --- Stdlib map wrappers (always available) ----------------------------
    "map_from_list": (["pairs"], None),
    "map_merge": (["map1", "map2"], None),
    "map_filter_map": (["map", "predicate"], None),
    "map_map_values": (["map", "transform"], None),
    "map_entries": (["map"], None),
    "map_from_entries": (["entries"], None),
    # --- Stdlib result wrappers (always available) -------------------------
    "result_map": (["result", "f"], None),
    "result_map_err": (["result", "f"], None),
    "result_and_then": (["result", "f"], None),
    "result_unwrap_or": (["result", "default"], None),
    "result_is_ok": (["result"], None),
    "result_is_err": (["result"], None),
    "result_to_option": (["result"], None),
    # --- Stdlib option wrappers (always available) -------------------------
    "option_map": (["option", "f"], None),
    "option_and_then": (["option", "f"], None),
    "option_unwrap_or": (["option", "default"], None),
    "option_is_some": (["option"], None),
    "option_is_none": (["option"], None),
    "option_flatten": (["option"], None),
    "option_to_result": (["option", "err"], None),
    # --- Stdlib path wrappers (always available, pure string ops) ----------
    "path_join": (["base", "child"], None),
    "path_parent": (["path"], None),
    "path_filename": (["path"], None),
    "path_extension": (["path"], None),
    "path_is_absolute": (["path"], None),
    # --- Stdlib datetime wrappers (gated by clock) -------------------------
    "datetime_now": ([], "clock"),
    "datetime_format": (["timestamp", "fmt"], "clock"),
    "datetime_parse": (["text", "fmt"], "clock"),
    "datetime_elapsed": (["start", "end_time"], "clock"),
    # --- Stdlib list extras (always available) -----------------------------
    "list_zip": (["list1", "list2"], None),
    "list_enumerate": (["list"], None),
    "list_all": (["list", "predicate"], None),
    "list_flatten": (["lists"], None),
    "list_chunk": (["list", "size"], None),
    "list_take": (["list", "count"], None),
    "list_drop": (["list", "count"], None),
    "list_find": (["list", "predicate"], None),
    "list_find_index": (["list", "predicate"], None),
    "list_any": (["list", "predicate"], None),
    "list_fold_right": (["list", "init", "f"], None),
    "list_intersperse": (["list", "separator"], None),
    "list_group_by": (["list", "key_fn"], None),
    # --- Short-form list extras (always available) -------------------------
    "zip": (["list1", "list2"], None),
    "enumerate": (["list"], None),
    "flat_map": (["list", "fn"], None),
    # --- Math operations (always available) --------------------------------
    "add": (["a", "b"], None),
    "subtract": (["a", "b"], None),
    "multiply": (["a", "b"], None),
    "divide": (["a", "b"], None),
    "sqrt": (["value"], None),
    "floor": (["value"], None),
    "ceil": (["value"], None),
    "round": (["value"], None),
    "max": (["a", "b"], None),
    "clamp": (["value", "min", "max"], None),
    "abs": (["value"], None),
    "square": (["value"], None),
    # --- Type predicates (always available) --------------------------------
    "is_sorted": (["list"], None),
    "is_positive": (["value"], None),
    "is_numeric_string": (["text"], None),
    "is_permutation": (["list1", "list2"], None),
    # --- Conversions (always available) ------------------------------------
    "parse_int": (["text"], None),
    "parse_float": (["text"], None),
    "to_string": (["value"], None),
    "float_to_int": (["value"], None),
    "int_to_float": (["value"], None),
    # --- List / String extras (always available) ---------------------------
    "to_upper": (["text"], None),
    "replace": (["text", "old", "new"], None),
    "ends_with": (["text", "suffix"], None),
    "reverse": (["list"], None),
    "bit_or": (["a", "b"], None),
    "range": (["start", "end", "step"], None),
    # --- String extras (always available) ----------------------------------
    "substring": (["text", "start", "stop"], None),
    "format": (["template", "values"], None),
    # --- Option operations (always available) ------------------------------
    "is_some": (["option"], None),
    "is_none": (["option"], None),
    "unwrap": (["option"], None),
    "unwrap_or": (["option", "default"], None),
    # --- Map operations (always available) ---------------------------------
    "map_insert": (["map", "key", "value"], None),
    "map_get": (["map", "key"], None),
    # --- IO (gated by print) -----------------------------------------------
    "print_": (["value"], "print"),
    # --- Array operations (always available) --------------------------------
    "array_new": (["size", "default"], None),
    "array_from_list": (["list"], None),
    "array_get": (["array", "index"], None),
    "array_set": (["array", "index", "value"], None),
    "array_length": (["array"], None),
    "array_to_list": (["array"], None),
    "array_fill": (["array", "value"], None),
    "array_copy": (["array"], None),
    # --- Graphics builtins (browser target; interpreter fallback contract) -
    "clear_screen": (["color"], None),
    "draw_rect": (["x", "y", "width", "height", "color"], None),
    "draw_rect_outline": (["x", "y", "width", "height", "color"], None),
    "draw_circle": (["x", "y", "radius", "color"], None),
    "draw_line": (["x1", "y1", "x2", "y2", "color"], None),
    "draw_text": (["text", "x", "y", "size", "color"], None),
    "screen_width": ([], None),
    "screen_height": ([], None),
    # --- Input builtins (browser target; interpreter fallback contract) ----
    "is_key_down": (["key"], None),
    "is_key_pressed": (["key"], None),
    # --- Mouse input builtins (browser target; interpreter fallback) -------
    "mouse_x": ([], None),
    "mouse_y": ([], None),
    "is_mouse_down": ([], None),
    "is_mouse_clicked": ([], None),
    # --- Text input builtins (browser target; interpreter fallback) --------
    "get_text_input": ([], None),
    "clear_text_input": ([], None),
    # --- Clock (gated by clock) --------------------------------------------
    "clock_now": ([], "clock"),
    "clock_format": (["timestamp", "fmt"], "clock"),
    "clock_parse": (["text", "fmt"], "clock"),
    "clock_elapsed": (["start", "end_time"], "clock"),
    "sleep_ms": (["ms"], "clock"),
    # --- Random (gated by random) ------------------------------------------
    "random_int": (["min", "max"], "random"),
    "random_float": ([], "random"),
    # --- Char codes (always available) -------------------------------------
    "char_code": (["text"], None),
    "from_char_code": (["code"], None),
    # --- MutableMap (always available) -------------------------------------
    "mutable_map_new": ([], None),
    "mutable_map_set": (["map", "key", "value"], None),
    "mutable_map_get": (["map", "key"], None),
    "mutable_map_contains": (["map", "key"], None),
    "mutable_map_delete": (["map", "key"], None),
    "mutable_map_size": (["map"], None),
    "mutable_map_keys": (["map"], None),
    # --- Vec (always available) --------------------------------------------
    "vec_new": ([], None),
    "vec_push": (["vec", "item"], None),
    "vec_get": (["vec", "index"], None),
    "vec_set": (["vec", "index", "value"], None),
    "vec_length": (["vec"], None),
    "vec_pop": (["vec"], None),
    "vec_to_list": (["vec"], None),
    "vec_from_list": (["list"], None),
    # --- Set (always available) --------------------------------------------
    "set_new": ([], None),
    "set_from_list": (["list"], None),
    "set_add": (["set", "item"], None),
    "set_remove": (["set", "item"], None),
    "set_contains": (["set", "item"], None),
    "set_size": (["set"], None),
    "set_to_list": (["set"], None),
    "set_union": (["a", "b"], None),
    "set_intersection": (["a", "b"], None),
    # --- File I/O (gated by fs) --------------------------------------------
    "fs_read_text": (["path"], "fs"),
    "fs_write_text": (["path", "content"], "fs"),
    "fs_list_dir": (["path"], "fs"),
    "fs_exists": (["path"], "fs"),
    # --- Regex (gated by regex) --------------------------------------------
    "regex_match": (["pattern", "text"], "regex"),
    "regex_find_all": (["pattern", "text"], "regex"),
    "regex_replace": (["pattern", "replacement", "text"], "regex"),
    # --- HTTP (gated by http) ----------------------------------------------
    "http_fetch": (["url"], "http"),
    "http_post": (["url", "body"], "http"),
    "http_request": (["method", "url", "headers", "body"], "http"),
    # --- Serve (gated by serve) --------------------------------------------
    "http_listen": (["port"], "serve"),
    "http_route": (["method", "path", "handler"], "serve"),
    "http_respond": (["status", "headers", "body"], "serve"),
    # --- JSON (always available, pure computation) -------------------------
    "json_parse": (["text"], None),
    "json_stringify": (["value"], None),
    "json_stringify_pretty": (["value", "indent"], None),
    "json_to_string": (["value"], None),
    # --- CSV / TOML (always available, pure computation) -------------------
    "csv_parse": (["text"], None),
    "csv_parse_with_headers": (["text"], None),
    "toml_parse": (["text"], None),
    # --- Process execution (gated by process) ------------------------------
    "exec": (["command"], "process"),
    "exec_with_input": (["command", "stdin"], "process"),
    "spawn": (["program", "args"], "process"),
    "spawn_with_input": (["program", "args", "stdin"], "process"),
    # --- Standard input (gated by stdin) -----------------------------------
    "stdin_read_all": ([], "stdin"),
    # --- Environment variables (gated by env) ------------------------------
    "env_get": (["name"], "env"),
    "env_get_or": (["name", "default"], "env"),
    "cli_args": ([], "env"),
}


# ---------------------------------------------------------------------------
# Derived helpers — used by builtin_registry to stay in sync
# ---------------------------------------------------------------------------


def manifest_param_names() -> dict[str, list[str]]:
    """Return ``{runtime_name: param_names}`` derived from the manifest."""
    return {name: list(params) for name, (params, _cap) in BUILTIN_MANIFEST.items()}


def manifest_capability_map(
    source_name_overrides: dict[str, str],
) -> dict[str, list[str]]:
    """Return ``{capability: [source_names]}`` derived from the manifest."""
    cap_map: dict[str, list[str]] = {}
    for runtime_name, (_params, cap) in BUILTIN_MANIFEST.items():
        if cap is not None:
            source_name = source_name_overrides.get(runtime_name, runtime_name)
            cap_map.setdefault(cap, []).append(source_name)
    return cap_map


def manifest_capability_names() -> frozenset[str]:
    """Capability names referenced by the manifest.

    Equals ``frozenset(manifest_capability_map(...))`` for any overrides
    (overrides rename builtin source names, never capability names). Lets
    capability *parsing* avoid importing the full builtin registry.
    """
    return frozenset(
        cap for (_params, cap) in BUILTIN_MANIFEST.values() if cap is not None
    )


def manifest_always_available(
    source_name_overrides: dict[str, str],
) -> frozenset[str]:
    """Return the set of source-level names that are always available."""
    return frozenset(
        source_name_overrides.get(runtime_name, runtime_name)
        for runtime_name, (_params, cap) in BUILTIN_MANIFEST.items()
        if cap is None
    )
