"""Generated-Python runtime graph limit checks."""

import gc
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from geno.compiler import compile_and_exec

RUNTIME_PATH = Path(__file__).resolve().parents[1] / "_runtime_support.py"


def _runtime(*, collection_limit: int = 10_000, integer_limit: int = 1_024):
    env = {
        "_GENO_MAX_COLLECTION_SIZE": collection_limit,
        "_GENO_MAX_INTEGER_BITS": integer_limit,
    }
    exec(RUNTIME_PATH.read_text(), env)
    return env


def _instrumented_runtime(
    *,
    forced_hash: int | None = None,
    hash_function: Callable[[object], int] | None = None,
):
    env = _runtime()
    original_hash = env["_GENO_OBJECT"].__hash__
    hash_calls: list[object] = []

    class HashProbe:
        @staticmethod
        def __hash__(value):  # type: ignore[override]
            hash_calls.append(value)
            if hash_function is not None:
                return hash_function(value)
            if forced_hash is not None:
                return forced_hash
            return original_hash(value)

    env["_GENO_OBJECT"] = HashProbe
    return env, hash_calls


def test_check_collection_size_returns_scalar_inputs_unchanged():
    env = _runtime(collection_limit=8, integer_limit=8)
    check = env["_check_collection_size"]
    marker = object()

    for value in (None, False, True, 0, 127, 1.5, "short", marker):
        assert check(value) is value


def test_check_collection_size_accepts_flat_and_wide_unique_lists():
    env = _runtime(collection_limit=5_000)
    check = env["_check_collection_size"]
    flat = list(range(5_000))
    wide = [[value] for value in range(5_000)]

    assert check(flat) is flat
    assert check(wide) is wide


def test_check_collection_size_walks_deep_graph_without_recursion():
    env = _runtime(collection_limit=1)
    check = env["_check_collection_size"]
    root: list[object] = []
    value: list[object] = root
    for _ in range(2_000):
        child: list[object] = []
        value.append(child)
        value = child

    assert check(root) is root


def test_check_collection_size_handles_cycles_and_shared_aliases():
    env = _runtime(collection_limit=4_000)
    check = env["_check_collection_size"]
    cycle: list[object] = []
    cycle.append(cycle)
    shared = [1, 2, 3]
    aliases = [shared] * 4_000

    assert check(cycle) is cycle
    assert check(aliases) is aliases


def test_collection_checker_is_the_single_production_graph_boundary():
    env = _runtime(collection_limit=8, integer_limit=8)
    check = env["_check_collection_size"]
    assert "_check_collection_graph" not in env

    marker = object()
    for value in (None, False, True, 0, 127, 1.5, "short", marker):
        assert check(value) is value
    for value in ([], (), {}):
        assert check(value) is value


def test_compact_tracker_classes_are_absent_from_the_runtime_prelude():
    env = _runtime()
    assert "_IdentityTracker" not in env
    assert "_IdentityCollisionBucket" not in env


def test_compact_identity_table_starts_power_of_two_below_80_percent_load():
    env = _runtime()
    size = env["_IDENTITY_TABLE_INITIAL_SIZE"]
    promotion_size = env["_IDENTITY_TRACKER_THRESHOLD"] + 1
    assert size & (size - 1) == 0
    assert promotion_size * 5 <= size * 4


@pytest.mark.parametrize("offset", (-1, 0, 1, 2))
def test_compact_identity_table_promotes_only_above_threshold(offset):
    env, hash_calls = _instrumented_runtime()
    threshold = env["_IDENTITY_TRACKER_THRESHOLD"]
    unique_nodes = threshold + offset
    root: list[list[object]] = [[] for _ in range(unique_nodes - 1)]

    assert env["_check_collection_size"](root) is root
    expected_calls = 0 if unique_nodes <= threshold else unique_nodes
    assert len(hash_calls) == expected_calls


@pytest.mark.parametrize(
    ("unique_nodes", "expected_hash_calls"),
    ((51, 51), (52, 103), (53, 104), (102, 153), (103, 256)),
)
def test_compact_identity_table_resizes_before_full(unique_nodes, expected_hash_calls):
    env, hash_calls = _instrumented_runtime()
    root: list[list[object]] = [[] for _ in range(unique_nodes - 1)]

    assert env["_check_collection_size"](root) is root
    assert len(hash_calls) == expected_hash_calls


def test_compact_identity_table_handles_cycles_and_aliases_across_resize():
    env = _runtime()
    check = env["_check_collection_size"]

    small_cycle: list[object] = []
    small_cycle.append(small_cycle)
    shared: list[object] = []
    aliases = [shared] * 128
    assert check(small_cycle) is small_cycle
    assert check(aliases) is aliases

    nodes: list[list[object]] = [[] for _ in range(80)]
    for index in range(len(nodes) - 1):
        nodes[index].append(nodes[index + 1])
    nodes[-1].append(nodes[0])
    assert check(nodes[0]) is nodes[0]

    root = [nodes[0], *nodes, nodes[-1], nodes[0]]
    assert check(root) is root


def test_forced_constant_identity_hash_probes_wrap_and_keep_duplicates_exact():
    env, hash_calls = _instrumented_runtime(forced_hash=-1)
    nodes: list[list[object]] = [[] for _ in range(80)]
    root: list[object] = [nodes[0], *nodes, nodes[-1], nodes[0]]
    root.append(root)

    assert env["_check_collection_size"](root) is root
    assert len(hash_calls) > len(nodes)


@pytest.mark.parametrize("negative", (False, True))
def test_perturb_probing_spreads_clustered_low_bits_and_negative_hashes(negative):
    nodes: list[list[object]] = [[] for _ in range(80)]
    root: list[object] = [nodes[0], *nodes, nodes[-1], nodes[0]]
    root.append(root)
    tracked_values = [root, *nodes]
    hashes = {
        id(value): (-1 if negative else 1) * ((index + 1) << 20)
        for index, value in enumerate(tracked_values)
    }
    env, _hash_calls = _instrumented_runtime(
        hash_function=lambda value: hashes[id(value)]
    )

    assert env["_check_collection_size"](root) is root

    values = tracked_values[:40]
    size = 64
    missing = env["_GENO_MISSING"]
    hash_mask = env["_IDENTITY_HASH_MASK"]
    expected: list[object] = [missing] * size
    probe_steps = 0
    for value in values:
        identity_hash = hashes[id(value)] & hash_mask
        index = identity_hash & (size - 1)
        perturb = identity_hash
        while expected[index] is not missing:
            index = (index * 5 + perturb + 1) & (size - 1)
            perturb >>= 5
            probe_steps += 1
        expected[index] = value

    actual = env["_rehash_identity_table"](values, size)
    assert all(
        actual_value is expected_value
        for actual_value, expected_value in zip(actual, expected, strict=True)
    )
    assert probe_steps < len(values) * 6


def test_compact_identity_table_bypasses_user_hash_and_equality():
    env = _runtime()

    class HostileList(list):
        def __hash__(self):  # type: ignore[override]
            raise AssertionError("user hash must not run")

        def __eq__(self, other):
            raise AssertionError("user equality must not run")

    nodes = [HostileList() for _ in range(80)]
    root = HostileList([nodes[0], *nodes, nodes[-1], nodes[0]])
    root.append(root)

    assert env["_check_collection_size"](root) is root


def test_compact_identity_table_keeps_strong_references_until_walk_finishes():
    env = _runtime(collection_limit=256)
    observed: list[bool] = []
    root: list[object] = []

    class Node(list):
        pass

    references: list[weakref.ReferenceType[Node]] = []

    class RetentionProbe(list):
        def __len__(self):
            if not observed:
                root.clear()
                gc.collect()
                observed.append(
                    all(reference() is not None for reference in references)
                )
            return super().__len__()

    nodes = [Node() for _ in range(80)]
    references.extend(weakref.ref(node) for node in nodes)
    root.extend([RetentionProbe(), *nodes])
    del nodes

    assert env["_check_collection_size"](root) is root
    assert observed == [True]
    gc.collect()
    assert all(reference() is None for reference in references)


def test_check_collection_size_walks_dict_keys_and_values_in_existing_order():
    env = _runtime(collection_limit=4, integer_limit=4)
    check = env["_check_collection_size"]

    with pytest.raises(
        RuntimeError, match=r"^Integer exceeds maximum size \(8 bits\)$"
    ):
        check({128: "ok"})

    with pytest.raises(RuntimeError, match=r"^String size exceeds limit \(8 > 4\)$"):
        check({128: "too long"})


def test_check_collection_size_preserves_sequence_and_constructor_error_order():
    env = _runtime(collection_limit=4, integer_limit=4)
    check = env["_check_collection_size"]

    @dataclass(frozen=True)
    class Pair(env["Constructor"]):  # type: ignore[misc, valid-type, name-defined]
        left: object
        right: object

    values = (
        ["too long", 128],
        ("too long", 128),
        Pair("too long", 128),
    )
    for value in values:
        with pytest.raises(
            RuntimeError, match=r"^Integer exceeds maximum size \(8 bits\)$"
        ):
            check(value)


def test_check_collection_size_walks_generated_runtime_container_types():
    env = _runtime(collection_limit=4, integer_limit=4)
    check = env["_check_collection_size"]
    array = env["_GenoArray"]([128])
    vec = env["_GenoVec"]([128])
    geno_set = env["_GenoSet"]({128})
    mutable_map = env["_GenoMutableMap"]()
    mutable_map._data["key"] = 128

    for value in (array, vec, geno_set, mutable_map):
        with pytest.raises(
            RuntimeError, match=r"^Integer exceeds maximum size \(8 bits\)$"
        ):
            check(value)


@pytest.mark.parametrize(
    ("kind", "build"),
    (
        ("List", lambda env: [0, 1, 2]),
        ("Tuple", lambda env: (0, 1, 2)),
        ("Map", lambda env: {0: 0, 1: 1, 2: 2}),
        ("Array", lambda env: env["_GenoArray"]([0, 1, 2])),
        ("Vec", lambda env: env["_GenoVec"]([0, 1, 2])),
        ("Set", lambda env: env["_GenoSet"]({0, 1, 2})),
        (
            "MutableMap",
            lambda env: _mutable_map_with(env, {0: 0, 1: 1, 2: 2}),
        ),
    ),
)
def test_check_collection_size_reports_exact_tightened_collection_limit(kind, build):
    env = _runtime(collection_limit=2)

    with pytest.raises(RuntimeError, match=rf"^{kind} size exceeds limit \(3 > 2\)$"):
        env["_check_collection_size"](build(env))


def _mutable_map_with(env, data):
    mutable_map = env["_GenoMutableMap"]()
    mutable_map._data.update(data)
    return mutable_map


def test_check_collection_size_reports_exact_tightened_integer_limit():
    env = _runtime(integer_limit=7)

    with pytest.raises(
        RuntimeError, match=r"^Integer exceeds maximum size \(8 bits\)$"
    ):
        env["_check_collection_size"](128)


def test_compact_identity_table_is_available_in_restricted_compiled_runtime():
    source = """
    func main() -> List[List[Int]]
        return [[1], [2]]
    end func
    """
    compiled = compile_and_exec(source, timeout=None)

    assert compiled["main"]() == [[1], [2]]
