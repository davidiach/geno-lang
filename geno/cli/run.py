"""``geno run`` — run a Geno source file."""

from __future__ import annotations

import os
import re
import shlex
import sys
from collections.abc import Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

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
from ..capabilities import (
    DEFAULT_ALLOWED_CAPABILITIES,
    KNOWN_CAPABILITIES,
    isolated_env_name_error,
    isolated_run_capabilities,
    isolated_run_refusals,
)
from ..execution_limits import (
    DEFAULT_MAX_RECURSION_DEPTH,
    DEFAULT_PROCESS_MAX_MEMORY_BYTES,
)

# A leaf module by design: the parent side of the process-isolated lane needs
# the modulo, and `geno.entrypoint` would drag `ast_nodes` and `types` into a
# process that exists to spawn the worker. Classification is imported lazily,
# inside the two paths that already hold the frontend.
from ..exit_status import exit_status_for_int_result
from ._util import (
    _format_error,
    _format_source_snippet,
    _print_error,
    _print_runtime_error,
    report_deep_nesting_error,
)

if TYPE_CHECKING:
    from ..isolated_host import IsolatedRunClock, IsolatedRunPolicy

RunMode = Literal["json", "unsafe", "process"]


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
    target_names: list[str] = (
        [target]
        if target is not None
        else resolve_manifest_targets(resolved.project.root)
    )
    check_targets: list[str | None] = list(target_names) if target_names else [None]
    return ResolvedRunProgram(
        dependency_graph=dependency_graph,
        program=dependency_graph.parsed[resolved.entrypoint],
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

    from ..json_encoding import GenoJSONEncoder

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
    return json_mod.dumps(output, cls=GenoJSONEncoder, indent=2, allow_nan=False)


def _is_unit_main_result(value: Any) -> bool:
    """Return whether *value* is either a compiled or interpreted Unit."""
    return value is None or (type(value) is tuple and not value)


def _main_result_exit_status(value: Any, kind: Any) -> int | None:
    """Return the status ``geno run`` must exit with, or ``None`` for zero.

    Only a declared ``Int`` entrypoint sets a status; ``Unit``, a missing
    ``main`` and any other declared type leave the process to exit normally.
    ``None`` therefore means "nothing to say", not "exit 0 explicitly", which
    is what lets the callers below return it straight up to ``SystemExit``.

    The value is only ever read here, never rewritten: what crosses an
    embedding boundary or appears in ``--json`` stays the raw result.
    """
    from ..entrypoint import EntrypointResultKind

    if kind.is_result:
        # `--json` reports an `Err` from `main` in its envelope; the status is
        # all that is left to say about it here.
        if _main_err_value(value) is not _NO_MAIN_ERR:
            return 1
        value = _main_ok_value(value)
        kind = kind.ok_kind
    if kind is not EntrypointResultKind.INT:
        return None
    # A checked `Int` is a Python int, and `bool` is excluded because
    # `isinstance(True, int)` holds while `Bool` is its own Geno type.
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return exit_status_for_int_result(value)


_NO_MAIN_ERR = object()


def _main_err_value(value: Any) -> Any:
    """The error of an interpreter `Err` returned by `main`, or ``_NO_MAIN_ERR``."""
    from ..values import ConstructorValue

    if isinstance(value, ConstructorValue) and value.constructor == "Err":
        return value.fields["error"]
    return _NO_MAIN_ERR


def _main_ok_value(value: Any) -> Any:
    """The value of an interpreter `Ok` returned by `main`."""
    from ..values import ConstructorValue

    if isinstance(value, ConstructorValue) and value.constructor == "Ok":
        return value.fields["value"]
    return value


def _process_run_result_envelope(kind: Any) -> str:
    """Emit the worker tail reporting display text and exit status together.

    The isolated worker owns this lane's whole frontend, so the parent cannot
    classify ``main`` itself and the classification is baked in here, where it
    is already known. Both halves the specification requires cross the channel:
    the display string, formatted while values still carry their Geno types,
    and the exit status, already reduced into range.

    ``__result__`` is unbound when the program has no ``main``, so a branch that
    reads it tests ``_geno_entry_fn`` first; the silent branch overwrites
    ``__result__`` outright and never reads it.
    """
    from ..entrypoint import EntrypointResultKind

    if kind.is_result:
        # An `Err` from `main` crosses as its display text and status 1; an
        # `Ok` is unwrapped and then reported as its own kind would be.
        ok_envelope = _process_run_result_envelope(kind.ok_kind)
        return (
            "\nif _geno_entry_fn is not None and isinstance(__result__, Err):\n"
            "    __result__ = {'status': 1, 'display': None, "
            "'error': _geno_format(__result__.error)}\n"
            "else:\n"
            "    if _geno_entry_fn is not None:\n"
            "        __result__ = __result__.value\n"
            + "".join(f"    {line}\n" for line in ok_envelope.strip().splitlines())
        )
    if kind is EntrypointResultKind.INT:
        # An `Int` result is the status and is never displayed, so it needs no
        # formatting -- but it does need reducing before it crosses. The channel
        # is JSON, and `max_integer_bits` admits an `Int` of roughly 10_000
        # digits while CPython refuses to render one wider than 4_300, so a
        # valid program returning a large result would die in the transport
        # rather than exiting with a status. Reducing here keeps every integer
        # the language accepts on the contract; the parent still applies
        # `exit_status_for_int_result`, which agrees with this and stays the one
        # definition of the modulo.
        return (
            "\nif _geno_entry_fn is None:\n"
            "    __result__ = {'status': None, 'display': None}\n"
            "else:\n"
            "    __result__ = {'status': __result__ % 256, 'display': None}\n"
        )
    if kind is EntrypointResultKind.OTHER:
        # JSON transport turns tuples into lists and map keys into strings, and
        # its fallback renders constructors and mutable containers with Python
        # repr, so the display string is built here rather than in the parent.
        return (
            "\nif _geno_entry_fn is None or __result__ is None:\n"
            "    __result__ = {'status': None, 'display': None}\n"
            "else:\n"
            "    __result__ = {'status': None, 'display': _geno_format(__result__)}\n"
        )
    # `Unit` and a missing `main` are silent and exit 0.
    return "\n__result__ = {'status': None, 'display': None}\n"


def _read_process_run_envelope(
    result: Any,
) -> tuple[int | None, str | None, str | None]:
    """Read the worker envelope written by :func:`_process_run_result_envelope`.

    Returns the status, the display line and the text of an `Err` returned by
    `main`. Anything else is treated as having none of them: the worker cannot
    reach that state, and inventing a status from a shape this lane did not
    emit would be the one failure mode worth avoiding here.
    """
    if not isinstance(result, dict):
        return None, None, None
    status_value = result.get("status")
    display = result.get("display")
    error = result.get("error")
    status = (
        exit_status_for_int_result(status_value)
        if isinstance(status_value, int) and not isinstance(status_value, bool)
        else None
    )
    return (
        status,
        display if isinstance(display, str) else None,
        error if isinstance(error, str) else None,
    )


def _explicit_fs_roots_for_run(
    filename: str,
    project_root,
    program_args: list[str] | None,
) -> list[str]:
    """Honor configured roots, otherwise grant the CLI's explicit input scope."""
    configured_roots = os.environ.get("GENO_FS_ROOTS")
    if configured_roots is not None:
        roots = [root for root in configured_roots.split(os.pathsep) if root]
        if not roots:
            raise RuntimeError(
                "GENO_FS_ROOTS must contain at least one filesystem root"
            )
        return roots

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
    "geno.isolated_host",
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


def _prepare_process_run(
    request: dict[str, Any],
    policy: IsolatedRunPolicy | None = None,
    clock: IsolatedRunClock | None = None,
) -> dict[str, Any]:
    """Compile a validated default-run request inside the sandbox worker.

    *policy* is the trusted grant the worker validated from its parent-set
    configuration (the default grant when omitted) and *clock* the run's one
    deadline. Examples run with exactly that grant, under that deadline, and
    the output they use is returned so ``main`` cannot get a fresh allowance.
    """
    from ..isolated_host import (
        IsolatedRunClock,
        IsolatedRunPolicy,
        build_example_interpreter,
        build_host_table,
    )

    if policy is None:
        policy = IsolatedRunPolicy.default()
    if not isinstance(policy, IsolatedRunPolicy):
        raise TypeError("invalid process-run policy")
    if clock is None:
        clock = IsolatedRunClock(policy.deadline_seconds)

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
    optional_keys = {"render_result", "program_args", "env_snapshot"}
    if set(request) - optional_keys != expected_keys:
        raise ValueError("invalid process-run request fields")

    filename = request["filename"]
    target = request["target"]
    check_examples = request["check_examples"]
    render_result = request.get("render_result", False)
    program_args = request.get("program_args", [])
    env_snapshot = request.get("env_snapshot")
    if not isinstance(filename, str) or not filename or "\x00" in filename:
        raise ValueError("invalid process-run filename")
    if target is not None and not isinstance(target, str):
        raise ValueError("invalid process-run target")
    if not isinstance(check_examples, bool):
        raise ValueError("invalid process-run example setting")
    if not isinstance(render_result, bool):
        raise ValueError("invalid process-run result rendering setting")
    if not isinstance(program_args, list) or any(
        not isinstance(arg, str) for arg in program_args
    ):
        raise ValueError("invalid process-run program arguments")
    # The snapshot holds only the caller's allowed, present variables, and
    # crosses only with an `env` grant; the trusted policy decides both.
    if env_snapshot is not None:
        if "env" not in policy.capabilities:
            raise ValueError("invalid process-run environment snapshot")
        if not isinstance(env_snapshot, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or key not in policy.env_allowed_names
            for key, value in env_snapshot.items()
        ):
            raise ValueError("invalid process-run environment snapshot")

    from ..ast_nodes import FunctionDef
    from ..compiler import (
        Compiler,
        _compiled_main_result_capture,
        _strip_runtime_prelude_imports,
        _trusted_runtime_prelude_line_count,
    )
    from ..entrypoint import classify_entrypoint_result, is_async_execution_form
    from ..sandbox import SandboxConfig

    resolved_run = _resolve_run_program(filename, target)
    dependency_graph = resolved_run.dependency_graph
    program = resolved_run.program
    parsed_modules = resolved_run.parsed_modules
    _typecheck_run_graph(dependency_graph, resolved_run.check_targets)

    host_table = build_host_table(
        policy, program_args=program_args, env_snapshot=env_snapshot
    )
    example_output_length = 0
    if check_examples and _program_has_example_clauses(program, parsed_modules):
        # The displayed timeout stays the run's --timeout; the time already
        # spent is charged through the outer deadline below, which the
        # interpreter's own deadline nests inside (it takes the minimum).
        example_config = SandboxConfig(
            timeout=request["timeout"],
            max_recursion_depth=request["max_recursion_depth"],
            max_output_length=request["max_output_length"],
            max_collection_size=request["max_collection_size"],
            max_integer_bits=request["max_integer_bits"],
        )
        interpreter = build_example_interpreter(policy, example_config, host_table)
        remaining = clock.remaining()
        with interpreter._execution_deadline(
            None if remaining is None else max(remaining, 0.001)
        ):
            interpreter.run(program, modules=parsed_modules, execute_main=False)
        # Example output is never shown, but it is counted: the run has one
        # output budget across examples and `main`.
        example_output_length = interpreter.example_output_length

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
        bool(main_defn is not None and is_async_execution_form(main_defn)),
        main_name="_geno_entry_main" if parsed_modules else "main",
        allow_missing_main=True,
    )
    if render_result:
        python_code += _process_run_result_envelope(
            classify_entrypoint_result(program, parsed_modules)
        )
    return {
        "python_code": python_code,
        "runtime_capabilities": sorted(policy.capabilities),
        "trusted_prelude_line_count": trusted_prelude_line_count,
        "host_table": host_table,
        "example_output_length": example_output_length,
    }


# A variable name the environment-policy hint may echo. The name is chosen by
# the program, so anything but a short plain identifier gets no hint at all
# rather than a terminal or shell injection.
_ENV_HINT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


def _default_lane_capability_hint(
    capability: str | None,
    filename: str,
    program_args: list[str] | None = None,
    *,
    grant: Collection[str] | None = None,
    allow_env: Collection[str] = (),
    env_name: str | None = None,
    os_name: str | None = None,
) -> str | None:
    """Explain how to grant a capability the default process lane denied.

    ``capability`` must come from the worker's structured denial tag
    (``ProcessSandbox.last_capability_denied``), never from the error text: a
    program can panic with any message, and the hint for a capability the
    isolated lane cannot grant recommends leaving process isolation.

    ``grant`` is the run's grant (``None`` for the default one); the hinted
    command grants it plus the missing capability, because ``--cap`` is exact.
    A capability the isolated lane supports keeps the program isolated.

    ``env_name`` comes from the worker's environment-policy tag
    (``ProcessSandbox.last_env_policy_denied``): ``env`` was granted but the
    variable is not in the ``--allow-env`` allowlist, so the hint names that
    option instead of ``--cap env``. ``allow_env`` holds the names the run
    already allowed, repeated so that the hinted command is complete. A hint
    never includes a variable's value.
    """
    granted = frozenset(DEFAULT_ALLOWED_CAPABILITIES if grant is None else grant)
    target = shlex.quote(filename)
    if program_args:
        target += " -- " + " ".join(shlex.quote(arg) for arg in program_args)
    env_flags = "".join(
        f" --allow-env {shlex.quote(name)}" for name in dict.fromkeys(allow_env)
    )
    if capability is None:
        if (
            env_name is None
            or "env" not in granted
            or _ENV_HINT_NAME.fullmatch(env_name) is None
        ):
            return None
        return (
            f"Hint: '{env_name}' is not in this run's environment allowlist; "
            "allow it with --allow-env (the program can then read its value):\n"
            f"  geno run --cap {','.join(sorted(granted))}{env_flags} "
            f"--allow-env {env_name} {target}"
        )
    if capability not in KNOWN_CAPABILITIES or capability in granted:
        return None
    caps = ",".join(sorted(granted | {capability}))
    if capability in isolated_run_capabilities(os_name):
        return (
            "Hint: grant it with --cap; the program stays process-isolated:\n"
            f"  geno run --cap {caps}{env_flags} {target}"
        )
    return (
        f"Hint: the process-isolated 'geno run' lane cannot grant '{capability}'.\n"
        "  To grant it, rerun without process isolation (trusted code only):\n"
        f"  geno run --unsafe --cap {caps} {target}"
    )


def _format_process_frontend_error(filename: str, error: BaseException) -> str:
    """Format a frontend failure without leaking a worker traceback."""
    from io import StringIO

    from ..compiler import CompileError
    from ..dependency_graph import (
        CircularDependencyError,
        DependencyGraphError,
        NameCollisionError,
    )
    from ..js_compiler import JSCompileError
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
        return _format_error("Lexer Error", error)
    if isinstance(error, ParseErrors):
        lines = [f"Parse Errors ({len(error.errors)} errors):"]
        lines.extend(
            f"  {item}{_format_source_snippet(getattr(item, 'location', None))}"
            for item in error.errors
        )
        return "\n".join(lines)
    if isinstance(error, ParseError):
        return _format_error("Parse Error", error)
    if isinstance(error, GenoTypeError):
        return _format_error("Type Error", error)
    if isinstance(error, (CompileError, JSCompileError)):
        # Backend rejections (reserved runtime names, unsupported constructs)
        # carry the only actionable text, so surface the same message the
        # `geno compile` path prints instead of the opaque frontend fallback.
        return f"Compile Error: {error}"
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
    max_recursion_depth: int = DEFAULT_MAX_RECURSION_DEPTH,
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
    allow_env: Collection[str] | None = None,
) -> int | None:
    """Run a Geno source file.

    By default, compiles to Python and runs in ProcessSandbox for hard timeouts.
    Use --unsafe to run with the direct interpreter (no process isolation).
    Use --json to get structured JSON output via the embedding API.

    ``allow_env`` holds the ``--allow-env`` names: the environment variables
    an isolated run granted ``env`` may read. Only that lane accepts them.

    Returns the process exit status a declared ``Int`` ``main`` asks for, or
    ``None`` when there is nothing to say and the process should exit 0. A
    normal nonzero result is returned rather than raised, so the caller exits
    without a traceback and buffered output is already on its way out. Errors
    keep exiting 1 through ``sys.exit`` as they always have.
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

    # The isolated lane grants exactly --cap (proposal 0013), but only the
    # capabilities it implements. Anything else fails here, before a worker
    # starts, so a grant can never silently downgrade process isolation.
    if run_mode == "process" and capabilities is not None:
        refusals = isolated_run_refusals(capabilities)
        if refusals:
            for message in refusals:
                print(f"Error: {message}", file=sys.stderr)
            sys.exit(1)

    # `--allow-env` names variables an isolated `env` grant may read. It never
    # grants `env` itself, and the other lanes do not take it: failing here
    # beats ignoring a policy the user thinks is in force.
    allowed_env = tuple(dict.fromkeys(allow_env or ()))
    if allowed_env:
        if run_mode != "process":
            print(
                "Error: --allow-env only applies to the default process-isolated "
                "run mode.",
                file=sys.stderr,
            )
            sys.exit(1)
        for name in allowed_env:
            problem = isolated_env_name_error(name)
            if problem is not None:
                print(f"Error: --allow-env {name!r} {problem}", file=sys.stderr)
                sys.exit(1)
        if capabilities is None or "env" not in capabilities:
            print(
                "Error: --allow-env requires an explicit --cap env grant in the "
                "process-isolated run mode",
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
                "capabilities": capabilities,
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
        # The envelope keeps the raw result: 258 stays 258 in `value` while the
        # process exits 2, and a normal nonzero result leaves `ok` true with an
        # empty stderr and no diagnostics.
        print(_format_json_run_output(result))
        if not result.ok:
            sys.exit(1)
        return _main_result_exit_status(result.value_raw, result.entrypoint_kind)

    if run_mode == "process":
        # The isolated worker owns this path's entire frontend: it resolves,
        # typechecks and compiles the program, and reports every frontend
        # failure back as pre-formatted text behind
        # ``_GENO_FRONTEND_ERROR_PREFIX`` (see the ``geno_cli`` branch of the
        # sandbox worker, which catches ``BaseException`` around
        # ``_prepare_process_run``). Importing the frontend here would load
        # lexer/parser/typechecker/dependency-graph in the parent purely to
        # bind exception names this path cannot raise, duplicating imports the
        # worker pays for again in the child. So this branch stays above the
        # frontend import block and imports the sandbox alone.
        from ..isolated_host import isolated_env_snapshot, isolated_run_policy_for
        from ..sandbox import (
            ProcessSandbox,
            ProcessSandboxConfig,
            SandboxError,
            SecurityViolation,
            StepLimitExceeded,
            TimeoutError,
        )

        run_policy = isolated_run_policy_for(
            capabilities, timeout=timeout, allow_env=allowed_env
        )
        # Only the allowed variables the caller actually has cross to the
        # worker, read before GENO_CLI_ARGS is set below, and only with `env`.
        env_snapshot = (
            isolated_env_snapshot(allowed_env)
            if "env" in run_policy.capabilities
            else None
        )
        try:
            request = {
                "filename": filename,
                "target": target,
                "check_examples": check_examples,
                "render_result": True,
                "timeout": timeout,
                "max_recursion_depth": max_recursion_depth,
                "max_output_length": max_output_length,
                "max_collection_size": max_collection_size,
                "max_integer_bits": max_integer_bits,
                # Trusted setup always receives the program arguments; only
                # the program-visible `cli_args` binding is gated (by `env`).
                "program_args": list(program_args or []),
            }
            if env_snapshot is not None:
                request["env_snapshot"] = env_snapshot
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
                process_sandbox = ProcessSandbox(process_config)
                result, run_output, error = process_sandbox.execute_geno_request(
                    request, run_policy
                )
            finally:
                _restore_cli_args_env()

            if error is not None:
                # Output the program produced before it failed is part of
                # what it did; flush it before the error, as compiled
                # backends do (#130).
                if run_output:
                    print(run_output, end="", flush=True)
                if error.startswith(_GENO_FRONTEND_ERROR_PREFIX):
                    print(
                        error.removeprefix(_GENO_FRONTEND_ERROR_PREFIX),
                        file=sys.stderr,
                    )
                else:
                    _print_runtime_error(RuntimeError(error))
                # A denial in an example (frontend phase) is as actionable as
                # one in `main`; both come with the worker's structured tag.
                hint = _default_lane_capability_hint(
                    process_sandbox.last_capability_denied,
                    filename,
                    program_args,
                    grant=run_policy.capabilities,
                    allow_env=allowed_env,
                    env_name=process_sandbox.last_env_policy_denied,
                )
                if hint:
                    print(hint, file=sys.stderr)
                sys.exit(1)
            # Captured output is printed before the status is returned, so a
            # nonzero exit never costs the program its output.
            if run_output:
                print(run_output, end="")
            status, display, main_error = _read_process_run_envelope(result)
            if main_error is not None:
                sys.stdout.flush()
                print(f"Error: {main_error}", file=sys.stderr)
            if display is not None:
                print(f"=> {display}")
            return status
        except FileNotFoundError:
            print(f"Error: File not found: {filename}", file=sys.stderr)
            sys.exit(1)
        except ValueError as e:
            print(f"Manifest Error: {e}", file=sys.stderr)
            sys.exit(1)
        except SecurityViolation as e:
            print(f"Security Error: {e}", file=sys.stderr)
            sys.exit(1)
        except (TimeoutError, StepLimitExceeded) as e:
            partial_output = getattr(e, "partial_output", "")
            if partial_output:
                print(partial_output, end="", flush=True)
            print(f"Limit Error: {e}", file=sys.stderr)
            sys.exit(1)
        except SandboxError as e:
            # Infrastructure failure inside the process sandbox (e.g. the
            # worker's result channel was lost) — not a user-program error.
            print(f"Sandbox Error: {e}", file=sys.stderr)
            sys.exit(1)
        except RecursionError:
            report_deep_nesting_error(filename)
            # Unreachable today (the reporter exits), but this branch now sits
            # above the interpreter path rather than inside its try block, so
            # falling through would run the program unsandboxed.
            return None
        except RuntimeError as e:
            _print_runtime_error(e)
            sys.exit(1)

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
        SandboxConfig,
        SandboxError,
        SecurityViolation,
        StepLimitExceeded,
        TimeoutError,
    )
    from ..typechecker import TypeError
    from ..values import RuntimeError as GenoRuntimeError

    try:
        resolved_run = _resolve_run_program(filename, target)
        dg = resolved_run.dependency_graph
        program = resolved_run.program
        parsed_modules = resolved_run.parsed_modules

        # Type check all modules (not just the entrypoint)
        _typecheck_run_graph(dg, resolved_run.check_targets)

        if run_mode == "unsafe":
            # Direct interpreter (no process isolation)
            from ..api import _apply_capabilities
            from ..entrypoint import classify_entrypoint_result
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
                    allow_absolute_paths=(
                        None
                        if "GENO_FS_ROOTS" in os.environ
                        or "GENO_FS_ALLOW_ABSOLUTE" in os.environ
                        else True
                    ),
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
            except BaseException:
                # Keep what the program printed before it failed (#130); the
                # handlers below then report the error itself.
                partial_output = interpreter.get_output()
                if partial_output:
                    print(partial_output, end="", flush=True)
                raise
            finally:
                _restore_cli_args_env()
            kind = classify_entrypoint_result(program, parsed_modules)
            status = _main_result_exit_status(result, kind)
            run_output = interpreter.get_output()
            if run_output:
                print(run_output, end="")
            if kind.is_result:
                main_error = _main_err_value(result)
                if main_error is not _NO_MAIN_ERR:
                    sys.stdout.flush()
                    error_text = interpreter.format_display_value(main_error)
                    print(f"Error: {error_text}", file=sys.stderr)
                    return status
                result = _main_ok_value(result)
            # An `Int` result is the status, so displaying it too would print
            # the number the process is about to exit with.
            if status is None and not _is_unit_main_result(result):
                print(f"=> {interpreter.format_display_value(result)}")
            return status

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

    # Only `report_deep_nesting_error` reaches here without returning, and it
    # exits; every other path above returns its own status.
    return None
