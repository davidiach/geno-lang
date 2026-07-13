"""
Tests for Backend Parity
=========================

Verifies that the interpreter, compiled Python backend, and compiled JS
backend produce identical stdout output for a range of Geno programs.

Each test program uses print() to emit output and returns Unit so that the
main-guard return-value printing does not interfere.  We compare the
interpreter's captured output against subprocess stdout from each compiled
backend.

The parity corpus compares canonical stdout directly, including nested string
formatting. Top-level strings print bare; strings nested in values use Geno's
double-quoted escaped representation on every backend.
"""
# mypy: disable-error-code="no-redef"

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from geno.api import RunConfig, run
from geno.compiler import Compiler, compile_to_python
from geno.dependency_graph import DependencyGraph
from geno.js_compiler import JSCompiler, compile_to_js
from geno.project_graph import ProjectGraph
from geno.tests._script_runner import run_node_code, run_python_code
from geno.tests.fuzzing.arith import (
    ArithmeticExpr,
    IntPredicateSpec,
    IntTransformSpec,
    LetBinding,
    ParityPrintStep,
)
from geno.tests.fuzzing.arith import (
    apply_transform as _apply_transform,
)
from geno.tests.fuzzing.arith import (
    binding_ref as _binding_ref,
)
from geno.tests.fuzzing.arith import (
    combine as _combine,
)
from geno.tests.fuzzing.arith import (
    geno_int_literal as _geno_int_literal,
)
from geno.tests.fuzzing.arith import (
    if_print_step as _if_print_step,
)
from geno.tests.fuzzing.arith import (
    lit as _lit,
)
from geno.tests.fuzzing.arith import (
    predicate_source as _predicate_source,
)
from geno.tests.fuzzing.arith import (
    print_step as _print_step,
)
from geno.tests.fuzzing.arith import (
    test_predicate as _test_predicate,
)
from geno.tests.fuzzing.arith import (
    transform_source as _transform_source,
)
from geno.typechecker import TypeChecker
from geno.types import TypeError as GenoTypeError

try:
    from hypothesis import example, given, settings
    from hypothesis import strategies as st

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False

    def given(*args, **kwargs):
        def decorator(f):
            return pytest.mark.skip(reason="hypothesis not installed")(f)

        return decorator

    def example(*args, **kwargs):
        def decorator(f):
            return pytest.mark.skip(reason="hypothesis not installed")(f)

        return decorator

    def settings(*args, **kwargs):
        def decorator(f):
            return f

        return decorator

    class st:
        @staticmethod
        def integers(*args, **kwargs):
            return None

        @staticmethod
        def sampled_from(*args, **kwargs):
            return None

        @staticmethod
        def builds(*args, **kwargs):
            return None

        @staticmethod
        def one_of(*args, **kwargs):
            return None

        @staticmethod
        def recursive(*args, **kwargs):
            return None

        @staticmethod
        def just(*args, **kwargs):
            return None

        @staticmethod
        def lists(*args, **kwargs):
            return None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HAS_NODE = shutil.which("node") is not None


@dataclass(frozen=True)
class HigherOrderListSpec:
    """Inputs for one generated higher-order/list parity scenario."""

    xs: tuple[int, ...]
    transform: IntTransformSpec
    predicate: IntPredicateSpec
    initial: int
    extra: int


@dataclass(frozen=True)
class ImportedProjectSpec:
    """Inputs for one generated imported two-file parity scenario."""

    xs: tuple[int, ...]
    transform: IntTransformSpec
    predicate: IntPredicateSpec
    initial: int
    arg: int


@dataclass(frozen=True)
class AlgebraicParitySpec:
    """Inputs for one generated ADT/Option/Result parity scenario."""

    adt_tag: str
    adt_left: int
    adt_right: int
    option_tag: str
    option_value: int
    result_tag: str
    result_value: int
    nested_tag: str
    nested_value: int


@dataclass(frozen=True)
class TryCatchParitySpec:
    """Inputs for one generated ``try`` / ``catch`` parity scenario.

    Three input integers exercise each of the three code paths through
    ``safe_run`` (see ``_try_catch_program_for_spec``): the success
    path, a ``throw "negative"`` caught by the explicit match arm, and
    a ``throw "too big"`` caught by the fallback arm.  A generated
    ``multiplier`` is applied to the recovered values so different
    Hypothesis draws produce different reference outputs instead of
    converging on the same printed numbers.
    """

    pass_value: int
    negative_value: int
    big_value: int
    multiplier: int


@dataclass(frozen=True)
class ParityProgramScenario:
    """One generated Geno program plus its expected stdout."""

    source: str
    expected_output: str


@dataclass(frozen=True)
class MultiModuleParityScenario:
    """One generated multi-module Geno project plus its expected stdout."""

    main_source: str
    modules: tuple[tuple[str, str], ...]
    expected_output: str


def _program_for_scenario(
    bindings: list[LetBinding], steps: list[ParityPrintStep]
) -> ParityProgramScenario:
    """Build a tiny Geno program with optional let bindings and print steps."""
    binding_lines = [
        f"    let {binding.name}: Int = {binding.expr.source}" for binding in bindings
    ]
    step_lines = [step.source for step in steps]
    body = "\n".join(binding_lines + step_lines)
    expected_output = "".join(f"{value}\n" for step in steps for value in step.outputs)
    return ParityProgramScenario(
        source=f"func main() -> Unit\n{body}\n    return ()\nend func\n",
        expected_output=expected_output,
    )


def _higher_order_list_program_for_spec(
    spec: HigherOrderListSpec,
) -> ParityProgramScenario:
    """Build a small list/lambda Geno program and its reference stdout."""
    xs_list = list(spec.xs)
    mapped = [_apply_transform(spec.transform, x) for x in xs_list]
    filtered = [x for x in xs_list if _test_predicate(spec.predicate, x)]
    folded = spec.initial
    for x in xs_list:
        folded += _apply_transform(spec.transform, x)
    combined = mapped + filtered + [spec.extra]

    xs_source = ", ".join(_geno_int_literal(x) for x in xs_list)
    transform_source = _transform_source(spec.transform, "x")
    predicate_source = _predicate_source(spec.predicate, "x")
    reducer_source = f"(acc + {_transform_source(spec.transform, 'x')})"
    source = f"""\
func main() -> Unit
    let xs: List[Int] = [{xs_source}]
    let mapped: List[Int] = map(xs, fn(x: Int) -> {transform_source})
    let filtered: List[Int] = filter(xs, fn(x: Int) -> {predicate_source})
    let combined: List[Int] = concat(mapped, append(filtered, {_geno_int_literal(spec.extra)}))
    print(length(xs))
    print(length(mapped))
    if length(mapped) == 0 then
        print(0)
    else
        print(head(mapped))
    end if
    print(length(filtered))
    if length(filtered) == 0 then
        print(0)
    else
        print(head(filtered))
    end if
    print(fold(list: xs, initial: {_geno_int_literal(spec.initial)}, reducer: fn(acc: Int, x: Int) -> {reducer_source}))
    print(length(combined))
    print(head(combined))
    return ()
end func
"""
    expected_values = (
        len(xs_list),
        len(mapped),
        mapped[0] if mapped else 0,
        len(filtered),
        filtered[0] if filtered else 0,
        folded,
        len(combined),
        combined[0],
    )
    return ParityProgramScenario(
        source=source,
        expected_output="".join(f"{value}\n" for value in expected_values),
    )


def _imported_project_for_spec(spec: ImportedProjectSpec) -> MultiModuleParityScenario:
    """Build a small two-file project and its reference stdout."""
    xs_list = list(spec.xs)
    mapped = [_apply_transform(spec.transform, x) for x in xs_list]
    filtered = [x for x in xs_list if _test_predicate(spec.predicate, x)]
    summed = spec.initial + sum(mapped)
    branched = (
        _apply_transform(spec.transform, spec.arg)
        if _test_predicate(spec.predicate, spec.arg)
        else 0 - _apply_transform(spec.transform, spec.arg)
    )
    conditional_count = (
        len(filtered) + 1
        if _test_predicate(spec.predicate, spec.arg)
        else len(filtered) - 1
    )

    xs_source = ", ".join(_geno_int_literal(x) for x in xs_list)
    transform_source = _transform_source(spec.transform, "x")
    predicate_source = _predicate_source(spec.predicate, "x")

    helper_source = f"""\
@untested("generated parity")
func transform(x: Int) -> Int
    return {transform_source}
end func

@untested("generated parity")
func keep(x: Int) -> Bool
    return {predicate_source}
end func

@untested("generated parity")
func sum_transformed(xs: List[Int]) -> Int
    let mapped: List[Int] = map(xs, fn(x: Int) -> transform(x))
    return fold(list: mapped, initial: {_geno_int_literal(spec.initial)}, reducer: fn(acc: Int, x: Int) -> acc + x)
end func

@untested("generated parity")
func count_kept(xs: List[Int]) -> Int
    return length(filter(xs, fn(x: Int) -> keep(x)))
end func

@untested("generated parity")
func branch_value(x: Int) -> Int
    if keep(x) then
        return transform(x)
    else
        return 0 - transform(x)
    end if
end func
"""
    main_source = f"""\
import Helper

func main() -> Unit
    let xs: List[Int] = [{xs_source}]
    let mapped: List[Int] = map(xs, fn(x: Int) -> transform(x))
    let filtered: List[Int] = filter(xs, fn(x: Int) -> keep(x))
    print(transform({_geno_int_literal(spec.arg)}))
    print(sum_transformed(xs))
    print(count_kept(xs))
    print(branch_value({_geno_int_literal(spec.arg)}))
    if keep({_geno_int_literal(spec.arg)}) then
        print(count_kept(xs) + 1)
    else
        print(count_kept(xs) - 1)
    end if
    if length(mapped) == 0 then
        print(0)
    else
        print(head(mapped))
    end if
    if length(filtered) == 0 then
        print(0)
    else
        print(head(filtered))
    end if
    return ()
end func
"""
    expected_values = (
        _apply_transform(spec.transform, spec.arg),
        summed,
        len(filtered),
        branched,
        conditional_count,
        mapped[0] if mapped else 0,
        filtered[0] if filtered else 0,
    )
    return MultiModuleParityScenario(
        main_source=main_source,
        modules=(("Helper", helper_source),),
        expected_output="".join(f"{value}\n" for value in expected_values),
    )


def _adt_value_source(tag: str, left: int, right: int) -> str:
    """Render one generated custom-ADT constructor."""
    if tag == "Wrap":
        return f"Wrap({_geno_int_literal(left)})"
    if tag == "Pair":
        return f"Pair({_geno_int_literal(left)}, {_geno_int_literal(right)})"
    if tag == "Absent":
        return "Absent"
    raise ValueError(f"unknown ADT tag: {tag}")


def _option_value_source(tag: str, value: int) -> str:
    """Render one generated Option[Int] constructor."""
    if tag == "some":
        return f"Some({_geno_int_literal(value)})"
    if tag == "none":
        return "None"
    raise ValueError(f"unknown Option tag: {tag}")


def _result_value_source(tag: str, value: int) -> str:
    """Render one generated Result[Int, Int] constructor."""
    if tag == "ok":
        return f"Ok({_geno_int_literal(value)})"
    if tag == "err":
        return f"Err({_geno_int_literal(value)})"
    raise ValueError(f"unknown Result tag: {tag}")


def _nested_option_result_source(tag: str, value: int) -> str:
    """Render one generated Option[Result[Int, Int]] constructor."""
    if tag == "none":
        return "None"
    if tag == "ok":
        return f"Some(Ok({_geno_int_literal(value)}))"
    if tag == "err":
        return f"Some(Err({_geno_int_literal(value)}))"
    raise ValueError(f"unknown nested tag: {tag}")


def _algebraic_program_for_spec(spec: AlgebraicParitySpec) -> ParityProgramScenario:
    """Build a small ADT/Option/Result program and its reference stdout."""
    adt_source = _adt_value_source(spec.adt_tag, spec.adt_left, spec.adt_right)
    option_source = _option_value_source(spec.option_tag, spec.option_value)
    result_source = _result_value_source(spec.result_tag, spec.result_value)
    nested_source = _nested_option_result_source(spec.nested_tag, spec.nested_value)

    if spec.adt_tag == "Wrap":
        payload_score = spec.adt_left + 1
        payload_detail = spec.adt_left
    elif spec.adt_tag == "Pair":
        payload_score = spec.adt_left + spec.adt_right
        payload_detail = spec.adt_left - spec.adt_right
    else:
        payload_score = 0
        payload_detail = 0

    option_score = spec.option_value + 5 if spec.option_tag == "some" else -5
    result_score = (
        spec.result_value * 2 if spec.result_tag == "ok" else 0 - spec.result_value
    )
    if spec.nested_tag == "ok":
        nested_score = spec.nested_value + 10
    elif spec.nested_tag == "err":
        nested_score = spec.nested_value - 10
    else:
        nested_score = 99

    source = f"""\
type Payload = Wrap(value: Int) | Pair(left: Int, right: Int) | Absent

@untested("generated parity")
func payload_score(p: Payload) -> Int
    match p with
        | Wrap(v) -> return v + 1
        | Pair(a, b) -> return a + b
        | Absent -> return 0
    end match
end func

@untested("generated parity")
func option_score(value: Option[Int]) -> Int
    match value with
        | Some(v) -> return v + 5
        | None -> return 0 - 5
    end match
end func

@untested("generated parity")
func result_score(value: Result[Int, Int]) -> Int
    match value with
        | Ok(v) -> return v * 2
        | Err(e) -> return 0 - e
    end match
end func

func main() -> Unit
    let payload: Payload = {adt_source}
    let maybe: Option[Int] = {option_source}
    let outcome: Result[Int, Int] = {result_source}
    let nested: Option[Result[Int, Int]] = {nested_source}
    print(payload_score(payload))
    print(option_score(maybe))
    print(result_score(outcome))
    match nested with
        | Some(Ok(v)) -> print(v + 10)
        | Some(Err(e)) -> print(e - 10)
        | None -> print(99)
    end match
    match payload with
        | Wrap(v) -> print(v)
        | Pair(a, b) -> print(a - b)
        | Absent -> print(0)
    end match
    return ()
end func
"""
    expected_values = (
        payload_score,
        option_score,
        result_score,
        nested_score,
        payload_detail,
    )
    return ParityProgramScenario(
        source=source,
        expected_output="".join(f"{value}\n" for value in expected_values),
    )


def _try_catch_program_for_spec(spec: TryCatchParitySpec) -> ParityProgramScenario:
    """Build a small ``try`` / ``catch`` program and its reference stdout.

    Exercises the three backend code paths for exception control-flow
    (#627 gap: the existing fuzz suite covered pure arithmetic, higher-
    order list programs, imported projects, and ADT/Option/Result
    programs — but no ``try`` / ``catch``).  ``safe_run`` dispatches
    on the thrown message so each arm of the catch actually runs on
    every backend rather than just the success path.
    """
    inputs = (spec.pass_value, spec.negative_value, spec.big_value)

    # Keep the branching in ``_reference_output`` in sync with the
    # constants in the generated ``maybe_throw`` / ``safe_run`` source
    # below (the ``< 0`` / ``> 100`` guards and the ``+10`` / ``-1`` /
    # ``-2`` payloads).  Drift between the two sides turns this test
    # into a false positive / negative.
    def _reference_output(n: int, mult: int) -> int:
        if n < 0:
            return -1 * mult
        if n > 100:
            return -2 * mult
        return (n + 10) * mult

    source = f"""\
@untested("generated parity")
func maybe_throw(n: Int) -> Int
    if n < 0 then
        throw "negative"
    end if
    if n > 100 then
        throw "too big"
    end if
    return n
end func

@untested("generated parity")
func safe_run(n: Int, mult: Int) -> Int
    try
        return (maybe_throw(n) + 10) * mult
    catch msg: String
        if msg == "negative" then
            return (0 - 1) * mult
        else
            return (0 - 2) * mult
        end if
    end try
end func

func main() -> Unit
    print(safe_run({_geno_int_literal(spec.pass_value)}, {_geno_int_literal(spec.multiplier)}))
    print(safe_run({_geno_int_literal(spec.negative_value)}, {_geno_int_literal(spec.multiplier)}))
    print(safe_run({_geno_int_literal(spec.big_value)}, {_geno_int_literal(spec.multiplier)}))
    return ()
end func
"""
    expected = "".join(f"{_reference_output(n, spec.multiplier)}\n" for n in inputs)
    return ParityProgramScenario(source=source, expected_output=expected)


if HYPOTHESIS_AVAILABLE:
    _LEAF_EXPR_STRATEGY = st.integers(min_value=-9, max_value=9).map(_lit)
    _INT_TRANSFORM_STRATEGY = st.builds(
        IntTransformSpec,
        operator=st.sampled_from(["+", "-", "*"]),
        constant=st.integers(min_value=-4, max_value=4),
    )
    _INT_PREDICATE_STRATEGY = st.builds(
        IntPredicateSpec,
        operator=st.sampled_from(["==", "!=", "<", "<=", ">", ">="]),
        constant=st.integers(min_value=-6, max_value=6),
    )
    _HIGHER_ORDER_LIST_SPEC_STRATEGY = st.builds(
        HigherOrderListSpec,
        xs=st.lists(st.integers(min_value=-6, max_value=6), min_size=0, max_size=5).map(
            tuple
        ),
        transform=_INT_TRANSFORM_STRATEGY,
        predicate=_INT_PREDICATE_STRATEGY,
        initial=st.integers(min_value=-8, max_value=8),
        extra=st.integers(min_value=-8, max_value=8),
    )
    _IMPORTED_PROJECT_SPEC_STRATEGY = st.builds(
        ImportedProjectSpec,
        xs=st.lists(st.integers(min_value=-6, max_value=6), min_size=0, max_size=5).map(
            tuple
        ),
        transform=_INT_TRANSFORM_STRATEGY,
        predicate=_INT_PREDICATE_STRATEGY,
        initial=st.integers(min_value=-8, max_value=8),
        arg=st.integers(min_value=-8, max_value=8),
    )
    _ALGEBRAIC_PARITY_SPEC_STRATEGY = st.builds(
        AlgebraicParitySpec,
        adt_tag=st.sampled_from(["Wrap", "Pair", "Absent"]),
        adt_left=st.integers(min_value=-6, max_value=6),
        adt_right=st.integers(min_value=-6, max_value=6),
        option_tag=st.sampled_from(["some", "none"]),
        option_value=st.integers(min_value=-8, max_value=8),
        result_tag=st.sampled_from(["ok", "err"]),
        result_value=st.integers(min_value=-8, max_value=8),
        nested_tag=st.sampled_from(["none", "ok", "err"]),
        nested_value=st.integers(min_value=-8, max_value=8),
    )
    _TRY_CATCH_PARITY_SPEC_STRATEGY = st.builds(
        TryCatchParitySpec,
        # Success path — a value ``maybe_throw`` does not reject.
        pass_value=st.integers(min_value=0, max_value=100),
        # Negative-throw path — exercised by the explicit catch arm.
        negative_value=st.integers(min_value=-50, max_value=-1),
        # Too-big-throw path — exercised by the fallback catch arm.
        big_value=st.integers(min_value=101, max_value=200),
        # Non-zero multiplier keeps the three printed paths distinct;
        # allowing ``0`` would collapse the whole program to ``0 0 0``
        # and turn the parity check into a vacuous pass.
        multiplier=st.one_of(
            st.integers(min_value=-5, max_value=-1),
            st.integers(min_value=1, max_value=5),
        ),
    )

    def _expr_strategy_with_terms(
        extra_terms: list[ArithmeticExpr],
    ):  # type: ignore[no-untyped-def]
        """Build an expression strategy with optional bound-name terminals."""
        leaf = _LEAF_EXPR_STRATEGY
        if extra_terms:
            leaf = st.one_of(leaf, st.sampled_from(extra_terms))
        return st.recursive(
            leaf,
            lambda inner: st.one_of(
                st.builds(_combine, st.just("+"), inner, inner),
                st.builds(_combine, st.just("-"), inner, inner),
                st.builds(_combine, st.just("*"), inner, inner),
                st.builds(
                    _combine,
                    st.just("/"),
                    inner,
                    inner.filter(lambda e: e.value != 0),
                ),
                st.builds(
                    _combine,
                    st.just("%"),
                    inner,
                    inner.filter(lambda e: e.value != 0),
                ),
            ),
            max_leaves=8,
        )

    _ARITH_EXPR_STRATEGY = _expr_strategy_with_terms([])

    def _parity_step_strategy_with_terms(
        extra_terms: list[ArithmeticExpr],
    ):  # type: ignore[no-untyped-def]
        """Build a parity-step strategy using the provided bound expressions."""
        exprs = _expr_strategy_with_terms(extra_terms)
        return st.one_of(
            exprs.map(_print_step),
            st.builds(
                _if_print_step,
                st.sampled_from(["==", "!=", "<", "<=", ">", ">="]),
                exprs,
                exprs,
                exprs,
                exprs,
            ),
        )

    _PARITY_STEP_STRATEGY = _parity_step_strategy_with_terms([])

    @st.composite
    def _program_scenario_strategy(draw):
        """Generate a small pure program with optional let bindings."""
        binding_count = draw(st.integers(min_value=0, max_value=3))
        bindings: list[LetBinding] = []
        bound_terms: list[ArithmeticExpr] = []

        for index in range(binding_count):
            expr = draw(_expr_strategy_with_terms(bound_terms))
            binding = LetBinding(name=f"x{index}", expr=expr)
            bindings.append(binding)
            bound_terms.append(_binding_ref(binding))

        step_strategy = _parity_step_strategy_with_terms(bound_terms)
        steps = draw(st.lists(step_strategy, min_size=0, max_size=3))
        if bound_terms:
            forced_expr = draw(st.sampled_from(bound_terms))
            steps = [_print_step(forced_expr)] + steps
        elif not steps:
            steps = [draw(_parity_step_strategy_with_terms([]))]

        return _program_for_scenario(bindings, steps)

    _PROGRAM_SCENARIO_STRATEGY = _program_scenario_strategy()
else:  # pragma: no cover - exercised only without hypothesis installed
    _ARITH_EXPR_STRATEGY = cast(Any, None)
    _PARITY_STEP_STRATEGY = cast(Any, None)
    _PROGRAM_SCENARIO_STRATEGY = cast(Any, None)
    _HIGHER_ORDER_LIST_SPEC_STRATEGY = cast(Any, None)
    _IMPORTED_PROJECT_SPEC_STRATEGY = cast(Any, None)
    _ALGEBRAIC_PARITY_SPEC_STRATEGY = cast(Any, None)
    _TRY_CATCH_PARITY_SPEC_STRATEGY = cast(Any, None)


def _interpreter_output(source: str) -> str:
    """Run source via the interpreter and return captured print output."""
    config = RunConfig(timeout=10.0, capabilities={"print"})
    result = run(source, config=config)
    assert result.ok, f"Interpreter failed: {[d.message for d in result.diagnostics]}"
    return cast(str, result.output)


def _interpreter_project_output(
    main_source: str, modules: dict[str, str], entrypoint: str = "Main"
) -> str:
    """Run a generated multi-module project via the embedding API."""
    result = run(
        main_source,
        filename=f"{entrypoint}.geno",
        config=RunConfig(modules=modules, timeout=10.0, capabilities={"print"}),
    )
    assert result.ok, f"Interpreter failed: {[d.message for d in result.diagnostics]}"
    return cast(str, result.output)


def _cap_args(capabilities: set[str] | frozenset[str] | None) -> list[str]:
    if not capabilities:
        return []
    return ["--cap", ",".join(sorted(capabilities))]


def _compiled_python_output(
    source: str, capabilities: set[str] | frozenset[str] | None = frozenset({"print"})
) -> str:
    """Compile source to Python, execute as a subprocess, return stdout."""
    python_code = compile_to_python(source)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(python_code)
        f.flush()
        tmp_path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, tmp_path, *_cap_args(capabilities)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Compiled Python failed (rc={proc.returncode}):\n{proc.stderr}"
            )
        return proc.stdout
    finally:
        os.unlink(tmp_path)


def _compiled_js_output(
    source: str, capabilities: set[str] | frozenset[str] | None = frozenset({"print"})
) -> str:
    """Compile source to JS, execute via Node.js, return stdout."""
    js_out = compile_to_js(source)
    assert isinstance(js_out, str)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".js",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as f:
        f.write(js_out)
        f.flush()
        tmp_path = f.name
    try:
        proc = subprocess.run(
            ["node", tmp_path, *_cap_args(capabilities)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Compiled JS failed (rc={proc.returncode}):\n{proc.stderr}"
            )
        return proc.stdout
    finally:
        os.unlink(tmp_path)


def _compiled_python_project_output(
    main_source: str,
    modules: dict[str, str],
    entrypoint: str = "Main",
    capabilities: set[str] | frozenset[str] | None = frozenset({"print"}),
) -> str:
    """Compile a generated multi-module project to Python and run it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        files = [entrypoint, *modules.keys()]
        files_toml = ", ".join(f'"{name}"' for name in files)
        (root / "geno.toml").write_text(
            f'entrypoint = "{entrypoint}"\nfiles = [{files_toml}]\n'
        )
        (root / f"{entrypoint}.geno").write_text(main_source)
        for module_name, module_source in modules.items():
            (root / f"{module_name}.geno").write_text(module_source)

        dg = DependencyGraph.resolve(ProjectGraph.discover(root))
        TypeChecker().check_project_graph(dg)
        py_code = Compiler().compile_project(dg)
        proc = run_python_code(
            py_code,
            python_executable=sys.executable,
            args=_cap_args(capabilities),
            timeout=10,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Compiled Python project failed (rc={proc.returncode}):\n{proc.stderr}"
            )
        return cast(str, proc.stdout)


def _compiled_js_project_output(
    main_source: str,
    modules: dict[str, str],
    entrypoint: str = "Main",
    capabilities: set[str] | frozenset[str] | None = frozenset({"print"}),
) -> str:
    """Compile a generated multi-module project to JS and run it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        files = [entrypoint, *modules.keys()]
        files_toml = ", ".join(f'"{name}"' for name in files)
        (root / "geno.toml").write_text(
            f'entrypoint = "{entrypoint}"\nfiles = [{files_toml}]\n'
        )
        (root / f"{entrypoint}.geno").write_text(main_source)
        for module_name, module_source in modules.items():
            (root / f"{module_name}.geno").write_text(module_source)

        dg = DependencyGraph.resolve(ProjectGraph.discover(root))
        TypeChecker().check_project_graph(dg)
        js_code = JSCompiler().compile_project(dg)
        proc = run_node_code(js_code, args=_cap_args(capabilities), timeout=10)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Compiled JS project failed (rc={proc.returncode}):\n{proc.stderr}"
            )
        return cast(str, proc.stdout)


def _assert_expected_backend_outputs(
    *,
    label: str,
    context: str,
    expected: str,
    interp_out: str,
    py_out: str,
    js_out: str,
) -> None:
    """Assert interpreter/Python/JS outputs all match one oracle."""
    assert interp_out == expected, (
        f"Interpreter mismatch against {label} oracle:\n"
        f"{context}"
        f"--- expected ---\n{expected!r}\n"
        f"--- interpreter ---\n{interp_out!r}"
    )
    assert py_out == expected, (
        f"Compiled Python mismatch against {label} oracle:\n"
        f"{context}"
        f"--- expected ---\n{expected!r}\n"
        f"--- compiled py ---\n{py_out!r}"
    )
    assert js_out == expected, (
        f"Compiled JS mismatch against {label} oracle:\n"
        f"{context}"
        f"--- expected ---\n{expected!r}\n"
        f"--- compiled js ---\n{js_out!r}"
    )


# ---------------------------------------------------------------------------
# Test programs
#
# Each program uses print() to produce canonical output and returns Unit.
# Interpreter, compiled Python, and compiled JavaScript are expected to agree
# for both scalar and composite values.
# ---------------------------------------------------------------------------

PARITY_PROGRAMS = [
    pytest.param(
        # --- Arithmetic ---
        """\
func main() -> Unit
    print(1 + 2)
    print(10 - 3)
    print(4 * 5)
    print(10 / 3)
    print(2 + 3 * 4)
    print(17 % 5)
    return ()
end func
""",
        id="arithmetic",
    ),
    pytest.param(
        # --- Map builtins with tuple pairs ---
        """\
func main() -> Unit
    let m: Map[Int, String] = map_from_list([(1, "one"), (2, "two")])
    let entries: List[(Int, String)] = map_entries(m)
    print(length(entries))
    let rebuilt: Map[Int, String] = map_from_entries(entries)
    match map_get(rebuilt, 1) with
        | Some(text) -> print(length(text))
        | None -> print(0)
    end match
    match map_get(rebuilt, 2) with
        | Some(text) -> print(length(text))
        | None -> print(0)
    end match
    return ()
end func
""",
        id="map_tuple_pairs",
    ),
    pytest.param(
        # --- Negative arithmetic edge cases ---
        """\
func main() -> Unit
    print((0 - 7) / 2)
    print(7 / (0 - 2))
    print((0 - 7) / (0 - 2))
    print((0 - 7) % 3)
    print(7 % (0 - 3))
    print((0 - 7) % (0 - 3))
    print((0.0 - 7.5) % 3.0)
    return ()
end func
""",
        id="negative_arithmetic",
    ),
    pytest.param(
        # --- Array index-assign with bounds checking ---
        """\
func main() -> Unit
    var arr: Array[Int] = array_new(3, 0)
    arr[0] = 10
    arr[1] = 20
    arr[2] = 30
    print(array_get(arr, 0))
    print(array_get(arr, 1))
    print(array_get(arr, 2))
    return ()
end func
""",
        id="array_index_assign",
    ),
    pytest.param(
        # --- Boolean printing uses Geno conventions ---
        """\
func main() -> Unit
    print(true)
    print(false)
    print(3 > 2)
    print(1 == 1)
    print(1 != 1)
    return ()
end func
""",
        id="boolean_print_format",
    ),
    pytest.param(
        # --- Float / tuple / constructor formatting matches across backends ---
        """\
type Box = Box(value: Bool, items: List[Bool])

func main() -> Unit
    let msg: String = "hi"
    print(to_string(1.0) == "1.0")
    print(to_string(()) == "()")
    print(to_string((1, 2)) == "(1, 2)")
    print(to_string([true, false]) == "[true, false]")
    print(to_string(Some(1)) == "Some(value: 1)")
    print(to_string(Some(msg)) == "Some(value: 'hi')")
    print(to_string(Box(true, [false, true])) == "Box(value: true, items: [false, true])")
    print(f"{1.0}|{(1, 2)}|{Some(msg)}" == "1.0|(1, 2)|Some(value: 'hi')")
    print(f"{true}|{[false, true]}" == "true|[false, true]")
    return ()
end func
""",
        id="stringify_format_parity",
    ),
    pytest.param(
        # --- Negative exponent returns float ---
        """\
func main() -> Unit
    print(2 ** -1)
    print(4 ** -1)
    print(10 ** -2)
    print((0.0 - 2.0) ** 3.0)
    return ()
end func
""",
        id="negative_exponent",
    ),
    pytest.param(
        # --- Integer comparisons and conditionals ---
        """\
func main() -> Unit
    if 3 > 2 then
        print(1)
    else
        print(0)
    end if
    if 5 == 5 then
        print(1)
    else
        print(0)
    end if
    if 1 != 2 then
        print(1)
    else
        print(0)
    end if
    if 4 <= 4 then
        print(1)
    else
        print(0)
    end if
    return ()
end func
""",
        id="conditionals",
    ),
    pytest.param(
        # --- String operations (print lengths as integers) ---
        """\
func main() -> Unit
    let s: String = "hello" + " " + "world"
    print(length(s))
    let parts: List[String] = split(s, " ")
    print(length(parts))
    let trimmed: String = trim("  hi  ")
    print(length(trimmed))
    return ()
end func
""",
        id="string_ops",
    ),
    pytest.param(
        # --- List operations ---
        """\
func main() -> Unit
    let xs: List[Int] = [10, 20, 30, 40, 50]
    print(length(xs))
    print(head(xs))
    print(length(tail(xs)))
    let ys: List[Int] = append(xs, 60)
    print(length(ys))
    let zs: List[Int] = concat([1, 2], [3, 4])
    print(length(zs))
    print(head(zs))
    return ()
end func
""",
        id="list_ops",
    ),
    pytest.param(
        # --- While loop ---
        """\
func main() -> Unit
    var sum: Int = 0
    var i: Int = 1
    while i <= 10 do
        sum = sum + i
        i = i + 1
    end while
    print(sum)
    return ()
end func
""",
        id="while_loop",
    ),
    pytest.param(
        # --- For loop ---
        """\
func main() -> Unit
    var total: Int = 0
    for x: Int in [1, 2, 3, 4, 5] do
        total = total + x
    end for
    print(total)
    return ()
end func
""",
        id="for_loop",
    ),
    pytest.param(
        # --- Recursion (fibonacci) ---
        """\
func fib(n: Int) -> Int
    example 0 -> 0
    example 1 -> 1
    example 5 -> 5
    if n <= 1 then
        return n
    end if
    return fib(n - 1) + fib(n - 2)
end func

func main() -> Unit
    print(fib(0))
    print(fib(1))
    print(fib(5))
    print(fib(10))
    return ()
end func
""",
        id="recursion_fibonacci",
    ),
    pytest.param(
        # --- Higher-order functions: map, filter, fold ---
        """\
func main() -> Unit
    let nums: List[Int] = [1, 2, 3, 4, 5]
    let doubled: List[Int] = map(nums, fn(x: Int) -> x * 2)
    print(head(doubled))
    print(length(doubled))

    let evens: List[Int] = filter(nums, fn(x: Int) -> x % 2 == 0)
    print(length(evens))
    print(head(evens))

    let total: Int = fold(list: nums, initial: 0, reducer: fn(acc: Int, x: Int) -> acc + x)
    print(total)
    return ()
end func
""",
        id="higher_order_functions",
    ),
    pytest.param(
        # --- Closures and lambdas ---
        """\
func apply(f: (Int) -> Int, x: Int) -> Int
    example (fn(n: Int) -> n + 1, 5) -> 6
    return f(x)
end func

func main() -> Unit
    let add5: (Int) -> Int = fn(x: Int) -> x + 5
    let add10: (Int) -> Int = fn(x: Int) -> x + 10
    print(apply(add5, 3))
    print(apply(add10, 7))
    print(apply(add5, apply(add10, 1)))

    let double: (Int) -> Int = fn(x: Int) -> x * 2
    print(apply(double, 6))
    return ()
end func
""",
        id="closures",
    ),
    pytest.param(
        # --- ADTs and pattern matching ---
        """\
type Color = Red | Green | Blue

func color_code(c: Color) -> Int
    example Red -> 1
    example Green -> 2
    match c with
        | Red -> return 1
        | Green -> return 2
        | Blue -> return 3
    end match
end func

func main() -> Unit
    print(color_code(Red))
    print(color_code(Green))
    print(color_code(Blue))
    return ()
end func
""",
        id="adt_simple",
    ),
    pytest.param(
        # --- ADTs with fields ---
        """\
type Shape = Circle(radius: Int) | Rect(w: Int, h: Int)

func area(s: Shape) -> Int
    example Circle(5) -> 75
    example Rect(3, 4) -> 12
    match s with
        | Circle(r) -> return r * r * 3
        | Rect(w, h) -> return w * h
    end match
end func

func main() -> Unit
    print(area(Circle(5)))
    print(area(Rect(3, 4)))
    print(area(Circle(1)) + area(Rect(2, 3)))
    return ()
end func
""",
        id="adt_with_fields",
    ),
    pytest.param(
        # --- Option type ---
        """\
func safe_head(xs: List[Int]) -> Option[Int]
    example [10, 20] -> Some(10)
    example [] -> None
    if length(xs) == 0 then
        return None
    end if
    return Some(head(xs))
end func

func option_value(o: Option[Int]) -> Int
    example Some(7) -> 7
    example None -> -1
    match o with
        | Some(v) -> return v
        | None -> return -1
    end match
end func

func main() -> Unit
    print(option_value(safe_head([10, 20, 30])))
    print(option_value(safe_head([])))
    return ()
end func
""",
        id="option_type",
    ),
    pytest.param(
        # --- Result type ---
        """\
func safe_div(a: Int, b: Int) -> Result[Int, Int]
    example (10, 2) -> Ok(5)
    example (7, 0) -> Err(0)
    if b == 0 then
        return Err(0)
    end if
    return Ok(a / b)
end func

func result_val(r: Result[Int, Int]) -> Int
    example Ok(5) -> 5
    example Err(0) -> -1
    match r with
        | Ok(v) -> return v
        | Err(e) -> return e - 1
    end match
end func

func main() -> Unit
    print(result_val(safe_div(10, 2)))
    print(result_val(safe_div(7, 0)))
    print(result_val(safe_div(100, 4)))
    return ()
end func
""",
        id="result_type",
    ),
    pytest.param(
        # --- Nested pattern matching with multiple ADTs ---
        """\
type Expr = Lit(val: Int) | Add(a: Int, b: Int) | Neg(val: Int)

func eval_expr(e: Expr) -> Int
    example Lit(5) -> 5
    example Add(3, 4) -> 7
    example Neg(10) -> -10
    match e with
        | Lit(v) -> return v
        | Add(a, b) -> return a + b
        | Neg(v) -> return 0 - v
    end match
end func

func main() -> Unit
    print(eval_expr(Lit(42)))
    print(eval_expr(Add(10, 20)))
    print(eval_expr(Neg(5)))
    print(eval_expr(Lit(1)) + eval_expr(Add(2, 3)))
    return ()
end func
""",
        id="nested_pattern_matching",
    ),
    pytest.param(
        # --- Variables and mutation ---
        """\
func main() -> Unit
    var x: Int = 10
    print(x)
    x = x + 5
    print(x)
    x = x * 2
    print(x)
    return ()
end func
""",
        id="variables_and_mutation",
    ),
    pytest.param(
        # --- Pipeline operator ---
        """\
func double(x: Int) -> Int
    example 3 -> 6
    return x * 2
end func

func add_one(x: Int) -> Int
    example 3 -> 4
    return x + 1
end func

func main() -> Unit
    let result: Int = 5 |> double(_) |> add_one(_)
    print(result)
    let result2: Int = 3 |> double(_) |> double(_) |> add_one(_)
    print(result2)
    return ()
end func
""",
        id="pipeline",
    ),
    pytest.param(
        # --- Multiple functions calling each other ---
        """\
func square(x: Int) -> Int
    example 3 -> 9
    return x * x
end func

func sum_squares(n: Int) -> Int
    example 1 -> 1
    example 3 -> 14
    var total: Int = 0
    var i: Int = 1
    while i <= n do
        total = total + square(i)
        i = i + 1
    end while
    return total
end func

func main() -> Unit
    print(sum_squares(1))
    print(sum_squares(3))
    print(sum_squares(5))
    return ()
end func
""",
        id="multi_function",
    ),
    pytest.param(
        # --- Boolean logic ---
        """\
func main() -> Unit
    if true and true then
        print(1)
    else
        print(0)
    end if
    if true and false then
        print(1)
    else
        print(0)
    end if
    if false or true then
        print(1)
    else
        print(0)
    end if
    if not false then
        print(1)
    else
        print(0)
    end if
    return ()
end func
""",
        id="boolean_logic",
    ),
    pytest.param(
        # --- Nested if/else ---
        """\
func classify(n: Int) -> Int
    example -5 -> -1
    example 0 -> 0
    example 42 -> 1
    if n < 0 then
        return -1
    else
        if n == 0 then
            return 0
        else
            return 1
        end if
    end if
end func

func main() -> Unit
    print(classify(-5))
    print(classify(0))
    print(classify(42))
    return ()
end func
""",
        id="nested_if_else",
    ),
    pytest.param(
        # --- List index access ---
        """\
func main() -> Unit
    let xs: List[Int] = [10, 20, 30, 40, 50]
    print(xs[0])
    print(xs[2])
    print(xs[4])
    print(xs[0] + xs[4])
    return ()
end func
""",
        id="list_index_access",
    ),
    pytest.param(
        # --- format() string template substitution ---
        """\
func main() -> Unit
    let result: String = format("Hello {} and {}!", ["world", "friends"])
    if result == "Hello world and friends!" then
        print(1)
    else
        print(0)
    end if
    let single: String = format("{}", ["test"])
    if single == "test" then
        print(1)
    else
        print(0)
    end if
    let empty: String = format("no placeholders", [])
    if empty == "no placeholders" then
        print(1)
    else
        print(0)
    end if
    let two: String = format("{} plus {}", ["3", "4"])
    if two == "3 plus 4" then
        print(1)
    else
        print(0)
    end if
    return ()
end func
""",
        id="format_string",
    ),
    pytest.param(
        # --- csv_parse basic ---
        """\
func main() -> Unit
    let rows: List[List[String]] = csv_parse("a,b,c\\n1,2,3\\n4,5,6")
    print(length(rows))
    let first_row: List[String] = head(rows)
    print(length(first_row))
    let single: List[List[String]] = csv_parse("hello")
    print(length(single))
    let empty_field: List[List[String]] = csv_parse("a,,b")
    print(length(head(empty_field)))
    return ()
end func
""",
        id="csv_parse_basic",
    ),
    pytest.param(
        # --- to_string conversions (print lengths to avoid quote diffs) ---
        """\
func main() -> Unit
    print(length(to_string(42)))
    print(length(to_string(0)))
    print(length(to_string(true)))
    print(length(to_string(false)))
    print(length(to_string(0 - 123)))
    print(length(to_string(999)))
    return ()
end func
""",
        id="to_string_conversions",
    ),
    pytest.param(
        # --- char_code and from_char_code roundtrip ---
        """\
func main() -> Unit
    print(char_code("A"))
    print(char_code("a"))
    print(char_code("0"))
    print(char_code(" "))
    print(length(from_char_code(65)))
    print(length(from_char_code(122)))
    print(char_code(from_char_code(72)))
    return ()
end func
""",
        id="char_code_roundtrip",
    ),
    pytest.param(
        # --- parse_int and parse_float ---
        """\
func main() -> Unit
    match parse_int("42") with
        | Some(n) -> print(n)
        | None -> print(-999)
    end match
    match parse_int("-7") with
        | Some(n) -> print(n)
        | None -> print(-999)
    end match
    match parse_int("abc") with
        | Some(n) -> print(n)
        | None -> print(-999)
    end match
    match parse_int("0") with
        | Some(n) -> print(n)
        | None -> print(-999)
    end match
    match parse_float("3.14") with
        | Some(f) -> print(f)
        | None -> print(-999)
    end match
    match parse_float("bad") with
        | Some(f) -> print(f)
        | None -> print(-999)
    end match
    return ()
end func
""",
        id="parse_int_parse_float",
    ),
    pytest.param(
        # --- float_to_int and int_to_float ---
        """\
func main() -> Unit
    print(float_to_int(3.7))
    print(float_to_int(0.0 - 2.9))
    print(float_to_int(0.0))
    print(floor(int_to_float(5) + 0.5))
    print(floor(int_to_float(0 - 3) * 2.0))
    return ()
end func
""",
        id="float_int_conversion",
    ),
    pytest.param(
        # --- math rounding: floor, ceil, round ---
        """\
func main() -> Unit
    print(floor(3.7))
    print(floor(0.0 - 2.3))
    print(floor(5.0))
    print(ceil(3.2))
    print(ceil(0.0 - 2.9))
    print(ceil(5.0))
    print(round(3.5))
    print(round(3.4))
    print(round(0.0 - 2.5))
    return ()
end func
""",
        id="math_rounding",
    ),
    pytest.param(
        # --- abs, max, clamp ---
        """\
func main() -> Unit
    print(abs(5))
    print(abs(0 - 7))
    print(abs(0))
    print(max(3, 8))
    print(max(0 - 2, 0 - 5))
    print(clamp(value: 15, min: 0, max: 10))
    print(clamp(value: 0 - 5, min: 0, max: 10))
    print(clamp(value: 5, min: 0, max: 10))
    return ()
end func
""",
        id="math_abs_max_clamp",
    ),
    pytest.param(
        # --- clamp with inverted bounds (min > max) ---
        # All backends compute max(min, min(max, value)) without guarding the
        # ordering of the bounds, so the result must agree even when min > max.
        """\
func main() -> Unit
    print(clamp(value: 5, min: 10, max: 0))
    print(clamp(value: 15, min: 10, max: 0))
    print(clamp(value: 0 - 5, min: 10, max: 0))
    return ()
end func
""",
        id="clamp_inverted_bounds",
    ),
    pytest.param(
        # --- Array operations ---
        """\
func main() -> Unit
    let arr: Array[Int] = array_new(5, 0)
    print(array_length(arr))
    print(array_get(arr, 0))
    array_set(array: arr, index: 2, value: 42)
    print(array_get(arr, 2))
    let from_list: Array[Int] = array_from_list([10, 20, 30])
    print(array_get(from_list, 1))
    let as_list: List[Int] = array_to_list(from_list)
    print(head(as_list))
    print(length(as_list))
    array_fill(arr, 7)
    print(array_get(arr, 0))
    print(array_get(arr, 4))
    let copied: Array[Int] = array_copy(from_list)
    array_set(array: copied, index: 0, value: 99)
    print(array_get(from_list, 0))
    print(array_get(copied, 0))
    return ()
end func
""",
        id="array_operations",
    ),
    pytest.param(
        # --- Set operations ---
        """\
func main() -> Unit
    let s: Set[Int] = set_new()
    set_add(s, 1)
    set_add(s, 2)
    set_add(s, 3)
    set_add(s, 2)
    print(set_size(s))
    if set_contains(s, 2) then
        print(1)
    else
        print(0)
    end if
    if set_contains(s, 5) then
        print(1)
    else
        print(0)
    end if
    set_remove(s, 2)
    print(set_size(s))
    if set_contains(s, 2) then
        print(1)
    else
        print(0)
    end if
    return ()
end func
""",
        id="set_operations",
    ),
    pytest.param(
        # --- MutableMap operations ---
        """\
func main() -> Unit
    let m: MutableMap[String, Int] = mutable_map_new()
    mutable_map_set(map: m, key: "a", value: 10)
    mutable_map_set(map: m, key: "b", value: 20)
    mutable_map_set(map: m, key: "c", value: 30)
    print(mutable_map_size(m))
    match mutable_map_get(m, "b") with
        | Some(v) -> print(v)
        | None -> print(-1)
    end match
    if mutable_map_contains(m, "a") then
        print(1)
    else
        print(0)
    end if
    if mutable_map_contains(m, "z") then
        print(1)
    else
        print(0)
    end if
    mutable_map_delete(m, "b")
    print(mutable_map_size(m))
    match mutable_map_get(m, "b") with
        | Some(v) -> print(v)
        | None -> print(-1)
    end match
    return ()
end func
""",
        id="mutable_map_operations",
    ),
    pytest.param(
        # --- Vec operations ---
        """\
func main() -> Unit
    let v: Vec[Int] = vec_new()
    vec_push(v, 10)
    vec_push(v, 20)
    vec_push(v, 30)
    print(vec_length(v))
    print(vec_get(v, 0))
    print(vec_get(v, 2))
    vec_set(vec: v, index: 1, value: 99)
    print(vec_get(v, 1))
    match vec_pop(v) with
        | Some(val) -> print(val)
        | None -> print(-1)
    end match
    print(vec_length(v))
    let as_list: List[Int] = vec_to_list(v)
    print(length(as_list))
    print(head(as_list))
    return ()
end func
""",
        id="vec_operations",
    ),
    pytest.param(
        # --- String padding and search ---
        """\
func main() -> Unit
    let padded_left: String = string_pad_left(text: "hi", width: 5, fill_char: ".")
    print(length(padded_left))
    let padded_right: String = string_pad_right(text: "hi", width: 5, fill_char: ".")
    print(length(padded_right))
    print(string_index_of("hello world", "world"))
    print(string_index_of("hello world", "xyz"))
    print(string_last_index_of("abcabc", "bc"))
    print(string_last_index_of("abcabc", "xyz"))
    let sub: String = substring(text: "hello world", start: 0, stop: 5)
    print(length(sub))
    return ()
end func
""",
        id="string_pad_search",
    ),
    pytest.param(
        # --- list_take and list_drop ---
        """\
func main() -> Unit
    let xs: List[Int] = [1, 2, 3, 4, 5]
    let taken: List[Int] = list_take(xs, 3)
    print(length(taken))
    print(head(taken))
    let dropped: List[Int] = list_drop(xs, 2)
    print(length(dropped))
    print(head(dropped))
    let take_zero: List[Int] = list_take(xs, 0)
    print(length(take_zero))
    let drop_all: List[Int] = list_drop(xs, 5)
    print(length(drop_all))
    return ()
end func
""",
        id="list_take_drop",
    ),
    pytest.param(
        # --- list_find, list_find_index, list_any ---
        """\
func main() -> Unit
    let xs: List[Int] = [1, 4, 9, 16, 25]
    match list_find(xs, fn(x: Int) -> x > 10) with
        | Some(v) -> print(v)
        | None -> print(-1)
    end match
    match list_find(xs, fn(x: Int) -> x > 100) with
        | Some(v) -> print(v)
        | None -> print(-1)
    end match
    match list_find_index(xs, fn(x: Int) -> x > 10) with
        | Some(i) -> print(i)
        | None -> print(-1)
    end match
    match list_find_index(xs, fn(x: Int) -> x > 100) with
        | Some(i) -> print(i)
        | None -> print(-1)
    end match
    if list_any(xs, fn(x: Int) -> x == 9) then
        print(1)
    else
        print(0)
    end if
    if list_any(xs, fn(x: Int) -> x == 7) then
        print(1)
    else
        print(0)
    end if
    return ()
end func
""",
        id="list_find_any",
    ),
    pytest.param(
        # --- list_flatten and list_chunk ---
        """\
func main() -> Unit
    let nested: List[List[Int]] = [[1, 2], [3], [4, 5, 6]]
    let flat: List[Int] = list_flatten(nested)
    print(length(flat))
    print(head(flat))
    let xs: List[Int] = [1, 2, 3, 4, 5, 6, 7]
    let chunks: List[List[Int]] = list_chunk(xs, 3)
    print(length(chunks))
    print(length(head(chunks)))
    let last_chunk: List[List[Int]] = list_chunk([1, 2, 3, 4, 5], 2)
    print(length(last_chunk))
    return ()
end func
""",
        id="list_flatten_chunk",
    ),
    pytest.param(
        # --- enumerate and zip ---
        """\
func main() -> Unit
    let xs: List[Int] = [10, 20, 30]
    let indexed: List[(Int, Int)] = enumerate(xs)
    print(length(indexed))
    let ys: List[Int] = [100, 200, 300, 400]
    let zipped: List[(Int, Int)] = zip(xs, ys)
    print(length(zipped))
    let short: List[(Int, Int)] = zip([1], [2, 3, 4])
    print(length(short))
    return ()
end func
""",
        id="enumerate_zip",
    ),
    pytest.param(
        # --- option_map, option_and_then, result_map, result_unwrap_or ---
        """\
func main() -> Unit
    let some_val: Option[Int] = Some(5)
    let none_val: Option[Int] = None
    match option_map(some_val, fn(x: Int) -> x * 2) with
        | Some(v) -> print(v)
        | None -> print(-1)
    end match
    match option_map(none_val, fn(x: Int) -> x * 2) with
        | Some(v) -> print(v)
        | None -> print(-1)
    end match
    match option_and_then(some_val, fn(x: Int) -> Some(x + 10)) with
        | Some(v) -> print(v)
        | None -> print(-1)
    end match
    match option_and_then(some_val, fn(x: Int) -> None) with
        | Some(v) -> print(v)
        | None -> print(-1)
    end match
    let ok_val: Result[Int, Int] = Ok(7)
    let err_val: Result[Int, Int] = Err(3)
    match result_map(ok_val, fn(x: Int) -> x + 1) with
        | Ok(v) -> print(v)
        | Err(e) -> print(0 - e)
    end match
    match result_map(err_val, fn(x: Int) -> x + 1) with
        | Ok(v) -> print(v)
        | Err(e) -> print(0 - e)
    end match
    print(result_unwrap_or(ok_val, 0))
    print(result_unwrap_or(err_val, 0))
    return ()
end func
""",
        id="option_result_combinators",
    ),
    pytest.param(
        """
func main() -> Unit
    print(bit_or(5, 3))
    print(bit_or(255, 256))
    print(bit_or(0, 0))
    let large: Int = 1 << 40
    print(bit_or(large, 1))
    print(bit_or(large, large))
    return ()
end func
""",
        id="bitwise_or_large_values",
    ),
    # -----------------------------------------------------------------------
    # #657 — match-expression lowering parity.  The code fixes for
    # F-0004/F-0005/F-0008 landed in #668 (invalid Python syntax for
    # non-exhaustive match expressions, silent JS divergence, and
    # unescaped string-literal patterns on Python); these programs are
    # the remaining parity-test deliverable asked for by the issue.
    # Each exercises a case the old code diverged on.
    # -----------------------------------------------------------------------
    pytest.param(
        # --- Match expression over Option[T] (constructor patterns) ---
        # Before the fix the JS backend silently fell through to `null`
        # and the Python backend emitted a syntactically invalid fallback
        # when the compiler couldn't prove exhaustiveness.  The arms here
        # ARE exhaustive (Some / None), and all three backends must agree.
        """\
func describe(opt: Option[Int]) -> Int
    example Some(5) -> 5
    example None -> 0
    return match opt with
        | Some(value) -> value
        | None -> 0
    end match
end func

func main() -> Unit
    print(describe(Some(42)))
    print(describe(Some(0)))
    print(describe(None))
    return ()
end func
""",
        id="match_expr_option_constructors",
    ),
    pytest.param(
        # --- Match expression over Result[T, E] ---
        # Inner Ok/Err pattern destructuring across backends.
        """\
func unwrap_or_neg(r: Result[Int, Int]) -> Int
    example Ok(7) -> 7
    example Err(3) -> -3
    return match r with
        | Ok(v) -> v
        | Err(e) -> 0 - e
    end match
end func

func main() -> Unit
    print(unwrap_or_neg(Ok(100)))
    print(unwrap_or_neg(Err(5)))
    print(unwrap_or_neg(Ok(0)))
    return ()
end func
""",
        id="match_expr_result_constructors",
    ),
    pytest.param(
        # --- Match expression with STRING literal patterns ---
        # F-0008: Python backend did not escape string patterns, so a
        # pattern containing a quote or backslash produced invalid
        # generated source.  JS already escaped correctly.  The patterns
        # here contain a double-quote and a backslash, which previously
        # broke Python codegen and would diverge from JS behaviour.
        """\
func classify(tag: String) -> Int
    example "ok" -> 1
    example "err" -> 2
    example "has\\"quote" -> 3
    example "has\\\\slash" -> 4
    example "other" -> 0
    return match tag with
        | "ok" -> 1
        | "err" -> 2
        | "has\\"quote" -> 3
        | "has\\\\slash" -> 4
        | _ -> 0
    end match
end func

func main() -> Unit
    print(classify("ok"))
    print(classify("err"))
    print(classify("has\\"quote"))
    print(classify("has\\\\slash"))
    print(classify("other"))
    return ()
end func
""",
        id="match_expr_string_literal_patterns",
    ),
    pytest.param(
        # --- Match expression over Int with literal + wildcard patterns ---
        # Exhaustiveness is proven by the ``_`` arm; both backends must
        # emit a well-formed expression and never hit a silent fallback.
        """\
func classify_num(n: Int) -> Int
    example 0 -> 0
    example 1 -> 10
    example 99 -> -1
    return match n with
        | 0 -> 0
        | 1 -> 10
        | 2 -> 20
        | 3 -> 30
        | _ -> -1
    end match
end func

func main() -> Unit
    print(classify_num(0))
    print(classify_num(1))
    print(classify_num(2))
    print(classify_num(3))
    print(classify_num(99))
    return ()
end func
""",
        id="match_expr_int_literals_wildcard",
    ),
    pytest.param(
        # --- Match expression as RHS of a ``let`` ---
        # Expression-position lowering is the specific path this issue
        # was about (statement-position match always worked).  Ensures
        # the value actually flows into the binding on all three
        # backends.
        """\
func main() -> Unit
    let opt: Option[Int] = Some(21)
    let doubled: Int = match opt with
        | Some(v) -> v + v
        | None -> 0
    end match
    print(doubled)

    let none_opt: Option[Int] = None
    let fallback: Int = match none_opt with
        | Some(v) -> v
        | None -> -1
    end match
    print(fallback)
    return ()
end func
""",
        id="match_expr_as_let_rhs",
    ),
    pytest.param(
        # --- Nested match expressions ---
        # Inner match inside an arm RHS must compose correctly.
        """\
func main() -> Unit
    let outer: Option[Option[Int]] = Some(Some(7))
    let value: Int = match outer with
        | Some(inner) -> match inner with
            | Some(x) -> x
            | None -> -1
        end match
        | None -> -2
    end match
    print(value)

    let missing_inner: Option[Option[Int]] = Some(None)
    let value2: Int = match missing_inner with
        | Some(inner) -> match inner with
            | Some(x) -> x
            | None -> -1
        end match
        | None -> -2
    end match
    print(value2)

    let missing_outer: Option[Option[Int]] = None
    let value3: Int = match missing_outer with
        | Some(inner) -> match inner with
            | Some(x) -> x
            | None -> -1
        end match
        | None -> -2
    end match
    print(value3)
    return ()
end func
""",
        id="match_expr_nested",
    ),
    pytest.param(
        # --- Match expression with guards and fallthrough ---
        # Guarded arms use extra lowering on both backends because the
        # guard must see constructor bindings while still allowing
        # fallthrough to later arms when the guard fails.  This path is
        # distinct from the unguarded expression cases above and should
        # stay in cross-backend parity coverage for #657.
        """\
type Num = Num(value: Int)

func classify(n: Num) -> Int
    example Num(11) -> 2
    example Num(5) -> 1
    example Num(0) -> 0
    example Num(-3) -> 0
    return match n with
        | Num(v) when v > 10 -> 2
        | Num(v) when v > 0 -> 1
        | Num(_) -> 0
    end match
end func

func main() -> Unit
    print(classify(Num(11)))
    print(classify(Num(5)))
    print(classify(Num(0)))
    print(classify(Num(-3)))
    return ()
end func
""",
        id="match_expr_guard_fallthrough",
    ),
    pytest.param(
        # --- Match expression over a user-defined variant type ---
        # Multi-variant ADT (beyond Option/Result) exercises the generic
        # constructor-pattern lowering on both backends.
        """\
type Shape = Circle(radius: Int) | Square(side: Int) | Triangle(base: Int, height: Int)

func area_estimate(s: Shape) -> Int
    example Circle(3) -> 27
    example Square(4) -> 16
    example Triangle(4, 5) -> 10
    return match s with
        | Circle(r) -> r * r * 3
        | Square(side) -> side * side
        | Triangle(b, h) -> (b * h) / 2
    end match
end func

func main() -> Unit
    print(area_estimate(Circle(3)))
    print(area_estimate(Circle(10)))
    print(area_estimate(Square(4)))
    print(area_estimate(Triangle(4, 5)))
    return ()
end func
""",
        id="match_expr_user_variant",
    ),
    pytest.param(
        # --- Float printing keeps the fractional part across backends ---
        # Floats and JS numbers share one runtime representation, so whole-valued
        # floats must be formatted via static type info, not a runtime check.
        """\
type Box = Box(value: Float)
type FloatList = Cons(head: Float, tail: FloatList) | Nil

func main() -> Unit
    print(3.0)
    print(2.0 + 2.0)
    print(0.0 - 5.0)
    print([1.0, 2.5, 3.0])
    print((1.0, 2))
    print(Some(4.0))
    print(Box(7.0))
    print(Cons(1.0, Cons(2.0, Nil)))
    print(1.0 / 10000000.0)
    print(10000000000000000.0)
    print(1000000000000000000000.0)
    return ()
end func
""",
        id="float_print_whole_values",
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackendParity:
    """Verify interpreter, compiled Python, and compiled JS produce
    identical output for each test program."""

    @pytest.mark.parametrize("source", PARITY_PROGRAMS)
    def test_interpreter_vs_compiled_python(self, source: str):
        interp_out = _interpreter_output(source)
        py_out = _compiled_python_output(source)
        assert interp_out == py_out, (
            f"Interpreter vs compiled Python mismatch:\n"
            f"--- interpreter ---\n{interp_out!r}\n"
            f"--- compiled py ---\n{py_out!r}"
        )

    @pytest.mark.parametrize("source", PARITY_PROGRAMS)
    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_interpreter_vs_compiled_js(self, source: str):
        interp_out = _interpreter_output(source)
        js_out = _compiled_js_output(source)
        assert interp_out == js_out, (
            f"Interpreter vs compiled JS mismatch:\n"
            f"--- interpreter ---\n{interp_out!r}\n"
            f"--- compiled js ---\n{js_out!r}"
        )

    @pytest.mark.parametrize("source", PARITY_PROGRAMS)
    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_compiled_python_vs_compiled_js(self, source: str):
        py_out = _compiled_python_output(source)
        js_out = _compiled_js_output(source)
        assert py_out == js_out, (
            f"Compiled Python vs compiled JS mismatch:\n"
            f"--- compiled py ---\n{py_out!r}\n"
            f"--- compiled js ---\n{js_out!r}"
        )

    def test_project_compile_helpers_typecheck_before_codegen(self):
        modules = {
            "Utils": (
                'func helper() -> Int\n  example () -> 0\n  return "oops"\nend func\n'
            )
        }
        main_source = (
            "import Utils\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "  return helper()\n"
            "end func\n"
        )

        with pytest.raises(GenoTypeError, match="expected Int, got String"):
            _compiled_python_project_output(main_source, modules)

        with pytest.raises(GenoTypeError, match="expected Int, got String"):
            _compiled_js_project_output(main_source, modules)

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_qualified_module_named_args_are_reordered_by_compilers(self):
        modules = {
            "Lib": (
                "func encode(a: Int, b: Int, c: Int) -> Int\n"
                "  example 1, 2, 3 -> 123\n"
                "  return a * 100 + b * 10 + c\n"
                "end func\n"
            )
        }
        main_source = (
            "import Lib\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "  return Lib.encode(c: 3, a: 1, b: 2)\n"
            "end func\n"
        )

        assert _compiled_python_project_output(main_source, modules).strip() == "123"
        assert _compiled_js_project_output(main_source, modules).strip() == "123"

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_js_project_duplicate_function_names_keep_module_namespaces(self):
        modules = {
            "A": "func value() -> Int\n  example () -> 1\n  return 1\nend func\n",
            "B": "func value() -> Int\n  example () -> 2\n  return 2\nend func\n",
        }
        main_source = (
            "import A\n"
            "import B\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "  return A.value() * 10 + B.value()\n"
            "end func\n"
        )

        assert _compiled_python_project_output(main_source, modules).strip() == "12"
        assert _compiled_js_project_output(main_source, modules).strip() == "12"

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_math_round_half_values_match_source_round_contract(self):
        source = """
        func main() -> Unit
            print(math_round(2.5))
            print(math_round(3.4))
            print(math_round(0.0 - 2.5))
            print(math_round(0.0 - 0.5))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="math_round",
            context=source,
            expected="3\n3\n-2\n0\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_stdlib_math_round_matches_source_round_contract(self):
        main_source = (
            "import Math\n"
            '@untested("entry point")\n'
            "func main() -> Unit\n"
            "  print(Math.round(2.5))\n"
            "  print(Math.round(0.0 - 0.5))\n"
            "  return ()\n"
            "end func\n"
        )

        expected = "3\n0\n"
        assert _compiled_python_project_output(main_source, {}) == expected
        assert _compiled_js_project_output(main_source, {}) == expected

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_random_int_rejects_inverted_range_in_all_backends(self):
        source = """
        func main() -> Int
            return random_int(min: 10, max: 1)
        end func
        """

        result = run(source, config=RunConfig(timeout=10.0, capabilities={"random"}))
        assert not result.ok
        assert any("empty range" in d.message for d in result.diagnostics)

        with pytest.raises(RuntimeError, match="empty range"):
            _compiled_python_output(source, capabilities={"random"})

        with pytest.raises(
            RuntimeError,
            match="random_int: lower bound must be <= upper bound",
        ):
            _compiled_js_output(source, capabilities={"random"})

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    @pytest.mark.parametrize("chunk_size", [0, -1])
    def test_list_chunk_rejects_non_positive_size_in_all_backends(self, chunk_size):
        source = f"""
        func main() -> Unit
            let chunks: List[List[Int]] = list_chunk([1, 2, 3], {chunk_size})
            print(length(chunks))
            return ()
        end func
        """

        result = run(source, config=RunConfig(timeout=10.0, capabilities={"print"}))
        assert not result.ok
        assert any(
            "list_chunk: chunk size must be positive" in diagnostic.message
            for diagnostic in result.diagnostics
        )

        with pytest.raises(
            RuntimeError, match="list_chunk: chunk size must be positive"
        ):
            _compiled_python_output(source)

        with pytest.raises(
            RuntimeError, match="list_chunk: chunk size must be positive"
        ):
            _compiled_js_output(source)

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_list_flatten_handles_large_sublist_across_backends(self):
        source = """
        func main() -> Unit
            let xs: List[Int] = range(0, 200000)
            let nested: List[List[Int]] = [xs]
            let flat: List[Int] = list_flatten(nested)
            print(length(flat))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="large list_flatten",
            context=source,
            expected="200000\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_stdlib_math_random_int_rejects_inverted_range_in_js(self):
        main_source = (
            "import Math\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "  return Math.random_int(10, 1)\n"
            "end func\n"
        )

        with pytest.raises(RuntimeError, match="empty range"):
            _compiled_python_project_output(main_source, {}, capabilities={"random"})

        with pytest.raises(
            RuntimeError,
            match="math_random_int: lower bound must be <= upper bound",
        ):
            _compiled_js_project_output(main_source, {}, capabilities={"random"})

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_path_join_uses_posix_join_rules_across_backends(self):
        source = """
        func main() -> Unit
            print(path_join("/base", "/abs") == "/abs")
            print(path_join("", "child") == "child")
            print(path_join("/base", "") == "/base/")
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="path_join POSIX rules",
            context=source,
            expected="true\ntrue\ntrue\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_stdlib_path_join_uses_posix_join_rules_in_compilers(self):
        main_source = """
        import Path

        @untested("entry point")
        func main() -> Unit
            print(Path.join("/base", "/abs") == "/abs")
            print(Path.join("", "child") == "child")
            print(Path.join("/base", "") == "/base/")
            return ()
        end func
        """

        expected = "true\ntrue\ntrue\n"
        assert _compiled_python_project_output(main_source, {}) == expected
        assert _compiled_js_project_output(main_source, {}) == expected

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_clock_parse_rejects_non_padded_fields_across_backends(self):
        source = """
        func main() -> Unit
            match clock_parse("1970-1-01", "%Y-%m-%d") with
                | Some(ts) -> print(1)
                | None -> print(0)
            end match
            match datetime_parse("1970-1-01", "%Y-%m-%d") with
                | Some(ts) -> print(1)
                | None -> print(0)
            end match
            return ()
        end func
        """

        result = run(
            source,
            config=RunConfig(timeout=10.0, capabilities={"print", "clock"}),
        )
        assert result.ok, (
            f"Interpreter failed: {[d.message for d in result.diagnostics]}"
        )

        _assert_expected_backend_outputs(
            label="strict clock_parse fields",
            context=source,
            expected="0\n0\n",
            interp_out=cast(str, result.output),
            py_out=_compiled_python_output(source, capabilities={"print", "clock"}),
            js_out=_compiled_js_output(source, capabilities={"print", "clock"}),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_clock_parse_low_years_match_across_backends(self):
        source = """
        func main() -> Unit
            match datetime_parse("0001-01-01", "%Y-%m-%d") with
                | Some(ts) -> print(ts)
                | None -> print(0)
            end match
            match clock_parse("0000-01-01", "%Y-%m-%d") with
                | Some(ts) -> print(1)
                | None -> print(0)
            end match
            return ()
        end func
        """

        result = run(
            source,
            config=RunConfig(timeout=10.0, capabilities={"print", "clock"}),
        )
        assert result.ok, (
            f"Interpreter failed: {[d.message for d in result.diagnostics]}"
        )

        _assert_expected_backend_outputs(
            label="clock_parse low years",
            context=source,
            expected="-62135596800\n0\n",
            interp_out=cast(str, result.output),
            py_out=_compiled_python_output(source, capabilities={"print", "clock"}),
            js_out=_compiled_js_output(source, capabilities={"print", "clock"}),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_parse_int_decimal_contract_matches_across_backends(self):
        source = """
        func main() -> Unit
            match parse_int(" 42 ") with
                | Some(n) -> print(n)
                | None -> print(-999)
            end match
            match parse_int("+42") with
                | Some(n) -> print(n)
                | None -> print(-999)
            end match
            match parse_int("1_000") with
                | Some(n) -> print(n)
                | None -> print(-999)
            end match
            match parse_int("-7") with
                | Some(n) -> print(n)
                | None -> print(-999)
            end match
            print(is_numeric_string(" 42 "))
            print(is_numeric_string("+42"))
            print(is_numeric_string("1_000"))
            print(is_numeric_string("-7"))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="parse_int decimal contract",
            context=source,
            expected="42\n-999\n-999\n-7\ntrue\nfalse\nfalse\ntrue\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_parse_int_rejects_unsafe_range_across_backends(self):
        source = """
        func main() -> Unit
            match parse_int("9007199254740993") with
                | Some(_) -> print(0)
                | None -> print(1)
            end match
            match parse_int("-9007199254740993") with
                | Some(_) -> print(0)
                | None -> print(1)
            end match
            match parse_int("9007199254740991") with
                | Some(_) -> print(1)
                | None -> print(0)
            end match
            match parse_int("-9007199254740991") with
                | Some(_) -> print(1)
                | None -> print(0)
            end match
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="parse_int safe range",
            context=source,
            expected="1\n1\n1\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_is_permutation_uses_equality_across_backends(self):
        source = """
        func main() -> Unit
            print(is_permutation([1, 2, 1], [2, 1, 1]))
            print(is_permutation([Some(1), None], [None, Some(1)]))
            print(is_permutation([Some(1), Some(1)], [Some(1), None]))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="is_permutation equality",
            context=source,
            expected="true\ntrue\nfalse\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_unicode_string_operations_count_code_points_across_backends(self):
        source = """
        func main() -> Unit
            let smile: String = from_char_code(128512)
            let s: String = smile + "xy" + smile
            print(length(smile))
            print(length(s))
            print(char_code(s[0]))
            print(char_code(s[-1]))
            print(char_code(string_char_at(text: s, index: 0)))
            print(length(substring(text: s, start: 0, stop: 1)))
            print(char_code(substring(text: s, start: 0, stop: 1)))
            print(length(string_substring(text: s, start: 0, stop: 1)))
            print(char_code(string_substring(text: s, start: 0, stop: 1)))
            print(string_index_of(text: s, substring: "x"))
            print(string_last_index_of(text: s, substring: smile))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="unicode string code point operations",
            context=source,
            expected="1\n4\n128512\n128512\n128512\n1\n128512\n1\n128512\n1\n3\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_ordered_comparisons_use_geno_ordering_across_backends(self):
        source = """
        func main() -> Unit
            let smile: String = from_char_code(128512)
            let private_use: String = from_char_code(57344)
            print(smile > private_use)
            print(smile < private_use)
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="ordered comparison Geno semantics",
            context=source,
            expected="true\nfalse\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_sort_by_orders_astral_string_keys_across_backends(self):
        source = """
        func key(x: Int) -> String
            example 1 -> from_char_code(57344)
            if x == 1 then
                return from_char_code(57344)
            end if
            return from_char_code(128512)
        end func

        func main() -> Unit
            let sorted: List[Int] = sort_by([2, 1], key)
            print(sorted[0])
            print(sorted[1])
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="sort_by astral string key ordering",
            context=source,
            expected="1\n2\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_sort_strings_orders_astral_strings_across_backends(self):
        source = """
        func main() -> Unit
            let smile: String = from_char_code(128512)
            let private_use: String = from_char_code(57344)
            let sorted: List[String] = sort_strings([smile, private_use])
            print(char_code(sorted[0]))
            print(char_code(sorted[1]))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="sort_strings astral string ordering",
            context=source,
            expected="57344\n128512\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_trim_uses_python_whitespace_set_across_backends(self):
        source = """
        func main() -> Unit
            let nel: String = from_char_code(133)
            let bom: String = from_char_code(65279)
            print(length(trim(nel + "x" + nel)))
            print(length(trim(bom + "x" + bom)))
            print(length(string_trim_start(nel + "x" + nel)))
            print(length(string_trim_end(nel + "x" + nel)))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="Python whitespace trim set",
            context=source,
            expected="1\n3\n2\n2\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_stdlib_string_trim_uses_python_whitespace_set_in_compilers(self):
        main_source = """
        import String

        @untested("entry point")
        func main() -> Unit
            let nel: String = from_char_code(133)
            let bom: String = from_char_code(65279)
            print(length(String.trim(nel + "x" + nel)))
            print(length(String.trim(bom + "x" + bom)))
            print(length(String.trim_start(nel + "x" + nel)))
            print(length(String.trim_end(nel + "x" + nel)))
            return ()
        end func
        """

        expected = "1\n3\n2\n2\n"
        assert _compiled_python_project_output(main_source, {}) == expected
        assert _compiled_js_project_output(main_source, {}) == expected

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_from_char_code_survives_stdlib_string_import_in_compilers(self):
        main_source = """
        import String

        @untested("entry point")
        func main() -> Unit
            print(length(from_char_code(133)))
            return ()
        end func
        """

        expected = "1\n"
        assert _compiled_python_project_output(main_source, {}) == expected
        assert _compiled_js_project_output(main_source, {}) == expected

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_string_case_preserves_python_unicode_version_across_backends(self):
        source = """
        func main() -> Unit
            let garay_upper_a: String = from_char_code(68944)
            let garay_lower_a: String = from_char_code(68976)
            let greek_word: String = from_char_code(927) + from_char_code(931)
            print(char_code(to_upper(garay_lower_a)))
            print(char_code(to_lower(garay_upper_a)))
            print(char_code(string_to_upper(text: garay_lower_a)))
            print(char_code(string_to_lower(text: garay_upper_a)))
            print(char_code(to_lower(greek_word)[1]))
            print(char_code(string_to_lower(text: greek_word)[1]))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="Python Unicode case table",
            context=source,
            expected="68976\n68944\n68976\n68944\n962\n962\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_stdlib_string_case_preserves_python_unicode_version_in_compilers(self):
        main_source = """
        import String

        @untested("entry point")
        func main() -> Unit
            let garay_upper_a: String = from_char_code(68944)
            let garay_lower_a: String = from_char_code(68976)
            let greek_word: String = from_char_code(927) + from_char_code(931)
            print(char_code(String.to_upper(garay_lower_a)))
            print(char_code(String.to_lower(garay_upper_a)))
            print(char_code(String.to_lower(greek_word)[1]))
            return ()
        end func
        """

        expected = "68976\n68944\n962\n"
        assert _compiled_python_project_output(main_source, {}) == expected
        assert _compiled_js_project_output(main_source, {}) == expected

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_replace_empty_search_uses_python_semantics_across_backends(self):
        source = """
        func main() -> Unit
            let smile: String = from_char_code(128512)
            let direct: String = replace(text: "ab", old: "", new: "-")
            let wrapped: String = string_replace(text: "ab", old: "", new: "-")
            let empty: String = replace(text: "", old: "", new: "-")
            let astral: String = replace(text: smile + "x", old: "", new: "|")
            print(length(direct))
            print(char_code(direct[0]))
            print(char_code(direct[3]))
            print(char_code(direct[4]))
            print(length(wrapped))
            print(length(empty))
            print(length(astral))
            print(char_code(astral[1]))
            print(char_code(astral[2]))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="Python empty-string replace",
            context=source,
            expected="5\n45\n98\n45\n5\n1\n5\n128512\n124\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_stdlib_string_replace_empty_search_uses_python_semantics_in_compilers(
        self,
    ):
        main_source = """
        import String

        @untested("entry point")
        func main() -> Unit
            let direct: String = String.replace(text: "ab", old: "", new: "-")
            let empty: String = String.replace(text: "", old: "", new: "-")
            print(length(direct))
            print(char_code(direct[0]))
            print(char_code(direct[4]))
            print(length(empty))
            return ()
        end func
        """

        expected = "5\n45\n45\n1\n"
        assert _compiled_python_project_output(main_source, {}) == expected
        assert _compiled_js_project_output(main_source, {}) == expected

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_split_once_empty_delimiter_rejected_across_backends(self):
        source = """
        func main() -> Unit
            print(is_some(split_once("abc", "")))
            return ()
        end func
        """

        with pytest.raises(
            AssertionError, match="split_once: delimiter cannot be empty"
        ):
            _interpreter_output(source)
        with pytest.raises(RuntimeError, match="split_once: delimiter cannot be empty"):
            _compiled_python_output(source)
        with pytest.raises(RuntimeError, match="split_once: delimiter cannot be empty"):
            _compiled_js_output(source)

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_string_split_once_empty_delimiter_rejected_across_backends(self):
        source = """
        func main() -> Unit
            print(is_some(string_split_once(text: "abc", delimiter: "")))
            return ()
        end func
        """

        with pytest.raises(
            AssertionError, match="string_split_once: delimiter cannot be empty"
        ):
            _interpreter_output(source)
        with pytest.raises(
            RuntimeError, match="string_split_once: delimiter cannot be empty"
        ):
            _compiled_python_output(source)
        with pytest.raises(
            RuntimeError, match="string_split_once: delimiter cannot be empty"
        ):
            _compiled_js_output(source)

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_stdlib_string_split_once_empty_delimiter_rejected_in_compilers(self):
        main_source = """
        import String

        @untested("entry point")
        func main() -> Unit
            print(is_some(String.split_once("abc", "")))
            return ()
        end func
        """

        with pytest.raises(
            RuntimeError, match="string_split_once: delimiter cannot be empty"
        ):
            _compiled_python_project_output(main_source, {})
        with pytest.raises(
            RuntimeError, match="string_split_once: delimiter cannot be empty"
        ):
            _compiled_js_project_output(main_source, {})

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_regex_replace_treats_dollars_as_literals_across_backends(self):
        source = """
        func main() -> Unit
            let literal_group: String = regex_replace(pattern: "(a)", replacement: "$1", text: "aba")
            let literal_dollars: String = regex_replace(pattern: "(a)", replacement: "$$", text: "aba")
            let numeric_backref: String = regex_replace(pattern: "(a)", replacement: "X\\\\1", text: "aba")
            print(length(literal_group))
            print(char_code(literal_group[0]))
            print(char_code(literal_group[1]))
            print(length(literal_dollars))
            print(char_code(literal_dollars[0]))
            print(length(numeric_backref))
            return ()
        end func
        """

        result = run(
            source,
            config=RunConfig(timeout=10.0, capabilities={"print", "regex"}),
        )
        assert result.ok, (
            f"Interpreter failed: {[d.message for d in result.diagnostics]}"
        )

        _assert_expected_backend_outputs(
            label="regex replacement dollar literals",
            context=source,
            expected="5\n36\n49\n5\n36\n5\n",
            interp_out=cast(str, result.output),
            py_out=_compiled_python_output(source, capabilities={"print", "regex"}),
            js_out=_compiled_js_output(source, capabilities={"print", "regex"}),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_regex_treats_astral_chars_as_code_points_across_backends(self):
        source = """
        func main() -> Unit
            let smile: String = from_char_code(128512)
            match regex_match(pattern: ".", text: smile) with
                | Some(value) -> print(char_code(value))
                | None -> print(-1)
            end match

            let dots: List[String] = regex_find_all(pattern: ".", text: smile)
            print(length(dots))
            print(char_code(dots[0]))

            let empties: List[String] = regex_find_all(pattern: "", text: smile)
            print(length(empties))

            let replaced_dot: String = regex_replace(pattern: ".", replacement: "X", text: smile)
            print(length(replaced_dot))
            print(char_code(replaced_dot[0]))

            let replaced_empty: String = regex_replace(pattern: "", replacement: "-", text: smile)
            print(length(replaced_empty))
            print(char_code(replaced_empty[0]))
            print(char_code(replaced_empty[1]))
            print(char_code(replaced_empty[2]))
            return ()
        end func
        """

        result = run(
            source,
            config=RunConfig(timeout=10.0, capabilities={"print", "regex"}),
        )
        assert result.ok, (
            f"Interpreter failed: {[d.message for d in result.diagnostics]}"
        )

        _assert_expected_backend_outputs(
            label="regex astral code point handling",
            context=source,
            expected="128512\n1\n128512\n2\n1\n88\n3\n45\n128512\n45\n",
            interp_out=cast(str, result.output),
            py_out=_compiled_python_output(source, capabilities={"print", "regex"}),
            js_out=_compiled_js_output(source, capabilities={"print", "regex"}),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_js_string_length_guards_count_astral_chars_across_backends(self):
        source = """
        func main() -> Unit
            let smile: String = from_char_code(128512)
            let parse_text: String = string_repeat(text: smile, count: 600)
            match parse_int(parse_text) with
                | Some(_) -> print(1)
                | None -> print(0)
            end match
            match parse_float(parse_text) with
                | Some(_) -> print(1)
                | None -> print(0)
            end match

            let regex_pattern: String = string_repeat(text: smile, count: 600)
            match regex_match(pattern: regex_pattern, text: "x") with
                | Some(_) -> print(1)
                | None -> print(0)
            end match

            let regex_text: String = string_repeat(text: smile, count: 6000)
            match regex_match(pattern: "z", text: regex_text) with
                | Some(_) -> print(1)
                | None -> print(0)
            end match

            let replacement: String = string_repeat(text: smile, count: 6000)
            let unchanged: String = regex_replace(pattern: "z", replacement: replacement, text: "x")
            print(length(unchanged))
            print(char_code(unchanged[0]))
            return ()
        end func
        """

        result = run(
            source,
            config=RunConfig(timeout=10.0, capabilities={"print", "regex"}),
        )
        assert result.ok, (
            f"Interpreter failed: {[d.message for d in result.diagnostics]}"
        )

        _assert_expected_backend_outputs(
            label="astral string length guards",
            context=source,
            expected="0\n0\n0\n0\n1\n120\n",
            interp_out=cast(str, result.output),
            py_out=_compiled_python_output(source, capabilities={"print", "regex"}),
            js_out=_compiled_js_output(source, capabilities={"print", "regex"}),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_mutable_wrapper_equality_is_structural_across_backends(self):
        source = """
        func main() -> Unit
            let v1: Vec[Int] = vec_new()
            let v2: Vec[Int] = vec_new()
            vec_push(v1, 1)
            vec_push(v2, 1)
            print(v1 == v2)
            vec_push(v2, 2)
            print(v1 != v2)

            let s1: Set[Int] = set_from_list([1])
            let s2: Set[Int] = set_from_list([1])
            let s3: Set[Int] = set_from_list([2])
            print(s1 == s2)
            print(s1 != s3)

            let m1: MutableMap[String, Int] = mutable_map_new()
            let m2: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m1, key: "a", value: 1)
            mutable_map_set(map: m2, key: "a", value: 1)
            print(m1 == m2)
            mutable_map_set(map: m2, key: "a", value: 2)
            print(m1 != m2)
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="mutable wrapper structural equality",
            context=source,
            expected="true\ntrue\ntrue\ntrue\ntrue\ntrue\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_js_to_string_formats_keyed_and_mutable_wrappers(self):
        source = """
        func main() -> Unit
            let m: Map[String, Int] = map_from_entries([("a", 1)])
            print(to_string(m))

            let mm: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: mm, key: "a", value: 1)
            print(to_string(mm))

            let v: Vec[Float] = vec_from_list([1.0, 2.5])
            print(to_string(v))

            let s: Set[String] = set_from_list(["b", "a"])
            print(to_string(s))

            let vectors: List[Vec[Int]] = [vec_from_list([3])]
            let rendered: List[String] = map(vectors, to_string)
            print(head(rendered))
            return ()
        end func
        """

        expected = (
            '{"a": 1}'
            + chr(10)
            + 'MutableMap({"a": 1})'
            + chr(10)
            + "Vec([1.0, 2.5])"
            + chr(10)
            + 'Set({"a", "b"})'
            + chr(10)
            + "Vec([3])"
            + chr(10)
        )
        _assert_expected_backend_outputs(
            label="JS to_string wrapper formatting",
            context=source,
            expected=expected,
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_js_map_to_string_preserves_static_element_formatting(self):
        source = """
        func main() -> Unit
            let rendered_floats: List[String] = map([1.0, 2.5], to_string)
            if head(rendered_floats) == "1.0" then
                print(1)
            else
                print(0)
            end if
            if rendered_floats[1] == "2.5" then
                print(1)
            else
                print(0)
            end if

            let rendered_ints: List[String] = map([1, 2], to_string)
            if head(rendered_ints) == "1" then
                print(1)
            else
                print(0)
            end if

            let vectors: List[Vec[Float]] = [vec_from_list([1.0])]
            let rendered_vectors: List[String] = map(vectors, to_string)
            if head(rendered_vectors) == "Vec([1.0])" then
                print(1)
            else
                print(0)
            end if
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="JS map to_string static formatting",
            context=source,
            expected="1\n1\n1\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_js_list_helpers_to_string_preserve_static_element_formatting(self):
        source = """
        func main() -> Unit
            let rendered_floats: List[String] = list_map([1.0, 2.5], to_string)
            if head(rendered_floats) == "1.0" then
                print(1)
            else
                print(0)
            end if
            if rendered_floats[1] == "2.5" then
                print(1)
            else
                print(0)
            end if

            let groups: List[(String, List[Float])] = list_group_by([1.0, 2.0], to_string)
            let (key, values): (String, List[Float]) = groups[0]
            if key == "1.0" then
                print(1)
            else
                print(0)
            end if
            if head(values) == 1.0 then
                print(1)
            else
                print(0)
            end if
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="JS list helper to_string static formatting",
            context=source,
            expected="1\n1\n1\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_js_higher_order_to_string_preserves_static_value_formatting(self):
        source = """
        func main() -> Unit
            match option_map(Some(1.0), to_string) with
                | Some(text) ->
                    if text == "1.0" then
                        print(1)
                    else
                        print(0)
                    end if
                | None -> print(9)
            end match

            match result_map(Ok(1.0), to_string) with
                | Ok(text) ->
                    if text == "1.0" then
                        print(1)
                    else
                        print(0)
                    end if
                | Err(_) -> print(9)
            end match

            match result_map_err(Err(1.0), to_string) with
                | Ok(_) -> print(9)
                | Err(text) ->
                    if text == "1.0" then
                        print(1)
                    else
                        print(0)
                    end if
            end match

            let m: Map[String, Float] = map_from_entries([("a", 1.0)])
            let rendered: Map[String, String] = map_map_values(m, to_string)
            match map_get(rendered, "a") with
                | Some(text) ->
                    if text == "1.0" then
                        print(1)
                    else
                        print(0)
                    end if
                | None -> print(9)
            end match
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="JS higher-order to_string static formatting",
            context=source,
            expected="1\n1\n1\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_js_json_preserves_integer_valued_float_spelling(self):
        source = """
        func main() -> Unit
            if json_to_string(1.0) == "1.0" then
                print(1)
            else
                print(0)
            end if

            if json_to_string([1.0]) == "[1.0]" then
                print(1)
            else
                print(0)
            end if

            if json_stringify(JsonFloat(1.0)) == "1.0" then
                print(1)
            else
                print(0)
            end if

            if json_to_string(JsonFloat(1.0)) == "1.0" then
                print(1)
            else
                print(0)
            end if

            match json_parse("1.0") with
                | Ok(value) ->
                    match value with
                        | JsonFloat(_) ->
                            if json_stringify(value) == "1.0" then
                                print(1)
                            else
                                print(0)
                            end if
                            if json_to_string(value) == "1.0" then
                                print(1)
                            else
                                print(0)
                            end if
                        | _ -> print(0)
                    end match
                | Err(_) -> print(9)
            end match

            let tiny: Float = 1.0 / 10000000.0
            if json_to_string(tiny) == "1e-07" then
                print(1)
            else
                print(0)
            end if

            let large: Float = 10000000000000000.0
            if json_to_string([large]) == "[1e+16]" then
                print(1)
            else
                print(0)
            end if

            let larger: Float = 1000000000000000000000.0
            if json_stringify(JsonFloat(larger)) == "1e+21" then
                print(1)
            else
                print(0)
            end if
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="JS JSON integer-valued float spelling",
            context=source,
            expected="1\n1\n1\n1\n1\n1\n1\n1\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_js_json_preserves_float_spelling_in_maps_and_object_parse_order(self):
        source = """
        func main() -> Unit
            let entries: List[(String, Float)] = [("a", 1.0)]
            let floats: Map[String, Float] = map_from_entries(entries)
            if json_to_string(floats) == "{\\"a\\":1.0}" then
                print(1)
            else
                print(0)
            end if

            match json_parse("{\\"2\\":1.0,\\"1\\":2}") with
                | Ok(value) ->
                    if json_stringify(value) == "{\\"2\\":1.0,\\"1\\":2}" then
                        print(1)
                    else
                        print(0)
                    end if
                | Err(_) -> print(9)
            end match

            match json_parse("{\\"2\\":1.0,\\"2\\":2}") with
                | Ok(value) ->
                    if json_stringify(value) == "{\\"2\\":2}" then
                        print(1)
                    else
                        print(0)
                    end if
                | Err(_) -> print(9)
            end match
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="JS JSON map float spelling and object parse order",
            context=source,
            expected="1\n1\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_json_to_string_serializes_user_adts_across_backends(self):
        source = """
        type Box = Box(value: Int)
        type FloatBox = FloatBox(value: Float)
        type Color = Red | Blue

        func main() -> Unit
            if json_to_string(Box(1)) == "{\\"_tag\\":\\"Box\\",\\"value\\":1}" then
                print(1)
            else
                print(0)
            end if

            if json_to_string(FloatBox(1.0)) == "{\\"_tag\\":\\"FloatBox\\",\\"value\\":1.0}" then
                print(1)
            else
                print(0)
            end if

            if json_to_string(Red) == "{\\"_tag\\":\\"Red\\"}" then
                print(1)
            else
                print(0)
            end if
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="json_to_string user ADTs",
            context=source,
            expected="1\n1\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_json_to_string_runtime_containers_use_display_fallback(self):
        source = """
        func main() -> Unit
            let ai: Array[Int] = array_new(1, 1)
            print(json_to_string(ai))

            let af: Array[Float] = array_new(1, 1.0)
            print(json_to_string(af))

            let v: Vec[Int] = vec_new()
            vec_push(v, 1)
            print(json_to_string(v))

            let s: Set[Int] = set_from_list([1, 2])
            print(json_to_string(s))

            let m: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m, key: "a", value: 1)
            print(json_to_string(m))

            let fm: MutableMap[String, Float] = mutable_map_new()
            mutable_map_set(map: fm, key: "a", value: 1.0)
            print(json_to_string(fm))
            return ()
        end func
        """

        quote = '"'
        escaped_quote = chr(92) + quote
        newline = chr(10)
        expected = (
            quote
            + "Array([1])"
            + quote
            + newline
            + quote
            + "Array([1.0])"
            + quote
            + newline
            + quote
            + "Vec([1])"
            + quote
            + newline
            + quote
            + "Set({1, 2})"
            + quote
            + newline
            + quote
            + "MutableMap({"
            + escaped_quote
            + "a"
            + escaped_quote
            + ": 1})"
            + quote
            + newline
            + quote
            + "MutableMap({"
            + escaped_quote
            + "a"
            + escaped_quote
            + ": 1.0})"
            + quote
            + newline
        )
        _assert_expected_backend_outputs(
            label="json_to_string runtime container display fallback",
            context=source,
            expected=expected,
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_compact_json_serialization_ascii_escapes_unicode_across_backends(self):
        source = """
        func main() -> Unit
            let acute: String = from_char_code(233)
            let smile: String = from_char_code(128512)
            let compact: String = json_stringify(value: JsonString(acute))
            let compact_astral: String = json_stringify(value: JsonString(smile))
            let any_json: String = json_to_string(value: acute)
            let pretty: String = json_stringify_pretty(value: JsonString(acute), indent: 0)
            let pretty_astral: String = json_stringify_pretty(value: JsonString(smile), indent: 0)
            print(length(compact))
            print(char_code(compact[1]))
            print(length(compact_astral))
            print(char_code(compact_astral[1]))
            print(length(any_json))
            print(char_code(any_json[1]))
            print(length(pretty))
            print(char_code(pretty[1]))
            print(length(pretty_astral))
            print(char_code(pretty_astral[1]))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="compact JSON ASCII escaping",
            context=source,
            expected="8\n92\n14\n92\n8\n92\n3\n233\n3\n128512\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_csv_parse_with_headers_returns_string_maps_across_backends(self):
        source = """
        func main() -> Unit
            let rows: List[Map[String, String]] = csv_parse_with_headers("name,age\\nAlice\\nBob,31,ignored")
            let first: Map[String, String] = head(rows)
            let second: Map[String, String] = rows[1]

            print(length(map_entries(first)))
            match map_get(first, "age") with
                | Some(value) -> print(length(value))
                | None -> print(99)
            end match

            print(length(map_entries(second)))
            match map_get(second, "age") with
                | Some(value) -> print(length(value))
                | None -> print(99)
            end match
            match map_get(second, "ignored") with
                | Some(_) -> print(1)
                | None -> print(0)
            end match
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="CSV headers string map contract",
            context=source,
            expected="2\n0\n2\n2\n0\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_csv_parse_empty_rows_and_fields_across_backends(self):
        source = """
        func main() -> Unit
            let blank: List[List[String]] = csv_parse("\\n")
            print(length(blank))
            print(length(head(blank)))

            let mixed: List[List[String]] = csv_parse("a\\n\\n")
            print(length(mixed))
            print(length(mixed[1]))

            let quoted_empty: List[List[String]] = csv_parse("\\"\\"")
            let quoted_row: List[String] = head(quoted_empty)
            print(length(quoted_empty))
            print(length(quoted_row))
            print(length(quoted_row[0]))

            let trailing_empty: List[List[String]] = csv_parse("a,")
            let trailing_row: List[String] = head(trailing_empty)
            print(length(trailing_row))
            print(length(trailing_row[1]))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="CSV empty rows and fields",
            context=source,
            expected="1\n0\n2\n0\n1\n1\n0\n2\n0\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_csv_parse_mid_field_quotes_are_literal_across_backends(self):
        source = """
        func main() -> Unit
            let rows: List[List[String]] = csv_parse("a\\\",b\\na\\\"\\\"")
            let first: List[String] = head(rows)
            let second: List[String] = rows[1]

            print(length(rows))
            print(length(first))
            print(length(first[0]))
            print(char_code(first[0][1]))
            print(length(first[1]))
            print(length(second))
            print(length(second[0]))
            print(char_code(second[0][1]))
            print(char_code(second[0][2]))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="CSV mid-field quote literals",
            context=source,
            expected="2\n2\n2\n34\n1\n1\n3\n34\n34\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_csv_parse_bare_carriage_return_rows_across_backends(self):
        source = """
        func main() -> Unit
            let cr: String = from_char_code(13)
            let rows: List[List[String]] = csv_parse("a" + cr + "b" + cr + cr)
            print(length(rows))
            print(length(rows[0]))
            print(length(rows[1]))
            print(length(rows[2]))

            let with_headers: List[Map[String, String]] = csv_parse_with_headers("name" + cr + "Alice" + cr)
            print(length(with_headers))
            match map_get(head(with_headers), "name") with
                | Some(value) -> print(length(value))
                | None -> print(99)
            end match
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="CSV bare carriage return rows",
            context=source,
            expected="3\n1\n1\n0\n1\n5\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_json_parse_rejects_non_standard_constants_across_backends(self):
        source = """
        func main() -> Unit
            match json_parse(text: "NaN") with
                | Ok(_) -> print(0)
                | Err(_) -> print(1)
            end match
            match json_parse(text: "Infinity") with
                | Ok(_) -> print(0)
                | Err(_) -> print(1)
            end match
            match json_parse(text: "-Infinity") with
                | Ok(_) -> print(0)
                | Err(_) -> print(1)
            end match
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="strict JSON constants",
            context=source,
            expected="1\n1\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_json_parse_rejects_non_finite_number_overflow_across_backends(self):
        source = """
        func main() -> Unit
            match json_parse(text: "1e309") with
                | Ok(_) -> print(0)
                | Err(_) -> print(1)
            end match
            match json_parse(text: "-1e309") with
                | Ok(_) -> print(0)
                | Err(_) -> print(1)
            end match
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="strict JSON number overflow",
            context=source,
            expected="1\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_parse_float_rejects_overflow_across_backends(self):
        huge = "9" * 400
        source = f"""
        func main() -> Unit
            match parse_float("{huge}") with
                | Some(_) -> print(0)
                | None -> print(1)
            end match
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="parse_float overflow",
            context=source,
            expected="1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    @pytest.mark.parametrize(
        "expr",
        [
            "float_to_int(x)",
            "floor(x)",
            "ceil(x)",
            "round(x)",
            "math_floor(x)",
            "math_ceil(x)",
            "math_round(x)",
        ],
    )
    def test_float_to_int_helpers_reject_unsafe_results_across_backends(self, expr):
        huge = "9" * 308
        source = f"""
        func main() -> Unit
            match parse_float("{huge}") with
                | Some(x) -> print({expr})
                | None -> print(-1)
            end match
            return ()
        end func
        """

        result = run(source, config=RunConfig(timeout=10.0, capabilities={"print"}))
        assert not result.ok
        assert any(
            "safe integer range" in diagnostic.message
            for diagnostic in result.diagnostics
        )

        with pytest.raises(RuntimeError, match="safe integer range"):
            _compiled_python_output(source)
        with pytest.raises(RuntimeError, match="safe integer range"):
            _compiled_js_output(source)

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_tuple_map_keys_are_structural_across_backends(self):
        source = """
        func main() -> Unit
            let m0: Map[(Int, Int), Int] = map_from_entries([])
            let m1: Map[(Int, Int), Int] = map_insert(map: m0, key: (1, 2), value: 7)
            let m2: Map[(Int, Int), Int] = map_insert(map: m1, key: (1, 2), value: 9)
            match map_get(m2, (1, 2)) with
                | Some(v) -> print(v)
                | None -> print(0)
            end match
            print(length(map_entries(m2)))

            let mm: MutableMap[(Int, Int), Int] = mutable_map_new()
            mutable_map_set(map: mm, key: (1, 2), value: 7)
            mutable_map_set(map: mm, key: (1, 2), value: 9)
            match mutable_map_get(mm, (1, 2)) with
                | Some(v) -> print(v)
                | None -> print(0)
            end match
            print(mutable_map_size(mm))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="tuple map key equality",
            context=source,
            expected="9\n1\n9\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_group_by_uses_structural_key_equality_across_backends(self):
        source = """
        func key(x: Int) -> List[List[Int]]
            example 1 -> [[1, 2], [3]]
            if x == 1 then
                return [[1, 2], [3]]
            end if
            return [[1], [2, 3]]
        end func

        func main() -> Unit
            let groups: List[(List[List[Int]], List[Int])] = list_group_by([1, 2], key)
            print(length(groups))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="group_by structural key equality",
            context=source,
            expected="2\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_sort_by_uses_geno_key_ordering_across_backends(self):
        source = """
        func key(x: Int) -> List[Int]
            example 1 -> [10]
            if x == 1 then
                return [10]
            end if
            return [2]
        end func

        func main() -> Unit
            let sorted: List[Int] = sort_by([1, 2], key)
            print(sorted[0])
            print(sorted[1])
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="sort_by structural key ordering",
            context=source,
            expected="2\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_set_to_list_sorts_adt_values_across_backends(self):
        source = """
        func main() -> Unit
            let s: Set[Option[Int]] = set_new()
            set_add(s, Some(1))
            set_add(s, None)
            let xs: List[Option[Int]] = set_to_list(s)
            print(length(xs))
            match xs[0] with
                | None -> print(1)
                | Some(_) -> print(0)
            end match
            match xs[1] with
                | Some(v) -> print(v)
                | None -> print(0)
            end match
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="set_to_list ADT ordering",
            context=source,
            expected="2\n1\n1\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_set_to_list_sorts_mixed_numeric_values_across_backends(self):
        source = """
        func main() -> Unit
            let s: Set[Float] = set_new()
            set_add(s, 0)
            set_add(s, 0 - 1.5)
            let xs: List[Float] = set_to_list(s)
            print(float_to_int(xs[0] * 10.0))
            print(float_to_int(xs[1]))
            return ()
        end func
        """

        _assert_expected_backend_outputs(
            label="set_to_list numeric ordering",
            context=source,
            expected="-15\n0\n",
            interp_out=_interpreter_output(source),
            py_out=_compiled_python_output(source),
            js_out=_compiled_js_output(source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_js_backend_rejects_large_ints_instead_of_rounding(self):
        source = """
        func main() -> Unit
            print(2 ** 60)
            return ()
        end func
        """
        interp_out = _interpreter_output(source)
        py_out = _compiled_python_output(source)

        assert interp_out == py_out
        assert "1152921504606846976" in interp_out

        with pytest.raises(RuntimeError, match="safe integer range"):
            _compiled_js_output(source)

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_zero_base_negative_power_raises_in_all_backends(self):
        source = """
        func main() -> Unit
            print(0 ** -1)
            return ()
        end func
        """
        with pytest.raises(AssertionError, match="Division by zero"):
            _interpreter_output(source)

        with pytest.raises(
            RuntimeError,
            match=r"cannot be raised to a negative power|Division by zero",
        ):
            _compiled_python_output(source)

        with pytest.raises(RuntimeError, match="Division by zero"):
            _compiled_js_output(source)

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_negative_float_base_fractional_power_raises_in_all_backends(self):
        source = """
        func main() -> Unit
            print((0.0 - 1.0) ** 0.5)
            return ()
        end func
        """
        with pytest.raises(AssertionError, match="not a real number"):
            _interpreter_output(source)

        with pytest.raises(RuntimeError, match="not a real number"):
            _compiled_python_output(source)

        with pytest.raises(RuntimeError, match="not a real number"):
            _compiled_js_output(source)

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_stdlib_math_floor_rejects_unsafe_result_across_backends(self):
        main_source = (
            "import Math\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "  return Math.floor(9007199254740992.0)\n"
            "end func\n"
        )

        with pytest.raises(RuntimeError, match="safe integer range"):
            _compiled_python_project_output(main_source, {})
        with pytest.raises(RuntimeError, match="safe integer range"):
            _compiled_js_project_output(main_source, {})

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_try_catch_deterministic_fixture_matches_across_backends(self):
        scenario = _try_catch_program_for_spec(
            TryCatchParitySpec(
                pass_value=5,
                negative_value=-3,
                big_value=200,
                multiplier=2,
            )
        )

        _assert_expected_backend_outputs(
            label="deterministic try-catch",
            context=f"--- source ---\n{scenario.source}\n",
            expected=scenario.expected_output,
            interp_out=_interpreter_output(scenario.source),
            py_out=_compiled_python_output(scenario.source),
            js_out=_compiled_js_output(scenario.source),
        )

    @pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    def test_qualified_pipeline_stage_matches_across_backends(self):
        main_source = """
        import Math

        func main() -> Unit
            print(-3 |> Math.abs)
            print(5 |> Math.max(9))
            return ()
        end func
        """
        modules = {
            "Math": """
            func abs(x: Int) -> Int
                example -3 -> 3
                if x < 0 then
                    return 0 - x
                end if
                return x
            end func

            func max(a: Int, b: Int) -> Int
                example 1, 2 -> 2
                if a > b then
                    return a
                end if
                return b
            end func
            """
        }

        _assert_expected_backend_outputs(
            label="qualified pipeline",
            context=f"--- source ---\n{main_source}\n",
            expected="3\n9\n",
            interp_out=_interpreter_project_output(main_source, modules),
            py_out=_compiled_python_project_output(main_source, modules),
            js_out=_compiled_js_project_output(main_source, modules),
        )


@pytest.mark.skipif(
    not HAS_NODE or not HYPOTHESIS_AVAILABLE,
    reason="Node.js and Hypothesis are required for differential property tests",
)
class TestBackendParityProperties:
    """Differential property tests for generated backend-parity programs."""

    def test_try_catch_strategy_excludes_zero_multiplier(self):
        """Regression for #627 review follow-up: zero would collapse the
        success and both catch paths to identical printed output."""
        from hypothesis import find
        from hypothesis.errors import NoSuchExample

        with pytest.raises(NoSuchExample):
            find(
                _TRY_CATCH_PARITY_SPEC_STRATEGY,
                lambda spec: spec.multiplier == 0,
                settings=settings(max_examples=200, deadline=None),
            )

    @given(_PROGRAM_SCENARIO_STRATEGY)
    @example(_program_for_scenario([], [_print_step(_combine("/", _lit(-7), _lit(2)))]))
    @example(_program_for_scenario([], [_print_step(_combine("%", _lit(-7), _lit(3)))]))
    @example(_program_for_scenario([], [_print_step(_combine("%", _lit(7), _lit(-3)))]))
    @example(
        _program_for_scenario(
            [],
            [
                _print_step(
                    _combine(
                        "+",
                        _combine("*", _lit(2), _lit(3)),
                        _combine("-", _lit(4), _lit(9)),
                    )
                )
            ],
        )
    )
    @example(
        _program_for_scenario(
            [],
            [
                _if_print_step(">", _lit(3), _lit(1), _lit(42), _lit(-1)),
                _if_print_step("==", _lit(-2), _lit(-2), _lit(7), _lit(9)),
            ],
        )
    )
    @example(
        _program_for_scenario(
            [
                LetBinding("x0", _combine("+", _lit(2), _lit(3))),
                LetBinding("x1", _combine("*", ArithmeticExpr("x0", 5), _lit(4))),
            ],
            [
                _print_step(ArithmeticExpr("x1", 20)),
                _if_print_step(
                    ">",
                    ArithmeticExpr("x1", 20),
                    _lit(10),
                    _combine("-", ArithmeticExpr("x1", 20), _lit(3)),
                    _lit(0),
                ),
            ],
        )
    )
    @settings(max_examples=30, deadline=None)
    def test_generated_pure_programs_match_across_backends(
        self, scenario: ParityProgramScenario
    ):
        """Generated pure arithmetic/control-flow/binding programs agree across backends."""
        source = scenario.source
        expected = scenario.expected_output

        interp_out = _interpreter_output(source)
        py_out = _compiled_python_output(source)
        js_out = _compiled_js_output(source)

        _assert_expected_backend_outputs(
            label="reference arithmetic",
            context=f"--- source ---\n{source}\n",
            expected=expected,
            interp_out=interp_out,
            py_out=py_out,
            js_out=js_out,
        )

    @given(_HIGHER_ORDER_LIST_SPEC_STRATEGY)
    @example(
        HigherOrderListSpec(
            xs=(),
            transform=IntTransformSpec("+", 2),
            predicate=IntPredicateSpec(">", 0),
            initial=0,
            extra=5,
        )
    )
    @example(
        HigherOrderListSpec(
            xs=(-3, -1, 0, 2),
            transform=IntTransformSpec("*", -1),
            predicate=IntPredicateSpec("<=", -1),
            initial=7,
            extra=-4,
        )
    )
    @example(
        HigherOrderListSpec(
            xs=(1, 2, 3),
            transform=IntTransformSpec("-", 5),
            predicate=IntPredicateSpec(">", 10),
            initial=-2,
            extra=9,
        )
    )
    @settings(max_examples=30, deadline=None)
    def test_generated_higher_order_list_programs_match_across_backends(  # type: ignore[misc]
        self, spec: HigherOrderListSpec
    ):
        """Generated pure list/lambda programs agree across backends."""
        scenario = _higher_order_list_program_for_spec(spec)
        source = scenario.source
        expected = scenario.expected_output

        interp_out = _interpreter_output(source)
        py_out = _compiled_python_output(source)
        js_out = _compiled_js_output(source)

        _assert_expected_backend_outputs(
            label="reference list",
            context=f"--- source ---\n{source}\n",
            expected=expected,
            interp_out=interp_out,
            py_out=py_out,
            js_out=js_out,
        )

    @given(_IMPORTED_PROJECT_SPEC_STRATEGY)
    @example(
        ImportedProjectSpec(
            xs=(),
            transform=IntTransformSpec("+", 3),
            predicate=IntPredicateSpec(">", 0),
            initial=0,
            arg=-2,
        )
    )
    @example(
        ImportedProjectSpec(
            xs=(-3, 0, 4),
            transform=IntTransformSpec("*", -1),
            predicate=IntPredicateSpec("<=", 0),
            initial=5,
            arg=7,
        )
    )
    @example(
        ImportedProjectSpec(
            xs=(1, 2, 3, 4),
            transform=IntTransformSpec("-", 2),
            predicate=IntPredicateSpec(">", 10),
            initial=-1,
            arg=6,
        )
    )
    @example(
        ImportedProjectSpec(
            xs=(-4, -1, 3),
            transform=IntTransformSpec("+", 1),
            predicate=IntPredicateSpec("<=", 0),
            initial=2,
            arg=-3,
        )
    )
    @settings(max_examples=12, deadline=None)
    def test_generated_imported_projects_match_across_backends(  # type: ignore[misc]
        self, spec: ImportedProjectSpec
    ):
        """Generated two-file projects agree across interpreter and both compilers."""
        scenario = _imported_project_for_spec(spec)
        modules = dict(scenario.modules)
        expected = scenario.expected_output

        interp_out = _interpreter_project_output(scenario.main_source, modules)
        py_out = _compiled_python_project_output(scenario.main_source, modules)
        js_out = _compiled_js_project_output(scenario.main_source, modules)

        _assert_expected_backend_outputs(
            label="imported-project",
            context=(
                f"--- main ---\n{scenario.main_source}\n--- modules ---\n{modules!r}\n"
            ),
            expected=expected,
            interp_out=interp_out,
            py_out=py_out,
            js_out=js_out,
        )

    @given(_ALGEBRAIC_PARITY_SPEC_STRATEGY)
    @example(
        AlgebraicParitySpec(
            adt_tag="Wrap",
            adt_left=-3,
            adt_right=0,
            option_tag="some",
            option_value=4,
            result_tag="ok",
            result_value=5,
            nested_tag="ok",
            nested_value=7,
        )
    )
    @example(
        AlgebraicParitySpec(
            adt_tag="Pair",
            adt_left=6,
            adt_right=-2,
            option_tag="none",
            option_value=0,
            result_tag="err",
            result_value=3,
            nested_tag="err",
            nested_value=-1,
        )
    )
    @example(
        AlgebraicParitySpec(
            adt_tag="Absent",
            adt_left=1,
            adt_right=2,
            option_tag="none",
            option_value=9,
            result_tag="ok",
            result_value=-4,
            nested_tag="none",
            nested_value=0,
        )
    )
    @settings(max_examples=20, deadline=None)
    def test_generated_algebraic_programs_match_across_backends(  # type: ignore[misc]
        self, spec: AlgebraicParitySpec
    ):
        """Generated ADT/Option/Result programs agree across backends."""
        scenario = _algebraic_program_for_spec(spec)
        source = scenario.source
        expected = scenario.expected_output

        interp_out = _interpreter_output(source)
        py_out = _compiled_python_output(source)
        js_out = _compiled_js_output(source)

        _assert_expected_backend_outputs(
            label="algebraic",
            context=f"--- source ---\n{source}\n",
            expected=expected,
            interp_out=interp_out,
            py_out=py_out,
            js_out=js_out,
        )

    @given(_TRY_CATCH_PARITY_SPEC_STRATEGY)
    @example(
        TryCatchParitySpec(
            pass_value=5,
            negative_value=-3,
            big_value=200,
            multiplier=1,
        )
    )
    @example(
        TryCatchParitySpec(
            pass_value=0,
            negative_value=-50,
            big_value=101,
            multiplier=-1,
        )
    )
    @example(
        TryCatchParitySpec(
            pass_value=100,
            negative_value=-1,
            big_value=150,
            multiplier=3,
        )
    )
    @settings(max_examples=20, deadline=None)
    def test_generated_try_catch_programs_match_across_backends(  # type: ignore[misc]
        self, spec: TryCatchParitySpec
    ):
        """Generated ``try`` / ``catch`` programs agree across backends.

        Closes the #627 gap on exception control-flow: ``safe_run``
        dispatches on the thrown message so every run exercises the
        success path, the explicit catch arm, and the fallback catch
        arm.  Any divergence in how a backend raises / catches strings
        surfaces as a parity-check mismatch.
        """
        scenario = _try_catch_program_for_spec(spec)
        source = scenario.source
        expected = scenario.expected_output

        interp_out = _interpreter_output(source)
        py_out = _compiled_python_output(source)
        js_out = _compiled_js_output(source)

        _assert_expected_backend_outputs(
            label="try-catch",
            context=f"--- source ---\n{source}\n",
            expected=expected,
            interp_out=interp_out,
            py_out=py_out,
            js_out=js_out,
        )
