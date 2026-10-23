"""Keep Geno names apart from the names a backend's host relies on.

Compiled programs share a namespace with their runtime prelude and the host
language's builtins. A Geno program may still name a function ``len``, a local
``list`` or a constructor ``T``: each backend renames such an identifier, at
every definition and every use, by appending ``_g`` (proposal 0011). Names that
Geno itself defines at run time (builtin functions, and the types and
constructors of builtin ADTs such as ``Some``) are never renamed, since a
reference to one must reach the runtime.
"""

from __future__ import annotations

from functools import lru_cache

HOST_NAME_SUFFIX = "_g"


@lru_cache(maxsize=1)
def geno_runtime_names() -> frozenset[str]:
    """Names the Geno runtime defines that Geno programs refer to."""
    from .builtin_registry import all_builtin_names
    from .typechecker import TypeChecker

    checker = TypeChecker()
    names = set(all_builtin_names())
    names.update(checker.type_defs)
    names.update(checker._constructor_to_type)
    names.add("None")
    return frozenset(names)
