"""`geno fmt` over everything the repository ships (milestone M9).

Every Geno program in the repository is in formatted form, formatting is a
fixed point, and it never changes what a program parses to. Two fixtures are
exempt because they hold syntax 0.6 removed, so they do not parse.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from geno.formatter import _ast_key, _without_hash_lines, format_source
from geno.parser import ParseError, ParseErrors, parse

_ROOT = Path(__file__).resolve().parents[2]

# Removed-syntax fixtures: they exist to be rejected, so they never parse.
_UNPARSABLE_FIXTURES = {
    "conformance/v0.6/programs/diagnostic_removed_try_catch.geno",
    "geno/tests/security_corpus/try_catch_removed.geno",
}


def _program_files() -> list[str]:
    files = []
    for path in sorted(_ROOT.rglob("*.geno")):
        relative = path.relative_to(_ROOT).as_posix()
        if relative.startswith((".venv/", "node_modules/")) or "/node_modules/" in (
            relative
        ):
            continue
        files.append(relative)
    return files


def _benchmark_solutions() -> list[str]:
    return sorted(
        path.relative_to(_ROOT).as_posix()
        for path in (_ROOT / "benchmark" / "problems").glob("*.yaml")
    )


def _doc_snippets() -> list[tuple[str, str]]:
    snippets = []
    for path in sorted((_ROOT / "docs").rglob("*.md")) + [_ROOT / "README.md"]:
        text = path.read_text(encoding="utf-8")
        for index, match in enumerate(re.finditer(r"```geno\n(.*?)```", text, re.S)):
            snippets.append((f"{path.relative_to(_ROOT)}#{index}", match.group(1)))
    return snippets


PROGRAM_FILES = _program_files()


def _parses_the_same(before: str, after: str) -> bool:
    try:
        original = parse(_without_hash_lines(before))
    except (ParseError, ParseErrors):
        return True
    return _ast_key(original) == _ast_key(parse(_without_hash_lines(after)))


def test_the_corpus_is_large() -> None:
    assert len(PROGRAM_FILES) > 200


@pytest.mark.parametrize("relative", PROGRAM_FILES)
def test_every_program_is_formatted(relative: str) -> None:
    source = (_ROOT / relative).read_text(encoding="utf-8")
    formatted = format_source(source)
    assert format_source(formatted) == formatted
    if relative in _UNPARSABLE_FIXTURES:
        return
    assert formatted == source, f"run `geno fmt {relative}`"


@pytest.mark.parametrize("relative", _benchmark_solutions())
def test_benchmark_reference_solutions_are_formatted(relative: str) -> None:
    problem = yaml.safe_load((_ROOT / relative).read_text(encoding="utf-8"))
    solution = problem["problem"]["canonical_solutions"]["geno"]
    assert format_source(solution) == solution


def test_every_doc_snippet_formats_to_a_fixed_point() -> None:
    """Fragments too: formatting never changes what a complete snippet means."""
    snippets = _doc_snippets()
    assert len(snippets) > 300
    for name, snippet in snippets:
        formatted = format_source(snippet)
        assert format_source(formatted) == formatted, name
        assert _parses_the_same(snippet, formatted), name
