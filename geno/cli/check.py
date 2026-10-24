"""``geno check`` — check types and target lowering compatibility."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..diagnostics import Fix

from ._util import (
    _format_source_snippet,
    _print_error,
    _print_warning,
    report_deep_nesting_error,
)


def check_file(filename: str, target: str | None = None):
    """Check types and any selected target backend for a file or project."""
    from ..dependency_graph import (
        CircularDependencyError,
        DependencyGraphError,
        NameCollisionError,
    )
    from ..lexer import LexerError
    from ..parser import ParseError, ParseErrors
    from ..project_graph import ProjectGraphError
    from ..project_resolution import ProjectResolutionError, resolve_project_context
    from ..typechecker import TypeChecker, TypeError
    from ..var_shadowing import project_warnings

    try:
        resolved = resolve_project_context(filename)
        pg = resolved.project
        dg = resolved.dependency_graph

        # Resolve targets: CLI flag checks one target; manifest checks all declared targets.
        from ..target_profile import TargetProfile, resolve_manifest_targets
        from ..target_validation import (
            TargetValidationError,
            validate_default_run_lowering,
            validate_project_for_target,
        )

        manifest_targets = resolve_manifest_targets(pg.root)
        target_names: list[str] = [target] if target is not None else manifest_targets
        check_targets: list[str | None] = list(target_names) if target_names else [None]

        checked = None
        target_errors: list[tuple[str | None, Exception]] = []
        for target_name in check_targets:
            target_profile = (
                TargetProfile.load(target_name) if target_name is not None else None
            )
            checker = TypeChecker(target_profile=target_profile)
            try:
                checked = checker.check_project_graph(dg)
                if target_profile is not None:
                    validate_project_for_target(dg, target_profile)
                else:
                    # No declared target: the default `geno run` still lowers
                    # through the Python backend, so validate against it rather
                    # than reporting success for a program that cannot run.
                    validate_default_run_lowering(dg)
            except (TypeError, TargetValidationError) as e:
                target_errors.append((target_name, e))

        if target_errors:
            for target_name, error in target_errors:
                if isinstance(error, TargetValidationError):
                    label = (
                        "Compile Error"
                        if target_name is None
                        else f"Target Error (target: {target_name})"
                    )
                    _print_error(label, error.backend_message)
                elif target_name is None:
                    _print_error("Type Error", error)
                else:
                    _print_error(f"Type Error (target: {target_name})", error)
            sys.exit(1)

        assert checked is not None

        # Warnings do not depend on the target, so report them once.
        warnings = project_warnings(dg)
        for warning in warnings:
            _print_warning(warning)

        mod_count = len(checked)
        total_defs = sum(len(prog.definitions) for prog in checked.values())
        if len(target_names) > 1:
            target_info = f" (targets: {', '.join(target_names)})"
        elif len(target_names) == 1:
            target_info = f" (target: {target_names[0]})"
        else:
            target_info = ""
        print(f"Type check passed: {filename}{target_info}")
        if len(target_names) > 1:
            for target_name in target_names:
                print(f"  {target_name}: passed")
        def_noun = "definition" if total_defs == 1 else "definitions"
        mod_noun = "module" if mod_count == 1 else "modules"
        print(f"  {total_defs} {def_noun}, {mod_count} {mod_noun}")
        if warnings:
            plural = "" if len(warnings) == 1 else "s"
            print(f"  {len(warnings)} warning{plural}")

    except FileNotFoundError:
        print(f"Error: File not found: {filename}", file=sys.stderr)
        sys.exit(1)
    except ProjectResolutionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ProjectGraphError as e:
        print(f"Project Error: {e}", file=sys.stderr)
        sys.exit(1)
    except CircularDependencyError as e:
        print(f"Circular Import: {e}", file=sys.stderr)
        sys.exit(1)
    except NameCollisionError as e:
        print(f"Name Collision: {e}", file=sys.stderr)
        sys.exit(1)
    except DependencyGraphError as e:
        print(f"Dependency Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Manifest Error: {e}", file=sys.stderr)
        sys.exit(1)
    except LexerError as e:
        _print_error("Lexer Error", e)
        sys.exit(1)
    except ParseErrors as e:
        print(f"Parse Errors ({len(e.errors)} errors):", file=sys.stderr)
        for err in e.errors:
            snippet = _format_source_snippet(getattr(err, "location", None))
            fix_note = "".join(
                f"\n    fix: {fix.title} (`geno check --fix` applies it)"
                for fix in err.fixes[:1]
            )
            print(f"  {err}{snippet}{fix_note}", file=sys.stderr)
        sys.exit(1)
    except ParseError as e:
        _print_error("Parse Error", e)
        sys.exit(1)
    except TypeError as e:
        _print_error("Type Error", e)
        sys.exit(1)
    except RecursionError:
        report_deep_nesting_error(filename)


# A fix can expose the next error (a misspelled name in a call that also lacks
# argument names); a few rounds settle any realistic file.
_MAX_FIX_ROUNDS = 10


def fix_file(filename: str, target: str | None = None) -> None:
    """Apply every unambiguous fix the diagnostics of ``filename`` carry.

    Then report what remains exactly as ``geno check`` does.
    """
    from ..diagnostics import apply_fixes

    path = Path(filename)
    applied = 0
    for _round in range(_MAX_FIX_ROUNDS):
        fixes = _available_fixes(path)
        if not fixes:
            break
        source = path.read_text(encoding="utf-8")
        fixed, count = apply_fixes(source, fixes)
        if count == 0 or fixed == source:
            break
        path.write_text(fixed, encoding="utf-8")
        applied += count
    noun = "fix" if applied == 1 else "fixes"
    print(f"Applied {applied} {noun} to {filename}")
    check_file(filename, target=target)


def _available_fixes(path: Path) -> list[Fix]:
    """The first fix of each error in ``path`` that has one."""
    from ..dependency_graph import DependencyGraphError
    from ..lexer import LexerError
    from ..parser import ParseError, ParseErrors
    from ..project_graph import ProjectGraphError
    from ..project_resolution import ProjectResolutionError, resolve_project_context
    from ..typechecker import TypeChecker, TypeError

    target = path.resolve()
    try:
        resolved = resolve_project_context(str(path))
        TypeChecker().check_project_graph(resolved.dependency_graph)
        return []
    except (ParseError, ParseErrors, TypeError) as error:
        errors = getattr(error, "errors", None) or [error]
    except (
        DependencyGraphError,
        LexerError,
        ProjectGraphError,
        ProjectResolutionError,
        OSError,
        ValueError,
    ):
        return []

    def in_file(err: Exception) -> bool:
        location = getattr(err, "location", None)
        if location is None:
            return False
        try:
            return bool(Path(location.filename).resolve() == target)
        except (OSError, ValueError):
            return False

    return [
        err.fixes[0] for err in errors if getattr(err, "fixes", ()) and in_file(err)
    ]
