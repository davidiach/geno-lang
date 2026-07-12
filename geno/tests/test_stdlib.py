"""
Tests for the Geno standard library (std/)
==========================================
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


class TestStdStringTypecheck:
    """String.geno should parse and typecheck standalone."""

    def test_string_geno_typechecks(self):
        from pathlib import Path

        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        path = Path(__file__).parent.parent / "std" / "String.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()

        checker = TypeChecker()
        checker.check_program(program)

    def test_string_geno_has_all_functions(self):
        from pathlib import Path

        from geno.ast_nodes import FunctionDef
        from geno.lexer import Lexer
        from geno.parser import Parser

        path = Path(__file__).parent.parent / "std" / "String.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()

        func_names = {d.name for d in program.definitions if isinstance(d, FunctionDef)}
        expected = {
            "pad_left",
            "pad_right",
            "trim",
            "trim_start",
            "trim_end",
            "repeat",
            "char_at",
            "index_of",
            "last_index_of",
            "substring",
            "split",
            "join",
            "replace",
            "to_upper",
            "to_lower",
            "starts_with",
            "ends_with",
            "contains",
            "split_once",
        }
        assert expected == func_names


class TestStdStringExamples:
    """All example clauses in String.geno should pass."""

    def test_examples_pass(self):
        from pathlib import Path

        from geno.test_runner import run_test_suite

        path = Path(__file__).parent.parent / "std" / "String.geno"
        result = run_test_suite([path])
        assert result.failed == 0
        assert result.passed > 0


class TestStdStringImport:
    """Import String from a project and use it via qualified access."""

    def test_qualified_access_typechecks(self, tmp_path):
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph
        from geno.typechecker import TypeChecker

        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import String\n"
            '@untested("test")\n'
            "func main() -> String\n"
            '    return String.trim("  hi  ")\n'
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    def test_qualified_access_interprets(self, tmp_path):
        from geno.dependency_graph import DependencyGraph
        from geno.interpreter import Interpreter
        from geno.project_graph import ProjectGraph
        from geno.typechecker import TypeChecker

        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import String\n"
            '@untested("test")\n'
            "func main() -> String\n"
            '    return String.trim("  hello  ")\n'
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)

        # Typecheck
        checker = TypeChecker()
        checker.check_project_graph(dg)

        # Run in interpreter
        main_mod = pg.entrypoint
        assert main_mod is not None
        program = dg.parsed[main_mod]
        parsed_modules = {
            name: dg.parsed[name] for name in dg.sorted_modules if name != main_mod
        }
        interp = Interpreter(check_examples=False)
        result = interp.run(program, modules=parsed_modules)
        assert result == "hello"

    def test_compiled_python(self, tmp_path):
        from geno.compiler import Compiler
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import String\n"
            '@untested("test")\n'
            "func main() -> String\n"
            '    return String.char_at("hello", 0)\n'
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        compiler = Compiler()
        code = compiler.compile_project(dg)
        assert "String" in code
        assert "char_at" in code

    def test_compiled_js(self, tmp_path):
        from geno.dependency_graph import DependencyGraph
        from geno.js_compiler import compile_project_to_html
        from geno.project_graph import ProjectGraph

        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import String\n"
            '@untested("test")\n'
            "func main() -> String\n"
            '    return String.index_of("abc", "b")\n'
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        html = compile_project_to_html(dg)
        assert "String" in html
        assert "index_of" in html

    def test_compiled_python_executes_wrapper_imports(self, tmp_path):
        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import String\n"
            "import List\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "    let ys: List[Int] = List.map([1, 2, 3], fn(x: Int) -> x + 1)\n"
            "    let zs: List[Int] = List.filter(ys, fn(x: Int) -> x > 2)\n"
            '    let parts: List[String] = String.split("a,b,c", ",")\n'
            '    let joined: String = String.join(parts, "-")\n'
            '    let replaced: String = String.replace(text: joined, old: "b", new: "B")\n'
            '    let lower: String = String.to_lower("HI")\n'
            '    if String.to_upper("abc") == "ABC" and String.starts_with(replaced, "a-B") and String.ends_with(replaced, "c") and String.contains(lower, "hi") and is_some(String.split_once("k=v", "=")) then\n'
            "        return List.length(zs)\n"
            "    end if\n"
            "    return 0\n"
            "end func\n"
        )

        outfile = tmp_path / "out.py"
        compile_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(tmp_path),
                "-o",
                str(outfile),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert compile_result.returncode == 0, compile_result.stderr

        run_result = subprocess.run(
            [sys.executable, str(outfile)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run_result.returncode == 0, run_result.stderr
        assert run_result.stdout.strip() == "2"

    def test_compiled_js_executes_wrapper_imports(self, tmp_path):
        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import String\n"
            "import List\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "    let ys: List[Int] = List.map([1, 2, 3], fn(x: Int) -> x + 1)\n"
            "    let zs: List[Int] = List.filter(ys, fn(x: Int) -> x > 2)\n"
            '    let parts: List[String] = String.split("a,b,c", ",")\n'
            '    let joined: String = String.join(parts, "-")\n'
            '    let replaced: String = String.replace(text: joined, old: "b", new: "B")\n'
            '    let lower: String = String.to_lower("HI")\n'
            '    if String.to_upper("abc") == "ABC" and String.starts_with(replaced, "a-B") and String.ends_with(replaced, "c") and String.contains(lower, "hi") and is_some(String.split_once("k=v", "=")) then\n'
            "        return List.length(zs)\n"
            "    end if\n"
            "    return 0\n"
            "end func\n"
        )

        outfile = tmp_path / "out.js"
        compile_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(tmp_path),
                "--target",
                "js",
                "-o",
                str(outfile),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert compile_result.returncode == 0, compile_result.stderr

        run_result = subprocess.run(
            ["node", str(outfile)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run_result.returncode == 0, run_result.stderr
        assert run_result.stdout.strip() == "2"


class TestStdGenericForwarders:
    """Std module wrappers expose the generic builtin signatures they call."""

    def _typecheck_project(self, tmp_path, source: str) -> None:
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph
        from geno.typechecker import TypeChecker

        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(source)

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        TypeChecker().check_project_graph(dg)

    def test_list_take_preserves_string_element_type(self, tmp_path):
        self._typecheck_project(
            tmp_path,
            "import List\n"
            "func main() -> List[String]\n"
            '    return List.take(["a", "b"], 1)\n'
            "end func\n",
        )

    def test_option_unwrap_or_preserves_string_type(self, tmp_path):
        self._typecheck_project(
            tmp_path,
            "import Option\n"
            "func main() -> String\n"
            '    return Option.unwrap_or(Some("a"), "fallback")\n'
            "end func\n",
        )

    def test_map_get_preserves_value_type(self, tmp_path):
        self._typecheck_project(
            tmp_path,
            "import Map\n"
            "func main() -> Option[String]\n"
            '    return Map.get(map_from_list([("a", "x")]), "a")\n'
            "end func\n",
        )

    def test_math_forwarders_preserve_float_numeric_type(self, tmp_path):
        self._typecheck_project(
            tmp_path,
            "import Math\n"
            "func main() -> Float\n"
            "    let x: Float = Math.abs(0.0 - 1.5)\n"
            "    let y: Float = Math.min(x, 2.5)\n"
            "    let z: Float = Math.max(y, 1.0)\n"
            "    return Math.clamp(value: z, lo: 0.5, hi: 2.0)\n"
            "end func\n",
        )

    def test_result_unwrap_or_preserves_ok_type(self, tmp_path):
        self._typecheck_project(
            tmp_path,
            "import Result\n"
            "func main() -> String\n"
            '    return Result.unwrap_or(Ok("a"), "fallback")\n'
            "end func\n",
        )


class TestStdListTypecheck:
    """List.geno should parse and typecheck standalone."""

    def test_list_geno_typechecks(self):
        from pathlib import Path

        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        path = Path(__file__).parent.parent / "std" / "List.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    def test_list_geno_has_all_functions(self):
        from pathlib import Path

        from geno.ast_nodes import FunctionDef
        from geno.lexer import Lexer
        from geno.parser import Parser

        path = Path(__file__).parent.parent / "std" / "List.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        func_names = {d.name for d in program.definitions if isinstance(d, FunctionDef)}
        expected = {
            "zip",
            "enumerate",
            "flatten",
            "chunk",
            "take",
            "drop",
            "find",
            "find_index",
            "all",
            "any",
            "fold_right",
            "intersperse",
            "group_by",
            "length",
            "map",
            "filter",
        }
        assert expected == func_names


class TestStdListExamples:
    def test_examples_pass(self):
        from pathlib import Path

        from geno.test_runner import run_test_suite

        path = Path(__file__).parent.parent / "std" / "List.geno"
        result = run_test_suite([path])
        assert result.failed == 0
        assert result.passed > 0


class TestStdMapTypecheck:
    def test_map_geno_typechecks(self):
        from pathlib import Path

        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        path = Path(__file__).parent.parent / "std" / "Map.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    def test_map_geno_has_all_functions(self):
        from pathlib import Path

        from geno.ast_nodes import FunctionDef
        from geno.lexer import Lexer
        from geno.parser import Parser

        path = Path(__file__).parent.parent / "std" / "Map.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        func_names = {d.name for d in program.definitions if isinstance(d, FunctionDef)}
        expected = {
            "from_list",
            "merge",
            "filter_map",
            "map_values",
            "entries",
            "from_entries",
            "get",
            "insert",
        }
        assert expected == func_names


class TestStdMathTypecheck:
    """Math.geno should parse and typecheck standalone."""

    def test_math_geno_typechecks(self):
        from pathlib import Path

        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        path = Path(__file__).parent.parent / "std" / "Math.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    def test_math_geno_has_all_functions(self):
        from pathlib import Path

        from geno.ast_nodes import FunctionDef
        from geno.lexer import Lexer
        from geno.parser import Parser

        path = Path(__file__).parent.parent / "std" / "Math.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        func_names = {d.name for d in program.definitions if isinstance(d, FunctionDef)}
        expected = {
            "abs",
            "min",
            "max",
            "clamp",
            "floor",
            "ceil",
            "round",
            "sqrt",
            "log",
            "sin",
            "cos",
            "pi",
            "e",
            "random_int",
            "random_float",
        }
        assert expected == func_names


class TestStdMathExamples:
    def test_examples_pass(self):
        from pathlib import Path

        from geno.test_runner import run_test_suite

        path = Path(__file__).parent.parent / "std" / "Math.geno"
        result = run_test_suite([path])
        assert result.failed == 0
        assert result.passed > 0


class TestStdResultTypecheck:
    """Result.geno should parse and typecheck standalone."""

    def test_result_geno_typechecks(self):
        from pathlib import Path

        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        path = Path(__file__).parent.parent / "std" / "Result.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    def test_result_geno_has_all_functions(self):
        from pathlib import Path

        from geno.ast_nodes import FunctionDef
        from geno.lexer import Lexer
        from geno.parser import Parser

        path = Path(__file__).parent.parent / "std" / "Result.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        func_names = {d.name for d in program.definitions if isinstance(d, FunctionDef)}
        expected = {
            "map",
            "map_err",
            "and_then",
            "unwrap_or",
            "is_ok",
            "is_err",
            "to_option",
        }
        assert expected == func_names


class TestStdResultExamples:
    def test_examples_pass(self):
        from pathlib import Path

        from geno.test_runner import run_test_suite

        path = Path(__file__).parent.parent / "std" / "Result.geno"
        result = run_test_suite([path])
        assert result.failed == 0
        assert result.passed > 0


class TestStdResultImport:
    def test_qualified_access_interprets(self, tmp_path):
        from geno.dependency_graph import DependencyGraph
        from geno.interpreter import Interpreter
        from geno.project_graph import ProjectGraph
        from geno.typechecker import TypeChecker

        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import Result\n"
            '@untested("test")\n'
            "func main() -> Bool\n"
            "    return Result.is_ok(Ok(42))\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

        main_mod = pg.entrypoint
        assert main_mod is not None
        program = dg.parsed[main_mod]
        parsed_modules = {
            name: dg.parsed[name] for name in dg.sorted_modules if name != main_mod
        }
        interp = Interpreter(check_examples=False)
        result = interp.run(program, modules=parsed_modules)
        assert result is True


class TestStdOptionTypecheck:
    """Option.geno should parse and typecheck standalone."""

    def test_option_geno_typechecks(self):
        from pathlib import Path

        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        path = Path(__file__).parent.parent / "std" / "Option.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    def test_option_geno_has_all_functions(self):
        from pathlib import Path

        from geno.ast_nodes import FunctionDef
        from geno.lexer import Lexer
        from geno.parser import Parser

        path = Path(__file__).parent.parent / "std" / "Option.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        func_names = {d.name for d in program.definitions if isinstance(d, FunctionDef)}
        expected = {
            "map",
            "and_then",
            "unwrap_or",
            "is_some",
            "is_none",
            "flatten",
            "to_result",
        }
        assert expected == func_names


class TestStdOptionExamples:
    def test_examples_pass(self):
        from pathlib import Path

        from geno.test_runner import run_test_suite

        path = Path(__file__).parent.parent / "std" / "Option.geno"
        result = run_test_suite([path])
        assert result.failed == 0
        assert result.passed > 0


class TestStdOptionImport:
    def test_qualified_access_interprets(self, tmp_path):
        from geno.dependency_graph import DependencyGraph
        from geno.interpreter import Interpreter
        from geno.project_graph import ProjectGraph
        from geno.typechecker import TypeChecker

        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import Option\n"
            '@untested("test")\n'
            "func main() -> Int\n"
            "    return Option.unwrap_or(Some(10), 0)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

        main_mod = pg.entrypoint
        assert main_mod is not None
        program = dg.parsed[main_mod]
        parsed_modules = {
            name: dg.parsed[name] for name in dg.sorted_modules if name != main_mod
        }
        interp = Interpreter(check_examples=False)
        result = interp.run(program, modules=parsed_modules)
        assert result == 10


class TestStdPathTypecheck:
    """Path.geno should parse and typecheck standalone."""

    def test_path_geno_typechecks(self):
        from pathlib import Path

        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        path = Path(__file__).parent.parent / "std" / "Path.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    def test_path_geno_has_all_functions(self):
        from pathlib import Path

        from geno.ast_nodes import FunctionDef
        from geno.lexer import Lexer
        from geno.parser import Parser

        path = Path(__file__).parent.parent / "std" / "Path.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        func_names = {d.name for d in program.definitions if isinstance(d, FunctionDef)}
        expected = {"join", "parent", "filename", "extension", "is_absolute"}
        assert expected == func_names


class TestStdPathExamples:
    def test_examples_pass(self):
        from pathlib import Path

        from geno.test_runner import run_test_suite

        path = Path(__file__).parent.parent / "std" / "Path.geno"
        result = run_test_suite([path])
        assert result.failed == 0
        assert result.passed > 0


class TestStdDateTimeTypecheck:
    """DateTime.geno should parse and typecheck standalone."""

    def test_datetime_geno_typechecks(self):
        from pathlib import Path

        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        path = Path(__file__).parent.parent / "std" / "DateTime.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    def test_datetime_geno_has_all_functions(self):
        from pathlib import Path

        from geno.ast_nodes import FunctionDef
        from geno.lexer import Lexer
        from geno.parser import Parser

        path = Path(__file__).parent.parent / "std" / "DateTime.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        func_names = {d.name for d in program.definitions if isinstance(d, FunctionDef)}
        expected = {
            "now",
            "from_timestamp",
            "to_timestamp",
            "format",
            "parse",
            "elapsed",
        }
        assert expected == func_names

    def test_datetime_geno_defines_type(self):
        from pathlib import Path

        from geno.ast_nodes import TypeDef
        from geno.lexer import Lexer
        from geno.parser import Parser

        path = Path(__file__).parent.parent / "std" / "DateTime.geno"
        source = path.read_text()
        tokens = Lexer(source, str(path)).tokenize()
        program = Parser(tokens).parse_program()
        type_names = {d.name for d in program.definitions if isinstance(d, TypeDef)}
        assert "DateTime" in type_names


class TestStdDateTimeExamples:
    def test_examples_pass(self):
        from pathlib import Path

        from geno.test_runner import run_test_suite

        path = Path(__file__).parent.parent / "std" / "DateTime.geno"
        result = run_test_suite([path])
        assert result.failed == 0
        assert result.passed > 0


class TestStdListImport:
    def test_qualified_access_interprets(self, tmp_path):
        from geno.dependency_graph import DependencyGraph
        from geno.interpreter import Interpreter
        from geno.project_graph import ProjectGraph
        from geno.typechecker import TypeChecker

        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import List\n"
            '@untested("test")\n'
            "func main() -> List[Int]\n"
            "    return List.take([1, 2, 3, 4, 5], 3)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

        main_mod = pg.entrypoint
        assert main_mod is not None
        program = dg.parsed[main_mod]
        parsed_modules = {
            name: dg.parsed[name] for name in dg.sorted_modules if name != main_mod
        }
        interp = Interpreter(check_examples=False)
        result = interp.run(program, modules=parsed_modules)
        assert result == [1, 2, 3]

    def test_aliased_import(self, tmp_path):
        from geno.dependency_graph import DependencyGraph
        from geno.project_graph import ProjectGraph
        from geno.typechecker import TypeChecker

        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            "import String as S\n"
            '@untested("test")\n'
            "func main() -> String\n"
            '    return S.trim("  hi  ")\n'
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)
