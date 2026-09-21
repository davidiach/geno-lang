"""Small call-resolution helpers for the typechecker.

The main ``TypeChecker`` still owns semantic diagnostics and inference. This
module holds pure helpers for call-site metadata so that call resolution can be
tested and refactored independently of the rest of the checker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .ast_nodes import Expression, FieldAccess, Identifier


@dataclass(frozen=True)
class CallParameterInfo:
    """Resolved parameter metadata for a function-call expression."""

    default_lookup_name: str | None
    default_count: int
    param_names: tuple[str, ...]
    #: The callee as it is written at the call site, when it can be named at
    #: all: ``f`` for a direct call, ``M.f`` for a call through an imported
    #: module. ``default_lookup_name`` cannot stand in for it — that one is
    #: deliberately None for a module call, because defaults are looked up per
    #: module — and a diagnostic that rewrites the call needs the source form.
    callee_display: str | None = None


def resolve_call_parameter_info(
    function: Expression,
    func_param_names: Mapping[str, Sequence[str]],
    module_param_names: Mapping[str, Mapping[str, Sequence[str]]],
    func_default_counts: Mapping[str, int] | None = None,
    module_default_counts: Mapping[str, Mapping[str, int]] | None = None,
) -> CallParameterInfo:
    """Resolve default and named-argument metadata for a call target."""

    func_default_counts = func_default_counts or {}
    module_default_counts = module_default_counts or {}

    if isinstance(function, Identifier):
        return CallParameterInfo(
            default_lookup_name=function.name,
            default_count=func_default_counts.get(function.name, 0),
            param_names=tuple(func_param_names.get(function.name, ())),
            callee_display=function.name,
        )

    if isinstance(function, FieldAccess):
        target_name = getattr(function.target, "name", None)
        if isinstance(target_name, str):
            module_params = module_param_names.get(target_name, {})
            module_defaults = module_default_counts.get(target_name, {})
            return CallParameterInfo(
                default_lookup_name=None,
                default_count=module_defaults.get(function.field_name, 0),
                param_names=tuple(module_params.get(function.field_name, ())),
                callee_display=f"{target_name}.{function.field_name}",
            )

    return CallParameterInfo(default_lookup_name=None, default_count=0, param_names=())
