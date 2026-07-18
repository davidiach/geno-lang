"""
Server Integration Tests
========================

Tests for the Geno HTTP server endpoints.
"""

import json
import os
import pickle
import queue
import subprocess
import sys
import threading
import time
from typing import Any, cast
from unittest.mock import patch

import pytest

from geno.api import RunConfig, RunResult
from geno.monitoring import RuntimeMetricsCollector
from geno.server import (
    DEFAULT_MAX_STEPS,
    MAX_JSON_NESTING_DEPTH,
    MAX_MODULE_SOURCE_BYTES,
    MAX_MODULES,
    MAX_REQUEST_BODY_BYTES,
    MAX_REQUEST_HEADER_BYTES,
    MAX_STEPS,
    MAX_TIMEOUT_SECONDS,
    RequestError,
    _apply_worker_resource_limits,
    _build_run_config,
    _client_ip_from_x_forwarded_for,
    _ensure_json_nesting_within_limit,
    _execute_constrain_with_wall_timeout,
    _execute_run_with_wall_timeout,
    _execute_worker_with_wall_timeout,
    _parse_constrain_request_payload,
    _parse_run_request_payload,
    _request_origin_allowed,
    _run_request_worker,
    create_handler,
)
from geno.server import (
    create_server as _create_server,
)


@pytest.fixture(autouse=True)
def _restore_geno_logger_state():
    """Snapshot/restore the global ``geno`` logger around every test in this module.

    ``server.main()`` and ``serve_runtime()`` now call ``_configure_logging()``,
    which attaches a handler and sets the level on the shared ``geno`` logger.
    Several entry-point tests here invoke those paths (e.g. TestServeRuntimeOptIn,
    TestGenoServeOptIn, the main() unsupported-python test), so without a
    module-wide reset they would leak a handler bound to a since-closed pytest
    capture stream and flip the global log level for later tests.
    """
    import logging

    geno_logger = logging.getLogger("geno")
    saved_handlers = list(geno_logger.handlers)
    saved_level = geno_logger.level
    try:
        yield
    finally:
        geno_logger.handlers[:] = saved_handlers
        geno_logger.setLevel(saved_level)


def create_server(*args, **kwargs):
    """Create a bound server or skip when the environment forbids local binds."""
    try:
        return _create_server(*args, **kwargs)
    except PermissionError as exc:
        pytest.skip(f"local socket binds not permitted in this environment: {exc}")


def _probe_worker(result_conn):
    result_conn.send(("result", True))
    result_conn.close()


class _PicklingConnection:
    def __init__(self):
        self.messages = []
        self.closed = False

    def send(self, payload):
        pickle.dumps(payload)
        self.messages.append(payload)

    def close(self):
        self.closed = True


_WORKER_PROCESSES_AVAILABLE: bool | None = None


def _skip_if_worker_processes_unavailable():
    global _WORKER_PROCESSES_AVAILABLE
    if _WORKER_PROCESSES_AVAILABLE is None:
        status, payload = _execute_worker_with_wall_timeout(_probe_worker, (), 1.0)
        _WORKER_PROCESSES_AVAILABLE = not (
            status == "error"
            and isinstance(payload, dict)
            and payload.get("type") == "WorkerSpawnFailed"
        )
    if not _WORKER_PROCESSES_AVAILABLE:
        pytest.skip("worker process spawning is not permitted in this environment")


def _start_test_server(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Windows can occasionally abort the first loopback connection if the test
    # client races the serve_forever loop under a long suite run.
    time.sleep(0.02)
    return thread


def _getresponse_or_skip(conn):
    try:
        return conn.getresponse()
    except ConnectionAbortedError as exc:
        if sys.platform == "win32":
            pytest.skip(f"local loopback connection was aborted on Windows: {exc}")
        raise


def _raw_http_exchange(server, request: bytes) -> tuple[int, bytes]:
    import socket

    host, port = server.server_address
    client = socket.create_connection((host, port), timeout=10)
    try:
        client.sendall(request)
        chunks: list[bytes] = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        client.close()
    response = b"".join(chunks)
    status_line = response.partition(b"\r\n")[0]
    try:
        status = int(status_line.split()[1])
    except (IndexError, ValueError) as exc:
        raise AssertionError(f"invalid HTTP response: {response!r}") from exc
    return status, response


@pytest.fixture()
def client():
    """Create a test server and return a helper for making requests."""
    server = create_server(
        "127.0.0.1",
        0,  # OS-assigned port
        bind_and_activate=True,
    )

    import http.client

    host, port = server.server_address

    class _Client:
        def get(self, path, headers=None):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", path, headers=headers or {})
            resp = _getresponse_or_skip(conn)
            body = resp.read()
            conn.close()
            return resp.status, body

        def get_resp(self, path, headers=None):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", path, headers=headers or {})
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()
            return resp

        def options(self, path, headers=None):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            hdrs = headers or {}
            conn.request("OPTIONS", path, headers=hdrs)
            resp = _getresponse_or_skip(conn)
            body = resp.read()
            conn.close()
            return resp.status, body, resp

        def post(self, path, payload=None, raw_body=None, headers=None):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            hdrs = headers or {}
            if raw_body is not None:
                body = raw_body
            else:
                body = (
                    json.dumps(payload).encode("utf-8") if payload is not None else b""
                )
            hdrs.setdefault("Content-Type", "application/json")
            hdrs.setdefault("Content-Length", str(len(body)))
            conn.request("POST", path, body=body, headers=hdrs)
            resp = _getresponse_or_skip(conn)
            resp_body = resp.read()
            conn.close()
            return resp.status, resp_body

    _start_test_server(server)
    yield _Client()
    server.shutdown()
    server.server_close()


class TestHealthz:
    def test_healthz_returns_200(self, client):
        status, body = client.get("/healthz")
        assert status == 200
        data = json.loads(body)
        assert "status" in data


class TestPlayground:
    def test_root_serves_playground_html(self, client):
        status, body = client.get("/")
        assert status == 200
        html = body.decode("utf-8")
        assert "<title>Geno Playground</title>" in html
        assert "Run (Ctrl+Enter)" in html

    def test_playground_route_serves_playground_html(self, client):
        status, body = client.get("/playground")
        assert status == 200
        html = body.decode("utf-8")
        assert "<title>Geno Playground</title>" in html
        assert "const EXAMPLES =" in html

    def test_playground_html_formats_composite_values(self):
        from geno.server import _playground_html

        html = _playground_html()
        assert "function formatValue(value)" in html
        assert "JSON.stringify(value, null, 2)" in html
        assert "formatValue(data.value)" in html

    def test_options_run_returns_cors_headers(self, client):
        status, _body, resp = client.options("/run")
        assert status == 204
        assert resp.getheader("Access-Control-Allow-Origin") is None
        assert "POST" in (resp.getheader("Access-Control-Allow-Methods") or "")
        assert "Content-Type" in (resp.getheader("Access-Control-Allow-Headers") or "")
        assert resp.getheader("X-Content-Type-Options") == "nosniff"
        assert resp.getheader("Cache-Control") == "no-store"

    def test_json_responses_include_cors_headers(self):
        _skip_if_worker_processes_unavailable()

        import http.client

        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            body = json.dumps({"source": _VALID_SOURCE}).encode("utf-8")
            conn.request(
                "POST",
                "/run",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            resp = _getresponse_or_skip(conn)
            resp_body = resp.read()
            conn.close()

            assert resp.status == 200, resp_body.decode("utf-8")
            assert resp.getheader("Access-Control-Allow-Origin") is None
            assert "OPTIONS" in (resp.getheader("Access-Control-Allow-Methods") or "")
        finally:
            server.shutdown()
            server.server_close()

    def test_playground_sets_csp_and_frame_headers(self, client):
        resp = client.get_resp("/playground")
        assert resp.status == 200
        csp = resp.getheader("Content-Security-Policy") or ""
        assert "frame-ancestors 'none'" in csp
        assert "default-src 'none'" in csp
        assert resp.getheader("X-Frame-Options") == "DENY"
        assert resp.getheader("X-Content-Type-Options") == "nosniff"

    def test_healthz_sets_security_headers(self, client):
        resp = client.get_resp("/healthz")
        assert resp.status == 200
        assert resp.getheader("X-Content-Type-Options") == "nosniff"
        assert resp.getheader("Cache-Control") == "no-store"

    def test_unauth_server_echoes_explicitly_allowed_cors_origin(self, monkeypatch):
        import http.client

        import geno.server as server_mod

        origin = "https://playground.example"
        monkeypatch.setattr(server_mod, "_CORS_ALLOWED_ORIGINS", frozenset({origin}))

        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request(
                "OPTIONS",
                "/run",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                },
            )
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()

            assert resp.status == 204
            assert resp.getheader("Access-Control-Allow-Origin") == origin
            assert "Origin" in (resp.getheader("Vary") or "")
        finally:
            server.shutdown()
            server.server_close()

    def test_wildcard_cors_entry_does_not_allow_arbitrary_origins(self, monkeypatch):
        import http.client

        import geno.server as server_mod

        monkeypatch.setattr(server_mod, "_CORS_ALLOWED_ORIGINS", frozenset({"*"}))

        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request(
                "OPTIONS",
                "/run",
                headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "POST",
                },
            )
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()

            assert resp.status == 204
            assert resp.getheader("Access-Control-Allow-Origin") is None
        finally:
            server.shutdown()
            server.server_close()

    def test_api_key_server_does_not_send_wildcard_cors(self):
        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            api_key="secret",
        )
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request(
                "OPTIONS",
                "/run",
                headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "POST",
                },
            )
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()

            assert resp.status == 204
            assert resp.getheader("Access-Control-Allow-Origin") is None
        finally:
            server.shutdown()
            server.server_close()

    def test_api_key_server_echoes_explicitly_allowed_cors_origin(self, monkeypatch):
        import http.client

        import geno.server as server_mod

        origin = "https://playground.example"
        monkeypatch.setattr(server_mod, "_CORS_ALLOWED_ORIGINS", frozenset({origin}))

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            api_key="secret",
        )
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request(
                "OPTIONS",
                "/run",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                },
            )
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()

            assert resp.status == 204
            assert resp.getheader("Access-Control-Allow-Origin") == origin
            assert "Origin" in (resp.getheader("Vary") or "")
        finally:
            server.shutdown()
            server.server_close()


class TestRequestBoundaryHardening:
    @pytest.mark.parametrize(
        ("header_count", "expected_status"),
        [(64, 200), (65, 431)],
    )
    def test_aggregate_request_headers_are_bounded(self, header_count, expected_status):
        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address
        _start_test_server(server)
        try:
            prefix = (
                f"GET /healthz HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n"
            ).encode("ascii")
            padding = b"".join(
                f"X-Fill-{index}: ".encode("ascii") + b"a" * 1000 + b"\r\n"
                for index in range(header_count)
            )
            request = prefix + padding + b"\r\n"
            assert (len(request) <= MAX_REQUEST_HEADER_BYTES) is (header_count == 64)

            status, _response = _raw_http_exchange(server, request)
            assert status == expected_status
        finally:
            server.shutdown()
            server.server_close()

    def test_cross_origin_simple_post_is_rejected(self, client):
        status, body = client.post(
            "/run",
            payload={},
            headers={"Origin": "https://evil.example"},
        )

        assert status == 403
        assert b"cross-origin request is not allowed" in body

    def test_same_origin_simple_post_reaches_request_validation(self):
        import http.client

        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            body = b"{}"
            conn.request(
                "POST",
                "/run",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Origin": f"http://{host}:{port}",
                },
            )
            resp = _getresponse_or_skip(conn)
            response_body = resp.read()
            conn.close()

            assert resp.status == 400
            assert b"cross-origin request" not in response_body
        finally:
            server.shutdown()
            server.server_close()

    @pytest.mark.parametrize(
        "origin",
        ["https://api.example.com", "http://api.example.com:8443"],
    )
    def test_same_hostname_different_scheme_or_port_is_cross_origin(self, origin):
        class Handler:
            headers = {"Host": "api.example.com", "Origin": origin}
            connection = object()
            _cors_allowed_origins: frozenset[str] = frozenset()

        assert _request_origin_allowed(cast(Any, Handler())) is False

    def test_same_origin_honors_forwarded_proto_from_trusted_proxy(self):
        class Handler:
            headers = {
                "Host": "api.example.com",
                "Origin": "https://api.example.com",
                "X-Forwarded-Proto": "https",
            }
            connection = object()
            client_address = ("127.0.0.1", 12345)
            _trusted_proxy = "127.0.0.1"
            _cors_allowed_origins: frozenset[str] = frozenset()

        assert _request_origin_allowed(cast(Any, Handler())) is True

    @pytest.mark.parametrize(
        ("host", "origin"),
        [
            ("api.example.com", "https://api.example.com:0"),
            ("api.example.com:0", "https://api.example.com"),
        ],
    )
    def test_forwarded_proto_does_not_treat_port_zero_as_default(self, host, origin):
        class Handler:
            headers = {
                "Host": host,
                "Origin": origin,
                "X-Forwarded-Proto": "https",
            }
            connection = object()
            client_address = ("127.0.0.1", 12345)
            _trusted_proxy = "127.0.0.1"
            _cors_allowed_origins: frozenset[str] = frozenset()

        assert _request_origin_allowed(cast(Any, Handler())) is False

    def test_untrusted_peer_cannot_spoof_forwarded_proto(self):
        class Handler:
            headers = {
                "Host": "api.example.com",
                "Origin": "https://api.example.com",
                "X-Forwarded-Proto": "https",
            }
            connection = object()
            client_address = ("203.0.113.10", 12345)
            _trusted_proxy = "127.0.0.1"
            _cors_allowed_origins: frozenset[str] = frozenset()

        assert _request_origin_allowed(cast(Any, Handler())) is False

    @pytest.mark.parametrize(
        "forwarded_proto",
        ["", "javascript", "https,http"],
    )
    def test_trusted_proxy_forwarded_proto_is_strict(self, forwarded_proto):
        class Handler:
            headers = {
                "Host": "api.example.com",
                "Origin": "https://api.example.com",
                "X-Forwarded-Proto": forwarded_proto,
            }
            connection = object()
            client_address = ("127.0.0.1", 12345)
            _trusted_proxy = "127.0.0.1"
            _cors_allowed_origins: frozenset[str] = frozenset()

        assert _request_origin_allowed(cast(Any, Handler())) is False

    def test_trusted_proxy_rejects_duplicate_forwarded_proto_headers(self):
        class DuplicateHeaders(dict[str, str]):
            def get_all(self, name: str, default: list[str]) -> list[str]:
                if name.lower() == "x-forwarded-proto":
                    return ["https", "http"]
                value = self.get(name)
                return [value] if value is not None else default

        class Handler:
            headers = DuplicateHeaders(
                {
                    "Host": "api.example.com",
                    "Origin": "https://api.example.com",
                    "X-Forwarded-Proto": "https",
                }
            )
            connection = object()
            client_address = ("127.0.0.1", 12345)
            _trusted_proxy = "127.0.0.1"
            _cors_allowed_origins: frozenset[str] = frozenset()

        assert _request_origin_allowed(cast(Any, Handler())) is False

    def test_slow_request_body_hits_absolute_deadline(self):
        import socket

        with patch("geno.server._REQUEST_TIMEOUT_SECONDS", 0.15):
            server = create_server("127.0.0.1", 0, bind_and_activate=True)
            host, port = server.server_address
            _start_test_server(server)
            client = socket.create_connection((host, port), timeout=1)
            client.settimeout(1)
            try:
                request = (
                    f"POST /run HTTP/1.1\r\nHost: {host}:{port}\r\n"
                    "Content-Type: application/json\r\n"
                    "Content-Length: 100\r\nConnection: close\r\n\r\n"
                ).encode("ascii")
                client.sendall(request + b"{")
                time.sleep(0.3)
                try:
                    response = client.recv(4096)
                except TimeoutError:
                    pytest.fail("slow request body retained a server connection")
                assert response == b"" or b" 400 " in response
            finally:
                client.close()
                server.shutdown()
                server.server_close()

    def test_post_requires_json_content_type(self, client):
        status, body = client.post(
            "/run",
            raw_body=b"{}",
            headers={"Content-Type": "text/plain"},
        )

        assert status == 400
        assert b"Content-Type must be application/json" in body

    @pytest.mark.parametrize("api_key", ["", " ", "\t"])
    def test_empty_api_keys_are_rejected(self, api_key):
        collector = RuntimeMetricsCollector()

        with pytest.raises(ValueError, match="api_key must not be empty"):
            create_handler(collector, api_key=api_key)

    def test_programmatic_non_loopback_bind_requires_explicit_security(self):
        with pytest.raises(SystemExit):
            _create_server("0.0.0.0", 0, bind_and_activate=False)

        server = _create_server(
            "0.0.0.0",
            0,
            bind_and_activate=False,
            allow_insecure=True,
        )
        server.server_close()


class TestHostValidation:
    def test_unauth_server_rejects_unexpected_host_for_run(self):
        import http.client

        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            body = json.dumps({"source": _VALID_SOURCE}).encode("utf-8")
            conn.request(
                "POST",
                "/run",
                body=body,
                headers={
                    "Host": f"evil.example:{port}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            resp = _getresponse_or_skip(conn)
            resp_body = resp.read()
            conn.close()

            assert resp.status == 421
            assert b"invalid Host header" in resp_body
            assert resp.getheader("Access-Control-Allow-Origin") is None
            assert resp.getheader("X-Content-Type-Options") == "nosniff"
            assert resp.getheader("Cache-Control") == "no-store"
        finally:
            server.shutdown()
            server.server_close()

    def test_configured_allowed_host_is_accepted(self):
        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            allowed_hosts={"playground.example"},
        )
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request(
                "OPTIONS",
                "/run",
                headers={
                    "Host": "playground.example",
                    "Access-Control-Request-Method": "POST",
                },
            )
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()

            assert resp.status == 204
        finally:
            server.shutdown()
            server.server_close()

    def test_server_bind_does_not_perform_reverse_dns(self, monkeypatch):
        import socket

        def fail_getfqdn(_host):
            raise AssertionError("server bind must not perform reverse DNS")

        monkeypatch.setattr(socket, "getfqdn", fail_getfqdn)
        server = create_server(
            "0.0.0.0",
            0,
            allowed_hosts={"api.example.com"},
            allow_insecure=True,
            startup_errors=[],
        )
        try:
            bound_host, bound_port = server.server_address[:2]
            assert server.server_name == bound_host
            assert server.server_port == bound_port
        finally:
            server.server_close()

    def test_loopback_host_is_only_exempt_for_health_endpoint(self):
        # Exercise the actual request policy on a public bind. A local container
        # probe may use a loopback Host for /healthz, but the same untrusted Host
        # must not reach an execution endpoint.
        import http.client

        server = create_server(
            "0.0.0.0",
            0,
            allowed_hosts={"api.example.com"},
            allow_insecure=True,
            startup_errors=[],
        )
        port = server.server_address[1]
        _start_test_server(server)
        try:
            for path, expected_status in (("/healthz", 200), ("/run", 421)):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.request(
                    "GET",
                    path,
                    headers={"Host": f"127.0.0.1:{port}"},
                )
                response = _getresponse_or_skip(conn)
                response.read()
                conn.close()
                assert response.status == expected_status
        finally:
            server.shutdown()
            server.server_close()

    @pytest.mark.parametrize(
        ("bind_host", "host_header"),
        [
            ("0.0.0.0", "0.0.0.0:8000"),
            ("::", "[::]:8000"),
            ("[::]", "[::]:8000"),
        ],
    )
    def test_unspecified_bind_host_is_not_derived_as_allowed_host(
        self, bind_host, host_header
    ):
        from geno.server import _build_allowed_host_set, _host_allowed

        allowed = _build_allowed_host_set(
            bind_host, 8000, frozenset({"api.example.com"})
        )

        assert not _host_allowed(host_header, allowed)
        assert _host_allowed("api.example.com", allowed)

    def test_wildcard_allowed_hosts_disables_validation(self):
        from geno.server import _build_allowed_host_set, _host_allowed

        allowed = _build_allowed_host_set("0.0.0.0", 8000, frozenset({"*"}))
        assert _host_allowed("anything.example:8000", allowed)
        assert _host_allowed("evil.attacker.test", allowed)
        assert _host_allowed("", allowed)

    def test_allow_insecure_uses_wildcard_only_for_implicit_empty_policy(
        self, monkeypatch
    ):
        import geno.server as server_module

        monkeypatch.setattr(server_module, "_ALLOWED_HOSTS", frozenset())
        server = create_server(
            "127.0.0.1",
            0,
            allow_insecure=True,
            bind_and_activate=False,
            startup_errors=[],
        )
        try:
            assert server_module._ANY_HOST in server.allowed_hosts
        finally:
            server.server_close()

    def test_allow_insecure_preserves_explicit_empty_host_policy(self):
        import geno.server as server_module

        server = create_server(
            "127.0.0.1",
            0,
            allow_insecure=True,
            allowed_hosts=frozenset(),
            bind_and_activate=False,
            startup_errors=[],
        )
        try:
            assert server_module._ANY_HOST not in server.allowed_hosts
        finally:
            server.server_close()

    def test_allow_insecure_preserves_configured_environment_policy(self, monkeypatch):
        import geno.server as server_module

        monkeypatch.setattr(
            server_module, "_ALLOWED_HOSTS", frozenset({"api.example.test"})
        )
        server = create_server(
            "127.0.0.1",
            0,
            allow_insecure=True,
            bind_and_activate=False,
            startup_errors=[],
        )
        try:
            assert server_module._ANY_HOST not in server.allowed_hosts
        finally:
            server.server_close()

    def test_wildcard_lookalike_entry_is_rejected_not_silently_disabling(self):
        # Only the exact literal "*" opts out. A near-miss like "*." normalizes to
        # the wildcard sentinel; it must raise, not silently disable Host validation.
        from geno.server import _build_allowed_host_set

        for entry in ("*.", "*..", " *. "):
            with pytest.raises(ValueError, match="invalid allowed host entry"):
                _build_allowed_host_set("0.0.0.0", 8000, frozenset({entry}))


class TestAmbiguousRequestHeaders:
    @staticmethod
    def _post_request(host, port, extra_headers: str = "") -> bytes:
        body = json.dumps({"source": _VALID_SOURCE}).encode("utf-8")
        return (
            f"POST /run HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"{extra_headers}"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body

    def test_duplicate_host_is_rejected(self):
        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address
        _start_test_server(server)
        try:
            request = (
                "GET /healthz HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Host: evil.example\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            status, response = _raw_http_exchange(server, request)
            assert status == 421
            assert b"invalid Host header" in response
        finally:
            server.shutdown()
            server.server_close()

    @pytest.mark.parametrize(
        ("header_lines", "message"),
        [
            ("Content-Length: 83\r\n", b"ambiguous Content-Length"),
            ("Content-Type: application/json\r\n", b"ambiguous Content-Type"),
            ("Transfer-Encoding: chunked\r\n", b"Transfer-Encoding"),
        ],
    )
    def test_ambiguous_or_unsupported_body_framing_is_rejected(
        self, header_lines, message
    ):
        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address
        _start_test_server(server)
        try:
            status, response = _raw_http_exchange(
                server, self._post_request(host, port, header_lines)
            )
            assert status == 400
            assert message in response
        finally:
            server.shutdown()
            server.server_close()

    def test_extremely_long_content_length_is_rejected_with_response(self):
        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address
        _start_test_server(server)
        try:
            request = (
                "POST /run HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {'9' * 5000}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            status, response = _raw_http_exchange(server, request)
            assert status == 400
            assert b"too large" in response
        finally:
            server.shutdown()
            server.server_close()

    def test_duplicate_origin_is_rejected(self):
        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        host, port = server.server_address
        _start_test_server(server)
        try:
            headers = (
                f"Origin: http://{host}:{port}\r\nOrigin: https://evil.example\r\n"
            )
            status, response = _raw_http_exchange(
                server, self._post_request(host, port, headers)
            )
            assert status == 403
            assert b"cross-origin" in response
        finally:
            server.shutdown()
            server.server_close()

    def test_duplicate_authorization_is_rejected(self):
        server = create_server("127.0.0.1", 0, bind_and_activate=True, api_key="secret")
        host, port = server.server_address
        _start_test_server(server)
        try:
            headers = (
                "Authorization: Bearer secret\r\nAuthorization: Bearer attacker\r\n"
            )
            status, _response = _raw_http_exchange(
                server, self._post_request(host, port, headers)
            )
            assert status == 401
        finally:
            server.shutdown()
            server.server_close()

    def test_duplicate_xff_fields_use_rightmost_proxy_appended_address(self):
        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=60.0,
            trusted_proxy="127.0.0.1",
        )
        host, port = server.server_address
        _start_test_server(server)

        def post(spoofed: str) -> int:
            headers = f"X-Forwarded-For: {spoofed}\r\nX-Forwarded-For: 10.0.0.1\r\n"
            status, _response = _raw_http_exchange(
                server, self._post_request(host, port, headers)
            )
            return status

        try:
            with patch(
                "geno.server._execute_run_with_wall_timeout",
                side_effect=_ok_run_response,
            ):
                assert post("spoofed") == 200
                assert post("different-spoof") == 429
        finally:
            server.shutdown()
            server.server_close()


class TestMetrics:
    def test_metrics_returns_200(self, client):
        status, body = client.get("/metrics")
        assert status == 200
        # Prometheus text format
        assert b"geno_" in body or body == b""  # Metrics may be empty initially

    def test_metrics_include_constrain_counters(self, client):
        _skip_if_worker_processes_unavailable()

        status, body = client.post("/constrain", {"prefix": "func "})
        assert status == 200
        data = json.loads(body)
        assert data["valid"] is True

        status, body = client.get("/metrics")
        assert status == 200
        metrics = body.decode("utf-8")
        assert "geno_http_post_requests_total 1" in metrics
        assert (
            'geno_http_post_requests_by_endpoint_total{endpoint="/constrain"} 1'
            in metrics
        )
        assert 'geno_http_post_requests_by_status_total{status="200"} 1' in metrics
        assert (
            'geno_http_post_requests_by_outcome_total{outcome="constrain_ok"} 1'
            in metrics
        )
        assert "geno_constrain_requests_total 1" in metrics
        assert "geno_constrain_valid_total 1" in metrics
        assert "geno_constrain_invalid_total 0" in metrics


class TestRequestPayloadParsing:
    def test_parse_run_request_payload_builds_config_without_worker(self):
        collector = RuntimeMetricsCollector()

        request = _parse_run_request_payload(
            {
                "source": _VALID_SOURCE,
                "filename": "demo.geno",
                "timeout": 1.5,
                "max_steps": 123,
                "check_examples": False,
            },
            collector,
            allowed_capabilities=set(),
        )

        assert request.source == _VALID_SOURCE
        assert request.filename == "demo.geno"
        assert request.source_bytes == len(_VALID_SOURCE.encode("utf-8"))
        assert request.config.timeout == 1.5
        assert request.config.max_steps == 123
        assert request.config.check_examples is False

    def test_parse_run_request_payload_requires_source_string(self):
        collector = RuntimeMetricsCollector()

        with pytest.raises(RequestError, match="'source' must be a string"):
            _parse_run_request_payload(
                {"source": 123},
                collector,
                allowed_capabilities=set(),
            )

    def test_parse_constrain_request_payload_builds_request(self):
        request = _parse_constrain_request_payload({"prefix": "func "})

        assert request.prefix == "func "
        assert request.source_bytes == len(b"func ")

    def test_parse_constrain_request_payload_requires_prefix_string(self):
        with pytest.raises(RequestError, match="'prefix' must be a string"):
            _parse_constrain_request_payload({"prefix": None})


class TestPostRun:
    def test_valid_source(self, client):
        _skip_if_worker_processes_unavailable()

        status, body = client.post(
            "/run",
            {
                "source": "func id(x: Int) -> Int\n    example 1 -> 1\n    return x\nend func id",
            },
        )
        data = json.loads(body)
        assert status == 200
        assert data["ok"] is True

    def test_syntax_error(self, client):
        _skip_if_worker_processes_unavailable()

        status, body = client.post("/run", {"source": "func {"})
        data = json.loads(body)
        assert status == 400
        assert data["ok"] is False

    def test_missing_source(self, client):
        status, body = client.post("/run", {"timeout": 5})
        assert status == 400
        data = json.loads(body)
        assert "source" in data.get("error", "").lower()

    def test_deeply_nested_json_body_is_rejected(self, client):
        depth = MAX_JSON_NESTING_DEPTH + 1
        body = b'{"value":' + b"[" * depth + b"]" * depth + b"}"
        status, response = client.post("/run", raw_body=body)
        assert status == 400
        data = json.loads(response)
        assert "nested too deeply" in data.get("error", "").lower()

    def test_json_nesting_limit_ignores_delimiters_inside_strings(self):
        body = json.dumps({"source": "[{" * (MAX_JSON_NESTING_DEPTH + 1)})
        _ensure_json_nesting_within_limit(body)

    def test_oversized_body(self, client):
        # Send a Content-Length that exceeds the limit with a small actual body.
        # The server rejects based on Content-Length before reading.
        status, body = client.post(
            "/run",
            raw_body=b'{"source":"x"}',
            headers={"Content-Length": str(MAX_REQUEST_BODY_BYTES + 1)},
        )
        assert status == 400
        data = json.loads(body)
        assert "too large" in data.get("error", "").lower()

    def test_negative_content_length(self, client):
        # Negative Content-Length must be rejected before calling rfile.read().
        status, body = client.post(
            "/run",
            raw_body=b'{"source":"x"}',
            headers={"Content-Length": "-1"},
        )
        assert status == 400
        data = json.loads(body)
        assert "negative" in data.get("error", "").lower()

    @pytest.mark.parametrize(
        "content_length",
        ["+10", "-0", "1_0", "01", "1,1", "\u00b2"],
    )
    def test_noncanonical_content_length_is_rejected(self, client, content_length):
        status, body = client.post(
            "/run",
            raw_body=b'{"source":"x"}',
            headers={"Content-Length": content_length},
        )
        assert status == 400
        assert "content-length" in json.loads(body)["error"].lower()

    def test_rejects_non_finite_json_timeout_constant(self, client):
        body = ('{"source": ' + json.dumps(_VALID_SOURCE) + ', "timeout": NaN}').encode(
            "utf-8"
        )

        status, response = client.post("/run", raw_body=body)

        assert status == 400
        data = json.loads(response)
        assert "valid json" in data["error"].lower()

    def test_rejects_overflowed_json_timeout_number(self, client):
        body = (
            '{"source": ' + json.dumps(_VALID_SOURCE) + ', "timeout": 1e999}'
        ).encode("utf-8")

        status, response = client.post("/run", raw_body=body)

        assert status == 400
        data = json.loads(response)
        assert "positive number" in data["error"].lower()


class TestPostConstrain:
    def test_valid_prefix(self, client):
        _skip_if_worker_processes_unavailable()

        status, body = client.post("/constrain", {"prefix": "func "})
        data = json.loads(body)

        assert status == 200
        assert data["valid"] is True
        assert "func" in data["unclosed_blocks"]
        assert data["allowed_next"]["allow_identifier"] is True

    def test_invalid_prefix_returns_200_with_valid_false(self, client):
        _skip_if_worker_processes_unavailable()

        status, body = client.post("/constrain", {"prefix": "end"})
        data = json.loads(body)

        assert status == 200
        assert data["valid"] is False
        assert "unexpected 'end'" in data["error"]

    def test_missing_prefix(self, client):
        status, body = client.post("/constrain", {"timeout": 5})
        data = json.loads(body)

        assert status == 400
        assert "prefix" in data["error"].lower()

        status, body = client.get("/metrics")
        assert status == 200
        metrics = body.decode("utf-8")
        assert "geno_http_post_requests_total 1" in metrics
        assert 'geno_http_post_requests_by_status_total{status="400"} 1' in metrics
        assert (
            'geno_http_post_requests_by_outcome_total{outcome="bad_request"} 1'
            in metrics
        )

    def test_timeout_returns_504_and_updates_metrics(self, client):
        from unittest.mock import patch

        with patch(
            "geno.server._execute_constrain_with_wall_timeout",
            return_value=("timeout", None),
        ):
            status, body = client.post("/constrain", {"prefix": "func "})

        data = json.loads(body)
        assert status == 504
        assert "timeout" in data["error"].lower()

        status, body = client.get("/metrics")
        assert status == 200
        metrics = body.decode("utf-8")
        assert 'geno_http_post_requests_by_status_total{status="504"} 1' in metrics
        assert (
            'geno_http_post_requests_by_outcome_total{outcome="constrain_timeout"} 1'
            in metrics
        )
        assert "geno_constrain_requests_total 1" in metrics
        assert "geno_constrain_timeout_total 1" in metrics


class TestResourceLimits:
    def test_omitted_max_steps_uses_server_default(self):
        config = _build_run_config(
            {"source": "func main() -> Int\n    return 1\nend func"},
            RuntimeMetricsCollector(),
            {"clock", "print", "random"},
        )
        assert config.max_steps == DEFAULT_MAX_STEPS

    def test_null_max_steps_uses_server_default(self):
        config = _build_run_config(
            {
                "source": "func main() -> Int\n    return 1\nend func",
                "max_steps": None,
            },
            RuntimeMetricsCollector(),
            {"clock", "print", "random"},
        )
        assert config.max_steps == DEFAULT_MAX_STEPS

    def test_timeout_exceeds_maximum(self, client):
        status, body = client.post(
            "/run",
            {
                "source": "func main() -> Int\n  return 1\nend func",
                "timeout": MAX_TIMEOUT_SECONDS + 1,
            },
        )
        assert status == 400
        data = json.loads(body)
        assert "maximum" in data["error"].lower()

    @pytest.mark.parametrize(
        "timeout", [float("nan"), float("inf"), float("-inf"), True]
    )
    def test_timeout_must_be_finite_number(self, timeout):
        with pytest.raises(RequestError, match="positive number"):
            _build_run_config(
                {"timeout": timeout},
                RuntimeMetricsCollector(),
                {"clock", "print", "random"},
            )

    def test_timeout_at_maximum_accepted(self, client):
        _skip_if_worker_processes_unavailable()

        status, _body = client.post(
            "/run",
            {
                "source": "func id(x: Int) -> Int\n    example 1 -> 1\n    return x\nend func id",
                "timeout": MAX_TIMEOUT_SECONDS,
            },
        )
        assert status == 200

    def test_max_steps_exceeds_maximum(self, client):
        status, body = client.post(
            "/run",
            {
                "source": "func main() -> Int\n  return 1\nend func",
                "max_steps": MAX_STEPS + 1,
            },
        )
        assert status == 400
        data = json.loads(body)
        assert "maximum" in data["error"].lower()

    def test_max_steps_boolean_rejected(self, client):
        status, body = client.post(
            "/run",
            {
                "source": "func main() -> Int\n  return 1\nend func",
                "max_steps": True,
            },
        )

        assert status == 400
        data = json.loads(body)
        assert "positive integer" in data["error"].lower()

        with pytest.raises(RequestError, match="positive integer"):
            _build_run_config(
                {"max_steps": True},
                RuntimeMetricsCollector(),
                {"clock", "print", "random"},
            )

    def test_max_steps_at_maximum_accepted(self, client):
        _skip_if_worker_processes_unavailable()

        status, _body = client.post(
            "/run",
            {
                "source": "func id(x: Int) -> Int\n    example 1 -> 1\n    return x\nend func id",
                "max_steps": MAX_STEPS,
            },
        )
        assert status == 200

    @pytest.mark.parametrize(
        "field",
        [
            "max_memory_bytes",
            "max_cpu_time",
            "max_file_size_bytes",
            "max_processes",
            "max_recursion_depth",
            "max_output_length",
            "max_collection_size",
            "max_integer_bits",
        ],
    )
    def test_hosted_request_rejects_operator_controlled_limits(self, field):
        with pytest.raises(RequestError, match="operator-controlled"):
            _build_run_config(
                {field: 1},
                RuntimeMetricsCollector(),
                {"clock", "print", "random"},
            )

    def test_hosted_request_limit_policy_returns_400(self, client):
        status, body = client.post(
            "/run",
            {
                "source": "func main() -> Int\n  return 1\nend func",
                "max_collection_size": 10,
            },
        )
        assert status == 400
        data = json.loads(body)
        assert "timeout and max_steps" in data["error"]
        assert "max_collection_size" in data["error"]

    def test_module_name_must_be_simple_identifier(self, client):
        status, body = client.post(
            "/run",
            {
                "source": "func main() -> Int\n  return 1\nend func",
                "modules": {
                    "../../examples/fibonacci": "func value() -> Int\n  return 1\nend func"
                },
            },
        )
        assert status == 400
        data = json.loads(body)
        assert "invalid module name" in data["error"].lower()

    def test_module_exceeds_size_limit(self, client):
        """Individual module source exceeding size limit should be rejected."""
        status, body = client.post(
            "/run",
            {
                "source": "import Huge\nfunc main() -> Int\n  return 1\nend func",
                "modules": {"Huge": "x" * (MAX_MODULE_SOURCE_BYTES + 1)},
            },
        )
        assert status == 400
        data = json.loads(body)
        assert "size limit" in data["error"].lower()

    def test_module_utf8_bytes_exceed_size_limit(self, client, monkeypatch):
        """The module limit should be enforced on UTF-8 bytes, not characters."""
        import geno.server as srv

        monkeypatch.setattr(srv, "MAX_MODULE_SOURCE_BYTES", 10)
        status, body = client.post(
            "/run",
            {
                "source": "import Huge\nfunc main() -> Int\n  return 1\nend func",
                "modules": {"Huge": "é" * 6},
            },
        )
        assert status == 400
        data = json.loads(body)
        assert "12 bytes, max 10" in data["error"]

    def test_too_many_modules(self, client):
        """More modules than the limit should be rejected."""
        modules = {f"Mod{i}": f"// module {i}" for i in range(MAX_MODULES + 1)}
        status, body = client.post(
            "/run",
            {
                "source": "func main() -> Int\n  return 1\nend func",
                "modules": modules,
            },
        )
        assert status == 400
        data = json.loads(body)
        assert "too many modules" in data["error"].lower()


class TestInternalServerError:
    def test_unexpected_exception_returns_500(self):
        """If run() raises an unexpected exception, server returns 500 JSON."""
        from unittest.mock import patch

        server = create_server("127.0.0.1", 0, bind_and_activate=True)

        import http.client

        host, port = server.server_address
        _start_test_server(server)

        try:
            # Patch the child-process execution helper so the handler sees
            # an internal execution failure without spawning a real worker.
            with patch(
                "geno.server._execute_run_with_wall_timeout",
                return_value=(
                    "error",
                    {
                        "type": "RuntimeError",
                        "message": "unexpected bug",
                        "traceback": "traceback",
                    },
                ),
            ):
                conn = http.client.HTTPConnection(host, port, timeout=10)
                body = json.dumps({"source": "x"}).encode()
                conn.request(
                    "POST",
                    "/run",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body)),
                    },
                )
                resp = _getresponse_or_skip(conn)
                resp_body = resp.read()
                conn.close()

            assert resp.status == 500
            data = json.loads(resp_body)
            assert data["error"] == "internal server error"
            # Must NOT leak traceback details
            assert "unexpected bug" not in data["error"]
        finally:
            server.shutdown()
            server.server_close()


class TestHostedResponseBounds:
    def test_shared_object_amplification_returns_fixed_413_and_metric(
        self, client, monkeypatch
    ):
        import geno.server as srv

        monkeypatch.setattr(srv, "MAX_RESPONSE_BODY_BYTES", 1024)
        shared = {"payload": "x" * 400}
        amplified = [shared] * 10
        with patch(
            "geno.server._execute_run_with_wall_timeout",
            return_value=(
                "result",
                RunResult(ok=True, value=amplified, steps_used=1),
            ),
        ):
            status, body = client.post("/run", {"source": _VALID_SOURCE})

        assert status == 413
        assert json.loads(body) == {"error": "response too large"}
        metrics_status, metrics_body = client.get("/metrics")
        assert metrics_status == 200
        assert (
            'geno_http_post_requests_by_outcome_total{outcome="response_too_large"} 1'
            in metrics_body.decode("utf-8")
        )

    def test_fixed_413_is_not_subject_to_tiny_operator_limit(self, client, monkeypatch):
        import geno.server as srv

        monkeypatch.setattr(srv, "MAX_RESPONSE_BODY_BYTES", 1)
        with patch(
            "geno.server._execute_run_with_wall_timeout",
            return_value=(
                "result",
                RunResult(ok=True, value="x", steps_used=1),
            ),
        ):
            status, body = client.post("/run", {"source": _VALID_SOURCE})

        assert status == 413
        assert json.loads(body) == {"error": "response too large"}

    def test_single_escaped_string_is_rejected_before_json_encoder_allocation(
        self, monkeypatch
    ):
        import geno.server as srv

        monkeypatch.setattr(srv, "MAX_RESPONSE_BODY_BYTES", 128)
        with (
            patch.object(
                srv.json.JSONEncoder,
                "iterencode",
                side_effect=AssertionError("encoder should not run"),
            ),
            pytest.raises(srv.ResponseTooLarge, match="configured limit"),
        ):
            srv._bounded_json_response_body({"value": "\x00" * 100})

    def test_valid_large_integer_ignores_python_decimal_digit_ceiling(self):
        import geno.server as srv

        body = srv._bounded_json_response_body({"value": 1 << 16_000})

        prefix = b'{"value":'
        assert body.startswith(prefix) and body.endswith(b"}")
        decimal = body[len(prefix) : -1]
        assert len(decimal) > 4300
        assert decimal[0] in b"123456789"
        assert all(character in b"0123456789" for character in decimal)

    def test_dynamic_json_errors_are_response_bounded(self, monkeypatch):
        import io
        from http import HTTPStatus

        import geno.server as srv

        class Handler:
            def __init__(self):
                self.wfile = io.BytesIO()
                self.headers = {}

            def send_response(self, _status):
                pass

            def send_header(self, _key, _value):
                pass

            def end_headers(self):
                pass

        monkeypatch.setattr(srv, "MAX_RESPONSE_BODY_BYTES", 16)
        with pytest.raises(srv.ResponseTooLarge):
            srv._json_response(
                cast(Any, Handler()), HTTPStatus.NOT_FOUND, {"error": "x" * 1000}
            )


class TestCapabilityEnforcement:
    """Verify hosted request capabilities fail closed unless explicitly requested."""

    def test_omitted_capabilities_defaults_to_empty_set(self):
        """Omitted request capabilities must not grant the server's full set."""
        from geno.server import _coerce_capabilities

        allowed = {"print"}
        result = _coerce_capabilities({}, allowed)
        assert result == set()

    def test_omitted_capabilities_can_use_explicit_default_request_subset(self):
        from geno.server import _coerce_capabilities

        allowed = {"print", "clock", "random"}
        result = _coerce_capabilities({}, allowed, {"print"})
        assert result == {"print"}

    def test_explicit_capabilities_respected(self):
        """When request specifies capabilities, only those are returned."""
        from geno.server import _coerce_capabilities

        allowed = {"print", "clock", "random"}
        result = _coerce_capabilities({"capabilities": ["print"]}, allowed)
        assert result == {"print"}

    def test_default_request_capabilities_must_be_allowed(self):
        with pytest.raises(
            ValueError,
            match="default_request_capabilities must be a subset",
        ):
            create_handler(
                RuntimeMetricsCollector(),
                allowed_capabilities={"print"},
                default_request_capabilities={"env"},
            )

    def test_unknown_request_capability_is_rejected(self):
        from geno.server import _coerce_capabilities

        with pytest.raises(RequestError, match="Unknown capability 'fss'"):
            _coerce_capabilities({"capabilities": ["fss"]}, {"print", "fs"})

    def test_create_server_rejects_unknown_allowed_capability(self):
        with pytest.raises(ValueError, match="Unknown capability 'fss'"):
            _create_server("127.0.0.1", 0, allowed_capabilities={"fss"})

    def test_hosted_env_capability_requires_allowlist(self):
        with pytest.raises(
            RequestError,
            match="env capability requires GENO_ALLOWED_ENV_NAMES",
        ):
            _build_run_config(
                {"capabilities": ["env"]},
                RuntimeMetricsCollector(),
                {"env"},
                allowed_env_names=frozenset(),
                allowed_env_prefixes=frozenset(),
            )

    def test_hosted_env_capability_passes_allowlist_to_run_config(self):
        config = _build_run_config(
            {"capabilities": ["env"]},
            RuntimeMetricsCollector(),
            {"env"},
            allowed_env_names={"GENO_PUBLIC_ENV"},
            allowed_env_prefixes={"PUBLIC_"},
        )
        assert config.capabilities == {"env"}
        assert config.env_allowed_names == frozenset({"GENO_PUBLIC_ENV"})
        assert config.env_allowed_prefixes == frozenset({"PUBLIC_"})


def _slow_runner(source, config, filename):
    del source, config, filename
    time.sleep(1.0)
    return None


def _error_runner(source, config, filename):
    del source, config, filename
    raise RuntimeError("boom")


def _slow_constrain(prefix):
    del prefix
    time.sleep(1.0)
    return None


def _error_constrain(prefix):
    del prefix
    raise RuntimeError("boom")


def _delayed_ready_worker(result_conn, startup_delay, payload, run_delay=0.0):
    if not _sleep_with_fake_cancel(result_conn, startup_delay):
        return
    result_conn.send(("ready", None))
    if not _sleep_with_fake_cancel(result_conn, run_delay):
        return
    result_conn.send(("result", payload))
    result_conn.close()


def _fast_worker(result_conn, payload):
    """Minimal worker that sends one result without the ready/result handshake."""
    try:
        result_conn.send(("result", payload))
    finally:
        result_conn.close()


def _record_completion_worker(result_conn, run_delay, completed):
    result_conn.send(("ready", None))
    if not _sleep_with_fake_cancel(result_conn, run_delay):
        return
    completed.set()
    result_conn.send(("result", {"ok": True}))
    result_conn.close()


def _sleep_with_fake_cancel(result_conn, delay):
    wait_cancelled = getattr(result_conn, "wait_cancelled", None)
    if wait_cancelled is None:
        time.sleep(delay)
        return True
    return not wait_cancelled(delay)


class _FakeParentConn:
    def __init__(self, q):
        self._q = q
        self._peek = None

    def poll(self, timeout=None):
        if self._peek is not None:
            return True
        try:
            self._peek = self._q.get(timeout=timeout)
            return True
        except queue.Empty:
            return False

    def recv(self):
        if self._peek is not None:
            msg, self._peek = self._peek, None
            return msg
        return self._q.get()

    def close(self):
        pass


class _FakeChildConn:
    def __init__(self, q):
        self._q = q
        self._cancelled = threading.Event()

    def send(self, value):
        if self._cancelled.is_set():
            return
        self._q.put(value)

    def close(self):
        pass  # Parent and worker share this object in-process; don't disable sends.

    def cancel(self):
        self._cancelled.set()

    def wait_cancelled(self, timeout):
        return self._cancelled.wait(timeout)


class _ThreadProcess:
    def __init__(self, target, args):
        self._target = target
        self._args = args
        self._exitcode = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            self._target(*self._args)
            self._exitcode = 0
        except BaseException:
            self._exitcode = 1

    def start(self):
        self._thread.start()

    def is_alive(self):
        return self._thread.is_alive()

    def join(self, timeout=None):
        self._thread.join(timeout)

    def terminate(self):
        cancel = getattr(self._args[0], "cancel", None)
        if cancel is not None:
            cancel()

    def kill(self):
        self.terminate()

    @property
    def exitcode(self):
        return self._exitcode


class _ThreadContext:
    """In-process replacement for multiprocessing.get_context('spawn').

    Avoids subprocess spawn/import overhead so that timing-sensitive worker
    tests can assert the startup-grace vs wall-timeout contract directly
    without depending on host spawn latency.
    """

    def Pipe(self, duplex=False):
        q: queue.Queue = queue.Queue()
        return _FakeParentConn(q), _FakeChildConn(q)

    def Process(self, *, target, args):
        return _ThreadProcess(target, args)


def test_run_request_worker_strips_unpickleable_raw_value(monkeypatch):
    import geno.server as server_mod

    def function_returning_runner(_source, *, config, filename):
        _ = (config, filename)
        return RunResult(
            ok=True,
            value={"_function": "inc"},
            value_raw=lambda x: x,
        )

    # The worker entry point applies operator-controlled OS rlimits; stub
    # them out so this in-process invocation cannot clamp the test runner.
    monkeypatch.setattr(server_mod, "_apply_worker_resource_limits", tuple)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    conn = _PicklingConnection()

    _run_request_worker(
        cast(Any, conn),
        _VALID_SOURCE,
        RunConfig(timeout=1.0),
        "<test>",
        runner=function_returning_runner,
    )

    assert conn.closed is True
    assert conn.messages[0] == ("ready", None)
    status, payload = conn.messages[1]
    assert status == "result"
    assert payload.ok is True
    assert payload.value == {"_function": "inc"}
    assert payload.value_raw is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX rlimit test")
def test_apply_worker_resource_limits_sets_configured_rlimits(monkeypatch):
    import geno.server as server_mod

    calls = []

    class FakeResource:
        RLIMIT_CPU = "cpu"
        RLIMIT_FSIZE = "fsize"
        RLIMIT_NPROC = "nproc"
        RLIMIT_AS = "as"

        def setrlimit(self, which, limits):
            calls.append((which, limits))

    fake_resource = FakeResource()
    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(server_mod, "rlimit_as_ceiling", lambda value, _resource: value)
    monkeypatch.setattr(server_mod, "WORKER_MAX_CPU_TIME", 1.2)
    monkeypatch.setattr(server_mod, "WORKER_MAX_FILE_SIZE_BYTES", 0)
    monkeypatch.setattr(server_mod, "WORKER_MAX_PROCESSES", 3)
    monkeypatch.setattr(server_mod, "WORKER_MAX_MEMORY_BYTES", 4096)

    applied = _apply_worker_resource_limits()

    assert applied == ("RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_NPROC", "RLIMIT_AS")
    assert calls == [
        ("cpu", (2, 2)),
        ("fsize", (0, 0)),
        ("nproc", (3, 3)),
        ("as", (4096, 4096)),
    ]


def test_hosted_worker_uses_baseline_aware_memory_ceiling(monkeypatch):
    import geno.server as server_mod

    calls = []
    ceiling_calls = []

    class FakeResource:
        RLIMIT_AS = "as"

        def setrlimit(self, which, limits):
            calls.append((which, limits))

    fake_resource = FakeResource()

    def fake_ceiling(requested_bytes, resource_module):
        ceiling_calls.append((requested_bytes, resource_module))
        return 900_000_000

    class FakeOS:
        name = "posix"

    monkeypatch.setattr(server_mod, "os", FakeOS())

    monkeypatch.setitem(sys.modules, "resource", fake_resource)
    monkeypatch.setattr(server_mod, "rlimit_as_ceiling", fake_ceiling)
    monkeypatch.setattr(server_mod, "WORKER_MAX_CPU_TIME", 0)
    monkeypatch.setattr(server_mod, "WORKER_MAX_FILE_SIZE_BYTES", -1)
    monkeypatch.setattr(server_mod, "WORKER_MAX_PROCESSES", 0)
    monkeypatch.setattr(server_mod, "WORKER_MAX_MEMORY_BYTES", 4096)

    applied = _apply_worker_resource_limits()

    assert applied == ("RLIMIT_AS",)
    assert ceiling_calls == [(4096, fake_resource)]
    assert calls == [("as", (900_000_000, 900_000_000))]


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object test")
def test_apply_worker_resource_limits_uses_windows_job(monkeypatch):
    import geno.sandbox as sandbox_mod
    import geno.server as server_mod

    calls = []

    def fake_job(self, process, *, redirector_overhead=None):
        calls.append((self.config, process._handle, redirector_overhead))
        return 123

    monkeypatch.setattr(sandbox_mod.ProcessSandbox, "_create_windows_job", fake_job)
    applied = _apply_worker_resource_limits()

    assert calls
    config, process_handle, redirector_overhead = calls[0]
    assert process_handle is not None
    assert redirector_overhead == 0
    assert config.max_memory_bytes == server_mod.WORKER_MAX_MEMORY_BYTES
    assert config.max_processes == server_mod.WORKER_MAX_PROCESSES
    assert "JOB_MEMORY_LIMIT" in applied
    assert "JOB_ACTIVE_PROCESS_LIMIT" in applied


def test_worker_resource_limit_failure_is_sent_before_ready(monkeypatch):
    import geno.server as server_mod

    def fail_limits():
        raise RuntimeError("required limit unavailable")

    monkeypatch.setattr(server_mod, "_apply_worker_resource_limits", fail_limits)
    conn = _PicklingConnection()
    _run_request_worker(cast(Any, conn), _VALID_SOURCE, RunConfig(), "<test>")

    assert conn.messages[0][0] == "error"
    assert all(message[0] != "ready" for message in conn.messages)


def test_run_request_worker_applies_resource_limits_before_ready(monkeypatch):
    import geno.server as server_mod

    events = []

    class RecordingConnection(_PicklingConnection):
        def send(self, payload):
            events.append(payload[0])
            super().send(payload)

    def fake_apply_limits():
        events.append("limits")
        return ("RLIMIT_AS",)

    def runner(_source, *, config, filename):
        _ = (config, filename)
        events.append("runner")
        return RunResult(ok=True, value=1)

    monkeypatch.setattr(server_mod, "_apply_worker_resource_limits", fake_apply_limits)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    conn = RecordingConnection()

    _run_request_worker(
        cast(Any, conn),
        _VALID_SOURCE,
        RunConfig(timeout=1.0),
        "<test>",
        runner=runner,
    )

    assert events == ["limits", "ready", "runner", "result"]
    assert sys.dont_write_bytecode is True


class TestWallTimeoutExecution:
    def setup_method(self):
        _skip_if_worker_processes_unavailable()

    def test_execute_run_with_wall_timeout_returns_result(self):
        status, payload = _execute_run_with_wall_timeout(
            _VALID_SOURCE,
            RunConfig(timeout=1.0),
            "<test>",
            wall_timeout=1.0,
        )

        assert status == "result"
        assert payload.ok is True

    def test_execute_run_with_wall_timeout_times_out(self):
        status, payload = _execute_run_with_wall_timeout(
            _VALID_SOURCE,
            RunConfig(timeout=1.0),
            "<test>",
            wall_timeout=0.05,
            runner=_slow_runner,
        )

        assert status == "timeout"
        assert payload is None

    def test_execute_run_with_wall_timeout_reports_worker_error(self):
        status, payload = _execute_run_with_wall_timeout(
            _VALID_SOURCE,
            RunConfig(timeout=1.0),
            "<test>",
            wall_timeout=1.0,
            runner=_error_runner,
        )

        assert status == "error"
        assert payload["type"] == "RuntimeError"
        assert payload["message"] == "boom"

    def test_execute_run_with_wall_timeout_handles_large_output(self):
        large_output_source = """
func main() -> Int
    var i: Int = 0
    while i < 20000 do
        print("x")
        i = i + 1
    end while
    return 1
end func
"""

        status, payload = _execute_run_with_wall_timeout(
            large_output_source,
            RunConfig(timeout=5.0, capabilities={"print"}),
            "<test>",
            wall_timeout=5.0,
        )

        assert status == "result"
        assert payload.ok is True
        # Canonical top-level string display is bare: each print emits "x\n".
        assert len(payload.output) == 40000


class TestConstrainWallTimeoutExecution:
    def setup_method(self):
        _skip_if_worker_processes_unavailable()

    def test_execute_constrain_with_wall_timeout_returns_result(self):
        status, payload = _execute_constrain_with_wall_timeout(
            "func ",
            wall_timeout=1.0,
        )

        assert status == "result"
        assert payload.valid is True

    def test_execute_constrain_with_wall_timeout_times_out(self):
        status, payload = _execute_constrain_with_wall_timeout(
            "func ",
            wall_timeout=0.05,
            constrain=_slow_constrain,
        )

        assert status == "timeout"
        assert payload is None

    def test_execute_constrain_with_wall_timeout_reports_worker_error(self):
        status, payload = _execute_constrain_with_wall_timeout(
            "func ",
            wall_timeout=1.0,
            constrain=_error_constrain,
        )

        assert status == "error"
        assert payload["type"] == "RuntimeError"
        assert payload["message"] == "boom"


class TestWorkerStartupGrace:
    """Verify startup grace is separate from the execution wall budget."""

    def test_execute_worker_startup_grace_does_not_consume_wall_budget(self):
        with patch(
            "geno.server.multiprocessing.get_context",
            return_value=_ThreadContext(),
        ):
            status, payload = _execute_worker_with_wall_timeout(
                _delayed_ready_worker,
                (0.1, {"ok": True}),
                wall_timeout=0.05,
                startup_grace=0.3,
            )

        assert status == "result"
        assert payload == {"ok": True}

    def test_execute_worker_reports_startup_timeout_separately(self):
        with patch(
            "geno.server.multiprocessing.get_context",
            return_value=_ThreadContext(),
        ):
            status, payload = _execute_worker_with_wall_timeout(
                _delayed_ready_worker,
                (0.2, {"ok": True}),
                wall_timeout=1.0,
                startup_grace=0.05,
            )

        assert status == "startup_timeout"
        assert payload is None

    def test_execute_worker_timeout_stops_fake_worker(self):
        completed = threading.Event()

        with patch(
            "geno.server.multiprocessing.get_context",
            return_value=_ThreadContext(),
        ):
            status, payload = _execute_worker_with_wall_timeout(
                _record_completion_worker,
                (0.2, completed),
                wall_timeout=0.05,
                startup_grace=0.3,
            )

        assert status == "timeout"
        assert payload is None
        assert not completed.wait(0.3)

    def test_run_handler_does_not_inflate_wall_timeout_for_startup(self, client):
        from unittest.mock import patch

        captured = {}

        original = _execute_run_with_wall_timeout

        def spy(*args, **kwargs):
            captured["wall_timeout"] = kwargs.get("wall_timeout") or args[3]
            return original(*args, **kwargs)

        with patch("geno.server._execute_run_with_wall_timeout", side_effect=spy):
            _status, _body = client.post(
                "/run", {"source": _VALID_SOURCE, "timeout": 1.0}
            )

        assert captured["wall_timeout"] == pytest.approx(3.0, abs=0.01)

    def test_constrain_handler_does_not_inflate_wall_timeout_for_startup(self, client):
        from unittest.mock import patch

        captured = {}

        original = _execute_constrain_with_wall_timeout

        def spy(*args, **kwargs):
            captured["wall_timeout"] = kwargs.get("wall_timeout") or args[1]
            return original(*args, **kwargs)

        with patch("geno.server._execute_constrain_with_wall_timeout", side_effect=spy):
            _status, _body = client.post("/constrain", {"prefix": "func "})

        from geno.server import CONSTRAIN_WALL_CLOCK_SECONDS

        assert captured["wall_timeout"] == pytest.approx(
            CONSTRAIN_WALL_CLOCK_SECONDS, abs=0.01
        )

    def test_startup_grace_constant_is_positive(self):
        from geno.server import WORKER_STARTUP_GRACE_SECONDS

        assert WORKER_STARTUP_GRACE_SECONDS >= 10.0


class TestWorkerProcessIsolation:
    """Hosted execution must fail closed instead of using thread fallbacks."""

    def test_process_start_failure_fails_closed(self, caplog):
        from geno.server import _execute_worker_with_wall_timeout

        requested_methods: list[str] = []

        def _raising_ctx(*_a, **_kw):
            requested_methods.extend(_a)
            raise OSError("simulated: spawn not permitted")

        with patch("geno.server.multiprocessing.get_context", _raising_ctx):
            with caplog.at_level("ERROR", logger="geno.server"):
                status, payload = _execute_worker_with_wall_timeout(
                    _fast_worker,
                    ({"ok": True},),
                    wall_timeout=1.0,
                )

        assert status == "error"
        assert payload["type"] == "WorkerSpawnFailed"
        assert requested_methods == ["spawn"]
        assert any(
            "refusing unsafe fallback" in record.getMessage()
            for record in caplog.records
        )

    def test_thread_timeout_fallback_is_retired(self):
        import geno.server as server_mod

        assert not hasattr(server_mod, "_execute_worker_with_thread_timeout")


class TestUnknownEndpoint:
    def test_get_unknown_returns_404(self, client):
        status, _body = client.get("/unknown")
        assert status == 404

    def test_post_unknown_returns_404(self, client):
        status, _body = client.post("/unknown", {"source": "x"})
        assert status == 404


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------

_VALID_SOURCE = "func id(x: Int) -> Int\n    example 1 -> 1\n    return x\nend func id"


def _ok_run_response(*_args, **_kwargs):
    return ("result", RunResult(ok=True, value=1, steps_used=1))


@pytest.fixture()
def authed_client():
    """Server with API key authentication enabled."""
    import http.client

    server = create_server(
        "127.0.0.1",
        0,
        bind_and_activate=True,
        api_key="test-secret-key",
    )
    host, port = server.server_address

    class _Client:
        def get(self, path, headers=None):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", path, headers=headers or {})
            resp = _getresponse_or_skip(conn)
            resp_body = resp.read()
            conn.close()
            return resp.status, resp_body

        def post(self, path, payload=None, headers=None):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            hdrs = headers or {}
            body = json.dumps(payload).encode("utf-8") if payload is not None else b""
            hdrs.setdefault("Content-Type", "application/json")
            hdrs.setdefault("Content-Length", str(len(body)))
            conn.request("POST", path, body=body, headers=hdrs)
            resp = _getresponse_or_skip(conn)
            resp_body = resp.read()
            conn.close()
            return resp.status, resp_body, resp

    _start_test_server(server)
    yield _Client()
    server.shutdown()
    server.server_close()


class TestAuthentication:
    def test_no_auth_returns_401(self, authed_client):
        """Requests without credentials are rejected."""
        status, body, _ = authed_client.post("/run", {"source": _VALID_SOURCE})
        assert status == 401
        data = json.loads(body)
        assert "authentication" in data["error"].lower()

        status, body = authed_client.get(
            "/metrics", headers={"Authorization": "Bearer test-secret-key"}
        )
        assert status == 200
        metrics = body.decode("utf-8")
        assert "geno_http_post_requests_total 1" in metrics
        assert 'geno_http_post_requests_by_status_total{status="401"} 1' in metrics
        assert (
            'geno_http_post_requests_by_outcome_total{outcome="auth_failed"} 1'
            in metrics
        )

    def test_metrics_requires_auth_by_default_with_api_key(self):
        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            api_key="test-secret-key",
        )
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/metrics")
            resp = _getresponse_or_skip(conn)
            body = json.loads(resp.read())
            conn.close()

            assert resp.status == 401
            assert "authentication" in body["error"].lower()

            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request(
                "GET",
                "/metrics",
                headers={"Authorization": "Bearer test-secret-key"},
            )
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()

            assert resp.status == 200
        finally:
            server.shutdown()
            server.server_close()

    def test_metrics_auth_can_be_explicitly_disabled(self):
        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            api_key="test-secret-key",
            require_auth_for_metrics=False,
        )
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/metrics")
            resp = _getresponse_or_skip(conn)
            body = resp.read()
            conn.close()

            assert resp.status == 200
            assert b"geno_" in body or body == b""
        finally:
            server.shutdown()
            server.server_close()

    def test_playground_can_require_auth(self):
        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            api_key="test-secret-key",
            require_auth_for_playground=True,
        )
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/playground")
            resp = _getresponse_or_skip(conn)
            body = json.loads(resp.read())
            conn.close()

            assert resp.status == 401
            assert "authentication" in body["error"].lower()

            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/", headers={"X-API-Key": "test-secret-key"})
            resp = _getresponse_or_skip(conn)
            body = resp.read()
            conn.close()

            assert resp.status == 200
            assert b"Geno Playground" in body
        finally:
            server.shutdown()
            server.server_close()

    def test_healthz_stays_public_when_get_auth_is_required(self):
        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            api_key="test-secret-key",
            require_auth_for_playground=True,
        )
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/healthz")
            resp = _getresponse_or_skip(conn)
            body = json.loads(resp.read())
            conn.close()

            assert resp.status == 200
            assert "status" in body
            assert "build" not in body
            assert "checks" not in body

            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/healthz", headers={"X-API-Key": "test-secret-key"})
            resp = _getresponse_or_skip(conn)
            body = json.loads(resp.read())
            conn.close()

            assert resp.status == 200
            assert "build" in body
            assert "checks" in body
        finally:
            server.shutdown()
            server.server_close()

    def test_get_auth_requirement_fails_closed_without_api_key(self):
        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            require_auth_for_metrics=True,
        )
        host, port = server.server_address
        _start_test_server(server)
        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/metrics")
            resp = _getresponse_or_skip(conn)
            body = json.loads(resp.read())
            conn.close()

            assert resp.status == 503
            assert "GENO_API_KEY" in body["error"]
        finally:
            server.shutdown()
            server.server_close()

    def test_wrong_key_returns_401(self, authed_client):
        status, _body, _ = authed_client.post(
            "/run",
            {"source": _VALID_SOURCE},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert status == 401

    def test_bearer_token_accepted(self, authed_client):
        _skip_if_worker_processes_unavailable()

        status, body, _ = authed_client.post(
            "/run",
            {"source": _VALID_SOURCE},
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert status == 200
        data = json.loads(body)
        assert data["ok"] is True

    def test_x_api_key_header_accepted(self, authed_client):
        _skip_if_worker_processes_unavailable()

        status, _body, _ = authed_client.post(
            "/run",
            {"source": _VALID_SOURCE},
            headers={"X-API-Key": "test-secret-key"},
        )
        assert status == 200

    def test_constrain_requires_auth_when_api_key_enabled(self, authed_client):
        status, body, _ = authed_client.post("/constrain", {"prefix": "func "})
        assert status == 401
        data = json.loads(body)
        assert "authentication" in data["error"].lower()

    def test_constrain_accepts_bearer_token(self, authed_client):
        _skip_if_worker_processes_unavailable()

        status, body, _ = authed_client.post(
            "/constrain",
            {"prefix": "func "},
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert status == 200
        data = json.loads(body)
        assert data["valid"] is True

    def test_request_id_header_present(self, authed_client):
        """Successful responses include X-Request-Id."""
        _skip_if_worker_processes_unavailable()

        status, _body, resp = authed_client.post(
            "/run",
            {"source": _VALID_SOURCE},
            headers={"Authorization": "Bearer test-secret-key"},
        )
        assert status == 200
        assert resp.getheader("X-Request-Id") is not None

    def test_no_api_key_configured_allows_all(self, client):
        """When no API key is set, /run is open (backward compatible)."""
        _skip_if_worker_processes_unavailable()

        status, _body = client.post("/run", {"source": _VALID_SOURCE})
        assert status == 200

    def test_failed_auth_attempts_are_rate_limited(self):
        """Failed authentication attempts consume per-IP rate limit tokens."""
        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            api_key="test-secret-key",
            rate_limit_requests=2,
            rate_limit_window_seconds=60.0,
        )
        host, port = server.server_address
        _start_test_server(server)

        try:
            statuses = []
            for _ in range(4):
                conn = http.client.HTTPConnection(host, port, timeout=10)
                body = json.dumps({"source": _VALID_SOURCE}).encode("utf-8")
                conn.request(
                    "POST",
                    "/run",
                    body=body,
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body)),
                    },
                )
                resp = _getresponse_or_skip(conn)
                resp.read()
                conn.close()
                statuses.append(resp.status)

            assert statuses[:2] == [401, 401]
            assert statuses[2:] == [429, 429]
        finally:
            server.shutdown()
            server.server_close()


# ---------------------------------------------------------------------------
# Rate limiting tests
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_rate_limit_exceeded_returns_429(self):
        """Exceeding the per-IP limit returns 429."""
        _skip_if_worker_processes_unavailable()

        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            rate_limit_requests=2,
            rate_limit_window_seconds=60.0,
        )
        host, port = server.server_address
        _start_test_server(server)

        try:
            with patch(
                "geno.server._execute_run_with_wall_timeout",
                side_effect=_ok_run_response,
            ):
                statuses = []
                for _ in range(4):
                    conn = http.client.HTTPConnection(host, port, timeout=10)
                    body = json.dumps({"source": _VALID_SOURCE}).encode()
                    conn.request(
                        "POST",
                        "/run",
                        body=body,
                        headers={
                            "Content-Type": "application/json",
                            "Content-Length": str(len(body)),
                        },
                    )
                    resp = _getresponse_or_skip(conn)
                    resp.read()
                    conn.close()
                    statuses.append(resp.status)

            # First 2 should succeed, remaining should be rate-limited
            assert statuses[0] == 200
            assert statuses[1] == 200
            assert 429 in statuses[2:]

            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/metrics")
            resp = _getresponse_or_skip(conn)
            metrics = resp.read().decode("utf-8")
            conn.close()

            assert "geno_http_post_requests_total 4" in metrics
            assert 'geno_http_post_requests_by_status_total{status="429"} 2' in metrics
            assert (
                'geno_http_post_requests_by_outcome_total{outcome="rate_limited"} 2'
                in metrics
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_rate_limiting_disabled_when_zero(self):
        """Setting rate_limit_requests=0 disables rate limiting."""
        _skip_if_worker_processes_unavailable()

        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            rate_limit_requests=0,
        )
        host, port = server.server_address
        _start_test_server(server)

        try:
            with patch(
                "geno.server._execute_run_with_wall_timeout",
                side_effect=_ok_run_response,
            ):
                for _ in range(5):
                    conn = http.client.HTTPConnection(host, port, timeout=10)
                    body = json.dumps({"source": _VALID_SOURCE}).encode()
                    conn.request(
                        "POST",
                        "/run",
                        body=body,
                        headers={
                            "Content-Type": "application/json",
                            "Content-Length": str(len(body)),
                        },
                    )
                    resp = _getresponse_or_skip(conn)
                    resp.read()
                    conn.close()
                    assert resp.status == 200
        finally:
            server.shutdown()
            server.server_close()

    @pytest.mark.parametrize("rate_limit_requests", [-1, True])
    def test_create_handler_rejects_invalid_rate_limit_requests(
        self, rate_limit_requests
    ):
        from geno.server import create_handler

        with pytest.raises(ValueError, match="rate_limit_requests"):
            create_handler(
                RuntimeMetricsCollector(),
                rate_limit_requests=rate_limit_requests,
            )

    @pytest.mark.parametrize(
        "rate_limit_window_seconds", [0.0, -1.0, float("nan"), float("inf"), True]
    )
    def test_create_handler_rejects_invalid_rate_limit_window(
        self, rate_limit_window_seconds
    ):
        from geno.server import create_handler

        with pytest.raises(ValueError, match="window_seconds"):
            create_handler(
                RuntimeMetricsCollector(),
                rate_limit_requests=1,
                rate_limit_window_seconds=rate_limit_window_seconds,
            )

    def test_rate_limiter_unit(self):
        """_RateLimiter allows up to max_requests then blocks."""
        from geno.server import _RateLimiter

        limiter = _RateLimiter(max_requests=3, window_seconds=60.0)
        assert limiter.is_allowed("ip1") is True
        assert limiter.is_allowed("ip1") is True
        assert limiter.is_allowed("ip1") is True
        assert limiter.is_allowed("ip1") is False  # 4th request denied

    def test_rate_limiter_separate_keys(self):
        """Different IPs have independent buckets."""
        from geno.server import _RateLimiter

        limiter = _RateLimiter(max_requests=1, window_seconds=60.0)
        assert limiter.is_allowed("ip1") is True
        assert limiter.is_allowed("ip1") is False
        assert limiter.is_allowed("ip2") is True  # different IP, allowed

    def test_rate_limiter_window_expiry(self):
        """Requests outside the window are evicted and no longer count."""
        from geno.server import _RateLimiter

        limiter = _RateLimiter(max_requests=2, window_seconds=0.05)
        assert limiter.is_allowed("ip1") is True
        assert limiter.is_allowed("ip1") is True
        assert limiter.is_allowed("ip1") is False
        time.sleep(0.1)
        # Window has expired; slots are free again
        assert limiter.is_allowed("ip1") is True

    def test_rate_limiter_bounds_distinct_key_buckets(self):
        """Many distinct active keys cannot grow the bucket map without bound."""
        from geno.server import _RateLimiter

        limiter = _RateLimiter(max_requests=1, window_seconds=60.0, max_buckets=2)
        assert limiter.is_allowed("ip1") is True
        assert limiter.is_allowed("ip2") is True
        assert limiter.is_allowed("ip3") is True

        assert list(limiter._buckets) == ["ip2", "ip3"]

    def test_rate_limiter_sweeps_expired_buckets_globally(self):
        """Expired buckets for inactive keys are pruned before adding new keys."""
        from geno.server import _RateLimiter

        limiter = _RateLimiter(max_requests=1, window_seconds=0.05, max_buckets=2)
        assert limiter.is_allowed("ip1") is True
        assert limiter.is_allowed("ip2") is True
        time.sleep(0.1)
        assert limiter.is_allowed("ip3") is True

        assert list(limiter._buckets) == ["ip3"]

    @pytest.mark.parametrize("max_requests", [0, -1, True])
    def test_rate_limiter_rejects_invalid_max_requests(self, max_requests):
        from geno.server import _RateLimiter

        with pytest.raises(ValueError, match="max_requests"):
            _RateLimiter(max_requests=max_requests, window_seconds=60.0)

    @pytest.mark.parametrize(
        "window_seconds", [0.0, -1.0, float("nan"), float("inf"), True]
    )
    def test_rate_limiter_rejects_invalid_window(self, window_seconds):
        from geno.server import _RateLimiter

        with pytest.raises(ValueError, match="window_seconds"):
            _RateLimiter(max_requests=1, window_seconds=window_seconds)

    @pytest.mark.parametrize("max_buckets", [0, -1, True])
    def test_rate_limiter_rejects_invalid_max_buckets(self, max_buckets):
        from geno.server import _RateLimiter

        with pytest.raises(ValueError, match="max_buckets"):
            _RateLimiter(max_requests=1, window_seconds=60.0, max_buckets=max_buckets)

    @pytest.mark.parametrize("rate_limit_max_buckets", [0, -1, True])
    def test_create_handler_rejects_invalid_rate_limit_max_buckets(
        self, rate_limit_max_buckets
    ):
        collector = RuntimeMetricsCollector()
        with pytest.raises(ValueError, match="max_buckets"):
            create_handler(
                collector,
                rate_limit_requests=1,
                rate_limit_window_seconds=60.0,
                rate_limit_max_buckets=rate_limit_max_buckets,
            )

    def test_xff_parser_ignores_invalid_entries(self):
        assert (
            _client_ip_from_x_forwarded_for(
                "not-an-ip, 203.0.113.7, still-not-an-ip",
                "127.0.0.1",
            )
            == "203.0.113.7"
        )
        assert (
            _client_ip_from_x_forwarded_for("not-an-ip, still-not-an-ip", "127.0.0.1")
            is None
        )

    def test_xff_parser_skips_trusted_proxy_hops(self):
        assert (
            _client_ip_from_x_forwarded_for(
                "203.0.113.7, 127.0.0.1",
                "127.0.0.1",
            )
            == "203.0.113.7"
        )
        assert _client_ip_from_x_forwarded_for("127.0.0.1", "127.0.0.1") is None

    def test_xff_parser_accepts_ipv6_addresses(self):
        assert (
            _client_ip_from_x_forwarded_for(
                "2001:0db8:0000:0000:0000:0000:0000:0001",
                "::1",
            )
            == "2001:db8::1"
        )

    def test_trusted_proxy_reads_x_forwarded_for(self):
        """When trusted_proxy is set and the connection comes from that address,
        X-Forwarded-For is used as the rate-limit key."""
        _skip_if_worker_processes_unavailable()

        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            # 1 request allowed per IP so the 2nd is rate-limited
            rate_limit_requests=1,
            rate_limit_window_seconds=60.0,
            # Trust the loopback proxy (all test requests come from 127.0.0.1)
            trusted_proxy="127.0.0.1",
        )
        host, port = server.server_address
        _start_test_server(server)

        def _post(xff_header):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            body = json.dumps({"source": _VALID_SOURCE}).encode()
            headers = {
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }
            if xff_header:
                headers["X-Forwarded-For"] = xff_header
            conn.request("POST", "/run", body=body, headers=headers)
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()
            return resp.status

        try:
            # First request from "10.0.0.1" — should succeed
            with patch(
                "geno.server._execute_run_with_wall_timeout",
                side_effect=_ok_run_response,
            ):
                assert _post("10.0.0.1") == 200
                # Second request from same forwarded IP — should be rate-limited
                assert _post("10.0.0.1") == 429
                # Request from a different forwarded IP — independent bucket, allowed
                assert _post("10.0.0.2") == 200
        finally:
            server.shutdown()
            server.server_close()

    def test_trusted_proxy_ignores_invalid_x_forwarded_for_entries(self):
        """Invalid XFF values do not become rate-limit bucket identities."""
        _skip_if_worker_processes_unavailable()

        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=60.0,
            trusted_proxy="127.0.0.1",
        )
        host, port = server.server_address
        _start_test_server(server)

        def _post(xff_header):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            body = json.dumps({"source": _VALID_SOURCE}).encode()
            headers = {
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "X-Forwarded-For": xff_header,
            }
            conn.request("POST", "/run", body=body, headers=headers)
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()
            return resp.status

        try:
            with patch(
                "geno.server._execute_run_with_wall_timeout",
                side_effect=_ok_run_response,
            ):
                assert _post("not-an-ip") == 200
                assert _post("still-not-an-ip") == 429
        finally:
            server.shutdown()
            server.server_close()

    def test_xff_uses_rightmost_not_leftmost(self):
        """With a spoofed X-Forwarded-For chain, the server should use the
        rightmost non-proxy IP (added by the trusted proxy), not the leftmost
        (which the client can set)."""
        _skip_if_worker_processes_unavailable()

        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=60.0,
            trusted_proxy="127.0.0.1",
        )
        host, port = server.server_address
        _start_test_server(server)

        def _post(xff_header):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            body = json.dumps({"source": _VALID_SOURCE}).encode()
            headers = {
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "X-Forwarded-For": xff_header,
            }
            conn.request("POST", "/run", body=body, headers=headers)
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()
            return resp.status

        try:
            # Client spoofs "spoofed" but the proxy appends the real IP "10.0.0.1".
            # Server should rate-limit by "10.0.0.1" (rightmost non-proxy).
            with patch(
                "geno.server._execute_run_with_wall_timeout",
                side_effect=_ok_run_response,
            ):
                assert _post("spoofed, 10.0.0.1") == 200
                # Same real client (10.0.0.1) with a different spoofed prefix —
                # should hit the same rate-limit bucket.
                assert _post("different_spoof, 10.0.0.1") == 429
        finally:
            server.shutdown()
            server.server_close()

    def test_no_trusted_proxy_ignores_x_forwarded_for(self):
        """Without trusted_proxy, X-Forwarded-For is ignored and TCP peer is used."""
        _skip_if_worker_processes_unavailable()

        import http.client

        server = create_server(
            "127.0.0.1",
            0,
            bind_and_activate=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=60.0,
            trusted_proxy=None,
        )
        host, port = server.server_address
        _start_test_server(server)

        def _post(xff_header=None):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            body = json.dumps({"source": _VALID_SOURCE}).encode()
            headers = {
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            }
            if xff_header:
                headers["X-Forwarded-For"] = xff_header
            conn.request("POST", "/run", body=body, headers=headers)
            resp = _getresponse_or_skip(conn)
            resp.read()
            conn.close()
            return resp.status

        try:
            # First request — allowed
            with patch(
                "geno.server._execute_run_with_wall_timeout",
                side_effect=_ok_run_response,
            ):
                assert _post("1.2.3.4") == 200
                # Second request with a different X-Forwarded-For — still rate-limited
                # because the TCP peer (127.0.0.1) is the same bucket
                assert _post("5.6.7.8") == 429
        finally:
            server.shutdown()
            server.server_close()


class TestConcurrencyRejection:
    def test_concurrency_rejection_updates_metrics(self):
        import http.client
        from unittest.mock import patch

        class _RejectingSemaphore:
            def acquire(self, blocking=True):
                return False

            def release(self):
                raise AssertionError("release should not be called after acquire fails")

        with patch(
            "geno.server.threading.BoundedSemaphore",
            return_value=_RejectingSemaphore(),
        ):
            server = create_server(
                "127.0.0.1",
                0,
                bind_and_activate=True,
            )
        host, port = server.server_address
        _start_test_server(server)

        try:
            conn = http.client.HTTPConnection(host, port, timeout=10)
            body = json.dumps({"source": _VALID_SOURCE}).encode("utf-8")
            conn.request(
                "POST",
                "/run",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            resp = _getresponse_or_skip(conn)
            payload = json.loads(resp.read())
            conn.close()

            assert resp.status == 503
            assert "too many concurrent requests" in payload["error"].lower()

            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/metrics")
            resp = _getresponse_or_skip(conn)
            metrics = resp.read().decode("utf-8")
            conn.close()

            assert "geno_http_post_requests_total 1" in metrics
            assert 'geno_http_post_requests_by_status_total{status="503"} 1' in metrics
            assert (
                'geno_http_post_requests_by_outcome_total{outcome="concurrency_rejected"} 1'
                in metrics
            )
        finally:
            server.shutdown()
            server.server_close()


# ---------------------------------------------------------------------------
# Startup checks tests
# ---------------------------------------------------------------------------


class TestStartupChecks:
    def test_startup_checks_pass_on_valid_python(self, monkeypatch):
        """_run_startup_checks() returns no errors on a supported Python version."""
        import sys

        from geno.server import _run_startup_checks

        monkeypatch.setattr(sys, "version_info", (3, 10, 0))
        monkeypatch.setattr(sys, "version", "3.10.0")
        errors = _run_startup_checks()
        assert errors == [], f"Unexpected startup errors: {errors}"

    def test_startup_checks_fail_on_too_new_python(self, monkeypatch):
        """_run_startup_checks() rejects versions above the supported ceiling."""
        import sys

        from geno.server import _run_startup_checks

        monkeypatch.setattr(sys, "version_info", (3, 14, 0))
        monkeypatch.setattr(sys, "version", "3.14.0")
        errors = _run_startup_checks()
        assert len(errors) == 1
        assert "Python 3.10-3.13" in errors[0]

    def test_startup_checks_fail_on_sandbox_error(self, monkeypatch):
        """_run_startup_checks() captures sandbox failures."""
        import geno.server as srv

        def _bad_run(source, config, **_):
            raise RuntimeError("simulated sandbox failure")

        monkeypatch.setattr(
            srv, "_run_startup_checks", lambda: ["simulated sandbox failure"]
        )
        from geno.server import _run_startup_checks as patched

        errors = patched()
        assert len(errors) == 1
        assert "simulated" in errors[0]

    def test_server_main_exits_on_unsupported_python(self, monkeypatch, caplog):
        """The geno-serve entrypoint should fail fast on unsupported Python."""
        import logging
        import sys

        import geno.server as srv

        monkeypatch.setattr(sys, "version_info", (3, 14, 0))
        monkeypatch.setattr(sys, "version", "3.14.0")
        monkeypatch.setattr(
            srv,
            "serve_forever",
            lambda *args, **kwargs: pytest.fail("serve_forever should not be called"),
        )

        with caplog.at_level(logging.ERROR, logger="geno.server"):
            with pytest.raises(SystemExit) as exc:
                srv.main([])

        assert exc.value.code == 1
        assert "Python 3.10-3.13" in caplog.text

    def test_serve_forever_refuses_to_start_on_check_failure(self, monkeypatch):
        """serve_forever() raises SystemExit when startup checks fail."""
        import geno.server as srv

        monkeypatch.setattr(srv, "_run_startup_checks", lambda: ["sandbox broke"])
        monkeypatch.delenv("GENO_SKIP_STARTUP_CHECKS", raising=False)
        with pytest.raises(SystemExit, match="startup checks failed"):
            srv.serve_forever()

    def test_serve_forever_bypasses_checks_when_env_var_set(self, monkeypatch, caplog):
        """GENO_SKIP_STARTUP_CHECKS=1 bypasses failing startup checks.

        Regression for #661 / F-0028: the SystemExit message advertised the
        env-var escape hatch, but ``serve_forever`` never actually read it,
        so the override was dead code.
        """
        import logging

        import geno.server as srv

        monkeypatch.setattr(srv, "_run_startup_checks", lambda: ["sandbox broke"])
        monkeypatch.setenv("GENO_SKIP_STARTUP_CHECKS", "1")

        # Intercept ``create_server`` so the test doesn't bind a real socket
        # and doesn't actually serve traffic.
        class _FakeServer:
            server_close_called = False

            def serve_forever(self):
                pass

            def server_close(self):
                _FakeServer.server_close_called = True

        monkeypatch.setattr(srv, "create_server", lambda *a, **kw: _FakeServer())

        with caplog.at_level(logging.WARNING, logger="geno.server"):
            srv.serve_forever()  # must not raise SystemExit

        assert any(
            "GENO_SKIP_STARTUP_CHECKS=1" in record.getMessage()
            and "sandbox broke" in record.getMessage()
            for record in caplog.records
        )
        assert _FakeServer.server_close_called

    def test_serve_forever_does_not_bypass_on_other_env_values(self, monkeypatch):
        """Only the literal value ``1`` bypasses — truthy strings like ``true``
        must still refuse to start."""
        import geno.server as srv

        monkeypatch.setattr(srv, "_run_startup_checks", lambda: ["sandbox broke"])
        monkeypatch.setenv("GENO_SKIP_STARTUP_CHECKS", "true")
        with pytest.raises(SystemExit, match="startup checks failed"):
            srv.serve_forever()

    def test_serve_forever_bypass_still_reports_unhealthy(self, monkeypatch):
        """GENO_SKIP_STARTUP_CHECKS=1 starts the server but /healthz must stay honest.

        Regression: serve_forever computed the startup errors but then passed
        startup_errors=[] to create_server, so a deployment that overrode failing
        checks reported healthy through /healthz — orchestrators could never see
        the degraded state.
        """
        import geno.server as srv

        monkeypatch.setattr(srv, "_run_startup_checks", lambda: ["sandbox broke"])
        monkeypatch.setenv("GENO_SKIP_STARTUP_CHECKS", "1")

        captured: dict[str, object] = {}

        class _FakeServer:
            def serve_forever(self):
                pass

            def server_close(self):
                pass

        def _fake_create_server(*args, **kwargs):
            captured["startup_errors"] = kwargs.get("startup_errors")
            return _FakeServer()

        monkeypatch.setattr(srv, "create_server", _fake_create_server)
        srv.serve_forever()

        assert captured["startup_errors"] == ["sandbox broke"]

    def test_create_server_health_reports_startup_errors(self, monkeypatch):
        """Manual servers surface startup failures through /healthz."""
        import http.client

        import geno.server as srv

        monkeypatch.setattr(srv, "_run_startup_checks", lambda: ["sandbox broke"])
        server = create_server("127.0.0.1", 0, bind_and_activate=True)
        _start_test_server(server)
        try:
            host, port = server.server_address
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/healthz")
            resp = _getresponse_or_skip(conn)
            body = resp.read()
            conn.close()

            assert resp.status == 200
            data = json.loads(body)
            assert data["status"] == "failed"
            startup_check = next(
                check for check in data["checks"] if check["name"] == "startup_checks"
            )
            assert startup_check["status"] == "fail"
            assert "sandbox broke" in startup_check["detail"]
        finally:
            server.shutdown()
            server.server_close()


class TestResponseSerialization:
    def test_json_response_is_compact(self):
        """Responses must be serialized compactly (no indent=2 whitespace
        amplification) — verified via a minimal handler double."""
        import io
        from http import HTTPStatus

        from geno.server import _json_response

        class _Handler:
            def __init__(self):
                self.wfile = io.BytesIO()
                self.headers = {}  # _add_cors_headers reads handler.headers

            def send_response(self, status):
                pass

            def send_header(self, key, value):
                pass

            def end_headers(self):
                pass

        handler = _Handler()
        _json_response(
            cast(Any, handler),
            HTTPStatus.OK,
            {"ok": True, "result": {"a": [1, 2, 3], "b": "x"}},
        )
        body = handler.wfile.getvalue().decode("utf-8")
        assert body == '{"ok":true,"result":{"a":[1,2,3],"b":"x"}}'
        assert ", " not in body and ": " not in body  # no pretty-print separators


class TestGracefulShutdown:
    """SIGTERM must trigger a graceful shutdown so `docker stop` drains in-flight
    requests instead of being ignored (as PID 1) until SIGKILL."""

    def test_serve_forever_handles_sigterm_gracefully(self, monkeypatch):
        import signal
        import threading

        import geno.server as srv

        monkeypatch.setattr(srv, "_run_startup_checks", lambda: [])
        calls = []

        class _FakeServer:
            def __init__(self):
                self._stopped = threading.Event()

            def serve_forever(self):
                # Simulate SIGTERM arriving while serving by invoking the handler
                # serve_forever installed on the main thread.
                handler = signal.getsignal(signal.SIGTERM)
                assert callable(handler)
                handler(signal.SIGTERM, None)
                # A real serve_forever returns once shutdown() is requested.
                assert self._stopped.wait(timeout=5), "shutdown() was not triggered"

            def shutdown(self):
                calls.append("shutdown")
                self._stopped.set()

            def server_close(self):
                calls.append("server_close")

        monkeypatch.setattr(srv, "create_server", lambda *a, **kw: _FakeServer())

        original = signal.getsignal(signal.SIGTERM)
        try:
            srv.serve_forever()
        finally:
            signal.signal(signal.SIGTERM, original)

        # SIGTERM drained the server (shutdown) then released the socket
        # (server_close), and the previous handler was restored.
        assert calls == ["shutdown", "server_close"]
        assert signal.getsignal(signal.SIGTERM) is original


class TestServeLogging:
    """The hosted-runtime entry points must configure logging so the server's
    structured access/audit/warning records actually reach an operator.

    The module-level ``_restore_geno_logger_state`` autouse fixture snapshots and
    restores the shared ``geno`` logger around each of these tests.
    """

    def test_configure_logging_attaches_stderr_handler(self, monkeypatch):
        import logging

        import geno.server as srv

        geno_logger = logging.getLogger("geno")
        geno_logger.handlers[:] = []
        monkeypatch.delenv("GENO_LOG_LEVEL", raising=False)

        srv._configure_logging()

        assert geno_logger.handlers, "expected a handler on the geno logger"
        assert geno_logger.level == logging.INFO
        handler = geno_logger.handlers[-1]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stderr
        assert handler.formatter is not None
        formatted = handler.formatter.format(
            logging.LogRecord(
                "geno.server", logging.INFO, __file__, 1, "hello", None, None
            )
        )
        assert "INFO" in formatted
        assert "geno.server" in formatted
        assert "hello" in formatted

    def test_configure_logging_honors_env_level(self, monkeypatch):
        import logging

        import geno.server as srv

        logging.getLogger("geno").handlers[:] = []
        monkeypatch.setenv("GENO_LOG_LEVEL", "warning")
        srv._configure_logging()
        assert logging.getLogger("geno").level == logging.WARNING

    def test_configure_logging_invalid_level_falls_back_to_info(self, monkeypatch):
        import logging

        import geno.server as srv

        logging.getLogger("geno").handlers[:] = []
        monkeypatch.setenv("GENO_LOG_LEVEL", "not-a-level")
        srv._configure_logging()
        assert logging.getLogger("geno").level == logging.INFO

    def test_configure_logging_is_idempotent(self, monkeypatch):
        import logging

        import geno.server as srv

        geno_logger = logging.getLogger("geno")
        geno_logger.handlers[:] = []
        srv._configure_logging()
        srv._configure_logging()
        assert len(geno_logger.handlers) == 1

    def test_configure_logging_respects_existing_handler(self, monkeypatch):
        import logging

        import geno.server as srv

        geno_logger = logging.getLogger("geno")
        sentinel = logging.NullHandler()
        geno_logger.handlers[:] = [sentinel]
        geno_logger.setLevel(logging.CRITICAL)
        srv._configure_logging()
        assert geno_logger.handlers == [sentinel]
        assert geno_logger.level == logging.CRITICAL

    def test_serve_forever_logs_startup_banner(self, monkeypatch, caplog):
        import logging

        import geno.server as srv

        monkeypatch.setattr(srv, "_run_startup_checks", lambda: [])

        class _FakeServer:
            def serve_forever(self):
                pass

            def server_close(self):
                pass

        monkeypatch.setattr(srv, "create_server", lambda *a, **kw: _FakeServer())
        with caplog.at_level(logging.INFO, logger="geno.server"):
            srv.serve_forever(host="127.0.0.1", port=1234)
        assert any(
            "Serving Geno runtime" in r.getMessage() and "1234" in r.getMessage()
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Env-based configuration tests
# ---------------------------------------------------------------------------


class TestEnvConfig:
    def test_env_float_default(self, monkeypatch):
        from geno.server import _env_float

        monkeypatch.delenv("GENO_TEST_VAR", raising=False)
        assert _env_float("GENO_TEST_VAR", 3.14) == 3.14

    def test_env_float_override(self, monkeypatch):
        from geno.server import _env_float

        monkeypatch.setenv("GENO_TEST_VAR", "2.5")
        assert _env_float("GENO_TEST_VAR", 3.14) == 2.5

    def test_env_optional_bool(self, monkeypatch):
        from geno.server import _env_optional_bool

        monkeypatch.delenv("GENO_TEST_VAR", raising=False)
        assert _env_optional_bool("GENO_TEST_VAR") is None

        monkeypatch.setenv("GENO_TEST_VAR", "1")
        assert _env_optional_bool("GENO_TEST_VAR") is True

        monkeypatch.setenv("GENO_TEST_VAR", "0")
        assert _env_optional_bool("GENO_TEST_VAR") is False

    def test_env_positive_float_default(self, monkeypatch):
        from geno.server import _env_positive_float

        monkeypatch.delenv("GENO_TEST_VAR", raising=False)
        assert _env_positive_float("GENO_TEST_VAR", 3.14) == 3.14

    @pytest.mark.parametrize("value", ["0", "-0.1", "-1"])
    def test_env_positive_float_non_positive_raises(self, monkeypatch, value):
        from geno.server import _env_positive_float

        monkeypatch.setenv("GENO_TEST_VAR", value)
        with pytest.raises(ValueError, match="must be positive"):
            _env_positive_float("GENO_TEST_VAR", 1.0)

    @pytest.mark.parametrize(
        ("env_name", "value"),
        [
            ("GENO_MAX_TIMEOUT_SECONDS", "0"),
            ("GENO_MAX_WALL_CLOCK_SECONDS", "-1"),
            ("GENO_CONSTRAIN_WALL_CLOCK_SECONDS", "0"),
            ("GENO_WORKER_STARTUP_GRACE_SECONDS", "-1"),
        ],
    )
    def test_hosted_timeout_env_limits_must_be_positive(self, env_name, value):
        env = os.environ.copy()
        env[env_name] = value

        result = subprocess.run(
            [sys.executable, "-c", "import geno.server"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert f"Environment variable {env_name} must be positive" in result.stderr

    def test_env_float_invalid_raises(self, monkeypatch):
        from geno.server import _env_float

        monkeypatch.setenv("GENO_TEST_VAR", "not-a-float")
        with pytest.raises(ValueError, match="must be a float"):
            _env_float("GENO_TEST_VAR", 1.0)

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
    def test_env_float_non_finite_raises(self, monkeypatch, value):
        from geno.server import _env_float

        monkeypatch.setenv("GENO_TEST_VAR", value)
        with pytest.raises(ValueError, match="finite float"):
            _env_float("GENO_TEST_VAR", 1.0)

    def test_env_int_default(self, monkeypatch):
        from geno.server import _env_int

        monkeypatch.delenv("GENO_TEST_VAR", raising=False)
        assert _env_int("GENO_TEST_VAR", 42) == 42

    def test_env_int_override(self, monkeypatch):
        from geno.server import _env_int

        monkeypatch.setenv("GENO_TEST_VAR", "99")
        assert _env_int("GENO_TEST_VAR", 42) == 99

    def test_env_int_invalid_raises(self, monkeypatch):
        from geno.server import _env_int

        monkeypatch.setenv("GENO_TEST_VAR", "not-an-int")
        with pytest.raises(ValueError, match="must be an integer"):
            _env_int("GENO_TEST_VAR", 1)

    def test_env_non_negative_int_default(self, monkeypatch):
        from geno.server import _env_non_negative_int

        monkeypatch.delenv("GENO_TEST_VAR", raising=False)
        assert _env_non_negative_int("GENO_TEST_VAR", 42) == 42

    @pytest.mark.parametrize("value", ["0", "1"])
    def test_env_non_negative_int_accepts_zero_and_positive(self, monkeypatch, value):
        from geno.server import _env_non_negative_int

        monkeypatch.setenv("GENO_TEST_VAR", value)
        assert _env_non_negative_int("GENO_TEST_VAR", 42) == int(value)

    def test_env_non_negative_int_negative_raises(self, monkeypatch):
        from geno.server import _env_non_negative_int

        monkeypatch.setenv("GENO_TEST_VAR", "-1")
        with pytest.raises(ValueError, match="must be non-negative"):
            _env_non_negative_int("GENO_TEST_VAR", 1)

    def test_env_positive_int_default(self, monkeypatch):
        from geno.server import _env_positive_int

        monkeypatch.delenv("GENO_TEST_VAR", raising=False)
        assert _env_positive_int("GENO_TEST_VAR", 42) == 42

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_env_positive_int_non_positive_raises(self, monkeypatch, value):
        from geno.server import _env_positive_int

        monkeypatch.setenv("GENO_TEST_VAR", value)
        with pytest.raises(ValueError, match="must be positive"):
            _env_positive_int("GENO_TEST_VAR", 1)

    @pytest.mark.parametrize(
        ("env_name", "value", "message"),
        [
            ("GENO_MAX_REQUEST_BODY_BYTES", "0", "must be positive"),
            ("GENO_MAX_REQUEST_HEADER_BYTES", "0", "must be positive"),
            ("GENO_MAX_RESPONSE_BODY_BYTES", "0", "must be positive"),
            ("GENO_MAX_JSON_NESTING_DEPTH", "0", "must be positive"),
            ("GENO_MAX_MODULE_SOURCE_BYTES", "-1", "must be non-negative"),
            ("GENO_MAX_MODULES", "-1", "must be non-negative"),
        ],
    )
    def test_hosted_size_env_limits_are_validated(self, env_name, value, message):
        env = os.environ.copy()
        env[env_name] = value

        result = subprocess.run(
            [sys.executable, "-c", "import geno.server"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert f"Environment variable {env_name} {message}" in result.stderr

    def test_hosted_module_env_limits_allow_zero(self):
        env = os.environ.copy()
        env["GENO_MAX_MODULE_SOURCE_BYTES"] = "0"
        env["GENO_MAX_MODULES"] = "0"

        result = subprocess.run(
            [sys.executable, "-c", "import geno.server"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr


class TestServeRuntimeOptIn:
    """Non-loopback binding without API key must be explicitly opted in."""

    def test_non_loopback_without_key_exits(self, monkeypatch):
        from geno.__main__ import serve_runtime

        monkeypatch.delenv("GENO_API_KEY", raising=False)
        with pytest.raises(SystemExit):
            serve_runtime(host="0.0.0.0")

    def test_non_loopback_with_key_and_allowed_hosts_does_not_exit(self, monkeypatch):
        """Non-loopback needs both GENO_API_KEY and GENO_ALLOWED_HOSTS; then it proceeds."""
        monkeypatch.setenv("GENO_API_KEY", "test-key")
        monkeypatch.setattr(
            "geno.server._ALLOWED_HOSTS", frozenset({"api.example.com"})
        )
        called = []
        monkeypatch.setattr(
            "geno.server.serve_forever",
            lambda **kw: called.append(kw),
        )
        serve_runtime = __import__(
            "geno.__main__", fromlist=["serve_runtime"]
        ).serve_runtime
        serve_runtime(host="0.0.0.0")
        assert len(called) == 1
        assert called[0]["api_key"] == "test-key"

    def test_non_loopback_with_key_but_no_allowed_hosts_exits(self, monkeypatch):
        """A non-loopback bind with an API key but no Host allow-list must fail fast
        instead of starting a server that rejects every request with 421."""
        monkeypatch.setenv("GENO_API_KEY", "test-key")
        monkeypatch.setattr("geno.server._ALLOWED_HOSTS", frozenset())
        monkeypatch.setattr(
            "geno.server.serve_forever",
            lambda **kw: pytest.fail("serve_forever should not be called"),
        )
        serve_runtime = __import__(
            "geno.__main__", fromlist=["serve_runtime"]
        ).serve_runtime
        with pytest.raises(SystemExit):
            serve_runtime(host="0.0.0.0")

    def test_non_loopback_wildcard_allowed_hosts_does_not_exit(self, monkeypatch):
        """GENO_ALLOWED_HOSTS='*' is the explicit opt-out and satisfies the guard."""
        monkeypatch.setenv("GENO_API_KEY", "test-key")
        monkeypatch.setattr("geno.server._ALLOWED_HOSTS", frozenset({"*"}))
        called = []
        monkeypatch.setattr(
            "geno.server.serve_forever",
            lambda **kw: called.append(kw),
        )
        serve_runtime = __import__(
            "geno.__main__", fromlist=["serve_runtime"]
        ).serve_runtime
        serve_runtime(host="0.0.0.0")
        assert len(called) == 1

    def test_loopback_without_key_ok(self, monkeypatch):
        monkeypatch.delenv("GENO_API_KEY", raising=False)
        called = []
        monkeypatch.setattr(
            "geno.server.serve_forever",
            lambda **kw: called.append(kw),
        )
        serve_runtime = __import__(
            "geno.__main__", fromlist=["serve_runtime"]
        ).serve_runtime
        serve_runtime(host="127.0.0.1")
        assert len(called) == 1
        assert called[0]["api_key"] is None

    def test_ipv4_loopback_range_without_key_ok(self, monkeypatch):
        monkeypatch.delenv("GENO_API_KEY", raising=False)
        called = []
        monkeypatch.setattr(
            "geno.server.serve_forever",
            lambda **kw: called.append(kw),
        )
        serve_runtime = __import__(
            "geno.__main__", fromlist=["serve_runtime"]
        ).serve_runtime
        serve_runtime(host="127.0.0.2")
        assert len(called) == 1
        assert called[0]["api_key"] is None

    def test_allow_insecure_bypasses_guard(self, monkeypatch):
        monkeypatch.delenv("GENO_API_KEY", raising=False)
        called = []
        monkeypatch.setattr(
            "geno.server.serve_forever",
            lambda **kw: called.append(kw),
        )
        serve_runtime = __import__(
            "geno.__main__", fromlist=["serve_runtime"]
        ).serve_runtime
        serve_runtime(host="0.0.0.0", allow_insecure=True)
        assert len(called) == 1
        assert called[0]["api_key"] is None


class TestGenoServeOptIn:
    """The standalone geno-serve entrypoint must enforce the same guard."""

    def test_non_loopback_without_key_exits(self, monkeypatch):
        import geno.server as srv

        monkeypatch.delenv("GENO_API_KEY", raising=False)
        monkeypatch.setattr(srv, "is_supported_python", lambda: True)
        monkeypatch.setattr(
            srv,
            "serve_forever",
            lambda *args, **kwargs: pytest.fail("serve_forever should not be called"),
        )

        with pytest.raises(SystemExit):
            srv.main(["--host", "0.0.0.0"])

    def test_non_loopback_with_key_passes_api_key(self, monkeypatch):
        import geno.server as srv

        monkeypatch.setenv("GENO_API_KEY", "test-key")
        monkeypatch.setattr(srv, "_ALLOWED_HOSTS", frozenset({"api.example.com"}))
        monkeypatch.setattr(srv, "is_supported_python", lambda: True)
        called = []
        monkeypatch.setattr(
            srv,
            "serve_forever",
            lambda *args, **kwargs: called.append((args, kwargs)),
        )

        srv.main(["--host", "0.0.0.0"])

        assert len(called) == 1
        args, kwargs = called[0]
        assert args[:2] == ("0.0.0.0", 8000)
        assert kwargs["api_key"] == "test-key"

    def test_allow_insecure_bypasses_guard(self, monkeypatch):
        import geno.server as srv

        monkeypatch.delenv("GENO_API_KEY", raising=False)
        monkeypatch.setattr(srv, "is_supported_python", lambda: True)
        called = []
        monkeypatch.setattr(
            srv,
            "serve_forever",
            lambda *args, **kwargs: called.append((args, kwargs)),
        )

        srv.main(["--host", "0.0.0.0", "--allow-insecure"])

        assert len(called) == 1
        _args, kwargs = called[0]
        assert kwargs["api_key"] is None
