"""Runtime failure diagnostics (draft proposal 0018).

This file covers the contract-failure half that every backend shares (the
clause text the parser captures, the bounded value renderers, the message
grammar and the structured fields of ``ContractViolationError``) and the
interpreter's Geno stacks: frame names and positions, callbacks, lambdas,
async functions, the ``<example>`` origin, limits, the 10/10 bound, the
report text and its budgeted fitting. The three-backend message parity lives
in ``test_backend_parity.py``.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from geno import api as geno_api
from geno import builtins as geno_builtins
from geno import runtime_report
from geno.ast_nodes import EnsuresClause, FunctionDef, RequiresClause
from geno.cli._util import runtime_report_lines
from geno.diagnostics import ErrorCode
from geno.formatter import format_source
from geno.interpreter import Interpreter, interpret
from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE
from geno.lexer import Lexer
from geno.parser import Parser, parse
from geno.parser_base import contract_clause_text
from geno.property_check import ensures_clause_text
from geno.sandbox import SandboxError
from geno.tests._script_runner import run_node_code
from geno.tokens import SourceLocation
from geno.values import (
    ArrayValue,
    ConstructorValue,
    ContractViolationError,
    GenoRuntimeError,
    MutableMapValue,
    RuntimeLimitError,
    SetValue,
    VecValue,
)

HAS_NODE = shutil.which("node") is not None
GENO_DIR = Path(__file__).resolve().parents[1]
RUNTIME_SUPPORT = GENO_DIR / "_runtime_support.py"
OLD_CONTRACT_WORDING = (
    "Precondition failed for",
    "Postcondition failed for",
    "requires clause",
    "ensures clause",
    "evaluated to false",
)
# The 0.6 messages named the function as `... for <name>:`.
OLD_FOR_NAME = re.compile(r'panics\s+"[^"]*\bfor [A-Za-z_][\w.]*:')


def _function(source: str, name: str) -> FunctionDef:
    for definition in parse(source).definitions:
        if isinstance(definition, FunctionDef) and definition.name == name:
            return definition
    raise AssertionError(f"no function {name}")


def _violation(source: str) -> ContractViolationError:
    with pytest.raises(ContractViolationError) as exc_info:
        interpret(source)
    return exc_info.value


def _prelude_namespace() -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    exec(RUNTIME_SUPPORT.read_text(), namespace)
    return namespace


# =============================================================================
# Clause text
# =============================================================================


class TestClauseText:
    def test_a_single_line_clause_is_its_condition(self) -> None:
        definition = _function(
            "func safe_div(a: Int, b: Int) -> Int\n"
            "    requires b != 0\n"
            "    ensures result * b <= a\n"
            "    example 6, 3 -> 2\n"
            "    return a / b\n"
            "end func\n",
            "safe_div",
        )

        assert definition.specs.requires[0].source_text == "b != 0"
        assert definition.specs.ensures[0].source_text == "result * b <= a"

    def test_a_multi_line_clause_loses_comments_and_line_breaks(self) -> None:
        definition = _function(
            "func bounded(lo: Int, hi: Int, x: Int) -> Int\n"
            "    requires lo <= x and // lower bound\n"
            "        x <= hi /* upper\n"
            "        bound */\n"
            "    example 0, 10, 5 -> 5\n"
            "    return x\n"
            "end func\n",
            "bounded",
        )

        assert definition.specs.requires[0].source_text == "lo <= x and x <= hi"

    def test_comment_markers_inside_strings_are_kept(self) -> None:
        definition = _function(
            "func path(s: String) -> String\n"
            '    requires s != "a//b" and s != "/* x */" // real comment\n'
            '    example "c" -> "c"\n'
            "    return s\n"
            "end func\n",
            "path",
        )

        assert definition.specs.requires[0].source_text == (
            's != "a//b" and s != "/* x */"'
        )

    def test_spacing_inside_one_line_is_kept_as_written(self) -> None:
        assert contract_clause_text("  a  +  b > 0  ") == "a  +  b > 0"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("a/* c */b", "a b"),
            ("a // trailing", "a"),
            ('"x\\"//y" == s', '"x\\"//y" == s'),
            ('"""a//b""" == s', '"""a//b""" == s'),
            ("x / y > 1", "x / y > 1"),
            ("// only a comment", None),
            ("", None),
        ],
    )
    def test_comment_removal(self, raw: str, expected: str | None) -> None:
        assert contract_clause_text(raw) == expected

    def test_a_long_clause_is_capped(self) -> None:
        condition = " and ".join(f"x != {n}" for n in range(60))
        definition = _function(
            "func f(x: Int) -> Int\n"
            f"    requires {condition}\n"
            "    example 100 -> 100\n"
            "    return x\n"
            "end func\n",
            "f",
        )

        text = definition.specs.requires[0].source_text
        assert text is not None
        assert len(text) == runtime_report.CLAUSE_TEXT_LIMIT == 200
        assert text == condition[:197] + "..."

    def test_the_cap_counts_code_points(self) -> None:
        text = "😀" * 250

        assert contract_clause_text(text) == "😀" * 197 + "..."

    def test_without_source_the_text_is_unknown(self) -> None:
        source = (
            "func f(x: Int) -> Int\n"
            "    requires x > 0\n"
            "    example 1 -> 1\n"
            "    return x\n"
            "end func\n"
        )
        program = Parser(Lexer(source).tokenize()).parse_program()
        definition = program.definitions[0]

        assert isinstance(definition, FunctionDef)
        assert definition.specs.requires[0].source_text is None

    def test_the_text_takes_no_part_in_equality(self) -> None:
        location = None
        condition = parse(
            "func f(x: Int) -> Int\n    requires x > 0\n    example 1 -> 1\n"
            "    return x\nend func\n"
        ).definitions[0]
        assert isinstance(condition, FunctionDef)
        expr = condition.specs.requires[0].condition

        assert RequiresClause(location, expr, "x > 0") == RequiresClause(
            location, expr, "x  >  0"
        )
        assert EnsuresClause(location, expr, "a") == EnsuresClause(location, expr)
        assert "source_text" not in repr(RequiresClause(location, expr, "x > 0"))

    def test_geno_fmt_round_trips_a_respaced_clause(self) -> None:
        source = (
            "func f(x: Int) -> Int\n"
            "    requires   x  >  0 // keep\n"
            "    ensures result   ==   x\n"
            "    example 1 -> 1\n"
            "    return x\n"
            "end func\n"
        )

        formatted = format_source(source)

        assert "requires x > 0 // keep" in formatted
        assert format_source(formatted) == formatted

    def test_property_checks_read_the_whole_multi_line_clause(self) -> None:
        source = (
            "func f(x: Int) -> Int\n"
            "    ensures result >= 0 and\n"
            "        result <= x\n"
            "    example 1 -> 1\n"
            "    return x\n"
            "end func\n"
        )
        definition = _function(source, "f")

        assert ensures_clause_text(source, definition, 0) == (
            "result >= 0 and result <= x"
        )

    def test_every_runtime_parse_site_passes_the_source(self) -> None:
        """Contracts quote the parser's text, so every parse whose program can
        run contracts must hand the parser its source (proposal 0018)."""
        runtime_modules = [
            "api.py",
            "compiler.py",
            "dependency_graph.py",
            "harness.py",
            "interpreter.py",
            "js_compiler.py",
            "module_resolver.py",
            "parser.py",
            "test_runner.py",
        ]
        # The REPL's expression wrappers (:type, :ast, evaluation) never
        # define a function, so only its definition paths are pinned.
        repl_functions = {"_execute", "_load_file"}
        missing: list[str] = []

        def check(tree: ast.AST, label: str) -> None:
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Parser"
                    and len(node.args) + len(node.keywords) < 2
                ):
                    missing.append(f"{label}:{node.lineno}")

        for name in runtime_modules:
            check(ast.parse((GENO_DIR / name).read_text()), name)
        repl = ast.parse((GENO_DIR / "repl.py").read_text())
        found = set()
        for node in ast.walk(repl):
            if isinstance(node, ast.FunctionDef) and node.name in repl_functions:
                found.add(node.name)
                check(node, f"repl.py:{node.name}")

        assert found == repl_functions
        assert missing == []


# =============================================================================
# Bounded rendering
# =============================================================================


def _vec(items: list[Any]) -> VecValue:
    value = VecValue()
    value._elements = list(items)
    return value


def _set(items: set[Any]) -> SetValue:
    value = SetValue()
    value._data = set(items)
    return value


def _mutable_map(items: dict[Any, Any]) -> MutableMapValue:
    value = MutableMapValue()
    value._data = dict(items)
    return value


def _sample_values() -> list[Any]:
    point = ConstructorValue("Point", {"x": 1.5, "y": -2})
    return [
        0,
        -7,
        2.5,
        float("inf"),
        True,
        None,
        "",
        "plain",
        'nested "quotes" and \\ backslash',
        "tab\tnew\nline\x01",
        "😀" * 40,
        "a" * 63,
        "a" * 64,
        "a" * 65,
        "x😀" * 70,
        list(range(100)),
        ["😀" * 10, "b" * 30, "c" * 30],
        (1, "two", 3.0),
        (1,),
        (),
        {"k": [1, 2], "😀": "v"},
        point,
        ConstructorValue("None", {}),
        ConstructorValue("Some", {"value": point}),
        [point] * 10,
        ArrayValue([1, 2, 3]),
        _vec(["a", "b"]),
        _set({3, 1, 2}),
        _mutable_map({"a": 1}),
        [[[[1, 2, 3]]], [["deep" * 20]]],
    ]


class TestBoundedRenderer:
    @pytest.mark.parametrize("limit", [0, 1, 3, 4, 10, 63, 64, 65, 200])
    def test_the_interpreter_renderer_equals_render_then_truncate(
        self, limit: int
    ) -> None:
        for value in _sample_values():
            full = geno_builtins.format_value(value)
            expected = runtime_report.truncate_display(full, limit)

            assert geno_builtins.format_value_bounded(value, limit) == expected, value

    @pytest.mark.parametrize("limit", [0, 1, 3, 4, 10, 63, 64, 65, 200])
    def test_the_python_prelude_renderer_equals_render_then_truncate(
        self, limit: int
    ) -> None:
        namespace = _prelude_namespace()
        bounded = namespace["_geno_format_bounded"]
        full_render = namespace["_geno_format"]
        samples: list[Any] = [
            0,
            2.5,
            True,
            None,
            "plain",
            'nested "quotes" \\',
            "tab\tnew\nline\x01",
            "😀" * 40,
            "a" * 65,
            "x😀" * 70,
            list(range(100)),
            ["😀" * 10, "b" * 30],
            (1, "two", 3.0),
            (1,),
            {"k": [1, 2], "😀": "v"},
            namespace["Some"]([1, "x"]),
            namespace["None_"],
            namespace["Ok"](2.0),
            namespace["_GenoArray"]([1, 2]),
            namespace["_GenoSet"]([3, 1, 2]),
            [[[["deep" * 20]]]],
        ]

        for value in samples:
            expected = runtime_report.truncate_display(
                full_render(value, _top_level=False), limit
            )

            assert bounded(value, limit) == expected, value

    def test_functions_render_alike_on_both_python_renderers(self) -> None:
        namespace = _prelude_namespace()

        assert namespace["_geno_format_bounded"](lambda x: x, 64) == "<function>"
        assert namespace["_geno_format_bounded"]([len], 64) == "[<function>]"

    def test_the_walk_stops_at_the_limit(self) -> None:
        """The cost is bounded by the limit, not by the value's size."""
        rendered: list[int] = []

        class Counted:
            def __str__(self) -> str:
                rendered.append(1)
                return "v"

        values = [Counted() for _ in range(10_000)]

        assert geno_builtins.format_value_bounded(values, 64) == (
            "[" + ", ".join(["v"] * 20) + ", ..."
        )
        assert len(rendered) < 40

    def test_large_sets_are_not_fully_sorted_before_rendering(self) -> None:
        big = _set(set(range(50_000)))

        shown = geno_builtins.format_value_bounded(big, 64)

        assert shown == runtime_report.truncate_display(
            geno_builtins.format_value(big), 64
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js is required")
    def test_the_javascript_renderer_equals_render_then_truncate(self) -> None:
        probe = """
const samples = [
    0, true, null, "", "plain", 'nested "quotes" \\\\', "tab\\tnew\\nline\\u0001",
    "\\u{1F600}".repeat(40), "a".repeat(65), "x\\u{1F600}".repeat(70),
    Array.from({length: 100}, (_, i) => i), ["\\u{1F600}".repeat(10), "b".repeat(30)],
    new Map([["k", [1, 2]], ["\\u{1F600}", "v"]]), Some([1, "x"]), None_, Ok("y"),
    [[[["deep".repeat(20)]]]],
];
const limits = [0, 1, 3, 4, 10, 63, 64, 65, 200];
const rows = [];
for (const value of samples) {
    for (const limit of limits) {
        rows.push([_formatValueBounded(value, limit), _formatValue(value), limit]);
    }
}
console.log(JSON.stringify(rows));
"""
        result = run_node_code(JS_RUNTIME_PRELUDE + probe)
        assert result.returncode == 0, result.stderr

        for bounded, full, limit in json.loads(result.stdout):
            assert bounded == runtime_report.truncate_display(full, limit), full

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js is required")
    def test_javascript_shapes_give_floats_and_tuples(self) -> None:
        probe = """
console.log(JSON.stringify([
    _formatValueBounded(2, 64, "float"),
    _formatValueBounded([1, 2.5], 64, ["sequence", "float"]),
    _formatValueBounded([1, "x"], 64, ["tuple", [null, null]]),
    _formatValueBounded(Some(2), 64, ["record", {"Some": [["value", "float"]]}]),
    _formatValueBounded((x) => x, 64),
]));
"""
        result = run_node_code(JS_RUNTIME_PRELUDE + probe)
        assert result.returncode == 0, result.stderr

        assert json.loads(result.stdout) == [
            "2.0",
            "[1.0, 2.5]",
            '(1, "x")',
            "Some(value: 2.0)",
            "<function>",
        ]


# =============================================================================
# Message grammar and the prelude copies
# =============================================================================


_MESSAGE_CASES: list[dict[str, Any]] = [
    {
        "clause": "requires",
        "reason": "false",
        "function": "safe_div",
        "clause_text": "b != 0",
        "clause_index": 0,
        "arguments": [["a", "8"], ["b", "0"]],
        "result": None,
        "checked_values": [],
    },
    {
        "clause": "requires",
        "reason": "propagate",
        "function": "Account.withdraw",
        "clause_text": "lookup(x)? > 0",
        "clause_index": 1,
        "arguments": [["self", "Account(balance: 3)"]],
        "result": None,
        "checked_values": [],
    },
    {
        "clause": "ensures",
        "reason": "false",
        "function": "shout",
        "clause_text": None,
        "clause_index": 2,
        "arguments": [["s", '"hi"']],
        "result": '"hi!"',
        "checked_values": [],
    },
    {
        "clause": "ensures",
        "reason": "propagate",
        "function": "Mathx.g",
        "clause_text": "n > 10",
        "clause_index": 0,
        "arguments": [["n", "1"], ["m", "2"]],
        "result": "6",
        "checked_values": [["n", "6"], ["m", "3"]],
    },
    {
        "clause": "requires",
        "reason": "false",
        "function": "answer",
        "clause_text": "true and false",
        "clause_index": 0,
        "arguments": [],
        "result": None,
        "checked_values": [],
    },
]


def _report_message(case: dict[str, Any]) -> str:
    return runtime_report.contract_message(
        clause=case["clause"],
        reason=case["reason"],
        function=case["function"],
        clause_text=case["clause_text"],
        clause_index=case["clause_index"],
        arguments=[tuple(pair) for pair in case["arguments"]],
        result=case["result"],
        checked_values=[tuple(pair) for pair in case["checked_values"]],
    )


class TestContractMessage:
    def test_the_grammar(self) -> None:
        assert [_report_message(case) for case in _MESSAGE_CASES] == [
            "Precondition failed: safe_div(a: 8, b: 0) requires b != 0",
            "Precondition failed: Account.withdraw(self: Account(balance: 3)) "
            "requires lookup(x)? > 0 ('?' propagated None/Err)",
            'Postcondition failed: shout(s: "hi") ensures clause #3 (result was "hi!")',
            "Postcondition failed: Mathx.g(n: 1, m: 2) ensures n > 10 "
            "('?' propagated None/Err; result was 6; n was 6, m was 3 when "
            "checked)",
            "Precondition failed: answer() requires true and false",
        ]

    def test_the_python_prelude_copy_agrees(self) -> None:
        message = _prelude_namespace()["_geno_contract_message"]

        for case in _MESSAGE_CASES:
            assert message(
                case["clause"],
                case["reason"],
                case["function"],
                case["clause_text"],
                case["clause_index"],
                [tuple(pair) for pair in case["arguments"]],
                case["result"],
                [tuple(pair) for pair in case["checked_values"]],
            ) == _report_message(case)

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js is required")
    def test_the_javascript_prelude_copy_agrees(self) -> None:
        probe = (
            f"const cases = {json.dumps(_MESSAGE_CASES)};\n"
            "console.log(JSON.stringify(cases.map((c) => _genoContractMessage("
            "c.clause, c.reason, c.function, c.clause_text, c.clause_index, "
            "c.arguments, c.result, c.checked_values))));\n"
        )
        result = run_node_code(JS_RUNTIME_PRELUDE + probe)
        assert result.returncode == 0, result.stderr

        assert json.loads(result.stdout) == [
            _report_message(case) for case in _MESSAGE_CASES
        ]

    def test_the_limits_are_shared(self) -> None:
        assert runtime_report.ARGUMENT_DISPLAY_LIMIT == 64
        assert _prelude_namespace()["_GENO_ARGUMENT_DISPLAY_LIMIT"] == 64
        assert re.search(
            r"^const _GENO_ARGUMENT_DISPLAY_LIMIT = 64;$",
            JS_RUNTIME_PRELUDE,
            re.MULTILINE,
        )

    def test_the_spec_property_sample_shows_the_engine_message(self) -> None:
        from geno.property_check import check_module_properties
        from geno.typechecker import TypeChecker

        spec = (GENO_DIR.parent / "docs/spec/v0.7.md").read_text()
        section = spec.split("### 5.4 Property Checks", 1)[1].split("\n### ", 1)[0]
        code = section.split("```geno\n", 1)[1].split("```", 1)[0]
        program = Parser(Lexer(code, "abs.geno").tokenize(), code).parse_program()
        checker = TypeChecker()
        checker.check_program(program)
        (result,) = check_module_properties(
            program, checker, module="abs", path="abs.geno", source=code
        )

        assert result.failure is not None
        assert result.failure.message == (
            "Postcondition failed: abs_val(n: -1) ensures result >= 0 (result was -1)"
        )
        assert f'"message": {json.dumps(result.failure.message)},' in section

    def test_no_corpus_panics_example_matches_the_old_wording(self) -> None:
        """Proposal 0018 rewords contract failures; `panics "substring"`
        examples written against the 0.6 wording would stop matching."""
        root = GENO_DIR.parent
        offenders: list[str] = []
        for directory in (
            "examples",
            "geno/std",
            "benchmark",
            "conformance",
            "selfhost",
        ):
            for path in sorted((root / directory).rglob("*.geno")):
                for number, line in enumerate(path.read_text().splitlines(), 1):
                    if "panics" not in line:
                        continue
                    if any(old in line for old in OLD_CONTRACT_WORDING) or (
                        OLD_FOR_NAME.search(line)
                    ):
                        offenders.append(f"{path.relative_to(root)}:{number}")

        assert offenders == []


# =============================================================================
# Interpreter contract failures
# =============================================================================


class TestInterpreterContractFailures:
    def test_a_failed_requires_names_the_call(self) -> None:
        violation = _violation(
            "func safe_div(a: Int, b: Int) -> Int\n"
            "    requires b != 0\n"
            "    example 6, 3 -> 2\n"
            "    return a / b\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "    return safe_div(a: 8, b: 0)\n"
            "end func\n"
        )

        assert violation.message == (
            "Precondition failed: safe_div(a: 8, b: 0) requires b != 0"
        )
        assert (violation.clause, violation.reason) == ("requires", "false")
        assert (violation.clause_index, violation.call_depth) == (0, 2)
        assert violation.function == "safe_div"
        assert violation.clause_text == "b != 0"
        assert violation.arguments == (("a", "8"), ("b", "0"))
        assert violation.result_display is None
        assert violation.checked_values == ()
        assert violation.location is not None
        assert (violation.location.line, violation.location.column) == (2, 14)

    def test_a_failed_ensures_shows_the_arguments_and_the_result(self) -> None:
        violation = _violation(
            "func shout(s: String) -> String\n"
            "    ensures length(result) > 10\n"
            '    example "0123456789" -> "0123456789!"\n'
            '    return s + "!"\n'
            "end func\n"
            "\n"
            "func main() -> Int\n"
            '    return length(shout("hi"))\n'
            "end func\n"
        )

        assert violation.message == (
            'Postcondition failed: shout(s: "hi") ensures length(result) > 10 '
            '(result was "hi!")'
        )
        assert violation.arguments == (("s", '"hi"'),)
        assert violation.result_display == '"hi!"'

    def test_a_top_level_rebinding_shows_the_call_and_the_checked_value(
        self,
    ) -> None:
        violation = _violation(
            "func g(n: Int) -> Int\n"
            "    ensures n > 10\n"
            "    example 7 -> 12\n"
            "    let n = n + 5\n"
            "    return n\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "    return g(1)\n"
            "end func\n"
        )

        assert violation.message == (
            "Postcondition failed: g(n: 1) ensures n > 10 "
            "(result was 6; n was 6 when checked)"
        )
        assert violation.arguments == (("n", "1"),)
        assert violation.checked_values == (("n", "6"),)

    def test_a_nested_shadow_is_not_a_checked_value(self) -> None:
        violation = _violation(
            "func g(n: Int) -> Int\n"
            "    ensures n > 10\n"
            "    example 11 -> 16\n"
            "    if n > 0 then\n"
            "        let n = n + 5\n"
            "        return n\n"
            "    end if\n"
            "    return n\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "    return g(1)\n"
            "end func\n"
        )

        assert violation.message == (
            "Postcondition failed: g(n: 1) ensures n > 10 (result was 6)"
        )
        assert violation.checked_values == ()

    def test_arguments_are_bounded_and_widened(self) -> None:
        violation = _violation(
            "func total(xs: List[Float], name: String) -> Float\n"
            "    requires length(xs) < 3\n"
            '    example [1.0], "a" -> 1.0\n'
            "    return xs[0]\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "    let n = total(xs: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, "
            '14, 15], name: "😀😀")\n'
            "    return 0\n"
            "end func\n"
        )

        widened = "[" + ", ".join(f"{n}.0" for n in range(1, 16)) + "]"
        assert violation.arguments == (
            ("xs", widened[:61] + "..."),
            ("name", '"😀😀"'),
        )
        assert all(
            len(shown) <= runtime_report.ARGUMENT_DISPLAY_LIMIT
            for _, shown in violation.arguments or ()
        )

    def test_methods_and_modules_use_their_display_names(self) -> None:
        violation = _violation(
            "type Account = Account(balance: Int)\n"
            "\n"
            "trait Bank\n"
            "    func withdraw(self: Self, amount: Int) -> Int\n"
            "end trait\n"
            "\n"
            "impl Bank for Account\n"
            "    func withdraw(self: Account, amount: Int) -> Int\n"
            "        requires amount <= self.balance\n"
            "        example Account(balance: 10), 5 -> 5\n"
            "        return self.balance - amount\n"
            "    end func\n"
            "end impl\n"
            "\n"
            "func main() -> Int\n"
            "    return Account(balance: 3).withdraw(7)\n"
            "end func\n"
        )

        assert violation.function == "Account.withdraw"
        assert violation.message.startswith(
            "Precondition failed: Account.withdraw(self: Account(balance: 3), "
            "amount: 7) requires"
        )

    def test_with_message_carries_every_field(self) -> None:
        violation = ContractViolationError(
            "Precondition failed: f(x: 0) requires x > 0",
            None,
            call_depth=1,
            clause="requires",
            reason="false",
            clause_index=0,
            function="f",
            clause_text="x > 0",
            arguments=(("x", "0"),),
            checked_values=(("x", "1"),),
            result_display="2",
        )

        copy = violation.with_message("other", None)

        assert copy.message == "other"
        for name in (
            "call_depth",
            "clause",
            "reason",
            "clause_index",
            "function",
            "clause_text",
            "arguments",
            "checked_values",
            "result_display",
        ):
            assert getattr(copy, name) == getattr(violation, name), name


# =============================================================================
# Interpreter stacks and locations (proposal 0018, package 2)
# =============================================================================

PANIC_PROGRAM = """\
func check(n: Int) -> Int
    example 1 -> 1
    if n > 5 then
        panic("value too large")
    end if
    return n
end func

func scale(n: Int) -> Int
    example 2 -> 4
    return check(n * 2)
end func

func main() -> Int
    return scale(4)
end func
"""

DEMO_PROGRAM = """\
func check(n: Int) -> Int
    example 1 -> 1
    if n > 5 then
        panic("value too large")
    end if
    return n
end func

func scale(n: Int) -> Int
    example 2 -> 4
    return check(n * 2)
end func

func safe_div(a: Int, b: Int) -> Int
    requires b != 0
    example 6, 3 -> 2
    return a / b
end func

func main() -> Int
    print(to_string(safe_div(a: 8, b: 0)))
    return scale(4)
end func
"""

COUNT_DOWN = """\
func count_down(n: Int) -> Int
    example 0 panics "bottom"
    if n == 0 then
        panic("bottom")
    end if
    return count_down(n - 1)
end func

func main() -> Int
    return count_down({depth})
end func
"""

BOOM = """\
func boom(x: Int) -> Int
    example 1 -> 1
    if x > 3 then
        panic("boom")
    end if
    return x
end func
"""

G_PANICS = """\
func g(x: Int) -> Int
    example 1 -> 1
    if x > 2 then
        panic("bad item")
    end if
    return x
end func
"""

ASYNC_WORK = """\
async func work(n: Int) -> Int
    if n > 3 then
        panic("work failed")
    end if
    return n
end func
"""

Position = tuple[int, int] | None


def _failure(source: str, filename: str = "t.geno", **config: Any) -> Any:
    result = geno_api.run(source, filename, config=geno_api.RunConfig(**config))
    assert not result.ok, result
    (diagnostic,) = result.diagnostics
    return diagnostic


def _position(location: Any) -> Position:
    return None if location is None else (location.line, location.column)


def _frames(diagnostic: Any) -> list[tuple[str, Position]]:
    assert diagnostic.stack is not None, diagnostic
    return [
        (frame.function, _position(frame.location)) for frame in diagnostic.stack.frames
    ]


def _raised(source: str, **interpreter: Any) -> BaseException:
    program = parse(source)
    with pytest.raises((GenoRuntimeError, SandboxError)) as exc_info:
        Interpreter(**interpreter).run(program)
    return exc_info.value


class TestInterpreterStacks:
    def test_a_panic_three_calls_deep(self) -> None:
        diagnostic = _failure(PANIC_PROGRAM)

        assert diagnostic.code is ErrorCode.RUNTIME_PANIC
        assert diagnostic.message == "value too large"
        assert _position(diagnostic.location) == (4, 9)
        assert _frames(diagnostic) == [
            ("main", (15, 12)),
            ("scale", (11, 12)),
            ("check", (4, 9)),
        ]
        assert diagnostic.stack.omitted == 0

    def test_the_report_text(self) -> None:
        error = _raised(PANIC_PROGRAM)

        assert runtime_report_lines(error) == [
            "Stack trace (most recent call last):",
            "  <stdin>:15:12 in main",
            "  <stdin>:11:12 in scale",
            "  <stdin>:4:9 in check",
            "Runtime Error: value too large",
        ]

    def test_an_index_error(self) -> None:
        diagnostic = _failure(
            """\
func c(xs: List[Int]) -> Int
    example [1, 2, 3, 4] -> 4
    return xs[3]
end func

func b(xs: List[Int]) -> Int
    example [1, 2, 3, 4] -> 4
    return c(xs)
end func

func main() -> Int
    return b([1, 2])
end func
"""
        )

        assert diagnostic.code is ErrorCode.RUNTIME_INDEX_OUT_OF_BOUNDS
        assert _frames(diagnostic) == [
            ("main", (12, 12)),
            ("b", (8, 12)),
            ("c", (3, 12)),
        ]

    def test_a_division_error(self) -> None:
        diagnostic = _failure(
            """\
func ratio(a: Int, b: Int) -> Int
    example 6, 3 -> 2
    return a / b
end func

func main() -> Int
    return ratio(1, 0)
end func
"""
        )

        assert diagnostic.code is ErrorCode.RUNTIME_DIVISION_BY_ZERO
        assert _frames(diagnostic) == [("main", (7, 12)), ("ratio", (3, 12))]

    def test_a_capability_denial(self) -> None:
        diagnostic = _failure(
            """\
@untested("reads a file")
func load(path: String) -> String
    return fs_read_text(path).unwrap()
end func

func main() -> Unit
    print(load("notes.txt"))
    return ()
end func
""",
            capabilities={"print"},
        )

        assert diagnostic.code is ErrorCode.RUNTIME_CAPABILITY_DENIED
        assert _position(diagnostic.location) == (3, 12)
        assert _frames(diagnostic) == [("main", (7, 11)), ("load", (3, 12))]

    def test_a_callback_is_positioned_at_the_builtin_call(self) -> None:
        diagnostic = _failure(
            G_PANICS
            + """
func main() -> Int
    let ys = [1, 2, 3].map(g)
    return length(ys)
end func
"""
        )

        assert _frames(diagnostic) == [("main", (10, 14)), ("g", (4, 9))]

    def test_a_default_value_runs_in_the_callee_frame(self) -> None:
        """The caller is at the call, the callee at its default expression."""
        source = """\
let items: List[Int] = [1, 2]

func boom() -> Int
    example () panics "bad default"
    panic("bad default")
    return 0
end func

func label(n: Int, tag: Int = boom()) -> Int
    example 1, 2 -> 3
    return n + tag
end func

func pick(a: Int, c: Int = items[7]) -> Int
    example 1, 2 -> 3
    return a + c
end func

func main() -> Int
    print(to_string(label(3, 4)))
    let tagged = label(n: 3)
    return pick(1)
end func
"""
        diagnostic = _failure(source, capabilities=frozenset({"print"}))
        assert _frames(diagnostic) == [
            ("main", (21, 18)),
            ("label", (9, 31)),
            ("boom", (5, 5)),
        ]
        indexed = _failure(
            source.replace("    let tagged = label(n: 3)\n", ""),
            capabilities=frozenset({"print"}),
        )
        assert _frames(indexed) == [("main", (21, 12)), ("pick", (14, 28))]

    def test_nested_builtins_in_callbacks_locate_their_own_depth(self) -> None:
        diagnostic = _failure(
            G_PANICS
            + """
func h(x: Int) -> Int
    example 1 -> 1
    let ys = [x, x + 5].map(g)
    return length(ys)
end func

func main() -> Int
    let zs = [1].map(h)
    return length(zs)
end func
""",
            check_examples=False,
        )

        assert _frames(diagnostic) == [
            ("main", (16, 14)),
            ("h", (11, 14)),
            ("g", (4, 9)),
        ]

    def test_a_recursion_limit_reached_through_a_callback(self) -> None:
        diagnostic = _failure(
            """\
func r(n: Int) -> Int
    example 0 -> 0
    if n == 0 then
        return 0
    end if
    let xs = [n - 1].map(r)
    return xs[0]
end func

func main() -> Int
    return r(100)
end func
""",
            max_recursion_depth=12,
        )

        assert diagnostic.code is ErrorCode.SANDBOX_RECURSION_LIMIT
        assert _position(diagnostic.location) == (6, 14)
        frames = _frames(diagnostic)
        assert frames[0] == ("main", (11, 12))
        assert frames[1:] == [("r", (6, 14))] * (len(frames) - 1)
        assert len(frames) + diagnostic.stack.omitted == 12

    def test_a_lambda_is_not_a_frame(self) -> None:
        diagnostic = _failure(
            BOOM
            + """
func main() -> Int
    let f = fn(x: Int) -> boom(x + 10)
    return f(1)
end func
"""
        )

        # main is positioned at the call made inside the lambda.
        assert _frames(diagnostic) == [("main", (10, 27)), ("boom", (4, 9))]

    def test_an_escaping_lambda_is_positioned_where_it_was_written(self) -> None:
        diagnostic = _failure(
            BOOM
            + """
@untested("returns a function")
func make(n: Int) -> (Int) -> Int
    return fn(x: Int) -> boom(x + n)
end func

func main() -> Int
    let f = make(10)
    return f(1)
end func
"""
        )

        assert _frames(diagnostic) == [("main", (11, 26)), ("boom", (4, 9))]

    def test_an_impl_method_is_type_dot_method(self) -> None:
        diagnostic = _failure(
            """\
type Shape = Circle(r: Int)

trait Area
    func area(self: Self) -> Int
end trait

impl Area for Shape
    func area(self: Shape) -> Int
        example Circle(1) -> 3
        match self with
            | Circle(r) ->
                if r > 5 then
                    panic("huge circle")
                end if
                return 3 * r * r
        end match
    end func
end impl

func main() -> Int
    return Circle(9).area()
end func
"""
        )

        assert _frames(diagnostic) == [("main", (21, 12)), ("Shape.area", (13, 21))]

    def test_a_stdlib_frame_displays_as_std(self) -> None:
        source = """\
import List

func main() -> Int
    let parts = List.chunk([1, 2, 3], 0)
    return length(parts)
end func
"""
        diagnostic = _failure(source)
        list_lines = (GENO_DIR / "std" / "List.geno").read_text().splitlines()
        chunk_line = next(
            number
            for number, text in enumerate(list_lines, 1)
            if "return list_chunk(" in text
        )

        frames = diagnostic.stack.frames
        assert [frame.function for frame in frames] == ["main", "List.chunk"]
        # The envelope shows std/ as the text does, never the install path.
        assert frames[1].location.filename == "std/List.geno"
        assert diagnostic.location.filename == "std/List.geno"
        lines = runtime_report.report_lines(
            "Runtime Error", diagnostic.message, stack=diagnostic.stack
        )
        assert lines[2] == f"  std/List.geno:{chunk_line}:12 in List.chunk"
        source_line = runtime_report.read_snippet_line(frames[1].location)
        assert source_line is not None
        assert "return list_chunk(xs, size)" in source_line

    def test_example_verification_has_an_example_origin(self) -> None:
        diagnostic = _failure(
            """\
func inner(n: Int) -> Int
    example 1 -> 1
    if n > 5 then
        panic("too big")
    end if
    return n
end func

func outer(n: Int) -> Int
    example 9 -> 9
    return inner(n)
end func

func main() -> Int
    return outer(1)
end func
"""
        )

        assert _frames(diagnostic) == [
            ("<example>", (10, 5)),
            ("outer", (11, 12)),
            ("inner", (4, 9)),
        ]

    def test_a_requires_rejecting_its_own_example_keeps_the_stack(self) -> None:
        diagnostic = _failure(
            """\
func safe_div(a: Int, b: Int) -> Result[Int, String]
    requires b != 0
    example 6, 3 -> Ok(2)
    example 1, 0 -> Err("division by zero")
    return Ok(a / b)
end func

func main() -> Int
    return safe_div(4, 2).unwrap()
end func
"""
        )

        assert _position(diagnostic.location) == (4, 5)
        assert _frames(diagnostic) == [("<example>", (4, 5)), ("safe_div", (2, 14))]
        assert diagnostic.contract is not None
        assert diagnostic.contract.function == "safe_div"

    def test_successful_calls_leave_nothing_behind(self) -> None:
        interpreter = Interpreter()
        interpreter.run(
            parse(BOOM + "\nfunc main() -> Int\n    return boom(1)\nend func\n")
        )
        assert interpreter._call_stack == []


class TestAsyncStacks:
    def test_async_main_helper_work(self) -> None:
        diagnostic = _failure(
            ASYNC_WORK
            + """
async func helper(n: Int) -> Int
    let v = await work(n)
    return v + 1
end func

async func main() -> Int
    return await helper(5)
end func
"""
        )

        assert _frames(diagnostic) == [
            ("main", (14, 18)),
            ("helper", (9, 19)),
            ("work", (3, 9)),
        ]

    def test_an_awaited_call_is_positioned_at_the_call(self) -> None:
        diagnostic = _failure(
            ASYNC_WORK
            + """
func main() -> Int
    return await work(5)
end func
"""
        )

        assert _frames(diagnostic) == [("main", (9, 18)), ("work", (3, 9))]

    def test_a_split_await_is_positioned_at_the_await(self) -> None:
        diagnostic = _failure(
            ASYNC_WORK
            + """
func main() -> Int
    let pending = work(5)
    let ignored = 1
    return await pending
end func
"""
        )

        assert _frames(diagnostic) == [("main", (11, 12)), ("work", (3, 9))]

    def test_a_second_await_raises_the_first_error_and_stack(self) -> None:
        interpreter = Interpreter()
        interpreter.run(parse(ASYNC_WORK), execute_main=False)
        work = interpreter.global_env.lookup("work")
        pending = interpreter.call_function(work, [5])
        site = SourceLocation(1, 1, "t.geno")

        with pytest.raises(GenoRuntimeError) as first:
            interpreter._execute_async_value(pending, site)
        with pytest.raises(GenoRuntimeError) as second:
            interpreter._execute_async_value(pending, site)

        assert second.value is first.value
        stack = runtime_report.stack_of(second.value)
        assert stack is not None
        assert [frame.function for frame in stack.frames] == ["work"]
        assert interpreter._call_stack == []


class TestLimitStacks:
    """Limits carry a stack; only structure is asserted, never timing."""

    SPIN = """\
func spin(n: Int) -> Int
    example 0 -> 0
    var x = n
    while x >= 0 do
        x = x + 1
    end while
    return x
end func

func main() -> Int
    return spin(1)
end func
"""

    def test_a_recursion_limit(self) -> None:
        diagnostic = _failure(
            """\
func forever(n: Int) -> Int
    example 0 -> 0
    if n == 0 then
        return 0
    end if
    return forever(n + 1)
end func

func main() -> Int
    return forever(1)
end func
""",
            max_recursion_depth=30,
        )

        assert diagnostic.code is ErrorCode.SANDBOX_RECURSION_LIMIT
        assert _position(diagnostic.location) == (6, 12)
        frames = _frames(diagnostic)
        assert frames[0] == ("main", (10, 12))
        assert set(frames[1:]) == {("forever", (6, 12))}
        assert len(frames) == 20
        assert diagnostic.stack.omitted == 10

    def test_a_step_limit(self) -> None:
        diagnostic = _failure(self.SPIN, check_examples=False, max_steps=500)

        assert diagnostic.code is ErrorCode.SANDBOX_STEP_LIMIT
        frames = _frames(diagnostic)
        assert [name for name, _ in frames] == ["main", "spin"]
        assert frames[0][1] == (11, 12)
        assert diagnostic.location is not None
        assert frames[1][1] == _position(diagnostic.location)
        assert 3 <= diagnostic.location.line <= 7

    def test_a_cooperative_timeout(self) -> None:
        diagnostic = _failure(
            self.SPIN, check_examples=False, max_steps=None, timeout=0.2
        )

        assert diagnostic.code is ErrorCode.SANDBOX_TIMEOUT
        frames = _frames(diagnostic)
        assert [name for name, _ in frames] == ["main", "spin"]
        assert diagnostic.location is not None
        assert frames[1][1] == _position(diagnostic.location)


class TestElision:
    @pytest.mark.parametrize(("depth", "omitted"), [(19, 1), (40, 22)])
    def test_long_stacks_keep_ten_and_ten(self, depth: int, omitted: int) -> None:
        source = COUNT_DOWN.format(depth=depth)
        diagnostic = _failure(source)

        frames = _frames(diagnostic)
        assert len(frames) == runtime_report.STACK_HEAD + runtime_report.STACK_TAIL
        assert diagnostic.stack.omitted == omitted
        assert frames[0] == ("main", (10, 12))
        assert frames[1:-1] == [("count_down", (6, 12))] * 18
        assert frames[-1] == ("count_down", (4, 9))
        payload = diagnostic.to_dict()
        assert len(payload["stack"]) == 20
        assert payload["stack_omitted"] == omitted

        lines = runtime_report_lines(_raised(source))
        noun = "frame" if omitted == 1 else "frames"
        assert lines[11] == f"  ... {omitted} {noun} omitted ..."
        assert len([line for line in lines if line.endswith(" in count_down")]) == 19

    def test_short_stacks_have_no_omitted_key(self) -> None:
        payload = _failure(PANIC_PROGRAM).to_dict()
        assert "stack_omitted" not in payload


class TestSetLocation:
    def test_it_keeps_type_code_tags_and_message(self) -> None:
        error = RuntimeLimitError(
            "List size exceeds limit (3 > 2)", limit="collection size"
        )
        error._geno_capability_denied = "fs"  # type: ignore[attr-defined]
        code = error.error_code
        location = SourceLocation(2, 17, "notes.geno")

        error.set_location(location)

        assert type(error) is RuntimeLimitError
        assert error.error_code is code
        assert error.limit == "collection size"
        assert error._geno_capability_denied == "fs"  # type: ignore[attr-defined]
        assert error.message == "List size exceeds limit (3 > 2)"
        assert error.location is location
        assert str(error) == (
            "notes.geno:2:17: Runtime Error: List size exceeds limit (3 > 2)"
        )

    def test_a_denial_keeps_its_trusted_tag_when_located(self) -> None:
        error = _raised(
            """\
func main() -> Unit
    let text = fs_read_text("notes.txt").unwrap()
    print(text)
    return ()
end func
""",
            capabilities={"print"},
        )

        assert isinstance(error, GenoRuntimeError)
        assert error.error_code is ErrorCode.RUNTIME_CAPABILITY_DENIED
        assert getattr(error, "_geno_capability_denied", None) == "fs"
        assert _position(error.location) == (2, 16)
        assert "2:16" not in error.message


class TestFitReportText:
    LINES = [
        runtime_report.STACK_HEADER,
        *[f"  deep.geno:6:12 in count_down{index}" for index in range(10)],
        "  ... 22 frames omitted ...",
        *[f"  deep.geno:6:12 in count_down{index}" for index in range(10, 20)],
        '      panic("bottom")',
        "      ^",
        "Runtime Error: bottom",
    ]

    def test_text_that_fits_is_unchanged(self) -> None:
        text = "\n".join(self.LINES)
        assert runtime_report.fit_report_text(self.LINES, len(text)) == text

    def test_the_snippet_goes_first(self) -> None:
        text = "\n".join(self.LINES)
        fitted = runtime_report.fit_report_text(self.LINES, len(text) - 1)
        assert "panic(" not in fitted
        assert fitted.splitlines()[:-1] == self.LINES[:-3]

    @pytest.mark.parametrize("limit", [400, 200, 120, 60, 30, 21, 12, 3, 0])
    def test_the_message_line_is_always_last(self, limit: int) -> None:
        fitted = runtime_report.fit_report_text(self.LINES, limit)
        assert len(fitted) <= limit
        last = fitted.splitlines()[-1] if fitted else ""
        if limit >= len("Runtime Error: bottom"):
            assert last == "Runtime Error: bottom"
        else:
            assert "Runtime Error: bottom".startswith(last.removesuffix("..."))
        lines = fitted.splitlines()
        omitted = [line for line in lines if "omitted" in line]
        frames = [line for line in lines if " in count_down" in line]
        if frames:
            # Frames dropped from the middle are counted.
            assert lines[0] == runtime_report.STACK_HEADER
            (marker,) = omitted
            count = int(marker.split()[1])
            assert count + len(frames) == 42
            # The innermost frame survives longest.
            assert frames[-1].endswith("count_down19")

    def test_a_report_without_frames_truncates_its_message(self) -> None:
        lines = ["Runtime Error: " + "x" * 100]
        fitted = runtime_report.fit_report_text(lines, 40)
        assert len(fitted) == 40
        assert fitted.startswith("Runtime Error: xxx")
        assert fitted.endswith("...")


class TestProcessLaneExamplePhase:
    def test_a_long_panic_message_keeps_its_report_shape(self, tmp_path: Path) -> None:
        source = tmp_path / "long.geno"
        source.write_text(
            """\
func shout(n: Int) -> Int
    example 9 -> 9
    if n > 5 then
        panic("{message}")
    end if
    return n
end func

func main() -> Int
    return shout(1)
end func
""".replace("{message}", "é" * 150),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "run",
                "--max-output-length",
                "200",
                str(source),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )

        assert result.returncode == 1
        lines = result.stderr.splitlines()
        assert lines[-1].startswith("Runtime Error: ééé")
        assert len(result.stderr.rstrip("\n")) <= 200
        assert "Sandbox Error" not in result.stderr
