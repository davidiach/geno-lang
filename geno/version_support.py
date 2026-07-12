"""Shared Python-version support helpers for Geno entry points."""

from __future__ import annotations

import sys
from typing import Optional

SUPPORTED_MIN_PYTHON = (3, 10)
SUPPORTED_MAX_PYTHON = (3, 13)


def python_version_tuple(
    version_info: tuple[int, ...] | None = None,
) -> tuple[int, int]:
    """Return the major/minor Python version tuple."""
    if version_info is None:
        return (sys.version_info[0], sys.version_info[1])
    return (version_info[0], version_info[1])


def supported_python_range() -> str:
    """Return the supported Python range for user-facing messages."""
    return (
        f"{SUPPORTED_MIN_PYTHON[0]}.{SUPPORTED_MIN_PYTHON[1]}-"
        f"{SUPPORTED_MAX_PYTHON[0]}.{SUPPORTED_MAX_PYTHON[1]}"
    )


def is_supported_python(version_info: tuple[int, ...] | None = None) -> bool:
    """Return whether the current interpreter is within Geno's supported range."""
    version = python_version_tuple(version_info)
    return SUPPORTED_MIN_PYTHON <= version <= SUPPORTED_MAX_PYTHON


def unsupported_python_message(
    version_info: tuple[int, ...] | None = None,
) -> str:
    """Return a consistent unsupported-version message."""
    version = python_version_tuple(version_info)
    return (
        f"Geno requires Python {supported_python_range()}, "
        f"but you are running {version[0]}.{version[1]}. "
        "Please install a supported version."
    )
