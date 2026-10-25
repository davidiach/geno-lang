"""Process output is bounded while streaming, before the producer completes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
LANES = ("interpreter", "runtime", "standalone")


def test_oversized_input_is_rejected_before_files_or_processes(monkeypatch):
    import tempfile

    from geno import _runtime_support as runtime

    def unexpected(*args, **kwargs):
        raise AssertionError("input exceeded its budget before host I/O")

    monkeypatch.setattr(tempfile, "TemporaryFile", unexpected)
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    with pytest.raises(RuntimeError, match="String size exceeds limit"):
        runtime._capture_process_output(
            [sys.executable],
            input_text="12345",
            env=None,
            timeout=1,
            limit=4,
            limit_error=RuntimeError,
        )


def test_expired_input_setup_does_not_launch_a_command(monkeypatch):
    import time

    from geno import _runtime_support as runtime

    clock = iter([0.0, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))

    def unexpected(*args, **kwargs):
        raise AssertionError("command launched after the input setup deadline")

    monkeypatch.setattr(subprocess, "Popen", unexpected)
    with pytest.raises(subprocess.TimeoutExpired):
        runtime._capture_process_output(
            [sys.executable],
            input_text="input",
            env=None,
            timeout=1,
            limit=8,
            limit_error=RuntimeError,
        )


def _probe(payload: dict) -> dict:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--probe"],
        input=json.dumps(payload),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return cast(dict, json.loads(completed.stdout))


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("descriptor", (1, 2))
def test_output_overflow_stops_and_reaps_producer_before_sleep(lane, descriptor):
    result = _probe({"case": "overflow", "lane": lane, "descriptor": descriptor})
    assert result["kind"] == "exception", result
    assert "String size exceeds limit" in result["error"], result
    assert result["elapsed"] < 1.5, result
    assert result["children_alive"] == [False], result
    assert result["extra_threads"] == [], result


@pytest.mark.parametrize("lane", LANES)
def test_process_without_input_never_spools_output_to_a_file(lane):
    result = _probe({"case": "no_spool", "lane": lane})
    assert result["kind"] == "ok", result
    assert result["stdout"] == "ok"
    assert result["stderr"] == ""


@pytest.mark.parametrize("lane", LANES)
def test_utf8_decoding_counts_characters_and_flushes_incomplete_tail(lane):
    result = _probe({"case": "utf8", "lane": lane})
    assert result["kind"] == "ok", result
    assert result["stdout"] == "a" * 8191 + "😀�"
    assert result["stderr"] == "�"


@pytest.mark.parametrize("lane", LANES)
def test_explicit_input_and_both_output_streams_exceed_pipe_capacity(lane):
    result = _probe({"case": "pressure", "lane": lane})
    assert result["kind"] == "ok", result
    assert result["stdout"] == "x" * 131072
    assert result["stderr"] == "x" * 131072
    assert result["children_alive"] == [False], result


@pytest.mark.parametrize("lane", LANES)
def test_empty_explicit_input_delivers_eof(lane):
    result = _probe({"case": "empty_input", "lane": lane})
    assert result["kind"] == "ok", result
    assert result["stdout"] == "0"


@pytest.mark.parametrize("case", ("timeout", "closed_pipes", "descendant_pipes"))
def test_capture_deadline_covers_child_exit_and_pipe_eof(case):
    result = _probe({"case": case, "lane": "helper"})
    assert result["kind"] == "exception", result
    assert result["error_type"] == "TimeoutExpired", result
    assert result["elapsed"] < 1.5, result
    assert result["children_alive"] == [False], result
    assert result["extra_threads"] == [], result
    if case == "descendant_pipes":
        assert result["descendant_finished"], result


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe polling only")
def test_pipe_polling_accepts_descriptors_above_select_fd_setsize():
    result = _probe({"case": "high_fd", "lane": "helper"})
    if "skip" in result:
        pytest.skip(result["skip"])
    assert result["descriptor"] >= 1024, result
    assert result["not_ready"], result
    assert result["data"] == "ready", result
    assert result["eof"], result


def _child_probe(payload: dict) -> dict:
    import os
    import tempfile
    import threading
    import time
    import types
    from unittest.mock import patch

    sys.path.insert(0, str(ROOT))
    from geno import _runtime_support as runtime

    case = payload["case"]
    lane = payload["lane"]
    if case == "high_fd":
        import errno
        import fcntl
        import resource

        posix_resource = cast(Any, resource)
        posix_fcntl = cast(Any, fcntl)
        soft_limit, _ = posix_resource.getrlimit(posix_resource.RLIMIT_NOFILE)
        if soft_limit != posix_resource.RLIM_INFINITY and soft_limit <= 1024:
            return {"skip": "RLIMIT_NOFILE does not allow descriptor 1024"}
        read_fd, write_fd = os.pipe()
        try:
            try:
                high_fd = posix_fcntl.fcntl(read_fd, posix_fcntl.F_DUPFD, 1024)
            except OSError as error:
                if error.errno == errno.EMFILE:
                    return {"skip": "process descriptor ceiling is exhausted"}
                raise
            with os.fdopen(high_fd, "rb", buffering=0) as stream:
                not_ready = runtime._process_pipe_chunk(stream) is None
                os.write(write_fd, b"ready")
                data = runtime._process_pipe_chunk(stream)
                os.close(write_fd)
                write_fd = -1
                eof = runtime._process_pipe_chunk(stream) == b""
                return {
                    "descriptor": high_fd,
                    "not_ready": not_ready,
                    "data": data.decode("ascii") if data is not None else None,
                    "eof": eof,
                }
        finally:
            os.close(read_fd)
            if write_fd != -1:
                os.close(write_fd)

    executable = getattr(sys, "_base_executable", None) or sys.executable
    # These finite stdlib producers need no site initialization. In particular,
    # do not spend their short deadline on coverage's subprocess startup hook:
    # the Geno capture code in this probe remains covered.
    producer_flags = ["-S", "-c"]
    limit = 131072 if case == "pressure" else 8193 if case == "utf8" else 64
    os.environ["GENO_PROCESS_EXECUTABLES"] = executable

    if lane == "interpreter":
        from geno._serve import install_process_callbacks
        from geno.interpreter import Interpreter
        from geno.sandbox import SandboxConfig

        interpreter = Interpreter(
            sandbox_config=SandboxConfig(max_collection_size=limit),
            capabilities={"process"},
        )
        install_process_callbacks(interpreter, allowed_executables=[executable])
        functions = interpreter.global_env.bindings

        def invoke(code, supplied_input):
            if supplied_input is None:
                return functions["spawn"].func(executable, [*producer_flags, code])
            return functions["spawn_with_input"].func(
                executable, [*producer_flags, code], supplied_input
            )

        def unpack(value):
            if value.constructor == "Err":
                return {"kind": "err", "error": value.fields["error"]}
            fields = value.fields["value"].fields
            return {
                "kind": "ok",
                "stdout": fields["stdout"],
                "stderr": fields["stderr"],
            }

    elif lane != "helper":
        namespace = runtime.__dict__
        if lane == "standalone":
            from geno.compiler import compile_to_python

            module = types.ModuleType("process_limit_standalone_probe")
            sys.modules[module.__name__] = module
            namespace = module.__dict__
            namespace["_GENO_MAX_COLLECTION_SIZE"] = limit
            exec(
                compile_to_python("func main() -> Int\nreturn 0\nend func\n"),
                namespace,
            )
        namespace["_GENO_CAPS"] = {"process"}
        namespace["_MAX_COLLECTION_SIZE"] = limit

        def invoke(code, supplied_input):
            if supplied_input is None:
                return namespace["spawn"](executable, [*producer_flags, code])
            return namespace["spawn_with_input"](
                executable, [*producer_flags, code], supplied_input
            )

        def unpack(value):
            if isinstance(value, namespace["Err"]):
                return {"kind": "err", "error": value.error}
            return {
                "kind": "ok",
                "stdout": value.value.stdout,
                "stderr": value.value.stderr,
            }

    else:

        def invoke(code, supplied_input):
            return runtime._capture_process_output(
                [executable, *producer_flags, code],
                input_text=supplied_input,
                env=None,
                timeout=0.25,
                limit=limit,
                limit_error=RuntimeError,
            )

        def unpack(value):
            return {"kind": "ok", "stdout": value.stdout, "stderr": value.stderr}

    supplied_input = None
    if case == "overflow":
        code = (
            f"import os,time; os.write({payload['descriptor']}, b'x'*2048); "
            "time.sleep(2)"
        )
    elif case == "no_spool":
        code = "import sys; sys.stdout.write('ok')"

        def reject_tempfile(*args, **kwargs):
            raise AssertionError("output was spooled to a temporary file")

        tempfile.TemporaryFile = reject_tempfile
    elif case == "utf8":
        code = (
            "import os; os.write(1,b'a'*8191+b'\\xf0'); "
            "os.write(1,b'\\x9f\\x98\\x80\\xe2'); os.write(2,b'\\xff')"
        )
    elif case == "pressure":
        supplied_input = "x" * limit
        code = (
            "import sys; data=sys.stdin.buffer.read(); "
            "sys.stdout.buffer.write(data); sys.stdout.buffer.flush(); "
            "sys.stderr.buffer.write(data); sys.stderr.buffer.flush()"
        )
    elif case == "empty_input":
        supplied_input = ""
        code = "import sys; sys.stdout.write(str(len(sys.stdin.buffer.read())))"
    elif case == "closed_pipes":
        code = "import os,time; os.close(1); os.close(2); time.sleep(2)"
    elif case == "descendant_pipes":
        # A finite descendant retains output handles after its parent exits.
        # Wait for its marker after the capture assertion so the test leaves no child.
        scratch = tempfile.TemporaryDirectory(prefix="geno-process-pipes-")
        marker = Path(scratch.name) / "finished"
        staging_marker = marker.with_suffix(".pending")
        # Publish only after write_text has closed its file. Existence of an
        # open marker is not sufficient for immediate cleanup on Windows.
        descendant = (
            "import time; from pathlib import Path; time.sleep(0.75); "
            f"staging = Path({str(staging_marker)!r}); "
            f"staging.write_text('done'); staging.replace({str(marker)!r})"
        )
        code = f"import subprocess; subprocess.Popen([{executable!r}, '-S', '-c', {descendant!r}])"
    else:
        code = "import time; time.sleep(2)"

    processes = []
    original_popen = subprocess.Popen

    def observed_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    before = set(threading.enumerate())
    started = time.monotonic()
    result: dict
    with patch.object(subprocess, "Popen", observed_popen):
        try:
            result = unpack(invoke(code, supplied_input))
        except Exception as error:
            result = {
                "kind": "exception",
                "error_type": type(error).__name__,
                "error": str(error),
            }
    result["elapsed"] = time.monotonic() - started
    result["children_alive"] = [process.poll() is None for process in processes]
    result["extra_threads"] = [
        thread.name for thread in set(threading.enumerate()) - before
    ]
    if case == "descendant_pipes":
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        result["descendant_finished"] = marker.exists()
        scratch.cleanup()
    return result


if __name__ == "__main__":
    print(json.dumps(_child_probe(json.load(sys.stdin))))
