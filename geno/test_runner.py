"""
Test runner for ``geno test``.

Discovers .geno files, runs example-based tests via the interpreter's
own example verification, and aggregates results.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .ast_nodes import AssertStatement, FunctionDef, TestBlock
from .execution_limits import (
    DEFAULT_INTERPRETER_MAX_STEPS,
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
    from .values import GenoRuntimeError, GenoThrowError

    return (
        GenoRuntimeError,
        GenoThrowError,
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


def _classify_runtime_failure(message: str) -> str:
    """Map a ``GenoRuntimeError`` message back to the spec kind it violated.

    The interpreter's ``_check_requires`` / ``_check_ensures`` helpers
    raise ``RuntimeError`` with stable prefixes.  We recognise those so
    the test report can show ``requires`` / ``ensures`` violations
    separately from generic runtime errors — F-0021 in #662, which
    otherwise labelled every runtime failure as a plain ``example``
    failure.
    """
    if message.startswith("Precondition failed"):
        return "requires"
    if message.startswith("Postcondition failed"):
        return "ensures"
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
    from .values import _UNBOUND

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
                with interp._execution_deadline(interp.sandbox_config.timeout):
                    input_val = interp.eval_expr(example.input_expr, interp.global_env)
                    expected = interp.eval_expr(example.output_expr, interp.global_env)

                    actual = interp._call_function(
                        func,
                        example_call_args(
                            input_val,
                            param_count=len(harness.param_names),
                            required_count=sum(
                                1
                                for param in getattr(func, "params", [])
                                if param.default_value is None
                            ),
                        ),
                    )

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
                runtime_message = getattr(e, "message", str(e))
                result.failed += 1
                result.violations.append(
                    SpecViolation(
                        kind=_classify_runtime_failure(runtime_message),
                        function=harness.name,
                        message=f"Runtime error: {_test_error_detail(e)}",
                    )
                )


@dataclass
class FileTestResult:
    """Result of testing a single file."""

    path: str
    harness_result: HarnessResult | None = None
    error: str | None = None
    elapsed_ms: float = 0.0


@dataclass
class SuiteResult:
    """Aggregated result across all files."""

    file_results: List[FileTestResult] = field(default_factory=list)

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
        return self.failed == 0 and self.errors == 0

    def to_dict(self) -> dict:
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
                entry["violations"] = [
                    {
                        "kind": v.kind,
                        "function": v.function,
                        "message": v.message,
                    }
                    for v in hr.violations
                ]
            entry["elapsed_ms"] = round(fr.elapsed_ms, 2)
            files.append(entry)
        return {
            "files": files,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "untested": self.untested,
            "success": self.success,
        }


def discover_files(target: Path) -> List[Path]:
    """Find .geno files in a path. Returns a single file or recursive directory listing."""
    if target.is_file():
        if target.suffix in {".geno", ".gen"}:
            return [target]
        return []
    return sorted(target.rglob("*.geno"))


def run_test_suite(
    files: List[Path],
    filter_pattern: str | None = None,
    target: str | None = None,
    sandbox_config: SandboxConfig | None = None,
) -> SuiteResult:
    """Run tests on a list of files and return aggregated results."""
    suite = SuiteResult()
    sandbox_config = sandbox_config or default_test_sandbox_config()

    for filepath in files:
        result = _test_file(
            filepath,
            filter_pattern,
            target=target,
            sandbox_config=sandbox_config,
        )
        suite.file_results.append(result)

    return suite


def run_project_test_suite(
    project_path: Path,
    filter_pattern: str | None = None,
    target: str | None = None,
    sandbox_config: SandboxConfig | None = None,
) -> SuiteResult:
    """Run tests using ProjectGraph.

    Accepts a directory (with or without geno.toml) or a single .geno file.
    All modules are parsed and loaded into a shared interpreter so that
    cross-module dependencies resolve correctly during test execution.
    Examples from ALL modules (not just the entrypoint) are tested.
    """
    from .dependency_graph import DependencyGraphError
    from .interpreter import Interpreter
    from .project_graph import ProjectGraphError
    from .project_resolution import ProjectResolutionError, resolve_project_context
    from .sandbox import SandboxError
    from .target_profile import TargetProfile, resolve_manifest_targets
    from .typechecker import TypeChecker, TypeError
    from .values import GenoRuntimeError, GenoThrowError

    sandbox_config = sandbox_config or default_test_sandbox_config()

    def _project_error_result(message: str) -> SuiteResult:
        suite = SuiteResult()
        suite.file_results.append(FileTestResult(path=str(project_path), error=message))
        return suite

    def _project_error_message(exc: Exception) -> str:
        return f"Project Error: {describe_project_resolution_error(exc)}"

    runtime_load_errors = (
        GenoRuntimeError,
        GenoThrowError,
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

    # Type check all modules for each declared target
    try:
        for target_name in check_targets:
            target_profile = (
                TargetProfile.load(target_name) if target_name is not None else None
            )
            checker = TypeChecker(target_profile=target_profile)
            checker.check_project_graph(dg)
    except ValueError as e:
        return _project_error_result(f"Manifest Error: {e}")
    except TypeError as e:
        return _project_error_result(f"Type Error: {e}")

    # Load all modules into a shared interpreter in topological order.
    # execute_main=False because the test runner only needs definitions
    # registered — it exercises examples directly, not main().
    interp = Interpreter(check_examples=False, sandbox_config=sandbox_config)
    parsed_modules = {name: dg.parsed[name] for name in dg.sorted_modules}
    all_programs = []
    for mod_name in dg.sorted_modules:
        program = dg.parsed[mod_name]
        try:
            interp.run(program, modules=parsed_modules, execute_main=False)
        except runtime_load_errors as e:
            return _project_error_result(f"Runtime Error: {e}")
        all_programs.append((mod_name, program))

    import time

    # Test examples from every module
    suite = SuiteResult()
    for mod_name, program in all_programs:
        resolved_file = dg.file_map.get(mod_name)
        file_path = str(resolved_file.path) if resolved_file else mod_name

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
                result.violations.append(
                    SpecViolation(
                        kind="test",
                        function=test_block.name,
                        message=_test_error_detail(e),
                    )
                )

        result.untested = untested_funcs
        mod_elapsed_ms = (time.monotonic() - mod_start) * 1000
        suite.file_results.append(
            FileTestResult(
                path=file_path, harness_result=result, elapsed_ms=mod_elapsed_ms
            )
        )

    return suite


def _test_file(
    filepath: Path,
    filter_pattern: str | None = None,
    target: str | None = None,
    sandbox_config: SandboxConfig | None = None,
) -> FileTestResult:
    """Run tests for a single file using the interpreter's example verification."""
    import time

    from .interpreter import Interpreter
    from .lexer import Lexer, LexerError
    from .parser import ParseError, ParseErrors, Parser
    from .project_resolution import ProjectResolutionError, resolve_file_context
    from .target_profile import TargetProfile, resolve_manifest_targets
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
        program = Parser(tokens).parse_program()
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

        total_examples = sum(len(h.examples) for h in harnesses)
        if total_examples == 0 and len(test_blocks) == 0:
            hr = HarnessResult()
            hr.untested = untested_funcs
            return FileTestResult(
                path=str(filepath),
                harness_result=hr,
                elapsed_ms=(time.monotonic() - file_start) * 1000,
            )

        # Load into interpreter (don't auto-verify — we verify per-function)
        interp = Interpreter(check_examples=False, sandbox_config=sandbox_config)
        # Match project-mode tests: register definitions without running main().
        interp.run(program, modules=parsed_modules, execute_main=False)

        # Run examples per function using the interpreter
        result = HarnessResult()
        _run_examples(interp, harnesses, result)

        # Run test blocks
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
                result.violations.append(
                    SpecViolation(
                        kind="test",
                        function=test_block.name,
                        message=_test_error_detail(e),
                    )
                )

        # Report @untested functions
        result.untested = untested_funcs

        return FileTestResult(
            path=str(filepath),
            harness_result=result,
            elapsed_ms=(time.monotonic() - file_start) * 1000,
        )

    except (LexerError, ParseError, ParseErrors) as e:
        return FileTestResult(
            path=str(filepath),
            error=f"Parse Error: {e}",
            elapsed_ms=(time.monotonic() - file_start) * 1000,
        )
    except Exception as e:
        return FileTestResult(
            path=str(filepath),
            error=_test_error_detail(e),
            elapsed_ms=(time.monotonic() - file_start) * 1000,
        )
