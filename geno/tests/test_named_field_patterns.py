"""Constructor patterns that name their fields: `Cfg(port: p, ..)` (proposal 0023).

Fields may come in any order, each at most once; `..` ignores the ones that
are not named. The type checker rewrites the pattern to the positional one it
means, so exhaustiveness and every backend treat it as an ordinary
constructor pattern.
"""

import pytest

import geno
from geno.ast_nodes import ConstructorPattern, VariablePattern, WildcardPattern
from geno.formatter import format_source
from geno.interpreter import Interpreter
from geno.js_compiler import compile_to_js
from geno.parser import ParseError, parse
from geno.tests._script_runner import run_node_code
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)
from geno.typechecker import TypeChecker

BACKENDS = [
    _interpreter_output,
    _compiled_python_output,
    pytest.param(
        _compiled_js_output,
        marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
    ),
]

TYPES = """
type Cfg = Cfg(host: String, port: Int, debug: Bool)
type Shape = Circle(r: Int) | Rect(w: Int, h: Int)
type Tree = Leaf | Node(left: Tree, value: Int, right: Tree)
"""

PROGRAM = (
    TYPES
    + """
func describe(c: Cfg) -> String
    example Cfg(host: "a", port: 80, debug: true) -> "debug a"
    example Cfg(host: "b", port: 443, debug: false) -> "secure"
    example Cfg(host: "c", port: 1, debug: false) -> "c:1"
    match c with
        | Cfg(debug: true, host: h, ..) -> return f"debug {h}"
        | Cfg(port: 443, ..) -> return "secure"
        | Cfg(debug: _, port: p, host: h) -> return f"{h}:{p}"
    end match
end func

func area(s: Shape) -> Int
    example Rect(w: 2, h: 3) -> 6
    example Circle(r: 1) -> 3
    match s with
        | Rect(h: h, w: w) -> return w * h
        | Circle(r: r) -> return 3 * r * r
    end match
end func

func leftmost(t: Tree) -> Int
    example Leaf -> 0
    example Node(left: Node(left: Leaf, value: 1, right: Leaf), value: 2, right: Leaf) -> 1
    match t with
        | Node(left: Node(..), ..) -> return leftmost(node_left(t))
        | Node(value: v, ..) -> return v
        | Leaf -> return 0
    end match
end func

func node_left(t: Tree) -> Tree
    example Leaf -> Leaf
    return if let Node(left: l, ..) = t then l else Leaf end if
end func

func main() -> Unit with io
    let c = Cfg(host: "x", port: 8080, debug: false)
    print(describe(c))
    print(describe(Cfg(host: "y", port: 443, debug: false)))
    print(describe(Cfg(host: "z", port: 1, debug: true)))
    print(area(Rect(w: 4, h: 5)))
    print(leftmost(Node(left: Node(left: Leaf, value: 7, right: Leaf), value: 2, right: Leaf)))
    if let Cfg(host: h, ..) = c then
        print(h)
    end if
    for item in [c, Cfg(host: "y", port: 443, debug: true)] do
        match item with
            | Cfg(debug: true, ..) | Cfg(port: 1, ..) -> print("flagged")
            | Cfg(..) -> print("plain")
        end match
    end for
    print([p for Cfg(port: p, ..) in [c] if p > 0] == [8080])
end func
"""
)

EXPECTED = "x:8080\nsecure\ndebug z\n20\n7\nx\nplain\nflagged\n"


def _messages(source: str) -> list[str]:
    return [d.message for d in geno.check(source).diagnostics]


def _match_on_cfg(arm: str, fallback: bool = True) -> str:
    rest = "        | _ -> return 0\n" if fallback else ""
    return (
        TYPES
        + "func f(c: Cfg) -> Int\n"
        + '    example Cfg(host: "a", port: 1, debug: false) -> 0\n'
        + "    match c with\n"
        + f"        | {arm} -> return 1\n"
        + rest
        + "    end match\n"
        + "end func\n"
    )


# The comprehension line is checked separately: it needs a pattern in a loop
# binding, which is not part of this feature.
RUNNABLE = PROGRAM.replace(
    "    print([p for Cfg(port: p, ..) in [c] if p > 0] == [8080])\n", ""
)


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_matches_named_fields_the_same(backend):
    assert backend(RUNNABLE) == EXPECTED


def test_the_program_checks_and_its_examples_hold():
    assert _messages(RUNNABLE) == []


def test_the_parser_keeps_the_names_and_the_checker_orders_the_fields():
    program = parse(_match_on_cfg("Cfg(debug: d, host: h, ..)"))
    pattern = program.definitions[-1].body[0].arms[0].pattern
    assert isinstance(pattern, ConstructorPattern)
    assert pattern.field_names == ["debug", "host"]
    assert pattern.ignores_rest

    TypeChecker().check_program(program)

    assert pattern.field_names is None and not pattern.ignores_rest
    host, port, debug = pattern.subpatterns
    assert isinstance(host, VariablePattern) and host.name == "h"
    assert isinstance(port, WildcardPattern)
    assert isinstance(debug, VariablePattern) and debug.name == "d"


def test_exhaustiveness_treats_an_ignored_field_as_a_wildcard():
    covered = _match_on_cfg("Cfg(..)", fallback=False)
    assert _messages(covered) == []
    partial = _match_on_cfg("Cfg(debug: true, ..)", fallback=False)
    assert any("exhaustive" in message.lower() for message in _messages(partial))


@pytest.mark.parametrize(
    ("arm", "message"),
    [
        (
            "Cfg(prt: p, ..)",
            "Constructor Cfg has no field 'prt'; its fields are: host, port, debug",
        ),
        ("Cfg(port: p)", "This Cfg pattern leaves out host, debug"),
        ('Cfg(port: "x", ..)', "Pattern type mismatch: expected Int, got String"),
    ],
)
def test_names_that_do_not_fit_the_constructor_are_type_errors(arm, message):
    assert any(message in found for found in _messages(_match_on_cfg(arm)))


@pytest.mark.parametrize(
    ("arm", "message"),
    [
        ("Cfg(port: p, port: q, ..)", "Field 'port' appears twice"),
        ("Cfg(.., port: p)", "'..' ignores the remaining fields, so it comes last"),
        ("Cfg(h, port: p, d)", "names all of its fields or none"),
        ("Cfg(port: p, h)", "names all of its fields or none"),
        ("Cfg(h, ..)", "names all of its fields or none"),
    ],
)
def test_malformed_named_patterns_are_parse_errors(arm, message):
    with pytest.raises(ParseError, match=message):
        parse(_match_on_cfg(arm))


def test_three_dots_are_accepted_for_the_rest_of_the_fields():
    assert _messages(_match_on_cfg("Cfg(port: 1, ...)")) == []


def test_positional_patterns_are_unchanged():
    assert _messages(_match_on_cfg("Cfg(h, 1, d)")) == []
    assert any(
        "expects 3 fields, got 2" in message
        for message in _messages(_match_on_cfg("Cfg(h, 1)"))
    )


def test_a_program_that_skips_type_checking_still_matches_by_name():
    """The backends read the names themselves when the checker did not run."""
    source = (
        TYPES
        + "func main() -> Unit with io\n"
        + '    match Cfg("a", 9, true) with\n'
        + "        | Cfg(debug: false, ..) -> print(0)\n"
        + "        | Cfg(port: p, ..) -> print(p)\n"
        + "    end match\n"
        + "end func\n"
    )
    program = parse(source)
    interpreter = Interpreter(check_examples=False)
    interpreter.run(program)
    assert interpreter.get_output() == "9\n"
    if HAS_NODE:
        compiled = compile_to_js(source, typecheck=False)
        result = run_node_code(compiled, args=("--cap", "print"))
        assert result.stdout == "9\n", result.stderr


def test_the_formatter_keeps_the_rest_marker_spaced_after_its_comma():
    source = _match_on_cfg("Cfg(port: p, ..)").lstrip("\n")
    assert format_source(source) == source
    assert format_source(source.replace("p, ..)", "p,..)")) == source
    bare = _match_on_cfg("Cfg(..)").lstrip("\n")
    assert format_source(bare) == bare
