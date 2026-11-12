"""Panic examples: ``example <inputs> panics "message"`` (proposal 0017).

Covers every layer the form passes through: the parser's contextual word and
same-line rules, type checking of the inputs, the shared classifier in
``geno.panic_outcome`` (a limit or a host refusal is never a panic), and each
evaluator: ``geno run`` (``Interpreter._verify_examples``), ``geno test``, and
the source and compiled harness runners. A structural scan keeps every
sandbox-configured limit site on ``RuntimeLimitError``, so the classifier
never needs to read a Geno-facing message.
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from geno import panic_outcome
from geno.api import RunConfig, run
from geno.ast_nodes import (
    ExampleClause,
    ExpressionStatement,
    FunctionDef,
    Identifier,
    ImplDef,
    IntegerLiteral,
    StringLiteral,
    TupleExpr,
)
from geno.diagnostics import ErrorCode
from geno.harness import (
    HarnessResult,
    run_harness_from_compiled,
    run_harness_from_source,
)
from geno.lexer import Lexer
from geno.panic_outcome import (
    CallFailure,
    check_panics_example,
    classify_call_failure,
    classify_compiled_failure,
    compiled_limit,
    panic_message_matches,
)
from geno.parser import ParseError, ParseErrors, Parser
from geno.sandbox import (
    RecursionLimitError,
    SandboxConfig,
    SecurityViolation,
    StepLimitExceeded,
)
from geno.sandbox import TimeoutError as SandboxTimeout
from geno.test_runner import SuiteResult, run_test_suite
from geno.typechecker import TypeChecker
from geno.types import GenoTypeError
from geno.values import GenoRuntimeError, RuntimeLimitError

ROOT = Path(__file__).resolve().parents[2]


def _parse(source: str):
    return Parser(Lexer(textwrap.dedent(source)).tokenize()).parse_program()


def _examples(source: str, name: str | None = None) -> list[ExampleClause]:
    program = _parse(source)
    for defn in program.definitions:
        if isinstance(defn, FunctionDef) and (name is None or defn.name == name):
            return defn.specs.examples
    raise AssertionError(f"no function {name!r}")


def _check(source: str) -> None:
    program = _parse(source)
    TypeChecker().check_program(program)


def _type_errors(source: str) -> list[str]:
    try:
        _check(source)
    except GenoTypeError as exc:
        errors = getattr(exc, "errors", None) or [exc]
        return [getattr(error, "message", str(error)) for error in errors]
    return []


def _parse_error(source: str) -> ParseError:
    with pytest.raises((ParseError, ParseErrors)) as info:
        _parse(source)
    error = info.value
    if isinstance(error, ParseErrors):
        return error.errors[0]
    return error


def _suite(tmp_path: Path, source: str, **config: object) -> SuiteResult:
    path = tmp_path / "case.geno"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    sandbox = SandboxConfig(**config) if config else None  # type: ignore[arg-type]
    return run_test_suite([path], sandbox_config=sandbox)


def _messages(result: SuiteResult) -> list[str]:
    harness = result.file_results[0].harness_result
    assert harness is not None, result.file_results[0].error
    return [violation.message for violation in harness.violations]


def _assert_one_failure(result: SuiteResult, *fragments: str) -> str:
    messages = _messages(result)
    assert len(messages) == 1, messages
    for fragment in fragments:
        assert fragment in messages[0], messages[0]
    assert not result.success
    return messages[0]


_MAIN = "\nfunc main() -> Int\n    return 0\nend func\n"


# =============================================================================
# Parser
# =============================================================================


class TestParsing:
    def test_inputs_then_panics(self) -> None:
        (example,) = _examples(
            """
            func f(a: Int, b: Int) -> Int
                example 1, 0 panics
                return a / b
            end func
            """
        )
        assert example.expects_panic
        assert example.output_expr is None
        assert example.panic_message is None
        assert isinstance(example.input_expr, TupleExpr)
        assert len(example.input_expr.elements) == 2

    def test_message(self) -> None:
        (example,) = _examples(
            """
            func f(a: Int, b: Int) -> Int
                example 1, 0 panics "division"
                return a / b
            end func
            """
        )
        assert example.panic_message == "division"
        assert example.expectation_location is not None
        assert example.end_location is not None
        assert example.end_location.column > example.expectation_location.column

    def test_zero_argument_form(self) -> None:
        (example,) = _examples(
            """
            func usage() -> String
                example () panics "not implemented"
                panic("not implemented")
            end func
            """
        )
        assert example.expects_panic
        assert example.panic_message == "not implemented"

    def test_triple_quoted_message(self) -> None:
        (example,) = _examples(
            '''
            func f(n: Int) -> Int
                example 1 panics """bad "input" here"""
                return n
            end func
            '''
        )
        assert example.panic_message == 'bad "input" here'

    @pytest.mark.parametrize(
        ("inputs", "kind"),
        [
            ("g(1)", "FunctionCall"),
            ("-1", "UnaryOp"),
            ("Some(1)", "ConstructorCall"),
            ("[1, 2] |> length", "Pipeline"),
            ("fn(x: Int) -> x + 1", "LambdaExpr"),
            ("(1, 2)", "TupleExpr"),
        ],
    )
    def test_inputs_of_every_shape(self, inputs: str, kind: str) -> None:
        (example,) = _examples(
            f"""
            func f(x: Int) -> Int
                example {inputs} panics
                return x
            end func
            """
        )
        assert example.expects_panic
        assert type(example.input_expr).__name__ == kind

    def test_impl_method(self) -> None:
        program = _parse(
            """
            type Box = MkBox(n: Int)

            trait Checked
                func check(self: Self) -> Int
            end trait

            impl Checked for Box
                func check(self: Box) -> Int
                    example MkBox(0) panics "zero"
                    return 10 / self.n
                end func
            end impl
            """
        )
        impl = next(d for d in program.definitions if isinstance(d, ImplDef))
        (example,) = impl.methods[0].specs.examples
        assert example.expects_panic
        assert example.panic_message == "zero"

    def test_value_example_is_unchanged(self) -> None:
        (example,) = _examples(
            """
            func f(n: Int) -> Int
                example 2 -> 4
                return n * 2
            end func
            """
        )
        assert not example.expects_panic
        assert isinstance(example.output_expr, IntegerLiteral)
        assert example.panic_message is None
        assert example.arrow_location is not None

    def test_panics_stays_an_identifier(self) -> None:
        source = """
            let panics = 3

            func panics_of(panics: Int) -> Int
                example panics -> 3
                example 3 -> panics
                let total = panics
                return total
            end func

            func main() -> Int
                return panics_of(panics)
            end func
            """
        examples = _examples(source, "panics_of")
        assert [example.expects_panic for example in examples] == [False, False]
        assert isinstance(examples[0].input_expr, Identifier)
        assert isinstance(examples[1].output_expr, Identifier)
        _check(source)
        result = run(textwrap.dedent(source))
        assert result.ok, result.diagnostics
        assert result.value == 3

    def test_a_function_named_panics(self) -> None:
        source = """
            func panics(n: Int) -> Int
                example 1 -> 1
                return n
            end func

            func main() -> Int
                return panics(4)
            end func
            """
        _check(source)
        assert run(textwrap.dedent(source)).value == 4

    def test_a_string_on_the_next_line_is_not_the_message(self) -> None:
        program = _parse(
            """
            func f(n: Int) -> Int
                example 1 panics
                "a statement, not a message"
                return n
            end func
            """
        )
        defn = program.definitions[0]
        assert isinstance(defn, FunctionDef)
        (example,) = defn.specs.examples
        assert example.panic_message is None
        assert isinstance(defn.body[0], ExpressionStatement)
        assert isinstance(defn.body[0].expression, StringLiteral)

    def test_panics_on_the_next_line_is_not_the_form(self) -> None:
        error = _parse_error(
            """
            func f(n: Int) -> Int
                example 1
                panics
                return n
            end func
            """
        )
        assert "Expected '->'" in error.message

    @pytest.mark.parametrize(
        "tail",
        ['panics f"x{1}"', 'panics "m" "n"', "panics 3", 'panics "m" + "n"'],
    )
    def test_a_stray_token_after_panics_is_e201(self, tail: str) -> None:
        error = _parse_error(
            f"""
            func f(n: Int) -> Int
                example 1 {tail}
                return n
            end func
            """
        )
        assert error.error_code is ErrorCode.PARSE_EXPECTED_TOKEN
        assert "after 'panics'" in error.message

    def test_message_is_compared_but_locations_are_not(self) -> None:
        def clause(message: str | None, column: int) -> ExampleClause:
            from geno.tokens import SourceLocation

            location = SourceLocation(1, column)
            return ExampleClause(
                location=location,
                input_expr=IntegerLiteral(location=location, value=1),
                output_expr=None,
                panic_message=message,
                expectation_location=location,
                end_location=location,
            )

        assert clause("a", 1) != clause("b", 1)
        assert clause("a", 1).panic_message != clause("b", 1).panic_message
        same = clause("a", 1)
        moved = clause("a", 1)
        moved.end_location = None
        assert same == moved


# =============================================================================
# Type checking
# =============================================================================


class TestTypeChecking:
    def test_inputs_are_type_checked(self) -> None:
        errors = _type_errors(
            """
            func f(n: Int) -> Int
                example "x" panics
                return n
            end func
            """
            + _MAIN
        )
        assert any("Example input type mismatch" in error for error in errors)

    def test_arity_is_checked(self) -> None:
        errors = _type_errors(
            """
            func f(a: Int, b: Int) -> Int
                example 1, 2, 3 panics
                return a / b
            end func
            """
            + _MAIN
        )
        assert any("Example expects 2..2 inputs, got 3" in e for e in errors)

    def test_input_names_must_resolve(self) -> None:
        errors = _type_errors(
            """
            func f(n: Int) -> Int
                example missing panics
                return n
            end func
            """
            + _MAIN
        )
        assert any("missing" in error for error in errors)

    def test_only_panics_examples_satisfy_the_example_rule(self) -> None:
        assert (
            _type_errors(
                """
                func never(n: Int) -> Int
                    example 1 panics
                    example 2 panics "unreachable"
                    panic("unreachable")
                end func
                """
                + _MAIN
            )
            == []
        )

    def test_a_type_parameter_left_unbound_is_not_bound_checked(self) -> None:
        assert (
            _type_errors(
                """
                func first[T: Ord](xs: List[T]) -> T
                    example [3, 1] -> 3
                    example [] panics
                    return xs[0]
                end func
                """
                + _MAIN
            )
            == []
        )

    def test_a_bound_the_inputs_bind_is_still_checked(self) -> None:
        errors = _type_errors(
            """
            func first[T: Num](xs: List[T]) -> T
                example [1] -> 1
                example ["a"] panics
                return xs[0]
            end func
            """
            + _MAIN
        )
        assert any("does not satisfy the bound 'Num'" in e for e in errors), errors


# =============================================================================
# The classifier
# =============================================================================


class TestClassifier:
    @pytest.mark.parametrize(
        ("exc", "kind", "limit"),
        [
            (StepLimitExceeded("Step limit exceeded (5 steps)"), "limit", "step limit"),
            (SandboxTimeout("Execution timed out"), "limit", "timeout"),
            (RecursionLimitError("deep"), "limit", "recursion depth limit"),
            (RecursionError("host"), "limit", "host recursion limit"),
            (MemoryError(), "limit", "memory limit"),
            (
                RuntimeLimitError("List size exceeds limit", limit="collection size"),
                "limit",
                "collection size limit",
            ),
            (SecurityViolation("blocked"), "refused", None),
            (
                GenoRuntimeError(
                    "Capability denied", error_code=ErrorCode.RUNTIME_CAPABILITY_DENIED
                ),
                "refused",
                None,
            ),
            (
                GenoRuntimeError(
                    "no host", error_code=ErrorCode.RUNTIME_HOST_CALLBACK_MISSING
                ),
                "refused",
                None,
            ),
            (GenoRuntimeError("Division by zero"), "panic", None),
            (GenoRuntimeError("List size exceeds limit (3 > 2)"), "panic", None),
        ],
    )
    def test_kinds(self, exc: BaseException, kind: str, limit: str | None) -> None:
        failure = classify_call_failure(exc)
        assert failure is not None
        assert (failure.kind, failure.limit) == (kind, limit)

    def test_an_internal_error_is_not_classified(self) -> None:
        assert classify_call_failure(TypeError("internal")) is None
        assert classify_call_failure(KeyError("x")) is None

    def test_messages_come_from_the_message_not_str(self) -> None:
        from geno.tokens import SourceLocation

        exc = GenoRuntimeError("boom", SourceLocation(3, 4))
        failure = classify_call_failure(exc)
        assert failure is not None
        assert failure.message == "boom"
        assert "Runtime Error" in str(exc)

    @pytest.mark.parametrize(
        ("message", "limit"),
        [
            ("List size exceeds limit (3 > 2)", "collection size limit"),
            ("join: String size exceeds limit (3 > 2)", "collection size limit"),
            ("string_repeat: result would exceed collection size limit", None),
            ("Integer exceeds maximum size (40000 bits)", "integer size limit"),
            (
                "Exponentiation result too large (estimated 200000 bits)",
                "integer size limit",
            ),
            ("Exponentiation result too large", "numeric range limit"),
            ("Left shift amount too large (100000 bits)", "integer size limit"),
            ("Right shift amount too large (100000 bits)", "integer size limit"),
            ("Output limit exceeded (10 characters)", "output limit"),
            ("Division by zero", None),
            ("parse_int: input string too long (max 1000 characters)", None),
        ],
    )
    def test_compiled_limit_patterns(self, message: str, limit: str | None) -> None:
        if message.startswith("string_repeat"):
            assert compiled_limit(message) == "collection size limit"
            return
        assert compiled_limit(message) == limit
        failure = classify_compiled_failure(RuntimeError(message))
        assert failure is not None
        assert failure.kind == ("panic" if limit is None else "limit")

    def test_a_compiled_panic_is_recognized_by_its_provenance(self) -> None:
        """``panic`` in compiled Python marks its exception, so its text never
        makes it a limit; an unmarked error with the same text is one."""
        from geno import _runtime_support as runtime

        message = "List size exceeds limit (1 > 0)"
        for raise_it in (
            lambda: runtime.panic(message),
            lambda: runtime._geno_panic(message),
            lambda: runtime.option_expect(runtime.None_, message),
        ):
            with pytest.raises(RuntimeError) as raised:
                raise_it()
            assert type(raised.value) is RuntimeError
            assert classify_compiled_failure(raised.value) == CallFailure(
                "panic", message
            )
        limit = classify_compiled_failure(RuntimeError(message))
        assert limit is not None and limit.kind == "limit"

    def test_compiled_arithmetic_and_lookup_errors_are_panics(self) -> None:
        for exc in (ZeroDivisionError("division by zero"), IndexError("index")):
            failure = classify_compiled_failure(exc)
            assert failure is not None and failure.kind == "panic"

    def test_a_compiled_capability_denial_is_a_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recognized by the field ``_require_cap`` sets, never by its text."""
        message = "Capability denied: 'env_get' requires the 'env' capability"
        denial: Any = RuntimeError(message)
        denial._geno_capability_denied = "env"
        assert classify_compiled_failure(denial) == CallFailure("refused", message)
        # A program's own panic with the same text carries no provenance.
        forged = classify_compiled_failure(RuntimeError(message))
        assert forged is not None and forged.kind == "panic"
        # The one refusal switch covers compiled denials too.
        monkeypatch.setattr(panic_outcome, "_refusal_satisfies_panics", lambda: True)
        assert classify_compiled_failure(denial) == CallFailure("panic", message)

    def test_an_isolated_env_policy_denial_is_a_refusal(self) -> None:
        """The isolated lane's env binding tags its denial (proposal 0013);
        that tag, not the text, makes it a refusal."""
        from geno.isolated_host import IsolatedEnv

        with pytest.raises(RuntimeError) as raised:
            IsolatedEnv({"HOME"}, {}, ()).lookup("env_get", "SECRET")
        message = str(raised.value)
        assert classify_compiled_failure(raised.value) == CallFailure(
            "refused", message
        )
        forged = classify_compiled_failure(RuntimeError(message))
        assert forged is not None and forged.kind == "panic"

    def test_matching_ignores_case(self) -> None:
        assert panic_message_matches(
            "Division by zero: cannot compute 1 / 0", "DIVISION"
        )
        assert panic_message_matches("straße", "STRASSE")
        assert not panic_message_matches("Index 5 out of bounds", "division")

    def test_reasons(self) -> None:
        assert check_panics_example(CallFailure("panic", "boom"), None) is None
        assert check_panics_example(CallFailure("panic", "Boom!"), "boom") is None
        assert (
            check_panics_example(None, None, returned="3")
            == "expected a panic, but it returned 3"
        )
        assert check_panics_example(CallFailure("panic", "other"), "division") == (
            'expected a panic containing "division", but it panicked with: other'
        )
        assert check_panics_example(
            CallFailure("limit", "Step limit exceeded", "step limit"), None
        ) == (
            "expected a panic, but the call hit the step limit "
            "(a resource limit is not a panic)"
        )
        assert check_panics_example(
            CallFailure("refused", "Capability denied"), None
        ) == ("expected a panic, but the call was refused: Capability denied")


# =============================================================================
# geno test
# =============================================================================


class TestGenoTest:
    def test_passes(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func safe_div(a: Int, b: Int) -> Int
                example 6, 3 -> 2
                example 1, 0 panics "division"
                example 2, 0 panics "DIVISION BY ZERO"
                example 3, 0 panics
                return a / b
            end func
            """
            + _MAIN,
        )
        assert result.success, _messages(result)
        assert (result.total, result.passed) == (4, 4)

    def test_wrong_message(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func at(xs: List[Int], i: Int) -> Int
                example [1], 5 panics "division"
                return xs[i]
            end func
            """
            + _MAIN,
        )
        _assert_one_failure(
            result,
            'expected a panic containing "division"',
            "but it panicked with: Index 5 out of bounds",
        )

    def test_no_panic(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func double(n: Int) -> Int
                example 2 panics
                return n * 2
            end func
            """
            + _MAIN,
        )
        _assert_one_failure(result, "expected a panic, but it returned 4")

    def test_only_panics_examples(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func usage() -> String
                example () panics "not implemented"
                panic("not implemented")
            end func
            """
            + _MAIN,
        )
        assert result.success, _messages(result)
        assert result.passed == 1

    def test_requires_and_ensures_failures_are_panics(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func positive(n: Int) -> Int
                requires n > 0
                example 1 -> 1
                example 0 panics "precondition failed"
                return n
            end func

            func calls_positive(n: Int) -> Int
                example 1 -> 1
                example -5 panics "positive"
                return positive(n)
            end func

            func wrong_abs(n: Int) -> Int
                ensures result >= 0
                example 3 -> 3
                example -3 panics "postcondition"
                return n
            end func
            """
            + _MAIN,
        )
        assert result.success, _messages(result)
        assert result.passed == 6

    def test_a_panicking_input_is_not_a_pass(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func first(xs: List[Int]) -> Int
                example [1] -> 1
                example [head([])] panics
                return xs[0]
            end func
            """
            + _MAIN,
        )
        message = _assert_one_failure(result, "Runtime error")
        assert "expected a panic" not in message

    def test_step_limit(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func spin(n: Int) -> Int
                example 0 panics
                var x = n
                while true do
                    x = x + 1
                end while
                return x
            end func
            """
            + _MAIN,
            max_steps=2_000,
        )
        _assert_one_failure(
            result,
            "expected a panic, but the call hit the step limit",
            "(a resource limit is not a panic)",
        )

    def test_timeout(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func spin(n: Int) -> Int
                example 0 panics
                var x = n
                while true do
                    x = x + 1
                end while
                return x
            end func
            """
            + _MAIN,
            timeout=0.2,
            max_steps=None,
        )
        _assert_one_failure(result, "the call hit the timeout")

    def test_recursion_depth(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func forever(n: Int) -> Int
                example 0 panics
                return forever(n + 1) + 0
            end func
            """
            + _MAIN,
            max_recursion_depth=40,
        )
        _assert_one_failure(result, "the call hit the recursion depth limit")

    @pytest.mark.parametrize(
        ("body", "limit"),
        [
            ("return length(range(0, 100))", "collection size limit"),
            (
                'var s = "abcd"\n    var i = 0\n    while i < 5 do\n'
                "        s = s + s\n        i = i + 1\n    end while\n"
                "    return length(s)",
                "collection size limit",
            ),
            ("return 2 ** 100000", "integer size limit"),
            ("return 1 << 100000", "integer size limit"),
            ("return 1 >> 100000", "integer size limit"),
            # 16001 bits each: the product passes the pre-checks and trips
            # the result guard.
            ("let x = 2 ** 16000\n    return x * x * x", "integer size limit"),
        ],
        ids=["collection", "string", "pow", "lshift", "rshift", "multiply"],
    )
    def test_size_limits(self, tmp_path: Path, body: str, limit: str) -> None:
        source = (
            "func blow_up(n: Int) -> Int\n    example 0 panics\n    "
            + body
            + "\nend func\n"
            + _MAIN
        )
        result = _suite(tmp_path, source, max_collection_size=50)
        _assert_one_failure(result, f"the call hit the {limit}")

    def test_output_limit(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func shout(n: Int) -> Int
                example 0 panics
                print("a long line of output")
                return n
            end func
            """
            + _MAIN,
            max_output_length=5,
        )
        _assert_one_failure(result, "the call hit the output limit")

    def test_capability_denial_is_a_refusal(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func home(fallback: String) -> String with env
                example "/" panics
                return unwrap_or(env_get(name: "HOME"), fallback)
            end func
            """
            + _MAIN,
        )
        _assert_one_failure(
            result, "expected a panic, but the call was refused: Capability denied"
        )

    def test_the_refusal_switch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flipping the one predicate makes a refusal satisfy the example."""
        monkeypatch.setattr(panic_outcome, "_refusal_satisfies_panics", lambda: True)
        result = _suite(
            tmp_path,
            """
            func home(fallback: String) -> String with env
                example "/" panics "capability denied"
                return unwrap_or(env_get(name: "HOME"), fallback)
            end func
            """
            + _MAIN,
        )
        assert result.success, _messages(result)

    def test_a_fixed_builtin_cap_is_a_panic(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            func parse_or_zero(text: String) -> Int
                example "12" -> 12
                example string_repeat("1", 2000) panics "input string too long"
                return unwrap_or(parse_int(text), 0)
            end func
            """
            + _MAIN,
        )
        assert result.success, _messages(result)

    def test_a_panic_quoting_a_limit_message_is_still_a_panic(
        self, tmp_path: Path
    ) -> None:
        result = _suite(
            tmp_path,
            """
            func fake(n: Int) -> Int
                example 0 panics "List size exceeds limit"
                panic("List size exceeds limit (3 > 2)")
            end func
            """
            + _MAIN,
        )
        assert result.success, _messages(result)

    def test_impl_method(self, tmp_path: Path) -> None:
        result = _suite(
            tmp_path,
            """
            type Box = MkBox(n: Int)

            trait Checked
                func check(self: Self) -> Int
            end trait

            impl Checked for Box
                func check(self: Box) -> Int
                    example MkBox(5) -> 2
                    example MkBox(0) panics "division"
                    example MkBox(1) panics
                    return 10 / self.n
                end func
            end impl
            """
            + _MAIN,
        )
        message = _assert_one_failure(result, "expected a panic, but it returned 10")
        assert result.passed == 2
        assert message


# =============================================================================
# geno run, the embedding API and the hosted server's path
# =============================================================================


class TestGenoRun:
    def test_pass(self) -> None:
        result = run(
            textwrap.dedent(
                """
                func safe_div(a: Int, b: Int) -> Int
                    example 1, 0 panics "division"
                    return a / b
                end func

                func main() -> Int
                    return safe_div(6, 3)
                end func
                """
            )
        )
        assert result.ok, result.diagnostics
        assert result.value == 2

    @pytest.mark.parametrize(
        ("example", "reason"),
        [
            ("example 3, 1 panics", "expected a panic, but it returned 3"),
            (
                'example 1, 0 panics "overflow"',
                'expected a panic containing "overflow"',
            ),
        ],
    )
    def test_failure_is_e409(self, example: str, reason: str) -> None:
        result = run(
            "func safe_div(a: Int, b: Int) -> Int\n"
            f"    {example}\n"
            "    return a / b\nend func\n" + _MAIN
        )
        assert not result.ok
        (diagnostic,) = result.diagnostics
        assert diagnostic.code is ErrorCode.RUNTIME_EXAMPLE_FAILED
        assert "Example verification failed for safe_div" in diagnostic.message
        assert reason in diagnostic.message

    def test_step_limit_keeps_its_code(self) -> None:
        result = run(
            "func spin(n: Int) -> Int\n    example 0 panics\n    var x = n\n"
            "    while true do\n        x = x + 1\n    end while\n    return x\n"
            "end func\n" + _MAIN,
            config=RunConfig(max_steps=100),
        )
        assert [d.code for d in result.diagnostics] == [ErrorCode.SANDBOX_STEP_LIMIT]

    def test_integer_limit_keeps_its_code(self) -> None:
        result = run(
            "func power(a: Int, b: Int) -> Int\n    example 2, 100000 panics\n"
            "    return a ** b\nend func\n" + _MAIN
        )
        (diagnostic,) = result.diagnostics
        assert diagnostic.code is ErrorCode.RUNTIME_UNKNOWN
        assert "Exponentiation result too large" in diagnostic.message

    def test_capability_denial_keeps_its_code(self) -> None:
        result = run(
            'func home(fallback: String) -> String with env\n    example "/" panics\n'
            '    return unwrap_or(env_get(name: "HOME"), fallback)\nend func\n' + _MAIN,
            config=RunConfig(capabilities=set()),
        )
        assert [d.code for d in result.diagnostics] == [
            ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]

    def test_examples_off_ignores_them(self) -> None:
        result = run(
            "func double(n: Int) -> Int\n    example 2 panics\n    return n * 2\n"
            "end func\n" + _MAIN,
            config=RunConfig(check_examples=False),
        )
        assert result.ok


# =============================================================================
# Harness runners
# =============================================================================

_HARNESS_SOURCE = """
func safe_div(a: Int, b: Int) -> Int
    example 6, 3 -> 2
    example 1, 0 panics "division"
    return a / b
end func

func double(n: Int) -> Int
    example 2 panics
    return n * 2
end func

func power(a: Int, b: Int) -> Int
    example 2, 100000 panics
    return a ** b
end func

func blank(text: String) -> String
    example "" panics "EMPTY"
    if text == "" then
        panic("empty")
    end if
    return text
end func
"""


@pytest.mark.parametrize(
    "runner",
    [run_harness_from_source, run_harness_from_compiled],
    ids=["source", "compiled"],
)
def test_harness_runners_agree(runner: Callable[[str], HarnessResult]) -> None:
    result = runner(_HARNESS_SOURCE)
    assert (result.total, result.passed, result.failed) == (5, 3, 2)
    returned, limit = sorted(
        (v.message for v in result.violations), key=lambda m: "limit" in m
    )
    assert "expected a panic, but it returned 4" in returned
    assert "hit the integer size limit" in limit


_HARNESS_REFUSAL_SOURCE = """
func first_match(p: String) -> Option[String]
    example "abc" panics
    return regex_match(p, "abc")
end func

func home(k: String) -> String
    example "HOME" panics
    return unwrap_or(env_get(k), "x")
end func

func shout(k: String) -> Int
    example "HOME" panics
    print(k)
    return 1
end func
"""


@pytest.mark.parametrize(
    "runner",
    [run_harness_from_source, run_harness_from_compiled],
    ids=["source", "compiled"],
)
def test_harness_runners_agree_on_refusals_and_grants(
    runner: Callable[[str], HarnessResult],
) -> None:
    """Both runners grant the same capabilities and refuse alike: a denied
    ``regex``/``env`` call is a refusal, not a panic, and ``print`` runs."""
    result = runner(_HARNESS_REFUSAL_SOURCE)
    assert (result.total, result.passed, result.failed) == (3, 0, 3)
    assert {v.function: v.message for v in result.violations} == {
        "first_match": (
            "Example failed: expected a panic, but the call was refused: "
            "Capability denied: 'regex_match' requires the 'regex' capability"
        ),
        "home": (
            "Example failed: expected a panic, but the call was refused: "
            "Capability denied: 'env_get' requires the 'env' capability"
        ),
        "shout": "Example failed: expected a panic, but it returned 1",
    }


# =============================================================================
# Structural guard: every sandbox-configured limit raises RuntimeLimitError
# =============================================================================

_INTERPRETER_LIMIT_FIELDS = frozenset(
    {
        "max_collection_size",
        "max_integer_bits",
        "max_output_length",
        "_effective_max_collection_size",
    }
)
_COMPILED_LIMIT_FIELDS = frozenset({"_MAX_COLLECTION_SIZE", "_MAX_INTEGER_BITS"})
_LIMIT_PRESERVING_CALLS = frozenset(
    {"_effective_max_collection_size", "_InterpreterCap", "int", "min", "max"}
)
_BROAD_RUNTIME_ERRORS = frozenset({"RuntimeError", "GenoRuntimeError"})


def _own_nodes(fn: ast.FunctionDef) -> Iterator[ast.AST]:
    """The nodes of *fn*, not descending into nested functions or lambdas."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _derived(node: ast.AST, names: set[str], attrs: set[str]) -> bool:
    """Whether *node* computes a limit (not data computed under one)."""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Attribute):
        return node.attr in attrs
    if isinstance(node, ast.Call):
        func = node.func
        if not (isinstance(func, ast.Name) and func.id in _LIMIT_PRESERVING_CALLS):
            return False
        return any(_derived(arg, names, attrs) for arg in node.args)
    if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp)):
        return any(
            _derived(child, names, attrs) for child in ast.iter_child_nodes(node)
        )
    return False


def _limit_guarded_raises(
    path: Path, fields: frozenset[str]
) -> list[tuple[str, ast.Raise, str]]:
    """Every ``raise`` a limit guards, as ``(function, node, source)``.

    A raise is guarded when it sits in the body of an ``if`` whose test reads a
    limit, or in a handler (for ``RuntimeError``/``GenoRuntimeError``) of a
    ``try`` that passes a limit to a call. A limit is one of *fields*, a local
    assigned from one, an attribute assigned from one, or a parameter some
    call in the module passes one to (to a fixpoint), which is how helpers
    such as ``_check_collection_size(kind, size, limit)`` are found.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    funcs: list[tuple[str | None, ast.FunctionDef]] = []
    for top in tree.body:
        if isinstance(top, ast.FunctionDef):
            funcs.append((None, top))
        elif isinstance(top, ast.ClassDef):
            funcs.extend(
                (top.name, item)
                for item in top.body
                if isinstance(item, ast.FunctionDef)
            )
    by_name = {fn.name: fn for owner, fn in funcs if owner is None}
    methods = {(owner, fn.name): fn for owner, fn in funcs if owner is not None}
    owners = {id(fn): owner for owner, fn in funcs}
    params: dict[int, set[str]] = {id(fn): set() for _owner, fn in funcs}
    attrs: set[str] = set(fields)

    def local_names(fn: ast.FunctionDef) -> set[str]:
        names = set(fields) | params[id(fn)]
        grew = True
        while grew:
            grew = False
            for node in _own_nodes(fn):
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value:
                    if not _derived(node.value, names, attrs):
                        continue
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id not in names:
                            names.add(target.id)
                            grew = True
        return names

    def callee(
        fn: ast.FunctionDef, call: ast.Call
    ) -> tuple[ast.FunctionDef | None, int]:
        func = call.func
        if isinstance(func, ast.Name):
            if func.id in by_name:
                return by_name[func.id], 0
            if (func.id, "__init__") in methods:
                return methods[(func.id, "__init__")], 1
        owner = owners[id(fn)]
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and owner is not None
            and (owner, func.attr) in methods
        ):
            return methods[(owner, func.attr)], 1
        return None, 0

    grew = True
    while grew:
        grew = False
        for _owner, fn in funcs:
            names = local_names(fn)
            for node in _own_nodes(fn):
                if isinstance(node, ast.Assign) and _derived(node.value, names, attrs):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                            and target.attr not in attrs
                        ):
                            attrs.add(target.attr)
                            grew = True
                if not isinstance(node, ast.Call):
                    continue
                target_fn, offset = callee(fn, node)
                if target_fn is None:
                    continue
                arg_names = [a.arg for a in target_fn.args.args]
                passed = [
                    (arg_names[i + offset], arg)
                    for i, arg in enumerate(node.args)
                    if i + offset < len(arg_names)
                ]
                passed += [(kw.arg, kw.value) for kw in node.keywords if kw.arg]
                for param, arg in passed:
                    if param not in params[id(target_fn)] and _derived(
                        arg, names, attrs
                    ):
                        params[id(target_fn)].add(param)
                        grew = True

    found: list[tuple[str, ast.Raise, str]] = []
    for owner, fn in funcs:
        names = local_names(fn)
        label = f"{owner}.{fn.name}" if owner else fn.name
        for node in _own_nodes(fn):
            bodies: list[list[ast.stmt]] = []
            if isinstance(node, ast.If) and _derived(node.test, names, attrs):
                bodies.append(node.body)
            elif isinstance(node, ast.Try) and any(
                isinstance(sub, ast.Call)
                and any(
                    _derived(arg, names, attrs)
                    for arg in [*sub.args, *(kw.value for kw in sub.keywords)]
                )
                for stmt in node.body
                for sub in ast.walk(stmt)
            ):
                for handler in node.handlers:
                    caught = (
                        {
                            n.id
                            for n in ast.walk(handler.type)
                            if isinstance(n, ast.Name)
                        }
                        if handler.type is not None
                        else set(_BROAD_RUNTIME_ERRORS)
                    )
                    if caught & (_BROAD_RUNTIME_ERRORS | {"RuntimeLimitError"}):
                        bodies.append(handler.body)
            for body in bodies:
                for stmt in body:
                    for sub in ast.walk(stmt):
                        if isinstance(sub, ast.Raise):
                            segment = ast.get_source_segment(source, sub) or ""
                            found.append((label, sub, " ".join(segment.split())))
    return found


def _raised_name(node: ast.Raise) -> str | None:
    exc = node.exc
    if exc is None:
        return None
    if isinstance(exc, ast.Call):
        exc = exc.func
    return exc.id if isinstance(exc, ast.Name) else None


@pytest.mark.parametrize("module", ["geno/interpreter.py", "geno/builtins.py"])
def test_every_sandbox_limit_raises_runtime_limit_error(module: str) -> None:
    guarded = _limit_guarded_raises(ROOT / module, _INTERPRETER_LIMIT_FIELDS)
    assert guarded, "the scan found no limit sites; it has stopped working"
    offenders = [
        f"{module}:{node.lineno} {label}: {text}"
        for label, node, text in guarded
        if node.exc is not None and _raised_name(node) != "RuntimeLimitError"
    ]
    assert offenders == []


def test_the_scan_covers_the_known_limit_helpers() -> None:
    labels = {
        label
        for module in ("geno/interpreter.py", "geno/builtins.py")
        for label, _node, _text in _limit_guarded_raises(
            ROOT / module, _INTERPRETER_LIMIT_FIELDS
        )
    }
    assert {
        "_check_collection_size",
        "_check_result_string_size",
        "_StringifyWriter.append",
        "Interpreter._check_collection_size",
        "Interpreter._check_integer_bits",
        "Interpreter._builtin_print",
        "_binop_pow",
        "_check_shift_amount",
    } <= labels


def _sample_message(node: ast.Raise) -> str | None:
    """The raise's message with every interpolated value replaced by 1."""
    call = node.exc
    if not isinstance(call, ast.Call) or not call.args:
        return None
    message = call.args[0]
    if isinstance(message, ast.Constant) and isinstance(message.value, str):
        return message.value
    if isinstance(message, ast.JoinedStr):
        return "".join(
            str(part.value) if isinstance(part, ast.Constant) else "1"
            for part in message.values
        )
    return None


def test_compiled_limit_messages_match_the_compiled_patterns() -> None:
    guarded = _limit_guarded_raises(
        ROOT / "geno/_runtime_support.py", _COMPILED_LIMIT_FIELDS
    )
    assert guarded
    unmatched = []
    for label, node, text in guarded:
        sample = _sample_message(node)
        if sample is None or compiled_limit(sample) is None:
            unmatched.append(f"{node.lineno} {label}: {text}")
    assert unmatched == []
    # The sandbox's own print guard for compiled programs.
    sandbox = (ROOT / "geno/sandbox.py").read_text(encoding="utf-8")
    assert 'f"Output limit exceeded ({max_output} characters)"' in sandbox
    assert compiled_limit("Output limit exceeded (100 characters)") == "output limit"


def test_limit_errors_keep_their_messages_and_codes() -> None:
    """The type changed; the published surface did not."""
    exc = RuntimeLimitError("List size exceeds limit (3 > 2)", limit="collection size")
    assert isinstance(exc, GenoRuntimeError)
    assert exc.error_code is None  # reported as E499, as before
    assert str(exc) == "Runtime Error: List size exceeds limit (3 > 2)"


# =============================================================================
# Runtime reports stay out of message matching (proposal 0018)
# =============================================================================

_LOCATED_PANIC = """
func check(n: Int) -> Int
    example 1 -> 1
    example 9 panics "{expected}"
    if n > 5 then
        panic("value too large")
    end if
    return n
end func
"""

# Text that appears only in a report's location and stack, never in the
# panic's own message.
_REPORT_ONLY_TEXT = ["case.geno", "6:9", "in check", "Stack trace", "Runtime Error"]


class TestReportsStayOutOfMatching:
    def test_the_panic_message_still_matches(self, tmp_path: Path) -> None:
        result = _suite(tmp_path, _LOCATED_PANIC.replace("{expected}", "too large"))
        assert result.success, _messages(result)

    @pytest.mark.parametrize("expected", _REPORT_ONLY_TEXT)
    def test_geno_test_never_matches_report_text(
        self, tmp_path: Path, expected: str
    ) -> None:
        result = _suite(tmp_path, _LOCATED_PANIC.replace("{expected}", expected))
        _assert_one_failure(result, "value too large", expected)

    @pytest.mark.parametrize("expected", _REPORT_ONLY_TEXT)
    def test_geno_run_never_matches_report_text(self, expected: str) -> None:
        source = textwrap.dedent(_LOCATED_PANIC.replace("{expected}", expected))
        result = run(source + _MAIN, "case.geno")

        (diagnostic,) = result.diagnostics
        assert diagnostic.code is ErrorCode.RUNTIME_EXAMPLE_FAILED
        assert "value too large" in diagnostic.message

    def test_a_located_panic_keeps_its_bare_message(self) -> None:
        source = textwrap.dedent(
            """
            func main() -> Int
                panic("value too large")
                return 0
            end func
            """
        )
        with pytest.raises(GenoRuntimeError) as exc_info:
            from geno.interpreter import Interpreter

            tokens = Lexer(source, "case.geno").tokenize()
            Interpreter().run(Parser(tokens, source).parse_program())

        error = exc_info.value
        assert error.location is not None
        assert error.geno_stack is not None
        assert panic_outcome.classify_call_failure(error) == panic_outcome.CallFailure(
            "panic", "value too large"
        )

    def test_property_failures_report_the_bare_message(self, tmp_path: Path) -> None:
        from geno.property_check import PropertyConfig

        path = tmp_path / "case.geno"
        path.write_text(
            textwrap.dedent(
                """
                func inc(n: Int) -> Int
                    ensures result > n
                    example 1 -> 2
                    if n > 3 then
                        panic("too big")
                    end if
                    return n + 1
                end func
                """
            ),
            encoding="utf-8",
        )

        suite = run_test_suite([path], properties=PropertyConfig())

        (property_result,) = suite.property_results
        assert property_result.status == "failed"
        assert property_result.failure is not None
        assert property_result.failure.message == "too big"
