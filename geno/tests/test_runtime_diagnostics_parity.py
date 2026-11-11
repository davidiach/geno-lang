"""Runtime report parity across lanes and backends (proposal 0018).

The same failure must give the same report (label, message, frames,
positions, omitted count) on the interpreter (``geno run --unsafe``), the
default process-isolated ``geno run`` lane (compiled ``main`` mapped by the
trusted worker), standalone compiled Python, and compiled JavaScript run by
Node as a CommonJS script and as an ES module. Compiled index and capability
messages keep their 0.6 wording, which the proposal documents, so those
cases compare the shared prefix.

It also covers the default lane's budgeted worker payload, the parent's
strict snippet reader, standalone output with no Geno install, the
``GENO_TRACEBACK=host`` escape hatch, the reporters' fallback on a corrupted
table, and the Node entry path: immediate termination after a report with
no output lost to a full pipe, the ``unhandledRejection`` reporter, the stack-text fallback and
``node --enable-source-maps``.
"""

from __future__ import annotations

import io
import os
import re
import shutil
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
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
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


def _compile_js(program: Path, out_dir: Path, *flags: str, esm: bool = False) -> Path:
    """Compile *program* to a Node entry build in *out_dir*."""
    out_dir.mkdir(exist_ok=True)
    artifact = out_dir / (program.stem + (".mjs" if esm else ".js"))
    args = ["compile", "--target", "js", str(program), "-o", str(artifact), *flags]
    if esm:
        args.append("--esm")
    compiled = _geno(*args, cwd=ROOT)
    assert compiled.returncode == 0, compiled.stderr
    return artifact


def _run_node(
    artifact: Path,
    *args: str,
    env: dict[str, str] | None = None,
    node_flags: tuple[str, ...] = (),
) -> Any:
    assert NODE is not None
    return subprocess.run(
        [NODE, *node_flags, str(artifact), *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        cwd=artifact.parent,
        env=_env(**(env or {})),
        check=False,
    )


def _node(
    program: Path, out_dir: Path, *args: str, esm: bool = False, **options: Any
) -> Any:
    """Compile *program* to JavaScript and run it with Node."""
    return _run_node(_compile_js(program, out_dir, esm=esm), *args, **options)


def _lanes(
    program: Path, tmp_path: Path, caps: str = "print", *, esm: bool = True
) -> dict[str, Any]:
    """Run *program* on the interpreter, the default lane, standalone Python
    and, when Node is installed, as a CommonJS script and an ES module."""
    common = ("--no-check-examples", "--cap", caps)
    runs = {
        "interpreter": _geno("run", "--unsafe", *common, str(program), cwd=tmp_path),
        "default": _geno("run", *common, str(program), cwd=tmp_path),
        "standalone": _standalone(program, tmp_path / "out", "--cap", caps),
    }
    if NODE is not None:
        runs["node"] = _node(program, tmp_path / "js", "--cap", caps)
        if esm:
            runs["node-esm"] = _node(program, tmp_path / "js", "--cap", caps, esm=True)
    return runs


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
    return count_down(n - 1) + 0
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

# A synchronous `main` that fails while an un-awaited async call is still
# pending: JavaScript starts it eagerly, so its continuation is queued.
PENDING_ASYNC = """async func noisy(n: Int) -> Int
    let v = await quiet(n)
    print("late output")
    return v
end func

async func quiet(n: Int) -> Int
    return n
end func

func check(n: Int) -> Int
    example 1 -> 1
    if n > 1 then
        panic("sync failure")
    end if
    return n
end func

func main() -> Int
    let pending = noisy(1)
    return check(2)
end func
"""

# Output larger than a pipe's buffer (typically 64 KiB) and within the
# default output limit (100,000 characters), then a failure, from a
# synchronous and an asynchronous `main`.
LOUD_LINES = 1400
LOUD_FILL = "x" * 56
LOUD_SYNC = f"""func work(n: Int) -> Int
    example 1 -> 1
    if n > 3 then
        panic("work failed")
    end if
    return n
end func

func main() -> Int
    for i: Int in range(0, {LOUD_LINES}) do
        print("line " + to_string(i) + " {LOUD_FILL}")
    end for
    return work(9)
end func
"""

LOUD_ASYNC = f"""async func work(n: Int) -> Int
    if n > 3 then
        panic("work failed")
    end if
    return n
end func

async func main() -> Int
    for i: Int in range(0, {LOUD_LINES}) do
        print("line " + to_string(i) + " {LOUD_FILL}")
    end for
    let v = await work(9)
    return v
end func
"""


def _run_with_a_late_reader(command: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run *command* while its stdout pipe is left full for a moment.

    The reader starts late, so the program fills the pipe and has the rest of
    its output queued, or blocks, when it fails. The delay only provokes that
    state: with complete output the result is the same however long it is.
    """
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=_env(),
    )
    assert process.stdout is not None and process.stderr is not None
    stderr = process.stderr
    errors: list[bytes] = []
    reader = threading.Thread(target=lambda: errors.append(stderr.read()))
    reader.start()
    threading.Event().wait(0.5)
    output = process.stdout.read()
    reader.join(TIMEOUT)
    status = process.wait(TIMEOUT)
    return status, output.decode("utf-8"), b"".join(errors).decode("utf-8")


# An Async that fails with nothing awaiting it.
UNAWAITED = """async func work(n: Int) -> Int
    if n > 3 then
        panic("work failed")
    end if
    return n
end func

func main() -> Int
    let pending = work(5)
    print("main done")
    return 0
end func
"""

# A default parameter value is evaluated inside the callee: the caller is
# positioned at the call, the callee at the default expression.
DEFAULT_ARGUMENT = """func boom() -> Int
    example () panics "bad default"
    panic("bad default")
    return 0
end func

func label(n: Int, tag: Int = boom()) -> Int
    example 1, 2 -> 3
    return n + tag
end func

func main() -> Int
    print(to_string(label(3, 4)))
    return label(3)
end func
"""

# Calls through function values: a parameter and a local.
CALLBACK_CHECK = """func check(n: Int) -> Int
    example 1 -> 1
    if n > 5 then
        panic("too big")
    end if
    return n
end func
"""

PARAMETER_CALLEE = (
    CALLBACK_CHECK
    + """
func apply(f: (Int) -> Int, x: Int) -> Int
    example check, 1 -> 2
    return 1 + f(x)
end func

func main() -> Int
    return apply(check, 9)
end func
"""
)

LOCAL_CALLEE = (
    CALLBACK_CHECK
    + """
func main() -> Int
    let g = check
    let r = 1 + g(9)
    return r
end func
"""
)

# A parameter or local that shadows a builtin or function name is still a
# function value.
SHADOWED_BUILTIN = """func boom(n: Int) -> String
    example 1 -> "1"
    if n > 3 then
        panic("boom")
    end if
    return to_string(n)
end func

func render(n: Int, format: (Int) -> String) -> String
    example 1, boom -> "<1>"
    return "<" + format(n) + ">"
end func

func main() -> Int
    print(render(9, boom))
    return 0
end func
"""

SHADOWED_FUNCTION = (
    CALLBACK_CHECK
    + """
func other(n: Int) -> Int
    example 1 -> 1
    if n > 3 then
        panic("other failed")
    end if
    return n
end func

func apply(check: (Int) -> Int, x: Int) -> Int
    example other, 1 -> 2
    return 1 + check(x)
end func

func main() -> Int
    return apply(other, 9)
end func
"""
)

SHADOWED_LOCAL = (
    CALLBACK_CHECK
    + """
func main() -> Int
    let abs = check
    let total = 1 + abs(9)
    return total
end func
"""
)

# A function called by name next to a call through a function value that
# holds the same function: either call could have failed.
NAMED_AND_INDIRECT = (
    CALLBACK_CHECK
    + """
func main() -> Int
    let f = check
    let total = check(1) + f(9)
    return total
end func
"""
)

NAMED_AROUND_INDIRECT = (
    CALLBACK_CHECK
    + """
func apply(f: (Int) -> Int, v: Int) -> Int
    example check, 1 -> 1
    return check(f(v))
end func

func main() -> Int
    return apply(check, 9)
end func
"""
)

# Failures inside f-string interpolations are located where they are written.
FSTRING_CALL = """func boom(n: Int) -> Int
    example 1 -> 1
    if n > 3 then
        panic("in boom")
    end if
    return n
end func

func show(n: Int) -> String
    example 1 -> "n: 1, twice: 2"
    return f"n: {boom(n)}, twice: {n * 2}"
end func

func main() -> Int
    print(show(7))
    return 0
end func
"""

FSTRING_INDEX = """func fstr(xs: List[Int]) -> String
    example [1, 2, 3, 4] -> "v=4"
    return f"v={xs[3]}"
end func

func main() -> Int
    print(fstr([2]))
    return 0
end func
"""

# An `ensures` on a generic function shows the result in the specialization's
# shape (JavaScript cannot tell 2.0 from 2 at run time).
GENERIC_FLOAT = """func ident[T](x: T) -> T
    ensures result != x
    example 1 panics "Postcondition"
    return x
end func

func main() -> Int
    print(to_string(ident(2.0)))
    return 0
end func
"""

GENERIC_FLOAT_LIST = """func rev[T](xs: List[T]) -> List[T]
    ensures length(result) > 5
    example [1, 2, 3, 4, 5, 6] -> [6, 5, 4, 3, 2, 1]
    return xs.reverse()
end func

func main() -> Int
    print(to_string(length(rev([1.0, 2.0]))))
    return 0
end func
"""

OVERFLOW = """func down(n: Int) -> Int
    example 0 -> 0
    if n == 0 then
        return 0
    end if
    return down(n + 1) + 0
end func

func main() -> Int
    return down(1)
end func
"""

SAME_MESSAGE = [
    ("panic.geno", PANIC),
    ("default_argument.geno", DEFAULT_ARGUMENT),
    ("parameter_callee.geno", PARAMETER_CALLEE),
    ("local_callee.geno", LOCAL_CALLEE),
    ("fstring_call.geno", FSTRING_CALL),
    ("generic_float.geno", GENERIC_FLOAT),
    ("generic_float_list.geno", GENERIC_FLOAT_LIST),
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
        assert "    at " not in run.stderr, (lane, run.stderr)
        if lane != "default":
            # Nothing is printed after a report (the default lane may add
            # the capability hint).
            last = run.stderr.rstrip("\n").split("\n")[-1]
            assert last == reports[lane].message, (lane, run.stderr)
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

    def test_a_failing_default_argument(self, tmp_path: Path) -> None:
        program = _write(tmp_path, "default_argument.geno", DEFAULT_ARGUMENT)
        report = _assert_parity(_lanes(program, tmp_path))
        assert report.frames == (
            ("default_argument.geno:14:12", "main"),
            ("default_argument.geno:7:31", "label"),
            ("default_argument.geno:3:5", "boom"),
        )
        assert report.message == "Runtime Error: bad default"

    def test_calls_through_function_values(self, tmp_path: Path) -> None:
        parameter = _write(tmp_path, "parameter_callee.geno", PARAMETER_CALLEE)
        assert _assert_parity(_lanes(parameter, tmp_path)).frames == (
            ("parameter_callee.geno:15:12", "main"),
            ("parameter_callee.geno:11:16", "apply"),
            ("parameter_callee.geno:4:9", "check"),
        )
        local = _write(tmp_path, "local_callee.geno", LOCAL_CALLEE)
        assert _assert_parity(_lanes(local, tmp_path)).frames == (
            ("local_callee.geno:11:17", "main"),
            ("local_callee.geno:4:9", "check"),
        )

    def test_shadowing_callback_names_keep_their_columns(self, tmp_path: Path) -> None:
        builtin = _write(tmp_path, "shadowed_builtin.geno", SHADOWED_BUILTIN)
        assert _assert_parity(_lanes(builtin, tmp_path)).frames == (
            ("shadowed_builtin.geno:15:11", "main"),
            ("shadowed_builtin.geno:11:18", "render"),
            ("shadowed_builtin.geno:4:9", "boom"),
        )
        function = _write(tmp_path, "shadowed_function.geno", SHADOWED_FUNCTION)
        assert _assert_parity(_lanes(function, tmp_path)).frames == (
            ("shadowed_function.geno:23:12", "main"),
            ("shadowed_function.geno:19:16", "apply"),
            ("shadowed_function.geno:12:9", "other"),
        )
        local = _write(tmp_path, "shadowed_local.geno", SHADOWED_LOCAL)
        assert _assert_parity(_lanes(local, tmp_path)).frames == (
            ("shadowed_local.geno:11:21", "main"),
            ("shadowed_local.geno:4:9", "check"),
        )

    @pytest.mark.parametrize(
        ("name", "source", "interpreter", "statement", "succeeded"),
        [
            # `check(1) + f(9)`: the interpreter reports `f(9)`; `check(1)`
            # had succeeded.
            ("named_and_indirect", NAMED_AND_INDIRECT, "11:28", "11:5", "11:17"),
            # `check(f(v))`: `f(v)` failed; `check(...)` had not started.
            ("named_around_indirect", NAMED_AROUND_INDIRECT, "11:18", "11:5", "11:12"),
        ],
    )
    def test_a_named_and_an_indirect_call_report_the_statement(
        self,
        tmp_path: Path,
        name: str,
        source: str,
        interpreter: str,
        statement: str,
        succeeded: str,
    ) -> None:
        """Either call could hold the failing function, so compiled backends
        report the statement's start, never the call that did not fail."""
        runs = _lanes(_write(tmp_path, f"{name}.geno", source), tmp_path)
        frame = 0 if name == "named_and_indirect" else 1
        reports = {lane: _parse(run.stderr) for lane, run in runs.items()}
        for lane, run in runs.items():
            assert run.returncode == 1, (lane, run.stderr)
            assert reports[lane].message == "Runtime Error: too big", lane
            assert reports[lane].frames[-1] == (f"{name}.geno:4:9", "check"), lane
            position = reports[lane].frames[frame][0]
            assert position != f"{name}.geno:{succeeded}", (lane, run.stderr)
            expected = interpreter if lane == "interpreter" else statement
            assert position == f"{name}.geno:{expected}", (lane, run.stderr)
        compiled = {reports[lane].frames for lane in reports if lane != "interpreter"}
        assert len(compiled) == 1, compiled

    def test_failures_inside_fstring_interpolations(self, tmp_path: Path) -> None:
        call = _write(tmp_path, "fstring_call.geno", FSTRING_CALL)
        assert _assert_parity(_lanes(call, tmp_path)).frames == (
            ("fstring_call.geno:15:11", "main"),
            ("fstring_call.geno:11:18", "show"),
            ("fstring_call.geno:4:9", "boom"),
        )
        index = _write(tmp_path, "fstring_index.geno", FSTRING_INDEX)
        report = _assert_parity(_lanes(index, tmp_path), same_message=False)
        assert report.frames == (
            ("fstring_index.geno:7:11", "main"),
            ("fstring_index.geno:3:17", "fstr"),
        )

    @pytest.mark.parametrize(
        "expression",
        [
            'f"n: {boom(n + length(""))}, twice: {n * 2}"',
            'f"{f"n: {boom(n)}, twice: {n * 2}"}"',
        ],
    )
    def test_literals_and_nested_fstrings_keep_runtime_locations(
        self, tmp_path: Path, expression: str
    ) -> None:
        """Proposal 0026's strings retain proposal 0018's Geno stack locations."""
        source = FSTRING_CALL.replace('f"n: {boom(n)}, twice: {n * 2}"', expression)
        program = _write(tmp_path, "nested_fstring.geno", source)
        column = source.splitlines()[10].index("boom(") + 1

        assert _assert_parity(_lanes(program, tmp_path)).frames == (
            ("nested_fstring.geno:15:11", "main"),
            (f"nested_fstring.geno:11:{column}", "show"),
            ("nested_fstring.geno:4:9", "boom"),
        )

    def test_a_generic_ensures_shows_the_specialized_result(
        self, tmp_path: Path
    ) -> None:
        expected = {
            GENERIC_FLOAT: "Postcondition failed: ident(x: 2.0) ensures "
            "result != x (result was 2.0)",
            GENERIC_FLOAT_LIST: "Postcondition failed: rev(xs: [1.0, 2.0]) "
            "ensures length(result) > 5 (result was [2.0, 1.0])",
        }
        for number, (source, message) in enumerate(expected.items()):
            program = _write(tmp_path, f"generic{number}.geno", source)
            report = _assert_parity(_lanes(program, tmp_path))
            assert report.message == f"Runtime Error: {message}"

    def test_a_host_stack_overflow_differs_only_in_its_innermost_frame(
        self, tmp_path: Path
    ) -> None:
        """Every kept frame but the innermost is the same on every lane.

        The depth (and so the omitted count) and the message are the host's,
        and so is the innermost frame's position: where the host ran out of
        stack, at the recursive call or inside the callee's first operation
        (CPython 3.10, V8).
        """
        runs = _lanes(_write(tmp_path, "overflow.geno", OVERFLOW), tmp_path)
        for lane, run in runs.items():
            assert run.returncode == 1, (lane, run.stderr)
            assert "Traceback" not in run.stderr, (lane, run.stderr)
            report = _parse(run.stderr)
            assert len(report.frames) == 20, (lane, run.stderr)
            assert report.omitted > 0, lane
            assert report.frames[0] == ("overflow.geno:10:12", "main"), lane
            assert report.frames[1:-1] == (("overflow.geno:6:12", "down"),) * 18, (
                lane,
                run.stderr,
            )
            assert report.frames[-1][1] == "down", lane
        # Where the interpreter's limit stops a call, so does a CPython that
        # runs out of stack making one.
        assert _parse(runs["interpreter"].stderr).frames[-1][0] == (
            "overflow.geno:6:12"
        )

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
        # An ES module build of a project does not run in 0.6 either (its
        # `export { main }` names a function inside the module factory).
        report = _assert_parity(_lanes(program, tmp_path, esm=False))
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


PANIC_REPORT = (
    "Stack trace (most recent call last):\n"
    "  panic.geno:15:12 in main\n"
    "  panic.geno:11:12 in scale\n"
    "  panic.geno:4:9 in check\n"
    "Runtime Error: value too large\n"
)


@needs_node
class TestNode:
    """The Node entry path (proposal 0018, section 9)."""

    @pytest.mark.parametrize("esm", [False, True], ids=["cjs", "esm"])
    def test_the_report_is_the_standalone_python_report(
        self, tmp_path: Path, esm: bool
    ) -> None:
        result = _node(_write(tmp_path, "panic.geno", PANIC), tmp_path / "js", esm=esm)
        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == PANIC_REPORT

    @pytest.mark.parametrize("esm", [False, True], ids=["cjs", "esm"])
    def test_geno_traceback_host_appends_the_host_stack(
        self, tmp_path: Path, esm: bool
    ) -> None:
        artifact = _compile_js(
            _write(tmp_path, "panic.geno", PANIC), tmp_path / "js", esm=esm
        )
        host = _run_node(artifact, env={"GENO_TRACEBACK": "host"})
        assert host.returncode == 1
        report, marker, traceback = host.stderr.partition(
            "Host traceback (GENO_TRACEBACK=host):\n"
        )
        assert marker
        assert report == PANIC_REPORT
        assert traceback.startswith("Error: value too large\n    at ")
        assert "    at check (" in traceback
        assert "    at scale (" in traceback

    @pytest.mark.parametrize(
        "table", ['"garbage"', '"G\\t2\\nE\\t9"', '"G\\t1\\nE\\tx"', "42"]
    )
    def test_a_corrupted_table_reports_the_message_alone(
        self, tmp_path: Path, table: str
    ) -> None:
        artifact = _compile_js(_write(tmp_path, "panic.geno", PANIC), tmp_path / "js")
        code = artifact.read_text(encoding="utf-8")
        code, replaced = re.subn(
            r"^const _GENO_TRACE = .*;$",
            lambda _match: f"const _GENO_TRACE = {table};",
            code,
            flags=re.M,
        )
        assert replaced == 1
        artifact.write_text(code, encoding="utf-8")
        result = _run_node(artifact)
        assert result.returncode == 1
        assert result.stderr == "Runtime Error: value too large\n"

    @pytest.mark.parametrize("esm", [False, True], ids=["cjs", "esm"])
    def test_an_already_formatted_stack_is_mapped_from_its_text(
        self, tmp_path: Path, esm: bool
    ) -> None:
        """When something formatted `e.stack` first, the reporter parses it."""
        artifact = _compile_js(
            _write(tmp_path, "panic.geno", PANIC), tmp_path / "js", esm=esm
        )
        code = artifact.read_text(encoding="utf-8")
        code, replaced = re.subn(
            r"_genoReportUncaught\((_temp_[0-9]+), ",
            lambda match: (
                f"void {match.group(1)}.stack, _genoReportUncaught({match.group(1)}, "
            ),
            code,
        )
        assert replaced == 1
        artifact.write_text(code, encoding="utf-8")
        result = _run_node(artifact)
        assert result.returncode == 1
        assert result.stderr == PANIC_REPORT

    def test_a_message_cannot_forge_frames_in_the_stack_text(
        self, tmp_path: Path
    ) -> None:
        """The text fallback skips the error's own message lines.

        The message forges a frame line that names the artifact and a line
        inside `main`'s body; read as a frame, it would add a fourth frame.
        """
        artifact = _compile_js(_write(tmp_path, "panic.geno", PANIC), tmp_path / "js")
        lines = artifact.read_text(encoding="utf-8").split("\n")
        body_line = 1 + next(
            index for index, line in enumerate(lines) if "return scale(" in line
        )
        escaped_path = str(artifact).replace("\\", "\\\\")
        forged = f"boom\\n    at main ({escaped_path}:{body_line}:16)"
        source = PANIC.replace('"value too large"', f'"{forged}"')
        artifact = _compile_js(_write(tmp_path, "panic.geno", source), tmp_path / "js")
        code = artifact.read_text(encoding="utf-8")
        assert code.split("\n")[body_line - 1] == lines[body_line - 1]
        code, replaced = re.subn(
            r"_genoReportUncaught\((_temp_[0-9]+), ",
            lambda match: (
                f"void {match.group(1)}.stack, _genoReportUncaught({match.group(1)}, "
            ),
            code,
        )
        assert replaced == 1
        artifact.write_text(code, encoding="utf-8")
        result = _run_node(artifact)
        assert f"at main ({artifact}:{body_line}:16)" in result.stderr
        assert _parse(result.stderr).frames == (
            ("panic.geno:15:12", "main"),
            ("panic.geno:11:12", "scale"),
            ("panic.geno:4:9", "check"),
        )

    def test_under_enable_source_maps_with_a_source_map(self, tmp_path: Path) -> None:
        artifact = _compile_js(
            _write(tmp_path, "panic.geno", PANIC), tmp_path / "js", "--source-map"
        )
        assert artifact.with_name(artifact.name + ".map").is_file()
        result = _run_node(artifact, node_flags=("--enable-source-maps",))
        assert result.returncode == 1
        assert result.stderr == PANIC_REPORT

    def test_pending_async_work_never_runs_after_the_report(
        self, tmp_path: Path
    ) -> None:
        program = _write(tmp_path, "pending.geno", PENDING_ASYNC)
        runs = {
            "interpreter": _geno(
                "run", "--unsafe", "--no-check-examples", str(program), cwd=tmp_path
            ),
            "node": _node(program, tmp_path / "js", "--cap", "print"),
            "node-esm": _node(program, tmp_path / "js", "--cap", "print", esm=True),
        }
        for lane, run in runs.items():
            assert run.returncode == 1, lane
            assert run.stdout == "", (lane, run.stdout)
            assert run.stderr.rstrip("\n").split("\n")[-1] == (
                "Runtime Error: sync failure"
            ), lane
        assert _parse(runs["node"].stderr).frames == (
            ("pending.geno:21:12", "main"),
            ("pending.geno:14:9", "check"),
        )

    @pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
    def test_output_before_a_failure_survives_a_full_pipe(
        self, tmp_path: Path, asynchronous: bool
    ) -> None:
        """The report ends the process at once, yet no output is lost.

        The Node entry path makes stdout and stderr blocking, as they are in
        Python, so nothing is still queued for a full pipe when the reporter
        exits; every lane prints every line, then the report.
        """
        program = _write(
            tmp_path, "loud.geno", LOUD_ASYNC if asynchronous else LOUD_SYNC
        )
        out = tmp_path / "out"
        out.mkdir()
        compiled = _geno("compile", str(program), "-o", str(out / "loud.py"), cwd=ROOT)
        assert compiled.returncode == 0, compiled.stderr
        commands = {
            "interpreter": [
                sys.executable,
                "-m",
                "geno",
                "run",
                "--unsafe",
                "--no-check-examples",
                "--cap",
                "print",
                str(program),
            ],
            "standalone": [sys.executable, str(out / "loud.py"), "--cap", "print"],
        }
        assert NODE is not None
        for lane, esm in (("node", False), ("node-esm", True)):
            artifact = _compile_js(program, tmp_path / "js", esm=esm)
            commands[lane] = [NODE, str(artifact), "--cap", "print"]
        expected = "".join(f"line {index} {LOUD_FILL}\n" for index in range(LOUD_LINES))
        for lane, command in commands.items():
            status, stdout, stderr = _run_with_a_late_reader(command, tmp_path)
            assert status == 1, (lane, stderr)
            assert stdout.count("\n") == LOUD_LINES, lane
            assert stdout == expected, lane
            assert stderr.rstrip("\n").split("\n")[-1] == (
                "Runtime Error: work failed"
            ), (lane, stderr)

    @pytest.mark.parametrize("esm", [False, True], ids=["cjs", "esm"])
    def test_an_unawaited_failing_async_is_reported(
        self, tmp_path: Path, esm: bool
    ) -> None:
        """JavaScript starts an `Async` eagerly, so its failure is reported.

        The interpreter and compiled Python never run an `Async` nothing
        awaits (a documented backend difference); on Node the rejection
        reaches the reporter through `unhandledRejection` instead of Node's
        raw V8 stack, with 0.6's status 1.
        """
        program = _write(tmp_path, "unawaited.geno", UNAWAITED)
        result = _node(program, tmp_path / "js", "--cap", "print", esm=esm)
        assert result.returncode == 1
        assert result.stdout == "main done\n"
        assert result.stderr == (
            "Stack trace (most recent call last):\n"
            "  unawaited.geno:9:19 in main\n"
            "  unawaited.geno:3:9 in work\n"
            "Runtime Error: work failed\n"
        )
        interpreter = _geno(
            "run", "--unsafe", "--no-check-examples", str(program), cwd=tmp_path
        )
        assert (interpreter.returncode, interpreter.stdout) == (0, "main done\n")

    def test_a_host_stack_overflow_keeps_its_outermost_frames(
        self, tmp_path: Path
    ) -> None:
        source = """func down(n: Int) -> Int
    example 0 -> 0
    if n == 0 then
        return 0
    end if
    return down(n + 1) + 0
end func

func main() -> Int
    return down(1)
end func
"""
        result = _node(_write(tmp_path, "overflow.geno", source), tmp_path / "js")
        assert result.returncode == 1
        report = _parse(result.stderr)
        assert report.message == (
            "Runtime Error: RangeError: Maximum call stack size exceeded"
        )
        # The depth is the host's (documented), the head and tail are not.
        assert report.omitted > 0
        assert report.frames[0] == ("overflow.geno:10:12", "main")
        assert report.frames[1] == ("overflow.geno:6:12", "down")
        assert len(report.frames) == 20


class TestDefaultLaneBudget:
    """The worker's `trace` object never costs the message its line."""

    @staticmethod
    def _assert_runtime_error(result: Any) -> list[str]:
        assert result.returncode == 1
        assert "Sandbox Error" not in result.stderr
        assert "result JSON" not in result.stderr
        lines: list[str] = result.stderr.rstrip("\n").split("\n")
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
