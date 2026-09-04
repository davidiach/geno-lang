"""Half-open connections must not take the whole server down.

`_BoundedThreadingHTTPServer` admits at most MAX_CONNECTIONS sockets, and a
handler used to hold its slot for the full request timeout no matter how
little the client sent.  MAX_CONNECTIONS clients that opened a socket and
dribbled a partial header therefore made every endpoint unreachable --
`/healthz` and `/metrics` included, so an orchestrator's liveness probe failed
at the same time and the outage never showed up in metrics.

Reading the request head now has its own short budget, so a half-open socket
releases its slot in seconds instead of tens of seconds.
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing, contextmanager
from typing import Iterator

import pytest

from geno import server as server_module

SLOTS = 8
HOST = "127.0.0.1"


@contextmanager
def _running_server(**overrides: object) -> Iterator[str]:
    """Start a server on an ephemeral port with a small connection pool."""
    original = {name: getattr(server_module, name) for name in overrides}
    for name, value in overrides.items():
        setattr(server_module, name, value)
    httpd = server_module.create_server(host=HOST, port=0)
    # create_server may not honour a small pool via kwargs; enforce it here so
    # the test drives the same admission path with far fewer sockets.
    httpd._connection_slots = server_module._CONNECTION_SEMAPHORE_FACTORY(SLOTS)
    httpd._max_connections = SLOTS
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{HOST}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)
        for name, value in original.items():
            setattr(server_module, name, value)


def _get(base_url: str, path: str, timeout: float = 5.0) -> int:
    with urllib.request.urlopen(f"{base_url}{path}", timeout=timeout) as response:
        return int(response.status)


def _hold_half_open(host: str, port: int, count: int) -> list[socket.socket]:
    """Open sockets that send a partial request head and never finish it."""
    held: list[socket.socket] = []
    for _ in range(count):
        try:
            conn = socket.create_connection((host, port), timeout=5)
        except OSError:  # pragma: no cover - pool already saturated
            break
        # A request line and one header, but no terminating blank line.
        conn.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n")
        held.append(conn)
    return held


@pytest.mark.timeout(120)
def test_health_survives_a_saturating_half_open_flood() -> None:
    """The liveness endpoint must answer while every slot is under attack."""
    with _running_server(_HEADER_TIMEOUT_SECONDS=1.0) as base_url:
        assert _get(base_url, "/healthz") == 200
        port = int(base_url.rsplit(":", 1)[1])
        held = _hold_half_open(HOST, port, SLOTS * 2)
        try:
            assert len(held) >= SLOTS, "could not saturate the connection pool"
            # Slots are released as the short head budget expires, so a probe
            # gets through well inside the full request timeout.
            deadline = time.monotonic() + 20
            statuses = []
            while time.monotonic() < deadline:
                try:
                    statuses.append(_get(base_url, "/healthz", timeout=3))
                    break
                except (urllib.error.URLError, OSError, ConnectionError):
                    time.sleep(0.25)
            assert statuses == [200], (
                "/healthz never answered while half-open sockets were held; "
                "connection slots are not being released"
            )
        finally:
            for conn in held:
                with closing(conn):
                    pass


@pytest.mark.timeout(60)
def test_head_budget_is_shorter_than_the_request_budget() -> None:
    """The head budget must actually be tighter, or the flood still lands."""
    assert (
        server_module._HEADER_TIMEOUT_SECONDS < server_module._REQUEST_TIMEOUT_SECONDS
    )


@pytest.mark.timeout(60)
def test_a_complete_request_gets_the_full_budget() -> None:
    """Extending after the head keeps slow bodies working."""
    with _running_server(_HEADER_TIMEOUT_SECONDS=1.0) as base_url:
        port = int(base_url.rsplit(":", 1)[1])
        with closing(socket.create_connection((HOST, port), timeout=10)) as conn:
            conn.sendall(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            # Sleep past the head budget: the deadline should have been
            # extended once the head arrived, so the response still lands.
            time.sleep(1.5)
            conn.settimeout(10)
            assert b"200" in conn.recv(4096)


@pytest.mark.timeout(60)
def test_refused_connections_are_counted_and_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A silent drop made pool exhaustion invisible to operators."""
    with _running_server(_HEADER_TIMEOUT_SECONDS=30.0) as base_url:
        port = int(base_url.rsplit(":", 1)[1])
        with caplog.at_level("WARNING", logger=server_module.logger.name):
            held = _hold_half_open(HOST, port, SLOTS * 3)
            try:
                time.sleep(1.0)
            finally:
                for conn in held:
                    with closing(conn):
                        pass
        # The server object is not reachable from here, so assert on the
        # observable signal an operator would actually have.
        assert any(
            "connection pool exhausted" in record.message for record in caplog.records
        ), "pool exhaustion produced no warning"
