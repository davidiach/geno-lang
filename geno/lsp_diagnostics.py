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
    fixes = tuple(getattr(diag, "fixes", ()))

    return types.Diagnostic(
        range=_fix_range(types, fixes, line, col),
        severity=types.DiagnosticSeverity(_SEVERITY_MAP.get(diag.severity, 1)),
        code=diag.code.value if diag.code else None,
        source="geno",
        message=diag.message,
        data={"fixes": [fix.to_dict() for fix in fixes]} if fixes else None,
    )


def error_diagnostic(
    message: str,
    *,
    line: int = 0,
    character: int = 0,
    code: str | None = None,
    fixes: tuple[Any, ...] = (),
) -> Any:
    """Build a categorized LSP error diagnostic, with its fixes if any."""
    types = _require_lsp_types()
    return types.Diagnostic(
        range=_fix_range(types, fixes, line, character),
        severity=types.DiagnosticSeverity.Error,
        code=code,
        source="geno",
        message=message,
        data={"fixes": [fix.to_dict() for fix in fixes]} if fixes else None,
    )


def _fix_range(types: Any, fixes: tuple[Any, ...], line: int, character: int) -> Any:
    """The text a diagnostic's first fix replaces, else its single point."""
    edit = fixes[0].edits[0] if fixes and fixes[0].edits else None
    if edit is not None and edit.line - 1 == line and edit.end_column > edit.column:
        return types.Range(
            start=types.Position(line=edit.line - 1, character=edit.column - 1),
            end=types.Position(line=edit.end_line - 1, character=edit.end_column - 1),
        )
    return types.Range(
        start=types.Position(line=line, character=character),
        end=types.Position(line=line, character=character),
    )


def fix_code_actions(uri: str, diagnostic: Any) -> list[Any]:
    """Quick fixes for the fixes a Geno diagnostic carries in ``data``."""
    types = _require_lsp_types()
    data = getattr(diagnostic, "data", None)
    fixes = data.get("fixes", []) if isinstance(data, dict) else []
    actions = []
    for index, fix in enumerate(fixes):
        edits = [
            types.TextEdit(
                range=types.Range(
                    start=types.Position(
                        line=edit["start"]["line"] - 1,
                        character=edit["start"]["column"] - 1,
                    ),
                    end=types.Position(
                        line=edit["end"]["line"] - 1,
                        character=edit["end"]["column"] - 1,
                    ),
                ),
                new_text=edit["text"],
            )
            for edit in fix["edits"]
        ]
        actions.append(
            types.CodeAction(
                title=fix["title"],
                kind=types.CodeActionKind.QuickFix,
                diagnostics=[diagnostic],
                is_preferred=index == 0,
                edit=types.WorkspaceEdit(changes={uri: edits}),
            )
        )
    return actions
