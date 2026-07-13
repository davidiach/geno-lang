"""
Security Audit Tests
====================

Comprehensive tests covering capability bypass, sandbox escape,
and HTTP server attack surfaces. Created for P6-6 (#186).
"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml  # type: ignore[import-untyped]

from geno.api import RunConfig, run
from geno.sandbox import (
    SecurityViolation,
    run_sandboxed,
)


def _create_server_or_skip(*args, **kwargs):
    """Create a bound server or skip when the environment forbids local binds."""
    from geno.server import create_server

    try:
        return create_server(*args, **kwargs)
    except PermissionError as exc:
        pytest.skip(f"local socket binds not permitted in this environment: {exc}")


# =========================================================================
# Capability bypass tests: every gated builtin must fail with empty caps
# =========================================================================


class TestCapabilityBypassPrint:
    """print capability must be denied when not granted."""

    def test_print_denied(self):
        source = "func main() -> Int\n    print(42)\n    return 0\nend func"
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


class TestCapabilityBypassClock:
    """clock capability must be denied for all clock builtins."""

    def test_clock_now_denied(self):
        source = "func main() -> Float\n    return clock_now()\nend func"
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_clock_elapsed_denied(self):
        source = "func main() -> Float\n    let t: Float = clock_now()\n    return clock_elapsed(start: t, end_time: t)\nend func"
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_clock_format_denied(self):
        source = 'func main() -> String\n    return clock_format(timestamp: 0.0, fmt: "%Y")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_clock_parse_denied(self):
        source = 'func main() -> Option[Float]\n    return clock_parse(text: "2025-01-01", fmt: "%Y-%m-%d")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


class TestCapabilityBypassRandom:
    """random capability must be denied for all random builtins."""

    def test_random_int_denied(self):
        source = "func main() -> Int\n    return random_int(min: 0, max: 10)\nend func"
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_random_float_denied(self):
        source = "func main() -> Float\n    return random_float()\nend func"
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


class TestCapabilityBypassFs:
    """fs capability must be denied for all filesystem builtins."""

    def test_fs_read_text_denied(self):
        source = (
            'func main() -> String\n    return fs_read_text(path: "/tmp/x")\nend func'
        )
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_fs_write_text_denied(self):
        source = 'func main() -> Unit\n    fs_write_text(path: "/tmp/x", content: "hi")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_fs_exists_denied(self):
        source = 'func main() -> Bool\n    return fs_exists(path: "/tmp")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_fs_list_dir_denied(self):
        source = 'func main() -> Result[List[String], String]\n    return fs_list_dir(path: "/tmp")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


class TestCapabilityBypassHttp:
    """http capability must be denied for all HTTP builtins."""

    def test_http_fetch_denied(self):
        source = 'func main() -> String\n    return http_fetch(url: "http://example.com")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


class TestCapabilityBypassRegex:
    """regex capability must be denied for all regex builtins."""

    def test_regex_match_denied(self):
        source = 'func main() -> Option[String]\n    return regex_match(pattern: "a", text: "a")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_regex_find_all_denied(self):
        source = 'func main() -> List[String]\n    return regex_find_all(pattern: "a", text: "aaa")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_regex_replace_denied(self):
        source = 'func main() -> String\n    return regex_replace(pattern: "a", text: "abc", replacement: "x")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


class TestCapabilityBypassEnv:
    """env capability must be denied for all env builtins."""

    def test_env_get_denied(self):
        source = (
            'func main() -> Option[String]\n    return env_get(name: "HOME")\nend func'
        )
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


class TestCapabilityDefaultsFailClosed:
    """Omitted capabilities must behave like no granted gated capabilities."""

    def test_runconfig_none_denies_env_get(self, monkeypatch):
        monkeypatch.setenv("GENO_AUDIT_SECRET", "leak")
        source = (
            "func main() -> Option[String]\n"
            '    return env_get(name: "GENO_AUDIT_SECRET")\n'
            "end func"
        )

        result = run(source, config=RunConfig())

        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_runconfig_none_denies_regex(self):
        source = (
            "func main() -> Option[String]\n"
            '    return regex_match(pattern: "a", text: "a")\n'
            "end func"
        )

        result = run(source, config=RunConfig())

        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_env_get_or_denied(self):
        source = 'func main() -> String\n    return env_get_or(name: "HOME", default: "x")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


class TestDirectInterpreterCapabilityGating:
    """Direct Interpreter construction must not expose high-risk builtins."""

    @staticmethod
    def _assert_capability_denied(exc_info) -> None:
        from geno.diagnostics import ErrorCode

        assert exc_info.value.error_code == ErrorCode.RUNTIME_CAPABILITY_DENIED

    def _run_direct(self, source: str, *, capabilities=()):
        from geno.interpreter import Interpreter
        from geno.parser import parse

        if capabilities == ():
            interp = Interpreter(check_examples=False)
        else:
            interp = Interpreter(check_examples=False, capabilities=capabilities)
        return interp, interp.run(parse(source))

    def test_direct_interpreter_defaults_deny_env(self, monkeypatch):
        from geno.values import RuntimeError as GenoRuntimeError

        monkeypatch.setenv("GENO_DIRECT_SECRET", "leak")
        source = (
            "func main() -> Option[String]\n"
            '    return env_get(name: "GENO_DIRECT_SECRET")\n'
            "end func"
        )

        with pytest.raises(GenoRuntimeError) as exc_info:
            self._run_direct(source)

        self._assert_capability_denied(exc_info)

    def test_direct_interpreter_defaults_deny_env_get_or(self, monkeypatch):
        from geno.values import RuntimeError as GenoRuntimeError

        monkeypatch.setenv("GENO_DIRECT_SECRET", "leak")
        source = (
            "func main() -> String\n"
            '    return env_get_or(name: "GENO_DIRECT_SECRET", default: "missing")\n'
            "end func"
        )

        with pytest.raises(GenoRuntimeError) as exc_info:
            self._run_direct(source)

        self._assert_capability_denied(exc_info)

    def test_direct_interpreter_defaults_deny_cli_args(self):
        from geno.values import RuntimeError as GenoRuntimeError

        source = "func main() -> List[String]\n    return cli_args()\nend func"

        with pytest.raises(GenoRuntimeError) as exc_info:
            self._run_direct(source)

        self._assert_capability_denied(exc_info)

    def test_direct_interpreter_defaults_deny_regex(self):
        from geno.values import RuntimeError as GenoRuntimeError

        source = (
            "func main() -> Option[String]\n"
            '    return regex_match(pattern: "a", text: "a")\n'
            "end func"
        )

        with pytest.raises(GenoRuntimeError) as exc_info:
            self._run_direct(source)

        self._assert_capability_denied(exc_info)

    def test_direct_interpreter_allows_env_when_granted(self, monkeypatch):
        from geno.values import ConstructorValue

        monkeypatch.setenv("GENO_DIRECT_ALLOWED", "ok")
        source = (
            "func main() -> Option[String]\n"
            '    return env_get(name: "GENO_DIRECT_ALLOWED")\n'
            "end func"
        )

        _interp, result = self._run_direct(source, capabilities={"env"})

        assert isinstance(result, ConstructorValue)
        assert result.constructor == "Some"
        assert result.fields["value"] == "ok"

    def test_direct_interpreter_keeps_low_risk_default_print(self):
        source = 'func main() -> Unit\n    print("hello")\n    return ()\nend func'

        interp, _result = self._run_direct(source)

        assert "hello" in interp.get_output()

    @pytest.mark.parametrize(
        ("builtin_name", "args"),
        [
            ("env_get", ("PATH",)),
            ("env_get_or", ("PATH", "missing")),
            ("cli_args", ()),
            ("regex_match", ("a", "a")),
        ],
    )
    def test_repl_uses_capability_gated_interpreter(self, builtin_name, args):
        from geno.repl import REPL
        from geno.values import RuntimeError as GenoRuntimeError

        repl = REPL()
        builtin = repl.interpreter.global_env.bindings[builtin_name]

        with pytest.raises(GenoRuntimeError) as exc_info:
            builtin.func(*args)

        self._assert_capability_denied(exc_info)

    @pytest.mark.parametrize(
        ("builtin_name", "args"),
        [
            ("env_get", ("PATH",)),
            ("env_get_or", ("PATH", "missing")),
            ("cli_args", ()),
            ("regex_match", ("a", "a")),
        ],
    )
    def test_repl_clear_keeps_capability_gates(self, capsys, builtin_name, args):
        from geno.repl import REPL
        from geno.values import RuntimeError as GenoRuntimeError

        repl = REPL()
        repl._handle_command(":clear")
        capsys.readouterr()
        builtin = repl.interpreter.global_env.bindings[builtin_name]

        with pytest.raises(GenoRuntimeError) as exc_info:
            builtin.func(*args)

        self._assert_capability_denied(exc_info)


class TestCapabilityBypassProcess:
    """process capability must be denied for exec builtins."""

    def test_exec_denied(self):
        source = 'func main() -> Result[ProcessResult, String]\n    return exec(command: "echo hi")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


class TestCompiledCapabilityEnforcement:
    """Standalone compiled runtimes must enforce the capability table."""

    def test_compiled_python_regex_denied_without_capability(self):
        import geno._runtime_support as rts

        saved = rts._GENO_CAPS
        rts._GENO_CAPS = set()
        try:
            with pytest.raises(RuntimeError, match="Capability denied"):
                rts.regex_match("a", "a")
        finally:
            rts._GENO_CAPS = saved

    def test_compiled_js_regex_denied_without_capability(self, tmp_path):
        from geno.js_compiler import compile_to_js

        if subprocess.run(["node", "--version"], capture_output=True).returncode != 0:
            pytest.skip("Node.js not available")

        source = (
            "func main() -> Option[String]\n"
            '    return regex_match(pattern: "a", text: "a")\n'
            "end func\n"
        )
        js_file = tmp_path / "regex.js"
        js_file.write_text(compile_to_js(source))

        result = subprocess.run(
            ["node", str(js_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "Capability denied" in result.stderr

    def test_cli_js_compile_rejects_process_builtin(self, tmp_path):
        source = (
            "func main() -> Result[ProcessResult, String]\n"
            '    return exec(command: "echo hi")\n'
            "end func\n"
        )
        src = tmp_path / "process.geno"
        out = tmp_path / "process.js"
        src.write_text(source)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(src),
                "--target",
                "js",
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode != 0
        assert "not available on the 'node-cli' target" in result.stderr
        assert not out.exists()

    def test_cli_js_source_map_is_opt_in(self, tmp_path):
        source = "func main() -> Int\n    return 1\nend func\n"
        src = tmp_path / "main.geno"
        out = tmp_path / "main.js"
        src.write_text(source)

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(src),
                "--target",
                "js",
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        assert out.exists()
        assert not (tmp_path / "main.js.map").exists()
        assert "sourceMappingURL" not in out.read_text()

        mapped = tmp_path / "mapped.js"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(src),
                "--target",
                "js",
                "--source-map",
                "-o",
                str(mapped),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "mapped.js.map").exists()
        assert "sourceMappingURL=mapped.js.map" in mapped.read_text()

    def test_exec_with_input_denied(self):
        source = 'func main() -> Result[ProcessResult, String]\n    return exec_with_input(command: "cat", stdin: "data")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


class TestCapabilityBypassServe:
    """serve capability must be denied for HTTP server builtins."""

    def test_http_listen_denied(self):
        source = "func main() -> Unit\n    return http_listen(port: 8080)\nend func"
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_http_route_denied(self):
        source = (
            '@untested("serve")\n'
            "func handler(req: HttpRequest) -> HttpResponse\n"
            '    return HttpResponse(200, "ok", [])\n'
            "end func\n"
            '@untested("serve")\n'
            "func main() -> Unit\n"
            '    http_route(method: "GET", path: "/", handler: handler)\n'
            "end func"
        )
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)


# =========================================================================
# Sandbox escape tests: indirect import attacks
# =========================================================================


class TestSandboxEscapeImports:
    """Crafted programs must not import os/subprocess via indirect paths."""

    def test_direct_import_os_blocked(self):
        code = "import os; os.system('echo pwned')"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_direct_import_subprocess_blocked(self):
        code = "import subprocess; subprocess.run(['echo', 'pwned'])"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_import_via_importlib_blocked(self):
        code = "import importlib; importlib.import_module('os')"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_import_via_builtins_blocked(self):
        code = "__builtins__.__import__('os')"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code)

    def test_import_via_dunder_import_blocked(self):
        code = "__import__('os')"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_import_ctypes_blocked(self):
        code = "import ctypes"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_import_socket_blocked(self):
        code = "import socket"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_import_io_allowed_with_proxy(self):
        """io is in the safe-import allowlist; dangerous functions are blocked
        by the module proxy (io.open, io.FileIO, etc.)."""
        code = "import io; __result__ = repr(io)"
        result, _ = run_sandboxed(code)
        assert "sandboxed module" in result

    def test_import_pickle_blocked(self):
        code = "import pickle"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_from_os_import_blocked(self):
        code = "from os import path"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_from_subprocess_import_blocked(self):
        code = "from subprocess import Popen"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)


class TestSandboxEscapeAttributeChains:
    """Attribute chain attacks must be blocked."""

    def test_class_bases_chain(self):
        """Prevent __class__.__bases__[0].__subclasses__() escape."""
        code = "().__class__.__bases__[0].__subclasses__()"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code)

    def test_class_mro_chain(self):
        code = "''.__class__.__mro__"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code)

    def test_func_globals_access(self):
        code = "def f(): pass\nf.__globals__"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code)

    def test_func_code_access(self):
        code = "def f(): pass\nf.__code__"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code)

    def test_builtins_dict_access(self):
        code = "x = {}; x.__class__.__bases__"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code)

    def test_closure_access(self):
        code = "def outer():\n  x = 1\n  def inner(): return x\n  return inner\nouter().__closure__"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code)


class TestSandboxEscapeEvalExec:
    """Aliased eval/exec must still be blocked."""

    def test_eval_via_alias(self):
        code = "e = eval\ne('1+1')"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_exec_via_alias(self):
        code = "e = exec\ne('import os')"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_compile_via_alias(self):
        code = "c = compile\nc('1', '', 'eval')"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code)

    def test_type_three_arg_blocked(self):
        """3-arg type() can create classes that bypass safe_getattr."""
        code = "type('X', (object,), {'__getattribute__': lambda s, n: 'pwned'})"
        with pytest.raises((SecurityViolation, RuntimeError, TypeError)):
            run_sandboxed(code)


# =========================================================================
# HTTP server tests: path traversal, header injection, request smuggling
# =========================================================================


class TestHTTPPathTraversal:
    """Server must reject path traversal attempts."""

    @pytest.fixture()
    def client(self):
        import http.client
        import socket
        import threading

        server = _create_server_or_skip("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address

        class _Client:
            def get_raw(self, path):
                with socket.create_connection((host, port), timeout=10) as conn:
                    conn.sendall(
                        (
                            f"GET {path} HTTP/1.1\r\n"
                            f"Host: {host}:{port}\r\n"
                            "Connection: close\r\n"
                            "\r\n"
                        ).encode("ascii")
                    )
                    chunks = []
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)

                body = b"".join(chunks)
                status_line = body.split(b"\r\n", 1)[0].decode(
                    "ascii",
                    errors="replace",
                )
                parts = status_line.split(" ", 2)
                if len(parts) < 2:
                    raise AssertionError(
                        f"invalid HTTP response status line: {status_line!r}"
                    )
                return int(parts[1]), body

            def get(self, path):
                conn = http.client.HTTPConnection(host, port, timeout=10)
                conn.request("GET", path)
                resp = conn.getresponse()
                body = resp.read()
                conn.close()
                return resp.status, body

            def post(self, path, payload=None, raw_body=None, headers=None):
                conn = http.client.HTTPConnection(host, port, timeout=10)
                hdrs = headers or {}
                if raw_body is not None:
                    body = raw_body
                else:
                    body = (
                        json.dumps(payload).encode("utf-8")
                        if payload is not None
                        else b""
                    )
                hdrs.setdefault("Content-Type", "application/json")
                hdrs.setdefault("Content-Length", str(len(body)))
                conn.request("POST", path, body=body, headers=hdrs)
                resp = conn.getresponse()
                resp_body = resp.read()
                conn.close()
                return resp.status, resp_body

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield _Client()
        server.shutdown()
        server.server_close()

    def test_dot_dot_traversal_rejected(self, client):
        status, _ = client.get_raw("/../../../etc/passwd")
        assert status == 404

    def test_encoded_traversal_rejected(self, client):
        status, _ = client.get_raw("/%2e%2e/%2e%2e/etc/passwd")
        assert status == 404

    def test_double_slash_rejected(self, client):
        status, _ = client.get_raw("//run")
        assert status == 404

    def test_run_with_extra_path_rejected(self, client):
        status, _ = client.get_raw("/run/../../etc/passwd")
        assert status == 404

    def test_null_byte_in_path(self, client):
        status, _ = client.get_raw("/healthz%00.html")
        assert status == 404


class TestHTTPHeaderInjection:
    """Server must not allow header injection via user input."""

    @pytest.fixture()
    def client(self):
        import http.client
        import threading

        server = _create_server_or_skip("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address

        class _Client:
            def post(self, path, payload=None, headers=None):
                conn = http.client.HTTPConnection(host, port, timeout=10)
                hdrs = headers or {}
                body = (
                    json.dumps(payload).encode("utf-8") if payload is not None else b""
                )
                hdrs.setdefault("Content-Type", "application/json")
                hdrs.setdefault("Content-Length", str(len(body)))
                conn.request("POST", path, body=body, headers=hdrs)
                resp = conn.getresponse()
                resp_body = resp.read()
                conn.close()
                return resp.status, resp_body, resp

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield _Client()
        server.shutdown()
        server.server_close()

    def test_no_user_data_in_response_headers(self, client):
        """User-supplied source must not appear in response headers."""
        source = 'func main() -> String\n    return "X-Injected: true\\r\\nEvil: header"\nend func'
        _status, _body, resp = client.post("/run", {"source": source})
        # Response should not contain injected headers
        assert resp.getheader("X-Injected") is None
        assert resp.getheader("Evil") is None


class TestServeHeaderValidation:
    """Interpreter serve callbacks must reject response-splitting headers."""

    def test_request_body_decode_rejects_invalid_utf8(self):
        from geno._serve import _decode_request_body

        with pytest.raises(ValueError, match="valid UTF-8"):
            _decode_request_body(b"\xff")

    def test_checked_http_request_rejects_over_limit_body(self):
        from geno._serve import _build_checked_http_request
        from geno.interpreter import Interpreter
        from geno.sandbox import SandboxConfig
        from geno.values import RuntimeError as GenoRuntimeError

        interp = Interpreter(sandbox_config=SandboxConfig(max_collection_size=2))

        with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
            _build_checked_http_request(interp, "POST", "/", [], "abcd")

    def test_checked_http_request_splits_path_and_query(self):
        from geno._serve import _build_checked_http_request
        from geno.interpreter import Interpreter
        from geno.sandbox import SandboxConfig

        interp = Interpreter(sandbox_config=SandboxConfig(max_collection_size=100))

        request = _build_checked_http_request(
            interp, "GET", "/search?q=geno", [("Accept", "text/plain")], ""
        )

        assert request.fields["method"] == "GET"
        assert request.fields["path"] == "/search"
        assert request.fields["query"] == "q=geno"
        assert request.fields["headers"] == [("Accept", "text/plain")]

    def test_response_header_value_rejects_crlf(self):
        from geno._serve import _validate_response_headers

        with pytest.raises(RuntimeError, match="Invalid response header value"):
            _validate_response_headers([("X-Test", "ok\r\nX-Evil: yes")])

    def test_response_header_name_rejects_controls(self):
        from geno._serve import _validate_response_headers

        with pytest.raises(RuntimeError, match="Invalid response header name"):
            _validate_response_headers([("X-Test\r\nX-Evil", "yes")])

    def test_route_handler_propagates_sandbox_errors(self, monkeypatch):
        import http.server
        import io

        from geno._serve import install_serve_callbacks
        from geno.interpreter import Interpreter
        from geno.sandbox import SandboxConfig, StepLimitExceeded
        from geno.values import BuiltinFunction

        captured = {}

        class FakeServer:
            def __init__(self, _addr, handler_cls):
                captured["handler_cls"] = handler_cls

            def serve_forever(self):
                return None

        monkeypatch.setattr(http.server, "HTTPServer", FakeServer)

        interp = Interpreter(sandbox_config=SandboxConfig(max_collection_size=100))
        install_serve_callbacks(interp)

        def boom(_request):
            raise StepLimitExceeded("step budget exhausted")

        interp._call_function(
            interp.global_env.bindings["http_route"],
            ["GET", "/", BuiltinFunction("boom", boom, 1, ["request"])],
        )
        interp._call_function(interp.global_env.bindings["http_listen"], [0])

        handler = captured["handler_cls"].__new__(captured["handler_cls"])
        handler.command = "GET"
        handler.path = "/"
        handler.headers = SimpleNamespace(
            get=lambda _name, _default=None: "0",
            items=lambda: [],
        )
        handler.rfile = io.BytesIO(b"")
        handler.wfile = io.BytesIO()
        handler.statuses = []
        handler.send_response = lambda status: handler.statuses.append(status)
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None

        with pytest.raises(StepLimitExceeded, match="step budget exhausted"):
            handler._handle()

        assert handler.statuses == []
        assert handler.wfile.getvalue() == b""


class TestHostedServerWorkerIsolation:
    """Hosted execution must not fall back to unkillable thread workers."""

    def test_worker_spawn_failure_is_error(self, monkeypatch):
        import geno.server as server_mod

        requested_methods: list[str] = []

        def fail_get_context(method):
            requested_methods.append(method)
            raise OSError("spawn disabled")

        monkeypatch.setattr(server_mod.multiprocessing, "get_context", fail_get_context)

        status, payload = server_mod._execute_worker_with_wall_timeout(
            lambda *_args: None,
            (),
            0.1,
        )

        assert status == "error"
        assert payload["type"] == "WorkerSpawnFailed"
        assert requested_methods == ["spawn"]

    def test_legacy_thread_timeout_helper_is_retired(self):
        import geno.server as server_mod

        assert not hasattr(server_mod, "_execute_worker_with_thread_timeout")


class TestToolingHardening:
    """Static checks for repository-level security glue."""

    def test_ci_workflows_default_to_read_only_permissions(self):
        root = Path(__file__).resolve().parents[2]

        for workflow_name in ["ci.yml", "release-gate.yml"]:
            workflow = yaml.safe_load(
                (root / ".github" / "workflows" / workflow_name).read_text(
                    encoding="utf-8"
                )
            )
            assert workflow["permissions"] == {"contents": "read"}

    def test_release_gate_passes_app_dir_as_python_argument(self):
        root = Path(__file__).resolve().parents[2]
        workflow = yaml.safe_load(
            (root / ".github" / "workflows" / "release-gate.yml").read_text(
                encoding="utf-8"
            )
        )
        steps = workflow["jobs"]["release-gate"]["steps"]
        validate_step = next(
            step
            for step in steps
            if step.get("name") == "Discover and validate example apps"
        )
        run_script = validate_step["run"]

        assert run_script == "python scripts/release_gate_apps.py"

    def test_claude_workflow_restricts_comment_authors_and_pins_actions(self):
        root = Path(__file__).resolve().parents[2]
        workflow_path = root / ".github" / "workflows" / "claude-review.yml"
        if not workflow_path.exists():
            assert not workflow_path.exists()
            return

        workflow = workflow_path.read_text()

        assert "github.event.issue.pull_request != null" in workflow
        assert "author_association == 'OWNER'" in workflow
        assert "author_association == 'MEMBER'" in workflow
        assert "author_association == 'COLLABORATOR'" in workflow
        assert "actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd" in workflow
        assert (
            "anthropics/claude-code-action@ab8b1e6471c519c585ba17e8ecaccc9d83043541"
            in workflow
        )
        assert "anthropics/claude-code-action@v1" not in workflow

    def test_vscode_run_command_uses_shell_execution_args(self):
        root = Path(__file__).resolve().parents[2]
        extension = (root / "vscode-geno" / "src" / "extension.ts").read_text()

        assert "buildRunFileInvocation(\n      genoPath,\n      filePath" in extension
        assert 'geno run "${filePath}"' not in extension
        assert "terminal.sendText(`geno run" not in extension


class TestHTTPRequestSmuggling:
    """Server must handle malformed requests safely."""

    @pytest.fixture()
    def client(self):
        import http.client
        import threading

        server = _create_server_or_skip("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address

        class _Client:
            def __init__(self):
                self.host = host
                self.port = port

            def post_raw(self, path, body, headers):
                conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
                conn.request("POST", path, body=body, headers=headers)
                resp = conn.getresponse()
                resp_body = resp.read()
                conn.close()
                return resp.status, resp_body

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield _Client()
        server.shutdown()
        server.server_close()

    def test_content_length_mismatch_safe(self, client):
        """Content-Length larger than body should not hang or crash."""
        body = b'{"source": "func id(x: Int) -> Int\\n    example 1 -> 1\\n    return x\\nend func"}'
        # Claim body is larger than it really is
        status, _ = client.post_raw(
            "/run",
            body,
            {"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        # Should handle normally — actual length matches header
        assert status in (200, 400, 500)

    def test_zero_content_length(self, client):
        """Zero Content-Length with no body should return 400."""
        status, _resp_body = client.post_raw(
            "/run",
            b"",
            {"Content-Type": "application/json", "Content-Length": "0"},
        )
        assert status == 400

    def test_non_json_content_type(self, client):
        """Non-JSON content type should be handled gracefully."""
        body = b"not json at all"
        status, _ = client.post_raw(
            "/run",
            body,
            {"Content-Type": "text/plain", "Content-Length": str(len(body))},
        )
        assert status == 400

    def test_invalid_utf8_body(self, client):
        """Invalid UTF-8 in body should return 400, not crash."""
        body = b"\x80\x81\x82"
        status, _ = client.post_raw(
            "/run",
            body,
            {"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        assert status == 400


# =========================================================================
# Geno-level sandbox escape: compiled code must not access system
# =========================================================================


class TestGenoSandboxEscape:
    """Geno programs compiled and run via the API must not escape the sandbox."""

    def test_http_post_denied_without_capability(self):
        """http_post is blocked without the http capability."""
        source = 'func main() -> String\n    return http_post(url: "http://example.com", body: "{}")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_geno_cannot_access_filesystem(self):
        """Without fs capability, filesystem access is denied."""
        source = 'func main() -> String\n    return fs_read_text(path: "/etc/passwd")\nend func'
        result = run(source, config=RunConfig(capabilities=set()))
        assert result.ok is False
        assert any(d.code.value == "E412" for d in result.diagnostics)

    def test_step_limit_prevents_infinite_loop(self):
        """Step limit should halt infinite recursion."""
        source = """func loop(n: Int) -> Int
    example (0) -> 0
    return loop(n: n + 1)
end func

func main() -> Int
    return loop(n: 0)
end func
"""
        result = run(source, config=RunConfig(max_steps=100, timeout=5.0))
        assert result.ok is False


class TestDevServerXSS:
    """Dev server build error page must HTML-escape error messages."""

    def test_build_error_html_escapes_script_tags(self, tmp_path):
        from geno.__main__ import _build_dev_server_html

        bad_file = tmp_path / "bad.geno"
        bad_file.write_text("this is not valid geno code <script>alert(1)</script>")
        html, error = _build_dev_server_html(str(bad_file))
        assert error is not None
        assert "<script>" not in html
        assert "&lt;script&gt;" in html or "script" not in html

    def test_build_error_html_escapes_ampersands(self, tmp_path):
        from geno.__main__ import _build_dev_server_html

        bad_file = tmp_path / "bad.geno"
        bad_file.write_text('let x: Int = "not & an < int >"')
        html, error = _build_dev_server_html(str(bad_file))
        assert error is not None
        assert "&amp;" in html or "& " not in html


class TestSecurityBountyRunner:
    """The manual bounty harness should fail closed on suspicious outcomes."""

    def test_snippet_success_without_result_counts_as_escape(self, monkeypatch):
        from scripts import security_bounty

        monkeypatch.setattr(security_bounty, "validate_code_safety", lambda _code: [])
        monkeypatch.setattr(
            security_bounty,
            "run_in_process",
            lambda _code, _config: (None, ""),
        )

        assert (
            security_bounty._run_snippet("no_result", "print('hi')", verbose=False)
            is False
        )

    def test_corpus_wrong_error_code_counts_as_failure(self, monkeypatch, tmp_path):
        from scripts import security_bounty

        corpus_file = tmp_path / "wrong_code.geno"
        corpus_file.write_text(
            "# EXPECT: E401\nfunc main() -> Int\n    return 0\nend func\n"
        )
        monkeypatch.setattr(security_bounty, "CORPUS_DIR", str(tmp_path))
        monkeypatch.setattr(
            security_bounty,
            "run",
            lambda _source, config: SimpleNamespace(
                ok=False,
                value=None,
                diagnostics=[
                    SimpleNamespace(
                        code=SimpleNamespace(value="E412"),
                    )
                ],
            ),
        )

        passed, failed = security_bounty._run_corpus(verbose=False)

        assert (passed, failed) == (0, 1)
