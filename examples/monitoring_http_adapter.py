#!/usr/bin/env python3
"""Reference launcher for the packaged Geno hosted runtime server."""

from geno.server import (
    DEFAULT_ALLOWED_CAPABILITIES,
    create_handler,
    create_server,
    main,
)

__all__ = [
    "DEFAULT_ALLOWED_CAPABILITIES",
    "create_handler",
    "create_server",
    "main",
]


if __name__ == "__main__":
    main()
