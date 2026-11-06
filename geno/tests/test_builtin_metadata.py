import pytest

from geno import builtin_manifest
from geno.builtin_manifest import (
    BORROWED_COLLECTION_PARAMS,
    BUILTIN_MANIFEST,
    manifest_borrowed_collection_params,
    manifest_fresh_collection_builtins,
)
from geno.builtin_metadata import (
    builtin_param_name_lists as compat_builtin_param_name_lists,
)
from geno.builtin_metadata import (
    source_builtin_param_name_lists as compat_source_builtin_param_name_lists,
)
from geno.builtin_registry import (
    ALWAYS_AVAILABLE_BUILTINS,
    BORROWED_ARGUMENT_NAMES,
    BORROWED_ARGUMENT_POSITIONS,
    BUILTIN_PARAM_NAMES,
    BUILTIN_REGISTRY,
    CAPABILITY_MAP,
    DEFAULT_ALLOWED_CAPABILITIES,
    FRESH_COLLECTION_BUILTINS,
    SOURCE_BUILTIN_NAME_OVERRIDES,
    all_builtin_names,
    allowed_gated_builtins,
    build_builtin_signatures,
    builtin_param_name_lists,
    interpreter_builtin_param_name_lists,
    js_backend_builtin_helper_names,
    js_backend_builtin_name_map,
    python_backend_builtin_helper_names,
    python_backend_builtin_name_map,
    source_builtin_param_name_lists,
    source_builtin_specs,
)
from geno.compiler import RESERVED_PRELUDE_NAMES, Compiler
from geno.interpreter import Interpreter
from geno.js_compiler import JS_RESERVED_PRELUDE_NAMES, JSCompiler
from geno.parser import parse
from geno.typechecker import TypeChecker
from geno.types import ArrayType, MutableMapType, SetType, VecType
from geno.values import BuiltinFunction


def test_all_builtin_names_include_pure_and_capability_gated_builtins():
    names = all_builtin_names()
    assert "length" in names
    assert "print" in names
    assert "exec" in names


def test_builtin_param_name_lists_returns_fresh_copies():
    param_names = builtin_param_name_lists()
    param_names["length"].append("unexpected")
    assert BUILTIN_PARAM_NAMES["length"] == ["list"]


def test_compatibility_wrappers_match_registry_views():
    assert compat_builtin_param_name_lists() == builtin_param_name_lists()
    assert compat_source_builtin_param_name_lists() == source_builtin_param_name_lists()


def test_allowed_gated_builtins_is_fail_closed_for_none():
    assert allowed_gated_builtins(None) == set()


def test_default_allowed_capabilities_match_public_server_defaults():
    assert frozenset({"print", "clock", "random"}) == DEFAULT_ALLOWED_CAPABILITIES


def test_source_builtin_param_names_expose_public_surface():
    param_names = source_builtin_param_name_lists()
    assert param_names["print"] == ["value"]
    assert "print_" not in param_names
    assert param_names["range"] == ["start", "end"]


def test_interpreter_uses_shared_builtin_param_names():
    interpreter = Interpreter(check_examples=False)
    expected_param_names = interpreter_builtin_param_name_lists()

    for name, value in interpreter.global_env.bindings.items():
        if isinstance(value, BuiltinFunction) and name in expected_param_names:
            assert value.param_names == expected_param_names[name]


def test_typechecker_uses_shared_source_builtin_param_names():
    checker = TypeChecker()
    builtin_signatures = build_builtin_signatures()
    expected_param_names = source_builtin_param_name_lists()

    assert set(builtin_signatures) == set(expected_param_names)

    for name, params in expected_param_names.items():
        assert checker.builtin_types[name][0] == builtin_signatures[name]
        assert checker.func_param_names[name] == params
        assert checker.builtin_types[name][1] == params


def test_builtin_registry_exposes_combined_spec_views():
    source_specs = source_builtin_specs()

    assert BUILTIN_REGISTRY["print_"].source_name == "print"
    assert BUILTIN_REGISTRY["print_"].python_backend_name == "print_"
    assert BUILTIN_REGISTRY["print_"].js_backend_name == "print_"
    assert source_specs["print"].signature == build_builtin_signatures()["print"]
    assert list(source_specs["range"].source_param_names) == ["start", "end"]
    assert list(BUILTIN_REGISTRY["range"].runtime_param_names) == [
        "start",
        "end",
        "step",
    ]


def test_named_argument_metadata_stays_aligned_across_subsystems():
    runtime_param_names = builtin_param_name_lists()
    source_param_names = source_builtin_param_name_lists()
    interpreter = Interpreter(check_examples=False)
    compiler = Compiler()
    js_compiler = JSCompiler()

    for name, params in runtime_param_names.items():
        assert compiler.func_param_names[name] == params
        assert js_compiler.func_param_names[name] == params
        if name != "print_":
            builtin = interpreter.global_env.bindings[name]
            assert isinstance(builtin, BuiltinFunction)
            assert builtin.param_names == source_param_names.get(name, params)

    print_builtin = interpreter.global_env.bindings["print"]
    assert isinstance(print_builtin, BuiltinFunction)
    assert print_builtin.param_names == source_param_names["print"]


def test_backend_builtin_remaps_stay_aligned_across_compilers():
    assert python_backend_builtin_name_map() == Compiler._BUILTIN_NAME_MAP
    assert js_backend_builtin_name_map() == JSCompiler._BUILTIN_NAME_MAP

    assert Compiler._target_name("exec") == "exec_"
    assert JSCompiler._target_name("exec") == "exec"


def test_backend_reserved_helper_names_match_shared_remap_metadata():
    assert python_backend_builtin_helper_names() <= RESERVED_PRELUDE_NAMES
    assert js_backend_builtin_helper_names() <= JS_RESERVED_PRELUDE_NAMES


# ---------------------------------------------------------------------------
# Manifest-driven invariants
# ---------------------------------------------------------------------------


def test_every_builtin_is_classified_by_manifest():
    """Every builtin in BUILTIN_PARAM_NAMES must be in the manifest, and vice versa."""
    assert set(BUILTIN_PARAM_NAMES) == set(BUILTIN_MANIFEST)


def test_no_builtin_falls_through_capability_cracks():
    """Every builtin is either always-available or capability-gated — no third state.

    This was the root cause of the to_upper / ends_with / replace bug:
    builtins existed in the param registry but were in neither
    ALWAYS_AVAILABLE_BUILTINS nor CAPABILITY_MAP, causing them to be
    denied under capability enforcement.
    """
    gated_builtins: set[str] = set()
    for names in CAPABILITY_MAP.values():
        gated_builtins.update(names)

    all_source_names = {
        SOURCE_BUILTIN_NAME_OVERRIDES.get(name, name) for name in BUILTIN_PARAM_NAMES
    }

    for source_name in all_source_names:
        classified = (
            source_name in ALWAYS_AVAILABLE_BUILTINS or source_name in gated_builtins
        )
        assert classified, (
            f"Builtin '{source_name}' is neither always-available nor "
            f"capability-gated — it would be denied under capability enforcement"
        )


def test_aliases_inherit_capability_classification():
    """Short-form aliases must share the capability of their long-form canonical name.

    Pairs like (to_upper, string_to_upper) and (ends_with, string_ends_with)
    must both be classified identically.
    """
    SHORT_LONG_PAIRS = [
        ("to_upper", "string_to_upper"),
        ("to_lower", "string_to_lower"),
        ("ends_with", "string_ends_with"),
        ("starts_with", "string_starts_with"),
        ("replace", "string_replace"),
        ("split", "string_split"),
        ("join", "string_join"),
        ("split_once", "string_split_once"),
        ("abs", "math_abs"),
        ("max", "math_max"),
        ("floor", "math_floor"),
        ("ceil", "math_ceil"),
        ("round", "math_round"),
        ("sqrt", "math_sqrt"),
        ("is_some", "option_is_some"),
        ("is_none", "option_is_none"),
        ("unwrap_or", "option_unwrap_or"),
        ("random_int", "math_random_int"),
        ("random_float", "math_random_float"),
    ]

    for short, long in SHORT_LONG_PAIRS:
        short_cap = BUILTIN_MANIFEST[short][1]
        long_cap = BUILTIN_MANIFEST[long][1]
        assert short_cap == long_cap, (
            f"Alias mismatch: '{short}' has capability={short_cap!r} but "
            f"'{long}' has capability={long_cap!r}"
        )


def test_always_available_builtins_derived_from_manifest():
    """ALWAYS_AVAILABLE_BUILTINS must match the manifest derivation exactly."""
    expected = frozenset(
        SOURCE_BUILTIN_NAME_OVERRIDES.get(name, name)
        for name, (_, cap) in BUILTIN_MANIFEST.items()
        if cap is None
    )
    assert expected == ALWAYS_AVAILABLE_BUILTINS


def test_capability_map_derived_from_manifest():
    """CAPABILITY_MAP must match the manifest derivation exactly."""
    expected: dict[str, list[str]] = {}
    for name, (_, cap) in BUILTIN_MANIFEST.items():
        if cap is not None:
            source = SOURCE_BUILTIN_NAME_OVERRIDES.get(name, name)
            expected.setdefault(cap, []).append(source)

    assert set(CAPABILITY_MAP) == set(expected)
    for cap in expected:
        assert set(CAPABILITY_MAP[cap]) == set(expected[cap]), (
            f"Capability '{cap}' mismatch: "
            f"registry={sorted(CAPABILITY_MAP[cap])}, "
            f"manifest={sorted(expected[cap])}"
        )


# ---------------------------------------------------------------------------
# Collection ownership metadata (proposal 0019)
# ---------------------------------------------------------------------------

_REFERENCE_COLLECTION_TYPES = (ArrayType, VecType, SetType, MutableMapType)

# Parameters whose declared type is not a reference collection but which the
# checker also accepts an Array for. Each is verified by type-checking a call.
_ARRAY_ACCEPTING_PARAMS = {("length", "list")}


def test_fresh_collection_builtins_are_derived_from_manifest():
    expected = frozenset(
        SOURCE_BUILTIN_NAME_OVERRIDES.get(name, name)
        for name in builtin_manifest.FRESH_COLLECTION_BUILTINS
    )
    assert expected == FRESH_COLLECTION_BUILTINS
    assert "array_new" in FRESH_COLLECTION_BUILTINS
    assert "array_fill" not in FRESH_COLLECTION_BUILTINS


def test_every_fresh_builtin_returns_a_reference_collection():
    signatures = build_builtin_signatures()
    for name in FRESH_COLLECTION_BUILTINS:
        return_type = signatures[name].return_type
        assert isinstance(return_type, _REFERENCE_COLLECTION_TYPES), (
            f"'{name}' is listed as allocating a collection but returns {return_type}"
        )


def test_every_borrowed_parameter_has_a_reference_collection_type():
    signatures = build_builtin_signatures()
    param_names = source_builtin_param_name_lists()
    for name, borrowed in BORROWED_ARGUMENT_NAMES.items():
        for param in borrowed:
            if (name, param) in _ARRAY_ACCEPTING_PARAMS:
                continue
            param_type = signatures[name].param_types[param_names[name].index(param)]
            assert isinstance(param_type, _REFERENCE_COLLECTION_TYPES), (
                f"'{name}({param})' is listed as borrowing a collection but has "
                f"type {param_type}"
            )


def test_array_accepting_borrowed_parameters_accept_an_array():
    for name, param in _ARRAY_ACCEPTING_PARAMS:
        assert param in BORROWED_ARGUMENT_NAMES[name]
        source = (
            "func f(n: Int) -> Int\n"
            "    example 2 -> 2\n"
            f"    return {name}({param}: array_new(n, 0))\n"
            "end func\n"
        )
        TypeChecker().check_program(parse(source))


def test_every_reference_collection_parameter_is_listed_as_borrowed():
    """Completeness: a builtin that keeps a collection must be classified.

    A parameter left out of BORROWED_COLLECTION_PARAMS only makes fewer
    collections owned, so this guards against forgetting a new builtin, not
    against unsoundness.
    """
    param_names = source_builtin_param_name_lists()
    for name, signature in build_builtin_signatures().items():
        for index, param_type in enumerate(signature.param_types):
            if isinstance(param_type, _REFERENCE_COLLECTION_TYPES):
                param = param_names[name][index]
                assert param in BORROWED_ARGUMENT_NAMES.get(name, frozenset()), (
                    f"'{name}({param}: {param_type})' takes a reference "
                    "collection: list it in BORROWED_COLLECTION_PARAMS"
                )


def test_borrowed_argument_views_are_derived_from_manifest():
    param_names = source_builtin_param_name_lists()
    assert set(BORROWED_ARGUMENT_NAMES) == {
        SOURCE_BUILTIN_NAME_OVERRIDES.get(name, name)
        for name in BORROWED_COLLECTION_PARAMS
    }
    assert set(BORROWED_ARGUMENT_POSITIONS) == set(BORROWED_ARGUMENT_NAMES)
    for name, borrowed in BORROWED_ARGUMENT_NAMES.items():
        positions = frozenset(
            index for index, param in enumerate(param_names[name]) if param in borrowed
        )
        assert len(positions) == len(borrowed)
        assert BORROWED_ARGUMENT_POSITIONS[name] == positions
    assert BORROWED_ARGUMENT_POSITIONS["array_set"] == {0}
    assert BORROWED_ARGUMENT_POSITIONS["vec_push"] == {0}
    assert BORROWED_ARGUMENT_POSITIONS["set_union"] == {0, 1}


def test_ownership_tables_reject_unknown_names(monkeypatch):
    monkeypatch.setattr(
        builtin_manifest,
        "FRESH_COLLECTION_BUILTINS",
        builtin_manifest.FRESH_COLLECTION_BUILTINS | {"array_nwe"},
    )
    with pytest.raises(ValueError, match="array_nwe"):
        manifest_fresh_collection_builtins(SOURCE_BUILTIN_NAME_OVERRIDES)

    monkeypatch.setattr(
        builtin_manifest,
        "BORROWED_COLLECTION_PARAMS",
        {**BORROWED_COLLECTION_PARAMS, "vec_push": ("vec", "elem")},
    )
    with pytest.raises(ValueError, match="elem"):
        manifest_borrowed_collection_params(SOURCE_BUILTIN_NAME_OVERRIDES)

    monkeypatch.setattr(
        builtin_manifest, "BORROWED_COLLECTION_PARAMS", {"vec_shove": ("vec",)}
    )
    with pytest.raises(ValueError, match="vec_shove"):
        manifest_borrowed_collection_params(SOURCE_BUILTIN_NAME_OVERRIDES)
