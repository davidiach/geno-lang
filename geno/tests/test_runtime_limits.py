"""Default limits that run ordinary programs (proposal 0011)."""

from __future__ import annotations

import contextvars
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import geno.execution_limits as execution_limits
from geno.api import RunConfig, run
from geno.compiler import compile_to_python
from geno.execution_limits import (
    DEFAULT_INTERPRETER_MAX_STEPS,
    DEFAULT_MAX_RECURSION_DEPTH,
    INLINE_RECURSION_LIMIT,
    call_with_recursion_room,
)
from geno.interpreter import Interpreter
from geno.parser import parse
from geno.sandbox import ProcessSandboxConfig, SandboxConfig
from geno.test_runner import run_test_suite
from geno.tests._script_runner import run_python_code
from geno.values import ConstructorValue, GenoRuntimeError, VecValue

_REPO_ROOT = Path(__file__).resolve().parents[2]

DEEP_PROGRAM = """type Chain = Link(value: Int, next: Chain) | End

func count(n: Int) -> Int
    example 3 -> 3
    if n == 0 then
        return 0
    end if
    return 1 + count(n - 1)
end func

func build(n: Int) -> Chain
    example 0 -> End
    if n == 0 then
        return End
    end if
    return Link(n, build(n - 1))
end func

func total(chain: Chain) -> Int
    example End -> 0
    match chain with
        | Link(value, next) -> return value + total(next)
        | End -> return 0
    end match
end func

func fib(n: Int) -> Int
    example 10 -> 55
    if n < 2 then
        return n
    end if
    return fib(n - 1) + fib(n - 2)
end func

func main() -> Unit
    print(count(9000))
    print(total(build(5000)))
    print(fib(24))
end func
"""

DEEP_OUTPUT = "9000\n12502500\n46368\n"


def test_defaults_are_shared_by_every_local_lane() -> None:
    assert DEFAULT_MAX_RECURSION_DEPTH == 10_000
    assert DEFAULT_INTERPRETER_MAX_STEPS == 20_000_000
    assert RunConfig().max_recursion_depth == DEFAULT_MAX_RECURSION_DEPTH
    assert SandboxConfig().max_recursion_depth == DEFAULT_MAX_RECURSION_DEPTH
    assert ProcessSandboxConfig().max_recursion_depth == DEFAULT_MAX_RECURSION_DEPTH
    assert Interpreter().max_recursion_depth == DEFAULT_MAX_RECURSION_DEPTH


def test_the_embedding_api_runs_ordinary_recursion() -> None:
    """Depth 9,000, a 5,000-node structure and fib(24) within the defaults."""
    result = run(DEEP_PROGRAM, config=RunConfig(capabilities={"print"}, timeout=60))
    assert result.ok, [d.message for d in result.diagnostics]
    assert result.output == DEEP_OUTPUT


def _sandbox_stack_grows() -> bool:
    """Whether the sandbox worker's stack holds a depth-10,000 run.

    Before Python 3.12 the worker cannot start a thread, so only Linux, which
    grows the main-thread stack up to the hard limit, gives it the room;
    elsewhere it keeps the recursion limit within its stack (proposal 0011).
    """
    if sys.version_info >= (3, 12):
        return True
    if not sys.platform.startswith("linux"):
        return False
    import resource

    hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
    return hard == resource.RLIM_INFINITY or hard >= 96 * 1024 * 1024


@pytest.mark.parametrize("lane", [[], ["--unsafe", "--cap", "print"]])
def test_geno_run_runs_ordinary_recursion(tmp_path: Path, lane: list[str]) -> None:
    path = tmp_path / "deep.geno"
    path.write_text(DEEP_PROGRAM, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "geno", "run", *lane, str(path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=_REPO_ROOT,
    )
    if not lane and not _sandbox_stack_grows():
        # The documented fallback: a recursion error, never a crash.
        assert completed.returncode == 1, completed.stderr
        assert completed.stderr.strip() == (
            "Runtime Error: maximum recursion depth exceeded"
        )
        return
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == DEEP_OUTPUT


def test_a_standalone_script_runs_ordinary_recursion() -> None:
    completed = run_python_code(
        compile_to_python(DEEP_PROGRAM),
        python_executable=sys.executable,
        args=["--cap", "print"],
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == DEEP_OUTPUT


def test_examples_run_with_the_same_depth_as_main(tmp_path: Path) -> None:
    path = tmp_path / "Deep.geno"
    path.write_text(
        "func count(n: Int) -> Int\n"
        "    example 3000 -> 3000\n"
        "    if n == 0 then\n"
        "        return 0\n"
        "    end if\n"
        "    return 1 + count(n - 1)\n"
        "end func\n"
        'test "deep"\n'
        "    assert count(4000) == 4000\n"
        "end test\n",
        encoding="utf-8",
    )
    suite = run_test_suite([path])
    assert suite.success, suite.file_results[0].harness_result


def test_the_hosted_server_keeps_its_own_depth() -> None:
    from geno.monitoring import RuntimeMetricsCollector
    from geno.server import MAX_RECURSION_DEPTH, _build_run_config

    config = _build_run_config(
        {"source": "func main() -> Int\n    return 1\nend func"},
        RuntimeMetricsCollector(),
        {"print"},
    )
    assert MAX_RECURSION_DEPTH == 500
    assert config.max_recursion_depth == MAX_RECURSION_DEPTH


def test_runaway_recursion_still_stops_at_the_limit() -> None:
    result = run(
        "func loop(n: Int) -> Int\n"
        "    example 0 -> 0\n"
        "    if n == 0 then\n"
        "        return 0\n"
        "    end if\n"
        "    return loop(n + 1)\n"
        "end func\n"
        "func main() -> Int\n"
        "    return loop(1)\n"
        "end func\n",
        config=RunConfig(timeout=60),
    )
    assert not result.ok
    assert "Maximum recursion depth exceeded (10000)" in result.diagnostics[0].message


# ---------------------------------------------------------------------------
# call_with_recursion_room
# ---------------------------------------------------------------------------


@pytest.fixture
def needs_c_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the pre-3.12 path on any interpreter."""
    monkeypatch.setattr(execution_limits, "C_STACK_FREE_RECURSION", False)


def test_a_shallow_run_stays_on_the_calling_thread(needs_c_stack: None) -> None:
    caller = threading.current_thread()
    seen: list[tuple[int, threading.Thread]] = []
    call_with_recursion_room(
        INLINE_RECURSION_LIMIT,
        lambda limit: seen.append((limit, threading.current_thread())),
    )
    assert seen == [(INLINE_RECURSION_LIMIT, caller)]


def test_a_deep_run_gets_a_thread_and_keeps_context(needs_c_stack: None) -> None:
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker")
    marker.set("caller")

    def body(limit: int) -> tuple[int, str, bool]:
        return (
            limit,
            marker.get(),
            threading.current_thread() is threading.main_thread(),
        )

    limit, value, on_main = call_with_recursion_room(120_100, body)
    assert (limit, value) == (120_100, "caller")
    assert not on_main


def test_a_deep_run_reraises_its_error(needs_c_stack: None) -> None:
    def body(limit: int) -> None:
        raise ValueError("from the run")

    with pytest.raises(ValueError, match="from the run"):
        call_with_recursion_room(120_100, body)


def test_without_a_thread_the_run_keeps_the_inline_limit(
    needs_c_stack: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", refuse)
    assert call_with_recursion_room(120_100, lambda limit: limit) == (
        INLINE_RECURSION_LIMIT
    )


def test_an_interrupted_interpreter_stops_at_its_next_step() -> None:
    interpreter = Interpreter()
    interpreter.interrupt()
    program = parse(
        "func main() -> Int\n"
        "    var n = 0\n"
        "    while true do\n"
        "        n = n + 1\n"
        "    end while\n"
        "    return n\n"
        "end func\n"
    )
    with pytest.raises(KeyboardInterrupt):
        interpreter.run(program)


# ---------------------------------------------------------------------------
# Limit checks stay linear
# ---------------------------------------------------------------------------


def _chain(length: int, tail: object = None) -> ConstructorValue:
    value = ConstructorValue("End", {}) if tail is None else tail
    for index in range(length):
        value = ConstructorValue("Link", {"value": index, "next": value})
    return value  # type: ignore[return-value]


def test_a_checked_constructor_value_is_not_walked_again() -> None:
    interpreter = Interpreter()
    chain = _chain(3)
    interpreter._check_collection_limits([chain], None)
    limits = (
        interpreter.sandbox_config.max_collection_size,
        interpreter.sandbox_config.max_integer_bits,
    )
    assert chain._limits_checked == limits


def test_a_value_reaching_a_mutable_container_is_rechecked() -> None:
    interpreter = Interpreter()
    chain = _chain(2, tail=ConstructorValue("Box", {"items": VecValue()}))
    interpreter._check_collection_limits([chain], None)
    assert chain._limits_checked is None


def test_a_stricter_limit_walks_the_value_again() -> None:
    chain = _chain(2, tail=ConstructorValue("Box", {"items": [1, 2, 3]}))
    Interpreter()._check_collection_limits([chain], None)
    strict = Interpreter(sandbox_config=SandboxConfig(max_collection_size=2))
    with pytest.raises(GenoRuntimeError, match="List size exceeds limit"):
        strict._check_collection_limits([chain], None)


def test_a_field_assignment_clears_the_check() -> None:
    result = run(
        "type Box = Box(items: List[Int])\n"
        "func main() -> Int\n"
        "    var box = Box([1])\n"
        "    box.items = [1, 2, 3]\n"
        "    return length(box.items)\n"
        "end func\n",
        config=RunConfig(max_collection_size=2),
    )
    assert not result.ok
    assert "List size exceeds limit" in result.diagnostics[0].message
