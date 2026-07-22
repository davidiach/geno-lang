"""
Tests for multi-module compilation (#128, #129)
================================================
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.compiler import CompileError, Compiler
from geno.dependency_graph import DependencyGraph
from geno.js_compiler import JSCompileError, JSCompiler, compile_project_to_html
from geno.project_graph import ProjectGraph
from geno.target_profile import TargetProfile
from geno.tests._script_runner import run_node_code, run_python_code
from geno.typechecker import TypeChecker

# =========================================================================
# Python multi-module compilation
# =========================================================================


class TestPythonMultiModule:
    @pytest.mark.parametrize(
        ("compiler", "error_type", "helper"),
        [
            (Compiler, CompileError, "_require_cap"),
            (JSCompiler, JSCompileError, "_requireCap"),
        ],
    )
    def test_project_rejects_entrypoint_runtime_helper_export(
        self, tmp_path, compiler, error_type, helper
    ):
        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            f"func {helper}(value: String, context: String) -> Unit\n"
            '  example "x", "y" -> ()\n'
            "  return ()\n"
            "end func\n"
            "func main() -> Int\n"
            "  return 1\n"
            "end func\n"
        )

        graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
        with pytest.raises(error_type, match="reserved runtime name"):
            compiler().compile_project(graph)

    @pytest.mark.parametrize(
        ("compiler", "error_type", "module_name"),
        [
            (Compiler, CompileError, "_require_cap"),
            (JSCompiler, JSCompileError, "_requireCap"),
        ],
    )
    def test_project_rejects_runtime_helper_module_name(
        self, tmp_path, compiler, error_type, module_name
    ):
        (tmp_path / "geno.toml").write_text(f'files = ["{module_name}"]\n')
        (tmp_path / f"{module_name}.geno").write_text(
            "func main() -> Int\n  return 1\nend func\n"
        )
        graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
        with pytest.raises(error_type, match="reserved runtime module name"):
            compiler().compile_project(graph)

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


def test_python_module_name_does_not_shadow_host_builtin(tmp_path):
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "RuntimeError"]\n'
    )
    (tmp_path / "RuntimeError.geno").write_text(
        "func value() -> Int\n  example () -> 7\n  return 7\nend func\n"
    )
    (tmp_path / "Main.geno").write_text(
        "import RuntimeError\n"
        '@untested("integration")\n'
        "func main() -> Int\n"
        "  return length([RuntimeError.value(), 2])\n"
        "end func\n"
    )

    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)
    result = run_python_code(
        Compiler().compile_project(graph), python_executable=sys.executable, timeout=10
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "2"


def test_python_export_cannot_capture_emitted_host_builtin(tmp_path):
    (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
    (tmp_path / "Main.geno").write_text(
        "func len(value: Int) -> Int\n"
        "  example 1 -> 1\n"
        "  return value\n"
        "end func\n"
        "func main() -> Int\n"
        "  return length([1, 2])\n"
        "end func\n"
    )

    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)
    with pytest.raises(CompileError, match="reserved runtime name"):
        Compiler().compile_project(graph)


def test_python_project_min_max_exports_do_not_capture_substring_intrinsics(tmp_path):
    (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
    (tmp_path / "Main.geno").write_text(
        "func min(a: Int, b: Int) -> Int\n"
        "  example 1, 2 -> 1\n"
        "  if a < b then\n"
        "    return a\n"
        "  end if\n"
        "  return b\n"
        "end func\n"
        "func max(a: Int, b: Int) -> Int\n"
        "  example 1, 2 -> 2\n"
        "  if a > b then\n"
        "    return a\n"
        "  end if\n"
        "  return b\n"
        "end func\n"
        "func clipped(text: String, start: Int, stop: Int) -> String\n"
        '  example "abc", 0, 2 -> "ab"\n'
        "  return substring(text: text, start: start, stop: stop)\n"
        "end func\n"
        "func main() -> Int\n"
        '  return length(clipped(text: "abc", start: 0, stop: 2))\n'
        "end func\n"
    )

    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)
    python_code = Compiler().compile_project(graph)
    result = run_python_code(python_code, python_executable=sys.executable, timeout=10)

    assert result.returncode == 0
    assert result.stdout.strip() == "2"
    assert "_builtin_max(0," in python_code
    assert "_builtin_min(len(" in python_code


def test_js_module_name_does_not_shadow_host_global(tmp_path):
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "Array"]\n'
    )
    (tmp_path / "Array.geno").write_text(
        "func value() -> Int\n  example () -> 7\n  return 7\nend func\n"
    )
    (tmp_path / "Main.geno").write_text(
        "import Array\n"
        '@untested("integration")\n'
        "func main() -> Int\n"
        "  return length([Array.value(), 2])\n"
        "end func\n"
    )

    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)
    result = run_node_code(JSCompiler().compile_project(graph), timeout=10)

    assert result.returncode == 0
    assert result.stdout.strip() == "2"


def test_js_lowercase_standalone_entry_module_executes(tmp_path):
    entry = tmp_path / "main.geno"
    entry.write_text("func main() -> Int\n  return 2\nend func\n")

    graph = DependencyGraph.resolve(ProjectGraph.discover(entry))
    TypeChecker().check_project_graph(graph)
    result = run_node_code(JSCompiler().compile_project(graph), timeout=10)

    assert result.returncode == 0
    assert result.stdout.strip() == "2"


def test_js_export_cannot_capture_emitted_host_global(tmp_path):
    (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
    (tmp_path / "Main.geno").write_text(
        "func console(value: Int) -> Int\n"
        "  example 1 -> 1\n"
        "  return value\n"
        "end func\n"
        "func main() -> Int\n"
        "  return length([1, 2])\n"
        "end func\n"
    )

    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)
    with pytest.raises(JSCompileError, match="reserved runtime name"):
        JSCompiler().compile_project(graph)


@pytest.mark.parametrize(
    ("compiler", "error_type"),
    [(Compiler, CompileError), (JSCompiler, JSCompileError)],
)
@pytest.mark.parametrize(
    ("import_line", "constructor_name"),
    [("import Helper", "Helper"), ("import Helper as H", "H")],
)
def test_imported_module_binding_cannot_collide_with_local_constructor(
    tmp_path, compiler, error_type, import_line, constructor_name
):
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "Helper"]\n'
    )
    (tmp_path / "Helper.geno").write_text(
        "func value() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )
    (tmp_path / "Main.geno").write_text(
        f"{import_line}\n"
        f"type Local = {constructor_name}(value: Int)\n"
        "func main() -> Int\n"
        "  return 1\n"
        "end func\n"
    )

    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)
    with pytest.raises(error_type, match="conflicts with a local export"):
        compiler().compile_project(graph)


def test_js_project_app_lifecycle_uses_private_entry_namespace(tmp_path):
    (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
    (tmp_path / "Main.geno").write_text(
        "func init() -> Int\n"
        "  return 0\n"
        "end func\n"
        "func update(model: Int, dt: Float) -> Int\n"
        "  return model + 1\n"
        "end func\n"
        "func render(model: Int) -> Unit\n"
        "  return ()\n"
        "end func\n"
    )

    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker(target_profile=TargetProfile.load("browser")).check_project_graph(graph)
    js_code = JSCompiler().compile_project(graph)
    runner = (
        "globalThis.__geno_frame_seen = false;\n"
        "globalThis.requestAnimationFrame = (cb) => {\n"
        "  if (!globalThis.__geno_frame_seen) {\n"
        "    globalThis.__geno_frame_seen = true; cb(16);\n"
        "  }\n"
        "};\n"
    )
    result = run_node_code(runner + js_code, timeout=10)

    assert result.returncode == 0
    assert "['init']()" in js_code
    assert "['update'](_geno_state, dt)" in js_code
    assert "['render'](_geno_state)" in js_code


def test_project_main_does_not_collide_with_trait_dispatcher(tmp_path):
    (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
    (tmp_path / "Main.geno").write_text(
        "type ThingType = Thing(value: Int)\n"
        "trait Runnable\n"
        "  func main(self: Self) -> Int\n"
        "end trait\n"
        "impl Runnable for ThingType\n"
        "  func main(self: ThingType) -> Int\n"
        "    return 1\n"
        "  end func\n"
        "end impl\n"
        "func main() -> Int\n"
        "  return 7\n"
        "end func\n"
    )

    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)
    py_result = run_python_code(
        Compiler().compile_project(graph), python_executable=sys.executable, timeout=10
    )
    js_result = run_node_code(JSCompiler().compile_project(graph), timeout=10)

    assert py_result.returncode == 0
    assert py_result.stdout.strip() == "7"
    assert js_result.returncode == 0
    assert js_result.stdout.strip() == "7"


def test_duplicate_import_alias_uses_last_module_without_redeclaration(tmp_path):
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "A", "B"]\n'
    )
    (tmp_path / "A.geno").write_text(
        "func value() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )
    (tmp_path / "B.geno").write_text(
        "func value() -> Int\n  example () -> 2\n  return 2\nend func\n"
    )
    (tmp_path / "Main.geno").write_text(
        "import A as X\n"
        "import B as X\n"
        "func main() -> Int\n"
        "  return X.value()\n"
        "end func\n"
    )

    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)
    py_result = run_python_code(
        Compiler().compile_project(graph), python_executable=sys.executable, timeout=10
    )
    js_result = run_node_code(JSCompiler().compile_project(graph), timeout=10)

    assert py_result.returncode == 0
    assert py_result.stdout.strip() == "2"
    assert js_result.returncode == 0
    assert js_result.stdout.strip() == "2"
