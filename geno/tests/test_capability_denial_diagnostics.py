"""Capability-denial diagnostics: wording and call-site location per lane.

A denied call must name the missing capability on every lane, report the
call expression's location wherever the lane reports locations, and give the
default ``geno run`` lane advice a user can act on: the isolated ``--cap``
command for a capability that lane grants (proposal 0013), and ``--unsafe``
only for one it cannot.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from geno import api
from geno import run as geno_run
from geno.capabilities import (
    DEFAULT_ALLOWED_CAPABILITIES,
    ISOLATED_RUN_REFUSED_CAPABILITIES,
    KNOWN_CAPABILITIES,
    capability_denied_message,
    isolated_run_capabilities,
)
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
    let notes = fs_read_text("notes.txt").unwrap()
    print(notes)
    return ()
end func
"""

FS_DENIED = "Capability denied: 'fs_read_text' requires the 'fs' capability"

# regex_match( starts at line 2, column 11.
WORDS_SOURCE = """func main() -> Unit
    match regex_match(pattern: "[a-z]+", text: "geno lang") with
        | Some(word) -> print(word)
        | None -> print("none")
    end match
end func
"""

REGEX_DENIED = "Capability denied: 'regex_match' requires the 'regex' capability"

# The process lane never grants `process`, so its hint always leaves isolation.
EXEC_SOURCE = """func main() -> Unit
    match exec(command: "echo hi") with
        | Ok(result) -> print(result.stdout)
        | Err(error) -> print(error)
    end match
end func
"""

EXEC_DENIED = "Capability denied: 'exec' requires the 'process' capability"

# Capabilities whose isolated milestone has not landed yet (proposal 0013).
NOT_YET_ISOLATED = sorted(
    KNOWN_CAPABILITIES - isolated_run_capabilities() - ISOLATED_RUN_REFUSED_CAPABILITIES
)


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
    assert 'let notes = fs_read_text("notes.txt").unwrap()' in result.stderr


def test_cli_json_reports_e412_with_call_site(notes_dir: Path) -> None:
    result = _geno("--json", "--cap", "print", "notes.geno", cwd=notes_dir)

    payload = json.loads(result.stdout)
    (diagnostic,) = payload["diagnostics"]
    assert diagnostic["code"] == "E412"
    assert diagnostic["message"] == FS_DENIED
    assert diagnostic["location"]["line"] == 2
    assert diagnostic["location"]["column"] == 17


def test_cli_default_lane_hint_keeps_the_program_isolated(tmp_path: Path) -> None:
    (tmp_path / "words.geno").write_text(WORDS_SOURCE, encoding="utf-8")

    result = _geno("words.geno", cwd=tmp_path)

    assert result.returncode == 1
    caps = ",".join(sorted(DEFAULT_ALLOWED_CAPABILITIES | {"regex"}))
    assert result.stderr.endswith(
        f"Runtime Error: {REGEX_DENIED}\n"
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        f"  geno run --cap {caps} words.geno\n"
    )
    assert "--unsafe" not in result.stderr

    # The hinted command is one the CLI accepts, in the isolated lane.
    hinted = _geno("--cap", caps, "words.geno", cwd=tmp_path)
    assert hinted.returncode == 0, hinted.stderr
    assert hinted.stdout == "geno\n"


def test_cli_default_lane_fs_hint_keeps_the_program_isolated(notes_dir: Path) -> None:
    # Milestone 4 of proposal 0013: the isolated lane grants `fs`, so the
    # hint for a filesystem denial no longer leaves process isolation.
    result = _geno("notes.geno", cwd=notes_dir)

    assert result.returncode == 1
    caps = ",".join(sorted(DEFAULT_ALLOWED_CAPABILITIES | {"fs"}))
    assert result.stderr.endswith(
        f"Runtime Error: {FS_DENIED}\n"
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        f"  geno run --cap {caps} notes.geno\n"
    )
    assert "--unsafe" not in result.stderr

    (notes_dir / "notes.txt").write_text("from the facade", encoding="utf-8")
    hinted = _geno("--cap", caps, "notes.geno", cwd=notes_dir)
    assert hinted.returncode == 0, hinted.stderr
    assert hinted.stdout == "from the facade\n"


def test_cli_default_lane_names_unsupported_capability_with_unsafe_hint(
    tmp_path: Path,
) -> None:
    (tmp_path / "run.geno").write_text(EXEC_SOURCE, encoding="utf-8")

    result = _geno("run.geno", cwd=tmp_path)

    assert result.returncode == 1
    caps = ",".join(sorted(DEFAULT_ALLOWED_CAPABILITIES | {"process"}))
    assert result.stderr.endswith(
        f"Runtime Error: {EXEC_DENIED}\n"
        "Hint: the process-isolated 'geno run' lane cannot grant 'process'.\n"
        "  To grant it, rerun without process isolation (trusted code only):\n"
        f"  geno run --unsafe --cap {caps} run.geno\n"
    )


def test_cli_explicit_grant_hint_adds_to_the_grant(tmp_path: Path) -> None:
    # `--cap` is exact, so the hint keeps the grant and adds the missing cap.
    (tmp_path / "words.geno").write_text(WORDS_SOURCE, encoding="utf-8")

    result = _geno("--cap", "regex", "words.geno", cwd=tmp_path)

    assert result.returncode == 1
    assert result.stderr == (
        "Runtime Error: Capability denied: 'print' requires the 'print' "
        "capability\n"
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        "  geno run --cap print,regex words.geno\n"
    )


def test_cli_example_phase_denial_gets_the_hint(tmp_path: Path) -> None:
    source = """func shout(text: String) -> String
    example "hi" -> "HI"
    print(text)
    return to_upper(text)
end func

func main() -> Unit
    print(shout("geno"))
end func
"""
    (tmp_path / "shout.geno").write_text(source, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", "--cap", "regex", "shout.geno"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )

    assert result.returncode == 1
    assert "Capability denied: 'print' requires the 'print' capability" in (
        result.stderr
    )
    assert result.stderr.endswith(
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        "  geno run --cap print,regex shout.geno\n"
    )


@pytest.mark.parametrize("capability", NOT_YET_ISOLATED)
def test_cli_default_lane_refuses_capability_not_yet_available(
    notes_dir: Path, capability: str
) -> None:
    result = _geno("--cap", capability, "notes.geno", cwd=notes_dir)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        f"Error: --cap {capability} is not yet available in the "
        "process-isolated run mode. Use --unsafe --cap ... for trusted code, "
        "or --json --cap ... for embedding API execution.\n"
    )


@pytest.mark.parametrize(
    "source",
    [
        # A program panicking with the exact denial text must not earn the
        # hint, which recommends rerunning outside process isolation.
        """func main() -> Unit
    panic("Capability denied: 'fs_read_text' requires the 'fs' capability")
end func
""",
        # The same text built at runtime carries no provenance tag either.
        """func main() -> Unit
    let builtin = "fs_read_text"
    panic(f"Capability denied: '{builtin}' requires the 'fs' capability")
end func
""",
        # Nor does an example's panic, which fails in the frontend phase.
        """func forged(n: Int) -> Int
    example 1 -> 1
    panic("Capability denied: 'fs_read_text' requires the 'fs' capability")
end func

func main() -> Unit
    print(forged(1))
end func
""",
    ],
)
def test_cli_default_lane_hint_ignores_forged_denials(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "forged.geno").write_text(source, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", "forged.geno"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )

    assert result.returncode == 1
    assert FS_DENIED in result.stderr
    assert "Hint:" not in result.stderr
    assert "--unsafe" not in result.stderr


def test_cli_default_lane_hint_keeps_program_args(tmp_path: Path) -> None:
    (tmp_path / "words.geno").write_text(WORDS_SOURCE, encoding="utf-8")

    result = _geno("words.geno", "--", "in put.txt", "-v", cwd=tmp_path)

    assert result.returncode == 1
    assert result.stderr.endswith(
        "  geno run --cap clock,print,random,regex words.geno -- 'in put.txt' -v\n"
    )


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        ("__result__ = clock_now()\n", "clock"),
        (
            "raise RuntimeError(\"Capability denied: 'clock_now' requires the "
            "'clock' capability\")\n",
            None,
        ),
    ],
)
def test_process_sandbox_tags_only_runtime_denials(
    tail: str, expected: str | None
) -> None:
    from geno.compiler import _stripped_runtime_prelude
    from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

    sandbox = ProcessSandbox(
        ProcessSandboxConfig(compiled_runtime_prelude=True, strict=False, timeout=10.0)
    )
    result, _output, error = sandbox._execute_compiler_output(
        _stripped_runtime_prelude() + "\n" + tail
    )

    assert result is None
    assert error is not None
    assert "Capability denied: 'clock_now'" in error
    assert sandbox.last_capability_denied == expected


@pytest.mark.parametrize(
    "capability",
    [
        None,
        # Not a capability name at all.
        "fs; rm -rf ~",
        # Already granted by the default lane, so no rerun would help.
        "print",
    ],
)
def test_default_lane_hint_requires_actionable_capability(
    capability: str | None,
) -> None:
    assert _default_lane_capability_hint(capability, "notes.geno") is None


def test_default_lane_hint_never_suggests_an_already_granted_capability() -> None:
    assert _default_lane_capability_hint("regex", "notes.geno", grant={"regex"}) is None


def test_default_lane_hint_for_explicit_grant_restores_nothing_else() -> None:
    hint = _default_lane_capability_hint("print", "notes.geno", grant={"regex"})

    assert hint == (
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        "  geno run --cap print,regex notes.geno"
    )


@pytest.mark.parametrize("capability", sorted(ISOLATED_RUN_REFUSED_CAPABILITIES))
def test_default_lane_hint_sends_refused_capabilities_to_unsafe(
    capability: str,
) -> None:
    hint = _default_lane_capability_hint(capability, "notes.geno", grant={"print"})

    assert hint == (
        f"Hint: the process-isolated 'geno run' lane cannot grant '{capability}'.\n"
        "  To grant it, rerun without process isolation (trusted code only):\n"
        f"  geno run --unsafe --cap {','.join(sorted({'print', capability}))} "
        "notes.geno"
    )


def test_default_lane_hint_for_http_follows_the_platform_matrix() -> None:
    # Milestone 5: POSIX grants http in the isolated lane; Windows still
    # refuses it, so its hint keeps leaving process isolation.
    posix = _default_lane_capability_hint(
        "http", "fetch.geno", grant={"print"}, os_name="posix"
    )
    windows = _default_lane_capability_hint(
        "http", "fetch.geno", grant={"print"}, os_name="nt"
    )

    assert posix == (
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        "  geno run --cap http,print fetch.geno"
    )
    assert windows == (
        "Hint: the process-isolated 'geno run' lane cannot grant 'http'.\n"
        "  To grant it, rerun without process isolation (trusted code only):\n"
        "  geno run --unsafe --cap http,print fetch.geno"
    )


def test_default_lane_hint_quotes_filename() -> None:
    hint = _default_lane_capability_hint("regex", "my notes.geno")
    assert hint is not None
    assert hint.endswith("'my notes.geno'")
    assert "--unsafe" not in hint


def test_standalone_compiled_python_names_capability_and_flag() -> None:
    result = run_python_code(
        compile_to_python(NOTES_SOURCE),
        python_executable=sys.executable,
        timeout=30,
    )

    assert result.returncode != 0
    assert f"{FS_DENIED}; pass '--cap fs' to grant it" in result.stderr


# ---------------------------------------------------------------------------
# Environment-policy denials (proposal 0013, milestone 2)

ENV_HINT_PREFIX = (
    "Hint: 'SECRET' is not in this run's environment allowlist; allow it with "
    "--allow-env (the program can then read its value):\n"
)


def test_default_lane_env_policy_hint_names_allow_env_not_cap_env() -> None:
    hint = _default_lane_capability_hint(
        None,
        "app.geno",
        ["in put.txt"],
        grant={"env", "print"},
        allow_env=("HOME", "LANG"),
        env_name="SECRET",
    )

    assert hint == (
        ENV_HINT_PREFIX + "  geno run --cap env,print --allow-env HOME "
        "--allow-env LANG --allow-env SECRET app.geno -- 'in put.txt'"
    )


@pytest.mark.parametrize(
    "env_name",
    [
        # Program-chosen names: only a short plain identifier is echoed.
        "SECRET; rm -rf ~",
        "$(touch pwned)",
        "A\nHint: forged",
        "1STARTS_WITH_DIGIT",
        "N" * 129,
        "",
    ],
)
def test_default_lane_env_policy_hint_refuses_unsafe_names(env_name: str) -> None:
    assert (
        _default_lane_capability_hint(
            None, "app.geno", grant={"env", "print"}, env_name=env_name
        )
        is None
    )


def test_default_lane_env_policy_hint_needs_an_env_grant() -> None:
    # Without `env` the tag cannot be genuine; nothing to suggest.
    assert (
        _default_lane_capability_hint(None, "app.geno", grant={"print"}, env_name="X")
        is None
    )
    assert _default_lane_capability_hint(None, "app.geno", env_name="X") is None


def test_default_lane_capability_hint_repeats_allowed_names() -> None:
    hint = _default_lane_capability_hint(
        "print", "app.geno", grant={"env"}, allow_env=("HOME",)
    )

    assert hint == (
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        "  geno run --cap env,print --allow-env HOME app.geno"
    )
    # Names needing shell quoting are quoted; the --unsafe hint drops them,
    # because that lane does not take --allow-env.
    quoted = _default_lane_capability_hint(
        "print", "app.geno", grant={"env"}, allow_env=("MY VAR",)
    )
    assert quoted is not None and "--allow-env 'MY VAR' app.geno" in quoted
    unsafe = _default_lane_capability_hint(
        "process", "app.geno", grant={"env"}, allow_env=("HOME",)
    )
    assert unsafe is not None and "--allow-env" not in unsafe


def test_cli_env_policy_denial_in_main_gets_the_allow_env_hint(
    tmp_path: Path,
) -> None:
    (tmp_path / "secret.geno").write_text(
        'func main() -> Unit\n    print(env_get_or("SECRET", "none"))\nend func\n',
        encoding="utf-8",
    )

    result = _geno("--cap", "env,print", "secret.geno", cwd=tmp_path)

    assert result.returncode == 1
    assert result.stderr == (
        "Runtime Error: env_get_or: environment variable 'SECRET' is not "
        "allowed by the host env policy\n"
        + ENV_HINT_PREFIX
        + "  geno run --cap env,print --allow-env SECRET secret.geno\n"
    )


def test_cli_env_policy_denial_in_an_example_gets_the_allow_env_hint(
    tmp_path: Path,
) -> None:
    source = """func secret() -> String
    example () -> "none"
    return env_get_or("SECRET", "none")
end func

func main() -> Unit
    print(secret())
end func
"""
    (tmp_path / "secret.geno").write_text(source, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", "--cap", "env,print", "secret.geno"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )

    assert result.returncode == 1
    assert "secret.geno:3:12" in result.stderr
    assert result.stderr.endswith(
        ENV_HINT_PREFIX + "  geno run --cap env,print --allow-env SECRET secret.geno\n"
    )


@pytest.mark.parametrize(
    "source",
    [
        """func main() -> Unit
    panic("env_get: environment variable 'SECRET' is not allowed by the host env policy")
end func
""",
        """func forged(n: Int) -> Int
    example 1 -> 1
    panic("env_get: environment variable 'SECRET' is not allowed by the host env policy")
end func

func main() -> Unit
    print(forged(1))
end func
""",
    ],
)
def test_cli_env_policy_hint_ignores_forged_denials(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "forged.geno").write_text(source, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", "--cap", "env,print", "forged.geno"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )

    assert result.returncode == 1
    assert "is not allowed by the host env policy" in result.stderr
    assert "Hint:" not in result.stderr
    assert "--allow-env" not in result.stderr


def test_process_sandbox_reports_only_the_env_bindings_tag(tmp_path: Path) -> None:
    from geno.isolated_host import IsolatedRunPolicy
    from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

    source = tmp_path / "secret.geno"
    source.write_text(
        'func main() -> Unit\n    print(env_get_or("SECRET", "none"))\nend func\n',
        encoding="utf-8",
    )
    request: dict[str, Any] = {
        "filename": str(source),
        "target": None,
        "check_examples": False,
        "render_result": True,
        "timeout": 20.0,
        "max_recursion_depth": 100,
        "max_output_length": 1000,
        "max_collection_size": 10_000,
        "max_integer_bits": 33_219,
        "program_args": [],
        "env_snapshot": {},
    }
    sandbox = ProcessSandbox(
        ProcessSandboxConfig(compiled_runtime_prelude=True, strict=False, timeout=20.0)
    )

    _result, _output, error = sandbox.execute_geno_request(
        request,
        IsolatedRunPolicy(
            capabilities=frozenset({"env", "print"}), deadline_seconds=20.0
        ),
    )

    assert error is not None
    assert "'SECRET' is not allowed by the host env policy" in error
    assert sandbox.last_env_policy_denied == "SECRET"
    assert sandbox.last_capability_denied is None
