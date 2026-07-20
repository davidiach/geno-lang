"""``geno check`` — check types and target lowering compatibility."""

from __future__ import annotations

import sys

from ._util import _format_source_snippet, _print_error, report_deep_nesting_error


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

    try:
        resolved = resolve_project_context(filename)
        pg = resolved.project
        dg = resolved.dependency_graph

        # Resolve targets: CLI flag checks one target; manifest checks all declared targets.
        from ..target_profile import TargetProfile, resolve_manifest_targets
        from ..target_validation import (
            TargetValidationError,
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
            except (TypeError, TargetValidationError) as e:
                target_errors.append((target_name, e))

        if target_errors:
            for target_name, error in target_errors:
                if isinstance(error, TargetValidationError):
                    label = (
                        "Target Error"
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
        print(f"  {total_defs} definitions, {mod_count} modules")

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
            print(f"  {err}{snippet}", file=sys.stderr)
        sys.exit(1)
    except ParseError as e:
        _print_error("Parse Error", e)
        sys.exit(1)
    except TypeError as e:
        _print_error("Type Error", e)
        sys.exit(1)
    except RecursionError:
        report_deep_nesting_error(filename)
