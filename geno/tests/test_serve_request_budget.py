"""Regression tests for the ``--cap serve`` per-request execution budget.

``http_listen`` runs inside the program's own execution, which carries a
program-wide wall-clock deadline (``sandbox_config.timeout``) and a cumulative
step counter. Before the fix, request handlers reused those, so once the program
deadline passed or the step budget was exhausted (a few seconds after startup)
the server stayed alive but rejected every request — a zombified server. The fix
gives each request a fresh budget while keeping it individually bounded.
"""

from geno._serve import (
    _build_checked_http_request,
    _run_serve_handler,
    install_serve_callbacks,
)
from geno.interpreter import Interpreter
from geno.parser import parse
from geno.sandbox import SandboxConfig, StepLimitExceeded
from geno.sandbox import TimeoutError as SandboxTimeout

_OK_HANDLER = """func handle(req: HttpRequest) -> HttpResponse
    return http_respond(200, [], "ok")
end func
func main() -> Int
    return 0
end func
"""

_EXPENSIVE_HANDLER = """func fib(n: Int) -> Int
    if n < 2 then
        return n
    end if
    return fib(n - 1) + fib(n - 2)
end func
func handle(req: HttpRequest) -> HttpResponse
    let x: Int = fib(30)
    return http_respond(200, [], "done")
end func
func main() -> Int
    return 0
end func
"""


def _serve_interpreter(source: str, *, timeout=5.0, max_steps=None) -> Interpreter:
    config = (
        SandboxConfig(timeout=timeout)
        if max_steps is None
        else SandboxConfig(timeout=timeout, max_steps=max_steps)
    )
    interp = Interpreter(
        check_examples=False, capabilities={"serve"}, sandbox_config=config
    )
    install_serve_callbacks(interp)
    interp.run(parse(source))
    return interp


def test_handler_runs_after_program_budget_exhausted():
    """A request must succeed even after the program-wide deadline and step
    budget are spent — otherwise the server zombifies shortly after startup."""
    import time

    interp = _serve_interpreter(_OK_HANDLER)
    handler = interp.global_env.bindings["handle"]
    request = _build_checked_http_request(interp, "GET", "/", [], "")

    # Simulate a server that has been running past its program deadline and has
    # exhausted the cumulative step budget.
    interp._deadline = time.perf_counter() - 100
    interp.steps = interp.max_steps or 10**9

    response = _run_serve_handler(interp, handler, request)
    assert response.fields["status"] == 200
    assert response.fields["body"] == "ok"


def test_run_serve_handler_restores_interpreter_state():
    import time

    interp = _serve_interpreter(_OK_HANDLER)
    handler = interp.global_env.bindings["handle"]
    request = _build_checked_http_request(interp, "GET", "/", [], "")

    sentinel_deadline = time.perf_counter() - 100
    sentinel_steps = 777
    interp._deadline = sentinel_deadline
    interp.steps = sentinel_steps

    _run_serve_handler(interp, handler, request)

    # Per-request state must not leak: the interpreter's prior deadline/steps are
    # restored so the serve loop's own bookkeeping is unchanged.
    assert interp._deadline == sentinel_deadline
    assert interp.steps == sentinel_steps


_PRINTING_HANDLER = """func handle(req: HttpRequest) -> HttpResponse
    print("hello there this is some request output")
    return http_respond(200, [], "ok")
end func
func main() -> Int
    return 0
end func
"""


def test_output_budget_is_per_request():
    """Cumulative print output must not zombie the server: each request gets a
    fresh output budget, otherwise the Nth request trips max_output_length and
    every later request 500s."""
    interp = Interpreter(
        check_examples=False,
        capabilities={"serve", "print"},
        sandbox_config=SandboxConfig(timeout=5.0, max_output_length=50),
    )
    install_serve_callbacks(interp)
    interp.run(parse(_PRINTING_HANDLER))
    handler = interp.global_env.bindings["handle"]
    request = _build_checked_http_request(interp, "GET", "/", [], "")

    # Each request prints ~40 chars; cumulatively that would exceed the 50-char
    # cap after the second request without a per-request reset.
    for _ in range(20):
        response = _run_serve_handler(interp, handler, request)
        assert response.fields["status"] == 200


def test_handler_is_still_bounded_per_request():
    """The fresh per-request budget must NOT be unbounded: a handler that exceeds
    max_steps must still be stopped (the sandbox promise is preserved)."""
    interp = _serve_interpreter(_EXPENSIVE_HANDLER, max_steps=5000)
    handler = interp.global_env.bindings["handle"]
    request = _build_checked_http_request(interp, "GET", "/", [], "")

    try:
        _run_serve_handler(interp, handler, request)
        raise AssertionError("expensive handler was not bounded per request")
    except (StepLimitExceeded, SandboxTimeout):
        pass


_RAISING_HANDLER = """func handle(req: HttpRequest) -> HttpResponse
    let xs: List[Int] = []
    let y: Int = xs[10]
    return http_respond(200, [], "unreached")
end func
func main() -> Int
    http_route("GET", "/boom", handle)
    return 0
end func
"""


def test_serve_handler_error_returns_generic_500_and_logs(monkeypatch, caplog):
    """M-05: a serve handler exception must return a generic 500 (no leaked
    handler detail) and be logged server-side with a traceback."""
    import http.client
    import http.server as _http_server_mod
    import time
    from threading import Thread

    captured: dict = {}

    class CapturingServer(_http_server_mod.HTTPServer):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            captured["server"] = self

    # install_serve_callbacks captures HTTPServer as a closure local, so patch
    # before building the interpreter.
    monkeypatch.setattr(_http_server_mod, "HTTPServer", CapturingServer)

    interp = _serve_interpreter(_RAISING_HANDLER)
    http_listen = interp.global_env.bindings["http_listen"].func

    thread = Thread(target=lambda: http_listen(0), daemon=True)
    with caplog.at_level("ERROR", logger="geno._serve"):
        thread.start()
        deadline = time.time() + 5
        while "server" not in captured and time.time() < deadline:
            time.sleep(0.01)
        assert "server" in captured, "serve server never bound"
        server = captured["server"]
        # H-05: the handler must carry a per-connection socket timeout so a
        # stalled client cannot wedge the single-threaded server.
        assert server.RequestHandlerClass.timeout == 30
        try:
            conn = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            conn.request("GET", "/boom")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            conn.close()
        finally:
            server.shutdown()
            server.server_close()

    assert resp.status == 500
    # Generic message only — no leaked exception detail (index/bounds/etc.).
    assert body == "Internal Server Error"
    assert "index" not in body.lower()
    # But the failure is logged server-side with a traceback.
    assert any(
        "Unhandled error in serve handler" in rec.message and rec.exc_info is not None
        for rec in caplog.records
    )
