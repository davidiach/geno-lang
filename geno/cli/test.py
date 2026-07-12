"""``geno test`` — run example-based tests."""

from __future__ import annotations

import sys
from pathlib import Path

from .._cli_format import dim as _dim
from .._cli_format import green as _green
from .._cli_format import red as _red
from .._cli_format import yellow as _yellow
from ..test_runner import DEFAULT_TEST_MAX_STEPS, DEFAULT_TEST_TIMEOUT


def _run_test_suite_once(
    path: str,
    filter_pattern: str | None = None,
    target_name: str | None = None,
    sandbox_config=None,
):
    """Run tests once and return the suite result."""
    import time

    from ..test_runner import (
        default_test_sandbox_config,
        discover_files,
        run_project_test_suite,
        run_test_suite,
    )

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
        )

    elapsed = time.monotonic() - start
    return suite_result, elapsed


def _print_test_results(suite_result, elapsed: float, verbose: bool = False):
    """Print test results with color, timing, and untested counts."""
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
            for name, reason in hr.untested:
                print(f"        {_yellow('o')} {name}: @untested({reason!r})")

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
):
    """Run example-based tests on Geno files."""
    from ..test_runner import default_test_sandbox_config

    def _json_error_result(message: str) -> None:
        import json as json_mod

        print(
            json_mod.dumps(
                {
                    "files": [
                        {
                            "path": path,
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
                },
                indent=2,
            )
        )

    target = Path(path)
    if not target.exists():
        if json_output:
            _json_error_result(f"Error: '{path}' not found")
        else:
            print(f"Error: '{path}' not found", file=sys.stderr)
        sys.exit(1)

    if timeout is None:
        timeout = DEFAULT_TEST_TIMEOUT
    if max_steps is None:
        max_steps = DEFAULT_TEST_MAX_STEPS
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
        )
        return

    try:
        suite_result, elapsed = _run_test_suite_once(
            path,
            filter_pattern,
            target_name=target_name,
            sandbox_config=sandbox_config,
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


def _run_tests_watch(
    path: str,
    filter_pattern: str | None = None,
    verbose: bool = False,
    target_name: str | None = None,
    sandbox_config=None,
):
    """Watch for file changes and re-run tests."""
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
                    )
                    _print_test_results(suite_result, elapsed, verbose=verbose)
                except Exception as e:
                    print(f"Error: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print()
        print(_dim("Watch mode stopped."))
