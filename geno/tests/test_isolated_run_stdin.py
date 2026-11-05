"""Standard input in the process-isolated ``geno run`` lane (proposal 0013).

Milestone 3: with ``--cap stdin`` the parent duplicates its standard input
for the worker alone to inherit (``pass_fds``); trusted code in the worker
reads it lazily, in bounded chunks, only when ``stdin_read_all`` asks, under
the run's one deadline, and examples and ``main`` share the stream with no
replay. Without the grant no program-input handle is inherited.

Proposal 0013 keeps isolated ``stdin`` refused until its stdin gate (handle
setup, inheritance, timeout and interruption cleanup on Linux, macOS *and*
Windows) passes. Without the project lead's sign-off on a POSIX-only
deviation it stays refused on every platform, so the tests that need a real
worker to read input skip while the lane does not grant it. The rest hold
either way: a denied or refused run consumes no input, no handle is
inherited without the grant, a worker refuses a ``stdin`` policy its own lane
does not grant, and the parent always closes its duplicate.

The binding itself (chunking, UTF-8 errors at stream offsets, the size
bound, end of input) is covered in-process by ``test_isolated_host.py``.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

# Imported at collection: the sandbox annotates its methods with
# subprocess.Popen, which tests patch, and the CLI's error printer binds
# sys.stderr as a default argument, which capsys replaces per test.
import geno.cli.run
import geno.sandbox  # noqa: F401
from geno.capabilities import isolated_run_capabilities

STDIN_ENABLED = "stdin" in isolated_run_capabilities()

needs_stdin = pytest.mark.skipif(
    not STDIN_ENABLED,
    reason="isolated stdin pending proposal 0013's stdin platform gate",
)
posix_only = pytest.mark.skipif(
    os.name == "nt", reason="POSIX pipes and shells (Windows refuses stdin)"
)

READ_TWICE = """func main() -> Unit
    match stdin_read_all() with
        | Ok(text) -> print("first [" + text + "]")
        | Err(error) -> print("Err " + error)
    end match
    match stdin_read_all() with
        | Ok(text) -> print("second [" + text + "]")
        | Err(error) -> print("Err " + error)
    end match
end func
"""

READ_ONCE = """func main() -> Unit
    match stdin_read_all() with
        | Ok(text) -> print("got [" + text + "]")
        | Err(error) -> print("Err " + error)
    end match
end func
"""

TIMEOUT_TAIL = "Limit Error: Execution timed out after {timeout} seconds"


def _write(tmp_path: Path, source: str, name: str = "main.geno") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _geno_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "geno", "run", *args]


def _spawn(*args: str, cwd: Path) -> subprocess.Popen[bytes]:
    """Start ``geno run`` with an input pipe the test keeps open."""
    return subprocess.Popen(
        _geno_command(*args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
    )


def _finish(process: subprocess.Popen[bytes]) -> None:
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()
    if process.poll() is None:
        process.kill()
        process.wait()


def _run(
    *args: str, cwd: Path, input: bytes = b"", timeout: float = 60
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        _geno_command(*args),
        input=input,
        capture_output=True,
        timeout=timeout,
        cwd=cwd,
    )


# ---------------------------------------------------------------------------
# Always: no input is consumed and no handle is inherited without a grant


def _run_then_cat(tmp_path: Path, flags: str) -> subprocess.CompletedProcess[str]:
    """``printf abc | { geno run FLAGS main.geno; echo exit=$?; cat; }``."""
    geno = " ".join(shlex.quote(part) for part in _geno_command())
    script = (
        f'printf abc | {{ {geno} {flags} main.geno 2>/dev/null; echo "exit=$?"; cat; }}'
    )
    return subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )


DENIED_READ = """func main() -> Unit
    print("before")
    match stdin_read_all() with
        | Ok(text) -> print("ESCAPED " + text)
        | Err(error) -> print("ESCAPED " + error)
    end match
end func
"""


@posix_only
def test_a_denied_read_consumes_no_input(tmp_path: Path) -> None:
    _write(tmp_path, DENIED_READ)

    result = _run_then_cat(tmp_path, "--cap print")

    assert result.stdout == "before\nexit=1\nabc"


@posix_only
@pytest.mark.skipif(STDIN_ENABLED, reason="stdin is granted: see the unused case")
def test_a_refused_stdin_grant_consumes_no_input(tmp_path: Path) -> None:
    _write(tmp_path, READ_ONCE)

    result = _run_then_cat(tmp_path, "--cap print,stdin")

    assert result.stdout == "exit=1\nabc"


@posix_only
@needs_stdin
def test_a_granted_but_unused_stdin_consumes_no_input(tmp_path: Path) -> None:
    _write(tmp_path, 'func main() -> Unit\n    print("ran")\nend func\n')

    result = _run_then_cat(tmp_path, "--cap print,stdin")

    assert result.stdout == "ran\nexit=0\nabc"


class _PopenSpy:
    """Record every worker launch, with what it inherited."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.processes: list[subprocess.Popen[Any]] = []
        self.pass_fds: list[tuple[int, ...]] = []
        real_popen = subprocess.Popen

        def spy(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
            process = real_popen(*args, **kwargs)
            self.processes.append(process)
            self.pass_fds.append(tuple(kwargs.get("pass_fds", ())))
            return process

        monkeypatch.setattr(subprocess, "Popen", spy)


def _assert_closed(fd: int) -> None:
    with pytest.raises(OSError):
        os.fstat(fd)


@pytest.mark.parametrize("grant", [None, {"print"}, {"print", "regex"}])
def test_no_program_input_handle_without_the_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    grant: set[str] | None,
) -> None:
    import geno.isolated_host as isolated_host
    from geno.cli.run import run_file

    duplicated: list[Any] = []
    monkeypatch.setattr(
        isolated_host, "duplicate_program_stdin", lambda *a: duplicated.append(a)
    )
    spy = _PopenSpy(monkeypatch)
    source = _write(tmp_path, 'func main() -> Unit\n    print("ran")\nend func\n')

    assert run_file(str(source), capabilities=grant) is None

    assert capsys.readouterr().out == "ran\n"
    assert duplicated == []
    assert spy.pass_fds == [()]


@posix_only
def test_a_worker_inherits_only_the_handles_it_is_passed() -> None:
    """``pass_fds`` is the only way a handle reaches a worker."""
    from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

    sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=30.0, strict=False))
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, b"handed over")
        os.close(write_end)
        probe = (
            "import os\n"
            "try:\n"
            f"    print(os.read({read_end}, 100).decode())\n"
            "except OSError as error:\n"
            "    print('closed', error.errno)\n"
        )
        command = [sys.executable, "-c", probe]

        _status, without, _stderr, _cut = sandbox._run_worker(command, "")
        _status, passed, _stderr, _cut = sandbox._run_worker(
            command, "", pass_fds=(read_end,)
        )

        assert without.strip().startswith("closed ")
        assert passed.strip() == "handed over"
    finally:
        os.close(read_end)


@pytest.mark.skipif(os.name == "posix", reason="non-POSIX workers only")
def test_handles_are_never_passed_to_a_non_posix_worker() -> None:
    from geno.sandbox import ProcessSandbox, ProcessSandboxConfig, SandboxError

    sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=30.0, strict=False))

    with pytest.raises(SandboxError, match="POSIX only"):
        sandbox._run_worker([sys.executable, "-c", "pass"], "", pass_fds=(3,))


@posix_only
@pytest.mark.skipif(STDIN_ENABLED, reason="the worker grants stdin")
def test_worker_refuses_a_stdin_policy_its_lane_does_not_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Even a parent that sends a stdin grant gets nothing run, and no leak.

    The parent's lane rules are patched to grant ``stdin``, so it duplicates
    its input and the worker inherits it; the worker applies its own rules,
    refuses the policy before reading any input, and the parent still closes
    its copy.
    """
    from geno import capabilities as capabilities_module
    from geno.cli import run as run_module

    # The printer's default stream is the sys.stderr of its import; print to
    # the one capsys installed for this test.
    print_runtime_error = run_module._print_runtime_error
    monkeypatch.setattr(
        run_module,
        "_print_runtime_error",
        lambda error: print_runtime_error(error, file=sys.stderr),
    )
    monkeypatch.setattr(
        capabilities_module,
        "ISOLATED_RUN_CAPABILITIES",
        capabilities_module.ISOLATED_RUN_CAPABILITIES | {"stdin"},
    )
    read_end, write_end = os.pipe()
    os.write(write_end, b"abc")
    os.close(write_end)
    spy = _PopenSpy(monkeypatch)
    with open(read_end, encoding="utf-8") as program_input:
        monkeypatch.setattr(sys, "stdin", program_input)
        source = _write(tmp_path, READ_ONCE)

        with pytest.raises(SystemExit) as excinfo:
            run_module.run_file(str(source), capabilities={"print", "stdin"})

        assert excinfo.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert (
            "invalid isolated run policy: capability 'stdin' is not available"
            in captured.err
        )
        ((inherited,),) = spy.pass_fds
        assert inherited >= 3
        _assert_closed(inherited)
        # Nothing read the input.
        assert program_input.read() == "abc"


# ---------------------------------------------------------------------------
# Real worker reads (only once the lane grants stdin)


@needs_stdin
def test_reads_the_input_then_nothing_after_its_end(tmp_path: Path) -> None:
    _write(tmp_path, READ_TWICE)

    piped = _run("--cap", "print,stdin", "main.geno", cwd=tmp_path, input=b"x\ny")
    empty = _run("--cap", "print,stdin", "main.geno", cwd=tmp_path)

    assert piped.returncode == 0, piped.stderr
    assert piped.stdout == b"first [x\ny]\nsecond []\n"
    assert piped.stderr == b""
    assert empty.returncode == 0, empty.stderr
    assert empty.stdout == b"first []\nsecond []\n"


@needs_stdin
def test_unavailable_input_is_an_err(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # pytest's own stdin has no file descriptor, so there is no handle.
    from geno.cli.run import run_file

    source = _write(tmp_path, READ_ONCE)

    assert run_file(str(source), capabilities={"print", "stdin"}) is None
    assert capsys.readouterr().out == "Err stdin is not available\n"


@needs_stdin
def test_invalid_utf8_and_text_match_unsafe(tmp_path: Path) -> None:
    _write(tmp_path, READ_ONCE)
    inputs = [
        "héllo wörld\n".encode() * 2000,
        b"a" * 9000 + b"\xff" + b"b" * 10,
        b"a" * 8191 + b"\xe2" + b"A",
        b"abc\xe2\x82",
    ]

    for data in inputs:
        isolated = _run("--cap", "print,stdin", "main.geno", cwd=tmp_path, input=data)
        unsafe = _run(
            "--unsafe", "--cap", "print,stdin", "main.geno", cwd=tmp_path, input=data
        )
        assert isolated.returncode == unsafe.returncode == 0, isolated.stderr
        assert isolated.stdout == unsafe.stdout


@needs_stdin
def test_over_limit_input_reports_the_size_at_detection(tmp_path: Path) -> None:
    """A documented difference: the size is counted when the limit is hit.

    ``--unsafe`` reads everything and reports the whole input's length; the
    isolated reader stops within one chunk past the limit.
    """
    _write(tmp_path, READ_ONCE)
    limit = 1000
    data = b"a" * (limit + 3 * 8192)

    isolated = _run(
        "--max-collection-size",
        str(limit),
        "--cap",
        "print,stdin",
        "main.geno",
        cwd=tmp_path,
        input=data,
    )

    assert isolated.returncode == 1
    assert isolated.stdout == b""
    message = isolated.stderr.decode().strip().splitlines()[0]
    prefix, _, rest = message.partition("String size exceeds limit (")
    assert prefix == "Runtime Error: "
    size, _, suffix = rest.partition(f" > {limit})")
    assert suffix == ""
    assert limit < int(size) <= limit + 8192


@needs_stdin
@posix_only
def test_never_ending_input_is_killed_at_the_deadline(tmp_path: Path) -> None:
    _write(tmp_path, READ_ONCE)

    started = time.monotonic()
    process = _spawn(
        "--timeout", "2", "--cap", "print,stdin", "main.geno", cwd=tmp_path
    )
    assert process.stdin is not None
    assert process.stdout is not None and process.stderr is not None
    try:
        # Never closed, so the input never ends (communicate() would close it).
        process.wait(timeout=60)
        elapsed = time.monotonic() - started
        stdout = process.stdout.read()
        stderr = process.stderr.read()
        assert process.returncode == 1
        assert stdout == b""
        assert stderr.decode().strip().splitlines()[-1] == TIMEOUT_TAIL.format(
            timeout=2.0
        )
        assert elapsed < 2.0 + 15.0
        # No process holds the input any more: the CLI exited, and the worker
        # and its inherited duplicate are gone, so the pipe has no reader.
        with pytest.raises(BrokenPipeError):
            os.write(process.stdin.fileno(), b"x")
    finally:
        _finish(process)


@needs_stdin
@posix_only
def test_slow_input_within_the_deadline_succeeds(tmp_path: Path) -> None:
    _write(tmp_path, READ_ONCE)
    process = _spawn(
        "--timeout", "30", "--cap", "print,stdin", "main.geno", cwd=tmp_path
    )
    assert process.stdin is not None

    def feed() -> None:
        assert process.stdin is not None
        for piece in (b"slow ", "ïnput".encode()[:1], "ïnput".encode()[1:]):
            process.stdin.write(piece)
            process.stdin.flush()
            time.sleep(0.5)
        process.stdin.close()

    # The writer alone ends the input (communicate() would close it at once).
    writer = threading.Thread(target=feed)
    writer.start()
    try:
        process.wait(timeout=60)
        writer.join()
        assert process.stdout is not None and process.stderr is not None
        stdout = process.stdout.read()
        stderr = process.stderr.read()
    finally:
        writer.join()
        _finish(process)

    assert process.returncode == 0, stderr
    assert stdout.decode() == "got [slow ïnput]\n"


def _blocked_input(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, int]:
    """Make the parent's stdin a pipe that never ends; return it and its writer."""
    read_end, write_end = os.pipe()
    program_input = open(read_end, encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", program_input)
    return program_input, write_end


def _assert_nothing_left(spy: _PopenSpy, program_input: Any, write_end: int) -> None:
    (process,) = spy.processes
    ((inherited,),) = spy.pass_fds
    assert inherited >= 3
    # The worker was killed and reaped, and the parent closed its duplicate.
    assert process.returncode is not None
    _assert_closed(inherited)
    # With our own read end closed, nothing reads the pipe any more.
    program_input.close()
    with pytest.raises(BrokenPipeError):
        os.write(write_end, b"x")


@needs_stdin
@posix_only
def test_a_timeout_during_a_read_leaves_no_process_or_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from geno.cli.run import run_file

    program_input, write_end = _blocked_input(monkeypatch)
    spy = _PopenSpy(monkeypatch)
    source = _write(tmp_path, READ_ONCE)
    try:
        with pytest.raises(SystemExit) as excinfo:
            run_file(str(source), capabilities={"print", "stdin"}, timeout=2.0)

        assert excinfo.value.code == 1
        assert capsys.readouterr().err.strip().splitlines()[-1] == (
            TIMEOUT_TAIL.format(timeout=2.0)
        )
        _assert_nothing_left(spy, program_input, write_end)
    finally:
        program_input.close()
        os.close(write_end)


@needs_stdin
@posix_only
def test_parent_interruption_leaves_no_process_or_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    def interrupted_wait(self: ProcessSandbox, *args: Any) -> int:
        time.sleep(1.0)  # the worker is reading by now
        raise KeyboardInterrupt

    monkeypatch.setattr(ProcessSandbox, "_wait_for_worker_tree", interrupted_wait)
    program_input, write_end = _blocked_input(monkeypatch)
    spy = _PopenSpy(monkeypatch)
    source = _write(tmp_path, READ_ONCE)
    try:
        with pytest.raises(KeyboardInterrupt):
            run_file(str(source), capabilities={"print", "stdin"}, timeout=30.0)

        _assert_nothing_left(spy, program_input, write_end)
    finally:
        program_input.close()
        os.close(write_end)


EXAMPLE_READER = """func read_input() -> String
    example () -> "piped"
    match stdin_read_all() with
        | Ok(text) -> return text
        | Err(error) -> return "Err " + error
    end match
end func

func main() -> Unit
    print("main sees [" + read_input() + "]")
end func
"""


@needs_stdin
def test_examples_and_main_share_one_stream(tmp_path: Path) -> None:
    _write(tmp_path, EXAMPLE_READER)

    checked = _run("--cap", "print,stdin", "main.geno", cwd=tmp_path, input=b"piped")
    skipped = _run(
        "--no-check-examples",
        "--cap",
        "print,stdin",
        "main.geno",
        cwd=tmp_path,
        input=b"piped",
    )

    # The example consumed the input; nothing is replayed for `main`.
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == b"main sees []\n"
    # Skipping examples skips their effects.
    assert skipped.returncode == 0, skipped.stderr
    assert skipped.stdout == b"main sees [piped]\n"


SLEEPY_EXAMPLE = """func settle() -> Int
    example () -> 1
    sleep_ms(3000)
    return 1
end func

func main() -> Unit
    match stdin_read_all() with
        | Ok(text) -> print("got " + text)
        | Err(error) -> print("Err " + error)
    end match
end func
"""


@needs_stdin
@posix_only
def test_an_example_sleep_and_a_read_share_the_runs_deadline(tmp_path: Path) -> None:
    """No phase or blocking read resets the deadline set before launch."""
    _write(tmp_path, SLEEPY_EXAMPLE)

    started = time.monotonic()
    process = _spawn(
        "--timeout", "4", "--cap", "clock,print,stdin", "main.geno", cwd=tmp_path
    )
    try:
        process.wait(timeout=60)
        elapsed = time.monotonic() - started
        assert process.stderr is not None
        stderr = process.stderr.read().decode()
    finally:
        _finish(process)

    assert process.returncode == 1
    assert stderr.strip().splitlines()[-1] == TIMEOUT_TAIL.format(timeout=4.0)
    # The example's 3 s and the blocked read share one 4 s deadline; a reset
    # at `main` or at the read would run until about 7 s after launch.
    assert elapsed < 6.5

    # Control: the same program, given input, really sleeps in its example.
    started = time.monotonic()
    control = _run(
        "--timeout",
        "30",
        "--cap",
        "clock,print,stdin",
        "main.geno",
        cwd=tmp_path,
        input=b"done",
    )
    assert control.returncode == 0, control.stderr
    assert control.stdout == b"got done\n"
    assert time.monotonic() - started >= 3.0
