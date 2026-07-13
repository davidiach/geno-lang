"""Cross-backend regressions required before Geno's first public release."""

from __future__ import annotations

from pathlib import Path

import pytest

from geno.interpreter import Interpreter
from geno.sandbox import SandboxConfig
from geno.test_runner import run_test_suite
from geno.values import RuntimeError as GenoRuntimeError
from geno.values import VecValue

from .test_backend_parity import (
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)


def _outputs(source: str) -> tuple[str, str, str]:
    return (
        _interpreter_output(source),
        _compiled_python_output(source),
        _compiled_js_output(source),
    )


def test_mixed_numeric_equality_has_one_semantics_on_every_backend() -> None:
    source = """
func main() -> Unit
    let integer: Int = 2
    let floating: Float = 2.0
    print(floating == integer)
    print([floating] == [integer])
end func
"""

    assert _outputs(source) == ("true\ntrue\n",) * 3


def test_geno_test_uses_the_shipping_numeric_equality_verdict(tmp_path: Path) -> None:
    passing = tmp_path / "passing.geno"
    passing.write_text(
        """
func is_two(x: Float) -> Bool
    example 2.0 -> true
    let integer: Int = 2
    return x == integer
end func
""",
        encoding="utf-8",
    )
    failing = tmp_path / "failing.geno"
    failing.write_text(
        passing.read_text(encoding="utf-8").replace("2.0 -> true", "2.0 -> false"),
        encoding="utf-8",
    )

    accepted = run_test_suite([passing])
    rejected = run_test_suite([failing])

    assert (accepted.passed, accepted.failed, accepted.errors) == (1, 0, 0)
    assert (rejected.passed, rejected.failed, rejected.errors) == (0, 1, 0)


def test_float_divide_builtin_preserves_float_semantics() -> None:
    source = """
func main() -> Unit
    print(divide(7.0, 2.0))
    print(divide(7, 2))
end func
"""

    assert _outputs(source) == ("3.5\n3\n",) * 3


def test_map_update_preserves_existing_key_position() -> None:
    source = """
func main() -> Unit
    let original: Map[Int, Int] = map_from_entries([(1, 10), (2, 20), (3, 30)])
    let updated: Map[Int, Int] = map_insert(map: original, key: 1, value: 99)
    let entries: List[(Int, Int)] = map_entries(updated)
    let (key0, value0): (Int, Int) = entries[0]
    let (key1, value1): (Int, Int) = entries[1]
    let (key2, value2): (Int, Int) = entries[2]
    print(key0)
    print(key1)
    print(key2)
end func
"""

    assert _outputs(source) == ("1\n2\n3\n",) * 3


def test_constructor_bindings_have_value_semantics() -> None:
    source = """
type Counter = Counter(count: Int)

func main() -> Unit
    var current: Counter = Counter(1)
    let snapshot = current
    current.count = 5
    print(snapshot.count)
    print(current.count)
end func
"""

    assert _outputs(source) == ("1\n5\n",) * 3


def test_with_expression_result_remains_mutable() -> None:
    source = """
type Counter = Counter(count: Int)

func main() -> Unit
    let initial: Counter = Counter(1)
    var changed: Counter = initial with (count: 2)
    changed.count = 7
    print(changed.count)
end func
"""

    assert _outputs(source) == ("7\n",) * 3


def test_print_uses_bare_top_level_strings() -> None:
    source = """
func main() -> Unit
    print("hello")
end func
"""

    assert _outputs(source) == ("hello\n",) * 3


def test_compiled_python_oob_index_uses_geno_runtime_error() -> None:
    source = """
func main() -> Unit
    try
        let values: List[Int] = []
        print(values[0])
    catch message: String
        print(message)
    end try
end func
"""

    python_output = _compiled_python_output(source)
    assert "Index 0 out of bounds" in python_output
    assert "list index out of range" not in python_output


def test_js_rejects_overlapping_noncapturing_regex() -> None:
    source = r"""
func main() -> Unit with regex, io
    try
        regex_match(pattern: "(?:a|a)*b", text: "aaaaaaaa")
        print("accepted")
    catch message: String
        print(message)
    end try
end func
"""

    output = _compiled_js_output(source, {"print", "regex"})
    assert "overlapping alternation" in output
    assert "accepted" not in output


def test_deep_json_returns_err_in_compiled_js() -> None:
    nested = "[" * 129 + "0" + "]" * 129
    source = f'''
func main() -> Unit
    match json_parse(text: "{nested}") with
        | Ok(_) -> print("accepted")
        | Err(message) -> print(message)
    end match
end func
'''

    output = _compiled_js_output(source)
    assert "nested too deeply" in output
    assert "accepted" not in output


def test_rounding_near_half_is_consistent_across_backends() -> None:
    source = """
func main() -> Unit
    print(round(0.49999999999999994))
    print(round(0.5))
    print(round(0.0 - 0.5))
end func
"""

    assert _outputs(source) == ("0\n1\n0\n",) * 3


def test_structural_is_permutation_matches_across_backends() -> None:
    source = """
func main() -> Unit
    print(is_permutation([Some(1), None, Some(2)], [Some(2), Some(1), None]))
    print(is_permutation([Some(1), None], [Some(1), Some(1)]))
end func
"""

    assert _outputs(source) == ("true\nfalse\n",) * 3


def test_print_composites_use_canonical_double_quoted_strings() -> None:
    source = """
type Box = Box(label: String)

func main() -> Unit
    print(["a", "b"])
    print(Box("hi"))
    print(to_string(["a", "b"]))
end func
"""

    assert _outputs(source) == ('["a", "b"]\nBox(label: "hi")\n["a", "b"]\n',) * 3


def test_incremental_mutators_do_not_rewalk_existing_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interpreter = Interpreter(sandbox_config=SandboxConfig(max_collection_size=10_000))
    target = VecValue()
    target._elements.extend(range(2_000))
    inserted = [[1, 2, 3]]
    checked_roots: list[list[object]] = []
    original_check = interpreter._check_collection_limits

    def record_check(roots, location):
        checked_roots.append(list(roots))
        original_check(roots, location)

    monkeypatch.setattr(interpreter, "_check_collection_limits", record_check)
    interpreter._call_function(
        interpreter.global_env.bindings["vec_push"],
        [target, inserted],
    )

    assert all(target not in roots for roots in checked_roots)
    assert any(inserted in roots for roots in checked_roots)
    assert target._elements[-1] is inserted


def test_incremental_mutators_still_reject_oversized_nested_values() -> None:
    interpreter = Interpreter(sandbox_config=SandboxConfig(max_collection_size=2))
    target = VecValue()
    inserted = [[1, 2, 3]]

    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        interpreter._call_function(
            interpreter.global_env.bindings["vec_push"],
            [target, inserted],
        )

    assert target._elements == []


def test_mixed_numeric_equality_is_symmetric_in_nested_values() -> None:
    source = """
func main() -> Unit
    let integer: Int = 2
    let floating: Float = 2.0
    print(integer == floating)
    print(floating == integer)
    print([integer] == [floating])
    print([floating] == [integer])
end func
"""

    assert _outputs(source) == ("true\ntrue\ntrue\ntrue\n",) * 3


def test_first_class_divide_preserves_annotated_numeric_semantics() -> None:
    source = """
func main() -> Unit
    let float_divide: (Float, Float) -> Float = divide
    let int_divide: (Int, Int) -> Int = divide
    print(float_divide(7.0, 2.0))
    print(int_divide(7, 2))
end func
"""

    assert _outputs(source) == ("3.5\n3\n",) * 3


def test_nested_constructor_bindings_have_value_semantics() -> None:
    source = """
type Counter = Counter(count: Int)

func main() -> Unit
    var current: List[Counter] = [Counter(1)]
    let snapshot = current
    current[0].count = 5
    print(snapshot[0].count)
    print(current[0].count)
end func
"""

    assert _outputs(source) == ("1\n5\n",) * 3


def test_print_map_keys_and_singleton_tuples_are_canonical() -> None:
    source = """
func main() -> Unit
    print(map_from_list([("a", 1)]))
    print((1,))
end func
"""

    assert _outputs(source) == ('{"a": 1}\n(1,)\n',) * 3


def test_js_rejects_equivalent_repeated_regex_alternatives() -> None:
    source = r"""
func main() -> Unit with regex, io
    try
        regex_match(pattern: "(a|[a])*b", text: "aaaaaaaa")
        print("accepted")
    catch message: String
        print(message)
    end try
end func
"""

    output = _compiled_js_output(source, {"print", "regex"})
    assert "overlapping alternation" in output
    assert "accepted" not in output


def test_first_class_divide_uses_context_in_callbacks_and_containers() -> None:
    source = """
type Ops = Ops(op: (Float, Float) -> Float)

func apply(op: (Float, Float) -> Float) -> Float
    example divide -> 3.5
    return op(7.0, 2.0)
end func

func main() -> Unit
    let boxed = Ops(divide)
    let operations: List[(Float, Float) -> Float] = [divide]
    print(apply(divide))
    print(boxed.op(7.0, 2.0))
    print(operations[0](7.0, 2.0))
end func
"""

    assert _outputs(source) == ("3.5\n3.5\n3.5\n",) * 3


def test_js_rejects_nested_optional_quantifier() -> None:
    source = r"""
func main() -> Unit with regex, io
    try
        regex_match(pattern: "(a?){25}a{25}", text: "aaaaaaaaaaaaaaaaaaaaaaaaa")
        print("accepted")
    catch message: String
        print(message)
    end try
end func
"""

    output = _compiled_js_output(source, {"print", "regex"})
    assert "nested quantifiers" in output
    assert "accepted" not in output


def test_first_class_divide_context_covers_updates_matches_and_generics() -> None:
    source = """
type Ops = Ops(op: (Float, Float) -> Float)

func main() -> Unit
    let float_divide: (Float, Float) -> Float = divide

    var boxed: Ops = Ops(float_divide)
    boxed.op = divide
    print(boxed.op(7.0, 2.0))

    let original: Ops = Ops(float_divide)
    let changed = original with (op: divide)
    print(changed.op(7.0, 2.0))

    var operations: Array[(Float, Float) -> Float] = array_from_list([float_divide])
    operations[0] = divide
    print(operations[0](7.0, 2.0))

    var vector: Vec[(Float, Float) -> Float] = vec_from_list([float_divide])
    vector[0] = divide
    let vector_op = vec_get(vector, 0)
    print(vector_op(7.0, 2.0))

    let selected: (Float, Float) -> Float = match true with
        | true -> divide
        | false -> divide
    end match
    print(selected(7.0, 2.0))

    let (tuple_operation, number): ((Float, Float) -> Float, Int) = (divide, 1)
    print(tuple_operation(7.0, 2.0))

    let via_head: (Float, Float) -> Float = head([divide])
    print(via_head(7.0, 2.0))

    let via_comprehension: List[(Float, Float) -> Float] = [
        divide for item: Int in [1]
    ]
    print(via_comprehension[0](7.0, 2.0))

    let optional: Option[(Float, Float) -> Float] = Some(divide)
    match optional with
        | Some(op) -> print(op(7.0, 2.0))
        | None -> print(0.0)
    end match
end func
"""

    assert _outputs(source) == ("3.5\n3.5\n3.5\n3.5\n3.5\n3.5\n3.5\n3.5\n3.5\n",) * 3


@pytest.mark.parametrize(
    "source",
    [
        """
func divide(a: Float, b: Float) -> Float
    example (1.0, 2.0) -> 3.0
    return a + b
end func

func main() -> Unit
    let operation: (Float, Float) -> Float = divide
    print(operation(7.0, 2.0))
end func
""",
        """
func main() -> Unit
    let divide: (Float, Float) -> Float = fn(a: Float, b: Float) -> a + b
    let operation: (Float, Float) -> Float = divide
    print(operation(7.0, 2.0))
end func
""",
    ],
)
def test_user_shadowed_divide_is_not_lowered_as_builtin(source: str) -> None:
    assert _outputs(source) == ("9.0\n",) * 3
