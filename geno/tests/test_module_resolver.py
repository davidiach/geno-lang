"""
Dedicated tests for module_resolver.py — security-critical path resolution.

Complements the integration-level tests in test_fs_imports.py with focused
unit tests for edge cases: source overrides, indirect circular imports,
standard library fallback, and path validation.
"""

from pathlib import Path

import pytest

from geno.lexer import Lexer
from geno.module_resolver import (
    AmbiguousModuleError,
    CircularImportError,
    ModuleResolutionError,
    ResolvedModuleSource,
    resolve_module_sources,
    resolve_modules,
)
from geno.parser import Parser


def _parse(source: str):
    """Parse source into a program AST."""
    tokens = Lexer(source, "<test>").tokenize()
    return Parser(tokens).parse_program()


# Minimal valid Geno function source (no examples needed for resolver)
_HELPER_SRC = "func helper(x: Int) -> Int\n    return x\nend func\n"


def _supports_case_distinct_paths(tmp_path: Path) -> bool:
    """Return True when the temp filesystem distinguishes names by case."""
    probe = tmp_path / "CaseProbe.geno"
    probe.write_text(_HELPER_SRC)
    try:
        return not (tmp_path / "caseprobe.geno").exists()
    finally:
        probe.unlink()


class TestResolveModulesBasic:
    """Core resolution: files found, returned correctly."""

    def test_no_imports_returns_empty(self, tmp_path):
        src = tmp_path / "main.geno"
        src.write_text("func main() -> Int\n    return 1\nend func\n")
        program = _parse(src.read_text())
        result = resolve_modules(src, program)
        assert result == {}

    def test_single_import(self, tmp_path):
        dep = tmp_path / "Utils.geno"
        dep.write_text(_HELPER_SRC)
        src = tmp_path / "main.geno"
        src.write_text(
            "import Utils\n\nfunc main() -> Int\n    return helper(1)\nend func\n"
        )
        program = _parse(src.read_text())
        result = resolve_modules(src, program)
        assert "Utils" in result
        assert "helper" in result["Utils"]

    def test_resolve_module_sources_returns_paths(self, tmp_path):
        dep = tmp_path / "Dep.geno"
        dep.write_text(_HELPER_SRC)
        src = tmp_path / "main.geno"
        src.write_text(
            "import Dep\nfunc main() -> Int\n    return helper(1)\nend func\n"
        )
        program = _parse(src.read_text())
        result = resolve_module_sources(src, program)
        assert "Dep" in result
        assert isinstance(result["Dep"], ResolvedModuleSource)
        assert result["Dep"].path == dep.resolve()
        assert result["Dep"].module_name == "Dep"


class TestSourceOverrides:
    """Source overrides let the LSP/editor provide unsaved buffer content."""

    def test_override_replaces_disk_content(self, tmp_path):
        dep = tmp_path / "Dep.geno"
        dep.write_text("func old(x: Int) -> Int\n    return 0\nend func\n")
        src = tmp_path / "main.geno"
        src.write_text("import Dep\nfunc main() -> Int\n    return 1\nend func\n")
        program = _parse(src.read_text())

        override_source = "func new_fn(x: Int) -> Int\n    return 99\nend func\n"
        result = resolve_modules(src, program, source_overrides={dep: override_source})
        assert "new_fn" in result["Dep"]
        assert "old" not in result["Dep"]

    def test_override_for_transitive_dep(self, tmp_path):
        c = tmp_path / "C.geno"
        c.write_text("func c_fn(x: Int) -> Int\n    return 1\nend func\n")
        b = tmp_path / "B.geno"
        b.write_text(
            "import C\nfunc b_fn(x: Int) -> Int\n    return c_fn(x)\nend func\n"
        )
        src = tmp_path / "main.geno"
        src.write_text("import B\nfunc main() -> Int\n    return b_fn(1)\nend func\n")
        program = _parse(src.read_text())
        override_source = "func c_fn(x: Int) -> Int\n    return 999\nend func\n"
        result = resolve_modules(src, program, source_overrides={c: override_source})
        assert "999" in result["C"]


class TestCircularImportDetection:
    """Cycles must be detected and raised."""

    def test_indirect_cycle_a_b_c_a(self, tmp_path):
        (tmp_path / "A.geno").write_text(
            "import B\nfunc a_fn(x: Int) -> Int\n    return 1\nend func\n"
        )
        (tmp_path / "B.geno").write_text(
            "import C\nfunc b_fn(x: Int) -> Int\n    return 1\nend func\n"
        )
        (tmp_path / "C.geno").write_text(
            "import A\nfunc c_fn(x: Int) -> Int\n    return 1\nend func\n"
        )
        src = tmp_path / "main.geno"
        src.write_text("import A\nfunc main() -> Int\n    return a_fn(1)\nend func\n")
        program = _parse(src.read_text())
        with pytest.raises(CircularImportError, match="A"):
            resolve_modules(src, program)


class TestPathTraversal:
    """Module names containing path separators must be rejected."""

    def test_slash_in_module_name_rejected(self, tmp_path):
        from geno.ast_nodes import ImportStatement, Program
        from geno.tokens import SourceLocation

        loc = SourceLocation("<test>", 1, 1)
        imp = ImportStatement(module_name="../Evil", location=loc)
        program = Program(definitions=[imp], location=loc)
        src = tmp_path / "main.geno"
        src.write_text("")
        with pytest.raises(ModuleResolutionError):
            resolve_modules(src, program)

    def test_backslash_in_module_name_rejected(self, tmp_path):
        from geno.ast_nodes import ImportStatement, Program
        from geno.tokens import SourceLocation

        loc = SourceLocation("<test>", 1, 1)
        imp = ImportStatement(module_name="..\\Evil", location=loc)
        program = Program(definitions=[imp], location=loc)
        src = tmp_path / "main.geno"
        src.write_text("")
        with pytest.raises(ModuleResolutionError):
            resolve_modules(src, program)

    def test_missing_module_raises(self, tmp_path):
        src = tmp_path / "main.geno"
        src.write_text(
            "import NonExistent\nfunc main() -> Int\n    return 0\nend func\n"
        )
        program = _parse(src.read_text())
        with pytest.raises(ModuleResolutionError, match="NonExistent"):
            resolve_modules(src, program)


class TestStdLibFallback:
    """Standard library modules resolve from geno/std/."""

    def test_std_module_found(self, tmp_path):
        """If a std lib module exists, importing it should succeed."""
        src = tmp_path / "main.geno"
        # String.geno is a known std lib module
        src.write_text("import String\nfunc main() -> Int\n    return 0\nend func\n")
        program = _parse(src.read_text())
        result = resolve_modules(src, program)
        assert "String" in result

    def test_local_takes_precedence_over_std(self, tmp_path):
        """A local module with the same name as a std module takes precedence."""
        local = tmp_path / "String.geno"
        local.write_text("func my_local(x: Int) -> Int\n    return 42\nend func\n")
        src = tmp_path / "main.geno"
        src.write_text(
            "import String\nfunc main() -> Int\n    return my_local(1)\nend func\n"
        )
        program = _parse(src.read_text())
        result = resolve_modules(src, program)
        assert "my_local" in result["String"]


class TestCaseInsensitiveFallback:
    """Case-insensitive filesystem fallback for PascalCase imports."""

    def test_lowercase_file_resolves_pascal_import(self, tmp_path):
        (tmp_path / "cli.geno").write_text(_HELPER_SRC)
        src = tmp_path / "main.geno"
        src.write_text(
            "import Cli\nfunc main() -> Int\n    return helper(1)\nend func\n"
        )
        program = _parse(src.read_text())
        result = resolve_module_sources(src, program)
        assert "Cli" in result
        assert result["Cli"].path.name == "cli.geno"

    def test_exact_match_wins_over_case_fallback(self, tmp_path):
        if not _supports_case_distinct_paths(tmp_path):
            pytest.skip("filesystem is case-insensitive")
        (tmp_path / "Utils.geno").write_text(
            "func exact(x: Int) -> Int\n    return 1\nend func\n"
        )
        (tmp_path / "utils.geno").write_text(
            "func lower(x: Int) -> Int\n    return 2\nend func\n"
        )
        src = tmp_path / "main.geno"
        src.write_text(
            "import Utils\nfunc main() -> Int\n    return exact(1)\nend func\n"
        )
        program = _parse(src.read_text())
        result = resolve_modules(src, program)
        # Exact match should win — no ambiguity error because Utils.geno exists.
        assert "exact" in result["Utils"]

    def test_ambiguous_case_only_matches_raises(self, tmp_path):
        if not _supports_case_distinct_paths(tmp_path):
            pytest.skip("filesystem is case-insensitive")
        # Two lowercase variants, no exact PascalCase match.
        (tmp_path / "cli.geno").write_text(_HELPER_SRC)
        (tmp_path / "CLI.geno").write_text(_HELPER_SRC)
        src = tmp_path / "main.geno"
        src.write_text(
            "import Cli\nfunc main() -> Int\n    return helper(1)\nend func\n"
        )
        program = _parse(src.read_text())
        with pytest.raises(AmbiguousModuleError, match="Cli"):
            resolve_modules(src, program)


class TestDiamondDependency:
    """Diamond imports (A->B, A->C, B->D, C->D) should resolve D only once."""

    def test_shared_dep_resolved_once(self, tmp_path):
        (tmp_path / "D.geno").write_text(
            "func shared(x: Int) -> Int\n    return 1\nend func\n"
        )
        (tmp_path / "B.geno").write_text(
            "import D\nfunc b_fn(x: Int) -> Int\n    return shared(x)\nend func\n"
        )
        (tmp_path / "C.geno").write_text(
            "import D\nfunc c_fn(x: Int) -> Int\n    return shared(x)\nend func\n"
        )
        src = tmp_path / "main.geno"
        src.write_text(
            "import B\nimport C\nfunc main() -> Int\n    return b_fn(1)\nend func\n"
        )
        program = _parse(src.read_text())
        result = resolve_module_sources(src, program)
        # D should appear exactly once
        assert "D" in result
        assert "B" in result
        assert "C" in result
