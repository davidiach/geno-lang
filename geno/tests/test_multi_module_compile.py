"""
Tests for multi-module compilation (#128, #129)
================================================
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.compiler import Compiler
from geno.dependency_graph import DependencyGraph
from geno.js_compiler import JSCompiler, compile_project_to_html
from geno.project_graph import ProjectGraph
from geno.tests._script_runner import run_node_code, run_python_code

# =========================================================================
# Python multi-module compilation
# =========================================================================


class TestPythonMultiModule:
    def test_two_module_project(self, tmp_path):
        """Two-module project compiles and runs correctly."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Math"]\n'
        )
        (tmp_path / "Math.geno").write_text(
            "func double(x: Int) -> Int\n  example 3 -> 6\n  return x * 2\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Math\n"
            '@untested("integration")\n'
            "func main() -> Int\n"
            "  return double(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        compiler = Compiler()
        py_code = compiler.compile_project(dg)

        result = run_python_code(py_code, python_executable=sys.executable, timeout=10)
        assert result.returncode == 0
        assert "10" in result.stdout

    def test_three_module_chain(self, tmp_path):
        """A -> B -> C chain compiles correctly."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "A"\nfiles = ["A", "B", "C"]\n'
        )
        (tmp_path / "C.geno").write_text(
            '@untested("leaf")\nfunc base() -> Int\n  return 1\nend func\n'
        )
        (tmp_path / "B.geno").write_text(
            "import C\n"
            '@untested("test")\n'
            "func middle() -> Int\n  return base() + 1\nend func\n"
        )
        (tmp_path / "A.geno").write_text(
            "import B\n"
            '@untested("test")\n'
            "func main() -> Int\n  return middle() + 1\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        compiler = Compiler()
        py_code = compiler.compile_project(dg)

        result = run_python_code(py_code, python_executable=sys.executable, timeout=10)
        assert result.returncode == 0
        assert "3" in result.stdout

    def test_diamond_dependency(self, tmp_path):
        """Diamond: A -> B, A -> C, B -> D, C -> D compiles correctly."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "A"\nfiles = ["A", "B", "C", "D"]\n'
        )
        (tmp_path / "D.geno").write_text(
            '@untested("leaf")\nfunc val() -> Int\n  return 10\nend func\n'
        )
        (tmp_path / "B.geno").write_text(
            "import D\n"
            '@untested("test")\n'
            "func b_val() -> Int\n  return val() + 1\nend func\n"
        )
        (tmp_path / "C.geno").write_text(
            "import D\n"
            '@untested("test")\n'
            "func c_val() -> Int\n  return val() + 2\nend func\n"
        )
        (tmp_path / "A.geno").write_text(
            "import B\nimport C\n"
            '@untested("test")\n'
            "func main() -> Int\n  return b_val() + c_val()\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        compiler = Compiler()
        py_code = compiler.compile_project(dg)

        result = run_python_code(py_code, python_executable=sys.executable, timeout=10)
        assert result.returncode == 0
        assert "23" in result.stdout  # (10+1) + (10+2) = 23

    def test_import_no_longer_raises(self, tmp_path):
        """ImportStatement in single-file compile no longer raises."""
        (tmp_path / "geno.toml").write_text('files = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import SomeModule\n"
            '@untested("test")\n'
            "func main() -> Int\n  return 42\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        compiler = Compiler()
        # Should not raise
        py_code = compiler.compile_project(dg)
        assert "def main" in py_code

    def test_cross_module_types(self, tmp_path):
        """Cross-module type variants remain available in Python output."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Types"]\n'
        )
        (tmp_path / "Types.geno").write_text(
            "type Color = Red | Green | Blue\n"
            "func color_name(c: Color) -> String\n"
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
        compiler = Compiler()
        py_code = compiler.compile_project(dg)

        result = run_python_code(py_code, python_executable=sys.executable, timeout=10)
        assert result.returncode == 0
        assert "green" in result.stdout

    def test_duplicate_function_names_do_not_leak_into_module_closures(self, tmp_path):
        """A module function should keep calling same-module helpers."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "A", "B"]\n'
        )
        (tmp_path / "A.geno").write_text(
            "func value() -> Int\n"
            "  example () -> 1\n"
            "  return 1\n"
            "end func\n\n"
            "func own() -> Int\n"
            "  example () -> 1\n"
            "  return value()\n"
            "end func\n"
        )
        (tmp_path / "B.geno").write_text(
            "func value() -> Int\n  example () -> 2\n  return 2\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import A\n"
            "import B\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return A.own() * 10 + B.value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        compiler = Compiler()
        py_code = compiler.compile_project(dg)

        result = run_python_code(py_code, python_executable=sys.executable, timeout=10)
        assert result.returncode == 0
        assert "12" in result.stdout


# =========================================================================
# JS multi-module compilation
# =========================================================================


class TestJSMultiModule:
    @pytest.fixture(autouse=True)
    def _check_node(self):
        result = subprocess.run(["node", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            pytest.skip("Node.js not available")

    def test_two_module_project(self, tmp_path):
        """Two-module JS project compiles and runs correctly."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Math"]\n'
        )
        (tmp_path / "Math.geno").write_text(
            "func double(x: Int) -> Int\n  example 3 -> 6\n  return x * 2\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Math\n"
            '@untested("integration")\n'
            "func main() -> Int\n"
            "  return double(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        compiler = JSCompiler()
        js_code = compiler.compile_project(dg)

        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert "10" in result.stdout

    def test_cross_module_types(self, tmp_path):
        """Cross-module type references work in JS output."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Types"]\n'
        )
        (tmp_path / "Types.geno").write_text(
            "type Color = Red | Green | Blue\n"
            "func color_name(c: Color) -> String\n"
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
        compiler = JSCompiler()
        js_code = compiler.compile_project(dg)

        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert "green" in result.stdout

    def test_duplicate_function_names_do_not_leak_into_module_closures(self, tmp_path):
        """A module function should keep calling same-module helpers."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "A", "B"]\n'
        )
        (tmp_path / "A.geno").write_text(
            "func value() -> Int\n"
            "  example () -> 1\n"
            "  return 1\n"
            "end func\n\n"
            "func own() -> Int\n"
            "  example () -> 1\n"
            "  return value()\n"
            "end func\n"
        )
        (tmp_path / "B.geno").write_text(
            "func value() -> Int\n  example () -> 2\n  return 2\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import A\n"
            "import B\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "  return A.own() * 10 + B.value()\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        compiler = JSCompiler()
        js_code = compiler.compile_project(dg)

        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert "12" in result.stdout


# =========================================================================
# HTML multi-module build
# =========================================================================


class TestHTMLMultiModuleBuild:
    def test_two_module_html_build(self, tmp_path):
        """Two-module project compiles to self-contained HTML."""
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Math"]\n'
        )
        (tmp_path / "Math.geno").write_text(
            "func double(x: Int) -> Int\n  example 3 -> 6\n  return x * 2\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Math\n"
            '@untested("integration")\n'
            "func main() -> Int\n"
            "  return double(5)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        html = compile_project_to_html(dg, title="Test App")

        assert "<!DOCTYPE html>" in html
        assert "<canvas" in html
        assert "Test App" in html
        assert "function double" in html
        assert "function main" in html

    def test_single_file_html_still_works(self, tmp_path):
        """Single-file project still builds to HTML via compile_to_html."""
        from geno.js_compiler import compile_to_html

        (tmp_path / "app.geno").write_text(
            '@untested("test")\nfunc main() -> Int\n  return 42\nend func\n'
        )
        source = (tmp_path / "app.geno").read_text()
        html = compile_to_html(source, str(tmp_path / "app.geno"), title="Single")

        assert "<!DOCTYPE html>" in html
        assert "Single" in html
        assert "function main" in html
