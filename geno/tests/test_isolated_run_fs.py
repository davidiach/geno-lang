"""The ``fs`` grant in the process-isolated ``geno run`` lane (proposal 0013).

Milestone 4: ``--cap fs`` reaches the host only through the worker's trusted
filesystem facade (``geno.isolated_host.IsolatedFs``). The facade applies the
``--unsafe`` roots, symlink and absolute-path rules (the same ``_serve``
validators), the parent's ``GENO_FS_*`` policy (read only for an ``fs``
grant) and two portable write limits: 64 MiB per file and 64 MiB per run by
default, shared by examples and ``main``, reserved before the destination is
opened. Quota rejections are ``Err``; read-only writes and ``fs_exists``
escapes still raise.

Runs here go through real ``geno run`` subprocesses: only a real worker
applies its restricted builtins to the runtime prelude. This file is part of
the Windows smoke job; only the ``RLIMIT_FSIZE`` probe is POSIX-only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from geno.capabilities import ISOLATED_RUN_FS_WRITE_LIMIT_BYTES

ESCAPES = "path escapes configured filesystem roots"

# Variables a run in this file must not inherit from the test environment.
_POLICY_VARIABLES = (
    "GENO_FS_ROOTS",
    "GENO_FS_ALLOW_ABSOLUTE",
    "GENO_FS_READ_ONLY",
    "GENO_HTTP_ALLOW_PRIVATE",
    "GENO_CLI_ARGS",
)


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _POLICY_VARIABLES}
    env.update(extra)
    return env


def _geno(
    *args: str, cwd: Path, env: dict[str, str] | None = None, timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "geno", "run", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=_env() if env is None else env,
    )


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _literal(text: str) -> str:
    """A Geno string literal for a host path (portable separators)."""
    return _text(text.replace("\\", "/"))


def _text(text: str) -> str:
    """A Geno string literal for *text* (no non-ASCII escapes)."""
    return json.dumps(text, ensure_ascii=False)


def _write_attempts(*attempts: tuple[str, str]) -> str:
    """A program that writes each ``(path, content)`` and prints the outcome."""
    lines = ["func main() -> Unit"]
    for path, content in attempts:
        lines += [
            f"    match fs_write_text({_literal(path)}, {_text(content)}) with",
            f'        | Ok(_) -> print("ok {path}")',
            '        | Err(error) -> print("Err " + error)',
            "    end match",
        ]
    lines.append("end func")
    return "\n".join(lines) + "\n"


def _isolated_and_unsafe(
    tmp_path: Path,
    source: str,
    *flags: str,
    setup: Any = None,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    """Run *source* in both lanes from the same directory, set up afresh each time.

    One directory keeps host paths in error texts identical across lanes.
    """
    results = []
    for lane_flags in ((), ("--unsafe",)):
        work = tmp_path / "work"
        if work.exists():
            _remove_tree(work)
        work.mkdir()
        if setup is not None:
            setup(work)
        _write(work, "main.geno", source)
        results.append(_geno(*lane_flags, *flags, "main.geno", cwd=work, env=env))
    isolated, unsafe = results
    return isolated, unsafe


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path)


# ---------------------------------------------------------------------------
# Parity with --unsafe --cap fs

READ_WRITE_LIST = """func main() -> Unit
    match fs_write_text("out/new.txt", "fresh") with
        | Ok(_) -> print("wrote")
        | Err(error) -> print("write Err " + error)
    end match
    match fs_read_text("out/new.txt") with
        | Ok(text) -> print("read " + text)
        | Err(error) -> print("read Err " + error)
    end match
    match fs_read_text("missing.txt") with
        | Ok(text) -> print("read " + text)
        | Err(error) -> print("missing Err " + error)
    end match
    match fs_write_text("nowhere/new.txt", "x") with
        | Ok(_) -> print("wrote nowhere")
        | Err(error) -> print("nowhere Err " + error)
    end match
    match fs_list_dir("out") with
        | Ok(names) -> print("list " + join(names, ","))
        | Err(error) -> print("list Err " + error)
    end match
    match fs_list_dir("absent") with
        | Ok(names) -> print("list " + join(names, ","))
        | Err(error) -> print("absent Err " + error)
    end match
    print("exists " + to_string(fs_exists("out/new.txt")))
    print("exists " + to_string(fs_exists("absent")))
end func
"""


def test_read_write_and_list_match_unsafe(tmp_path: Path) -> None:
    def setup(work: Path) -> None:
        _write(work, "out/old.txt", "old")

    isolated, unsafe = _isolated_and_unsafe(
        tmp_path, READ_WRITE_LIST, "--cap", "fs,print", setup=setup
    )

    assert isolated.returncode == 0, isolated.stderr
    assert isolated.stderr == ""
    assert (isolated.stdout, isolated.returncode) == (unsafe.stdout, unsafe.returncode)
    lines = isolated.stdout.splitlines()
    assert lines[:2] == ["wrote", "read fresh"]
    assert lines[2].startswith("missing Err ")
    assert lines[3].startswith("nowhere Err ")
    assert lines[4:] == [
        "list new.txt,old.txt",
        lines[5],
        "exists true",
        "exists false",
    ]
    assert lines[5].startswith("absent Err ")
    assert (tmp_path / "work" / "out" / "new.txt").read_text(encoding="utf-8") == (
        "fresh"
    )


METADATA_HELPERS = """@untested("filesystem fixture")
func kind_name(kind: FileKind) -> String
    match kind with
        | FileKindFile -> return "file"
        | FileKindDirectory -> return "directory"
        | FileKindSymlink -> return "symlink"
        | FileKindOther -> return "other"
    end match
end func

@untested("filesystem fixture")
func show(label: String, result: Result[FileMetadata, String]) -> String
    match result with
        | Ok(m) -> return label + " " + kind_name(m.kind) + " " + to_string(m.size)
        | Err(error) -> return label + " Err " + error
    end match
end func

@untested("filesystem fixture")
func canonical(label: String, path: String) -> String
    match fs_canonicalize(path) with
        | Ok(resolved) -> return label + " " + resolved
        | Err(error) -> return label + " Err " + error
    end match
end func
"""

MODIFIED_NS = 1_700_000_000_123_000_000


def test_metadata_of_every_kind_and_canonicalize_match_unsafe(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    calls = [
        'print("modified " + to_string(fs_metadata("data.txt").unwrap().modified_ms))',
        'print(show("file", fs_metadata("data.txt")))',
        'print(show("file-l", fs_symlink_metadata("data.txt")))',
        'print(show("dir", fs_metadata("sub")))',
        'print(show("missing", fs_metadata("missing.txt")))',
        'print(show("escape", fs_metadata("../outside.txt")))',
        'print(canonical("canon", "sub/../data.txt"))',
        'print(canonical("canon-missing", "missing.txt"))',
    ]
    expected_kinds = {"modified": "1700000000123", "file": "file 4", "file-l": "file 4"}
    links = False
    fifo = False

    def setup(work: Path) -> None:
        nonlocal links, fifo
        data = _write(work, "data.txt", "geno")
        os.utime(data, ns=(MODIFIED_NS, MODIFIED_NS))
        (work / "sub").mkdir()
        try:
            (work / "link.txt").symlink_to(data)
            (work / "escape.txt").symlink_to(outside)
            links = True
        except (OSError, NotImplementedError):
            links = False
        if hasattr(os, "mkfifo"):
            os.mkfifo(work / "pipe")
            fifo = True

    # Decide which entries exist before writing the program.
    probe = tmp_path / "probe"
    probe.mkdir()
    setup(probe)
    if links:
        calls += [
            'print(show("link", fs_metadata("link.txt")))',
            'print(show("link-l", fs_symlink_metadata("link.txt")))',
            'print(show("escape-link", fs_metadata("escape.txt")))',
            'print(show("escape-link-l", fs_symlink_metadata("escape.txt")))',
            'print(canonical("canon-link", "link.txt"))',
            'print(canonical("canon-escape", "escape.txt"))',
        ]
    if fifo:
        calls.append('print(show("pipe", fs_metadata("pipe")))')
    source = (
        METADATA_HELPERS
        + "\nfunc main() -> Unit\n"
        + "".join(f"    {call}\n" for call in calls)
        + "end func\n"
    )

    isolated, unsafe = _isolated_and_unsafe(
        tmp_path, source, "--cap", "fs,print", setup=setup
    )

    assert isolated.returncode == 0, isolated.stderr
    assert isolated.stderr == ""
    assert isolated.stdout == unsafe.stdout
    shown = dict(line.split(" ", 1) for line in isolated.stdout.splitlines())
    for label, prefix in expected_kinds.items():
        assert shown[label].startswith(prefix)
    assert shown["dir"].startswith("directory ")
    assert shown["missing"].startswith("Err ")
    assert shown["escape"] == f"Err fs_metadata: {ESCAPES}"
    work = tmp_path / "work"
    assert shown["canon"] == os.path.realpath(work / "data.txt").replace("\\", "/")
    assert shown["canon-missing"] == (
        "Err fs_canonicalize: path does not exist: missing.txt"
    )
    if links:
        assert shown["link"] == "file 4"
        assert shown["link-l"].startswith("symlink ")
        assert shown["escape-link"] == f"Err fs_metadata: {ESCAPES}"
        assert shown["escape-link-l"].startswith("symlink ")
        assert shown["canon-link"] == shown["canon"]
        assert shown["canon-escape"] == f"Err fs_canonicalize: {ESCAPES}"
    if fifo:
        assert shown["pipe"].startswith("other ")


# ---------------------------------------------------------------------------
# Escapes and absolute paths

ESCAPE_PROGRAM = """func main() -> Unit
    match fs_read_text("../outside/secret.txt") with
        | Ok(text) -> print("ESCAPED " + text)
        | Err(error) -> print(error)
    end match
    match fs_write_text("../outside/secret.txt", "CHANGED") with
        | Ok(_) -> print("ESCAPED write")
        | Err(error) -> print(error)
    end match
    match fs_list_dir("..") with
        | Ok(names) -> print("ESCAPED " + join(names, ","))
        | Err(error) -> print(error)
    end match
    match fs_symlink_metadata("jump/../secret.txt") with
        | Ok(_) -> print("ESCAPED metadata")
        | Err(error) -> print(error)
    end match
    match fs_canonicalize("../outside") with
        | Ok(path) -> print("ESCAPED " + path)
        | Err(error) -> print(error)
    end match
    match fs_read_text("link/secret.txt") with
        | Ok(text) -> print("ESCAPED " + text)
        | Err(error) -> print(error)
    end match
end func
"""


def test_dot_dot_and_symlink_escapes_are_err(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    secret = _write(outside, "secret.txt", "secret")
    work = tmp_path / "work"
    work.mkdir()
    _write(work, "main.geno", ESCAPE_PROGRAM)
    links = True
    try:
        (work / "link").symlink_to(outside, target_is_directory=True)
        (work / "jump").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        links = False

    result = _geno("--cap", "fs,print", "main.geno", cwd=work)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    names = ["fs_read_text", "fs_write_text", "fs_list_dir"]
    assert lines[:3] == [f"{name}: {ESCAPES}" for name in names]
    assert lines[4] == f"fs_canonicalize: {ESCAPES}"
    if links:
        assert lines[3] == f"fs_symlink_metadata: {ESCAPES}"
        assert lines[5] == f"fs_read_text: {ESCAPES}"
    assert "ESCAPED" not in result.stdout
    assert secret.read_text(encoding="utf-8") == "secret"


def test_fs_exists_escape_raises(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "main.geno",
        'func main() -> Unit\n    print(to_string(fs_exists("../x")))\nend func\n',
    )

    result = _geno("--cap", "fs,print", "main.geno", cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert f"Runtime Error: fs_exists: {ESCAPES}" in result.stderr
    assert "Traceback" not in result.stderr


def test_absolute_paths_are_allowed_only_inside_the_roots(tmp_path: Path) -> None:
    work = tmp_path / "work"
    inside = _write(work, "inside.txt", "inside")
    outside = _write(tmp_path, "outside.txt", "outside")
    source = "\n".join(
        [
            "func main() -> Unit",
            f"    match fs_read_text({_literal(str(inside))}) with",
            '        | Ok(text) -> print("read " + text)',
            "        | Err(error) -> print(error)",
            "    end match",
            f"    match fs_read_text({_literal(str(outside))}) with",
            '        | Ok(text) -> print("ESCAPED " + text)',
            "        | Err(error) -> print(error)",
            "    end match",
            "end func",
            "",
        ]
    )
    _write(work, "main.geno", source)

    result = _geno("--cap", "fs,print", "main.geno", cwd=work)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"read inside\nfs_read_text: {ESCAPES}\n"


def test_empty_roots_with_fs_exit_before_any_worker_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    launched: list[Any] = []
    monkeypatch.setattr(
        ProcessSandbox, "_run_worker", lambda *args, **kwargs: launched.append(args)
    )
    monkeypatch.setenv("GENO_FS_ROOTS", os.pathsep)
    source = _write(
        tmp_path, "main.geno", 'func main() -> Unit\n    print("x")\nend func\n'
    )

    with pytest.raises(SystemExit) as excinfo:
        run_file(str(source), capabilities={"fs", "print"})

    assert excinfo.value.code == 1
    assert launched == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Error: GENO_FS_ROOTS must contain at least one filesystem root\n"
    )


def test_path_argument_authorizes_its_directory_without_env(tmp_path: Path) -> None:
    external = tmp_path / "external"
    data = _write(external, "input.csv", "a,b")
    work = tmp_path / "work"
    source = "\n".join(
        [
            "func main() -> Unit",
            f"    match fs_read_text({_literal(str(data))}) with",
            '        | Ok(text) -> print("read " + text)',
            "        | Err(error) -> print(error)",
            "    end match",
            '    print(join(cli_args(), ","))',
            "end func",
            "",
        ]
    )
    _write(work, "main.geno", source)

    with_arg = _geno("--cap", "fs,print", "main.geno", "--", str(data), cwd=work)
    without = _geno("--cap", "fs,print", "main.geno", cwd=work)

    # The argument is input scope for trusted setup, never program-visible.
    assert with_arg.returncode == 1
    assert with_arg.stdout == "read a,b\n"
    assert (
        "Capability denied: 'cli_args' requires the 'env' capability" in with_arg.stderr
    )
    assert without.returncode == 1
    assert without.stdout == f"fs_read_text: {ESCAPES}\n"


# ---------------------------------------------------------------------------
# Read-only policy


def test_read_only_policy_raises_and_leaves_the_file(tmp_path: Path) -> None:
    keep = _write(tmp_path, "keep.txt", "ORIGINAL")
    source = """func main() -> Unit
    print(fs_read_text("keep.txt").unwrap())
    match fs_write_text("keep.txt", "CHANGED") with
        | Ok(_) -> print("ESCAPED")
        | Err(error) -> print("Err " + error)
    end match
end func
"""
    _write(tmp_path, "main.geno", source)

    # No env grant: the parent's GENO_FS_READ_ONLY is trusted host policy.
    result = _geno(
        "--cap", "fs,print", "main.geno", cwd=tmp_path, env=_env(GENO_FS_READ_ONLY="1")
    )

    assert result.returncode == 1
    assert result.stdout == "ORIGINAL\n"
    assert "Runtime Error: fs_write_text: filesystem writes are not allowed" in (
        result.stderr
    )
    assert keep.read_text(encoding="utf-8") == "ORIGINAL"


# ---------------------------------------------------------------------------
# Write limits


def _run_writes(
    tmp_path: Path, *flags: str, attempts: tuple[tuple[str, str], ...]
) -> subprocess.CompletedProcess[str]:
    _write(tmp_path, "main.geno", _write_attempts(*attempts))
    return _geno("--cap", "fs,print", *flags, "main.geno", cwd=tmp_path)


def test_per_file_limit_at_the_boundary(tmp_path: Path) -> None:
    result = _run_writes(
        tmp_path,
        "--max-file-size-bytes",
        "4",
        attempts=(("a.txt", "1234"), ("b.txt", "12345")),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "ok a.txt\n"
        "Err fs_write_text: writing 5 bytes exceeds the per-file limit of 4 bytes "
        "(--max-file-size-bytes)\n"
    )
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "1234"
    assert not (tmp_path / "b.txt").exists()


def test_total_limit_at_the_boundary(tmp_path: Path) -> None:
    result = _run_writes(
        tmp_path,
        "--max-total-write-bytes",
        "6",
        attempts=(("a.txt", "1234"), ("b.txt", "12"), ("c.txt", "1")),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "ok a.txt\n"
        "ok b.txt\n"
        "Err fs_write_text: writing 1 bytes exceeds the remaining total write "
        "budget (0 of 6 bytes left; --max-total-write-bytes)\n"
    )
    assert not (tmp_path / "c.txt").exists()


@pytest.mark.parametrize("flag", ["--max-file-size-bytes", "--max-total-write-bytes"])
def test_zero_limits_reject_empty_writes_and_truncation(
    tmp_path: Path, flag: str
) -> None:
    keep = _write(tmp_path, "keep.txt", "ORIGINAL")

    result = _run_writes(tmp_path, flag, "0", attempts=(("keep.txt", ""),))

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"Err fs_write_text: file writes are disabled in this run ({flag} is 0)\n"
    )
    assert keep.read_text(encoding="utf-8") == "ORIGINAL"


def test_multibyte_text_counts_its_utf8_bytes(tmp_path: Path) -> None:
    result = _run_writes(
        tmp_path,
        "--max-file-size-bytes",
        "3",
        attempts=(("a.txt", "éa"), ("b.txt", "éé")),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "ok a.txt\n"
        "Err fs_write_text: writing 4 bytes exceeds the per-file limit of 3 bytes "
        "(--max-file-size-bytes)\n"
    )
    assert (tmp_path / "a.txt").read_bytes() == "éa".encode()


def test_rewrites_and_failed_writes_consume_the_budget(tmp_path: Path) -> None:
    result = _run_writes(
        tmp_path,
        "--max-total-write-bytes",
        "10",
        attempts=(
            ("same.txt", "123"),
            ("same.txt", "123"),
            ("missing/x.txt", "12"),
            ("other.txt", "123"),
            ("other.txt", "12"),
        ),
    )

    # A rewrite counts again, a failed open keeps its reservation, and a
    # rejected write consumes nothing: the program recovers and goes on.
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["ok same.txt", "ok same.txt"]
    assert lines[2].startswith("Err ")
    assert "exceeds" not in lines[2]
    assert lines[3:] == [
        "Err fs_write_text: writing 3 bytes exceeds the remaining total write "
        "budget (2 of 10 bytes left; --max-total-write-bytes)",
        "ok other.txt",
    ]
    assert (tmp_path / "other.txt").read_text(encoding="utf-8") == "12"


def test_quotas_differ_from_unsafe_as_documented(tmp_path: Path) -> None:
    # --unsafe has no write quotas, and its limit flags are refused there.
    source = _write_attempts(("a.txt", "12345"))

    isolated, unsafe = _isolated_and_unsafe(
        tmp_path, source, "--cap", "fs,print", "--max-file-size-bytes", "4"
    )
    plain_unsafe = _isolated_and_unsafe(tmp_path, source, "--cap", "fs,print")[1]

    assert isolated.returncode == 0, isolated.stderr
    assert isolated.stdout.startswith("Err fs_write_text: writing 5 bytes exceeds")
    assert unsafe.returncode == 1
    assert "--max-file-size-bytes only apply to the default process-isolated" in (
        unsafe.stderr
    )
    assert plain_unsafe.returncode == 0, plain_unsafe.stderr
    assert plain_unsafe.stdout == "ok a.txt\n"


# ---------------------------------------------------------------------------
# Examples share the facade, its roots and its budget

EXAMPLE_WRITER = """func save(text: String) -> String
    example "abc" -> "saved"
    match fs_write_text("example.txt", text) with
        | Ok(_) -> return "saved"
        | Err(error) -> return error
    end match
end func

func main() -> Unit
    match fs_write_text("main.txt", "abc") with
        | Ok(_) -> print("main saved")
        | Err(error) -> print("Err " + error)
    end match
end func
"""


def test_example_write_is_visible_and_counted(tmp_path: Path) -> None:
    _write(tmp_path, "main.geno", EXAMPLE_WRITER)

    result = _geno(
        "--cap", "fs,print", "--max-total-write-bytes", "5", "main.geno", cwd=tmp_path
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "Err fs_write_text: writing 3 bytes exceeds the remaining total write "
        "budget (2 of 5 bytes left; --max-total-write-bytes)\n"
    )
    assert (tmp_path / "example.txt").read_text(encoding="utf-8") == "abc"
    assert not (tmp_path / "main.txt").exists()


def test_no_check_examples_skips_the_examples_write(tmp_path: Path) -> None:
    _write(tmp_path, "main.geno", EXAMPLE_WRITER)

    result = _geno(
        "--no-check-examples",
        "--cap",
        "fs,print",
        "--max-total-write-bytes",
        "5",
        "main.geno",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "main saved\n"
    assert not (tmp_path / "example.txt").exists()


def test_example_and_compiled_paths_return_the_same_err(tmp_path: Path) -> None:
    expected = (
        "fs_write_text: writing 5 bytes exceeds the per-file limit of 4 bytes "
        "(--max-file-size-bytes)"
    )
    source = f"""func attempt(text: String) -> String
    example "12345" -> {_text(expected)}
    match fs_write_text("x.txt", text) with
        | Ok(_) -> return "saved"
        | Err(error) -> return error
    end match
end func

func main() -> Unit
    print(attempt("12345"))
end func
"""
    _write(tmp_path, "main.geno", source)

    result = _geno(
        "--cap", "fs,print", "--max-file-size-bytes", "4", "main.geno", cwd=tmp_path
    )

    # The example ran first and passed, so both paths gave this text.
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{expected}\n"


def test_example_fs_calls_are_denied_without_the_grant(tmp_path: Path) -> None:
    _write(tmp_path, "main.geno", EXAMPLE_WRITER)

    result = _geno("--cap", "print", "main.geno", cwd=tmp_path)

    assert result.returncode == 1
    assert "Capability denied: 'fs_write_text' requires the 'fs' capability" in (
        result.stderr
    )
    assert result.stderr.endswith(
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        "  geno run --cap fs,print main.geno\n"
    )
    assert not (tmp_path / "example.txt").exists()


# ---------------------------------------------------------------------------
# Limit flags: lane defaults, overrides and refusals


def _spy_launch(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, Any]]:
    from geno.sandbox import ProcessSandbox

    seen: list[tuple[int, Any]] = []
    original = ProcessSandbox.execute_geno_request

    def spy(self: ProcessSandbox, request: Any, policy: Any = None) -> Any:
        seen.append((self.config.max_file_size_bytes, policy))
        return original(self, request, policy)

    monkeypatch.setattr(ProcessSandbox, "execute_geno_request", spy)
    return seen


@pytest.mark.parametrize(
    ("capabilities", "flags", "per_file", "total"),
    [
        (None, {}, 0, 0),
        ({"print"}, {}, 0, 0),
        ({"fs", "print"}, {}, ISOLATED_RUN_FS_WRITE_LIMIT_BYTES, None),
        (
            {"fs", "print"},
            {"max_file_size_bytes": 7, "max_total_write_bytes": 9},
            7,
            9,
        ),
        # An explicit value never grants fs; the total stays neutral.
        ({"print"}, {"max_file_size_bytes": 5, "max_total_write_bytes": 9}, 5, 0),
    ],
)
def test_parent_resolves_the_lane_default_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    capabilities: set[str] | None,
    flags: dict[str, int],
    per_file: int,
    total: int | None,
) -> None:
    from geno.cli.run import run_file

    for name in _POLICY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    seen = _spy_launch(monkeypatch)
    source = _write(
        tmp_path, "main.geno", "func main() -> Int\n    return 3\nend func\n"
    )

    status = run_file(str(source), capabilities=capabilities, **flags)

    assert status == 3
    assert capsys.readouterr().err == ""
    ((file_limit, policy),) = seen
    assert file_limit == per_file
    expected_total = ISOLATED_RUN_FS_WRITE_LIMIT_BYTES if total is None else total
    assert policy.max_total_write_bytes == expected_total
    assert ISOLATED_RUN_FS_WRITE_LIMIT_BYTES == 64 * 1024 * 1024


@pytest.mark.parametrize("lane", ["--unsafe", "--json"])
@pytest.mark.parametrize("flag", ["--max-file-size-bytes", "--max-total-write-bytes"])
def test_limit_flags_are_process_only(tmp_path: Path, lane: str, flag: str) -> None:
    _write(tmp_path, "main.geno", "func main() -> Unit\n    print(1)\nend func\n")

    result = _geno(lane, "--cap", "fs,print", flag, "0", "main.geno", cwd=tmp_path)

    assert result.returncode == 1
    assert f"{flag} only apply to the default process-isolated run mode." in (
        result.stderr + result.stdout
    )


@pytest.mark.parametrize("flag", ["--max-file-size-bytes", "--max-total-write-bytes"])
def test_negative_limits_are_rejected(tmp_path: Path, flag: str) -> None:
    _write(tmp_path, "main.geno", "func main() -> Unit\n    print(1)\nend func\n")

    result = _geno("--cap", "fs,print", flag, "-1", "main.geno", cwd=tmp_path)

    assert result.returncode == 2
    assert "must be a non-negative integer" in result.stderr


@pytest.mark.parametrize(
    "flags",
    [
        {"max_total_write_bytes": -1},
        {"max_file_size_bytes": -1},
        {"max_total_write_bytes": True},
        {"max_file_size_bytes": 1.5},
    ],
)
def test_run_file_rejects_invalid_limits_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: dict[str, Any],
) -> None:
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    launched: list[Any] = []
    monkeypatch.setattr(
        ProcessSandbox, "_run_worker", lambda *args, **kwargs: launched.append(args)
    )
    source = _write(
        tmp_path, "main.geno", "func main() -> Unit\n    print(1)\nend func\n"
    )

    with pytest.raises(SystemExit) as excinfo:
        run_file(str(source), capabilities={"fs", "print"}, **flags)

    assert excinfo.value.code == 1
    assert launched == []
    (name,) = flags
    flag = "--" + name.replace("_", "-")
    assert capsys.readouterr().err == f"Error: {flag} must be a non-negative integer\n"


def _limits_line(stdout: str) -> str:
    (line,) = [row for row in stdout.splitlines() if row.startswith("Max file size")]
    return line


@pytest.mark.skipif(
    not os.path.exists("/proc/self/limits"),
    reason="RLIMIT_FSIZE probe reads Linux /proc (POSIX-only limit)",
)
@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ((), ISOLATED_RUN_FS_WRITE_LIMIT_BYTES),
        (("--max-file-size-bytes", "1234"), 1234),
    ],
)
def test_rlimit_fsize_is_the_resolved_per_file_limit(
    tmp_path: Path, flags: tuple[str, ...], expected: int
) -> None:
    _write(
        tmp_path,
        "main.geno",
        'func main() -> Unit\n    print(fs_read_text("/proc/self/limits").unwrap())\n'
        "end func\n",
    )
    # The worker reads its own limits: /proc/self resolves inside the worker.
    env = _env(
        GENO_FS_ROOTS=os.pathsep.join(["/proc/self", str(tmp_path)]),
        GENO_FS_ALLOW_ABSOLUTE="1",
    )

    result = _geno("--cap", "fs,print", *flags, "main.geno", cwd=tmp_path, env=env)

    assert result.returncode == 0, result.stderr
    assert _limits_line(result.stdout).split()[3:5] == [str(expected)] * 2


# ---------------------------------------------------------------------------
# The worker's own checks


def test_worker_refuses_an_fs_policy_without_roots(tmp_path: Path) -> None:
    from geno.isolated_host import IsolatedRunPolicy
    from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

    config = ProcessSandboxConfig(
        timeout=15.0, strict=False, compiled_runtime_prelude=True
    )
    source = _write(
        tmp_path, "main.geno", 'func main() -> Unit\n    print("ESCAPED")\nend func\n'
    )
    request = {
        "filename": str(source),
        "target": None,
        "check_examples": False,
        "timeout": config.timeout,
        "max_recursion_depth": config.max_recursion_depth,
        "max_output_length": config.max_output_length,
        "max_collection_size": config.max_collection_size,
        "max_integer_bits": config.max_integer_bits,
    }
    overrides = {
        "worker_mode": "geno_cli",
        "prelude_blob_sha256": None,
        "trusted_prelude_line_count": 0,
        "run_policy": {
            **IsolatedRunPolicy.default().to_config(),
            "capabilities": ["fs", "print"],
            "fs_configured_roots": [],
        },
    }

    result, output, error = ProcessSandbox(config)._execute_worker_payload(
        json.dumps(request), overrides
    )

    assert result is None
    assert output == ""
    assert error is not None
    assert error.startswith("startup_error: invalid isolated run policy: ")
    assert "at least one filesystem root" in error
