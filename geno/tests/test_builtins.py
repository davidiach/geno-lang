"""
Tests for Geno Built-in Functions
===================================

Direct unit tests for geno/builtins.py.
"""

import pytest

from geno.builtins import (
    builtin_append,
    builtin_clamp,
    builtin_cli_args,
    builtin_concat,
    builtin_contains,
    builtin_divide,
    builtin_head,
    builtin_is_none,
    builtin_is_numeric_string,
    builtin_is_permutation,
    builtin_is_some,
    builtin_is_sorted,
    builtin_join,
    builtin_length,
    builtin_map_entries,
    builtin_map_from_entries,
    builtin_map_from_list,
    builtin_map_get,
    builtin_map_insert,
    builtin_max,
    builtin_parse_int,
    builtin_reverse,
    builtin_set_at,
    builtin_slice,
    builtin_sort_strings,
    builtin_split,
    builtin_split_once,
    builtin_sqrt,
    builtin_starts_with,
    builtin_substring,
    builtin_tail,
    builtin_to_chars,
    builtin_to_lower,
    builtin_trim,
    builtin_unwrap,
    builtin_unwrap_or,
    format_value,
    set_max_collection_size,
    stringify_value,
)
from geno.values import (
    BuiltinFunction,
    Closure,
    ConstructorValue,
    Environment,
    RuntimeError,
)


class TestListBuiltins:
    """Tests for list operations."""

    def test_length(self):
        assert builtin_length([1, 2, 3]) == 3

    def test_length_empty(self):
        assert builtin_length([]) == 0

    def test_length_string(self):
        assert builtin_length("abc") == 3

    def test_length_non_list(self):
        with pytest.raises(RuntimeError):
            builtin_length(42)

    def test_head(self):
        assert builtin_head([10, 20, 30]) == 10

    def test_head_empty(self):
        with pytest.raises(RuntimeError, match="head of empty list"):
            builtin_head([])

    def test_head_non_list(self):
        with pytest.raises(RuntimeError):
            builtin_head("not a list")

    def test_tail(self):
        assert builtin_tail([10, 20, 30]) == [20, 30]

    def test_tail_empty(self):
        with pytest.raises(RuntimeError, match="tail of empty list"):
            builtin_tail([])

    def test_tail_non_list(self):
        with pytest.raises(RuntimeError):
            builtin_tail(42)

    def test_append(self):
        assert builtin_append([1, 2], 3) == [1, 2, 3]

    def test_append_non_list(self):
        with pytest.raises(RuntimeError):
            builtin_append("not a list", 1)

    def test_concat(self):
        assert builtin_concat([1, 2], [3, 4]) == [1, 2, 3, 4]

    def test_concat_non_lists(self):
        with pytest.raises(RuntimeError, match="concat expects two lists"):
            builtin_concat([1], "not a list")

    def test_set_at(self):
        assert builtin_set_at([1, 2, 3], 1, 99) == [1, 99, 3]

    def test_set_at_out_of_range(self):
        with pytest.raises(RuntimeError, match="index out of range"):
            builtin_set_at([1, 2], 5, 0)

    def test_slice(self):
        assert builtin_slice([1, 2, 3, 4], 1, 3) == [2, 3]

    def test_slice_clamped(self):
        assert builtin_slice([1, 2], -5, 5) == [1, 2]

    def test_contains_true(self):
        assert builtin_contains([1, 2, 3], 2) is True

    def test_contains_false(self):
        assert builtin_contains([1, 2, 3], 5) is False

    def test_contains_non_list(self):
        with pytest.raises(RuntimeError):
            builtin_contains("not a list", 1)

    def test_reverse(self):
        assert builtin_reverse([1, 2, 3]) == [3, 2, 1]

    def test_reverse_empty(self):
        assert builtin_reverse([]) == []

    def test_reverse_non_list(self):
        with pytest.raises(RuntimeError):
            builtin_reverse("not a list")

    def test_is_sorted_true(self):
        assert builtin_is_sorted([1, 2, 3]) is True

    def test_is_sorted_false(self):
        assert builtin_is_sorted([3, 1, 2]) is False

    def test_is_sorted_empty(self):
        assert builtin_is_sorted([]) is True

    def test_is_sorted_non_list(self):
        with pytest.raises(RuntimeError):
            builtin_is_sorted("abc")

    def test_is_permutation_true(self):
        assert builtin_is_permutation([1, 2, 3], [3, 1, 2]) is True

    def test_is_permutation_false(self):
        assert builtin_is_permutation([1, 2, 3], [1, 2, 4]) is False

    def test_is_permutation_non_lists(self):
        with pytest.raises(RuntimeError):
            builtin_is_permutation("abc", [1, 2])

    def test_is_permutation_mixed_types_use_equality(self):
        assert builtin_is_permutation([1, "a"], ["a", 1]) is True
        assert builtin_is_permutation([1, "a"], ["a", 2]) is False

    def test_is_permutation_unorderable_values_use_equality(self):
        left = [ConstructorValue("Some", {"value": 1}), ConstructorValue("None", {})]
        right = [ConstructorValue("None", {}), ConstructorValue("Some", {"value": 1})]
        duplicate_mismatch = [
            ConstructorValue("Some", {"value": 1}),
            ConstructorValue("Some", {"value": 1}),
        ]

        assert builtin_is_permutation(left, right) is True
        assert builtin_is_permutation(left, duplicate_mismatch) is False


class TestStringBuiltins:
    """Tests for string operations."""

    def test_split(self):
        assert builtin_split("a,b,c", ",") == ["a", "b", "c"]

    def test_split_non_string(self):
        with pytest.raises(RuntimeError, match="split expects strings"):
            builtin_split(42, ",")

    def test_join(self):
        assert builtin_join(["a", "b", "c"], ",") == "a,b,c"

    def test_join_non_list(self):
        with pytest.raises(RuntimeError, match="join expects list and string"):
            builtin_join("not a list", ",")

    def test_trim(self):
        assert builtin_trim("  hello  ") == "hello"

    def test_trim_non_string(self):
        with pytest.raises(RuntimeError):
            builtin_trim(42)

    def test_to_lower(self):
        assert builtin_to_lower("HeLLo") == "hello"

    def test_starts_with(self):
        assert builtin_starts_with("prefix_value", "prefix") is True

    def test_starts_with_non_string(self):
        with pytest.raises(RuntimeError, match="starts_with expects strings"):
            builtin_starts_with("value", 1)

    def test_split_once_found(self):
        result = builtin_split_once("key=value", "=")
        assert isinstance(result, ConstructorValue)
        assert result.constructor == "Some"
        assert result.fields["value"] == ("key", "value")

    def test_split_once_not_found(self):
        result = builtin_split_once("no separator", "=")
        assert isinstance(result, ConstructorValue)
        assert result.constructor == "None"

    def test_split_once_empty_separator(self):
        with pytest.raises(RuntimeError, match="split_once: delimiter cannot be empty"):
            builtin_split_once("abc", "")

    def test_split_once_non_string(self):
        with pytest.raises(RuntimeError):
            builtin_split_once(42, "=")

    def test_substring(self):
        assert builtin_substring("hello world", 0, 5) == "hello"

    def test_substring_clamped(self):
        assert builtin_substring("hi", -1, 100) == "hi"

    def test_substring_non_string(self):
        with pytest.raises(RuntimeError):
            builtin_substring(42, 0, 5)

    def test_substring_non_int_bounds(self):
        with pytest.raises(RuntimeError, match="integer"):
            builtin_substring("hello", "a", 5)

    def test_is_numeric_string_true(self):
        assert builtin_is_numeric_string("123") is True
        assert builtin_is_numeric_string(" -123 ") is True

    def test_is_numeric_string_false(self):
        assert builtin_is_numeric_string("abc") is False
        assert builtin_is_numeric_string("+123") is False
        assert builtin_is_numeric_string("1_000") is False

    def test_is_numeric_string_non_string(self):
        assert builtin_is_numeric_string(42) is False

    def test_to_chars(self):
        assert builtin_to_chars("abc") == ["a", "b", "c"]

    def test_sort_strings(self):
        assert builtin_sort_strings(["beta", "alpha"]) == ["alpha", "beta"]


class TestEnvBuiltins:
    """Tests for environment-backed builtins."""

    def test_cli_args_reads_json_env_var(self, monkeypatch):
        monkeypatch.setenv("GENO_CLI_ARGS", '["foo", "bar"]')
        assert builtin_cli_args() == ["foo", "bar"]

    def test_cli_args_rejects_non_string_json_array(self, monkeypatch):
        monkeypatch.setenv("GENO_CLI_ARGS", '[1, "bar"]')
        with pytest.raises(
            RuntimeError, match="GENO_CLI_ARGS must be a JSON array of strings"
        ):
            builtin_cli_args()


class TestMathBuiltins:
    """Tests for math operations."""

    def test_divide_integers(self):
        assert builtin_divide(10, 3) == 3

    def test_divide_negative_integers(self):
        assert builtin_divide(-7, 2) == -3
        assert builtin_divide(7, -2) == -3

    def test_divide_floats(self):
        assert builtin_divide(10.0, 3.0) == pytest.approx(10.0 / 3.0)

    def test_divide_by_zero(self):
        with pytest.raises(RuntimeError, match="Division by zero"):
            builtin_divide(1, 0)

    def test_sqrt_positive(self):
        assert builtin_sqrt(9) == pytest.approx(3.0)

    def test_sqrt_negative(self):
        with pytest.raises(RuntimeError, match="sqrt of negative"):
            builtin_sqrt(-1)

    def test_max(self):
        assert builtin_max(7, 3) == 7

    def test_clamp_allows_inverted_bounds(self):
        assert builtin_clamp(5, 10, 0) == 10

    def test_clamp_rejects_non_numeric_arguments(self):
        with pytest.raises(RuntimeError, match="value must be a number"):
            builtin_clamp("b", "a", "c")

    def test_parse_int_valid(self):
        result = builtin_parse_int("42")
        assert result.constructor == "Some"
        assert result.fields["value"] == 42
        result = builtin_parse_int(" -7 ")
        assert result.constructor == "Some"
        assert result.fields["value"] == -7

    def test_parse_int_invalid(self):
        result = builtin_parse_int("abc")
        assert result.constructor == "None"
        result = builtin_parse_int("+42")
        assert result.constructor == "None"
        result = builtin_parse_int("1_000")
        assert result.constructor == "None"

    def test_parse_int_non_string(self):
        with pytest.raises(RuntimeError, match="parse_int expects string"):
            builtin_parse_int(42)


class TestOptionBuiltins:
    """Tests for Option operations."""

    def test_is_some_true(self):
        val = ConstructorValue("Some", {"value": 42})
        assert builtin_is_some(val) is True

    def test_is_some_false_none(self):
        val = ConstructorValue("None", {})
        assert builtin_is_some(val) is False

    def test_is_some_non_constructor(self):
        assert builtin_is_some(42) is False

    def test_is_none_true(self):
        val = ConstructorValue("None", {})
        assert builtin_is_none(val) is True

    def test_is_none_python_none(self):
        assert builtin_is_none(None) is True

    def test_is_none_false(self):
        val = ConstructorValue("Some", {"value": 42})
        assert builtin_is_none(val) is False

    def test_unwrap_some(self):
        val = ConstructorValue("Some", {"value": 42})
        assert builtin_unwrap(val) == 42

    def test_unwrap_none_constructor(self):
        val = ConstructorValue("None", {})
        with pytest.raises(RuntimeError, match="unwrap called on None"):
            builtin_unwrap(val)

    def test_unwrap_python_none(self):
        with pytest.raises(RuntimeError, match="unwrap called on None"):
            builtin_unwrap(None)

    def test_unwrap_non_option(self):
        with pytest.raises(RuntimeError, match="unwrap expects Option"):
            builtin_unwrap(42)

    def test_unwrap_or_some(self):
        val = ConstructorValue("Some", {"value": 42})
        assert builtin_unwrap_or(val, 0) == 42

    def test_unwrap_or_none_constructor(self):
        val = ConstructorValue("None", {})
        assert builtin_unwrap_or(val, 99) == 99

    def test_unwrap_or_python_none(self):
        assert builtin_unwrap_or(None, 99) == 99

    def test_unwrap_or_non_option(self):
        with pytest.raises(RuntimeError, match="unwrap_or expects Option"):
            builtin_unwrap_or(42, 0)


class TestMapBuiltins:
    """Tests for map operations."""

    def test_map_from_list_accepts_tuple_pairs(self):
        result = builtin_map_from_list([("a", 1), ("b", 2)])
        assert result == {"a": 1, "b": 2}

    def test_map_entries_returns_tuple_pairs(self):
        result = builtin_map_entries({"a": 1, "b": 2})
        assert result == [("a", 1), ("b", 2)]

    def test_map_from_entries_accepts_tuple_pairs(self):
        result = builtin_map_from_entries([("a", 1), ("b", 2)])
        assert result == {"a": 1, "b": 2}

    def test_map_from_list_rejects_non_pairs(self):
        with pytest.raises(RuntimeError, match="map_from_list: each element must be a"):
            builtin_map_from_list([[1, 2, 3]])

    def test_map_insert(self):
        result = builtin_map_insert({"a": 1}, "b", 2)
        assert result == {"a": 1, "b": 2}

    def test_map_insert_non_map(self):
        with pytest.raises(RuntimeError, match="map_insert expects map"):
            builtin_map_insert([1, 2], "key", "val")

    def test_map_get_found(self):
        result = builtin_map_get({"a": 1}, "a")
        assert result.constructor == "Some"
        assert result.fields["value"] == 1

    def test_map_get_not_found(self):
        result = builtin_map_get({"a": 1}, "b")
        assert result.constructor == "None"

    def test_map_get_non_map(self):
        with pytest.raises(RuntimeError, match="map_get expects map"):
            builtin_map_get([1, 2], "key")


class TestFormatValue:
    """Tests for format_value display function."""

    def test_format_list(self):
        assert format_value([1, 2, 3]) == "[1, 2, 3]"

    def test_format_tuple(self):
        assert format_value((1, 2)) == "(1, 2)"
        assert format_value((1,)) == "(1,)"

    def test_format_dict(self):
        assert format_value({"a": 1}) == '{"a": 1}'

    def test_format_none(self):
        assert format_value(None) == "()"

    def test_format_string(self):
        assert format_value("hello") == '"hello"'

    def test_format_constructor(self):
        val = ConstructorValue("Some", {"value": 42})
        result = format_value(val)
        assert "Some" in result

    def test_format_constructor_formats_nested_booleans(self):
        val = ConstructorValue("Box", {"value": True, "items": [False, True]})
        assert format_value(val) == "Box(value: true, items: [false, true])"

    def test_format_constructor_no_fields(self):
        val = ConstructorValue("None", {})
        result = format_value(val)
        assert "None" in result

    def test_format_closure(self):
        env = Environment()
        closure = Closure(params=[], body=[], env=env, name="foo")
        result = format_value(closure)
        assert "foo" in result

    def test_format_builtin_function(self):
        bf = BuiltinFunction("test_fn", lambda x: x, 1, ["x"])
        result = format_value(bf)
        assert "test_fn" in result

    def test_format_integer(self):
        assert format_value(42) == "42"

    def test_format_bool(self):
        assert format_value(True) == "true"
        assert format_value(False) == "false"

    def test_format_value_cycle_detection(self):
        """Cyclic structures should not cause infinite recursion."""
        lst: list[object] = [1, 2]
        lst.append(lst)  # create cycle
        result = format_value(lst)
        assert "[...]" in result

    def test_stringify_value_unit_and_nested_booleans(self):
        val = ConstructorValue("Box", {"value": True, "items": [False, True]})

        assert stringify_value(None) == "()"
        assert stringify_value((1, 2)) == "(1, 2)"
        assert stringify_value([True, False]) == "[true, false]"
        assert stringify_value(val) == "Box(value: true, items: [false, true])"

    def test_stringify_value_keeps_top_level_string_unquoted(self):
        assert stringify_value("hello") == "hello"
        assert stringify_value(["hello"]) == '["hello"]'

    def test_stringify_value_stops_oversized_adt_output_early(self):
        set_max_collection_size(80)
        try:
            tree = ConstructorValue("Leaf", {"value": 0})
            for _ in range(5):
                tree = ConstructorValue("Node", {"left": tree, "right": tree})

            with pytest.raises(RuntimeError, match="to_string: String size exceeds"):
                stringify_value(tree)
        finally:
            set_max_collection_size(10_000_000)
