"""``geno serve`` and ``geno dev`` — HTTP runtime and dev server."""

from __future__ import annotations

import http.server
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Union
from urllib.parse import urlsplit

from .._cli_format import dim as _dim
from .._cli_format import green as _green
from .._cli_format import red as _red

_DEV_MAX_CONNECTIONS = 64
_DEV_MAX_SSE_CLIENTS = 16
_DEV_HEADER_TIMEOUT_SECONDS = 5.0
_DEV_WRITE_TIMEOUT_SECONDS = 5.0
_DEV_SSE_HEARTBEAT_SECONDS = 15.0


def _normalize_dev_authority(value: str, expected_port: int) -> tuple[str, int] | None:
    if not value or value != value.strip() or any(ch.isspace() for ch in value):
        return None
    if any(ch in value for ch in ("/", "\\", "@", "#", "?")):
        return None
    try:
        parsed = urlsplit("//" + value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or hostname not in {"localhost", "127.0.0.1"}
        or port != expected_port
    ):
        return None
    return hostname, port


def _dev_request_header_error(headers: Any, expected_port: int) -> int | None:
    hosts = headers.get_all("Host", [])
    if len(hosts) != 1:
        return 421
    authority = _normalize_dev_authority(hosts[0], expected_port)
    if authority is None:
        return 421

    origins = headers.get_all("Origin", [])
    if not origins:
        return None
    if len(origins) != 1:
        return 403
    origin = origins[0]
    if not origin or origin != origin.strip():
        return 403
    try:
        parsed = urlsplit(origin)
        origin_port = parsed.port if parsed.port is not None else 80
    except ValueError:
        return 403
    if (
        parsed.scheme.lower() != "http"
        or parsed.hostname != authority[0]
        or origin_port != authority[1]
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return 403
    return None


class _BoundedDevHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[http.server.BaseHTTPRequestHandler],
        *,
        max_connections: int = _DEV_MAX_CONNECTIONS,
        max_sse_clients: int = _DEV_MAX_SSE_CLIENTS,
        header_timeout: float = _DEV_HEADER_TIMEOUT_SECONDS,
    ) -> None:
        self._connection_slots = threading.BoundedSemaphore(max_connections)
        self._sse_slots = threading.BoundedSemaphore(max_sse_clients)
        self._sse_clients: set[threading.Event] = set()
        self._sse_lock = threading.Lock()
        self._header_timeout = header_timeout
        self._header_timers: dict[Any, threading.Timer] = {}
        self._header_timer_lock = threading.Lock()
        self.stop_event = threading.Event()
        super().__init__(server_address, handler_class)

    @staticmethod
    def _send_capacity_response(request: Any) -> None:
        body = b"Service Unavailable\n"
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Connection: close\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            + body
        )
        try:
            request.settimeout(1.0)
            request.sendall(response)
        except OSError:
            pass

    def _expire_headers(self, request: Any) -> None:
        with self._header_timer_lock:
            if request not in self._header_timers:
                return
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _cancel_header_deadline(self, request: Any) -> None:
        with self._header_timer_lock:
            timer = self._header_timers.pop(request, None)
        if timer is not None:
            timer.cancel()

    def complete_request_headers(self, request: Any) -> None:
        self._cancel_header_deadline(request)
        request.settimeout(_DEV_WRITE_TIMEOUT_SECONDS)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._connection_slots.acquire(blocking=False):
            self._send_capacity_response(request)
            self.shutdown_request(request)
            return
        request.settimeout(self._header_timeout)
        timer = threading.Timer(self._header_timeout, self._expire_headers, (request,))
        timer.daemon = True
        with self._header_timer_lock:
            self._header_timers[request] = timer
        timer.start()
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._cancel_header_deadline(request)
            self._connection_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._cancel_header_deadline(request)
            self._connection_slots.release()

    def register_sse(self, event: threading.Event) -> bool:
        if not self._sse_slots.acquire(blocking=False):
            return False
        with self._sse_lock:
            self._sse_clients.add(event)
        return True

    def unregister_sse(self, event: threading.Event) -> None:
        with self._sse_lock:
            if event not in self._sse_clients:
                return
            self._sse_clients.remove(event)
        self._sse_slots.release()

    def broadcast_reload(self) -> None:
        with self._sse_lock:
            clients = tuple(self._sse_clients)
        for client in clients:
            client.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.broadcast_reload()
        with self._header_timer_lock:
            pending = tuple(self._header_timers.items())
            self._header_timers.clear()
        for request, timer in pending:
            timer.cancel()
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def server_close(self) -> None:
        self.stop()
        super().server_close()


def _serve_dev_sse(handler: Any, server: _BoundedDevHTTPServer) -> None:
    client_event = threading.Event()
    if not server.register_sse(client_event):
        handler.close_connection = True
        handler.send_error(503, "Too many live-reload clients")
        return
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(b": keepalive\n\n")
        handler.wfile.flush()
        while not server.stop_event.is_set():
            reload_requested = client_event.wait(_DEV_SSE_HEARTBEAT_SECONDS)
            client_event.clear()
            if server.stop_event.is_set():
                break
            payload = b"data: reload\n\n" if reload_requested else b": keepalive\n\n"
            handler.wfile.write(payload)
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
        pass
    finally:
        server.unregister_sse(client_event)


def serve_runtime(
    host: str = "127.0.0.1",
    port: int = 8000,
    service: str | None = None,
    revision: str | None = None,
    capabilities: set[str] | None = None,
    allow_insecure: bool = False,
):
    """Run the hosted HTTP runtime."""
    from ..server import _configure_logging, _enforce_secure_bind, serve_forever

    _configure_logging()
    api_key = os.getenv("GENO_API_KEY") or None
    _enforce_secure_bind(host, api_key, allow_insecure=allow_insecure)

    serve_forever(
        host=host,
        port=port,
        service=service or "geno-api",
        revision=revision,
        allowed_capabilities=capabilities,
        api_key=api_key,
        allow_insecure=allow_insecure,
    )


def _build_dev_server_html(
    path: Union[str, Path],
    width: int = 800,
    height: int = 600,
    title: str = "Geno App",
    sse_script: str = "",
) -> tuple[str, str | None]:
    """Build the current dev-server HTML snapshot."""
    from ..js_compiler import compile_project_to_html, compile_to_html
    from ..project_resolution import resolve_project_context
    from ..target_profile import TargetProfile
    from ..typechecker import TypeChecker

    try:
        resolved = resolve_project_context(path)
        dg = resolved.dependency_graph
        if len(dg.sorted_modules) > 1:
            checker = TypeChecker(target_profile=TargetProfile.load("browser"))
            checker.check_project_graph(dg)
            result = compile_project_to_html(
                dg,
                width=width,
                height=height,
                title=title,
            )
        else:
            result = compile_to_html(
                resolved.source,
                resolved.filename,
                width=width,
                height=height,
                title=title,
            )
        if sse_script:
            result = result.replace("</body>", sse_script + "\n</body>")
        return result, None
    except Exception as e:
        import html as _html_mod

        build_error = str(e)
        escaped_error = _html_mod.escape(build_error)
        return (
            f"<!DOCTYPE html><html><body style='background:#111;color:#f44;"
            f"font-family:monospace;padding:2em;white-space:pre-wrap'>"
            f"Build Error:\n\n{escaped_error}"
            f"{sse_script}</body></html>",
            build_error,
        )


def dev_server(
    path: str,
    port: int = 3000,
    width: int = 800,
    height: int = 600,
    title: str = "Geno App",
):
    """Start a dev server with live-reload for browser targets."""
    from .watch import _snapshot_watch_mtimes

    file_path = Path(path)

    # SSE script injected into HTML for live-reload
    _SSE_SCRIPT = """
<script>
(function() {
  var es = new EventSource('/__geno_reload');
  es.onmessage = function(e) { if (e.data === 'reload') location.reload(); };
  es.onerror = function() { setTimeout(function() { location.reload(); }, 2000); };
})();
</script>"""

    # Build HTML from current sources
    build_error: str | None = None
    html_content = ""

    def _build() -> str:
        nonlocal build_error
        result, build_error = _build_dev_server_html(
            file_path,
            width=width,
            height=height,
            title=title,
            sse_script=_SSE_SCRIPT,
        )
        return result

    html_content = _build()
    html_lock = threading.Lock()

    # Track file changes
    reload_event = threading.Event()

    from ..dependency_graph import DependencyGraphError
    from ..lexer import LexerError
    from ..parser import ParseError, ParseErrors
    from ..project_graph import ProjectGraphError
    from ..project_resolution import ProjectResolutionError

    _WATCH_RESOLVE_ERRORS = (
        DependencyGraphError,
        ProjectGraphError,
        ProjectResolutionError,
        ParseError,
        ParseErrors,
        LexerError,
        OSError,
        ValueError,
    )

    def _snapshot_or_none() -> object:
        # Resolution can raise ProjectGraphError/DependencyGraphError (e.g. a
        # module-name collision introduced while a file is mid-edit). Those
        # must not kill the watcher thread and permanently disable live-reload
        # (M-09) — swallow to a sentinel and let the next tick retry.
        try:
            return _snapshot_watch_mtimes(file_path)
        except _WATCH_RESOLVE_ERRORS as exc:
            return ("__watch_error__", str(exc))

    def _watcher():
        nonlocal html_content
        prev = _snapshot_or_none()
        while not server.stop_event.wait(0.5):
            current = _snapshot_or_none()
            if current != prev:
                prev = current
                print(_dim("  File change detected, rebuilding..."))
                with html_lock:
                    html_content = _build()
                if build_error:
                    print(_red(f"  Build error: {build_error}"))
                else:
                    print(_green("  Build OK"))
                reload_event.set()

    class DevHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            active_server = self.server
            assert isinstance(active_server, _BoundedDevHTTPServer)
            active_server.complete_request_headers(self.connection)
            header_error = _dev_request_header_error(
                self.headers, int(active_server.server_address[1])
            )
            if header_error is not None:
                self.close_connection = True
                self.send_error(header_error)
                return
            if self.path == "/__geno_reload":
                _serve_dev_sse(self, active_server)
                return

            with html_lock:
                content = html_content
            body = content.encode("utf-8")
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass  # Suppress request logs

    def _sse_broadcaster():
        while not server.stop_event.is_set():
            if not reload_event.wait(0.5):
                continue
            reload_event.clear()
            server.broadcast_reload()

    try:
        server = _BoundedDevHTTPServer(("127.0.0.1", port), DevHandler)
    except OSError as e:
        print(f"Error: cannot bind to port {port}: {e}", file=sys.stderr)
        sys.exit(1)
    watcher_thread = threading.Thread(target=_watcher, daemon=True)
    watcher_thread.start()
    sse_thread = threading.Thread(target=_sse_broadcaster, daemon=True)
    sse_thread.start()

    bound_port = int(server.server_address[1])
    print(
        f"  {_green('Dev server')} running at {_dim(f'http://localhost:{bound_port}')}"
    )
    print(f"  Watching {_dim(str(file_path))} via resolved project files")
    print(f"  Press {_dim('Ctrl+C')} to stop")
    print()
    if build_error:
        print(_red(f"  Build error: {build_error}"))
    else:
        print(_green("  Build OK"))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print(_dim("Dev server stopped."))
    finally:
        server.stop()
        server.server_close()
