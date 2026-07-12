"""Smoke tests for the monitoring HTTP adapter example."""

import runpy
from pathlib import Path

import geno


def test_monitoring_http_adapter_example_loads_and_creates_server():
    repo_root = Path(__file__).resolve().parents[2]
    module = runpy.run_path(str(repo_root / "examples" / "monitoring_http_adapter.py"))

    server = module["create_server"](
        "127.0.0.1",
        0,
        service="test-service",
        bind_and_activate=False,
    )
    try:
        assert isinstance(server.collector, geno.RuntimeMetricsCollector)
        assert server.collector.health_report().build.service == "test-service"
    finally:
        server.server_close()
