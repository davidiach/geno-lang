"""Interpolated values are rendered incrementally within the string budget."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from geno import _runtime_support as runtime


@dataclass(frozen=True)
class AuditPair(runtime.Constructor):
    left: Any
    right: Any


def _mutable_map(values: dict) -> Any:
    result = runtime._GenoMutableMap()
    result._data.update(values)
    return result


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        123,
        -17,
        1.25,
        "raw\ntext",
        "",
        [],
        (),
        (1,),
        {},
        ['quote"\\\b\f\n\r\t\x00\x1f😀', None, True],
        ("nested", [1, 2]),
        {"key": [1, False], "second": "value"},
        runtime._GenoArray(["a", [1, 2]]),
        runtime._GenoVec(["a", (1,)]),
        runtime._GenoSet({"z", "a", "b"}),
        runtime._GenoSet({runtime.Some(2), runtime.Some(1)}),
        _mutable_map({"key": [1, 2]}),
        runtime.None_,
        runtime.Some("hello"),
        runtime.Ok([1, 2]),
        runtime.Err("error"),
        AuditPair([1, "a"], {"enabled": True}),
    ],
)
def test_append_value_matches_canonical_format_at_exact_budget(monkeypatch, value):
    expected = runtime._geno_format(value)
    monkeypatch.setattr(runtime, "_MAX_COLLECTION_SIZE", len(expected))
    builder = runtime._GenoFString()

    builder.append_value(value)

    assert builder.finish() == expected
    if expected:
        monkeypatch.setattr(runtime, "_MAX_COLLECTION_SIZE", len(expected) - 1)
        with pytest.raises(RuntimeError, match="String size exceeds limit"):
            runtime._GenoFString().append_value(value)


def _cyclic_values():
    values = []
    sequence: list[Any] = []
    sequence.append(sequence)
    values.append(sequence)
    mapping: dict[str, Any] = {}
    mapping["self"] = mapping
    values.append(mapping)
    tuple_child: list[Any] = []
    tuple_value = (tuple_child,)
    tuple_child.append(tuple_value)
    values.append(tuple_value)
    for wrapper in (runtime._GenoArray, runtime._GenoVec):
        wrapped = wrapper([])
        wrapped._elements.append(wrapped)
        values.append(wrapped)
    mutable = _mutable_map({})
    mutable._data["self"] = mutable
    values.append(mutable)
    array = runtime._GenoArray([])
    constructor = runtime.Some(array)
    array._elements.append(constructor)
    values.append(constructor)
    return values


@pytest.mark.parametrize("value", _cyclic_values())
def test_append_value_preserves_canonical_cycle_markers(monkeypatch, value):
    expected = runtime._geno_format(value)
    monkeypatch.setattr(runtime, "_MAX_COLLECTION_SIZE", len(expected))
    builder = runtime._GenoFString()

    builder.append_value(value)

    assert builder.finish() == expected


@pytest.mark.parametrize(
    "wrap",
    [
        list,
        tuple,
        runtime._GenoArray,
        runtime._GenoVec,
        lambda values: {str(index): value for index, value in enumerate(values)},
        lambda values: _mutable_map(
            {str(index): value for index, value in enumerate(values)}
        ),
        runtime.Some,
        lambda values: AuditPair(values, True),
    ],
)
def test_container_overflow_stops_before_formatting_later_values(monkeypatch, wrap):
    class LaterValue:
        def __str__(self):
            raise AssertionError("formatted a later value after exceeding the budget")

    monkeypatch.setattr(runtime, "_MAX_COLLECTION_SIZE", 32)
    value = wrap(["abcdefgh"] * 6 + [LaterValue()])
    builder = runtime._GenoFString()

    with pytest.raises(RuntimeError, match="String size exceeds limit"):
        builder.append_value(value)

    assert builder.size <= 32


def test_set_overflow_stops_formatting_items_early(monkeypatch):
    formatted = []

    class ObservedInt(int):
        def __str__(self):
            formatted.append(int(self))
            return super().__str__()

    monkeypatch.setattr(runtime, "_MAX_COLLECTION_SIZE", 12)
    value = runtime._GenoSet({ObservedInt(index) for index in range(10)})

    with pytest.raises(RuntimeError, match="String size exceeds limit"):
        runtime._GenoFString().append_value(value)

    assert formatted == [0, 1, 2]


def test_nested_string_escaping_stops_before_consuming_the_whole_string(monkeypatch):
    visited = []

    class ObservedString(str):
        def __iter__(self):
            for char in super().__iter__():
                visited.append(char)
                yield char

    # Each NUL expands to six output characters. Stop after the second one,
    # without constructing an escaped representation of all 16 characters.
    monkeypatch.setattr(runtime, "_MAX_COLLECTION_SIZE", 10)

    with pytest.raises(RuntimeError, match="String size exceeds limit"):
        runtime._GenoFString().append_value([ObservedString("\x00" * 16)])

    assert len(visited) == 2


def test_value_budget_includes_preceding_interpolation_parts(monkeypatch):
    monkeypatch.setattr(runtime, "_MAX_COLLECTION_SIZE", 8)
    builder = runtime._GenoFString()
    builder.append("prefix")

    with pytest.raises(RuntimeError, match="String size exceeds limit"):
        builder.append_value([1])

    assert builder.size <= 8
