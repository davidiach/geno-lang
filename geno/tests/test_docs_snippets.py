"""Targeted parser regressions for runnable documentation snippets."""

import re
from pathlib import Path

import pytest

from geno.interpreter import interpret
from geno.lexer import Lexer
from geno.parser import ParseError, Parser
from geno.typechecker import TypeChecker

ROOT = Path(__file__).resolve().parents[2]


def _geno_snippet(path: str, snippet_no: int) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    matches = list(re.finditer(r"```geno\s*\n(.*?)\n```", text, re.S))
    return matches[snippet_no - 1].group(1)


def _geno_snippet_after_heading(path: str, heading: str, snippet_no: int = 1) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    heading_match = re.search(rf"^{re.escape(heading)}\s*$", text, re.M)
    assert heading_match is not None

    section = text[heading_match.end() :]
    next_heading = re.search(r"^##\s+", section, re.M)
    if next_heading is not None:
        section = section[: next_heading.start()]

    matches = list(re.finditer(r"```geno\s*\n(.*?)\n```", section, re.S))
    assert len(matches) >= snippet_no
    return matches[snippet_no - 1].group(1)


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


def test_readme_opening_example_executes_with_examples_enabled():
    code = _geno_snippet("README.md", 1)
    source = "README.md#opening-example"
    program = Parser(Lexer(code, source).tokenize()).parse_program()

    TypeChecker().check_program(program)
    assert (
        interpret(
            code,
            source,
            check_examples=True,
        )
        == "excellent"
    )


def test_parse_int_option_snippets_execute_with_examples_enabled():
    # Regression for #77: docs must show parse_int/parse_float as Option, not
    # Result, and the shown Some/None matches must actually run.
    for path, heading, snippet_no in [
        ("docs/guide/language-tour.md", "### Parsing Returns Option, Not Result", 1),
        ("docs/reference/common-pitfalls.md", "## 6. No Null — Use Option[T]", 3),
    ]:
        code = _geno_snippet_after_heading(path, heading, snippet_no)
        assert "match parse_int(" in code
        assert "| Some(" in code and "| None" in code
        assert "| Ok(" not in code and "| Err(" not in code
        source = f"{path}#{heading}"
        program = Parser(Lexer(code, source).tokenize()).parse_program()
        TypeChecker().check_program(program)
        interpret(code, source, check_examples=True)


def test_module_constant_snippet_executes_with_examples_enabled():
    # Regression for #72: the tour's module-constant example must run, and the
    # rejection it documents must be the diagnostic the parser actually emits.
    path = "docs/guide/language-tour.md"
    heading = "### Module Constants"
    code = _geno_snippet_after_heading(path, heading, 1)
    source = f"{path}#{heading}"

    program = Parser(Lexer(code, source).tokenize()).parse_program()
    TypeChecker().check_program(program)
    assert interpret(code, source, check_examples=True) == "roman <number> uses M"

    rejected = "let limit = max_size() + 1\n"
    with pytest.raises(ParseError) as excinfo:
        Parser(Lexer(rejected, source).tokenize()).parse_program()
    assert "must be initialized by a literal" in str(excinfo.value)
    assert "an operator expression" in str(excinfo.value)


def test_tour_list_snippet_typechecks_and_runs():
    # The tour's map/filter/fold snippet must satisfy the named-argument rule
    # for three-parameter builtins such as fold.
    path = "docs/guide/language-tour.md"
    heading = "### Lists (Immutable)"
    snippet = _geno_snippet_after_heading(path, heading, 1)
    body = "\n".join("    " + line for line in snippet.splitlines())
    code = (
        "func lists() -> Int\n"
        "    example () -> 15\n"
        f"{body}\n"
        "    return total + length(doubled) + length(evens) - 7\n"
        "end func\n"
    )
    source = f"{path}#{heading}"
    program = Parser(Lexer(code, source).tokenize()).parse_program()
    TypeChecker().check_program(program)
    interpret(code, source, check_examples=True)


def _section(path: str, heading: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    start = text.index(f"{heading}\n")
    following = re.search(r"^#{2,3} ", text[start + len(heading) :], re.M)
    end = start + len(heading) + following.start() if following else len(text)
    return text[start:end]


@pytest.mark.parametrize("heading", ["### Contracts", "### Panics"])
def test_tour_panics_examples_pass(heading):
    """The tour's panics examples (0.7 draft, proposal 0017) hold when run."""
    path = "docs/guide/language-tour.md"
    code = _geno_snippet_after_heading(path, heading, 1)
    assert " panics" in code
    source = f"{path}#{heading}"
    interpret(f"{code}\n\nfunc main() -> Unit\nend func\n", source, check_examples=True)


def test_llm_prompting_panics_section_shows_the_right_and_wrong_forms():
    import geno

    section = _section(
        "docs/llm-prompting.md",
        "### 20. Examples that expect a panic, and `requires` as the domain",
    )
    wrong, *right = re.findall(r"```\n(.*?)```", section, re.S)
    main = "\nfunc main() -> Unit\nend func\n"

    result = geno.check(wrong + main)
    assert [d.code.value for d in result.diagnostics] == ["E330", "E330"]
    fixed = geno.fix(wrong + main)
    assert fixed.check.ok, [d.message for d in fixed.check.diagnostics]

    assert len(right) == 2
    for code in right:
        assert " panics" in code
        result = geno.run(code + main)
        assert result.ok, [d.message for d in result.diagnostics]


def test_common_pitfalls_panics_fix_is_the_one_geno_applies():
    import geno

    section = _section(
        "docs/reference/common-pitfalls.md", "## 12. Expecting a Panic After an Arrow"
    )
    wrong, right = re.findall(r"```\n(.*?)```", section, re.S)[:2]
    # Both start with comment lines: two before the program, one before the fix.
    assert geno.fix(wrong).source.split("\n")[2:] == right.split("\n")[1:]
    assert geno.run(right + "\nfunc main() -> Unit\nend func\n").ok
