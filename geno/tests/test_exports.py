"""
Tests for explicit exports (#146)
==================================
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.lexer import Lexer
from geno.parser import Parser
from geno.typechecker import TypeChecker
from geno.typechecker import TypeError as GenoTypeError


def _parse(source, filename="<test>"):
    tokens = Lexer(source, filename).tokenize()
    return Parser(tokens).parse_program()


class TestExportParsing:
    def test_export_func(self):
        source = (
            "export func greet(name: String) -> String\n"
            '  example "World" -> "Hello, World!"\n'
            '  return "Hello, " + name + "!"\n'
            "end func\n"
        )
        program = _parse(source)
        assert len(program.definitions) == 1
        assert program.definitions[0].exported is True
        assert program.definitions[0].name == "greet"

    def test_export_type(self):
        source = "export type Color = Red | Green | Blue\n"
        program = _parse(source)
        assert len(program.definitions) == 1
        assert program.definitions[0].exported is True
        assert program.definitions[0].name == "Color"

    def test_export_type_alias(self):
        source = "export type UserId = Int\n"
        program = _parse(source)
        assert len(program.definitions) == 1
        assert program.definitions[0].exported is True
        assert program.definitions[0].name == "UserId"

    def test_non_exported_func(self):
        source = (
            "func helper(x: Int) -> Int\n  example 1 -> 2\n  return x + 1\nend func\n"
        )
        program = _parse(source)
        assert program.definitions[0].exported is False

    def test_export_with_untested(self):
        source = (
            'export @untested("scaffold")\nfunc main() -> Int\n  return 42\nend func\n'
        )
        program = _parse(source)
        defn = program.definitions[0]
        assert defn.exported is True
        assert defn.untested_reason == "scaffold"

    def test_export_async_func(self):
        source = (
            'export @untested("async")\n'
            "async func fetch_data() -> String\n"
            '  return "data"\n'
            "end func\n"
        )
        program = _parse(source)
        defn = program.definitions[0]
        assert defn.exported is True
        assert defn.is_async is True

    def test_export_import_rejected(self):
        source = "export import Foo\n"
        with pytest.raises(Exception, match=r"export.*import"):
            _parse(source)

    def test_export_test_rejected(self):
        source = 'export test "bad"\n  assert true\nend test\n'
        with pytest.raises(Exception, match=r"export.*test"):
            _parse(source)


class TestExportVisibility:
    def test_exported_symbols_visible(self, tmp_path):
        """Exported functions are accessible from importers."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Lib"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "export func double(x: Int) -> Int\n"
            "  example 3 -> 6\n"
            "  return x * 2\n"
            "end func\n"
            "\n"
            "func internal_helper(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Lib\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return double(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        # Should not raise — double is exported
        checker.check_project_graph(dg)

    def test_non_exported_symbols_hidden(self, tmp_path):
        """Non-exported functions are inaccessible when module has exports."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Lib"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "export func public_fn(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
            "\n"
            "func private_fn(x: Int) -> Int\n"
            "  example 1 -> 3\n"
            "  return x + 2\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Lib\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return private_fn(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        with pytest.raises(GenoTypeError, match="private_fn"):
            checker.check_project_graph(dg)

    def test_no_exports_means_all_visible(self, tmp_path):
        """Modules with no export keywords export everything (backward compat)."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Lib"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "func alpha(x: Int) -> Int\n"
            "  example 1 -> 2\n"
            "  return x + 1\n"
            "end func\n"
            "\n"
            "func beta(x: Int) -> Int\n"
            "  example 1 -> 3\n"
            "  return x + 2\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Lib\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return alpha(1) + beta(1)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        # Should not raise — both alpha and beta visible (no exports)
        checker.check_project_graph(dg)

    def test_exported_type_visible(self, tmp_path):
        """Exported types are accessible from importers."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Types"]\n'
        )
        (tmp_path / "Types.geno").write_text(
            "export type Color = Red | Green | Blue\n"
            "export func color_name(c: Color) -> String\n"
            '  example Red -> "red"\n'
            "  match c with\n"
            '  | Red -> return "red"\n'
            '  | Green -> return "green"\n'
            '  | Blue -> return "blue"\n'
            "  end match\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Types\n"
            '@untested("test")\n'
            "func main() -> String\n"
            "  return color_name(Green)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    def test_exported_type_alias_counts_as_explicit_export(self, tmp_path):
        """Exported aliases make private module functions inaccessible."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Types"]\n'
        )
        (tmp_path / "Types.geno").write_text(
            "export type UserId = Int\n"
            "\n"
            "func private_user_id() -> UserId\n"
            "  example () -> 1\n"
            "  return 1\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Types\n"
            '@untested("test")\n'
            "func main() -> UserId\n"
            "  return private_user_id()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        with pytest.raises(GenoTypeError, match="private_user_id"):
            checker.check_project_graph(dg)

    def test_exported_type_alias_counts_as_explicit_export_direct_modules(self):
        """String/API-style module imports apply alias export visibility too."""
        types_program = _parse(
            "export type UserId = Int\n"
            "\n"
            "func private_user_id() -> UserId\n"
            "  example () -> 1\n"
            "  return 1\n"
            "end func\n",
            filename="<module:Types>",
        )
        main_program = _parse(
            "import Types\n"
            '@untested("test")\n'
            "func main() -> UserId\n"
            "  return private_user_id()\n"
            "end func\n"
        )

        checker = TypeChecker()
        with pytest.raises(GenoTypeError, match="private_user_id"):
            checker.check_program(main_program, modules={"Types": types_program})

    def test_private_type_alias_hidden_when_module_has_exports(self, tmp_path):
        """Private aliases are inaccessible when a module uses explicit exports."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Types"]\n'
        )
        (tmp_path / "Types.geno").write_text(
            "export func public_value() -> Int\n"
            "  example () -> 1\n"
            "  return 1\n"
            "end func\n"
            "\n"
            "type PrivateId = Int\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Types\n"
            '@untested("test")\n'
            "func main() -> PrivateId\n"
            "  return public_value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        with pytest.raises(GenoTypeError, match="PrivateId"):
            checker.check_project_graph(dg)

    def test_private_alias_can_back_exported_function_signature(self, tmp_path):
        """Private aliases can resolve exported signatures without being imported."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Types"]\n'
        )
        (tmp_path / "Types.geno").write_text(
            "type PrivateId = Int\n"
            "\n"
            "export func public_user_id() -> PrivateId\n"
            "  example () -> 1\n"
            "  return 1\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Types\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return public_user_id()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    def test_aliased_import_hides_exported_type_alias_unqualified(self, tmp_path):
        """Aliased imports do not bind exported aliases as unqualified types."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Types"]\n'
        )
        (tmp_path / "Types.geno").write_text("export type UserId = Int\n")
        (tmp_path / "Main.geno").write_text(
            "import Types as T\n"
            '@untested("test")\n'
            "func main(x: UserId) -> Int\n"
            "  return x\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        with pytest.raises(GenoTypeError, match="UserId"):
            checker.check_project_graph(dg)

    def test_aliased_import_hides_exported_type_alias_direct_modules(self):
        """Direct module-map checking applies the same alias visibility rule."""
        types_program = _parse(
            "export type UserId = Int\n",
            filename="<module:Types>",
        )
        main_program = _parse(
            "import Types as T\n"
            '@untested("test")\n'
            "func main(x: UserId) -> Int\n"
            "  return x\n"
            "end func\n"
        )

        checker = TypeChecker()
        with pytest.raises(GenoTypeError, match="UserId"):
            checker.check_program(main_program, modules={"Types": types_program})
