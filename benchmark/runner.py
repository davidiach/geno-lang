"""
Benchmark Runner
================

Evaluates LLM-generated solutions against benchmark problems.
"""

import math
import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator

from benchmark.schema import (
    Problem,
    TestCase,
    _allows_none_payload,
    _is_unit_type,
    _is_unit_value,
    _requires_explicit_option_payload,
    _tuple_type_args,
    _type_args,
    _variant_payload_type,
)
from geno.sandbox import (
    BLOCKED_BUILTINS,
    SAFE_BUILTINS,
    ProcessSandbox,
    ProcessSandboxConfig,
    SandboxError,
    _create_module_proxy,
    safe_getattr,
    safe_hasattr,
    validate_code_safety,
)
from geno.sandbox import (
    TimeoutError as ProcessSandboxTimeoutError,
)
from geno.values import ConstructorValue

_MISSING = object()

# Python benchmark workers load the research evaluator and schema before
# applying their address-space limit. macOS treats this as a growth budget
# above that trusted bootstrap baseline, and 256 MiB is too close to the
# allocator/VM-map boundary across hosted runner images. Keep the production
# sandbox default at 256 MiB while giving this research-only worker a still-
# bounded budget that also remains below the existing allocation-bomb test.
_PYTHON_BENCHMARK_MAX_MEMORY_BYTES = 512 * 1024 * 1024


class ErrorCategory(Enum):
    """Categories of errors for analysis."""

    NONE = "none"  # No error
    SYNTAX = "syntax"  # Parse/syntax error
    TYPE = "type"  # Type checking error
    RUNTIME = "runtime"  # Runtime exception
    WRONG_ANSWER = "wrong_answer"  # Incorrect output
    TIMEOUT = "timeout"  # Exceeded time limit
    INCOMPLETE = "incomplete"  # Missing code


class BenchmarkTimeoutError(TimeoutError):
    """Raised when a benchmark execution exceeds its time budget."""

    pass


class UnsafePythonEvaluationDisabled(RuntimeError):
    """Raised when raw Python benchmark execution is used without an explicit opt-in."""

    pass


@dataclass
class TestResult:
    """Result of running a single test case."""

    test_case: TestCase
    passed: bool
    actual_output: Any = None
    error_category: ErrorCategory = ErrorCategory.NONE
    error_message: str = ""
    execution_time_ms: float = 0.0


@dataclass
class EvaluationResult:
    """Result of evaluating a solution."""

    problem_id: str
    language: str
    solution_code: str

    # Results
    parsed: bool = False
    type_checked: bool = False
    visible_passed: int = 0
    visible_total: int = 0
    hidden_passed: int = 0
    hidden_total: int = 0

    # Error info
    error_category: ErrorCategory = ErrorCategory.NONE
    error_message: str = ""

    # Details
    test_results: list[TestResult] = field(default_factory=list)

    # Timing
    parse_time_ms: float = 0.0
    typecheck_time_ms: float = 0.0
    total_execution_time_ms: float = 0.0

    # Token counts
    solution_tokens: int = 0

    @property
    def all_passed(self) -> bool:
        """Check if all tests passed."""
        return (
            self.visible_passed == self.visible_total
            and self.hidden_passed == self.hidden_total
        )

    @property
    def pass_rate(self) -> float:
        """Calculate overall pass rate."""
        total = self.visible_total + self.hidden_total
        if total == 0:
            return 0.0
        return (self.visible_passed + self.hidden_passed) / total

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "problem_id": self.problem_id,
            "language": self.language,
            "parsed": self.parsed,
            "type_checked": self.type_checked,
            "visible_passed": self.visible_passed,
            "visible_total": self.visible_total,
            "hidden_passed": self.hidden_passed,
            "hidden_total": self.hidden_total,
            "all_passed": self.all_passed,
            "pass_rate": self.pass_rate,
            "error_category": self.error_category.value,
            "error_message": self.error_message,
            "parse_time_ms": self.parse_time_ms,
            "typecheck_time_ms": self.typecheck_time_ms,
            "total_execution_time_ms": self.total_execution_time_ms,
            "solution_tokens": self.solution_tokens,
        }


_WORKER_RESULT_VERSION = 1
_MAX_WIRE_ITEMS = 1_000
_MAX_WIRE_DEPTH = 20
_MAX_WIRE_STRING_CHARS = 10_000


def _bounded_worker_text(value: Any) -> str:
    """Render diagnostic-only values without allowing unbounded IPC."""
    text = repr(value)
    if len(text) > _MAX_WIRE_STRING_CHARS:
        return text[: _MAX_WIRE_STRING_CHARS - 16] + "... [truncated]"
    return text


def _worker_json_value(value: Any, depth: int = 0) -> Any:
    """Convert an actual test output to bounded, non-executable JSON data."""
    if depth >= _MAX_WIRE_DEPTH:
        return "<maximum result depth exceeded>"
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, str):
        if len(value) <= _MAX_WIRE_STRING_CHARS:
            return value
        return value[: _MAX_WIRE_STRING_CHARS - 16] + "... [truncated]"
    if isinstance(value, list | tuple):
        sequence_items = [
            _worker_json_value(item, depth + 1) for item in value[:_MAX_WIRE_ITEMS]
        ]
        if len(value) > _MAX_WIRE_ITEMS:
            sequence_items.append("<additional items truncated>")
        return sequence_items
    if isinstance(value, dict):
        mapping_items: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_WIRE_ITEMS:
                mapping_items["<truncated>"] = "<additional items truncated>"
                break
            wire_key = key if isinstance(key, str) else _bounded_worker_text(key)
            if len(wire_key) > _MAX_WIRE_STRING_CHARS:
                wire_key = wire_key[: _MAX_WIRE_STRING_CHARS - 16] + "... [truncated]"
            mapping_items[wire_key] = _worker_json_value(item, depth + 1)
        return mapping_items
    return _bounded_worker_text(value)


def _evaluation_result_to_worker_payload(result: EvaluationResult) -> dict[str, Any]:
    """Serialize an evaluation result for bounded JSON IPC."""
    return {
        "version": _WORKER_RESULT_VERSION,
        "problem_id": result.problem_id,
        "language": result.language,
        "parsed": result.parsed,
        "type_checked": result.type_checked,
        "visible_passed": result.visible_passed,
        "visible_total": result.visible_total,
        "hidden_passed": result.hidden_passed,
        "hidden_total": result.hidden_total,
        "error_category": result.error_category.value,
        "error_message": result.error_message[:_MAX_WIRE_STRING_CHARS],
        "parse_time_ms": result.parse_time_ms,
        "typecheck_time_ms": result.typecheck_time_ms,
        "total_execution_time_ms": result.total_execution_time_ms,
        "solution_tokens": result.solution_tokens,
        "test_results": [
            {
                "passed": item.passed,
                "actual_output": _worker_json_value(item.actual_output),
                "error_category": item.error_category.value,
                "error_message": item.error_message[:_MAX_WIRE_STRING_CHARS],
                "execution_time_ms": item.execution_time_ms,
            }
            for item in result.test_results[:_MAX_WIRE_ITEMS]
        ],
    }


def _evaluation_result_from_worker_payload(
    problem: Problem,
    solution_code: str,
    payload: Any,
) -> EvaluationResult:
    """Reconstruct trusted dataclasses from a validated JSON-only result."""
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _WORKER_RESULT_VERSION
    ):
        raise ValueError("unsupported worker result version")
    expected_tests = [*problem.visible_examples, *problem.hidden_tests]
    wire_tests = payload.get("test_results")
    if not isinstance(wire_tests, list) or len(wire_tests) > len(expected_tests):
        raise ValueError("invalid worker test result count")

    result = EvaluationResult(
        problem_id=str(payload["problem_id"]),
        language=str(payload["language"]),
        solution_code=solution_code,
        parsed=bool(payload["parsed"]),
        type_checked=bool(payload["type_checked"]),
        visible_passed=int(payload["visible_passed"]),
        visible_total=int(payload["visible_total"]),
        hidden_passed=int(payload["hidden_passed"]),
        hidden_total=int(payload["hidden_total"]),
        error_category=ErrorCategory(str(payload["error_category"])),
        error_message=str(payload["error_message"]),
        parse_time_ms=float(payload["parse_time_ms"]),
        typecheck_time_ms=float(payload["typecheck_time_ms"]),
        total_execution_time_ms=float(payload["total_execution_time_ms"]),
        solution_tokens=int(payload["solution_tokens"]),
    )
    for test_case, wire_test in zip(expected_tests, wire_tests):
        if not isinstance(wire_test, dict):
            raise TypeError("worker test result must be an object")
        result.test_results.append(
            TestResult(
                test_case=test_case,
                passed=bool(wire_test["passed"]),
                actual_output=wire_test.get("actual_output"),
                error_category=ErrorCategory(str(wire_test["error_category"])),
                error_message=str(wire_test["error_message"]),
                execution_time_ms=float(wire_test["execution_time_ms"]),
            )
        )
    return result


def _python_process_error_result(
    problem: Problem,
    solution_code: str,
    category: ErrorCategory,
    message: str,
) -> EvaluationResult:
    """Build a fail-closed result when the isolated evaluator cannot answer."""
    return EvaluationResult(
        problem_id=problem.id,
        language="python",
        solution_code=solution_code,
        visible_total=len(problem.visible_examples),
        hidden_total=len(problem.hidden_tests),
        error_category=category,
        error_message=message[:_MAX_WIRE_STRING_CHARS],
        solution_tokens=len(solution_code.split()),
    )


class BenchmarkRunner:
    """
    Runs benchmark evaluations for Geno and Python solutions.

    Example:
        runner = BenchmarkRunner()
        result = runner.evaluate_geno(problem, solution_code)
    """

    def __init__(
        self,
        timeout_seconds: float = 5.0,
        allow_unsafe_python_execution: bool = False,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.allow_unsafe_python_execution = allow_unsafe_python_execution

    @classmethod
    def for_research(cls, timeout_seconds: float = 5.0) -> "BenchmarkRunner":
        """Opt into Python evaluation with process isolation enabled."""
        return cls(
            timeout_seconds=timeout_seconds,
            allow_unsafe_python_execution=True,
        )

    @contextmanager
    def _timeout_guard(self) -> Iterator[None]:
        """Apply a per-call timeout when the platform supports it."""
        if self.timeout_seconds is None or self.timeout_seconds <= 0:
            yield
            return

        if (
            not hasattr(signal, "setitimer")
            or threading.current_thread() is not threading.main_thread()
        ):
            yield
            return

        sigalrm = vars(signal)["SIGALRM"]
        itimer_real = vars(signal)["ITIMER_REAL"]
        previous_handler = signal.getsignal(sigalrm)

        def handle_timeout(signum: int, frame: Any) -> None:
            raise BenchmarkTimeoutError(
                f"Execution timed out after {self.timeout_seconds} seconds"
            )

        signal.signal(sigalrm, handle_timeout)
        signal.setitimer(itimer_real, self.timeout_seconds)
        try:
            yield
        finally:
            signal.setitimer(itimer_real, 0)
            signal.signal(sigalrm, previous_handler)

    def _call_with_timeout(self, func: Callable[[], Any]) -> Any:
        """Execute a callback under the configured benchmark timeout."""
        if self.timeout_seconds is None or self.timeout_seconds <= 0:
            return func()

        if (
            hasattr(signal, "setitimer")
            and threading.current_thread() is threading.main_thread()
        ):
            with self._timeout_guard():
                return func()

        result: dict[str, Any] = {}
        error: dict[str, BaseException] = {}
        finished_at: dict[str, float] = {}
        deadline = time.monotonic() + self.timeout_seconds

        def target() -> None:
            try:
                result["value"] = func()
            except BaseException as exc:
                error["value"] = exc
            finally:
                finished_at["value"] = time.monotonic()

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(max(0.0, deadline - time.monotonic()))
        completed_at = finished_at.get("value")
        if thread.is_alive() or completed_at is None or completed_at >= deadline:
            raise BenchmarkTimeoutError(
                f"Execution timed out after {self.timeout_seconds} seconds"
            )
        if "value" in error:
            raise error["value"]
        return result.get("value")

    def _record_test_result(
        self,
        result: EvaluationResult,
        test_result: TestResult,
        *,
        visible: bool,
    ) -> None:
        """Persist a per-test result and surface the first failure category."""
        result.test_results.append(test_result)
        if test_result.passed:
            if visible:
                result.visible_passed += 1
            else:
                result.hidden_passed += 1
            return

        if result.error_category == ErrorCategory.NONE:
            result.error_category = test_result.error_category
            if test_result.error_message:
                result.error_message = test_result.error_message
            elif test_result.error_category == ErrorCategory.WRONG_ANSWER:
                result.error_message = "Wrong answer on at least one test"

    def _prepare_test_args(
        self, test: TestCase, problem: Problem, *, target: str
    ) -> list[Any]:
        """Normalize benchmark inputs into positional arguments."""
        if not problem.inputs:
            return []

        if len(problem.inputs) > 1:
            if isinstance(test.input, list):
                return [
                    self._normalize_test_value(
                        value,
                        problem.inputs[index].type
                        if index < len(problem.inputs)
                        else None,
                        target=target,
                    )
                    for index, value in enumerate(test.input)
                ]
            return [test.input]

        if not isinstance(test.input, list):
            return [
                self._normalize_test_value(
                    test.input, problem.inputs[0].type, target=target
                )
            ]

        input_type = problem.inputs[0].type if problem.inputs else ""
        if len(test.input) == 1:
            return [
                self._normalize_test_value(test.input[0], input_type, target=target)
            ]

        return [self._normalize_test_value(test.input, input_type, target=target)]

    def _normalize_test_value(
        self, value: Any, expected_type: str | None, *, target: str = "python"
    ) -> Any:
        """Normalize JSON-backed benchmark values into runtime-shaped values."""
        if _is_unit_type(expected_type):
            if _is_unit_value(value):
                return () if target == "geno" else None
            raise ValueError(f"Cannot normalize {value!r} as Unit")

        option_args = _type_args(expected_type or "", "Option")
        if option_args and len(option_args) == 1:
            return self._normalize_option_value(value, option_args[0], target)

        result_args = _type_args(expected_type or "", "Result")
        if result_args and len(result_args) == 2:
            return self._normalize_result_value(
                value, result_args[0], result_args[1], target
            )

        tuple_args = _tuple_type_args(expected_type or "")
        if tuple_args is not None:
            if not isinstance(value, list | tuple):
                raise ValueError(
                    f"Expected sequence for tuple type {expected_type!r}, got {value!r}"
                )
            if len(value) != len(tuple_args):
                raise ValueError(
                    f"Expected {len(tuple_args)} values for tuple type "
                    f"{expected_type!r}, got {len(value)}"
                )
            return tuple(
                self._normalize_test_value(
                    item,
                    tuple_args[index],
                    target=target,
                )
                for index, item in enumerate(value)
            )

        list_args = _type_args(expected_type or "", "List")
        if list_args and len(list_args) == 1 and isinstance(value, list):
            return [
                self._normalize_test_value(item, list_args[0], target=target)
                for item in value
            ]

        return value

    def _normalize_option_value(
        self, value: Any, payload_type: str, target: str
    ) -> Any:
        """Normalize schema-backed Option values for the selected backend."""
        if value is None or value == "None" or value == {"None": None}:
            return ConstructorValue("None", {}) if target == "geno" else None

        if not (isinstance(value, dict) and set(value) == {"Some"}):
            raise ValueError(f"Cannot normalize {value!r} as Option[{payload_type}]")

        if value["Some"] is None and not _allows_none_payload(payload_type):
            raise ValueError(f"Cannot normalize None as Option[{payload_type}] payload")

        payload = self._normalize_test_value(value["Some"], payload_type, target=target)
        if target == "geno":
            return ConstructorValue("Some", {"value": payload})
        if _requires_explicit_option_payload(payload_type):
            return {"Some": payload}
        return payload

    def _normalize_result_value(
        self, value: Any, ok_type: str, err_type: str, target: str
    ) -> Any:
        """Normalize schema-backed Result values for the selected backend."""
        if not isinstance(value, dict) or len(value) != 1:
            raise ValueError(
                f"Cannot normalize {value!r} as Result[{ok_type}, {err_type}]"
            )

        if set(value) == {"Ok"}:
            if value["Ok"] is None and not _allows_none_payload(ok_type):
                raise ValueError(
                    f"Cannot normalize None as Result[{ok_type}, {err_type}] Ok payload"
                )
            payload = self._normalize_test_value(value["Ok"], ok_type, target=target)
            if target == "geno":
                return ConstructorValue("Ok", {"value": payload})
            return {"Ok": payload}
        if set(value) == {"Err"}:
            if value["Err"] is None and not _allows_none_payload(err_type):
                raise ValueError(
                    f"Cannot normalize None as Result[{ok_type}, {err_type}] Err payload"
                )
            payload = self._normalize_test_value(value["Err"], err_type, target=target)
            if target == "geno":
                return ConstructorValue("Err", {"error": payload})
            return {"Err": payload}

        raise ValueError(f"Cannot normalize {value!r} as Result[{ok_type}, {err_type}]")

    def _require_python_execution_opt_in(self) -> None:
        """Fail closed unless the caller explicitly opts into raw Python execution."""
        if self.allow_unsafe_python_execution:
            return
        raise UnsafePythonEvaluationDisabled(
            "BenchmarkRunner.evaluate_python() is disabled by default. "
            "Raw Python benchmark execution is a research-only convenience, "
            "not a production sandbox. Use BenchmarkRunner.for_research() "
            "or pass allow_unsafe_python_execution=True in a controlled "
            "local workflow."
        )

    def evaluate_geno(self, problem: Problem, solution_code: str) -> EvaluationResult:
        """Evaluate a Geno solution."""
        from geno.interpreter import Interpreter
        from geno.interpreter import RuntimeError as GenoRuntimeError
        from geno.lexer import Lexer, LexerError
        from geno.parser import ParseError, ParseErrors, Parser
        from geno.sandbox import SandboxConfig
        from geno.sandbox import TimeoutError as GenoTimeoutError
        from geno.typechecker import TypeChecker, TypeError

        result = EvaluationResult(
            problem_id=problem.id,
            language="geno",
            solution_code=solution_code,
            visible_total=len(problem.visible_examples),
            hidden_total=len(problem.hidden_tests),
        )

        # Count tokens (simple approximation)
        result.solution_tokens = len(solution_code.split())

        # Parse
        start_time = time.time()
        try:
            lexer = Lexer(solution_code, "<solution>")
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            program = parser.parse_program()
            result.parsed = True
            result.parse_time_ms = (time.time() - start_time) * 1000
        except (LexerError, ParseError, ParseErrors) as e:
            result.error_category = ErrorCategory.SYNTAX
            if isinstance(e, ParseErrors):
                result.error_message = "\n".join(str(err) for err in e.errors)
            else:
                result.error_message = str(e)
            return result

        # Type check
        start_time = time.time()
        try:
            checker = TypeChecker()
            checker.check_program(program)
            result.type_checked = True
            result.typecheck_time_ms = (time.time() - start_time) * 1000
        except TypeError as e:
            result.error_category = ErrorCategory.TYPE
            result.error_message = str(e)
            return result

        # Initialize interpreter
        try:
            interpreter = Interpreter(
                check_examples=False,
                sandbox_config=SandboxConfig(timeout=self.timeout_seconds),
            )
            interpreter.run(program, execute_main=False)
            func = interpreter.global_env.lookup(problem.function_name)
            if func is None:
                result.error_category = ErrorCategory.INCOMPLETE
                result.error_message = f"Function '{problem.function_name}' not found"
                return result
        except GenoTimeoutError as e:
            result.error_category = ErrorCategory.TIMEOUT
            result.error_message = str(e)
            return result
        except GenoRuntimeError as e:
            result.error_category = ErrorCategory.RUNTIME
            result.error_message = str(e)
            return result

        # Run visible examples
        for test in problem.visible_examples:
            test_result = self._run_geno_test(interpreter, func, test, problem)
            self._record_test_result(result, test_result, visible=True)

        # Run hidden tests
        for test in problem.hidden_tests:
            test_result = self._run_geno_test(interpreter, func, test, problem)
            self._record_test_result(result, test_result, visible=False)

        result.total_execution_time_ms = sum(
            t.execution_time_ms for t in result.test_results
        )

        return result

    def _run_geno_test(
        self, interpreter: Any, func: Any, test: TestCase, problem: Problem
    ) -> TestResult:
        """Run a single test case with Geno interpreter."""
        from geno.interpreter import RuntimeError as GenoRuntimeError
        from geno.sandbox import TimeoutError as GenoTimeoutError

        start_time = time.time()
        try:
            args = self._prepare_test_args(test, problem, target="geno")

            # Call function
            actual = interpreter.call_function(func, args, timeout=self.timeout_seconds)

            # Compare result
            passed = self._values_equal(
                actual, test.output, problem.output.type, target="geno"
            )

            return TestResult(
                test_case=test,
                passed=passed,
                actual_output=actual,
                error_category=ErrorCategory.NONE
                if passed
                else ErrorCategory.WRONG_ANSWER,
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        except GenoTimeoutError as e:
            return TestResult(
                test_case=test,
                passed=False,
                error_category=ErrorCategory.TIMEOUT,
                error_message=str(e),
                execution_time_ms=(time.time() - start_time) * 1000,
            )
        except GenoRuntimeError as e:
            return TestResult(
                test_case=test,
                passed=False,
                error_category=ErrorCategory.RUNTIME,
                error_message=str(e),
                execution_time_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            return TestResult(
                test_case=test,
                passed=False,
                error_category=ErrorCategory.RUNTIME,
                error_message=f"Unexpected error: {e}",
                execution_time_ms=(time.time() - start_time) * 1000,
            )

    def evaluate_python(
        self, problem: Problem, solution_code: str, sandboxed: bool = True
    ) -> EvaluationResult:
        """Evaluate a Python solution in a resource-limited child by default.

        Args:
            problem: The problem to evaluate against
            solution_code: Python solution code
            sandboxed: Whether to run in the restricted benchmark namespace.
                Passing False is an explicit in-process escape hatch for
                trusted debugging only.
        """
        self._require_python_execution_opt_in()

        if sandboxed:
            return self._evaluate_python_isolated(problem, solution_code)
        return self._evaluate_python_in_process(
            problem,
            solution_code,
            sandboxed=sandboxed,
        )

    def _evaluate_python_isolated(
        self,
        problem: Problem,
        solution_code: str,
    ) -> EvaluationResult:
        """Evaluate generated Python without sharing parent memory or secrets."""
        test_count = len(problem.visible_examples) + len(problem.hidden_tests)
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int | float)
            or self.timeout_seconds <= 0
        ):
            return _python_process_error_result(
                problem,
                solution_code,
                ErrorCategory.RUNTIME,
                "Isolated Python evaluation requires a positive timeout",
            )
        else:
            # Preserve the existing per-phase/per-test timeout semantics while
            # also bounding interpreter startup and the complete child lifetime.
            process_timeout = 2.0 + self.timeout_seconds * (test_count + 1)

        config = ProcessSandboxConfig(
            timeout=process_timeout,
            max_memory_bytes=_PYTHON_BENCHMARK_MAX_MEMORY_BYTES,
            max_cpu_time=process_timeout,
            strict=False,
            allow_print=False,
            max_output_length=100_000,
        )
        try:
            payload, _output, error = ProcessSandbox(config).execute_python_benchmark(
                {
                    "problem": problem.to_dict(),
                    "solution_code": solution_code,
                    "timeout_seconds": self.timeout_seconds,
                }
            )
        except ProcessSandboxTimeoutError as exc:
            return _python_process_error_result(
                problem,
                solution_code,
                ErrorCategory.TIMEOUT,
                str(exc),
            )
        except SandboxError as exc:
            return _python_process_error_result(
                problem,
                solution_code,
                ErrorCategory.RUNTIME,
                f"Isolated Python worker failed: {exc}",
            )

        if error is not None:
            return _python_process_error_result(
                problem,
                solution_code,
                ErrorCategory.RUNTIME,
                f"Isolated Python worker failed: {error}",
            )
        try:
            return _evaluation_result_from_worker_payload(
                problem,
                solution_code,
                payload,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _python_process_error_result(
                problem,
                solution_code,
                ErrorCategory.RUNTIME,
                f"Isolated Python worker returned an invalid result: {exc}",
            )

    def _evaluate_python_in_process(
        self,
        problem: Problem,
        solution_code: str,
        *,
        sandboxed: bool,
    ) -> EvaluationResult:
        """Child-only implementation, plus the explicit unsafe debug path."""
        result = EvaluationResult(
            problem_id=problem.id,
            language="python",
            solution_code=solution_code,
            visible_total=len(problem.visible_examples),
            hidden_total=len(problem.hidden_tests),
        )

        result.solution_tokens = len(solution_code.split())

        # Parse and compile
        start_time = time.time()
        try:
            compiled = compile(solution_code, "<solution>", "exec")
            result.parsed = True
            result.parse_time_ms = (time.time() - start_time) * 1000
        except SyntaxError as e:
            result.error_category = ErrorCategory.SYNTAX
            result.error_message = str(e)
            return result

        if sandboxed:
            warnings = validate_code_safety(solution_code)
            if warnings:
                result.error_category = ErrorCategory.RUNTIME
                result.error_message = (
                    "Code failed benchmark sandbox validation: " + "; ".join(warnings)
                )
                return result

        # Execute to define function
        try:
            if sandboxed:
                # Use sandboxed execution for Python code
                namespace = self._create_sandboxed_namespace()
            else:
                namespace = {}

            self._call_with_timeout(lambda: exec(compiled, namespace))
            result.type_checked = (
                True  # Python doesn't have static types, consider passed
            )

            if problem.function_name not in namespace:
                result.error_category = ErrorCategory.INCOMPLETE
                result.error_message = f"Function '{problem.function_name}' not found"
                return result

            func = namespace[problem.function_name]
        except BenchmarkTimeoutError as e:
            result.error_category = ErrorCategory.TIMEOUT
            result.error_message = str(e)
            return result
        except Exception as e:
            result.error_category = ErrorCategory.RUNTIME
            result.error_message = str(e)
            return result

        # Run visible examples
        for test in problem.visible_examples:
            test_result = self._run_python_test(func, test, problem)
            self._record_test_result(result, test_result, visible=True)

        # Run hidden tests
        for test in problem.hidden_tests:
            test_result = self._run_python_test(func, test, problem)
            self._record_test_result(result, test_result, visible=False)

        result.total_execution_time_ms = sum(
            t.execution_time_ms for t in result.test_results
        )

        return result

    def _create_sandboxed_namespace(self) -> dict[str, Any]:
        """Create a restricted namespace for benchmark Python execution.

        Uses the shared constants from ``geno.sandbox`` so that the
        benchmark sandbox stays in sync with the production sandbox.
        Benchmark-specific additions (``property``, ``staticmethod``, etc.)
        are layered on top.
        """
        import builtins
        import typing

        typing_proxy = _create_module_proxy(typing)

        # Preserve canonical safe values such as the restricted type() wrapper.
        safe_builtins = dict(SAFE_BUILTINS)
        benchmark_builtin_names = {
            "property",
            "staticmethod",
            "classmethod",
            "super",
            "AttributeError",
            "ZeroDivisionError",
            "NameError",
            # The Python prompts suggest signatures like `dict[str, object]`
            # (see format_python_type); annotations are evaluated at def time,
            # so `object` must resolve inside the sandbox.
            "object",
        }

        for name in benchmark_builtin_names:
            if hasattr(builtins, name):
                safe_builtins[name] = getattr(builtins, name)

        # Block dangerous operations using the shared BLOCKED_BUILTINS set
        def make_blocked(name: str) -> Callable[..., None]:
            def blocked(*args: Any, **kwargs: Any) -> None:
                raise RuntimeError(
                    f"Blocked operation: {name}() is not allowed in sandboxed Python code"
                )

            blocked.__name__ = name
            return blocked

        for name in BLOCKED_BUILTINS:
            safe_builtins[name] = make_blocked(name)

        def safe_import(
            name: str,
            globals: dict[str, Any] | None = None,
            locals: dict[str, Any] | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> Any:
            if level != 0:
                raise RuntimeError(
                    "Relative imports are not allowed in sandboxed Python code"
                )
            if name == "typing":
                return typing_proxy
            raise RuntimeError(
                f"Blocked operation: __import__({name!r}) is not allowed in sandboxed Python code"
            )

        safe_builtins["__import__"] = safe_import

        safe_builtins["getattr"] = safe_getattr
        safe_builtins["hasattr"] = safe_hasattr

        return {
            "__builtins__": safe_builtins,
            "__name__": "__benchmark_sandbox__",
        }

    def _run_python_test(
        self, func: Callable, test: TestCase, problem: Problem
    ) -> TestResult:
        """Run a single test case with Python function."""
        start_time = time.time()
        try:
            args = self._prepare_test_args(test, problem, target="python")

            # Call function
            actual = self._call_with_timeout(lambda: func(*args))

            # Compare result
            passed = self._values_equal(
                actual, test.output, problem.output.type, target="python"
            )

            return TestResult(
                test_case=test,
                passed=passed,
                actual_output=actual,
                error_category=ErrorCategory.NONE
                if passed
                else ErrorCategory.WRONG_ANSWER,
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        except BenchmarkTimeoutError as e:
            return TestResult(
                test_case=test,
                passed=False,
                error_category=ErrorCategory.TIMEOUT,
                error_message=str(e),
                execution_time_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            return TestResult(
                test_case=test,
                passed=False,
                error_category=ErrorCategory.RUNTIME,
                error_message=str(e),
                execution_time_ms=(time.time() - start_time) * 1000,
            )

    def _values_equal(
        self,
        actual: Any,
        expected: Any,
        expected_type: str | None = None,
        *,
        target: str = "python",
    ) -> bool:
        """Check if two values are equal."""
        if _is_unit_type(expected_type):
            return _is_runtime_unit_value(actual) and _is_unit_value(expected)

        option_args = _type_args(expected_type or "", "Option")
        if (
            option_args
            and len(option_args) == 1
            and (expected is None or expected == "None" or expected == {"None": None})
        ):
            return _is_absent_option_value(actual)

        result_args = _type_args(expected_type or "", "Result")
        expected_is_option = option_args is not None and len(option_args) == 1
        expected_is_result = result_args is not None and len(result_args) == 2

        if expected_is_option:
            if not (
                isinstance(expected, dict)
                and len(expected) == 1
                and set(expected) == {"Some"}
            ):
                return False
        if expected_is_result:
            if not (
                isinstance(expected, dict)
                and len(expected) == 1
                and bool(set(expected) & {"Ok", "Err"})
            ):
                return False

        if isinstance(expected, dict) and len(expected) == 1:
            variant, expected_payload = next(iter(expected.items()))
            variant_is_adt = variant in {"Some", "None", "Ok", "Err"}
            if not (expected_is_option or expected_is_result or variant_is_adt):
                return bool(actual == expected)
            if not (expected_is_option or expected_is_result):
                return False
            if expected_is_option and variant != "Some":
                return False
            if expected_is_result and variant not in {"Ok", "Err"}:
                return False
            payload_type = _variant_payload_type(expected_type, variant)
            if payload_type is None:
                return False
            if expected_payload is None and not _allows_none_payload(payload_type):
                return False
            actual_payload = self._extract_variant_payload(
                actual, variant, target=target
            )
            if actual_payload is not _MISSING:
                if (
                    target == "python"
                    and expected_is_option
                    and variant == "Some"
                    and not _requires_explicit_option_payload(payload_type)
                ):
                    return False
                return self._values_equal(
                    actual_payload,
                    expected_payload,
                    payload_type,
                    target=target,
                )
            if (
                variant == "Some" and _requires_explicit_option_payload(payload_type)
            ) or _is_unit_type(payload_type):
                return False
            if expected_is_result:
                return False
            if target != "python":
                return False
            if _is_adt_shaped(actual):
                return False
            if variant in {"Some", "Ok"}:
                return self._values_equal(
                    actual, expected_payload, payload_type, target=target
                )
            if variant == "Err":
                return False

        if expected == "None" and expected_type is None:
            if actual is None:
                return True
            return hasattr(actual, "constructor") and actual.constructor == "None"

        # Handle Geno constructor values
        if hasattr(actual, "constructor"):
            if not _is_option_or_result_type(expected_type):
                return False
            if actual.constructor == "Some" or actual.constructor == "Ok":
                return self._values_equal(
                    actual.fields.get("value"),
                    expected,
                    _variant_payload_type(expected_type, actual.constructor),
                    target=target,
                )
            elif actual.constructor == "Err":
                if isinstance(expected, dict) and "Err" in expected:
                    return self._values_equal(
                        actual.fields.get("error"),
                        expected["Err"],
                        _variant_payload_type(expected_type, "Err"),
                        target=target,
                    )
                return False
            elif actual.constructor == "None":
                return expected is None or (
                    hasattr(expected, "constructor") and expected.constructor == "None"
                )

        # Handle Python dict representations of Ok/Err/Some/None
        if isinstance(actual, dict) and len(actual) == 1:
            if not _is_option_or_result_type(expected_type):
                return False
            if "Some" in actual:
                payload_type = _variant_payload_type(expected_type, "Some")
                if (
                    target == "python"
                    and _type_args(expected_type or "", "Option") is not None
                    and not _requires_explicit_option_payload(payload_type)
                ):
                    return False
                return self._values_equal(
                    actual["Some"],
                    expected,
                    payload_type,
                    target=target,
                )
            if "Ok" in actual:
                return self._values_equal(
                    actual["Ok"],
                    expected,
                    _variant_payload_type(expected_type, "Ok"),
                    target=target,
                )
            if "Err" in actual:
                if isinstance(expected, dict) and "Err" in expected:
                    return self._values_equal(
                        actual["Err"],
                        expected["Err"],
                        _variant_payload_type(expected_type, "Err"),
                        target=target,
                    )
                return False

        if actual is None:
            return expected is None

        # Handle ordered sequence values, including JSON-backed tuple expectations.
        tuple_args = _tuple_type_args(expected_type or "")
        if tuple_args is not None:
            if not isinstance(actual, tuple) or not isinstance(expected, list | tuple):
                return False
            if len(actual) != len(tuple_args) or len(expected) != len(tuple_args):
                return False
            return all(
                self._values_equal(a, e, tuple_args[index], target=target)
                for index, (a, e) in enumerate(zip(actual, expected, strict=True))
            )

        list_args = _type_args(expected_type or "", "List")
        if list_args and len(list_args) == 1:
            if not isinstance(actual, list) or not isinstance(expected, list):
                return False
            if len(actual) != len(expected):
                return False
            return all(
                self._values_equal(a, e, list_args[0], target=target)
                for a, e in zip(actual, expected, strict=True)
            )

        if isinstance(actual, list | tuple) and isinstance(expected, list | tuple):
            if len(actual) != len(expected):
                return False
            if type(actual) is not type(expected):
                return False
            return all(
                self._values_equal(a, e, target=target)
                for a, e in zip(actual, expected, strict=True)
            )

        # Handle floats with tolerance
        if isinstance(actual, float) and isinstance(expected, float):
            return abs(actual - expected) < 1e-9

        return bool(actual == expected)

    def _extract_variant_payload(self, value: Any, variant: str, *, target: str) -> Any:
        """Extract a payload from a matching Geno/Python ADT representation."""
        if hasattr(value, "constructor") and value.constructor == variant:
            field_name = "error" if variant == "Err" else "value"
            return value.fields.get(field_name)
        if target != "geno" and isinstance(value, dict) and set(value) == {variant}:
            return value[variant]
        return _MISSING


def _is_runtime_unit_value(value: Any) -> bool:
    """Return whether an actual runtime value represents Unit."""
    return value is None or value == ()


def _is_absent_option_value(value: Any) -> bool:
    """Return whether an actual value represents an absent Option."""
    if value is None:
        return True
    if hasattr(value, "constructor"):
        return bool(value.constructor == "None")
    return isinstance(value, dict) and value == {"None": None}


def _is_adt_shaped(value: Any) -> bool:
    """Return whether a runtime value looks like a benchmark ADT wrapper."""
    if hasattr(value, "constructor"):
        return value.constructor in {"Some", "None", "Ok", "Err"}
    return (
        isinstance(value, dict)
        and len(value) == 1
        and next(iter(value))
        in {
            "Some",
            "None",
            "Ok",
            "Err",
        }
    )


def _is_option_or_result_type(geno_type: str | None) -> bool:
    """Return whether a type is an Option or Result ADT."""
    type_text = geno_type or ""
    return (
        _type_args(type_text, "Option") is not None
        or _type_args(type_text, "Result") is not None
    )


@dataclass
class BenchmarkSummary:
    """Summary statistics for a benchmark run."""

    total_problems: int = 0
    problems_passed: int = 0

    syntax_errors: int = 0
    type_errors: int = 0
    runtime_errors: int = 0
    wrong_answers: int = 0
    timeouts: int = 0

    total_tests: int = 0
    tests_passed: int = 0

    avg_execution_time_ms: float = 0.0
    avg_tokens: float = 0.0

    results_by_difficulty: dict = field(default_factory=dict)
    results_by_domain: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_problems": self.total_problems,
            "problems_passed": self.problems_passed,
            "pass_rate": self.problems_passed / self.total_problems
            if self.total_problems > 0
            else 0,
            "error_breakdown": {
                "syntax": self.syntax_errors,
                "type": self.type_errors,
                "runtime": self.runtime_errors,
                "wrong_answer": self.wrong_answers,
                "timeout": self.timeouts,
            },
            "tests": {
                "total": self.total_tests,
                "passed": self.tests_passed,
                "pass_rate": self.tests_passed / self.total_tests
                if self.total_tests > 0
                else 0,
            },
            "avg_execution_time_ms": self.avg_execution_time_ms,
            "avg_tokens": self.avg_tokens,
            "by_difficulty": self.results_by_difficulty,
            "by_domain": self.results_by_domain,
        }


def summarize_results(
    results: list[EvaluationResult], problems: list[Problem]
) -> BenchmarkSummary:
    """Generate summary statistics from evaluation results."""
    summary = BenchmarkSummary()

    problem_map = {p.id: p for p in problems}

    summary.total_problems = len(results)

    for result in results:
        problem = problem_map.get(result.problem_id)

        if result.all_passed:
            summary.problems_passed += 1

        # Error categorization
        if result.error_category == ErrorCategory.SYNTAX:
            summary.syntax_errors += 1
        elif result.error_category == ErrorCategory.TYPE:
            summary.type_errors += 1
        elif result.error_category == ErrorCategory.RUNTIME:
            summary.runtime_errors += 1
        elif result.error_category == ErrorCategory.WRONG_ANSWER:
            summary.wrong_answers += 1
        elif result.error_category == ErrorCategory.TIMEOUT:
            summary.timeouts += 1

        # Test counts
        summary.total_tests += result.visible_total + result.hidden_total
        summary.tests_passed += result.visible_passed + result.hidden_passed

        # Difficulty breakdown
        if problem:
            diff = problem.difficulty.value
            if diff not in summary.results_by_difficulty:
                summary.results_by_difficulty[diff] = {"total": 0, "passed": 0}
            summary.results_by_difficulty[diff]["total"] += 1
            if result.all_passed:
                summary.results_by_difficulty[diff]["passed"] += 1

            # Domain breakdown
            dom = problem.domain.value
            if dom not in summary.results_by_domain:
                summary.results_by_domain[dom] = {"total": 0, "passed": 0}
            summary.results_by_domain[dom]["total"] += 1
            if result.all_passed:
                summary.results_by_domain[dom]["passed"] += 1

    # Averages
    if results:
        summary.avg_execution_time_ms = sum(
            r.total_execution_time_ms for r in results
        ) / len(results)
        summary.avg_tokens = sum(r.solution_tokens for r in results) / len(results)

    return summary
