"""
Geno Documentation Generator
=============================

Generates HTML documentation from Geno source files, extracting
function signatures, type definitions, examples, contracts,
impl blocks, and /// doc comments.
"""

import html as _html
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .ast_nodes import (
    ExampleClause,
    FunctionDef,
    FunctionType,
    ImplDef,
    Parameter,
    Program,
    SimpleType,
    TraitDef,
    TraitMethodSig,
    TypeAlias,
    TypeAnnotation,
    TypeDef,
)
from .lexer import Lexer
from .parser import Parser

# ---------------------------------------------------------------------------
# Type rendering
# ---------------------------------------------------------------------------


def _render_type(t: TypeAnnotation) -> str:
    """Render a type annotation as a human-readable string."""
    if isinstance(t, SimpleType):
        if t.type_params:
            params = ", ".join(_render_type(p) for p in t.type_params)
            return f"{t.name}[{params}]"
        return t.name
    if isinstance(t, FunctionType):
        params = ", ".join(_render_type(p) for p in t.param_types)
        ret = _render_type(t.return_type)
        base = f"({params}) -> {ret}"
        if t.effects:
            return f"{base} with {', '.join(sorted(t.effects))}"
        return base
    return str(t)


def _render_param(p: Parameter) -> str:
    """Render a parameter as 'name: Type'."""
    base = f"{p.name}: {_render_type(p.param_type)}"
    if p.default_value is not None:
        base += " = ..."
    return base


# ---------------------------------------------------------------------------
# Doc comment extraction
# ---------------------------------------------------------------------------


_DOC_COMMENT_RE = re.compile(r"^(\s*)///\s?(.*)")


def _extract_doc_comments(source: str) -> Dict[int, str]:
    """Extract /// doc comments, keyed by the line number of the definition
    that immediately follows the comment block.

    Returns {line_number: doc_text} where line_number is 1-based.

    Blank lines between the doc-comment block and the following
    definition are tolerated — before #663 / F-0025 the code's own
    comment claimed it skipped them, but it actually attached the doc
    comment to whatever came immediately after, so a single blank
    separator orphaned the doc.
    """
    lines = source.split("\n")
    result: Dict[int, str] = {}
    i = 0
    while i < len(lines):
        # Collect consecutive /// lines
        doc_lines: List[str] = []
        while i < len(lines):
            m = _DOC_COMMENT_RE.match(lines[i])
            if m:
                doc_lines.append(m.group(2))
                i += 1
            else:
                break
        if doc_lines:
            # Skip any blank lines between the doc-comment block and
            # the next definition so a stray blank separator does not
            # orphan the documentation.  If the blank-skip lands on
            # another ``///`` block instead of a definition, the first
            # block has no owner — drop it rather than letting the
            # next iteration overwrite the intended attachment of the
            # second block.
            def_line = i
            while def_line < len(lines) and not lines[def_line].strip():
                def_line += 1
            if def_line < len(lines) and not _DOC_COMMENT_RE.match(lines[def_line]):
                result[def_line + 1] = "\n".join(doc_lines)
            # Resume scanning at the first blank-skipped line so the
            # next iteration can pick up a second ``///`` block (or a
            # normal definition) without re-reading the one we just
            # consumed.
            i = def_line
        else:
            i += 1
    return result


# ---------------------------------------------------------------------------
# Expression rendering (for examples)
# ---------------------------------------------------------------------------


def _render_expr(expr) -> str:
    """Best-effort rendering of an AST expression node."""
    from .ast_nodes import (
        BinaryOp,
        BooleanLiteral,
        ConstructorCall,
        FloatLiteral,
        FunctionCall,
        Identifier,
        IntegerLiteral,
        ListLiteral,
        StringLiteral,
        TupleExpr,
        UnaryOp,
    )

    if isinstance(expr, IntegerLiteral):
        return str(expr.value)
    if isinstance(expr, FloatLiteral):
        return str(expr.value)
    if isinstance(expr, StringLiteral):
        return f'"{expr.value}"'
    if isinstance(expr, BooleanLiteral):
        return "true" if expr.value else "false"
    if isinstance(expr, Identifier):
        return expr.name
    if isinstance(expr, ListLiteral):
        elems = ", ".join(_render_expr(e) for e in expr.elements)
        return f"[{elems}]"
    if isinstance(expr, TupleExpr):
        elems = ", ".join(_render_expr(e) for e in expr.elements)
        return f"({elems})"
    if isinstance(expr, ConstructorCall):
        if expr.arguments:
            args = ", ".join(_render_expr(a) for a in expr.arguments)
            return f"{expr.constructor}({args})"
        return expr.constructor
    if isinstance(expr, FunctionCall):
        fn_name = _render_expr(expr.function)
        args = ", ".join(
            f"{a.name}: {_render_expr(a.value)}" if a.name else _render_expr(a.value)
            for a in expr.arguments
        )
        return f"{fn_name}({args})"
    if isinstance(expr, BinaryOp):
        return f"{_render_expr(expr.left)} {expr.operator} {_render_expr(expr.right)}"
    if isinstance(expr, UnaryOp):
        return f"{expr.operator}{_render_expr(expr.operand)}"
    return "..."


# ---------------------------------------------------------------------------
# Module documentation model
# ---------------------------------------------------------------------------


class FuncDoc:
    __slots__ = (
        "doc_comment",
        "effects",
        "ensures",
        "examples",
        "exported",
        "is_async",
        "name",
        "params",
        "requires",
        "return_type",
    )

    def __init__(self, fdef: FunctionDef, doc: str | None = None):
        self.name = fdef.name
        self.params = fdef.params
        self.return_type = fdef.return_type
        self.effects = fdef.effects
        self.examples = fdef.specs.examples if fdef.specs else []
        self.requires = fdef.specs.requires if fdef.specs else []
        self.ensures = fdef.specs.ensures if fdef.specs else []
        self.doc_comment = doc
        self.is_async = fdef.is_async
        self.exported = fdef.exported


class TypeDoc:
    __slots__ = ("doc_comment", "exported", "name", "type_params", "variants")

    def __init__(self, tdef: TypeDef, doc: str | None = None):
        self.name = tdef.name
        self.type_params = tdef.type_params
        self.variants = tdef.variants
        self.doc_comment = doc
        self.exported = tdef.exported


class TypeAliasDoc:
    __slots__ = ("doc_comment", "exported", "name", "target_type", "type_params")

    def __init__(self, alias: TypeAlias, doc: str | None = None):
        self.name = alias.name
        self.type_params = alias.type_params
        self.target_type = alias.target_type
        self.doc_comment = doc
        self.exported = alias.exported


class TraitMethodDoc:
    __slots__ = ("name", "params", "return_type")

    def __init__(self, method: TraitMethodSig):
        self.name = method.name
        self.params = method.params
        self.return_type = method.return_type


class TraitDoc:
    __slots__ = ("doc_comment", "methods", "name")

    def __init__(self, trait: TraitDef, doc: str | None = None):
        self.name = trait.name
        self.methods = [TraitMethodDoc(m) for m in trait.methods]
        self.doc_comment = doc


class ImplDoc:
    __slots__ = ("doc_comment", "methods", "target_type", "trait_name")

    def __init__(self, idef: ImplDef, doc: str | None = None):
        self.trait_name = idef.trait_name
        self.target_type = idef.target_type
        self.methods = [FuncDoc(m) for m in idef.methods]
        self.doc_comment = doc


class ModuleDoc:
    __slots__ = (
        "functions",
        "impls",
        "name",
        "path",
        "traits",
        "type_aliases",
        "types",
    )

    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.functions: List[FuncDoc] = []
        self.types: List[TypeDoc] = []
        self.type_aliases: List[TypeAliasDoc] = []
        self.traits: List[TraitDoc] = []
        self.impls: List[ImplDoc] = []


# ---------------------------------------------------------------------------
# Parse a file into a ModuleDoc
# ---------------------------------------------------------------------------


def parse_module(source: str, filename: str) -> ModuleDoc:
    """Parse a Geno source file and extract documentation."""
    doc_comments = _extract_doc_comments(source)

    lexer = Lexer(source, filename)
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    program = parser.parse_program()

    mod_name = Path(filename).stem
    mod = ModuleDoc(mod_name, filename)

    for defn in program.definitions:
        line = defn.location.line if hasattr(defn, "location") else 0
        doc = doc_comments.get(line)

        if isinstance(defn, FunctionDef):
            if defn.name != "main":
                mod.functions.append(FuncDoc(defn, doc))
        elif isinstance(defn, TypeDef):
            mod.types.append(TypeDoc(defn, doc))
        elif isinstance(defn, TypeAlias):
            mod.type_aliases.append(TypeAliasDoc(defn, doc))
        elif isinstance(defn, TraitDef):
            mod.traits.append(TraitDoc(defn, doc))
        elif isinstance(defn, ImplDef):
            mod.impls.append(ImplDoc(defn, doc))

    return mod


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

_CSS = """\
:root { --bg: #1a1a2e; --fg: #e0e0e0; --accent: #0f3460;
        --link: #53a8b6; --code-bg: #16213e; --border: #333; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg);
       color: var(--fg); line-height: 1.6; display: flex; min-height: 100vh; }
nav { width: 260px; background: var(--accent); padding: 1.5rem 1rem;
      position: fixed; top: 0; left: 0; bottom: 0; overflow-y: auto; }
nav h2 { color: #fff; margin-bottom: 1rem; font-size: 1.1rem; }
nav a { display: block; color: var(--link); text-decoration: none;
        padding: 0.2rem 0.4rem; border-radius: 4px; font-size: 0.9rem; }
nav a:hover { background: rgba(255,255,255,0.1); }
nav .section-label { color: #aaa; font-size: 0.75rem; text-transform: uppercase;
                     letter-spacing: 0.05em; margin-top: 0.8rem; margin-bottom: 0.2rem; }
main { margin-left: 260px; padding: 2rem 3rem; max-width: 900px; flex: 1; }
h1 { font-size: 1.8rem; margin-bottom: 1.5rem; border-bottom: 1px solid var(--border);
     padding-bottom: 0.5rem; }
h2 { font-size: 1.3rem; margin-top: 2rem; margin-bottom: 0.8rem;
     color: var(--link); }
h3 { font-size: 1.05rem; margin-top: 1.2rem; margin-bottom: 0.4rem; }
.doc-comment { color: #aaa; margin-bottom: 0.5rem; font-style: italic; }
.signature { background: var(--code-bg); padding: 0.6rem 1rem; border-radius: 6px;
             font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 0.9rem;
             overflow-x: auto; margin-bottom: 0.5rem; border: 1px solid var(--border); }
.keyword { color: #c792ea; }
.type-name { color: #82aaff; }
.param-name { color: #f78c6c; }
.example { background: var(--code-bg); padding: 0.4rem 0.8rem; border-radius: 4px;
           font-family: monospace; font-size: 0.85rem; margin: 0.2rem 0;
           border-left: 3px solid var(--link); }
.contract { font-family: monospace; font-size: 0.85rem; color: #c3e88d; margin: 0.2rem 0; }
.variant { font-family: monospace; font-size: 0.9rem; margin: 0.2rem 0 0.2rem 1rem; }
.badge { display: inline-block; font-size: 0.7rem; padding: 0.1rem 0.4rem;
         border-radius: 3px; margin-left: 0.5rem; vertical-align: middle; }
.badge-async { background: #c792ea33; color: #c792ea; }
.badge-export { background: #c3e88d33; color: #c3e88d; }
.impl-block { border: 1px solid var(--border); border-radius: 6px;
              padding: 1rem; margin: 0.5rem 0; background: rgba(255,255,255,0.02); }
.module-link { font-weight: bold; }
"""


def _esc(text: str) -> str:
    return _html.escape(text)


def _render_signature_html(func: FuncDoc) -> str:
    """Render a function signature with syntax highlighting."""
    kw = '<span class="keyword">'
    tn = '<span class="type-name">'
    pn = '<span class="param-name">'
    end = "</span>"

    prefix = f"{kw}async func{end}" if func.is_async else f"{kw}func{end}"
    params_parts = []
    for p in func.params:
        params_parts.append(
            f"{pn}{_esc(p.name)}{end}: {tn}{_esc(_render_type(p.param_type))}{end}"
        )
    params_str = ", ".join(params_parts)
    ret = f"{tn}{_esc(_render_type(func.return_type))}{end}"
    effects = ""
    if func.effects:
        effects = f" {kw}with{end} {_esc(', '.join(sorted(func.effects)))}"
    return f"{prefix} {_esc(func.name)}({params_str}) -> {ret}{effects}"


def _render_func_html(func: FuncDoc) -> str:
    """Render a complete function documentation block."""
    parts = [f'<div class="func-doc" id="fn-{_esc(func.name)}">']

    # Header
    badges = ""
    if func.is_async:
        badges += '<span class="badge badge-async">async</span>'
    if func.exported:
        badges += '<span class="badge badge-export">export</span>'
    parts.append(f"<h3>{_esc(func.name)}{badges}</h3>")

    # Doc comment
    if func.doc_comment:
        parts.append(f'<p class="doc-comment">{_esc(func.doc_comment)}</p>')

    # Signature
    parts.append(f'<div class="signature">{_render_signature_html(func)}</div>')

    # Examples
    if func.examples:
        parts.append("<h4>Examples</h4>")
        for ex in func.examples:
            inp = _esc(_render_expr(ex.input_expr))
            out = _esc(_render_expr(ex.output_expr))
            parts.append(f'<div class="example">{inp} &rarr; {out}</div>')

    # Contracts
    if func.requires:
        parts.append("<h4>Requires</h4>")
        for req in func.requires:
            parts.append(
                f'<div class="contract">requires {_esc(_render_expr(req.condition))}</div>'
            )
    if func.ensures:
        parts.append("<h4>Ensures</h4>")
        for ens in func.ensures:
            parts.append(
                f'<div class="contract">ensures {_esc(_render_expr(ens.condition))}</div>'
            )

    parts.append("</div>")
    return "\n".join(parts)


def _render_type_html(tdef: TypeDoc) -> str:
    """Render a type definition documentation block."""
    parts = [f'<div class="type-doc" id="type-{_esc(tdef.name)}">']

    badges = ""
    if tdef.exported:
        badges += '<span class="badge badge-export">export</span>'

    tp = ""
    if tdef.type_params:
        tp = f"[{', '.join(tdef.type_params)}]"
    parts.append(f"<h3>{_esc(tdef.name)}{_esc(tp)}{badges}</h3>")

    if tdef.doc_comment:
        parts.append(f'<p class="doc-comment">{_esc(tdef.doc_comment)}</p>')

    for v in tdef.variants:
        if v.fields:
            fields = ", ".join(
                f"{_esc(fn)}: {_esc(_render_type(ft))}" for fn, ft in v.fields
            )
            parts.append(f'<div class="variant">| {_esc(v.name)}({fields})</div>')
        else:
            parts.append(f'<div class="variant">| {_esc(v.name)}</div>')

    parts.append("</div>")
    return "\n".join(parts)


def _render_type_alias_html(alias: TypeAliasDoc) -> str:
    """Render a type alias documentation block."""
    parts = [f'<div class="type-doc" id="alias-{_esc(alias.name)}">']

    badges = ""
    if alias.exported:
        badges += '<span class="badge badge-export">export</span>'

    tp = ""
    if alias.type_params:
        tp = f"[{', '.join(alias.type_params)}]"
    parts.append(f"<h3>{_esc(alias.name)}{_esc(tp)}{badges}</h3>")

    if alias.doc_comment:
        parts.append(f'<p class="doc-comment">{_esc(alias.doc_comment)}</p>')

    parts.append(
        f'<div class="signature"><span class="keyword">type</span> '
        f'<span class="type-name">{_esc(alias.name)}{_esc(tp)}</span> = '
        f'<span class="type-name">{_esc(_render_type(alias.target_type))}</span></div>'
    )

    parts.append("</div>")
    return "\n".join(parts)


def _render_trait_method_signature_html(method: TraitMethodDoc) -> str:
    """Render a trait method signature with syntax highlighting."""
    kw = '<span class="keyword">'
    tn = '<span class="type-name">'
    pn = '<span class="param-name">'
    end = "</span>"

    params_parts = []
    for p in method.params:
        params_parts.append(
            f"{pn}{_esc(p.name)}{end}: {tn}{_esc(_render_type(p.param_type))}{end}"
        )
    params_str = ", ".join(params_parts)
    ret = f"{tn}{_esc(_render_type(method.return_type))}{end}"
    return f"{kw}func{end} {_esc(method.name)}({params_str}) -> {ret}"


def _render_trait_html(trait: TraitDoc) -> str:
    """Render a trait documentation block."""
    parts = [f'<div class="trait-doc" id="trait-{_esc(trait.name)}">']
    parts.append(f"<h3>{_esc(trait.name)}</h3>")

    if trait.doc_comment:
        parts.append(f'<p class="doc-comment">{_esc(trait.doc_comment)}</p>')

    for method in trait.methods:
        parts.append(
            f'<div class="signature">{_render_trait_method_signature_html(method)}</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)


def _render_impl_html(idef: ImplDoc) -> str:
    """Render an impl block."""
    parts = [
        f'<div class="impl-block" id="impl-{_esc(idef.trait_name)}-{_esc(idef.target_type)}">'
    ]
    parts.append(f"<h3>impl {_esc(idef.trait_name)} for {_esc(idef.target_type)}</h3>")

    if idef.doc_comment:
        parts.append(f'<p class="doc-comment">{_esc(idef.doc_comment)}</p>')

    for method in idef.methods:
        parts.append(_render_func_html(method))

    parts.append("</div>")
    return "\n".join(parts)


def _render_module_html(mod: ModuleDoc) -> str:
    """Render a single module's documentation."""
    parts = [f"<h1>{_esc(mod.name)}</h1>"]
    parts.append(f'<p class="doc-comment">Source: {_esc(mod.path)}</p>')

    if mod.types:
        parts.append("<h2>Types</h2>")
        for tdef in mod.types:
            parts.append(_render_type_html(tdef))

    if mod.type_aliases:
        parts.append("<h2>Type Aliases</h2>")
        for alias in mod.type_aliases:
            parts.append(_render_type_alias_html(alias))

    if mod.traits:
        parts.append("<h2>Traits</h2>")
        for trait in mod.traits:
            parts.append(_render_trait_html(trait))

    if mod.functions:
        parts.append("<h2>Functions</h2>")
        for func in mod.functions:
            parts.append(_render_func_html(func))

    if mod.impls:
        parts.append("<h2>Implementations</h2>")
        for idef in mod.impls:
            parts.append(_render_impl_html(idef))

    return "\n".join(parts)


def generate_html(modules: List[ModuleDoc], title: str = "Geno Documentation") -> str:
    """Generate a complete HTML documentation page for multiple modules."""
    # Navigation
    nav_parts = [f"<h2>{_esc(title)}</h2>"]
    for mod in modules:
        nav_parts.append(
            f'<a class="module-link" href="#mod-{_esc(mod.name)}">{_esc(mod.name)}</a>'
        )
        if mod.types:
            nav_parts.append('<div class="section-label">Types</div>')
            for type_doc in mod.types:
                nav_parts.append(
                    f'<a href="#type-{_esc(type_doc.name)}">{_esc(type_doc.name)}</a>'
                )
        if mod.type_aliases:
            nav_parts.append('<div class="section-label">Type Aliases</div>')
            for alias in mod.type_aliases:
                nav_parts.append(
                    f'<a href="#alias-{_esc(alias.name)}">{_esc(alias.name)}</a>'
                )
        if mod.traits:
            nav_parts.append('<div class="section-label">Traits</div>')
            for trait in mod.traits:
                nav_parts.append(
                    f'<a href="#trait-{_esc(trait.name)}">{_esc(trait.name)}</a>'
                )
        if mod.functions:
            nav_parts.append('<div class="section-label">Functions</div>')
            for func in mod.functions:
                nav_parts.append(
                    f'<a href="#fn-{_esc(func.name)}">{_esc(func.name)}</a>'
                )
        if mod.impls:
            nav_parts.append('<div class="section-label">Impl</div>')
            for impl in mod.impls:
                nav_parts.append(
                    f'<a href="#impl-{_esc(impl.trait_name)}-{_esc(impl.target_type)}">'
                    f"{_esc(impl.trait_name)} for {_esc(impl.target_type)}</a>"
                )

    nav_html = "\n".join(nav_parts)

    # Main content
    main_parts = []
    for mod in modules:
        main_parts.append(f'<section id="mod-{_esc(mod.name)}">')
        main_parts.append(_render_module_html(mod))
        main_parts.append("</section>")
    main_html = "\n".join(main_parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<nav>{nav_html}</nav>
<main>{main_html}</main>
</body>
</html>"""
