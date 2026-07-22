"""
Tests for qualified imports and aliases (#147)
================================================
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.api import RunConfig, run
from geno.ast_nodes import ImportStatement
from geno.compiler import Compiler
from geno.dependency_graph import DependencyGraph
from geno.js_compiler import JSCompiler
from geno.lexer import Lexer
from geno.parser import Parser
from geno.project_graph import ProjectGraph
from geno.typechecker import TypeChecker
from geno.typechecker import TypeError as GenoTypeError


def _parse(source, filename="<test>"):
    tokens = Lexer(source, filename).tokenize()
    return Parser(tokens).parse_program()


class TestImportAliasParsing:
    def test_import_as(self):
        source = "import Math as M\n"
        program = _parse(source)
        imp = program.definitions[0]
        assert isinstance(imp, ImportStatement)
        assert imp.module_name == "Math"
        assert imp.alias == "M"

    def test_import_no_alias(self):
        source = "import Math\n"
        program = _parse(source)
        imp = program.definitions[0]
        assert imp.module_name == "Math"
        assert imp.alias is None

    def test_import_as_lowercase_rejected(self):
        """Alias must be PascalCase (TYPE_IDENTIFIER)."""
        source = "import Math as m\n"
        with pytest.raises(Exception):
            _parse(source)


class TestQualifiedAccess:
    def test_qualified_function_call(self, tmp_path):
        """Foo.symbol resolves to an exported function."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Math"]\n'
        )
        (tmp_path / "Math.geno").write_text(
            "func double(x: Int) -> Int\n  example 3 -> 6\n  return x * 2\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Math\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return Math.double(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    def test_aliased_import(self, tmp_path):
        """import Foo as F makes F.symbol work."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Math"]\n'
        )
        (tmp_path / "Math.geno").write_text(
            "func double(x: Int) -> Int\n  example 3 -> 6\n  return x * 2\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Math as M\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return M.double(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    @pytest.mark.parametrize(
        ("imports", "body", "expected"),
        [
            ("import Lib as A\nimport Lib as B\n", "B.value()", 7),
            ("import Lib\nimport Lib as B\n", "value() + B.value()", 14),
            ("import Lib as A\nimport Lib\n", "A.value() + value()", 14),
        ],
    )
    def test_repeated_imports_apply_each_import_shape_in_api(
        self, imports, body, expected
    ):
        """Repeated imports should still bind each statement's namespace shape."""
        lib_source = (
            "export func value() -> Int\n  example () -> 7\n  return 7\nend func\n"
        )
        main_source = (
            imports
            + '@untested("test")\n'
            + "func main() -> Int\n"
            + f"  return {body}\n"
            + "end func\n"
        )

        result = run(
            main_source,
            RunConfig(modules={"Lib": lib_source}, check_examples=False),
        )

        assert result.ok, [d.message for d in result.diagnostics]
        assert result.value == expected

    def test_repeated_aliased_imports_apply_each_import_shape_in_project(
        self, tmp_path
    ):
        """Project import summaries should also replay later aliases."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Lib"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "export func value() -> Int\n  example () -> 7\n  return 7\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Lib as A\n"
            "import Lib as B\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return A.value() + B.value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        TypeChecker().check_project_graph(dg)

    def test_aliased_import_hides_unqualified(self, tmp_path):
        """import Foo as F does NOT make unqualified symbol available."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Math"]\n'
        )
        (tmp_path / "Math.geno").write_text(
            "func double(x: Int) -> Int\n  example 3 -> 6\n  return x * 2\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Math as M\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return double(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        with pytest.raises(GenoTypeError, match="double"):
            checker.check_project_graph(dg)

    def test_no_collision_with_qualified(self, tmp_path):
        """Two modules with same-named exports don't collide when qualified."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "A", "B"]\n'
        )
        (tmp_path / "A.geno").write_text(
            'export @untested("test")\nfunc value() -> Int\n  return 1\nend func\n'
        )
        (tmp_path / "B.geno").write_text(
            'export @untested("test")\nfunc value() -> Int\n  return 2\nend func\n'
        )
        (tmp_path / "Main.geno").write_text(
            "import A\nimport B\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return A.value() + B.value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    def test_unqualified_still_works(self, tmp_path):
        """Regular import Foo still dumps symbols unqualified (backward compat)."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Lib"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "func helper(x: Int) -> Int\n  example 1 -> 2\n  return x + 1\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Lib\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return helper(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        # Should not raise — unqualified still works
        checker.check_project_graph(dg)

    def test_qualified_named_args_still_work(self, tmp_path):
        """Qualified calls keep per-module parameter metadata for named args."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Math"]\n'
        )
        (tmp_path / "Math.geno").write_text(
            'export @untested("test")\n'
            "func make_point(x: Int, y: Int, z: Int) -> Int\n"
            "  return x + y + z\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Math\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return Math.make_point(x: 1, y: 2, z: 3)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    def test_imported_default_args_still_work(self, tmp_path):
        """Unqualified imported functions preserve default-argument metadata."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Lib"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "func add(x: Int, y: Int = 10) -> Int\n"
            "  example 5 -> 15\n"
            "  return x + y\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Lib\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return add(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    @pytest.mark.parametrize(
        ("import_stmt", "call_expr"),
        [
            ("import Lib\n", "Lib.add(5)"),
            ("import Lib as L\n", "L.add(5)"),
        ],
    )
    def test_qualified_imported_default_args_still_work(
        self, tmp_path, import_stmt, call_expr
    ):
        """Qualified imported functions preserve default-argument metadata."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Lib"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "func add(x: Int, y: Int = 10) -> Int\n"
            "  example 5 -> 15\n"
            "  return x + y\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            import_stmt
            + '@untested("test")\n'
            + "func main() -> Int\n"
            + f"  return {call_expr}\n"
            + "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    def test_transitive_imports_still_resolve(self, tmp_path):
        """Import summaries preserve recursive import visibility across modules."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Mid", "Leaf"]\n'
        )
        (tmp_path / "Leaf.geno").write_text(
            "func leaf_value() -> Int\n  example () -> 7\n  return 7\nend func\n"
        )
        (tmp_path / "Mid.geno").write_text(
            "import Leaf\n"
            "func mid_value() -> Int\n"
            "  example () -> 7\n"
            "  return leaf_value()\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Mid\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return mid_value() + leaf_value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    def test_transitive_aliased_imports_still_resolve(self, tmp_path):
        """Nested import aliases stay visible when project-checking summaries."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Mid", "Leaf"]\n'
        )
        (tmp_path / "Leaf.geno").write_text(
            'export @untested("test")\nfunc leaf_value() -> Int\n  return 7\nend func\n'
        )
        (tmp_path / "Mid.geno").write_text(
            "import Leaf as L\n"
            '@untested("test")\n'
            "func mid_value() -> Int\n"
            "  return L.leaf_value()\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Mid\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return L.leaf_value() + mid_value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    def test_transitive_aliased_imports_do_not_become_unqualified(self, tmp_path):
        """Nested aliased imports should not leak unqualified names."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Mid", "Leaf"]\n'
        )
        (tmp_path / "Leaf.geno").write_text(
            'export @untested("test")\nfunc leaf_value() -> Int\n  return 7\nend func\n'
        )
        (tmp_path / "Mid.geno").write_text(
            "import Leaf as L\n"
            '@untested("test")\n'
            "func mid_value() -> Int\n"
            "  return L.leaf_value()\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Mid\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return leaf_value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        with pytest.raises(GenoTypeError, match="leaf_value"):
            checker.check_project_graph(dg)

    def test_qualified_async_import_matches_direct_module_path(self, tmp_path):
        """Sync qualified calls to async imports should be rejected everywhere."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Lib", "Main"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "export async func value() -> Int\n  return 7\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Lib\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return Lib.value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        with pytest.raises(GenoTypeError, match="Async\\[Int\\]"):
            TypeChecker().check_project_graph(dg)
        with pytest.raises(GenoTypeError, match="Async\\[Int\\]"):
            TypeChecker().check_program(
                dg.parsed["Main"],
                modules={name: program for name, program in dg.parsed.items()},
            )

    def test_qualified_async_import_alias_rejected_in_sync_context(self, tmp_path):
        """Aliases should preserve async semantics for qualified module calls."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Lib", "Main"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "export async func value() -> Int\n  return 7\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Lib as L\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return L.value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        with pytest.raises(GenoTypeError, match="Async\\[Int\\]"):
            TypeChecker().check_project_graph(dg)

    def test_awaited_qualified_async_import_typechecks_and_runs(self, tmp_path):
        """Awaited qualified async calls should typecheck and run on both backends."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Lib", "Main"]\n'
        )
        (tmp_path / "Lib.geno").write_text(
            "export async func value() -> Int\n  return 7\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Lib\n"
            "async func main() -> Int\n"
            "  return await Lib.value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        TypeChecker().check_project_graph(dg)
        TypeChecker().check_program(
            dg.parsed["Main"],
            modules={name: program for name, program in dg.parsed.items()},
        )

        py_code = Compiler().compile_project(dg)
        py_out = tmp_path / "qualified_async.py"
        py_out.write_text(py_code, encoding="utf-8")
        py_result = subprocess.run(
            [sys.executable, str(py_out)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert py_result.returncode == 0, py_result.stderr
        assert "7" in py_result.stdout

        js_code = JSCompiler().compile_project(dg)
        js_out = tmp_path / "qualified_async.js"
        js_out.write_text(js_code, encoding="utf-8")
        js_result = subprocess.run(
            ["node", str(js_out)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert js_result.returncode == 0, js_result.stderr
        assert "7" in js_result.stdout

    def test_unknown_transitive_import_still_errors(self, tmp_path):
        """Project checking still rejects unknown imports nested in dependencies."""
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Mid"]\n'
        )
        (tmp_path / "Mid.geno").write_text(
            "import Missing\n"
            "func mid_value() -> Int\n"
            "  example () -> 7\n"
            "  return 7\n"
            "end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Mid\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return mid_value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        with pytest.raises(GenoTypeError, match="Missing"):
            checker.check_project_graph(dg)
