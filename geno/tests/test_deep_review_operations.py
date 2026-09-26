"""Regression coverage for JSON grants, hosted draining and readiness."""

from __future__ import annotations

import http.client
import json
import multiprocessing
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from typing import cast

import pytest

from geno.api import RunResult, run
from geno.server import _BoundedThreadingHTTPServer, create_server


@pytest.mark.parametrize(
    ("capability", "expression", "return_type"),
    [
        ("print", 'print("review")', "Unit"),
        ("clock", "clock_now()", "Int"),
        ("random", "random_int(min: 1, max: 10)", "Int"),
    ],
)
def test_json_cli_requires_explicit_capabilities(
    tmp_path, capability, expression, return_type
):
    source = f"func main() -> {return_type}\n    return {expression}\nend func\n"
    path = tmp_path / "Main.geno"
    path.write_text(source)
    assert not run(source).ok

    command = [sys.executable, "-m", "geno", "run", "--json", str(path)]
    denied = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert denied.returncode != 0
    result = json.loads(denied.stdout)
    assert not result["ok"]
    assert capability in result["diagnostics"][0]["message"]

    granted = subprocess.run(
        [*command, "--cap", capability], capture_output=True, text=True, timeout=15
    )
    payload = json.loads(granted.stdout)
    assert payload["ok"], granted.stdout + granted.stderr
    # A granted run succeeds, and under docs/spec/v0.5.md 4.1.1 its status is
    # `main`'s result rather than 0 whenever that result is an `Int`. Both
    # `Int` expressions here return a value chosen at runtime, so the status is
    # pinned against the value the envelope reports rather than a literal.
    assert granted.returncode == (
        0 if return_type == "Unit" else payload["value"] % 256
    ), granted.stdout + granted.stderr


@contextmanager
def _serving(server):
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _request(server, path, *, method="GET", payload=None, headers=None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        request_headers = dict(headers or {})
        if payload is not None:
            request_headers.setdefault("Content-Type", "application/json")
        conn.request(
            method,
            path,
            body=json.dumps(payload) if payload is not None else None,
            headers=request_headers,
        )
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def test_close_waits_for_an_active_response_before_returning():
    entered, release, closed = (threading.Event() for _ in range(3))
    responses = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            assert release.wait(5)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args):
            pass

    server = _BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler)
    with _serving(server):
        client = threading.Thread(
            target=lambda: responses.append(_request(server, "/"))
        )
        client.start()
        assert entered.wait(3)
        server.shutdown()

        def close():
            server.server_close()
            closed.set()

        closer = threading.Thread(target=close)
        closer.start()
        try:
            assert not closed.wait(0.1)
            release.set()
            assert closed.wait(3)
            client.join(timeout=3)
            assert responses == [(200, b"ok")]
            assert not server._active_requests
        finally:
            release.set()
            closer.join(timeout=3)
            client.join(timeout=3)


def test_close_cancels_blocked_socket_after_a_bounded_grace():
    entered, release = threading.Event(), threading.Event()
    outcomes = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            release.wait(5)

        def log_message(self, *_args):
            pass

    def request():
        try:
            outcomes.append(_request(server, "/"))
        except (OSError, http.client.HTTPException):
            outcomes.append("connection closed")

    server = _BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler, shutdown_grace=0.05)
    with _serving(server):
        client = threading.Thread(target=request)
        client.start()
        assert entered.wait(3)
        server.shutdown()
        started = time.monotonic()
        try:
            server.server_close()
            assert time.monotonic() - started < 3
            client.join(timeout=1)
            assert outcomes == ["connection closed"]
        finally:
            release.set()
            client.join(timeout=3)


def _waiting_worker(result_conn, entered):
    result_conn.send(("ready", None))
    entered.set()
    threading.Event().wait(30)


def test_close_stops_an_active_child_process():
    from geno.server import _execute_worker_with_wall_timeout

    entered = multiprocessing.get_context("spawn").Event()
    outcomes = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            outcomes.append(
                _execute_worker_with_wall_timeout(
                    _waiting_worker,
                    (entered,),
                    30.0,
                    worker_owner=cast(_BoundedThreadingHTTPServer, self.server),
                )
            )

        def log_message(self, *_args):
            pass

    def request():
        try:
            _request(server, "/")
        except (OSError, http.client.HTTPException):
            pass

    server = _BoundedThreadingHTTPServer(("127.0.0.1", 0), Handler, shutdown_grace=0.05)
    with _serving(server):
        client = threading.Thread(target=request)
        client.start()
        try:
            assert entered.wait(10)
            with server._drain_condition:
                workers = tuple(server._workers)
            assert len(workers) == 1
            server.shutdown()
            started = time.monotonic()
            server.server_close()
            assert time.monotonic() - started < 3
            assert all(not worker.is_alive() for worker in workers)
            assert not server._workers
            assert outcomes[0][0] == "error"
        finally:
            client.join(timeout=3)


def test_startup_smoke_uses_the_real_hosted_worker():
    from geno.server import _run_startup_checks

    assert _run_startup_checks() == []


def test_startup_smoke_fails_when_child_spawning_is_unavailable(monkeypatch):
    import geno.server as srv

    def unavailable(_method):
        raise OSError("worker creation unavailable")

    monkeypatch.setattr(srv.multiprocessing, "get_context", unavailable)
    errors = srv._run_startup_checks()
    assert len(errors) == 1
    assert "Hosted worker self-test failed" in errors[0]
    assert "worker creation unavailable" in errors[0]


def test_closed_server_refuses_to_start_a_late_worker():
    from geno.server import _execute_worker_with_wall_timeout

    server = _BoundedThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    server.server_close()
    status, payload = _execute_worker_with_wall_timeout(
        _waiting_worker, (), 1.0, worker_owner=server
    )
    assert status == "error"
    assert payload["type"] == "ServerShuttingDown"
    assert not server._workers


@pytest.mark.parametrize("api_key", [None, "review-secret"])
def test_readiness_is_503_for_failed_checks_but_liveness_is_200(api_key):
    server = create_server(
        "127.0.0.1", 0, api_key=api_key, startup_errors=["recorded startup failure"]
    )
    with _serving(server):
        for path in ("/healthz", "/readyz"):
            status, body = _request(server, path)
            assert status == 503
            report = json.loads(body)
            assert report["status"] == "failed"
            if api_key:
                assert report == {"status": "failed"}
        assert _request(server, "/livez") == (200, b'{"status":"ok"}')


def test_worker_failures_and_recovery_update_readiness(monkeypatch):
    import geno.server as srv

    server = create_server("127.0.0.1", 0, startup_errors=[])
    payload = {"source": "func main() -> Int\n    return 1\nend func"}
    with _serving(server):
        monkeypatch.setattr(
            srv,
            "_execute_run_with_wall_timeout",
            lambda *_a, **_kw: (
                "error",
                {"type": "WorkerSpawnFailed", "message": "failed"},
            ),
        )
        assert _request(server, "/run", method="POST", payload=payload)[0] == 500
        assert _request(server, "/readyz")[0] == 503

        monkeypatch.setattr(
            srv,
            "_execute_run_with_wall_timeout",
            lambda *_a, **_kw: ("result", RunResult(ok=False)),
        )
        assert _request(server, "/run", method="POST", payload=payload)[0] == 400
        status, body = _request(server, "/readyz")
        assert status == 200
        checks = json.loads(body)["checks"]
        assert any(
            c["name"] == "hosted_worker" and c["status"] == "pass" for c in checks
        )
        assert not any(c["name"] == "runtime_api" for c in checks)


def _exiting_worker(result_conn, ready):
    if ready:
        result_conn.send(("ready", None))
    result_conn.close()


@pytest.mark.parametrize(("ready", "readiness_status"), [(False, 503), (True, 200)])
def test_readiness_distinguishes_startup_failure_from_workload_exit(
    monkeypatch, ready, readiness_status
):
    import geno.server as srv

    def execute(*_args, worker_owner=None, **_kwargs):
        return srv._execute_worker_with_wall_timeout(
            _exiting_worker, (ready,), 1.0, worker_owner=worker_owner
        )

    server = create_server("127.0.0.1", 0, startup_errors=[])
    monkeypatch.setattr(srv, "_execute_run_with_wall_timeout", execute)
    with _serving(server):
        payload = {"source": "func main() -> Int\n    return 1\nend func"}
        assert _request(server, "/run", method="POST", payload=payload)[0] == 500
        assert _request(server, "/readyz")[0] == readiness_status


@pytest.mark.parametrize("path", ["/healthz", "/readyz", "/livez"])
def test_local_health_probes_keep_host_exception_on_public_bind(path):
    server = create_server(
        "0.0.0.0",
        0,
        api_key="review-secret",
        allowed_hosts={"geno.example"},
        startup_errors=[],
    )
    with _serving(server):
        conn = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=3
        )
        try:
            conn.request("GET", path, headers={"Host": "127.0.0.1"})
            response = conn.getresponse()
            response.read()
            assert response.status == 200
        finally:
            conn.close()


@pytest.mark.parametrize("path", ["/healthz", "/readyz", "/livez"])
def test_remote_peers_cannot_use_the_health_probe_host_exception(monkeypatch, path):
    import geno.server as srv

    monkeypatch.setattr(srv, "_peer_is_loopback", lambda _handler: False)
    server = create_server(
        "0.0.0.0",
        0,
        api_key="review-secret",
        allowed_hosts={"geno.example"},
        startup_errors=[],
    )
    with _serving(server):
        conn = http.client.HTTPConnection(
            "127.0.0.1", server.server_address[1], timeout=3
        )
        try:
            conn.request("GET", path, headers={"Host": "127.0.0.1"})
            response = conn.getresponse()
            response.read()
            assert response.status == 421
        finally:
            conn.close()
