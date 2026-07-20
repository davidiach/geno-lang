"""``geno compile`` — compile to Python or JavaScript."""

from __future__ import annotations

import sys
from pathlib import Path

from ._util import (
    _format_source_snippet,
    _print_error,
    report_deep_nesting_error,
    write_text_output,
)


def compile_file(
    filename: str,
    output: str | None = None,
    target: str = "python",
    esm: bool = False,
    source_map: bool = False,
):
    """Compile a Geno source file or project to Python or JavaScript."""
    from ..dependency_graph import (
        CircularDependencyError,
        DependencyGraphError,
        NameCollisionError,
    )
    from ..lexer import LexerError
    from ..parser import ParseError, ParseErrors
    from ..project_graph import ProjectGraphError
    from ..project_resolution import ProjectResolutionError, resolve_project_context
    from ..target_profile import (
        ManifestTargetError,
        TargetProfile,
        resolve_manifest_targets,
    )
    from ..typechecker import TypeChecker, TypeError

    try:
        resolved = resolve_project_context(filename)
        pg = resolved.project
        dg = resolved.dependency_graph
        is_multi = len(dg.sorted_modules) > 1

        # Type check via project graph with the selected backend's target profile.
        target_name = "node-cli" if target == "js" else "python-cli"
        # Validate the project manifest fail-closed even though compile targets
        # are selected by the backend flag rather than by the manifest list.
        resolve_manifest_targets(pg.root)
        checker = TypeChecker(target_profile=TargetProfile.load(target_name))
        checker.check_project_graph(dg)

        if target == "js":
            from ..js_compiler import (
                _ESM_SOURCE_MAP_LINE_DELTA,
                JSCompiler,
                _offset_source_map_lines,
                _to_esm,
                generate_dts,
            )

            if source_map and not output:
                raise ValueError("--source-map requires -o/--output for JS compile")
            emit_source_map = bool(output and source_map)
            compiler = JSCompiler(track_source_map=emit_source_map)
            sources_content: dict[str, str] = {}

            if is_multi:
                code = compiler.compile_project(dg)
                if emit_source_map:
                    for mod_name in dg.sorted_modules:
                        rf = dg.file_map.get(mod_name)
                        if rf:
                            sources_content[str(rf.path)] = dg.original_sources[
                                mod_name
                            ]
            else:
                program = dg.parsed[dg.sorted_modules[0]]
                code = compiler.compile(program)
                if emit_source_map:
                    source_path = pg.files[0].path
                    sources_content[str(source_path)] = dg.original_sources[
                        dg.sorted_modules[0]
                    ]

            if esm:
                entrypoint_mod = resolved.entrypoint
                code = _to_esm(code, dg.parsed[entrypoint_mod])

            if output:
                js_file = output
                if emit_source_map:
                    map_file = output + ".map"
                    sm_json = compiler.generate_source_map(
                        out_file=Path(js_file).name,
                        sources_content=sources_content,
                    )
                    code += f"\n//# sourceMappingURL={Path(map_file).name}\n"
                    if esm:
                        sm_json = _offset_source_map_lines(
                            sm_json, _ESM_SOURCE_MAP_LINE_DELTA
                        )
                    write_text_output(map_file, sm_json)
                # Generate .d.ts file
                entrypoint_mod = resolved.entrypoint
                dts = generate_dts(dg.parsed[entrypoint_mod])
                if dts:
                    dts_file = output.replace(".js", ".d.ts")
                    if dts_file == output:
                        dts_file = output + ".d.ts"
                    write_text_output(dts_file, dts)
        else:
            from ..compiler import Compiler

            py_compiler = Compiler()
            if is_multi:
                code = py_compiler.compile_project(dg)
            else:
                program = dg.parsed[dg.sorted_modules[0]]
                code = py_compiler.compile(program)

        if output:
            write_text_output(output, code)
            print(f"Compiled to {output}")
        else:
            print(code)

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
    except ManifestTargetError as e:
        print(f"Manifest Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except RecursionError:
        report_deep_nesting_error(filename)
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
