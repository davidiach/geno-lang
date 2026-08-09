"""
Regression tests for MED-01: SandboxConfig.max_collection_size must be
respected by builtin pre-checks, not just by the interpreter's post-call
size check.

Prior to the fix the following builtins pre-checked against a hardcoded
10_000_000 ceiling instead of the configured limit, so a tightened sandbox
allowed pre-allocation up to 10 M elements before the post-check could
reject the result.
"""

import functools
import inspect
import io
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from geno import builtins as _builtins
from geno.builtin_registry import CAPABILITY_MAP
from geno.interpreter import Interpreter
from geno.parser import parse
from geno.sandbox import SandboxConfig
from geno.values import (
    ArrayValue,
    BuiltinFunction,
    ConstructorValue,
    MutableMapValue,
    SetValue,
    VecValue,
)
from geno.values import RuntimeError as GenoRuntimeError


@pytest.fixture(autouse=True)
def _restore_cap():
    """Save/restore the module-level cap around each test."""
    saved = _builtins.get_max_collection_size()
    yield
    _builtins.set_max_collection_size(saved)


def _call_installed(interp: Interpreter, name: str, args: list):
    return interp._call_function(interp.global_env.bindings[name], args)


def _append_one(interp: Interpreter):
    return _call_installed(interp, "append", [[0, 1, 2], 3])


@pytest.mark.parametrize(
    "construction_order",
    [("wide", "tight"), ("tight", "wide")],
)
@pytest.mark.parametrize("call_order", [("wide", "tight"), ("tight", "wide")])
def test_live_interpreters_keep_collection_limits_isolated(
    construction_order, call_order
):
    limits = {"wide": 4, "tight": 3}
    interpreters = {}
    for name in construction_order:
        interpreters[name] = _interp_with_limit(limits[name])

    for name in call_order:
        if name == "wide":
            assert _append_one(interpreters[name]) == [0, 1, 2, 3]
        else:
            with pytest.raises(
                GenoRuntimeError,
                match=r"List size exceeds limit \(4 > 3\)",
            ):
                _append_one(interpreters[name])


def test_interpreter_init_does_not_mutate_direct_builtin_limit():
    _builtins.set_max_collection_size(7)

    _interp_with_limit(3)

    assert _builtins.get_max_collection_size() == 7


def test_direct_builtin_limit_remains_legacy_global_and_not_interpreter_context():
    interp = _interp_with_limit(4)
    _builtins.set_max_collection_size(3)

    with pytest.raises(
        GenoRuntimeError,
        match=r"List size exceeds limit \(4 > 3\)",
    ):
        _builtins.builtin_append([0, 1, 2], 3)

    with pytest.raises(
        GenoRuntimeError,
        match=r"List size exceeds limit \(4 > 3\)",
    ):
        _builtins.builtin_append([0, 1, 2], 3, max_collection_size=4)

    assert _append_one(interp) == [0, 1, 2, 3]


def test_interpreter_bound_limit_wins_above_and_below_legacy_global():
    wide = _interp_with_limit(4)
    tight = _interp_with_limit(3)

    _builtins.set_max_collection_size(3)
    assert _append_one(wide) == [0, 1, 2, 3]

    _builtins.set_max_collection_size(4)
    with pytest.raises(
        GenoRuntimeError,
        match=r"List size exceeds limit \(4 > 3\)",
    ):
        _append_one(tight)


def test_all_limit_aware_interpreter_builtins_bind_the_interpreter_limit():
    limit = 42
    interp = Interpreter(
        check_examples=False,
        sandbox_config=SandboxConfig(max_collection_size=limit),
        capabilities=CAPABILITY_MAP,
    )
    bound_names = set()
    bound_funcs = set()
    bound_targets = set()
    unbound_names = []

    for name, value in interp.global_env.bindings.items():
        if not isinstance(value, BuiltinFunction):
            continue
        func = value.func
        target = func.func if isinstance(func, functools.partial) else func
        try:
            parameters = inspect.signature(target).parameters
        except (TypeError, ValueError):
            continue
        if "max_collection_size" not in parameters:
            if (
                isinstance(func, functools.partial)
                and "max_collection_size" in func.keywords
            ):
                unbound_names.append(name)
                continue
            continue
        if name == "to_string":
            if (
                isinstance(func, functools.partial)
                or not inspect.isfunction(func)
                or (func.__kwdefaults__ or {}).get("max_collection_size") != limit
            ):
                unbound_names.append(name)
                continue
        elif (
            not isinstance(func, functools.partial)
            or func.keywords.get("max_collection_size") != limit
        ):
            unbound_names.append(name)
            continue
        if not inspect.isfunction(target):
            unbound_names.append(name)
            continue
        shadow_globals = target.__globals__
        if (
            shadow_globals is vars(_builtins)
            or shadow_globals.get("_effective_max_collection_size")
            is not _builtins._interpreter_effective_max_collection_size
        ):
            unbound_names.append(name)
            continue
        bound_names.add(name)
        bound_funcs.add(func)
        bound_targets.add(target)

    assert unbound_names == []
    assert len(bound_names) == 41
    assert {"to_string", "vec_push", "mutable_map_set"} <= bound_names

    roots = _builtins._INTERPRETER_BUILTIN_ROOTS
    clones = _builtins._INTERPRETER_BUILTIN_CLONES
    bound_cache = _builtins._INTERPRETER_BOUND_BUILTINS
    assert roots is not None
    assert bound_cache is not None
    assert len(roots) == 41
    assert bound_cache[0] == limit
    assert set(bound_cache[1].values()) == bound_funcs
    assert len(set(roots.values()) & bound_targets) == 40
    assert len(clones) == 82

    for original, clone in roots.items():
        assert clones[original] is clone

    for original, clone in clones.items():
        assert clone.__code__ is original.__code__
        assert clone.__defaults__ == original.__defaults__
        assert clone.__kwdefaults__ == original.__kwdefaults__
        assert clone.__annotations__ == original.__annotations__
        assert "_MAX_COLLECTION_SIZE" not in clone.__code__.co_names
        for dependency_name in _builtins._iter_code_global_names(original.__code__):
            dependency = vars(_builtins).get(dependency_name)
            if dependency_name == "_effective_max_collection_size":
                assert (
                    clone.__globals__[dependency_name]
                    is _builtins._interpreter_effective_max_collection_size
                )
            elif (
                inspect.isfunction(dependency)
                and dependency.__module__ == _builtins.__name__
            ):
                assert clone.__globals__[dependency_name] is clones[dependency]

    to_string = interp.global_env.bindings["to_string"].func
    root_to_string = roots[_builtins._bounded_stringify_value]
    assert inspect.isfunction(to_string)
    assert to_string.__code__ is root_to_string.__code__
    assert to_string.__globals__ is root_to_string.__globals__
    assert to_string.__kwdefaults__ is not None
    assert to_string.__kwdefaults__["max_collection_size"] == limit
    assert interp._builtin_stringify_value is to_string
    assert "_effective_max_collection_size" not in to_string.__code__.co_names

    second = Interpreter(
        check_examples=False,
        sandbox_config=SandboxConfig(max_collection_size=limit),
        capabilities=CAPABILITY_MAP,
    )
    assert _builtins._INTERPRETER_BUILTIN_ROOTS is roots
    assert _builtins._INTERPRETER_BOUND_BUILTINS is bound_cache
    assert second.global_env.bindings["to_string"].func is to_string
    assert (
        second.global_env.bindings["append"].func
        is interp.global_env.bindings["append"].func
    )


def test_interpreter_stringify_limit_is_isolated_from_newer_interpreter():
    wide = _interp_with_limit(10)
    tight = _interp_with_limit(4)
    value = [1, 2]

    assert _call_installed(wide, "to_string", [value]) == "[1, 2]"
    with pytest.raises(
        GenoRuntimeError,
        match=r"to_string: String size exceeds limit \(5 > 4\)",
    ):
        _call_installed(tight, "to_string", [value])


def test_reentrant_callback_uses_each_bound_interpreter_limit():
    wide = _interp_with_limit(10)
    tight = _interp_with_limit(4)
    value = [1, 2]

    def call_tight_then_wide(arg):
        with pytest.raises(
            GenoRuntimeError,
            match=r"to_string: String size exceeds limit \(5 > 4\)",
        ):
            _call_installed(tight, "to_string", [arg])
        return _call_installed(wide, "to_string", [arg])

    callback = BuiltinFunction(
        "call_tight_then_wide",
        call_tight_then_wide,
        1,
        ["value"],
    )
    assert wide._call_function(callback, [value]) == "[1, 2]"

    _builtins.set_max_collection_size(4)
    with pytest.raises(
        GenoRuntimeError,
        match=r"to_string: String size exceeds limit \(5 > 4\)",
    ):
        _builtins.stringify_value(value)


def test_bound_limit_survives_nested_builtin_exception():
    interp = _interp_with_limit(10)
    value = [1, 2]

    def fail_after_bound_stringify(arg):
        assert _call_installed(interp, "to_string", [arg]) == "[1, 2]"
        raise GenoRuntimeError("callback failed")

    callback = BuiltinFunction(
        "fail_after_bound_stringify",
        fail_after_bound_stringify,
        1,
        ["value"],
    )
    _builtins.set_max_collection_size(4)

    with pytest.raises(GenoRuntimeError, match="callback failed"):
        interp._call_function(callback, [value])
    with pytest.raises(
        GenoRuntimeError,
        match=r"to_string: String size exceeds limit \(5 > 4\)",
    ):
        _builtins.stringify_value(value)


def test_host_callback_output_is_checked_at_interpreter_egress():
    interp = _interp_with_limit(2)
    observed = []

    def host_callback():
        observed.append(True)
        return "abcd"

    callback = BuiltinFunction(
        "host_callback",
        host_callback,
        0,
        [],
    )

    with pytest.raises(
        GenoRuntimeError,
        match=r"String size exceeds limit \(4 > 2\)",
    ):
        interp._call_function(callback, [])

    assert observed == [True]


def test_direct_builtin_inside_host_callback_uses_legacy_global_cap():
    interp = _interp_with_limit(10)
    _builtins.set_max_collection_size(4)
    callback = BuiltinFunction(
        "direct_stringify_callback",
        _builtins.stringify_value,
        1,
        ["value"],
    )

    with pytest.raises(
        GenoRuntimeError,
        match=r"to_string: String size exceeds limit \(5 > 4\)",
    ):
        interp._call_function(callback, [[1, 2]])


def test_api_env_policy_wrapper_retains_interpreter_bound_limit(monkeypatch):
    from geno.api import RunConfig, _install_env_policy

    env_name = "G"
    monkeypatch.setenv(env_name, "abcd")
    interp = Interpreter(
        check_examples=False,
        sandbox_config=SandboxConfig(max_collection_size=4),
        capabilities={"env"},
    )
    _install_env_policy(
        interp,
        RunConfig(
            capabilities={"env"},
            env_allowed_names={env_name},
        ),
    )
    _builtins.set_max_collection_size(3)

    assert _call_installed(interp, "env_get_or", [env_name, ""]) == "abcd"


def test_concurrent_interpreter_initialization_and_calls_are_isolated():
    wide_ready = threading.Event()
    tight_ready = threading.Event()
    call_barrier = threading.Barrier(2)

    def wide_worker():
        interp = _interp_with_limit(4)
        wide_ready.set()
        assert tight_ready.wait(timeout=5)
        call_barrier.wait(timeout=5)
        for _ in range(100):
            assert _append_one(interp) == [0, 1, 2, 3]

    def tight_worker():
        assert wide_ready.wait(timeout=5)
        interp = _interp_with_limit(3)
        tight_ready.set()
        call_barrier.wait(timeout=5)
        for _ in range(100):
            with pytest.raises(
                GenoRuntimeError,
                match=r"List size exceeds limit \(4 > 3\)",
            ):
                _append_one(interp)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(wide_worker), executor.submit(tight_worker)]
        for future in futures:
            future.result(timeout=10)


def test_concurrent_different_limit_cache_replacement_is_isolated():
    barrier = threading.Barrier(16)

    def worker(limit):
        barrier.wait(timeout=5)
        for _ in range(20):
            interp = _interp_with_limit(limit)
            if limit == 10:
                assert _call_installed(interp, "append", [[0, 1, 2, 3], 4]) == [
                    0,
                    1,
                    2,
                    3,
                    4,
                ]
                assert _call_installed(interp, "to_string", [[1, 2]]) == "[1, 2]"
            else:
                with pytest.raises(GenoRuntimeError, match="size exceeds limit"):
                    _call_installed(interp, "append", [[0, 1, 2, 3], 4])
                with pytest.raises(GenoRuntimeError, match="size exceeds limit"):
                    _call_installed(interp, "to_string", [[1, 2]])

    limits = [4, 10] * 8
    with ThreadPoolExecutor(max_workers=len(limits)) as executor:
        futures = [executor.submit(worker, limit) for limit in limits]
        for future in futures:
            future.result(timeout=15)

    bound_cache = _builtins._INTERPRETER_BOUND_BUILTINS
    assert bound_cache is not None
    assert bound_cache[0] in {4, 10}
    assert len(bound_cache[1]) == 41


def test_unbound_mutation_prechecks_use_interpreter_local_limit():
    wide = _interp_with_limit(2)
    _interp_with_limit(1)
    vec = _call_installed(wide, "vec_new", [])

    _call_installed(wide, "vec_push", [vec, 1])
    _call_installed(wide, "vec_push", [vec, 2])
    with pytest.raises(
        GenoRuntimeError,
        match=r"Vec size exceeds limit \(3 > 2\)",
    ):
        _call_installed(wide, "vec_push", [vec, 3])

    assert _call_installed(wide, "vec_length", [vec]) == 2

    mutable_map = _call_installed(wide, "mutable_map_new", [])
    _call_installed(wide, "mutable_map_set", [mutable_map, "a", 1])
    _call_installed(wide, "mutable_map_set", [mutable_map, "b", 2])
    with pytest.raises(
        GenoRuntimeError,
        match=r"MutableMap size exceeds limit \(3 > 2\)",
    ):
        _call_installed(wide, "mutable_map_set", [mutable_map, "c", 3])

    assert _call_installed(wide, "mutable_map_size", [mutable_map]) == 2


def test_stringify_limit_fails_before_result_allocation(monkeypatch):
    interp = _interp_with_limit(4)
    result_called = False
    original_result = _builtins._StringifyWriter.result

    def observed_result(writer):
        nonlocal result_called
        result_called = True
        return original_result(writer)

    monkeypatch.setattr(_builtins._StringifyWriter, "result", observed_result)

    with pytest.raises(
        GenoRuntimeError,
        match=r"to_string: String size exceeds limit \(5 > 4\)",
    ):
        _call_installed(interp, "to_string", [[1, 2]])

    assert not result_called


def test_internal_fstring_stringify_uses_interpreter_local_limit():
    source = """
    func main() -> String
        return f"{[1, 2]}"
    end func
    """
    wide = _interp_with_limit(10)
    tight = _interp_with_limit(4)

    assert wide.run(parse(source)) == "[1, 2]"
    with pytest.raises(
        GenoRuntimeError,
        match=r"to_string: String size exceeds limit \(5 > 4\)",
    ):
        tight.run(parse(source))


def test_concat_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(100)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_concat([0] * 60, [0] * 60)


def test_append_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(5)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_append([0] * 5, 0)


def test_range_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(100)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_range(0, 101)


def test_array_new_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(100)
    with pytest.raises(GenoRuntimeError, match="Array size exceeds limit"):
        _builtins.builtin_array_new(200, 0)


def test_array_from_list_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(100)
    with pytest.raises(GenoRuntimeError, match="Array size exceeds limit"):
        _builtins.builtin_array_from_list([0] * 200)


def test_set_from_list_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(100)
    with pytest.raises(GenoRuntimeError, match="Set size exceeds limit"):
        _builtins.builtin_set_from_list(list(range(200)))


def test_set_union_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(100)
    s1 = _builtins.builtin_set_from_list(list(range(80)))
    s2 = _builtins.builtin_set_from_list(list(range(80, 160)))
    with pytest.raises(GenoRuntimeError, match="Set size exceeds limit"):
        _builtins.builtin_set_union(s1, s2)


def test_repeat_string_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(100)
    with pytest.raises(GenoRuntimeError, match="collection size limit"):
        _builtins.builtin_repeat_string("ab", 200)


def test_string_repeat_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(100)
    with pytest.raises(GenoRuntimeError, match="collection size limit"):
        _builtins.builtin_string_repeat("ab", 200)


def test_split_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(4)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_split("a,a,a,a,a", ",")


def test_string_split_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(4)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_string_split("a,a,a,a,a", ",")


def test_join_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(5)
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_join(["abc", "def"], "")


def test_string_join_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(5)
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_string_join(["abc", "def"], "")


def test_format_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(5)
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_format("{}{}", ["abc", "def"])


def test_replace_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(5)
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_replace("aaaa", "a", "bb")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_string_replace("aaaa", "a", "bb")


def test_regex_helpers_honor_configured_limit():
    _builtins.set_max_collection_size(2)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_regex_find_all("a", "aaaa")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_regex_replace("a", "bb", "aaaa")


def test_env_helpers_honor_configured_limit(monkeypatch):
    _builtins.set_max_collection_size(2)
    monkeypatch.setenv("GENO_BIG_ENV", "abcd")

    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_env_get("GENO_BIG_ENV")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_env_get_or("GENO_BIG_ENV", "")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_env_get_or("GENO_MISSING_ENV", "abcd")

    monkeypatch.setenv("GENO_CLI_ARGS", '["a","b","c"]')
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_cli_args()

    monkeypatch.setenv("GENO_CLI_ARGS", '["abcd"]')
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_cli_args()


def test_string_pad_pre_checks_honor_configured_limit():
    _builtins.set_max_collection_size(5)
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_string_pad_left("x", 6, "0")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_string_pad_right("x", 6, "0")


def test_list_flatten_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(5)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_list_flatten([[1, 2, 3], [4, 5, 6]])


def test_list_intersperse_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(6)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_list_intersperse([1, 2, 3, 4], 0)


def test_flat_map_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(5)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_flat_map([1, 2], lambda x: [x, x, x])


def test_json_parse_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(2)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_json_parse("[1,2,3]")
    with pytest.raises(GenoRuntimeError, match="Map size exceeds limit"):
        _builtins.builtin_json_parse('{"a":1,"b":2,"c":3}')


def test_json_stringify_pre_check_honors_configured_limit():
    from geno.values import ConstructorValue

    _builtins.set_max_collection_size(5)
    json_array = ConstructorValue(
        "JsonArray",
        {"items": [ConstructorValue("JsonString", {"value": "abc"})]},
    )
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_json_stringify(json_array)
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_json_stringify_pretty(json_array, 2)
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _builtins.builtin_json_to_string(["abc"])


def test_csv_parse_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(2)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_csv_parse("a\nb\nc")
    with pytest.raises(GenoRuntimeError, match="Map size exceeds limit"):
        _builtins.builtin_csv_parse_with_headers("a,b,c\n1,2,3")


def test_toml_parse_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(2)
    with pytest.raises(GenoRuntimeError, match="Map size exceeds limit"):
        _builtins.builtin_toml_parse("a = 1\nb = 2\nc = 3")


def test_map_from_list_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(2)
    with pytest.raises(GenoRuntimeError, match="Map size exceeds limit"):
        _builtins.builtin_map_from_list([("a", 1), ("b", 2), ("c", 3)])


def test_map_insert_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(2)
    with pytest.raises(GenoRuntimeError, match="Map size exceeds limit"):
        _builtins.builtin_map_insert({"a": 1, "b": 2}, "c", 3)


def test_map_merge_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(2)
    with pytest.raises(GenoRuntimeError, match="Map size exceeds limit"):
        _builtins.builtin_map_merge({"a": 1, "b": 2}, {"c": 3})


def test_map_entries_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(2)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _builtins.builtin_map_entries({"a": 1, "b": 2, "c": 3})


def test_map_from_entries_pre_check_honors_configured_limit():
    _builtins.set_max_collection_size(2)
    with pytest.raises(GenoRuntimeError, match="Map size exceeds limit"):
        _builtins.builtin_map_from_entries([("a", 1), ("b", 2), ("c", 3)])


def test_interpreter_construction_preserves_explicit_direct_builtin_cap():
    """Interpreter-local limits never rewrite the legacy direct-call cap."""
    _builtins.set_max_collection_size(50)
    Interpreter(sandbox_config=SandboxConfig(max_collection_size=50))
    assert _builtins.get_max_collection_size() == 50
    Interpreter(sandbox_config=SandboxConfig())
    assert _builtins.get_max_collection_size() == 50


def test_set_max_collection_size_rejects_negative():
    with pytest.raises(GenoRuntimeError):
        _builtins.set_max_collection_size(-1)


# ---------------------------------------------------------------------------
# Nested-container recursion (#661 / F-0026)
#
# Prior to the fix, the interpreter's post-call size check only inspected the
# top-level builtin result / argument length. A builtin that returned a
# one-element outer list containing a huge inner list, or a ConstructorValue
# wrapping a huge inner list, slipped through the sandbox limit. Test
# ``_check_collection_limits`` directly at the helper level.
# ---------------------------------------------------------------------------


def _interp_with_limit(limit: int) -> Interpreter:
    return Interpreter(sandbox_config=SandboxConfig(max_collection_size=limit))


def _installed_callback(limit: int, installer, name: str):
    interp = _interp_with_limit(limit)
    installer(interp)
    return interp.global_env.bindings[name].func


def _run_source_with_limit(source: str, limit: int):
    return Interpreter(
        check_examples=False,
        sandbox_config=SandboxConfig(max_collection_size=limit),
    ).run(parse(source))


def test_string_literal_result_honors_collection_limit():
    source = """
    func main() -> String
        return "abcd"
    end func
    """
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _run_source_with_limit(source, 2)


def test_list_literal_result_honors_collection_limit():
    source = """
    func main() -> List[Int]
        return [1, 2, 3]
    end func
    """
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _run_source_with_limit(source, 2)


def test_tuple_literal_result_honors_collection_limit():
    source = """
    func main() -> (Int, Int, Int)
        return (1, 2, 3)
    end func
    """
    with pytest.raises(GenoRuntimeError, match="Tuple size exceeds limit"):
        _run_source_with_limit(source, 2)


def test_map_merge_result_honors_collection_limit():
    source = """
    func main() -> Map[String, Int]
        let left: Map[String, Int] = map_from_list([("a", 1), ("b", 2)])
        let right: Map[String, Int] = map_from_list([("c", 3), ("d", 4)])
        return map_merge(left, right)
    end func
    """
    with pytest.raises(GenoRuntimeError, match="Map size exceeds limit"):
        _run_source_with_limit(source, 2)


def test_interpreter_string_pad_precheck_honors_collection_limit():
    source = """
    func main() -> String
        return string_pad_left(text: "x", width: 6, fill_char: "0")
    end func
    """
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        _run_source_with_limit(source, 5)


def test_interpreter_list_flatten_precheck_honors_collection_limit():
    source = """
    func main() -> List[Int]
        let nested: List[List[Int]] = [[1, 2, 3], [4, 5, 6]]
        return list_flatten(nested)
    end func
    """
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _run_source_with_limit(source, 5)


def test_interpreter_flat_map_precheck_honors_collection_limit():
    source = """
    func trip(x: Int) -> List[Int]
        example 1 -> [1, 1, 1]
        return [x, x, x]
    end func

    func main() -> List[Int]
        return flat_map([1, 2], trip)
    end func
    """
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _run_source_with_limit(source, 5)


def test_check_collection_limits_rejects_nested_list_over_limit():
    # Outer list has length 1, inner list has length 10, limit is 5.
    # Previously the shallow check accepted this because the outer length
    # fit within the limit.
    interp = _interp_with_limit(5)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        interp._check_collection_limits([[[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]]], None)


def test_check_collection_limits_accepts_nested_list_under_limit():
    interp = _interp_with_limit(5)
    # Must not raise.
    interp._check_collection_limits([[[1, 2, 3]]], None)


def test_check_collection_limits_walks_constructor_fields():
    from geno.values import ConstructorValue

    interp = _interp_with_limit(5)
    wrapped = ConstructorValue("Bag", {"items": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]})
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        interp._check_collection_limits([wrapped], None)


def test_check_collection_limits_walks_tuple_elements():
    interp = _interp_with_limit(5)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        interp._check_collection_limits([(1, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])], None)


def test_check_collection_limits_rejects_tuple_over_limit():
    interp = _interp_with_limit(5)
    with pytest.raises(GenoRuntimeError, match="Tuple size exceeds limit"):
        interp._check_collection_limits([tuple(range(10))], None)


def test_check_collection_limits_rejects_dict_over_limit():
    interp = _interp_with_limit(5)
    with pytest.raises(GenoRuntimeError, match="Map size exceeds limit"):
        interp._check_collection_limits([{i: i for i in range(10)}], None)


def test_check_collection_limits_walks_dict_values():
    interp = _interp_with_limit(5)
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        interp._check_collection_limits([{"big": list(range(10))}], None)


def test_check_collection_limits_walks_vec_elements():
    from geno.values import VecValue

    interp = _interp_with_limit(5)
    outer = VecValue()
    outer._elements.append([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        interp._check_collection_limits([outer], None)


def test_check_collection_limits_walks_map_values():
    from geno.values import MutableMapValue

    interp = _interp_with_limit(5)
    m = MutableMapValue()
    m._data["big"] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        interp._check_collection_limits([m], None)


def test_check_collection_limits_handles_cyclic_structures():
    """Cyclic references must not loop forever — the visited-id set guards
    recursion."""
    interp = _interp_with_limit(5)
    a: list = []
    a.append(a)
    # Self-referential list, size 1 — within the limit, must terminate.
    interp._check_collection_limits([a], None)


def test_check_collection_limits_exact_runtime_kinds_enforce_nested_limits():
    interp = Interpreter(
        check_examples=False,
        sandbox_config=SandboxConfig(
            max_collection_size=2,
            max_integer_bits=3,
        ),
    )

    array = ArrayValue([0, 1, 2])
    vec = VecValue()
    vec._elements.extend([0, 1, 2])
    set_value = SetValue()
    set_value._data.update({0, 1, 2})
    mutable_map = MutableMapValue()
    mutable_map._data.update({0: 0, 1: 1, 2: 2})
    cases = [
        ([0, 1, 2], "List"),
        ((0, 1, 2), "Tuple"),
        ({0: 0, 1: 1, 2: 2}, "Map"),
        ("abc", "String"),
        (array, "Array"),
        (vec, "Vec"),
        (set_value, "Set"),
        (mutable_map, "MutableMap"),
        (ConstructorValue("Wrap", {"value": [0, 1, 2]}), "List"),
        (8, "Integer"),
    ]

    for value, kind in cases:
        with pytest.raises(
            GenoRuntimeError,
            match=rf"{kind} .*exceeds (?:limit|maximum size)",
        ):
            # Nest scalars so they exercise the full DFS instead of the
            # scalar-only root fast path.
            interp._check_collection_limits([[value]], None)

    for value in (True, 1.5, None, 7, "ab"):
        interp._check_collection_limits([[[value]]], None)


def test_check_collection_limits_runtime_subclasses_keep_fallback_semantics():
    class ListSubclass(list):
        pass

    class TupleSubclass(tuple):
        pass

    class DictSubclass(dict):
        pass

    class StringSubclass(str):
        pass

    class IntSubclass(int):
        pass

    class ArraySubclass(ArrayValue):
        pass

    class VecSubclass(VecValue):
        pass

    class SetSubclass(SetValue):
        pass

    class MutableMapSubclass(MutableMapValue):
        pass

    class ConstructorSubclass(ConstructorValue):
        pass

    interp = Interpreter(
        check_examples=False,
        sandbox_config=SandboxConfig(
            max_collection_size=2,
            max_integer_bits=3,
        ),
    )
    vec = VecSubclass()
    vec._elements.extend([0, 1, 2])
    set_value = SetSubclass()
    set_value._data.update({0, 1, 2})
    mutable_map = MutableMapSubclass()
    mutable_map._data.update({0: 0, 1: 1, 2: 2})
    cases = [
        (ListSubclass([0, 1, 2]), "List"),
        (TupleSubclass((0, 1, 2)), "Tuple"),
        (DictSubclass({0: 0, 1: 1, 2: 2}), "Map"),
        (StringSubclass("abc"), "String"),
        (ArraySubclass([0, 1, 2]), "Array"),
        (vec, "Vec"),
        (set_value, "Set"),
        (mutable_map, "MutableMap"),
        (ConstructorSubclass("Wrap", {"value": [0, 1, 2]}), "List"),
        (IntSubclass(8), "Integer"),
    ]

    for value, kind in cases:
        with pytest.raises(
            GenoRuntimeError,
            match=rf"{kind} .*exceeds (?:limit|maximum size)",
        ):
            interp._check_collection_limits([[value]], None)


def test_check_collection_limits_exact_dispatch_preserves_dfs_child_order(
    monkeypatch,
):
    interp = _interp_with_limit(10)
    events = []
    original = interp._check_collection_size

    def observe(kind, size, location):
        events.append((kind, size))
        original(kind, size, location)

    monkeypatch.setattr(interp, "_check_collection_size", observe)
    leaf = [1]
    array = ArrayValue([leaf])
    mutable_map = MutableMapValue()
    mutable_map._data["key"] = array

    interp._check_collection_limits([("tail",), mutable_map], None)

    # The stack is LIFO. Map values therefore precede keys, while roots and
    # children are otherwise extended in their original order.
    assert events == [
        ("MutableMap", 1),
        ("Array", 1),
        ("List", 1),
        ("String", 3),
        ("Tuple", 1),
        ("String", 4),
    ]


def test_check_collection_limits_exact_dispatch_preserves_error_precedence():
    interp = _interp_with_limit(2)

    # Dict keys are pushed before values, so the value is visited first by
    # the LIFO walker. Preserve that ordering when both descendants violate.
    value = {"oversized-key": [0, 1, 2]}
    with pytest.raises(
        GenoRuntimeError,
        match=r"List size exceeds limit \(3 > 2\)",
    ):
        interp._check_collection_limits([value], None)

    # Later roots are also visited first.
    with pytest.raises(
        GenoRuntimeError,
        match=r"Tuple size exceeds limit \(3 > 2\)",
    ):
        interp._check_collection_limits([[0, 1, 2], (0, 1, 2)], None)


def test_check_collection_limits_handles_wide_deep_shared_and_cyclic_graphs():
    interp = _interp_with_limit(2_500)
    shared = [1]
    cycle: list[object] = []
    cycle.append(cycle)
    deep: object = shared
    for _ in range(2_000):
        deep = [deep]
    wide = list(range(2_000))
    graph = ConstructorValue(
        "Graph",
        {
            "deep": deep,
            "wide": wide,
            "shared": [shared, shared],
            "cycle": cycle,
        },
    )

    interp._check_collection_limits([graph], None)

    assert cycle[0] is cycle
    assert graph.fields["shared"][0] is graph.fields["shared"][1]
    assert len(wide) == 2_000


def test_exact_dispatch_keeps_callback_checks_around_side_effects():
    interp = _interp_with_limit(2)
    observed: list[str] = []

    def capture_nested(value):
        observed.append("called")
        return value

    capture = BuiltinFunction(
        "capture_nested",
        capture_nested,
        1,
        ["value"],
    )
    oversized = ConstructorValue("Wrap", {"value": [0, 1, 2]})

    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        interp._call_function(capture, [oversized])
    assert observed == []

    def return_nested():
        observed.append("called")
        return oversized

    result = BuiltinFunction(
        "return_nested",
        return_nested,
        0,
        [],
    )
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        interp._call_function(result, [])
    assert observed == ["called"]


def test_exact_dispatch_keeps_incremental_mutation_target_exclusion():
    interp = _interp_with_limit(2)
    vec = VecValue()
    vec._elements.extend([0, 1, 2])

    # The already-large target is intentionally excluded for non-growing
    # incremental mutations; their new value is still checked before mutation.
    _call_installed(interp, "vec_set", [vec, 0, 9])
    assert vec._elements == [9, 1, 2]

    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        _call_installed(interp, "vec_set", [vec, 0, [0, 1, 2]])
    assert vec._elements == [9, 1, 2]


def test_exact_dispatch_isolated_across_concurrent_and_reentrant_interpreters():
    tight = _interp_with_limit(2)
    wide = _interp_with_limit(3)
    graph = ConstructorValue("Wrap", {"value": [0, 1, 2]})

    def check(interp, accepted):
        for _ in range(50):
            if accepted:
                interp._check_collection_limits([graph], None)
            else:
                with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
                    interp._check_collection_limits([graph], None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(check, tight, False),
            executor.submit(check, wide, True),
        ]
        for future in futures:
            future.result(timeout=10)

    def check_tight_then_wide(value):
        with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
            tight._check_collection_limits([value], None)
        wide._check_collection_limits([value], None)
        return 1

    callback = BuiltinFunction(
        "check_tight_then_wide",
        check_tight_then_wide,
        1,
        ["value"],
    )
    assert wide._call_function(callback, [graph]) == 1


def test_installed_fs_callbacks_honor_configured_limit(tmp_path):
    from geno._serve import install_fs_callbacks

    def _install_fs(interp):
        install_fs_callbacks(interp, roots=[tmp_path], allow_absolute_paths=True)

    read_text = _installed_callback(2, _install_fs, "fs_read_text")
    big_file = tmp_path / "big.txt"
    big_file.write_text("abcd")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        read_text(str(big_file))

    list_dir = _installed_callback(2, _install_fs, "fs_list_dir")
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text("x")
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        list_dir(str(tmp_path))


def test_builtin_arguments_are_checked_before_side_effects():
    observed = []
    interp = _interp_with_limit(2)
    capture = BuiltinFunction(
        "capture",
        lambda value: observed.append(value),
        1,
        ["value"],
    )

    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        interp._call_function(capture, ["abcd"])

    assert observed == []


def test_fs_write_text_checks_args_before_writing(monkeypatch, tmp_path):
    from geno._serve import install_fs_callbacks

    monkeypatch.chdir(tmp_path)
    interp = _interp_with_limit(2)
    install_fs_callbacks(interp)

    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        interp._call_function(
            interp.global_env.bindings["fs_write_text"], ["x", "abcd"]
        )

    assert not (tmp_path / "x").exists()


def test_installed_http_callbacks_honor_configured_limit(monkeypatch):
    from geno._serve import install_http_callbacks

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size=-1):
            return b"abcd"

        def getheaders(self):
            return [("a", "1"), ("b", "2"), ("c", "3")]

    class _Opener:
        def open(self, req, timeout):
            return _Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: _Opener())

    def _install_http(interp):
        install_http_callbacks(interp, allow_private_networks=True)

    http_fetch = _installed_callback(2, _install_http, "http_fetch")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        http_fetch("http://example.test")

    http_post = _installed_callback(2, _install_http, "http_post")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        http_post("http://example.test", "")

    http_request = _installed_callback(2, _install_http, "http_request")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        http_request("GET", "http://example.test", [], "")


def test_installed_process_callbacks_honor_configured_limit():
    from geno._serve import install_process_callbacks

    spawn = _installed_callback(2, install_process_callbacks, "spawn")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        spawn(sys.executable, ["-V"])


def test_installed_stdin_callback_honors_configured_limit(monkeypatch):
    from geno._serve import install_stdin_callbacks

    monkeypatch.setattr(sys, "stdin", io.StringIO("abcd"))
    stdin_read_all = _installed_callback(2, install_stdin_callbacks, "stdin_read_all")
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        stdin_read_all()


def test_installed_serve_route_registry_honors_configured_limit():
    from geno._serve import install_serve_callbacks

    http_route = _installed_callback(2, install_serve_callbacks, "http_route")
    handler = object()
    http_route("GET", "/a", handler)
    http_route("GET", "/b", handler)

    with pytest.raises(GenoRuntimeError, match="Route registry size exceeds limit"):
        http_route("GET", "/c", handler)


def test_vec_push_limit_error_does_not_mutate_when_caught():
    source = """
    func main() -> Int
        let v: Vec[Int] = vec_new()
        vec_push(v, 1)
        vec_push(v, 2)
        vec_push(v, 3)
        vec_push(v, 4)
        vec_push(v, 5)
        try
            vec_push(v, 6)
        catch err: String
        end try
        return vec_length(v)
    end func
    """
    assert _run_source_with_limit(source, 5) == 5


def test_mutable_map_set_limit_error_does_not_mutate_when_caught():
    source = """
    func main() -> Int
        let m: MutableMap[String, Int] = mutable_map_new()
        mutable_map_set(m, "a", 1)
        mutable_map_set(m, "b", 2)
        mutable_map_set(m, "c", 3)
        mutable_map_set(m, "d", 4)
        mutable_map_set(m, "e", 5)
        try
            mutable_map_set(m, "f", 6)
        catch err: String
        end try
        return mutable_map_size(m)
    end func
    """
    assert _run_source_with_limit(source, 5) == 5


def test_set_add_limit_error_does_not_mutate_when_caught():
    source = """
    func main() -> Int
        let s: Set[Int] = set_new()
        set_add(s, 1)
        set_add(s, 2)
        set_add(s, 3)
        set_add(s, 4)
        set_add(s, 5)
        try
            set_add(s, 6)
        catch err: String
        end try
        return set_size(s)
    end func
    """
    assert _run_source_with_limit(source, 5) == 5


def test_mutable_map_index_assign_honors_limit_before_mutating():
    source = """
    func main() -> Int
        var m: MutableMap[String, Int] = mutable_map_new()
        mutable_map_set(m, "a", 1)
        mutable_map_set(m, "b", 2)
        mutable_map_set(m, "c", 3)
        mutable_map_set(m, "d", 4)
        mutable_map_set(m, "e", 5)
        try
            m["f"] = 6
        catch err: String
        end try
        return mutable_map_size(m)
    end func
    """
    assert _run_source_with_limit(source, 5) == 5


def test_closure_arguments_honor_configured_limit():
    source = """
    func accepts(x: String) -> Int
        return 1
    end func
    """
    interp = Interpreter(
        check_examples=False,
        sandbox_config=SandboxConfig(max_collection_size=2),
    )
    interp.run(parse(source), execute_main=False)
    accepts = interp.global_env.bindings["accepts"]

    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        interp._call_function(accepts, ["abcd"])


def test_closure_argument_check_walks_constructor_fields():
    from geno.values import ConstructorValue

    source = """
    func handler(req: HttpRequest) -> Int
        return 1
    end func
    """
    interp = Interpreter(
        check_examples=False,
        sandbox_config=SandboxConfig(max_collection_size=2),
    )
    interp.run(parse(source), execute_main=False)
    handler = interp.global_env.bindings["handler"]
    request = ConstructorValue(
        "HttpRequest",
        {
            "method": "GET",
            "path": "/",
            "query": "",
            "headers": [],
            "body": "abcd",
        },
    )

    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        interp._call_function(handler, [request])
