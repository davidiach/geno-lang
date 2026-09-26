"""Independent semantic oracles for the deep review's backend regressions."""

import pytest

from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_js_project_output,
    _compiled_python_output,
    _compiled_python_project_output,
    _interpreter_output,
    _interpreter_project_output,
)

BACKENDS = [
    _interpreter_output,
    _compiled_python_output,
    pytest.param(
        _compiled_js_output,
        marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
    ),
]

PROJECT_BACKENDS = [
    _interpreter_project_output,
    _compiled_python_project_output,
    pytest.param(
        _compiled_js_project_output,
        marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
    ),
]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "transfer,mutation",
    [
        ("var b = Point(2)\n    b = a", "b.x = 3"),
        ("var (b, n): (Point, Int) = (a, 2)", "b.x = 3"),
        ("var b = [Point(2)]\n    b = [a]", "b[0].x = 3"),
        ("var (b, n): (List[Point], Int) = ([a], 2)", "b[0].x = 3"),
        ("var b = a", "b.x = 3"),
        ("var b = Box(Point(2))\n    b.p = a", "b.p.x = 3"),
        ("var b = array_from_list([Point(2)])\n    b[0] = a", "b[0].x = 3"),
    ],
    ids=[
        "assignment",
        "destructure",
        "nested-assignment",
        "nested-destructure",
        "binding",
        "field-assignment",
        "index-assignment",
    ],
)
def test_record_transfers_preserve_value_snapshots(backend, transfer, mutation):
    source = f"""
type Point = Point(x: Int)
type Box = Box(p: Point)
func main() -> Unit
    let a = Point(1)
    {transfer}
    {mutation}
    print(a.x)
    return ()
end func
"""
    assert backend(source) == "1\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_reference_collections_remain_shared_on_transfer(backend):
    source = """
func main() -> Unit
    let a = array_from_list([1])
    var b = array_from_list([2])
    b = a
    b[0] = 3
    var (c, n): (Array[Int], Int) = (a, 0)
    c[0] = 4
    print(a[0])
    let v = vec_from_list([1])
    var w = vec_from_list([2])
    w = v
    w[0] = 5
    var (z, m): (Vec[Int], Int) = (v, 0)
    z[0] = 6
    print(vec_get(v, 0))
    let s: Set[Int] = set_new()
    var t: Set[Int] = set_new()
    t = s
    set_add(t, 7)
    let (u, q): (Set[Int], Int) = (s, 0)
    set_add(u, 8)
    print(set_contains(s, 8))
    let mm: MutableMap[String, Int] = mutable_map_new()
    var nn: MutableMap[String, Int] = mutable_map_new()
    nn = mm
    mutable_map_set(map: nn, key: "a", value: 9)
    let (oo, r): (MutableMap[String, Int], Int) = (mm, 0)
    mutable_map_set(map: oo, key: "a", value: 10)
    print(mutable_map_get(mm, "a"))
    return ()
end func
"""
    assert backend(source) == "4\n6\ntrue\nSome(value: 10)\n"


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "setup,observe",
    [
        ("let values = array_new(2, a)", "print(values[0].x)"),
        ("let values = array_from_list([a])", "print(values[0].x)"),
        (
            "let values = array_from_list([Point(0)])\n"
            "    array_set(array: values, index: 0, value: a)",
            "print(values[0].x)",
        ),
        (
            "let values = array_from_list([Point(0)])\n    array_fill(values, a)",
            "print(values[0].x)",
        ),
        ("let values = vec_from_list([a])", "print(vec_get(values, 0).x)"),
        (
            "let values = vec_from_list([Point(0)])\n"
            "    vec_set(vec: values, index: 0, value: a)",
            "print(vec_get(values, 0).x)",
        ),
        (
            "let values: Vec[Point] = vec_new()\n    vec_push(values, a)",
            "print(vec_get(values, 0).x)",
        ),
        (
            "let values: MutableMap[Int, Point] = mutable_map_new()\n"
            "    mutable_map_set(map: values, key: 0, value: a)",
            "match mutable_map_get(values, 0) with\n"
            "        | Some(p) -> print(p.x)\n"
            "        | None -> print(0)\n"
            "    end match",
        ),
        (
            "let values: Set[Point] = set_new()\n    set_add(values, a)",
            "if set_contains(values, Point(1)) then\n"
            "        print(1)\n    else\n        print(0)\n    end if",
        ),
        (
            "let values = set_from_list([a])",
            "if set_contains(values, Point(1)) then\n"
            "        print(1)\n    else\n        print(0)\n    end if",
        ),
    ],
    ids=[
        "array-new",
        "array-from-list",
        "array-set",
        "array-fill",
        "vec-from-list",
        "vec-set",
        "vec-push",
        "mutable-map-set",
        "set-add",
        "set-from-list",
    ],
)
def test_builtin_storage_snapshots_record_values(backend, setup, observe):
    source = f"""
type Point = Point(x: Int)
func main() -> Unit
    var a = Point(1)
    {setup}
    a.x = 9
    {observe}
    return ()
end func
"""
    assert backend(source) == "1\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_array_fill_copies_each_record_but_preserves_reference_elements(backend):
    source = """
type Point = Point(x: Int)
type Box = Box(items: Array[Int])
func main() -> Unit
    let a = Point(1)
    var values = array_new(2, a)
    values[0].x = 2
    print(values[1].x)
    array_fill(values, a)
    values[0].x = 3
    print(values[1].x)
    var copied = array_copy(values)
    copied[0].x = 4
    print(values[0].x)
    var inner = array_from_list([1])
    let refs = array_new(2, inner)
    var box = Box(array_from_list([2]))
    box.items = inner
    let vec: Vec[Array[Int]] = vec_new()
    vec_push(vec, inner)
    inner[0] = 8
    print(refs[0][0])
    print(refs[1][0])
    print(box.items[0])
    print(vec_get(vec, 0)[0])
    return ()
end func
"""
    assert backend(source) == "1\n1\n3\n8\n8\n8\n8\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_map_keys_are_value_snapshots(backend):
    source = """
type Point = Point(x: Int)
func main() -> Unit
    var key = Point(1)
    let immutable = map_from_entries([(key, 5)])
    let mutable: MutableMap[Point, Int] = mutable_map_new()
    mutable_map_set(map: mutable, key: key, value: 6)
    var indexed: MutableMap[Point, Int] = mutable_map_new()
    indexed[key] = 7
    key.x = 9
    print(map_get(immutable, Point(1)))
    print(mutable_map_get(mutable, Point(1)))
    print(mutable_map_get(indexed, Point(1)))
    return ()
end func
"""
    assert backend(source) == "Some(value: 5)\nSome(value: 6)\nSome(value: 7)\n"


SHADOW_SOURCE = """
let count = 7
func read(count: Int = count) -> Int
    requires count > 0
    ensures result == count
    example 7 -> 7
    return count
end func
func main() -> Unit
    print(count)
    let read_outer = fn() -> count
    print(read_outer())
    let count = count + 1
    print(count)
    print(read_outer())
    print(read())
    let nested = fn() do
        print(count)
        let count = count + 1
        return count
    end fn
    print(nested())
    return ()
end func
"""


@pytest.mark.parametrize("backend", BACKENDS)
def test_module_constant_shadows_keep_lexical_scope(backend):
    assert backend(SHADOW_SOURCE) == "7\n7\n8\n8\n7\n8\n9\n"


@pytest.mark.parametrize("backend", PROJECT_BACKENDS)
def test_module_constant_scope_in_project_output(backend):
    assert backend(SHADOW_SOURCE, {}) == "7\n7\n8\n8\n7\n8\n9\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_lambda_parameter_shadows_constant(backend):
    assert (
        backend("""
let count = 7
func main() -> Unit
    print(map([1, 2], fn(count: Int) -> count + 1))
    return ()
end func
""")
        == "[2, 3]\n"
    )


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("block", [False, True])
@pytest.mark.parametrize(
    "parameter,body,argument,expected",
    [
        ("Option[Int]", "Some(x?)", "None", "None"),
        ("Result[Int, String]", "Ok(x?)", 'Err("bad")', 'Err(error: "bad")'),
    ],
)
def test_propagation_returns_from_its_lambda(
    backend, block, parameter, body, argument, expected
):
    lambda_body = f"do\n        return {body}\n    end fn" if block else f"-> {body}"
    source = f"""
func main() -> Unit
    let f = fn(x: {parameter}) {lambda_body}
    print(f({argument}))
    print("after")
    return ()
end func
"""
    assert backend(source) == f"{expected}\nafter\n"


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "binding", ["let x = x + 1", "let x: Int = x + 1", "var x = x + 1"]
)
def test_postconditions_observe_rebound_parameter(backend, binding):
    source = f"""
func bump(x: Int) -> Int
    ensures result > 0
    ensures result == x
    example 7 -> 8
    {binding}
    return x
end func
func main() -> Unit
    print(bump(7))
    return ()
end func
"""
    assert backend(source) == "8\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_stringifier_specializations_preserve_callback_identity(backend):
    source = """
func main() -> Unit
    let to_string = fn(x: Int) -> "custom"
    print(map([1, 2], to_string))
    print(list_map([3], to_string))
    print(list_group_by([4, 5], to_string))
    print(option_map(Some(6), to_string))
    let ok: Result[Int, Int] = Ok(7)
    print(result_map(ok, to_string))
    let err: Result[Int, Int] = Err(8)
    print(result_map_err(err, to_string))
    print(map_map_values(map_from_entries([("a", 9)]), to_string))
    return ()
end func
"""
    assert backend(source) == (
        '["custom", "custom"]\n["custom"]\n'
        '[("custom", [4, 5])]\nSome(value: "custom")\n'
        'Ok(value: "custom")\nErr(error: "custom")\n{"a": "custom"}\n'
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_shadowed_stringifier_callback_is_actually_evaluated(backend):
    source = """
func main() -> Unit
    let to_string = fn(x: Int) do
        let quotient = 1 / x
        return "custom"
    end fn
    print(map([0], to_string))
    return ()
end func
"""
    with pytest.raises((AssertionError, RuntimeError), match="zero"):
        backend(source)


@pytest.mark.parametrize("backend", BACKENDS)
def test_builtin_fast_paths_respect_function_parameters(backend):
    source = """
func apply(to_string: (Int) -> String, length: (List[Int]) -> Int) -> Unit
    example (fn(x: Int) -> "custom", fn(xs: List[Int]) -> 42) -> ()
    print(map([1], to_string))
    print(length([1, 2]))
    return ()
end func
func main() -> Unit
    apply(fn(x: Int) -> "custom", fn(xs: List[Int]) -> 42)
    return ()
end func
"""
    assert backend(source) == '["custom"]\n42\n'


@pytest.mark.parametrize("backend", BACKENDS)
def test_nan_remains_unordered_for_every_relational_operator(backend):
    checks = [
        f"    print({left} {operator} {right})"
        for left, right in [("nan", "0.0"), ("0.0", "nan"), ("nan", "nan")]
        for operator in ["<", "<=", ">", ">="]
    ]
    source = (
        """
func main() -> Unit
    let large = 10.0 ** 200.0
    let infinity = large * large
    let nan = infinity - infinity
"""
        + "\n".join(checks)
        + """
    print(1.0 <= 1.0)
    print(1.0 < 2.0)
    return ()
end func
"""
    )
    assert backend(source) == "false\n" * 12 + "true\ntrue\n"
