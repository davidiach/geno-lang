#!/usr/bin/env python3
"""Audit the Linux/Python 3.11 release lock on its target platform."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]


def build_command(platform: str | None = None) -> tuple[str, ...] | None:
    """Return the release-lock audit command for its supported platform."""
    platform = sys.platform if platform is None else platform
    if not platform.startswith("linux"):
        return None
    return (
        sys.executable,
        "-m",
        "pip_audit",
        "--require-hashes",
        "-r",
        "requirements-release.lock",
        "--strict",
        "--progress-spinner",
        "off",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the audit on Linux and report an explicit skip elsewhere."""
    del argv
    command = build_command()
    if command is None:
        print(
            "Skipping requirements-release.lock audit: "
            "the publish lock targets Linux/Python 3.11"
        )
        return 0
    return subprocess.run(command, cwd=ROOT, check=False).returncode  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(main())
