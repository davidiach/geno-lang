"""Tests for filesystem-based module imports."""

import os
from pathlib import Path

import pytest

from geno.api import RunConfig, run
from geno.module_resolver import (
    CircularImportError,
    ModuleResolutionError,
    resolve_modules,
)


def _parse(source: str, filename: str = "<test>"):
    from geno.lexer import Lexer
    from geno.parser import Parser

    tokens = Lexer(source, filename).tokenize()
    return Parser(tokens).parse_program()


class TestModuleResolver:
    """Test the module resolver directly."""

    def test_resolve_single_import(self, tmp_path):
        (tmp_path / "Utils.geno").write_text(
            "func double(x: Int) -> Int\n"
            "    example 3 -> 6\n"
            "    return x * 2\n"
            "end func\n"
        )
        main_source = (
            "import Utils\n\nfunc main() -> Int\n    return double(3)\nend func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        modules = resolve_modules(main_file, program)

        assert "Utils" in modules
        assert "double" in modules["Utils"]

    def test_resolve_transitive_imports(self, tmp_path):
        (tmp_path / "Base.geno").write_text(
            "func base_val(x: Int) -> Int\n    example 1 -> 1\n    return x\nend func\n"
        )
        (tmp_path / "Middle.geno").write_text(
            "import Base\n\n"
            "func middle_val(x: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    return base_val(x)\n"
            "end func\n"
        )
        main_source = (
            "import Middle\n\nfunc main() -> Int\n    return middle_val(1)\nend func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        modules = resolve_modules(main_file, program)

        assert "Middle" in modules
        assert "Base" in modules

    def test_resolve_missing_module(self, tmp_path):
        main_source = "import Missing\n\nfunc main() -> Int\n    return 0\nend func\n"
        main_file = tmp_path / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        with pytest.raises(ModuleResolutionError, match="Missing"):
            resolve_modules(main_file, program)

    def test_no_imports(self, tmp_path):
        main_source = "func main() -> Int\n    return 42\nend func\n"
        main_file = tmp_path / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        modules = resolve_modules(main_file, program)

        assert modules == {}

    def test_duplicate_import_resolved_once(self, tmp_path):
        (tmp_path / "Shared.geno").write_text(
            "func shared_val(x: Int) -> Int\n"
            "    example 42 -> 42\n"
            "    return x\n"
            "end func\n"
        )
        (tmp_path / "A.geno").write_text(
            "import Shared\n\n"
            "func a_val(x: Int) -> Int\n"
            "    example 42 -> 42\n"
            "    return shared_val(x)\n"
            "end func\n"
        )
        (tmp_path / "B.geno").write_text(
            "import Shared\n\n"
            "func b_val(x: Int) -> Int\n"
            "    example 42 -> 42\n"
            "    return shared_val(x)\n"
            "end func\n"
        )
        main_source = (
            "import A\nimport B\n\n"
            "func main() -> Int\n    return a_val(1) + b_val(2)\nend func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        modules = resolve_modules(main_file, program)

        assert "A" in modules
        assert "B" in modules
        assert "Shared" in modules


class TestFsImportsInterpreter:
    """Test filesystem imports through the full interpreter pipeline."""

    def test_import_and_call(self, tmp_path):
        (tmp_path / "Math.geno").write_text(
            "func triple(x: Int) -> Int\n"
            "    example 3 -> 9\n"
            "    return x * 3\n"
            "end func\n"
        )
        main_source = (
            "import Math\n\nfunc main() -> Int\n    return triple(3)\nend func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(main_source)

        # Resolve modules from filesystem
        program = _parse(main_source, str(main_file))
        fs_modules = resolve_modules(main_file, program)

        # API expects source strings, not parsed programs
        result = run(main_source, config=RunConfig(modules=fs_modules))
        assert result.ok is True
        assert result.value == 9

    def test_import_type_definitions(self, tmp_path):
        (tmp_path / "Types.geno").write_text(
            "type Color = Red | Green | Blue\n\n"
            "func is_red(c: Color) -> Bool\n"
            "    example Red -> true\n"
            "    example Green -> false\n"
            "    match c with\n"
            "        | Red -> return true\n"
            "        | _ -> return false\n"
            "    end match\n"
            "end func\n"
        )
        main_source = (
            "import Types\n\nfunc main() -> Bool\n    return is_red(Red)\nend func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        fs_modules = resolve_modules(main_file, program)

        result = run(main_source, config=RunConfig(modules=fs_modules))
        assert result.ok is True
        assert result.value is True

    def test_transitive_import_execution(self, tmp_path):
        (tmp_path / "Base.geno").write_text(
            "func base_fn(x: Int) -> Int\n"
            "    example 10 -> 10\n"
            "    return x\n"
            "end func\n"
        )
        (tmp_path / "Mid.geno").write_text(
            "import Base\n\n"
            "func mid_fn(x: Int) -> Int\n"
            "    example 5 -> 10\n"
            "    return base_fn(x) * 2\n"
            "end func\n"
        )
        main_source = (
            "import Mid\n\nfunc main() -> Int\n    return mid_fn(5)\nend func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        fs_modules = resolve_modules(main_file, program)

        result = run(main_source, config=RunConfig(modules=fs_modules))
        assert result.ok is True
        assert result.value == 10


class TestCircularImports:
    """Test that circular imports are detected."""

    def test_direct_circular_import(self, tmp_path):
        """A imports B, B imports A -> CircularImportError."""
        (tmp_path / "A.geno").write_text(
            "import B\n\n"
            "func a_fn(x: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    return x\n"
            "end func\n"
        )
        (tmp_path / "B.geno").write_text(
            "import A\n\n"
            "func b_fn(x: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    return x\n"
            "end func\n"
        )
        main_source = "import A\n\nfunc main() -> Int\n    return a_fn(1)\nend func\n"
        main_file = tmp_path / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        with pytest.raises(CircularImportError, match="Circular import"):
            resolve_modules(main_file, program)

    def test_self_import(self, tmp_path):
        """A module importing itself should raise CircularImportError."""
        (tmp_path / "Self.geno").write_text(
            "import Self\n\n"
            "func self_fn(x: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    return x\n"
            "end func\n"
        )
        main_source = (
            "import Self\n\nfunc main() -> Int\n    return self_fn(1)\nend func\n"
        )
        main_file = tmp_path / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        with pytest.raises(CircularImportError, match="Circular import"):
            resolve_modules(main_file, program)


class TestPathTraversal:
    """Test that path traversal attacks are blocked."""

    def test_dotdot_in_module_name_rejected_by_parser(self, tmp_path):
        """Module names with .. are rejected at parse time (not valid PascalCase)."""
        from geno.parser import ParseError

        main_source = "import ..Evil\n\nfunc main() -> Int\n    return 0\nend func\n"
        with pytest.raises(ParseError):
            _parse(main_source, "test.geno")

    @pytest.mark.skipif(
        os.name == "nt", reason="Symlinks require privileges on Windows"
    )
    def test_symlink_escape(self, tmp_path):
        """Symlink pointing outside base directory should be rejected."""
        # Create a file outside the project
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "Evil.geno").write_text(
            "func evil_fn() -> Int\n    example () -> 666\n    return 666\nend func\n"
        )

        # Create symlink inside project pointing to outside
        project = tmp_path / "project"
        project.mkdir()
        (project / "Evil.geno").symlink_to(outside / "Evil.geno")

        main_source = (
            "import Evil\n\nfunc main() -> Int\n    return evil_fn()\nend func\n"
        )
        main_file = project / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        with pytest.raises(ModuleResolutionError):
            resolve_modules(main_file, program)


class TestDependencyInternalResolution:
    """Tests for dependency packages resolving their own sibling modules."""

    def test_dependency_sibling_import(self, tmp_path):
        """A dependency package can import its own sibling modules."""
        project = tmp_path / "myproject"
        project.mkdir()
        (project / "geno.toml").write_text('files = ["Main"]\n')

        dep_dir = project / "geno_modules" / "my-dep"
        dep_dir.mkdir(parents=True)
        (dep_dir / "geno.toml").write_text(
            'entrypoint = "DepMain"\nfiles = ["DepMain", "DepUtils"]\n'
        )
        (dep_dir / "DepMain.geno").write_text(
            "import DepUtils\n"
            '@untested("dep")\n'
            "func dep_func() -> Int\n"
            "  return dep_helper()\n"
            "end func\n"
        )
        (dep_dir / "DepUtils.geno").write_text(
            '@untested("dep")\nfunc dep_helper() -> Int\n  return 42\nend func\n'
        )

        main_source = (
            "import MyDep\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return dep_func()\n"
            "end func\n"
        )
        main_file = project / "Main.geno"
        main_file.write_text(main_source)

        program = _parse(main_source, str(main_file))
        modules = resolve_modules(main_file, program)
        # Both dep modules should be resolved
        assert "MyDep" in modules
        assert "DepUtils" in modules

    def test_nested_dependency_imports(self, tmp_path):
        """Nested imports within a dependency chain resolve correctly."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "geno.toml").write_text('files = ["App"]\n')

        dep_dir = project / "geno_modules" / "tools"
        dep_dir.mkdir(parents=True)
        (dep_dir / "geno.toml").write_text(
            'entrypoint = "Tools"\nfiles = ["Tools", "Helpers"]\n'
        )
        (dep_dir / "Helpers.geno").write_text(
            '@untested("dep")\nfunc add_one(x: Int) -> Int\n  return x + 1\nend func\n'
        )
        (dep_dir / "Tools.geno").write_text(
            "import Helpers\n"
            '@untested("dep")\n'
            "func add_two(x: Int) -> Int\n"
            "  return add_one(add_one(x))\n"
            "end func\n"
        )

        app_source = (
            "import Tools\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return add_two(5)\n"
            "end func\n"
        )
        app_file = project / "App.geno"
        app_file.write_text(app_source)

        program = _parse(app_source, str(app_file))
        modules = resolve_modules(app_file, program)
        assert "Tools" in modules
        assert "Helpers" in modules
