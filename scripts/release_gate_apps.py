#!/usr/bin/env python3
"""Validate example applications as part of the release gate."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from geno.manifest import parse_manifest  # noqa: E402

DEFAULT_APP_DIR = ROOT / "examples" / "apps"
_LIFECYCLE_RE = re.compile(r"^\s*func\s+(init|update|render)\b", re.MULTILINE)


@dataclass(frozen=True)
class AppCandidate:
    """One example app project or standalone app file."""

    path: Path
    target: str
    is_project: bool


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _project_target(path: Path) -> str:
    manifest = parse_manifest(path / "geno.toml")
    return manifest.targets[0] if manifest.targets else ""


def _standalone_target(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if _LIFECYCLE_RE.search(text):
        return "browser"
    return "standalone-cli"


def discover_app_candidates(app_dir: Path = DEFAULT_APP_DIR) -> list[AppCandidate]:
    """Return release-gate app candidates under ``examples/apps``."""
    if not app_dir.is_dir():
        return []

    candidates: list[AppCandidate] = []
    project_dirs = sorted({path.parent for path in app_dir.rglob("geno.toml")})
    for project_dir in project_dirs:
        candidates.append(
            AppCandidate(
                path=project_dir,
                target=_project_target(project_dir),
                is_project=True,
            )
        )

    for file_path in sorted(app_dir.glob("*.geno")):
        candidates.append(
            AppCandidate(
                path=file_path,
                target=_standalone_target(file_path),
                is_project=False,
            )
        )

    return candidates


def _run(command: Sequence[str], *, timeout: int | None = None) -> int:
    print("  Running: " + " ".join(command), flush=True)
    try:
        result = subprocess.run(  # noqa: S603
            command,
            cwd=ROOT,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124
    return result.returncode


def _validate_project(candidate: AppCandidate, temp_dir: Path) -> int:
    path = _display(candidate.path)
    print(f"=== Validating project: {path} ===", flush=True)

    for subcommand in ("check", "test"):
        code = _run((sys.executable, "-m", "geno", subcommand, path))
        if code != 0:
            print(f"::error::geno {subcommand} failed for {path}", flush=True)
            return code

    target = candidate.target
    if target == "browser":
        output = temp_dir / f"{candidate.path.name}.html"
        code = _run((sys.executable, "-m", "geno", "build", path, "-o", str(output)))
        if code != 0:
            print(f"::error::geno build failed for {path}", flush=True)
            return code
    elif target == "python-hosted":
        output = temp_dir / f"{candidate.path.name}_hosted.py"
        code = _run((sys.executable, "-m", "geno", "compile", path, "-o", str(output)))
        if code != 0:
            print(f"::error::geno compile failed for hosted app {path}", flush=True)
            return code
    elif target == "node-cli":
        output = temp_dir / f"{candidate.path.name}.js"
        code = _run(
            (
                sys.executable,
                "-m",
                "geno",
                "compile",
                path,
                "--target",
                "js",
                "-o",
                str(output),
            )
        )
        if code != 0:
            print(f"::error::geno compile --target js failed for {path}", flush=True)
            return code

        node_bin = shutil.which("node")
        if node_bin is None:
            print(f"::error::node executable not found for {path}", flush=True)
            return 127
        result = subprocess.run(  # noqa: S603
            (node_bin, str(output)),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"::error::node execution failed for {path}", flush=True)
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            return result.returncode
        expected = candidate.path / "expected.out"
        if expected.is_file() and result.stdout != expected.read_text(encoding="utf-8"):
            print(f"::error::node output mismatch for {path}", flush=True)
            print(result.stdout, end="")
            return 1
    elif target in {"python-cli", "cli"}:
        output = temp_dir / f"{candidate.path.name}.py"
        code = _run((sys.executable, "-m", "geno", "compile", path, "-o", str(output)))
        if code != 0:
            print(f"::error::geno compile failed for {path}", flush=True)
            return code
    else:
        print(
            f"::warning::Unknown target '{target}' for {path}; skipping target-specific validation",
            flush=True,
        )

    print("  PASSED", flush=True)
    return 0


def _validate_standalone(candidate: AppCandidate, temp_dir: Path) -> int:
    path = _display(candidate.path)
    print(f"=== Validating app: {path} ===", flush=True)

    for subcommand in ("check", "test"):
        code = _run((sys.executable, "-m", "geno", subcommand, path))
        if code != 0:
            print(f"::error::geno {subcommand} failed for {path}", flush=True)
            return code

    if candidate.target == "browser":
        output = temp_dir / f"{candidate.path.stem}.html"
        code = _run((sys.executable, "-m", "geno", "build", path, "-o", str(output)))
        if code != 0:
            print(f"::error::geno build failed for {path}", flush=True)
            return code
    else:
        code = _run((sys.executable, "-m", "geno", "run", path), timeout=30)
        if code != 0:
            print(f"::error::geno run failed for {path}", flush=True)
            return code

    print("  PASSED", flush=True)
    return 0


def validate_app_candidates(candidates: Sequence[AppCandidate]) -> int:
    """Validate app candidates and return a process exit code."""
    if not candidates:
        print("::notice::No apps validated (release gate is a no-op)", flush=True)
        return 0

    failed = 0
    passed = 0
    with tempfile.TemporaryDirectory(prefix="geno-release-apps-") as raw_temp:
        temp_dir = Path(raw_temp)
        for candidate in candidates:
            code = (
                _validate_project(candidate, temp_dir)
                if candidate.is_project
                else _validate_standalone(candidate, temp_dir)
            )
            if code == 0:
                passed += 1
            else:
                failed += 1

    print("", flush=True)
    print("=== Release Gate Summary ===", flush=True)
    print(f"  Passed: {passed}", flush=True)
    print(f"  Failed: {failed}", flush=True)
    if failed:
        print(f"::error::{failed} app(s) failed the release gate", flush=True)
        return 1
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate example apps for release readiness."
    )
    parser.add_argument(
        "--app-dir",
        type=Path,
        default=DEFAULT_APP_DIR,
        help="Directory containing example apps.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    return validate_app_candidates(discover_app_candidates(args.app_dir))


if __name__ == "__main__":
    raise SystemExit(main())
