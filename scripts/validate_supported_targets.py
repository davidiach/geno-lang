#!/usr/bin/env python3
"""Validate docs/SUPPORTED_TARGETS.md against targets.toml."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]


def _load_targets(path: Path | None = None) -> dict[str, Any]:
    target_path = path or (ROOT / "targets.toml")
    return cast(dict[str, Any], tomllib.loads(target_path.read_text(encoding="utf-8")))


def _target_names(raw: dict[str, Any], errors: list[str]) -> tuple[str, ...]:
    targets = raw.get("targets", {})
    if not isinstance(targets, dict):
        errors.append("'targets' must be an object in targets.toml")
        return ()
    return tuple(name for name in targets if isinstance(name, str))


def _strip_code(text: str) -> str:
    return text.strip().strip("`")


def _split_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(set(cell.replace(":", "")) <= {"-"} for cell in cells)


def _runtime_text(value: object) -> str:
    if value == "python":
        return "Python"
    if value == "node":
        return "Node.js"
    if value == "browser":
        return "Browser"
    return str(value)


def _status_text(info: dict[str, Any], target: str) -> str:
    availability = info.get(target, "available")
    if availability == "available":
        return "Available"
    if availability == "unavailable":
        return "--"
    if availability == "capability-gated":
        return f"Cap: {info.get('capability', '')}"
    return f"<unknown: {availability}>"


def _doc_target_overview_rows(
    text: str,
    errors: list[str],
) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    expected_header = ["Target", "Runtime", "Entry Command", "Compile Command"]
    in_target_table = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            if in_target_table:
                break
            continue
        cells = _split_table_cells(line)
        if cells == expected_header:
            in_target_table = True
            continue
        if not in_target_table or _is_table_separator(cells):
            continue
        if len(cells) != len(expected_header):
            errors.append(f"Target table row has {len(cells)} columns: {line}")
            continue
        name = _strip_code(cells[0])
        if name in rows:
            errors.append(f"Duplicate target row for '{name}'")
        rows[name] = dict(zip(expected_header[1:], cells[1:]))
    return rows


def _doc_target_capabilities(
    text: str,
    target_names: tuple[str, ...],
    errors: list[str],
) -> dict[str, set[str]]:
    current_target: str | None = None
    current_heading: str | None = None
    capabilities: dict[str, set[str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("### "):
            candidate = line.removeprefix("### ").strip("` ")
            current_heading = candidate
            current_target = candidate if candidate in target_names else None
            continue
        if not line.startswith("**Capabilities:**"):
            continue
        if current_target is None:
            if current_heading:
                errors.append(
                    f"Capabilities line found for unknown target '{current_heading}'"
                )
            continue
        raw_caps = line.removeprefix("**Capabilities:**").strip()
        capabilities[current_target] = {
            cap.strip().strip("`") for cap in raw_caps.split(",") if cap.strip()
        }
    return capabilities


def _doc_builtin_rows(
    text: str,
    target_names: tuple[str, ...],
    errors: list[str],
) -> dict[str, tuple[str | None, dict[str, str]]]:
    rows: dict[str, tuple[str | None, dict[str, str]]] = {}
    current_header: list[str] | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("|"):
            current_header = None
            continue
        cells = _split_table_cells(stripped)
        if cells and cells[0] == "Builtin":
            has_capability = len(cells) > 1 and cells[1] == "Capability"
            target_cells = tuple(cells[2:] if has_capability else cells[1:])
            if target_cells != target_names:
                errors.append(
                    "Builtin table target columns mismatch: "
                    f"expected {list(target_names)}, got {list(target_cells)}"
                )
            current_header = cells
            continue
        if current_header is None or _is_table_separator(cells):
            continue
        if not cells or not cells[0].startswith("`"):
            continue
        name = _strip_code(cells[0])
        if len(cells) != len(current_header):
            errors.append(f"Builtin row for '{name}' has wrong column count")
            continue
        if len(current_header) > 1 and current_header[1] == "Capability":
            capability = cells[1].strip("` ") or None
            status_cells = cells[2:]
        else:
            capability = None
            status_cells = cells[1:]
        if name in rows:
            errors.append(f"Duplicate builtin row for '{name}'")
        rows[name] = (capability, dict(zip(target_names, status_cells)))
    return rows


def validate_supported_targets(
    targets: dict[str, Any] | None = None,
    docs_text: str | None = None,
) -> list[str]:
    """Return validation errors for supported-target docs."""
    raw = targets or _load_targets()
    text = docs_text or (ROOT / "docs" / "SUPPORTED_TARGETS.md").read_text(
        encoding="utf-8"
    )
    errors: list[str] = []

    target_infos = raw.get("targets", {})
    target_names = _target_names(raw, errors)
    doc_target_rows = _doc_target_overview_rows(text, errors)
    expected_target_rows = set(target_names)
    actual_target_rows = set(doc_target_rows)
    missing_targets = sorted(expected_target_rows - actual_target_rows)
    extra_targets = sorted(actual_target_rows - expected_target_rows)
    if missing_targets:
        errors.append(f"Missing target rows: {missing_targets}")
    if extra_targets:
        errors.append(f"Extra target rows: {extra_targets}")

    for target in target_names:
        info = target_infos.get(target, {})
        row = doc_target_rows.get(target)
        if not isinstance(info, dict) or row is None:
            continue
        expected_fields = {
            "Runtime": _runtime_text(info.get("runtime", "")),
            "Entry Command": f"`{info.get('entry_command', '')}`",
            "Compile Command": f"`{info.get('compile_command', '')}`",
        }
        for field, expected in expected_fields.items():
            actual = row.get(field)
            if actual != expected:
                errors.append(
                    f"Target '{target}' {field.lower()} mismatch: "
                    f"expected {expected!r}, got {actual!r}"
                )

    doc_caps = _doc_target_capabilities(text, target_names, errors)
    for target in target_names:
        expected_capabilities = set(
            target_infos.get(target, {}).get("capabilities", [])
        )
        actual_capabilities = doc_caps.get(target)
        if actual_capabilities is None:
            errors.append(f"Missing capabilities line for target '{target}'")
        elif actual_capabilities != expected_capabilities:
            errors.append(
                f"Capabilities for target '{target}' mismatch: "
                f"expected {sorted(expected_capabilities)}, "
                f"got {sorted(actual_capabilities)}"
            )

    doc_rows = _doc_builtin_rows(text, target_names, errors)
    builtin_infos = raw.get("builtins", {})
    if not isinstance(builtin_infos, dict):
        errors.append("'builtins' must be an object in targets.toml")
        return errors
    extra_builtins = sorted(set(doc_rows) - set(builtin_infos))
    if extra_builtins:
        errors.append(f"Extra builtin rows: {extra_builtins}")

    for builtin_name, info in sorted(builtin_infos.items()):
        builtin_row = doc_rows.get(builtin_name)
        if builtin_row is None:
            errors.append(f"Missing builtin row for '{builtin_name}'")
            continue
        if not isinstance(info, dict):
            errors.append(f"Builtin '{builtin_name}' entry must be an object")
            continue
        doc_capability, doc_statuses = builtin_row
        expected_capability = info.get("capability")
        if doc_capability != expected_capability:
            errors.append(
                f"Capability for builtin '{builtin_name}' mismatch: "
                f"expected {expected_capability!r}, got {doc_capability!r}"
            )
        for target in target_names:
            expected_status = _status_text(info, target)
            actual_status = doc_statuses.get(target)
            if actual_status != expected_status:
                errors.append(
                    f"Status for builtin '{builtin_name}' on target '{target}' "
                    f"mismatch: expected {expected_status!r}, got {actual_status!r}"
                )

    return errors


def main() -> int:
    errors = validate_supported_targets()
    if errors:
        print("supported target documentation errors:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("supported target documentation OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
