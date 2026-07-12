"""``geno repl`` — interactive REPL."""

from __future__ import annotations


def start_repl():
    """Start the interactive REPL."""
    from ..repl import run_repl

    run_repl()
