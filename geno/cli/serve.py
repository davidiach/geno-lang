"""``geno serve`` and ``geno dev`` — HTTP runtime and dev server."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Union

from .._cli_format import dim as _dim
from .._cli_format import green as _green
from .._cli_format import red as _red


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
    import http.server
    import threading
    import time

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
        while True:
            time.sleep(0.5)
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

    # SSE clients waiting for reload
    sse_clients: list[threading.Event] = []

    class DevHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/__geno_reload":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                client_event = threading.Event()
                sse_clients.append(client_event)
                try:
                    while True:
                        client_event.wait()
                        client_event.clear()
                        self.wfile.write(b"data: reload\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    if client_event in sse_clients:
                        sse_clients.remove(client_event)
                return

            with html_lock:
                content = html_content
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))

        def log_message(self, format, *args):
            pass  # Suppress request logs

    def _sse_broadcaster():
        while True:
            reload_event.wait()
            reload_event.clear()
            for client in list(sse_clients):
                client.set()

    # Start watcher and SSE broadcaster threads
    watcher_thread = threading.Thread(target=_watcher, daemon=True)
    watcher_thread.start()
    sse_thread = threading.Thread(target=_sse_broadcaster, daemon=True)
    sse_thread.start()

    try:
        # ThreadingHTTPServer: the SSE live-reload handler blocks on
        # client_event.wait(), so a single-threaded server would let one open
        # EventSource connection wedge every other request (M-10).
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), DevHandler)
    except OSError as e:
        print(f"Error: cannot bind to port {port}: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  {_green('Dev server')} running at {_dim(f'http://localhost:{port}')}")
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
        server.server_close()
