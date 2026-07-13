"""
Geno Test Harness
=================

Automatically generates and runs tests from function specifications.
Extracts `example`, `requires`, and `ensures` clauses and validates
that implementations match their specifications.

Ported from Stitch's harness system, adapted for Geno.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from .ast_nodes import (
    EnsuresClause,
    ExampleClause,
    FunctionDef,
    ImplDef,
    Program,
    RequiresClause,
)
from .values import _UNBOUND, ConstructorValue

# =============================================================================
# Result Types
# =============================================================================


@dataclass
class SpecViolation:
    """Represents a specification violation."""

    kind: str  # "example", "requires", "ensures"
    function: str
    message: str
    inputs: Dict[str, Any] | None = None
    expected: Any | None = None
    actual: Any | None = None


@dataclass
class HarnessResult:
    """Result of running all harness tests."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    violations: List[SpecViolation] = field(default_factory=list)
    untested: List[tuple] = field(default_factory=list)  # (name, reason) pairs

    @property
    def success(self) -> bool:
        return self.failed == 0

    def __repr__(self) -> str:
        status = "PASS" if self.success else "FAIL"
        return f"HarnessResult({status}: {self.passed}/{self.total} passed)"


@dataclass
class FunctionHarness:
    """Test harness for a single function.

    For methods defined inside an ``impl`` block, ``impl_trait`` and
    ``impl_target`` identify the implementation so the runner can dispatch
    through the interpreter's ``trait_impls`` table.  The ``name`` is the
    fully qualified ``"Target.Trait.method"`` form in that case so two impls
    of the same trait method on the same target remain distinguishable.
    """

    name: str
    param_names: List[str]
    examples: List[ExampleClause]
    requires: List[RequiresClause]
    ensures: List[EnsuresClause]
    required_param_count: int | None = None
    impl_trait: str | None = None
    impl_target: str | None = None

    @property
    def base_name(self) -> str:
        """The bare method/function name (without the ``Target.`` prefix)."""
        if self.impl_target is not None and self.impl_trait is not None:
            prefix = f"{self.impl_target}.{self.impl_trait}."
            if self.name.startswith(prefix):
                return self.name[len(prefix) :]
        return self.name

    @property
    def target_method_name(self) -> str:
        """Compatibility alias for matching ``Target.method`` filters."""
        if self.impl_target is not None and self.impl_trait is not None:
            return f"{self.impl_target}.{self.base_name}"
        return self.name


def example_call_args(
    input_val: Any, *, param_count: int, required_count: int
) -> list[Any]:
    """Convert an evaluated example input into function call arguments.

    A tuple expression in an example is historically the way to pass multiple
    arguments.  When the target has exactly one parameter, though, a non-empty
    tuple is an ordinary tuple value for that single parameter.
    """
    if required_count == 0 and input_val == ():
        return []
    if param_count == 1:
        return [input_val]
    if isinstance(input_val, tuple):
        return list(input_val)
    return [input_val]


# =============================================================================
# Harness Extraction
# =============================================================================


def _harness_from_function_def(
    func_def: FunctionDef,
    *,
    impl_trait: str | None = None,
    impl_target: str | None = None,
) -> FunctionHarness | None:
    """Build a ``FunctionHarness`` from one ``FunctionDef`` if it has any
    spec clauses worth running.  Returns ``None`` otherwise."""
    if not (
        func_def.specs.examples or func_def.specs.requires or func_def.specs.ensures
    ):
        return None
    qualified_name = (
        f"{impl_target}.{impl_trait}.{func_def.name}"
        if impl_target is not None and impl_trait is not None
        else func_def.name
    )
    return FunctionHarness(
        name=qualified_name,
        param_names=[p.name for p in func_def.params],
        examples=func_def.specs.examples,
        requires=func_def.specs.requires,
        ensures=func_def.specs.ensures,
        required_param_count=sum(
            1 for param in func_def.params if param.default_value is None
        ),
        impl_trait=impl_trait,
        impl_target=impl_target,
    )


def extract_harnesses(program: Program) -> List[FunctionHarness]:
    """
    Extract test harnesses from a program's definitions.

    Top-level ``func``s become plain harnesses (``impl_trait`` /
    ``impl_target`` are ``None``).  Methods inside an ``impl Trait for
    Target`` block are surfaced as well — otherwise ``geno test`` would
    silently skip every contract, example, and postcondition that lives on
    a trait implementation (F-0022).
    """
    harnesses: List[FunctionHarness] = []

    for defn in program.definitions:
        if isinstance(defn, FunctionDef):
            harness = _harness_from_function_def(defn)
            if harness is not None:
                harnesses.append(harness)
        elif isinstance(defn, ImplDef):
            for method in defn.methods:
                harness = _harness_from_function_def(
                    method,
                    impl_trait=defn.trait_name,
                    impl_target=defn.target_type,
                )
                if harness is not None:
                    harnesses.append(harness)

    return harnesses


def _coerce_value_for_compiled_harness(value: Any, globals_dict: dict[str, Any]) -> Any:
    """Convert interpreter values into compiled-runtime values."""
    if isinstance(value, ConstructorValue):
        if value.constructor == "None" and "None_" in globals_dict:
            return globals_dict["None_"]
        constructor = globals_dict.get(value.constructor)
        if callable(constructor):
            fields = {
                name: _coerce_value_for_compiled_harness(field_value, globals_dict)
                for name, field_value in value.fields.items()
            }
            return constructor(**fields)
        return value

    if isinstance(value, list):
        return [
            _coerce_value_for_compiled_harness(item, globals_dict) for item in value
        ]

    if isinstance(value, tuple):
        return tuple(
            _coerce_value_for_compiled_harness(item, globals_dict) for item in value
        )

    if isinstance(value, dict):
        return {
            _coerce_value_for_compiled_harness(key, globals_dict): (
                _coerce_value_for_compiled_harness(item, globals_dict)
            )
            for key, item in value.items()
        }

    return value


# =============================================================================
# Harness Runner
# =============================================================================


class HarnessRunner:
    """
    Runs test harnesses against function implementations.

    Can work with either:
    - Interpreter: Pass the interpreter instance
    - Compiled code: Pass the globals dict from compile_and_exec
    """

    def __init__(
        self,
        get_function: Callable[[str], Callable | None],
        *,
        eval_example_input: Callable[[ExampleClause], Any] | None = None,
        eval_example_output: Callable[[ExampleClause], Any] | None = None,
    ):
        """
        Args:
            get_function: Callable that takes a function name and returns
                         the function implementation, or None if not found.
        """
        self.get_function = get_function
        self.eval_example_input = eval_example_input
        self.eval_example_output = eval_example_output

    def run_harnesses(self, harnesses: List[FunctionHarness]) -> HarnessResult:
        """Run all harnesses and collect results."""
        result = HarnessResult()

        for harness in harnesses:
            violations = self.run_harness(harness)
            result.total += len(harness.examples)
            result.failed += len(violations)
            result.passed += len(harness.examples) - len(violations)
            result.violations.extend(violations)

        return result

    def run_harness(self, harness: FunctionHarness) -> List[SpecViolation]:
        """Run a single function's harness."""
        violations = []

        func = self.get_function(harness.name)
        if func is None:
            violations.append(
                SpecViolation(
                    kind="error",
                    function=harness.name,
                    message=f"Function '{harness.name}' not found",
                )
            )
            return violations

        for example in harness.examples:
            violation = self._run_example(harness, func, example)
            if violation:
                violations.append(violation)

        return violations

    def _run_example(
        self, harness: FunctionHarness, func: Callable, example: ExampleClause
    ) -> SpecViolation | None:
        """Run a single example test."""
        try:
            inputs = self._eval_example_input(example)
            expected = self._eval_example_output(example)
            args = example_call_args(
                inputs,
                param_count=len(harness.param_names),
                required_count=(
                    harness.required_param_count
                    if harness.required_param_count is not None
                    else len(harness.param_names)
                ),
            )

            # Call the function
            actual = func(*args)

            # Compare results
            if not self._values_equal(actual, expected):
                return SpecViolation(
                    kind="example",
                    function=harness.name,
                    message=f"Example failed: expected {expected!r}, got {actual!r}",
                    inputs={"args": args},
                    expected=expected,
                    actual=actual,
                )

            return None

        except Exception as e:
            return SpecViolation(
                kind="example",
                function=harness.name,
                message=f"Runtime error: {e}",
                inputs={"args": str(example.input_expr)},
            )

    def _eval_example_input(self, example: ExampleClause) -> Any:
        """Evaluate the input expression of an example."""
        if self.eval_example_input is not None:
            return self.eval_example_input(example)
        return example.input_expr

    def _eval_example_output(self, example: ExampleClause) -> Any:
        """Evaluate the expected output of an example."""
        if self.eval_example_output is not None:
            return self.eval_example_output(example)
        return example.output_expr

    def _values_equal(self, a: Any, b: Any) -> bool:
        """Check if two values are equal (handling special cases)."""
        # Handle floating point comparison
        if isinstance(a, float) and isinstance(b, float):
            return abs(a - b) < 1e-9

        # Handle list comparison
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                return False
            return all(self._values_equal(x, y) for x, y in zip(a, b))

        return bool(a == b)


# =============================================================================
# Convenience Functions
# =============================================================================


def run_harness_from_source(source: str, check_examples: bool = True) -> HarnessResult:
    """
    Parse source, extract harnesses, and run them.

    This is a convenience function that does everything in one call.
    """
    from .interpreter import Interpreter
    from .lexer import Lexer
    from .parser import Parser
    from .typechecker import TypeChecker

    # Parse
    lexer = Lexer(source)
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    program = parser.parse_program()

    # Type check
    checker = TypeChecker()
    checker.check_program(program)

    # Create interpreter
    interp = Interpreter(check_examples=False)  # We'll check manually

    # Load definitions without executing main; the harness drives the calls.
    interp.run(program, execute_main=False)

    # Extract harnesses.  ``run_harness_from_source`` pre-dates impl-method
    # extraction (F-0022) and its ``HarnessRunner`` dispatch is purely
    # name-based, so it can't resolve ``trait_impls``.  Route the impl
    # harnesses through ``trait_impls`` explicitly instead of silently
    # failing with "function not found" on the qualified
    # ``Target.Trait.method`` name.
    # name.  The primary ``geno test`` path in ``test_runner.py`` has its
    # own, richer resolver and does not go through this function.
    harnesses = extract_harnesses(program)

    def get_func(name: str) -> Callable | None:
        for harness in harnesses:
            if (
                harness.name != name
                or harness.impl_trait is None
                or harness.impl_target is None
            ):
                continue
            impl_closures = interp.trait_impls.get(
                (harness.impl_trait, harness.impl_target)
            )
            if impl_closures is None:
                return None
            method_closure = impl_closures.get(harness.base_name)
            if method_closure is None:
                return None
            return lambda *args, _mc=method_closure: interp._call_function(
                _mc, list(args)
            )
        closure = interp.global_env.lookup(name)
        if closure is _UNBOUND:
            return None
        return lambda *args, _c=closure: interp._call_function(_c, list(args))

    runner = HarnessRunner(
        get_func,
        eval_example_input=lambda example: interp.eval_expr(
            example.input_expr, interp.global_env
        ),
        eval_example_output=lambda example: interp.eval_expr(
            example.output_expr, interp.global_env
        ),
    )
    return runner.run_harnesses(harnesses)


def run_harness_from_compiled(source: str) -> HarnessResult:
    """
    Compile source to Python, execute, and run harnesses.

    Impl-method harnesses are skipped here: the compiled-Python code path
    does not expose trait-implementation closures under the qualified
    ``Target.Trait.method`` name that ``extract_harnesses`` now emits.  The
    primary ``geno test`` runner uses the interpreter path and covers
    those; this helper stays scoped to top-level functions.
    """
    from .compiler import compile_and_exec
    from .interpreter import Interpreter
    from .lexer import Lexer
    from .parser import Parser

    # Parse for harness extraction
    lexer = Lexer(source)
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    program = parser.parse_program()

    # Compile and execute
    globals_dict = compile_and_exec(source, sandboxed=True, timeout=None)

    evaluator = Interpreter(check_examples=False)
    evaluator.run(program, execute_main=False)

    # Extract only top-level harnesses (impl methods aren't reachable via
    # the compiled-globals dict under their qualified names).
    harnesses = [h for h in extract_harnesses(program) if h.impl_trait is None]

    # Create runner that gets functions from compiled globals
    def get_func(name: str) -> Callable | None:
        return globals_dict.get(name)

    def eval_compiled_input(example: ExampleClause) -> Any:
        return _coerce_value_for_compiled_harness(
            evaluator.eval_expr(example.input_expr, evaluator.global_env),
            globals_dict,
        )

    def eval_compiled_output(example: ExampleClause) -> Any:
        return _coerce_value_for_compiled_harness(
            evaluator.eval_expr(example.output_expr, evaluator.global_env),
            globals_dict,
        )

    runner = HarnessRunner(
        get_func,
        eval_example_input=eval_compiled_input,
        eval_example_output=eval_compiled_output,
    )
    return runner.run_harnesses(harnesses)


def generate_test_report(result: HarnessResult) -> str:
    """Generate a human-readable test report."""
    lines = []
    lines.append("=" * 60)
    lines.append("GENO TEST HARNESS REPORT")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Total:  {result.total}")
    lines.append(f"Passed: {result.passed}")
    lines.append(f"Failed: {result.failed}")
    lines.append("")

    if result.violations:
        lines.append("-" * 60)
        lines.append("VIOLATIONS:")
        lines.append("-" * 60)
        for v in result.violations:
            lines.append(f"  [{v.kind.upper()}] {v.function}: {v.message}")
            if v.inputs:
                lines.append(f"    Inputs: {v.inputs}")
            if v.expected is not None:
                lines.append(f"    Expected: {v.expected!r}")
            if v.actual is not None:
                lines.append(f"    Actual:   {v.actual!r}")
            lines.append("")

    lines.append("=" * 60)
    status = "PASS" if result.success else "FAIL"
    lines.append(f"RESULT: {status}")
    lines.append("=" * 60)

    return "\n".join(lines)
