"""Tests for http_post and http_request builtins."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Any, cast

import pytest

from geno.api import RunConfig, run
from geno.diagnostics import ErrorCode


@contextmanager
def _redirect_server(location: str):
    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/target":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"redirect-ok")
                return
            self.send_response(302)
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
    def _serve(self, monkeypatch, routes):
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
                assert resp.read().decode("utf-8") == "created"

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
