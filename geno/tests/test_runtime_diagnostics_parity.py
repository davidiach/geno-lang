"""Runtime report parity across lanes and backends (proposal 0018).

The same failure must give the same report (label, message, frames,
positions, omitted count) on the interpreter (``geno run --unsafe``), the
default process-isolated ``geno run`` lane (compiled ``main`` mapped by the
trusted worker) and standalone compiled Python. Compiled index and capability
messages keep their 0.6 wording, which the proposal documents, so those
cases compare the shared prefix.

This file holds the Python half; the JavaScript backend joins it in P4. It
also covers the default lane's budgeted worker payload, the parent's strict
snippet reader, standalone output with no Geno install, the
``GENO_TRACEBACK=host`` escape hatch and the reporter's fallback on a
corrupted table.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import threading
from contextlib import redirect_stderr
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from geno import runtime_report
from geno.diagnostics import GenoStack, StackFrame
from geno.sandbox import ProcessSandbox, ProcessSandboxConfig
from geno.tokens import SourceLocation

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 120
LABELS = ("Runtime Error: ", "Sandbox Error: ", "Limit Error: ")
_FRAME = re.compile(r"^  (?:(.+):(\d+):(\d+) )?in (.+)$")
_OMITTED = re.compile(r"^  \.\.\. (\d+) frames? omitted \.\.\.$")


@dataclass(frozen=True)
class Report:
    frames: tuple[tuple[str | None, str], ...]
    omitted: int
    message: str


def _parse(stderr: str) -> Report:
    """Parse a runtime report; positions keep only the file's base name."""
    lines = stderr.rstrip("\n").split("\n")
    message_at = max(
        index for index, line in enumerate(lines) if line.startswith(LABELS)
    )
    frames: list[tuple[str | None, str]] = []
    omitted = 0
    if lines[0] == runtime_report.STACK_HEADER:
        for line in lines[1:message_at]:
            if line.startswith("      "):
                continue  # the snippet
            elided = _OMITTED.match(line)
            if elided is not None:
                omitted = int(elided.group(1))
                continue
            frame = _FRAME.match(line)
            assert frame is not None, line
            path, row, column, name = frame.groups()
            position = (
                None if path is None else f"{os.path.basename(path)}:{row}:{column}"
            )
            frames.append((position, name))
    return Report(tuple(frames), omitted, lines[message_at])


def _env(**extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "GENO_TRACEBACK"}
    env.update(extra)
    return env


def _geno(*args: str, cwd: Path, env: dict[str, str] | None = None) -> Any:
    return subprocess.run(
        [sys.executable, "-m", "geno", *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        cwd=cwd,
        env=env or _env(),
        check=False,
    )


def _standalone(
    program: Path, out_dir: Path, *args: str, env: dict[str, str] | None = None
) -> Any:
    """Compile *program* and run the artifact where no Geno can be imported."""
    out_dir.mkdir(exist_ok=True)
    artifact = out_dir / (program.stem + ".py")
    compiled = _geno("compile", str(program), "-o", str(artifact), cwd=ROOT)
    assert compiled.returncode == 0, compiled.stderr
    return subprocess.run(
        # -I ignores PYTHONPATH and the user site; -S drops site-packages, so
        # an installed (or editable) geno is not importable either.
        [sys.executable, "-I", "-S", str(artifact), *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        cwd=out_dir,
        env={
            key: value
            for key, value in _env(**(env or {})).items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        },
        check=False,
    )


def _lanes(program: Path, tmp_path: Path, caps: str = "print") -> dict[str, Any]:
    """Run *program* on the interpreter, the default lane and standalone."""
    common = ("--no-check-examples", "--cap", caps)
    return {
        "interpreter": _geno("run", "--unsafe", *common, str(program), cwd=tmp_path),
        "default": _geno("run", *common, str(program), cwd=tmp_path),
        "standalone": _standalone(program, tmp_path / "out", "--cap", caps),
    }


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


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

INDEX = """func c(xs: List[Int]) -> Int
    example [1, 2, 3, 4, 5, 6, 7, 8] -> 8
    return xs[7]
end func

func b(xs: List[Int]) -> Int
    example [1, 2, 3, 4, 5, 6, 7, 8] -> 8
    return c(xs)
end func

func main() -> Int
    let xs = [1, 2, 3]
    return b(xs)
end func
"""

REQUIRES = """func safe_div(a: Int, b: Int) -> Int
    requires b != 0
    example 6, 3 -> 2
    return a / b
end func

func main() -> Int
    print(to_string(safe_div(a: 8, b: 0)))
    return 0
end func
"""

ENSURES = """func shout(s: String) -> String
    ensures length(result) > 10
    example "a long sentence" -> "a long sentence!"
    return s + "!"
end func

func main() -> Int
    print(shout("hi"))
    return 0
end func
"""

ENSURES_REBOUND = """func g(n: Int) -> Int
    ensures n > 10
    example 20 -> 25
    let n = n + 5
    return n
end func

func main() -> Int
    print(to_string(g(1)))
    return 0
end func
"""

ENSURES_SHADOW = """func g(n: Int) -> Int
    ensures n > 10
    example 20 -> 20
    if n > 0 then
        let n = 99
        print(to_string(n))
    end if
    return n
end func

func main() -> Int
    print(to_string(g(1)))
    return 0
end func
"""

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

DEEP = """func count_down(n: Int) -> Int
    example 0 panics "bottom"
    if n == 0 then
        panic("bottom")
    end if
    return count_down(n - 1)
end func

func main() -> Int
    return count_down(40)
end func
"""

DENIAL = """func read_it(path: String) -> String
    example "x" -> "x"
    match fs_read_text(path) with
        | Ok(text) -> return text
        | Err(e) -> return e
    end match
end func

func main() -> Int
    print(read_it("data.txt"))
    return 0
end func
"""

ASYNC = """async func work(n: Int) -> Int
    if n > 3 then
        panic("work failed")
    end if
    return n
end func

async func helper(n: Int) -> Int
    let v = await work(n)
    return v
end func

async func main() -> Int
    let a = await helper(1)
    let b = await helper(5)
    return a + b
end func
"""

OUTPUT_FIRST = """func check(n: Int) -> Int
    example 1 -> 1
    if n > 1 then
        panic("after output")
    end if
    return n
end func

func main() -> Int
    print("before")
    return check(2)
end func
"""

SAME_MESSAGE = [
    ("panic.geno", PANIC),
    ("requires.geno", REQUIRES),
    ("ensures.geno", ENSURES),
    ("rebound.geno", ENSURES_REBOUND),
    ("shadow.geno", ENSURES_SHADOW),
    ("lambdas.geno", NESTED_LAMBDAS),
    ("deep.geno", DEEP),
    ("async_main.geno", ASYNC),
    ("output_first.geno", OUTPUT_FIRST),
]


def _assert_parity(runs: dict[str, Any], *, same_message: bool = True) -> Report:
    reports = {lane: _parse(run.stderr) for lane, run in runs.items()}
    for lane, run in runs.items():
        assert run.returncode == 1, (lane, run.stderr)
        assert "Traceback" not in run.stderr, (lane, run.stderr)
    expected = reports["interpreter"]
    assert expected.frames, runs["interpreter"].stderr
    for lane, report in reports.items():
        assert report.frames == expected.frames, (lane, runs[lane].stderr)
        assert report.omitted == expected.omitted, lane
        if same_message:
            assert report.message == expected.message, lane
    stdouts = {run.stdout for run in runs.values()}
    assert len(stdouts) == 1, stdouts
    return expected


class TestParity:
    @pytest.mark.parametrize(
        ("name", "source"), SAME_MESSAGE, ids=[n for n, _s in SAME_MESSAGE]
    )
    def test_the_same_report_on_every_lane(
        self, tmp_path: Path, name: str, source: str
    ) -> None:
        program = _write(tmp_path, name, source)
        _assert_parity(_lanes(program, tmp_path))

    def test_the_panic_frames(self, tmp_path: Path) -> None:
        report = _assert_parity(_lanes(_write(tmp_path, "panic.geno", PANIC), tmp_path))
        assert report.frames == (
            ("panic.geno:15:12", "main"),
            ("panic.geno:11:12", "scale"),
            ("panic.geno:4:9", "check"),
        )
        assert report.message == "Runtime Error: value too large"

    def test_contract_messages_carry_the_arguments(self, tmp_path: Path) -> None:
        expected = {
            REQUIRES: "Precondition failed: safe_div(a: 8, b: 0) requires b != 0",
            ENSURES: 'Postcondition failed: shout(s: "hi") ensures '
            'length(result) > 10 (result was "hi!")',
            ENSURES_REBOUND: "Postcondition failed: g(n: 1) ensures n > 10 "
            "(result was 6; n was 6 when checked)",
            ENSURES_SHADOW: "Postcondition failed: g(n: 1) ensures n > 10 "
            "(result was 1)",
        }
        for number, (source, message) in enumerate(expected.items()):
            program = _write(tmp_path, f"contract{number}.geno", source)
            report = _assert_parity(_lanes(program, tmp_path))
            assert report.message == f"Runtime Error: {message}"

    def test_recursion_is_bounded_alike(self, tmp_path: Path) -> None:
        report = _assert_parity(_lanes(_write(tmp_path, "deep.geno", DEEP), tmp_path))
        assert len(report.frames) == 20
        assert report.omitted == 22
        assert report.frames[0] == ("deep.geno:10:12", "main")
        assert report.frames[-1] == ("deep.geno:4:9", "count_down")

    def test_an_index_error(self, tmp_path: Path) -> None:
        report = _assert_parity(
            _lanes(_write(tmp_path, "index.geno", INDEX), tmp_path), same_message=False
        )
        assert report.frames == (
            ("index.geno:13:12", "main"),
            ("index.geno:8:12", "b"),
            ("index.geno:3:12", "c"),
        )
        # Compiled index messages keep their shorter 0.6 wording.
        assert report.message.startswith("Runtime Error: Index 7 out of bounds")

    def test_a_capability_denial(self, tmp_path: Path) -> None:
        program = _write(tmp_path, "denial.geno", DENIAL)
        runs = _lanes(program, tmp_path)
        report = _assert_parity(runs, same_message=False)
        assert report.frames == (
            ("denial.geno:10:11", "main"),
            ("denial.geno:3:11", "read_it"),
        )
        for lane, run in runs.items():
            message = _parse(run.stderr).message
            assert message.startswith(
                "Runtime Error: Capability denied: 'fs_read_text' requires the "
                "'fs' capability"
            ), lane
        # The isolated lane's hint is still last, after the report.
        assert runs["default"].stderr.rstrip().splitlines()[-2].startswith("Hint: ")

    def test_an_async_main(self, tmp_path: Path) -> None:
        report = _assert_parity(
            _lanes(_write(tmp_path, "async_main.geno", ASYNC), tmp_path)
        )
        assert report.frames == (
            ("async_main.geno:15:19", "main"),
            ("async_main.geno:9:19", "helper"),
            ("async_main.geno:3:9", "work"),
        )

    def test_a_module_project(self, tmp_path: Path) -> None:
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Helper"]\n', encoding="utf-8"
        )
        program = _write(
            tmp_path,
            "Main.geno",
            "import Helper\n\nfunc main() -> Int\n"
            "    let n = Helper.scale(4)\n    return n\nend func\n",
        )
        _write(tmp_path, "Helper.geno", PANIC.split("func main", 1)[0])
        report = _assert_parity(_lanes(program, tmp_path))
        assert report.frames == (
            ("Main.geno:4:13", "main"),
            ("Helper.geno:11:12", "Helper.scale"),
            ("Helper.geno:4:9", "Helper.check"),
        )

    def test_the_default_lane_prints_the_snippet(self, tmp_path: Path) -> None:
        result = _geno("run", str(_write(tmp_path, "panic.geno", PANIC)), cwd=tmp_path)
        assert result.stderr.splitlines()[-3:] == [
            '      panic("value too large")',
            "      ^",
            "Runtime Error: value too large",
        ]


class TestStandalone:
    def test_geno_is_not_importable_where_the_artifact_runs(
        self, tmp_path: Path
    ) -> None:
        probe = subprocess.run(
            [sys.executable, "-I", "-S", "-c", "import geno"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=tmp_path,
            check=False,
        )
        assert probe.returncode != 0
        result = _standalone(_write(tmp_path, "panic.geno", PANIC), tmp_path / "out")
        assert result.returncode == 1
        assert result.stderr == (
            "Stack trace (most recent call last):\n"
            "  panic.geno:15:12 in main\n"
            "  panic.geno:11:12 in scale\n"
            "  panic.geno:4:9 in check\n"
            "Runtime Error: value too large\n"
        )

    def test_geno_traceback_host_appends_the_host_traceback(
        self, tmp_path: Path
    ) -> None:
        program = _write(tmp_path, "panic.geno", PANIC)
        plain = _standalone(program, tmp_path / "out")
        host = _standalone(program, tmp_path / "out", env={"GENO_TRACEBACK": "host"})
        assert host.returncode == plain.returncode == 1
        report, marker, traceback = host.stderr.partition(
            "Host traceback (GENO_TRACEBACK=host):\n"
        )
        assert marker
        assert report == plain.stderr
        assert traceback.startswith("Traceback (most recent call last):")
        assert traceback.rstrip().endswith("RuntimeError: value too large")

    @pytest.mark.parametrize(
        "table", ["'garbage'", "'G\\t2\\nE\\t9'", "'G\\t1\\nE\\tx'", "42"]
    )
    def test_a_corrupted_table_reports_the_message_alone(
        self, tmp_path: Path, table: str
    ) -> None:
        program = _write(tmp_path, "panic.geno", PANIC)
        out = tmp_path / "out"
        out.mkdir()
        artifact = out / "panic.py"
        assert (
            _geno("compile", str(program), "-o", str(artifact), cwd=ROOT).returncode
            == 0
        )
        code = artifact.read_text(encoding="utf-8")
        code, replaced = re.subn(
            r"^_GENO_TRACE = .*$",
            lambda _match: f"_GENO_TRACE = {table}",
            code,
            flags=re.M,
        )
        assert replaced == 1
        artifact.write_text(code, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(artifact)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=out,
            check=False,
        )
        assert result.returncode == 1
        assert result.stderr == "Runtime Error: value too large\n"


class TestDefaultLaneBudget:
    """The worker's `trace` object never costs the message its line."""

    @staticmethod
    def _assert_runtime_error(result: Any) -> list[str]:
        assert result.returncode == 1
        assert "Sandbox Error" not in result.stderr
        assert "result JSON" not in result.stderr
        lines = result.stderr.rstrip("\n").split("\n")
        assert lines[-1].startswith("Runtime Error: ")
        return lines

    def _panic_three_deep(self, tmp_path: Path, message: str) -> Path:
        source = PANIC.replace('"value too large"', f'"{message}"')
        return _write(tmp_path, "budget.geno", source)

    def test_a_message_close_to_the_output_limit(self, tmp_path: Path) -> None:
        message = "m" * 1990
        program = self._panic_three_deep(tmp_path, message)
        result = _geno("run", "--max-output-length", "2000", str(program), cwd=tmp_path)
        lines = self._assert_runtime_error(result)
        assert lines[-1] == f"Runtime Error: {message}"
        assert _parse(result.stderr).frames[-1] == ("budget.geno:4:9", "check")

    def test_a_small_output_limit(self, tmp_path: Path) -> None:
        program = self._panic_three_deep(tmp_path, "x" * 400)
        result = _geno("run", "--max-output-length", "200", str(program), cwd=tmp_path)
        lines = self._assert_runtime_error(result)
        # The message itself is cut as in 0.6; the stack survives beside it.
        assert lines[-1].endswith("... [truncated]")
        assert len(_parse(result.stderr).frames) == 3

    def test_a_non_ascii_message(self, tmp_path: Path) -> None:
        message = "Überfehler ✓ 🎉 ñ " * 10
        program = self._panic_three_deep(tmp_path, message)
        result = _geno("run", str(program), cwd=tmp_path)
        lines = self._assert_runtime_error(result)
        assert lines[-1] == f"Runtime Error: {message}"
        assert len(_parse(result.stderr).frames) == 3

    def test_a_deep_recursion_with_long_paths_and_names(self, tmp_path: Path) -> None:
        directory = tmp_path
        for part in ("d" * 100, "e" * 100, "f" * 100):
            directory = directory / part
        directory.mkdir(parents=True)
        name = "count_" + "x" * 114
        source = DEEP.replace("count_down", name).replace("(40)", "(59)")
        program = _write(directory, "deep.geno", source)
        result = _geno("run", str(program), cwd=tmp_path)
        lines = self._assert_runtime_error(result)
        assert lines[-1] == "Runtime Error: bottom"
        report = _parse(result.stderr)
        assert report.frames, result.stderr
        assert len(report.frames) <= 20
        assert len(report.frames) + report.omitted == 61
        assert report.frames[0][1] == "main"
        assert report.frames[-1] == ("deep.geno:4:9", name)

    def test_geno_traceback_host_shows_the_worker_traceback(
        self, tmp_path: Path
    ) -> None:
        program = _write(tmp_path, "denial.geno", DENIAL)
        plain = _geno("run", "--no-check-examples", str(program), cwd=tmp_path)
        host = _geno(
            "run",
            "--no-check-examples",
            str(program),
            cwd=tmp_path,
            env=_env(GENO_TRACEBACK="host"),
        )
        assert host.returncode == plain.returncode == 1
        report, marker, rest = host.stderr.partition(
            "Host traceback (GENO_TRACEBACK=host):\n"
        )
        assert marker
        hint_at = plain.stderr.index("Hint: ")
        assert report == plain.stderr[:hint_at]
        assert rest.startswith("Traceback (most recent call last):")
        # The capability hint is still last.
        assert rest.endswith(plain.stderr[hint_at:])


class TestTracePayload:
    FRAMES = [("main", ("/p/app.geno", 15, 12)), ("check", ("/p/app.geno", 4, 9))]

    def test_a_payload_round_trips(self) -> None:
        payload = runtime_report.encode_trace_payload(
            self.FRAMES, 0, ("/p/app.geno", 4, 9)
        )
        assert payload == {
            "files": ["/p/app.geno"],
            "location": [0, 4, 9],
            "stack": [["main", 0, 15, 12], ["check", 0, 4, 9]],
            "omitted": 0,
        }
        decoded = runtime_report.decode_trace_payload(payload)
        assert decoded is not None
        location, stack, omitted_at = decoded
        assert location == SourceLocation(line=4, column=9, filename="/p/app.geno")
        assert stack is not None
        assert [frame.function for frame in stack.frames] == ["main", "check"]
        assert omitted_at is None

    def test_over_budget_frames_next_to_the_elision_point_go_first(self) -> None:
        frames = [
            (f"f{index:03d}" + "n" * 100, ("/p/app.geno", index + 1, 5))
            for index in range(20)
        ]
        payload = runtime_report.encode_trace_payload(
            frames, 7, ("/p/app.geno", 20, 5), limit=1500
        )
        assert payload is not None
        assert len(__import__("json").dumps(payload)) <= 1500
        names = [entry[0] for entry in payload["stack"]]
        assert names[0] == frames[0][0] and names[-1] == frames[-1][0]
        assert payload["omitted"] == 7 + 20 - len(names)
        at = payload["omitted_at"]
        kept_head = names[:at]
        kept_tail = names[at:]
        assert kept_head == [name for name, _ in frames[: len(kept_head)]]
        assert kept_tail == [name for name, _ in frames[20 - len(kept_tail) :]]
        decoded = runtime_report.decode_trace_payload(payload)
        assert decoded is not None
        lines = runtime_report.stack_lines(decoded[1], decoded[2])
        assert lines[at].startswith("  ... ")

    def test_then_paths_shrink_then_the_stack_goes(self) -> None:
        long_path = "/" + "p" * 400 + "/app.geno"
        frames = [("main", (long_path, 1, 1)), ("check", (long_path, 2, 2))]
        shortened = runtime_report.encode_trace_payload(
            frames, 0, (long_path, 2, 2), limit=200
        )
        assert shortened is not None
        assert shortened["files"] == ["app.geno"]
        bare = runtime_report.encode_trace_payload(
            [("m" * 120, (long_path, 1, 1)), ("c" * 120, (long_path, 2, 2))],
            0,
            (long_path, 2, 2),
            limit=120,
        )
        assert bare == {"files": ["app.geno"], "location": [0, 2, 2]}
        assert (
            runtime_report.encode_trace_payload(frames, 0, (long_path, 2, 2), 5) is None
        )

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            {"files": [], "extra": 1},
            {"files": "app.geno"},
            {"files": ["app\x1b[31m.geno"], "location": [0, 1, 1]},
            {"files": ["app.geno"], "location": [1, 1, 1]},
            {"files": ["app.geno"], "location": [0, 0, 1]},
            {"files": ["app.geno"], "location": [0, True, 1]},
            {"files": ["app.geno"], "location": [0, 1]},
            {"files": ["app.geno"], "stack": [["x" * 129, 0, 1, 1]]},
            {"files": ["app.geno"], "stack": [["main\n", 0, 1, 1]]},
            {"files": ["app.geno"], "stack": [["main", 0, 1]]},
            {"files": ["app.geno"], "stack": [["main"]] * 21},
            {"files": ["app.geno"], "stack": [["main"]], "omitted": -1},
            {"files": ["app.geno"], "stack": [["main"]], "omitted_at": 2},
            {"files": ["app.geno"], "stack": [], "omitted": 3},
        ],
    )
    def test_a_malformed_payload_is_dropped_whole(self, payload: Any) -> None:
        assert runtime_report.decode_trace_payload(payload) is None

    def test_the_sandbox_keeps_only_a_valid_trace(self) -> None:
        sandbox = ProcessSandbox(ProcessSandboxConfig())
        sandbox._keep_failure_trace({"files": ["app.geno"], "location": [9, 1, 1]})
        assert sandbox.last_failure_location is None
        assert sandbox.last_failure_stack is None
        sandbox._keep_failure_trace(
            {"files": ["app.geno"], "location": [0, 3, 4], "stack": [["main", 0, 3, 4]]}
        )
        assert sandbox.last_failure_location == SourceLocation(
            line=3, column=4, filename="app.geno"
        )
        assert sandbox.last_failure_stack == GenoStack(
            (StackFrame("main", sandbox.last_failure_location),)
        )

    def test_the_capture_window_reserves_the_trace_budget(self) -> None:
        sandbox = ProcessSandbox(ProcessSandboxConfig(max_output_length=200))
        assert sandbox._STDERR_TRACE_OVERHEAD == runtime_report.TRACE_PAYLOAD_LIMIT
        assert sandbox._stderr_capture_limit() == (
            200 + sandbox._STDERR_RESULT_OVERHEAD + runtime_report.TRACE_PAYLOAD_LIMIT
        )
        # The success-result bound the worker enforces is unchanged.
        assert sandbox._stderr_result_limit() == 200 + sandbox._STDERR_RESULT_OVERHEAD


class TestParentSnippetReader:
    """The default lane's parent reads snippets from forged locations safely."""

    @staticmethod
    def _read(path: Path, root: Path, line: int = 1) -> str | None:
        return runtime_report.read_snippet_line(
            SourceLocation(line=line, column=1, filename=str(path)),
            allowed_roots=[root],
        )

    def test_a_program_file_is_read(self, tmp_path: Path) -> None:
        (tmp_path / "app.geno").write_text("func main() -> Int\n    return 1\n")
        assert self._read(tmp_path / "app.geno", tmp_path, 2) == "    return 1"

    def test_a_dot_env_file_is_not_read(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("SECRET=hunter2\n")
        assert self._read(tmp_path / ".env", tmp_path) is None

    def test_a_non_geno_file_is_not_read(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("private notes\n")
        assert self._read(tmp_path / "notes.txt", tmp_path) is None

    def test_a_directory_is_not_read(self, tmp_path: Path) -> None:
        (tmp_path / "folder.geno").mkdir()
        assert self._read(tmp_path / "folder.geno", tmp_path) is None

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs only")
    def test_a_fifo_is_not_read_and_does_not_block(self, tmp_path: Path) -> None:
        fifo = tmp_path / "pipe.geno"
        os.mkfifo(fifo)
        outcome: list[str | None] = []
        reader = threading.Thread(
            target=lambda: outcome.append(self._read(fifo, tmp_path)), daemon=True
        )
        reader.start()
        reader.join(TIMEOUT)
        assert not reader.is_alive(), "the snippet reader blocked on a FIFO"
        assert outcome == [None]

    @pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
    def test_a_symlink_out_of_the_tree_is_not_read(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.geno").write_text("func secret() -> Int\n")
        tree = tmp_path / "tree"
        tree.mkdir()
        link = tree / "link.geno"
        try:
            link.symlink_to(outside / "secret.geno")
        except OSError:
            pytest.skip("symlinks are not available")
        assert self._read(link, tree) is None
        assert self._read(outside / "secret.geno", tree) is None

    def test_an_oversized_file_is_not_read_past_the_cap(self, tmp_path: Path) -> None:
        big = tmp_path / "big.geno"
        filler = "// " + "x" * 1000 + "\n"
        count = runtime_report.SNIPPET_READ_LIMIT // len(filler) + 10
        big.write_text(filler * count + "func main() -> Int\n")
        assert self._read(big, tmp_path, count + 1) is None
        long_line = tmp_path / "long.geno"
        long_line.write_text("x" * (runtime_report.SNIPPET_LINE_LIMIT + 1) + "\n")
        assert self._read(long_line, tmp_path) is None

    def test_the_parent_prints_no_snippet_for_a_forged_location(
        self, tmp_path: Path
    ) -> None:
        from geno.cli.run import _print_default_lane_runtime_error

        secret = tmp_path / ".env"
        secret.write_text("SECRET=hunter2\n")
        forged = SourceLocation(line=1, column=1, filename=str(secret))
        sandbox = SimpleNamespace(
            last_failure_location=forged,
            last_failure_stack=GenoStack((StackFrame("main", forged),)),
            last_failure_omitted_at=None,
        )
        captured = io.StringIO()
        with redirect_stderr(captured):
            _print_default_lane_runtime_error(
                "boom", sandbox, str(tmp_path / "app.geno")
            )
        assert "hunter2" not in captured.getvalue()
        assert captured.getvalue().splitlines() == [
            runtime_report.STACK_HEADER,
            f"  {secret}:1:1 in main",
            "Runtime Error: boom",
        ]
