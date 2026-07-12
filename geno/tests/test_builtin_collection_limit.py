"""
Regression tests for MED-01: SandboxConfig.max_collection_size must be
respected by builtin pre-checks, not just by the interpreter's post-call
size check.

Prior to the fix the following builtins pre-checked against a hardcoded
10_000_000 ceiling instead of the configured limit, so a tightened sandbox
allowed pre-allocation up to 10 M elements before the post-check could
reject the result.
"""

import io
import sys

import pytest

from geno import builtins as _builtins
from geno.interpreter import Interpreter
from geno.parser import parse
from geno.sandbox import SandboxConfig
from geno.values import BuiltinFunction
from geno.values import RuntimeError as GenoRuntimeError


@pytest.fixture(autouse=True)
def _restore_cap():
    """Save/restore the module-level cap around each test."""
    saved = _builtins.get_max_collection_size()
    yield
    _builtins.set_max_collection_size(saved)


def test_interpreter_init_propagates_max_collection_size():
    Interpreter(sandbox_config=SandboxConfig(max_collection_size=42))
    assert _builtins.get_max_collection_size() == 42


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


def test_default_cap_restored_after_interpreter_configured_down():
    """Creating a default-config Interpreter after a tightened one
    restores the default cap so tests that share the module do not
    observe stale tightened limits."""
    Interpreter(sandbox_config=SandboxConfig(max_collection_size=50))
    assert _builtins.get_max_collection_size() == 50
    Interpreter(sandbox_config=SandboxConfig())
    assert _builtins.get_max_collection_size() == SandboxConfig().max_collection_size


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
