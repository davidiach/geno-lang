"""Runtime/backend builtin contracts derived from manifest metadata.

The long-term extraction path is to move interpreter builtin registration out
of ``Interpreter.__init__`` in slices. Until that initializer is split, this
module is the executable contract for the slice with the most target-sensitive
behavior: browser graphics/input builtins and their direct-interpreter
compatibility fallbacks.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, cast

from .builtin_registry import BuiltinSpec, source_builtin_specs
from .target_profile import TARGETS_TOML, VALID_TARGETS

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


Availability = Literal["available", "capability-gated", "unavailable"]
FallbackBehavior = Literal[
    "noop",
    "screen-width",
    "screen-height",
    "false",
    "zero",
    "empty-string",
]

BROWSER_TARGET = "browser"

# These builtins are valid for browser builds. Direct interpreter execution
# keeps compatibility fallbacks so existing app examples can be evaluated
# without a canvas/event-loop host.
INTERPRETER_BROWSER_FALLBACKS: Mapping[str, FallbackBehavior] = {
    "clear_screen": "noop",
    "draw_rect": "noop",
    "draw_rect_outline": "noop",
    "draw_circle": "noop",
    "draw_line": "noop",
    "draw_text": "noop",
    "screen_width": "screen-width",
    "screen_height": "screen-height",
    "is_key_down": "false",
    "is_key_pressed": "false",
    "mouse_x": "zero",
    "mouse_y": "zero",
    "is_mouse_down": "false",
    "is_mouse_clicked": "false",
    "get_text_input": "empty-string",
    "clear_text_input": "noop",
}


@dataclass(frozen=True)
class BuiltinBackendContract:
    """A manifest builtin as seen by compiler backends and target policy."""

    source_name: str
    runtime_name: str
    runtime_param_names: tuple[str, ...]
    source_param_names: tuple[str, ...]
    capability: str | None
    python_backend_name: str
    js_backend_name: str
    target_status: Mapping[str, Availability]

    def status_for(self, target: str) -> Availability:
        return self.target_status[target]


def browser_interpreter_fallbacks() -> dict[str, FallbackBehavior]:
    """Return browser-only builtins that have interpreter compatibility stubs."""

    return dict(INTERPRETER_BROWSER_FALLBACKS)


def browser_interpreter_fallback_names() -> frozenset[str]:
    """Return source builtin names with explicit interpreter fallback behavior."""

    return frozenset(INTERPRETER_BROWSER_FALLBACKS)


def builtin_backend_contracts(
    targets: Mapping[str, Any] | None = None,
) -> dict[str, BuiltinBackendContract]:
    """Return source builtin contracts for Python/JS backends and targets."""

    raw_targets = targets if targets is not None else _load_targets()
    builtin_infos = cast(Mapping[str, Any], raw_targets.get("builtins", {}))
    contracts: dict[str, BuiltinBackendContract] = {}

    for source_name, spec in source_builtin_specs().items():
        info = cast(Mapping[str, Any], builtin_infos.get(source_name, {}))
        contracts[source_name] = _contract_from_spec(source_name, spec, info)

    return contracts


def browser_only_builtin_names(
    targets: Mapping[str, Any] | None = None,
) -> frozenset[str]:
    """Return builtins available only on the browser target."""

    contracts = builtin_backend_contracts(targets)
    names = {
        name
        for name, contract in contracts.items()
        if contract.status_for(BROWSER_TARGET) == "available"
        and all(
            contract.status_for(target) == "unavailable"
            for target in VALID_TARGETS
            if target != BROWSER_TARGET
        )
    }
    return frozenset(names)


def _contract_from_spec(
    source_name: str,
    spec: BuiltinSpec,
    info: Mapping[str, Any],
) -> BuiltinBackendContract:
    target_status = {
        target: _availability(info.get(target, "available"))
        for target in sorted(VALID_TARGETS)
    }
    return BuiltinBackendContract(
        source_name=source_name,
        runtime_name=spec.runtime_name,
        runtime_param_names=spec.runtime_param_names,
        source_param_names=spec.source_param_names,
        capability=spec.capability,
        python_backend_name=spec.python_backend_name,
        js_backend_name=spec.js_backend_name,
        target_status=target_status,
    )


def _availability(value: Any) -> Availability:
    if value in {"available", "capability-gated", "unavailable"}:
        return cast(Availability, value)
    raise ValueError(f"invalid builtin availability status: {value!r}")


def _load_targets(path: Path | None = None) -> dict[str, Any]:
    target_path = path or TARGETS_TOML
    return cast(dict[str, Any], tomllib.loads(target_path.read_text(encoding="utf-8")))
