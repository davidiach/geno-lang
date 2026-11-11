"""Targeted parser regressions for runnable documentation snippets."""

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from geno.interpreter import interpret
from geno.lexer import Lexer
from geno.parser import ParseError, Parser
from geno.typechecker import TypeChecker

if TYPE_CHECKING:
    from geno.property_check import PropertyResult

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


def _property_results(
    code: str, source: str, module: str = "snippet"
) -> "dict[str, PropertyResult]":
    """`geno test --properties` results (default settings) by function name.

    *module* is the logical module name, which keys each function's inputs:
    the file stem for a single file.
    """
    from geno.property_check import check_module_properties

    program = Parser(Lexer(code, source).tokenize(), code).parse_program()
    checker = TypeChecker()
    checker.check_program(program)
    results = check_module_properties(
        program, checker, module=module, path=source, source=code
    )
    return {result.function: result for result in results}


def test_docs_that_state_a_domain_pass_their_property_checks():
    """The contracts the docs teach as domains survive `geno test --properties`.

    Without its upper bound, the tour's `factorial` runs out of the integer
    size limit and the guide's `isqrt` out of the step limit.
    """
    tour = _geno_snippet_after_heading("docs/guide/language-tour.md", "### Contracts")
    statuses = {
        name: result.status
        for name, result in _property_results(tour, "tour#contracts").items()
    }
    assert statuses == {"factorial": "passed"}

    section = _section(
        "docs/llm-prompting.md",
        "### 20. Examples that expect a panic, and `requires` as the domain",
    )
    _wrong, *right = re.findall(r"```\n(.*?)```", section, re.S)
    prompting_statuses: dict[str, str] = {}
    for number, code in enumerate(right):
        results = _property_results(code, f"llm-prompting#20-{number}")
        prompting_statuses.update(
            {name: result.status for name, result in results.items()}
        )
    # `usage` takes no arguments, so it is out of scope.
    assert prompting_statuses == {"safe_div": "passed", "isqrt": "passed"}


def test_spec_property_check_sample_matches_the_engine():
    path = "docs/spec/v0.7.md"
    heading = "### 5.4 Property Checks"
    code = _geno_snippet_after_heading(path, heading)
    # The sample is `geno test --properties abs.geno`: module `abs`.
    result = _property_results(code, "abs.geno", module="abs")["abs_val"]

    section = _section(path, heading)
    assert result.failure is not None and result.counterexample is not None
    assert f"        {result.failure.summary}\n" in section
    assert f"        counterexample: {result.counterexample.arguments}\n" in section
    assert f'"trials_run": {result.trials_run},' in section


def test_spec_runtime_report_sample_matches_the_engine(tmp_path):
    """The 6.9.1 sample (proposal 0018) is what `geno run` prints.

    The default lane runs `main` as compiled Python in the isolated worker and
    maps the failure back to Geno source, so this also pins that lane to the
    report the interpreter lanes give. Only the program's directory is
    shortened, as the sample says.
    """
    import os
    import subprocess
    import sys

    path = "docs/spec/v0.7.md"
    heading = "#### 6.9.1 Runtime Failure Reports"
    program = tmp_path / "panic.geno"
    program.write_text(
        _geno_snippet_after_heading(path, heading), encoding="utf-8", newline="\n"
    )
    section = _section(path, heading)
    (expected,) = [
        block
        for block in re.findall(r"```text\n(.*?)```", section, re.S)
        if block.startswith("Stack trace")
    ]

    completed = subprocess.run(
        [sys.executable, "-m", "geno", "run", str(program)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 1
    stderr = completed.stderr
    for directory in {os.path.realpath(tmp_path), str(tmp_path)}:
        stderr = stderr.replace(directory + os.sep, "")
    assert stderr == expected


def _function_effects(code: str, source: str) -> dict[str, frozenset[str]]:
    """The checked effect set of every top-level function in *code*."""
    from geno.types import FuncType

    program = Parser(Lexer(code, source).tokenize(), code).parse_program()
    checker = TypeChecker()
    checker.check_program(program)
    effects: dict[str, frozenset[str]] = {}
    for definition in program.definitions:
        name = getattr(definition, "name", None)
        if not isinstance(name, str):
            continue
        func_type = checker.global_env.lookup(name)
        if isinstance(func_type, FuncType):
            effects[name] = func_type.effects
    return effects


def test_tour_effects_snippet_passes_local_accumulators_to_map():
    """The tour's local-mutation example (0.7 draft, proposal 0019) checks,
    passes its examples and prints what it says; 0.6 rejected it (E300 for
    `.map(total)`, E310 for `main`'s own `var`)."""
    import geno
    from geno.formatter import format_source

    path = "docs/guide/language-tour.md"
    heading = "### Effects"
    code = _geno_snippet_after_heading(path, heading)
    source = f"{path}#{heading}"
    assert ".map(total)" in code and "array_new(" in code

    assert format_source(code + "\n") == code + "\n"
    assert _function_effects(code, source) == {
        "total": frozenset(),
        "histogram": frozenset(),
        "main": frozenset({"io"}),
    }
    interpret(code, source, check_examples=True)
    result = geno.run(code, config=geno.RunConfig(capabilities=["print"]))
    assert result.ok, [d.message for d in result.diagnostics]
    assert result.output == "[3, 3, 9]\n[1, 0, 2]\n3\n"

    section = " ".join(_section(path, heading).split())
    for shape in ("array_new(h, array_new(w, 0))", "`prev = cur`", "known limitation"):
        assert shape in section, shape


def test_llm_prompting_says_not_to_declare_mutation_for_local_state():
    import geno
    from geno.formatter import format_source

    section = _section(
        "docs/llm-prompting.md",
        "### 21. Declaring `with mutation` for a local accumulator",
    )
    wrong, right = re.findall(r"```\n(.*?)```", section, re.S)
    main = "\nfunc main() -> Unit\nend func\n"

    result = geno.check(wrong + main)
    assert [d.code.value for d in result.diagnostics] == ["E300"]
    assert "with mutation" in result.diagnostics[0].message
    effects = _function_effects(right + main, "llm-prompting#21")
    assert effects["total"] == effects["histogram"] == frozenset()
    assert geno.run(right + main).ok
    for code in (wrong, right):
        assert format_source(code) == code
    # The shapes that still carry `mutation` are listed for models.
    text = " ".join(section.split())
    for shape in (
        "a two-dimensional table",
        "a rolling buffer",
        "a user higher-order function",
        "passed to a helper function",
    ):
        assert shape in text, shape


def test_common_pitfalls_observable_write_fix_is_pure():
    import geno

    section = _section(
        "docs/reference/common-pitfalls.md",
        "## 13. Writes a Caller Can See Are `mutation`",
    )
    wrong, right = re.findall(r"```\n(.*?)```", section, re.S)[:2]

    result = geno.check(wrong)
    assert [d.code.value for d in result.diagnostics] == ["E300"]
    start, end = wrong.index("func bump"), wrong.index("func apply")
    fixed = wrong[:start] + right.split("\n", 1)[1] + "\n" + wrong[end:]
    run = geno.run(fixed, config=geno.RunConfig(capabilities=["print"]))
    assert run.ok, [d.message for d in run.diagnostics]
    assert run.output == "1\n"
    for shape in (
        "**Two-dimensional tables.**",
        "**Rolling buffers.**",
        "**Accumulators updated through a user higher-order function.**",
        "**Collections passed to helpers.**",
    ):
        assert shape in section, shape


def test_runtime_semantics_argument_alias_is_current_behaviour():
    """The documented argument-passing alias (proposal 0019, Unresolved
    Question 7) is what the interpreter does; a runtime fix must update the
    page. The soundness tests pin the compiled backends."""
    import textwrap

    import geno

    section = _section(
        "docs/reference/runtime-semantics.md", "## Effects and local state"
    )
    (snippet,) = re.findall(r"```geno\n(.*?)```", section, re.S)
    code = textwrap.dedent(snippet)

    assert _function_effects(code, "runtime-semantics#effects")["main"] == frozenset(
        {"io"}
    )
    result = geno.run(code, config=geno.RunConfig(capabilities=["print"]))
    assert result.ok, [d.message for d in result.diagnostics]
    assert result.output == "7\n"
    assert "This prints `7` on every backend" in section
    # The two shapes compiled JavaScript used to diverge on (#241, #242) are
    # documented as agreeing; test_write_path_parity runs them on each backend.
    prose = " ".join(section.split())
    assert "does not alias on any backend" in prose
    assert "**A `for` loop over an `Array` iterates a snapshot.**" in prose
