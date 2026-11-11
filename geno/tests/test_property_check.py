"""The property-check engine, ``geno.property_check`` (proposal 0017).

Each eligible function is called on generated arguments and judged against
its own ``requires``/``ensures``: a top-level precondition failure discards
the input, an ``ensures`` failure, any other panic and any resource limit
fail it, and a ``panics`` example exempts panics. These tests drive the
engine as a library; the ``geno test --properties`` command line has its own
tests.
"""

from __future__ import annotations

import ast
import itertools
import json
import os
import random
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from geno import property_check
from geno.interpreter import Interpreter
from geno.lexer import Lexer
from geno.parser import Parser, parse_expression
from geno.property_check import (
    MAX_SAFE_INT,
    Generators,
    PropertyConfig,
    PropertyReport,
    PropertyResult,
    Sample,
    check_module_properties,
    render,
    render_arguments,
    render_float,
    trial_size,
)
from geno.sandbox import SandboxConfig
from geno.typechecker import TypeChecker
from geno.types import FuncType, Type
from geno.values import ConstructorValue, ContractViolationError

REPO_ROOT = Path(__file__).resolve().parents[2]

ABS_BUG = """\
func abs_val(n: Int) -> Int
    ensures result >= 0
    example 3 -> 3
    if n >= 0 then
        return n
    end if
    return n
end func
"""

SPIN = """\
func spin(n: Int) -> Int
    example 0 -> 0
    var i = 0
    while n > 0 do
        i = i + 1
    end while
    return i
end func
"""


# Step-cheap but slow: ten builtin sorts of 20,000 elements use few steps.
SLOW_SORT = """\
func slow(b: Bool) -> Int
    example true -> 10
    let big = reverse(range(0, 20000))
    var i = 0
    while i < 10 do
        let s = sort(big)
        i = i + 1
    end while
    return i
end func
"""


def _load(source: str, filename: str = "props.geno") -> tuple[str, Any, TypeChecker]:
    text = textwrap.dedent(source)
    program = Parser(Lexer(text, filename).tokenize(), text).parse_program()
    checker = TypeChecker()
    checker.check_program(program)
    return text, program, checker


def check(
    source: str,
    *,
    module: str = "props",
    path: str = "props.geno",
    sandbox_config: SandboxConfig | None = None,
    **config: Any,
) -> dict[str, PropertyResult]:
    """Property-check every function of *source*; results by function name."""
    text, program, checker = _load(source, path)
    results = check_module_properties(
        program,
        checker,
        module=module,
        path=path,
        source=text,
        config=PropertyConfig(**config),
        sandbox_config=sandbox_config,
    )
    return {result.function: result for result in results}


def _first_seed(
    source: str, name: str, predicate: Any, **config: Any
) -> PropertyResult:
    """The result for the first seed (0, 1, ...) whose result satisfies *predicate*."""
    for seed in range(200):
        result = check(source, seed=seed, **config)[name]
        if predicate(result):
            return result
    raise AssertionError("no seed in 0..199 produced the wanted result")


# =============================================================================
# Finding, shrinking and reporting failures
# =============================================================================


class TestEnsuresFailures:
    def test_abs_val_bug_is_found_with_clause_and_result(self):
        result = check(ABS_BUG)["abs_val"]

        assert result.status == "failed"
        assert result.failure is not None
        assert result.failure.kind == "ensures"
        assert result.failure.clause == "result >= 0"
        assert result.failure.result == "-1"
        assert result.failure.summary == "ensures result >= 0 failed (result was -1)"
        assert result.counterexample is not None
        assert result.counterexample.arguments == "-1"

    def test_a_large_counterexample_shrinks_to_minus_one(self):
        result = _first_seed(
            ABS_BUG,
            "abs_val",
            lambda r: (
                r.counterexample is not None
                and r.counterexample.original not in ("-1",)
            ),
        )

        assert result.counterexample is not None
        assert result.counterexample.arguments == "-1"
        assert result.counterexample.shrink_steps > 0
        assert int(result.counterexample.original) < -1

    def test_a_hundred_trials_take_well_under_a_second(self):
        source = ABS_BUG.replace("    return n\nend func", "    return 0 - n\nend func")
        import time

        started = time.perf_counter()
        result = check(source)["abs_val"]
        elapsed = time.perf_counter() - started

        assert result.status == "passed"
        assert result.trials_run == 100
        # Locally this takes well under a second; the budget leaves room for
        # coverage instrumentation and slow hosted runners while still
        # catching a pathological slowdown.
        assert elapsed < 10.0

    def test_clause_text_drops_the_keyword_and_a_trailing_comment(self):
        source = """\
        func neg(n: Int) -> Int
            ensures -result <= 0  // never positive "really"
            example 0 -> 0
            return n
        end func
        """
        result = check(source)["neg"]

        assert result.failure is not None
        assert result.failure.clause == "-result <= 0"

    def test_the_failing_clause_is_named_by_its_index(self):
        source = """\
        func pick(n: Int) -> Int
            ensures result >= -5000
            ensures result != 7
            example 0 -> 0
            if n == 7 then
                return 7
            end if
            return 0
        end func
        """
        result = check(source, trials=1000)["pick"]

        assert result.failure is not None
        assert result.failure.clause == "result != 7"
        assert result.counterexample is not None
        assert result.counterexample.arguments == "7"

    def test_nested_ensures_failure_is_a_panic(self):
        source = """\
        func inner(n: Int) -> Int
            ensures result > 0
            example 1 -> 1
            return n
        end func

        func outer(n: Int) -> Int
            example 1 -> 1
            return inner(n)
        end func
        """
        result = check(source)["outer"]

        assert result.failure is not None
        assert result.failure.kind == "panic"
        assert result.failure.message == (
            "Postcondition failed: inner(n: 0) ensures result > 0 (result was 0)"
        )
        assert result.counterexample is not None
        assert result.counterexample.arguments == "0"

    def test_without_contracts_a_panic_is_the_property(self):
        source = """\
        func div(a: Int, b: Int) -> Int
            example 6, 3 -> 2
            return a / b
        end func
        """
        result = check(source)["div"]

        assert result.status == "failed"
        assert result.failure is not None
        assert result.failure.kind == "panic"
        assert result.counterexample is not None
        assert result.counterexample.arguments == "0, 0"


class TestRequires:
    def test_requires_failures_are_discarded_and_counted(self):
        source = """\
        func half(n: Int) -> Int
            requires n % 2 == 0
            ensures result * 2 == n
            example 4 -> 2
            return n / 2
        end func
        """
        result = check(source)["half"]

        assert result.status == "passed"
        assert result.trials_run == 100
        assert result.discarded > 0

    def test_a_requires_that_propagates_is_a_failure_not_a_discard(self):
        source = """\
        func first_positive(xs: List[Int]) -> Option[Int]
            requires list_first(xs)? > 0
            example [1] -> Some(1)
            return list_first(xs)
        end func
        """
        result = check(source)["first_positive"]

        assert result.status == "failed"
        assert result.failure is not None
        assert result.failure.kind == "panic"
        assert result.failure.message == (
            "Precondition failed: first_positive(xs: []) requires "
            "list_first(xs)? > 0 ('?' propagated None/Err)"
        )
        assert result.counterexample is not None
        assert result.counterexample.arguments == "[]"

    def test_a_requires_that_panics_is_a_failure(self):
        source = """\
        func head_positive(xs: List[Int]) -> Int
            requires xs[0] > 0
            example [1] -> 1
            return xs[0]
        end func
        """
        result = check(source)["head_positive"]

        assert result.status == "failed"
        assert result.failure is not None
        assert result.failure.kind == "panic"
        assert result.counterexample is not None
        assert result.counterexample.arguments == "[]"

    def test_a_nested_requires_failure_is_a_panic(self):
        source = """\
        func positive(n: Int) -> Int
            requires n > 0
            example 1 -> 1
            return n
        end func

        func caller(n: Int) -> Int
            example 1 -> 1
            return positive(n)
        end func
        """
        result = check(source)["caller"]

        assert result.failure is not None
        assert result.failure.kind == "panic"
        assert result.failure.message == (
            "Precondition failed: positive(n: 0) requires n > 0"
        )
        assert result.discarded == 0

    def test_nearly_everything_discarded_gives_up_as_a_warning(self):
        source = """\
        func exact(n: Int) -> Int
            requires n == 12345
            example 12345 -> 12345
            return n
        end func
        """
        results = check(source)
        result = results["exact"]

        assert result.status == "gave_up"
        assert result.trials_run == 0
        assert result.discarded == 1000
        assert result.note is not None and "gave up" in result.note
        report = PropertyReport(seed=0, trials=100, results=list(results.values()))
        assert report.success

    def test_classification_does_not_read_the_message(self, monkeypatch):
        """Rewriting every contract message must not change any outcome."""
        source = """\
        func half(n: Int) -> Int
            requires n % 2 == 0
            ensures result >= 0
            example 4 -> 2
            return n / 2
        end func
        """
        before = check(source)["half"]
        original_init = ContractViolationError.__init__

        def scrambled(
            self: ContractViolationError, message: str, *args: Any, **kwargs: Any
        ) -> None:
            original_init(self, "contract text changed", *args, **kwargs)

        monkeypatch.setattr(ContractViolationError, "__init__", scrambled)
        after = check(source)["half"]

        assert before.status == after.status == "failed"
        assert before.discarded == after.discarded > 0
        assert before.counterexample == after.counterexample
        assert after.failure is not None
        assert after.failure.kind == "ensures"
        assert after.failure.clause == "result >= 0"
        assert after.failure.message == "contract text changed"


class TestPanicsExamples:
    def test_a_panics_example_exempts_panics(self):
        source = """\
        func safe_div(a: Int, b: Int) -> Int
            example 6, 3 -> 2
            example 1, 0 panics "division"
            return a / b
        end func
        """
        assert check(source)["safe_div"].status == "passed"

    def test_ensures_still_fails_a_function_with_a_panics_example(self):
        source = """\
        func shrink_div(a: Int, b: Int) -> Int
            ensures result <= a
            example 6, 3 -> 2
            example 1, 0 panics
            return a / b
        end func
        """
        result = check(source)["shrink_div"]

        assert result.status == "failed"
        assert result.failure is not None
        assert result.failure.kind == "ensures"


class TestLimits:
    def test_the_failure_names_the_per_call_allowance(self):
        # A generous wall-clock allowance keeps the step limit the binding one
        # on however slow a runner (coverage instrumentation slows 200,000
        # steps several times over).
        result = check(SPIN, call_max_seconds=120.0, timeout=120.0)["spin"]

        assert result.status == "failed"
        assert result.failure is not None
        assert result.failure.kind == "limit"
        assert result.failure.limit == "step limit"
        # No " at <file>:<line>:<col>" suffix: the reported message never
        # depends on where the module's file is.
        assert result.failure.message == "Step limit exceeded (200000 steps)"
        assert result.failure.summary == (
            "hit the step limit (200,000 steps per property call)"
        )

    def test_a_loop_first_hit_on_a_later_trial_fails_and_shrinks_to_one(self):
        result = _first_seed(
            SPIN,
            "spin",
            lambda r: (
                r.trials_run > 1
                and r.counterexample is not None
                and r.counterexample.original != "1"
            ),
            call_max_steps=20_000,
        )

        assert result.status == "failed"
        assert result.failure is not None
        assert result.failure.kind == "limit"
        assert result.counterexample is not None
        assert result.counterexample.arguments == "1"
        assert result.counterexample.shrink_steps > 0
        assert result.note is None

    def test_recursion_depth_is_a_limit_failure(self):
        source = """\
        func down(n: Int) -> Int
            example 0 -> 0
            if n == 0 then
                return 0
            end if
            return down(n) + 0
        end func
        """
        result = check(source, sandbox_config=SandboxConfig(max_recursion_depth=40))[
            "down"
        ]

        assert result.failure is not None
        assert result.failure.kind == "limit"
        assert result.failure.limit == "recursion depth limit"
        assert result.counterexample is not None
        assert result.counterexample.arguments in ("1", "-1")

    def test_a_size_limit_is_a_limit_failure(self):
        source = """\
        func fill(n: Int) -> Int
            example 0 -> 0
            return length(range(0, n))
        end func
        """
        result = check(source, sandbox_config=SandboxConfig(max_collection_size=50))[
            "fill"
        ]

        assert result.failure is not None
        assert result.failure.kind == "limit"
        assert result.failure.limit == "collection size limit"
        assert result.counterexample is not None
        assert result.counterexample.arguments == "51"

    def test_a_clipped_allowance_running_out_is_incomplete(self):
        result = check(SPIN, call_max_steps=20_000, trial_step_budget=10_000)["spin"]

        assert result.status == "incomplete"
        assert result.failure is None
        assert result.note is not None and "step budget spent" in result.note

    def test_a_spent_trial_budget_on_passing_calls_is_incomplete(self):
        source = """\
        func count(n: Int) -> Int
            ensures result >= 0
            example 3 -> 3
            var i = 0
            var k = 0
            while k < 50 do
                k = k + 1
                i = i + 1
            end while
            return i
        end func
        """
        result = check(source, trial_step_budget=5_000)["count"]

        assert result.status == "incomplete"
        assert 0 < result.trials_run < 100

    def test_a_full_allowance_hit_is_never_incomplete(self):
        """With room left for a full allowance, a loop is a failure even when
        the phase budget is almost spent."""
        result = check(SPIN, call_max_steps=20_000, trial_step_budget=21_000)["spin"]

        assert result.status == "failed"
        assert result.failure is not None and result.failure.kind == "limit"

    def test_the_wall_clock_safety_stop_is_incomplete(self, monkeypatch):
        clock = itertools.count(0, 100)
        monkeypatch.setattr(property_check, "_clock", lambda: next(clock))

        result = check(ABS_BUG)["abs_val"]

        assert result.status == "incomplete"
        assert result.note is not None and "wall-clock safety stop" in result.note

    def test_a_slow_call_with_steps_left_is_incomplete_not_a_failure(self):
        """The per-call deadline is a safety stop: a call that reaches it
        before using its step allowance (step-cheap builtin work) says
        something about the machine, not the input, so the same seed never
        passes on an idle machine and fails on a busy one."""
        result = check(SLOW_SORT, trials=3, call_max_seconds=0.005)["slow"]

        assert result.status == "incomplete"
        assert result.failure is None
        assert result.counterexample is None
        assert result.note == (
            "stopped after 0 trials by the per-call wall-clock safety stop "
            "(0.005 s): a call ran that long without using its step allowance"
        )

    def test_the_call_deadline_never_outlasts_the_functions_safety_stop(
        self, monkeypatch
    ):
        # 9.95 s of the 10 s safety stop (2 x --timeout) are gone after the
        # start, so the next call gets the 0.05 s that are left, not 5 s.
        clock = itertools.chain([0.0], itertools.repeat(9.95))
        monkeypatch.setattr(property_check, "_clock", lambda: next(clock))

        result = check(SLOW_SORT, trials=3, timeout=5.0)["slow"]

        assert result.status == "incomplete"
        assert result.note == "stopped after 0 trials by the wall-clock safety stop"

    def test_a_deadline_while_shrinking_ends_shrinking(self, monkeypatch):
        """A shrink candidate stopped by a deadline is not accepted as the same
        failure: shrinking ends with the best counterexample so far."""
        original_call = property_check.PropertyRunner.call
        failed: list[bool] = []

        def call(self, check_, args, remaining):
            if failed:
                return self._deadline(0, safety_stop=False)
            outcome = original_call(self, check_, args, remaining)
            if outcome.is_failure:
                failed.append(True)
            return outcome

        monkeypatch.setattr(property_check.PropertyRunner, "call", call)

        result = check(ABS_BUG)["abs_val"]

        assert result.status == "failed"
        assert result.counterexample is not None
        assert result.counterexample.arguments == result.counterexample.original
        assert result.counterexample.shrink_steps == 0
        assert result.note is not None
        assert result.note.startswith(
            "shrinking stopped by the per-call wall-clock safety stop"
        )

    def test_default_budgets_do_not_make_ordinary_functions_incomplete(self):
        source = """\
        func sum_to(n: Int) -> Int
            requires n >= 0 and n <= 1000
            ensures result >= 0
            example 3 -> 6
            var total = 0
            for i in range(0, n + 1) do
                total = total + i
            end for
            return total
        end func
        """
        assert check(source)["sum_to"].status == "passed"


class TestIsolation:
    def test_mutation_never_sees_a_previous_trials_arguments(self):
        source = """\
        func grow(v: Vec[Int]) -> Int
            ensures result <= 9
            example vec_from_list([]) -> 1
            vec_push(v, 1)
            return vec_length(v)
        end func
        """
        assert check(source)["grow"].status == "passed"

    def test_a_capability_denial_skips_the_function(self):
        """An effect-inference gap meets a fail-closed interpreter."""
        text, program, checker = _load(
            """\
            func shout(s: String) -> Int
                example "a" -> 1
                print(s)
                return 1
            end func
            """
        )
        func_type = checker.global_env.lookup("shout")
        assert isinstance(func_type, FuncType)
        checker.global_env.bind(
            "shout",
            FuncType(func_type.param_types, func_type.return_type, frozenset()),
        )
        [result] = check_module_properties(
            program, checker, module="props", path="props.geno", source=text
        )

        assert result.status == "skipped"
        assert result.note is not None
        assert "needs a capability at run time" in result.note
        assert "print" in result.note

    def test_property_steps_do_not_touch_another_interpreter(self):
        """The engine loads its own interpreter, so the examples' is untouched."""
        text, program, checker = _load(ABS_BUG)
        examples_interp = Interpreter(check_examples=False)
        examples_interp.run(program, execute_main=False)
        steps = examples_interp.steps

        check_module_properties(program, checker, module="props", path="p", source=text)

        assert examples_interp.steps == steps


# =============================================================================
# Determinism
# =============================================================================

DETERMINISM = """\
type Shape = Circle(radius: Float) | Square(side: Float)

func area(s: Shape) -> Float
    ensures result < 100.0
    example Circle(1.0) -> 3.0
    match s with
        | Circle(r) -> return 3.0 * r * r
        | Square(x) -> return x * x
    end match
end func

func keys(m: Map[String, Int], s: Set[Int]) -> Int
    ensures result < 4
    example {}, set_from_list([]) -> 0
    return length(map_keys(m)) + set_size(s)
end func
"""


class TestDeterminism:
    def test_the_same_seed_gives_identical_results(self):
        assert check(DETERMINISM, seed=7) == check(DETERMINISM, seed=7)

    def test_different_seeds_explore_different_inputs(self):
        originals = set()
        for seed in range(5):
            counterexample = check(DETERMINISM, seed=seed)["keys"].counterexample
            assert counterexample is not None
            originals.add(counterexample.original)
        assert len(originals) > 1

    def test_the_stream_is_keyed_by_module_not_path(self):
        first = check(DETERMINISM, module="shapes", path="shapes.geno")
        second = check(DETERMINISM, module="shapes", path="/elsewhere/./shapes.geno")
        for name in first:
            assert first[name].to_dict() | {"path": None} == second[name].to_dict() | {
                "path": None
            }

    def test_filtering_does_not_change_a_functions_stream(self):
        everything = check(DETERMINISM, seed=3)
        only_keys = check(DETERMINISM, seed=3, filter_pattern="keys")

        assert set(only_keys) == {"keys"}
        assert only_keys["keys"] == everything["keys"]

    def test_runs_match_across_cwds_path_spellings_and_hash_seeds(self, tmp_path):
        source = tmp_path / "project" / "shapes.geno"
        source.parent.mkdir()
        source.write_text(DETERMINISM)
        script = tmp_path / "run_properties.py"
        script.write_text(
            textwrap.dedent(
                """\
                import json, sys
                from pathlib import Path
                from geno.lexer import Lexer
                from geno.parser import Parser
                from geno.property_check import PropertyConfig, check_module_properties
                from geno.typechecker import TypeChecker

                path = sys.argv[1]
                text = Path(path).read_text()
                program = Parser(Lexer(text, path).tokenize(), text).parse_program()
                checker = TypeChecker()
                checker.check_program(program)
                results = check_module_properties(
                    program, checker, module=Path(path).stem, path=path,
                    source=text, config=PropertyConfig(seed=11),
                )
                print(json.dumps([dict(r.to_dict(), path=None) for r in results]))
                """
            )
        )
        outputs = []
        runs = [
            (tmp_path, "project/shapes.geno", "0"),
            (source.parent, "./shapes.geno", "1"),
            (REPO_ROOT, str(source), "123"),
        ]
        for cwd, spelling, hash_seed in runs:
            env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONPATH=str(REPO_ROOT))
            completed = subprocess.run(
                [sys.executable, str(script), spelling],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            outputs.append(completed.stdout)

        assert outputs[0] == outputs[1] == outputs[2]
        decoded = json.loads(outputs[0])
        assert {entry["status"] for entry in decoded} == {"failed"}

    def test_trial_sizes_grow_from_zero_to_the_maximum(self):
        sizes = [trial_size(index, 100) for index in range(100)]
        assert sizes[0] == 0 and sizes[-1] == 64
        assert sizes == sorted(sizes)
        assert trial_size(0, 1) == 0


# =============================================================================
# Generators: every rendering parses, type-checks and realizes equal
# =============================================================================

PRELUDE = """\
type Score = Int
type IntTree = Leaf | Node(left: IntTree, value: Int, right: IntTree)
type Box[T] = Box(value: T)
type Tree[T] = Empty | Branch(left: Tree[T], value: T, right: Tree[T])
type Expr = Num(value: Int) | Neg(inner: Expr) | Block(body: Stmt)
type Stmt = Eval(expr: Expr) | Seq(first: Stmt, second: Stmt)
type Rose = Rose(value: Int, children: List[Rose])
type Chain = Chain(next: Option[Chain])
type Attempt = Attempt(next: Result[Attempt, String])
"""

TYPE_CASES = [
    "Int",
    "Float",
    "Bool",
    "String",
    "Unit",
    "(Int, String)",
    "(Bool, (Float, Int))",
    "List[Int]",
    "List[List[String]]",
    "Option[Int]",
    "Result[Int, String]",
    "Map[String, Int]",
    "Map[Int, Option[Float]]",
    "Set[Int]",
    "Set[String]",
    "Vec[Int]",
    "Array[Float]",
    "Score",
    "IntTree",
    "Box[String]",
    "Box[List[Int]]",
    "Tree[Int]",
    "Expr",
    "List[Tree[Bool]]",
    # Recursive only through a container, an Option or a Result.
    "Rose",
    "Chain",
    "Attempt",
]


def _probe_program(examples: dict[int, list[str]] | None = None) -> str:
    parts = [PRELUDE]
    for index, annotation in enumerate(TYPE_CASES):
        if examples is None:
            header = '@untested("generator probe")\n'
            clauses = ""
        else:
            header = ""
            clauses = "".join(
                f"    example {rendered} -> 0\n" for rendered in examples[index]
            )
        parts.append(
            f"{header}func probe{index}(x: {annotation}) -> Int\n"
            f"{clauses}    return 0\nend func\n"
        )
    return "\n".join(parts)


@pytest.fixture(scope="module")
def probe_types() -> tuple[TypeChecker, list[Type]]:
    _text, _program, checker = _load(_probe_program())
    types: list[Type] = []
    for index in range(len(TYPE_CASES)):
        func_type = checker.global_env.lookup(f"probe{index}")
        assert isinstance(func_type, FuncType)
        types.append(func_type.param_types[0])
    return checker, types


def _samples(checker: TypeChecker, type_: Type) -> list[Sample]:
    gens = Generators(checker.type_defs)
    rng = random.Random(5)
    samples = [gens.minimal(type_)]
    for size in (0, 4, 8, 16, 32, 48, 64):
        samples.extend(gens.generate(rng, type_, size) for _ in range(6))
    return samples


def _expected(interp: Interpreter, checker: TypeChecker, sample: Sample) -> Any:
    """The runtime value *sample* stands for, built without parsing it."""
    kind = sample.kind
    if kind in ("int", "float", "bool", "string"):
        return sample.value
    if kind == "unit":
        return ()
    if kind == "map":
        return {
            _expected(interp, checker, entry.items[0]): _expected(
                interp, checker, entry.items[1]
            )
            for entry in sample.items
        }
    items = [_expected(interp, checker, item) for item in sample.items]
    if kind == "tuple":
        return tuple(items)
    if kind == "list":
        return items
    if kind in ("set", "vec", "array"):
        builder = interp.global_env.lookup(f"{kind}_from_list")
        return interp._call_function(builder, [items])
    assert kind == "ctor" and sample.name is not None
    if sample.name in ("Some", "Ok"):
        return ConstructorValue(sample.name, {"value": items[0]})
    if sample.name == "Err":
        return ConstructorValue("Err", {"error": items[0]})
    if sample.name == "None":
        return ConstructorValue("None", {})
    type_name = checker._constructor_to_type[sample.name]
    fields = checker.type_defs[type_name].variants[sample.name]
    return ConstructorValue(
        sample.name, {name: value for (name, _type), value in zip(fields, items)}
    )


class TestGenerators:
    def test_every_rendering_parses_and_realizes_an_equal_value(self, probe_types):
        checker, types = probe_types
        _text, program, _checker = _load(PRELUDE)
        interp = Interpreter(check_examples=False, capabilities=frozenset())
        interp.run(program, execute_main=False)
        for annotation, type_ in zip(TYPE_CASES, types):
            for sample in _samples(checker, type_):
                source = render(sample)
                value = interp.eval_expr(parse_expression(source), interp.global_env)
                expected = _expected(interp, checker, sample)
                assert interp._values_equal(value, expected), (annotation, source)

    def test_every_rendering_type_checks_as_an_example_input(self, probe_types):
        checker, types = probe_types
        examples = {
            index: [render(sample) for sample in _samples(checker, type_)]
            for index, type_ in enumerate(types)
        }
        _load(_probe_program(examples))  # raises on any type error

    def test_tree_of_a_type_parameter_round_trips(self, probe_types):
        """Needs the nested-nullary-constructor checker fix (proposal 0017)."""
        checker, types = probe_types
        tree = types[TYPE_CASES.index("Tree[Int]")]
        rendered = {
            render(sample)
            for sample in _samples(checker, tree)
            if sample.items and any(item.items for item in sample.items)
        }
        assert any(text.count("Branch(") >= 2 for text in rendered)

    def test_generation_covers_every_variant_and_terminates(self, probe_types):
        checker, types = probe_types
        gens = Generators(checker.type_defs)
        rng = random.Random(1)
        for name in ("IntTree", "Expr"):
            type_ = types[TYPE_CASES.index(name)]
            seen = set()
            for _ in range(300):
                sample = gens.generate(rng, type_, 64)
                seen.add(sample.name)
                # The depth bound (1 + 64 // 16 = 5), then the smallest
                # value from there: Block(Eval(Num(n))) adds two more.
                assert _depth(sample) <= 1 + 64 // 16 + 2
            expected = set(checker.type_defs[name].variants)
            assert seen == expected

    def test_ints_stay_within_the_javascript_safe_range(self, probe_types):
        checker, types = probe_types
        gens = Generators(checker.type_defs)
        rng = random.Random(2)
        values = [
            gens.generate(rng, types[0], size).value
            for size in range(65)
            for _ in range(40)
        ]
        assert all(
            isinstance(v, int) and -MAX_SAFE_INT <= v <= MAX_SAFE_INT for v in values
        )
        assert MAX_SAFE_INT in values and -MAX_SAFE_INT in values
        assert {0, 1, -1} <= set(values)

    def test_floats_are_finite_and_never_rendered_with_an_exponent(self, probe_types):
        checker, types = probe_types
        gens = Generators(checker.type_defs)
        rng = random.Random(3)
        for size in range(65):
            for _ in range(30):
                sample = gens.generate(rng, types[1], size)
                assert isinstance(sample.value, float)
                text = render(sample)
                assert "e" not in text.lower() and "." in text
                assert float(text) == sample.value
        for value in (1e-05, 1e16, -2.5e-7, 123.0, -0.0):
            text = render_float(value)
            assert "e" not in text and "." in text and float(text) == value

    def test_strings_are_ascii_and_escaped(self, probe_types):
        checker, types = probe_types
        gens = Generators(checker.type_defs)
        rng = random.Random(4)
        texts = [gens.generate(rng, types[3], 64).value for _ in range(400)]
        assert all(isinstance(t, str) and t.isascii() for t in texts)
        assert "" in texts
        joined = "".join(t for t in texts if isinstance(t, str))
        assert '"' in joined and "\\" in joined and "\n" in joined

    def test_map_and_set_entries_are_deduplicated_in_generation_order(
        self, probe_types
    ):
        checker, types = probe_types
        gens = Generators(checker.type_defs)
        rng = random.Random(6)
        for type_ in (types[TYPE_CASES.index("Map[String, Int]")], types[13]):
            for _ in range(200):
                sample = gens.generate(rng, type_, 64)
                if sample.kind == "map":
                    keys = [render(entry.items[0]) for entry in sample.items]
                else:
                    keys = [render(item) for item in sample.items]
                assert len(keys) == len(set(keys))

    def test_unsupported_types_are_reported(self):
        _text, _program, checker = _load(
            """\
            @untested("probe")
            func f(g: (Int) -> Int, m: MutableMap[String, Int]) -> Int
                return 0
            end func
            """
        )
        func_type = checker.global_env.lookup("f")
        assert isinstance(func_type, FuncType)
        gens = Generators(checker.type_defs)
        for param_type in func_type.param_types:
            assert gens.unsupported(param_type) == param_type

    def test_a_type_with_no_finite_value_has_no_generator(self):
        _text, _program, checker = _load(
            """\
            type Loop = Loop(next: Loop)

            @untested("probe")
            func f(x: Loop) -> Int
                return 0
            end func
            """
        )
        func_type = checker.global_env.lookup("f")
        assert isinstance(func_type, FuncType)
        assert Generators(checker.type_defs).unsupported(func_type.param_types[0])

    def test_recursion_through_a_container_option_or_result_is_generated(
        self, probe_types
    ):
        """The empty collection, ``None`` and the other ``Result`` side end
        the recursion, so these are generated, nested, and never skipped."""
        checker, types = probe_types
        gens = Generators(checker.type_defs)
        rng = random.Random(3)
        for name in ("Rose", "Chain", "Attempt"):
            type_ = types[TYPE_CASES.index(name)]
            assert gens.unsupported(type_) is None
            assert gens.min_depth(type_) == 1
            samples = [gens.generate(rng, type_, size) for size in range(65)]
            assert max(len(render(sample)) for sample in samples) > len(
                render(gens.minimal(type_))
            )
        assert render(gens.minimal(types[TYPE_CASES.index("Rose")])) == "Rose(0, [])"
        assert render(gens.minimal(types[TYPE_CASES.index("Chain")])) == "Chain(None)"
        assert render(gens.minimal(types[TYPE_CASES.index("Attempt")])) == (
            'Attempt(Err(""))'
        )

    def test_a_collection_of_a_type_with_no_value_is_generated_empty(self):
        _text, _program, checker = _load(
            """\
            type Loop = Loop(next: Loop)

            @untested("probe")
            func f(xs: List[Loop], o: Option[Loop], m: Map[Int, Loop]) -> Int
                return 0
            end func
            """
        )
        func_type = checker.global_env.lookup("f")
        assert isinstance(func_type, FuncType)
        gens = Generators(checker.type_defs)
        rng = random.Random(4)
        for param_type in func_type.param_types:
            assert gens.unsupported(param_type) is None
            rendered = {
                render(gens.generate(rng, param_type, size)) for size in range(65)
            }
            assert rendered == {render(gens.minimal(param_type))}

    def test_a_field_with_no_generator_is_named(self):
        _text, _program, checker = _load(
            """\
            type Fn = Fn(f: (Int) -> Int)
            type Holder = Holder(fn_box: Fn)
            type Handlers = Handlers(all: List[(Int) -> Int])

            @untested("probe")
            func f(x: Fn, y: Holder, z: Handlers) -> Int
                return 0
            end func
            """
        )
        func_type = checker.global_env.lookup("f")
        assert isinstance(func_type, FuncType)
        gens = Generators(checker.type_defs)
        reasons = [gens.unsupported_reason(t) for t in func_type.param_types]
        assert all(reason is not None for reason in reasons)
        assert [(str(r[0]), str(r[1])) for r in reasons if r is not None] == [
            ("(Int) -> Int", "Fn"),
            ("(Int) -> Int", "Fn"),
            ("(Int) -> Int", "Handlers"),
        ]


def _depth(sample: Sample) -> int:
    if sample.kind != "ctor" or not sample.items:
        return 1 if sample.kind == "ctor" else 0
    return 1 + max(_depth(item) for item in sample.items)


class TestShrinking:
    def _shrunk(self, source: str, name: str) -> str:
        result = check(source)[name]
        assert result.status == "failed", result
        assert result.counterexample is not None
        return result.counterexample.arguments

    def test_lists_shrink_to_the_shortest_failing_list(self):
        source = """\
        func short(xs: List[Int]) -> Int
            ensures result < 3
            example [] -> 0
            return length(xs)
        end func
        """
        assert self._shrunk(source, "short") == "[0, 0, 0]"

    def test_strings_shrink(self):
        source = """\
        func no_zero(s: String) -> Bool
            ensures not result
            example "a" -> false
            return contains(s, "0")
        end func
        """
        assert self._shrunk(source, "no_zero") == '"0"'

    def test_floats_shrink_toward_zero(self):
        source = """\
        func small(x: Float) -> Float
            ensures result < 10.0
            example 1.0 -> 1.0
            return x
        end func
        """
        shrunk = float(self._shrunk(source, "small"))
        assert 10.0 <= shrunk <= 20.0
        assert shrunk == int(shrunk)

    def test_options_results_and_bools_shrink(self):
        source = """\
        func picky(o: Option[Int], r: Result[Bool, String]) -> Int
            ensures result == 0
            example None, Ok(false) -> 0
            match o with
                | Some(n) -> return 1
                | None -> return 0
            end match
        end func
        """
        # A Result shrinks its inner value and keeps its side.
        assert self._shrunk(source, "picky") in (
            "Some(0), Ok(false)",
            'Some(0), Err("")',
        )

    def test_maps_sets_vecs_and_arrays_shrink(self):
        source = """\
        func sizes(m: Map[Int, Int], s: Set[Int], v: Vec[Int], a: Array[Int]) -> Int
            ensures result < 1
            example {}, set_from_list([]), vec_from_list([]), array_from_list([]) -> 0
            return length(map_keys(m)) + set_size(s) + vec_length(v) + array_length(a)
        end func
        """
        shrunk = self._shrunk(source, "sizes")
        assert shrunk.count("0") == 1
        assert shrunk in (
            "{0: 0}, set_from_list([]), vec_from_list([]), array_from_list([])",
            "{}, set_from_list([0]), vec_from_list([]), array_from_list([])",
            "{}, set_from_list([]), vec_from_list([0]), array_from_list([])",
            "{}, set_from_list([]), vec_from_list([]), array_from_list([0])",
        )

    def test_recursive_adts_shrink_to_a_subtree(self):
        source = """\
        type IntTree = Leaf | Node(left: IntTree, value: Int, right: IntTree)

        func count(t: IntTree) -> Int
            ensures result < 2
            example Leaf -> 0
            match t with
                | Leaf -> return 0
                | Node(l, _, r) -> return 1 + count(l) + count(r)
            end match
        end func
        """
        shrunk = self._shrunk(source, "count")
        assert shrunk in (
            "Node(Leaf, 0, Node(Leaf, 0, Leaf))",
            "Node(Node(Leaf, 0, Leaf), 0, Leaf)",
        )

    def test_generic_adts_shrink(self):
        source = """\
        type Box[T] = Box(value: T)
        type Tree[T] = Empty | Branch(left: Tree[T], value: T, right: Tree[T])

        func boxed(b: Box[Int]) -> Int
            ensures result < 5
            example Box(1) -> 1
            return b.value
        end func

        func deepest(t: Tree[Int]) -> Int
            ensures result < 1
            example Empty -> 0
            match t with
                | Empty -> return 0
                | Branch(_, v, _) -> return v
            end match
        end func
        """
        results = check(source)
        assert results["boxed"].counterexample is not None
        assert results["boxed"].counterexample.arguments == "Box(5)"
        assert results["deepest"].counterexample is not None
        assert results["deepest"].counterexample.arguments == "Branch(Empty, 1, Empty)"

    def test_mutually_recursive_adts_shrink(self):
        source = """\
        type Expr = Num(value: Int) | Neg(inner: Expr) | Block(body: Stmt)
        type Stmt = Eval(expr: Expr) | Seq(first: Stmt, second: Stmt)

        func negs(e: Expr) -> Int
            ensures result < 1
            example Num(1) -> 0
            match e with
                | Num(_) -> return 0
                | Neg(inner) -> return 1 + negs(inner)
                | Block(_) -> return 0
            end match
        end func
        """
        result = check(source)["negs"]
        assert result.counterexample is not None
        assert result.counterexample.arguments == "Neg(Num(0))"

    def test_a_generic_functions_counterexample_pastes_back_as_an_example(self):
        """``Empty`` for ``size[T](t: Tree[T])`` is a valid example input."""
        body = """\
        type Tree[T] = Empty | Branch(left: Tree[T], value: T, right: Tree[T])

        func size[T](t: Tree[T]) -> Int
            ensures result > 0
        {examples}
            match t with
                | Empty -> return 0
                | Branch(l, _, r) -> return 1 + size(l) + size(r)
            end match
        end func
        """
        source = body.replace("{examples}", "    example Branch(Empty, 1, Empty) -> 1")
        result = check(source)["size"]
        assert result.status == "failed"
        assert result.counterexample is not None
        assert result.counterexample.arguments == "Empty"
        pasted = body.replace(
            "{examples}",
            f"    example {result.counterexample.arguments} -> 0",
        )
        _load(pasted)  # raises on a type error

    def test_adts_recursive_through_a_list_or_an_option_shrink(self):
        source = """\
        type Rose = Rose(value: Int, children: List[Rose])
        type Chain = Chain(next: Option[Chain])

        func rsize(r: Rose) -> Int
            ensures result < 3
            example Rose(1, []) -> 1
            var total = 1
            for c: Rose in r.children do
                total = total + rsize(c)
            end for
            return total
        end func

        func clen(c: Chain) -> Int
            ensures result < 3
            example Chain(None) -> 1
            match c.next with
                | Some(n) -> return 1 + clen(n)
                | None -> return 1
            end match
        end func
        """
        results = check(source)
        assert results["rsize"].counterexample is not None
        assert results["rsize"].counterexample.arguments in (
            "Rose(0, [Rose(0, []), Rose(0, [])])",
            "Rose(0, [Rose(0, [Rose(0, [])])])",
        )
        assert results["clen"].failure is not None
        assert results["clen"].failure.kind == "ensures"
        assert results["clen"].counterexample is not None
        assert results["clen"].counterexample.arguments == (
            "Chain(Some(Chain(Some(Chain(None)))))"
        )

    def test_shrinking_stops_at_the_shrink_budget(self):
        result = check(ABS_BUG, max_shrink_calls=1, seed=1)["abs_val"]
        assert result.status == "failed"
        assert result.counterexample is not None

    def test_integer_candidates_come_in_increasing_magnitude(self):
        # 0, the small fractions value/2**k first, then value - value/2**k
        # from value/2 up, then one step toward 0 (999 is value - value/512).
        assert list(property_check._shrink_int(1000)) == [
            0, 1, 3, 7, 15, 31, 62, 125, 250,
            500, 750, 875, 938, 969, 985, 993, 997, 999,
        ]  # fmt: skip
        assert list(property_check._shrink_int(-9))[:3] == [0, -1, -2]
        assert list(property_check._shrink_int(1)) == [0]
        assert list(property_check._shrink_int(0)) == []

    def test_a_limit_found_at_the_largest_int_shrinks_near_its_threshold(self):
        # Only the generated extreme 2**53 - 1 reaches the loop, and every
        # failing candidate costs a full call allowance. Halving one step at a
        # time spent the shrink budget about 20 halvings in, near 2**33; the
        # small candidates first reach the threshold within the same budget
        # ratio as the defaults (20 failing calls).
        source = """\
        func slow_above(n: Int) -> Int
            example 0 -> 0
            var i = 0
            if n > 1000000 then
                while true do
                    i = i + 1
                end while
            end if
            return i
        end func
        """
        result = _first_seed(
            source,
            "slow_above",
            lambda r: (
                r.counterexample is not None
                and r.counterexample.original == str(MAX_SAFE_INT)
            ),
            call_max_steps=20_000,
            shrink_step_budget=400_000,
        )

        assert result.failure is not None and result.failure.kind == "limit"
        assert result.counterexample is not None
        assert 1_000_000 < int(result.counterexample.arguments) < 2_000_002

    def test_render_arguments(self):
        int_sample = Sample("int", Type(), 3)
        tuple_sample = Sample("tuple", Type(), items=(int_sample, int_sample))
        assert render_arguments([int_sample]) == "3"
        assert render_arguments([int_sample, int_sample]) == "3, 3"
        assert render_arguments([tuple_sample]) == "(3, 3)"


# =============================================================================
# Eligibility
# =============================================================================


class TestEligibility:
    SOURCE = """\
    type Shape = Circle(radius: Float)

    trait Show
        func show(self: Self) -> String
    end trait

    func main() -> Unit
        print("hi")
    end func

    func init() -> Int
        example () -> 0
        return 0
    end func

    func update(n: Int, dt: Float) -> Int
        example 1, 1.0 -> 1
        return n
    end func

    func render(n: Int) -> Unit
        example 1 -> ()
        return ()
    end func

    async func later(n: Int) -> Int
        return n
    end func

    @untested("talks to the host")
    func host(n: Int) -> Int
        return n
    end func

    func shout(s: String) -> Unit
        example "a" -> ()
        print(s)
    end func

    func constant() -> Int
        example () -> 1
        return 1
    end func

    func apply(f: (Int) -> Int, x: Int) -> Int
        example fn(n: Int) -> n, 1 -> 1
        return f(x)
    end func

    func lookup(m: MutableMap[String, Int]) -> Int
        example mutable_map_new() -> 0
        return 0
    end func

    func shown[T: Show](x: T) -> Int
        example Circle(1.0) -> 0
        return 0
    end func

    func ordered[T: Ord](x: T, y: T) -> Bool
        example 1, 2 -> true
        return x < y or x >= y
    end func

    impl Show for Shape
        func show(self: Shape) -> String
            example Circle(1.0) -> "circle"
            return "circle"
        end func
    end impl
    """

    @pytest.fixture
    def results(self) -> dict[str, PropertyResult]:
        return check(self.SOURCE)

    def test_out_of_scope_functions_are_left_out(self, results):
        for name in (
            "main",
            "init",
            "update",
            "render",
            "later",
            "host",
            "shout",
            "constant",
        ):
            assert name not in results

    def test_parameters_without_a_generator_are_skipped_with_a_note(self, results):
        assert results["apply"].status == "skipped"
        assert results["apply"].note == "parameter f: (Int) -> Int has no generator"
        assert results["lookup"].status == "skipped"
        assert results["lookup"].note is not None
        assert "MutableMap[String, Int] has no generator" in results["lookup"].note

    def test_masking_local_mutation_leaves_the_eligible_set_unchanged(self):
        """Proposal 0019 only removes `mutation`: `{mutation}` became `{}` and
        both are checked; `{mutation, io}` became `{io}` and both are not."""
        source = """\
        func local_sum(n: Int) -> Int
            ensures result >= n
            example 2 -> 3
            var y = n
            y = y + 1
            return y
        end func

        func noisy_sum(n: Int) -> Int
            example 2 -> 3
            var y = n
            y = y + 1
            print(y)
            return y
        end func

        func ticks(n: Int) -> Int
            ensures result == 1
            example 2 -> 1
            var count = 0
            let tick = fn() do
                count = count + 1
                return count
            end fn
            return tick()
        end func
        """
        _text, _program, checker = _load(source)
        effects = {}
        for name in ("local_sum", "noisy_sum", "ticks"):
            func_type = checker.global_env.lookup(name)
            assert isinstance(func_type, FuncType)
            effects[name] = func_type.effects
        assert effects == {
            "local_sum": frozenset(),
            "noisy_sum": frozenset({"io"}),
            "ticks": frozenset({"mutation"}),
        }
        results = check(source)
        assert set(results) == {"local_sum", "ticks"}
        assert results["local_sum"].status == "passed"
        assert results["ticks"].status == "passed"

    def test_a_field_without_a_generator_is_named_in_the_note(self):
        source = """\
        type Fn = Fn(f: (Int) -> Int)
        type Loop = Loop(next: Loop)

        func fnf(f: Fn) -> Int
            example Fn(fn(x: Int) -> x) -> 1
            return 1
        end func

        func loops(xs: List[Loop]) -> Int
            ensures result == 0
            example [] -> 0
            return length(xs)
        end func
        """
        results = check(source)
        assert results["fnf"].status == "skipped"
        assert results["fnf"].note == (
            "parameter f: type Fn needs (Int) -> Int, which has no generator"
        )
        # ``[]`` is the one value of List[Loop], and it is generated.
        assert results["loops"].status == "passed"
        assert results["loops"].trials_run == 100

    def test_a_bound_int_cannot_satisfy_is_skipped_with_a_note(self, results):
        assert results["shown"].status == "skipped"
        assert results["shown"].note == (
            "type parameter T: Show cannot be instantiated with Int"
        )

    def test_satisfiable_bounds_are_instantiated_with_int(self, results):
        assert results["ordered"].status == "passed"

    def test_impl_methods_are_checked_under_their_qualified_name(self, results):
        assert results["Shape.Show.show"].status == "passed"

    def test_filter_matches_the_names_examples_use(self):
        for pattern in ("Shape.Show.show", "Shape.show", "show"):
            assert set(check(self.SOURCE, filter_pattern=pattern)) == {
                "Shape.Show.show"
            }

    def test_impl_method_bugs_are_found(self):
        source = """\
        type Meters = Meters(value: Int)

        trait Measure
            func amount(self: Self) -> Int
        end trait

        impl Measure for Meters
            func amount(self: Meters) -> Int
                ensures result >= 0
                example Meters(1) -> 1
                return self.value
            end func
        end impl
        """
        result = check(source)["Meters.Measure.amount"]
        assert result.status == "failed"
        assert result.counterexample is not None
        assert result.counterexample.arguments == "Meters(-1)"

    def test_a_module_that_cannot_load_skips_its_functions(self, monkeypatch):
        """Constants are literals, so loading rarely fails; if it does, every
        function is skipped with the reason instead of the run failing."""
        from geno.values import GenoRuntimeError

        def failing_run(self: Interpreter, *args: Any, **kwargs: Any) -> None:
            raise GenoRuntimeError("module failed to load")

        monkeypatch.setattr(Interpreter, "run", failing_run)
        result = check(ABS_BUG)["abs_val"]

        assert result.status == "skipped"
        assert result.note == (
            "the module could not be loaded for property checks: module failed to load"
        )


class TestProjectMode:
    def test_each_module_is_checked_with_its_own_checker(self, tmp_path):
        from geno.project_resolution import resolve_project_context

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Util"]\n'
        )
        (tmp_path / "App.geno").write_text(
            "import Util\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "    return twice(1)\n"
            "end func\n"
        )
        (tmp_path / "Util.geno").write_text(
            "type Pair = Pair(a: Int, b: Int)\n"
            "\n"
            "func twice(n: Int) -> Int\n"
            "    ensures result >= n\n"
            "    example 1 -> 2\n"
            "    return n * 2\n"
            "end func\n"
            "\n"
            "func first(p: Pair) -> Int\n"
            "    example Pair(1, 2) -> 1\n"
            "    return p.a\n"
            "end func\n"
        )
        context = resolve_project_context(tmp_path)
        graph = context.dependency_graph
        checker = TypeChecker()
        checker.check_project_graph(graph, retain_module_checkers=True)
        assert set(checker.module_checkers) == set(graph.sorted_modules)

        util = graph.parsed["Util"]
        results = check_module_properties(
            util,
            checker.module_checkers["Util"],
            module="Util",
            path="Util.geno",
            modules={name: graph.parsed[name] for name in graph.sorted_modules},
            source=(tmp_path / "Util.geno").read_text(),
        )
        by_name = {result.function: result for result in results}

        assert by_name["first"].status == "passed"
        assert by_name["twice"].status == "failed"
        assert by_name["twice"].counterexample is not None
        assert by_name["twice"].counterexample.arguments == "-1"

    def test_module_checkers_are_not_kept_by_default(self, tmp_path):
        from geno.project_resolution import resolve_project_context

        (tmp_path / "geno.toml").write_text('entrypoint = "App"\nfiles = ["App"]\n')
        (tmp_path / "App.geno").write_text(
            "func main() -> Int\n    return 1\nend func\n"
        )
        checker = TypeChecker()
        checker.check_project_graph(resolve_project_context(tmp_path).dependency_graph)
        assert checker.module_checkers == {}


# =============================================================================
# Results
# =============================================================================


class TestResults:
    def test_json_shape_is_stable(self):
        results = list(check(ABS_BUG + "\n" + SPIN.replace("spin", "spin2")).values())
        report = PropertyReport(seed=0, trials=100, results=results)
        data = report.to_dict()

        assert list(data) == [
            "seed",
            "trials",
            "passed",
            "failed",
            "gave_up",
            "incomplete",
            "skipped",
            "functions",
        ]
        assert data["failed"] == 2 and not report.success
        entry = data["functions"][0]
        assert list(entry) == [
            "path",
            "function",
            "line",
            "status",
            "trials_run",
            "discarded",
            "failure",
            "counterexample",
            "note",
        ]
        assert list(entry["failure"]) == [
            "kind",
            "message",
            "clause",
            "result",
            "limit",
        ]
        assert list(entry["counterexample"]) == [
            "arguments",
            "original",
            "shrink_steps",
        ]
        assert json.loads(json.dumps(data)) == data

    def test_a_passing_entry_has_null_fields(self):
        source = ABS_BUG.replace("    return n\nend func", "    return 0 - n\nend func")
        entry = check(source)["abs_val"].to_dict()
        assert entry["failure"] is None
        assert entry["counterexample"] is None
        assert entry["note"] is None
        assert entry["line"] == 1

    def test_trials_must_be_positive(self):
        with pytest.raises(ValueError):
            PropertyConfig(trials=0)


# =============================================================================
# Interpreter support
# =============================================================================


class TestStepAllowance:
    def test_the_allowance_starts_from_zero_and_restores_the_limits(self):
        _text, program, _checker = _load(SPIN)
        interp = Interpreter(
            check_examples=False, sandbox_config=SandboxConfig(max_steps=10_000_000)
        )
        interp.run(program, execute_main=False)
        interp.steps = 1234
        func = interp.global_env.lookup("spin")

        from geno.sandbox import StepLimitExceeded

        with pytest.raises(
            StepLimitExceeded, match=r"Step limit exceeded \(500 steps\)"
        ):
            with interp.step_allowance(500):
                interp._call_function(func, [1])

        assert interp.max_steps == 10_000_000
        assert interp._step_limit == 10_000_000
        assert 1234 + 500 <= interp.steps <= 1234 + 600

    def test_steps_used_inside_are_added_back(self):
        _text, program, _checker = _load(SPIN)
        interp = Interpreter(check_examples=False)
        interp.run(program, execute_main=False)
        before = interp.steps
        func = interp.global_env.lookup("spin")
        with interp.step_allowance(1000):
            interp._call_function(func, [0])
        assert interp.steps > before


class TestContractFields:
    def _violation(self, source: str, args: list[Any]) -> ContractViolationError:
        _text, program, _checker = _load(source)
        interp = Interpreter(check_examples=False)
        interp.run(program, execute_main=False)
        func = interp.global_env.lookup("f")
        with pytest.raises(ContractViolationError) as exc_info:
            interp._call_function(func, args)
        return exc_info.value

    def test_requires_false(self):
        violation = self._violation(
            "func f(n: Int) -> Int\n"
            "    requires n > -10\n"
            "    requires n > 0\n"
            "    example 1 -> 1\n"
            "    return n\n"
            "end func\n",
            [0],
        )
        assert (violation.clause, violation.reason) == ("requires", "false")
        assert (violation.clause_index, violation.call_depth) == (1, 1)

    def test_requires_propagate(self):
        violation = self._violation(
            "func f(xs: List[Int]) -> Option[Int]\n"
            "    requires list_first(xs)? > 0\n"
            "    example [1] -> Some(1)\n"
            "    return list_first(xs)\n"
            "end func\n",
            [[]],
        )
        assert (violation.clause, violation.reason) == ("requires", "propagate")
        assert (violation.clause_index, violation.call_depth) == (0, 1)

    def test_ensures_false_carries_the_result(self):
        violation = self._violation(
            "func f(n: Int) -> Int\n"
            "    ensures result > 0\n"
            "    example 1 -> 1\n"
            "    return n\n"
            "end func\n",
            [-3],
        )
        assert (violation.clause, violation.reason) == ("ensures", "false")
        assert (violation.clause_index, violation.call_depth) == (0, 1)
        assert violation.result_display == "-3"

    def test_ensures_propagate(self):
        violation = self._violation(
            "func f(xs: List[Int]) -> Option[Int]\n"
            "    ensures list_first(xs)? >= 0\n"
            "    example [1] -> Some(1)\n"
            "    return list_first(xs)\n"
            "end func\n",
            [[]],
        )
        assert (violation.clause, violation.reason) == ("ensures", "propagate")

    def test_with_message_keeps_the_fields(self):
        original = ContractViolationError(
            "m", None, call_depth=1, clause="requires", reason="false", clause_index=2
        )
        copy = original.with_message("explained", None)
        assert copy.message == "explained"
        assert (copy.clause, copy.reason, copy.clause_index, copy.call_depth) == (
            "requires",
            "false",
            2,
            1,
        )

    def test_every_raise_site_sets_clause(self):
        """No ContractViolationError is built without its structured fields."""
        offenders = []
        for path in sorted((REPO_ROOT / "geno").glob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "ContractViolationError"
                ):
                    keywords = {keyword.arg for keyword in node.keywords}
                    if (
                        not {"clause", "reason", "clause_index", "call_depth"}
                        <= keywords
                    ):
                        offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == []
