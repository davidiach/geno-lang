"""
Test runner for ``geno test``.

Discovers .geno files, runs example-based tests via the interpreter's
own example verification, and aggregates results.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List, Mapping

from .ast_nodes import ExampleClause, FunctionDef, TestBlock
from .diagnostics import GenoStack, format_labeled_error, location_to_dict
from .execution_limits import (
    DEFAULT_TEST_MAX_STEPS,
    DEFAULT_TEST_TIMEOUT,
)
from .harness import (
    FunctionHarness,
    HarnessResult,
    SpecViolation,
    example_call_args,
    extract_harnesses,
)
from .project_resolution import describe_project_resolution_error
from .sandbox import SandboxConfig
from .tokens import SourceLocation

if TYPE_CHECKING:
    from .ast_nodes import Program
    from .interpreter import Interpreter

    # Imported only for annotations: ``geno test`` without ``--properties``
    # never loads the property-check engine (proposal 0017).
    from .property_check import PropertyConfig, PropertyReport, PropertyResult
    from .typechecker import TypeChecker

logger = logging.getLogger(__name__)


def default_test_sandbox_config(
    *,
    timeout: float | None = DEFAULT_TEST_TIMEOUT,
    max_steps: int | None = DEFAULT_TEST_MAX_STEPS,
) -> SandboxConfig:
    return SandboxConfig(timeout=timeout, max_steps=max_steps)


def _geno_facing_test_errors() -> tuple[type[BaseException], ...]:
    """Exception types that represent a user-program/spec failure (not an
    internal toolchain bug) when they escape test execution."""
    from .sandbox import SandboxError
    from .target_profile import ManifestTargetError
    from .types import GenoTypeError
    from .values import GenoRuntimeError

    return (
        GenoRuntimeError,
        SandboxError,
        # Type errors and manifest/target errors are ordinary user-facing
        # failures on the single-file `geno test` path (the project-suite path
        # already catches TypeError separately); without them here they were
        # misreported as internal defects and spammed to the log (M-28 fix
        # follow-up). GenoTypeError is the base of TypeErrors.
        GenoTypeError,
        ManifestTargetError,
        ZeroDivisionError,
        RecursionError,
        OverflowError,
    )


def _test_error_detail(exc: Exception) -> str:
    """Describe *exc* for a test report.

    A Geno-facing error (user program error, contract violation, sandbox
    limit) is rendered exactly as before. Any other type is an internal
    interpreter/toolchain defect that previously masqueraded as a user test
    failure with only ``str(e)``: log it with its traceback and prefix the
    exception type name so it is not misattributed to the user's code (M-28).
    """
    if isinstance(exc, _geno_facing_test_errors()):
        return str(exc)
    logger.exception("Internal error while running tests: %s", exc)
    return f"{type(exc).__name__}: {exc}"


def _runtime_failure_detail(exc: Exception) -> str:
    """The message a ``Runtime error:`` violation shows for *exc*.

    A Geno runtime error shows its own message: its ``str()`` would repeat
    the location and the ``Runtime Error:`` label (proposal 0018), which the
    violation's ``location`` and ``stack`` now carry.
    """
    from .values import GenoRuntimeError

    if isinstance(exc, GenoRuntimeError):
        return exc.message
    return _test_error_detail(exc)


def _unlocated_text(exc: BaseException) -> str:
    """``str(exc)`` without the location a Geno runtime error puts first.

    Proposal 0018 locates every runtime failure, so ``str()`` of a Geno
    runtime error now starts with ``<file:line:col>:`` (a host path) where
    0.6 often had none. A ``geno test`` message never shows it: a violation
    carries it as ``location`` and ``stack`` instead.
    """
    from .values import GenoRuntimeError

    if isinstance(exc, GenoRuntimeError):
        return f"Runtime Error: {exc.message}"
    return str(exc)


def _test_block_failure_detail(exc: Exception) -> str:
    """The message of a failed ``test`` block or file load for *exc*."""
    from .values import GenoRuntimeError

    if isinstance(exc, GenoRuntimeError):
        return _unlocated_text(exc)
    return _test_error_detail(exc)


def _failure_location(exc: BaseException) -> SourceLocation | None:
    location = getattr(exc, "location", None)
    return location if isinstance(location, SourceLocation) else None


def _failure_stack(exc: BaseException) -> GenoStack | None:
    from .runtime_report import stack_of

    return stack_of(exc)


def _classify_runtime_failure(exc: BaseException) -> str:
    """Map a failure back to the spec kind it violated.

    The interpreter's ``_check_requires`` / ``_check_ensures`` helpers raise
    ``ContractViolationError`` with a structured ``clause`` field, so the
    test report can show ``requires`` / ``ensures`` violations separately
    from generic runtime errors — F-0021 in #662, which otherwise labelled
    every runtime failure as a plain ``example`` failure. The field, not the
    message, decides (proposal 0017): a user ``panic`` whose text mentions a
    precondition stays an ``example`` failure.
    """
    from .values import ContractViolationError

    if isinstance(exc, ContractViolationError) and exc.clause is not None:
        return exc.clause
    return "example"


def _resolve_harness_callable(interp, harness: FunctionHarness):  # type: ignore[type-arg]
    """Return the callable target for *harness*, or ``None`` if unresolved.

    For ordinary top-level functions this is the closure bound in the
    interpreter's global env.  For methods inside an ``impl`` block it is
    the closure stored under ``trait_impls[(trait, target)][method]``.
    """
    if harness.impl_trait is not None and harness.impl_target is not None:
        impl_closures = interp.trait_impls.get(
            (harness.impl_trait, harness.impl_target)
        )
        if impl_closures is None:
            return None
        return impl_closures.get(harness.base_name)
    return interp.global_env.lookup(harness.name)


def _run_examples_and_tests(
    interp: Interpreter,
    harnesses: list[FunctionHarness],
    test_blocks: list[TestBlock],
    result: HarnessResult,
) -> None:
    """Run examples and test blocks with the stack the call depth needs."""

    def run() -> None:
        _run_examples(interp, harnesses, result)
        for test_block in test_blocks:
            result.total += 1
            try:
                test_env = interp.global_env.child()
                with interp._execution_deadline(interp.sandbox_config.timeout):
                    for stmt in test_block.body:
                        interp.exec_stmt(stmt, test_env)
                result.passed += 1
            except Exception as e:
                result.failed += 1
                interp.mark_test_origin(e)
                result.violations.append(
                    SpecViolation(
                        kind="test",
                        function=test_block.name,
                        message=_test_block_failure_detail(e),
                        location=_failure_location(e),
                        stack=_failure_stack(e),
                    )
                )

    interp.run_with_recursion_room(run)


def _run_examples(
    interp,  # type: ignore[type-arg]
    harnesses: list[FunctionHarness],
    result: HarnessResult,
) -> None:
    """Execute examples for each *harness* and record results into *result*.

    Impl-block harnesses are dispatched through the interpreter's
    ``trait_impls`` table (F-0022): iterating ``interp.functions`` would
    silently skip them since impl methods are stored separately from
    top-level function closures.
    """
    from .interpreter import _explain_rejection_example
    from .values import _UNBOUND, ContractViolationError

    for harness in harnesses:
        func = _resolve_harness_callable(interp, harness)
        if func is None or func is _UNBOUND:
            # Missing target — record one violation per example so the
            # harness totals stay aligned with the spec surface.
            for _example in harness.examples:
                result.total += 1
                result.failed += 1
                result.violations.append(
                    SpecViolation(
                        kind="error",
                        function=harness.name,
                        message=(
                            f"Function '{harness.name}' not found in interpreter "
                            f"(impl={harness.impl_trait}/{harness.impl_target})"
                            if harness.impl_trait is not None
                            else f"Function '{harness.name}' not found"
                        ),
                    )
                )
            continue

        for example in harness.examples:
            result.total += 1
            try:
                if example.output_expr is None:
                    reason = _panics_example_reason(interp, harness, func, example)
                    if reason is None:
                        result.passed += 1
                    else:
                        result.failed += 1
                        result.violations.append(
                            SpecViolation(
                                kind="example",
                                function=harness.name,
                                message=reason,
                            )
                        )
                    continue
                with interp._execution_deadline(interp.sandbox_config.timeout):
                    input_val = interp.eval_expr(example.input_expr, interp.global_env)
                    expected = interp.eval_expr(example.output_expr, interp.global_env)

                    call_args = example_call_args(
                        input_val,
                        param_count=len(harness.param_names),
                        required_count=sum(
                            1
                            for param in getattr(func, "params", [])
                            if param.default_value is None
                        ),
                    )
                    try:
                        actual = interp._call_function(func, call_args)
                    except ContractViolationError as violation:
                        # This path runs examples itself rather than going
                        # through ``Interpreter._verify_examples``, so the
                        # requires-rejects-its-own-example explanation (#73)
                        # has to be applied here too or ``geno test`` reports
                        # the bare "evaluated to false" that `geno run` no
                        # longer does.
                        explained = _explain_rejection_example(
                            getattr(func, "name", None) or harness.base_name,
                            expected,
                            violation,
                        )
                        if explained is None:
                            raise
                        raise violation.with_message(
                            explained, example.location
                        ) from None

                if interp._values_equal(actual, expected, approximate_floats=True):
                    result.passed += 1
                else:
                    result.failed += 1
                    result.violations.append(
                        SpecViolation(
                            kind="example",
                            function=harness.name,
                            message=(
                                f"expected {interp._format_value(expected)}, "
                                f"got {interp._format_value(actual)}"
                            ),
                            expected=expected,
                            actual=actual,
                        )
                    )
            except Exception as e:
                result.failed += 1
                interp.mark_example_origin(e, example.location)
                result.violations.append(
                    SpecViolation(
                        kind=_classify_runtime_failure(e),
                        function=harness.name,
                        message=f"Runtime error: {_runtime_failure_detail(e)}",
                        location=_failure_location(e),
                        stack=_failure_stack(e),
                    )
                )


def _panics_example_reason(
    interp: Interpreter,
    harness: FunctionHarness,
    func: object,
    example: ExampleClause,
) -> str | None:
    """Run ``example <inputs> panics "m"``; ``None`` when it passes.

    The inputs are evaluated first, and an error there propagates to the
    caller's handler: an input that panics is a broken example, not one the
    function satisfied. Every way the call itself can fail to satisfy the
    example (it returned, panicked with another message, hit a resource
    limit, or was refused by the host) comes back as the reason.
    """
    from .panic_outcome import evaluate_panics_call

    timeout = interp.sandbox_config.timeout
    with interp._execution_deadline(timeout):
        input_val = interp.eval_expr(example.input_expr, interp.global_env)
    call_args = example_call_args(
        input_val,
        param_count=len(harness.param_names),
        required_count=sum(
            1 for param in getattr(func, "params", []) if param.default_value is None
        ),
    )

    def call() -> object:
        with interp._execution_deadline(timeout):
            return interp._call_function(func, call_args)

    return evaluate_panics_call(
        call, example.panic_message, render=interp._format_value
    )


@dataclass
class FileTestResult:
    """Result of testing a single file.

    ``properties`` holds the file's property-check results in source order
    when the suite ran with property checks (``geno test --properties``), and
    is ``None`` otherwise. ``elapsed_ms`` covers the examples and test blocks
    only.
    """

    path: str
    harness_result: HarnessResult | None = None
    error: str | None = None
    elapsed_ms: float = 0.0
    properties: list[PropertyResult] | None = None


@dataclass
class SuiteResult:
    """Aggregated result across all files.

    ``property_config`` is set when the suite ran property checks. Property
    results are kept out of ``total``, ``passed`` and ``failed`` (those count
    examples and test blocks), but a failed property makes ``success`` false.
    """

    file_results: List[FileTestResult] = field(default_factory=list)
    property_config: PropertyConfig | None = None

    @property
    def property_results(self) -> list[PropertyResult]:
        """Every file's property results, in file order then source order."""
        return [
            result
            for fr in self.file_results
            if fr.properties is not None
            for result in fr.properties
        ]

    @property
    def property_failures(self) -> int:
        return sum(1 for result in self.property_results if result.status == "failed")

    def property_report(self) -> PropertyReport | None:
        """The property results with their seed and trial count, if any ran."""
        if self.property_config is None:
            return None
        from .property_check import PropertyReport

        return PropertyReport(
            seed=self.property_config.seed,
            trials=self.property_config.trials,
            results=self.property_results,
        )

    @property
    def total(self) -> int:
        return sum(
            fr.harness_result.total
            for fr in self.file_results
            if fr.harness_result is not None
        )

    @property
    def passed(self) -> int:
        return sum(
            fr.harness_result.passed
            for fr in self.file_results
            if fr.harness_result is not None
        )

    @property
    def failed(self) -> int:
        return sum(
            fr.harness_result.failed
            for fr in self.file_results
            if fr.harness_result is not None
        )

    @property
    def errors(self) -> int:
        return sum(1 for fr in self.file_results if fr.error is not None)

    @property
    def untested(self) -> int:
        return sum(
            len(fr.harness_result.untested)
            for fr in self.file_results
            if fr.harness_result is not None
        )

    @property
    def success(self) -> bool:
        return self.failed == 0 and self.errors == 0 and self.property_failures == 0

    def to_dict(self) -> dict:
        """The ``geno test --json`` payload.

        The ``properties`` key appears only when the suite ran property
        checks, so the payload of a plain run is unchanged.
        """
        files = []
        for fr in self.file_results:
            entry: dict = {"path": fr.path}
            if fr.error:
                entry["error"] = fr.error
            elif fr.harness_result:
                hr = fr.harness_result
                entry["total"] = hr.total
                entry["passed"] = hr.passed
                entry["failed"] = hr.failed
                entry["untested"] = [
                    {
                        "function": name,
                        "reason": reason,
                    }
                    for name, reason in hr.untested
                ]
                entry["violations"] = [_violation_dict(v) for v in hr.violations]
            entry["elapsed_ms"] = round(fr.elapsed_ms, 2)
            files.append(entry)
        payload: dict = {
            "files": files,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "untested": self.untested,
            "success": self.success,
        }
        report = self.property_report()
        if report is not None:
            payload["properties"] = report.to_dict()
        return payload


def _violation_dict(violation: SpecViolation) -> dict:
    """One ``geno test --json`` violation.

    ``location``, ``stack`` and ``stack_omitted`` appear only for a runtime
    failure that has them (proposal 0018), so other entries are unchanged.
    A standard-library file shows as ``std/<Name>.geno``, as in text reports.
    """
    entry: dict = {
        "kind": violation.kind,
        "function": violation.function,
        "message": violation.message,
    }
    if violation.location is None and violation.stack is None:
        return entry
    from .runtime_report import display_location, display_stack

    location = display_location(violation.location)
    if location is not None:
        entry["location"] = location_to_dict(location)
    stack = display_stack(violation.stack)
    if stack is not None:
        entry["stack"] = stack.to_dicts()
        if stack.omitted > 0:
            entry["stack_omitted"] = stack.omitted
    return entry


def discover_files(target: Path) -> List[Path]:
    """Find .geno files in a path. Returns a single file or recursive directory listing."""
    if target.is_file():
        if target.suffix in {".geno", ".gen"}:
            return [target]
        return []
    return sorted(target.rglob("*.geno"))


def _suite_property_config(
    properties: PropertyConfig | None, filter_pattern: str | None
) -> PropertyConfig | None:
    """*properties* selecting functions with the suite's own ``--filter``.

    Examples, test blocks and property checks always select by the same
    pattern, so ``--filter f`` narrows all three alike.
    """
    if properties is None:
        return None
    return dataclasses.replace(properties, filter_pattern=filter_pattern)


def _check_properties(
    program: Program,
    checker: TypeChecker,
    *,
    module: str,
    path: str,
    config: PropertyConfig,
    sandbox_config: SandboxConfig,
    modules: Mapping[str, Program] | None,
    source: str | None,
) -> list[PropertyResult]:
    """Property-check one module after its examples and test blocks ran.

    *module* is the logical module name (the project module, or the file's
    module name outside a project), never a path, so a seed reproduces from
    any working directory. The checks run in an interpreter of their own, so
    they never use the step budget or output the examples ran on.
    """
    from .property_check import check_module_properties

    return check_module_properties(
        program,
        checker,
        module=module,
        path=path,
        config=config,
        sandbox_config=sandbox_config,
        modules=modules,
        source=source,
    )


def run_test_suite(
    files: List[Path],
    filter_pattern: str | None = None,
    target: str | None = None,
    sandbox_config: SandboxConfig | None = None,
    properties: PropertyConfig | None = None,
) -> SuiteResult:
    """Run tests on a list of files and return aggregated results.

    With *properties*, each file's eligible functions are also property
    checked after its examples and test blocks (``geno test --properties``).
    """
    property_config = _suite_property_config(properties, filter_pattern)
    suite = SuiteResult(property_config=property_config)
    sandbox_config = sandbox_config or default_test_sandbox_config()

    for filepath in files:
        result = _test_file(
            filepath,
            filter_pattern,
            target=target,
            sandbox_config=sandbox_config,
            properties=property_config,
        )
        suite.file_results.append(result)

    return suite


def run_project_test_suite(
    project_path: Path,
    filter_pattern: str | None = None,
    target: str | None = None,
    sandbox_config: SandboxConfig | None = None,
    properties: PropertyConfig | None = None,
) -> SuiteResult:
    """Run tests using ProjectGraph.

    Accepts a directory (with or without geno.toml) or a single .geno file.
    Each module is tested in its own interpreter with its declared imports,
    so private bindings and identically named helpers retain module scope.
    Examples from ALL modules (not just the entrypoint) are tested.

    With *properties*, each of the project's own modules is also property
    checked, with that module's checker, after its examples and test blocks.
    Standard-library and dependency-package modules are not: their examples
    run, but their properties are not the project's to fix.
    """
    from .dependency_graph import DependencyGraphError
    from .interpreter import Interpreter
    from .project_graph import ProjectGraphError
    from .project_resolution import ProjectResolutionError, resolve_project_context
    from .sandbox import SandboxError
    from .stdlib_scope import is_stdlib_program
    from .target_profile import TargetProfile, resolve_manifest_targets
    from .target_validation import (
        TargetValidationError,
        validate_default_run_lowering,
    )
    from .typechecker import TypeChecker, TypeError
    from .values import GenoRuntimeError

    sandbox_config = sandbox_config or default_test_sandbox_config()
    property_config = _suite_property_config(properties, filter_pattern)

    def _project_error_result(message: str) -> SuiteResult:
        suite = SuiteResult(property_config=property_config)
        suite.file_results.append(FileTestResult(path=str(project_path), error=message))
        return suite

    def _project_error_message(exc: Exception) -> str:
        return f"Project Error: {describe_project_resolution_error(exc)}"

    runtime_load_errors = (
        GenoRuntimeError,
        SandboxError,
        ZeroDivisionError,
        RecursionError,
        OverflowError,
    )

    try:
        context = resolve_project_context(project_path)
    except (
        FileNotFoundError,
        DependencyGraphError,
        ProjectGraphError,
        ProjectResolutionError,
    ) as e:
        return _project_error_result(_project_error_message(e))
    pg = context.project
    dg = context.dependency_graph
    target_names: list[str] = (
        [target] if target is not None else resolve_manifest_targets(pg.root)
    )
    check_targets: list[str | None] = list(target_names) if target_names else [None]

    # Type check all modules for each declared target. Property checks read
    # each module's resolved types from the last target's module checkers.
    module_checkers: dict[str, TypeChecker] = {}
    try:
        for target_name in check_targets:
            target_profile = (
                TargetProfile.load(target_name) if target_name is not None else None
            )
            checker = TypeChecker(target_profile=target_profile)
            checker.check_project_graph(
                dg, retain_module_checkers=property_config is not None
            )
            module_checkers = checker.module_checkers
            if target_profile is None:
                # A target-less suite is still run by the default `geno run`,
                # which lowers through the Python backend. Validate that here so
                # examples cannot pass for a program that cannot run (#70).
                validate_default_run_lowering(dg)
    except ValueError as e:
        return _project_error_result(f"Manifest Error: {e}")
    except TypeError as e:
        return _project_error_result("\n".join(format_labeled_error("Type Error", e)))
    except TargetValidationError as e:
        return _project_error_result(f"Compile Error: {e.backend_message}")

    parsed_modules = {name: dg.parsed[name] for name in dg.sorted_modules}
    all_programs = []
    suite_steps = 0
    suite_output_length = 0
    for mod_name in dg.sorted_modules:
        program = dg.parsed[mod_name]
        # Loading every module into one global environment lets later modules
        # overwrite the private constants and helpers that earlier tests use.
        # Load the module under test as the entry module instead; its imports
        # still resolve through their own lexical environments. Carry counters
        # forward so isolation does not grant each module a fresh suite budget.
        interp = Interpreter(check_examples=False, sandbox_config=sandbox_config)
        interp.steps = suite_steps
        interp._output_length = suite_output_length
        try:
            interp.run(program, modules=parsed_modules, execute_main=False)
        except runtime_load_errors as e:
            return _project_error_result(f"Runtime Error: {_unlocated_text(e)}")
        suite_steps = interp.steps
        suite_output_length = interp._output_length
        all_programs.append((mod_name, program, interp))

    import time

    # Load all contexts before testing, as before: exhaustion during a test
    # should fail subsequent tests rather than turn module loading into a
    # project error that discards the results already collected.
    suite = SuiteResult(property_config=property_config)
    for mod_name, program, interp in all_programs:
        resolved_file = dg.file_map.get(mod_name)
        file_path = str(resolved_file.path) if resolved_file else mod_name
        interp.steps = suite_steps
        interp._output_length = suite_output_length

        harnesses = extract_harnesses(program)
        if filter_pattern:
            # Match against the fully qualified impl name, the legacy
            # ``Target.method`` alias, and the bare method name so existing
            # filters keep working after impl harnesses became unique per trait.
            harnesses = [
                h
                for h in harnesses
                if fnmatch.fnmatch(h.name, filter_pattern)
                or fnmatch.fnmatch(h.target_method_name, filter_pattern)
                or fnmatch.fnmatch(h.base_name, filter_pattern)
            ]

        test_blocks = [
            defn for defn in program.definitions if isinstance(defn, TestBlock)
        ]
        if filter_pattern:
            test_blocks = [
                tb for tb in test_blocks if fnmatch.fnmatch(tb.name, filter_pattern)
            ]

        untested_funcs = [
            (defn.name, defn.untested_reason)
            for defn in program.definitions
            if isinstance(defn, FunctionDef) and defn.untested_reason is not None
        ]

        result = HarnessResult()
        mod_start = time.monotonic()

        _run_examples_and_tests(interp, harnesses, test_blocks, result)

        result.untested = untested_funcs
        suite_steps = interp.steps
        suite_output_length = interp._output_length
        mod_elapsed_ms = (time.monotonic() - mod_start) * 1000
        property_results: list[PropertyResult] | None = None
        module_checker = module_checkers.get(mod_name)
        if (
            property_config is not None
            and module_checker is not None
            and not (resolved_file is not None and resolved_file.is_dependency)
            and not is_stdlib_program(program)
        ):
            property_results = _check_properties(
                program,
                module_checker,
                module=mod_name,
                path=file_path,
                config=property_config,
                sandbox_config=sandbox_config,
                modules=parsed_modules,
                source=dg.original_sources.get(mod_name),
            )
        suite.file_results.append(
            FileTestResult(
                path=file_path,
                harness_result=result,
                elapsed_ms=mod_elapsed_ms,
                properties=property_results,
            )
        )

    return suite


def _test_file(
    filepath: Path,
    filter_pattern: str | None = None,
    target: str | None = None,
    sandbox_config: SandboxConfig | None = None,
    properties: PropertyConfig | None = None,
) -> FileTestResult:
    """Run tests for a single file using the interpreter's example verification.

    With *properties* (whose filter is already the suite's), the file's
    eligible functions are property checked after its examples and test
    blocks, including a file that has neither.
    """
    import time

    from .interpreter import Interpreter
    from .lexer import Lexer, LexerError
    from .parser import ParseError, ParseErrors, Parser
    from .project_resolution import ProjectResolutionError, resolve_file_context
    from .target_profile import TargetProfile, resolve_manifest_targets
    from .target_validation import (
        TargetValidationError,
        validate_default_run_lowering_for_program,
    )
    from .typechecker import TypeChecker

    sandbox_config = sandbox_config or default_test_sandbox_config()

    file_start = time.monotonic()
    try:
        try:
            context = resolve_file_context(filepath)
        except ProjectResolutionError as e:
            return FileTestResult(
                path=str(filepath),
                error=f"Module Error: {e}",
                elapsed_ms=(time.monotonic() - file_start) * 1000,
            )

        source = context.source
        tokens = Lexer(source, context.filename).tokenize()
        program = Parser(tokens, source).parse_program()
        parsed_modules = context.parsed_modules or None
        target_names: list[str] = (
            [target]
            if target is not None
            else resolve_manifest_targets(context.project.root)
        )
        check_targets: list[str | None] = list(target_names) if target_names else [None]

        # Type check
        for target_name in check_targets:
            target_profile = (
                TargetProfile.load(target_name) if target_name is not None else None
            )
            checker = TypeChecker(target_profile=target_profile)
            checker.check_program(program, modules=parsed_modules)
            if target_profile is None:
                # A target-less file is still run by the default `geno run`,
                # which lowers through the Python backend. Validate that here so
                # examples cannot pass for a program that cannot run (#70).
                validate_default_run_lowering_for_program(
                    program, parsed_modules, entrypoint_name=context.module_name
                )

        # Count examples (apply filter)
        harnesses = extract_harnesses(program)
        if filter_pattern:
            # Match against the fully qualified impl name, the legacy
            # ``Target.method`` alias, and the bare method name so existing
            # filters keep working after impl harnesses became unique per trait.
            harnesses = [
                h
                for h in harnesses
                if fnmatch.fnmatch(h.name, filter_pattern)
                or fnmatch.fnmatch(h.target_method_name, filter_pattern)
                or fnmatch.fnmatch(h.base_name, filter_pattern)
            ]

        # Collect test blocks
        test_blocks = [
            defn for defn in program.definitions if isinstance(defn, TestBlock)
        ]
        if filter_pattern:
            test_blocks = [
                tb for tb in test_blocks if fnmatch.fnmatch(tb.name, filter_pattern)
            ]

        # Collect @untested functions
        untested_funcs = [
            (defn.name, defn.untested_reason)
            for defn in program.definitions
            if isinstance(defn, FunctionDef) and defn.untested_reason is not None
        ]

        result = HarnessResult()
        total_examples = sum(len(h.examples) for h in harnesses)
        # A file with no examples and no test blocks is not loaded at all.
        if total_examples > 0 or len(test_blocks) > 0:
            # Load into interpreter (don't auto-verify — we verify per-function)
            interp = Interpreter(check_examples=False, sandbox_config=sandbox_config)
            # Match project-mode tests: register definitions without running main().
            interp.run(program, modules=parsed_modules, execute_main=False)

            # Run examples per function, then test blocks, using the interpreter
            _run_examples_and_tests(interp, harnesses, test_blocks, result)

        # Report @untested functions
        result.untested = untested_funcs
        elapsed_ms = (time.monotonic() - file_start) * 1000

        property_results: list[PropertyResult] | None = None
        if properties is not None:
            # Also for a file with nothing above to run: its functions still
            # have contracts (or, without any, must not panic).
            property_results = _check_properties(
                program,
                checker,
                module=context.module_name,
                path=str(filepath),
                config=properties,
                sandbox_config=sandbox_config,
                modules=parsed_modules,
                source=source,
            )

        return FileTestResult(
            path=str(filepath),
            harness_result=result,
            elapsed_ms=elapsed_ms,
            properties=property_results,
        )

    except (LexerError, ParseError, ParseErrors) as e:
        return FileTestResult(
            path=str(filepath),
            error=f"Parse Error: {e}",
            elapsed_ms=(time.monotonic() - file_start) * 1000,
        )
    except TargetValidationError as e:
        return FileTestResult(
            path=str(filepath),
            error=f"Compile Error: {e.backend_message}",
            elapsed_ms=(time.monotonic() - file_start) * 1000,
        )
    except Exception as e:
        return FileTestResult(
            path=str(filepath),
            error=_test_block_failure_detail(e),
            elapsed_ms=(time.monotonic() - file_start) * 1000,
        )
