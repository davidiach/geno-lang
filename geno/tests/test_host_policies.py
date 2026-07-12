"""Host callback policy regressions for fs/http/process capabilities."""

from __future__ import annotations

import contextlib
import http.server
import socket
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from geno.interpreter import Interpreter


def _callback(interpreter: Interpreter, name: str) -> Any:
    return interpreter.global_env.bindings[name].func


@contextlib.contextmanager
def _marker_server() -> Iterator[tuple[str, list[str]]]:
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"PRIVATE_TARGET_HIT")

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://rebind.test:{server.server_address[1]}/secret", hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _dns_rebind_getaddrinfo(
    real_getaddrinfo: Callable[..., object],
) -> tuple[Callable[..., object], dict[str, int]]:
    state = {"calls": 0}

    def fake_getaddrinfo(host: str, port: int, *args: Any, **kwargs: Any) -> object:
        if host == "rebind.test":
            state["calls"] += 1
            address = "93.184.216.34" if state["calls"] == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    return fake_getaddrinfo, state


def test_fs_callbacks_reject_absolute_paths_by_default(tmp_path):
    from geno._serve import install_fs_callbacks

    target = tmp_path / "secret.txt"
    target.write_text("secret")
    interpreter = Interpreter()
    install_fs_callbacks(interpreter, roots=[tmp_path])

    with pytest.raises(RuntimeError, match="absolute paths are not allowed"):
        _callback(interpreter, "fs_read_text")(str(target))


def test_fs_callbacks_can_be_read_only(tmp_path):
    from geno._serve import install_fs_callbacks

    target = tmp_path / "data.txt"
    target.write_text("ok")
    interpreter = Interpreter()
    install_fs_callbacks(interpreter, roots=[tmp_path], allow_write=False)

    assert _callback(interpreter, "fs_read_text")("data.txt") == "ok"
    with pytest.raises(RuntimeError, match="filesystem writes are not allowed"):
        _callback(interpreter, "fs_write_text")("out.txt", "nope")
    assert not (tmp_path / "out.txt").exists()


def test_fs_callbacks_reject_symlink_escape(tmp_path):
    from geno._serve import install_fs_callbacks

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")

    interpreter = Interpreter()
    install_fs_callbacks(interpreter, roots=[root])

    with pytest.raises(RuntimeError, match="escapes configured filesystem roots"):
        _callback(interpreter, "fs_read_text")("link/secret.txt")


def test_http_callbacks_reject_private_targets(monkeypatch):
    from geno._serve import install_http_callbacks

    def fake_getaddrinfo(*args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("169.254.169.254", 80),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    interpreter = Interpreter()
    install_http_callbacks(interpreter)

    with pytest.raises(RuntimeError, match="private, local, or reserved"):
        _callback(interpreter, "http_fetch")("http://metadata.local/latest")


def test_http_callbacks_recheck_address_used_for_connection(monkeypatch) -> None:
    from geno._serve import install_http_callbacks

    fake_getaddrinfo, state = _dns_rebind_getaddrinfo(socket.getaddrinfo)
    monkeypatch.delenv("GENO_HTTP_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    interpreter = Interpreter()
    install_http_callbacks(interpreter)

    with _marker_server() as (url, hits):
        with pytest.raises(RuntimeError, match="private, local, or reserved"):
            _callback(interpreter, "http_fetch")(url)

    assert state["calls"] == 2
    assert hits == []


def test_process_callbacks_require_absolute_executables_by_default():
    from geno._serve import install_process_callbacks

    interpreter = Interpreter()
    install_process_callbacks(interpreter)

    result = _callback(interpreter, "spawn")("python", [])

    assert result.constructor == "Err"
    assert "absolute path" in result.fields["error"]


def test_process_callbacks_honor_executable_allowlist(tmp_path):
    from geno._serve import install_process_callbacks

    interpreter = Interpreter()
    install_process_callbacks(interpreter, allowed_executables=[sys.executable])

    result = _callback(interpreter, "spawn")(str(tmp_path / "tool"), [])

    assert result.constructor == "Err"
    assert "allowlist" in result.fields["error"]


def test_compiled_runtime_fs_policy_rejects_absolute_path(monkeypatch, tmp_path):
    from geno import _runtime_support as rs

    monkeypatch.setattr(rs, "_GENO_CAPS", {"fs"})
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "data.txt"
    path.write_text("ok")

    assert rs.fs_read_text("data.txt") == "ok"
    with pytest.raises(RuntimeError, match="absolute paths are not allowed"):
        rs.fs_read_text(str(path))


def test_compiled_runtime_fs_policy_can_be_read_only(monkeypatch, tmp_path):
    from geno import _runtime_support as rs

    monkeypatch.setattr(rs, "_GENO_CAPS", {"fs"})
    monkeypatch.setenv("GENO_FS_READ_ONLY", "1")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data.txt").write_text("ok")

    assert rs.fs_read_text("data.txt") == "ok"
    with pytest.raises(RuntimeError, match="filesystem writes are not allowed"):
        rs.fs_write_text("out.txt", "nope")
    assert not (tmp_path / "out.txt").exists()


def test_compiled_runtime_http_policy_rejects_private_target(monkeypatch):
    from geno import _runtime_support as rs

    def fake_getaddrinfo(*args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1", 80),
            )
        ]

    monkeypatch.setattr(rs, "_GENO_CAPS", {"http"})
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(RuntimeError, match="private, local, or reserved"):
        rs.http_fetch("http://localhost/")


def test_compiled_runtime_http_policy_rechecks_connected_address(monkeypatch) -> None:
    from geno import _runtime_support as rs

    fake_getaddrinfo, state = _dns_rebind_getaddrinfo(socket.getaddrinfo)
    monkeypatch.setattr(rs, "_GENO_CAPS", {"http"})
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.delenv("GENO_HTTP_ALLOW_PRIVATE", raising=False)

    with _marker_server() as (url, hits):
        with pytest.raises(RuntimeError, match="private, local, or reserved"):
            rs.http_fetch(url)

    assert state["calls"] == 2
    assert hits == []


def test_compiled_runtime_process_policy_requires_absolute_executable(monkeypatch):
    from geno import _runtime_support as rs

    monkeypatch.setattr(rs, "_GENO_CAPS", {"process"})

    result = rs.spawn("python", [])

    assert isinstance(result, rs.Err)
    assert "absolute path" in result.error


class TestHostCallbackValidation:
    """M-06: host_callbacks with an unknown or ungranted key must not be
    silently dropped — a typo would leave the real host builtin exposed."""

    def test_unknown_callback_name_raises(self):
        from geno.api import RunConfig

        # 'htttp_fetch' is a typo — must fail loudly at config construction
        # (fail-fast at the embedding API boundary), not silently no-op leaving
        # the real builtin exposed.
        with pytest.raises(ValueError, match="not a capability-gated builtin"):
            RunConfig(
                capabilities={"http"},
                host_callbacks={"htttp_fetch": lambda url: "x"},
            )

    def test_ungranted_capability_callback_warns(self, caplog):
        from geno.api import RunConfig, run

        with caplog.at_level("WARNING", logger="geno.api"):
            # http_fetch is valid but the 'http' capability is not granted.
            config = RunConfig(
                capabilities=set(),
                host_callbacks={"http_fetch": lambda url: "x"},
            )
            run("func main() -> Int\n  return 0\nend func", config=config)
        assert any("will not take effect" in rec.message for rec in caplog.records)

    def test_valid_granted_callback_still_installs(self):
        from geno.api import RunConfig, run

        config = RunConfig(
            capabilities={"http"},
            host_callbacks={"http_fetch": lambda url: "intercepted"},
        )
        source = """
        func main() -> String
            return http_fetch(url: "https://example.com")
        end func
        """
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "intercepted"
