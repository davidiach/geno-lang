"""Capability grants in the process-isolated ``geno run`` lane (proposal 0013).

Milestone 1: the default lane accepts ``--cap`` for the capabilities it
implements (``clock``, ``print``, ``random``, ``regex``), refuses ``process``,
``serve`` and every capability whose milestone has not landed before a worker
starts, and gives examples exactly the run's grant, one deadline and one
output budget. Runs here go through real ``geno run`` subprocesses unless a
test needs to observe the parent in-process.
"""

from __future__ import annotations

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


@pytest.mark.parametrize("capability", NOT_YET_ISOLATED)
def test_not_yet_available_refusal_names_the_alternatives(capability: str) -> None:
    assert isolated_run_refusal(capability) == (
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
        self: ProcessSandbox, cmd: Any, code: Any, config_overrides: Any = None
    ) -> Any:
        seen.setdefault("launches", []).append(config_overrides)
        return original_run_worker(self, cmd, code, config_overrides)

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
    assert str(excinfo.value) == "Execution timed out after 30.0 seconds"

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
    assert frozenset({"clock", "print", "random", "regex"}) == ISOLATED_RUN_CAPABILITIES
    assert frozenset({"process", "serve"}) == ISOLATED_RUN_REFUSED_CAPABILITIES
    assert frozenset({"stdin", "http"}) == ISOLATED_RUN_WINDOWS_UNAVAILABLE
    assert isolated_run_capabilities("posix") == frozenset(
        {"clock", "print", "random", "regex"}
    )
    assert isolated_run_capabilities("nt") == frozenset(
        {"clock", "print", "random", "regex"}
    )
    assert isolated_run_capabilities() == isolated_run_capabilities(os.name)
    for os_name in ("posix", "nt"):
        for capability in sorted(KNOWN_CAPABILITIES):
            granted = capability in isolated_run_capabilities(os_name)
            assert (isolated_run_refusal(capability, os_name) is None) == granted
    # Nothing the lane refuses everywhere is ever granted on one platform.
    assert not ISOLATED_RUN_REFUSED_CAPABILITIES & ISOLATED_RUN_CAPABILITIES
    assert ISOLATED_RUN_CAPABILITIES <= KNOWN_CAPABILITIES


def test_windows_refusal_names_the_platform_once_a_capability_lands() -> None:
    # A Windows-unavailable capability that has not landed anywhere yet gets
    # the ordinary rollout message; once landed, the message names Windows.
    for capability in sorted(ISOLATED_RUN_WINDOWS_UNAVAILABLE):
        message = isolated_run_refusal(capability, "nt")
        assert message is not None
        on_windows = " on Windows." in message
        assert on_windows == (capability in ISOLATED_RUN_CAPABILITIES)
