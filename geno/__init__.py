"""
Geno - An LLM-Native Programming Language
==========================================

This package provides a complete implementation of the Geno language,
designed from first principles for generation by large language models.

Modules:
    tokens: Token definitions and types
    lexer: Lexical analysis
    ast_nodes: Abstract syntax tree definitions
    parser: Recursive descent parser
    types: Type system implementation
    typechecker: Static type checking
    interpreter: Runtime evaluation
    compiler: Compilation to Python
    api: Embedding API (geno.run, geno.check, geno.constrain_prefix)
    diagnostics: Error codes and structured diagnostics
    repl: Interactive read-eval-print loop
    constraints: Constrained decoding for LLM generation
    harness: Test harness generation from specifications

The public names below are imported lazily (PEP 562): importing ``geno``
no longer pays for the whole toolchain, so CLI commands and embedders
load only what they touch. ``from geno import X`` and ``geno.X`` behave
exactly as before.
"""

from typing import TYPE_CHECKING

from ._version import __author__, __version__

if TYPE_CHECKING:  # static imports for type checkers and IDEs only
    from .api import (
        CheckResult,
        ConstraintResult,
        RunConfig,
        RunResult,
        Timing,
        check,
        check_path,
        constrain_prefix,
        run,
        run_path,
        validate_prefix,
    )
    from .ast_nodes import *
    from .compiler import Compiler, compile_and_exec, compile_to_python
    from .diagnostics import Diagnostic, ErrorCode, Severity
    from .interpreter import Interpreter, interpret
    from .js_compiler import JSCompiler, compile_to_js
    from .lexer import Lexer, LexerError
    from .monitoring import (
        BenchmarkValidationState,
        BuildInfo,
        HealthCheck,
        HealthReport,
        MetricsSnapshot,
        RunMetrics,
        RunOutcome,
        RuntimeMetricsCollector,
    )
    from .parser import ParseError, Parser, parse
    from .repl import run_repl
    from .sandbox import StepLimitExceeded
    from .tokens import Token, TokenType
    from .typechecker import TypeChecker, type_check
    from .types import TypeError as GenoTypeError
    from .values import GenoRuntimeError, value_to_json

__all__ = [  # noqa: RUF022 — grouped by category, not alphabetical
    # Package metadata
    "__author__",
    "__version__",
    # Core types
    "Token",
    "TokenType",
    "Lexer",
    "LexerError",
    "Parser",
    "ParseError",
    "TypeChecker",
    "GenoTypeError",
    "Interpreter",
    "GenoRuntimeError",
    "Compiler",
    "JSCompiler",
    "compile_to_js",
    "run_repl",
    # Convenience functions
    "parse",
    "type_check",
    "interpret",
    "compile_to_python",
    "compile_and_exec",
    # Embedding API
    "run",
    "run_path",
    "check",
    "check_path",
    "constrain_prefix",
    "validate_prefix",
    "RunResult",
    "CheckResult",
    "ConstraintResult",
    "RunConfig",
    "Timing",
    # Diagnostics
    "Diagnostic",
    "ErrorCode",
    "Severity",
    # Monitoring
    "BenchmarkValidationState",
    "BuildInfo",
    "HealthCheck",
    "HealthReport",
    "MetricsSnapshot",
    "RunMetrics",
    "RunOutcome",
    "RuntimeMetricsCollector",
    # Sandbox
    "StepLimitExceeded",
    # Serialization
    "value_to_json",
]

# Maps each public name to the submodule that defines it. Aliased names map
# to (module, original_name).
_LAZY_EXPORTS: dict[str, str | tuple[str, str]] = {
    "CheckResult": "api",
    "ConstraintResult": "api",
    "RunConfig": "api",
    "RunResult": "api",
    "Timing": "api",
    "check": "api",
    "check_path": "api",
    "constrain_prefix": "api",
    "run": "api",
    "run_path": "api",
    "validate_prefix": "api",
    "Compiler": "compiler",
    "compile_and_exec": "compiler",
    "compile_to_python": "compiler",
    "Diagnostic": "diagnostics",
    "ErrorCode": "diagnostics",
    "Severity": "diagnostics",
    "Interpreter": "interpreter",
    "interpret": "interpreter",
    "JSCompiler": "js_compiler",
    "compile_to_js": "js_compiler",
    "Lexer": "lexer",
    "LexerError": "lexer",
    "BenchmarkValidationState": "monitoring",
    "BuildInfo": "monitoring",
    "HealthCheck": "monitoring",
    "HealthReport": "monitoring",
    "MetricsSnapshot": "monitoring",
    "RunMetrics": "monitoring",
    "RunOutcome": "monitoring",
    "RuntimeMetricsCollector": "monitoring",
    "ParseError": "parser",
    "Parser": "parser",
    "parse": "parser",
    "run_repl": "repl",
    "StepLimitExceeded": "sandbox",
    "Token": "tokens",
    "TokenType": "tokens",
    "TypeChecker": "typechecker",
    "type_check": "typechecker",
    "GenoTypeError": ("types", "TypeError"),
    "GenoRuntimeError": "values",
    "value_to_json": "values",
}


def __getattr__(name: str) -> "object":
    import importlib

    target = _LAZY_EXPORTS.get(name)
    if target is not None:
        if isinstance(target, tuple):
            module_name, attr = target
        else:
            module_name, attr = target, name
        return getattr(importlib.import_module(f".{module_name}", __name__), attr)

    # ``from .ast_nodes import *`` re-exports: resolve AST node names on
    # demand without importing the whole toolchain eagerly.
    ast_nodes = importlib.import_module(".ast_nodes", __name__)
    if not name.startswith("_") and hasattr(ast_nodes, name):
        return getattr(ast_nodes, name)

    # Submodule access (``import geno; geno.api``) without prior import.
    try:
        return importlib.import_module(f".{name}", __name__)
    except ImportError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    import importlib

    ast_nodes = importlib.import_module(".ast_nodes", __name__)
    ast_names = [n for n in dir(ast_nodes) if not n.startswith("_")]
    return sorted(set(__all__) | set(ast_names) | set(globals()))
