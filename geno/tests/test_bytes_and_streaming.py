"""Tests for first-class Bytes type, binary I/O, and scoped streaming I/O."""

from __future__ import annotations

import http.server
import io
import json
import os
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

import geno
from geno._serve import (
    install_fs_callbacks,
    install_http_callbacks,
    install_stdin_callbacks,
)
from geno.capabilities import isolated_run_capabilities
from geno.compiler import compile_to_python
from geno.diagnostics import ErrorCode
from geno.interpreter import Interpreter
from geno.isolated_host import IsolatedStdin, IsolatedStdinDecodeError
from geno.js_compiler import compile_to_js
from geno.parser import parse
from geno.property_check import PropertyConfig, check_module_properties
from geno.sandbox import SandboxConfig
from geno.tests._script_runner import GENERATED_PROGRAM_TIMEOUT
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)
from geno.typechecker import TypeChecker
from geno.types import GenoTypeError

needs_node = pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
BACKENDS = [
    _interpreter_output,
    _compiled_python_output,
    pytest.param(_compiled_js_output, marks=needs_node),
]


def _run_with_caps(
    source: str,
    backend: str,
    tmp_path: Path,
    *,
    caps: set[str],
    fs_root: Path | None = None,
    max_collection_size: int | None = None,
    stdin_text: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    program = parse(source)
    TypeChecker().check_program(program)
    config = (
        SandboxConfig(max_collection_size=max_collection_size)
        if max_collection_size is not None
        else SandboxConfig()
    )

    if backend == "interpreter":
        interp = Interpreter(
            check_examples=False,
            sandbox_config=config,
            capabilities=set(caps),
        )
        if "fs" in caps:
            install_fs_callbacks(
                interp,
                roots=[str(fs_root or tmp_path)],
                allow_absolute_paths=True,
                allow_write=True,
            )
        if "http" in caps:
            install_http_callbacks(interp, allow_private_networks=True)
        if "stdin" in caps:
            install_stdin_callbacks(interp)
        old_stdin = sys.stdin
        if stdin_text is not None:
            sys.stdin = io.StringIO(stdin_text)
        old_cwd = os.getcwd()
        os.chdir(fs_root or tmp_path)
        try:
            interp.run(program)
            return 0, "".join(interp.output_buffer), ""
        except Exception as exc:
            return 1, "".join(interp.output_buffer), str(exc)
        finally:
            os.chdir(old_cwd)
            sys.stdin = old_stdin

    env = dict(os.environ)
    env["GENO_FS_ROOTS"] = str(fs_root or tmp_path)
    env["GENO_FS_ALLOW_ABSOLUTE"] = "1"
    env["GENO_HTTP_ALLOW_PRIVATE"] = "1"
    if extra_env:
        env.update(extra_env)

    cap_args: list[str] = []
    for cap in sorted(caps):
        cap_args.extend(["--cap", cap])

    if backend == "python":
        code = compile_to_python(source)
        if max_collection_size is not None:
            code = f"_GENO_MAX_COLLECTION_SIZE = {max_collection_size}\n" + code
        script = tmp_path / "prog.py"
        script.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script), *cap_args],
            cwd=str(fs_root or tmp_path),
            capture_output=True,
            text=True,
            input=stdin_text,
            env=env,
            timeout=GENERATED_PROGRAM_TIMEOUT,
        )
        return proc.returncode, proc.stdout, proc.stderr

    if backend == "js":
        code = compile_to_js(source)
        assert isinstance(code, str)
        if max_collection_size is not None:
            code = (
                f"globalThis.__GENO_MAX_COLLECTION_SIZE = {max_collection_size};\n"
                + code
            )
        script = tmp_path / "prog.js"
        script.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            ["node", str(script), *cap_args],
            cwd=str(fs_root or tmp_path),
            capture_output=True,
            text=True,
            input=stdin_text,
            env=env,
            timeout=GENERATED_PROGRAM_TIMEOUT,
        )
        return proc.returncode, proc.stdout, proc.stderr

    raise AssertionError(f"Unknown backend: {backend}")


# =============================================================================
# Pure Bytes Operations (3-backend parity)
# =============================================================================


@pytest.mark.parametrize("run", BACKENDS)
def test_bytes_roundtrips_and_methods_agree_on_every_backend(run: Any) -> None:
    source = """
func main() -> Unit with io
    let b: Bytes = bytes_from_utf8("Hi 🌍")
    print(bytes_length(b))
    print(length(b))
    print(b.length())
    print(b.to_hex())
    print(b.to_base64())
    print(to_string(b))
    print(f"raw={b}")
    print(json_to_string(b))
    match b.to_utf8() with
        | Ok(text) -> print(text)
        | Err(msg) -> print("ERR:" + msg)
    end match
    let from_hex = result_unwrap(bytes_from_hex("486920f09f8c8d"))
    print(from_hex == b)
    let from_b64 = result_unwrap(bytes_from_base64(b.to_base64()))
    print(from_b64 == b)
    let from_list = result_unwrap(bytes_from_list(b.to_list()))
    print(from_list == b)
    print(b[0])
    print(b[-1])
    print(b[0:2].to_hex())
    print(bytes_slice(data: b, start: 1, stop: 3).to_hex())
    print((bytes_from_utf8("ab") + bytes_from_utf8("cd")).to_hex())
    print(bytes_concat(bytes_from_utf8("ab"), bytes_from_utf8("cd")).to_hex())
end func
"""
    assert run(source).splitlines() == [
        "7",
        "7",
        "7",
        "486920f09f8c8d",
        "SGkg8J+MjQ==",
        "Bytes(486920f09f8c8d)",
        "raw=Bytes(486920f09f8c8d)",
        '"Bytes(486920f09f8c8d)"',
        "Hi 🌍",
        "true",
        "true",
        "true",
        "72",
        "141",
        "4869",
        "6920",
        "61626364",
        "61626364",
    ]


@pytest.mark.parametrize("run", BACKENDS)
def test_bytes_validation_errors_and_padding_cases(run: Any) -> None:
    source = """
func main() -> Unit with io
    print(result_is_err(bytes_from_list([-1])))
    print(result_is_err(bytes_from_list([256])))
    print(result_is_ok(bytes_from_list([])))
    print(result_is_err(bytes_from_hex("abc")))
    print(result_is_err(bytes_from_hex("zz")))
    print(result_is_err(bytes_from_hex("00 01")))
    print(result_unwrap(bytes_from_hex("DeAdBeEf")).to_hex())
    print(result_is_err(bytes_from_base64("not!base64")))
    print(result_is_err(bytes_from_base64("abc")))
    print(result_unwrap(bytes_from_base64("")).length())
    print(result_unwrap(bytes_from_base64("Zg==")).to_utf8())
    print(result_unwrap(bytes_from_base64("Zm8=")).to_utf8())
    print(result_unwrap(bytes_from_base64("Zm9v")).to_utf8())
    print(bytes_from_utf8("").to_base64())
    print(bytes_from_utf8("f").to_base64())
    print(bytes_from_utf8("fo").to_base64())
    print(bytes_from_utf8("foo").to_base64())
    let bad_utf8 = result_unwrap(bytes_from_hex("fffe80"))
    print(result_is_err(bad_utf8.to_utf8()))
end func
"""
    assert run(source).splitlines() == [
        "true",
        "true",
        "true",
        "true",
        "true",
        "true",
        "deadbeef",
        "true",
        "true",
        "0",
        'Ok(value: "f")',
        'Ok(value: "fo")',
        'Ok(value: "foo")',
        "",
        "Zg==",
        "Zm8=",
        "Zm9v",
        "true",
    ]


@pytest.mark.parametrize("run", BACKENDS)
def test_bytes_ordering_equality_maps_sets_and_sorting(run: Any) -> None:
    source = """
func main() -> Unit with io
    let a = result_unwrap(bytes_from_hex("7f"))
    let b = result_unwrap(bytes_from_hex("80"))
    let c = result_unwrap(bytes_from_hex("ff"))
    let d = result_unwrap(bytes_from_hex("7f00"))
    print(a < b)
    print(b < c)
    print(a < d)
    print(d < b)
    print(a == result_unwrap(bytes_from_list([127])))
    print(a != b)
    let xs: List[Bytes] = [c, a, d, b]
    print(xs.sort())
    let m: Map[Bytes, Int] = {a: 1, b: 2}
    print(m[a])
    print(map_contains_key(m, result_unwrap(bytes_from_hex("80"))))
    let s: Set[Bytes] = set_from_list([b, a, b])
    print(s.length())
    print(s.contains(a))
end func
"""
    assert run(source).splitlines() == [
        "true",
        "true",
        "true",
        "true",
        "true",
        "true",
        "[Bytes(7f), Bytes(7f00), Bytes(80), Bytes(ff)]",
        "1",
        "true",
        "2",
        "true",
    ]


# =============================================================================
# Static Type & Effect Checks
# =============================================================================


def test_bytes_is_immutable_and_rejected_by_typed_json() -> None:
    with pytest.raises(GenoTypeError):
        TypeChecker().check_program(
            parse(
                """
func bad() -> Unit
    example () -> ()
    var b: Bytes = bytes_from_utf8("abc")
    b[0] = 65
end func
"""
            )
        )

    with pytest.raises(GenoTypeError) as exc_info:
        TypeChecker().check_program(
            parse(
                """
func bad(b: Bytes) -> String
    example bytes_from_utf8("a") -> ""
    return json_encode(b)
end func
"""
            )
        )
    assert exc_info.value.error_code == ErrorCode.TYPE_JSON_FORM
    assert "bytes_to_base64" in str(exc_info.value)


def test_bytes_match_requires_default_arm() -> None:
    with pytest.raises(GenoTypeError, match="requires a default arm"):
        TypeChecker().check_program(
            parse(
                """
func f(b: Bytes) -> Int
    example bytes_from_utf8("a") -> 0
    return match b with
        | x when bytes_length(x) == 0 -> 0
    end match
end func
"""
            )
        )


def test_streaming_callback_effects_propagate_soundly() -> None:
    # Calling print inside fs_each_line requires `io` in addition to `fs`.
    with pytest.raises(GenoTypeError, match="undeclared effects: io"):
        TypeChecker().check_program(
            parse(
                """
func bad(path: String) -> Result[Unit, String] with fs
    example "missing.txt" -> Ok(())
    return fs_each_line(path, fn(line: String) do
        print(line)
        return Ok(true)
    end fn)
end func
"""
            )
        )

    # Declaring both `fs, io` succeeds.
    TypeChecker().check_program(
        parse(
            """
func ok_fn(path: String) -> Result[Unit, String] with fs, io
    example "missing.txt" -> Ok(())
    return fs_each_line(path, fn(line: String) do
        print(line)
        return Ok(true)
    end fn)
end func
"""
        )
    )

    # First-class passing of fs_each_line cannot smuggle callback effects.
    with pytest.raises(GenoTypeError):
        TypeChecker().check_program(
            parse(
                """
type LineCb = (String) -> Result[Bool, String] with io

func run_with(path: String, f: (String, LineCb) -> Result[Unit, String] with fs) -> Result[Unit, String] with fs
    example ("x.txt", fs_each_line) -> Ok(())
    return f(path, fn(line: String) do
        print(line)
        return Ok(true)
    end fn)
end func

func main() -> Unit with fs
    let ignored = run_with("x.txt", fs_each_line)
end func
"""
            )
        )


def test_bytes_property_check_generates_and_shrinks() -> None:
    source = """
func roundtrip_hex(b: Bytes) -> Bytes
    ensures result == b
    example bytes_from_utf8("hi") -> bytes_from_utf8("hi")
    return result_unwrap(bytes_from_hex(bytes_to_hex(b)))
end func
"""
    program = parse(source)
    checker = TypeChecker()
    checker.check_program(program)
    results = check_module_properties(
        program,
        checker,
        module="bytes_prop",
        path="<test>",
        config=PropertyConfig(trials=20, seed=7),
        source=source,
    )
    assert len(results) == 1
    assert results[0].status == "passed"


# =============================================================================
# Bytes Across Extern FFI (all 3 backends)
# =============================================================================


def test_bytes_across_extern_ffi_all_backends(tmp_path: Path) -> None:
    py_mod = tmp_path / "bytes_host.py"
    py_mod.write_text(
        "def xor_bytes(data):\n"
        "    assert type(data) is bytes\n"
        "    return bytearray(b ^ 0xFF for b in data)\n"
        "def wrap_packet(pkt):\n"
        "    return {'id': pkt['id'] + 1, 'payload': bytes(reversed(pkt['payload']))}\n",
        encoding="utf-8",
    )
    js_mod = tmp_path / "bytes_host.cjs"
    js_mod.write_text(
        "exports.xor_bytes = (data) => {\n"
        "    if (!(data instanceof Uint8Array)) throw new Error('expected Uint8Array');\n"
        "    const out = Buffer.alloc(data.length);\n"
        "    for (let i = 0; i < data.length; i++) out[i] = data[i] ^ 0xff;\n"
        "    // Mutating the argument must not affect Geno's immutable Bytes.\n"
        "    if (data.length > 0) data[0] = 0x00;\n"
        "    return out;\n"
        "};\n"
        "exports.wrap_packet = (pkt) => ({\n"
        "    id: pkt.id + 1,\n"
        "    payload: Uint8Array.from(Array.from(pkt.payload).reverse()),\n"
        "});\n",
        encoding="utf-8",
    )

    source = f"""
type Packet = Packet(id: Int, payload: Bytes)

extern func xor_bytes(data: Bytes) -> Bytes with ffi
    python "bytes_host:xor_bytes"
    js "{js_mod.as_posix()}#xor_bytes"
end func

extern func wrap_packet(pkt: Packet) -> Packet with ffi
    python "bytes_host:wrap_packet"
    js "{js_mod.as_posix()}#wrap_packet"
end func

func main() -> Unit with ffi, io
    let orig = result_unwrap(bytes_from_hex("0102fe"))
    let flipped = xor_bytes(orig)
    print(orig.to_hex())
    print(flipped.to_hex())
    let p2 = wrap_packet(Packet(10, orig))
    print(p2.id)
    print(p2.payload.to_hex())
end func
"""
    expected = "0102fe\nfefd01\n11\nfe0201\n"

    # Interpreter
    sys.path.insert(0, str(tmp_path))
    try:
        rc, out, err = _run_with_caps(
            source, "interpreter", tmp_path, caps={"ffi", "print"}
        )
        assert rc == 0, err
        assert out == expected
    finally:
        sys.path.remove(str(tmp_path))

    # Compiled Python
    py_env = {"PYTHONPATH": str(tmp_path)}
    rc, out, err = _run_with_caps(
        source, "python", tmp_path, caps={"ffi", "print"}, extra_env=py_env
    )
    assert rc == 0, err
    assert out == expected

    # Compiled JS
    if HAS_NODE:
        rc, out, err = _run_with_caps(source, "js", tmp_path, caps={"ffi", "print"})
        assert rc == 0, err
        assert out == expected


# =============================================================================
# Binary File I/O & Streaming I/O (all 3 backends + isolated geno run)
# =============================================================================


@pytest.mark.parametrize(
    "backend",
    ["interpreter", "python", pytest.param("js", marks=needs_node)],
)
def test_fs_read_write_bytes_and_streaming_on_every_backend(
    backend: str, tmp_path: Path
) -> None:
    bin_path = tmp_path / "payload.bin"
    lines_path = tmp_path / "lines.txt"
    lines_path.write_bytes(b"alpha\r\nbeta\ngamma\n\ndelta")

    source = f"""
func main() -> Unit with fs, io, mutation
    let raw = result_unwrap(bytes_from_hex("000102fffe80"))
    let w = fs_write_bytes({json.dumps(bin_path.as_posix())}, raw)
    print(result_is_ok(w))
    let read_back = result_unwrap(fs_read_bytes({json.dumps(bin_path.as_posix())}))
    print(read_back == raw)
    print(read_back.to_hex())

    let missing = fs_read_bytes({json.dumps((tmp_path / "missing.bin").as_posix())})
    print(result_is_err(missing))

    let collected: Vec[String] = vec_new()
    let r1 = fs_each_line({json.dumps(lines_path.as_posix())}, fn(line: String) do
        vec_push(collected, line)
        return Ok(true)
    end fn)
    print(result_is_ok(r1))
    print(vec_to_list(collected))

    let early: Vec[String] = vec_new()
    let r2 = fs_each_line({json.dumps(lines_path.as_posix())}, fn(line: String) do
        vec_push(early, line)
        return Ok(line != "beta")
    end fn)
    print(result_is_ok(r2))
    print(vec_to_list(early))

    let r3 = fs_each_line({json.dumps(lines_path.as_posix())}, fn(line: String) do
        if line == "gamma" then
            return Err("stopped at gamma")
        end if
        return Ok(true)
    end fn)
    print(r3)

    let chunks: Vec[String] = vec_new()
    let r4 = fs_each_chunk(
        path: {json.dumps(bin_path.as_posix())},
        chunk_size: 4,
        callback: fn(chunk: Bytes) do
            vec_push(chunks, chunk.to_hex())
            return Ok(true)
        end fn
    )
    print(result_is_ok(r4))
    print(vec_to_list(chunks))

    let r5 = fs_each_chunk(
        path: {json.dumps(bin_path.as_posix())},
        chunk_size: 0,
        callback: fn(_chunk: Bytes) -> Ok(true)
    )
    print(result_is_err(r5))
end func
"""
    rc, out, err = _run_with_caps(
        source, backend, tmp_path, caps={"fs", "print"}, fs_root=tmp_path
    )
    assert rc == 0, err
    assert out.splitlines() == [
        "true",
        "true",
        "000102fffe80",
        "true",
        "true",
        '["alpha", "beta", "gamma", "", "delta"]',
        "true",
        '["alpha", "beta"]',
        'Err(error: "stopped at gamma")',
        "true",
        '["000102ff", "fe80"]',
        "true",
    ]


@pytest.mark.parametrize(
    "backend",
    ["interpreter", "python", pytest.param("js", marks=needs_node)],
)
def test_streaming_reads_files_larger_than_max_collection_size(
    backend: str, tmp_path: Path
) -> None:
    # File is 200 bytes > max_collection_size=64, yet streaming succeeds because
    # every line and every chunk stays within the 64-element limit.
    big_text = tmp_path / "many_lines.txt"
    big_text.write_text("".join(f"line-{i:02d}\n" for i in range(30)), encoding="utf-8")
    big_bin = tmp_path / "many_bytes.bin"
    big_bin.write_bytes(bytes(range(200)))

    source = """
func main() -> Unit with fs, io, mutation
    let lines: Vec[String] = vec_new()
    let r1 = fs_each_line("many_lines.txt", fn(line: String) do
        vec_push(lines, line)
        return Ok(true)
    end fn)
    print(result_is_ok(r1))
    print(vec_length(lines))

    let sizes: Vec[Int] = vec_new()
    let r2 = fs_each_chunk(
        path: "many_bytes.bin",
        chunk_size: 32,
        callback: fn(chunk: Bytes) do
            vec_push(sizes, chunk.length())
            return Ok(true)
        end fn
    )
    print(result_is_ok(r2))
    print(sum(vec_to_list(sizes)))
end func
"""
    rc, out, err = _run_with_caps(
        source,
        backend,
        tmp_path,
        caps={"fs", "print"},
        fs_root=tmp_path,
        max_collection_size=64,
    )
    assert rc == 0, err
    assert out.splitlines() == ["true", "30", "true", "200"]


@pytest.mark.parametrize(
    "backend",
    ["interpreter", "python", pytest.param("js", marks=needs_node)],
)
def test_streaming_enforces_per_line_and_chunk_limits(
    backend: str, tmp_path: Path
) -> None:
    long_line = tmp_path / "long_line.txt"
    long_line.write_text("x" * 100 + "\n", encoding="utf-8")

    source = """
func main() -> Unit with fs, io
    let ignored = fs_each_line("long_line.txt", fn(_line: String) -> Ok(true))
end func
"""
    rc, _out, err = _run_with_caps(
        source,
        backend,
        tmp_path,
        caps={"fs", "print"},
        fs_root=tmp_path,
        max_collection_size=32,
    )
    assert rc != 0
    assert "String size exceeds limit" in err

    chunk_source = """
func main() -> Unit with fs, io
    let ignored = fs_each_chunk(
        path: "long_line.txt",
        chunk_size: 64,
        callback: fn(_c: Bytes) -> Ok(true)
    )
end func
"""
    rc2, _out2, err2 = _run_with_caps(
        chunk_source,
        backend,
        tmp_path,
        caps={"fs", "print"},
        fs_root=tmp_path,
        max_collection_size=32,
    )
    assert rc2 != 0
    assert "Bytes size exceeds limit" in err2


def test_isolated_geno_run_bytes_fs_quotas_and_streaming(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    root.mkdir()
    outside = tmp_path / "secret.bin"
    outside.write_bytes(b"top-secret")
    (root / "in.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")

    prog = root / "main.geno"
    prog.write_text(
        """
func main() -> Unit with fs, io, mutation
    let raw = result_unwrap(bytes_from_hex("deadbeef"))
    print(result_is_ok(fs_write_bytes("out.bin", raw)))
    print(result_unwrap(fs_read_bytes("out.bin")).to_hex())
    print(result_is_err(fs_write_bytes("too_big.bin", bytes_from_utf8("12345678901234567890"))))
    print(result_is_err(fs_read_bytes("../secret.bin")))
    let seen: Vec[String] = vec_new()
    let r1 = fs_each_line("in.txt", fn(line: String) do
        vec_push(seen, line)
        return Ok(line != "two")
    end fn)
    print(result_is_ok(r1))
    print(vec_to_list(seen))
    let chunks: Vec[String] = vec_new()
    let r2 = fs_each_chunk(
        path: "out.bin",
        chunk_size: 2,
        callback: fn(c: Bytes) do
            vec_push(chunks, c.to_hex())
            return Ok(true)
        end fn
    )
    print(result_is_ok(r2))
    print(vec_to_list(chunks))
end func
""",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["GENO_FS_ROOTS"] = str(root)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--cap",
            "fs",
            "--cap",
            "print",
            "--max-file-size-bytes",
            "10",
            "--max-total-write-bytes",
            "12",
            str(prog),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=env,
        timeout=GENERATED_PROGRAM_TIMEOUT,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "true",
        "deadbeef",
        "true",
        "true",
        "true",
        '["one", "two"]',
        "true",
        '["dead", "beef"]',
    ]
    assert (root / "out.bin").read_bytes() == b"\xde\xad\xbe\xef"
    assert not (root / "too_big.bin").exists()


@pytest.mark.parametrize("backend", ["interpreter", "python"])
def test_stdin_each_line_streaming_and_early_stop(backend: str, tmp_path: Path) -> None:
    source = """
func main() -> Unit with stdin, io, mutation
    let first_two: Vec[String] = vec_new()
    let r = stdin_each_line(fn(line: String) do
        vec_push(first_two, line)
        return Ok(vec_length(first_two) < 2)
    end fn)
    print(result_is_ok(r))
    print(vec_to_list(first_two))
    print(result_unwrap(stdin_read_all()))
end func
"""
    rc, out, err = _run_with_caps(
        source,
        backend,
        tmp_path,
        caps={"stdin", "print"},
        stdin_text="first\r\nsecond\nthird\nfourth\n",
    )
    assert rc == 0, err
    assert out == 'true\n["first", "second"]\nthird\nfourth\n\n'


def test_isolated_stdin_read_line_and_utf8_errors() -> None:
    r_fd, w_fd = os.pipe()
    try:
        os.write(w_fd, b"one\r\nstop\nrest\n")
        os.close(w_fd)
        w_fd = -1
        stdin = IsolatedStdin(r_fd)
        assert stdin.read_line(lambda _n: None) == "one"
        assert stdin.read_line(lambda _n: None) == "stop"
        assert stdin.read_text(lambda _n: None) == "rest\n"
        assert stdin.read_line(lambda _n: None) is None
    finally:
        if w_fd >= 0:
            os.close(w_fd)
        os.close(r_fd)

    r_fd2, w_fd2 = os.pipe()
    try:
        os.write(w_fd2, b"\xff\xfe\n")
        os.close(w_fd2)
        w_fd2 = -1
        stdin2 = IsolatedStdin(r_fd2)
        with pytest.raises(IsolatedStdinDecodeError):
            stdin2.read_line(lambda _n: None)
    finally:
        if w_fd2 >= 0:
            os.close(w_fd2)
        os.close(r_fd2)


@pytest.mark.skipif(
    "stdin" not in isolated_run_capabilities(),
    reason="stdin not enabled in isolated run lane",
)
def test_isolated_geno_run_stdin_each_line(tmp_path: Path) -> None:
    prog = tmp_path / "stdin_stream.geno"
    prog.write_text(
        """
func main() -> Unit with stdin, io, mutation
    let seen: Vec[String] = vec_new()
    let r = stdin_each_line(fn(line: String) do
        vec_push(seen, line)
        return Ok(line != "stop")
    end fn)
    print(result_is_ok(r))
    print(vec_to_list(seen))
    print(result_unwrap(stdin_read_all()))
end func
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--cap",
            "stdin",
            "--cap",
            "print",
            str(prog),
        ],
        input="one\r\nstop\nrest\n",
        capture_output=True,
        text=True,
        timeout=GENERATED_PROGRAM_TIMEOUT,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == 'true\n["one", "stop"]\nrest\n\n'


@pytest.mark.parametrize(
    "backend",
    ["interpreter", "python", pytest.param("js", marks=needs_node)],
)
def test_http_fetch_bytes_rejects_bad_scheme(backend: str, tmp_path: Path) -> None:
    source = """
func main() -> Unit with http, io
    let r = http_fetch_bytes("file:///etc/passwd")
    print(result_is_err(r))
end func
"""
    rc, out, err = _run_with_caps(source, backend, tmp_path, caps={"http", "print"})
    assert rc == 0, err
    assert out.strip() == "true"


@pytest.mark.parametrize(
    "backend",
    ["interpreter", "python", pytest.param("js", marks=needs_node)],
)
def test_http_fetch_bytes_binary_payload_roundtrip(
    backend: str, tmp_path: Path
) -> None:
    payload = bytes([0x00, 0x01, 0x7F, 0x80, 0xFE, 0xFF])

    class _BinaryHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return None

    try:
        server = socketserver.TCPServer(("127.0.0.1", 0), _BinaryHandler)
    except PermissionError:
        pytest.skip("Loopback sockets blocked in sandboxed test environment")

    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = f"""
func main() -> Unit with http, io
    let r = http_fetch_bytes("http://127.0.0.1:{port}/bin")
    match r with
        | Ok(data) -> print(data.to_hex())
        | Err(msg) -> print("ERR:" + msg)
    end match
end func
"""
        rc, out, err = _run_with_caps(source, backend, tmp_path, caps={"http", "print"})
        assert rc == 0, err
        if "Operation not permitted" in out or "EPERM" in out:
            pytest.skip("Loopback connections blocked in sandboxed test environment")
        assert out.strip() == "00017f80feff"
    finally:
        server.shutdown()
        server.server_close()


def test_bytes_api_run_rejects_ungranted_streaming_capabilities() -> None:
    res = geno.run(
        """
func main() -> Unit with fs
    let ignored = fs_each_line("x.txt", fn(_l: String) -> Ok(true))
end func
"""
    )
    assert not res.ok
    assert any("fs" in str(diag).lower() for diag in res.diagnostics)


@pytest.mark.parametrize(
    "backend",
    ["interpreter", "python", pytest.param("js", marks=needs_node)],
)
def test_bytes_to_utf8_preserves_bom_across_backends(
    backend: str, tmp_path: Path
) -> None:
    source = """
func main() -> Unit with io
    let raw = result_unwrap(bytes_from_hex("efbbbf61"))
    let text = result_unwrap(bytes_to_utf8(raw))
    print(bytes_to_hex(bytes_from_utf8(text)))
    print(to_string(length(text)))
end func
"""
    rc, out, err = _run_with_caps(source, backend, tmp_path, caps={"print"})
    assert rc == 0, err
    assert out.splitlines() == ["efbbbf61", "2"]


@pytest.mark.parametrize(
    "backend",
    ["interpreter", "python", pytest.param("js", marks=needs_node)],
)
def test_fs_each_line_universal_newlines_and_chunk_boundary_crlf(
    backend: str, tmp_path: Path
) -> None:
    # Mix BOM, lone CR, CRLF split right across the 8192-byte JS buffer boundary
    # (3 + 13 + 8175 = 8191, so '\r' is byte 8191 and '\n' is byte 8192), and
    # lone CR right at the 16384-byte boundary.
    sample = tmp_path / "newlines.txt"
    chunk1_line = "a" * 8175
    chunk2_line = "b" * 8190
    sample.write_bytes(
        b"\xef\xbb\xbf"
        + b"first\rsecond\r"
        + chunk1_line.encode("utf-8")
        + b"\r\n"
        + chunk2_line.encode("utf-8")
        + b"\rlast\r"
    )
    path_literal = str(sample).replace("\\", "\\\\")
    source = f"""
func main() -> Unit with fs, io, mutation
    let lengths: Vec[Int] = vec_new()
    let r = fs_each_line("{path_literal}", fn(line: String) do
        vec_push(lengths, length(line))
        return Ok(true)
    end fn)
    print(result_is_ok(r))
    print(vec_to_list(lengths))
end func
"""
    rc, out, err = _run_with_caps(source, backend, tmp_path, caps={"fs", "print"})
    assert rc == 0, err
    assert out.splitlines() == ["true", "[6, 6, 8175, 8190, 4]"]
