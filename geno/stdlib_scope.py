"""Which names an unqualified import of a standard-library module binds.

Several standard-library modules re-export an always-available builtin under
its own name, sometimes at a narrower type: ``List.length`` takes only a list,
while the builtin ``length`` also takes a ``String`` or an ``Array``. Binding
such a name on ``import List`` would make ``length("abc")`` a type error in
the importing program. So an unqualified import of a standard-library module
leaves those names to the builtin, which accepts everything the module
function does, and the module function stays reachable qualified
(``List.length(xs)``). Modules a program defines itself are unaffected.

The type checker, the interpreter and both compilers consult this module, so
all of them bind the same names.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

STDLIB_DIR = (Path(__file__).parent / "std").resolve()


def is_stdlib_program(node: Any) -> bool:
    """Whether *node* (a program or a definition) was parsed from the stdlib."""
    location = getattr(node, "location", None)
    filename = getattr(location, "filename", None)
    if not filename or filename.startswith("<"):
        return False
    try:
        return Path(filename).resolve().parent == STDLIB_DIR
    except (OSError, ValueError):
        return False


def binds_unqualified(node: Any, name: str) -> bool:
    """Whether ``import M`` binds the exported function *name* of *node*.

    *node* is the module's program or the function's definition.
    """
    if not is_stdlib_program(node):
        return True
    from .builtin_registry import ALWAYS_AVAILABLE_BUILTINS

    return name not in ALWAYS_AVAILABLE_BUILTINS
