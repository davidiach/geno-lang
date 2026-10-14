"""Capability-denial diagnostics: wording and call-site location per lane.

A denied call must name the missing capability on every lane, report the
call expression's location wherever the lane reports locations, and give the
default ``geno run`` lane (which rejects ``--cap``) advice a user can act on.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from geno import api
from geno import run as geno_run
from geno.capabilities import DEFAULT_ALLOWED_CAPABILITIES, capability_denied_message
from geno.cli.run import _default_lane_capability_hint
from geno.compiler import compile_to_python
from geno.diagnostics import ErrorCode
from geno.interpreter import Interpreter
from geno.lexer import Lexer
from geno.parser import Parser
from geno.tests._script_runner import run_python_code
from geno.values import GenoRuntimeError

# fs_read_text( starts at line 2, column 17.
NOTES_SOURCE = """func main() -> Unit
    let notes = fs_read_text("notes.txt")
    print(notes)
    return ()
end func
"""

FS_DENIED = "Capability denied: 'fs_read_text' requires the 'fs' capability"


def _geno(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "geno", "run", "--no-check-examples", *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd,
    )


@pytest.fixture
def notes_dir(tmp_path: Path) -> Path:
    (tmp_path / "notes.geno").write_text(NOTES_SOURCE, encoding="utf-8")
    return tmp_path


def test_message_names_capability() -> None:
    assert capability_denied_message("fs_read_text", "fs") == FS_DENIED


def test_interpreter_denial_reports_call_site() -> None:
    program = Parser(Lexer(NOTES_SOURCE, "notes.geno").tokenize()).parse_program()
    interpreter = Interpreter(capabilities={"print"})

    with pytest.raises(GenoRuntimeError) as excinfo:
        interpreter.run(program)

    error = excinfo.value
    assert error.message == FS_DENIED
    assert error.error_code is ErrorCode.RUNTIME_CAPABILITY_DENIED
    assert error.location is not None
    assert (error.location.line, error.location.column) == (2, 17)


def test_interpreter_denial_through_function_value_reports_call_site() -> None:
    source = """func apply(f: (String) -> String, path: String) -> String
    example (to_upper, "a") -> "A"
    return f(path)
end func

func main() -> Unit
    print(apply(f: fs_read_text, path: "notes.txt"))
    return ()
end func
"""
    program = Parser(Lexer(source, "apply.geno").tokenize()).parse_program()
    interpreter = Interpreter(capabilities={"print"})

    with pytest.raises(GenoRuntimeError) as excinfo:
        interpreter.run(program)

    assert excinfo.value.message == FS_DENIED
    location = excinfo.value.location
    assert location is not None
    assert (location.line, location.column) == (3, 12)


def test_embedding_api_diagnostic_has_call_site() -> None:
    result = geno_run(NOTES_SOURCE, "notes.geno", api.RunConfig(capabilities={"print"}))

    assert not result.ok
    (diagnostic,) = result.diagnostics
    assert diagnostic.code is ErrorCode.RUNTIME_CAPABILITY_DENIED
    assert diagnostic.message == FS_DENIED
    assert diagnostic.location is not None
    assert (diagnostic.location.line, diagnostic.location.column) == (2, 17)


def test_embedding_api_env_policy_denial_has_call_site() -> None:
    source = """func main() -> String
    return env_get_or(name: "SECRET_TOKEN", default: "none")
end func
"""
    result = geno_run(
        source,
        "env.geno",
        api.RunConfig(capabilities={"env"}, env_allowed_names={"HOME"}),
    )

    assert not result.ok
    (diagnostic,) = result.diagnostics
    assert diagnostic.code is ErrorCode.RUNTIME_CAPABILITY_DENIED
    assert diagnostic.location is not None
    assert (diagnostic.location.line, diagnostic.location.column) == (2, 12)


def test_cli_unsafe_reports_call_site(notes_dir: Path) -> None:
    result = _geno("--unsafe", "--cap", "print", "notes.geno", cwd=notes_dir)

    assert result.returncode == 1
    # The trace keeps the frame's definition site and adds the call site.
    assert f"  in main, {notes_dir / 'notes.geno'}:1:1\n" in result.stderr
    assert f"  at {notes_dir / 'notes.geno'}:2:17\n" in result.stderr
    assert f"Runtime Error: {FS_DENIED}\n" in result.stderr
    assert 'let notes = fs_read_text("notes.txt")' in result.stderr


def test_cli_json_reports_e412_with_call_site(notes_dir: Path) -> None:
    result = _geno("--json", "--cap", "print", "notes.geno", cwd=notes_dir)

    payload = json.loads(result.stdout)
    (diagnostic,) = payload["diagnostics"]
    assert diagnostic["code"] == "E412"
    assert diagnostic["message"] == FS_DENIED
    assert diagnostic["location"]["line"] == 2
    assert diagnostic["location"]["column"] == 17


def test_cli_default_lane_names_capability_with_actionable_hint(
    notes_dir: Path,
) -> None:
    result = _geno("notes.geno", cwd=notes_dir)

    assert result.returncode == 1
    caps = ",".join(sorted(DEFAULT_ALLOWED_CAPABILITIES | {"fs"}))
    assert result.stderr == (
        f"Runtime Error: {FS_DENIED}\n"
        "Hint: the default 'geno run' lane grants only clock, print, random "
        "and does not accept --cap.\n"
        "  To grant 'fs', rerun without process isolation (trusted code only):\n"
        f"  geno run --unsafe --cap {caps} notes.geno\n"
    )

    # The hinted command is one the CLI actually accepts and that succeeds.
    (notes_dir / "notes.txt").write_text("hello notes", encoding="utf-8")
    hinted = _geno("--unsafe", "--cap", caps, "notes.geno", cwd=notes_dir)
    assert hinted.returncode == 0, hinted.stderr
    assert hinted.stdout == "hello notes\n"


def test_cli_default_lane_still_rejects_cap(notes_dir: Path) -> None:
    result = _geno("--cap", "fs", "notes.geno", cwd=notes_dir)

    assert result.returncode == 1
    assert "--cap is not supported" in result.stderr


@pytest.mark.parametrize(
    "error",
    [
        # Builtin and capability disagree with the manifest.
        "Capability denied: 'fs_read_text' requires the 'process' capability",
        # Unknown builtin.
        "Capability denied: 'not_a_builtin' requires the 'fs' capability",
        # Already granted by the default lane, so no rerun would help.
        "Capability denied: 'print' requires the 'print' capability",
        # Extra text around the denial.
        "boom: Capability denied: 'fs_read_text' requires the 'fs' capability",
        "Capability denied: 'fs_read_text' requires the 'fs' capability\nmore",
    ],
)
def test_default_lane_hint_only_for_manifest_denials(error: str) -> None:
    assert _default_lane_capability_hint(error, "notes.geno") is None


def test_default_lane_hint_quotes_filename() -> None:
    hint = _default_lane_capability_hint(FS_DENIED, "my notes.geno")
    assert hint is not None
    assert hint.endswith("'my notes.geno'")


def test_standalone_compiled_python_names_capability_and_flag() -> None:
    result = run_python_code(
        compile_to_python(NOTES_SOURCE),
        python_executable=sys.executable,
        timeout=30,
    )

    assert result.returncode != 0
    assert f"{FS_DENIED}; pass '--cap fs' to grant it" in result.stderr
