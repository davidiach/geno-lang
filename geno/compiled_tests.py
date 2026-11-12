"""``geno test --backend python|js``: examples and test blocks in compiled code.

The file or project is compiled with the pipeline ``geno compile`` uses, in a
test-harness build (proposal 0024): every example and ``test`` block is
registered as callable thunks and ``main`` is not called. A small driver then
runs the selected cases one at a time in a subprocess and writes one JSON line
per case to a results file. This module decides, from those raw outcomes, what
passed and what failed, in the terms the interpreter's runner uses.

The subprocess is given the capabilities ``geno test`` grants and nothing
else. Each case has a hard timeout: the parent watches the results file, and a
case that runs past its allowance has its process killed and the remaining
cases continue in a new one.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ast_nodes import FunctionDef
from .base_compiler import TEST_REGISTRY_NAME, CompiledTestCase
from .diagnostics import format_labeled_error
from .harness import HARNESS_CAPABILITIES, CaseOutcome, HarnessResult, SpecViolation
from .panic_outcome import CallFailure, check_panics_example, compiled_limit

if TYPE_CHECKING:
    from .test_runner import FileTestResult

COMPILED_BACKENDS = ("python", "js")

# How much of a value a report shows; the same bound on every backend.
_DISPLAY_LIMIT = 10_000
# Loading the compiled program (the prelude, module constants, and on a busy
# machine the host's own start-up) is not a test: it gets its own allowance.
_STARTUP_TIMEOUT = 120.0
_POLL_SECONDS = 0.01
_ERROR_LINE = re.compile(r"^[\w.]*(Error|Exception)\b[^:]*:")

# The Python launcher. It runs in its own namespace, so no name of the
# program can shadow what it uses, and it reaches the program only through
# the registry and the runtime's diagnostic formatter.
_PYTHON_LAUNCHER = f"""
import asyncio, dataclasses, inspect, json, math, os, runpy, sys, threading, time

def _main():
    with open(os.environ["GENO_TEST_PLAN"], encoding="utf-8") as handle:
        plan = json.load(handle)
    out = open(plan["results"], "a", encoding="utf-8")

    def emit(record):
        out.write(json.dumps(record) + "\\n")
        out.flush()

    namespace = runpy.run_path(sys.argv[1], run_name="__geno_test__")
    tests = namespace[{TEST_REGISTRY_NAME!r}]
    render = namespace["_geno_format_bounded"]

    def show(value):
        return render(value, {_DISPLAY_LIMIT})

    wrappers = {{
        "_GenoArray": "_elements",
        "_GenoVec": "_elements",
        "_GenoMutableMap": "_data",
        "_GenoSet": "_data",
    }}

    def numbers_match(a, b):
        if type(a) is int and type(b) is int:
            return a == b
        try:
            return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
        except OverflowError:
            return bool(a == b)

    def equal(a, b):
        if type(a) in (int, float) and type(b) in (int, float):
            return numbers_match(a, b)
        if isinstance(a, (list, tuple)) and type(a) is type(b):
            return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
        if isinstance(a, dict) and isinstance(b, dict):
            return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
        if dataclasses.is_dataclass(a) and not isinstance(a, type) and type(a) is type(b):
            return all(
                equal(getattr(a, f.name), getattr(b, f.name))
                for f in dataclasses.fields(a)
            )
        # The runtime's own collections: an Array or Vec holds a list, a
        # MutableMap a dict, a Set a set.
        if type(a) is type(b) and type(a).__name__ in wrappers:
            inner_a = getattr(a, wrappers[type(a).__name__])
            inner_b = getattr(b, wrappers[type(a).__name__])
            if isinstance(inner_a, (set, frozenset)):
                return len(inner_a) == len(inner_b) and all(
                    any(equal(x, y) for y in inner_b) for x in inner_a
                )
            return equal(inner_a, inner_b)
        return bool(a == b)

    def describe(exc):
        clause = getattr(exc, "clause", None)
        denied = getattr(exc, "_geno_capability_denied", None)
        env_denied = getattr(exc, "_geno_env_policy_denied", None)
        return {{
            "type": type(exc).__name__,
            "message": str(exc) or type(exc).__name__,
            "panic": getattr(exc, "_geno_panic", None) is True,
            "denied": isinstance(denied, str) or isinstance(env_denied, str),
            "contract": clause if clause in ("requires", "ensures") else None,
        }}

    def finish(value):
        return asyncio.run(value) if inspect.iscoroutine(value) else value

    def run():
        emit({{"event": "ready"}})
        for index in plan["indices"]:
            emit({{"event": "start", "i": index}})
            arguments, function, expected = tests[index]
            record = {{"i": index}}
            phase = "body" if arguments is None else "input"
            started = time.perf_counter()
            try:
                if arguments is None:
                    finish(function())
                    record["outcome"] = "returned"
                else:
                    values = arguments()
                    wanted = None
                    if expected is not None:
                        phase = "expected"
                        wanted = expected()
                    phase = "call"
                    actual = finish(function(*values))
                    phase = "compare"
                    if expected is None:
                        record["outcome"] = "returned"
                    else:
                        record["outcome"] = "equal" if equal(actual, wanted) else "different"
                        record["expected"] = show(wanted)
                    record["actual"] = show(actual)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                record["outcome"] = "threw"
                record["phase"] = phase
                record["error"] = describe(exc)
            record["ms"] = (time.perf_counter() - started) * 1000
            emit(record)
        emit({{"event": "done"}})

    # The depth a standalone compiled program runs with.
    sys.setrecursionlimit(60100)
    if sys.version_info >= (3, 12):
        run()
        return
    failure = []

    def target():
        try:
            run()
        except BaseException as exc:
            failure.append(exc)

    try:
        threading.stack_size(60100 * 2048)
    except (ValueError, RuntimeError):
        pass  # the host's default stack: deep recursion ends sooner
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]

_main()
"""

# The JavaScript driver, appended to the compiled program: its functions are
# not exported, so the driver shares their scope. It is one closure, and the
# only program names it reads are the registry and runtime helpers, which a
# program cannot redefine.
_JS_DRIVER = f"""
;(async () => {{
    const _fs = require("fs");
    const _plan = JSON.parse(_fs.readFileSync(process.env.GENO_TEST_PLAN, "utf8"));
    const _emit = (record) => _fs.appendFileSync(_plan.results, JSON.stringify(record) + "\\n");
    const _show = (value, shape) => _formatValueBounded(value, {_DISPLAY_LIMIT}, shape);
    const _close = (a, b) => {{
        if (a === b) return true;
        if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
        return Math.abs(a - b) <= Math.max(1e-9 * Math.max(Math.abs(a), Math.abs(b)), 1e-12);
    }};
    // Geno equality, with the tolerance an example gives a Float. A
    // JavaScript value does not say whether it is a Float, so the result's
    // static shape does.
    const _equal = (a, b, shape) => {{
        if (shape === "float") {{
            return typeof a === "number" && typeof b === "number" ? _close(a, b) : _valuesEqual(a, b);
        }}
        // The runtime's own collections hold their elements in a field.
        if (a instanceof GenoArray && b instanceof GenoArray) return _equal(a._elements, b._elements, shape);
        if (a instanceof GenoVec && b instanceof GenoVec) return _equal(a._elements, b._elements, shape);
        if (a instanceof GenoMutableMap && b instanceof GenoMutableMap) return _equal(a._data, b._data, shape);
        if (a instanceof GenoSet && b instanceof GenoSet) {{
            const inner = Array.isArray(shape) && shape[0] === "sequence" ? shape[1] : null;
            const left = [...a._data.values()];
            const right = [...b._data.values()];
            return left.length === right.length
                && left.every((item) => right.some((other) => _equal(item, other, inner)));
        }}
        if (Array.isArray(shape) && Array.isArray(a) && Array.isArray(b)) {{
            if (shape[0] === "sequence") {{
                return a.length === b.length && a.every((item, i) => _equal(item, b[i], shape[1]));
            }}
            if (shape[0] === "tuple") {{
                return a.length === b.length && a.every((item, i) => _equal(item, b[i], shape[1][i]));
            }}
        }}
        if (Array.isArray(shape) && shape[0] === "record" && a && b
            && typeof a === "object" && typeof b === "object" && a._tag === b._tag
            && Object.prototype.hasOwnProperty.call(shape[1], a._tag)) {{
            return shape[1][a._tag].every(([name, inner]) => _equal(a[name], b[name], inner));
        }}
        if (Array.isArray(shape) && shape[0] === "map" && a instanceof Map && b instanceof Map) {{
            if (a.size !== b.size) return false;
            for (const [key, value] of a) {{
                const found = _mapFindKey(b, key);
                if (found === _MAP_MISSING || !_equal(value, b.get(found), shape[2])) return false;
            }}
            return true;
        }}
        return _valuesEqual(a, b);
    }};
    const _describe = (error) => {{
        const isError = error instanceof Error;
        const contract = isError && error instanceof _GenoContractViolation ? error.clause : null;
        return {{
            type: isError ? error.name : typeof error,
            message: isError ? (error.message || error.name) : String(error),
            panic: false,
            denied: false,
            contract: contract === "requires" || contract === "ensures" ? contract : null,
        }};
    }};
    _emit({{event: "ready"}});
    for (const index of _plan.indices) {{
        _emit({{event: "start", i: index}});
        const [args, fn, expected, shape] = {TEST_REGISTRY_NAME}[index];
        const record = {{i: index}};
        let phase = args === null ? "body" : "input";
        const started = Date.now();
        try {{
            if (args === null) {{
                await fn();
                record.outcome = "returned";
            }} else {{
                const values = args();
                let wanted = null;
                if (expected !== null) {{
                    phase = "expected";
                    wanted = expected();
                }}
                phase = "call";
                const actual = await fn(...values);
                phase = "compare";
                if (expected === null) {{
                    record.outcome = "returned";
                }} else {{
                    record.outcome = _equal(actual, wanted, shape) ? "equal" : "different";
                    record.expected = _show(wanted, shape);
                }}
                record.actual = _show(actual, shape);
            }}
        }} catch (error) {{
            record.outcome = "threw";
            record.phase = phase;
            record.error = _describe(error);
        }}
        record.ms = Date.now() - started;
        _emit(record);
    }}
    _emit({{event: "done"}});
    process.exit(0);
}})().catch((error) => {{
    process.stderr.write(String(error && error.stack || error) + "\\n");
    process.exit(70);
}});
"""


class BackendUnavailable(Exception):
    """The host a compiled backend runs on is missing."""


def require_backend_host(backend: str) -> None:
    """Raise ``BackendUnavailable`` when *backend* cannot run here."""
    if backend == "js" and shutil.which("node") is None:
        raise BackendUnavailable(
            "--backend js runs the compiled tests with Node.js, and `node` was "
            "not found on PATH"
        )


@dataclass
class _Build:
    """A compiled test-harness artifact and what it registered."""

    code: str
    cases: list[CompiledTestCase]


def _build(dep_graph: Any, backend: str) -> _Build:
    """Compile *dep_graph* for *backend* as ``geno compile`` does, for tests."""
    multi = len(dep_graph.sorted_modules) > 1
    cases: list[CompiledTestCase] = []
    if backend == "python":
        from .compiler import Compiler

        compiler = Compiler()
        compiler.test_cases = cases
        if multi:
            code = compiler.compile_project(dep_graph)
        else:
            code = compiler.compile(dep_graph.parsed[dep_graph.sorted_modules[0]])
        return _Build(code, cases)

    from .js_compiler import JSCompiler

    js_compiler = JSCompiler()
    js_compiler.test_cases = cases
    # The driver uses runtime helpers the program may not: keep them all.
    if multi:
        code = js_compiler.compile_project(dep_graph, tree_shake=False)
    else:
        program = dep_graph.parsed[dep_graph.sorted_modules[0]]
        code = js_compiler.compile(program, tree_shake=False)
    return _Build(code + _JS_DRIVER, cases)


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Every complete record the driver has written so far."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    records = []
    for line in text.split("\n")[:-1]:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _stderr_tail(path: Path) -> str:
    """The line of a dead host's stderr that says what went wrong.

    Python ends a traceback with ``Type: message``. Node prints the error
    first and its own version last, with ``at ...`` frames between.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
        and not line.startswith("Node.js v")
        and not line.lstrip().startswith("at ")
    ]
    named = [line for line in lines if _ERROR_LINE.match(line)]
    if named:
        return named[-1]
    return lines[-1] if lines else ""


class _LoadFailure(Exception):
    """The compiled program did not get as far as running a case."""


def _run_cases(
    backend: str, code: str, indices: list[int], timeout: float | None
) -> dict[int, dict[str, Any]]:
    """Run the registered cases *indices*; return each one's raw record.

    A case that outlives *timeout* is recorded as ``{"outcome": "timeout"}``
    and one whose process died as ``{"outcome": "crashed", ...}``; the cases
    after it run in a new process. Raises ``_LoadFailure`` when the program
    fails before its first case.
    """
    results: dict[int, dict[str, Any]] = {}
    if not indices:
        return results
    with tempfile.TemporaryDirectory(prefix="geno-test-") as scratch:
        root = Path(scratch)
        plan_path = root / "plan.json"
        results_path = root / "results.jsonl"
        stderr_path = root / "stderr.txt"
        caps = ",".join(sorted(HARNESS_CAPABILITIES))
        if backend == "python":
            artifact = root / "program.py"
            launcher = root / "launcher.py"
            launcher.write_text(_PYTHON_LAUNCHER, encoding="utf-8")
            command = [sys.executable, str(launcher), str(artifact)]
        else:
            artifact = root / "program.js"
            command = ["node", str(artifact)]
        artifact.write_text(code, encoding="utf-8", newline="\n")
        if caps:
            command += ["--cap", caps]
        environment = dict(os.environ, GENO_TEST_PLAN=str(plan_path))

        remaining = list(indices)
        while remaining:
            results_path.write_text("", encoding="utf-8")
            plan_path.write_text(
                json.dumps({"results": str(results_path), "indices": remaining}),
                encoding="utf-8",
            )
            with open(stderr_path, "wb") as stderr:
                # The command is this interpreter or `node` on files this
                # module wrote; no shell and no user-supplied argument.
                process = subprocess.Popen(  # noqa: S603
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr,
                    env=environment,
                )
            launched = time.monotonic()
            seen = 0
            ready = False
            current: int | None = None
            current_since = launched
            timed_out = False
            try:
                while True:
                    exited = process.poll() is not None
                    records = _read_records(results_path)
                    for record in records[seen:]:
                        event = record.get("event")
                        if event == "ready":
                            ready = True
                        elif event == "start":
                            current = record["i"]
                            current_since = time.monotonic()
                        elif "i" in record:
                            results[record["i"]] = record
                            current = None
                    seen = len(records)
                    if exited:
                        break
                    now = time.monotonic()
                    if current is None and not ready:
                        if now - launched > _STARTUP_TIMEOUT:
                            raise _LoadFailure(
                                f"the compiled {backend} program did not start "
                                f"within {_STARTUP_TIMEOUT:g} seconds"
                            )
                    elif (
                        current is not None
                        and timeout is not None
                        and now - current_since > timeout
                    ):
                        timed_out = True
                        break
                    time.sleep(_POLL_SECONDS)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()

            if timed_out and current is not None:
                results[current] = {"outcome": "timeout"}
            elif current is not None:
                results[current] = {
                    "outcome": "crashed",
                    "status": process.returncode,
                    "detail": _stderr_tail(stderr_path),
                }
            elif not ready:
                detail = _stderr_tail(stderr_path)
                raise _LoadFailure(
                    detail
                    or f"the compiled {backend} program exited with status "
                    f"{process.returncode} before running a test"
                )
            before = len(remaining)
            remaining = [index for index in remaining if index not in results]
            if remaining and len(remaining) == before:
                raise _LoadFailure(
                    f"the compiled {backend} program stopped without running "
                    "its remaining tests"
                )
    return results


def _classify(backend: str, error: dict[str, Any]) -> CallFailure:
    """How a compiled call that threw ended (see ``geno.panic_outcome``).

    Compiled Python marks its own panics and capability denials. JavaScript
    throws a plain ``Error`` for both, so a denial is recognized there by the
    runtime's fixed wording; a program that panics with that wording reads as
    refused, which can fail a panics example but never pass one.
    """
    message = str(error.get("message", ""))
    kind = error.get("type")
    if kind in ("RecursionError", "RangeError") and (
        kind == "RecursionError" or "call stack" in message
    ):
        return CallFailure("limit", message, "host recursion limit")
    if kind == "MemoryError":
        return CallFailure("limit", message, "memory limit")
    if kind == "_GenoOutputLimitError":
        return CallFailure("limit", message, "output limit")
    if error.get("panic"):
        return CallFailure("panic", message)
    if error.get("denied") or (
        backend == "js" and message.startswith("Capability denied: ")
    ):
        return CallFailure("refused", message)
    limit = compiled_limit(message)
    if limit is not None:
        return CallFailure("limit", message, limit)
    return CallFailure("panic", message)


def _timeout_text(timeout: float | None) -> str:
    # As the interpreter words it: the allowance as a float (`2.0 seconds`).
    seconds = float(timeout) if timeout is not None else timeout
    return f"Execution timed out after {seconds} seconds"


def _judge(
    backend: str, case: CompiledTestCase, record: dict[str, Any], timeout: float | None
) -> tuple[SpecViolation | None, CaseOutcome]:
    """What *record* means for *case*: its violation, if any, and outcome."""

    # As the interpreter's runner records them: a test block has no ordinal.
    ordinal = case.ordinal if case.kind == "example" else 0

    def failed(kind: str, message: str, actual: str | None = None) -> tuple[Any, Any]:
        violation = SpecViolation(kind=kind, function=case.function, message=message)
        return violation, CaseOutcome(
            case.kind, case.function, ordinal, False, actual, message
        )

    passed = (None, CaseOutcome(case.kind, case.function, ordinal, True))
    outcome = record.get("outcome")
    error = record.get("error") or {}

    if outcome == "crashed":
        detail = record.get("detail") or f"exit status {record.get('status')}"
        text = f"the compiled {backend} process stopped: {detail}"
        if case.kind == "test":
            return failed("test", f"Runtime Error: {text}")
        return failed("example", f"Runtime error: {text}")

    if case.kind == "test":
        if outcome == "returned":
            return passed
        thrown = str(error.get("message"))
        text = _timeout_text(timeout) if outcome == "timeout" else thrown
        return failed("test", f"Runtime Error: {text}")

    if case.expects_panic:
        if outcome == "returned":
            reason = check_panics_example(
                None, case.panic_message, returned=record.get("actual")
            )
        elif outcome == "timeout":
            reason = check_panics_example(
                CallFailure("limit", _timeout_text(timeout), "timeout"),
                case.panic_message,
            )
        elif record.get("phase") != "call":
            # An input that panics is a broken example, not a satisfied one.
            return failed("example", f"Runtime error: {error.get('message')}")
        else:
            reason = check_panics_example(_classify(backend, error), case.panic_message)
        return passed if reason is None else failed("example", reason)

    if outcome == "equal":
        return passed
    if outcome == "different":
        actual = record.get("actual")
        return failed(
            "example", f"expected {record.get('expected')}, got {actual}", actual
        )
    if outcome == "timeout":
        return failed("example", f"Runtime error: {_timeout_text(timeout)}")
    return failed(
        error.get("contract") or "example", f"Runtime error: {error.get('message')}"
    )


def _selected(case: CompiledTestCase, filter_pattern: str | None) -> bool:
    if not filter_pattern:
        return True
    return any(
        fnmatch.fnmatch(name, filter_pattern) for name in (case.function, *case.aliases)
    )


def run_compiled_project(
    project_path: Path,
    backend: str,
    *,
    filter_pattern: str | None = None,
    target: str | None = None,
    timeout: float | None = None,
    entry_only: bool = False,
) -> list[FileTestResult]:
    """Test *project_path* (a project, or one file) in compiled *backend*.

    Returns one result per module, as the interpreter's project runner does,
    or with *entry_only* just the file's own module, as its single-file
    runner does. A failure before any case runs is one result with ``error``.
    """
    from .compiler import CompileError
    from .dependency_graph import DependencyGraphError
    from .js_compiler import JSCompileError
    from .lexer import LexerError
    from .parser import ParseError, ParseErrors
    from .project_graph import ProjectGraphError
    from .project_resolution import (
        ProjectResolutionError,
        describe_project_resolution_error,
        resolve_project_context,
    )
    from .target_profile import (
        ManifestTargetError,
        TargetProfile,
        resolve_manifest_targets,
    )
    from .test_runner import FileTestResult
    from .typechecker import TypeChecker, TypeError

    started = time.monotonic()

    def error_result(message: str) -> list[FileTestResult]:
        return [
            FileTestResult(
                path=str(project_path),
                error=message,
                elapsed_ms=(time.monotonic() - started) * 1000,
            )
        ]

    try:
        require_backend_host(backend)
    except BackendUnavailable as exc:
        return error_result(f"Backend Error: {exc}")

    try:
        context = resolve_project_context(project_path)
    except (LexerError, ParseError, ParseErrors) as exc:
        return error_result(f"Parse Error: {exc}")
    except (
        FileNotFoundError,
        DependencyGraphError,
        ProjectGraphError,
        ProjectResolutionError,
    ) as exc:
        label = "Module Error" if entry_only else "Project Error"
        return error_result(f"{label}: {describe_project_resolution_error(exc)}")
    dep_graph = context.dependency_graph

    try:
        targets: list[str | None] = list(
            [target]
            if target is not None
            else resolve_manifest_targets(context.project.root)
        )
        for target_name in targets or [None]:
            profile = (
                TargetProfile.load(target_name) if target_name is not None else None
            )
            TypeChecker(target_profile=profile).check_project_graph(dep_graph)
    except (ValueError, ManifestTargetError) as exc:
        return error_result(f"Manifest Error: {exc}")
    except TypeError as exc:
        return error_result("\n".join(format_labeled_error("Type Error", exc)))

    try:
        build = _build(dep_graph, backend)
    except (CompileError, JSCompileError) as exc:
        return error_result(f"Compile Error: {exc}")

    entry = context.entrypoint
    modules = [entry] if entry_only else list(dep_graph.sorted_modules)
    # A single-module build does not name its module.
    by_module: dict[str, list[CompiledTestCase]] = {name: [] for name in modules}
    for case in build.cases:
        owner = case.module if case.module is not None else entry
        if owner in by_module and _selected(case, filter_pattern):
            by_module[owner].append(case)

    wanted = [case.index for cases in by_module.values() for case in cases]
    try:
        records = _run_cases(backend, build.code, wanted, timeout)
    except _LoadFailure as exc:
        return error_result(f"Runtime Error: {exc}")

    results = []
    for module in modules:
        resolved = dep_graph.file_map.get(module)
        path = str(resolved.path) if resolved else module
        if entry_only:
            path = str(project_path)
        harness = HarnessResult()
        elapsed = 0.0
        for case in by_module[module]:
            record = records.get(case.index, {"outcome": "crashed", "status": None})
            violation, outcome = _judge(backend, case, record, timeout)
            harness.total += 1
            harness.outcomes.append(outcome)
            if violation is None:
                harness.passed += 1
            else:
                harness.failed += 1
                harness.violations.append(violation)
            elapsed += float(record.get("ms") or 0.0)
        program = dep_graph.parsed[module]
        harness.untested = [
            (defn.name, defn.untested_reason)
            for defn in program.definitions
            if isinstance(defn, FunctionDef) and defn.untested_reason is not None
        ]
        results.append(
            FileTestResult(path=path, harness_result=harness, elapsed_ms=elapsed)
        )
    if results:
        # Compiling and starting the host is part of the first file's time.
        measured = sum(result.elapsed_ms for result in results)
        results[0].elapsed_ms += max(
            0.0, (time.monotonic() - started) * 1000 - measured
        )
    return results
