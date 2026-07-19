"""Diagnostic conversion helpers for the Geno language server."""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

from geno.diagnostics import Severity

_lsp_types: ModuleType | None
try:
    _lsp_types = importlib.import_module("lsprotocol.types")
except ImportError:  # pragma: no cover - exercised by dependency-free imports
    _lsp_types = None

_SEVERITY_MAP = {
    Severity.ERROR: 1,  # DiagnosticSeverity.Error
    Severity.WARNING: 2,  # DiagnosticSeverity.Warning
    Severity.INFO: 3,  # DiagnosticSeverity.Information
}


def _require_lsp_types() -> Any:
    if _lsp_types is None:
        raise RuntimeError("lsprotocol is required for LSP diagnostic conversion")
    return _lsp_types


def to_lsp_diagnostic(diag: Any) -> Any:
    """Convert a Geno Diagnostic to an LSP Diagnostic."""
    types = _require_lsp_types()
    line = max((diag.location.line if diag.location else 1) - 1, 0)
    col = max((diag.location.column if diag.location else 1) - 1, 0)

    return types.Diagnostic(
        range=types.Range(
            start=types.Position(line=line, character=col),
            end=types.Position(line=line, character=col),
        ),
        severity=types.DiagnosticSeverity(_SEVERITY_MAP.get(diag.severity, 1)),
        code=diag.code.value if diag.code else None,
        source="geno",
        message=diag.message,
    )


def error_diagnostic(
    message: str,
    *,
    line: int = 0,
    character: int = 0,
    code: str | None = None,
) -> Any:
    """Build a categorized single-point LSP error diagnostic."""
    types = _require_lsp_types()
    return types.Diagnostic(
        range=types.Range(
            start=types.Position(line=line, character=character),
            end=types.Position(line=line, character=character),
        ),
        severity=types.DiagnosticSeverity.Error,
        code=code,
        source="geno",
        message=message,
    )
