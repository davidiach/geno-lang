"""
Geno Embedding API
===================

High-level API for embedding Geno in host applications and agent runtimes.

    import geno
    result = geno.run("func main() -> Int\\n  return 42\\nend func")
    print(result.value)   # 42
    print(result.ok)      # True
"""

from __future__ import annotations

import logging
import math
import re
import time
import warnings
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    AbstractSet,
    Any,
    Callable,
    Mapping,
    Union,
    cast,
)

from .builtin_registry import allowed_gated_builtins
from .capabilities import normalize_capability_values
from .dependency_graph import DependencyGraphError
from .diagnostics import Diagnostic, ErrorCode, Severity
from .entrypoint import entrypoint_returns_int
from .execution_limits import DEFAULT_INTERPRETER_MAX_STEPS
from .project_graph import ProjectGraphError
from .project_resolution import (
    ProjectResolutionError,
    describe_project_resolution_error,
    resolve_project_context,
)
from .tokens import SourceLocation

if TYPE_CHECKING:
    from .ast_nodes import Program
    from .constraints import AllowedNext
from .values import value_to_json

logger = logging.getLogger(__name__)

_STDLIB_DIR = Path(__file__).resolve().parent / "std"
_MODULE_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*$")

_SENSITIVE_ENV_NAMES = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GENO_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
)
_SENSITIVE_ENV_PREFIXES = (
    "AWS_",
    "AZURE_",
    "GCP_",
    "GCLOUD_",
    "GOOGLE_",
)
# Suffixes for secret names that carry no substring below (e.g. FOO_KEY,
# FOO_PWD). Names ending in _PASSWORD/_PASSWD/_PASSPHRASE/_SECRET/_TOKEN are
# already covered by the corresponding substrings, so they are not repeated.
_SENSITIVE_ENV_SUFFIXES = (
    "_CREDENTIAL",
    "_CREDENTIALS",
    "_KEY",
    "_PASS",
    "_PWD",
)
# Substrings catch merged-word secrets that carry no delimiting underscore
# (e.g. PGPASSWORD, MYSQLPASSWORD) and whole-word names (PASSWORD, TOKEN,
# SECRET). This is a deny-side safety net, so over-matching only fails closed —
# a genuinely non-secret name that happens to contain one of these must be
# added to the explicit allow-list.
_SENSITIVE_ENV_SUBSTRINGS = (
    "APIKEY",
    "CONNECTION_STRING",
    "DATABASE_URL",
    "DB_URL",
    "PASSPHRASE",
    "PASSWD",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)

# =============================================================================
# Configuration
# =============================================================================


@dataclass
class RunConfig:
    """Configuration for geno.run().

    Attributes:
        timeout: Maximum wall-clock seconds for execution (None = no limit).
                 In the embedding API this is enforced cooperatively by the
                 interpreter; host callbacks are not preempted mid-call.
        max_steps: Maximum interpreter steps (None = no cooperative step limit).
        max_recursion_depth: Maximum call stack depth.
        max_output_length: Maximum characters of captured print output.
        max_collection_size: Maximum size for strings and collections.
        max_integer_bits: Maximum bit length for integer arithmetic results.
        capabilities: Only these capability builtins are installed.
                      None is treated as an empty set for fail-closed defaults.
        env_allowed_names: Optional exact environment-variable names that may
                           be read when the env capability is granted. ``None``
                           preserves legacy unrestricted env behavior for
                           non-hosted embedding API callers; an empty set denies
                           all env reads unless a prefix below matches.
        env_allowed_prefixes: Optional environment-variable name prefixes that
                              may be read when the env capability is granted.
                              Sensitive names are still denied unless explicitly
                              listed in ``env_allowed_names``.
        host_callbacks: Map of builtin names to host-provided callables.
                        Used for fs_read_text, http_fetch, etc.
        modules: Map of module names to Geno source code strings.
                 Used for in-memory module resolution with ``import`` statements.
                 When combined with ``run_path()``, explicit modules override
                 filesystem-resolved project/dependency modules of the same name.
        target: Optional target for builtin availability checking.
                ``run_path()`` uses this explicit value when provided;
                otherwise it honors the nearest project's ``geno.toml`` targets.
                Multi-target manifests are prechecked against every declared
                target; execution uses the first declared target because a
                single run can only use one runtime profile.
        check_examples: Whether to verify example clauses at runtime.
        monitoring_hook: Optional callback that receives a normalized
                         ``geno.monitoring.RunMetrics`` payload after every
                         ``geno.run()`` completion, including parse/type/runtime
                         failures.

    Note:
        ``RunConfig`` executes through the in-process interpreter. OS-backed
        process limits such as memory bytes, CPU seconds, file size, and
        process/thread count are intentionally unavailable here; callers that
        need those limits should use ``geno.sandbox.ProcessSandboxConfig`` with
        ``run_in_process()`` or ``SandboxConfig`` with ``run_sandboxed()``.
    """

    timeout: float | None = 5.0
    max_steps: int | None = DEFAULT_INTERPRETER_MAX_STEPS
    max_recursion_depth: int = 500
    max_output_length: int = 100_000
    max_collection_size: int = 10_000_000
    max_integer_bits: int = 33_219
    capabilities: set[str] | None = None
    env_allowed_names: AbstractSet[str] | None = None
    env_allowed_prefixes: AbstractSet[str] | None = None
    host_callbacks: dict[str, Callable] | None = None
    """Map of builtin names to host-provided callables.

    IMPORTANT: Host callbacks run as trusted code inside the interpreter.
    They are NOT subject to the execution timeout. A blocking callback
    will stall the entire run. Implementers must enforce their own
    timeouts for I/O operations.
    """
    modules: dict[str, str] | None = None
    target: str | None = None
    check_examples: bool = True
    monitoring_hook: Callable[[Any], None] | None = None

    def __post_init__(self) -> None:
        if self.timeout is not None and (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise ValueError(
                "RunConfig.timeout must be a positive finite number or None"
            )
        if self.max_steps is not None and (
            isinstance(self.max_steps, bool)
            or not isinstance(self.max_steps, int)
            or self.max_steps <= 0
        ):
            raise ValueError("RunConfig.max_steps must be a positive integer or None")
        if (
            isinstance(self.max_recursion_depth, bool)
            or not isinstance(self.max_recursion_depth, int)
            or self.max_recursion_depth <= 0
        ):
            raise ValueError("RunConfig.max_recursion_depth must be a positive integer")
        if (
            isinstance(self.max_output_length, bool)
            or not isinstance(self.max_output_length, int)
            or self.max_output_length < 0
        ):
            raise ValueError(
                "RunConfig.max_output_length must be a non-negative integer"
            )
        if (
            isinstance(self.max_collection_size, bool)
            or not isinstance(self.max_collection_size, int)
            or self.max_collection_size < 0
        ):
            raise ValueError(
                "RunConfig.max_collection_size must be a non-negative integer"
            )
        if (
            isinstance(self.max_integer_bits, bool)
            or not isinstance(self.max_integer_bits, int)
            or self.max_integer_bits <= 0
        ):
            raise ValueError("RunConfig.max_integer_bits must be a positive integer")
        if self.capabilities is not None:
            self.capabilities = normalize_capability_values(self.capabilities)
        self.env_allowed_names = _normalize_env_policy_values(
            self.env_allowed_names,
            "RunConfig.env_allowed_names",
        )
        self.env_allowed_prefixes = _normalize_env_policy_values(
            self.env_allowed_prefixes,
            "RunConfig.env_allowed_prefixes",
        )
        if self.host_callbacks:
            all_gated = _all_gated_builtin_names()
            for name in self.host_callbacks:
                if name not in all_gated:
                    # A callback keyed to an unknown builtin (typically a typo)
                    # would be silently ignored, so the embedder's interception
                    # never takes effect — a security footgun. Fail at config
                    # construction, consistent with the other field validation
                    # here (M-06).
                    raise ValueError(
                        f"RunConfig.host_callbacks entry {name!r} is not a "
                        "capability-gated builtin. Valid names: "
                        f"{', '.join(sorted(all_gated))}"
                    )


_ALL_GATED_BUILTINS: frozenset[str] | None = None


def _all_gated_builtin_names() -> frozenset[str]:
    """Return every capability-gated builtin name (cached; CAPABILITY_MAP is a
    module constant so the flattened set is derived once per process)."""
    global _ALL_GATED_BUILTINS
    if _ALL_GATED_BUILTINS is None:
        from .builtin_registry import CAPABILITY_MAP

        _ALL_GATED_BUILTINS = frozenset(
            b for names in CAPABILITY_MAP.values() for b in names
        )
    return _ALL_GATED_BUILTINS


def _normalize_env_policy_values(
    values: AbstractSet[str] | None,
    field_name: str,
) -> frozenset[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        raise ValueError(f"{field_name} must be a collection of non-empty strings")
    try:
        normalized = frozenset(values)
    except TypeError as exc:
        raise ValueError(
            f"{field_name} must be a collection of non-empty strings"
        ) from exc
    if any(not isinstance(item, str) or item == "" for item in normalized):
        raise ValueError(f"{field_name} must contain only non-empty strings")
    return normalized


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class Timing:
    """Phase timings in milliseconds."""

    total_ms: float = 0.0
    lex_ms: float = 0.0
    parse_ms: float = 0.0
    typecheck_ms: float = 0.0
    run_ms: float = 0.0


@dataclass
class RunResult:
    """
    Result of geno.run().

    Attributes:
        ok: True if execution completed without errors.
        value: The return value of main() (JSON-serializable).
        value_raw: The raw Python value before JSON conversion.
        output: Captured stdout from print() calls.
        diagnostics: List of structured diagnostics (errors/warnings).
        timing: Per-phase timing breakdown.
        steps_used: Number of interpreter steps consumed.
    """

    ok: bool
    value: Any = None
    value_raw: Any = None
    output: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list)
    timing: Timing = field(default_factory=Timing)
    steps_used: int = 0


@dataclass
class CheckResult:
    """
    Result of geno.check().

    Attributes:
        ok: True if source passes lexing, parsing, and type checking.
        diagnostics: List of structured diagnostics.
        timing: Per-phase timing breakdown.
    """

    ok: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)
    timing: Timing = field(default_factory=Timing)


@dataclass(frozen=True)
class ConstraintResult:
    """Result of geno.constrain_prefix()."""

    allowed_next: AllowedNext
    valid: bool
    error: str | None = None
    unclosed_blocks: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the constraint result."""
        return {
            "valid": self.valid,
            "error": self.error,
            "unclosed_blocks": list(self.unclosed_blocks),
            "allowed_next": asdict(self.allowed_next),
        }


# =============================================================================
# Internal Helpers
# =============================================================================


def _make_diagnostic(exc, default_code: ErrorCode) -> Diagnostic:
    """Create a Diagnostic from a Geno exception."""
    return Diagnostic(
        code=getattr(exc, "error_code", None) or default_code,
        message=getattr(exc, "message", str(exc)),
        severity=Severity.ERROR,
        location=getattr(exc, "location", None),
    )


def _make_config_diagnostic(message: str) -> Diagnostic:
    """Create a diagnostic for project/target configuration errors."""
    return Diagnostic(
        code=ErrorCode.PROJECT_RESOLUTION_ERROR,
        message=message,
        severity=Severity.ERROR,
    )


def _prefix_diagnostics_for_target(
    diagnostics: list[Diagnostic],
    target: str,
    *,
    enabled: bool,
) -> list[Diagnostic]:
    """Prefix diagnostics when a multi-target pass needs target attribution."""
    if not enabled:
        return diagnostics
    return [
        Diagnostic(
            code=diag.code,
            message=f"[{target}] {diag.message}",
            severity=diag.severity,
            location=diag.location,
        )
        for diag in diagnostics
    ]


def _module_filename(name: str, source: str) -> str:
    if not _MODULE_NAME_RE.fullmatch(name):
        raise ValueError(
            f"Invalid module name {name!r}: expected a simple PascalCase identifier"
        )
    stdlib_path = (_STDLIB_DIR / f"{name}.geno").resolve()
    if not stdlib_path.is_relative_to(_STDLIB_DIR):
        raise ValueError(f"Invalid module name {name!r}")
    try:
        if stdlib_path.is_file() and stdlib_path.read_text(encoding="utf-8") == source:
            return str(stdlib_path)
    except OSError:
        pass
    return f"<module:{name}>"


def _parse_modules(modules: dict[str, str]) -> dict[str, Program]:
    """Parse module source strings into Program ASTs.

    Args:
        modules: Map of module names to Geno source strings.

    Returns:
        Map of module names to parsed Program ASTs.

    Raises:
        ParseError/LexerError on invalid module source.
    """
    from .lexer import Lexer
    from .parser import Parser

    parsed: dict[str, Program] = {}
    for name, source in modules.items():
        lexer = Lexer(source, _module_filename(name, source))
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        parsed[name] = parser.parse_program()
    return parsed


def _finalize_timing(
    timing: Timing, phase_attr: str, t_phase: float, t0: float
) -> None:
    """Record phase timing and total elapsed time."""
    setattr(timing, phase_attr, (time.perf_counter() - t_phase) * 1000)
    timing.total_ms = (time.perf_counter() - t0) * 1000


def _allowed_gated_builtins(capabilities: set[str] | None) -> set[str]:
    """Return gated builtin names allowed by the provided capabilities.

    Returns an empty set when ``capabilities`` is ``None`` (fail-closed).
    """
    return cast(set[str], allowed_gated_builtins(capabilities))


def _env_policy_configured(cfg: RunConfig) -> bool:
    return cfg.env_allowed_names is not None or cfg.env_allowed_prefixes is not None


def _is_sensitive_env_name(name: str) -> bool:
    upper_name = name.upper()
    return (
        upper_name in _SENSITIVE_ENV_NAMES
        or any(upper_name.startswith(prefix) for prefix in _SENSITIVE_ENV_PREFIXES)
        or any(upper_name.endswith(suffix) for suffix in _SENSITIVE_ENV_SUFFIXES)
        or any(part in upper_name for part in _SENSITIVE_ENV_SUBSTRINGS)
    )


def _env_name_allowed_by_policy(name: str, cfg: RunConfig) -> bool:
    allowed_names = cfg.env_allowed_names or frozenset()
    allowed_prefixes = cfg.env_allowed_prefixes or frozenset()
    if name in allowed_names:
        return True
    if _is_sensitive_env_name(name):
        return False
    return any(name.startswith(prefix) for prefix in allowed_prefixes)


def _emit_monitoring_hook(cfg: RunConfig, result: RunResult) -> RunResult:
    """Emit a normalized run metrics payload without affecting run semantics."""
    if cfg.monitoring_hook is None:
        return result

    from .monitoring import RunMetrics

    try:
        cfg.monitoring_hook(RunMetrics.from_run_result(result))
    except Exception as exc:
        warnings.warn(
            f"RunConfig.monitoring_hook raised {exc.__class__.__name__}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
    return result


def _remap_module_diagnostic_locations(
    diagnostics: list[Diagnostic],
    module_paths: dict[str, str],
    overlay_modules: Mapping[str, str] | None = None,
) -> list[Diagnostic]:
    """Replace synthetic ``<module:Name>`` locations with real file paths.

    Explicit module-string overlays keep synthetic module locations because
    their source is in-memory only and may not match any on-disk file.
    ``overlay_modules`` maps each overlay's effective module key back to the
    user-facing overlay name, so overlays resolved onto internal graph keys
    surface under the name the caller supplied.
    """
    remapped: list[Diagnostic] = []
    preserved = overlay_modules or {}
    for diagnostic in diagnostics:
        location = diagnostic.location
        if (
            location is not None
            and location.filename.startswith("<module:")
            and location.filename.endswith(">")
        ):
            module_name = location.filename[len("<module:") : -1]
            if module_name in preserved:
                display_name = preserved[module_name]
                if display_name != module_name:
                    remapped.append(
                        Diagnostic(
                            code=diagnostic.code,
                            message=diagnostic.message,
                            severity=diagnostic.severity,
                            location=SourceLocation(
                                location.line,
                                location.column,
                                f"<module:{display_name}>",
                            ),
                        )
                    )
                else:
                    remapped.append(diagnostic)
                continue
            path = module_paths.get(module_name)
            if path is not None:
                remapped.append(
                    Diagnostic(
                        code=diagnostic.code,
                        message=diagnostic.message,
                        severity=diagnostic.severity,
                        location=SourceLocation(
                            location.line,
                            location.column,
                            path,
                        ),
                    )
                )
                continue
        remapped.append(diagnostic)
    return remapped


# =============================================================================
# Public API
# =============================================================================


def run(
    source: str,
    filename: Union[str, RunConfig] = "<api>",
    config: RunConfig | None = None,
) -> RunResult:
    """
    Parse, type-check, and execute a Geno program.

    Args:
        source: Geno source code.
        filename: Filename for error messages.
        config: Execution configuration (uses sensible defaults if None).

    Returns:
        RunResult with value, output, diagnostics, and timing.

    Notes:
        ``run(source, RunConfig(...))`` remains supported for backward
        compatibility. Prefer ``config=...`` or ``run(source, filename, config=...)``
        for new call sites.
    """
    from .interpreter import Interpreter
    from .lexer import Lexer, LexerError
    from .parser import ParseError, ParseErrors, Parser
    from .sandbox import (
        RecursionLimitError,
        ResourceLimitExceeded,
        SandboxConfig,
        SecurityViolation,
        StepLimitExceeded,
        TimeoutError,
    )
    from .target_profile import TargetProfile
    from .typechecker import TypeChecker
    from .types import TypeError as GenoTypeError
    from .types import TypeErrors as GenoTypeErrors
    from .values import RuntimeError as GenoRuntimeError

    resolved_filename = filename
    if isinstance(resolved_filename, RunConfig):
        if config is not None:
            raise TypeError(
                "run() received RunConfig both positionally and via config="
            )
        config = resolved_filename
        resolved_filename = "<api>"

    cfg = config or RunConfig()
    diagnostics: list[Diagnostic] = []
    timing = Timing()
    t0 = time.perf_counter()

    # --- Lex ---
    t_lex = time.perf_counter()
    try:
        lexer = Lexer(source, resolved_filename)
        tokens = lexer.tokenize()
    except LexerError as e:
        _finalize_timing(timing, "lex_ms", t_lex, t0)
        diagnostics.append(_make_diagnostic(e, ErrorCode.LEX_UNEXPECTED_CHAR))
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )
    timing.lex_ms = (time.perf_counter() - t_lex) * 1000

    # --- Parse ---
    t_parse = time.perf_counter()
    try:
        parser = Parser(tokens)
        program = parser.parse_program()
    except ParseErrors as e:
        _finalize_timing(timing, "parse_ms", t_parse, t0)
        for err in e.errors:
            diagnostics.append(_make_diagnostic(err, ErrorCode.PARSE_UNEXPECTED_TOKEN))
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )
    except ParseError as e:
        _finalize_timing(timing, "parse_ms", t_parse, t0)
        diagnostics.append(_make_diagnostic(e, ErrorCode.PARSE_UNEXPECTED_TOKEN))
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )

    # Parse module sources (if any)
    parsed_modules = None
    if cfg.modules is not None:
        try:
            parsed_modules = _parse_modules(cfg.modules)
        except ValueError as e:
            _finalize_timing(timing, "parse_ms", t_parse, t0)
            diagnostics.append(_make_config_diagnostic(str(e)))
            return _emit_monitoring_hook(
                cfg,
                RunResult(ok=False, diagnostics=diagnostics, timing=timing),
            )
        except (ParseErrors, ParseError, LexerError) as e:
            _finalize_timing(timing, "parse_ms", t_parse, t0)
            if isinstance(e, ParseErrors):
                for err in e.errors:
                    diagnostics.append(
                        _make_diagnostic(err, ErrorCode.PARSE_UNEXPECTED_TOKEN)
                    )
            else:
                diagnostics.append(
                    _make_diagnostic(e, ErrorCode.PARSE_UNEXPECTED_TOKEN)
                )
            return _emit_monitoring_hook(
                cfg,
                RunResult(ok=False, diagnostics=diagnostics, timing=timing),
            )
    timing.parse_ms = (time.perf_counter() - t_parse) * 1000

    # --- Type Check ---
    t_tc = time.perf_counter()
    try:
        target_profile = TargetProfile.load(cfg.target) if cfg.target else None
        checker = TypeChecker(target_profile=target_profile)
        # Check imported modules' bodies too (not just the entrypoint),
        # so type errors in them are caught before execution.
        if parsed_modules:
            for mod_name, mod_ast in parsed_modules.items():
                other_mods = {k: v for k, v in parsed_modules.items() if k != mod_name}
                mod_checker = TypeChecker(target_profile=target_profile)
                mod_checker.check_program(mod_ast, modules=other_mods or None)
        checker.check_program(program, modules=parsed_modules)
    except (ValueError, RuntimeError) as e:
        _finalize_timing(timing, "typecheck_ms", t_tc, t0)
        diagnostics.append(_make_config_diagnostic(str(e)))
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )
    except GenoTypeErrors as e:
        _finalize_timing(timing, "typecheck_ms", t_tc, t0)
        for type_error in e.errors:
            diagnostics.append(_make_diagnostic(type_error, ErrorCode.TYPE_MISMATCH))
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )
    except GenoTypeError as e:
        _finalize_timing(timing, "typecheck_ms", t_tc, t0)
        diagnostics.append(_make_diagnostic(e, ErrorCode.TYPE_MISMATCH))
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )
    timing.typecheck_ms = (time.perf_counter() - t_tc) * 1000

    # --- Execute ---
    t_run = time.perf_counter()
    try:
        sandbox_config = SandboxConfig(
            timeout=cfg.timeout,
            max_recursion_depth=cfg.max_recursion_depth,
            max_output_length=cfg.max_output_length,
            max_steps=cfg.max_steps,
            max_collection_size=cfg.max_collection_size,
            max_integer_bits=cfg.max_integer_bits,
        )
        interp = Interpreter(
            check_examples=cfg.check_examples,
            sandbox_config=sandbox_config,
            capabilities=cfg.capabilities,
        )

        # Apply capability gating. ``None`` is fail-closed so callers that omit
        # capabilities do not accidentally expose ambient env/clock/random APIs.
        _apply_capabilities(interp, cfg.capabilities)

        # Install host callbacks (after capability gating so denied stubs take priority).
        # Require explicit capabilities when host_callbacks are provided:
        # omitting capabilities no longer silently grants all gated builtins.
        if cfg.host_callbacks:
            _install_host_callbacks(interp, cfg.host_callbacks, cfg.capabilities)

        _install_env_policy(interp, cfg)

        result_raw = interp.run(program, modules=parsed_modules)
        output = interp.get_output()
        steps = interp.steps

        timing.run_ms = (time.perf_counter() - t_run) * 1000
        timing.total_ms = (time.perf_counter() - t0) * 1000

        run_result = RunResult(
            ok=True,
            value=value_to_json(result_raw),
            value_raw=result_raw,
            output=output,
            diagnostics=diagnostics,
            timing=timing,
            steps_used=steps,
        )
        run_result.__dict__["_main_returns_int"] = entrypoint_returns_int(
            program, parsed_modules
        )
        return _emit_monitoring_hook(cfg, run_result)

    # Exception ordering matters: subclasses must be caught before their
    # parent classes.  StepLimitExceeded and RecursionLimitError are both
    # subclasses of ResourceLimitExceeded, so they must precede it.
    # GenoRuntimeError is the broadest runtime exception and goes last.

    except StepLimitExceeded as e:
        _finalize_timing(timing, "run_ms", t_run, t0)
        diagnostics.append(
            Diagnostic(
                code=ErrorCode.SANDBOX_STEP_LIMIT,
                message=str(e),
                severity=Severity.ERROR,
            )
        )
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )

    except RecursionLimitError as e:
        _finalize_timing(timing, "run_ms", t_run, t0)
        diagnostics.append(
            Diagnostic(
                code=ErrorCode.SANDBOX_RECURSION_LIMIT,
                message=str(e),
                severity=Severity.ERROR,
            )
        )
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )

    except RecursionError as e:
        # Host-language recursion limit can still be reached even with the
        # interpreter's own depth check (e.g. effects + try/catch chains use
        # extra Python frames). Convert to a Geno-level E502 rather than
        # letting the host RecursionError leak past the sandbox (issue #650).
        _finalize_timing(timing, "run_ms", t_run, t0)
        diagnostics.append(
            Diagnostic(
                code=ErrorCode.SANDBOX_RECURSION_LIMIT,
                message=f"Host recursion limit exceeded: {e}",
                severity=Severity.ERROR,
            )
        )
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )

    except (TimeoutError, ResourceLimitExceeded) as e:
        _finalize_timing(timing, "run_ms", t_run, t0)
        code = (
            ErrorCode.SANDBOX_TIMEOUT
            if isinstance(e, TimeoutError)
            else ErrorCode.SANDBOX_RESOURCE_LIMIT
        )
        diagnostics.append(
            Diagnostic(
                code=code,
                message=str(e),
                severity=Severity.ERROR,
            )
        )
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )

    except SecurityViolation as e:
        _finalize_timing(timing, "run_ms", t_run, t0)
        diagnostics.append(
            Diagnostic(
                code=ErrorCode.SANDBOX_SECURITY_VIOLATION,
                message=str(e),
                severity=Severity.ERROR,
            )
        )
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )

    except GenoRuntimeError as e:
        _finalize_timing(timing, "run_ms", t_run, t0)
        diagnostics.append(_make_diagnostic(e, ErrorCode.RUNTIME_UNKNOWN))
        return _emit_monitoring_hook(
            cfg,
            RunResult(ok=False, diagnostics=diagnostics, timing=timing),
        )


def run_path(path: str, config: RunConfig | None = None) -> RunResult:
    """
    Resolve a Geno file or project from disk, then execute its entrypoint.

    Explicit ``RunConfig.modules`` overlays take precedence over filesystem
    project modules, but the resolved entrypoint source always comes from *path*.
    """
    cfg = config or RunConfig()
    timing = Timing()
    t0 = time.perf_counter()

    try:
        resolved = resolve_project_context(path)
        merged_inputs = resolved.merged_module_inputs(cfg.modules)
        effective_modules = merged_inputs.module_sources
        overlay_names = merged_inputs.overlay_graph_keys or None
        from .target_profile import resolve_manifest_targets

        target_names = (
            [cfg.target]
            if cfg.target is not None
            else resolve_manifest_targets(resolved.project.root)
        )
        module_paths = {
            name: str(resolved_file.path)
            for name, resolved_file in resolved.dependency_graph.file_map.items()
        }
        module_paths.update(merged_inputs.module_paths)
    except (
        FileNotFoundError,
        DependencyGraphError,
        ProjectGraphError,
        ProjectResolutionError,
        ValueError,
    ) as exc:
        timing.total_ms = (time.perf_counter() - t0) * 1000
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else describe_project_resolution_error(exc)
        )
        return _emit_monitoring_hook(
            cfg,
            RunResult(
                ok=False,
                diagnostics=[
                    Diagnostic(
                        code=ErrorCode.PROJECT_RESOLUTION_ERROR,
                        message=message,
                        severity=Severity.ERROR,
                    )
                ],
                timing=timing,
            ),
        )

    if len(target_names) > 1:
        diagnostics: list[Diagnostic] = []
        for target_name in target_names:
            check_result = check(
                resolved.source,
                filename=resolved.filename,
                modules=effective_modules,
                target=target_name,
            )
            if not check_result.ok:
                diagnostics.extend(
                    _prefix_diagnostics_for_target(
                        check_result.diagnostics,
                        target_name,
                        enabled=True,
                    )
                )
        if diagnostics:
            timing.total_ms = (time.perf_counter() - t0) * 1000
            diagnostics = _remap_module_diagnostic_locations(
                diagnostics,
                module_paths,
                overlay_names,
            )
            return _emit_monitoring_hook(
                cfg,
                RunResult(ok=False, diagnostics=diagnostics, timing=timing),
            )

    effective_target = target_names[0] if target_names else None
    result = run(
        resolved.source,
        filename=resolved.filename,
        config=replace(cfg, modules=effective_modules, target=effective_target),
    )
    result.diagnostics = _remap_module_diagnostic_locations(
        result.diagnostics,
        module_paths,
        overlay_names,
    )
    return result


def check(
    source: str,
    filename: str = "<api>",
    modules: dict[str, str] | None = None,
    target: str | None = None,
    *,
    _module_name: str | None = None,
) -> CheckResult:
    """
    Parse and type-check Geno source code without executing it.

    Args:
        source: Geno source code.
        filename: Filename for error messages.
        modules: Optional map of module names to Geno source strings.
        target: Optional target for builtin and backend-lowering validation.
            A real filename also supplies the entrypoint module name for
            project-style target validation.

    Returns:
        CheckResult with ok status and diagnostics.
    """
    from .lexer import Lexer, LexerError
    from .parser import ParseError, ParseErrors, Parser
    from .target_profile import TargetProfile
    from .target_validation import (
        TargetValidationError,
        validate_program_collection_for_target,
    )
    from .typechecker import TypeChecker
    from .types import TypeError, TypeErrors

    diagnostics: list[Diagnostic] = []
    timing = Timing()
    t0 = time.perf_counter()

    # --- Lex ---
    t_lex = time.perf_counter()
    try:
        lexer = Lexer(source, filename)
        tokens = lexer.tokenize()
    except LexerError as e:
        _finalize_timing(timing, "lex_ms", t_lex, t0)
        diagnostics.append(_make_diagnostic(e, ErrorCode.LEX_UNEXPECTED_CHAR))
        return CheckResult(ok=False, diagnostics=diagnostics, timing=timing)
    timing.lex_ms = (time.perf_counter() - t_lex) * 1000

    # --- Parse ---
    t_parse = time.perf_counter()
    try:
        parser = Parser(tokens)
        program = parser.parse_program()
    except ParseErrors as e:
        _finalize_timing(timing, "parse_ms", t_parse, t0)
        for err in e.errors:
            diagnostics.append(_make_diagnostic(err, ErrorCode.PARSE_UNEXPECTED_TOKEN))
        return CheckResult(ok=False, diagnostics=diagnostics, timing=timing)
    except ParseError as e:
        _finalize_timing(timing, "parse_ms", t_parse, t0)
        diagnostics.append(_make_diagnostic(e, ErrorCode.PARSE_UNEXPECTED_TOKEN))
        return CheckResult(ok=False, diagnostics=diagnostics, timing=timing)

    # Parse module sources (if any)
    parsed_modules = None
    if modules is not None:
        try:
            parsed_modules = _parse_modules(modules)
        except ValueError as e:
            _finalize_timing(timing, "parse_ms", t_parse, t0)
            diagnostics.append(_make_config_diagnostic(str(e)))
            return CheckResult(ok=False, diagnostics=diagnostics, timing=timing)
        except (ParseErrors, ParseError, LexerError) as e:
            _finalize_timing(timing, "parse_ms", t_parse, t0)
            if isinstance(e, ParseErrors):
                for err in e.errors:
                    diagnostics.append(
                        _make_diagnostic(err, ErrorCode.PARSE_UNEXPECTED_TOKEN)
                    )
            else:
                diagnostics.append(
                    _make_diagnostic(e, ErrorCode.PARSE_UNEXPECTED_TOKEN)
                )
            return CheckResult(ok=False, diagnostics=diagnostics, timing=timing)
    timing.parse_ms = (time.perf_counter() - t_parse) * 1000

    # --- Type Check ---
    t_tc = time.perf_counter()
    try:
        target_profile = TargetProfile.load(target) if target else None
        checker = TypeChecker(target_profile=target_profile)
        # Check imported modules' bodies too (not just the entrypoint)
        if parsed_modules:
            for mod_name, mod_ast in parsed_modules.items():
                other_mods = {k: v for k, v in parsed_modules.items() if k != mod_name}
                mod_checker = TypeChecker(target_profile=target_profile)
                mod_checker.check_program(mod_ast, modules=other_mods or None)
        checker.check_program(program, modules=parsed_modules)
        if target_profile is not None:
            entrypoint_name = _module_name
            if entrypoint_name is None and filename and not filename.startswith("<"):
                entrypoint_name = Path(filename).stem or None
            validate_program_collection_for_target(
                program,
                parsed_modules or {},
                target_profile,
                entrypoint_name=entrypoint_name,
            )
    except TargetValidationError as e:
        _finalize_timing(timing, "typecheck_ms", t_tc, t0)
        diagnostics.append(_make_diagnostic(e, ErrorCode.TYPE_MISMATCH))
        return CheckResult(ok=False, diagnostics=diagnostics, timing=timing)
    except (ValueError, RuntimeError) as e:
        _finalize_timing(timing, "typecheck_ms", t_tc, t0)
        diagnostics.append(_make_config_diagnostic(str(e)))
        return CheckResult(ok=False, diagnostics=diagnostics, timing=timing)
    except TypeErrors as e:
        _finalize_timing(timing, "typecheck_ms", t_tc, t0)
        for type_error in e.errors:
            diagnostics.append(_make_diagnostic(type_error, ErrorCode.TYPE_MISMATCH))
        return CheckResult(ok=False, diagnostics=diagnostics, timing=timing)
    except TypeError as e:
        _finalize_timing(timing, "typecheck_ms", t_tc, t0)
        diagnostics.append(_make_diagnostic(e, ErrorCode.TYPE_MISMATCH))
        return CheckResult(ok=False, diagnostics=diagnostics, timing=timing)
    timing.typecheck_ms = (time.perf_counter() - t_tc) * 1000

    timing.total_ms = (time.perf_counter() - t0) * 1000
    return CheckResult(ok=True, diagnostics=diagnostics, timing=timing)


def check_path(
    path: str,
    modules: dict[str, str] | None = None,
    target: str | None = None,
) -> CheckResult:
    """Resolve, type-check, and target-validate a Geno file or project."""
    timing = Timing()
    t0 = time.perf_counter()

    try:
        resolved = resolve_project_context(path)
        merged_inputs = resolved.merged_module_inputs(modules)
        effective_modules = merged_inputs.module_sources
        overlay_names = merged_inputs.overlay_graph_keys or None
        from .target_profile import resolve_manifest_targets

        manifest_targets = resolve_manifest_targets(resolved.project.root)
        target_names: list[str] = [target] if target is not None else manifest_targets
        module_paths = {
            name: str(resolved_file.path)
            for name, resolved_file in resolved.dependency_graph.file_map.items()
        }
        module_paths.update(merged_inputs.module_paths)
    except (
        FileNotFoundError,
        DependencyGraphError,
        ProjectGraphError,
        ProjectResolutionError,
        ValueError,
    ) as exc:
        timing.total_ms = (time.perf_counter() - t0) * 1000
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else describe_project_resolution_error(exc)
        )
        return CheckResult(
            ok=False,
            diagnostics=[
                Diagnostic(
                    code=ErrorCode.PROJECT_RESOLUTION_ERROR,
                    message=message,
                    severity=Severity.ERROR,
                )
            ],
            timing=timing,
        )

    diagnostics: list[Diagnostic] = []
    result = CheckResult(ok=True, timing=timing)
    check_targets: list[str | None] = list(target_names) if target_names else [None]
    for target_name in check_targets:
        current = check(
            resolved.source,
            filename=resolved.filename,
            modules=effective_modules,
            target=target_name,
            _module_name=resolved.entrypoint,
        )
        if not current.ok:
            diagnostics.extend(
                _prefix_diagnostics_for_target(
                    current.diagnostics,
                    cast(str, target_name),
                    enabled=target_name is not None and len(target_names) > 1,
                )
            )
        result = current

    if diagnostics:
        result = CheckResult(ok=False, diagnostics=diagnostics, timing=result.timing)
    result.diagnostics = _remap_module_diagnostic_locations(
        result.diagnostics,
        module_paths,
        overlay_names,
    )
    return result


def constrain_prefix(prefix: str) -> ConstraintResult:
    """
    Compute next-token constraints for a partial Geno source prefix.

    Args:
        prefix: Partial Geno source code.

    Returns:
        ConstraintResult with the allowed-next-token set and validation status.
    """
    from .constraints import allowed_next_for_prefix
    from .constraints import validate_prefix as constraints_validate_prefix

    allowed_next = allowed_next_for_prefix(prefix)
    valid, error = constraints_validate_prefix(prefix)
    return ConstraintResult(
        allowed_next=allowed_next,
        valid=valid,
        error=error,
        unclosed_blocks=tuple(allowed_next.expected_end_stack),
    )


def validate_prefix(prefix: str) -> tuple[bool, str | None]:
    """
    Check whether a partial Geno source prefix can still be extended validly.

    Args:
        prefix: Partial Geno source code.

    Returns:
        ``(is_valid, error_message)``.
    """
    from .constraints import validate_prefix as constraints_validate_prefix

    return cast(tuple[bool, str | None], constraints_validate_prefix(prefix))


# =============================================================================
# Capability Gating
# =============================================================================


def _apply_capabilities(interp, capabilities: set[str] | None) -> None:
    """Replace disallowed builtins with stubs that raise capability-denied errors."""
    interp.apply_capabilities(capabilities)


def _install_host_callbacks(
    interp, host_callbacks: dict[str, Callable], capabilities: set[str] | None
) -> None:
    """Replace host-callback stubs with host-provided callables.

    Only installs callbacks for builtins that are capability-allowed.
    If a capability is granted but no callback is provided, the stub
    stays in place (raises RUNTIME_HOST_CALLBACK_MISSING at call time).
    """
    from .values import BuiltinFunction

    env = interp.global_env
    allowed_gated = _allowed_gated_builtins(capabilities)
    # Unknown/typo'd keys were already rejected in RunConfig.__post_init__; this
    # path only decides installation vs the ungranted-capability warning.
    for name, callback in host_callbacks.items():
        if name not in allowed_gated:
            # Valid builtin, but its capability was not granted, so the stub
            # stays capability-denied and the callback would never run. Warn
            # rather than drop silently.
            logger.warning(
                "host_callbacks entry %r will not take effect: its capability "
                "is not granted in this RunConfig, so the builtin stays "
                "capability-denied.",
                name,
            )
            continue
        # Only install if the builtin exists in the environment
        current = env.bindings.get(name)
        if isinstance(current, BuiltinFunction):
            env.bindings[name] = BuiltinFunction(
                name=current.name,
                func=callback,
                arity=current.arity,
                param_names=current.param_names,
            )
        else:  # pragma: no cover - defensive: granted gated builtin always a stub
            logger.warning(
                "host_callbacks entry %r has no installable builtin stub; skipped.",
                name,
            )


def _install_env_policy(interp, cfg: RunConfig) -> None:
    """Wrap env builtins with the configured variable allowlist policy."""
    if not _env_policy_configured(cfg):
        return
    allowed_gated = _allowed_gated_builtins(cfg.capabilities)
    if not {"env_get", "env_get_or", "cli_args"} & allowed_gated:
        return

    from .values import BuiltinFunction
    from .values import RuntimeError as GenoRuntimeError

    env = interp.global_env

    def _deny(builtin_name: str, env_name: str) -> None:
        raise GenoRuntimeError(
            f"{builtin_name}: environment variable {env_name!r} is not allowed "
            "by the host env policy",
            error_code=ErrorCode.RUNTIME_CAPABILITY_DENIED,
        )

    def _check_name(builtin_name: str, env_name: str) -> None:
        if not _env_name_allowed_by_policy(env_name, cfg):
            _deny(builtin_name, env_name)

    current_env_get = env.bindings.get("env_get")
    if "env_get" in allowed_gated and isinstance(current_env_get, BuiltinFunction):
        original_env_get = current_env_get.func

        def policy_env_get(name: str):
            if isinstance(name, str):
                _check_name("env_get", name)
            return original_env_get(name)

        env.bindings["env_get"] = BuiltinFunction(
            current_env_get.name,
            policy_env_get,
            current_env_get.arity,
            current_env_get.param_names,
        )

    current_env_get_or = env.bindings.get("env_get_or")
    if "env_get_or" in allowed_gated and isinstance(
        current_env_get_or,
        BuiltinFunction,
    ):
        original_env_get_or = current_env_get_or.func

        def policy_env_get_or(name: str, default: str):
            if isinstance(name, str):
                _check_name("env_get_or", name)
            return original_env_get_or(name, default)

        env.bindings["env_get_or"] = BuiltinFunction(
            current_env_get_or.name,
            policy_env_get_or,
            current_env_get_or.arity,
            current_env_get_or.param_names,
        )

    current_cli_args = env.bindings.get("cli_args")
    if "cli_args" in allowed_gated and isinstance(current_cli_args, BuiltinFunction):
        original_cli_args = current_cli_args.func

        def policy_cli_args():
            _check_name("cli_args", "GENO_CLI_ARGS")
            return original_cli_args()

        env.bindings["cli_args"] = BuiltinFunction(
            current_cli_args.name,
            policy_cli_args,
            current_cli_args.arity,
            current_cli_args.param_names,
        )
