"""``geno doc`` — generate HTML documentation."""

from __future__ import annotations

import sys

from ._util import write_text_output


def generate_docs(path: str, output: str, title: str):
    """Generate HTML documentation for a Geno file or project."""
    from ..doc_generator import generate_html

    try:
        modules = _resolve_doc_modules(path)
    except FileNotFoundError:
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error parsing {path}: {e}", file=sys.stderr)
        sys.exit(1)

    if not modules:
        print("No Geno files found.", file=sys.stderr)
        sys.exit(1)

    html = generate_html(modules, title=title)
    write_text_output(output, html)
    print(f"Documentation generated: {output}")
    total_funcs = sum(len(m.functions) for m in modules)
    total_types = sum(len(m.types) + len(m.type_aliases) for m in modules)
    total_traits = sum(len(m.traits) for m in modules)
    print(
        f"  {len(modules)} module(s), {total_funcs} function(s), "
        f"{total_types} type(s), {total_traits} trait(s)"
    )


def _resolve_doc_modules(path: str):
    """Resolve the modules that should be included in generated docs."""
    from ..doc_generator import parse_module
    from ..project_resolution import resolve_project_context

    resolved = resolve_project_context(path)
    modules = []
    for rf in resolved.project.files:
        source = resolved.dependency_graph.original_sources[rf.graph_key]
        modules.append(parse_module(source, str(rf.path)))
    return modules
