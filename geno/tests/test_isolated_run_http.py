"""The ``http`` grant in the process-isolated ``geno run`` lane (proposal 0013).

Milestone 5: ``geno run --cap http,...`` sends requests through the validated
client ``--unsafe --cap http`` uses, in trusted worker code
(``isolated_host.IsolatedHttp`` over ``_serve``'s module-level client):
destinations resolved, validated and pinned on every hop, the same redirect
and credential rules, ``ProxyHandler({})``, TLS checks and bounded bodies.
Private networks are reachable only with the parent's
``GENO_HTTP_ALLOW_PRIVATE``, and every connection attempt is bounded by the
run's remaining deadline, with the parent's hard kill as the backstop.

Every test uses local servers only. Tests that need a real worker run
``geno run`` in a subprocess; the address-pinning, DNS-rebinding and proxy
tests call ``IsolatedHttp`` in-process, which is the same trusted code the
worker runs, with real builtins in both places.
"""

from __future__ import annotations

import contextlib
import http.server
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from geno.capabilities import isolated_run_capabilities
from geno.isolated_host import (
    HTTP_MIN_TIMEOUT_SECONDS,
    IsolatedHttp,
    IsolatedRunPolicy,
)

pytestmark = pytest.mark.skipif(
    "http" not in isolated_run_capabilities(),
    reason="the isolated lane does not grant http on this platform",
)

# ---------------------------------------------------------------------------
# Local servers


@dataclass(frozen=True)
class Hit:
    """One request a local server received."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class LocalServer:
    """A local HTTP(S) server that records what it receives."""

    base: str
    host: str
    port: int
    hits: list[Hit] = field(default_factory=list)
    release: threading.Event = field(default_factory=threading.Event)

    def url(self, path: str = "/") -> str:
        return self.base + path


Responder = Callable[[http.server.BaseHTTPRequestHandler, Hit, LocalServer], None]


def reply(
    handler: http.server.BaseHTTPRequestHandler,
    status: int = 200,
    body: bytes = b"",
    headers: tuple[tuple[str, str], ...] = (),
) -> None:
    """Send a complete response from a responder."""
    handler.send_response(status)
    for name, value in headers:
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(body)


def stall(
    handler: http.server.BaseHTTPRequestHandler, hit: Hit, server: LocalServer
) -> None:
    """Accept the request and never answer (until the test ends)."""
    server.release.wait(30)


class _QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Stalled or rejected clients (a failed TLS handshake) are expected.
        pass


@contextlib.contextmanager
def local_server(
    responder: Responder,
    *,
    host: str = "127.0.0.1",
    tls: ssl.SSLContext | None = None,
) -> Iterator[LocalServer]:
    """Serve *responder* on *host*, recording every request it receives."""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def _dispatch(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            hit = Hit(
                self.command,
                self.path,
                {key.lower(): value for key, value in self.headers.items()},
                body,
            )
            state.hits.append(hit)
            responder(self, hit, state)

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _dispatch

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = _QuietServer((host, 0), Handler)
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    port = server.server_address[1]
    scheme = "https" if tls is not None else "http"
    state = LocalServer(f"{scheme}://{host}:{port}", host, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def echo(handler: http.server.BaseHTTPRequestHandler, hit: Hit, _: LocalServer) -> None:
    """Answer with the method, path and body received."""
    reply(
        handler,
        200,
        f"{hit.method} {hit.path} {hit.body.decode()}".encode(),
        (("X-Echo", "yes"),),
    )


# ---------------------------------------------------------------------------
# Running geno


def _env(**extra: str) -> dict[str, str]:
    """The caller's environment for a child ``geno run``, minus host policy."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {"GENO_SANDBOX_CONFIG", "GENO_CLI_ARGS", "GENO_HTTP_ALLOW_PRIVATE"}
    }
    env.update(extra)
    return env


PRIVATE = {"GENO_HTTP_ALLOW_PRIVATE": "1"}


def _geno(
    *args: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "geno", "run", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=_env() if env is None else env,
    )


def _program(tmp_path: Path, body: str, name: str = "main.geno") -> Path:
    path = tmp_path / name
    path.write_text(f"func main() -> Unit\n{body}end func\n", encoding="utf-8")
    return path


def _print_result(call: str, ok: str = "body") -> str:
    """Geno lines printing a ``Result``'s value or its ``Err`` text."""
    return (
        f"    match {call} with\n"
        f"        | Ok(body) -> print({ok})\n"
        '        | Err(error) -> print("Err " + error)\n'
        "    end match\n"
    )


def _fetch(url: str) -> str:
    return _print_result(f'http_fetch("{url}")')


def _request(method: str, url: str, headers: str = "[]", body: str = '""') -> str:
    return _print_result(
        f'http_request(method: "{method}", url: "{url}", headers: {headers}, '
        f"body: {body})",
        'to_string(body.status) + " " + body.body',
    )


NON_PUBLIC = "non-public network targets are not allowed"

# ---------------------------------------------------------------------------
# Private networks: only the parent's trusted opt-in


def test_private_server_is_reachable_with_the_trusted_opt_in(tmp_path: Path) -> None:
    with local_server(echo) as server:
        _program(
            tmp_path,
            _fetch(server.url("/a"))
            + _print_result(f'http_post("{server.url("/b")}", "{{}}")')
            + _request("get", server.url("/c")),
        )

        result = _geno(
            "--cap", "http,print", "main.geno", cwd=tmp_path, env=_env(**PRIVATE)
        )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "GET /a \nPOST /b {}\n200 GET /c \n"
    assert result.stderr == ""
    assert [hit.method for hit in server.hits] == ["GET", "POST", "GET"]
    # http_post sends JSON, as under --unsafe.
    assert server.hits[1].headers["content-type"] == "application/json"


def test_private_server_is_refused_without_the_opt_in(tmp_path: Path) -> None:
    with local_server(echo) as server:
        _program(
            tmp_path,
            _fetch(server.url("/a"))
            + _fetch(f"http://localhost:{server.port}/b")
            + _request("GET", server.url("/c")),
        )

        result = _geno("--cap", "http,print", "main.geno", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"Err http_fetch: {NON_PUBLIC}\n"
        f"Err http_fetch: {NON_PUBLIC}\n"
        f"Err http_request: {NON_PUBLIC}\n"
    )
    assert server.hits == []


def test_allowed_variables_cannot_enable_private_networks(tmp_path: Path) -> None:
    """Naming the opt-in with --allow-env exposes it; it never sets the policy."""
    with local_server(echo) as server:
        _program(
            tmp_path,
            '    print(env_get_or("GENO_HTTP_ALLOW_PRIVATE", "unset"))\n'
            + _fetch(server.url("/a")),
        )

        result = _geno(
            "--cap",
            "env,http,print",
            "--allow-env",
            "GENO_HTTP_ALLOW_PRIVATE",
            "main.geno",
            cwd=tmp_path,
        )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"unset\nErr http_fetch: {NON_PUBLIC}\n"
    assert server.hits == []


def test_the_opt_in_is_read_only_with_the_http_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from geno.cli.run import run_file
    from geno.sandbox import ProcessSandbox

    configs: list[Any] = []
    original_run_worker = ProcessSandbox._run_worker

    def spy_run_worker(
        self: ProcessSandbox,
        cmd: Any,
        code: Any,
        config_overrides: Any = None,
        **kwargs: Any,
    ) -> Any:
        configs.append(config_overrides)
        return original_run_worker(self, cmd, code, config_overrides, **kwargs)

    monkeypatch.setattr(ProcessSandbox, "_run_worker", spy_run_worker)
    monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")
    source = _program(tmp_path, '    print("ok")\n')

    assert run_file(str(source), capabilities={"print", "regex"}) is None
    assert run_file(str(source), capabilities={"http", "print"}) is None
    monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "0")
    assert run_file(str(source), capabilities={"http", "print"}) is None

    without, with_opt_in, opted_out = (config["run_policy"] for config in configs)
    assert without["capabilities"] == ["print", "regex"]
    assert without["http_allow_private"] is False
    assert with_opt_in["capabilities"] == ["http", "print"]
    assert with_opt_in["http_allow_private"] is True
    assert opted_out["http_allow_private"] is False


# ---------------------------------------------------------------------------
# Destinations: the same Err as --unsafe

REJECTED_URLS = [
    "file:///etc/passwd",
    "ftp://127.0.0.1/secret.txt",
    "gopher://127.0.0.1/",
    "http:///no-host",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/",
    "http://[::1]:9/",
    "http://127.0.0.1:9/",
]


def test_scheme_and_non_public_rejections_match_unsafe(tmp_path: Path) -> None:
    body = "".join(
        _fetch(url)
        + _print_result(f'http_post("{url}", "{{}}")')
        + _request("GET", url)
        for url in REJECTED_URLS
    )
    _program(tmp_path, body)

    isolated = _geno("--cap", "http,print", "main.geno", cwd=tmp_path)
    unsafe = _geno("--unsafe", "--cap", "http,print", "main.geno", cwd=tmp_path)

    assert isolated.returncode == 0, isolated.stderr
    assert unsafe.returncode == 0, unsafe.stderr
    assert isolated.stdout == unsafe.stdout
    lines = isolated.stdout.splitlines()
    assert len(lines) == 3 * len(REJECTED_URLS)
    assert all(line.startswith("Err http_") for line in lines)
    assert "Err http_fetch: scheme 'file' is not allowed, only http and https" in lines
    assert "Err http_request: scheme 'ftp' is not allowed, only http and https" in lines
    assert "Err http_post: URL must include a hostname" in lines
    assert f"Err http_fetch: {NON_PUBLIC}" in lines


# ---------------------------------------------------------------------------
# Redirects (two local ports)


@contextlib.contextmanager
def _redirecting(
    target: LocalServer, routes: dict[str, tuple[int, str]]
) -> Iterator[LocalServer]:
    """A server that answers ``path -> (status, location)`` and echoes the rest."""

    def responder(
        handler: http.server.BaseHTTPRequestHandler, hit: Hit, server: LocalServer
    ) -> None:
        if hit.path in routes:
            status, location = routes[hit.path]
            reply(handler, status, b"redirecting", (("Location", location),))
        else:
            echo(handler, hit, server)

    with local_server(responder) as server:
        yield server


def _echo_credentials(
    handler: http.server.BaseHTTPRequestHandler, hit: Hit, _: LocalServer
) -> None:
    received = ",".join(
        f"{name}={hit.headers.get(name, '-')}"
        for name in ("authorization", "cookie", "x-api-key")
    )
    reply(handler, 200, received.encode())


CREDENTIALS = (
    '[("Authorization", "Bearer secret"), ("Cookie", "session=secret"), '
    '("X-API-Key", "secret")]'
)


def test_redirects_follow_validated_hops_like_unsafe(tmp_path: Path) -> None:
    with local_server(echo) as target:
        routes = {
            "/302": (302, target.url("/from-302")),
            "/308": (308, target.url("/from-308")),
            "/303": (303, target.url("/from-303")),
            "/relative": (302, "/landed"),
        }
        with _redirecting(target, routes) as origin:
            _program(
                tmp_path,
                _fetch(origin.url("/302"))
                + _fetch(origin.url("/308"))
                + _fetch(origin.url("/relative"))
                + _print_result(f'http_post("{origin.url("/308")}", "kept")')
                + _print_result(f'http_post("{origin.url("/303")}", "dropped")')
                + _request("POST", origin.url("/308"), body='"kept"'),
            )

            isolated = _geno(
                "--cap", "http,print", "main.geno", cwd=tmp_path, env=_env(**PRIVATE)
            )
            unsafe = _geno(
                "--unsafe",
                "--cap",
                "http,print",
                "main.geno",
                cwd=tmp_path,
                env=_env(**PRIVATE),
            )

    assert isolated.returncode == 0, isolated.stderr
    assert isolated.stdout == (
        "GET /from-302 \n"
        "GET /from-308 \n"
        "GET /landed \n"
        # 308 keeps the method and the body; 303 turns a POST into a GET.
        "POST /from-308 kept\n"
        "GET /from-303 \n"
        "200 POST /from-308 kept\n"
    )
    assert unsafe.stdout == isolated.stdout


def test_every_redirect_hop_passes_the_scheme_check(tmp_path: Path) -> None:
    with local_server(echo) as target:
        routes = {"/to-ftp": (302, "ftp://127.0.0.1/secret.txt")}
        with _redirecting(target, routes) as origin:
            _program(
                tmp_path,
                _fetch(origin.url("/to-ftp")) + _request("GET", origin.url("/to-ftp")),
            )

            result = _geno(
                "--cap", "http,print", "main.geno", cwd=tmp_path, env=_env(**PRIVATE)
            )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "Err http_fetch: scheme 'ftp' is not allowed, only http and https\n"
        "Err http_request: scheme 'ftp' is not allowed, only http and https\n"
    )


def _redirect_loop(
    handler: http.server.BaseHTTPRequestHandler, hit: Hit, _: LocalServer
) -> None:
    step = int(hit.path.strip("/") or 0)
    reply(handler, 302, b"again", (("Location", f"/{step + 1}"),))


def test_redirect_count_is_limited_like_unsafe(tmp_path: Path) -> None:
    hits: dict[str, int] = {}
    outputs: dict[str, str] = {}
    for lane in ("isolated", "unsafe"):
        with local_server(_redirect_loop) as server:
            _program(tmp_path, _fetch(server.url("/0")))
            flags = ["--cap", "http,print"]
            if lane == "unsafe":
                flags.insert(0, "--unsafe")
            result = _geno(*flags, "main.geno", cwd=tmp_path, env=_env(**PRIVATE))
            hits[lane] = len(server.hits)
            outputs[lane] = result.stdout

    assert outputs["isolated"] == "Err http_fetch: too many redirects\n"
    assert outputs["unsafe"] == outputs["isolated"]
    # The first request and ten redirects reach the server; no eleventh.
    assert hits == {"isolated": 11, "unsafe": 11}


def test_cross_origin_redirects_drop_credentials(tmp_path: Path) -> None:
    with local_server(_echo_credentials) as target:
        routes = {
            "/cross": (302, target.url("/creds")),
            "/same": (302, "/creds"),
        }

        def responder(
            handler: http.server.BaseHTTPRequestHandler, hit: Hit, server: LocalServer
        ) -> None:
            if hit.path in routes:
                status, location = routes[hit.path]
                reply(handler, status, b"", (("Location", location),))
            else:
                _echo_credentials(handler, hit, server)

        with local_server(responder) as origin:
            _program(
                tmp_path,
                _request("GET", origin.url("/cross"), headers=CREDENTIALS)
                + _request("GET", origin.url("/same"), headers=CREDENTIALS),
            )

            result = _geno(
                "--cap", "http,print", "main.geno", cwd=tmp_path, env=_env(**PRIVATE)
            )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        # Another port is another origin: every caller header is dropped.
        "200 authorization=-,cookie=-,x-api-key=-\n"
        "200 authorization=Bearer secret,cookie=session=secret,x-api-key=secret\n"
    )
    assert [hit.path for hit in target.hits] == ["/creds"]


def _bind_loopback_alias(address: str) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((address, 0))
    except OSError:
        return False
    return True


needs_loopback_alias = pytest.mark.skipif(
    not _bind_loopback_alias("127.0.0.2"),
    reason="127.0.0.2 is not a usable loopback address here",
)


def _treat_as_public(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    """Make the trusted address policy see *addresses* as public (test only)."""
    from geno import _serve

    real = _serve._is_public_http_ipv4
    monkeypatch.setattr(
        _serve,
        "_is_public_http_ipv4",
        lambda address: str(address) in addresses or real(address),
    )


def _read(response: Any) -> bytes:
    with response as handle:
        return bytes(handle.read())


@needs_loopback_alias
def test_every_redirect_hop_passes_the_destination_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process: a public origin cannot redirect into a private network."""
    _treat_as_public(monkeypatch, "127.0.0.2")
    http_binding = IsolatedHttp(allow_private=False, remaining=lambda: 10.0)

    with local_server(echo) as private_target:

        def responder(
            handler: http.server.BaseHTTPRequestHandler, hit: Hit, server: LocalServer
        ) -> None:
            if hit.path == "/escape":
                location = private_target.url("/secret")
                reply(handler, 302, b"", (("Location", location),))
            else:
                echo(handler, hit, server)

        with local_server(responder, host="127.0.0.2") as origin:
            fetched = _read(http_binding.open_body("GET", origin.url("/x"), None, "f"))
            with pytest.raises(RuntimeError, match=NON_PUBLIC):
                http_binding.open_body("GET", origin.url("/escape"), None, "http_fetch")

    assert fetched == b"GET /x "
    assert private_target.hits == []
    assert [hit.path for hit in origin.hits] == ["/x", "/escape"]


# ---------------------------------------------------------------------------
# Pinning and DNS rebinding (in-process, trusted code)


def _fake_resolver(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, list[list[str]]]
) -> dict[str, int]:
    """Resolve each name in *answers* to its next address list, in order."""
    real_getaddrinfo = socket.getaddrinfo
    calls: dict[str, int] = {}

    def fake_getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in answers:
            return real_getaddrinfo(host, port, *args, **kwargs)
        index = calls.get(host, 0)
        calls[host] = index + 1
        addresses = answers[host][min(index, len(answers[host]) - 1)]
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    return calls


def test_dns_rebinding_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Validation resolves to a public address; the connection's own
    # resolution then rebinds to loopback, and that is what gets checked.
    calls = _fake_resolver(
        monkeypatch, {"rebind.test": [["93.184.216.34"], ["127.0.0.1"]]}
    )
    http_binding = IsolatedHttp(allow_private=False, remaining=lambda: 10.0)

    with local_server(echo) as server:
        url = f"http://rebind.test:{server.port}/secret"
        for call in (
            lambda: http_binding.open_body("GET", url, None, "http_fetch"),
            lambda: http_binding.open_request("GET", url, [], "", "http_request"),
        ):
            calls.clear()
            with pytest.raises(RuntimeError, match=NON_PUBLIC):
                call()
            assert calls == {"rebind.test": 2}

    assert server.hits == []


@needs_loopback_alias
def test_connections_use_only_validated_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _treat_as_public(monkeypatch, "127.0.0.2")
    calls = _fake_resolver(
        monkeypatch,
        {"pinned.test": [["127.0.0.2"]], "mixed.test": [["127.0.0.2", "127.0.0.1"]]},
    )
    http_binding = IsolatedHttp(allow_private=False, remaining=lambda: 10.0)

    with local_server(echo, host="127.0.0.2") as server:
        body = _read(
            http_binding.open_body(
                "GET", f"http://pinned.test:{server.port}/ok", None, "http_fetch"
            )
        )
        # One private answer among public ones refuses the whole destination
        # before any connection is made.
        with pytest.raises(RuntimeError, match=NON_PUBLIC):
            http_binding.open_body(
                "GET", f"http://mixed.test:{server.port}/no", None, "http_fetch"
            )

    assert body == b"GET /ok "
    assert [hit.path for hit in server.hits] == ["/ok"]
    assert server.hits[0].headers["host"] == f"pinned.test:{server.port}"
    assert calls == {"pinned.test": 2, "mixed.test": 1}


# ---------------------------------------------------------------------------
# Proxies

PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


@contextlib.contextmanager
def _recording_proxy() -> Iterator[LocalServer]:
    def responder(
        handler: http.server.BaseHTTPRequestHandler, hit: Hit, _: LocalServer
    ) -> None:
        reply(handler, 200, b"PROXIED")

    with local_server(responder) as proxy:
        yield proxy


def _route_through(monkeypatch: pytest.MonkeyPatch, proxy: LocalServer) -> None:
    for name in ("NO_PROXY", "no_proxy", "REQUEST_METHOD"):
        monkeypatch.delenv(name, raising=False)
    for name in PROXY_VARIABLES:
        monkeypatch.setenv(name, proxy.base)
        monkeypatch.setenv(name.lower(), proxy.base)


def test_proxy_variables_never_route_isolated_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process, with the proxy variables in this process's real environment."""
    http_binding = IsolatedHttp(allow_private=True, remaining=lambda: 10.0)

    with _recording_proxy() as proxy, local_server(echo) as origin:
        _route_through(monkeypatch, proxy)
        # Negative control: a plain opener in the same environment is proxied,
        # so a request that went through the proxy would be seen.
        with urllib.request.build_opener().open(
            origin.url("/control"), timeout=10
        ) as r:
            assert r.read() == b"PROXIED"
        assert [hit.path for hit in proxy.hits] == [origin.url("/control")]

        fetched = _read(http_binding.open_body("GET", origin.url("/a"), None, "f"))
        requested = _read(
            http_binding.open_request("GET", origin.url("/b"), [], "", "f")
        )

    assert fetched == b"GET /a "
    assert requested == b"GET /b "
    assert [hit.path for hit in origin.hits] == ["/a", "/b"]
    assert [hit.path for hit in proxy.hits] == [origin.url("/control")]


def test_allowed_proxy_variables_never_route_requests_end_to_end(
    tmp_path: Path,
) -> None:
    with _recording_proxy() as proxy, local_server(echo) as origin:
        _program(
            tmp_path,
            '    print(env_get_or("HTTP_PROXY", "unset"))\n' + _fetch(origin.url("/a")),
        )
        env = _env(**PRIVATE)
        for name in PROXY_VARIABLES:
            env[name] = env[name.lower()] = proxy.base
        env.pop("NO_PROXY", None)
        env.pop("no_proxy", None)

        result = _geno(
            "--cap",
            "env,http,print",
            "--allow-env",
            "HTTP_PROXY",
            "main.geno",
            cwd=tmp_path,
            env=env,
        )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{proxy.base}\nGET /a \n"
    assert proxy.hits == []


# ---------------------------------------------------------------------------
# TLS


@pytest.fixture(scope="module")
def self_signed_tls(tmp_path_factory: pytest.TempPathFactory) -> ssl.SSLContext:
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is not available to make a self-signed certificate")
    assert openssl is not None
    directory = tmp_path_factory.mktemp("tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    made = subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if made.returncode != 0:
        pytest.skip(f"openssl could not make a certificate: {made.stderr}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    return context


def test_tls_certificates_are_verified(
    tmp_path: Path, self_signed_tls: ssl.SSLContext
) -> None:
    with local_server(echo, tls=self_signed_tls) as server:
        _program(tmp_path, _fetch(server.url("/a")) + _request("GET", server.url("/b")))

        isolated = _geno(
            "--cap", "http,print", "main.geno", cwd=tmp_path, env=_env(**PRIVATE)
        )
        unsafe = _geno(
            "--unsafe",
            "--cap",
            "http,print",
            "main.geno",
            cwd=tmp_path,
            env=_env(**PRIVATE),
        )

    assert isolated.returncode == 0, isolated.stderr
    lines = isolated.stdout.splitlines()
    assert len(lines) == 2
    assert all(line.startswith("Err <urlopen error [SSL: ") for line in lines)
    assert all("certificate verify failed" in line for line in lines)
    assert unsafe.stdout == isolated.stdout
    assert server.hits == []


# ---------------------------------------------------------------------------
# Bounds: response size and the run's deadline


def test_oversized_bodies_keep_the_size_error(tmp_path: Path) -> None:
    def big(handler: http.server.BaseHTTPRequestHandler, hit: Hit, _: Any) -> None:
        reply(handler, 200, b"x" * 5000)

    with local_server(big) as server:
        for builtin, call in (
            ("http_fetch", _fetch(server.url("/a"))),
            ("http_request", _request("GET", server.url("/b"))),
        ):
            _program(tmp_path, call)

            result = _geno(
                "--cap",
                "http,print",
                "--max-collection-size",
                "1000",
                "main.geno",
                cwd=tmp_path,
                env=_env(**PRIVATE),
            )

            assert result.returncode == 1, result.stdout
            assert result.stdout == ""
            assert result.stderr.startswith(
                f"Runtime Error: {builtin}: String size exceeds limit ("
            )
            assert result.stderr.rstrip().endswith("> 1000)")


def _partial_body(
    handler: http.server.BaseHTTPRequestHandler, hit: Hit, server: LocalServer
) -> None:
    handler.send_response(200)
    handler.send_header("Content-Length", "100000")
    handler.end_headers()
    handler.wfile.write(b"partial")
    handler.wfile.flush()
    server.release.wait(30)


STALL_TIMEOUT = 3.0
# Worker start-up, the frontend and process teardown.
STALL_SLACK = 6.0


@pytest.mark.parametrize("stage", ["headers", "body", "redirect"])
def test_stalls_end_by_the_run_deadline(tmp_path: Path, stage: str) -> None:
    responder = _partial_body if stage == "body" else stall
    with local_server(responder) as stalled:
        url = stalled.url("/stall")
        routes = {"/hop": (302, url)}
        with _redirecting(stalled, routes) as origin:
            if stage == "redirect":
                url = origin.url("/hop")
            _program(tmp_path, _fetch(url) + '    print("after")\n')

            started = time.monotonic()
            result = _geno(
                "--cap",
                "http,print",
                "--timeout",
                str(STALL_TIMEOUT),
                "main.geno",
                cwd=tmp_path,
                env=_env(**PRIVATE),
            )
            elapsed = time.monotonic() - started

    assert elapsed < STALL_TIMEOUT + STALL_SLACK, elapsed
    assert "timed out" in result.stdout + result.stderr
    assert stalled.hits


def test_each_hop_waits_at_most_the_time_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-process: the timeout is read again for every connection attempt."""
    left = {"seconds": 20.0}

    with local_server(stall) as stalled:

        def responder(
            handler: http.server.BaseHTTPRequestHandler, hit: Hit, _: LocalServer
        ) -> None:
            # The first hop used most of the run's time.
            left["seconds"] = 0.5
            reply(handler, 302, b"", (("Location", stalled.url("/stall")),))

        with local_server(responder) as origin:
            http_binding = IsolatedHttp(
                allow_private=True, remaining=lambda: left["seconds"]
            )
            started = time.monotonic()
            with pytest.raises(OSError, match="timed out"):
                _read(http_binding.open_body("GET", origin.url("/hop"), None, "f"))
            elapsed = time.monotonic() - started

    assert elapsed < 5.0, elapsed
    assert stalled.hits


def test_timeouts_never_exceed_the_operation_timeout_or_the_time_left() -> None:
    from geno._serve import HTTP_REQUEST_TIMEOUT_SECONDS

    def binding(remaining: float | None) -> IsolatedHttp:
        return IsolatedHttp(allow_private=False, remaining=lambda: remaining)

    assert binding(None).timeout_seconds() == HTTP_REQUEST_TIMEOUT_SECONDS
    assert binding(1000.0).timeout_seconds() == HTTP_REQUEST_TIMEOUT_SECONDS
    assert binding(2.5).timeout_seconds() == 2.5
    assert binding(0.0).timeout_seconds() == HTTP_MIN_TIMEOUT_SECONDS
    assert binding(-4.0).timeout_seconds() == HTTP_MIN_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Examples


def _example_program(url: str) -> str:
    return f"""func ping() -> String
    example () -> "GET /ping "
    match http_fetch("{url}") with
        | Ok(body) -> return body
        | Err(error) -> return "Err " + error
    end match
end func

func main() -> Unit
    print(ping())
end func
"""


def test_an_example_can_use_the_http_grant_before_main(tmp_path: Path) -> None:
    with local_server(echo) as server:
        (tmp_path / "main.geno").write_text(
            _example_program(server.url("/ping")), encoding="utf-8"
        )

        checked = _geno(
            "--cap", "http,print", "main.geno", cwd=tmp_path, env=_env(**PRIVATE)
        )
        example_hits = len(server.hits)
        skipped = _geno(
            "--cap",
            "http,print",
            "--no-check-examples",
            "main.geno",
            cwd=tmp_path,
            env=_env(**PRIVATE),
        )

    assert checked.returncode == 0, checked.stderr
    assert checked.stdout == "GET /ping \n"
    # One request from the example, one from main; none from a skipped example.
    assert example_hits == 2
    assert skipped.stdout == "GET /ping \n"
    assert len(server.hits) == 3


def test_an_example_without_the_grant_gets_the_isolated_hint(tmp_path: Path) -> None:
    with local_server(echo) as server:
        (tmp_path / "main.geno").write_text(
            _example_program(server.url("/ping")), encoding="utf-8"
        )

        result = _geno("--cap", "print", "main.geno", cwd=tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert (
        "Runtime Error: Capability denied: 'http_fetch' requires the 'http' "
        "capability" in result.stderr
    )
    # Only the interpreter's E412 denial stub tags the failure, and only a
    # tagged denial gets a hint (a forged message gets none).
    assert result.stderr.rstrip().endswith(
        "Hint: grant it with --cap; the program stays process-isolated:\n"
        "  geno run --cap http,print main.geno"
    )
    assert server.hits == []


def test_an_example_is_bound_by_the_trusted_private_policy(tmp_path: Path) -> None:
    with local_server(echo) as server:
        (tmp_path / "main.geno").write_text(
            _example_program(server.url("/ping")), encoding="utf-8"
        )

        result = _geno("--cap", "http,print", "main.geno", cwd=tmp_path)

    assert result.returncode == 1
    assert f"Err http_fetch: {NON_PUBLIC}" in result.stderr
    assert server.hits == []


# ---------------------------------------------------------------------------
# What the worker receives


def test_the_worker_binds_http_only_for_the_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from geno.cli.run import _prepare_process_run

    source = _program(tmp_path, '    print("ok")\n')
    request = {
        "filename": str(source),
        "target": None,
        "check_examples": True,
        "timeout": 5.0,
        "max_recursion_depth": 1000,
        "max_output_length": 100000,
        "max_collection_size": 100000,
        "max_integer_bits": 4096,
    }

    granted = _prepare_process_run(
        request,
        IsolatedRunPolicy(
            capabilities=frozenset({"http", "print"}), http_allow_private=True
        ),
    )
    plain = _prepare_process_run(
        request, IsolatedRunPolicy(capabilities=frozenset({"print"}))
    )

    (binding,) = granted["host_table"].values()
    assert type(binding) is IsolatedHttp
    assert binding.allow_private is True
    assert plain["host_table"] == {}
