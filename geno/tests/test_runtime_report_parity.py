"""The three runtime-report renderers agree byte for byte (proposal 0018).

Generated code stays standalone, so the Python prelude
(``geno/_runtime_support.py``) and the JavaScript prelude
(``geno/_js_runtime_support.js``) carry their own copies of
``geno/runtime_report.py``'s frame-block renderer and constants. Without a
snippet (compiled artifacts embed no source), the three must render the same
report to the same bytes, and each copy of a shared constant must have the
same value.
"""

from __future__ import annotations

import json
import re
import shutil
from typing import Any

import pytest

from geno import _runtime_support as python_prelude
from geno import parser_base, runtime_report
from geno.diagnostics import GenoStack, StackFrame
from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE
from geno.tests._script_runner import run_node_code
from geno.tokens import SourceLocation

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

_Position = tuple[str, int, int] | None


def _case(
    count: int, *, omit_every: int = 3, path: str = "app.geno"
) -> tuple[list[tuple[str, _Position]], int, _Position]:
    """A bounded report of *count* frames, as the preludes' mappers return it."""
    frames: list[tuple[str, _Position]] = [
        (f"f{index}", (path, index + 1, 5) if index % omit_every else None)
        for index in range(count)
    ]
    omitted = 0
    if count > runtime_report.STACK_HEAD + runtime_report.STACK_TAIL:
        frames = (
            frames[: runtime_report.STACK_HEAD] + frames[-runtime_report.STACK_TAIL :]
        )
        omitted = count - runtime_report.STACK_HEAD - runtime_report.STACK_TAIL
    location = frames[-1][1] if frames else (path, 9, 2)
    return frames, omitted, location


CASES = {
    "no-stack": _case(0),
    "one": _case(1, omit_every=2),
    "three": _case(3),
    "twenty": _case(20),
    "one-omitted": _case(21),
    "many-omitted": _case(42),
    "no-location": ([], 0, None),
    "other-paths": _case(4, path="std/List.geno"),
    "spaces-and-unicode": _case(3, path="my dir/prögram ✓.geno"),
    "names": (
        [
            ("main", ("Main.geno", 4, 13)),
            ("Helper.scale", ("Helper.geno", 11, 12)),
            ("Point.show", ("Helper.geno", 2, 1)),
            ("<example>", None),
        ],
        0,
        ("Helper.geno", 2, 1),
    ),
}
MESSAGE = 'value too large: "x" (result was 6; n was 6 when checked)'


def _location(position: _Position) -> SourceLocation | None:
    if position is None:
        return None
    return SourceLocation(line=position[1], column=position[2], filename=position[0])


def _reference(case: Any) -> list[str]:
    """``runtime_report``'s rendering of a report without a snippet."""
    frames, omitted, location = case
    stack = (
        GenoStack(
            tuple(StackFrame(name, _location(position)) for name, position in frames),
            omitted,
        )
        if frames
        else None
    )
    return runtime_report.report_lines(
        "Runtime Error", MESSAGE, stack=stack, location=_location(location)
    )


def _python_prelude(case: Any) -> list[str]:
    frames, omitted, location = case
    return [
        *python_prelude._geno_report_lines(frames, omitted, location),
        "Runtime Error: " + MESSAGE,
    ]


def _run_js(script: str) -> Any:
    """Run *script* after the whole JavaScript prelude; its stdout as JSON."""
    assert NODE is not None
    result = run_node_code(
        JS_RUNTIME_PRELUDE + "\n" + script, node_executable=NODE, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_the_three_renderers_produce_the_same_bytes() -> None:
    cases = {
        name: [
            [
                [frame, None if position is None else list(position)]
                for frame, position in frames
            ],
            omitted,
            None if location is None else list(location),
        ]
        for name, (frames, omitted, location) in CASES.items()
    }
    # The JavaScript report is composed as _genoReportUncaught composes it.
    js = _run_js(
        f"const cases = {json.dumps(cases)};\n"
        f"const message = {json.dumps(MESSAGE)};\n"
        "const out = {};\n"
        "for (const [name, [frames, omitted, location]] of Object.entries(cases)) {\n"
        "    const lines = _genoReportLines(frames, omitted, location);\n"
        '    lines.push("Runtime Error: " + message);\n'
        '    out[name] = lines.join("\\n") + "\\n";\n'
        "}\n"
        "console.log(JSON.stringify(out));\n"
    )
    for name, case in CASES.items():
        reference = "\n".join(_reference(case)) + "\n"
        assert "\n".join(_python_prelude(case)) + "\n" == reference, name
        assert js[name] == reference, name


def test_the_python_prelude_matches_the_reference_renderer() -> None:
    """Runs without Node, so a missing Node never hides a Python drift."""
    for name, case in CASES.items():
        assert _python_prelude(case) == _reference(case), name


def _js_constants() -> dict[str, Any]:
    return _run_js(
        "console.log(JSON.stringify({\n"
        "    head: _GENO_STACK_HEAD,\n"
        "    tail: _GENO_STACK_TAIL,\n"
        "    header: _GENO_STACK_HEADER,\n"
        "    await_key: _GENO_TRACE_AWAIT_KEY,\n"
        "    argument_limit: _GENO_ARGUMENT_DISPLAY_LIMIT,\n"
        "    host_header: _GENO_HOST_TRACEBACK_HEADER,\n"
        "}));\n"
    )


@needs_node
def test_the_shared_constants_agree() -> None:
    expected = {
        "head": runtime_report.STACK_HEAD,
        "tail": runtime_report.STACK_TAIL,
        "header": runtime_report.STACK_HEADER,
        "await_key": runtime_report.TRACE_AWAIT_KEY,
        "argument_limit": runtime_report.ARGUMENT_DISPLAY_LIMIT,
        "host_header": "Host traceback (GENO_TRACEBACK=host):",
    }
    assert _js_constants() == expected
    python = {
        "head": python_prelude._GENO_STACK_HEAD,
        "tail": python_prelude._GENO_STACK_TAIL,
        "header": python_prelude._GENO_STACK_HEADER,
        "await_key": python_prelude._GENO_TRACE_AWAIT_KEY,
        "argument_limit": python_prelude._GENO_ARGUMENT_DISPLAY_LIMIT,
    }
    assert python == {key: expected[key] for key in python}
    # The clause text is cut at parse time, from the one definition.
    assert parser_base.CLAUSE_TEXT_LIMIT is runtime_report.CLAUSE_TEXT_LIMIT


def test_every_host_traceback_header_is_the_same_text() -> None:
    from pathlib import Path

    root = Path(runtime_report.__file__).parent
    header = "Host traceback (GENO_TRACEBACK=host):"
    for relative in ("cli/run.py", "compiler.py"):
        assert header in (root / relative).read_text(encoding="utf-8"), relative
    assert re.search(
        r'^const _GENO_HOST_TRACEBACK_HEADER = "' + re.escape(header) + r'";$',
        JS_RUNTIME_PRELUDE,
        re.M,
    )


@needs_node
@pytest.mark.parametrize(
    "table",
    [
        "",
        "G\t2\nE\t1",
        "G\t1",
        "G\t1\nX\t1\nE\t3",
        "G\t1\nT\t0\t7\t1\t1\nE\t3",
        "G\t1\nS\tnot-a-line\t0\nE\t3",
        "G\t1\nD\t1\t2\tmain\nE\t3",
    ],
)
def test_both_preludes_reject_a_malformed_or_newer_table(table: str) -> None:
    with pytest.raises((ValueError, KeyError)):
        python_prelude._geno_parse_trace(table)
    outcome = _run_js(
        "let outcome;\n"
        f"try {{ _genoParseTrace({json.dumps(table)}); outcome = 'parsed'; }}\n"
        "catch (e) { outcome = 'rejected'; }\n"
        "console.log(JSON.stringify(outcome));\n"
    )
    assert outcome == "rejected"


@needs_node
def test_both_preludes_parse_a_table_alike() -> None:
    table = "\n".join(
        [
            "G\t1",
            "F\t0\tdir/we\\tird\\nname\\\\.geno",
            "D\t3\t14\tcheck\tcheck",
            "D\t15\t23\tShow_show_Point\tPoint.show",
            "T\t0\t0\t3\t5",
            "T\t1\t0\t4\t9",
            "S\t3\t-1",
            "S\t5\t0",
            "S\t6\t1",
            "C\t1\tpanic\t4\t9",
            "C\t1\t<await>\t4\t2",
            "E\t30",
        ]
    )
    functions, statements, marks, candidates, end = python_prelude._geno_parse_trace(
        table
    )
    python = {
        "functions": [list(function) for function in functions],
        "statements": {str(key): list(value) for key, value in statements.items()},
        "marks": [list(mark) for mark in marks],
        "candidates": {
            str(key): [list(site) for site in sites]
            for key, sites in candidates.items()
        },
        "end": end,
    }
    js = _run_js(
        f"const [functions, statements, marks, candidates, end] = "
        f"_genoParseTrace({json.dumps(table)});\n"
        "console.log(JSON.stringify({functions, marks, end,\n"
        "    statements: Object.fromEntries(statements),\n"
        "    candidates: Object.fromEntries(candidates)}));\n"
    )
    assert js == python
    assert python["statements"]["0"][0] == "dir/we\tird\nname\\.geno"
