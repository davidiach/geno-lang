"""Capability grants in the process-isolated ``geno run`` lane (proposal 0013).

Milestone 1: the default lane accepts ``--cap`` for the capabilities it
implements (``clock``, ``print``, ``random``, ``regex``), refuses ``process``,
``serve`` and every capability whose milestone has not landed before a worker
starts, and gives examples exactly the run's grant, one deadline and one
output budget.

Milestone 2: ``env`` gives ``cli_args()`` the program arguments without
exposing any variable; ``--allow-env NAME`` permits individual variables from
the caller's environment, examples and ``main`` share one snapshot, and issue
#68's ``geno run --cap env,print main.geno -- hello world`` works while the
run keeps its hard limits.

Milestone 3: ``stdin`` is implemented (``test_isolated_run_stdin.py``) but
stays refused on every platform until its platform gate passes; the pins
here record that, and the builtin matrix runs its entry once it is granted.

Milestone 4: ``fs`` goes through the trusted facade with the ``--unsafe``
roots and portable write limits (``test_isolated_run_fs.py``); the builtin
matrix runs each of its seven builtins once.

Milestone 5: ``http`` goes through ``--unsafe``'s validated client in trusted
worker code (``test_isolated_run_http.py``), on POSIX only: Windows refuses
it before launch. The builtin matrix runs its three builtins against a local
server.

Runs here go through real ``geno run`` subprocesses unless a test needs to
observe the parent in-process. Only a real worker applies its restricted
builtins to the runtime prelude, so every gated builtin with a host binding
runs once through one (the builtin matrix).
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from geno.capabilities import (
    DEFAULT_ALLOWED_CAPABILITIES,
    ISOLATED_RUN_CAPABILITIES,
    ISOLATED_RUN_REFUSED_CAPABILITIES,
    ISOLATED_RUN_WINDOWS_UNAVAILABLE,
    KNOWN_CAPABILITIES,
    isolated_run_capabilities,
    isolated_run_refusal,
)
from geno.isolated_host import (
    IsolatedEnv,
    IsolatedRunClock,
    IsolatedRunPolicy,
    isolated_run_policy_for,
)

REGEX_DENIED = "Capability denied: 'regex_match' requires the 'regex' capability"
PRINT_DENIED = "Capability denied: 'print' requires the 'print' capability"

# A regex program whose example also needs the regex engine.
REGEX_PROGRAM = """func first_word(text: String) -> Option[String]
    example "hello world" -> Some("hello")
    return regex_match(pattern: "[a-z]+", text: text)
end func

func main() -> Unit
    match first_word("geno lang") with
        | Some(word) -> print(word)
        | None -> print("none")
    end match
end func
"""

# The defaults: clock, print and random all work without --cap.
DEFAULTS_PROGRAM = """func main() -> Unit
    let start = clock_now()
    let roll = random_int(min: 1, max: 6)
    sleep_ms(1)
    print(f"clock {clock_now() >= start}")
    print(f"random {roll >= 1 and roll <= 6}")
end func
"""

# Capabilities whose isolated milestone has not landed yet.
NOT_YET_ISOLATED = sorted(
    KNOWN_CAPABILITIES - isolated_run_capabilities() - ISOLATED_RUN_REFUSED_CAPABILITIES
)


def _geno(
    *args: str, cwd: Path, timeout: float = 60, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "geno", "run", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
    )


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# regex


def test_print_and_regex_grant_runs_isolated_with_a_regex_example(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "words.geno", REGEX_PROGRAM)

    result = _geno("--cap", "print,regex", "words.geno", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "geno\n"
    assert result.stderr == ""


def test_print_grant_denies_regex_with_the_isolated_hint(tmp_path: Path) -> None:
    _write(tmp_path, "words.geno", REGEX_PROGRAM)

    result = _geno("--no-check-examples", "--cap", "print", "words.geno", cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert f"Runtime Error: {REGEX_DENIED}\n" in result.stderr
    assert result.stderr.endswith(
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        "  geno run --cap print,regex words.geno\n"
    )
    assert "--unsafe" not in result.stderr


def test_regex_example_denied_in_the_example_phase(tmp_path: Path) -> None:
    # The example runs before `main`, so its denial is the run's failure.
    _write(tmp_path, "words.geno", REGEX_PROGRAM)

    result = _geno("--cap", "print", "words.geno", cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert f"Runtime Error: {REGEX_DENIED}\n" in result.stderr
    assert result.stderr.endswith("  geno run --cap print,regex words.geno\n")


def test_no_cap_keeps_the_default_grant_and_denies_regex(tmp_path: Path) -> None:
    _write(tmp_path, "defaults.geno", DEFAULTS_PROGRAM)
    _write(tmp_path, "words.geno", REGEX_PROGRAM)

    defaults = _geno("defaults.geno", cwd=tmp_path)
    regex = _geno("--no-check-examples", "words.geno", cwd=tmp_path)

    assert defaults.returncode == 0, defaults.stderr
    assert defaults.stdout == "clock true\nrandom true\n"
    assert regex.returncode == 1
    assert REGEX_DENIED in regex.stderr
    assert regex.stderr.endswith(
        "  geno run --cap clock,print,random,regex words.geno\n"
    )


def test_explicit_grant_is_exact_and_restores_no_default(tmp_path: Path) -> None:
    # `--cap regex` alone denies print, in examples and in `main` alike.
    source = """func shout(text: String) -> String
    example "hi" -> "HI"
    print(text)
    return to_upper(text)
end func

func main() -> Unit
    print(shout("geno"))
end func
"""
    _write(tmp_path, "shout.geno", source)

    in_example = _geno("--cap", "regex", "shout.geno", cwd=tmp_path)
    in_main = _geno("--no-check-examples", "--cap", "regex", "shout.geno", cwd=tmp_path)

    for result in (in_example, in_main):
        assert result.returncode == 1
        assert result.stdout == ""
        assert PRINT_DENIED in result.stderr
        assert result.stderr.endswith(
            "Hint: grant it with --cap; the program stays process-isolated:\n"
            "  geno run --cap print,regex shout.geno\n"
        )
    # The example phase is interpreted, so its trace names the call site.
    assert "shout.geno:3:5" in in_example.stderr


def test_example_sleep_uses_the_clock_grant(tmp_path: Path) -> None:
    # Examples get the run's grant with its host behaviour: `sleep_ms` is real.
    source = """func pause(ms: Int) -> Int
    example 1 -> 1
    sleep_ms(ms)
    return ms
end func

func main() -> Unit
    print(pause(1))
end func
"""
    _write(tmp_path, "pause.geno", source)

    result = _geno("pause.geno", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "1\n"


# ---------------------------------------------------------------------------
# Pre-launch refusals


def _refusal_lines(capabilities: list[str]) -> str:
    lines = []
    for capability in sorted(capabilities):
        message = isolated_run_refusal(capability)
        if message is not None:
            lines.append(f"Error: {message}\n")
    assert lines
    return "".join(lines)


@pytest.mark.parametrize(
    "capabilities",
    [
        ["process"],
        ["serve"],
        ["print", "process", "serve"],
        *([capability] for capability in NOT_YET_ISOLATED),
    ],
    ids=lambda caps: ",".join(caps),
)
def test_refused_grants_exit_before_any_worker_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    capabilities: list[str],
) -> None:
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    launched: list[Any] = []
    monkeypatch.setattr(
        ProcessSandbox, "_run_worker", lambda *args, **kwargs: launched.append(args)
    )
    source = _write(
        tmp_path, "main.geno", 'func main() -> Unit\n    print("ESCAPED")\nend func\n'
    )

    with pytest.raises(SystemExit) as excinfo:
        run_file(str(source), capabilities=set(capabilities))

    assert excinfo.value.code == 1
    assert launched == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == _refusal_lines(capabilities)


@pytest.mark.parametrize("capability", sorted(ISOLATED_RUN_REFUSED_CAPABILITIES))
def test_process_and_serve_refusals_name_unsafe(capability: str) -> None:
    message = isolated_run_refusal(capability)

    assert message is not None
    assert message.startswith(
        f"--cap {capability} is not available in the process-isolated run mode: "
    )
    assert message.endswith("Use --unsafe --cap ... for trusted code.")


# Not granted on any platform yet. A capability that has landed but is still
# refused on one platform gets a message naming that platform instead
# (test_windows_refusal_names_the_platform_once_a_capability_lands).
NOT_LANDED_ANYWHERE = sorted(
    KNOWN_CAPABILITIES - ISOLATED_RUN_CAPABILITIES - ISOLATED_RUN_REFUSED_CAPABILITIES
)


@pytest.mark.parametrize("os_name", ["posix", "nt"])
@pytest.mark.parametrize("capability", NOT_LANDED_ANYWHERE)
def test_not_yet_available_refusal_names_the_alternatives(
    capability: str, os_name: str
) -> None:
    assert isolated_run_refusal(capability, os_name) == (
        f"--cap {capability} is not yet available in the process-isolated run "
        "mode. Use --unsafe --cap ... for trusted code, or --json --cap ... for "
        "embedding API execution."
    )


# ---------------------------------------------------------------------------
# What the parent sends


def test_parent_sends_the_grant_in_the_worker_config_and_args_in_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    seen: dict[str, Any] = {}
    original_execute = ProcessSandbox.execute_geno_request
    original_run_worker = ProcessSandbox._run_worker

    def spy_execute(self: ProcessSandbox, request: Any, policy: Any = None) -> Any:
        seen["request"] = request
        seen["policy"] = policy
        return original_execute(self, request, policy)

    def spy_run_worker(
        self: ProcessSandbox,
        cmd: Any,
        code: Any,
        config_overrides: Any = None,
        **kwargs: Any,
    ) -> Any:
        seen.setdefault("launches", []).append(config_overrides)
        seen.setdefault("pass_fds", []).append(kwargs.get("pass_fds", ()))
        return original_run_worker(self, cmd, code, config_overrides, **kwargs)

    monkeypatch.setattr(ProcessSandbox, "execute_geno_request", spy_execute)
    monkeypatch.setattr(ProcessSandbox, "_run_worker", spy_run_worker)
    source = _write(tmp_path, "words.geno", REGEX_PROGRAM)

    status = run_file(
        str(source),
        capabilities={"print", "regex"},
        timeout=20.0,
        program_args=["in put.txt", "-v"],
    )

    assert status is None
    assert capsys.readouterr().out == "geno\n"
    policy = seen["policy"]
    assert isinstance(policy, IsolatedRunPolicy)
    assert policy.capabilities == frozenset({"print", "regex"})
    assert policy.deadline_seconds == 20.0
    assert seen["request"]["program_args"] == ["in put.txt", "-v"]
    (overrides,) = seen["launches"]
    assert overrides["worker_mode"] == "geno_cli"
    assert overrides["run_policy"] == policy.to_config()
    # Without `stdin` the worker inherits no program-input handle.
    assert seen["pass_fds"] == [()]
    # The grant never travels in the request the worker parses later.
    assert "capabilities" not in seen["request"]


def test_no_cap_sends_the_default_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    seen: list[Any] = []
    original_execute = ProcessSandbox.execute_geno_request

    def spy_execute(self: ProcessSandbox, request: Any, policy: Any = None) -> Any:
        seen.append(policy)
        return original_execute(self, request, policy)

    monkeypatch.setattr(ProcessSandbox, "execute_geno_request", spy_execute)
    source = _write(
        tmp_path, "main.geno", "func main() -> Int\n    return 3\nend func\n"
    )

    assert run_file(str(source)) == 3
    (policy,) = seen
    assert policy.capabilities == DEFAULT_ALLOWED_CAPABILITIES
    assert policy.to_config() == {
        **IsolatedRunPolicy.default().to_config(),
        "deadline_seconds": 30.0,
    }


@pytest.mark.parametrize("roots", ["", os.pathsep])
def test_empty_fs_roots_does_not_affect_a_run_without_fs(
    tmp_path: Path, roots: str
) -> None:
    # Host policy is read only for the capability it configures (0.6 behaviour).
    _write(
        tmp_path, "hello.geno", 'func main() -> Unit\n    print("hello")\nend func\n'
    )
    env = {**os.environ, "GENO_FS_ROOTS": roots, "GENO_HTTP_ALLOW_PRIVATE": "1"}

    plain = _geno("hello.geno", cwd=tmp_path, env=env)
    granted = _geno("--cap", "print,regex", "hello.geno", cwd=tmp_path, env=env)

    for result in (plain, granted):
        assert result.returncode == 0, result.stderr
        assert result.stdout == "hello\n"


def test_policy_without_fs_or_http_keeps_their_settings_neutral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENO_FS_ROOTS", "")
    monkeypatch.setenv("GENO_FS_READ_ONLY", "1")
    monkeypatch.setenv("GENO_FS_ALLOW_ABSOLUTE", "1")
    monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")

    policy = isolated_run_policy_for({"print", "regex"}, timeout=5.0)

    config = policy.to_config()
    assert config["fs_read_only"] is False
    assert config["fs_configured_roots"] is None
    assert config["fs_allow_absolute"] is False
    assert config["max_total_write_bytes"] == 0
    assert config["http_allow_private"] is False
    assert config["env_allowed_names"] == []
    assert config["stdin_fd"] is None


# ---------------------------------------------------------------------------
# Shared budgets across examples and main

NOISY_PROGRAM = """func noisy(n: Int) -> Int
    example 1 -> 1
    print("example output that is counted but never shown...........")
    return n
end func

func main() -> Unit
    print("main output that shares one budget with the examples......")
end func
"""


def test_example_output_counts_against_main_output_budget(tmp_path: Path) -> None:
    _write(tmp_path, "noisy.geno", NOISY_PROGRAM)

    shared = _geno("--max-output-length", "100", "noisy.geno", cwd=tmp_path)
    without_examples = _geno(
        "--no-check-examples", "--max-output-length", "100", "noisy.geno", cwd=tmp_path
    )
    roomy = _geno("--max-output-length", "200", "noisy.geno", cwd=tmp_path)

    assert shared.returncode == 1
    assert shared.stdout == ""
    assert "Output limit exceeded (100 characters)" in shared.stderr
    # Each line alone fits; only their sum exceeds the limit.
    assert without_examples.returncode == 0, without_examples.stderr
    assert roomy.returncode == 0, roomy.stderr
    # Example output is counted, never shown.
    assert roomy.stdout == (
        "main output that shares one budget with the examples......\n"
    )


def test_one_deadline_spans_examples_and_main(tmp_path: Path) -> None:
    source = """func pause(ms: Int) -> Int
    example 1500 -> 1500
    sleep_ms(ms)
    return ms
end func

func main() -> Unit
    print(pause(1500))
end func
"""
    _write(tmp_path, "pause.geno", source)

    started = time.monotonic()
    result = _geno("--timeout", "2", "pause.geno", cwd=tmp_path)
    elapsed = time.monotonic() - started

    assert result.returncode == 1
    assert result.stdout == ""
    # Whichever bound fires (the example's cooperative one or the hard kill
    # in `main`), the message names the run's --timeout, never a remainder.
    assert result.stderr.strip().splitlines()[-1] == (
        "Limit Error: Execution timed out after 2.0 seconds"
    )
    assert elapsed < 2.0 + 15.0


def test_examples_run_under_the_remaining_deadline(tmp_path: Path) -> None:
    from geno.cli.run import _prepare_process_run
    from geno.sandbox import TimeoutError as SandboxTimeout

    source = _write(
        tmp_path,
        "pause.geno",
        """func pause(ms: Int) -> Int
    example 50 -> 50
    sleep_ms(ms)
    return ms
end func

func main() -> Unit
    print(pause(1))
end func
""",
    )
    request: dict[str, Any] = {
        "filename": str(source),
        "target": None,
        "check_examples": True,
        "timeout": 30.0,
        "max_recursion_depth": 100,
        "max_output_length": 1000,
        "max_collection_size": 10_000,
        "max_integer_bits": 33_219,
        "program_args": [],
    }
    policy = IsolatedRunPolicy(
        capabilities=DEFAULT_ALLOWED_CAPABILITIES, deadline_seconds=30.0
    )
    # The run started long ago: almost nothing of its deadline is left.
    spent = IsolatedRunClock(30.0, started_at=time.monotonic() - 29.99)

    with pytest.raises(SandboxTimeout) as excinfo:
        _prepare_process_run(request, policy, spent)

    # The bound is the remainder, but the message keeps the run's --timeout.
    # Where the deadline lands decides whether a location follows: inside
    # sleep_ms there is none, at an interpreter step the node's is appended.
    message = str(excinfo.value)
    assert message == "Execution timed out after 30.0 seconds" or message.startswith(
        f"Execution timed out after 30.0 seconds at {source}:"
    ), message

    fresh = _prepare_process_run(request, policy, IsolatedRunClock(30.0))
    assert fresh["host_table"] == {}
    assert fresh["example_output_length"] == 0
    assert fresh["runtime_capabilities"] == sorted(DEFAULT_ALLOWED_CAPABILITIES)


def test_prepare_counts_example_output(tmp_path: Path) -> None:
    from geno.cli.run import _prepare_process_run

    source = _write(tmp_path, "noisy.geno", NOISY_PROGRAM)
    request = {
        "filename": str(source),
        "target": None,
        "check_examples": True,
        "timeout": 30.0,
        "max_recursion_depth": 100,
        "max_output_length": 1000,
        "max_collection_size": 10_000,
        "max_integer_bits": 33_219,
    }

    prepared = _prepare_process_run(request)

    line = "example output that is counted but never shown...........\n"
    assert prepared["example_output_length"] == len(line)


def test_prepare_rejects_malformed_program_args(tmp_path: Path) -> None:
    from geno.cli.run import _prepare_process_run

    source = _write(
        tmp_path, "main.geno", "func main() -> Unit\n    return ()\nend func\n"
    )
    request = {
        "filename": str(source),
        "target": None,
        "check_examples": False,
        "timeout": 30.0,
        "max_recursion_depth": 100,
        "max_output_length": 1000,
        "max_collection_size": 10_000,
        "max_integer_bits": 33_219,
        "program_args": ["ok", 3],
    }

    with pytest.raises(ValueError, match="program arguments"):
        _prepare_process_run(request)
    with pytest.raises(TypeError, match="policy"):
        _prepare_process_run({**request, "program_args": []}, policy=object())  # type: ignore[arg-type]


def test_max_steps_warning_is_unchanged(tmp_path: Path) -> None:
    _write(tmp_path, "words.geno", REGEX_PROGRAM)

    result = _geno(
        "--max-steps", "10", "--cap", "print,regex", "words.geno", cwd=tmp_path
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "geno\n"
    assert result.stderr == (
        "Warning: --max-steps is ignored in compiled mode. "
        "Use --unsafe or --json for step limiting.\n"
    )


def test_no_check_examples_skips_the_example_phase(tmp_path: Path) -> None:
    source = """func shout(text: String) -> String
    example "hi" -> "HI"
    print(text)
    return to_upper(text)
end func

func main() -> Int
    return length(shout("geno"))
end func
"""
    _write(tmp_path, "shout.geno", source)

    # `print` is denied; only skipping the example phase avoids calling it.
    checked = _geno("--cap", "regex", "shout.geno", cwd=tmp_path)
    skipped = _geno(
        "--no-check-examples", "--cap", "print,regex", "shout.geno", cwd=tmp_path
    )

    assert checked.returncode == 1
    assert PRINT_DENIED in checked.stderr
    assert skipped.returncode == 4, skipped.stderr
    assert skipped.stdout == "geno\n"


# ---------------------------------------------------------------------------
# Platform matrix


def test_platform_matrix() -> None:
    """Pin which capabilities the isolated lane grants on each platform."""
    assert (
        frozenset({"clock", "env", "fs", "http", "print", "random", "regex"})
        == ISOLATED_RUN_CAPABILITIES
    )
    assert frozenset({"process", "serve"}) == ISOLATED_RUN_REFUSED_CAPABILITIES
    assert frozenset({"stdin", "http"}) == ISOLATED_RUN_WINDOWS_UNAVAILABLE
    assert isolated_run_capabilities("posix") == frozenset(
        {"clock", "env", "fs", "http", "print", "random", "regex"}
    )
    assert isolated_run_capabilities("nt") == frozenset(
        {"clock", "env", "fs", "print", "random", "regex"}
    )
    assert isolated_run_capabilities() == isolated_run_capabilities(os.name)
    for os_name in ("posix", "nt"):
        for capability in sorted(KNOWN_CAPABILITIES):
            granted = capability in isolated_run_capabilities(os_name)
            assert (isolated_run_refusal(capability, os_name) is None) == granted
    # Nothing the lane refuses everywhere is ever granted on one platform.
    assert not ISOLATED_RUN_REFUSED_CAPABILITIES & ISOLATED_RUN_CAPABILITIES
    assert ISOLATED_RUN_CAPABILITIES <= KNOWN_CAPABILITIES


def test_stdin_stays_refused_pending_its_platform_gate() -> None:
    """Milestone 3 is implemented but not enabled (the proposal's stdin gate).

    Proposal 0013 keeps isolated ``stdin`` refused until handle setup,
    inheritance, timeout and interruption cleanup are validated on Linux,
    macOS and Windows. Without the project lead's sign-off on a POSIX-only
    deviation, it stays refused on every platform, before launch, with the
    rollout message; nothing but this constant enables it.
    """
    assert "stdin" not in ISOLATED_RUN_CAPABILITIES
    for os_name in ("posix", "nt"):
        assert "stdin" not in isolated_run_capabilities(os_name)
        assert isolated_run_refusal("stdin", os_name) == (
            "--cap stdin is not yet available in the process-isolated run mode. "
            "Use --unsafe --cap ... for trusted code, or --json --cap ... for "
            "embedding API execution."
        )


def test_windows_refuses_stdin_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Simulated Windows: ``--cap stdin`` never reaches a worker."""
    from geno import capabilities as capabilities_module
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    launched: list[Any] = []
    monkeypatch.setattr(
        ProcessSandbox, "_run_worker", lambda *args, **kwargs: launched.append(args)
    )
    real_refusals = capabilities_module.isolated_run_refusals
    monkeypatch.setattr(
        "geno.cli.run.isolated_run_refusals",
        lambda caps, os_name=None: real_refusals(caps, "nt"),
    )
    source = _write(
        tmp_path, "main.geno", 'func main() -> Unit\n    print("ESCAPED")\nend func\n'
    )

    with pytest.raises(SystemExit) as excinfo:
        run_file(str(source), capabilities={"print", "stdin"})

    assert excinfo.value.code == 1
    assert launched == []
    captured = capsys.readouterr()
    assert captured.out == ""
    message = isolated_run_refusal("stdin", "nt")
    assert message is not None
    assert captured.err == f"Error: {message}\n"
    # Windows stays refused even once POSIX is enabled.
    assert "stdin" in ISOLATED_RUN_WINDOWS_UNAVAILABLE


def test_windows_refuses_http_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Simulated Windows: ``--cap http`` never reaches a worker.

    The proposal's HTTP gate has no Windows evidence yet, so the lane keeps
    ``http`` refused there, with the Windows wording, while POSIX grants it.
    """
    from geno import capabilities as capabilities_module
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    launched: list[Any] = []
    monkeypatch.setattr(
        ProcessSandbox, "_run_worker", lambda *args, **kwargs: launched.append(args)
    )
    real_refusals = capabilities_module.isolated_run_refusals
    monkeypatch.setattr(
        "geno.cli.run.isolated_run_refusals",
        lambda caps, os_name=None: real_refusals(caps, "nt"),
    )
    source = _write(
        tmp_path, "main.geno", 'func main() -> Unit\n    print("ESCAPED")\nend func\n'
    )

    with pytest.raises(SystemExit) as excinfo:
        run_file(str(source), capabilities={"http", "print"})

    assert excinfo.value.code == 1
    assert launched == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Error: --cap http is not yet available in the process-isolated run mode "
        "on Windows. Use --unsafe --cap ... for trusted code, or --json --cap ... "
        "for embedding API execution.\n"
    )
    assert isolated_run_refusal("http", "posix") is None
    # A worker on Windows refuses an http policy too, whatever its parent sent.
    with pytest.raises(ValueError, match="'http' is not available"):
        IsolatedRunPolicy(capabilities=frozenset({"http", "print"})).validate("nt")


def test_windows_refusal_names_the_platform_once_a_capability_lands() -> None:
    # A Windows-unavailable capability that has not landed anywhere yet gets
    # the ordinary rollout message; once landed, the message names Windows.
    for capability in sorted(ISOLATED_RUN_WINDOWS_UNAVAILABLE):
        message = isolated_run_refusal(capability, "nt")
        assert message is not None
        on_windows = " on Windows." in message
        assert on_windows == (capability in ISOLATED_RUN_CAPABILITIES)


# ---------------------------------------------------------------------------
# env: program arguments, the exact-name allowlist and issue #68

ENV_POLICY_DENIED = "is not allowed by the host env policy"

# Issue #68: a program whose entry point needs only its arguments.
ARGS_PROGRAM = """func main() -> Unit
    print(join(cli_args(), ","))
end func
"""


def _without_geno_vars(**extra: str) -> dict[str, str]:
    """The caller's environment for a child `geno run`, minus worker state."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GENO_SANDBOX_CONFIG", "GENO_CLI_ARGS"}
    }
    env.update(extra)
    return env


def test_issue_68_cli_args_runs_isolated(tmp_path: Path) -> None:
    _write(tmp_path, "main.geno", ARGS_PROGRAM)

    result = _geno(
        "--cap", "env,print", "main.geno", "--", "hello", "world", cwd=tmp_path
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "hello,world\n"
    assert result.stderr == ""


def test_issue_68_run_goes_through_one_isolated_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from geno.cli import run as run_module
    from geno.sandbox import ProcessSandbox

    def parent_frontend(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the parent resolved the program itself")

    launches: list[Any] = []
    original_run_worker = ProcessSandbox._run_worker

    def spy_run_worker(self: ProcessSandbox, *args: Any, **kwargs: Any) -> Any:
        launches.append(args)
        return original_run_worker(self, *args, **kwargs)

    monkeypatch.setattr(run_module, "_resolve_run_program", parent_frontend)
    monkeypatch.setattr(ProcessSandbox, "_run_worker", spy_run_worker)
    source = _write(tmp_path, "main.geno", ARGS_PROGRAM)

    status = run_module.run_file(
        str(source), capabilities={"env", "print"}, program_args=["hello", "world"]
    )

    assert status is None
    assert capsys.readouterr().out == "hello,world\n"
    (launch,) = launches
    config_overrides = launch[2]
    assert config_overrides["worker_mode"] == "geno_cli"
    assert config_overrides["run_policy"]["capabilities"] == ["env", "print"]


def test_issue_68_grant_keeps_the_hard_timeout(tmp_path: Path) -> None:
    source = """func main() -> Unit
    print(join(cli_args(), ","))
    var i = 0
    while i >= 0 do
        i = i + 1
    end while
end func
"""
    _write(tmp_path, "busy.geno", source)

    started = time.monotonic()
    result = _geno(
        "--timeout",
        "2",
        "--cap",
        "env,print",
        "busy.geno",
        "--",
        "hello",
        "world",
        cwd=tmp_path,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 1
    # Output printed before the kill is kept, then the limit is reported.
    assert result.stdout == "hello,world\n"
    assert result.stderr.strip().splitlines()[-1] == (
        "Limit Error: Execution timed out after 2.0 seconds"
    )
    assert elapsed < 2.0 + 15.0


def test_issue_68_grant_keeps_the_memory_limit(tmp_path: Path) -> None:
    from geno.execution_limits import DEFAULT_PROCESS_MAX_MEMORY_BYTES

    # Doubling a string reaches gigabytes long before the collection limit.
    source = """func main() -> Unit
    print(join(cli_args(), ","))
    var text = "x"
    var i = 0
    while i < 32 do
        text = text + text
        i = i + 1
    end while
    print(length(text))
end func
"""
    _write(tmp_path, "big.geno", source)

    result = _geno(
        "--max-memory-bytes",
        str(DEFAULT_PROCESS_MAX_MEMORY_BYTES),
        "--max-collection-size",
        str(8 * 1024**3),
        "--cap",
        "env,print",
        "big.geno",
        "--",
        "hello",
        "world",
        cwd=tmp_path,
    )

    assert result.returncode == 1
    assert result.stdout == "hello,world\n"
    if sys.platform != "win32":
        # Windows reports its Job Object limit in its own words.
        assert "memory limit exceeded" in result.stderr


# Every gated builtin whose isolated implementation reaches the host through
# a binding runs once through a real worker: only there does the runtime
# prelude execute under the worker's restricted builtins. Each later
# milestone adds its capability's builtins here.
HOST_BINDING_CAPABILITIES = frozenset({"env", "stdin", "fs", "http"})

# Host-binding capabilities whose isolated implementation exists. `stdin` is
# implemented but stays refused until its platform gate passes; its entries
# run once the lane grants it.
IMPLEMENTED_HOST_CAPABILITIES = frozenset({"env", "fs", "http", "stdin"})

MATRIX_VALUE = "matrix value"

BUILTIN_MATRIX: dict[str, tuple[list[str], str, list[str], str]] = {
    "env_get": (
        ["--cap", "env,print", "--allow-env", "GENO_MATRIX_VALUE"],
        """func main() -> Unit
    match env_get("GENO_MATRIX_VALUE") with
        | Some(value) -> print(value)
        | None -> print("unset")
    end match
end func
""",
        [],
        f"{MATRIX_VALUE}\n",
    ),
    "env_get_or": (
        ["--cap", "env,print", "--allow-env", "GENO_MATRIX_VALUE"],
        """func main() -> Unit
    print(env_get_or("GENO_MATRIX_VALUE", "fallback"))
end func
""",
        [],
        f"{MATRIX_VALUE}\n",
    ),
    "cli_args": (["--cap", "env,print"], ARGS_PROGRAM, ["a b", "-v"], "a b,-v\n"),
    "stdin_read_all": (
        ["--cap", "print,stdin"],
        """func main() -> Unit
    match stdin_read_all() with
        | Ok(text) -> print(text)
        | Err(error) -> print("Err " + error)
    end match
end func
""",
        [],
        f"{MATRIX_VALUE}\n",
    ),
}

# The filesystem builtins: each runs once through the worker's facade, so no
# worker branch may import `os` or `stat`, call `open` or name `bytes`.
_FS_MATRIX_FLAGS = ["--cap", "fs,print"]


def _fs_matrix_program(body: str) -> str:
    return f"func main() -> Unit\n{body}end func\n"


def _fs_matrix_match(call: str, ok: str) -> str:
    return (
        f"    match {call} with\n"
        f"        | Ok(value) -> print({ok})\n"
        '        | Err(error) -> print("Err " + error)\n'
        "    end match\n"
    )


BUILTIN_MATRIX.update(
    {
        "fs_read_text": (
            _FS_MATRIX_FLAGS,
            _fs_matrix_program(_fs_matrix_match('fs_read_text("data.txt")', "value")),
            [],
            f"{MATRIX_VALUE}\n",
        ),
        "fs_write_text": (
            _FS_MATRIX_FLAGS,
            _fs_matrix_program(
                _fs_matrix_match(
                    'fs_write_text("out.txt", "written")',
                    'fs_read_text("out.txt").unwrap()',
                )
            ),
            [],
            "written\n",
        ),
        "fs_list_dir": (
            _FS_MATRIX_FLAGS,
            _fs_matrix_program(
                _fs_matrix_match('fs_list_dir("listed")', 'join(value, ",")')
            ),
            [],
            "a.txt,b.txt\n",
        ),
        "fs_exists": (
            _FS_MATRIX_FLAGS,
            _fs_matrix_program(
                '    print(to_string(fs_exists("data.txt")))\n'
                '    print(to_string(fs_exists("absent.txt")))\n'
            ),
            [],
            "true\nfalse\n",
        ),
        "fs_metadata": (
            _FS_MATRIX_FLAGS,
            _fs_matrix_program(
                _fs_matrix_match(
                    'fs_metadata("data.txt")', '"size " + to_string(value.size)'
                )
            ),
            [],
            f"size {len(MATRIX_VALUE)}\n",
        ),
        "fs_symlink_metadata": (
            _FS_MATRIX_FLAGS,
            _fs_matrix_program(
                _fs_matrix_match(
                    'fs_symlink_metadata("data.txt")',
                    '"size " + to_string(value.size)',
                )
            ),
            [],
            f"size {len(MATRIX_VALUE)}\n",
        ),
        "fs_canonicalize": (
            _FS_MATRIX_FLAGS,
            _fs_matrix_program(
                _fs_matrix_match('fs_canonicalize("data.txt")', "path_filename(value)")
            ),
            [],
            "data.txt\n",
        ),
    }
)

# The HTTP builtins: each runs once through the worker's binding against a
# local server (`@URL@` in the source), with the parent's private-network
# opt-in, so no worker branch may import `urllib`, name `bytes` or call
# `getattr` on a private name.
_HTTP_MATRIX_FLAGS = ["--cap", "http,print"]

BUILTIN_MATRIX.update(
    {
        "http_fetch": (
            _HTTP_MATRIX_FLAGS,
            _fs_matrix_program(_fs_matrix_match('http_fetch("@URL@/fetch")', "value")),
            [],
            "GET /fetch \n",
        ),
        "http_post": (
            _HTTP_MATRIX_FLAGS,
            _fs_matrix_program(
                _fs_matrix_match('http_post("@URL@/post", "{}")', "value")
            ),
            [],
            "POST /post {}\n",
        ),
        "http_request": (
            _HTTP_MATRIX_FLAGS,
            _fs_matrix_program(
                _fs_matrix_match(
                    'http_request(method: "PUT", url: "@URL@/request", '
                    'headers: [("X-Matrix", "1")], body: "sent")',
                    'to_string(value.status) + " " + value.body',
                )
            ),
            [],
            "200 PUT /request sent\n",
        ),
    }
)

# The standard input each matrix entry is run with.
MATRIX_STDIN = {"stdin_read_all": MATRIX_VALUE}

# Files every matrix run starts with, next to main.geno.
MATRIX_FILES = {
    "data.txt": MATRIX_VALUE,
    "listed/a.txt": "a",
    "listed/b.txt": "b",
}


def _matrix_capability(builtin: str) -> str:
    from geno.builtin_registry import CAPABILITY_MAP

    (capability,) = (
        capability
        for capability in HOST_BINDING_CAPABILITIES
        if builtin in CAPABILITY_MAP[capability]
    )
    return capability


def test_builtin_matrix_covers_every_landed_host_builtin() -> None:
    from geno.builtin_registry import CAPABILITY_MAP

    # Nothing is granted before its binding exists...
    assert ISOLATED_RUN_CAPABILITIES & HOST_BINDING_CAPABILITIES <= (
        IMPLEMENTED_HOST_CAPABILITIES
    )
    # ...and every implemented binding's builtins are in the matrix.
    implemented = {
        builtin
        for capability in IMPLEMENTED_HOST_CAPABILITIES
        for builtin in CAPABILITY_MAP[capability]
    }

    assert set(BUILTIN_MATRIX) == implemented


@pytest.mark.parametrize("builtin", sorted(BUILTIN_MATRIX))
def test_isolated_run_builtin_matrix(tmp_path: Path, builtin: str) -> None:
    capability = _matrix_capability(builtin)
    if capability not in isolated_run_capabilities():
        pytest.skip(f"the isolated lane does not grant {capability!r} yet")
    flags, source, program_args, expected = BUILTIN_MATRIX[builtin]
    for name, text in MATRIX_FILES.items():
        (tmp_path / name).parent.mkdir(exist_ok=True)
        _write(tmp_path, name, text)
    env = _without_geno_vars(GENO_MATRIX_VALUE=MATRIX_VALUE)

    with contextlib.ExitStack() as stack:
        if capability == "http":
            from geno.tests.test_isolated_run_http import echo, local_server

            server = stack.enter_context(local_server(echo))
            source = source.replace("@URL@", server.base)
            env["GENO_HTTP_ALLOW_PRIVATE"] = "1"
        _write(tmp_path, "main.geno", source)
        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", *flags, "main.geno", "--"]
            + program_args,
            input=MATRIX_STDIN.get(builtin, ""),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=tmp_path,
            env=env,
        )

    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
    assert "not allowed" not in result.stderr
    assert "Blocked:" not in result.stderr
    assert result.stderr == ""


def test_cli_args_needs_no_allowed_name_but_needs_env(tmp_path: Path) -> None:
    _write(tmp_path, "main.geno", ARGS_PROGRAM)

    granted = _geno("--cap", "env,print", "main.geno", cwd=tmp_path)
    denied = _geno("--cap", "print", "main.geno", "--", "a", cwd=tmp_path)

    assert granted.returncode == 0, granted.stderr
    assert granted.stdout == "\n"
    assert denied.returncode == 1
    assert denied.stdout == ""
    assert (
        "Runtime Error: Capability denied: 'cli_args' requires the 'env' "
        "capability\n" in denied.stderr
    )
    assert denied.stderr.endswith(
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        "  geno run --cap env,print main.geno -- a\n"
    )


def test_cli_args_never_reads_the_internal_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # GENO_CLI_ARGS still reaches the worker's environment for other modes,
    # but the isolated `cli_args` reads only the parent's request.
    from geno.cli.run import run_file

    monkeypatch.setenv("GENO_CLI_ARGS", json.dumps(["forged"]))
    source = _write(tmp_path, "main.geno", ARGS_PROGRAM)

    assert run_file(str(source), capabilities={"env", "print"}) is None
    assert capsys.readouterr().out == "\n"


ENV_PROGRAM = """func main() -> Unit
    match env_get("HOME") with
        | Some(home) -> print("HOME " + home)
        | None -> print("HOME unset")
    end match
    print(env_get_or("GENO_SANDBOX_CONFIG", "config unset"))
    print(env_get_or("GENO_CLI_ARGS", "args variable unset"))
end func
"""


def test_allowed_names_read_the_callers_environment_not_the_workers(
    tmp_path: Path,
) -> None:
    # The worker runs with a temporary HOME, its own sandbox configuration
    # and GENO_CLI_ARGS; an allowed name reads only the caller's snapshot,
    # taken before the parent set GENO_CLI_ARGS.
    caller_home = str(tmp_path / "caller-home")
    _write(tmp_path, "main.geno", ENV_PROGRAM)

    result = _geno(
        "--cap",
        "env,print",
        "--allow-env",
        "HOME",
        "--allow-env",
        "GENO_SANDBOX_CONFIG",
        "--allow-env",
        "GENO_CLI_ARGS",
        "main.geno",
        "--",
        "an argument",
        cwd=tmp_path,
        env=_without_geno_vars(HOME=caller_home),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (f"HOME {caller_home}\nconfig unset\nargs variable unset\n")


@pytest.mark.parametrize(
    ("call", "builtin"),
    [
        (
            'match env_get("GENO_WP2_SECRET") with\n'
            '        | Some(value) -> print("ESCAPED " + value)\n'
            '        | None -> print("ESCAPED none")\n'
            "    end match",
            "env_get",
        ),
        ('print("ESCAPED " + env_get_or("GENO_WP2_SECRET", "default"))', "env_get_or"),
        # The caller lacks this one: still a denial, never a fallback.
        (
            'print("ESCAPED " + env_get_or("GENO_WP2_NEVER_SET", "default"))',
            "env_get_or",
        ),
    ],
)
def test_unlisted_names_are_an_env_policy_denial_with_an_allow_env_hint(
    tmp_path: Path, call: str, builtin: str
) -> None:
    name = "GENO_WP2_NEVER_SET" if "NEVER_SET" in call else "GENO_WP2_SECRET"
    _write(tmp_path, "main.geno", f"func main() -> Unit\n    {call}\nend func\n")

    result = _geno(
        "--cap",
        "env,print",
        "--allow-env",
        "HOME",
        "main.geno",
        cwd=tmp_path,
        env=_without_geno_vars(GENO_WP2_SECRET="top-secret-value"),
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "top-secret-value" not in result.stderr
    assert (
        f"Runtime Error: {builtin}: environment variable '{name}' "
        f"{ENV_POLICY_DENIED}\n" in result.stderr
    )
    assert result.stderr.endswith(
        f"Hint: '{name}' is not in this run's environment allowlist; allow it "
        "with --allow-env (the program can then read its value):\n"
        f"  geno run --cap env,print --allow-env HOME --allow-env {name} "
        "main.geno\n"
    )


def test_allowed_but_missing_names_keep_their_behaviour(tmp_path: Path) -> None:
    source = """func main() -> Unit
    match env_get("GENO_WP2_NEVER_SET") with
        | Some(value) -> print("set " + value)
        | None -> print("none")
    end match
    print(env_get_or("GENO_WP2_NEVER_SET", "the default"))
end func
"""
    _write(tmp_path, "main.geno", source)

    result = _geno(
        "--cap",
        "env,print",
        "--allow-env",
        "GENO_WP2_NEVER_SET",
        "main.geno",
        cwd=tmp_path,
        env=_without_geno_vars(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "none\nthe default\n"


@pytest.mark.skipif(os.name == "nt", reason="Windows folds variable names")
def test_posix_names_are_exact_and_case_sensitive(tmp_path: Path) -> None:
    source = """func main() -> Unit
    print(env_get_or("PATH", "unreachable"))
end func
"""
    _write(tmp_path, "main.geno", source)

    result = _geno(
        "--cap", "env,print", "--allow-env", "Path", "main.geno", cwd=tmp_path
    )

    assert result.returncode == 1
    assert f"env_get_or: environment variable 'PATH' {ENV_POLICY_DENIED}" in (
        result.stderr
    )


def test_only_allowed_present_variables_cross_to_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    monkeypatch.setenv("GENO_WP2_SECRET", "secret-value-never-sent")
    monkeypatch.setenv("GENO_WP2_ALLOWED", "allowed value")
    monkeypatch.delenv("GENO_WP2_MISSING", raising=False)
    seen: dict[str, Any] = {}
    original_execute = ProcessSandbox.execute_geno_request
    original_run_worker = ProcessSandbox._run_worker

    def spy_execute(self: ProcessSandbox, request: Any, policy: Any = None) -> Any:
        seen["request"] = request
        seen["policy"] = policy
        return original_execute(self, request, policy)

    def spy_run_worker(self: ProcessSandbox, *args: Any, **kwargs: Any) -> Any:
        _cmd, worker_input, config_overrides = args
        seen["worker_input"] = worker_input
        seen["config_overrides"] = config_overrides
        seen["worker_env"] = self._create_restricted_env(config_overrides)
        return original_run_worker(self, *args, **kwargs)

    monkeypatch.setattr(ProcessSandbox, "execute_geno_request", spy_execute)
    monkeypatch.setattr(ProcessSandbox, "_run_worker", spy_run_worker)
    source = _write(
        tmp_path,
        "main.geno",
        'func main() -> Unit\n    print(env_get_or("GENO_WP2_ALLOWED", "-"))\n'
        "end func\n",
    )

    status = run_file(
        str(source),
        capabilities={"env", "print"},
        allow_env=["GENO_WP2_ALLOWED", "GENO_WP2_MISSING"],
        program_args=["x"],
    )

    assert status is None
    assert capsys.readouterr().out == "allowed value\n"
    assert seen["request"]["env_snapshot"] == {"GENO_WP2_ALLOWED": "allowed value"}
    assert seen["request"]["program_args"] == ["x"]
    assert seen["policy"].env_allowed_names == frozenset(
        {"GENO_WP2_ALLOWED", "GENO_WP2_MISSING"}
    )
    assert seen["config_overrides"]["run_policy"]["env_allowed_names"] == [
        "GENO_WP2_ALLOWED",
        "GENO_WP2_MISSING",
    ]
    for blob in (
        json.dumps(seen["request"]),
        json.dumps(seen["config_overrides"]),
        seen["worker_input"],
        json.dumps(seen["worker_env"]),
    ):
        assert "secret-value-never-sent" not in blob
        assert "GENO_WP2_SECRET" not in blob


def test_no_snapshot_crosses_without_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    requests: list[Any] = []
    original_execute = ProcessSandbox.execute_geno_request

    def spy_execute(self: ProcessSandbox, request: Any, policy: Any = None) -> Any:
        requests.append(request)
        return original_execute(self, request, policy)

    monkeypatch.setattr(ProcessSandbox, "execute_geno_request", spy_execute)
    source = _write(
        tmp_path, "main.geno", 'func main() -> Unit\n    print("hi")\nend func\n'
    )

    assert run_file(str(source), capabilities={"print"}) is None
    assert run_file(str(source)) is None
    assert capsys.readouterr().out == "hi\nhi\n"
    for request in requests:
        assert "env_snapshot" not in request


EXAMPLE_ENV_PROGRAM = """func greeting() -> String
    example () -> "hi from the caller"
    return env_get_or("GENO_WP2_GREETING", "no greeting")
end func

func arg_count() -> Int
    example () -> 2
    return length(cli_args())
end func

func main() -> Unit
    print(greeting())
    print(arg_count())
end func
"""


def test_examples_and_main_share_one_env_snapshot(tmp_path: Path) -> None:
    _write(tmp_path, "main.geno", EXAMPLE_ENV_PROGRAM)
    env = _without_geno_vars(GENO_WP2_GREETING="hi from the caller")

    allowed = _geno(
        "--cap",
        "env,print",
        "--allow-env",
        "GENO_WP2_GREETING",
        "main.geno",
        "--",
        "a",
        "b",
        cwd=tmp_path,
        env=env,
    )
    denied = _geno(
        "--cap", "env,print", "main.geno", "--", "a", "b", cwd=tmp_path, env=env
    )

    assert allowed.returncode == 0, allowed.stderr
    assert allowed.stdout == "hi from the caller\n2\n"
    # The example phase is interpreted: same denial, at the call site, with
    # the same hint as a denial in `main`.
    assert denied.returncode == 1
    assert denied.stdout == ""
    assert "hi from the caller" not in denied.stderr
    assert (
        "Runtime Error: env_get_or: environment variable 'GENO_WP2_GREETING' "
        f"{ENV_POLICY_DENIED}\n" in denied.stderr
    )
    assert "main.geno:3:12" in denied.stderr
    assert denied.stderr.endswith(
        "  geno run --cap env,print --allow-env GENO_WP2_GREETING main.geno -- a b\n"
    )


def test_env_grant_with_a_print_example_hints_the_full_grant(tmp_path: Path) -> None:
    # `--cap env` is exact: the example's `print` is denied, and the hint
    # names the whole grant to use (proposal 0013's example).
    source = """func loud(text: String) -> String
    example "hi" -> "hi"
    print(text)
    return text
end func

func main() -> Unit
    print(loud(join(cli_args(), ",")))
end func
"""
    _write(tmp_path, "main.geno", source)

    result = _geno("--cap", "env", "main.geno", "--", "hello", cwd=tmp_path)

    assert result.returncode == 1
    assert "Capability denied: 'print' requires the 'print' capability" in (
        result.stderr
    )
    assert result.stderr.endswith(
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        "  geno run --cap env,print main.geno -- hello\n"
    )


def test_cli_args_parity_with_unsafe(tmp_path: Path) -> None:
    _write(tmp_path, "main.geno", ARGS_PROGRAM)
    args = ("main.geno", "--", "in put.txt", "-v")

    isolated = _geno("--cap", "env,print", *args, cwd=tmp_path)
    direct = _geno("--unsafe", "--cap", "env,print", *args, cwd=tmp_path)

    assert isolated.returncode == direct.returncode == 0
    assert isolated.stdout == direct.stdout == "in put.txt,-v\n"
    assert isolated.stderr == direct.stderr == ""


# regex, program arguments and an allowed variable: builtins whose semantics
# match on every backend, so the isolated lane must agree with compiled
# Python and compiled JavaScript as well as with `--unsafe`.
BACKEND_PARITY_PROGRAM = """func first_number(text: String) -> Option[String]
    example "order 42 shipped" -> Some("42")
    example "no digits" -> None
    return regex_match("[0-9]+", text)
end func

func main() -> Unit
    let args = cli_args()
    print(length(args))
    print(join(args, "|"))
    match first_number(join(args, " ")) with
        | Some(n) -> print("number " + n)
        | None -> print("no number")
    end match
    print(regex_replace(pattern: "[aeiou]", replacement: "_", text: args[0]))
    match env_get("GENO_PARITY_NAME") with
        | Some(value) -> print("env " + value)
        | None -> print("env unset")
    end match
    print(env_get_or("GENO_PARITY_MISSING", "fallback"))
end func
"""


def test_regex_env_and_cli_args_match_every_backend(tmp_path: Path) -> None:
    """Isolated `--cap` output equals `--unsafe --cap`, compiled Python and Node."""
    import shutil

    from geno.compiler import compile_to_python
    from geno.js_compiler import compile_to_js

    _write(tmp_path, "main.geno", BACKEND_PARITY_PROGRAM)
    (tmp_path / "main.py").write_text(
        compile_to_python(BACKEND_PARITY_PROGRAM), encoding="utf-8"
    )
    (tmp_path / "main.js").write_text(
        compile_to_js(BACKEND_PARITY_PROGRAM), encoding="utf-8"
    )
    caps = ("--cap", "env,print,regex")
    program_args = ("--", "capabilities", "run 7", "-v")
    env = _without_geno_vars(GENO_PARITY_NAME="v1")
    env.pop("GENO_PARITY_MISSING", None)
    allow = ("--allow-env", "GENO_PARITY_NAME", "--allow-env", "GENO_PARITY_MISSING")
    lanes: dict[str, list[str]] = {
        "isolated": [
            sys.executable,
            "-m",
            "geno",
            "run",
            *caps,
            *allow,
            "main.geno",
            *program_args,
        ],
        "unsafe": [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--unsafe",
            *caps,
            "main.geno",
            *program_args,
        ],
        "python": [sys.executable, "main.py", *caps, *program_args],
    }
    node = shutil.which("node")
    if node is not None:
        lanes["js"] = [node, "main.js", *caps, *program_args]

    results = {
        lane: subprocess.run(
            argv, capture_output=True, text=True, timeout=60, cwd=tmp_path, env=env
        )
        for lane, argv in lanes.items()
    }

    expected = "3\ncapabilities|run 7|-v\nnumber 7\nc_p_b_l_t__s\nenv v1\nfallback\n"
    for lane, result in results.items():
        assert (result.stdout, result.stderr, result.returncode) == (
            expected,
            "",
            0,
        ), lane


def test_env_scope_differs_from_unsafe_as_documented(tmp_path: Path) -> None:
    # `--unsafe --cap env` exposes the caller's whole environment; the
    # isolated lane exposes only the names it allows, with the same values.
    caller_home = str(tmp_path / "caller-home")
    source = """func main() -> Unit
    match env_get("HOME") with
        | Some(home) -> print(home)
        | None -> print("HOME unset")
    end match
end func
"""
    _write(tmp_path, "main.geno", source)
    env = _without_geno_vars(HOME=caller_home)

    direct = _geno("--unsafe", "--cap", "env,print", "main.geno", cwd=tmp_path, env=env)
    isolated = _geno("--cap", "env,print", "main.geno", cwd=tmp_path, env=env)
    allowed = _geno(
        "--cap",
        "env,print",
        "--allow-env",
        "HOME",
        "main.geno",
        cwd=tmp_path,
        env=env,
    )

    assert direct.returncode == 0, direct.stderr
    assert direct.stdout == f"{caller_home}\n"
    assert isolated.returncode == 1
    assert f"env_get: environment variable 'HOME' {ENV_POLICY_DENIED}" in (
        isolated.stderr
    )
    assert allowed.returncode == 0, allowed.stderr
    assert allowed.stdout == direct.stdout


def test_prepare_validates_the_environment_snapshot(tmp_path: Path) -> None:
    from geno.cli.run import _prepare_process_run

    source = _write(tmp_path, "main.geno", ARGS_PROGRAM)
    request: dict[str, Any] = {
        "filename": str(source),
        "target": None,
        "check_examples": False,
        "timeout": 30.0,
        "max_recursion_depth": 100,
        "max_output_length": 1000,
        "max_collection_size": 10_000,
        "max_integer_bits": 33_219,
        "program_args": ["a"],
    }
    env_policy = IsolatedRunPolicy(
        capabilities=frozenset({"env", "print"}),
        env_allowed_names=frozenset({"HOME"}),
    )

    prepared = _prepare_process_run(
        {**request, "env_snapshot": {"HOME": "/home/caller"}}, env_policy
    )
    env = prepared["host_table"]["env"]
    assert isinstance(env, IsolatedEnv)
    assert env.program_args == ("a",)
    assert dict(env.environ) == {"HOME": "/home/caller"}

    for snapshot, policy in (
        # Only allowed names may cross.
        ({"SECRET": "x"}, env_policy),
        ({"HOME": 3}, env_policy),
        (["HOME"], env_policy),
        # And no snapshot at all without `env`.
        ({}, IsolatedRunPolicy.default()),
    ):
        with pytest.raises(ValueError, match="environment snapshot"):
            _prepare_process_run({**request, "env_snapshot": snapshot}, policy)
