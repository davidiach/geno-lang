"""
Tests for ``geno.doc_generator``.

Focuses on the doc-comment-extraction helper that associates consecutive
``///`` comment blocks with the definition that follows them.  Adds a
regression for #663 / F-0025: the pre-fix code attached the comment
block to the very next line, so a single blank separator between the
comment block and the definition orphaned the documentation.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.doc_generator import _extract_doc_comments, generate_html, parse_module


class TestExtractDocComments:
    def test_single_comment_attaches_to_next_line(self):
        source = "/// Doubles its input.\nfunc double(x: Int) -> Int\n    return x * 2\nend func\n"
        docs = _extract_doc_comments(source)
        # ``func double`` is the 2nd line (1-based)
        assert docs.get(2) == "Doubles its input."

    def test_multi_line_comment_block(self):
        source = (
            "/// First line.\n"
            "/// Second line.\n"
            "func foo() -> Int\n"
            "    return 0\n"
            "end func\n"
        )
        docs = _extract_doc_comments(source)
        assert docs.get(3) == "First line.\nSecond line."

    def test_blank_line_between_comment_and_definition_is_skipped(self):
        """F-0025 regression: the code comment claimed blank lines were
        skipped, but the code attached the doc to whatever was on the
        very next line.  After the fix the comment jumps past blanks
        to the following definition."""
        source = (
            "/// Documented helper.\n\nfunc helper() -> Int\n    return 1\nend func\n"
        )
        docs = _extract_doc_comments(source)
        # ``func helper`` is now line 3 (1-based) with the blank at line 2
        assert docs.get(3) == "Documented helper."
        # And we must not have spuriously attached to the blank line
        assert 2 not in docs

    def test_multiple_blank_lines_between_comment_and_definition(self):
        source = (
            "/// Distant doc.\n\n\n\nfunc distant() -> Int\n    return 0\nend func\n"
        )
        docs = _extract_doc_comments(source)
        # ``func distant`` is line 5
        assert docs.get(5) == "Distant doc."

    def test_trailing_comment_block_with_no_following_definition(self):
        """A comment block at EOF with no definition must not crash and
        must not be recorded as attached to any line."""
        source = "/// Orphaned comment.\n"
        docs = _extract_doc_comments(source)
        assert docs == {}

    def test_non_doc_comment_lines_are_ignored(self):
        """Plain ``//`` comments are not doc comments."""
        source = "// not a doc comment\nfunc foo() -> Int\n    return 0\nend func\n"
        docs = _extract_doc_comments(source)
        assert docs == {}

    def test_adjacent_comment_blocks_separated_only_by_blank(self):
        """Two ``///`` blocks separated only by a blank line (no
        definition between them) must not silently drop the second
        block.  The first block has no owner — the helper drops it;
        the second block attaches to the following definition.

        Earlier iterations of the F-0025 fix overwrote the second
        block's mapping by having the first block's blank-skip chase
        past the second block and claim the same definition line.
        """
        source = (
            "/// First block.\n"
            "\n"
            "/// Second block.\n"
            "func foo() -> Int\n"
            "    return 0\n"
            "end func\n"
        )
        docs = _extract_doc_comments(source)
        # Second block attaches to func foo (line 4)
        assert docs.get(4) == "Second block."
        # First block has no owner — it must not squat on line 4
        assert docs.get(4) != "First block."

    def test_two_separate_comment_blocks_do_not_merge(self):
        source = (
            "/// First function doc.\n"
            "func first() -> Int\n"
            "    return 1\n"
            "end func\n"
            "\n"
            "/// Second function doc.\n"
            "func second() -> Int\n"
            "    return 2\n"
            "end func\n"
        )
        docs = _extract_doc_comments(source)
        assert docs.get(2) == "First function doc."
        assert docs.get(7) == "Second function doc."


class TestModuleDocGeneration:
    def test_function_signatures_include_effects(self):
        source = """
/// Calls an effectful callback and prints.
func call_and_print(cb: () -> String with io) -> Unit with io
    print(cb())
    return ()
end func call_and_print
"""

        module = parse_module(source, "Main.geno")
        html = generate_html([module])

        assert len(module.functions) == 1
        assert module.functions[0].effects == ["io"]
        assert "func</span> call_and_print" in html
        assert "() -&gt; String with io" in html
        assert 'Unit</span> <span class="keyword">with</span> io' in html

    def test_type_aliases_and_traits_are_documented(self):
        source = """
/// Alias docs
export type Label = String

/// Callback docs
type Callback = () -> String with io

/// Trait docs
trait Named
    func name(self: Self) -> String
end trait

/// Function docs
export func label(x: Label) -> Label
    example "hi" -> "hi"
    return x
end func label
"""

        module = parse_module(source, "Main.geno")
        html = generate_html([module])

        assert [alias.name for alias in module.type_aliases] == ["Label", "Callback"]
        assert [trait.name for trait in module.traits] == ["Named"]
        assert module.type_aliases[0].doc_comment == "Alias docs"
        assert module.type_aliases[0].exported is True
        assert module.type_aliases[1].doc_comment == "Callback docs"
        assert module.traits[0].doc_comment == "Trait docs"

        assert '<div class="section-label">Type Aliases</div>' in html
        assert 'href="#alias-Label"' in html
        assert "Alias docs" in html
        assert (
            '<span class="keyword">type</span> <span class="type-name">Label</span> = '
            in html
        )
        assert '<span class="type-name">String</span>' in html
        assert "() -&gt; String with io" in html
        assert '<div class="section-label">Traits</div>' in html
        assert 'href="#trait-Named"' in html
        assert "Trait docs" in html
        assert "func</span> name" in html
