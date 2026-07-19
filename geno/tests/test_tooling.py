"""
Tests for benchmark, experiment, and script tooling.
"""

import builtins
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchmark.runner as benchmark_runner

pytest.importorskip("yaml", reason="pyyaml required for experiment tooling tests")

from analysis.analyzer import ResultsAnalyzer
from analysis.report_generator import ReportGenerator
from benchmark import load_all_problems, verify_problem_set
from benchmark.runner import (
    BenchmarkRunner,
    BenchmarkTimeoutError,
    ErrorCategory,
    EvaluationResult,
    UnsafePythonEvaluationDisabled,
    summarize_results,
)
from benchmark.schema import (
    Difficulty,
    Domain,
    Problem,
    TypeSignature,
    format_geno_example_input,
    format_geno_literal,
    format_python_example_call,
    format_python_literal,
    format_python_type,
    validate_problem,
)
from benchmark.schema import TestCase as BenchmarkTestCase
from experiment.runner import ExperimentConfig, ExperimentRunner, GenerationResult
from geno.execution_limits import DEFAULT_PROCESS_MAX_MEMORY_BYTES
from geno.values import ConstructorValue
from scripts.validate_benchmark import build_benchmark_analysis

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_problem() -> Problem:
    return Problem(
        id="PROB-TEST",
        name="Identity",
        difficulty=Difficulty.EASY,
        domain=Domain.MATH,
        description="Return the input unchanged.",
        function_name="identity",
        inputs=[TypeSignature("x", "Int")],
        output=TypeSignature("out", "Int"),
        visible_examples=[BenchmarkTestCase(1, 1)],
        hidden_tests=[
            BenchmarkTestCase(2, 2),
            BenchmarkTestCase(3, 3),
            BenchmarkTestCase(4, 4),
        ],
        geno_solution="""
        func identity(x: Int) -> Int
            example 1 -> 1
            return x
        end func
        """,
        python_solution="""
def identity(x: int) -> int:
    return x
        """.strip(),
    )


def make_list_problem() -> Problem:
    return Problem(
        id="PROB-LIST",
        name="Count Items",
        difficulty=Difficulty.EASY,
        domain=Domain.ARRAYS,
        description="Return the number of items.",
        function_name="count_items",
        inputs=[TypeSignature("items", "List[Int]")],
        output=TypeSignature("out", "Int"),
        visible_examples=[BenchmarkTestCase([[1, 2, 3]], 3)],
        hidden_tests=[BenchmarkTestCase([[]], 0)],
        geno_solution="""
        func count_items(items: List[Int]) -> Int
            example [1, 2, 3] -> 3
            return length(items)
        end func
        """,
        python_solution="""
def count_items(items: list[int]) -> int:
    return len(items)
        """.strip(),
    )


def make_option_problem() -> Problem:
    return Problem(
        id="PROB-OPTION",
        name="First Positive",
        difficulty=Difficulty.EASY,
        domain=Domain.ARRAYS,
        description="Return the first positive value if present.",
        function_name="first_positive",
        inputs=[TypeSignature("items", "List[Int]")],
        output=TypeSignature("out", "Option[Int]"),
        visible_examples=[BenchmarkTestCase([[0, -1, 5]], {"Some": 5})],
        hidden_tests=[BenchmarkTestCase([[-1, -2]], "None")],
        geno_solution="""
        func first_positive(items: List[Int]) -> Option[Int]
            example [0, -1, 5] -> Some(5)
            var i: Int = 0
            while i < length(items) do
                if items[i] > 0 then
                    return Some(items[i])
                end if
                i = i + 1
            end while
            return None
        end func
        """,
        python_solution="""
def first_positive(items: list[int]):
    for item in items:
        if item > 0:
            return item
    return None
        """.strip(),
    )


def make_option_string_none_problem() -> Problem:
    return Problem(
        id="PROB-OPTION-STRING-NONE",
        name="Optional None String",
        difficulty=Difficulty.EASY,
        domain=Domain.STRINGS,
        description="Return the string None when requested.",
        function_name="maybe_none_text",
        inputs=[TypeSignature("flag", "Bool")],
        output=TypeSignature("out", "Option[String]"),
        visible_examples=[BenchmarkTestCase(True, {"Some": "None"})],
        hidden_tests=[BenchmarkTestCase(False, "None")],
        geno_solution="""
        func maybe_none_text(flag: Bool) -> Option[String]
            example true -> Some("None")
            example false -> None
            if flag then
                return Some("None")
            end if
            return None
        end func
        """,
        python_solution="""\
def maybe_none_text(flag: bool):
    if flag:
        return "None"
    return None
        """.strip(),
    )


def make_result_string_none_problem() -> Problem:
    return Problem(
        id="PROB-RESULT-STRING-NONE",
        name="Result None String",
        difficulty=Difficulty.EASY,
        domain=Domain.STRINGS,
        description="Return the string None as an error payload.",
        function_name="none_error",
        inputs=[TypeSignature("flag", "Bool")],
        output=TypeSignature("out", "Result[Int, String]"),
        visible_examples=[BenchmarkTestCase(True, {"Err": "None"})],
        hidden_tests=[BenchmarkTestCase(False, {"Ok": 1})],
        geno_solution="""
        func none_error(flag: Bool) -> Result[Int, String]
            example true -> Err("None")
            example false -> Ok(1)
            if flag then
                return Err("None")
            end if
            return Ok(1)
        end func
        """,
        python_solution="""\
def none_error(flag: bool):
    if flag:
        return {"Err": "None"}
    return {"Ok": 1}
        """.strip(),
    )


def make_zero_arg_problem() -> Problem:
    return Problem(
        id="PROB-ZERO-ARG",
        name="Zero Arg",
        difficulty=Difficulty.TRIVIAL,
        domain=Domain.MATH,
        description="Return the answer.",
        function_name="answer",
        inputs=[],
        output=TypeSignature("out", "Int"),
        visible_examples=[BenchmarkTestCase([], 42)],
        hidden_tests=[BenchmarkTestCase([], 42)],
        geno_solution="""
        func answer() -> Int
            example () -> 42
            return 42
        end func
        """,
        python_solution="""
def answer() -> int:
    return 42
        """.strip(),
    )


def make_unit_problem() -> Problem:
    return Problem(
        id="PROB-UNIT",
        name="Unit Output",
        difficulty=Difficulty.TRIVIAL,
        domain=Domain.MATH,
        description="Return Unit.",
        function_name="noop",
        inputs=[],
        output=TypeSignature("out", "Unit"),
        visible_examples=[BenchmarkTestCase([], None)],
        hidden_tests=[BenchmarkTestCase([], [])],
        geno_solution="""
        func noop() -> Unit
            example () -> ()
            return ()
        end func
        """,
        python_solution="""\
def noop() -> None:
    return None
        """.strip(),
    )


def make_option_unit_problem() -> Problem:
    return Problem(
        id="PROB-OPTION-UNIT",
        name="Optional Unit",
        difficulty=Difficulty.TRIVIAL,
        domain=Domain.MATH,
        description="Return an optional Unit value.",
        function_name="maybe_unit",
        inputs=[TypeSignature("flag", "Bool")],
        output=TypeSignature("out", "Option[Unit]"),
        visible_examples=[BenchmarkTestCase(True, {"Some": None})],
        hidden_tests=[BenchmarkTestCase(False, "None")],
        geno_solution="""
        func maybe_unit(flag: Bool) -> Option[Unit]
            example true -> Some(())
            example false -> None
            if flag then
                return Some(())
            end if
            return None
        end func
        """,
        python_solution="""\
def maybe_unit(flag: bool):
    if flag:
        return {"Some": None}
    return None
        """.strip(),
    )


def make_nested_option_problem() -> Problem:
    return Problem(
        id="PROB-NESTED-OPTION",
        name="Nested Option",
        difficulty=Difficulty.EASY,
        domain=Domain.MATH,
        description="Return a nested optional value.",
        function_name="nested_option",
        inputs=[TypeSignature("x", "Int")],
        output=TypeSignature("out", "Option[Option[Int]]"),
        visible_examples=[BenchmarkTestCase(0, {"Some": "None"})],
        hidden_tests=[
            BenchmarkTestCase(1, {"Some": {"Some": 1}}),
            BenchmarkTestCase(2, "None"),
        ],
        geno_solution="""
        func nested_option(x: Int) -> Option[Option[Int]]
            example 0 -> Some(None)
            example 1 -> Some(Some(1))
            example 2 -> None
            if x == 0 then
                return Some(None)
            end if
            if x == 1 then
                return Some(Some(1))
            end if
            return None
        end func
        """,
        python_solution="""\
def nested_option(x):
    if x == 0:
        return {"Some": None}
    if x == 1:
        return {"Some": 1}
    return None
        """.strip(),
    )


def make_option_result_problem() -> Problem:
    return Problem(
        id="PROB-OPTION-RESULT",
        name="Optional Result",
        difficulty=Difficulty.EASY,
        domain=Domain.MATH,
        description="Return an optional result.",
        function_name="optional_result",
        inputs=[TypeSignature("x", "Int")],
        output=TypeSignature("out", "Option[Result[Int, String]]"),
        visible_examples=[BenchmarkTestCase(1, {"Some": {"Ok": 5}})],
        hidden_tests=[
            BenchmarkTestCase(0, "None"),
            BenchmarkTestCase(-1, {"Some": {"Err": "bad"}}),
        ],
        geno_solution="""
        func optional_result(x: Int) -> Option[Result[Int, String]]
            example 1 -> Some(Ok(5))
            example 0 -> None
            example -1 -> Some(Err("bad"))
            if x == 1 then
                return Some(Ok(5))
            end if
            if x == -1 then
                return Some(Err("bad"))
            end if
            return None
        end func
        """,
        python_solution="""\
def optional_result(x):
    if x == 1:
        return {"Some": {"Ok": 5}}
    if x == -1:
        return {"Some": {"Err": "bad"}}
    return None
        """.strip(),
    )


def make_result_tuple_problem() -> Problem:
    return Problem(
        id="PROB-RESULT-TUPLE",
        name="Result Tuple",
        difficulty=Difficulty.EASY,
        domain=Domain.MATH,
        description="Return a swapped pair in a Result.",
        function_name="swap_result",
        inputs=[TypeSignature("pair", "(Int, Int)")],
        output=TypeSignature("out", "Result[(Int, Int), String]"),
        visible_examples=[BenchmarkTestCase([[1, 2]], {"Ok": [2, 1]})],
        hidden_tests=[BenchmarkTestCase([[3, 4]], {"Ok": [4, 3]})],
        geno_solution="""
        func swap_result(pair: (Int, Int)) -> Result[(Int, Int), String]
            example (1, 2) -> Ok((2, 1))
            let (a, b): (Int, Int) = pair
            return Ok((b, a))
        end func
        """,
        python_solution="""\
def swap_result(pair):
    a, b = pair
    return {"Ok": (b, a)}
        """.strip(),
    )


def make_option_tuple_input_problem() -> Problem:
    return Problem(
        id="PROB-OPTION-TUPLE-IN",
        name="Optional Pair Sum",
        difficulty=Difficulty.EASY,
        domain=Domain.MATH,
        description="Sum a pair when it is present.",
        function_name="sum_optional_pair",
        inputs=[TypeSignature("pair", "Option[(Int, Int)]")],
        output=TypeSignature("out", "Int"),
        visible_examples=[BenchmarkTestCase({"Some": [1, 2]}, 3)],
        hidden_tests=[BenchmarkTestCase("None", 0)],
        geno_solution="""
        func sum_optional_pair(pair: Option[(Int, Int)]) -> Int
            example Some((1, 2)) -> 3
            example None -> 0
            match pair with
                | Some(value) ->
                    let (a, b): (Int, Int) = value
                    return a + b
                | None -> return 0
            end match
        end func
        """,
        python_solution="""\
def sum_optional_pair(pair):
    if pair is None:
        return 0
    a, b = pair
    return a + b
        """.strip(),
    )


def make_result_tuple_input_problem() -> Problem:
    return Problem(
        id="PROB-RESULT-TUPLE-IN",
        name="Result Pair Sum",
        difficulty=Difficulty.EASY,
        domain=Domain.MATH,
        description="Sum a pair from a successful result.",
        function_name="sum_result_pair",
        inputs=[TypeSignature("result", "Result[(Int, Int), String]")],
        output=TypeSignature("out", "Int"),
        visible_examples=[BenchmarkTestCase({"Ok": [1, 2]}, 3)],
        hidden_tests=[BenchmarkTestCase({"Err": "bad"}, -1)],
        geno_solution="""
        func sum_result_pair(result: Result[(Int, Int), String]) -> Int
            example Ok((1, 2)) -> 3
            example Err("bad") -> -1
            match result with
                | Ok(value) ->
                    let (a, b): (Int, Int) = value
                    return a + b
                | Err(_) -> return -1
            end match
        end func
        """,
        python_solution="""\
def sum_result_pair(result):
    if "Ok" in result:
        a, b = result["Ok"]
        return a + b
    return -1
        """.strip(),
    )


def make_tuple_problem() -> Problem:
    return Problem(
        id="PROB-TUPLE",
        name="Swap Pair",
        difficulty=Difficulty.EASY,
        domain=Domain.MATH,
        description="Swap the values in a pair.",
        function_name="swap_pair",
        inputs=[TypeSignature("pair", "(Int, Int)")],
        output=TypeSignature("out", "(Int, Int)"),
        visible_examples=[BenchmarkTestCase([[1, 2]], [2, 1])],
        hidden_tests=[BenchmarkTestCase([[3, 4]], [4, 3])],
        geno_solution="""
        func swap_pair(pair: (Int, Int)) -> (Int, Int)
            example (1, 2) -> (2, 1)
            let (a, b): (Int, Int) = pair
            return (b, a)
        end func
        """,
        python_solution="""
def swap_pair(pair: tuple[int, int]) -> tuple[int, int]:
    a, b = pair
    return (b, a)
        """.strip(),
    )


def make_python_runner(timeout_seconds: float = 5.0) -> BenchmarkRunner:
    return BenchmarkRunner.for_research(timeout_seconds=timeout_seconds)


class TestBenchmarkRunner:
    def test_geno_prompt_literals_use_geno_syntax(self):
        problem = Problem(
            id="PROB-LITERALS",
            name="Literal Rendering",
            difficulty=Difficulty.EASY,
            domain=Domain.STRINGS,
            description="Render literals.",
            function_name="literal_demo",
            inputs=[
                TypeSignature("text", "String"),
                TypeSignature("flag", "Bool"),
            ],
            output=TypeSignature("out", "Option[String]"),
            visible_examples=[
                BenchmarkTestCase(["hello\nworld", True], {"Some": "ok"}),
                BenchmarkTestCase(["", False], "None"),
            ],
            hidden_tests=[],
        )

        prompt = problem.generate_geno_prompt()

        assert 'example "hello\\nworld", true -> Some("ok")' in prompt
        assert 'example "", false -> None' in prompt
        assert "True" not in prompt
        assert "False" not in prompt
        assert "example 'hello" not in prompt

    def test_geno_literal_renderer_handles_nested_benchmark_values(self):
        assert format_geno_literal(["a", True, {"Ok": 3}]) == '["a", true, Ok(3)]'
        assert (
            format_geno_example_input(
                [["x", "y"]],
                [TypeSignature("items", "List[String]")],
            )
            == '["x", "y"]'
        )
        assert format_geno_literal([None], "List[Option[Int]]") == "[None]"
        with pytest.raises(TypeError, match="Cannot render None as Geno type"):
            format_geno_literal(None, "Int")

    def test_geno_literal_renderer_handles_tuple_types(self):
        assert format_geno_literal([1, 2], "(Int, Int)") == "(1, 2)"
        assert format_geno_literal([1], "(Int)") == "(1,)"
        assert format_geno_literal(None, "()") == "()"
        assert format_geno_literal([], "()") == "()"
        assert format_geno_literal([], "Unit") == "()"
        assert format_geno_literal([[1, True]], "List[(Int, Bool)]") == "[(1, true)]"
        with pytest.raises(TypeError, match="Expected sequence"):
            format_geno_literal(1, "(Int, Int)")
        with pytest.raises(TypeError, match="Expected 2 values"):
            format_geno_literal([1], "(Int, Int)")
        with pytest.raises(TypeError, match="Expected 2 values"):
            format_geno_literal([1, 2, 3], "(Int, Int)")

    def test_geno_literal_renderer_propagates_adt_payload_types(self):
        assert format_geno_literal({"Some": None}, "Option[Option[Int]]") == (
            "Some(None)"
        )
        assert format_geno_literal({"Some": "None"}, "Option[Option[Int]]") == (
            "Some(None)"
        )
        assert format_geno_literal({"Ok": "None"}, "Result[Option[Int], String]") == (
            "Ok(None)"
        )
        assert (
            format_geno_literal(
                {"Some": [None]},
                "Option[List[Option[Int]]]",
            )
            == "Some([None])"
        )

    def test_geno_prompt_renders_nested_adt_payload_literals(self):
        problem = Problem(
            id="PROB-NESTED-OPTION",
            name="Nested Option",
            difficulty=Difficulty.EASY,
            domain=Domain.MATH,
            description="Return a nested optional value.",
            function_name="nested_option",
            inputs=[TypeSignature("x", "Int")],
            output=TypeSignature("out", "Option[Option[Int]]"),
            visible_examples=[
                BenchmarkTestCase([0], {"Some": "None"}),
                BenchmarkTestCase([1], {"Some": {"Some": 1}}),
            ],
            hidden_tests=[],
        )

        prompt = problem.generate_geno_prompt()

        assert "example 0 -> Some(None)" in prompt
        assert "example 1 -> Some(Some(1))" in prompt
        assert 'Some("None")' not in prompt

    def test_geno_literal_renderer_distinguishes_unit_payload_from_nullary_variant(
        self,
    ):
        assert format_geno_literal({"Some": None}, "Option[Unit]") == "Some(())"
        assert format_geno_literal({"Some": None}, "Option[()]") == "Some(())"
        assert format_geno_literal({"Ok": None}, "Result[Unit, String]") == "Ok(())"
        assert format_geno_literal({"Ok": None}, "Result[(), String]") == "Ok(())"
        assert format_geno_literal({"Err": None}, "Result[Int, Unit]") == "Err(())"
        assert format_geno_literal({"Err": None}, "Result[Int, ()]") == "Err(())"
        assert format_geno_literal({"None": None}, "Option[Int]") == "None"

    def test_geno_prompt_renders_unit_adt_payload_literals(self):
        problem = Problem(
            id="PROB-UNIT-OPTION",
            name="Unit Option",
            difficulty=Difficulty.EASY,
            domain=Domain.MATH,
            description="Return an optional unit value.",
            function_name="maybe_unit",
            inputs=[TypeSignature("flag", "Bool")],
            output=TypeSignature("out", "Option[Unit]"),
            visible_examples=[
                BenchmarkTestCase([True], {"Some": None}),
                BenchmarkTestCase([False], "None"),
            ],
            hidden_tests=[],
        )

        prompt = problem.generate_geno_prompt()

        assert "example true -> Some(())" in prompt
        assert "example false -> None" in prompt
        assert "example true -> Some\n" not in prompt

    def test_geno_example_input_renders_zero_arg_examples_as_unit(self):
        assert format_geno_example_input([], []) == "()"

    def test_geno_prompt_renders_zero_arg_examples_as_unit(self):
        problem = make_zero_arg_problem()

        prompt = problem.generate_geno_prompt()

        assert "func answer() -> Int" in prompt
        assert "example () -> 42" in prompt
        assert "example [] -> 42" not in prompt

    def test_python_prompt_renders_zero_arg_examples_without_args(self):
        problem = make_zero_arg_problem()

        prompt = problem.generate_python_prompt()

        assert "def answer() -> int:" in prompt
        assert "# answer() -> 42" in prompt
        assert "# answer([]) -> 42" not in prompt

    def test_python_prompt_renders_option_as_python_values(self):
        problem = make_option_problem()

        prompt = problem.generate_python_prompt()

        assert "def first_positive(items: list[int]) -> int | None:" in prompt
        assert "# first_positive([0, -1, 5]) -> 5" in prompt
        assert "{'Some': 5}" not in prompt
        assert '"Some":' not in prompt
        assert "-> 'None'" not in prompt
        assert "Optional[" not in prompt

    def test_python_prompt_keeps_present_none_string_distinct_from_absent_option(self):
        problem = make_option_string_none_problem()

        prompt = problem.generate_python_prompt()

        assert "def maybe_none_text(flag: bool) -> str | None:" in prompt
        assert "# maybe_none_text(True) -> 'None'" in prompt
        assert "# maybe_none_text(True) -> None" not in prompt

    def test_python_prompt_renders_result_with_explicit_dict_convention(self):
        problem = Problem(
            id="PROB-RESULT",
            name="Divide",
            difficulty=Difficulty.EASY,
            domain=Domain.MATH,
            description="Divide two numbers.",
            function_name="safe_divide",
            inputs=[TypeSignature("a", "Int"), TypeSignature("b", "Int")],
            output=TypeSignature("out", "Result[Int, String]"),
            visible_examples=[
                BenchmarkTestCase([10, 2], {"Ok": 5}),
                BenchmarkTestCase([10, 0], {"Err": "division by zero"}),
            ],
            hidden_tests=[],
        )

        prompt = problem.generate_python_prompt()

        assert "Result[" not in prompt
        assert "def safe_divide(a: int, b: int) -> dict[str, object]:" in prompt
        assert 'use {"Ok": value}' in prompt
        assert '# safe_divide(10, 2) -> {"Ok": 5}' in prompt
        assert "# safe_divide(10, 0) -> {\"Err\": 'division by zero'}" in prompt

    def test_python_prompt_helpers_do_not_use_schema_literals(self):
        problem = make_option_problem()

        assert format_python_type("Option[Int]") == "int | None"
        assert format_python_example_call(problem, problem.visible_examples[0]) == (
            "first_positive([0, -1, 5]) -> 5"
        )

    def test_python_prompt_helpers_render_tuple_types_and_literals(self):
        assert format_python_type("(Int, Bool)") == "tuple[int, bool]"
        assert format_python_type("List[(Int, Bool)]") == "list[tuple[int, bool]]"
        assert format_python_type("Tuple[Int, String]") == "tuple[int, str]"
        assert format_python_type("()") == "None"
        assert format_python_type("Option[Unit]") == "dict[str, object] | None"
        assert format_python_type("Option[()]") == "dict[str, object] | None"
        assert format_python_type("Option[Option[Int]]") == ("dict[str, object] | None")
        assert format_python_type("Option[Result[Int, String]]") == (
            "dict[str, object] | None"
        )
        assert format_python_literal([1, False], "(Int, Bool)") == "(1, False)"
        assert format_python_literal(None, "()") == "None"
        assert format_python_literal([], "()") == "None"
        assert format_python_literal([], "Unit") == "None"
        assert format_python_literal({"Some": None}, "Option[Unit]") == (
            '{"Some": None}'
        )
        assert format_python_literal({"Some": None}, "Option[()]") == ('{"Some": None}')
        assert format_python_literal({"Some": "None"}, "Option[Option[Int]]") == (
            '{"Some": None}'
        )
        assert (
            format_python_literal({"Some": {"Some": 1}}, "Option[Option[Int]]")
            == '{"Some": 1}'
        )
        assert (
            format_python_literal(
                {"Some": {"Ok": 5}},
                "Option[Result[Int, String]]",
            )
            == '{"Some": {"Ok": 5}}'
        )
        assert format_python_literal({"None": None}, "Option[Int]") == "None"
        with pytest.raises(TypeError, match="Cannot render"):
            format_python_literal({"None": 123}, "Option[Int]")
        with pytest.raises(TypeError, match="Cannot render None payload"):
            format_geno_literal({"Some": None}, "Option[String]")
        with pytest.raises(TypeError, match="Cannot render None payload"):
            format_python_literal({"Some": None}, "Option[String]")
        with pytest.raises(TypeError, match="Cannot render"):
            format_geno_literal(5, "Option[Int]")
        with pytest.raises(TypeError, match="Cannot render"):
            format_geno_literal(5, "Result[Int, String]")
        with pytest.raises(TypeError, match="Cannot render"):
            format_python_literal({"Ok": 1}, "Option[Int]")
        with pytest.raises(TypeError, match="Cannot render"):
            format_python_literal({"Some": 1, "None": None}, "Option[Int]")
        assert format_python_literal([[1, True]], "List[(Int, Bool)]") == (
            "[(1, True)]"
        )
        with pytest.raises(TypeError, match="Expected sequence"):
            format_python_literal(1, "(Int, Int)")
        with pytest.raises(TypeError, match="Expected 2 values"):
            format_python_literal([1], "(Int, Int)")
        with pytest.raises(TypeError, match="Expected 2 values"):
            format_python_literal([1, 2, 3], "(Int, Int)")

    def test_python_literal_renderer_propagates_list_item_types_for_adts(self):
        assert (
            format_python_literal(
                [{"Some": 1}, "None"],
                "List[Option[Int]]",
            )
            == "[1, None]"
        )
        assert (
            format_python_literal(
                [{"Ok": 1}, {"Err": "bad"}],
                "List[Result[Int, String]]",
            )
            == '[{"Ok": 1}, {"Err": \'bad\'}]'
        )

    def test_python_prompt_renders_list_adt_items_as_python_values(self):
        problem = Problem(
            id="PROB-PY-LIST-OPTION",
            name="Collect positives",
            difficulty=Difficulty.EASY,
            domain=Domain.ARRAYS,
            description="Return optional positive values.",
            function_name="collect_positive",
            inputs=[TypeSignature("items", "List[Option[Int]]")],
            output=TypeSignature("out", "List[Option[Int]]"),
            visible_examples=[
                BenchmarkTestCase(
                    [[{"Some": 1}, "None", {"Some": 3}]],
                    [{"Some": 1}, "None", {"Some": 3}],
                ),
            ],
            hidden_tests=[],
        )

        prompt = problem.generate_python_prompt()

        assert "def collect_positive(items: list[int | None]) -> list[int | None]:" in (
            prompt
        )
        assert "# collect_positive([1, None, 3]) -> [1, None, 3]" in prompt
        assert "{'Some': 1}" not in prompt
        assert "'None'" not in prompt

    def test_prompts_render_tuple_examples(self):
        problem = make_tuple_problem()

        geno_prompt = problem.generate_geno_prompt()
        python_prompt = problem.generate_python_prompt()

        assert "func swap_pair(pair: (Int, Int)) -> (Int, Int)" in geno_prompt
        assert "example (1, 2) -> (2, 1)" in geno_prompt
        assert "example [1, 2] -> [2, 1]" not in geno_prompt
        assert "def swap_pair(pair: tuple[int, int]) -> tuple[int, int]:" in (
            python_prompt
        )
        assert "# swap_pair((1, 2)) -> (2, 1)" in python_prompt
        assert "# swap_pair([1, 2]) -> [2, 1]" not in python_prompt

    def test_python_prompt_renders_option_unit_with_explicit_dict_convention(self):
        problem = make_option_unit_problem()

        prompt = problem.generate_python_prompt()

        assert "def maybe_unit(flag: bool) -> dict[str, object] | None:" in prompt
        assert 'use {"Some": value} for present values' in prompt
        assert '# maybe_unit(True) -> {"Some": None}' in prompt
        assert "# maybe_unit(True) -> None" not in prompt

    def test_python_prompt_renders_nested_option_with_explicit_outer_wrapper(self):
        problem = make_nested_option_problem()

        prompt = problem.generate_python_prompt()

        assert "def nested_option(x: int) -> dict[str, object] | None:" in prompt
        assert 'use {"Some": value} for present values' in prompt
        assert '# nested_option(0) -> {"Some": None}' in prompt
        assert "# nested_option(0) -> None" not in prompt

    def test_python_prompt_renders_option_result_with_explicit_outer_wrapper(self):
        problem = make_option_result_problem()

        prompt = problem.generate_python_prompt()

        assert "def optional_result(x: int) -> dict[str, object] | None:" in prompt
        assert 'use {"Some": value} for present values' in prompt
        assert '# optional_result(1) -> {"Some": {"Ok": 5}}' in prompt
        assert '# optional_result(1) -> {"Ok": 5}' not in prompt

    def test_prompts_render_adt_inputs_in_backend_shapes(self):
        option_problem = make_option_tuple_input_problem()
        result_problem = make_result_tuple_input_problem()

        geno_option_prompt = option_problem.generate_geno_prompt()
        python_option_prompt = option_problem.generate_python_prompt()
        geno_result_prompt = result_problem.generate_geno_prompt()
        python_result_prompt = result_problem.generate_python_prompt()

        assert "example Some((1, 2)) -> 3" in geno_option_prompt
        assert "# sum_optional_pair((1, 2)) -> 3" in python_option_prompt
        assert "example Ok((1, 2)) -> 3" in geno_result_prompt
        assert '# sum_result_pair({"Ok": (1, 2)}) -> 3' in python_result_prompt

    def test_geno_multiple_parse_errors_become_syntax_result(self):
        problem = make_problem()
        result = BenchmarkRunner().evaluate_geno(
            problem,
            """
            func identity(x: Int) -> Int
                let y: Int =
            end func identity
            func other() -> Int
                return
            end func other
            """,
        )

        assert result.error_category == ErrorCategory.SYNTAX
        assert "Expected expression" in result.error_message
        assert "\n" in result.error_message

    def test_wrong_answer_propagates_to_summary(self):
        problem = make_problem()
        result = make_python_runner().evaluate_python(
            problem,
            "def identity(x):\n    return 0\n",
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER

        summary = summarize_results([result], [problem])
        assert summary.wrong_answers == 1
        assert summary.problems_passed == 0

    def test_sandbox_accepts_prompt_suggested_annotations(self):
        """Signatures the Python prompts suggest must run in the sandbox.

        format_python_type emits annotations like `dict[str, object]`, which
        Python evaluates at def time; the sandbox namespace must resolve them.
        """
        problem = make_problem()
        result = make_python_runner().evaluate_python(
            problem,
            "def identity(x: int) -> dict[str, object]:\n    return x\n",
        )

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_python_timeout_is_reported(self):
        problem = make_problem()
        result = make_python_runner(timeout_seconds=0.05).evaluate_python(
            problem,
            "def identity(x):\n    while True:\n        pass\n",
        )

        assert result.error_category == ErrorCategory.TIMEOUT
        assert result.test_results[0].error_category == ErrorCategory.TIMEOUT

    def test_python_process_watchdog_survives_caught_timeout(self):
        problem = make_problem()
        source = """
while True:
    try:
        while True:
            pass
    except Exception:
        pass

def identity(x):
    return x
"""

        result = make_python_runner(timeout_seconds=0.05).evaluate_python(
            problem,
            source,
        )

        assert result.error_category == ErrorCategory.TIMEOUT

    def test_python_allocation_bomb_is_contained_by_worker(self):
        problem = make_problem()
        source = """
values = [0] * 100_000_000

def identity(x):
    return x
"""

        result = make_python_runner(timeout_seconds=1.0).evaluate_python(
            problem,
            source,
        )

        assert result.error_category in {ErrorCategory.RUNTIME, ErrorCategory.TIMEOUT}

    def test_python_evaluation_is_disabled_by_default(self):
        problem = make_problem()

        with pytest.raises(UnsafePythonEvaluationDisabled, match="disabled by default"):
            BenchmarkRunner().evaluate_python(
                problem, "def identity(x):\n    return x\n"
            )

    def test_timeout_fallback_works_off_main_thread(self):
        runner = BenchmarkRunner(timeout_seconds=0.05)
        observed: dict[str, object] = {}

        def invoke():
            try:
                runner._call_with_timeout(lambda: time.sleep(0.1))
            except Exception as exc:  # pragma: no cover - assertion inspects exact type
                observed["error"] = exc

        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join(timeout=1.0)

        assert not worker.is_alive()
        assert isinstance(observed.get("error"), BenchmarkTimeoutError)

    def test_python_typing_import_is_allowed(self):
        problem = make_problem()
        result = make_python_runner().evaluate_python(
            problem,
            "from typing import Optional\n\ndef identity(x: int) -> int:\n    maybe: Optional[int] = x\n    return maybe\n",
        )

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_python_evaluation_runs_in_fresh_worker(self, monkeypatch):
        problem = make_problem()

        def fail_if_parent_executes(*args, **kwargs):
            raise AssertionError("generated Python executed in the parent")

        monkeypatch.setattr(
            BenchmarkRunner,
            "_evaluate_python_in_process",
            fail_if_parent_executes,
        )

        result = make_python_runner().evaluate_python(
            problem,
            "def identity(x):\n    return x\n",
        )

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_python_worker_uses_explicit_bounded_memory_budget(self, monkeypatch):
        observed = {}

        class CapturingProcessSandbox:
            def __init__(self, config):
                observed["config"] = config

            def execute_python_benchmark(self, _request):
                return None, "", "stopped after config capture"

        monkeypatch.setattr(
            benchmark_runner,
            "ProcessSandbox",
            CapturingProcessSandbox,
        )

        result = make_python_runner().evaluate_python(
            make_problem(),
            "def identity(x):\n    return x\n",
        )

        assert result.error_category == ErrorCategory.RUNTIME
        assert observed["config"].max_memory_bytes == 512 * 1024 * 1024
        assert (
            benchmark_runner.ProcessSandboxConfig().max_memory_bytes
            == DEFAULT_PROCESS_MAX_MEMORY_BYTES
        )

    @pytest.mark.parametrize(
        "source",
        [
            (
                "import typing\n\n"
                "def identity(x):\n"
                "    return typing.sys.modules['os'].environ.get("
                "'GENO_BENCHMARK_PARENT_SECRET')\n"
            ),
            (
                "import typing\n\n"
                "def identity(x):\n"
                "    n = ''.join(['__buil', 'tins__'])\n"
                "    get_builtins = typing.operator.attrgetter(n)\n"
                "    real = get_builtins(typing.cast)\n"
                "    os = real['__import__']('os')\n"
                "    return os.environ.get('GENO_BENCHMARK_PARENT_SECRET')\n"
            ),
            ("import typing\ntyping.Any = 123\n\ndef identity(x):\n    return x\n"),
        ],
    )
    def test_python_typing_module_cannot_escape_or_mutate_parent(
        self, monkeypatch, source
    ):
        sentinel = "parent-provider-secret-7f6a"
        monkeypatch.setenv("GENO_BENCHMARK_PARENT_SECRET", sentinel)

        result = make_python_runner().evaluate_python(make_problem(), source)

        assert not result.all_passed
        assert sentinel not in result.error_message
        assert all(item.actual_output != sentinel for item in result.test_results)

    def test_python_three_argument_type_cannot_create_dynamic_class(self):
        source = (
            "def identity(x):\n"
            "    name = ''.join(['__get', 'attribute__'])\n"
            "    cls = type('C', (dict,), {name: dict.__getitem__})\n"
            "    return cls(public=x).public\n"
        )

        result = make_python_runner().evaluate_python(make_problem(), source)

        assert result.error_category == ErrorCategory.RUNTIME
        assert not result.all_passed
        assert "multiple arguments" in result.error_message

    @pytest.mark.parametrize(
        "constructor",
        ["typing.ABCMeta", "typing.NamedTupleMeta"],
    )
    def test_python_module_metaclass_constructors_are_blocked(self, constructor):
        module = constructor.partition(".")[0]
        source = (
            f"import {module}\n\n"
            "def identity(x):\n"
            "    name = ''.join(['__get', 'attribute__'])\n"
            f"    cls = {constructor}('C', (dict,), {{name: dict.__getitem__}})\n"
            "    return cls(public=x).public\n"
        )

        result = make_python_runner().evaluate_python(make_problem(), source)

        assert result.error_category == ErrorCategory.RUNTIME
        assert not result.all_passed
        assert "metaclass constructor" in result.error_message

    @pytest.mark.parametrize(
        "source",
        [
            (
                "def identity(x):\n"
                "    name = ''.join(['__get', 'attribute__'])\n"
                "    meta = type(type(0))\n"
                "    cls = meta('C', (dict,), {name: dict.__getitem__})\n"
                "    return cls(public=x).public\n"
            ),
            (
                "import typing\n\n"
                "def identity(x):\n"
                "    name = ''.join(['__get', 'attribute__'])\n"
                "    meta = typing.get_origin(typing.Type[int])\n"
                "    cls = meta('C', (dict,), {name: dict.__getitem__})\n"
                "    return cls(public=x).public\n"
            ),
        ],
    )
    def test_python_metaclass_results_are_blocked(self, source):
        result = make_python_runner().evaluate_python(make_problem(), source)

        assert result.error_category == ErrorCategory.RUNTIME
        assert not result.all_passed
        assert "metaclass" in result.error_message

    def test_python_worker_ignores_cwd_module_shadows(self, tmp_path, monkeypatch):
        marker = tmp_path / "shadow-imported"
        marker_write = f"open({str(marker)!r}, 'w').close()\n"
        (tmp_path / "base64.py").write_text(marker_write)
        package = tmp_path / "benchmark"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "runner.py").write_text(marker_write)
        monkeypatch.chdir(tmp_path)

        result = make_python_runner().evaluate_python(
            make_problem(), "def identity(x):\n    return x\n"
        )

        assert result.all_passed
        assert not marker.exists()

    def test_python_wrong_answer_details_survive_worker_json_round_trip(self):
        problem = make_problem()

        result = make_python_runner().evaluate_python(
            problem,
            "def identity(x):\n    return 0\n",
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert result.test_results[0].test_case is problem.visible_examples[0]
        assert result.test_results[0].actual_output == 0

    @pytest.mark.parametrize(
        ("source", "expected_message"),
        [
            (
                "import os\n\ndef identity(x):\n    return x\n",
                "Potentially dangerous import: os",
            ),
            (
                "from os import path\n\ndef identity(x):\n    return x\n",
                "Potentially dangerous import: os",
            ),
            (
                "def identity(x):\n    return eval('1')\n",
                "Potentially dangerous call: eval()",
            ),
            (
                "def identity(x):\n    return open('/tmp/demo')\n",
                "Potentially dangerous call: open()",
            ),
            (
                "def identity(x):\n    return identity.__globals__\n",
                "Potentially dangerous attribute access: __globals__",
            ),
            (
                "def identity(x):\n    return identity.__builtins__\n",
                "Potentially dangerous attribute access: __builtins__",
            ),
        ],
    )
    def test_python_sandbox_rejects_validation_warnings(self, source, expected_message):
        problem = make_problem()
        result = make_python_runner().evaluate_python(problem, source)

        assert result.error_category == ErrorCategory.RUNTIME
        assert "Code failed benchmark sandbox validation" in result.error_message
        assert expected_message in result.error_message

    @pytest.mark.parametrize(
        "source",
        [
            "import math\n\ndef identity(x):\n    return x\n",
            "from math import sqrt\n\ndef identity(x):\n    return x\n",
        ],
    )
    def test_python_sandbox_blocks_non_typing_imports_at_runtime(self, source):
        problem = make_problem()
        result = make_python_runner().evaluate_python(problem, source)

        assert result.error_category == ErrorCategory.RUNTIME
        assert "Blocked operation: __import__('math')" in result.error_message

    def test_single_list_argument_is_unwrapped_for_geno(self):
        problem = make_list_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_single_list_argument_is_unwrapped_for_python(self):
        problem = make_list_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_zero_arg_inputs_are_empty_for_geno_evaluation(self):
        problem = make_zero_arg_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_zero_arg_inputs_are_empty_for_python_evaluation(self):
        problem = make_zero_arg_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_unit_outputs_match_for_geno_evaluation(self):
        problem = make_unit_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_unit_outputs_match_for_python_evaluation(self):
        problem = make_unit_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_unit_equality_accepts_json_null_and_empty_list(self):
        runner = BenchmarkRunner()

        assert runner._values_equal(None, None, "()")
        assert runner._values_equal(None, [], "()")
        assert runner._values_equal((), None, "()")
        assert not runner._values_equal([], None, "()")
        assert not runner._values_equal([1], None, "()")
        assert not runner._values_equal(None, {"Some": None}, "Option[Unit]")
        assert not runner._values_equal({"Some": None}, None, "Option[Unit]")
        assert not runner._values_equal({"Some": None}, "None", "Option[Unit]")
        assert not runner._values_equal({"None": 123}, "None", "Option[Unit]")
        assert not runner._values_equal({"None": 123}, None, "Option[Int]")
        assert runner._values_equal(None, {"None": None}, "Option[Int]")
        assert runner._normalize_test_value({"None": None}, "Option[Int]") is None
        assert not runner._values_equal(5, 5, "Option[Int]")
        assert not runner._values_equal([1], [1], "Option[Int]")
        assert not runner._values_equal(5, 5, "Result[Int, String]")
        assert not runner._values_equal(None, {"Some": "None"}, "Option[Option[Int]]")
        assert not runner._values_equal({"Ok": 5}, {"Some": 5}, "Option[Int]")
        assert not runner._values_equal(5, {"Some": 5}, "Int")
        assert not runner._values_equal(5, {"Ok": 5}, "Int")
        assert not runner._values_equal({"Some": 5}, 5, "Int")
        assert not runner._values_equal(None, {"Some": None}, "Option[String]")
        assert not runner._values_equal({"Some": 5}, {"Some": 5}, "Result[Int, String]")
        assert runner._values_equal("None", {"Some": "None"}, "Option[String]")
        assert not runner._values_equal(
            {"Some": "None"}, {"Some": "None"}, "Option[String]"
        )
        assert not runner._values_equal(
            "None", {"Some": "None"}, "Option[String]", target="geno"
        )
        assert runner._values_equal(
            ConstructorValue("Some", {"value": "None"}),
            {"Some": "None"},
            "Option[String]",
            target="geno",
        )
        assert not runner._values_equal(
            "None", {"Some": {"Some": "None"}}, "Option[String]"
        )
        assert runner._values_equal(
            {"Err": "None"}, {"Err": "None"}, "Result[Int, String]"
        )

    def test_option_unit_outputs_match_for_geno_evaluation(self):
        problem = make_option_unit_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_unit_outputs_match_for_python_evaluation(self):
        problem = make_option_unit_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_unit_outputs_require_present_wrapper_for_python_evaluation(self):
        problem = make_option_unit_problem()
        result = make_python_runner().evaluate_python(
            problem,
            "def maybe_unit(flag):\n    return None\n",
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed
        assert result.test_results[1].passed

    def test_option_unit_absent_null_rejects_present_wrapper(self):
        problem = make_option_unit_problem()
        problem.visible_examples = [BenchmarkTestCase(True, None)]
        problem.hidden_tests = []

        result = make_python_runner().evaluate_python(
            problem,
            'def maybe_unit(flag):\n    return {"Some": None}\n',
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_nested_option_outputs_match_for_geno_evaluation(self):
        problem = make_nested_option_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_nested_option_outputs_match_for_python_evaluation(self):
        problem = make_nested_option_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_nested_option_outputs_require_outer_wrapper_for_python_evaluation(self):
        problem = make_nested_option_problem()
        result = make_python_runner().evaluate_python(
            problem,
            "def nested_option(x):\n    return None\n",
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_option_result_outputs_match_for_geno_evaluation(self):
        problem = make_option_result_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_result_outputs_match_for_python_evaluation(self):
        problem = make_option_result_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_result_outputs_require_outer_wrapper_for_python_evaluation(self):
        problem = make_option_result_problem()
        result = make_python_runner().evaluate_python(
            problem,
            'def optional_result(x):\n    return {"Ok": 5}\n',
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_option_string_none_outputs_match_for_geno_evaluation(self):
        problem = make_option_string_none_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_string_none_outputs_match_for_python_evaluation(self):
        problem = make_option_string_none_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_result_string_none_outputs_match_for_python_evaluation(self):
        problem = make_result_string_none_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_tuple_inputs_are_normalized_for_geno_evaluation(self):
        problem = make_option_tuple_input_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_tuple_inputs_are_normalized_for_python_evaluation(self):
        problem = make_option_tuple_input_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_tuple_wrapper_list_inputs_are_normalized_for_geno_evaluation(self):
        problem = make_option_tuple_input_problem()
        problem.visible_examples = [BenchmarkTestCase([{"Some": [1, 2]}], 3)]
        problem.hidden_tests = [BenchmarkTestCase(["None"], 0)]

        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_tuple_wrapper_list_inputs_are_normalized_for_python_evaluation(
        self,
    ):
        problem = make_option_tuple_input_problem()
        problem.visible_examples = [BenchmarkTestCase([{"Some": [1, 2]}], 3)]
        problem.hidden_tests = [BenchmarkTestCase(["None"], 0)]

        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_result_tuple_inputs_are_normalized_for_geno_evaluation(self):
        problem = make_result_tuple_input_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_result_tuple_inputs_are_normalized_for_python_evaluation(self):
        problem = make_result_tuple_input_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_result_tuple_wrapper_list_inputs_are_normalized_for_geno_evaluation(self):
        problem = make_result_tuple_input_problem()
        problem.visible_examples = [BenchmarkTestCase([{"Ok": [1, 2]}], 3)]
        problem.hidden_tests = [BenchmarkTestCase([{"Err": "bad"}], -1)]

        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_result_tuple_wrapper_list_inputs_are_normalized_for_python_evaluation(
        self,
    ):
        problem = make_result_tuple_input_problem()
        problem.visible_examples = [BenchmarkTestCase([{"Ok": [1, 2]}], 3)]
        problem.hidden_tests = [BenchmarkTestCase([{"Err": "bad"}], -1)]

        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_output_rejects_wrong_adt_wrapper_for_python_evaluation(self):
        problem = make_option_problem()
        result = make_python_runner().evaluate_python(
            problem,
            'def first_positive(items):\n    return {"Ok": 5}\n',
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_option_output_rejects_simple_some_wrapper_for_python_evaluation(self):
        problem = make_option_problem()
        result = make_python_runner().evaluate_python(
            problem,
            'def first_positive(items):\n    return {"Some": 5}\n',
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_option_output_rejects_bare_expected_value_for_python_evaluation(self):
        problem = make_option_problem()
        problem.visible_examples = [BenchmarkTestCase([[0, -1, 5]], 5)]
        problem.hidden_tests = []
        result = make_python_runner().evaluate_python(
            problem,
            "def first_positive(items):\n    return 5\n",
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_result_output_rejects_bare_expected_value_for_python_evaluation(self):
        problem = Problem(
            id="PROB-RESULT-BARE",
            name="Result Bare",
            difficulty=Difficulty.EASY,
            domain=Domain.MATH,
            description="Reject bare Result expected values.",
            function_name="get_value",
            inputs=[],
            output=TypeSignature("out", "Result[Int, String]"),
            visible_examples=[BenchmarkTestCase([], 5)],
            hidden_tests=[],
        )
        result = make_python_runner().evaluate_python(
            problem,
            "def get_value():\n    return 5\n",
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_scalar_output_rejects_adt_wrapper_for_python_evaluation(self):
        problem = make_problem()
        result = make_python_runner().evaluate_python(
            problem,
            'def identity(x):\n    return {"Some": x}\n',
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_tuple_inputs_are_normalized_for_geno_evaluation(self):
        problem = make_tuple_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_tuple_inputs_are_normalized_for_python_evaluation(self):
        problem = make_tuple_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_tuple_input_normalization_rejects_wrong_arity(self):
        runner = BenchmarkRunner()

        with pytest.raises(ValueError, match="Expected 2 values"):
            runner._normalize_test_value([1], "(Int, Int)")
        with pytest.raises(ValueError, match="Expected 2 values"):
            runner._normalize_test_value([1, 2, 3], "(Int, Int)")
        with pytest.raises(ValueError, match="Expected sequence"):
            runner._normalize_test_value(1, "(Int, Int)")

    def test_result_tuple_outputs_match_for_geno_evaluation(self):
        problem = make_result_tuple_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_result_tuple_outputs_match_for_python_evaluation(self):
        problem = make_result_tuple_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_result_tuple_outputs_require_wrapper_for_python_evaluation(self):
        problem = make_result_tuple_problem()
        result = make_python_runner().evaluate_python(
            problem,
            "def swap_result(pair):\n    a, b = pair\n    return (b, a)\n",
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_result_tuple_outputs_reject_list_payload_for_python_evaluation(self):
        problem = make_result_tuple_problem()
        result = make_python_runner().evaluate_python(
            problem,
            'def swap_result(pair):\n    a, b = pair\n    return {"Ok": [b, a]}\n',
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_tuple_output_rejects_list_actual_for_python_evaluation(self):
        problem = make_tuple_problem()
        result = make_python_runner().evaluate_python(
            problem,
            "def swap_pair(pair):\n    a, b = pair\n    return [b, a]\n",
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.test_results[0].passed

    def test_list_output_rejects_tuple_actual_for_python_evaluation(self):
        problem = Problem(
            id="PROB-LIST-OUTPUT",
            name="List Output",
            difficulty=Difficulty.EASY,
            domain=Domain.ARRAYS,
            description="Return the list unchanged.",
            function_name="identity_list",
            inputs=[TypeSignature("items", "List[Int]")],
            output=TypeSignature("out", "List[Int]"),
            visible_examples=[BenchmarkTestCase([[1, 2]], [1, 2])],
            hidden_tests=[],
        )

        result = make_python_runner().evaluate_python(
            problem,
            "def identity_list(items: list[int]) -> list[int]:\n"
            "    return (items[0], items[1])\n",
        )

        assert result.error_category == ErrorCategory.WRONG_ANSWER
        assert not result.all_passed

    def test_option_outputs_match_benchmark_schema_for_geno(self):
        problem = make_option_problem()
        result = BenchmarkRunner().evaluate_geno(problem, problem.geno_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_option_outputs_match_benchmark_schema_for_python(self):
        problem = make_option_problem()
        result = make_python_runner().evaluate_python(problem, problem.python_solution)

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_geno_evaluation_ignores_main_function(self):
        problem = make_problem()
        result = BenchmarkRunner(timeout_seconds=0.05).evaluate_geno(
            problem,
            """
            func identity(x: Int) -> Int
                example 1 -> 1
                return x
            end func

            func main() -> Int
                while true do
                end while
                return 0
            end func
            """,
        )

        assert result.error_category == ErrorCategory.NONE
        assert result.all_passed

    def test_python_timeout_is_reported_off_main_thread(self):
        problem = make_problem()
        observed: dict[str, object] = {}

        def invoke():
            observed["result"] = make_python_runner(
                timeout_seconds=0.05
            ).evaluate_python(
                problem,
                "import time\n\ndef identity(x):\n    time.sleep(0.1)\n    return x\n",
                sandboxed=False,
            )

        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join(timeout=1.0)

        assert not worker.is_alive()
        result = observed["result"]
        assert isinstance(result, EvaluationResult)
        assert result.error_category == ErrorCategory.TIMEOUT
        assert result.test_results[0].error_category == ErrorCategory.TIMEOUT

    def test_python_timeout_includes_delay_before_join(self, monkeypatch):
        observed: dict[str, object] = {}
        real_thread = threading.Thread

        class DelayedStartThread(threading.Thread):
            def start(self) -> None:
                super().start()
                self.join(timeout=1.0)

        def invoke():
            with monkeypatch.context() as patch:
                patch.setattr(
                    benchmark_runner,
                    "threading",
                    SimpleNamespace(
                        Thread=DelayedStartThread,
                        current_thread=threading.current_thread,
                        main_thread=threading.main_thread,
                    ),
                )
                try:
                    make_python_runner(timeout_seconds=0.05)._call_with_timeout(
                        lambda: time.sleep(0.1)
                    )
                except BenchmarkTimeoutError as exc:
                    observed["error"] = exc

        worker = real_thread(target=invoke)
        worker.start()
        worker.join(timeout=1.0)

        assert not worker.is_alive()
        assert isinstance(observed.get("error"), BenchmarkTimeoutError)

    def test_python_runtime_error_propagates_off_main_thread(self):
        problem = make_problem()
        observed: dict[str, object] = {}

        def invoke():
            observed["result"] = make_python_runner(
                timeout_seconds=0.05
            ).evaluate_python(
                problem,
                "def identity(x):\n    raise ValueError('boom')\n",
            )

        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join(timeout=1.0)

        assert not worker.is_alive()
        result = observed["result"]
        assert isinstance(result, EvaluationResult)
        assert result.error_category == ErrorCategory.RUNTIME
        assert result.test_results[0].error_category == ErrorCategory.RUNTIME
        assert "boom" in result.test_results[0].error_message


class TestExperimentRunner:
    def test_run_returns_serializable_payload(self):
        config = ExperimentConfig(
            experiment_id="exp-test",
            models=["canonical"],
            languages=["python"],
            trials_per_condition=1,
            problems=[make_problem()],
        )

        payload = ExperimentRunner(config).run()

        assert payload["config"]["experiment_id"] == "exp-test"
        assert payload["config"]["problem_ids"] == ["PROB-TEST"]
        assert len(payload["generation_results"]) == 1
        assert len(payload["evaluation_results"]) == 1
        generation = payload["generation_results"][0]
        assert (
            generation["prompt_hash"]
            == hashlib.sha256(generation["prompt"].encode("utf-8")).hexdigest()
        )

    def test_few_shot_examples_are_in_actual_generation_prompt(self):
        config = ExperimentConfig(
            experiment_id="exp-test",
            models=["canonical"],
            languages=["geno"],
            trials_per_condition=1,
            few_shot_examples=2,
            problems=[make_problem()],
        )

        payload = ExperimentRunner(config).run()
        prompt = payload["generation_results"][0]["prompt"]

        assert "## Example Solutions" in prompt
        assert "func double" in prompt
        assert "func sum_list" in prompt

    def test_pass_at_k_metrics_are_grouped_by_problem(self):
        config = ExperimentConfig(
            experiment_id="exp-pass-k",
            models=["m"],
            languages=["geno"],
            trials_per_condition=2,
            problems=[make_problem(), make_list_problem()],
        )
        runner = ExperimentRunner(config)

        def generation(problem_id: str, trial: int) -> GenerationResult:
            return GenerationResult(
                model="m",
                problem_id=problem_id,
                language="geno",
                trial=trial,
                prompt="",
                raw_response="",
                extracted_code="",
                generation_time_ms=0.0,
                prompt_hash="hash",
            )

        def evaluation(problem_id: str, passed: bool) -> EvaluationResult:
            return EvaluationResult(
                problem_id=problem_id,
                language="geno",
                solution_code="",
                parsed=True,
                type_checked=True,
                visible_passed=1 if passed else 0,
                visible_total=1,
                hidden_passed=0,
                hidden_total=0,
            )

        runner.run_data.generation_results = [
            generation("PROB-TEST", 0),
            generation("PROB-TEST", 1),
            generation("PROB-LIST", 0),
            generation("PROB-LIST", 1),
        ]
        runner.run_data.evaluation_results = [
            evaluation("PROB-TEST", False),
            evaluation("PROB-TEST", True),
            evaluation("PROB-LIST", False),
            evaluation("PROB-LIST", False),
        ]

        runner._compute_metrics()

        metrics = runner.run_data.metrics["m_geno"]
        assert metrics["n_problems"] == 2
        assert metrics["pass_at_1"] == 0.0
        assert metrics["pass_at_k"] == 0.5
        assert metrics["pass_k"] == 2
        assert metrics["overall_pass_rate"] == 0.25

    def test_pass_at_k_uses_attempt_count_not_trial_id(self):
        config = ExperimentConfig(
            experiment_id="exp-pass-k-sparse",
            models=["m"],
            languages=["geno"],
            problems=[make_problem()],
        )
        runner = ExperimentRunner(config)

        def generation(trial: int) -> GenerationResult:
            return GenerationResult(
                model="m",
                problem_id="PROB-TEST",
                language="geno",
                trial=trial,
                prompt="",
                raw_response="",
                extracted_code="",
                generation_time_ms=0.0,
                prompt_hash="hash",
            )

        def evaluation(passed: bool) -> EvaluationResult:
            return EvaluationResult(
                problem_id="PROB-TEST",
                language="geno",
                solution_code="",
                parsed=True,
                type_checked=True,
                visible_passed=1 if passed else 0,
                visible_total=1,
                hidden_passed=0,
                hidden_total=0,
            )

        metrics = runner._compute_pass_at_metrics(
            [
                (generation(2), evaluation(False)),
                (generation(4), evaluation(True)),
            ]
        )

        assert metrics["pass_at_1"] == 0.0
        assert metrics["pass_at_k"] == 1.0
        assert metrics["pass_k"] == 2

    def test_results_analyzer_accepts_pathlike(self, tmp_path):
        config = ExperimentConfig(
            experiment_id="exp-test",
            models=["canonical"],
            languages=["python"],
            trials_per_condition=1,
            problems=[make_problem()],
        )

        runner = ExperimentRunner(config)
        runner.run()
        runner.save_results(str(tmp_path))

        analyzer = ResultsAnalyzer(tmp_path)
        results = analyzer.run_all_analyses()

        assert "canonical_python" in results.summary_stats

    def test_results_analyzer_primary_comparison_counts_each_trial(self):
        payload = {
            "config": {
                "models": ["m"],
                "languages": ["geno", "python"],
                "trials_per_condition": 2,
            },
            "generations": [
                {
                    "model": "m",
                    "problem_id": "PROB-001",
                    "language": "geno",
                    "trial": 0,
                },
                {
                    "model": "m",
                    "problem_id": "PROB-001",
                    "language": "python",
                    "trial": 0,
                },
                {
                    "model": "m",
                    "problem_id": "PROB-001",
                    "language": "geno",
                    "trial": 1,
                },
                {
                    "model": "m",
                    "problem_id": "PROB-001",
                    "language": "python",
                    "trial": 1,
                },
            ],
            "evaluations": [
                {
                    "all_passed": False,
                    "pass_rate": 0.0,
                    "error_category": "wrong_answer",
                },
                {
                    "all_passed": False,
                    "pass_rate": 0.0,
                    "error_category": "wrong_answer",
                },
                {"all_passed": True, "pass_rate": 1.0, "error_category": "none"},
                {
                    "all_passed": False,
                    "pass_rate": 0.0,
                    "error_category": "wrong_answer",
                },
            ],
        }

        results = ResultsAnalyzer(payload).run_all_analyses()
        comparison = results.primary_comparison["m"]

        assert comparison["n_problems"] == 2
        assert comparison["n_problem_trials"] == 2
        assert comparison["unique_problems"] == 1
        assert comparison["geno_pass_rate"] == 0.5
        assert comparison["python_pass_rate"] == 0.0
        assert comparison["contingency"] == {
            "both_pass": 0,
            "geno_only": 1,
            "python_only": 0,
            "both_fail": 1,
        }

    def test_results_analyzer_uses_benchmark_difficulty_metadata(self):
        analyzer = ResultsAnalyzer({"config": {}, "generations": [], "evaluations": []})

        mismatches = [
            (
                problem.id,
                problem.difficulty.value,
                analyzer._infer_difficulty(problem.id),
            )
            for problem in load_all_problems()
            if problem.difficulty.value != analyzer._infer_difficulty(problem.id)
        ]

        assert mismatches == []

    def test_results_analyzer_difficulty_analysis_uses_metadata_bucket(self):
        problem = next(
            problem for problem in load_all_problems() if problem.id == "PROB-008"
        )
        assert problem.difficulty == Difficulty.EASY
        payload = {
            "config": {
                "models": ["m"],
                "languages": ["geno"],
                "trials_per_condition": 1,
            },
            "generations": [
                {
                    "model": "m",
                    "problem_id": problem.id,
                    "difficulty": "trivial",
                    "language": "geno",
                    "trial": 0,
                },
            ],
            "evaluations": [
                {"all_passed": True, "pass_rate": 1.0, "error_category": "none"},
            ],
        }

        results = ResultsAnalyzer(payload).run_all_analyses()

        assert results.difficulty_analysis == {
            "easy": {"m_geno": {"n": 1, "pass_rate": 1.0}}
        }

    def test_results_analyzer_uses_benchmark_domain_metadata(self):
        analyzer = ResultsAnalyzer({"config": {}, "generations": [], "evaluations": []})

        mismatches = [
            (
                problem.id,
                problem.domain.value,
                analyzer._infer_domain(problem.id),
            )
            for problem in load_all_problems()
            if problem.domain.value != analyzer._infer_domain(problem.id)
        ]

        assert mismatches == []

    def test_results_analyzer_domain_analysis_uses_metadata_bucket(self):
        problem = next(
            problem for problem in load_all_problems() if problem.id == "PROB-008"
        )
        assert problem.domain == Domain.RECURSION
        payload = {
            "config": {
                "models": ["m"],
                "languages": ["geno"],
                "trials_per_condition": 1,
            },
            "generations": [
                {
                    "model": "m",
                    "problem_id": problem.id,
                    "domain": "math",
                    "language": "geno",
                    "trial": 0,
                },
            ],
            "evaluations": [
                {"all_passed": True, "pass_rate": 1.0, "error_category": "none"},
            ],
        }

        results = ResultsAnalyzer(payload).run_all_analyses()

        assert results.domain_analysis == {
            "recursion": {"m_geno": {"n": 1, "pass_rate": 1.0}}
        }

    def test_results_analyzer_summary_includes_domain_analysis(self):
        payload = {
            "config": {
                "models": ["m"],
                "languages": ["geno"],
                "trials_per_condition": 1,
            },
            "generations": [
                {
                    "model": "m",
                    "problem_id": "custom-problem",
                    "domain": Domain.MATH,
                    "language": "geno",
                    "trial": 0,
                },
            ],
            "evaluations": [
                {"all_passed": False, "pass_rate": 0.0, "error_category": "none"},
            ],
        }
        analyzer = ResultsAnalyzer(payload)

        analyzer.run_all_analyses()
        report = analyzer.get_summary_table()

        assert "## By Domain" in report
        assert "MATH" in report
        assert "m_geno: 0.0% (n=1)" in report

    def test_report_generator_includes_domain_analysis(self):
        problem = next(
            problem for problem in load_all_problems() if problem.id == "PROB-008"
        )
        payload = {
            "config": {
                "models": ["m"],
                "languages": ["geno", "python"],
                "trials_per_condition": 1,
            },
            "generations": [
                {
                    "model": "m",
                    "problem_id": problem.id,
                    "language": "geno",
                    "trial": 0,
                },
                {
                    "model": "m",
                    "problem_id": problem.id,
                    "language": "python",
                    "trial": 0,
                },
            ],
            "evaluations": [
                {"all_passed": True, "pass_rate": 1.0, "error_category": "none"},
                {
                    "all_passed": False,
                    "pass_rate": 0.0,
                    "error_category": "wrong_answer",
                },
            ],
        }
        analyzer = ResultsAnalyzer(payload)
        analyzer.run_all_analyses()
        generator = ReportGenerator(analyzer)

        full_report = generator._generate_results_section()
        markdown_report = generator.generate_markdown_report()

        assert "RESULTS BY DOMAIN" in full_report
        assert "RECURSION" in full_report
        assert "m_geno" in full_report
        assert "## Results by Domain" in markdown_report
        assert "| Domain | m_geno | m_python |" in markdown_report
        assert "| recursion | 100.0% | 0.0% |" in markdown_report

    def test_report_generator_domain_markdown_keeps_condition_columns(self):
        problem = next(
            problem for problem in load_all_problems() if problem.id == "PROB-008"
        )
        payload = {
            "config": {
                "models": ["geno-model", "python-model", "m"],
                "languages": ["js", "geno", "python"],
                "trials_per_condition": 1,
            },
            "generations": [
                {
                    "model": "geno-model",
                    "problem_id": problem.id,
                    "language": "js",
                    "trial": 0,
                },
                {
                    "model": "m",
                    "problem_id": problem.id,
                    "language": "python",
                    "trial": 0,
                },
                {
                    "model": "python-model",
                    "problem_id": problem.id,
                    "language": "geno",
                    "trial": 0,
                },
            ],
            "evaluations": [
                {"all_passed": True, "pass_rate": 1.0, "error_category": "none"},
                {
                    "all_passed": False,
                    "pass_rate": 0.0,
                    "error_category": "wrong_answer",
                },
                {"all_passed": True, "pass_rate": 1.0, "error_category": "none"},
            ],
        }
        analyzer = ResultsAnalyzer(payload)
        analyzer.run_all_analyses()

        markdown_report = ReportGenerator(analyzer).generate_markdown_report()

        assert (
            "| Domain | geno-model_js | m_python | python-model_geno |"
            in markdown_report
        )
        assert "| recursion | 100.0% | 0.0% | 100.0% |" in markdown_report

    def test_results_analyzer_serializes_problem_results(self, tmp_path):
        payload = {
            "config": {
                "models": ["m"],
                "languages": ["geno", "python"],
                "trials_per_condition": 1,
            },
            "generations": [
                {
                    "model": "m",
                    "problem_id": "PROB-001",
                    "language": "geno",
                    "trial": 0,
                },
                {
                    "model": "m",
                    "problem_id": "PROB-001",
                    "language": "python",
                    "trial": 0,
                },
            ],
            "evaluations": [
                {"all_passed": True, "pass_rate": 1.0, "error_category": "none"},
                {
                    "all_passed": False,
                    "pass_rate": 0.0,
                    "error_category": "wrong_answer",
                },
            ],
        }
        analyzer = ResultsAnalyzer(payload)
        results = analyzer.run_all_analyses()
        output_path = tmp_path / "analysis.json"

        analyzer.save_analysis(str(output_path))
        saved = json.loads(output_path.read_text(encoding="utf-8"))

        assert saved["problem_results"] == results.problem_results
        assert saved["problem_results"] == [
            {
                "problem_id": "PROB-001",
                "m_geno": {
                    "passed": True,
                    "pass_rate": 1.0,
                    "error": "none",
                },
                "m_python": {
                    "passed": False,
                    "pass_rate": 0.0,
                    "error": "wrong_answer",
                },
            }
        ]

    def test_save_results_writes_unicode_report_as_utf8(self, tmp_path, monkeypatch):
        config = ExperimentConfig(
            experiment_id="exp-test",
            models=["canonical"],
            languages=["geno", "python"],
            trials_per_condition=1,
            problems=[make_problem()],
        )
        runner = ExperimentRunner(config)
        metrics = {
            "parse_success_rate": 1.0,
            "typecheck_success_rate": 1.0,
            "visible_pass_rate": 1.0,
            "hidden_pass_rate": 1.0,
            "overall_pass_rate": 1.0,
        }
        runner.run_data.metrics = {
            "canonical_geno": metrics,
            "canonical_python": metrics,
            "canonical_comparison": {
                "pass_rate_diff": 0.0,
                "relative_improvement": 0.0,
                "mcnemar_statistic": 1.23,
            },
        }
        real_open = builtins.open

        def cp1252_default_open(file, mode="r", *args, **kwargs):
            if "b" not in mode and any(flag in mode for flag in ("w", "a", "x")):
                kwargs.setdefault("encoding", "cp1252")
            return real_open(file, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", cp1252_default_open)

        runner.save_results(str(tmp_path))

        report = (tmp_path / "report.txt").read_text(encoding="utf-8")
        assert "McNemar's \u03c7\xb2: 1.230" in report


class TestBenchmarkVerification:
    def test_verify_problem_set_returns_summary(self):
        problem = load_all_problems()[0]
        report = verify_problem_set([problem], verbose=False)

        assert report["total_problems"] == 1
        assert "difficulty_distribution" in report
        assert "issues" in report

    def test_verify_problem_set_preserves_problem_ids_with_underscores(self):
        problem = make_problem()
        problem.id = "PROB_SET_1"
        problem.python_solution = "def identity(x):\n    return 0\n"
        report = verify_problem_set([problem], verbose=False)

        assert "PROB_SET_1" in report["issues"]

    def test_validate_problem_skips_empty_collection_warning_when_disallowed(self):
        problem = make_list_problem()
        problem.description = "Return the count from a non-empty list."
        problem.hidden_tests = [
            BenchmarkTestCase([[1, 2]], 2),
            BenchmarkTestCase([[3]], 1),
            BenchmarkTestCase([[4, 5, 6]], 3),
        ]

        issues = validate_problem(problem)

        assert "Consider adding empty collection test case" not in issues

    def test_benchmark_analysis_reports_domain_budgets_and_drift_snapshot(self):
        math_problem = make_problem()
        math_problem.constructs_tested = ["function"]
        array_problem = make_list_problem()
        array_problem.difficulty = Difficulty.HARD
        array_problem.constructs_tested = ["function", "for", "match"]
        report = {
            "valid_problems": 2,
            "problems_with_issues": 0,
        }

        analysis = build_benchmark_analysis([math_problem, array_problem], report)

        assert analysis["totals"]["problems"] == 2
        assert analysis["domain_analysis"]["math"]["problem_ids"] == ["PROB-TEST"]
        assert analysis["domain_analysis"]["arrays"]["hard_expert_problem_ids"] == [
            "PROB-LIST"
        ]
        assert analysis["construct_distribution"]["function"] == 2
        assert {
            "constructs": ["for", "function", "match"],
            "count": 1,
            "problem_ids": ["PROB-LIST"],
        } in analysis["construct_combination_distribution"]
        assert "thin_domains" in analysis["budget_findings"]
        assert analysis["drift_snapshot"]["domain_distribution"]["math"] == 1
        assert (
            analysis["drift_snapshot"]["construct_combination_distribution"]
            == analysis["construct_combination_distribution"]
        )
        assert analysis["environment"]["python_version"]


class TestScripts:
    def _write_analyze_results_payload(self, tmp_path):
        config = ExperimentConfig(
            experiment_id="exp-test",
            models=["canonical"],
            languages=["geno", "python"],
            trials_per_condition=1,
            problems=[make_problem()],
        )
        payload = ExperimentRunner(config).run()
        results_path = tmp_path / "results.json"
        results_path.write_text(json.dumps(payload), encoding="utf-8")
        return results_path

    def test_analysis_scripts_run_from_repo_root(self):
        for script_name, expected in [
            ("analyzer.py", "Usage: python analyzer.py <results_dir>"),
            (
                "report_generator.py",
                "Usage: python report_generator.py <results_dir> [output_file]",
            ),
        ]:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "analysis" / script_name),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

            assert proc.returncode == 0, proc.stderr
            assert expected in proc.stdout

    def test_run_experiment_dry_run(self, tmp_path):
        output_path = tmp_path / "results.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_experiment.py"),
                "--models",
                "canonical",
                "--trials",
                "1",
                "--output",
                str(output_path),
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        assert "Dry run - not executing experiment" in proc.stdout

    def test_run_experiment_accepts_config_file(self, tmp_path):
        config_path = tmp_path / "experiment.yaml"
        config_path.write_text(
            """
models:
  - name: "canonical"
benchmark:
  difficulties:
    - expert
trials:
  per_condition: 1
output:
  directory: "results/from-config"
""",
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_experiment.py"),
                "--config",
                str(config_path),
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        assert "Models: canonical" in proc.stdout
        assert "Filtered to " in proc.stdout
        assert "Results JSON: results.json" in proc.stdout
        assert "Artifacts Dir: results/from-config" in proc.stdout

    def test_run_experiment_dry_run_with_difficulty_filter(self, tmp_path):
        output_path = tmp_path / "results.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_experiment.py"),
                "--models",
                "canonical",
                "--trials",
                "1",
                "--difficulties",
                "easy",
                "--output",
                str(output_path),
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        assert "Filtered to " in proc.stdout
        assert "Filtered to 0 problems" not in proc.stdout

    def test_run_experiment_canonical_model_runs_without_flag(self, tmp_path):
        results_path = tmp_path / "results.json"
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_experiment.py"),
                "--models",
                "canonical",
                "--trials",
                "1",
                "--difficulties",
                "expert",
                "--output",
                str(results_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        assert results_path.is_file()
        payload = json.loads(results_path.read_text(encoding="utf-8"))
        assert payload["config"]["models"] == ["canonical"]
        assert "Unknown model provider" not in proc.stdout
        assert "Using canonical solutions" in proc.stdout

    def test_analyze_results_summary_only(self, tmp_path):
        config = ExperimentConfig(
            experiment_id="exp-test",
            models=["canonical"],
            languages=["python"],
            trials_per_condition=1,
            problems=[make_problem()],
        )
        payload = ExperimentRunner(config).run()

        results_path = tmp_path / "results.json"
        report_path = tmp_path / "report.txt"
        results_path.write_text(json.dumps(payload), encoding="utf-8")

        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "analyze_results.py"),
                "--input",
                str(results_path),
                "--output",
                str(report_path),
                "--summary-only",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        report = report_path.read_text(encoding="utf-8")
        assert "ANALYSIS SUMMARY" in report

    def test_analyze_results_respects_explicit_format(self, tmp_path):
        results_path = self._write_analyze_results_payload(tmp_path)
        cases = [
            (
                "markdown",
                tmp_path / "report.md",
                "# Geno Benchmark Evaluation Report",
                "GENOTYPE BENCHMARK EVALUATION REPORT",
            ),
            (
                "text",
                tmp_path / "report.txt",
                "GENOTYPE BENCHMARK EVALUATION REPORT",
                "# Geno Benchmark Evaluation Report",
            ),
            (
                "latex",
                tmp_path / "report.tex",
                "\\begin{table}",
                "# Geno Benchmark Evaluation Report",
            ),
        ]

        for report_format, report_path, expected, unexpected in cases:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "analyze_results.py"),
                    "--input",
                    str(results_path),
                    "--output",
                    str(report_path),
                    "--format",
                    report_format,
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

            assert proc.returncode == 0, proc.stderr
            report = report_path.read_text(encoding="utf-8")
            assert expected in report
            assert unexpected not in report

    def test_analyze_results_default_format_is_markdown(self, tmp_path):
        results_path = self._write_analyze_results_payload(tmp_path)
        report_path = tmp_path / "report.md"

        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "analyze_results.py"),
                "--input",
                str(results_path),
                "--output",
                str(report_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        report = report_path.read_text(encoding="utf-8")
        assert "# Geno Benchmark Evaluation Report" in report
        assert "GENOTYPE BENCHMARK EVALUATION REPORT" not in report

    def test_analyze_results_fails_closed_on_analysis_error(self, tmp_path):
        results_path = tmp_path / "bad-results.json"
        report_path = tmp_path / "report.md"
        results_path.write_text(
            json.dumps(
                {
                    "config": {},
                    "generations": ["not a generation object"],
                    "evaluations": [{"all_passed": True}],
                }
            ),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "analyze_results.py"),
                "--input",
                str(results_path),
                "--output",
                str(report_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 1
        assert "ERROR: Analysis failed" in proc.stdout
        assert "Generating demo report instead" not in proc.stdout
        assert not report_path.exists()

    def test_analyze_results_placeholder_generates_demo_report(self, tmp_path):
        results_path = tmp_path / "placeholder-results.json"
        report_path = tmp_path / "report.md"
        results_path.write_text(
            json.dumps({"status": "not_implemented"}),
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "analyze_results.py"),
                "--input",
                str(results_path),
                "--output",
                str(report_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        assert "Note: Results contain placeholder data." in proc.stdout
        assert "Analysis failed" not in proc.stdout
        assert "Geno Experiment Results (Demo)" in report_path.read_text(
            encoding="utf-8"
        )

    def test_validate_benchmark_writes_json_analysis(self, tmp_path):
        report_path = tmp_path / "benchmark-analysis.json"

        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "validate_benchmark.py"),
                "--json-output",
                str(report_path),
                "--strict-budgets",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert payload["totals"]["problems"] == 78
        assert "domain_analysis" in payload
        assert "drift_snapshot" in payload
        assert "construct_combination_distribution" in payload["drift_snapshot"]
        assert payload["budget_findings"]["thin_domains"] == []

    def test_publish_benchmark_results(self, tmp_path):
        config = ExperimentConfig(
            experiment_id="exp-test",
            models=["canonical"],
            languages=["geno", "python"],
            trials_per_condition=1,
            problems=[make_problem()],
        )
        payload = ExperimentRunner(config).run()

        results_path = tmp_path / "results.json"
        report_path = tmp_path / "benchmark-results.md"
        results_path.write_text(json.dumps(payload), encoding="utf-8")

        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "publish_benchmark_results.py"),
                "--input",
                str(results_path),
                "--output",
                str(report_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 0, proc.stderr
        report = report_path.read_text(encoding="utf-8")
        assert "# Geno vs Python Benchmark Results" in report
        assert "| canonical |" in report


class TestPromptSpecExcerpt:
    """Guard the prompt-visible language excerpt against known coverage gaps.

    Pilot-run failures traced directly to constructs the benchmark problems
    require but the zero-shot spec excerpt did not document.
    """

    def test_spec_documents_for_loops(self):
        from experiment.prompts import GENOTYPE_SPEC

        assert "end for" in GENOTYPE_SPEC
        assert "for x: Int in" in GENOTYPE_SPEC

    def test_spec_documents_named_argument_rule(self):
        from experiment.prompts import GENOTYPE_SPEC

        assert "Named Arguments" in GENOTYPE_SPEC
        assert "slice(list: xs, start: 0, stop: 2)" in GENOTYPE_SPEC

    def test_spec_documents_string_and_fold_builtins(self):
        from experiment.prompts import GENOTYPE_SPEC

        for needle in (
            "fold(list, initial, fn)",
            "split(text, delimiter)",
            "trim(text)",
            "starts_with(text, prefix)",
            "parse_int(text) -> Option[Int]",
            "to_string(value)",
        ):
            assert needle in GENOTYPE_SPEC, f"spec excerpt missing: {needle}"
