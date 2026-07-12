"""``geno watch`` — file watching and re-run."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Union

from .._cli_format import dim as _dim


def _resolve_watch_files(path: Union[str, Path]) -> list[Path]:
    """Resolve the concrete file set that watch-based tooling should observe."""
    from ..lexer import LexerError
    from ..parser_base import ParseError, ParseErrors
    from ..project_resolution import ProjectResolutionError, resolve_project_context

    target = Path(path)
    watched: list[Path] = []
    seen: set[Path] = set()

    def _add(candidate: Path) -> None:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            watched.append(resolved)

    try:
        resolved = resolve_project_context(target)
        for rf in resolved.project.files:
            _add(rf.path)

        if resolved.project.root is not None:
            manifest = resolved.project.root / "geno.toml"
            if manifest.exists():
                _add(manifest)

        for dep_dir in resolved.project.dependencies.values():
            dep_manifest = dep_dir / "geno.toml"
            if dep_manifest.exists():
                _add(dep_manifest)
    except (
        FileNotFoundError,
        ProjectResolutionError,
        ParseError,
        ParseErrors,
        LexerError,
    ):
        # A malformed watched file (syntax or lex error, or an unresolvable import)
        # must not kill the watcher: the whole point of `geno watch` / `geno dev` is
        # to keep running so the user can fix the error and have it re-run on save.
        # Fall back to filesystem-based discovery of the watch set.
        pass

    if watched:
        return watched

    if target.is_file():
        _add(target)
        return watched

    if target.is_dir():
        for pattern in ("*.geno", "*.gen"):
            for candidate in sorted(target.rglob(pattern)):
                _add(candidate)
        manifest = target / "geno.toml"
        if manifest.exists():
            _add(manifest)

    return watched


def _snapshot_watch_mtimes(path: Union[str, Path]) -> dict[str, float]:
    """Return a path -> mtime snapshot for the currently watched file set."""
    mtimes: dict[str, float] = {}
    for candidate in _resolve_watch_files(path):
        try:
            mtimes[str(candidate)] = candidate.stat().st_mtime
        except OSError:
            pass
    return mtimes


def watch_run(
    path: str,
    test_mode: bool = False,
    filter_pattern: str | None = None,
    verbose: bool = False,
    unsafe: bool = False,
):
    """Watch .geno files and re-run on changes.

    In test mode (--test), re-runs ``geno test`` on changes.
    Otherwise, re-runs ``geno run`` on the target file/project.
    """
    if test_mode:
        from .test import _run_tests_watch

        _run_tests_watch(path, filter_pattern=filter_pattern, verbose=verbose)
        return

    import time

    from .run import run_file

    target = Path(path)
    mode_label = "unsafe interpreter" if unsafe else "process sandbox"

    def _run_once():
        start = time.monotonic()
        try:
            run_file(str(target), check_examples=True, unsafe=unsafe)
        except SystemExit:
            pass
        elapsed = time.monotonic() - start
        print()
        print(_dim(f"Completed in {elapsed:.2f}s"))

    prev_mtimes = _snapshot_watch_mtimes(target)
    print(_dim(f"Watching resolved files for {target}... (Ctrl+C to stop)"))
    print(f"Execution mode: {mode_label}")
    print()
    _run_once()

    try:
        while True:
            time.sleep(0.5)
            current_mtimes = _snapshot_watch_mtimes(target)
            if current_mtimes != prev_mtimes:
                prev_mtimes = current_mtimes
                print()
                print(_dim("--- File change detected, re-running ---"))
                print()
                _run_once()
    except KeyboardInterrupt:
        print()
        print(_dim("Watch mode stopped."))
