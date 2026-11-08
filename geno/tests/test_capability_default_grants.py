"""Each surface's omitted-capabilities grant matches spec.json (#248).

``spec.json`` ``capabilities.default_grants`` is the normative statement of
what each execution surface grants when capabilities are omitted.
``scripts/validate_spec.py`` checks it against the source constants; these
tests check it against what each surface actually does at runtime.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]

UNIT_MAIN = "func main() -> Unit\n    return ()\nend func\n"
EXAMPLE_ONLY = "func one() -> Int\n    example () -> 1\n    return 1\nend func\n"


def _spec_grant(lane: str) -> set[str]:
    spec = json.loads((ROOT / "spec.json").read_text())
    return set(spec["capabilities"]["default_grants"][lane])


def _effective_grant(interpreter: Any) -> set[str]:
    """Capabilities whose builtins are not capability-denied stubs."""
    from geno.builtin_registry import CAPABILITY_MAP

    bindings = interpreter.global_env.bindings
    granted = set()
    for capability, names in CAPABILITY_MAP.items():
        bound = [bindings[name] for name in names if name in bindings]
        assert bound, f"no builtin of {capability!r} is bound"
        if not any(getattr(b.func, "__name__", "") == "denied" for b in bound):
            granted.add(capability)
    return granted


@pytest.fixture
def created_interpreters(monkeypatch):
    """Record every Interpreter a surface constructs, after its grant applies."""
    from geno.interpreter import Interpreter

    created: list[Any] = []
    original_init = Interpreter.__init__

    def recording_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(Interpreter, "__init__", recording_init)
    return created


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "probe.geno"
    path.write_text(source)
    return path


def test_embedding_api_default_grant(created_interpreters):
    import geno

    result = geno.run(UNIT_MAIN)

    assert result.ok, result.diagnostics
    assert created_interpreters
    for interpreter in created_interpreters:
        assert _effective_grant(interpreter) == _spec_grant("embedding_api")


def test_geno_run_unsafe_default_grant(tmp_path, created_interpreters):
    from geno.cli.run import run_file

    assert run_file(str(_write(tmp_path, UNIT_MAIN)), unsafe=True) is None

    assert len(created_interpreters) == 1
    assert _effective_grant(created_interpreters[0]) == _spec_grant("geno_run_unsafe")


def test_geno_run_json_default_grant(tmp_path, capsys, created_interpreters):
    from geno.cli.run import run_file

    run_file(str(_write(tmp_path, UNIT_MAIN)), json_output=True)

    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert created_interpreters
    for interpreter in created_interpreters:
        assert _effective_grant(interpreter) == _spec_grant("geno_run_json")


def test_geno_run_default_lane_grant(tmp_path, created_interpreters):
    """Examples and ``main`` in the isolated lane both get the default grant."""
    from geno.cli.run import _prepare_process_run

    payload = _prepare_process_run(
        {
            "filename": str(_write(tmp_path, EXAMPLE_ONLY + UNIT_MAIN)),
            "target": None,
            "check_examples": True,
            "timeout": 5.0,
            "max_recursion_depth": 100,
            "max_output_length": 10_000,
            "max_collection_size": 10_000,
            "max_integer_bits": 4096,
        }
    )

    expected = _spec_grant("geno_run")
    assert set(payload["runtime_capabilities"]) == expected
    assert len(created_interpreters) == 1  # the example pre-pass
    assert _effective_grant(created_interpreters[0]) == expected


def test_geno_test_default_grant(tmp_path, created_interpreters):
    from geno.test_runner import run_test_suite

    suite = run_test_suite([_write(tmp_path, EXAMPLE_ONLY)])

    assert [result.error for result in suite.file_results] == [None]
    assert created_interpreters
    for interpreter in created_interpreters:
        assert _effective_grant(interpreter) == _spec_grant("geno_test")


def test_repl_default_grant():
    from geno.repl import REPL

    assert _effective_grant(REPL().interpreter) == _spec_grant("repl")


def test_browser_build_grant():
    from geno.js_compiler import compile_to_html

    html = compile_to_html(UNIT_MAIN)

    match = re.search(r"globalThis\.__GENO_CAPS = (\[[^\]]*\]);", html)
    assert match is not None
    assert set(json.loads(match.group(1))) == _spec_grant("browser_builds")


def test_compiled_python_default_grant(monkeypatch):
    from geno._runtime_support import _geno_parse_caps

    monkeypatch.setattr(sys, "argv", ["out.py"])
    assert _geno_parse_caps() == _spec_grant("compiled_programs")
    # A list replaces the default rather than adding to it.
    monkeypatch.setattr(sys, "argv", ["out.py", "--cap", "fs"])
    assert _geno_parse_caps() == {"fs"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_compiled_node_default_grant(tmp_path):
    """Node output without --cap denies every capability, including print."""
    from geno.js_compiler import compile_to_js

    assert _spec_grant("compiled_programs") == set()
    js = tmp_path / "probe.js"
    js.write_text(
        compile_to_js('func main() -> Unit with io\n    print("x")\nend func\n')
    )

    denied = subprocess.run(
        ["node", str(js)], capture_output=True, text=True, timeout=60
    )
    granted = subprocess.run(
        ["node", str(js), "--cap", "print"], capture_output=True, text=True, timeout=60
    )

    assert denied.returncode != 0
    assert "Capability denied: 'print'" in denied.stderr
    assert granted.returncode == 0, granted.stderr
    assert granted.stdout == "x\n"


HOSTED_PROBES = {
    "print": 'func main() -> Unit with io\n    print("x")\nend func\n',
    "clock": "func main() -> Int with clock\n    return clock_now()\nend func\n",
    "random": "func main() -> Int with random\n    return random_int(1, 1)\nend func\n",
}


def test_hosted_server_default_grant():
    """A /run request that omits capabilities is denied every allowed one."""
    import http.client

    from geno.capabilities import DEFAULT_ALLOWED_CAPABILITIES
    from geno.server import create_server

    assert set(HOSTED_PROBES) == set(DEFAULT_ALLOWED_CAPABILITIES)
    try:
        server = create_server("127.0.0.1", 0, bind_and_activate=True)
    except PermissionError as exc:
        pytest.skip(f"local socket binds not permitted: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.02)
    host, port = server.server_address

    def run(source: str, **extra: Any) -> dict[str, Any]:
        body = json.dumps({"source": source, **extra}).encode()
        conn = http.client.HTTPConnection(host, port, timeout=30)
        conn.request(
            "POST",
            "/run",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        data = cast(dict[str, Any], json.loads(response.read()))
        conn.close()
        return data

    try:
        granted = set()
        for capability, source in sorted(HOSTED_PROBES.items()):
            data = run(source)
            message = json.dumps(data.get("diagnostics", data))
            if "WorkerSpawnFailed" in message:
                pytest.skip("worker process spawning is not permitted")
            if data.get("ok"):
                granted.add(capability)
            else:
                assert f"requires the '{capability}' capability" in message
            # Requesting the capability explicitly grants it.
            assert run(source, capabilities=[capability])["ok"] is True
        assert granted == _spec_grant("hosted_server")
    finally:
        server.shutdown()
        server.server_close()
