"""Tests for http_post and http_request builtins."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any, cast

import pytest

from geno.api import RunConfig, run
from geno.diagnostics import ErrorCode


@contextmanager
def _redirect_server(location: str, status: int = 302):
    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/target":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"redirect-ok")
                return
            self.send_response(status)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/redirect"
    finally:
        server.shutdown()
        server.server_close()


class TestHttpPostInterpreter:
    """Test http_post via the embedding API with host callbacks."""

    def test_http_post_with_callback(self):
        source = """
        func main() -> String
            return http_post(url: "https://api.example.com/data", body: "{}")
        end func
        """

        def fake_http_post(url, body):
            return f"posted {body} to {url}"

        config = RunConfig(
            capabilities={"http"},
            host_callbacks={"http_post": fake_http_post},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "posted {} to https://api.example.com/data"

    def test_http_post_denied_without_capability(self):
        source = """
        func main() -> String
            return http_post(url: "https://example.com", body: "data")
        end func
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0


class TestHttpRequestInterpreter:
    """Test http_request via the embedding API with host callbacks."""

    def test_http_request_with_callback(self):
        source = """
        func main() -> Int
            let headers: List[(String, String)] = [("Accept", "application/json")]
            let result: Result[HttpResponse, String] = http_request(
                method: "GET",
                url: "https://api.example.com",
                headers: headers,
                body: ""
            )
            match result with
                | Ok(resp) ->
                    match resp with
                        | HttpResponse(status, _, _) -> return status
                    end match
                | Err(_) -> return 0
            end match
        end func
        """
        from geno.values import ConstructorValue

        def fake_http_request(method, url, headers, body):
            return ConstructorValue(
                "Ok",
                {
                    "value": ConstructorValue(
                        "HttpResponse",
                        {
                            "status": 200,
                            "body": '{"ok": true}',
                            "headers": [("Content-Type", "application/json")],
                        },
                    )
                },
            )

        config = RunConfig(
            capabilities={"http"},
            host_callbacks={"http_request": fake_http_request},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == 200

    def test_http_request_error_result(self):
        source = """
        func main() -> String
            let result: Result[HttpResponse, String] = http_request(
                method: "GET",
                url: "https://bad.example.com",
                headers: [],
                body: ""
            )
            match result with
                | Ok(_) -> return "unexpected"
                | Err(msg) -> return msg
            end match
        end func
        """
        from geno.values import ConstructorValue

        def fake_http_request(method, url, headers, body):
            return ConstructorValue("Err", {"error": "connection refused"})

        config = RunConfig(
            capabilities={"http"},
            host_callbacks={"http_request": fake_http_request},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "connection refused"

    def test_http_request_denied_without_capability(self):
        source = """
        func main() -> String
            let result: Result[HttpResponse, String] = http_request(
                method: "GET",
                url: "https://example.com",
                headers: [],
                body: ""
            )
            match result with
                | Ok(_) -> return "ok"
                | Err(e) -> return e
            end match
        end func
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is False
        denied = [
            d
            for d in result.diagnostics
            if d.code == ErrorCode.RUNTIME_CAPABILITY_DENIED
        ]
        assert len(denied) > 0

    def test_http_request_body_extraction(self):
        source = """
        func main() -> String
            let result: Result[HttpResponse, String] = http_request(
                method: "POST",
                url: "https://api.example.com",
                headers: [("Content-Type", "text/plain")],
                body: "hello"
            )
            match result with
                | Ok(resp) ->
                    match resp with
                        | HttpResponse(_, body, _) -> return body
                    end match
                | Err(e) -> return e
            end match
        end func
        """
        from geno.values import ConstructorValue

        def fake_http_request(method, url, headers, body):
            return ConstructorValue(
                "Ok",
                {
                    "value": ConstructorValue(
                        "HttpResponse",
                        {
                            "status": 201,
                            "body": f"received: {body}",
                            "headers": [],
                        },
                    )
                },
            )

        config = RunConfig(
            capabilities={"http"},
            host_callbacks={"http_request": fake_http_request},
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == "received: hello"


class TestHttpSchemeValidation:
    """Test that HTTP builtins reject non-http/https schemes."""

    def test_validate_http_scheme_allows_http(self, monkeypatch):
        from geno._runtime_support import _validate_http_scheme

        monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")
        _validate_http_scheme("http://example.com", "test")
        _validate_http_scheme("https://example.com", "test")

    def test_validate_http_scheme_rejects_file(self):
        from geno._runtime_support import _validate_http_scheme

        with pytest.raises(RuntimeError, match="scheme 'file' is not allowed"):
            _validate_http_scheme("file:///etc/passwd", "http_fetch")

    def test_validate_http_scheme_rejects_ftp(self):
        from geno._runtime_support import _validate_http_scheme

        with pytest.raises(RuntimeError, match="scheme 'ftp' is not allowed"):
            _validate_http_scheme("ftp://example.com/file", "http_fetch")

    def test_validate_http_scheme_rejects_data(self):
        from geno._runtime_support import _validate_http_scheme

        with pytest.raises(RuntimeError, match="scheme 'data' is not allowed"):
            _validate_http_scheme("data:text/html,<h1>hi</h1>", "http_fetch")

    def test_validate_http_scheme_rejects_empty(self):
        from geno._runtime_support import _validate_http_scheme

        with pytest.raises(RuntimeError, match="scheme '' is not allowed"):
            _validate_http_scheme("/etc/passwd", "http_fetch")

    def test_validate_rejects_no_scheme(self):
        from geno._runtime_support import _validate_http_scheme

        with pytest.raises(RuntimeError, match="scheme '' is not allowed"):
            _validate_http_scheme("just-a-path", "test")

    def test_runtime_http_fetch_allows_relative_http_redirect(self, monkeypatch):
        import geno._runtime_support as runtime_support

        monkeypatch.setattr(runtime_support, "_GENO_CAPS", {"http"})
        monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")
        with _redirect_server("/target") as url:
            assert runtime_support.http_fetch(url) == "redirect-ok"

    def test_runtime_http_fetch_supports_308_without_stdlib_handler(self, monkeypatch):
        import urllib.request

        import geno._runtime_support as runtime_support

        monkeypatch.delattr(
            urllib.request.HTTPRedirectHandler, "http_error_308", raising=False
        )
        monkeypatch.setattr(runtime_support, "_GENO_CAPS", {"http"})
        monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")
        with _redirect_server("/target", status=308) as url:
            assert runtime_support.http_fetch(url) == "redirect-ok"

    def test_runtime_http_fetch_rejects_non_http_redirect(self, monkeypatch):
        import geno._runtime_support as runtime_support

        monkeypatch.setattr(runtime_support, "_GENO_CAPS", {"http"})
        monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")
        with _redirect_server("ftp://127.0.0.1/secret.txt") as url:
            with pytest.raises(RuntimeError, match="scheme 'ftp' is not allowed"):
                runtime_support.http_fetch(url)

    def test_http_request_non_http_redirect_returns_err(self, monkeypatch):
        import geno._runtime_support as runtime_support

        monkeypatch.setattr(runtime_support, "_GENO_CAPS", {"http"})
        monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")
        with _redirect_server("ftp://127.0.0.1/secret.txt") as url:
            result = runtime_support.http_request("GET", url, [], "")

        assert isinstance(result, runtime_support.Err)
        assert "scheme 'ftp' is not allowed" in result.error

    def test_http_request_invalid_scheme_returns_err(self, monkeypatch):
        import geno._runtime_support as runtime_support

        monkeypatch.setattr(runtime_support, "_GENO_CAPS", {"http"})
        result = runtime_support.http_request("GET", "file:///etc/passwd", [], "")

        assert isinstance(result, runtime_support.Err)
        assert "scheme 'file' is not allowed" in result.error

    def test_unsafe_http_request_invalid_scheme_returns_err(self):
        from geno._serve import install_http_callbacks as _install_http_callbacks
        from geno.interpreter import Interpreter
        from geno.values import ConstructorValue

        interpreter = Interpreter()
        _install_http_callbacks(interpreter)
        http_request = interpreter.global_env.bindings["http_request"].func

        result = http_request("GET", "file:///etc/passwd", [], "")

        assert isinstance(result, ConstructorValue)
        assert result.constructor == "Err"
        assert "scheme 'file' is not allowed" in result.fields["error"]

    def test_unsafe_http_fetch_rejects_non_http_redirect(self):
        from geno._serve import install_http_callbacks as _install_http_callbacks
        from geno.interpreter import Interpreter

        interpreter = Interpreter()
        _install_http_callbacks(interpreter, allow_private_networks=True)
        http_fetch = interpreter.global_env.bindings["http_fetch"].func

        with _redirect_server("ftp://127.0.0.1/secret.txt") as url:
            with pytest.raises(RuntimeError, match="scheme 'ftp' is not allowed"):
                http_fetch(url)

    def test_unsafe_http_request_bad_headers_returns_err(self):
        from geno._serve import install_http_callbacks as _install_http_callbacks
        from geno.interpreter import Interpreter
        from geno.values import ConstructorValue

        interpreter = Interpreter()
        _install_http_callbacks(interpreter, allow_private_networks=True)
        http_request = interpreter.global_env.bindings["http_request"].func

        result = http_request("GET", "https://example.com", None, "")

        assert isinstance(result, ConstructorValue)
        assert result.constructor == "Err"
        assert "iterable" in result.fields["error"]


class TestInterpreterHttpListenHardening:
    def test_dns_rebinding_host_is_rejected(self, monkeypatch):
        import http.client
        import http.server
        import time

        from geno._serve import install_serve_callbacks
        from geno.interpreter import Interpreter

        captured: dict[str, Any] = {}

        class CapturingServer(http.server.HTTPServer):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                captured["server"] = self

        monkeypatch.setattr(http.server, "HTTPServer", CapturingServer)
        interpreter = Interpreter()
        install_serve_callbacks(interpreter)
        http_listen = interpreter.global_env.bindings["http_listen"].func

        thread = Thread(target=lambda: http_listen(0), daemon=True)
        thread.start()
        deadline = time.time() + 5
        while "server" not in captured and time.time() < deadline:
            time.sleep(0.01)
        assert "server" in captured, "interpreter http_listen never bound a server"
        server = captured["server"]
        try:
            conn = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=5,
            )
            conn.request("GET", "/", headers={"Host": "attacker.example"})
            response = conn.getresponse()
            response.read()
            conn.close()
            assert response.status == 421
        finally:
            server.shutdown()
            server.server_close()


class TestHttpTypeChecking:
    """Test that HTTP types are correctly checked."""

    def test_http_response_type_annotation(self):
        from geno.api import check

        source = """
        func use_response(resp: HttpResponse) -> Int
            example HttpResponse(200, "ok", []) -> 200
            match resp with
                | HttpResponse(status, _, _) -> return status
            end match
        end func

        func main() -> Int
            return 0
        end func
        """
        result = check(source)
        assert result.ok is True


class TestCompiledHttpResponseHeaderValidation:
    """H-03: the compiled http_listen runtime must reject CRLF in response
    headers, exactly like the interpreter serve path."""

    def test_rejects_crlf_in_header_value(self):
        from geno._runtime_support import _validate_http_response_headers

        with pytest.raises(RuntimeError, match="Invalid response header value"):
            _validate_http_response_headers([("X-Evil", "a\r\nInjected: yes")])

    @pytest.mark.parametrize("value", ["nul\x00byte", "delete\x7fbyte", "euro \u20ac"])
    @pytest.mark.parametrize(
        "validator_path",
        [
            "geno._serve._validate_response_headers",
            "geno._runtime_support._validate_http_response_headers",
        ],
    )
    def test_rejects_unencodable_or_control_header_value(self, value, validator_path):
        import importlib

        module_name, function_name = validator_path.rsplit(".", 1)
        validator = getattr(importlib.import_module(module_name), function_name)
        with pytest.raises(RuntimeError, match="Invalid response header value"):
            validator([("X-Evil", value)])

    def test_rejects_bad_header_name(self):
        from geno._runtime_support import _validate_http_response_headers

        with pytest.raises(RuntimeError, match="Invalid response header name"):
            _validate_http_response_headers([("Bad Name", "value")])

    def test_rejects_malformed_entry(self):
        from geno._runtime_support import _validate_http_response_headers

        with pytest.raises(RuntimeError, match="Invalid response header entry"):
            _validate_http_response_headers([("only-one",)])

    def test_none_headers_yield_empty(self):
        from geno._runtime_support import _validate_http_response_headers

        assert _validate_http_response_headers(None) == []

    def test_valid_headers_pass_through(self):
        from geno._runtime_support import _validate_http_response_headers

        assert _validate_http_response_headers([("Content-Type", "text/plain")]) == [
            ("Content-Type", "text/plain")
        ]

    @pytest.mark.parametrize(
        "name",
        [
            "Connection",
            "Content-Length",
            "Keep-Alive",
            "Proxy-Connection",
            "TE",
            "Trailer",
            "Transfer-Encoding",
            "Upgrade",
        ],
    )
    @pytest.mark.parametrize(
        "validator_path",
        [
            "geno._serve._validate_response_headers",
            "geno._runtime_support._validate_http_response_headers",
        ],
    )
    def test_server_managed_response_headers_are_rejected(self, name, validator_path):
        import importlib

        module_name, function_name = validator_path.rsplit(".", 1)
        validator = getattr(importlib.import_module(module_name), function_name)
        with pytest.raises(RuntimeError, match="managed by the server"):
            validator([(name, "unsafe")])


class TestHttpResponseStatusValidation:
    @pytest.mark.parametrize(
        "validator_path",
        [
            "geno._serve._validate_http_response_status",
            "geno._runtime_support._validate_http_response_status",
        ],
    )
    @pytest.mark.parametrize("status", [99, 100, 199, 600, 999, True, "200"])
    def test_invalid_final_status_is_rejected(self, validator_path, status):
        import importlib

        module_name, function_name = validator_path.rsplit(".", 1)
        validator = getattr(importlib.import_module(module_name), function_name)
        with pytest.raises(RuntimeError, match="Invalid response status"):
            validator(status, b"")

    @pytest.mark.parametrize(
        "validator_path",
        [
            "geno._serve._validate_http_response_status",
            "geno._runtime_support._validate_http_response_status",
        ],
    )
    @pytest.mark.parametrize("status", [204, 205, 304])
    def test_bodyless_status_rejects_nonempty_body(self, validator_path, status):
        import importlib

        module_name, function_name = validator_path.rsplit(".", 1)
        validator = getattr(importlib.import_module(module_name), function_name)
        with pytest.raises(RuntimeError, match="must not include"):
            validator(status, b"unexpected")
        assert validator(status, b"") is True


class TestHttpListenRequestFraming:
    @staticmethod
    def _headers(*pairs):
        from email.message import Message

        headers = Message()
        for name, value in pairs:
            headers[name] = value
        return headers

    @pytest.mark.parametrize(
        "validator_path",
        [
            "geno._serve._validated_http_request_content_length",
            "geno._runtime_support._validated_http_request_content_length",
        ],
    )
    @pytest.mark.parametrize(
        ("pairs", "message"),
        [
            (("Transfer-Encoding", "chunked"), "Transfer-Encoding"),
            (("Content-Length", "1,1"), "Ambiguous"),
            (("Content-Length", "+1"), "Invalid"),
            (("Content-Length", "-0"), "negative"),
            (("Content-Length", "01"), "Invalid"),
            (("Content-Length", "1_0"), "Invalid"),
        ],
    )
    def test_ambiguous_or_noncanonical_framing_is_rejected(
        self, validator_path, pairs, message
    ):
        import importlib

        module_name, function_name = validator_path.rsplit(".", 1)
        validator = getattr(importlib.import_module(module_name), function_name)
        with pytest.raises(ValueError, match=message):
            validator(self._headers(pairs))

    @pytest.mark.parametrize(
        "validator_path",
        [
            "geno._serve._validated_http_request_content_length",
            "geno._runtime_support._validated_http_request_content_length",
        ],
    )
    def test_extremely_long_content_length_is_rejected_before_int_conversion(
        self, validator_path
    ):
        import importlib

        module_name, function_name = validator_path.rsplit(".", 1)
        validator = getattr(importlib.import_module(module_name), function_name)
        with pytest.raises(OverflowError, match="too large"):
            validator(self._headers(("Content-Length", "9" * 5000)))

    @pytest.mark.parametrize(
        "reader_path",
        [
            "geno._serve._read_exact_http_request_body",
            "geno._runtime_support._read_exact_http_request_body",
        ],
    )
    def test_short_request_body_is_rejected(self, reader_path):
        import importlib
        import io

        module_name, function_name = reader_path.rsplit(".", 1)
        reader = getattr(importlib.import_module(module_name), function_name)
        with pytest.raises(ValueError, match="Incomplete request body"):
            reader(io.BytesIO(b"abc"), 4)


class TestCompiledHttpRouteLimit:
    """H-03: the compiled http_route must bound the route registry."""

    def test_route_registry_is_bounded(self, monkeypatch):
        import geno._runtime_support as rt

        monkeypatch.setattr(rt, "_GENO_CAPS", {"serve"})
        monkeypatch.setattr(rt, "_MAX_COLLECTION_SIZE", 3)
        monkeypatch.setattr(rt, "_http_routes", [])

        for i in range(3):
            rt.http_route("GET", f"/r{i}", lambda req: None)
        with pytest.raises(RuntimeError, match="Route registry size exceeds limit"):
            rt.http_route("GET", "/overflow", lambda req: None)


class TestCompiledHttpListenHardening:
    """H-03: end-to-end hardening of the compiled http_listen server —
    handler exceptions become 500s (no dropped connections / leaked
    tracebacks), invalid UTF-8 bodies become 400s, and CRLF header injection
    is blocked."""

    @contextmanager
    def _serve(self, monkeypatch, routes, *, request_deadline=None):
        import http.server as _http_server_mod
        import time
        import urllib.error
        import urllib.request

        import geno._runtime_support as rt

        captured: dict = {}

        class CapturingServer(_http_server_mod.HTTPServer):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                captured["server"] = self

        monkeypatch.setattr(_http_server_mod, "HTTPServer", CapturingServer)
        monkeypatch.setattr(rt, "_GENO_CAPS", {"serve"})
        monkeypatch.setattr(rt, "_http_routes", list(routes))
        if request_deadline is not None:
            monkeypatch.setattr(
                rt, "_HTTP_LISTEN_REQUEST_DEADLINE_SECONDS", request_deadline
            )

        thread = Thread(target=lambda: rt.http_listen(0), daemon=True)
        thread.start()
        deadline = time.time() + 5
        while "server" not in captured and time.time() < deadline:
            time.sleep(0.01)
        assert "server" in captured, "compiled http_listen never bound a server"
        server = captured["server"]
        port = server.server_port
        try:
            yield f"http://127.0.0.1:{port}", urllib.request, urllib.error
        finally:
            server.shutdown()
            server.server_close()

    def test_handler_exception_returns_500_not_dropped_connection(self, monkeypatch):
        def boom(_request):
            raise IndexError("handler blew up")

        with self._serve(monkeypatch, [("GET", "/boom", boom)]) as (
            base,
            request,
            error,
        ):
            with pytest.raises(error.HTTPError) as exc_info:
                request.urlopen(base + "/boom", timeout=5)
            assert exc_info.value.code == 500
            body = exc_info.value.read().decode("utf-8")
            # Generic message only — no Python traceback leaked to the client.
            assert "Internal Server Error" in body
            assert "IndexError" not in body
            assert "Traceback" not in body

    def test_crlf_header_injection_is_blocked(self, monkeypatch):
        from geno._runtime_support import HttpResponse

        def inject(_request):
            return HttpResponse(
                status=200, body="ok", headers=[("X-Evil", "a\r\nInjected: yes")]
            )

        with self._serve(monkeypatch, [("GET", "/inject", inject)]) as (
            base,
            request,
            error,
        ):
            # The CRLF header makes _validate_http_response_headers raise, which
            # is caught and turned into a 500 — the injected header never
            # reaches the wire.
            with pytest.raises(error.HTTPError) as exc_info:
                request.urlopen(base + "/inject", timeout=5)
            assert exc_info.value.code == 500
            assert exc_info.value.headers.get("Injected") is None

    def test_invalid_utf8_body_returns_400(self, monkeypatch):
        def echo(_request):
            from geno._runtime_support import HttpResponse

            return HttpResponse(status=200, body="ok", headers=[])

        with self._serve(monkeypatch, [("POST", "/echo", echo)]) as (
            base,
            request,
            error,
        ):
            req = request.Request(base + "/echo", data=b"\xff\xfe\xfa", method="POST")
            with pytest.raises(error.HTTPError) as exc_info:
                request.urlopen(req, timeout=5)
            assert exc_info.value.code == 400
            assert "valid UTF-8" in exc_info.value.read().decode("utf-8")

    def test_valid_request_succeeds(self, monkeypatch):
        def ok(_request):
            from geno._runtime_support import HttpResponse

            return HttpResponse(status=201, body="created", headers=[("X-Ok", "yes")])

        with self._serve(monkeypatch, [("GET", "/ok", ok)]) as (base, request, _err):
            with request.urlopen(base + "/ok", timeout=5) as resp:
                assert resp.status == 201
                assert resp.headers.get("X-Ok") == "yes"
                assert resp.headers.get("Content-Length") == "7"
                assert resp.headers.get("Connection") == "close"
                assert resp.read().decode("utf-8") == "created"

    def test_dns_rebinding_host_is_rejected(self, monkeypatch):
        import http.client
        from urllib.parse import urlsplit

        with self._serve(monkeypatch, []) as (base, _request, _error):
            parsed = urlsplit(base)
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            conn.request("GET", "/", headers={"Host": "attacker.example"})
            response = conn.getresponse()
            response.read()
            conn.close()
            assert response.status == 421

    def test_request_collection_limit_is_enforced(self, monkeypatch):
        import http.client
        from urllib.parse import urlsplit

        import geno._runtime_support as rt

        called = False

        def route(_request):
            nonlocal called
            called = True
            return rt.HttpResponse(status=200, body="ok", headers=[])

        monkeypatch.setattr(rt, "_MAX_COLLECTION_SIZE", 8)
        with self._serve(monkeypatch, [("GET", "/too-long", route)]) as (
            base,
            _request,
            _error,
        ):
            parsed = urlsplit(base)
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            conn.request("GET", "/too-long")
            response = conn.getresponse()
            response.read()
            conn.close()
            assert response.status == 413
            assert called is False

    def test_absolute_request_deadline_closes_slow_header_client(self, monkeypatch):
        import socket
        import time
        from urllib.parse import urlsplit

        with self._serve(monkeypatch, [], request_deadline=0.1) as (
            base,
            _request,
            _error,
        ):
            parsed = urlsplit(base)
            client = socket.create_connection((parsed.hostname, parsed.port), timeout=2)
            client.settimeout(2)
            started = time.monotonic()
            try:
                client.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Slow: partial")
                assert client.recv(1) == b""
            finally:
                client.close()
            assert time.monotonic() - started < 1.0

    def test_non_int_status_returns_500(self, monkeypatch):
        def bad_status(_request):
            from geno._runtime_support import HttpResponse

            # A non-int status (reachable via an Any-typed value) must be caught
            # by the handler guard and become a 500, not drop the connection.
            return HttpResponse(status=cast(Any, "200"), body="x", headers=[])

        with self._serve(monkeypatch, [("GET", "/bad", bad_status)]) as (
            base,
            request,
            error,
        ):
            with pytest.raises(error.HTTPError) as exc_info:
                request.urlopen(base + "/bad", timeout=5)
            assert exc_info.value.code == 500

    @pytest.mark.parametrize("status", [99, 100, 199, 600, 999])
    def test_out_of_range_status_returns_500(self, monkeypatch, status):
        from geno._runtime_support import HttpResponse

        def bad_status(_request):
            return HttpResponse(status=status, body="", headers=[])

        with self._serve(monkeypatch, [("GET", "/bad", bad_status)]) as (
            base,
            request,
            error,
        ):
            with pytest.raises(error.HTTPError) as exc_info:
                request.urlopen(base + "/bad", timeout=5)
            assert exc_info.value.code == 500

    def test_204_response_omits_body_and_content_length(self, monkeypatch):
        from geno._runtime_support import HttpResponse

        def no_content(_request):
            return HttpResponse(status=204, body="", headers=[])

        with self._serve(monkeypatch, [("GET", "/empty", no_content)]) as (
            base,
            request,
            _error,
        ):
            with request.urlopen(base + "/empty", timeout=5) as response:
                assert response.status == 204
                assert response.headers.get("Content-Length") is None
                assert response.read() == b""

    def test_204_response_with_body_returns_500(self, monkeypatch):
        from geno._runtime_support import HttpResponse

        def invalid(_request):
            return HttpResponse(status=204, body="unexpected", headers=[])

        with self._serve(monkeypatch, [("GET", "/bad", invalid)]) as (
            base,
            request,
            error,
        ):
            with pytest.raises(error.HTTPError) as exc_info:
                request.urlopen(base + "/bad", timeout=5)
            assert exc_info.value.code == 500

    def test_bind_failure_raises_runtime_error(self, monkeypatch):
        import socket

        import geno._runtime_support as rt

        monkeypatch.setattr(rt, "_GENO_CAPS", {"serve"})
        monkeypatch.setattr(rt, "_http_routes", [])

        # Occupy an ephemeral port, then ask http_listen to bind the same one:
        # the OSError must surface as a RuntimeError, not an uncaught traceback.
        occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        try:
            port = occupied.getsockname()[1]
            with pytest.raises(RuntimeError, match="failed to bind port"):
                rt.http_listen(port)
        finally:
            occupied.close()
