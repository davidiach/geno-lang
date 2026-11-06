"""The compiled-Python runtime trace table (proposal 0018, section 8).

A compiled program with a ``main`` starts its generated body with
``_geno_trace_anchor`` and ``_GENO_TRACE``, a versioned table that maps host
lines back to Geno source. These tests pin the table's encoding, where the
anchor lands in every layout the generated code is executed in, that the
recorder's lines name the right generated code even when a block lambda is
compiled through a scratch buffer, and the reporter's body-frame rule.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from geno import _runtime_support as runtime
from geno import runtime_report
from geno.compiler import (
    CompileError,
    Compiler,
    _insert_compiled_runtime_capability_assignment,
    _strip_runtime_prelude_imports,
    _stripped_runtime_prelude,
    compile_to_python,
)
from geno.dependency_graph import DependencyGraph
from geno.project_graph import ProjectGraph
from geno.runtime_prelude import RUNTIME_PRELUDE
from geno.runtime_report import trace_field
from geno.sandbox import ProcessSandbox, ProcessSandboxConfig
from geno.typechecker import TypeChecker

ROOT = Path(__file__).resolve().parents[2]
ANCHOR = "def _geno_trace_anchor(): pass"
TABLE_PREFIX = "_GENO_TRACE = "

PANIC = """func check(n: Int) -> Int
    example 1 -> 1
    if n > 5 then
        panic("value too large")
    end if
    return n
end func

func scale(n: Int) -> Int
    example 2 -> 4
    return check(n * 2)
end func

func main() -> Int
    return scale(4)
end func
"""

# A block lambda (a `map` callback) holding an expression lambda whose body
# is a block lambda: the innermost one compiles into a scratch buffer and is
# spliced into the enclosing definitions later.
NESTED_LAMBDAS = """func g(x: Int) -> Int
    example 1 -> 2
    if x > 2 then
        panic("g failed")
    end if
    return x + 1
end func

func main() -> Int
    let h = fn(z: Int) -> [z].map(fn(x: Int) do
        let f = fn(y: Int) -> g(y)
        return f(x)
    end fn)
    let ys = h(5)
    return length(ys)
end func
"""

CALLBACK = """func g(x: Int) -> Int
    example 1 -> 2
    if x > 2 then
        panic("g failed")
    end if
    return x + 1
end func

func main() -> Int
    let ys = [1, 2, 3].map(fn(x: Int) do
        let doubled = x * 2
        return g(doubled)
    end fn)
    return length(ys)
end func
"""


def _table(code: str) -> tuple[int, list[str], Any]:
    """The anchor's line index, the code's lines and the parsed table."""
    lines = code.split("\n")
    anchor = lines.index(ANCHOR)
    assert lines[anchor + 1].startswith(TABLE_PREFIX)
    table = ast.literal_eval(lines[anchor + 1][len(TABLE_PREFIX) :])
    return anchor, lines, runtime._geno_parse_trace(table)


def _statement_of(code: str, needle: str, occurrence: int = 0) -> Any:
    """The Geno statement the generated line holding *needle* belongs to."""
    anchor, lines, (_functions, statements, marks, candidates, _end) = _table(code)
    found = [
        index
        for index, line in enumerate(lines)
        if index > anchor + 1 and needle in line
    ]
    host_line = found[occurrence] - anchor
    statement = runtime._geno_trace_statement(marks, host_line)
    return statements.get(statement), candidates.get(statement, [])


def _assert_ranges_name_their_defs(code: str) -> None:
    """Every function range holds the `def` of the host name it records."""
    anchor, lines, (functions, _statements, _marks, _candidates, end) = _table(code)
    assert functions
    for start, stop, host, _shown in functions:
        assert 0 <= start < stop <= end
        window = lines[anchor + start : anchor + stop]
        assert any(
            line.lstrip().startswith((f"def {host}(", f"async def {host}("))
            for line in window
        ), (host, window[:3])


def _project(tmp_path: Path, main: str, modules: dict[str, str]) -> str:
    files = ", ".join(f'"{name}"' for name in ["Main", *modules])
    (tmp_path / "geno.toml").write_text(f'entrypoint = "Main"\nfiles = [{files}]\n')
    (tmp_path / "Main.geno").write_text(main)
    for name, source in modules.items():
        (tmp_path / f"{name}.geno").write_text(source)
    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)
    return Compiler().compile_project(graph)


class TestEncoding:
    @given(st.text())
    def test_text_fields_round_trip(self, text: str) -> None:
        escaped = trace_field(text)
        assert "\t" not in escaped and "\n" not in escaped
        assert runtime._geno_trace_text(escaped) == text

    def test_a_path_with_tabs_newlines_and_backslashes_round_trips(self) -> None:
        name = "we\tird\nna\\me.geno"
        code = compile_to_python(PANIC, filename=f"dir/{name}")
        _anchor, _lines, (_functions, statements, *_rest) = _table(code)
        assert {statement[0] for statement in statements.values()} == {name}

    def test_the_table_is_one_string_constant(self) -> None:
        code = compile_to_python(PANIC, filename="panic.geno")
        body = code[len(RUNTIME_PRELUDE) :]
        tree = ast.parse(body)
        assert isinstance(tree.body[0], ast.FunctionDef)
        assert tree.body[0].name == "_geno_trace_anchor"
        assignment = tree.body[1]
        assert isinstance(assignment, ast.Assign)
        assert isinstance(assignment.value, ast.Constant)
        assert isinstance(assignment.value.value, str)
        assert assignment.value.value.startswith("G\t1\n")

    def test_no_marker_survives(self) -> None:
        for source in (PANIC, NESTED_LAMBDAS, CALLBACK):
            assert "\0" not in compile_to_python(source)

    @pytest.mark.parametrize(
        "table",
        [
            "",
            "G\t2\nE\t1",
            "G\t1",
            "G\t1\nX\t1\nE\t3",
            "G\t1\nT\t0\t7\t1\t1\nE\t3",
            "G\t1\nS\tnot-a-line\t0\nE\t3",
            "G\t1\nD\t1\t2\tmain\nE\t3",
        ],
    )
    def test_a_malformed_or_newer_table_is_rejected(self, table: str) -> None:
        with pytest.raises((ValueError, KeyError)):
            runtime._geno_parse_trace(table)

    def test_a_program_without_main_has_no_table(self) -> None:
        library = "func double(n: Int) -> Int\n    example 1 -> 2\n    return n * 2\nend func\n"
        code = compile_to_python(library)
        assert ANCHOR not in code
        assert TABLE_PREFIX not in code

    def test_paths_are_relative_to_the_entry_file(self) -> None:
        code = compile_to_python(PANIC, filename="/build/machine/app/panic.geno")
        _anchor, _lines, (_functions, statements, *_rest) = _table(code)
        assert {statement[0] for statement in statements.values()} == {"panic.geno"}

    def test_the_worker_keeps_the_frontend_paths(self) -> None:
        from geno.lexer import Lexer
        from geno.parser import Parser

        filename = "/build/machine/app/panic.geno"
        program = Parser(Lexer(PANIC, filename).tokenize(), PANIC).parse_program()
        TypeChecker().check_program(program)
        compiler = Compiler()
        compiler.trace_absolute_paths = True
        code = compiler.compile(program)
        _anchor, _lines, (_functions, statements, *_rest) = _table(code)
        assert {statement[0] for statement in statements.values()} == {filename}

    @pytest.mark.parametrize("name", ["_geno_trace_anchor", "_geno_uncaught"])
    def test_the_table_names_are_reserved(self, name: str) -> None:
        source = (
            f"func {name}() -> Int\n    example () -> 1\n    return 1\nend func\n\n"
            "func main() -> Int\n    return 0\nend func\n"
        )
        with pytest.raises(CompileError):
            compile_to_python(source)


class TestAnchorLayouts:
    """The anchor is the body's first line, wherever the body is executed."""

    def test_standalone(self) -> None:
        code = compile_to_python(PANIC, filename="panic.geno")
        assert code.startswith(RUNTIME_PRELUDE)
        assert code[len(RUNTIME_PRELUDE) :].startswith(ANCHOR + "\n" + TABLE_PREFIX)
        _assert_ranges_name_their_defs(code)

    def test_the_worker_stripped_prelude(self) -> None:
        stripped = _strip_runtime_prelude_imports(compile_to_python(PANIC))
        prelude = _stripped_runtime_prelude()
        assert stripped.startswith(prelude)
        assert stripped[len(prelude) :].startswith(ANCHOR + "\n")
        _assert_ranges_name_their_defs(stripped)

    def test_the_prelude_blob(self) -> None:
        stripped = _strip_runtime_prelude_imports(compile_to_python(PANIC))
        sandbox = ProcessSandbox(ProcessSandboxConfig(compiled_runtime_prelude=True))
        payload, overrides = sandbox._frame_prelude_blob(stripped)
        assert overrides is not None
        _header, _blob, tail = payload.split("\n", 2)
        assert tail.startswith(ANCHOR + "\n")
        _assert_ranges_name_their_defs(tail)

    @pytest.mark.parametrize("strip", [False, True], ids=["prelude", "stripped"])
    def test_a_capability_assignment_lands_before_the_anchor(self, strip: bool) -> None:
        code = compile_to_python(PANIC, filename="panic.geno")
        if strip:
            code = _strip_runtime_prelude_imports(code)
        inserted = _insert_compiled_runtime_capability_assignment(code, {"fs"})
        assignment_at = inserted.index("_GENO_CAPS = {'fs',}")
        assert assignment_at < inserted.index(ANCHOR)
        # The table still names the right lines: offsets count from the anchor.
        _assert_ranges_name_their_defs(inserted)
        assert _statement_of(inserted, "return scale(")[0] == ("panic.geno", 15, 5)


class TestRecorderLines:
    """Each statement record names the generated lines that hold its code."""

    def test_every_statement_kind_is_recorded(self) -> None:
        source = """func main() -> Int
    var total = 0
    total = total + 1
    print(to_string(total))
    let xs = [1, 2]
    return xs[total]
end func
"""
        code = compile_to_python(source, filename="kinds.geno")
        assert _statement_of(code, "total = _geno_deepcopy(0)")[0] == (
            "kinds.geno",
            2,
            5,
        )
        # Assignments and expression statements have no `# geno:` comment.
        assert _statement_of(code, "total = ((total)")[0] == ("kinds.geno", 3, 5)
        assert _statement_of(code, "print_(")[0] == ("kinds.geno", 4, 5)
        statement, candidates = _statement_of(code, "return _safe_index(")
        assert statement == ("kinds.geno", 6, 5)
        assert ("_safe_index", 6, 12) in candidates

    def test_block_lambdas_nested_in_expression_lambdas(self) -> None:
        code = compile_to_python(NESTED_LAMBDAS, filename="nested.geno")
        statement, candidates = _statement_of(code, "g(y)")
        assert statement == ("nested.geno", 11, 9)
        assert ("g", 11, 31) in candidates
        assert _statement_of(code, "return f(x)")[0] == ("nested.geno", 12, 9)
        # The enclosing statements resume after the spliced definitions.
        assert _statement_of(code, "h = ")[0] == ("nested.geno", 10, 5)
        assert _statement_of(code, "ys = ")[0] == ("nested.geno", 14, 5)
        _assert_ranges_name_their_defs(code)

    def test_a_block_lambda_callback(self) -> None:
        code = compile_to_python(CALLBACK, filename="callback.geno")
        assert _statement_of(code, "doubled = ")[0] == ("callback.geno", 11, 9)
        statement, candidates = _statement_of(code, "return g(doubled)")
        assert statement == ("callback.geno", 12, 9)
        assert ("g", 12, 16) in candidates
        statement, candidates = _statement_of(code, "list_map(")
        assert statement == ("callback.geno", 10, 5)
        assert ("list_map", 10, 14) in candidates

    def test_contract_checks_are_located_at_their_clause(self) -> None:
        source = """func safe_div(a: Int, b: Int) -> Int
    requires b != 0
    ensures result >= 0
    example 6, 3 -> 2
    return a / b
end func

func main() -> Int
    return safe_div(a: 8, b: 0)
end func
"""
        code = compile_to_python(source, filename="contract.geno")
        assert _statement_of(code, "raise _geno_contract_violation('requires'")[0] == (
            "contract.geno",
            2,
            14,
        )
        assert _statement_of(code, "raise _geno_contract_violation('ensures'")[0] == (
            "contract.geno",
            3,
            13,
        )

    def test_module_factories(self, tmp_path: Path) -> None:
        main = "import Helper\n\nfunc main() -> Int\n    return Helper.scale(4)\nend func\n"
        helper = PANIC.split("func main", 1)[0]
        code = _project(tmp_path, main, {"Helper": helper})
        _anchor, _lines, (functions, *_rest) = _table(code)
        shown = {function[3] for function in functions}
        assert {"main", "Helper.check", "Helper.scale"} <= shown
        statement, candidates = _statement_of(code, "return check(")
        assert statement == ("Helper.geno", 11, 5)
        assert ("check", 11, 12) in candidates
        statement, candidates = _statement_of(code, "return get_field(")
        assert statement == ("Main.geno", 4, 5)
        assert ("scale", 4, 12) in candidates
        _assert_ranges_name_their_defs(code)


def _exec_blob_layout(source: str) -> dict[str, Any]:
    """Execute *source*'s compiled body as the prelude-blob worker does.

    The prelude is compiled under its own file name, and the body is a
    ``<string>`` whose line 1 is the anchor.
    """
    code = compile_to_python(source, filename="long.geno")
    module = types.ModuleType("__geno_trace_test__")
    namespace = module.__dict__
    # Dataclasses look their module up while the prelude's classes are made.
    sys.modules[module.__name__] = module
    try:
        exec(compile(RUNTIME_PRELUDE, "<geno-runtime-prelude>", "exec"), namespace)
        namespace["_GENO_CAPS"] = frozenset()
        exec(compile(code[len(RUNTIME_PRELUDE) :], "<string>", "exec"), namespace)
    finally:
        sys.modules.pop(module.__name__, None)
    return namespace


class TestBodyFrames:
    def test_a_worker_frame_with_the_same_file_name_lines_and_name_is_dropped(
        self,
    ) -> None:
        """The trusted worker is itself a ``<string>``: globals tell them apart.

        In the blob layout the body starts at line 1, so a program `main`
        longer than the worker script covers the lines of the worker's own
        ``def main()``, which has the same code name and file name.
        """
        statements = "\n".join(f"    let a{i} = n + {i}" for i in range(80))
        source = (
            "func check(n: Int) -> Int\n    example 1 -> 1\n"
            '    if n > 5 then\n        panic("too big")\n    end if\n'
            "    return n\nend func\n\n"
            f"func main() -> Int\n    let n = 9\n{statements}\n"
            "    return check(n)\nend func\n"
        )
        program = _exec_blob_layout(source)
        anchor_line = program["_geno_trace_anchor"].__code__.co_firstlineno
        functions = runtime._geno_parse_trace(program["_GENO_TRACE"])[0]
        start, stop = next((f[0], f[1]) for f in functions if f[2] == "main")
        # A worker `main` whose call line falls inside the program's `main`.
        worker_line = anchor_line + start + (stop - start) // 2
        worker_source = "\n" * (worker_line - 2) + "def main():\n    run_program()\n"
        worker: dict[str, Any] = {"run_program": program["main"]}
        exec(compile(worker_source, "<string>", "exec"), worker)
        with pytest.raises(RuntimeError) as caught:
            worker["main"]()
        frame = caught.value.__traceback__.tb_next  # the worker's main
        assert frame.tb_frame.f_code.co_name == "main"
        assert frame.tb_frame.f_code.co_filename == "<string>"
        assert start <= frame.tb_lineno - anchor_line < stop

        frames, omitted, location = runtime._geno_failure_report(
            caught.value, program["_GENO_TRACE"], program["_geno_trace_anchor"]
        )
        assert [name for name, _position in frames] == ["main", "check"]
        assert frames[0][1] == ("long.geno", 91, 12)
        assert location == ("long.geno", 4, 9)
        assert omitted == 0

    def test_the_mapping_needs_no_program_method(self) -> None:
        program = _exec_blob_layout(PANIC)
        with pytest.raises(RuntimeError) as caught:
            program["main"]()
        frames, omitted, location = runtime._geno_failure_report(
            caught.value, program["_GENO_TRACE"], program["_geno_trace_anchor"]
        )
        assert frames == [
            ("main", ("long.geno", 15, 12)),
            ("scale", ("long.geno", 11, 12)),
            ("check", ("long.geno", 4, 9)),
        ]
        assert (omitted, location) == (0, ("long.geno", 4, 9))

    def test_a_safe_print_output_limit_failure_points_at_the_print(
        self, tmp_path: Path
    ) -> None:
        """The worker's ``safe_print`` raises; the frame is the print call."""
        (tmp_path / "flood.geno").write_text(
            "func main() -> Int\n    var i = 0\n    while i < 100 do\n"
            '        print("0123456789")\n        i = i + 1\n    end while\n'
            "    return 0\nend func\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "run",
                "--max-output-length",
                "100",
                "flood.geno",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tmp_path,
        )
        assert result.returncode == 1
        lines = result.stderr.splitlines()
        assert lines[0] == runtime_report.STACK_HEADER
        assert lines[1].endswith("flood.geno:4:9 in main")
        assert lines[-1] == "Runtime Error: Output limit exceeded (100 characters)"
        assert "<string>" not in result.stderr
        assert " in safe_print" not in result.stderr
        assert " in print_" not in result.stderr


class TestPreludeCopies:
    """The prelude's copies of runtime_report agree with it."""

    def test_the_constants_agree(self) -> None:
        assert runtime._GENO_STACK_HEAD == runtime_report.STACK_HEAD
        assert runtime._GENO_STACK_TAIL == runtime_report.STACK_TAIL
        assert runtime._GENO_STACK_HEADER == runtime_report.STACK_HEADER

    @pytest.mark.parametrize("count", [0, 1, 3, 20, 21, 42])
    def test_the_frame_block_renders_the_same_bytes(self, count: int) -> None:
        from geno.diagnostics import StackFrame
        from geno.tokens import SourceLocation

        frames = [
            (f"f{i}", ("app.geno", i + 1, 5) if i % 3 else None) for i in range(count)
        ]
        location = ("app.geno", 9, 2)
        kept = frames
        omitted = 0
        if count > 20:
            kept = frames[:10] + frames[-10:]
            omitted = count - 20
        ours = runtime._geno_report_lines(kept, omitted, location)
        stack = runtime_report.GenoStack(
            tuple(
                StackFrame(
                    name,
                    None
                    if position is None
                    else SourceLocation(
                        line=position[1], column=position[2], filename=position[0]
                    ),
                )
                for name, position in kept
            ),
            omitted,
        )
        theirs = runtime_report.report_lines(
            "Runtime Error",
            "boom",
            stack=stack if kept else None,
            location=SourceLocation(line=9, column=2, filename="app.geno"),
        )[:-1]
        assert ours == theirs
