"""
Seeded Mistakes
===============

How Geno's diagnostics handle the mistakes language models make.

Each mistake in ``MISTAKES`` is a habit carried over from another language
(``let mut`` from Rust, ``True`` and ``elif`` from Python, ``&&`` from C)
or a slip any author makes (a transposed letter). It is seeded into the
canonical Geno solution of every benchmark problem at the first place it
applies, and the mutant is run through ``geno.fix``:

- *rejected*: ``geno check`` reports an error (a mutant that still checks
  is reported as *accepted* and left out of the rates below);
- *located*: the first error is on the line the mistake was seeded on;
- *fixable*: an error carries a fix;
- *repaired*: after ``geno.fix`` the program checks and passes every
  visible and hidden test of the problem;
- *misrepaired*: the fixed program checks but fails a test. This is the
  outcome a fix must never have, so it is reported separately.

The result needs no model and no API key, so anyone can reproduce it with
``python3 scripts/seeded_mistakes.py``. It measures the repair path, not
how often models make each mistake.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from benchmark.runner import BenchmarkRunner
from benchmark.schema import Problem
from geno.lexer import Lexer, LexerError
from geno.tokens import Token, TokenType

# A mutation returns the mutated source and the 1-based line it changed, or
# None when the solution has no place for the mistake.
Mutation = Callable[[str, list[Token]], tuple[str, int] | None]

_TYPE_NAMES = {"Int", "Float", "Bool", "String", "List", "Option", "Map", "Result"}


@dataclass(frozen=True)
class Mistake:
    """A mistake, the habit it comes from, and how to seed it."""

    name: str
    habit: str
    example: str
    mutate: Mutation


@dataclass
class Outcome:
    """What happened to one seeded mutant."""

    problem_id: str
    mistake: str
    line: int
    rejected: bool = False
    located: bool = False
    fixable: bool = False
    repaired: bool = False
    misrepaired: bool = False
    first_error: str = ""


@dataclass
class MistakeSummary:
    """Totals for one mistake over every problem it applied to."""

    mistake: Mistake
    seeded: int = 0
    accepted: int = 0
    rejected: int = 0
    located: int = 0
    fixable: int = 0
    repaired: int = 0
    misrepaired: int = 0
    outcomes: list[Outcome] = field(default_factory=list)


def _offset(source: str, line: int, column: int) -> int:
    lines = source.split("\n")
    return sum(len(text) + 1 for text in lines[: line - 1]) + column - 1


def _replace(source: str, token: Token, length: int, text: str) -> str:
    start = _offset(source, token.location.line, token.location.column)
    return source[:start] + text + source[start + length :]


def _code_tokens(tokens: list[Token]) -> list[Token]:
    """The tokens without line breaks; the list still ends with EOF."""
    return [t for t in tokens if t.type is not TokenType.NEWLINE]


def _last_on_line(tokens: list[Token], index: int) -> bool:
    following = tokens[index + 1]
    return (
        following.type is TokenType.EOF
        or following.location.line > tokens[index].location.line
    )


def _swap_last_two(name: str) -> str | None:
    """``name`` with a transposition near its end, or None if it is too short."""
    for i in range(len(name) - 2, 0, -1):
        if name[i] != name[i + 1]:
            return name[:i] + name[i + 1] + name[i] + name[i + 2 :]
    return None


def _first(
    tokens: list[Token], predicate: Callable[[int, Token], bool]
) -> tuple[int, Token] | None:
    for index, token in enumerate(tokens):
        if predicate(index, token):
            return index, token
    return None


def _keyword_swap(token_type: TokenType, old: str, new: str) -> Mutation:
    def mutate(source: str, tokens: list[Token]) -> tuple[str, int] | None:
        found = _first(tokens, lambda _i, t: t.type is token_type)
        if found is None:
            return None
        _index, token = found
        return _replace(source, token, len(old), new), token.location.line

    return mutate


def _bool_capitalized(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    found = _first(tokens, lambda _i, t: t.type in (TokenType.TRUE, TokenType.FALSE))
    if found is None:
        return None
    _index, token = found
    word = "true" if token.type is TokenType.TRUE else "false"
    return _replace(source, token, len(word), word.title()), token.location.line


def _plus_plus(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    strings = (TokenType.STRING, TokenType.FSTRING)

    def concatenates(index: int, token: Token) -> bool:
        return (
            token.type is TokenType.PLUS
            and 0 < index < len(tokens) - 1
            and (tokens[index - 1].type in strings or tokens[index + 1].type in strings)
        )

    found = _first(tokens, concatenates)
    if found is None:
        return None
    _index, token = found
    return _replace(source, token, 1, "++"), token.location.line


def _drop_clause_keyword(token_type: TokenType, word: str) -> Mutation:
    def mutate(source: str, tokens: list[Token]) -> tuple[str, int] | None:
        found = _first(
            tokens, lambda i, t: t.type is token_type and _last_on_line(tokens, i)
        )
        if found is None:
            return None
        _index, token = found
        start = _offset(source, token.location.line, token.location.column)
        return source[: start - 1] + source[start + len(word) :], token.location.line

    return mutate


def _elif(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    found = _first(
        tokens,
        lambda i, t: (
            t.type is TokenType.ELSE
            and i + 1 < len(tokens)
            and tokens[i + 1].type is TokenType.IF
            and tokens[i + 1].location.line == t.location.line
        ),
    )
    if found is None:
        return None
    index, token = found
    length = tokens[index + 1].location.column + 2 - token.location.column
    return _replace(source, token, length, "elif"), token.location.line


def _bang(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    found = _first(tokens, lambda _i, t: t.type is TokenType.NOT)
    if found is None:
        return None
    index, token = found
    following = tokens[index + 1]
    length = _offset(
        source, following.location.line, following.location.column
    ) - _offset(source, token.location.line, token.location.column)
    return _replace(source, token, length, "!"), token.location.line


def _bare_end(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    blocks = {TokenType.IF: "if", TokenType.FOR: "for", TokenType.WHILE: "while"}
    found = _first(
        tokens,
        lambda i, t: (
            t.type is TokenType.END
            and i + 1 < len(tokens)
            and tokens[i + 1].type in blocks
            and tokens[i + 1].location.line == t.location.line
        ),
    )
    if found is None:
        return None
    index, token = found
    keyword = blocks[tokens[index + 1].type]
    return (
        _replace(source, token, len("end ") + len(keyword), "end"),
        token.location.line,
    )


def _calls(tokens: list[Token]) -> list[tuple[int, bool]]:
    """Each call's ``(index of its name, is a method call)``."""
    calls = []
    for index, token in enumerate(tokens[:-1]):
        if (
            token.type is TokenType.IDENTIFIER
            and tokens[index + 1].type is TokenType.LPAREN
            and (index == 0 or tokens[index - 1].type is not TokenType.FUNC)
        ):
            calls.append((index, index > 0 and tokens[index - 1].type is TokenType.DOT))
    return calls


def _positional(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    """Drop the argument names of the first call that needs them."""
    for index, _is_method in _calls(tokens):
        labels: list[Token] = []
        depth, position = 0, index + 1
        at_argument_start = True
        while position < len(tokens):
            token = tokens[position]
            if token.type in (TokenType.LPAREN, TokenType.LBRACKET, TokenType.LBRACE):
                depth += 1
            elif token.type in (
                TokenType.RPAREN,
                TokenType.RBRACKET,
                TokenType.RBRACE,
            ):
                depth -= 1
                if depth == 0:
                    break
            if depth == 1 and at_argument_start and token.type is TokenType.IDENTIFIER:
                if tokens[position + 1].type is TokenType.COLON:
                    labels.append(token)
            at_argument_start = depth == 1 and token.type in (
                TokenType.LPAREN,
                TokenType.COMMA,
            )
            position += 1
        if len(labels) < 3:
            continue
        mutated = source
        for label in reversed(labels):
            start = _offset(source, label.location.line, label.location.column)
            colon = source.index(":", start)
            end = colon + 1
            while source[end] == " ":
                end += 1
            mutated = mutated[:start] + mutated[end:]
        return mutated, tokens[index].location.line
    return None


def _rename_call(old: str, new: str) -> Mutation:
    """Call ``new``, another language's name, where the solution calls ``old``."""

    def mutate(source: str, tokens: list[Token]) -> tuple[str, int] | None:
        for index, _is_method in _calls(tokens):
            token = tokens[index]
            if token.value == old:
                return _replace(source, token, len(old), new), token.location.line
        return None

    return mutate


def _null(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    found = _first(
        tokens,
        lambda i, t: (
            t.type is TokenType.TYPE_IDENTIFIER
            and t.value == "None"
            and not (i > 0 and tokens[i - 1].type is TokenType.BAR)
        ),
    )
    if found is None:
        return None
    _index, token = found
    return _replace(source, token, len("None"), "null"), token.location.line


def _defined_names(tokens: list[Token]) -> set[str]:
    return {
        tokens[i + 1].value
        for i, t in enumerate(tokens[:-1])
        if t.type is TokenType.FUNC and tokens[i + 1].type is TokenType.IDENTIFIER
    }


def _typo_in_call(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    defined = _defined_names(tokens)
    for index, _is_method in _calls(tokens):
        token = tokens[index]
        name = str(token.value)
        typo = _swap_last_two(name)
        if len(name) >= 5 and name not in defined and typo is not None:
            return _replace(source, token, len(name), typo), token.location.line
    return None


def _typo_in_variable(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    """Misspell the first use of a parameter in a function body."""
    parameters: set[str] = set()
    in_signature = False
    for index, token in enumerate(tokens):
        if token.type is TokenType.FUNC:
            in_signature = True
        elif in_signature and token.type is TokenType.ARROW:
            in_signature = False
        elif (
            in_signature
            and token.type is TokenType.IDENTIFIER
            and tokens[index + 1].type is TokenType.COLON
        ):
            parameters.add(str(token.value))
        elif (
            not in_signature
            and token.type is TokenType.IDENTIFIER
            and token.value in parameters
            and tokens[index - 1].type is not TokenType.DOT
            and tokens[index + 1].type is not TokenType.COLON
            and not _in_example(tokens, index)
        ):
            name = str(token.value)
            typo = _swap_last_two(name)
            if len(name) >= 3 and typo is not None and typo not in parameters:
                return _replace(source, token, len(name), typo), token.location.line
    return None


def _in_example(tokens: list[Token], index: int) -> bool:
    line = tokens[index].location.line
    return any(t.type is TokenType.EXAMPLE and t.location.line == line for t in tokens)


def _typo_in_type(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    found = _first(
        tokens,
        lambda i, t: (
            t.type is TokenType.TYPE_IDENTIFIER
            and t.value in _TYPE_NAMES
            and i > 0
            and tokens[i - 1].type in (TokenType.COLON, TokenType.ARROW)
        ),
    )
    if found is None:
        return None
    _index, token = found
    name = str(token.value)
    typo = _swap_last_two(name)
    if typo is None:
        return None
    return _replace(source, token, len(name), typo), token.location.line


def _colon_for_then(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    found = _first(
        tokens, lambda i, t: t.type is TokenType.THEN and _last_on_line(tokens, i)
    )
    if found is None:
        return None
    _index, token = found
    start = _offset(source, token.location.line, token.location.column)
    return source[: start - 1] + ":" + source[start + 4 :], token.location.line


def _else_colon(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    found = _first(
        tokens, lambda i, t: t.type is TokenType.ELSE and _last_on_line(tokens, i)
    )
    if found is None:
        return None
    _index, token = found
    return _replace(source, token, len("else"), "else:"), token.location.line


def _python_lambda(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    """Write the first one-line ``fn(a, b) -> body`` as ``lambda a, b: body``."""
    for index, token in enumerate(tokens):
        if token.type is not TokenType.FN or tokens[index + 1].type is not (
            TokenType.LPAREN
        ):
            continue
        names, depth, position = [], 0, index + 1
        while True:
            current = tokens[position]
            if current.type in (TokenType.LPAREN, TokenType.LBRACKET):
                depth += 1
            elif current.type in (TokenType.RPAREN, TokenType.RBRACKET):
                depth -= 1
                if depth == 0:
                    break
            elif (
                depth == 1
                and current.type is TokenType.IDENTIFIER
                and tokens[position - 1].type in (TokenType.LPAREN, TokenType.COMMA)
            ):
                names.append(str(current.value))
            position += 1
        arrow = tokens[position + 1]
        if arrow.type is not TokenType.ARROW or _last_on_line(tokens, position + 1):
            continue
        start = _offset(source, token.location.line, token.location.column)
        stop = _offset(source, arrow.location.line, arrow.location.column) + 2
        lambda_ = f"lambda {', '.join(names)}:"
        return source[:start] + lambda_ + source[stop:], token.location.line
    return None


def _fat_arrow(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    """Write the first match arm's ``->`` as ``=>``."""
    starts = {}
    for token in tokens:
        starts.setdefault(token.location.line, token)
    found = _first(
        tokens,
        lambda _i, t: (
            t.type is TokenType.ARROW and starts[t.location.line].type is TokenType.BAR
        ),
    )
    if found is None:
        return None
    _index, token = found
    return _replace(source, token, 2, "=>"), token.location.line


def _hash_comment(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    for number, text in enumerate(source.split("\n"), start=1):
        stripped = text.lstrip()
        if stripped.startswith("//") and not stripped.startswith("///"):
            indent = len(text) - len(stripped)
            line_start = _offset(source, number, 1)
            start = line_start + indent
            return source[:start] + "#" + source[start + 2 :], number
    return None


def _implicit_return(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    """Drop the ``return`` of a top-level function's final statement."""
    for index, token in enumerate(tokens):
        if token.type is not TokenType.RETURN or token.location.column != 5:
            continue
        following = index + 1
        while tokens[following].location.line == token.location.line:
            following += 1
        if (
            tokens[following].type is TokenType.END
            and tokens[following + 1].type is TokenType.FUNC
            and following > index + 1
        ):
            return _replace(source, token, len("return "), ""), token.location.line
    return None


def _no_return_type(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    for index, token in enumerate(tokens):
        if token.type is not TokenType.FUNC:
            continue
        depth, position = 0, index + 2
        while True:
            current = tokens[position]
            if current.type is TokenType.LPAREN:
                depth += 1
            elif current.type is TokenType.RPAREN:
                depth -= 1
                if depth == 0:
                    break
            position += 1
        arrow = tokens[position + 1]
        if arrow.type is not TokenType.ARROW:
            continue
        line = source.split("\n")[arrow.location.line - 1]
        start = _offset(source, arrow.location.line, arrow.location.column) - 1
        stop = _offset(source, arrow.location.line, len(line) + 1)
        return source[:start] + source[stop:], arrow.location.line
    return None


def _no_examples(source: str, tokens: list[Token]) -> tuple[str, int] | None:
    lines = source.split("\n")
    examples = [
        t.location.line
        for t in tokens
        if t.type is TokenType.EXAMPLE and t.location.column == 5
    ]
    if not examples:
        return None
    first = examples[0]
    function_line = max(
        t.location.line
        for t in tokens
        if t.type is TokenType.FUNC and t.location.line < first
    )
    removed = {
        line
        for line in examples
        if max(
            t.location.line
            for t in tokens
            if t.type is TokenType.FUNC and t.location.line < line
        )
        == function_line
    }
    kept = [text for number, text in enumerate(lines, 1) if number not in removed]
    return "\n".join(kept), function_line


MISTAKES: tuple[Mistake, ...] = (
    Mistake(
        "let mut",
        "Rust",
        "let mut total = 0",
        _keyword_swap(TokenType.VAR, "var", "let mut"),
    ),
    Mistake("True / False", "Python", "return True", _bool_capitalized),
    Mistake("++ concatenation", "Haskell, Elm", 'name ++ "!"', _plus_plus),
    Mistake(
        "no then", "Rust, Go", "if x > 0", _drop_clause_keyword(TokenType.THEN, "then")
    ),
    Mistake(
        "no do", "Rust, Go", "for x in xs", _drop_clause_keyword(TokenType.DO, "do")
    ),
    Mistake("colon block", "Python", "if x > 0:", _colon_for_then),
    Mistake("else:", "Python", "else:", _else_colon),
    Mistake("bare end", "Ruby, Lua", "end", _bare_end),
    Mistake("&&", "C, JavaScript", "a && b", _keyword_swap(TokenType.AND, "and", "&&")),
    Mistake("||", "C, JavaScript", "a || b", _keyword_swap(TokenType.OR, "or", "||")),
    Mistake("!", "C, JavaScript", "!done", _bang),
    Mistake("=> arm", "Rust, Scala", "| None => 0", _fat_arrow),
    Mistake("lambda", "Python", "lambda x: x * 2", _python_lambda),
    Mistake("# comment", "Python, Ruby", "# note", _hash_comment),
    Mistake("null", "Java, JavaScript", "return null", _null),
    Mistake("implicit return", "Rust, Ruby", "total", _implicit_return),
    Mistake("positional arguments", "every language", "slice(xs, 0, 2)", _positional),
    Mistake("len", "Python", "len(xs)", _rename_call("length", "len")),
    Mistake("str", "Python", "str(n)", _rename_call("to_string", "str")),
    Mistake("strip", "Python", "s.strip()", _rename_call("trim", "strip")),
    Mistake(
        "startswith",
        "Python",
        "s.startswith(p)",
        _rename_call("starts_with", "startswith"),
    ),
    Mistake("misspelled function", "a slip", "lenght(xs)", _typo_in_call),
    Mistake("misspelled variable", "a slip", "nusm[0]", _typo_in_variable),
    Mistake("misspelled type", "a slip", "Strign", _typo_in_type),
    Mistake("no return type", "Python", "func f(x: Int)", _no_return_type),
    Mistake("no examples", "every language", "(no example clause)", _no_examples),
)


def seed(mistake: Mistake, source: str) -> tuple[str, int] | None:
    """``source`` with ``mistake`` seeded at its first place, and the line."""
    try:
        tokens = Lexer(source, "<solution>").tokenize()
    except LexerError:
        return None
    return mistake.mutate(source, _code_tokens(tokens))


def _error_line(diagnostic_location: object) -> int:
    return int(getattr(diagnostic_location, "line", 0) or 0)


def evaluate(
    problems: Sequence[Problem],
    mistakes: Sequence[Mistake] = MISTAKES,
    runner: BenchmarkRunner | None = None,
) -> list[MistakeSummary]:
    """Seed every mistake into every problem's solution and try to repair it."""
    import geno
    from geno.diagnostics import Severity

    runner = runner or BenchmarkRunner()
    summaries = []
    for mistake in mistakes:
        summary = MistakeSummary(mistake)
        for problem in problems:
            seeded = seed(mistake, problem.geno_solution)
            if seeded is None:
                continue
            mutant, line = seeded
            summary.seeded += 1
            checked = geno.check(mutant)
            errors = [d for d in checked.diagnostics if d.severity is Severity.ERROR]
            outcome = Outcome(problem.id, mistake.name, line)
            summary.outcomes.append(outcome)
            if not errors:
                summary.accepted += 1
                continue
            outcome.rejected = True
            outcome.first_error = f"[{errors[0].code.value}] {errors[0].message}"
            outcome.located = _error_line(errors[0].location) == line
            outcome.fixable = any(d.fixes for d in errors)
            if outcome.fixable:
                fixed = geno.fix(mutant)
                if fixed.check.ok:
                    passed = runner.evaluate_geno(problem, fixed.source).all_passed
                    outcome.repaired = passed
                    outcome.misrepaired = not passed
            summary.rejected += 1
            summary.located += outcome.located
            summary.fixable += outcome.fixable
            summary.repaired += outcome.repaired
            summary.misrepaired += outcome.misrepaired
        summaries.append(summary)
    return summaries


def _pct(part: int, whole: int) -> str:
    """A percentage that never rounds a shortfall up to 100%."""
    if not whole:
        return "n/a"
    if part == whole:
        return "100%"
    return f"{min(100 * part / whole, 99.9):.1f}%"


def render_report(summaries: Sequence[MistakeSummary], problem_count: int) -> str:
    """The Markdown results page for ``summaries``."""
    rejected = sum(s.rejected for s in summaries)
    totals = {
        "seeded": sum(s.seeded for s in summaries),
        "accepted": sum(s.accepted for s in summaries),
        "located": sum(s.located for s in summaries),
        "fixable": sum(s.fixable for s in summaries),
        "repaired": sum(s.repaired for s in summaries),
        "misrepaired": sum(s.misrepaired for s in summaries),
    }
    lines = [
        "# Seeded Mistakes: Diagnostics and Fixes",
        "",
        "<!-- Generated by `python3 scripts/seeded_mistakes.py --write`. Do not",
        "edit by hand; geno/tests/test_seeded_mistakes.py checks it is current. -->",
        "",
        "Each mistake is a habit models carry over from another language, or a",
        "slip any author makes. It is seeded at its first place in the canonical",
        f"Geno solution of each of the {problem_count} benchmark problems, and the",
        "mutant goes through `geno check` and then `geno.fix` (what",
        "`geno check --fix` applies). No model or API key is involved: the",
        "result measures the repair path, not how often models make each",
        "mistake. See `benchmark/seeded_mistakes.py` for the definitions.",
        "",
        "- **Rejected**: `geno check` reports an error. A mutant that still",
        "  checks is counted as accepted and left out of the other columns.",
        "- **Located**: the first error is on the line the mistake is on.",
        "- **Fixable**: an error carries a fix.",
        "- **Repaired**: after the fixes, the program checks and passes every",
        "  visible and hidden test of the problem.",
        "- **Misrepaired**: the fixed program checks but fails a test.",
        "",
        "## Summary",
        "",
        f"- Mutants: {totals['seeded']} ({totals['accepted']} accepted as valid Geno)",
        f"- Rejected: {rejected}",
        f"- Located on the mistake's line: {totals['located']} "
        f"({_pct(totals['located'], rejected)})",
        f"- Carry a fix: {totals['fixable']} ({_pct(totals['fixable'], rejected)})",
        f"- Repaired by `geno check --fix`: {totals['repaired']} "
        f"({_pct(totals['repaired'], rejected)})",
        f"- Misrepaired: {totals['misrepaired']}",
        "",
        "## By Mistake",
        "",
        "| Mistake | Habit from | Example | Mutants | Rejected | Located | Fixable | Repaired | Misrepaired |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.mistake.name} | {s.mistake.habit} | `{s.mistake.example}` | "
            f"{s.seeded} | {s.rejected} | {_pct(s.located, s.rejected)} | "
            f"{_pct(s.fixable, s.rejected)} | {_pct(s.repaired, s.rejected)} | "
            f"{s.misrepaired} |"
        )
    lines += ["", "## Not Repaired", ""]
    unrepaired = [
        (s, [o for o in s.outcomes if o.rejected and not o.repaired]) for s in summaries
    ]
    unrepaired = [(s, outcomes) for s, outcomes in unrepaired if outcomes]
    if not unrepaired:
        lines.append("Every rejected mutant was repaired.")
    else:
        lines += [
            "Each mistake with a mutant the fixes did not repair, the problems",
            "it was seeded in, and the first error of the first such mutant.",
            "",
            "| Mistake | Problems | First error |",
            "|---|---|---|",
        ]
        for summary, outcomes in unrepaired:
            problems = ", ".join(
                o.problem_id + (" (misrepaired)" if o.misrepaired else "")
                for o in outcomes
            )
            if len(outcomes) == summary.rejected:
                problems = f"all {len(outcomes)}"
            message = re.sub(r"\s+", " ", outcomes[0].first_error).replace("|", "\\|")
            lines.append(f"| {summary.mistake.name} | {problems} | {message} |")
    return "\n".join(lines) + "\n"
