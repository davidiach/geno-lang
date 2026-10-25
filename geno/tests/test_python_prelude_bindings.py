"""Preserve the compiler's module binding safety set without a full Python AST."""

from __future__ import annotations

import ast

import pytest

from geno.compiler import (
    _python_prelude_binding_names,
    _uncached_python_prelude_binding_names,
)
from geno.runtime_prelude import RUNTIME_PRELUDE


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import os.path\nimport math as maths\nfrom io import StringIO as stream\n",
            {"os", "maths", "stream"},
        ),
        (
            "first = second = 1\nleft, [middle, tail] = value\nannotated: int\n",
            {"first", "second", "left", "middle", "tail", "annotated"},
        ),
        ("if condition:\n    yes = 1\nelse:\n    no = 2\n", {"yes", "no"}),
        (
            "try:\n    from math import inf\nexcept ImportError as error:\n    inf = 0\n"
            "else:\n    success = True\nfinally:\n    done = True\n",
            {"inf", "error", "success", "done"},
        ),
        (
            "for item in items:\n    inside = item\nelse:\n    outside = 0\n"
            "with context() as handle:\n    loaded = handle.read()\n",
            {"item", "inside", "outside", "handle", "loaded"},
        ),
        (
            "def function(parameter):\n    local = parameter\n    return external\n"
            "async def async_function():\n    return free_name\n"
            "class Kind:\n    member = 1\n",
            {"function", "async_function", "Kind"},
        ),
        ("values = [item for item in source]\nprint(values)\n", {"values"}),
        ("raise RuntimeError('must not execute')\nbound = unknown\n", {"bound"}),
    ],
)
def test_module_bindings_exclude_reference_names_and_nested_locals(
    source: str, expected: set[str]
) -> None:
    assert _python_prelude_binding_names(source) == frozenset(expected)


def _ast_module_bindings(node: ast.AST) -> set[str]:
    """Independent AST oracle: stop at each nested function/class scope."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return {node.id}
    if isinstance(node, ast.alias):
        return {node.asname or node.name.split(".", 1)[0]}
    names = {node.name} if isinstance(node, ast.ExceptHandler) and node.name else set()
    for child in ast.iter_child_nodes(node):
        names.update(_ast_module_bindings(child))
    return names


def test_shipped_prelude_preserves_every_module_binding() -> None:
    # Compare different parsing interfaces on the actual security-sensitive input.
    expected = _ast_module_bindings(ast.parse(RUNTIME_PRELUDE))
    assert _python_prelude_binding_names(RUNTIME_PRELUDE) == frozenset(expected)


def test_modified_prelude_cannot_reuse_stale_bindings() -> None:
    source = RUNTIME_PRELUDE + "\nnew_security_helper = object()\n"
    names = _python_prelude_binding_names(source)
    assert names == _uncached_python_prelude_binding_names(source)
    assert "new_security_helper" in names


def test_unchanged_prelude_uses_generated_inventory(monkeypatch) -> None:
    from geno import compiler

    def unexpected_parse(source: str) -> frozenset[str]:
        raise AssertionError("unchanged prelude should not need an AST")

    monkeypatch.setattr(
        compiler, "_uncached_python_prelude_binding_names", unexpected_parse
    )
    assert compiler._python_prelude_binding_names(RUNTIME_PRELUDE)


def test_invalid_modified_prelude_still_fails_closed() -> None:
    with pytest.raises(SyntaxError):
        _python_prelude_binding_names(RUNTIME_PRELUDE + "\ninvalid Python !!!\n")


def test_generated_inventory_matches_exact_source() -> None:
    import hashlib

    from geno._prelude_bindings import PRELUDE_BINDING_NAMES, PRELUDE_SOURCE_SHA256

    assert (
        hashlib.sha256(RUNTIME_PRELUDE.encode("utf-8")).hexdigest()
        == PRELUDE_SOURCE_SHA256
    )
    assert (
        _uncached_python_prelude_binding_names(RUNTIME_PRELUDE) == PRELUDE_BINDING_NAMES
    )


def test_generated_inventory_is_reproducible() -> None:
    from scripts.update_prelude_bindings import OUTPUT, render_bindings

    assert OUTPUT.read_text(encoding="utf-8") == render_bindings(RUNTIME_PRELUDE)
