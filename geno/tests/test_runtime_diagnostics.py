"""Runtime failure diagnostics (draft proposal 0018).

This file covers the contract-failure half that every backend shares: the
clause text the parser captures, the bounded value renderers, the message
grammar and the structured fields of ``ContractViolationError``. The
three-backend message parity lives in ``test_backend_parity.py``.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from geno import builtins as geno_builtins
from geno import runtime_report
from geno.ast_nodes import EnsuresClause, FunctionDef, RequiresClause
from geno.formatter import format_source
from geno.interpreter import interpret
from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE
from geno.lexer import Lexer
from geno.parser import Parser, parse
from geno.parser_base import contract_clause_text
from geno.property_check import ensures_clause_text
from geno.tests._script_runner import run_node_code
from geno.values import (
    ArrayValue,
    ConstructorValue,
    ContractViolationError,
    MutableMapValue,
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
                    if any(old in line for old in OLD_CONTRACT_WORDING):
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
