"""Tests for geno fmt auto-formatter."""

import pytest

from geno.formatter import format_source


class TestBasicIndentation:
    def test_function_body(self):
        source = (
            "func add(x: Int, y: Int) -> Int\n"
            "example 1, 2 -> 3\n"
            "return x + y\n"
            "end func\n"
        )
        expected = (
            "func add(x: Int, y: Int) -> Int\n"
            "    example 1, 2 -> 3\n"
            "    return x + y\n"
            "end func\n"
        )
        assert format_source(source) == expected

    def test_if_else(self):
        source = (
            "func abs(x: Int) -> Int\n"
            "example -1 -> 1\n"
            "if x < 0 then\n"
            "return -x\n"
            "else\n"
            "return x\n"
            "end if\n"
            "end func\n"
        )
        result = format_source(source)
        lines = result.strip().split("\n")
        assert lines[0] == "func abs(x: Int) -> Int"
        assert lines[1] == "    example -1 -> 1"
        assert lines[2] == "    if x < 0 then"
        assert lines[3] == "        return -x"
        assert lines[4] == "    else"
        assert lines[5] == "        return x"
        assert lines[6] == "    end if"
        assert lines[7] == "end func"

    def test_match_block(self):
        source = (
            "func f(x: Int) -> Int\n"
            "example 1 -> 1\n"
            "match x with\n"
            "| 0 -> return 0\n"
            "| _ -> return x\n"
            "end match\n"
            "end func\n"
        )
        result = format_source(source)
        lines = result.strip().split("\n")
        assert lines[2] == "    match x with"
        assert lines[3] == "        | 0 -> return 0"
        assert lines[4] == "        | _ -> return x"
        assert lines[5] == "    end match"

    def test_nested_blocks(self):
        source = (
            "func f(x: Int) -> Int\n"
            "example 1 -> 1\n"
            "if x > 0 then\n"
            "while x > 0 do\n"
            "return x\n"
            "end while\n"
            "end if\n"
            "return 0\n"
            "end func\n"
        )
        result = format_source(source)
        lines = result.strip().split("\n")
        assert lines[3] == "        while x > 0 do"
        assert lines[4] == "            return x"
        assert lines[5] == "        end while"

    def test_try_catch(self):
        source = (
            "func f(x: Int) -> Int\n"
            "example 1 -> 1\n"
            "try\n"
            "return x\n"
            "catch e: String\n"
            "return 0\n"
            "end try\n"
            "end func\n"
        )
        result = format_source(source)
        lines = result.strip().split("\n")
        assert lines[2] == "    try"
        assert lines[3] == "        return x"
        assert lines[4] == "    catch e: String"
        assert lines[5] == "        return 0"
        assert lines[6] == "    end try"


class TestCommentPreservation:
    def test_line_comments_preserved(self):
        source = (
            "// This is a comment\n"
            "func f(x: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    // inner comment\n"
            "    return x\n"
            "end func\n"
        )
        result = format_source(source)
        assert "// This is a comment" in result
        assert "// inner comment" in result

    def test_blank_lines_preserved(self):
        source = (
            "func f(x: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    return x\n"
            "end func\n"
            "\n"
            "func g(x: Int) -> Int\n"
            "    example 2 -> 2\n"
            "    return x\n"
            "end func\n"
        )
        result = format_source(source)
        assert "\n\n" in result


class TestIdempotency:
    def test_already_formatted(self):
        source = (
            "func add(x: Int, y: Int) -> Int\n"
            "    example 1, 2 -> 3\n"
            "    return x + y\n"
            "end func\n"
        )
        assert format_source(source) == source

    def test_double_format(self):
        source = (
            "func f(x: Int) -> Int\n"
            "example 1 -> 1\n"
            "if x > 0 then\n"
            "return x\n"
            "else\n"
            "return -x\n"
            "end if\n"
            "end func\n"
        )
        once = format_source(source)
        twice = format_source(once)
        assert once == twice


class TestEdgeCases:
    def test_empty_source(self):
        assert format_source("") == "\n"

    def test_type_definition(self):
        source = "type Color = Red\n| Green\n| Blue\n"
        result = format_source(source)
        # type opens a block, | lines get indented
        assert "    | Green" in result
        assert "    | Blue" in result

    def test_trait_and_impl(self):
        source = (
            "trait Describable\n"
            "func describe(self: Self) -> String\n"
            "end trait\n"
            "\n"
            "impl Describable for Int\n"
            "func describe(self: Int) -> String\n"
            'example 5 -> "5"\n'
            "return to_string(self)\n"
            "end func\n"
            "end impl\n"
        )
        result = format_source(source)
        lines = result.strip().split("\n")
        assert lines[0] == "trait Describable"
        assert lines[1] == "    func describe(self: Self) -> String"
        assert lines[2] == "end trait"

    def test_test_block(self):
        source = 'test "double works"\nassert double(2) == 4\nend test\n'
        result = format_source(source)
        lines = result.strip().split("\n")
        assert lines[0] == 'test "double works"'
        assert lines[1] == "    assert double(2) == 4"
        assert lines[2] == "end test"

    def test_for_loop(self):
        source = (
            "func f(items: List[Int]) -> Int\n"
            "example [1] -> 1\n"
            "for item in items do\n"
            "return item\n"
            "end for\n"
            "return 0\n"
            "end func\n"
        )
        result = format_source(source)
        lines = result.strip().split("\n")
        assert lines[2] == "    for item in items do"
        assert lines[3] == "        return item"
        assert lines[4] == "    end for"

    def test_trailing_newline(self):
        source = "func f(x: Int) -> Int\n    example 1 -> 1\n    return x\nend func"
        result = format_source(source)
        assert result.endswith("\n")
        assert not result.endswith("\n\n")

    def test_inline_if_then_end_if(self):
        source = (
            "func clamp(value: Int, lo: Int, hi: Int) -> Int\n"
            "    example 5, 0, 10 -> 5\n"
            "    if value < lo then return lo end if\n"
            "    if value > hi then return hi end if\n"
            "    return value\n"
            "end func\n"
        )
        result = format_source(source)
        lines = result.strip().split("\n")
        # Both inline ifs should be at the same indent level
        assert lines[2] == "    if value < lo then return lo end if"
        assert lines[3] == "    if value > hi then return hi end if"

    def test_string_contents_do_not_close_inline_block(self):
        source = (
            "func f(message: String) -> Int\n"
            'if message == "end if" then\n'
            "return 1\n"
            "end if\n"
            "return 0\n"
            "end func\n"
        )
        result = format_source(source)
        lines = result.strip().split("\n")
        assert lines[1] == '    if message == "end if" then'
        assert lines[2] == "        return 1"
        assert lines[3] == "    end if"
        assert lines[4] == "    return 0"
        assert lines[5] == "end func"

    def test_import_at_top_level(self):
        source = "import Utils\n\nfunc main() -> Int\n    return 42\nend func\n"
        result = format_source(source)
        assert result.startswith("import Utils\n")


class TestBlockCommentEdgeCases:
    def test_block_comment_marker_inside_string(self):
        """/* inside a string literal should NOT trigger block comment mode."""
        source = (
            "func check_val() -> String\n"
            '    example () -> "a"\n'
            '    let s: String = "hello /* world"\n'
            "    return s\n"
            "end func\n"
        )
        result = format_source(source)
        lines = result.strip().split("\n")
        # The return should still be indented inside the function, not
        # corrupted by a false block-comment detection
        assert lines[2] == '    let s: String = "hello /* world"'
        assert lines[3] == "    return s"
        assert lines[4] == "end func"

    def test_real_block_comment_still_works(self):
        """A real block comment should still be handled."""
        source = "/* this is\na comment */\nfunc foo() -> Int\nreturn 1\nend func\n"
        result = format_source(source)
        lines = result.strip().split("\n")
        assert lines[0] == "/* this is"
        assert lines[1] == "a comment */"
        assert lines[3] == "    return 1"


class TestTypeVariantBlankLines:
    def test_blank_line_between_variants(self):
        """Blank line between type variants should preserve indentation."""
        source = "type Color = Red\n\n| Green\n| Blue\n"
        result = format_source(source)
        lines = result.strip().split("\n")
        # Variants after blank line should still be indented
        assert lines[2] == "    | Green"
        assert lines[3] == "    | Blue"

    def test_comment_between_variants(self):
        """Comments between type variants should preserve type-list indentation."""
        source = (
            "type Color = Red\n"
            "// secondary colors\n"
            "| Green\n"
            "/* terminal color */\n"
            "| Blue\n"
        )
        result = format_source(source)
        lines = result.strip().split("\n")
        assert lines[1] == "    // secondary colors"
        assert lines[2] == "    | Green"
        assert lines[3] == "    /* terminal color */"
        assert lines[4] == "    | Blue"


class TestRoundtrip:
    """Verify formatter preserves AST structure on all shipped files."""

    def _strip_locations(self, s: str) -> str:
        import re

        return re.sub(r"(location|filename)=SourceLocation\([^)]*\)", "LOC", s)

    def _check_roundtrip(self, path: str) -> None:
        from geno.parser import parse

        source = open(path).read()
        formatted = format_source(source)
        ast_before = self._strip_locations(str(parse(source)))
        ast_after = self._strip_locations(str(parse(formatted)))
        assert ast_before == ast_after, f"AST mismatch for {path}"

    def test_all_examples_roundtrip(self):
        """Formatter preserves AST structure on all example programs."""
        import glob

        files = sorted(glob.glob("examples/**/*.geno", recursive=True))
        assert len(files) >= 10, f"Expected >=10 example files, found {len(files)}"
        for f in files:
            self._check_roundtrip(f)

    def test_all_selfhost_roundtrip(self):
        """Formatter preserves AST structure on all selfhost files."""
        import glob

        files = sorted(glob.glob("selfhost/*.geno"))
        assert len(files) >= 5, f"Expected >=5 selfhost files, found {len(files)}"
        for f in files:
            self._check_roundtrip(f)

    def test_examples_are_formatted(self):
        """All example files pass geno fmt --check."""
        import glob

        files = sorted(glob.glob("examples/**/*.geno", recursive=True))
        for f in files:
            source = open(f).read()
            assert format_source(source) == source, f"{f} is not formatted"

    def test_selfhost_is_formatted(self):
        """All selfhost files pass geno fmt --check."""
        import glob

        files = sorted(glob.glob("selfhost/*.geno"))
        for f in files:
            source = open(f).read()
            assert format_source(source) == source, f"{f} is not formatted"


class TestExportKeywordIndentation:
    """Regression tests for #663 / F-0020.  The formatter used to
    classify lines by their first token, so ``export func`` / ``export
    type`` bypassed the block-opener handling intended for ``func`` /
    ``type`` and came out with the wrong depth."""

    def test_export_func_opens_block(self):
        source = (
            "export func add(x: Int, y: Int) -> Int\n"
            "example 1, 2 -> 3\n"
            "return x + y\n"
            "end func\n"
        )
        expected = (
            "export func add(x: Int, y: Int) -> Int\n"
            "    example 1, 2 -> 3\n"
            "    return x + y\n"
            "end func\n"
        )
        assert format_source(source) == expected

    def test_export_type_is_not_treated_as_block(self):
        """``export type Foo = Bar`` is a single-line alias; the
        formatter must not begin indenting subsequent top-level
        definitions as if they were inside an open block."""
        source = "export type MyInt = Int\n\nfunc main() -> Int\nreturn 0\nend func\n"
        expected = (
            "export type MyInt = Int\n\nfunc main() -> Int\n    return 0\nend func\n"
        )
        assert format_source(source) == expected

    def test_export_async_func_opens_block(self):
        """``export async func`` is a real construct (used across
        ``geno/tests/test_qualified_imports.py``).  The ``async func``
        detection must run against the post-``export`` prefix so the
        body gets indented like a regular ``async func``."""
        source = (
            "export async func fetch(url: String) -> String\n"
            'example "x" -> "ok"\n'
            'return "ok"\n'
            "end func\n"
        )
        expected = (
            "export async func fetch(url: String) -> String\n"
            '    example "x" -> "ok"\n'
            '    return "ok"\n'
            "end func\n"
        )
        assert format_source(source) == expected


class TestFormatFilesErrorBoundary:
    """`geno fmt` must not abort the whole run when one file cannot be read."""

    def test_unreadable_file_does_not_abort_run(self, tmp_path, capsys):
        from geno._cli_format import format_files

        good = tmp_path / "good.geno"
        good.write_text(
            "func add(x: Int, y: Int) -> Int\n"
            "example 1, 2 -> 3\n"
            "return x + y\n"
            "end func\n"
        )
        bad = tmp_path / "bad.geno"
        bad.write_bytes(b"\xff\xfe func main() -> Int\n")

        with pytest.raises(SystemExit) as exc_info:
            format_files(str(tmp_path))

        assert exc_info.value.code == 1  # non-zero: a file failed
        captured = capsys.readouterr()
        # The unreadable file is reported to stderr, not raised as a traceback...
        assert "bad.geno" in captured.err
        assert "cannot read" in captured.err
        # ...and the good file was still formatted rather than skipped by the abort.
        assert "good.geno" in captured.out
        assert good.read_text(encoding="utf-8").startswith(
            "func add(x: Int, y: Int) -> Int\n    example"
        )
