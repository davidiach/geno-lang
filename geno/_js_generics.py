"""Specialize generic functions for the JavaScript backend.

JavaScript has one number type, so a ``Float`` holding ``2.0`` and an ``Int``
holding ``2`` are the same runtime value. The JS backend therefore formats a
number by its static type, which the Python backend and the interpreter never
need to. Inside the body of ``func show[T](x: T)`` the static type of ``x`` is
the opaque ``T``, so a single compiled body would print ``show(2.0)`` as
``2`` where every other backend prints ``2.0``.

This module plans one extra copy of a generic function for each distinct way
its type arguments involve ``Float``, and the backend compiles each copy with
the type parameters replaced by those arguments. Instantiations that involve
no ``Float`` share the ordinary, erased definition, so a program that never
passes a ``Float`` through a type parameter compiles exactly as before.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from .ast_nodes import FieldAccess, FunctionDef, Identifier, ImplDef, Program
from .types import (
    AsyncType,
    FloatType,
    FuncType,
    GenericInstance,
    Type,
    TypeParam,
    map_type,
    type_children,
)

# A generic function that keeps instantiating itself at ever larger types
# (``f[T]`` calling ``f[List[T]]``) would otherwise plan copies forever.
# Instantiations past this many per function use the erased definition.
MAX_SPECIALIZATIONS_PER_FUNCTION = 32

_ERASED = "_"


def float_key(type_: Type) -> str:
    """Describe where *type_* holds a ``Float``, or ``"_"`` if nowhere.

    Function and async types are opaque inside a generic body: it can pass
    such a value along but never call or await it, so what they return does
    not change how the body formats anything.
    """
    if isinstance(type_, FloatType):
        return "F"
    if isinstance(type_, (FuncType, AsyncType)):
        return _ERASED
    parts = type_children(type_)
    if parts is None:
        return _ERASED
    children = [float_key(child) for child in parts[0]]
    if all(child == _ERASED for child in children):
        return _ERASED
    label = type(type_).__name__ + str(getattr(type_, "name", ""))
    return f"{label}[{','.join(children)}]"


def substitute_type_params(type_: Type, subst: Mapping[str, Type]) -> Type:
    """Replace each rigid type parameter named in *subst* inside *type_*."""
    if not subst:
        return type_
    if isinstance(type_, TypeParam):
        return subst.get(type_.name, type_)
    return map_type(type_, lambda child: substitute_type_params(child, subst))


@dataclass
class Specialization:
    """One planned copy of a generic function."""

    export_name: str
    subst: dict[str, Type]


@dataclass
class SpecializationPlan:
    """The copies to compile, keyed by ``(defining module, function name)``.

    The module is ``None`` for a single-program compile.
    """

    by_function: dict[tuple[str | None, str], dict[tuple[str, ...], Specialization]] = (
        field(default_factory=dict)
    )
    _generic_defs: dict[tuple[str | None, str], FunctionDef] = field(
        default_factory=dict
    )

    def specializations(self, module: str | None, name: str) -> list[Specialization]:
        return list(self.by_function.get((module, name), {}).values())

    def export_names(self, module: str | None, names: Iterable[str]) -> list[str]:
        """Exported names for the copies of the functions in *names*."""
        exported = set(names)
        return [
            spec.export_name
            for (spec_module, name), specs in self.by_function.items()
            if spec_module == module and name in exported
            for spec in specs.values()
        ]

    def lookup(
        self,
        instance: GenericInstance,
        subst: Mapping[str, Type],
        current_module: str | None,
    ) -> str | None:
        """Return the copy an instance resolves to, or ``None`` for the base."""
        target = self._target(instance, current_module)
        if target is None:
            return None
        key = self._key(instance, subst)
        if key is None:
            return None
        spec = self.by_function.get(target, {}).get(key)
        return None if spec is None else spec.export_name

    def _target(
        self, instance: GenericInstance, current_module: str | None
    ) -> tuple[str | None, str] | None:
        module = instance.module if instance.module is not None else current_module
        target = (module, instance.name)
        return target if target in self._generic_defs else None

    @staticmethod
    def _key(
        instance: GenericInstance, subst: Mapping[str, Type]
    ) -> tuple[str, ...] | None:
        key = tuple(
            float_key(substitute_type_params(arg, subst)) for arg in instance.type_args
        )
        return None if all(part == _ERASED for part in key) else key

    def _request(
        self,
        instance: GenericInstance,
        subst: Mapping[str, Type],
        current_module: str | None,
    ) -> tuple[str | None, FunctionDef, Specialization] | None:
        target = self._target(instance, current_module)
        if target is None:
            return None
        key = self._key(instance, subst)
        if key is None:
            return None
        specs = self.by_function.setdefault(target, {})
        if key in specs:
            return None
        if len(specs) >= MAX_SPECIALIZATIONS_PER_FUNCTION:
            return None
        defn = self._generic_defs[target]
        spec = Specialization(
            export_name=f"{defn.name}$g{len(specs) + 1}",
            subst={
                param: substitute_type_params(arg, subst)
                for param, arg in zip(defn.type_params, instance.type_args)
            },
        )
        specs[key] = spec
        return target[0], defn, spec


def _generic_references(node: Any) -> Iterator[GenericInstance]:
    """Yield the generic instances named anywhere inside *node*.

    Instances live on the identifier or module field access that names the
    function, whether it is called or used as a value. Example clauses are
    skipped because the JS backend does not compile them.
    """
    stack = [node]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (list, tuple)):
            stack.extend(current)
            continue
        if isinstance(current, (Identifier, FieldAccess)):
            instance = getattr(current, "_generic_instance", None)
            if instance is not None:
                yield instance
        if not hasattr(current, "__dict__"):
            continue
        for name, value in vars(current).items():
            if name.startswith("_") or name == "examples":
                continue
            if not isinstance(value, (str, int, float, bool, type(None))):
                stack.append(value)


def plan_specializations(
    modules: Iterable[tuple[str | None, Program]],
) -> SpecializationPlan:
    """Plan every Float-dependent copy the compiled program can reach.

    That is every copy named by non-generic code or by a generic function's
    erased body, and then by the copies those need.
    """
    plan = SpecializationPlan()
    module_list = list(modules)
    for module, program in module_list:
        for defn in program.definitions:
            if isinstance(defn, FunctionDef) and defn.type_params:
                plan._generic_defs[(module, defn.name)] = defn

    work: list[tuple[str | None, FunctionDef, Specialization]] = []

    def visit(node: Any, subst: Mapping[str, Type], module: str | None) -> None:
        for instance in _generic_references(node):
            requested = plan._request(instance, subst, module)
            if requested is not None:
                work.append(requested)

    for module, program in module_list:
        for defn in program.definitions:
            if isinstance(defn, FunctionDef):
                # A generic function's erased body is compiled too, and it may
                # call another generic function at concrete Float types.
                visit(defn, {}, module)
            elif isinstance(defn, ImplDef):
                visit(defn.methods, {}, module)

    while work:
        module, defn, spec = work.pop()
        visit(defn, spec.subst, module)
    return plan
