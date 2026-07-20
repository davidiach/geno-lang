"""Focused contract tests for target-aware backend validation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from geno.api import check, check_path
from geno.target_profile import (
    TargetProfile,
    resolve_compilation_profiles,
)

UNSAFE_JS_INT = """
func main() -> Int
  return 9007199254740992
end func
"""

JS_RESERVED_FIELD = """
type Box = Box(constructor: Int)
func main() -> Int
  return 0
end func
"""

PYTHON_RESERVED_FIELD = """
type Box = Box(class: Int)
func main() -> Int
  return 0
end func
"""

RESERVED_RUNTIME_NAME = """
func _safe_div(x: Int, y: Int) -> Int
  example (4, 2) -> 2
  return x / y
end func

func main() -> Int
  return 0
end func
"""

HOST_INTRINSIC_CONSTRUCTOR = "type Severity = Error | Warning\n"


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "geno", *args],
        capture_output=True,
        text=True,
        timeout=20,
    )


def _write_project(
    root: Path,
    source: str,
    *,
    targets: tuple[str, ...] = (),
) -> Path:
    target_line = (
        "targets = [" + ", ".join(f'"{target}"' for target in targets) + "]\n"
        if targets
        else ""
    )
    (root / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main"]\n' + target_line,
        encoding="utf-8",
    )
    source_path = root / "Main.geno"
    source_path.write_text(source, encoding="utf-8")
    return source_path


@pytest.mark.parametrize(
    ("target", "runtime", "backend"),
    [
        ("python-cli", "python", "python"),
        ("python-hosted", "python", "python"),
        ("node-cli", "node", "javascript"),
        ("browser", "browser", "javascript"),
    ],
)
def test_target_profile_declares_canonical_backend(target, runtime, backend):
    profile = TargetProfile.load(target)

    assert profile.runtime == runtime
    assert profile.backend_kind == backend


@pytest.mark.parametrize("target", ["node-cli", "browser"])
def test_api_target_check_rejects_unsafe_js_integer(target):
    result = check(UNSAFE_JS_INT, target=target)

    assert not result.ok
    assert any("safe integer range" in diag.message for diag in result.diagnostics)


def test_api_target_check_rejects_unsafe_js_pattern_integer():
    source = """
func main() -> Int
  let x: Int = 0
  match x with
    | 9007199254740992 -> return 1
    | _ -> return 0
  end match
end func
"""

    result = check(source, target="node-cli")

    assert not result.ok
    assert any("safe integer range" in diag.message for diag in result.diagnostics)


@pytest.mark.parametrize(
    ("source", "target", "message"),
    [
        (JS_RESERVED_FIELD, "node-cli", "constructor"),
        (JS_RESERVED_FIELD, "browser", "constructor"),
        (PYTHON_RESERVED_FIELD, "python-cli", "cannot be represented safely"),
        (PYTHON_RESERVED_FIELD, "python-hosted", "cannot be represented safely"),
        (RESERVED_RUNTIME_NAME, "python-cli", "reserved runtime name"),
        (RESERVED_RUNTIME_NAME, "node-cli", "reserved runtime name"),
    ],
)
def test_api_target_check_reuses_backend_validation(source, target, message):
    result = check(source, target=target)

    assert not result.ok
    assert any(message in diag.message for diag in result.diagnostics)


def test_targetless_api_check_remains_permissive_for_backend_constraints():
    assert check(UNSAFE_JS_INT).ok
    assert check(JS_RESERVED_FIELD).ok
    assert check(PYTHON_RESERVED_FIELD).ok
    assert check(RESERVED_RUNTIME_NAME).ok


def test_explicit_check_target_still_validates_manifest_spelling(tmp_path):
    source_path = _write_project(tmp_path, "func main() -> Int\n  return 0\nend func\n")
    manifest = tmp_path / "geno.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + 'targets = ["browzer"]\n',
        encoding="utf-8",
    )

    cli_result = _run_cli("check", str(source_path), "--target", "python-cli")
    api_result = check_path(str(source_path), target="python-cli")

    assert cli_result.returncode != 0
    assert "Unknown target 'browzer'" in cli_result.stderr
    assert "Traceback" not in cli_result.stderr
    assert not api_result.ok
    assert any("Unknown target 'browzer'" in d.message for d in api_result.diagnostics)


def test_cli_check_reports_backend_error_without_traceback(tmp_path):
    source_path = _write_project(tmp_path, UNSAFE_JS_INT)

    result = _run_cli("check", str(source_path), "--target", "node-cli")

    assert result.returncode != 0
    assert "Target Error (target: node-cli)" in result.stderr
    assert "safe integer range" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("source", "backend", "message", "suffix"),
    [
        (UNSAFE_JS_INT, "js", "safe integer range", ".js"),
        (PYTHON_RESERVED_FIELD, "python", "cannot be represented safely", ".py"),
    ],
)
def test_cli_compile_backend_error_is_clean_and_writes_no_output(
    tmp_path, source, backend, message, suffix
):
    source_path = _write_project(tmp_path, source)
    output = tmp_path / f"app{suffix}"

    result = _run_cli(
        "compile",
        str(source_path),
        "--target",
        backend,
        "-o",
        str(output),
    )

    assert result.returncode != 0
    assert "Compile Error:" in result.stderr
    assert message in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_compile_uses_compatible_manifest_profile(tmp_path):
    source = """
func main() -> Result[ProcessResult, String]
  return exec("echo ok")
end func
"""
    source_path = _write_project(
        tmp_path,
        source,
        targets=("python-cli", "python-hosted"),
    )
    output = tmp_path / "app.py"

    result = _run_cli("compile", str(source_path), "-o", str(output))

    assert result.returncode != 0
    assert "exec" in result.stderr
    assert "python-hosted" in result.stderr
    assert not output.exists()


def test_compile_profile_explicitly_overrides_valid_manifest(tmp_path):
    source = """
func main() -> Result[ProcessResult, String]
  return exec("echo ok")
end func
"""
    source_path = _write_project(tmp_path, source, targets=("python-hosted",))
    output = tmp_path / "app.py"

    default_result = _run_cli("compile", str(source_path), "-o", str(output))
    override_result = _run_cli(
        "compile",
        str(source_path),
        "--profile",
        "python-cli",
        "-o",
        str(output),
    )

    assert default_result.returncode != 0
    assert "python-hosted" in default_result.stderr
    assert override_result.returncode == 0, override_result.stderr
    assert output.exists()


def test_compile_uses_legacy_profile_without_compatible_manifest_target(tmp_path):
    source_path = _write_project(
        tmp_path,
        "func main() -> Int\n  return 0\nend func\n",
        targets=("node-cli",),
    )
    output = tmp_path / "app.py"

    result = _run_cli("compile", str(source_path), "-o", str(output))

    assert result.returncode == 0, result.stderr
    assert output.exists()


def test_raw_js_compile_routes_browser_profile_to_build(tmp_path):
    source_path = _write_project(
        tmp_path,
        "func main() -> Int\n  return 0\nend func\n",
        targets=("browser",),
    )

    result = _run_cli(
        "compile",
        str(source_path),
        "--target",
        "js",
        "-o",
        str(tmp_path / "app.js"),
    )

    assert result.returncode != 0
    assert "geno build" in result.stderr
    assert "Traceback" not in result.stderr


def test_browser_build_error_is_clean_and_creates_no_directory(tmp_path):
    source_path = _write_project(tmp_path, JS_RESERVED_FIELD, targets=("browser",))
    output = tmp_path / "dist"

    result = _run_cli("build", str(source_path), "-o", str(output))

    assert result.returncode != 0
    assert "Build Error:" in result.stderr
    assert "constructor" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_resolve_compilation_profiles_preserves_legacy_defaults(tmp_path):
    assert [
        profile.target for profile in resolve_compilation_profiles(tmp_path, "python")
    ] == ["python-cli"]
    assert [
        profile.target for profile in resolve_compilation_profiles(tmp_path, "js")
    ] == ["node-cli"]


def test_single_module_check_matches_standalone_compiler(tmp_path):
    source = HOST_INTRINSIC_CONSTRUCTOR + "\nfunc main() -> Int\n  return 0\nend func\n"
    source_path = _write_project(tmp_path, source, targets=("python-cli",))
    output = tmp_path / "app.py"

    check_result = _run_cli("check", str(source_path))
    compile_result = _run_cli("compile", str(source_path), "-o", str(output))

    assert check_result.returncode != 0
    assert compile_result.returncode != 0
    assert "Warning" in check_result.stderr
    assert "Warning" in compile_result.stderr
    assert not output.exists()


def test_module_api_check_uses_project_lowering_rules():
    main_source = """
import Messages
func main() -> Int
  return 0
end func
"""

    result = check(
        main_source,
        modules={"Messages": HOST_INTRINSIC_CONSTRUCTOR},
        target="python-cli",
    )

    assert result.ok, result.diagnostics


@pytest.mark.parametrize("overlays", [None, {}])
def test_project_check_validates_backend_module_namespace(tmp_path, overlays):
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\n'
        'files = ["Main", "Constructor"]\n'
        'targets = ["python-cli"]\n',
        encoding="utf-8",
    )
    (tmp_path / "Main.geno").write_text(
        "import Constructor\n"
        "func main() -> Int\n"
        "  return Constructor.value()\n"
        "end func\n",
        encoding="utf-8",
    )
    (tmp_path / "Constructor.geno").write_text(
        "func value() -> Int\n  example () -> 0\n  return 0\nend func\n",
        encoding="utf-8",
    )

    api_result = check_path(str(tmp_path), modules=overlays)
    cli_result = _run_cli("check", str(tmp_path))

    assert not api_result.ok
    assert any(
        "reserved runtime module name" in d.message for d in api_result.diagnostics
    )
    assert cli_result.returncode != 0
    assert "reserved runtime module name" in cli_result.stderr
    assert "Traceback" not in cli_result.stderr


def test_overlay_project_check_validates_backend_module_namespace(tmp_path):
    source_path = _write_project(
        tmp_path,
        "func main() -> Int\n  return 0\nend func\n",
        targets=("python-cli",),
    )
    overlay = "func value() -> Int\n  example () -> 0\n  return 0\nend func\n"

    result = check_path(
        str(source_path),
        modules={"Constructor": overlay},
    )

    assert not result.ok
    assert any("reserved runtime module name" in d.message for d in result.diagnostics)


def test_direct_browser_api_check_uses_project_lowering():
    source = """
type ThingType = Thing(value: Int)
trait Runnable
  func main(self: Self) -> Int
end trait
impl Runnable for ThingType
  func main(self: ThingType) -> Int
    return 1
  end func
end impl
func main() -> Int
  return 7
end func
"""

    browser_result = check(source, target="browser")
    node_result = check(source, target="node-cli")

    assert browser_result.ok, browser_result.diagnostics
    assert not node_result.ok
    assert any(
        "conflicts with a trait dispatcher" in d.message
        for d in node_result.diagnostics
    )


def test_direct_browser_api_check_uses_filename_module_name():
    source = "func main() -> Int\n  return 0\nend func\n"

    result = check(
        source,
        filename="Some.geno",
        target="browser",
    )

    assert not result.ok
    assert any("reserved runtime module name" in d.message for d in result.diagnostics)
