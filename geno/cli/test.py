"""``geno test`` — run example-based tests (and, opt-in, property checks)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .._cli_format import dim as _dim
from .._cli_format import green as _green
from .._cli_format import red as _red
from .._cli_format import yellow as _yellow
from ..execution_limits import DEFAULT_PROPERTY_SEED, DEFAULT_PROPERTY_TRIALS
from ..test_runner import DEFAULT_TEST_MAX_STEPS, DEFAULT_TEST_TIMEOUT

if TYPE_CHECKING:
    # The property-check engine is imported only under --properties.
    from ..harness import SpecViolation
    from ..property_check import PropertyConfig, PropertyResult
    from ..test_runner import SuiteResult


def _run_test_suite_once(
    path: str,
    filter_pattern: str | None = None,
    target_name: str | None = None,
    sandbox_config=None,
    properties: PropertyConfig | None = None,
    backend: str = "interpreter",
):
    """Run tests once and return the suite result.

    With *properties*, eligible functions are also property checked. With
    *backend* ``"all"``, the result is a ``BackendComparison`` of the
    interpreter, compiled Python and compiled JavaScript (proposal 0024).
    """
    import time

    from ..test_runner import (
        REAL_BACKENDS,
        BackendComparison,
        default_test_sandbox_config,
        discover_files,
        find_divergences,
        run_project_test_suite,
        run_test_suite,
    )

    if backend == "all":
        start = time.monotonic()
        suites = {
            name: _run_test_suite_once(
                path,
                filter_pattern,
                target_name=target_name,
                sandbox_config=sandbox_config,
                backend=name,
            )[0]
            for name in REAL_BACKENDS
        }
        comparison = BackendComparison(suites, find_divergences(suites))
        return comparison, time.monotonic() - start

    target = Path(path)
    sandbox_config = sandbox_config or default_test_sandbox_config()

    start = time.monotonic()

    # Use ProjectGraph when a manifest exists (multi-module deps are
    # resolved correctly). Fall back to the legacy per-file path for
    # directories and files without a manifest — the legacy path uses
    # resolve_modules() which finds sibling imports on the filesystem.
    manifest = None
    if target.is_dir():
        manifest = target / "geno.toml"
    elif target.is_file():
        manifest = target.parent / "geno.toml"

    if manifest is not None and manifest.exists():
        project_target = target if target.is_file() else manifest.parent
        suite_result = run_project_test_suite(
            project_target,
            filter_pattern=filter_pattern,
            target=target_name,
            sandbox_config=sandbox_config,
            properties=properties,
            backend=backend,
        )
    else:
        files = discover_files(target)
        if not files:
            print(f"No .geno files found in '{path}'", file=sys.stderr)
            sys.exit(1)
        suite_result = run_test_suite(
            files,
            filter_pattern=filter_pattern,
            target=target_name,
            sandbox_config=sandbox_config,
            properties=properties,
            backend=backend,
        )

    elapsed = time.monotonic() - start
    return suite_result, elapsed


def _print_comparison(comparison, elapsed: float, verbose: bool = False) -> None:
    """Print ``--backend all``: each backend's files, then how they differ."""
    for name, suite in comparison.suites.items():
        print(f"Backend: {name}")
        _print_file_results(suite, verbose=verbose)
    print()
    if comparison.divergences:
        print(_red("Divergences") + " (the backends disagree)")
        for divergence in comparison.divergences:
            what = (
                f"test {divergence.function!r}"
                if divergence.kind == "test"
                else f"{divergence.function}, example {divergence.ordinal + 1}"
            )
            print(f"  {_red('x')} {divergence.path}: {what}")
            for backend, outcome in divergence.outcomes.items():
                print(f"        {backend}: {outcome}")
        print()
    for name, suite in comparison.suites.items():
        failed = f"{suite.failed} failed"
        parts = [
            _green(f"{suite.passed} passed"),
            _red(failed) if suite.failed > 0 else failed,
        ]
        if suite.errors > 0:
            noun = "error" if suite.errors == 1 else "errors"
            parts.append(_red(f"{suite.errors} {noun}"))
        print(f"{name}: " + ", ".join(parts))
    count = len(comparison.divergences)
    noun = "divergence" if count == 1 else "divergences"
    summary = _red(f"{count} {noun}") if count else f"{count} {noun}"
    if comparison.untested > 0:
        summary += ", " + _yellow(f"{comparison.untested} untested")
    print(f"{summary}, {_dim(f'in {elapsed:.2f}s')}")


def _print_file_results(suite_result, verbose: bool = False) -> int:
    """Print one line per file (and its details); return the untested count."""
    total_untested = 0

    for file_result in suite_result.file_results:
        hr = file_result.harness_result
        if file_result.error:
            status = _red("ERROR")
            counts = file_result.error
        elif hr is not None and hr.success:
            status = _green("PASS")
            counts = f"{hr.passed}/{hr.total}"
        elif hr is not None:
            status = _red("FAIL")
            counts = f"{hr.passed}/{hr.total}"
        else:
            status = _dim("SKIP")
            counts = "0/0"

        untested_count = len(hr.untested) if hr else 0
        total_untested += untested_count
        untested_suffix = (
            f" {_yellow(f'({untested_count} untested)')}" if untested_count > 0 else ""
        )

        timing = ""
        if file_result.elapsed_ms >= 1000:
            timing = f" {_dim(f'{file_result.elapsed_ms / 1000:.2f}s')}"
        elif file_result.elapsed_ms >= 1:
            timing = f" {_dim(f'{file_result.elapsed_ms:.0f}ms')}"

        print(f"  {status}  {file_result.path} ({counts}){untested_suffix}{timing}")

        if verbose and hr:
            for v in hr.violations:
                print(f"        {_red('x')} {v.function}: {v.message}")
                for line in _violation_trace_lines(v):
                    print(f"          {line}")
            for name, reason in hr.untested:
                print(f"        {_yellow('o')} {name}: @untested({reason!r})")
    return total_untested


def _print_test_results(suite_result, elapsed: float, verbose: bool = False):
    """Print test results with color, timing, and untested counts."""
    if getattr(suite_result, "backend", "interpreter") == "all":
        _print_comparison(suite_result, elapsed, verbose=verbose)
        return
    if suite_result.backend != "interpreter":
        print(f"Backend: {suite_result.backend}")
    total_untested = _print_file_results(suite_result, verbose=verbose)

    print()
    if suite_result.property_config is not None:
        _print_property_results(suite_result, verbose=verbose)
        print()
    parts = [
        _green(f"{suite_result.passed} passed"),
        (
            _red(f"{suite_result.failed} failed")
            if suite_result.failed > 0
            else f"{suite_result.failed} failed"
        ),
    ]
    if total_untested > 0:
        parts.append(_yellow(f"{total_untested} untested"))
    parts.append(_dim(f"in {elapsed:.2f}s"))
    print(", ".join(parts))
    if suite_result.property_config is not None:
        print(_property_summary_line(suite_result))


def _property_label(status: str) -> str:
    """The text-report label of a property status.

    ``gave_up``, ``incomplete`` and ``skipped`` are warnings, never failures.
    """
    if status == "passed":
        return _green("PASS")
    if status == "failed":
        return _red("FAIL")
    if status == "gave_up":
        return _yellow("GAVE UP")
    if status == "incomplete":
        return _yellow("INCOMPLETE")
    return _dim("SKIP")


def _violation_trace_lines(violation: SpecViolation) -> list[str]:
    """The frame lines of a runtime failure, shown under its violation.

    The Geno stack, most recent call last (proposal 0018), or the failure's
    location when no stack was captured; nothing for other violations.
    """
    from ..runtime_report import STACK_HEADER, format_position, stack_lines

    stack = getattr(violation, "stack", None)
    if stack is not None and stack.frames:
        return [STACK_HEADER, *stack_lines(stack)]
    location = getattr(violation, "location", None)
    if location is not None:
        return [f"at {format_position(location)}"]
    return []


def _print_property_results(suite_result: SuiteResult, verbose: bool = False) -> None:
    """Print the ``--properties`` block: one entry per notable function.

    Failures and warnings (gave up, incomplete) are always listed; passed and
    skipped functions only with ``-v``.
    """
    config = suite_result.property_config
    if config is None:
        return
    results = suite_result.property_results
    print(f"Properties (seed {config.seed}, {config.trials} trials per function)")
    listed = 0
    for result in results:
        if result.status in ("passed", "skipped") and not verbose:
            continue
        listed += 1
        location = (
            f"{result.path}:{result.line}" if result.line is not None else result.path
        )
        trials = ""
        if result.status == "passed":
            discarded = (
                f", {result.discarded} discarded" if result.discarded > 0 else ""
            )
            trials = f"  {_dim(f'({result.trials_run} trials{discarded})')}"
        label = _property_label(result.status)
        print(f"  {label}  {result.function}  {location}{trials}")
        for line in _property_detail_lines(config, result):
            print(f"        {line}")
    if listed == 0:
        print(
            f"  {_dim('no failures or warnings' if results else 'no functions to check')}"
        )


def _property_detail_lines(config: PropertyConfig, result: PropertyResult) -> list[str]:
    """The indented lines under one function's entry."""
    lines: list[str] = []
    if result.failure is not None:
        lines.append(result.failure.summary or result.failure.message)
    if result.counterexample is not None:
        example = result.counterexample
        shrunk = ""
        if example.shrink_steps > 0:
            steps = "step" if example.shrink_steps == 1 else "steps"
            shrunk = (
                f"  (shrunk from {example.original} in {example.shrink_steps} {steps})"
            )
        lines.append(f"counterexample: {example.arguments}{shrunk}")
    if result.note is not None:
        lines.append(_dim(result.note) if result.status != "failed" else result.note)
    if result.status == "failed":
        lines.append(f"reproduce: {property_reproduce_command(config, result)}")
    return lines


def property_reproduce_command(config: PropertyConfig, result: PropertyResult) -> str:
    """A ``geno test`` command that re-runs exactly this function's checks.

    A function's inputs depend only on the seed, the trial count, its
    logical module and its name (never on other functions, the path or the
    working directory), so ``--filter`` narrows the run without changing
    them. The per-call limits derive from ``--timeout`` and ``--max-steps``,
    so those are repeated when they are not the defaults.
    """
    import shlex

    parts = ["geno", "test", "--properties", "--seed", str(config.seed)]
    if config.trials != DEFAULT_PROPERTY_TRIALS:
        parts += ["--trials", str(config.trials)]
    if config.timeout is not None and config.timeout != DEFAULT_TEST_TIMEOUT:
        parts += ["--timeout", f"{config.timeout:g}"]
    if config.max_steps is not None and config.max_steps != DEFAULT_TEST_MAX_STEPS:
        parts += ["--max-steps", str(config.max_steps)]
    parts += ["--filter", result.function, result.path]
    return " ".join(shlex.quote(part) for part in parts)


def _property_summary_line(suite_result: SuiteResult) -> str:
    """``Properties: 0 passed, 1 failed`` plus any nonzero warning counts."""
    counts = dict.fromkeys(("passed", "failed", "gave_up", "incomplete", "skipped"), 0)
    for result in suite_result.property_results:
        counts[result.status] += 1
    failed = counts["failed"]
    parts = [
        _green(f"{counts['passed']} passed"),
        _red(f"{failed} failed") if failed > 0 else f"{failed} failed",
    ]
    if counts["gave_up"] > 0:
        parts.append(_yellow(f"{counts['gave_up']} gave up"))
    if counts["incomplete"] > 0:
        parts.append(_yellow(f"{counts['incomplete']} incomplete"))
    if counts["skipped"] > 0:
        parts.append(_dim(f"{counts['skipped']} skipped"))
    return "Properties: " + ", ".join(parts)


def run_tests(
    path: str,
    filter_pattern: str | None = None,
    verbose: bool = False,
    json_output: bool = False,
    watch: bool = False,
    target_name: str | None = None,
    fail_on_untested: bool = False,
    timeout: float | None = None,
    max_steps: int | None = None,
    properties: bool = False,
    trials: int | None = None,
    seed: int | None = None,
    backend: str = "interpreter",
):
    """Run example-based tests on Geno files.

    With *properties* (``--properties``), eligible functions are also
    property checked with *trials* generated inputs each from *seed*
    (defaults: 100 trials, seed 0); a failed property fails the run.

    *backend* (``--backend``) chooses where examples and test blocks run:
    the interpreter, compiled Python, compiled JavaScript, or ``all`` three
    with their outcomes compared (proposal 0024). Property checks run on the
    interpreter only.
    """
    from ..test_runner import default_test_sandbox_config

    if timeout is None:
        timeout = DEFAULT_TEST_TIMEOUT
    if max_steps is None:
        max_steps = DEFAULT_TEST_MAX_STEPS
    property_config = (
        _property_config(trials=trials, seed=seed, timeout=timeout, max_steps=max_steps)
        if properties
        else None
    )

    def _json_error_result(message: str) -> None:
        import json as json_mod

        named = {} if backend == "interpreter" else {"backend": backend}
        payload: dict = {
            **named,
            "files": [
                {
                    "path": path,
                    **named,
                    "error": message,
                    "elapsed_ms": 0.0,
                }
            ],
            "total": 0,
            "passed": 0,
            "failed": 0,
            "errors": 1,
            "untested": 0,
            "success": False,
        }
        if property_config is not None:
            from ..property_check import PropertyReport

            payload["properties"] = PropertyReport(
                seed=property_config.seed, trials=property_config.trials
            ).to_dict()
        print(json_mod.dumps(payload, indent=2))

    if properties and backend != "interpreter":
        message = (
            "Error: --properties runs on the interpreter only; use it without "
            f"--backend {backend}"
        )
        if json_output:
            _json_error_result(message)
        else:
            print(message, file=sys.stderr)
        sys.exit(1)

    target = Path(path)
    if not target.exists():
        if json_output:
            _json_error_result(f"Error: '{path}' not found")
        else:
            print(f"Error: '{path}' not found", file=sys.stderr)
        sys.exit(1)

    try:
        sandbox_config = default_test_sandbox_config(
            timeout=timeout,
            max_steps=max_steps,
        )
    except ValueError as e:
        if json_output:
            _json_error_result(f"Error: {e}")
        else:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if watch:
        _run_tests_watch(
            path,
            filter_pattern,
            verbose,
            target_name=target_name,
            sandbox_config=sandbox_config,
            properties=property_config,
            backend=backend,
        )
        return

    try:
        suite_result, elapsed = _run_test_suite_once(
            path,
            filter_pattern,
            target_name=target_name,
            sandbox_config=sandbox_config,
            properties=property_config,
            backend=backend,
        )
    except Exception as e:
        if json_output:
            _json_error_result(f"Project Error: {e}")
        else:
            print(f"Project Error: {e}", file=sys.stderr)
        sys.exit(1)

    if json_output:
        import json as json_mod

        payload = suite_result.to_dict()
        if fail_on_untested and suite_result.untested > 0:
            payload["success"] = False
        print(json_mod.dumps(payload, indent=2))
    else:
        _print_test_results(suite_result, elapsed, verbose=verbose)

    success = suite_result.success and (
        not fail_on_untested or suite_result.untested == 0
    )
    sys.exit(0 if success else 1)


def _property_config(
    *, trials: int | None, seed: int | None, timeout: float, max_steps: int
) -> PropertyConfig:
    """The ``--properties`` settings; imports the engine only when asked for."""
    from ..property_check import PropertyConfig

    return PropertyConfig(
        trials=DEFAULT_PROPERTY_TRIALS if trials is None else trials,
        seed=DEFAULT_PROPERTY_SEED if seed is None else seed,
        timeout=timeout,
        max_steps=max_steps,
    )


def _run_tests_watch(
    path: str,
    filter_pattern: str | None = None,
    verbose: bool = False,
    target_name: str | None = None,
    sandbox_config=None,
    properties: PropertyConfig | None = None,
    backend: str = "interpreter",
):
    """Watch for file changes and re-run tests (and property checks)."""
    import time

    target = Path(path)
    if target.is_file():
        watch_dir = target.parent
    else:
        watch_dir = target

    def _get_mtimes() -> dict[str, float]:
        mtimes: dict[str, float] = {}
        for f in watch_dir.rglob("*.geno"):
            try:
                mtimes[str(f)] = f.stat().st_mtime
            except OSError:
                pass
        return mtimes

    prev_mtimes = _get_mtimes()
    print(_dim(f"Watching {watch_dir} for changes... (Ctrl+C to stop)"))
    print()

    try:
        suite_result, elapsed = _run_test_suite_once(
            path,
            filter_pattern,
            target_name=target_name,
            sandbox_config=sandbox_config,
            properties=properties,
            backend=backend,
        )
        _print_test_results(suite_result, elapsed, verbose=verbose)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)

    try:
        while True:
            time.sleep(0.5)
            current_mtimes = _get_mtimes()
            if current_mtimes != prev_mtimes:
                prev_mtimes = current_mtimes
                if sys.stdout.isatty():
                    print("\033[H\033[2J", end="", flush=True)
                print(_dim(f"Watching {watch_dir} for changes... (Ctrl+C to stop)"))
                print()
                try:
                    suite_result, elapsed = _run_test_suite_once(
                        path,
                        filter_pattern,
                        target_name=target_name,
                        sandbox_config=sandbox_config,
                        properties=properties,
                        backend=backend,
                    )
                    _print_test_results(suite_result, elapsed, verbose=verbose)
                except Exception as e:
                    print(f"Error: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print()
        print(_dim("Watch mode stopped."))
