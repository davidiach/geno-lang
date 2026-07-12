"""
Semantic source-map tests for the Geno JavaScript compiler.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass
from typing import Any, cast

import pytest

from geno.js_compiler import compile_to_js

SEMANTIC_SOURCE = textwrap.dedent(
    """\
    type Shape = Circle(radius: Float) | Rectangle(w: Float, h: Float)

    func bump(x: Int) -> Int
        example 1 -> 2
        return x + 1
    end func

    func classify(n: Int, shape: Shape) -> String
        example (1, Circle(2.0)) -> "positive"
        if n > 0 then
            let inc: Int = bump(n)
            return "positive"
        else
            match shape with
                | Circle(r) -> return "circle"
                | Rectangle(w, h) -> return "rect"
            end match
        end if
    end func

    func main() -> String
        return classify(1, Circle(2.0))
    end func
    """
)

_SOURCE_MAP_BASE64_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
)


@dataclass(frozen=True)
class DecodedMapping:
    generated_line: int
    generated_column: int
    source: str
    source_line: int
    source_column: int


@dataclass(frozen=True)
class SourceMapFixture:
    generated_file: str
    source: str
    source_file: str
    generated_lines: list[str]
    mappings: list[DecodedMapping]


def _decode_vlq_segment(segment: str) -> list[int]:
    values: list[int] = []
    value = 0
    shift = 0

    for char in segment:
        digit = _SOURCE_MAP_BASE64_ALPHABET.index(char)
        continuation = digit & 32
        digit &= 31
        value += digit << shift
        if continuation:
            shift += 5
            continue

        values.append(-(value >> 1) if value & 1 else value >> 1)
        value = 0
        shift = 0

    return values


def _decode_mappings(source_map: dict[str, Any]) -> list[DecodedMapping]:
    sources = cast(list[str], source_map["sources"])
    mappings = cast(str, source_map["mappings"])
    decoded: list[DecodedMapping] = []

    prev_src_idx = 0
    prev_src_line = 0
    prev_src_col = 0

    for generated_line, line in enumerate(mappings.split(";"), start=1):
        prev_out_col = 0
        if not line:
            continue

        for segment in line.split(","):
            values = _decode_vlq_segment(segment)
            if not values:
                continue

            prev_out_col += values[0]
            if len(values) < 4:
                continue

            prev_src_idx += values[1]
            prev_src_line += values[2]
            prev_src_col += values[3]
            decoded.append(
                DecodedMapping(
                    generated_line=generated_line,
                    generated_column=prev_out_col + 1,
                    source=sources[prev_src_idx],
                    source_line=prev_src_line + 1,
                    source_column=prev_src_col + 1,
                )
            )

    return decoded


def _compile_semantic_fixture() -> SourceMapFixture:
    source_file = "semantic.geno"
    generated_file = "semantic.js"
    result = compile_to_js(
        SEMANTIC_SOURCE,
        filename=source_file,
        source_map=True,
        source_map_file=generated_file,
    )
    assert isinstance(result, tuple)
    js_code, source_map_json = result
    source_map = cast(dict[str, Any], json.loads(source_map_json))
    assert source_map["file"] == generated_file

    return SourceMapFixture(
        generated_file=generated_file,
        source=SEMANTIC_SOURCE,
        source_file=source_file,
        generated_lines=js_code.splitlines(),
        mappings=_decode_mappings(source_map),
    )


def _find_generated_line(fixture: SourceMapFixture, needle: str) -> tuple[int, str]:
    matches = [
        (line_number, line)
        for line_number, line in enumerate(fixture.generated_lines, start=1)
        if needle in line
    ]
    if not matches:
        raise AssertionError(f"generated JS line containing {needle!r} not found")
    if len(matches) > 1:
        formatted = ", ".join(
            f"{fixture.generated_file}:{line_number}" for line_number, _line in matches
        )
        raise AssertionError(
            f"generated JS line containing {needle!r} was ambiguous: {formatted}"
        )
    return matches[0]


def _find_source_location(
    fixture: SourceMapFixture, needle: str
) -> tuple[int, int, str]:
    for line_number, line in enumerate(fixture.source.splitlines(), start=1):
        column = line.find(needle)
        if column >= 0:
            return line_number, column + 1, line

    raise AssertionError(f"Geno source line containing {needle!r} not found")


def _first_mapping_for_generated_line(
    fixture: SourceMapFixture,
    generated_line: int,
    generated_text: str,
    expected_source_location: str,
) -> DecodedMapping:
    mappings = [
        mapping
        for mapping in fixture.mappings
        if mapping.generated_line == generated_line
    ]
    if mappings:
        return min(mappings, key=lambda mapping: mapping.generated_column)

    raise AssertionError(
        f"no mapping for generated "
        f"{fixture.generated_file}:{generated_line}:1 {generated_text.strip()!r}; "
        f"expected {expected_source_location}"
    )


def _format_mapping(mapping: DecodedMapping) -> str:
    return f"{mapping.source}:{mapping.source_line}:{mapping.source_column}"


def _assert_generated_line_maps_to_source(
    fixture: SourceMapFixture,
    generated_needle: str,
    source_needle: str,
) -> None:
    generated_line, generated_text = _find_generated_line(fixture, generated_needle)
    source_line, source_column, source_text = _find_source_location(
        fixture,
        source_needle,
    )
    expected_source_location = (
        f"{fixture.source_file}:{source_line}:{source_column} "
        f"containing {source_needle!r} in {source_text.strip()!r}"
    )
    mapping = _first_mapping_for_generated_line(
        fixture,
        generated_line,
        generated_text,
        expected_source_location,
    )

    if (
        mapping.source != fixture.source_file
        or mapping.source_line != source_line
        or mapping.source_column != source_column
    ):
        raise AssertionError(
            f"generated "
            f"{fixture.generated_file}:{generated_line}:{mapping.generated_column} "
            f"{generated_text.strip()!r} mapped to {_format_mapping(mapping)}; "
            f"expected {expected_source_location}"
        )


def _assert_generated_line_has_no_mapping(
    fixture: SourceMapFixture,
    generated_needle: str,
) -> None:
    generated_line, generated_text = _find_generated_line(fixture, generated_needle)
    mappings = [
        mapping
        for mapping in fixture.mappings
        if mapping.generated_line == generated_line
    ]
    if mappings:
        mapping = min(mappings, key=lambda item: item.generated_column)
        raise AssertionError(
            f"generated {fixture.generated_file}:{generated_line}:1 "
            f"{generated_text.strip()!r} unexpectedly mapped to "
            f"{_format_mapping(mapping)}; expected generated helper boundary"
        )


class TestSemanticSourceMapValidation:
    def test_decoder_keeps_columns_after_unmapped_segments(self):
        decoded = _decode_mappings(
            {
                "sources": ["synthetic.geno"],
                "mappings": "K,GAAA",
            }
        )

        assert decoded == [
            DecodedMapping(
                generated_line=1,
                generated_column=9,
                source="synthetic.geno",
                source_line=1,
                source_column=1,
            )
        ]

    def test_functions_branches_and_match_arms_have_semantic_mappings(self):
        fixture = _compile_semantic_fixture()

        _assert_generated_line_maps_to_source(
            fixture,
            "function bump(x)",
            "func bump",
        )
        _assert_generated_line_maps_to_source(
            fixture,
            "function classify(n, shape)",
            "func classify",
        )
        _assert_generated_line_maps_to_source(
            fixture,
            "if ((_compareOrderedValues",
            "if n > 0 then",
        )
        _assert_generated_line_maps_to_source(
            fixture,
            "const inc = bump(n);",
            "let inc",
        )
        _assert_generated_line_maps_to_source(
            fixture,
            'return _checkCollectionSize("positive");',
            'return "positive"',
        )
        _assert_generated_line_maps_to_source(
            fixture,
            " = shape;",
            "match shape with",
        )
        _assert_generated_line_maps_to_source(
            fixture,
            'return _checkCollectionSize("circle");',
            'return "circle"',
        )
        _assert_generated_line_maps_to_source(
            fixture,
            'return _checkCollectionSize("rect");',
            'return "rect"',
        )

    def test_generated_helper_boundaries_are_not_mapped_to_geno(self):
        fixture = _compile_semantic_fixture()

        _assert_generated_line_has_no_mapping(fixture, "function _safe_add")
        _assert_generated_line_maps_to_source(
            fixture,
            "return _safe_add(x",
            "return x + 1",
        )
        _assert_generated_line_has_no_mapping(
            fixture,
            "const _main_result = main();",
        )
        _assert_generated_line_has_no_mapping(
            fixture,
            "console.log(_main_result);",
        )

    def test_failure_messages_name_generated_span_and_expected_geno_location(self):
        fixture = _compile_semantic_fixture()

        with pytest.raises(AssertionError) as exc_info:
            _assert_generated_line_maps_to_source(
                fixture,
                "if ((_compareOrderedValues",
                'return "positive"',
            )

        message = str(exc_info.value)
        assert "generated semantic.js:" in message
        assert "if ((_compareOrderedValues" in message
        assert "expected semantic.geno:" in message
        assert 'return "positive"' in message

        with pytest.raises(AssertionError) as no_mapping_info:
            _assert_generated_line_maps_to_source(
                fixture,
                "const _main_result = main();",
                "func main",
            )

        no_mapping_message = str(no_mapping_info.value)
        assert "no mapping for generated semantic.js:" in no_mapping_message
        assert "const _main_result = main();" in no_mapping_message
        assert "expected semantic.geno:" in no_mapping_message
        assert "func main" in no_mapping_message
