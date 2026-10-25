"""Explicit filesystem policy must take precedence over automatic CLI grants."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def fs_layout(tmp_path: Path) -> dict[str, Path]:
    directories = {
        name: tmp_path / name for name in ("cwd", "source", "allowed", "external")
    }
    for directory in directories.values():
        directory.mkdir()
        (directory / "marker.txt").write_text("ORIGINAL", encoding="utf-8")
    return directories


def _run_fs_program(
    layout: dict[str, Path],
    operation: str,
    path: str,
    policy: dict[str, str],
    *,
    program_args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    argument = json.dumps(path.replace("\\", "/"))
    if operation == "read":
        call = f"fs_read_text({argument})"
        success = "value"
    else:
        call = f'fs_write_text({argument}, "CHANGED")'
        success = '"WROTE"'
    source = layout["source"] / "App.geno"
    source.write_text(
        "func main() -> String\n"
        f"    match {call} with\n"
        f"        | Ok(value) -> return {success}\n"
        '        | Err(message) -> return "DENIED: " + message\n'
        "    end match\n"
        "end func\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    for name in ("GENO_FS_ROOTS", "GENO_FS_ALLOW_ABSOLUTE", "GENO_FS_READ_ONLY"):
        env.pop(name, None)
    env.update(policy)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            str(source),
            "--unsafe",
            "--cap",
            "fs",
            "--",
            *program_args,
        ],
        cwd=layout["cwd"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.parametrize("operation", ["read", "write"])
@pytest.mark.parametrize("absolute", [False, True])
def test_explicit_roots_reject_access_outside_policy_even_with_path_argument(
    fs_layout, operation, absolute
):
    target = fs_layout["cwd"] / "marker.txt"
    path = str(target) if absolute else "../cwd/marker.txt"
    result = _run_fs_program(
        fs_layout,
        operation,
        path,
        {"GENO_FS_ROOTS": str(fs_layout["allowed"]), "GENO_FS_ALLOW_ABSOLUTE": "1"},
        program_args=(str(target),),
    )

    assert result.returncode == 0, result.stderr
    assert "DENIED: " in result.stdout
    assert "escapes configured filesystem roots" in result.stdout
    assert target.read_text(encoding="utf-8") == "ORIGINAL"


@pytest.mark.parametrize("operation", ["read", "write"])
def test_explicit_roots_allow_relative_access_inside_policy(fs_layout, operation):
    result = _run_fs_program(
        fs_layout,
        operation,
        "marker.txt",
        {"GENO_FS_ROOTS": str(fs_layout["allowed"])},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ("=> ORIGINAL\n" if operation == "read" else "=> WROTE\n")
    expected = "ORIGINAL" if operation == "read" else "CHANGED"
    assert (fs_layout["allowed"] / "marker.txt").read_text(encoding="utf-8") == expected
    assert (fs_layout["cwd"] / "marker.txt").read_text(encoding="utf-8") == "ORIGINAL"


@pytest.mark.parametrize("absolute_flag", [None, "0", "false", "", "1", "true"])
def test_explicit_roots_apply_absolute_path_policy(fs_layout, absolute_flag):
    target = fs_layout["allowed"] / "marker.txt"
    policy = {"GENO_FS_ROOTS": str(fs_layout["allowed"])}
    if absolute_flag is not None:
        policy["GENO_FS_ALLOW_ABSOLUTE"] = absolute_flag
    result = _run_fs_program(fs_layout, "read", str(target), policy)

    assert result.returncode == 0, result.stderr
    if absolute_flag in {"1", "true"}:
        assert result.stdout == "=> ORIGINAL\n"
    else:
        assert "DENIED: fs_read_text: absolute paths are not allowed" in result.stdout


@pytest.mark.parametrize("root_list", ["", os.pathsep, os.pathsep * 2])
def test_empty_explicit_roots_fail_closed(fs_layout, root_list):
    target = fs_layout["cwd"] / "marker.txt"
    result = _run_fs_program(
        fs_layout, "write", str(target), {"GENO_FS_ROOTS": root_list}
    )

    assert result.returncode == 1
    assert "GENO_FS_ROOTS must contain at least one filesystem root" in result.stderr
    assert "Traceback" not in result.stderr
    assert target.read_text(encoding="utf-8") == "ORIGINAL"


@pytest.mark.parametrize("location", ["cwd", "source", "external"])
def test_default_cli_preserves_automatic_input_scope(fs_layout, location):
    target = fs_layout[location] / "marker.txt"
    args = (str(target),) if location == "external" else ()
    result = _run_fs_program(fs_layout, "read", str(target), {}, program_args=args)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "=> ORIGINAL\n"


def test_explicit_false_absolute_flag_applies_to_automatic_roots(fs_layout):
    target = fs_layout["cwd"] / "marker.txt"
    result = _run_fs_program(
        fs_layout, "read", str(target), {"GENO_FS_ALLOW_ABSOLUTE": "0"}
    )

    assert result.returncode == 0, result.stderr
    assert "DENIED: fs_read_text: absolute paths are not allowed" in result.stdout
