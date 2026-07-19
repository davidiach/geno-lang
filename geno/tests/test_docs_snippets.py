"""Targeted parser regressions for runnable documentation snippets."""

import re
from pathlib import Path

from geno.lexer import Lexer
from geno.parser import Parser
from geno.typechecker import TypeChecker

ROOT = Path(__file__).resolve().parents[2]


def _geno_snippet(path: str, snippet_no: int) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    matches = list(re.finditer(r"```geno\s*\n(.*?)\n```", text, re.S))
    return matches[snippet_no - 1].group(1)


def _geno_snippet_after_heading(path: str, heading: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    heading_match = re.search(rf"^{re.escape(heading)}\s*$", text, re.M)
    assert heading_match is not None

    section = text[heading_match.end() :]
    next_heading = re.search(r"^##\s+", section, re.M)
    if next_heading is not None:
        section = section[: next_heading.start()]

    match = re.search(r"```geno\s*\n(.*?)\n```", section, re.S)
    assert match is not None
    return match.group(1)


def test_guide_walkthrough_snippets_parse():
    for path, snippet_no in [
        ("docs/guide/first-app.md", 1),
        ("docs/guide/tutorial-todo-app.md", 6),
    ]:
        code = _geno_snippet(path, snippet_no)
        Parser(Lexer(code, f"{path}#{snippet_no}").tokenize()).parse_program()


def test_spec_example_program_typechecks():
    code = _geno_snippet_after_heading("docs/spec/v0.4.md", "## 12. Example Program")
    program = Parser(
        Lexer(code, "docs/spec/v0.4.md#example-program").tokenize()
    ).parse_program()

    TypeChecker().check_program(program)
