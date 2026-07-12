"""``geno build`` and ``geno bundle`` — app building and bundling."""

from __future__ import annotations

import sys
from pathlib import Path

from ._util import report_deep_nesting_error, write_text_output


def build_app(
    filename: str,
    output: str | None = None,
    width: int = 800,
    height: int = 600,
    title: str = "Geno App",
    single_file: bool = False,
    source_map: bool = False,
):
    """Build a Geno app.

    Default: outputs a dist/ directory with index.html and app.js.
    Source maps are emitted only when requested.
    With --single-file: outputs a self-contained HTML file (legacy behavior).
    """
    from ..dependency_graph import (
        CircularDependencyError,
        DependencyGraphError,
        NameCollisionError,
    )
    from ..js_compiler import (
        JSCompiler,
        _browser_capability_bootstrap,
        _coerce_canvas_dimension,
        _offset_source_map_lines,
        compile_project_to_html,
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

    # Infer single-file mode when output path ends in .html
    if not single_file and output and output.endswith(".html"):
        single_file = True

    # Validate caller-supplied dimensions BEFORE the CLI error boundary: this
    # is a programmatic-input contract (embedding callers rely on the raised
    # ValueError — HTML-injection guard), not a project-file error, so it must
    # not be swallowed by the manifest ValueError handler below (M-07).
    safe_width = _coerce_canvas_dimension(width, "width")
    safe_height = _coerce_canvas_dimension(height, "height")

    try:
        resolved = resolve_project_context(filename)
        pg = resolved.project
        dg = resolved.dependency_graph

        # geno build targets browser — enforce the browser target profile
        manifest_targets = resolve_manifest_targets(pg.root)
        if manifest_targets and "browser" not in manifest_targets:
            raise ValueError(
                "Target 'browser' is not declared in geno.toml. "
                f"Declared targets: {', '.join(manifest_targets)}."
            )
        checker = TypeChecker(target_profile=TargetProfile.load("browser"))
        checker.check_project_graph(dg)

        if single_file:
            # Legacy single-file HTML output
            html = compile_project_to_html(
                dg,
                width=safe_width,
                height=safe_height,
                title=title,
                source_map=source_map,
            )
            if output is None:
                output = (pg.entrypoint or pg.files[0].module_name) + ".html"

            write_text_output(output, html)
            print(f"Built to {output}")
        else:
            # Directory output: dist/ with index.html and app.js
            dist_dir = Path(output) if output else Path("dist")
            dist_dir.mkdir(parents=True, exist_ok=True)

            # Compile JS via the unified project pipeline
            compiler = JSCompiler(track_source_map=source_map)
            js_code = compiler.compile_project(dg)

            sources_content: dict[str, str] = {}
            if source_map:
                for mod_name in dg.sorted_modules:
                    rf = dg.file_map.get(mod_name)
                    if rf:
                        try:
                            sources_content[str(rf.path)] = rf.path.read_text(
                                encoding="utf-8"
                            )
                        except OSError:
                            pass

            # Write app.js, optionally with source map reference
            browser_bootstrap = _browser_capability_bootstrap()
            js_path = dist_dir / "app.js"
            source_map_comment = (
                "\n//# sourceMappingURL=app.js.map\n" if source_map else ""
            )
            write_text_output(js_path, browser_bootstrap + js_code + source_map_comment)

            if source_map:
                # Write source map
                sm_json = compiler.generate_source_map(
                    out_file="app.js",
                    sources_content=sources_content,
                )
                map_path = dist_dir / "app.js.map"
                sm_json = _offset_source_map_lines(
                    sm_json, browser_bootstrap.count("\n")
                )
                write_text_output(map_path, sm_json)

            # Write index.html referencing app.js
            import html as _html_mod

            safe_title = _html_mod.escape(title)
            index_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>
body {{ margin: 0; background: #111; display: flex; justify-content: center; align-items: center; height: 100vh; }}
canvas {{ border: 1px solid #333; }}
</style>
</head>
<body>
<canvas id="geno-canvas" width="{safe_width}" height="{safe_height}"></canvas>
<script src="app.js"></script>
</body>
</html>"""
            index_path = dist_dir / "index.html"
            write_text_output(index_path, index_html)

            print(f"Built to {dist_dir}/")
            print(f"  {dist_dir}/index.html")
            print(f"  {dist_dir}/app.js")
            if source_map:
                print(f"  {dist_dir}/app.js.map")

    except ProjectGraphError as e:
        print(f"Project Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ProjectResolutionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except CircularDependencyError as e:
        print(f"Circular Dependency: {e}", file=sys.stderr)
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
    except FileNotFoundError:
        print(f"Error: File not found: {filename}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        # Malformed geno.toml (TOMLDecodeError subclasses ValueError) or an
        # undeclared browser target — report cleanly instead of a raw traceback
        # (M-07).
        print(f"Build Error: {e}", file=sys.stderr)
        sys.exit(1)
    except RecursionError:
        report_deep_nesting_error(filename)
    except LexerError as e:
        print(f"Lexer Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ParseErrors as e:
        print(f"Parse Errors ({len(e.errors)} errors):", file=sys.stderr)
        for err in e.errors:
            print(f"  {err}", file=sys.stderr)
        sys.exit(1)
    except ParseError as e:
        print(f"Parse Error: {e}", file=sys.stderr)
        sys.exit(1)
    except TypeError as e:
        print(f"Type Error: {e}", file=sys.stderr)
        sys.exit(1)


def bundle_project(config_path: str, output: str | None = None):
    """Bundle a Geno project into a JSON artifact using geno.toml."""
    import json as json_mod

    try:
        config_file = Path(config_path)
        if not config_file.exists():
            print(f"Error: Config file not found: {config_path}", file=sys.stderr)
            sys.exit(1)

        from ..manifest import parse_manifest

        manifest = parse_manifest(config_file)
        entrypoint = manifest.entrypoint or "Main"
        file_list = manifest.files
        base_dir = config_file.parent

        modules: dict[str, str] = {}
        resolved_base = base_dir.resolve()
        for filepath in file_list:
            full_path = base_dir / filepath
            try:
                resolved_path = full_path.resolve()
                resolved_path.relative_to(resolved_base)
            except ValueError:
                print(
                    f"Error: File path escapes project directory: {filepath}",
                    file=sys.stderr,
                )
                sys.exit(1)
            if not full_path.exists():
                print(f"Error: File not found: {full_path}", file=sys.stderr)
                sys.exit(1)
            # Module name is the filename stem (e.g., Utils.geno -> Utils)
            module_name = full_path.stem
            with open(resolved_path, encoding="utf-8") as f:
                modules[module_name] = f.read()

        artifact = {
            "version": "1",
            "entrypoint": entrypoint,
            "modules": modules,
        }

        json_str = json_mod.dumps(artifact, indent=2)
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(json_str)
            print(f"Bundled to {output}")
        else:
            print(json_str)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
