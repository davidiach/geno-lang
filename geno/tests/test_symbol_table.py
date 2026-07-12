"""
Dedicated tests for symbol_table.py — scope management, symbol resolution,
and the SymbolTable query API.

Complements the LSP-focused tests in test_lsp.py::TestSymbolTableScoping
with standalone unit tests for the public API.
"""

import pytest

from geno.lexer import Lexer
from geno.parser import Parser
from geno.symbol_table import (
    Scope,
    SymbolDef,
    SymbolRef,
    SymbolTable,
    build_symbol_table,
)
from geno.tokens import SourceLocation


def _build(source: str, filename: str = "test.geno", modules=None):
    """Parse source and build a symbol table."""
    tokens = Lexer(source, filename).tokenize()
    program = Parser(tokens).parse_program()
    parsed_modules = None
    if modules:
        parsed_modules = {}
        for mod_name, mod_source in modules.items():
            mod_tokens = Lexer(mod_source, f"{mod_name}.geno").tokenize()
            parsed_modules[mod_name] = Parser(mod_tokens).parse_program()
    return build_symbol_table(program, filename, parsed_modules)


# ---------------------------------------------------------------------------
# Scope (unit tests for the scope chain)
# ---------------------------------------------------------------------------


class TestScope:
    """Test Scope binding and lookup mechanics."""

    def test_bind_and_lookup(self):
        loc = SourceLocation("<test>", 1, 1)
        scope = Scope()
        defn = SymbolDef("x", loc, "variable")
        scope.bind("x", defn)
        assert scope.lookup("x") is defn

    def test_lookup_misses_unbound(self):
        scope = Scope()
        assert scope.lookup("nonexistent") is None

    def test_child_inherits_parent(self):
        loc = SourceLocation("<test>", 1, 1)
        parent = Scope()
        defn = SymbolDef("x", loc, "variable")
        parent.bind("x", defn)
        child = parent.child()
        assert child.lookup("x") is defn

    def test_child_shadows_parent(self):
        loc1 = SourceLocation("<test>", 1, 1)
        loc2 = SourceLocation("<test>", 2, 1)
        parent = Scope()
        parent.bind("x", SymbolDef("x", loc1, "variable"))
        child = parent.child()
        child_defn = SymbolDef("x", loc2, "variable")
        child.bind("x", child_defn)
        assert child.lookup("x") is child_defn
        # Parent still has original
        parent_defn = parent.lookup("x")
        assert parent_defn is not None
        assert parent_defn.location == loc1

    def test_deep_nesting(self):
        loc = SourceLocation("<test>", 1, 1)
        root = Scope()
        root.bind("x", SymbolDef("x", loc, "variable"))
        scope = root
        for _ in range(10):
            scope = scope.child()
        assert scope.lookup("x") is not None


# ---------------------------------------------------------------------------
# SymbolTable query methods
# ---------------------------------------------------------------------------


class TestSymbolTableQueries:
    """Test SymbolTable.def_at, ref_at, symbol_at, all_locations."""

    def _make_table(self):
        loc_def = SourceLocation("test.geno", 1, 5)
        loc_ref = SourceLocation("test.geno", 3, 10)
        defn = SymbolDef("foo", loc_def, "function")
        ref = SymbolRef("foo", loc_ref, defn)
        table = SymbolTable(definitions=[defn], references=[ref])
        return table, defn, ref

    def test_def_at_exact(self):
        table, defn, _ = self._make_table()
        found = table.def_at("test.geno", 1, 5)
        assert found is defn

    def test_def_at_within_name(self):
        table, defn, _ = self._make_table()
        # Column 6 is within "foo" (columns 5, 6, 7)
        found = table.def_at("test.geno", 1, 6)
        assert found is defn

    def test_def_at_outside_name(self):
        table, _, _ = self._make_table()
        assert table.def_at("test.geno", 1, 8) is None

    def test_def_at_wrong_file(self):
        table, _, _ = self._make_table()
        assert table.def_at("other.geno", 1, 5) is None

    def test_ref_at_exact(self):
        table, _, ref = self._make_table()
        found = table.ref_at("test.geno", 3, 10)
        assert found is ref

    def test_ref_at_wrong_line(self):
        table, _, _ = self._make_table()
        assert table.ref_at("test.geno", 5, 10) is None

    def test_symbol_at_on_definition(self):
        table, defn, _ = self._make_table()
        found = table.symbol_at("test.geno", 1, 5)
        assert found is defn

    def test_symbol_at_on_reference(self):
        table, defn, _ = self._make_table()
        found = table.symbol_at("test.geno", 3, 10)
        assert found is defn  # returns the definition, not the ref

    def test_symbol_at_fallback_by_name(self):
        loc = SourceLocation("test.geno", 5, 1)
        defn = SymbolDef("bar", loc, "function")
        table = SymbolTable(definitions=[defn])
        # col=20 doesn't match, but name="bar" on same line should work
        found = table.symbol_at("test.geno", 5, 20, name="bar")
        assert found is defn

    def test_symbol_at_returns_none(self):
        table = SymbolTable()
        assert table.symbol_at("test.geno", 99, 99) is None

    def test_refs_for_def(self):
        table, defn, ref = self._make_table()
        refs = table.refs_for_def(defn)
        assert len(refs) == 1
        assert refs[0] is ref

    def test_all_locations(self):
        table, defn, ref = self._make_table()
        locs = table.all_locations(defn)
        assert len(locs) == 2
        assert defn.location in locs
        assert ref.location in locs


# ---------------------------------------------------------------------------
# build_symbol_table integration
# ---------------------------------------------------------------------------


class TestBuildSymbolTable:
    """Integration tests for the full builder pipeline."""

    def test_empty_program(self):
        table = _build("")
        assert table.definitions == []
        assert table.references == []

    def test_function_definition_registered(self):
        source = 'func greet() -> String\n    return "hi"\nend func\n'
        table = _build(source)
        func_defs = [d for d in table.definitions if d.kind == "function"]
        assert any(d.name == "greet" for d in func_defs)

    def test_variable_definition_registered(self):
        source = "func main() -> Int\n    let x: Int = 42\n    return x\nend func\n"
        table = _build(source)
        var_defs = [d for d in table.definitions if d.kind == "variable"]
        assert any(d.name == "x" for d in var_defs)

    def test_reference_resolved(self):
        source = "func add_one(n: Int) -> Int\n    return n + 1\nend func\n"
        table = _build(source)
        n_refs = [r for r in table.references if r.name == "n"]
        assert len(n_refs) > 0
        # Reference should point to the parameter definition
        assert n_refs[0].definition.kind == "parameter"

    def test_type_definition(self):
        source = (
            "type Color = Red | Green | Blue\n"
            "\n"
            "func main() -> Int\n    return 0\nend func\n"
        )
        table = _build(source)
        type_defs = [d for d in table.definitions if d.kind == "type"]
        assert any(d.name == "Color" for d in type_defs)
        # Constructors
        ctor_defs = [d for d in table.definitions if d.kind == "constructor"]
        ctor_names = {d.name for d in ctor_defs}
        assert {"Red", "Green", "Blue"} <= ctor_names

    def test_module_import(self):
        mod_source = "func helper(x: Int) -> Int\n    return 1\nend func\n"
        source = "import Utils\nfunc main() -> Int\n    return helper(1)\nend func\n"
        table = _build(source, modules={"Utils": mod_source})
        mod_defs = [d for d in table.definitions if d.kind == "module"]
        assert any(d.name == "Utils" for d in mod_defs)


class TestNewlyCoveredStatementsAndPatterns:
    """Regression tests for #663 / F-0018 + F-0019: symbol-table scope
    resolution must walk ``IndexAssignStatement``, ``FieldAssignStatement``,
    and ``AssertStatement`` bodies, and bind names introduced by
    ``ListPattern`` / ``RestPattern``.  Before the fix the resolver
    silently ignored them, so rename / find-references quietly missed
    those symbols."""

    def test_index_assign_references_are_recorded(self):
        source = (
            "func main() -> Int\n"
            "    var xs: Array[Int] = Array(3, 0)\n"
            "    let i: Int = 2\n"
            "    xs[i] = 7\n"
            "    return 0\n"
            "end func\n"
        )
        table = _build(source)
        # Both xs (the target) and i (the index) must appear as refs
        # at the ``xs[i] = 7`` statement.
        xs_refs = [r for r in table.references if r.name == "xs"]
        i_refs = [r for r in table.references if r.name == "i"]
        assert xs_refs, "IndexAssignStatement target reference should be recorded"
        assert i_refs, "IndexAssignStatement index reference should be recorded"

    def test_field_assign_references_are_recorded(self):
        source = (
            "type Box = MkBox(value: Int)\n"
            "\n"
            "func main() -> Int\n"
            "    var b: Box = MkBox(1)\n"
            "    b.value = 5\n"
            "    return 0\n"
            "end func\n"
        )
        table = _build(source)
        # The ``b`` on the LHS of the field assignment is an identifier
        # use and must appear in the references table.
        b_refs = [r for r in table.references if r.name == "b"]
        assert b_refs, "FieldAssignStatement target reference should be recorded"

    def test_assert_statement_expression_is_resolved(self):
        source = (
            "func add(a: Int, b: Int) -> Int\n"
            "    example 1, 2 -> 3\n"
            "    return a + b\n"
            "end func\n"
            "\n"
            'test "add check"\n'
            "    assert add(1, 2) == 3\n"
            "end test\n"
        )
        table = _build(source)
        add_refs = [r for r in table.references if r.name == "add"]
        assert add_refs, "assert-expression must resolve its identifier uses"

    def test_list_pattern_binds_element_names(self):
        source = (
            "func head_or_zero(xs: List[Int]) -> Int\n"
            "    example [1, 2, 3] -> 1\n"
            "    match xs with\n"
            "        | [h, ...rest] -> return h\n"
            "        | _ -> return 0\n"
            "    end match\n"
            "end func\n"
        )
        table = _build(source)
        var_defs = {d.name for d in table.definitions if d.kind == "variable"}
        # Before the fix, neither ``h`` nor ``rest`` was recorded — the
        # resolver skipped the list/rest pattern entirely.
        assert "h" in var_defs, "ListPattern element should bind variable"
        assert "rest" in var_defs, "RestPattern name should bind variable"

    def test_anonymous_rest_pattern_binds_nothing(self):
        """``| [first, ...] -> ...`` binds ``first`` but not an
        anonymous rest — ``pattern.name is None`` in that case."""
        source = (
            "func first(xs: List[Int]) -> Int\n"
            "    example [1, 2] -> 1\n"
            "    match xs with\n"
            "        | [first, ...] -> return first\n"
            "        | _ -> return 0\n"
            "    end match\n"
            "end func\n"
        )
        table = _build(source)
        var_defs = {d.name for d in table.definitions if d.kind == "variable"}
        assert "first" in var_defs
        # No stray bindings for the anonymous rest pattern
        assert "..." not in var_defs


class TestBlockScopes:
    """Block bodies should not leak local bindings into sibling statements."""

    def test_if_body_local_does_not_resolve_after_block(self):
        source = (
            "func main() -> Int\n"
            "    if true then\n"
            "        let x: Int = 1\n"
            "    end if\n"
            "    return x\n"
            "end func\n"
        )
        table = _build(source)

        assert table.symbol_at("test.geno", 5, 12, name="x") is None

    def test_else_body_local_does_not_resolve_after_block(self):
        source = (
            "func main() -> Int\n"
            "    if true then\n"
            "        let a: Int = 1\n"
            "    else\n"
            "        let x: Int = 2\n"
            "    end if\n"
            "    return x\n"
            "end func\n"
        )
        table = _build(source)

        assert table.symbol_at("test.geno", 7, 12, name="x") is None

    def test_while_body_local_does_not_resolve_after_block(self):
        source = (
            "func main() -> Int\n"
            "    while true do\n"
            "        let x: Int = 1\n"
            "    end while\n"
            "    return x\n"
            "end func\n"
        )
        table = _build(source)

        assert table.symbol_at("test.geno", 5, 12, name="x") is None

    def test_try_body_local_does_not_resolve_after_block(self):
        source = (
            "func main() -> Int\n"
            "    try\n"
            "        let x: Int = 1\n"
            "    catch e: String\n"
            "        return 0\n"
            "    end try\n"
            "    return x\n"
            "end func\n"
        )
        table = _build(source)

        assert table.symbol_at("test.geno", 7, 12, name="x") is None

    def test_block_local_reference_still_resolves_inside_block(self):
        source = (
            "func main() -> Int\n"
            "    if true then\n"
            "        let x: Int = 1\n"
            "        return x\n"
            "    end if\n"
            "    return 0\n"
            "end func\n"
        )
        table = _build(source)
        found = table.symbol_at("test.geno", 4, 16, name="x")

        assert found is not None
        assert found.kind == "variable"
        assert found.location.line == 3
