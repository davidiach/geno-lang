"""Process exit status normalization for an ``Int`` entrypoint result.

Its own module, with no imports from the rest of the package, because the
parent side of ``geno run``'s process-isolated lane needs it and deliberately
keeps the frontend out of that process (see ``geno/cli/run.py`` and
``geno/tests/test_cli_run_import_footprint.py``). ``geno.entrypoint``, which
owns classification, costs roughly 34 ms to import; this costs nothing.
"""

from __future__ import annotations


def exit_status_for_int_result(value: int) -> int:
    """Normalize an ``Int`` entrypoint result to a process exit status.

    The normalization is mathematical modulo 256, so ``-1`` becomes ``255`` and
    ``258`` becomes ``2``.  Python's ``%`` already floors toward negative
    infinity, which is the behavior ``docs/spec/v0.5.md`` 4.1.1 asks for;
    C-style truncation would give ``-1`` for ``-1``, and no status is negative.

    Every executable host shares this one definition.  The value that crosses
    an embedding boundary is never normalized: only the host's own status is.
    """
    return value % 256
