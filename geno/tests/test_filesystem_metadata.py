"""Portable filesystem metadata and canonicalization contract tests."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from geno._serve import install_fs_callbacks
from geno.api import RunConfig, run
from geno.builtin_registry import source_builtin_specs
from geno.compiler import compile_to_python
from geno.interpreter import Interpreter
from geno.js_compiler import compile_to_js
from geno.lexer import Lexer
from geno.parser import Parser
from geno.target_profile import TargetProfile
from geno.tests._script_runner import run_node_code, run_python_code
from geno.typechecker import TypeChecker
from geno.values import ConstructorValue


def _typecheck(source: str, *, target: str | None = None) -> None:
    program = Parser(Lexer(source, "<test>").tokenize()).parse_program()
    profile = TargetProfile.load(target) if target is not None else None
    TypeChecker(target_profile=profile).check_program(program)


def _callback(interpreter: Interpreter, name: str) -> Any:
    return interpreter.global_env.bindings[name].func


def _ok_value(value: Any) -> ConstructorValue:
    return ConstructorValue("Ok", {"value": value})


def _metadata(kind: str, size: int, modified_ms: int) -> ConstructorValue:
    return ConstructorValue(
        "FileMetadata",
        {
            "kind": ConstructorValue(kind, {}),
            "size": size,
            "modified_ms": modified_ms,
        },
    )


def _unwrap_ok(result: ConstructorValue) -> Any:
    assert result.constructor == "Ok", result
    return result.fields["value"]


def _kind_name(metadata: ConstructorValue) -> str:
    kind = metadata.fields["kind"]
    assert isinstance(kind, ConstructorValue)
    return str(kind.constructor)


def _normalized_realpath(path: Path) -> str:
    return str(os.path.realpath(path)).replace("\\", "/")


def test_metadata_types_and_effects_are_registered() -> None:
    _typecheck(
        """
func main() -> FileMetadata
    return FileMetadata(FileKindFile, 4, 1700000000123)
end func
"""
    )
    _typecheck(
        """
func main() -> Result[FileMetadata, String]
    return fs_symlink_metadata("entry")
end func
"""
    )

    specs = source_builtin_specs()
    for name in ("fs_metadata", "fs_symlink_metadata", "fs_canonicalize"):
        assert specs[name].capability == "fs"
        signature = specs[name].signature
        assert signature is not None
        assert signature.effects == frozenset({"fs"})


def test_browser_target_rejects_metadata_builtins() -> None:
    browser = TargetProfile.load("browser")
    for name in ("fs_metadata", "fs_symlink_metadata", "fs_canonicalize"):
        assert not browser.is_available(name)


def test_embedding_requires_explicit_callback_and_preserves_host_process() -> None:
    source = """
func main() -> Int
    match fs_metadata("virtual.txt") with
        | Ok(metadata) -> return metadata.size
        | Err(_) -> return 0
    end match
end func
"""

    missing = run(source, config=RunConfig(capabilities={"fs"}))
    assert missing.ok is False
    assert any(
        "Host callback not provided" in item.message for item in missing.diagnostics
    )

    called: list[str] = []

    def callback(path: str) -> ConstructorValue:
        called.append(path)
        return _ok_value(_metadata("FileKindFile", 7, 11))

    denied = run(
        source,
        config=RunConfig(
            capabilities=set(),
            host_callbacks={"fs_metadata": callback},
        ),
    )
    assert denied.ok is False
    assert called == []
    assert any("Capability denied" in item.message for item in denied.diagnostics)

    allowed = run(
        source,
        config=RunConfig(
            capabilities={"fs"},
            host_callbacks={"fs_metadata": callback},
        ),
    )
    assert allowed.ok is True
    assert allowed.value == 7
    assert called == ["virtual.txt"]


def test_scoped_callbacks_report_file_directory_and_canonical_path(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "report.txt"
    file_path.write_bytes(b"geno")
    modified_ms = 1_700_000_000_123
    os.utime(file_path, ns=(modified_ms * 1_000_000, modified_ms * 1_000_000))

    interpreter = Interpreter()
    install_fs_callbacks(interpreter, roots=[str(tmp_path)])

    file_metadata = _unwrap_ok(_callback(interpreter, "fs_metadata")("report.txt"))
    assert _kind_name(file_metadata) == "FileKindFile"
    assert file_metadata.fields["size"] == 4
    assert file_metadata.fields["modified_ms"] == modified_ms

    dir_metadata = _unwrap_ok(_callback(interpreter, "fs_metadata")("."))
    assert _kind_name(dir_metadata) == "FileKindDirectory"

    canonical = _unwrap_ok(_callback(interpreter, "fs_canonicalize")("report.txt"))
    assert canonical == _normalized_realpath(file_path)
    assert "\\" not in canonical

    missing_metadata = _callback(interpreter, "fs_metadata")("missing.txt")
    missing_canonical = _callback(interpreter, "fs_canonicalize")("missing.txt")
    assert missing_metadata.constructor == "Err"
    assert missing_canonical.constructor == "Err"


def test_scoped_callbacks_distinguish_links_and_reject_target_escape(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = root / "target.txt"
    target.write_text("inside", encoding="utf-8")
    (root / "secret.txt").write_text("decoy", encoding="utf-8")
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    nested = outside / "nested"
    nested.mkdir()

    link = root / "link.txt"
    dangling = root / "dangling.txt"
    escape = root / "escape.txt"
    jump = root / "jump"
    try:
        link.symlink_to(target)
        dangling.symlink_to(root / "missing-target.txt")
        escape.symlink_to(outside / "secret.txt")
        jump.symlink_to(nested, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")

    interpreter = Interpreter()
    install_fs_callbacks(interpreter, roots=[str(root)])

    followed = _unwrap_ok(_callback(interpreter, "fs_metadata")("link.txt"))
    link_info = _unwrap_ok(_callback(interpreter, "fs_symlink_metadata")("link.txt"))
    dangling_info = _unwrap_ok(
        _callback(interpreter, "fs_symlink_metadata")("dangling.txt")
    )
    assert _kind_name(followed) == "FileKindFile"
    assert _kind_name(link_info) == "FileKindSymlink"
    assert _kind_name(dangling_info) == "FileKindSymlink"
    assert (
        _callback(interpreter, "fs_canonicalize")("dangling.txt").constructor == "Err"
    )

    assert _callback(interpreter, "fs_metadata")("escape.txt").constructor == "Err"
    assert _callback(interpreter, "fs_canonicalize")("escape.txt").constructor == "Err"
    escape_info = _unwrap_ok(
        _callback(interpreter, "fs_symlink_metadata")("escape.txt")
    )
    assert _kind_name(escape_info) == "FileKindSymlink"
    assert (
        _callback(interpreter, "fs_symlink_metadata")("jump/../secret.txt").constructor
        == "Err"
    )


def test_compiled_python_and_node_metadata_are_portable(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("Node.js not available")

    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(b"geno")
    modified_ms = 1_700_000_000_123
    os.utime(file_path, ns=(modified_ms * 1_000_000, modified_ms * 1_000_000))

    source = """
@untested("filesystem fixture")
func render_metadata(path: String) -> String
    match fs_metadata(path) with
        | Err(message) -> return "error:" + message
        | Ok(metadata) ->
            match metadata.kind with
                | FileKindFile -> return "file:" + to_string(metadata.size) + ":" + to_string(metadata.modified_ms)
                | FileKindDirectory -> return "directory"
                | FileKindSymlink -> return "symlink"
                | FileKindOther -> return "other"
            end match
    end match
end func

func main() -> Unit
    print(render_metadata("sample.txt"))
    print(render_metadata("."))
    print(path_is_absolute("C:/portable"))
    match fs_canonicalize("sample.txt") with
        | Ok(path) ->
            print(path_is_absolute(path))
            print(path_filename(path))
        | Err(message) -> print("error:" + message)
    end match
    match fs_canonicalize("missing.txt") with
        | Ok(_) -> print("unexpected")
        | Err(_) -> print("missing")
    end match
    return ()
end func
"""

    expected = f"file:4:{modified_ms}\ndirectory\ntrue\ntrue\nsample.txt\nmissing\n"

    python_result = run_python_code(
        compile_to_python(source),
        python_executable=sys.executable,
        args=("--cap", "fs,print"),
        cwd=tmp_path,
    )
    node_result = run_node_code(
        compile_to_js(source),
        args=("--cap", "fs,print"),
        cwd=tmp_path,
    )
    assert python_result.returncode == 0, python_result.stderr
    assert node_result.returncode == 0, node_result.stderr
    assert python_result.stderr == ""
    assert node_result.stderr == ""
    assert python_result.stdout == expected
    assert node_result.stdout == expected


def test_compiled_backends_distinguish_followed_and_unfollowed_links(
    tmp_path: Path,
) -> None:
    if shutil.which("node") is None:
        pytest.skip("Node.js not available")

    target = tmp_path / "target.txt"
    target.write_text("inside", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")

    source = """
@untested("filesystem fixture")
func metadata_kind(result: Result[FileMetadata, String]) -> String
    match result with
        | Err(_) -> return "error"
        | Ok(metadata) ->
            match metadata.kind with
                | FileKindFile -> return "file"
                | FileKindDirectory -> return "directory"
                | FileKindSymlink -> return "symlink"
                | FileKindOther -> return "other"
            end match
    end match
end func

func main() -> Unit
    print(metadata_kind(fs_metadata("link.txt")))
    print(metadata_kind(fs_symlink_metadata("link.txt")))
    match fs_canonicalize("link.txt") with
        | Ok(path) -> print(path_filename(path))
        | Err(_) -> print("error")
    end match
    return ()
end func
"""
    expected = "file\nsymlink\ntarget.txt\n"
    python_result = run_python_code(
        compile_to_python(source),
        python_executable=sys.executable,
        args=("--cap", "fs,print"),
        cwd=tmp_path,
    )
    node_result = run_node_code(
        compile_to_js(source),
        args=("--cap", "fs,print"),
        cwd=tmp_path,
    )
    assert python_result.returncode == 0, python_result.stderr
    assert node_result.returncode == 0, node_result.stderr
    assert python_result.stdout == expected
    assert node_result.stdout == expected


def test_compiled_python_rejects_followed_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("secret", encoding="utf-8")
    (root / "secret.txt").write_text("decoy", encoding="utf-8")
    nested = outside / "nested"
    nested.mkdir()
    link = root / "escape.txt"
    jump = root / "jump"
    try:
        link.symlink_to(target)
        jump.symlink_to(nested, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")

    source = """
func main() -> Unit
    match fs_metadata("escape.txt") with
        | Ok(_) -> print("followed")
        | Err(_) -> print("blocked")
    end match
    match fs_symlink_metadata("escape.txt") with
        | Ok(metadata) ->
            match metadata.kind with
                | FileKindSymlink -> print("link")
                | _ -> print("unexpected")
            end match
        | Err(_) -> print("missing")
    end match
    match fs_canonicalize("escape.txt") with
        | Ok(_) -> print("canonicalized")
        | Err(_) -> print("blocked")
    end match
    match fs_symlink_metadata("jump/../secret.txt") with
        | Ok(_) -> print("collapsed")
        | Err(_) -> print("blocked")
    end match
    return ()
end func
"""
    result = run_python_code(
        compile_to_python(source),
        python_executable=sys.executable,
        args=("--cap", "fs,print"),
        cwd=root,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "blocked\nlink\nblocked\nblocked\n"


@pytest.mark.parametrize(
    "compiler,runner", [(compile_to_python, "python"), (compile_to_js, "node")]
)
def test_compiled_metadata_capability_denial(
    tmp_path: Path,
    compiler: Any,
    runner: str,
) -> None:
    if runner == "node" and shutil.which("node") is None:
        pytest.skip("Node.js not available")

    source = """
func main() -> Result[FileMetadata, String]
    return fs_metadata("entry")
end func
"""
    code = compiler(source)
    if runner == "python":
        result = run_python_code(
            code,
            python_executable=sys.executable,
            args=("--cap", "print"),
            cwd=tmp_path,
        )
    else:
        result = run_node_code(code, args=("--cap", "print"), cwd=tmp_path)

    assert result.returncode != 0
    assert "Capability denied" in result.stderr
