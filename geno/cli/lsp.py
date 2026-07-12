"""``geno lsp`` — Language Server Protocol server."""

from __future__ import annotations

import sys


def start_lsp(tcp: bool = False, port: int = 2087):
    """Start the Geno LSP server."""
    try:
        from ..lsp_server import start_server

        start_server(tcp=tcp, port=port)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
