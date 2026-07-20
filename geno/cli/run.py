"""``geno run`` — run a Geno source file."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Literal

from .._serve import (
    install_clock_callbacks as _install_clock_callbacks,
)
from .._serve import (
    install_fs_callbacks as _install_fs_callbacks,
)
from .._serve import (
    install_http_callbacks as _install_http_callbacks,
)
from .._serve import (
    install_process_callbacks as _install_process_callbacks,
)
from .._serve import (
    install_serve_callbacks as _install_serve_callbacks,
)
from .._serve import (
    install_stdin_callbacks as _install_stdin_callbacks,
)
from ..capabilities import DEFAULT_ALLOWED_CAPABILITIES
from ..entrypoint import entrypoint_returns_int
from ..execution_limits import DEFAULT_PROCESS_MAX_MEMORY_BYTES
from ._util import (
    _format_source_snippet,
    _print_error,
    _print_runtime_error,
    report_deep_nesting_error,
)

RunMode = Literal["json", "unsafe", "process"]
_PROCESS_RESULT_DISPLAY = "display"
_PROCESS_RESULT_EXIT = "exit"
_PROCESS_RESULT_UNIT = "unit"


@dataclass(frozen=True)
class ResolvedRunProgram:
    """Resolved source graph and target plan for ``geno run``."""

    dependency_graph: Any
    program: Any
    parsed_modules: dict[str, Any] | None
    check_targets: list[str | None]
    project_root: Any


def _select_run_mode(*, unsafe: bool, json_output: bool) -> RunMode:
    if json_output:
        return "json"
    if unsafe:
        return "unsafe"
    return "process"


def _program_has_example_clauses(
    program: Any, parsed_modules: dict[str, Any] | None
) -> bool:
    """True when any function example or test block exists to verify.

    Gates the interpreter example pre-pass: with nothing to verify, the
    pre-pass is pure re-registration of definitions the typechecker has
    already validated, and skipping it keeps the interpreter (and the
    builtin registry behind it) entirely unimported for the common
    example-free ``geno run``.
    """
    from ..ast_nodes import FunctionDef, ImplDef, TestBlock

    for prog in (program, *(parsed_modules or {}).values()):
        for defn in prog.definitions:
            if isinstance(defn, TestBlock):
                return True
            if isinstance(defn, FunctionDef):
                if defn.specs and defn.specs.examples:
                    return True
            elif isinstance(defn, ImplDef):
                for method in defn.methods:
                    if method.specs and method.specs.examples:
                        return True
    return False


def _resolve_run_program(filename: str, target: str | None) -> ResolvedRunProgram:
    """Resolve source files and target checks for ``geno run``."""
    from ..project_resolution import resolve_project_context
    from ..target_profile import resolve_manifest_targets

    resolved = resolve_project_context(filename)
    dependency_graph = resolved.dependency_graph
    program = dependency_graph.parsed[resolved.entrypoint]
    target_names: list[str] = (
        [target]
        if target is not None
        else resolve_manifest_targets(resolved.project.root)
    )
    check_targets: list[str | None] = list(target_names) if target_names else [None]
    return ResolvedRunProgram(
        dependency_graph=dependency_graph,
        program=program,
        parsed_modules=resolved.parsed_modules or None,
        check_targets=check_targets,
        project_root=resolved.project.root,
    )


def _typecheck_run_graph(
    dependency_graph: Any, check_targets: list[str | None]
) -> None:
    """Type-check the resolved run graph for each requested target."""
    from ..target_profile import TargetProfile
    from ..typechecker import TypeChecker

    for target_name in check_targets:
        target_profile = (
            TargetProfile.load(target_name) if target_name is not None else None
        )
        checker = TypeChecker(target_profile=target_profile)
        checker.check_project_graph(dependency_graph)


def _format_json_run_output(result: Any) -> str:
    """Serialize embedding API run output for ``geno run --json``."""
    import json as json_mod

    output = {
        "ok": result.ok,
        "value": result.value,
        "output": result.output,
        "diagnostics": [d.to_dict() for d in result.diagnostics],
        "timing": {
            "total_ms": round(result.timing.total_ms, 2),
            "lex_ms": round(result.timing.lex_ms, 2),
            "parse_ms": round(result.timing.parse_ms, 2),
            "typecheck_ms": round(result.timing.typecheck_ms, 2),
            "run_ms": round(result.timing.run_ms, 2),
        },
        "steps_used": result.steps_used,
    }
    return json_mod.dumps(output, indent=2, allow_nan=False)


def _main_result_exit_status(value: Any, *, main_returns_int: bool) -> int | None:
    """Translate an exact Geno Int result to its portable process status."""
    if main_returns_int and type(value) is int:
        return value % 256
    return None


def _is_unit_main_result(value: Any) -> bool:
    """Return whether *value* is either compiled or interpreted Unit."""
    return value is None or (type(value) is tuple and not value)


def _explicit_fs_roots_for_run(
    filename: str,
    project_root,
    program_args: list[str] | None,
) -> list[str]:
    """Scope CLI fs grants to the project, cwd, source file, and path args."""
    roots = [os.getcwd(), os.path.dirname(os.path.abspath(filename))]
    if project_root is not None:
        roots.append(os.fspath(project_root))

    for arg in program_args or []:
        if not isinstance(arg, str) or "\x00" in arg:
            continue
        if os.path.isabs(arg):
            candidate = os.path.realpath(arg)
        elif os.sep in arg or (os.altsep and os.altsep in arg):
            candidate = os.path.realpath(os.path.abspath(arg))
        else:
            continue
        roots.append(
            candidate if os.path.isdir(candidate) else os.path.dirname(candidate)
        )
    return roots


_GENO_FRONTEND_ERROR_PREFIX = "\x1eGENO_FRONTEND\x1e"


_PROCESS_RUN_SUPPORT_MODULES = (
    "geno.api",
    "geno.ast_nodes",
    "geno.builtin_registry",
    "geno.compiler",
    "geno.constraints",
    "geno.dependency_graph",
    "geno.diagnostics",
    "geno.interpreter",
    "geno.lexer",
    "geno.manifest",
    "geno.module_resolver",
    "geno.monitoring",
    "geno.package_manager",
    "geno.parser",
    "geno.project_graph",
    "geno.project_resolution",
    "geno.sandbox",
    "geno.target_profile",
    "geno.typechecker",
    "geno.types",
    "geno.values",
)


def _preload_process_run_support() -> None:
    """Load trusted frontend modules before Darwin freezes its VM baseline.

    The default-run path intentionally uses lazy imports during normal CLI
    startup. The isolated worker calls this before reading its parent-framed
    request so only repository-controlled modules become part of the trusted
    bootstrap footprint; source resolution and compilation remain limited.
    """
    import importlib

    for module_name in _PROCESS_RUN_SUPPORT_MODULES:
        importlib.import_module(module_name)


def _prepare_process_run(request: dict[str, Any]) -> dict[str, Any]:
    """Compile a validated default-run request inside the sandbox worker."""
    expected_keys = {
        "filename",
        "target",
        "check_examples",
        "timeout",
        "max_recursion_depth",
        "max_output_length",
        "max_collection_size",
        "max_integer_bits",
    }
    if set(request) != expected_keys:
        raise ValueError("invalid process-run request fields")

    filename = request["filename"]
    target = request["target"]
    check_examples = request["check_examples"]
    if not isinstance(filename, str) or not filename or "\x00" in filename:
        raise ValueError("invalid process-run filename")
    if target is not None and not isinstance(target, str):
        raise ValueError("invalid process-run target")
    if not isinstance(check_examples, bool):
        raise ValueError("invalid process-run example setting")

    from ..ast_nodes import FunctionDef
    from ..compiler import (
        Compiler,
        _compiled_main_result_capture,
        _strip_runtime_prelude_imports,
        _trusted_runtime_prelude_line_count,
    )
    from ..sandbox import SandboxConfig

    resolved_run = _resolve_run_program(filename, target)
    dependency_graph = resolved_run.dependency_graph
    program = resolved_run.program
    parsed_modules = resolved_run.parsed_modules
    _typecheck_run_graph(dependency_graph, resolved_run.check_targets)
    main_returns_int = entrypoint_returns_int(program, dependency_graph.parsed)

    if check_examples and _program_has_example_clauses(program, parsed_modules):
        from ..api import _apply_capabilities as _apply_example_capabilities
        from ..interpreter import Interpreter

        example_config = SandboxConfig(
            timeout=request["timeout"],
            max_recursion_depth=request["max_recursion_depth"],
            max_output_length=request["max_output_length"],
            max_collection_size=request["max_collection_size"],
            max_integer_bits=request["max_integer_bits"],
        )
        interpreter = Interpreter(
            check_examples=True,
            sandbox_config=example_config,
        )
        _apply_example_capabilities(
            interpreter,
            set(DEFAULT_ALLOWED_CAPABILITIES),
        )
        interpreter.run(program, modules=parsed_modules, execute_main=False)

    compiler = Compiler()
    if parsed_modules:
        python_code = compiler.compile_project(dependency_graph)
    else:
        python_code = compiler.compile(program)
    python_code = _strip_runtime_prelude_imports(python_code)
    trusted_prelude_line_count = _trusted_runtime_prelude_line_count(python_code)
    main_defn = next(
        (
            definition
            for definition in program.definitions
            if isinstance(definition, FunctionDef) and definition.name == "main"
        ),
        None,
    )
    python_code += _compiled_main_result_capture(
        bool(main_defn and main_defn.is_async),
        main_name="_geno_entry_main" if parsed_modules else "main",
        catch_name_error=True,
    )
    if main_returns_int:
        python_code += f"\n__result__ = [{_PROCESS_RESULT_EXIT!r}, __result__ % 256]\n"
    else:
        python_code += (
            "\nif __result__ is None:\n"
            f"    __result__ = [{_PROCESS_RESULT_UNIT!r}]\n"
            "else:\n"
            f"    __result__ = [{_PROCESS_RESULT_DISPLAY!r}, str(__result__)]\n"
        )
    return {
        "python_code": python_code,
        "runtime_capabilities": sorted(DEFAULT_ALLOWED_CAPABILITIES),
        "trusted_prelude_line_count": trusted_prelude_line_count,
    }


def _format_process_frontend_error(filename: str, error: BaseException) -> str:
    """Format a frontend failure without leaking a worker traceback."""
    from io import StringIO

    from ..dependency_graph import (
        CircularDependencyError,
        DependencyGraphError,
        NameCollisionError,
    )
    from ..lexer import LexerError
    from ..parser import ParseError, ParseErrors
    from ..project_graph import ProjectGraphError
    from ..project_resolution import ProjectResolutionError
    from ..sandbox import (
        SandboxError,
        SecurityViolation,
        StepLimitExceeded,
        TimeoutError,
    )
    from ..typechecker import TypeError as GenoTypeError
    from ..values import RuntimeError as GenoRuntimeError

    if isinstance(error, FileNotFoundError):
        return f"Error: File not found: {filename}"
    if isinstance(error, ProjectResolutionError):
        return f"Error: {error}"
    if isinstance(error, ProjectGraphError):
        return f"Project Error: {error}"
    if isinstance(error, CircularDependencyError):
        return f"Circular Import: {error}"
    if isinstance(error, NameCollisionError):
        return f"Name Collision: {error}"
    if isinstance(error, DependencyGraphError):
        return f"Dependency Error: {error}"
    if isinstance(error, ValueError):
        return f"Manifest Error: {error}"
    if isinstance(error, LexerError):
        return f"Lexer Error: {error}{_format_source_snippet(error.location)}"
    if isinstance(error, ParseErrors):
        lines = [f"Parse Errors ({len(error.errors)} errors):"]
        lines.extend(
            f"  {item}{_format_source_snippet(getattr(item, 'location', None))}"
            for item in error.errors
        )
        return "\n".join(lines)
    if isinstance(error, ParseError):
        return f"Parse Error: {error}{_format_source_snippet(error.location)}"
    if isinstance(error, GenoTypeError):
        return f"Type Error: {error}{_format_source_snippet(error.location)}"
    if isinstance(error, SecurityViolation):
        return f"Security Error: {error}"
    if isinstance(error, (TimeoutError, StepLimitExceeded)):
        return f"Limit Error: {error}"
    if isinstance(error, SandboxError):
        return f"Sandbox Error: {error}"
    if isinstance(error, RecursionError):
        return (
            f"Error: expression nesting is too deep to process in {filename} "
            "(exceeded the interpreter's recursion limit). Simplify deeply "
            "nested expressions such as very long operator chains."
        )
    if isinstance(error, (GenoRuntimeError, RuntimeError)):
        buffer = StringIO()
        _print_runtime_error(error, file=buffer)
        return buffer.getvalue().rstrip()
    return (
        f"Compiler Error: the isolated frontend failed safely ({type(error).__name__})"
    )


def run_file(
    filename: str,
    check_examples: bool = True,
    unsafe: bool = False,
    timeout: float = 30.0,
    max_steps: int | None = None,
    max_recursion_depth: int = 500,
    max_output_length: int = 100_000,
    max_collection_size: int = 10_000_000,
    max_integer_bits: int = 33_219,
    max_memory_bytes: int | None = DEFAULT_PROCESS_MAX_MEMORY_BYTES,
    max_cpu_time: float | None = None,
    max_file_size_bytes: int = 0,
    max_processes: int = 1,
    capabilities: set[str] | None = None,
    target: str | None = None,
    json_output: bool = False,
    program_args: list[str] | None = None,
) -> int | None:
    """Run a Geno source file.

    By default, compiles to Python and runs in ProcessSandbox for hard timeouts.
    Use --unsafe to run with the direct interpreter (no process isolation).
    Use --json to get structured JSON output via the embedding API.
    """
    import json as json_mod

    run_mode = _select_run_mode(unsafe=unsafe, json_output=json_output)
    previous_cli_args = os.environ.get("GENO_CLI_ARGS")

    def _set_cli_args_env() -> None:
        if program_args is not None:
            os.environ["GENO_CLI_ARGS"] = json_mod.dumps(program_args)

    def _restore_cli_args_env() -> None:
        if program_args is None:
            return
        if previous_cli_args is None:
            os.environ.pop("GENO_CLI_ARGS", None)
        else:
            os.environ["GENO_CLI_ARGS"] = previous_cli_args

    # --cap is currently supported by the interpreter/embedding paths only.
    # Require the user to choose that execution mode explicitly so capability
    # grants cannot silently downgrade process isolation.
    if run_mode == "process" and capabilities is not None:
        print(
            "Error: --cap is not supported in the default process-isolated "
            "run mode. Use --unsafe --cap ... to run with capability callbacks "
            "in the direct interpreter, or use --json --cap ... for embedding "
            "API execution.",
            file=sys.stderr,
        )
        sys.exit(1)

    process_only_overrides = []
    if max_memory_bytes not in (DEFAULT_PROCESS_MAX_MEMORY_BYTES,):
        process_only_overrides.append("--max-memory-bytes")
    if max_cpu_time is not None:
        process_only_overrides.append("--max-cpu-time")
    if max_file_size_bytes != 0:
        process_only_overrides.append("--max-file-size-bytes")
    if max_processes != 1:
        process_only_overrides.append("--max-processes")
    if run_mode != "process" and process_only_overrides:
        print(
            "Error: "
            + ", ".join(process_only_overrides)
            + " only apply to the default process-isolated run mode.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --max-steps only applies to interpreter mode (--unsafe or --json).
    if run_mode == "process":
        if max_steps is not None:
            print(
                "Warning: --max-steps is ignored in compiled mode. "
                "Use --unsafe or --json for step limiting.",
                file=sys.stderr,
            )

    # --json mode uses the embedding API for structured output
    if run_mode == "json":
        from ..api import RunConfig, RunResult
        from ..api import run_path as api_run_path
        from ..diagnostics import Diagnostic, ErrorCode, Severity

        try:
            cfg_kwargs: dict[str, Any] = {
                "timeout": timeout,
                "max_recursion_depth": max_recursion_depth,
                "max_output_length": max_output_length,
                "max_collection_size": max_collection_size,
                "max_integer_bits": max_integer_bits,
                "capabilities": (
                    capabilities
                    if capabilities is not None
                    else set(DEFAULT_ALLOWED_CAPABILITIES)
                ),
                "target": target,
                "check_examples": check_examples,
            }
            if max_steps is not None:
                cfg_kwargs["max_steps"] = max_steps
            cfg = RunConfig(**cfg_kwargs)
        except ValueError as exc:
            result = RunResult(
                ok=False,
                diagnostics=[
                    Diagnostic(
                        code=ErrorCode.RUNTIME_UNKNOWN,
                        message=str(exc),
                        severity=Severity.ERROR,
                    )
                ],
            )
            print(_format_json_run_output(result))
            sys.exit(1)

        _set_cli_args_env()
        try:
            result = api_run_path(filename, cfg)
        finally:
            _restore_cli_args_env()
        print(_format_json_run_output(result))
        if not result.ok:
            sys.exit(1)
        return _main_result_exit_status(
            result.value_raw,
            main_returns_int=bool(getattr(result, "_main_returns_int", False)),
        )

    from ..dependency_graph import (
        CircularDependencyError,
        DependencyGraphError,
        NameCollisionError,
    )
    from ..lexer import LexerError
    from ..parser import ParseError, ParseErrors
    from ..project_graph import ProjectGraphError
    from ..project_resolution import ProjectResolutionError
    from ..sandbox import (
        ProcessSandbox,
        ProcessSandboxConfig,
        SandboxConfig,
        SandboxError,
        SecurityViolation,
        StepLimitExceeded,
        TimeoutError,
    )
    from ..typechecker import TypeError
    from ..values import RuntimeError as GenoRuntimeError

    try:
        if run_mode == "process":
            request = {
                "filename": filename,
                "target": target,
                "check_examples": check_examples,
                "timeout": timeout,
                "max_recursion_depth": max_recursion_depth,
                "max_output_length": max_output_length,
                "max_collection_size": max_collection_size,
                "max_integer_bits": max_integer_bits,
            }
            process_config = ProcessSandboxConfig(
                timeout=timeout,
                max_memory_bytes=max_memory_bytes,
                max_cpu_time=max_cpu_time,
                max_file_size_bytes=max_file_size_bytes,
                max_processes=max_processes,
                max_recursion_depth=max_recursion_depth,
                max_output_length=max_output_length,
                max_collection_size=max_collection_size,
                max_integer_bits=max_integer_bits,
                strict=False,
                compiled_runtime_prelude=True,
                trusted_prelude_line_count=0,
            )
            _set_cli_args_env()
            try:
                result, run_output, error = ProcessSandbox(
                    process_config
                ).execute_geno_request(request)
            finally:
                _restore_cli_args_env()

            if error is not None:
                if error.startswith(_GENO_FRONTEND_ERROR_PREFIX):
                    print(
                        error.removeprefix(_GENO_FRONTEND_ERROR_PREFIX),
                        file=sys.stderr,
                    )
                else:
                    _print_runtime_error(RuntimeError(error))
                sys.exit(1)
            if run_output:
                print(run_output, end="")
            if result == [_PROCESS_RESULT_UNIT]:
                return None
            if (
                type(result) is list
                and len(result) == 2
                and result[0] == _PROCESS_RESULT_EXIT
                and type(result[1]) is int
            ):
                return result[1]
            if (
                type(result) is list
                and len(result) == 2
                and result[0] == _PROCESS_RESULT_DISPLAY
                and type(result[1]) is str
            ):
                print(f"=> {result[1]}")
                return None

        resolved_run = _resolve_run_program(filename, target)
        dg = resolved_run.dependency_graph
        program = resolved_run.program
        parsed_modules = resolved_run.parsed_modules

        # Type check all modules (not just the entrypoint)
        _typecheck_run_graph(dg, resolved_run.check_targets)
        main_returns_int = entrypoint_returns_int(program, dg.parsed)

        if run_mode == "unsafe":
            # Direct interpreter (no process isolation)
            from ..api import _apply_capabilities
            from ..interpreter import Interpreter

            sandbox_kwargs: dict[str, Any] = {
                "timeout": timeout,
                "max_recursion_depth": max_recursion_depth,
                "max_output_length": max_output_length,
                "max_collection_size": max_collection_size,
                "max_integer_bits": max_integer_bits,
            }
            if max_steps is not None:
                sandbox_kwargs["max_steps"] = max_steps
            sandbox_config = SandboxConfig(**sandbox_kwargs)
            if capabilities is not None:
                interpreter = Interpreter(
                    check_examples=check_examples,
                    sandbox_config=sandbox_config,
                    capabilities=capabilities,
                )
            else:
                interpreter = Interpreter(
                    check_examples=check_examples,
                    sandbox_config=sandbox_config,
                )
            if capabilities is not None:
                _apply_capabilities(interpreter, capabilities)

            # Provide real file I/O only when fs capability is explicitly granted
            if capabilities is not None and "fs" in capabilities:
                _install_fs_callbacks(
                    interpreter,
                    roots=_explicit_fs_roots_for_run(
                        filename, resolved_run.project_root, program_args
                    ),
                    allow_absolute_paths=True,
                )
            if capabilities is not None and "http" in capabilities:
                _install_http_callbacks(interpreter)
            if capabilities is not None and "process" in capabilities:
                _install_process_callbacks(
                    interpreter, inherit_env="env" in capabilities
                )
            if capabilities is not None and "serve" in capabilities:
                _install_serve_callbacks(interpreter)
            if capabilities is not None and "clock" in capabilities:
                _install_clock_callbacks(interpreter)
            if capabilities is not None and "stdin" in capabilities:
                _install_stdin_callbacks(interpreter)

            _set_cli_args_env()
            try:
                result = interpreter.run(program, modules=parsed_modules)
            finally:
                _restore_cli_args_env()
            run_output = interpreter.get_output()
            if run_output:
                print(run_output, end="")
            exit_status = _main_result_exit_status(
                result, main_returns_int=main_returns_int
            )
            if exit_status is not None:
                return exit_status
            if not _is_unit_main_result(result):
                print(f"=> {interpreter._format_value(result)}")
            return None

    except FileNotFoundError:
        print(f"Error: File not found: {filename}", file=sys.stderr)
        sys.exit(1)
    except ProjectResolutionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ProjectGraphError as e:
        print(f"Project Error: {e}", file=sys.stderr)
        sys.exit(1)
    except CircularDependencyError as e:
        print(f"Circular Import: {e}", file=sys.stderr)
        sys.exit(1)
    except NameCollisionError as e:
        print(f"Name Collision: {e}", file=sys.stderr)
        sys.exit(1)
    except DependencyGraphError as e:
        print(f"Dependency Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Manifest Error: {e}", file=sys.stderr)
        sys.exit(1)
    except LexerError as e:
        _print_error("Lexer Error", e)
        sys.exit(1)
    except ParseErrors as e:
        print(f"Parse Errors ({len(e.errors)} errors):", file=sys.stderr)
        for err in e.errors:
            snippet = _format_source_snippet(getattr(err, "location", None))
            print(f"  {err}{snippet}", file=sys.stderr)
        sys.exit(1)
    except ParseError as e:
        _print_error("Parse Error", e)
        sys.exit(1)
    except TypeError as e:
        _print_error("Type Error", e)
        sys.exit(1)
    except SecurityViolation as e:
        print(f"Security Error: {e}", file=sys.stderr)
        sys.exit(1)
    except (TimeoutError, StepLimitExceeded) as e:
        print(f"Limit Error: {e}", file=sys.stderr)
        sys.exit(1)
    except SandboxError as e:
        # Infrastructure failure inside the process sandbox (e.g. the worker's
        # result channel was lost) — not a user-program error.
        print(f"Sandbox Error: {e}", file=sys.stderr)
        sys.exit(1)
    except RecursionError:
        report_deep_nesting_error(filename)
    except GenoRuntimeError as e:
        # Must precede builtin RuntimeError since GenoRuntimeError
        # (geno.values.RuntimeError) extends Exception, not builtin RuntimeError.
        # Use e.message to avoid "Runtime Error: Runtime Error: ..." duplication
        # since str(e) already includes the "Runtime Error:" prefix.
        _print_runtime_error(e)
        sys.exit(1)
    except RuntimeError as e:
        _print_runtime_error(e)
        sys.exit(1)

    return None
