#!/usr/bin/env python3
"""Profile Geno's project, tooling, process, and JavaScript surfaces.

The legacy benchmark laboratory intentionally concentrates on generated-Python
microbenchmarks.  This companion command measures the larger surfaces whose
costs are otherwise hidden by those kernels.  Every selected surface validates
its output before its samples are admitted to the JSON artifact.

The command is an observatory, not a wall-clock CI gate.  Compare artifacts
from alternating, controlled runs on the same host before accepting a change.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import http.client
import json
import math
import os
import queue
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.harness import (  # noqa: E402
    collect_environment_metadata,
    configure_utf8_streams,
    write_json_artifact,
    write_payload_to_stream,
)

SCHEMA_VERSION = 1
SURFACES = ("project", "lsp", "hosted", "javascript", "process_sandbox")
T = TypeVar("T")


def percentile(values: Sequence[int | float], fraction: float) -> float:
    """Return a linearly interpolated percentile for a non-empty sample."""
    if not values:
        raise ValueError("cannot calculate a percentile of no values")
    ordered = sorted(float(value) for value in values)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def summarize(values: Sequence[int | float]) -> dict[str, Any]:
    """Keep raw samples beside median and tail summaries."""
    if not values:
        raise ValueError("cannot summarize no values")
    numeric = [float(value) for value in values]
    median = statistics.median(numeric)
    return {
        "count": len(numeric),
        "median": median,
        "mad": statistics.median(abs(value - median) for value in numeric),
        "p95": percentile(numeric, 0.95),
        "p99": percentile(numeric, 0.99),
        "min": min(numeric),
        "max": max(numeric),
        "samples": numeric,
    }


def timed(function: Callable[[], T]) -> tuple[T, int]:
    """Return a function result and elapsed nanoseconds."""
    started = time.perf_counter_ns()
    result = function()
    return result, time.perf_counter_ns() - started


def node_executable() -> str:
    """Resolve Node once so subprocess calls never depend on shell lookup."""
    executable = shutil.which("node")
    if executable is None:
        raise RuntimeError("Node.js is required for this surface")
    return executable


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("page_fault_count", wintypes.DWORD),
        ("peak_working_set_size", ctypes.c_size_t),
        ("working_set_size", ctypes.c_size_t),
        ("quota_peak_paged_pool_usage", ctypes.c_size_t),
        ("quota_paged_pool_usage", ctypes.c_size_t),
        ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
        ("quota_non_paged_pool_usage", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage_count", wintypes.DWORD),
        ("process_id", wintypes.DWORD),
        ("default_heap_id", ctypes.c_size_t),
        ("module_id", wintypes.DWORD),
        ("thread_count", wintypes.DWORD),
        ("parent_process_id", wintypes.DWORD),
        ("base_priority", ctypes.c_long),
        ("flags", wintypes.DWORD),
        ("executable", ctypes.c_wchar * 260),
    ]


def _memory_for_handle(handle: int) -> dict[str, int] | None:
    if os.name != "nt":
        return None
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
        wintypes.HANDLE(handle), ctypes.byref(counters), counters.cb
    )
    if not ok:
        return None
    return {
        "working_set_bytes": int(counters.working_set_size),
        "peak_working_set_bytes": int(counters.peak_working_set_size),
        "private_bytes": int(counters.private_usage),
        "peak_pagefile_bytes": int(counters.peak_pagefile_usage),
    }


def _open_process(pid: int) -> int | None:
    if os.name != "nt":
        return None
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        0x1000 | 0x0010,
        False,
        pid,
    )
    return int(handle) if handle else None


def _close_handle(handle: int | None) -> None:
    if handle and os.name == "nt":
        ctypes.windll.kernel32.CloseHandle(  # type: ignore[attr-defined]
            wintypes.HANDLE(handle)
        )


def _process_parents() -> dict[int, int]:
    """Snapshot Windows process parent links for descendant RSS sampling."""
    if os.name != "nt":
        return {}
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if snapshot == invalid_handle:
        return {}
    parents: dict[int, int] = {}
    entry = _ProcessEntry32W()
    entry.size = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.process_id)] = int(entry.parent_process_id)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _close_handle(int(snapshot))
    return parents


def _process_memory(process: subprocess.Popen[str]) -> dict[str, int] | None:
    """Read exact retained Windows process counters when they are available."""
    if os.name != "nt":
        return None
    handle = getattr(process, "_handle", None)
    if handle is None:
        return None
    return _memory_for_handle(int(handle))


def _summarize_optional_memory(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    peaks = [
        int(row["memory"]["peak_working_set_bytes"])
        for row in rows
        if row.get("memory") is not None
    ]
    return summarize(peaks) if peaks else None


def _communicate_with_timeout(
    process: subprocess.Popen[str], *, timeout: float, context: str
) -> tuple[str, str]:
    """Communicate by a deadline and always kill/reap a timed-out child."""
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.communicate()
        raise RuntimeError(f"{context} timed out after {timeout:g} seconds") from error


def _terminate_and_reap(
    process: subprocess.Popen[str], *, timeout: float
) -> tuple[str, str]:
    """Terminate a long-lived helper, escalating to kill while always reaping."""
    if process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate()


def _readline_with_timeout(
    process: subprocess.Popen[str], *, timeout: float, context: str
) -> str:
    """Read one worker protocol line without an unbounded pipe wait."""
    if process.stdout is None:
        raise RuntimeError(f"{context} stdout unavailable")
    stream = process.stdout
    result: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            result.put(stream.readline())
        except BaseException as error:  # worker-protocol thread must report all exits
            result.put(error)

    threading.Thread(target=read, daemon=True).start()
    try:
        value = result.get(timeout=timeout)
    except queue.Empty as error:
        _terminate_and_reap(process, timeout=5)
        raise RuntimeError(
            f"{context} was not ready after {timeout:g} seconds"
        ) from error
    if isinstance(value, BaseException):
        _terminate_and_reap(process, timeout=5)
        raise RuntimeError(f"{context} readiness read failed") from value
    if value == "":
        _, stderr = _terminate_and_reap(process, timeout=5)
        raise RuntimeError(f"{context} exited before readiness: {stderr[-500:]}")
    return value


def make_project_fixture(root: Path, module_count: int = 12) -> dict[str, Any]:
    """Create a deterministic linear multi-module project."""
    root.mkdir(parents=True, exist_ok=False)
    modules = [f"M{index:02d}" for index in range(module_count)]
    files = ["Main", *modules]
    (root / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ['
        + ", ".join(json.dumps(name) for name in files)
        + "]\n",
        encoding="utf-8",
    )
    for index, name in enumerate(modules):
        if index == 0:
            imports = ""
            expression = "value + 1"
        else:
            previous = modules[index - 1]
            imports = f"import {previous}\n"
            expression = f"step{index - 1}(value) + 1"
        (root / f"{name}.geno").write_text(
            imports
            + '@untested("surface laboratory fixture")\n'
            + f"func step{index}(value: Int) -> Int\n"
            + f"    return {expression}\n"
            + f"end func step{index}\n",
            encoding="utf-8",
        )
    last = module_count - 1
    main_source = (
        f"import {modules[-1]}\n"
        '@untested("surface laboratory fixture")\n'
        "func main() -> Int\n"
        f"    return step{last}(0)\n"
        "end func main\n"
    )
    (root / "Main.geno").write_text(main_source, encoding="utf-8")
    return {
        "root": str(root),
        "module_count": module_count + 1,
        "expected_output": str(module_count),
        "main_source_sha256": hashlib.sha256(main_source.encode()).hexdigest(),
    }


def _project_worker(fixture: Path, repetitions: int) -> dict[str, Any]:
    import_started = time.perf_counter_ns()
    from geno.compiler import Compiler
    from geno.dependency_graph import DependencyGraph
    from geno.js_compiler import JSCompiler
    from geno.project_graph import ProjectGraph
    from geno.typechecker import TypeChecker

    import_ns = time.perf_counter_ns() - import_started

    def pipeline() -> tuple[dict[str, int], str, str, Any]:
        project, discover_ns = timed(lambda: ProjectGraph.discover(fixture))
        graph, resolve_ns = timed(lambda: DependencyGraph.resolve(project))
        _, typecheck_ns = timed(lambda: TypeChecker().check_project_graph(graph))
        python_code, python_codegen_ns = timed(
            lambda: Compiler().compile_project(graph)
        )
        javascript_code, javascript_codegen_ns = timed(
            lambda: JSCompiler().compile_project(graph)
        )
        phases = {
            "discover_ns": discover_ns,
            "resolve_ns": resolve_ns,
            "typecheck_ns": typecheck_ns,
            "python_codegen_ns": python_codegen_ns,
            "javascript_codegen_ns": javascript_codegen_ns,
        }
        phases["pipeline_ns"] = sum(phases.values())
        return phases, python_code, javascript_code, graph

    cold, python_code, javascript_code, graph = pipeline()
    warm_rows: list[dict[str, int]] = []
    hashes = {
        (
            hashlib.sha256(python_code.encode()).hexdigest(),
            hashlib.sha256(javascript_code.encode()).hexdigest(),
        )
    }
    for _ in range(repetitions):
        phases, python_code, javascript_code, graph = pipeline()
        warm_rows.append(phases)
        hashes.add(
            (
                hashlib.sha256(python_code.encode()).hexdigest(),
                hashlib.sha256(javascript_code.encode()).hexdigest(),
            )
        )

    python_path = fixture / "surface_out.py"
    javascript_path = fixture / "surface_out.js"
    python_path.write_text(python_code, encoding="utf-8")
    javascript_path.write_text(javascript_code, encoding="utf-8")
    python_run = subprocess.run(  # noqa: S603
        [sys.executable, str(python_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    javascript_run = subprocess.run(  # noqa: S603
        [node_executable(), str(javascript_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
    )
    expected = str(len(graph.sorted_modules) - 1)
    passed = (
        python_run.returncode == 0
        and javascript_run.returncode == 0
        and python_run.stdout.strip() == expected
        and javascript_run.stdout.strip() == expected
        and len(hashes) == 1
    )
    return {
        "protocol": {
            "cold": "first pipeline in a fresh process and dependency cache",
            "warm": "same process and unchanged files; new graph/check/compiler instances",
            "clock": "time.perf_counter_ns",
        },
        "import_ns": import_ns,
        "cold": cold,
        "warm": {
            name: summarize([row[name] for row in warm_rows]) for name in warm_rows[0]
        },
        "correctness": {
            "passed": passed,
            "module_count": len(graph.sorted_modules),
            "deterministic_hash_pairs": len(hashes),
            "python_exit": python_run.returncode,
            "python_stdout": python_run.stdout.strip(),
            "javascript_exit": javascript_run.returncode,
            "javascript_stdout": javascript_run.stdout.strip(),
        },
        "output": {
            "python_bytes": len(python_code.encode()),
            "javascript_bytes": len(javascript_code.encode()),
            "python_sha256": hashlib.sha256(python_code.encode()).hexdigest(),
            "javascript_sha256": hashlib.sha256(javascript_code.encode()).hexdigest(),
        },
    }


def _lsp_worker(fixture: Path, repetitions: int) -> dict[str, Any]:
    import_started = time.perf_counter_ns()
    from lsprotocol import types
    from pygls.workspace import Workspace

    from geno.lsp_server import create_server

    import_ns = time.perf_counter_ns() - import_started
    main_path = fixture / "Main.geno"
    source = main_path.read_text(encoding="utf-8")
    uri = main_path.resolve().as_uri()
    pygls_server = create_server(diag_debounce_sec=0)
    pygls_server.lsp._workspace = Workspace(
        fixture.resolve().as_uri(), sync_kind=types.TextDocumentSyncKind.Full
    )
    published: list[tuple[str, int]] = []
    pygls_server.publish_diagnostics = lambda target, diagnostics: published.append(
        (target, len(diagnostics))
    )
    did_open = pygls_server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
    completion = pygls_server.lsp._get_handler(types.TEXT_DOCUMENT_COMPLETION)
    hover = pygls_server.lsp._get_handler(types.TEXT_DOCUMENT_HOVER)
    open_params = types.DidOpenTextDocumentParams(
        text_document=types.TextDocumentItem(
            uri=uri, language_id="geno", version=1, text=source
        )
    )
    _, cold_open_ns = timed(lambda: did_open(open_params))
    server = pygls_server._geno_language_server
    identifier = types.TextDocumentIdentifier(uri=uri)
    target_line = next(
        index for index, line in enumerate(source.splitlines()) if "return step" in line
    )
    target_match = re.search(r"\bstep\d+\b", source.splitlines()[target_line])
    if target_match is None:
        raise RuntimeError("surface fixture is missing its final step call")
    target_name = target_match.group()
    target_column = source.splitlines()[target_line].index(target_name) + 2
    completion_params = types.CompletionParams(
        text_document=identifier,
        position=types.Position(line=target_line, character=4),
    )
    hover_params = types.HoverParams(
        text_document=identifier,
        position=types.Position(line=target_line, character=target_column),
    )
    cold_hover, cold_hover_ns = timed(lambda: hover(hover_params))
    completion_samples: list[int] = []
    hover_samples: list[int] = []
    project_view_samples: list[int] = []
    validation_samples: list[int] = []
    completion_result = None
    hover_result = cold_hover
    for _ in range(repetitions):
        completion_result, elapsed = timed(lambda: completion(completion_params))
        completion_samples.append(elapsed)
        hover_result, elapsed = timed(lambda: hover(hover_params))
        hover_samples.append(elapsed)
        _, elapsed = timed(lambda: server._project_view_for_uri(uri))
        project_view_samples.append(elapsed)
    for _ in range(max(3, repetitions // 2)):
        _, elapsed = timed(lambda: server._publish_diagnostics(uri, source))
        validation_samples.append(elapsed)
    if completion_result is None:
        raise RuntimeError("LSP completion handler returned no result")
    labels = {item.label for item in completion_result.items}
    hover_text = (
        hover_result.contents.value
        if hover_result is not None and hasattr(hover_result.contents, "value")
        else ""
    )
    return {
        "protocol": {
            "transport": "direct registered pygls handlers; JSON-RPC excluded",
            "cold": "new server/workspace and first didOpen/hover",
            "warm": "same server/document and cached project view",
        },
        "import_ns": import_ns,
        "cold_open_ns": cold_open_ns,
        "cold_hover_ns": cold_hover_ns,
        "warm": {
            "completion_ns": summarize(completion_samples),
            "hover_ns": summarize(hover_samples),
            "project_view_ns": summarize(project_view_samples),
            "validation_ns": summarize(validation_samples),
        },
        "correctness": {
            "passed": target_name in labels
            and target_name in hover_text
            and sum(count for _, count in published) == 0,
            "target_name": target_name,
            "completion_contains_target": target_name in labels,
            "hover_contains_target": target_name in hover_text,
            "published_batches": len(published),
            "published_diagnostic_total": sum(count for _, count in published),
        },
    }


def _process_sandbox_worker(repetitions: int) -> dict[str, Any]:
    import_started = time.perf_counter_ns()
    from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

    import_ns = time.perf_counter_ns() - import_started

    class TimedSandbox(ProcessSandbox):
        def __init__(self) -> None:
            super().__init__(
                ProcessSandboxConfig(timeout=5.0, max_memory_bytes=256 * 1024 * 1024)
            )
            self.phases: dict[str, int] = {}

        def _record(self, name: str, function: Callable[[], T]) -> T:
            value, elapsed = timed(function)
            self.phases[name] = elapsed
            return value

        def _create_worker_script(self) -> str:
            return cast(
                str,
                self._record("worker_script_ns", super()._create_worker_script),
            )

        def _create_worker_command(self) -> list[str]:
            return cast(
                list[str],
                self._record("worker_command_ns", super()._create_worker_command),
            )

        def _frame_worker_input(self, worker_script: str, code: str) -> str:
            return cast(
                str,
                self._record(
                    "frame_input_ns",
                    lambda: super(TimedSandbox, self)._frame_worker_input(
                        worker_script, code
                    ),
                ),
            )

        def _run_worker(
            self,
            cmd: list[str],
            code: str,
            config_overrides: dict[str, Any] | None = None,
        ) -> tuple[int, str, str, bool]:
            return cast(
                tuple[int, str, str, bool],
                self._record(
                    "worker_lifecycle_ns",
                    lambda: super(TimedSandbox, self)._run_worker(
                        cmd, code, config_overrides
                    ),
                ),
            )

    rows = []
    for _ in range(repetitions + 1):
        sandbox = TimedSandbox()
        started = time.perf_counter_ns()
        result, output, error = sandbox.execute("__result__ = 6 * 7")
        total_ns = time.perf_counter_ns() - started
        measured = sum(sandbox.phases.values())
        rows.append(
            {
                **sandbox.phases,
                "total_ns": total_ns,
                "validation_and_envelope_residual_ns": total_ns - measured,
                "result": result,
                "output": output,
                "error": error,
            }
        )
    warm_rows = rows[1:]
    phase_names = [
        name
        for name, value in warm_rows[0].items()
        if name.endswith("_ns") and isinstance(value, int)
    ]
    return {
        "protocol": {
            "surface": "ProcessSandbox.execute strict raw-Python path",
            "cold": "first isolated worker",
            "warm": "new isolated worker per repetition",
        },
        "import_ns": import_ns,
        "cold": rows[0],
        "warm": {
            name: summarize([row[name] for row in warm_rows]) for name in phase_names
        },
        "correctness": {
            "passed": all(
                row["result"] == 42 and row["output"] == "" and row["error"] is None
                for row in rows
            )
        },
    }


def _prepare_javascript(fixture: Path) -> dict[str, Any]:
    from geno.js_compiler import compile_to_js

    source = """
func step(value: Int) -> Int
    example 7 -> 28
    return ((value * 3) + 7) % 1000003
end func step

func main() -> Int
    return step(7)
end func main
"""
    code, compile_ns = timed(lambda: compile_to_js(source))
    base_path = fixture / "steady_generated.js"
    benchmark_path = fixture / "steady_benchmark.js"
    base_path.write_text(code, encoding="utf-8")
    benchmark = r"""
const __iterations = Number(process.argv[2]);
const __warmup = Number(process.argv[3]);
const __order = process.argv[4];
const __control = (value) => ((value * 3) + 7) % 1000003;
let __generatedChecksum = 0;
let __controlChecksum = 0;
for (let __i = 0; __i < __warmup; __i++) {
  __generatedChecksum = (__generatedChecksum + step(__i & 1023)) >>> 0;
  __controlChecksum = (__controlChecksum + __control(__i & 1023)) >>> 0;
}
let __generatedNs = 0;
let __controlNs = 0;
const __runGenerated = () => {
  const start = process.hrtime.bigint();
  for (let __i = 0; __i < __iterations; __i++) {
    __generatedChecksum = (__generatedChecksum + step(__i & 1023)) >>> 0;
  }
  __generatedNs = Number(process.hrtime.bigint() - start);
};
const __runControl = () => {
  const start = process.hrtime.bigint();
  for (let __i = 0; __i < __iterations; __i++) {
    __controlChecksum = (__controlChecksum + __control(__i & 1023)) >>> 0;
  }
  __controlNs = Number(process.hrtime.bigint() - start);
};
if (__order === "generated-first") {
  __runGenerated();
  __runControl();
} else {
  __runControl();
  __runGenerated();
}
console.error(JSON.stringify({
  iterations: __iterations,
  order: __order,
  generated_ns: __generatedNs,
  control_ns: __controlNs,
  generated_checksum: __generatedChecksum,
  control_checksum: __controlChecksum
}));
"""
    benchmark_path.write_text(code + benchmark, encoding="utf-8")
    return {
        "compile_ns": compile_ns,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "generated_sha256": hashlib.sha256(code.encode()).hexdigest(),
        "generated_bytes": len(code.encode()),
        "base_path": str(base_path),
        "benchmark_path": str(benchmark_path),
    }


def _run_command(command: Sequence[str]) -> dict[str, Any]:
    started = time.perf_counter_ns()
    process = subprocess.Popen(  # noqa: S603
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout, stderr = _communicate_with_timeout(
        process, timeout=60, context="surface command"
    )
    return {
        "wall_ns": time.perf_counter_ns() - started,
        "exit": process.returncode,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "memory": _process_memory(process),
    }


def _javascript_surface(fixture: Path, repetitions: int) -> dict[str, Any]:
    prepared = _prepare_javascript(fixture)
    node = node_executable()
    empty_rows = [_run_command((node, "-e", "")) for _ in range(repetitions)]
    load_rows = [
        _run_command((node, prepared["base_path"])) for _ in range(repetitions)
    ]
    benchmark_rows = []
    for repetition in range(repetitions):
        order = "generated-first" if repetition % 2 == 0 else "control-first"
        row = _run_command(
            (node, prepared["benchmark_path"], "1000000", "50000", order)
        )
        envelope = json.loads(row["stderr"].splitlines()[-1])
        benchmark_rows.append({**row, "envelope": envelope})
    generated = [
        row["envelope"]["generated_ns"] / row["envelope"]["iterations"]
        for row in benchmark_rows
    ]
    control = [
        row["envelope"]["control_ns"] / row["envelope"]["iterations"]
        for row in benchmark_rows
    ]
    correctness = {
        "empty_all_exit_zero": all(row["exit"] == 0 for row in empty_rows),
        "load_all_exit_zero": all(row["exit"] == 0 for row in load_rows),
        "load_all_output_28": all(row["stdout"] == "28" for row in load_rows),
        "benchmark_all_exit_zero": all(row["exit"] == 0 for row in benchmark_rows),
        "benchmark_all_output_28": all(row["stdout"] == "28" for row in benchmark_rows),
        "checksums_match": all(
            row["envelope"]["generated_checksum"] == row["envelope"]["control_checksum"]
            for row in benchmark_rows
        ),
        "orders_alternate": [row["envelope"]["order"] for row in benchmark_rows]
        == [
            "generated-first" if repetition % 2 == 0 else "control-first"
            for repetition in range(repetitions)
        ],
    }
    correctness["passed"] = all(correctness.values())
    return {
        "protocol": {
            "steady": "50k warmup then 1m generated and hand-control calls; order alternates",
            "startup": "fresh empty Node processes",
            "load": "fresh processes parsing/evaluating the generated bundle",
        },
        "prepared": prepared,
        "empty_node_wall_ns": summarize([row["wall_ns"] for row in empty_rows]),
        "empty_node_peak_working_set_bytes": _summarize_optional_memory(empty_rows),
        "generated_load_wall_ns": summarize([row["wall_ns"] for row in load_rows]),
        "generated_load_peak_working_set_bytes": _summarize_optional_memory(load_rows),
        "steady_generated_ns_per_op": summarize(generated),
        "steady_control_ns_per_op": summarize(control),
        "steady_calibrated_ns_per_op": summarize(
            [
                generated_value - control_value
                for generated_value, control_value in zip(generated, control)
            ]
        ),
        "benchmark_process_wall_ns": summarize(
            [row["wall_ns"] for row in benchmark_rows]
        ),
        "benchmark_peak_working_set_bytes": _summarize_optional_memory(benchmark_rows),
        "raw_rows": {
            "empty": empty_rows,
            "load": load_rows,
            "benchmark": benchmark_rows,
        },
        "correctness": correctness,
    }


class _ProcessTreeSampler:
    """Sample descendant RSS on Windows; record unavailability elsewhere."""

    def __init__(self, root_pid: int, interval_seconds: float = 0.002) -> None:
        self.root_pid = root_pid
        self.interval_seconds = interval_seconds
        self.max_aggregate_working_set = 0
        self.sample_count = 0
        self.handles: dict[int, int] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        if os.name == "nt":
            self._thread.start()

    def stop(self) -> dict[str, Any]:
        if os.name != "nt":
            return {"available": False, "reason": "Windows Toolhelp API required"}
        self._stop.set()
        self._thread.join()
        processes: list[dict[str, Any]] = []
        for pid, handle in sorted(self.handles.items()):
            processes.append({"pid": pid, "memory": _memory_for_handle(handle)})
            _close_handle(handle)
        child_peaks = [
            row["memory"]["peak_working_set_bytes"]
            for row in processes
            if row["pid"] != self.root_pid and row["memory"] is not None
        ]
        return {
            "available": True,
            "sampling_interval_seconds": self.interval_seconds,
            "sample_count": self.sample_count,
            "discovered_process_count": len(processes),
            "discovered_child_count": sum(
                row["pid"] != self.root_pid for row in processes
            ),
            "max_aggregate_working_set_bytes": self.max_aggregate_working_set,
            "max_child_peak_working_set_bytes": max(child_peaks, default=None),
            "processes": processes,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            parents = _process_parents()
            descendants = {self.root_pid}
            changed = True
            while changed:
                changed = False
                for pid, parent in parents.items():
                    if parent in descendants and pid not in descendants:
                        descendants.add(pid)
                        changed = True
            aggregate = 0
            for pid in descendants:
                if pid not in self.handles:
                    handle = _open_process(pid)
                    if handle is not None:
                        self.handles[pid] = handle
                handle = self.handles.get(pid)
                if handle is not None:
                    memory = _memory_for_handle(handle)
                    if memory is not None:
                        aggregate += memory["working_set_bytes"]
            self.max_aggregate_working_set = max(
                self.max_aggregate_working_set, aggregate
            )
            self.sample_count += 1
            self._stop.wait(self.interval_seconds)


def _server_worker() -> None:
    from geno.server import create_server

    server = create_server(
        "127.0.0.1",
        0,
        bind_and_activate=True,
        startup_errors=[],
        rate_limit_requests=10_000,
    )
    host, port = server.server_address
    write_payload_to_stream({"ready": True, "host": host, "port": port}, sys.stdout)
    sys.stdout.flush()
    server.serve_forever()


def _hosted_surface(script: Path, repetitions: int) -> dict[str, Any]:
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, str(script), "--worker", "server"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    sampler: _ProcessTreeSampler | None = None
    sampled_memory: dict[str, Any] = {
        "available": False,
        "reason": "server did not reach readiness",
    }
    server_memory = None
    stdout = ""
    stderr = ""
    rows: list[dict[str, Any]] = []
    try:
        ready = json.loads(
            _readline_with_timeout(process, timeout=20, context="hosted server worker")
        )
        sampler = _ProcessTreeSampler(process.pid)
        sampler.start()
        payload = json.dumps(
            {
                "source": "func main() -> Int\n    return 42\nend func\n",
                "filename": "surface-lab.geno",
                "timeout": 2.0,
                "max_steps": 10_000,
            }
        ).encode()
        for _ in range(repetitions + 1):
            connection = http.client.HTTPConnection(
                ready["host"], ready["port"], timeout=15
            )
            try:
                started = time.perf_counter_ns()
                connection.request(
                    "POST",
                    "/run",
                    body=payload,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                body = json.loads(response.read())
                elapsed = time.perf_counter_ns() - started
            finally:
                connection.close()
            rows.append({"wall_ns": elapsed, "status": response.status, "body": body})
    finally:
        if sampler is not None:
            sampled_memory = sampler.stop()
        server_memory = _process_memory(process)
        stdout, stderr = _terminate_and_reap(process, timeout=20)
    warm = rows[1:]
    phase_names = ("total_ms", "lex_ms", "parse_ms", "typecheck_ms", "run_ms")
    timed_warm = [row for row in warm if "timing" in row["body"]]
    correctness = {
        "all_status_200": all(row["status"] == 200 for row in rows),
        "all_ok": all(row["body"].get("ok") is True for row in rows),
        "all_value_42": all(row["body"].get("value") == 42 for row in rows),
        "all_no_diagnostics": all(not row["body"].get("diagnostics") for row in rows),
    }
    correctness["passed"] = all(correctness.values())
    return {
        "protocol": {
            "surface": "real loopback HTTP POST /run",
            "connection": "new connection and new spawned worker per request",
        },
        "cold": rows[0],
        "warm_wall_ns": summarize([row["wall_ns"] for row in warm]),
        "warm_internal_ms": {
            name: summarize([row["body"]["timing"][name] for row in timed_warm])
            for name in phase_names
        }
        if timed_warm
        else {},
        "correctness": correctness,
        "server_process_memory": server_memory,
        "sampled_server_memory": sampled_memory,
        "server_stdout_tail": stdout,
        "server_stderr": stderr,
        "raw_rows": rows,
    }


def _run_worker(
    script: Path,
    worker: str,
    fixture: Path,
    repetitions: int,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(script),
        "--worker",
        worker,
        "--fixture",
        str(fixture),
        "--repetitions",
        str(repetitions),
    ]
    environment = dict(os.environ)
    if cache_dir is not None:
        environment["GENO_CACHE_DIR"] = str(cache_dir)
    started = time.perf_counter_ns()
    process = subprocess.Popen(  # noqa: S603
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
    )
    stdout, stderr = _communicate_with_timeout(
        process, timeout=300, context=f"surface worker {worker!r}"
    )
    wall_ns = time.perf_counter_ns() - started
    memory = _process_memory(process)
    if process.returncode != 0:
        raise RuntimeError(
            f"surface worker {worker!r} failed ({process.returncode}): "
            f"{stderr[-1000:]}\n{stdout[-1000:]}"
        )
    result = cast(dict[str, Any], json.loads(stdout))
    result["fresh_process_wall_ns"] = wall_ns
    result["process_memory"] = memory
    result["stderr"] = stderr
    return result


def _run_fresh_workers(
    script: Path,
    worker: str,
    fixture: Path,
    repetitions: int,
    fresh_process_repetitions: int,
    *,
    cache_root: Path | None = None,
) -> dict[str, Any]:
    """Collect several source-cold worker runs and retain every raw payload."""
    runs = [
        _run_worker(
            script,
            worker,
            fixture,
            repetitions,
            cache_dir=(cache_root / f"run-{index}" if cache_root else None),
        )
        for index in range(fresh_process_repetitions)
    ]
    result = dict(runs[0])
    output_hash_pairs = {
        (
            run["output"]["python_sha256"],
            run["output"]["javascript_sha256"],
        )
        for run in runs
        if worker == "project"
    }
    hashes_deterministic = worker != "project" or len(output_hash_pairs) == 1
    top_level = {
        name: summarize([run[name] for run in runs])
        for name, value in runs[0].items()
        if isinstance(value, int) and name.endswith("_ns")
    }
    cold = runs[0].get("cold")
    cold_summary = (
        {
            name: summarize([run["cold"][name] for run in runs])
            for name, value in cold.items()
            if isinstance(value, int) and name.endswith("_ns")
        }
        if isinstance(cold, dict)
        else {}
    )
    result["fresh_process_evidence"] = {
        "count": fresh_process_repetitions,
        "top_level_ns": top_level,
        "cold_ns": cold_summary,
        "peak_working_set_bytes": _summarize_optional_memory(
            [{"memory": run["process_memory"]} for run in runs]
        ),
        "output_hash_pairs": [list(pair) for pair in sorted(output_hash_pairs)],
        "runs": runs,
    }
    result["correctness"] = {
        **result["correctness"],
        "passed": all(run["correctness"]["passed"] for run in runs)
        and hashes_deterministic,
        "fresh_process_runs_passed": sum(
            bool(run["correctness"]["passed"]) for run in runs
        ),
        "fresh_process_hashes_deterministic": hashes_deterministic,
    }
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile correctness-checked Geno end-to-end surfaces.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "surfaces",
        nargs="*",
        choices=SURFACES,
        help="surface selection; no values runs all surfaces",
    )
    parser.add_argument("--repetitions", type=int, default=21)
    parser.add_argument(
        "--fresh-process-repetitions",
        type=int,
        default=3,
        help="independent cold workers for project, LSP, and ProcessSandbox",
    )
    parser.add_argument("--modules", type=int, default=12)
    parser.add_argument("--json", metavar="PATH")
    parser.add_argument(
        "--keep-fixture", action="store_true", help="retain generated fixture files"
    )
    parser.add_argument(
        "--worker",
        choices=("project", "lsp", "process_sandbox", "server"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--fixture", type=Path, help=argparse.SUPPRESS)
    return parser


def _worker_main(args: argparse.Namespace) -> int:
    if args.worker == "project":
        payload = _project_worker(args.fixture, args.repetitions)
    elif args.worker == "lsp":
        payload = _lsp_worker(args.fixture, args.repetitions)
    elif args.worker == "process_sandbox":
        payload = _process_sandbox_worker(args.repetitions)
    elif args.worker == "server":
        _server_worker()
        return 0
    else:
        raise AssertionError("unknown worker")
    write_payload_to_stream(payload, sys.stdout)
    return 0 if payload["correctness"]["passed"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    configure_utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.repetitions < 2:
        parser.error("repetitions must be at least 2")
    if args.modules < 2:
        parser.error("modules must be at least 2")
    if args.fresh_process_repetitions < 1:
        parser.error("fresh process repetitions must be at least 1")
    if args.worker:
        if args.worker != "server" and args.fixture is None:
            parser.error("worker fixture is required")
        return _worker_main(args)
    if args.json is None:
        parser.error("--json is required")

    selected = list(args.surfaces or SURFACES)
    script = Path(__file__).resolve()
    fixture_root = Path(tempfile.mkdtemp(prefix="geno-surface-lab-"))
    fixture = fixture_root / "project"
    started = time.perf_counter()
    try:
        fixture_metadata = make_project_fixture(fixture, args.modules)
        surface_results: dict[str, Any] = {}
        if "project" in selected:
            surface_results["project"] = _run_fresh_workers(
                script,
                "project",
                fixture,
                args.repetitions,
                args.fresh_process_repetitions,
                cache_root=fixture_root / "project-cache",
            )
        if "lsp" in selected:
            surface_results["lsp"] = _run_fresh_workers(
                script,
                "lsp",
                fixture,
                args.repetitions,
                args.fresh_process_repetitions,
                cache_root=fixture_root / "lsp-cache",
            )
        if "hosted" in selected:
            surface_results["hosted"] = _hosted_surface(script, args.repetitions)
        if "javascript" in selected:
            node_executable()
            surface_results["javascript"] = _javascript_surface(
                fixture, args.repetitions
            )
        if "process_sandbox" in selected:
            surface_results["process_sandbox"] = _run_fresh_workers(
                script,
                "process_sandbox",
                fixture,
                args.repetitions,
                args.fresh_process_repetitions,
            )
        correctness = {
            name: bool(result["correctness"]["passed"])
            for name, result in surface_results.items()
        }
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "metadata": collect_environment_metadata(REPO_ROOT),
            "configuration": {
                "selected_surfaces": selected,
                "repetitions": args.repetitions,
                "fresh_process_repetitions": args.fresh_process_repetitions,
                "modules": args.modules,
                "clock": "time.perf_counter_ns",
                "cold_warm_separated": True,
                "fixture_retained": args.keep_fixture,
            },
            "fixture": fixture_metadata,
            "surfaces": surface_results,
            "correctness": correctness,
            "aggregate": {
                "success": bool(correctness) and all(correctness.values()),
                "elapsed_seconds": time.perf_counter() - started,
            },
            "limitations": [
                "LSP timings call registered handlers and exclude JSON-RPC transport.",
                "Hosted samples are sequential loopback requests with a new worker per request.",
                "Generated-JS steady state covers Node and one arithmetic kernel, not browsers.",
                "Peak working set is reported only where retained Windows process handles make it reliable.",
                "Wall-clock observations are evidence artifacts, not mandatory CI thresholds.",
            ],
        }
        write_json_artifact(artifact, args.json)
        if args.json != "-":
            print(
                json.dumps(
                    {
                        "artifact": str(Path(args.json).resolve()),
                        "elapsed_seconds": artifact["aggregate"]["elapsed_seconds"],
                        "correctness": correctness,
                    },
                    indent=2,
                )
            )
        return 0 if artifact["aggregate"]["success"] else 1
    finally:
        if args.keep_fixture:
            print(f"Fixture retained: {fixture_root}", file=sys.stderr)
        else:
            shutil.rmtree(fixture_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
