"""
Shared first-pass definition collection for compiler backends.
"""

from dataclasses import dataclass, field

from .ast_nodes import (
    FunctionDef,
    ImplDef,
    Program,
    TraitDef,
    TypeAlias,
    TypeDef,
    TypeVariant,
)


@dataclass
class DefinitionIndex:
    """Collected top-level definitions needed by the compiler backends."""

    type_defs: dict[str, TypeDef] = field(default_factory=dict)
    type_aliases: dict[str, TypeAlias] = field(default_factory=dict)
    func_param_names: dict[str, list[str]] = field(default_factory=dict)
    trait_defs: dict[str, TraitDef] = field(default_factory=dict)
    impl_defs: dict[tuple[str, str], ImplDef] = field(default_factory=dict)
    trait_dispatch: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    # Reverse index: constructor name -> TypeVariant (O(1) lookup)
    constructor_to_variant: dict[str, TypeVariant] = field(default_factory=dict)


def collect_definitions(
    program: Program, into: DefinitionIndex | None = None
) -> DefinitionIndex:
    """Collect compiler-facing definition metadata from a parsed program."""
    index = into or DefinitionIndex()

    for defn in program.definitions:
        if isinstance(defn, TypeDef):
            # Remove stale constructor entries if type is being redefined
            old_def = index.type_defs.get(defn.name)
            if old_def is not None:
                for v in old_def.variants:
                    index.constructor_to_variant.pop(v.name, None)
            index.type_defs[defn.name] = defn
            for variant in defn.variants:
                index.constructor_to_variant[variant.name] = variant
        elif isinstance(defn, TypeAlias):
            index.type_aliases[defn.name] = defn
        elif isinstance(defn, FunctionDef):
            index.func_param_names[defn.name] = [p.name for p in defn.params]
        elif isinstance(defn, TraitDef):
            index.trait_defs[defn.name] = defn
        elif isinstance(defn, ImplDef):
            index.impl_defs[(defn.trait_name, defn.target_type)] = defn
            for method in defn.methods:
                index.trait_dispatch.setdefault(method.name, []).append(
                    (defn.trait_name, defn.target_type)
                )
                index.func_param_names[method.name] = [p.name for p in method.params]

    return index
