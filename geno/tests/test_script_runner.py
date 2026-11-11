"""One timeout bound for the generated programs that tests run under Node (#249).

On a loaded CI runner every ``node`` launch took more than 10 seconds for a few
minutes, and each test that set its own 10-second limit failed. A timeout here
bounds a program that hangs; it is not a start-up budget. So the helpers default
to ``GENERATED_PROGRAM_TIMEOUT``, direct ``node`` launches pass it, and no test
sets a shorter literal of its own.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from geno.tests._script_runner import (
    GENERATED_PROGRAM_TIMEOUT,
    run_node_code,
    run_python_code,
)

TESTS = Path(__file__).resolve().parent
_SUBPROCESS_CALLS = {"subprocess.run", "subprocess.Popen", "subprocess.check_output"}


def _launches_node(call: ast.Call) -> bool:
    """Whether *call* runs Node: the helper, or a command list that starts with it.

    A command built elsewhere and passed as a variable is not recognized.
    """
    name = ast.unparse(call.func)
    if name.endswith("run_node_code"):
        return True
    if name not in _SUBPROCESS_CALLS or not call.args:
        return False
    command = call.args[0]
    if not isinstance(command, ast.List) or not command.elts:
        return False
    executable = ast.unparse(command.elts[0]).lower()
    return "node" in executable and "python" not in executable


def _short_node_timeouts(source: str) -> list[int]:
    """Lines of Node launches in *source* whose timeout is a literal below the bound."""
    lines = []
    for call in ast.walk(ast.parse(source)):
        if not isinstance(call, ast.Call) or not _launches_node(call):
            continue
        for keyword in call.keywords:
            value = keyword.value
            if (
                keyword.arg == "timeout"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, (int, float))
                and not isinstance(value.value, bool)
                and value.value < GENERATED_PROGRAM_TIMEOUT
            ):
                lines.append(call.lineno)
    return lines


def test_helpers_default_to_the_shared_bound() -> None:
    for helper in (run_node_code, run_python_code):
        default = inspect.signature(helper).parameters["timeout"].default
        assert default == GENERATED_PROGRAM_TIMEOUT, helper.__name__
    # Start-up alone exceeded 10 seconds in #249, so the bound stays far above it.
    assert GENERATED_PROGRAM_TIMEOUT >= 60


def test_no_node_launch_sets_a_shorter_literal_timeout() -> None:
    offenders = [
        f"{path.relative_to(TESTS)}:{line}"
        for path in sorted(TESTS.rglob("*.py"))
        for line in _short_node_timeouts(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "Node launches with their own short timeout; drop it from run_node_code "
        "or pass GENERATED_PROGRAM_TIMEOUT"
    )


def test_the_scan_recognizes_each_launch_form() -> None:
    short = (
        "run_node_code(js, timeout=10)\n"
        "runner.run_node_code(js, args=('--cap', 'print'), timeout=15.0)\n"
        "subprocess.run(['node', path], capture_output=True, timeout=10)\n"
        "subprocess.run([_NODE or 'node', path], timeout=30)\n"
        "subprocess.run([node, '--check'], input=code, timeout=15)\n"
    )
    assert _short_node_timeouts(short) == [1, 2, 3, 4, 5]

    accepted = (
        "run_node_code(js)\n"
        "run_node_code(js, timeout=None)\n"
        "run_node_code(js, timeout=120)\n"
        "subprocess.run(['node', path], timeout=GENERATED_PROGRAM_TIMEOUT)\n"
        "subprocess.run(['node', path], timeout=budget)\n"
        "subprocess.run([sys.executable, path], timeout=10)\n"
        "subprocess.run(['git', 'status'], timeout=5)\n"
        "run_python_code(py, python_executable=sys.executable, timeout=10)\n"
    )
    assert _short_node_timeouts(accepted) == []
