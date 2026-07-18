"""
CLI Tests for Geno
==================

Tests for the command-line interface.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Literal

import pytest

from geno.execution_limits import DEFAULT_INTERPRETER_MAX_STEPS
from geno.tests.project_resolution_fixture_helpers import (
    write_dependency_collision_fixture,
)


def _write_circular_project(tmp_path):
    (tmp_path / "geno.toml").write_text('entrypoint = "A"\nfiles = ["A", "B"]\n')
    (tmp_path / "A.geno").write_text(
        "import B\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return b()\n"
        "end func\n"
    )
    (tmp_path / "B.geno").write_text(
        "import A\nfunc b() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )


def _write_selfhost_cli_project(tmp_path):
    (tmp_path / "Main.geno").write_text(
        "import Util\n"
        '@untested("entry point")\n'
        "func main() -> Unit\n"
        "    print(to_string(add_one(41)))\n"
        "    return ()\n"
        "end func main\n"
    )
    (tmp_path / "Util.geno").write_text(
        "func add_one(x: Int) -> Int\n"
        "    example 1 -> 2\n"
        "    return x + 1\n"
        "end func add_one\n"
    )


def _write_selfhost_namespaced_builtin_project(tmp_path):
    (tmp_path / "Main.geno").write_text(
        "import Util\n"
        '@untested("entry point")\n'
        "func main() -> Unit\n"
        "    print(to_string(item_count()))\n"
        "    print(greeting())\n"
        "    return ()\n"
        "end func main\n"
    )
    (tmp_path / "Util.geno").write_text(
        "func item_count() -> Int\n"
        "    example () -> 3\n"
        "    return list_length([1, 2, 3])\n"
        "end func item_count\n"
        "\n"
        "func greeting() -> String\n"
        '    example () -> "HI,WORLD"\n'
        '    let updated: String = string_replace(text: "hi,geno", old: "geno", new: "world")\n'
        "    return string_to_upper(updated)\n"
        "end func greeting\n"
    )


class _ClosedTempGenoFile:
    """Minimal temp-file wrapper that never keeps the file open in test bodies."""

    def __init__(self) -> None:
        fd, self.name = tempfile.mkstemp(suffix=".geno")
        os.close(fd)

    def __enter__(self) -> "_ClosedTempGenoFile":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False

    def write(self, text: str) -> None:
        with open(self.name, "a", encoding="utf-8") as handle:
            handle.write(text)

    def flush(self) -> None:
        return None


def _closed_temp_geno() -> _ClosedTempGenoFile:
    return _ClosedTempGenoFile()


def _run_selfhost_cli(*args: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / "selfhost" / "Main.geno"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--unsafe",
            "--no-check-examples",
            "--cap",
            "env,fs,print",
            str(target),
            "--",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestCliParserDispatch:
    def test_build_parser_parses_run_command_without_dispatch(self):
        import geno.__main__ as cli_main

        parser = cli_main.build_parser()
        args, extra = parser.parse_known_args(
            ["run", "--no-check-examples", "App.geno", "alpha"]
        )

        assert args.command == "run"
        assert args.file == "App.geno"
        assert args.no_check_examples is True
        assert extra == ["alpha"]

    def test_dispatch_args_routes_run_command(self, monkeypatch):
        import geno.__main__ as cli_main

        calls = []
        parser = cli_main.build_parser()
        args, extra = parser.parse_known_args(["run", "App.geno", "alpha", "beta"])

        def fake_run_file(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr(cli_main, "run_file", fake_run_file)

        cli_main.dispatch_args(parser, args, extra)

        assert calls
        assert calls[0][0] == ("App.geno",)
        assert calls[0][1]["program_args"] == ["alpha", "beta"]


class TestGenoRun:
    """Test the 'geno run' command."""

    def test_run_simple_program(self):
        """Running a simple program should work."""
        with _closed_temp_geno() as f:
            f.write("""
func add(x: Int, y: Int) -> Int
    example 1, 2 -> 3
    return x + y
end func add

func main() -> Unit
    let result: Int = add(2, 3)
    print(result)
    return ()
end func main
""")
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "run", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode == 0
                assert "5" in result.stdout
                assert "--max-steps is ignored" not in result.stderr
            finally:
                os.unlink(f.name)

    def test_run_compiled_async_main_awaits_result(self):
        """Compiled default run mode awaits async main in the process sandbox."""
        with _closed_temp_geno() as f:
            f.write("""
async func fetch() -> Int
    return 42
end func

async func main() -> Int
    return await fetch()
end func
""")
            f.flush()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "geno",
                        "run",
                        "--no-check-examples",
                        f.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode == 0, result.stderr
                assert "=> 42" in result.stdout
                assert "coroutine" not in result.stdout
                assert "coroutine" not in result.stderr
            finally:
                os.unlink(f.name)

    def test_run_with_syntax_error(self):
        """Running a program with syntax error should fail."""
        with _closed_temp_geno() as f:
            f.write("""
func broken -> Int
    return 1
end func
""")
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "run", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
            finally:
                os.unlink(f.name)

    def test_run_nonexistent_file(self):
        """Running a nonexistent file should fail."""
        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", "/nonexistent/file.geno"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0

    def test_run_resolves_local_project_imports(self, tmp_path):
        app = tmp_path / "App.geno"
        app.write_text(
            "import Utils\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "    return triple(14)\n"
            "end func\n"
        )
        (tmp_path / "Utils.geno").write_text(
            "func triple(x: Int) -> Int\n"
            "    example 2 -> 6\n"
            "    return x * 3\n"
            "end func\n"
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", str(app)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "42" in result.stdout

    def test_run_resolves_local_imports_for_legacy_direct_file_extensions(
        self, tmp_path
    ):
        app = tmp_path / "App.gen"
        app.write_text(
            "import Utils\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "    return triple(14)\n"
            "end func\n"
        )
        (tmp_path / "Utils.geno").write_text(
            "func triple(x: Int) -> Int\n"
            "    example 2 -> 6\n"
            "    return x * 3\n"
            "end func\n"
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", str(app)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "42" in result.stdout

    def test_run_name_collision_reports_consistent_error(self, tmp_path):
        write_dependency_collision_fixture(tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "collision" in result.stderr.lower()
        assert "Module name collision" in result.stderr
        assert "Utils" in result.stderr

    def test_run_circular_import_reports_consistent_error(self, tmp_path):
        _write_circular_project(tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "Circular Import:" in result.stderr
        assert "Circular import detected" in result.stderr

    def test_run_selfhost_main_smoke(self):
        """The selfhost entrypoint should run successfully."""
        result = _run_selfhost_cli("demo")

        assert result.returncode == 0
        assert result.stderr == ""
        assert "=== Geno Self-Hosted Frontend + Interpreter ===" in result.stdout

    def test_run_selfhost_check_project_directory(self, tmp_path):
        _write_selfhost_cli_project(tmp_path)

        result = _run_selfhost_cli("check", str(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""
        assert f"{tmp_path}: ok" in result.stdout

    def test_run_selfhost_check_rejects_unterminated_block_comment(self, tmp_path):
        main_path = tmp_path / "Main.geno"
        main_path.write_text(
            "/* unterminated\nfunc main() -> Int\n    return 1\nend func main\n",
            encoding="utf-8",
        )

        result = _run_selfhost_cli("check", str(main_path))

        assert result.stderr == ""
        assert "Unterminated block comment" in result.stdout
        assert f"{main_path}: ok" not in result.stdout

    def test_run_selfhost_check_rejects_lambda_without_arrow(self, tmp_path):
        main_path = tmp_path / "Main.geno"
        main_path.write_text(
            "func main() -> Int\n"
            "    let f: (Int) -> Int = fn(x: Int) x + 1\n"
            "    return f(1)\n"
            "end func main\n",
            encoding="utf-8",
        )

        result = _run_selfhost_cli("check", str(main_path))

        assert result.stderr == ""
        assert "Expected '->' after lambda parameter list" in result.stdout
        assert f"{main_path}: ok" not in result.stdout

    def test_run_selfhost_check_rejects_mismatched_function_end_name(self, tmp_path):
        main_path = tmp_path / "Main.geno"
        main_path.write_text(
            "func foo() -> Int\n    example () -> 1\n    return 1\nend func bar\n",
            encoding="utf-8",
        )

        result = _run_selfhost_cli("check", str(main_path))

        assert result.stderr == ""
        assert "Function closing name 'bar' doesn't match 'foo'" in result.stdout
        assert f"{main_path}: ok" not in result.stdout

    def test_run_selfhost_check_accepts_untested_without_examples(self, tmp_path):
        main_path = tmp_path / "Main.geno"
        main_path.write_text(
            '@untested("skip")\nfunc helper() -> Int\n    return 1\nend func helper\n',
            encoding="utf-8",
        )

        result = _run_selfhost_cli("check", str(main_path))

        assert result.returncode == 0
        assert result.stderr == ""
        assert "must have at least one example clause" not in result.stdout
        assert f"{main_path}: ok" in result.stdout

    def test_run_selfhost_run_project_directory(self, tmp_path):
        _write_selfhost_cli_project(tmp_path)

        result = _run_selfhost_cli("run", str(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""
        assert "42" in result.stdout

    def test_run_selfhost_test_project_directory(self, tmp_path):
        _write_selfhost_cli_project(tmp_path)

        result = _run_selfhost_cli("test", str(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""
        assert f"{tmp_path}: ok" in result.stdout
        assert all(line.strip() != "42" for line in result.stdout.splitlines())

    def test_run_selfhost_project_with_namespaced_builtins(self, tmp_path):
        _write_selfhost_namespaced_builtin_project(tmp_path)

        result = _run_selfhost_cli("run", str(tmp_path))

        assert result.returncode == 0
        assert result.stderr == ""
        assert "3" in result.stdout
        assert "HI,WORLD" in result.stdout

    def test_run_selfhost_float_numeric_operations(self, tmp_path):
        (tmp_path / "Main.geno").write_text(
            '@untested("entry point")\n'
            "func main() -> Unit\n"
            "    print(to_string(1.5 + 2.5))\n"
            "    print(to_string(1 + 2.5))\n"
            "    print(to_string(5.5 % 2.0))\n"
            "    print(to_string(-1.5))\n"
            "    print(to_string(1.5 < 2.0))\n"
            "    print(to_string(2.0 >= 2))\n"
            "    return ()\n"
            "end func main\n",
            encoding="utf-8",
        )

        result = _run_selfhost_cli("run", str(tmp_path))

        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stderr == ""
        stdout_lines = [line.strip() for line in result.stdout.splitlines()]
        assert "4.0" in stdout_lines
        assert "3.5" in stdout_lines
        assert "1.5" in stdout_lines
        assert "-1.5" in stdout_lines
        assert stdout_lines.count("true") == 2

    def test_run_deeply_nested_chain_reports_clean_error(self, tmp_path):
        """H-08: non-JSON `geno run` must use the same clean deep-nesting
        boundary as check/compile, not a generic runtime error or traceback."""
        terms = " + ".join(["1"] * 400)
        source = tmp_path / "Main.geno"
        source.write_text(f"func main() -> Int\n  return {terms}\nend func\n")
        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", str(source)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        assert "Traceback (most recent call last)" not in result.stderr
        assert "nesting is too deep" in result.stderr


class TestGenoRunCap:
    """Test explicit capability run modes and capability enforcement."""

    def test_compiled_mode_example_preflight_denies_nondefault_capability(self):
        """Default compiled run must not evaluate examples with ungated env access."""
        with _closed_temp_geno() as f:
            f.write("""
func probe() -> String
    example () -> env_get_or(name: "GENO_CODEX_SECRET_PROBE", default: "missing")
    return "not-the-secret"
end func probe

func main() -> Int
    return 0
end func main
""")
            f.flush()
            env = os.environ.copy()
            env["GENO_CODEX_SECRET_PROBE"] = "secret-from-example"
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "run", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=env,
                )
                combined = result.stdout + result.stderr
                assert result.returncode != 0
                assert "Capability denied" in combined
                assert "env_get_or" in combined
                assert "secret-from-example" not in combined
            finally:
                os.unlink(f.name)

    def test_cap_without_explicit_mode_fails_fast(self):
        """--cap must not silently downgrade process isolation."""
        with _closed_temp_geno() as f:
            f.write("""
func main() -> Unit
    print("hello from cap test")
    return ()
end func main
""")
            f.flush()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "geno",
                        "run",
                        "--no-check-examples",
                        "--cap",
                        "print",
                        f.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                assert result.stdout == ""
                assert "--cap is not supported" in result.stderr
                assert "--unsafe --cap" in result.stderr
            finally:
                os.unlink(f.name)

    def test_unsafe_cap_enforces_capabilities(self):
        """Explicit --unsafe keeps capability-gated interpreter behavior."""
        with _closed_temp_geno() as f:
            f.write("""
func main() -> Unit
    print("hello from cap test")
    return ()
end func main
""")
            f.flush()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "geno",
                        "run",
                        "--no-check-examples",
                        "--unsafe",
                        "--cap",
                        "print",
                        f.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode == 0
                assert "hello from cap test" in result.stdout
                assert result.stderr == ""
            finally:
                os.unlink(f.name)

    def test_cap_denies_ungrated_capability(self):
        """--cap should deny builtins not in the granted set."""
        with _closed_temp_geno() as f:
            # Grant only 'print', but try to use fs_read_text (requires 'fs')
            f.write("""
@untested("tests capability denial")
func main() -> Unit
    let content: String = fs_read_text("nope.txt")
    print(content)
    return ()
end func main
""")
            f.flush()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "geno",
                        "run",
                        "--no-check-examples",
                        "--unsafe",
                        "--cap",
                        "print",
                        f.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                assert (
                    "Capability denied" in result.stderr
                    or "Capability denied" in result.stdout
                )
            finally:
                os.unlink(f.name)

    def test_unsafe_cap_preserves_cli_args_without_double_dash(self):
        """Explicit interpreter capability mode should honor parsed program args."""
        with _closed_temp_geno() as f:
            f.write("""
@untested("entry point")
func main() -> Unit
    print(join(cli_args(), ","))
    return ()
end func main
""")
            f.flush()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "geno",
                        "run",
                        "--no-check-examples",
                        "--unsafe",
                        "--cap",
                        "env,print",
                        f.name,
                        "alpha",
                        "beta",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode == 0
                assert "alpha,beta" in result.stdout
                assert result.stderr == ""
            finally:
                os.unlink(f.name)


class TestGenoRunResourceLimits:
    def test_run_resource_limit_flags_forwarded(self, tmp_path, monkeypatch):
        import geno.__main__ as cli_main
        from geno.__main__ import main

        app = tmp_path / "App.geno"
        app.write_text("func main() -> Int\n    return 1\nend func\n")
        calls = []

        def fake_run_file(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr(cli_main, "run_file", fake_run_file)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "geno",
                "run",
                "--max-recursion-depth",
                "77",
                "--max-output-length",
                "88",
                "--max-collection-size",
                "99",
                "--max-integer-bits",
                "111",
                "--max-memory-bytes",
                "0",
                "--max-cpu-time",
                "1.5",
                "--max-file-size-bytes",
                "222",
                "--max-processes",
                "3",
                str(app),
            ],
        )

        main()

        assert calls
        kwargs = calls[0][1]
        assert kwargs["max_recursion_depth"] == 77
        assert kwargs["max_output_length"] == 88
        assert kwargs["max_collection_size"] == 99
        assert kwargs["max_integer_bits"] == 111
        assert kwargs["max_memory_bytes"] is None
        assert kwargs["max_cpu_time"] == 1.5
        assert kwargs["max_file_size_bytes"] == 222
        assert kwargs["max_processes"] == 3

    def test_run_phase_helpers_are_testable(self, tmp_path):
        import json

        from geno.api import RunResult, Timing
        from geno.cli.run import (
            _format_json_run_output,
            _resolve_run_program,
            _select_run_mode,
        )

        app = tmp_path / "App.geno"
        app.write_text("func main() -> Int\n    return 7\nend func\n")

        resolved = _resolve_run_program(str(app), target="python-cli")
        output = _format_json_run_output(
            RunResult(
                ok=True,
                value=7,
                output="",
                timing=Timing(total_ms=1.234, lex_ms=0.1),
                steps_used=9,
            )
        )

        assert _select_run_mode(unsafe=False, json_output=False) == "process"
        assert _select_run_mode(unsafe=True, json_output=False) == "unsafe"
        assert _select_run_mode(unsafe=False, json_output=True) == "json"
        assert resolved.check_targets == ["python-cli"]
        assert json.loads(output)["timing"]["total_ms"] == 1.23
        assert json.loads(output)["steps_used"] == 9

    def test_process_only_limit_flags_rejected_outside_process_mode(self):
        with _closed_temp_geno() as f:
            f.write("func main() -> Int\n    return 1\nend func\n")
            f.flush()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "geno",
                        "run",
                        "--unsafe",
                        "--max-processes",
                        "2",
                        f.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                assert "--max-processes" in result.stderr
                assert "process-isolated" in result.stderr
            finally:
                os.unlink(f.name)

    def test_default_mode_does_not_resolve_source_in_parent(
        self, tmp_path, monkeypatch, capsys
    ):
        import geno.cli.run as run_command

        app = tmp_path / "App.geno"
        app.write_text("func main() -> Int\n    return 7\nend func\n")

        def fail_if_parent_resolves(*_args, **_kwargs):
            raise AssertionError("parent attempted to resolve untrusted source")

        monkeypatch.setattr(
            run_command,
            "_resolve_run_program",
            fail_if_parent_resolves,
        )

        run_command.run_file(str(app), check_examples=False)

        assert "=> 7" in capsys.readouterr().out

    def test_timeout_covers_frontend_startup(self, tmp_path):
        app = tmp_path / "App.geno"
        app.write_text("func main() -> Int\n    return 7\nend func\n")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "run",
                "--no-check-examples",
                "--timeout",
                "0.001",
                str(app),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "Limit Error:" in result.stderr
        assert "timed out" in result.stderr
        assert "Traceback (most recent call last)" not in result.stderr

    def test_frontend_diagnostics_do_not_leak_worker_tracebacks(self, tmp_path):
        app = tmp_path / "Broken.geno"
        app.write_text("func broken -> Int\n    return 1\nend func\n")

        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", str(app)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "Parse" in result.stderr
        assert "Traceback (most recent call last)" not in result.stderr


class TestGenoRunJson:
    """Test the 'geno run --json' command."""

    def test_run_json_output(self):
        """Running with --json should produce valid JSON output."""
        import json

        with _closed_temp_geno() as f:
            f.write("""
func main() -> Int
    return 42
end func main
""")
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "run", "--json", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode == 0
                data = json.loads(result.stdout)
                assert data["ok"] is True
                assert data["value"] == 42
                assert "timing" in data
                assert "diagnostics" in data
            finally:
                os.unlink(f.name)

    def test_run_json_error(self):
        """Running a broken program with --json should produce JSON with ok=false."""
        import json

        with _closed_temp_geno() as f:
            f.write("""
func main() -> Int
    return "not an int"
end func main
""")
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "run", "--json", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                data = json.loads(result.stdout)
                assert data["ok"] is False
                assert len(data["diagnostics"]) > 0
            finally:
                os.unlink(f.name)

    def test_run_json_honors_collection_limit_flag(self):
        import json

        with _closed_temp_geno() as f:
            f.write("""
func main() -> List[Int]
    let xs: List[Int] = range(0, 256)
    return concat(xs, xs)
end func main
""")
            f.flush()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "geno",
                        "run",
                        "--json",
                        "--max-collection-size",
                        "300",
                        f.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                data = json.loads(result.stdout)
                assert data["ok"] is False
                assert "size exceeds limit" in data["diagnostics"][0]["message"]
            finally:
                os.unlink(f.name)

    def test_run_json_missing_file_reports_clear_resolution_error(self):
        import json

        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", "--json", "/nonexistent/path.geno"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert result.stderr == ""
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert data["diagnostics"][0]["code"] == "E204"
        assert (
            data["diagnostics"][0]["message"]
            == "File not found: /nonexistent/path.geno"
        )

    @pytest.mark.parametrize(
        ("flag", "value", "message"),
        [
            ("--timeout", "-1", "RunConfig.timeout"),
            ("--max-steps", "0", "RunConfig.max_steps"),
        ],
    )
    def test_run_json_invalid_limits_report_structured_error(
        self, flag, value, message
    ):
        import json

        with _closed_temp_geno() as f:
            f.write("func main() -> Int\n    return 1\nend func\n")
            f.flush()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "geno",
                        "run",
                        "--json",
                        flag,
                        value,
                        f.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            finally:
                os.unlink(f.name)

        assert result.returncode != 0
        assert result.stderr == ""
        assert "Traceback" not in result.stdout
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert message in data["diagnostics"][0]["message"]

    def test_run_json_preserves_cli_args_without_double_dash(self):
        """JSON mode should honor parsed program args without an explicit '--'."""
        import json

        with _closed_temp_geno() as f:
            f.write("""
@untested("entry point")
func main() -> List[String]
    return cli_args()
end func main
""")
            f.flush()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "geno",
                        "run",
                        "--json",
                        "--cap",
                        "env",
                        f.name,
                        "alpha",
                        "beta",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode == 0
                data = json.loads(result.stdout)
                assert data["ok"] is True
                assert data["value"] == ["alpha", "beta"]
            finally:
                os.unlink(f.name)

    def test_run_json_resolves_local_project_imports(self, tmp_path):
        import json

        app = tmp_path / "App.geno"
        app.write_text(
            "import Utils\nfunc main() -> Int\n    return triple(14)\nend func\n"
        )
        (tmp_path / "Utils.geno").write_text(
            "func triple(x: Int) -> Int\n"
            "    example 2 -> 6\n"
            "    return x * 3\n"
            "end func\n"
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", "--json", str(app)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["ok"] is True
        assert data["value"] == 42

    def test_run_json_imported_type_error_uses_real_module_path(self, tmp_path):
        import json

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Utils"]\n'
        )
        utils = tmp_path / "Utils.geno"
        utils.write_text(
            '@untested("consistency fixture")\n'
            "func helper(x: Int) -> Int\n"
            '  return "oops"\n'
            "end func\n"
        )
        app = tmp_path / "App.geno"
        app.write_text(
            "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", "--json", str(app)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert result.stderr == ""
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert data["diagnostics"][0]["location"]["filename"] == str(utils.resolve())


class TestGenoCheck:
    """Test the 'geno check' command."""

    def test_check_valid_program(self):
        """Checking a valid program should succeed."""
        with _closed_temp_geno() as f:
            f.write("""
func factorial(n: Int) -> Int
    requires n >= 0
    example 5 -> 120
    example 0 -> 1

    if n <= 1 then
        return 1
    else
        return n * factorial(n - 1)
    end if
end func factorial
""")
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "check", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode == 0
            finally:
                os.unlink(f.name)

    def test_check_type_error(self):
        """Checking a program with type error should fail."""
        with _closed_temp_geno() as f:
            f.write("""
func bad(x: Int) -> String
    example 1 -> "hello"
    return x + 1
end func bad
""")
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "check", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                assert (
                    "error" in result.stderr.lower() or "error" in result.stdout.lower()
                )
            finally:
                os.unlink(f.name)

    def test_check_deeply_nested_chain_reports_clean_error(self, tmp_path):
        """H-08: a valid but extremely deeply-nested expression must produce a
        clean error, not a raw Python RecursionError traceback."""
        terms = " + ".join(["1"] * 400)
        source = tmp_path / "Main.geno"
        source.write_text(f"func main() -> Int\n  return {terms}\nend func\n")
        result = subprocess.run(
            [sys.executable, "-m", "geno", "check", str(source)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        # Clean, actionable message — never a raw traceback.
        assert "Traceback (most recent call last)" not in result.stderr
        assert "nesting is too deep" in result.stderr

    def test_compile_deeply_nested_chain_reports_clean_error(self, tmp_path):
        """H-08: `geno compile` must also report deep nesting cleanly (the
        boundary handler is shared with check), not dump a raw traceback."""
        terms = " + ".join(["1"] * 400)
        source = tmp_path / "Main.geno"
        source.write_text(f"func main() -> Int\n  return {terms}\nend func\n")
        result = subprocess.run(
            [sys.executable, "-m", "geno", "compile", str(source)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        assert "Traceback (most recent call last)" not in result.stderr
        assert "nesting is too deep" in result.stderr

    def test_check_accepts_documented_else_if_chain(self, tmp_path):
        source = tmp_path / "Main.geno"
        source.write_text(
            "func sign(x: Int) -> String\n"
            '    example 2 -> "positive"\n'
            '    example 0 -> "zero"\n'
            "    if x > 0 then\n"
            '        return "positive"\n'
            "    else if x == 0 then\n"
            '        return "zero"\n'
            "    else\n"
            '        return "negative"\n'
            "    end if\n"
            "end func sign\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "check", str(source)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0

    def test_check_resolves_local_project_imports(self, tmp_path):
        app = tmp_path / "App.geno"
        app.write_text(
            "import Types\nfunc main() -> Wrapper\n    return Wrapper(42)\nend func\n"
        )
        (tmp_path / "Types.geno").write_text("type Wrapper = Wrapper(value: Int)\n")

        result = subprocess.run(
            [sys.executable, "-m", "geno", "check", str(app)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0


class TestGenoConstrain:
    """Test the 'geno constrain' command."""

    def test_constrain_validate_valid_prefix(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "constrain",
                "--validate",
                "func add(x: Int, y: Int) ->",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert result.stdout.strip() == "valid"

    def test_constrain_json_reads_from_stdin(self):
        import json

        result = subprocess.run(
            [sys.executable, "-m", "geno", "constrain", "--json"],
            input="func add(",
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["valid"] is True
        assert "func" in data["unclosed_blocks"]
        assert data["allowed_next"]["allow_identifier"] is True

    def test_constrain_invalid_prefix_returns_nonzero_json(self):
        import json

        result = subprocess.run(
            [sys.executable, "-m", "geno", "constrain", "--json", "end"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        data = json.loads(result.stdout)
        assert data["valid"] is False
        assert data["error"] is not None


class TestErrorSourceDisplay:
    """Error messages should include the source line and caret."""

    def test_type_error_shows_source_line(self):
        with _closed_temp_geno() as f:
            f.write('func main() -> Int\n    return "hello"\nend func\n')
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "check", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                assert 'return "hello"' in result.stderr
                assert "^" in result.stderr
            finally:
                os.unlink(f.name)

    def test_parse_error_shows_source_line(self):
        with _closed_temp_geno() as f:
            f.write("func main() -> Int\n    let = 5\nend func\n")
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "run", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                assert "let = 5" in result.stderr
                assert "^" in result.stderr
            finally:
                os.unlink(f.name)


class TestDidYouMean:
    """Error messages should include 'Did you mean?' suggestions."""

    def test_variable_typo_suggestion(self):
        with _closed_temp_geno() as f:
            f.write(
                "func main() -> Int\n    let count: Int = 10\n    return cont\nend func\n"
            )
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "check", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                assert "Did you mean 'count'?" in result.stderr
            finally:
                os.unlink(f.name)

    def test_constructor_typo_suggestion(self):
        with _closed_temp_geno() as f:
            f.write("func main() -> Option[Int]\n    return Sme(42)\nend func\n")
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "check", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                assert "Did you mean 'Some'?" in result.stderr
            finally:
                os.unlink(f.name)

    def test_no_suggestion_when_no_match(self):
        with _closed_temp_geno() as f:
            f.write("func main() -> Int\n    return xyzzy\nend func\n")
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "check", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                assert "Did you mean" not in result.stderr
            finally:
                os.unlink(f.name)


class TestRuntimeStackTrace:
    """Runtime errors should include a stack trace."""

    def test_stack_trace_shows_call_chain(self):
        src = (
            '@untested("t")\nfunc inner(x: Int) -> Int\n    return 10 / x\nend func\n'
            '@untested("t")\nfunc outer(x: Int) -> Int\n    return inner(x)\nend func\n'
            "func main() -> Int\n    return outer(0)\nend func\n"
        )
        with _closed_temp_geno() as f:
            f.write(src)
            f.flush()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "run", "--unsafe", f.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode != 0
                assert "Stack trace" in result.stderr
                assert "in main" in result.stderr
                assert "in outer" in result.stderr
                assert "in inner" in result.stderr
            finally:
                os.unlink(f.name)


class TestGenoCompile:
    """Test the 'geno compile' command."""

    def test_compile_to_python(self):
        """Compiling to Python should produce valid Python code."""
        with _closed_temp_geno() as f:
            f.write("""
func double(x: Int) -> Int
    example 5 -> 10
    return x * 2
end func double
""")
            f.flush()
            outfile = f.name.replace(".geno", ".py")
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "compile", f.name, "-o", outfile],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode == 0
                assert os.path.exists(outfile)

                # Verify the output is valid Python
                with open(outfile) as out:
                    python_code = out.read()
                    # Should compile without errors
                    compile(python_code, outfile, "exec")
            finally:
                os.unlink(f.name)
                if os.path.exists(outfile):
                    os.unlink(outfile)

    def test_compile_name_collision_reports_consistent_error(self, tmp_path):
        write_dependency_collision_fixture(tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "geno", "compile", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "collision" in result.stderr.lower()
        assert "Module name collision" in result.stderr
        assert "Utils" in result.stderr

    def test_compile_explicit_js_esm_uses_requested_file_exports(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Good"]\n',
            encoding="utf-8",
        )
        (tmp_path / "App.geno").write_text(
            "func app_value() -> Int\n  example () -> 1\n  return 1\nend func\n",
            encoding="utf-8",
        )
        good_file = tmp_path / "Good.geno"
        good_file.write_text(
            "func good_value() -> Int\n  example () -> 7\n  return 7\nend func\n",
            encoding="utf-8",
        )
        outfile = tmp_path / "good.js"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(good_file),
                "--target",
                "js",
                "--esm",
                "-o",
                str(outfile),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0, result.stderr
        js_code = outfile.read_text(encoding="utf-8")
        dts = outfile.with_suffix(".d.ts").read_text(encoding="utf-8")
        assert "export { good_value }" in js_code
        assert "app_value" not in js_code
        assert "good_value" in dts
        assert "app_value" not in dts

    def test_compile_selfhost_main_python_runs(self, tmp_path):
        """Compiled selfhost Python should execute successfully."""
        repo_root = Path(__file__).resolve().parents[2]
        target = repo_root / "selfhost" / "Main.geno"
        outfile = tmp_path / "selfhost_main.py"

        compile_result = subprocess.run(
            [sys.executable, "-m", "geno", "compile", str(target), "-o", str(outfile)],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert compile_result.returncode == 0
        assert outfile.exists()

        run_result = subprocess.run(
            [sys.executable, str(outfile), "--cap", "env,print", "--", "demo"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert run_result.returncode == 0
        assert "=== Geno Self-Hosted Frontend + Interpreter ===" in run_result.stdout
        assert "=== Pipeline complete ===" in run_result.stdout

    def test_compile_geno_mark_python_escapes_html_attributes(self, tmp_path):
        """Compiled geno-mark Python should escape quotes in HTML attributes."""
        repo_root = Path(__file__).resolve().parents[2]
        app_dir = repo_root / "examples" / "apps" / "geno-mark"
        if not (app_dir / "Main.geno").exists():
            pytest.skip("geno-mark example not found")

        input_md = tmp_path / "input.md"
        input_md.write_text('# bad"id\n\n[go](a"b)\n', encoding="utf-8", newline="\r\n")
        outfile = tmp_path / "geno_mark.py"

        compile_result = subprocess.run(
            [sys.executable, "-m", "geno", "compile", str(app_dir), "-o", str(outfile)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert compile_result.returncode == 0, compile_result.stderr

        run_result = subprocess.run(
            [
                sys.executable,
                str(outfile),
                "--cap",
                "env,print,fs",
                "--",
                str(input_md),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run_result.returncode == 0, run_result.stderr
        assert '<a href="#bad&quot;id">bad"id</a>' in run_result.stdout
        assert '<h1 id="bad&quot;id">bad"id</h1>' in run_result.stdout
        assert '<a href="a&quot;b">go</a>' in run_result.stdout

    def test_compile_geno_mark_js_escapes_html_attributes(self, tmp_path):
        """Compiled geno-mark JS should escape quotes in HTML attributes."""
        repo_root = Path(__file__).resolve().parents[2]
        app_dir = repo_root / "examples" / "apps" / "geno-mark"
        if not (app_dir / "Main.geno").exists():
            pytest.skip("geno-mark example not found")

        input_md = tmp_path / "input.md"
        input_md.write_text('# bad"id\n\n[go](a"b)\n', encoding="utf-8", newline="\r\n")
        outfile = tmp_path / "geno_mark.js"

        compile_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(app_dir),
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
            [
                "node",
                str(outfile),
                "--cap",
                "env,print,fs",
                "--",
                str(input_md),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert run_result.returncode == 0, run_result.stderr
        assert '<a href="#bad&quot;id">bad"id</a>' in run_result.stdout
        assert '<h1 id="bad&quot;id">bad"id</h1>' in run_result.stdout
        assert '<a href="a&quot;b">go</a>' in run_result.stdout


class TestGenoBuild:
    """Test the 'geno build' command."""

    def test_build_name_collision_reports_consistent_error(self, tmp_path):
        write_dependency_collision_fixture(tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "geno", "build", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "collision" in result.stderr.lower()
        assert "Module name collision" in result.stderr
        assert "Utils" in result.stderr

    def test_build_malformed_manifest_reports_clean_error(self, tmp_path):
        """M-07: `geno build` on a malformed geno.toml must not dump a raw
        TOMLDecodeError traceback."""
        (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = [\n')
        (tmp_path / "Main.geno").write_text(
            "func main() -> Int\n  return 0\nend func\n"
        )
        result = subprocess.run(
            [sys.executable, "-m", "geno", "build", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 1
        assert "Traceback (most recent call last)" not in result.stderr
        assert "Error" in result.stderr

    def test_build_deeply_nested_chain_reports_clean_error(self, tmp_path):
        """H-08: `geno build` also walks/type-checks ASTs and should report
        pathological nesting cleanly."""
        terms = " + ".join(["1"] * 400)
        source = tmp_path / "Main.geno"
        source.write_text(f"func main() -> Int\n  return {terms}\nend func\n")
        result = subprocess.run(
            [sys.executable, "-m", "geno", "build", str(source), "--single-file"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        assert "Traceback (most recent call last)" not in result.stderr
        assert "nesting is too deep" in result.stderr


class TestGenoTestJson:
    """Test the 'geno test --json' command."""

    def test_test_json_project_name_collision_returns_structured_error(self, tmp_path):
        import json

        write_dependency_collision_fixture(tmp_path)

        result = subprocess.run(
            [sys.executable, "-m", "geno", "test", "--json", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert result.stderr == ""
        data = json.loads(result.stdout)
        assert data["success"] is False
        assert data["errors"] == 1
        assert data["files"][0]["path"] == str(tmp_path)
        assert "Project Error:" in data["files"][0]["error"]
        assert "Module name collision" in data["files"][0]["error"]

    def test_test_json_missing_target_returns_structured_error(self):
        import json

        result = subprocess.run(
            [sys.executable, "-m", "geno", "test", "--json", "/nonexistent/path.geno"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert result.stderr == ""
        data = json.loads(result.stdout)
        assert data["success"] is False
        assert data["errors"] == 1
        assert data["files"][0]["path"] == "/nonexistent/path.geno"
        assert "not found" in data["files"][0]["error"]

    def test_test_json_accepts_legacy_gen_direct_file(self, tmp_path):
        import json

        app = tmp_path / "App.gen"
        app.write_text(
            "func double(x: Int) -> Int\n  example 2 -> 4\n  return x * 2\nend func\n"
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "test", "--json", str(app)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["success"] is True
        assert data["passed"] == 1

    def test_test_json_manifest_file_targets_requested_file(self, tmp_path):
        import json

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Helper", "Scratch"]\n'
        )
        (tmp_path / "App.geno").write_text(
            '@untested("bad entry")\nfunc main() -> Int\n  return "oops"\nend func\n'
        )
        (tmp_path / "Helper.geno").write_text(
            "func bump(x: Int) -> Int\n  example 1 -> 2\n  return x + 1\nend func\n"
        )
        scratch = tmp_path / "Scratch.geno"
        scratch.write_text(
            "import Helper\n"
            "func scratch() -> Int\n"
            "  example () -> 2\n"
            "  return bump(1)\n"
            "end func\n"
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "test", "--json", str(scratch)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["success"] is True
        assert data["passed"] == 2
        assert {Path(item["path"]).name for item in data["files"]} == {
            "Helper.geno",
            "Scratch.geno",
        }

    def test_test_json_max_steps_bounds_test_blocks(self, tmp_path):
        import json

        test_file = tmp_path / "loop.geno"
        test_file.write_text(
            "func spin() -> Int\n"
            "    var i = 0\n"
            "    while true do\n"
            "        i = i + 1\n"
            "    end while\n"
            "    return i\n"
            "end func spin\n\n"
            'test "loop is bounded"\n'
            "    assert spin() == 0\n"
            "end test\n"
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "test",
                "--json",
                "--max-steps",
                "20",
                str(test_file),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        data = json.loads(result.stdout)
        assert data["failed"] == 1
        assert "Step limit exceeded" in data["files"][0]["violations"][0]["message"]


class TestGenoHelp:
    """Test help and version commands."""

    def test_run_help_shows_default_step_budget(self):
        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert f"default: {DEFAULT_INTERPRETER_MAX_STEPS}" in result.stdout
        assert "default: unlimited" not in result.stdout

    def test_help(self):
        """--help should show usage information."""
        result = subprocess.run(
            [sys.executable, "-m", "geno", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "usage" in result.stdout.lower() or "geno" in result.stdout.lower()

    def test_test_help_clarifies_target_execution_model(self):
        """`geno test --target` should not look like a backend selector."""
        result = subprocess.run(
            [sys.executable, "-m", "geno", "test", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "Typecheck availability" in result.stdout
        assert "interpreter" in result.stdout
        assert "--timeout" in result.stdout
        assert "--max-steps" in result.stdout

    def test_run_help_lists_resource_limit_flags(self):
        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "--max-collection-size" in result.stdout
        assert "--max-integer-bits" in result.stdout
        assert "--max-memory-bytes" in result.stdout
        assert "--max-cpu-time" in result.stdout
        assert "--max-file-size-bytes" in result.stdout
        assert "--max-processes" in result.stdout

    def test_version(self):
        """--version should print the version string."""
        result = subprocess.run(
            [sys.executable, "-m", "geno", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "geno" in result.stdout.lower()
        # Version should be present in output
        from geno import __version__

        assert __version__ in result.stdout

    def test_no_args_shows_repl_or_help(self):
        """Running without args should start REPL or show help."""
        # Just verify it doesn't crash - REPL would hang so we timeout quickly
        result = subprocess.run(
            [sys.executable, "-m", "geno", "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Should at least not crash
        assert result.returncode == 0


class TestGenoInit:
    """Test the 'geno init' command."""

    def test_init_rejects_hidden_legacy_app_template(self, tmp_path):
        repo_root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "init",
                str(tmp_path / "demo"),
                "--template",
                "app",
            ],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=10,
        )

        assert result.returncode != 0
        assert "invalid choice" in result.stderr
        assert "app" in result.stderr


class TestReferenceApps:
    """Smoke tests for release-gated reference apps."""

    def test_node_cli_reference_app_matches_golden_output(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node.js not available")
        assert node is not None

        repo_root = Path(__file__).resolve().parents[2]
        app_dir = repo_root / "examples" / "apps" / "geno-log"
        expected = (app_dir / "expected.out").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "geno-log.js"
            compile_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "geno",
                    "compile",
                    str(app_dir),
                    "--target",
                    "js",
                    "-o",
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                cwd=repo_root,
                timeout=15,
            )
            assert compile_result.returncode == 0, compile_result.stderr

            node_result = subprocess.run(
                [node, str(out_path)],
                capture_output=True,
                text=True,
                cwd=repo_root,
                timeout=15,
            )
            assert node_result.returncode == 0, node_result.stderr
            assert node_result.stdout == expected


class TestPythonVersionCheck:
    """Test that the CLI rejects unsupported Python versions."""

    def test_check_rejects_old_python(self, monkeypatch):
        from geno.__main__ import _check_python_version

        monkeypatch.setattr(sys, "version_info", (3, 8, 0, "final", 0))
        with pytest.raises(SystemExit):
            _check_python_version()

    def test_check_rejects_too_new_python(self, monkeypatch):
        from geno.__main__ import _check_python_version

        monkeypatch.setattr(sys, "version_info", (3, 14, 0, "final", 0))
        with pytest.raises(SystemExit):
            _check_python_version()

    def test_check_accepts_supported_python(self, monkeypatch):
        from geno.__main__ import _check_python_version

        for minor in (10, 11, 12, 13):
            monkeypatch.setattr(sys, "version_info", (3, minor, 0, "final", 0))
            _check_python_version()  # should not raise

    def test_run_json_unsupported_python_returns_structured_error(
        self, monkeypatch, capsys
    ):
        import json

        from geno.__main__ import main

        monkeypatch.setattr(sys, "version_info", (3, 14, 0, "final", 0))
        monkeypatch.setattr(
            sys,
            "argv",
            ["geno", "run", "--json", "example.geno"],
        )

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert captured.err == ""
        data = json.loads(captured.out)
        assert data["ok"] is False
        assert data["diagnostics"][0]["message"].startswith(
            "Geno requires Python 3.10-3.13"
        )

    def test_help_still_works_on_unsupported_python(self, monkeypatch, capsys):
        from geno.__main__ import main

        monkeypatch.setattr(sys, "version_info", (3, 14, 0, "final", 0))
        monkeypatch.setattr(sys, "argv", ["geno", "--help"])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "usage:" in captured.out
        assert captured.err == ""


class TestMainCompatExports:
    """Back-compat imports from geno.__main__ should remain available."""

    def test_legacy_helper_exports_still_exist(self):
        import geno.__main__ as cli_main

        for name in (
            "_emit_unsupported_python_error",
            "_format_source_snippet",
            "_print_error",
            "_print_runtime_error",
            "_resolve_doc_modules",
            "_print_test_results",
            "_run_test_suite_once",
            "_run_tests_watch",
            "_snapshot_watch_mtimes",
        ):
            assert hasattr(cli_main, name), f"missing compatibility export: {name}"


class TestGenoBundle:
    """Test the 'geno bundle' command, including path traversal protection."""

    def _make_toml(self, tmp_path, file_list, entrypoint="Main"):
        """Write a geno.toml and return its path."""
        import pathlib

        toml_path = pathlib.Path(tmp_path) / "geno.toml"
        files_str = ", ".join(f'"{f}"' for f in file_list)
        toml_path.write_text(
            f'entrypoint = "{entrypoint}"\nfiles = [{files_str}]\n',
            encoding="utf-8",
        )
        return str(toml_path)

    def test_bundle_path_traversal_rejected(self):
        """Bundle must reject file paths that escape the project directory."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create a target file outside the project dir
            import pathlib

            outside = pathlib.Path(tmp) / "outside.txt"
            outside.write_text("secret", encoding="utf-8")

            proj = pathlib.Path(tmp) / "project"
            proj.mkdir()

            toml = proj / "geno.toml"
            toml.write_text(
                'entrypoint = "Main"\nfiles = ["../outside.txt"]\n',
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, "-m", "geno", "bundle", "--config", str(toml)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0
            assert (
                "escapes" in result.stderr.lower() or "escape" in result.stderr.lower()
            )

    def test_bundle_normal_files_accepted(self):
        """Bundle should succeed for files within the project directory."""
        with tempfile.TemporaryDirectory() as tmp:
            import pathlib

            proj = pathlib.Path(tmp) / "project"
            proj.mkdir()

            src = proj / "Main.geno"
            src.write_text(
                "func main() -> Unit\n    return ()\nend func main\n",
                encoding="utf-8",
            )

            toml = proj / "geno.toml"
            toml.write_text(
                'entrypoint = "Main"\nfiles = ["Main.geno"]\n',
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, "-m", "geno", "bundle", "--config", str(toml)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0

    def test_bundle_invalid_manifest_field_type_is_rejected(self):
        """Bundle should use shared manifest validation for known fields."""
        with tempfile.TemporaryDirectory() as tmp:
            import pathlib

            proj = pathlib.Path(tmp) / "project"
            proj.mkdir()

            toml = proj / "geno.toml"
            toml.write_text('entrypoint = "Main"\nfiles = "Main"\n', encoding="utf-8")

            result = subprocess.run(
                [sys.executable, "-m", "geno", "bundle", "--config", str(toml)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode != 0
            assert "files" in result.stderr
            assert "list of strings" in result.stderr
            assert "Main.geno" not in result.stderr


class TestGenoServe:
    """Test the hosted runtime CLI surface."""

    def test_serve_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "geno", "serve", "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0
        assert "/healthz" in result.stdout
        assert "--port" in result.stdout


class TestGenoDoc:
    """Test the 'geno doc' command."""

    def test_doc_single_file(self):
        """Generate docs from a single file."""
        with _closed_temp_geno() as f:
            f.write("""
/// Adds two integers.
func add(x: Int, y: Int) -> Int
    example 1, 2 -> 3
    return x + y
end func add

type Shape = Circle(radius: Float) | Rectangle(width: Float, height: Float)
""")
            f.flush()
            out = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
            out.close()
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "geno", "doc", f.name, "-o", out.name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert result.returncode == 0
                assert "Documentation generated" in result.stdout

                with open(out.name) as html_f:
                    html = html_f.read()
                assert "<!DOCTYPE html>" in html
                assert "add" in html
                assert "Shape" in html
                assert "Circle" in html
                assert "Rectangle" in html
                assert "1, 2" in html  # example input
                assert "Adds two integers" in html  # doc comment
                assert "<nav>" in html  # navigation
            finally:
                os.unlink(f.name)
                os.unlink(out.name)

    def test_doc_multi_module(self):
        """Generate docs from a multi-module project."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write two module files
            main_path = os.path.join(tmpdir, "Main.geno")
            with open(main_path, "w") as f:
                f.write("""
import Helper

func greet(name: String) -> String
    example "world" -> "hello world"
    return "hello " + name
end func greet
""")
            helper_path = os.path.join(tmpdir, "Helper.geno")
            with open(helper_path, "w") as f:
                f.write("""
func double(n: Int) -> Int
    example 5 -> 10
    return n * 2
end func double
""")
            out_path = os.path.join(tmpdir, "docs.html")
            result = subprocess.run(
                [sys.executable, "-m", "geno", "doc", tmpdir, "-o", out_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0
            assert "2 module(s)" in result.stdout

            with open(out_path) as f:
                html = f.read()
            assert "mod-Main" in html
            assert "mod-Helper" in html
            assert "greet" in html
            assert "double" in html

    def test_doc_reports_type_aliases_and_traits(self, tmp_path):
        """Generated docs include aliases and traits in output and counts."""
        source = tmp_path / "Main.geno"
        source.write_text(
            "/// Alias docs\n"
            "export type Label = String\n"
            "\n"
            "/// Trait docs\n"
            "trait Named\n"
            "    func name(self: Self) -> String\n"
            "end trait\n"
            "\n"
            "/// Function docs\n"
            "export func label(x: Label) -> Label\n"
            '    example "hi" -> "hi"\n'
            "    return x\n"
            "end func label\n",
            encoding="utf-8",
        )
        out_path = tmp_path / "docs.html"

        result = subprocess.run(
            [sys.executable, "-m", "geno", "doc", str(source), "-o", str(out_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "1 module(s), 1 function(s), 1 type(s), 1 trait(s)" in result.stdout
        html = out_path.read_text(encoding="utf-8")
        assert "Alias docs" in html
        assert "Trait docs" in html
        assert 'href="#alias-Label"' in html
        assert 'href="#trait-Named"' in html

    def test_doc_direct_file_includes_sibling_imported_modules(self, tmp_path):
        """Direct-file docs include imported sibling modules without geno.toml."""
        main_path = tmp_path / "Main.geno"
        main_path.write_text(
            "import Helper\n"
            "func greet(name: String) -> String\n"
            '    example "world" -> "hello world"\n'
            "    return prefix() + name\n"
            "end func greet\n"
        )
        (tmp_path / "Helper.geno").write_text(
            "func prefix() -> String\n"
            '    example () -> "hello "\n'
            '    return "hello "\n'
            "end func prefix\n"
        )
        out_path = tmp_path / "docs.html"

        result = subprocess.run(
            [sys.executable, "-m", "geno", "doc", str(main_path), "-o", str(out_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "2 module(s)" in result.stdout
        html = out_path.read_text()
        assert "mod-Main" in html
        assert "mod-Helper" in html
        assert "greet" in html
        assert "prefix" in html


class TestGenoDevServerBuild:
    """Test dev-server HTML build helpers without binding sockets."""

    def test_build_helper_resolves_direct_file_sibling_imports(self, tmp_path):
        from geno.__main__ import _build_dev_server_html

        main_path = tmp_path / "Main.geno"
        main_path.write_text(
            "import Utils\nfunc main() -> Int\n    return double(21)\nend func\n"
        )
        (tmp_path / "Utils.geno").write_text(
            "func double(x: Int) -> Int\n"
            "    example 2 -> 4\n"
            "    return x * 2\n"
            "end func\n"
        )

        html, build_error = _build_dev_server_html(main_path, title="Test App")

        assert build_error is None
        assert "<!DOCTYPE html>" in html
        assert "Module: Utils" in html
        assert "Test App" in html

    def test_build_helper_typechecks_multi_module_browser_project(self, tmp_path):
        from geno.__main__ import _build_dev_server_html

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\nfiles = ["App", "Bad"]\n',
            encoding="utf-8",
        )
        (tmp_path / "App.geno").write_text(
            "import Bad\n"
            '@untested("entry")\n'
            "func main() -> Int\n"
            "    return Bad.bad()\n"
            "end func\n",
            encoding="utf-8",
        )
        (tmp_path / "Bad.geno").write_text(
            '@untested("repro")\nfunc bad() -> Int\n    return "oops"\nend func\n',
            encoding="utf-8",
        )

        html, build_error = _build_dev_server_html(tmp_path)

        assert build_error is not None
        assert "Return type mismatch" in build_error
        assert "Build Error" in html

    @pytest.mark.parametrize(
        ("host", "origin"),
        [
            ("localhost:3210", None),
            ("127.0.0.1:3210", None),
            ("localhost:3210", "http://localhost:3210"),
            ("127.0.0.1:3210", "http://127.0.0.1:3210"),
        ],
    )
    def test_dev_header_policy_accepts_exact_loopback_authority(self, host, origin):
        from email.message import Message

        from geno.cli.serve import _dev_request_header_error

        headers = Message()
        headers.add_header("Host", host)
        if origin is not None:
            headers.add_header("Origin", origin)

        assert _dev_request_header_error(headers, 3210) is None

    @pytest.mark.parametrize(
        ("host", "origin"),
        [
            ("localhost", None),
            ("127.0.0.1", None),
            ("localhost", "http://localhost"),
            ("127.0.0.1", "http://127.0.0.1"),
        ],
    )
    def test_dev_header_policy_accepts_implicit_default_http_port(self, host, origin):
        from email.message import Message

        from geno.cli.serve import _dev_request_header_error

        headers = Message()
        headers.add_header("Host", host)
        if origin is not None:
            headers.add_header("Origin", origin)

        assert _dev_request_header_error(headers, 80) is None

    def test_dev_header_policy_rejects_explicit_empty_default_port(self):
        from email.message import Message

        from geno.cli.serve import _dev_request_header_error

        headers = Message()
        headers.add_header("Host", "localhost:")

        assert _dev_request_header_error(headers, 80) == 421

    @pytest.mark.parametrize(
        "host",
        [
            "attacker.test:3210",
            "localhost",
            "localhost:3211",
            "user@localhost:3210",
            "localhost:3210/path",
            "localhost:not-a-port",
            "[::1]:3210",
        ],
    )
    def test_dev_header_policy_rejects_dns_rebinding_hosts(self, host):
        from email.message import Message

        from geno.cli.serve import _dev_request_header_error

        headers = Message()
        headers.add_header("Host", host)

        assert _dev_request_header_error(headers, 3210) == 421

    def test_dev_header_policy_rejects_missing_and_duplicate_host(self):
        from email.message import Message

        from geno.cli.serve import _dev_request_header_error

        missing = Message()
        duplicate = Message()
        duplicate.add_header("Host", "localhost:3210")
        duplicate.add_header("Host", "localhost:3210")

        assert _dev_request_header_error(missing, 3210) == 421
        assert _dev_request_header_error(duplicate, 3210) == 421

    @pytest.mark.parametrize(
        "origin",
        [
            "null",
            "https://localhost:3210",
            "http://attacker.test:3210",
            "http://127.0.0.1:3210",
            "http://localhost:3211",
            "http://user@localhost:3210",
            "http://localhost:",
            "http://localhost:3210/path",
        ],
    )
    def test_dev_header_policy_rejects_cross_origin_requests(self, origin):
        from email.message import Message

        from geno.cli.serve import _dev_request_header_error

        headers = Message()
        headers.add_header("Host", "localhost:3210")
        headers.add_header("Origin", origin)

        assert _dev_request_header_error(headers, 3210) == 403

    def test_dev_header_policy_rejects_duplicate_origin(self):
        from email.message import Message

        from geno.cli.serve import _dev_request_header_error

        headers = Message()
        headers.add_header("Host", "localhost:3210")
        headers.add_header("Origin", "http://localhost:3210")
        headers.add_header("Origin", "http://localhost:3210")

        assert _dev_request_header_error(headers, 3210) == 403

    def test_dev_header_completion_and_expiry_have_single_winner(self):
        import socket
        import threading

        from geno.cli.serve import (
            _DEV_WRITE_TIMEOUT_SECONDS,
            _BoundedDevHTTPServer,
        )

        class FakeRequest:
            def __init__(self):
                self.closed = False
                self.shutdown_calls = []
                self.timeouts = []

            def shutdown(self, how):
                self.shutdown_calls.append(how)

            def close(self):
                self.closed = True

            def settimeout(self, timeout):
                self.timeouts.append(timeout)

        def server_with_deadline():
            server = object.__new__(_BoundedDevHTTPServer)
            server._header_timer_lock = threading.Lock()
            request = FakeRequest()
            timer = threading.Timer(60, lambda: None)
            server._header_timers = {request: timer}
            return server, request, timer

        server, request, timer = server_with_deadline()
        server.complete_request_headers(request)
        server._expire_headers(request)
        assert timer.finished.is_set() is True
        assert request.timeouts == [_DEV_WRITE_TIMEOUT_SECONDS]
        assert request.shutdown_calls == []
        assert request.closed is False

        server, request, timer = server_with_deadline()
        server._expire_headers(request)
        server.complete_request_headers(request)
        assert timer.finished.is_set() is False
        assert request.timeouts == []
        assert request.shutdown_calls == [socket.SHUT_RDWR]
        assert request.closed is True

    def test_dev_server_bounds_connections_and_releases_timed_out_slot(self):
        import http.client
        import http.server
        import socket
        import threading
        import time

        from geno.cli.serve import _BoundedDevHTTPServer

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                active_server = self.server
                assert isinstance(active_server, _BoundedDevHTTPServer)
                active_server.complete_request_headers(self.connection)
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                try:
                    self.wfile.write(b"ok")
                except OSError:
                    pass

            def log_message(self, _format, *_args):
                pass

        server = _BoundedDevHTTPServer(
            ("127.0.0.1", 0),
            Handler,
            max_connections=1,
            header_timeout=2.0,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = int(server.server_address[1])
        slow = socket.create_connection(("127.0.0.1", port), timeout=2)
        try:
            slow.sendall(b"GET / HTTP/1.1\r\n")
            deadline = time.monotonic() + 2
            while server._connection_slots._value != 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server._connection_slots._value == 0

            overflow = socket.create_connection(("127.0.0.1", port), timeout=2)
            try:
                assert b"503 Service Unavailable" in overflow.recv(4096)
            finally:
                overflow.close()

            deadline = time.monotonic() + 4
            while server._connection_slots._value != 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server._connection_slots._value == 1

            client = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            client.request("GET", "/", headers={"Host": f"localhost:{port}"})
            response = client.getresponse()
            assert response.status == 200
            assert response.read() == b"ok"
            client.close()
        finally:
            slow.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_dev_sse_heartbeat_releases_client_slot(self):
        import http.server

        from geno.cli.serve import _BoundedDevHTTPServer, _serve_dev_sse

        server = _BoundedDevHTTPServer(
            ("127.0.0.1", 0), http.server.BaseHTTPRequestHandler, max_sse_clients=1
        )
        writes = []

        class Writer:
            def write(self, value):
                writes.append(value)
                server.stop_event.set()

            def flush(self):
                pass

        class Handler:
            wfile = Writer()

            def send_response(self, _status):
                pass

            def send_header(self, _name, _value):
                pass

            def end_headers(self):
                pass

            def send_error(self, _status, _message):
                raise AssertionError("SSE slot unexpectedly unavailable")

        try:
            _serve_dev_sse(Handler(), server)
            assert writes == [b": keepalive\n\n"]
            assert server._sse_slots._value == 1
        finally:
            server.server_close()

    def test_dev_sse_limit_preserves_capacity_for_normal_requests(self):
        import http.server
        import threading

        from geno.cli.serve import _BoundedDevHTTPServer, _serve_dev_sse

        server = _BoundedDevHTTPServer(
            ("127.0.0.1", 0), http.server.BaseHTTPRequestHandler, max_sse_clients=1
        )
        occupied = threading.Event()
        assert server.register_sse(occupied)
        errors = []

        class Handler:
            close_connection = False

            def send_error(self, status, message):
                errors.append((status, message))

        handler = Handler()
        try:
            _serve_dev_sse(handler, server)
            assert errors == [(503, "Too many live-reload clients")]
            assert handler.close_connection is True
            server.unregister_sse(occupied)
            assert server._sse_slots._value == 1
            assert server._connection_slots._value == 64
        finally:
            server.server_close()


class TestCliWatchResolution:
    """Test watched-file resolution for CLI watch surfaces."""

    def test_watch_run_defaults_to_process_sandbox(self, tmp_path, monkeypatch, capsys):
        import time

        from geno.cli import run as run_mod
        from geno.cli.watch import watch_run

        app = tmp_path / "App.geno"
        app.write_text("func main() -> Int\n    return 1\nend func\n")
        calls = []

        def fake_run_file(*args, **kwargs):
            calls.append((args, kwargs))

        def stop_watch(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(run_mod, "run_file", fake_run_file)
        monkeypatch.setattr(time, "sleep", stop_watch)

        watch_run(str(app))

        assert calls
        assert calls[0][1]["unsafe"] is False
        captured = capsys.readouterr()
        assert "Execution mode: process sandbox" in captured.out

    def test_watch_run_allows_explicit_unsafe_mode(self, tmp_path, monkeypatch, capsys):
        import time

        from geno.cli import run as run_mod
        from geno.cli.watch import watch_run

        app = tmp_path / "App.geno"
        app.write_text("func main() -> Int\n    return 1\nend func\n")
        calls = []

        def fake_run_file(*args, **kwargs):
            calls.append((args, kwargs))

        def stop_watch(_seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(run_mod, "run_file", fake_run_file)
        monkeypatch.setattr(time, "sleep", stop_watch)

        watch_run(str(app), unsafe=True)

        assert calls
        assert calls[0][1]["unsafe"] is True
        captured = capsys.readouterr()
        assert "Execution mode: unsafe interpreter" in captured.out

    def test_watch_cli_passes_explicit_unsafe_flag(self, tmp_path, monkeypatch):
        import geno.__main__ as cli_main
        from geno.__main__ import main

        app = tmp_path / "App.geno"
        app.write_text("func main() -> Int\n    return 1\nend func\n")
        calls = []

        def fake_watch_run(*args, **kwargs):
            calls.append((args, kwargs))

        monkeypatch.setattr(cli_main, "watch_run", fake_watch_run)
        monkeypatch.setattr(sys, "argv", ["geno", "watch", "--unsafe", str(app)])

        main()

        assert calls
        assert calls[0][1]["unsafe"] is True

    def test_watch_files_include_direct_file_and_sibling_imports(self, tmp_path):
        from geno.__main__ import _resolve_watch_files

        app = tmp_path / "App.gen"
        app.write_text(
            "import Utils\nfunc main() -> Int\n    return double(21)\nend func\n"
        )
        (tmp_path / "Utils.geno").write_text(
            "func double(x: Int) -> Int\n"
            "    example 2 -> 4\n"
            "    return x * 2\n"
            "end func\n"
        )
        (tmp_path / "Ignored.geno").write_text(
            "func noop() -> Int\n    example () -> 0\n    return 0\nend func\n"
        )

        watched = {path.name for path in _resolve_watch_files(app)}

        assert "App.gen" in watched
        assert "Utils.geno" in watched
        assert "Ignored.geno" not in watched

    def test_watch_files_survive_malformed_source(self, tmp_path):
        # A syntax/lex error in a watched file must not crash resolution: the whole
        # point of `geno watch` / `geno dev` is to keep running so the user can fix
        # the error and have it re-run on save. Previously ParseErrors/LexerError
        # escaped and killed the watch loop (and the dev live-reload thread).
        from geno.cli.watch import _resolve_watch_files, _snapshot_watch_mtimes

        for name, src in (
            ("Syntax.geno", "func main() -> Int\n  let x = @@@ broken\nend func\n"),
            ("Unterminated.geno", 'func main() -> Int\n  let x = "oops\nend func\n'),
        ):
            bad = tmp_path / name
            bad.write_text(src)
            # Neither call raises; resolution falls back to the target file.
            watched = {path.name for path in _resolve_watch_files(bad)}
            assert name in watched
            assert _snapshot_watch_mtimes(bad)
            bad.unlink()

    def test_watch_files_include_project_and_dependency_manifests(self, tmp_path):
        from geno.__main__ import _resolve_watch_files

        (tmp_path / "geno.toml").write_text(
            'entrypoint = "App"\n'
            'files = ["App.geno"]\n'
            "[dependencies.http-utils]\n"
            'git = "https://example.com/http-utils.git"\n'
        )
        (tmp_path / "App.geno").write_text(
            "import HttpUtils\nfunc main() -> Int\n    return status_code()\nend func\n"
        )
        dep_dir = tmp_path / "geno_modules" / "http-utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "HttpUtils.geno").write_text(
            "func status_code() -> Int\n"
            "    example () -> 204\n"
            "    return 204\n"
            "end func\n"
        )
        (dep_dir / "geno.toml").write_text('entrypoint = "HttpUtils"\n')

        watched = {
            path.relative_to(tmp_path).as_posix()
            for path in _resolve_watch_files(tmp_path)
        }

        assert "App.geno" in watched
        assert "geno.toml" in watched
        assert "geno_modules/http-utils/HttpUtils.geno" in watched
        assert "geno_modules/http-utils/geno.toml" in watched
