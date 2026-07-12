"""Tests for deploy-facing monitoring and health contracts."""

import pytest

import geno
from geno.api import RunConfig, run
from geno.monitoring import HealthCheck, RunMetrics, RunOutcome


class TestMonitoringHook:
    """Test RunConfig.monitoring_hook integration with geno.run()."""

    def test_monitoring_hook_receives_success_metrics(self):
        observed: list[RunMetrics] = []

        source = """
        func main() -> Int
            return 42
        end func
        """
        result = run(source, config=RunConfig(monitoring_hook=observed.append))

        assert result.ok is True
        assert len(observed) == 1
        assert observed[0].outcome is RunOutcome.SUCCESS
        assert observed[0].ok is True
        assert observed[0].steps_used == result.steps_used
        assert observed[0].diagnostic_codes == ()

    def test_monitoring_hook_receives_success_metrics_with_positional_config(self):
        observed: list[RunMetrics] = []

        source = """
        func main() -> Int
            return 42
        end func
        """
        result = run(source, RunConfig(monitoring_hook=observed.append))

        assert result.ok is True
        assert len(observed) == 1
        assert observed[0].outcome is RunOutcome.SUCCESS
        assert observed[0].ok is True

    def test_monitoring_hook_receives_parse_failure_metrics(self):
        observed: list[RunMetrics] = []

        result = run("func -> end", config=RunConfig(monitoring_hook=observed.append))

        assert result.ok is False
        assert len(observed) == 1
        assert observed[0].outcome is RunOutcome.SYNTAX_ERROR
        assert (
            geno.ErrorCode.PARSE_UNEXPECTED_TOKEN.value in observed[0].diagnostic_codes
        )

    def test_monitoring_hook_receives_capability_denied_metrics(self):
        observed: list[RunMetrics] = []

        source = """
        func main() -> Int
            print(42)
            return 0
        end func
        """
        result = run(
            source,
            config=RunConfig(capabilities=set(), monitoring_hook=observed.append),
        )

        assert result.ok is False
        assert len(observed) == 1
        assert observed[0].outcome is RunOutcome.CAPABILITY_DENIED
        assert (
            geno.ErrorCode.RUNTIME_CAPABILITY_DENIED.value
            in observed[0].diagnostic_codes
        )

    def test_monitoring_hook_failures_do_not_change_run_result(self):
        def broken_hook(_metrics: RunMetrics) -> None:
            raise RuntimeError("sink unavailable")

        source = """
        func main() -> Int
            return 7
        end func
        """
        with pytest.warns(RuntimeWarning, match="monitoring_hook raised RuntimeError"):
            result = run(source, config=RunConfig(monitoring_hook=broken_hook))

        assert result.ok is True
        assert result.value == 7


class TestRuntimeMetricsCollector:
    """Test the in-memory collector and serialization helpers."""

    def test_collector_builds_health_and_metrics_snapshots(self):
        collector = geno.RuntimeMetricsCollector(
            service="geno-api",
            revision="abc123",
        )
        collector.record_http_post_request(endpoint="/run", status=200, outcome="ok")
        collector.record_http_post_request(
            endpoint="/constrain", status=401, outcome="auth_failed"
        )
        collector.record_http_post_request(
            endpoint="/run", status=400, outcome="bad_request"
        )
        collector.record(
            RunMetrics(
                outcome=RunOutcome.SUCCESS,
                ok=True,
                wall_time_ms=12.5,
                steps_used=4,
            )
        )
        collector.record(
            RunMetrics(
                outcome=RunOutcome.TIMEOUT,
                ok=False,
                wall_time_ms=30.0,
                steps_used=10,
                diagnostic_codes=(geno.ErrorCode.SANDBOX_TIMEOUT.value,),
            )
        )
        collector.record_constrain_result(valid=True, wall_time_ms=1.5)
        collector.record_constrain_result(valid=False, wall_time_ms=2.5)
        collector.record_constrain_result(valid=None, wall_time_ms=3.5)
        collector.record_benchmark_validation(
            total_problems=77,
            canonical_passed=77,
            problems_with_issues=0,
        )

        snapshot = collector.snapshot()
        assert snapshot.http_post_requests == 3
        assert snapshot.http_post_requests_by_endpoint == {
            "/constrain": 1,
            "/run": 2,
        }
        assert snapshot.http_post_requests_by_status == {"200": 1, "400": 1, "401": 1}
        assert snapshot.http_post_requests_by_outcome == {
            "auth_failed": 1,
            "bad_request": 1,
            "ok": 1,
        }
        assert snapshot.total_runs == 2
        assert snapshot.successful_runs == 1
        assert snapshot.timeout_runs == 1
        assert snapshot.total_steps == 14
        assert snapshot.average_wall_time_ms == pytest.approx(21.25)
        assert snapshot.average_steps == pytest.approx(7.0)
        assert snapshot.constrain_requests == 3
        assert snapshot.valid_constrain_requests == 1
        assert snapshot.invalid_constrain_requests == 1
        assert snapshot.timeout_constrain_requests == 1
        assert snapshot.average_constrain_wall_time_ms == pytest.approx(2.5)

        snapshot_dict = snapshot.to_dict()
        assert snapshot_dict["http_post_requests"] == 3
        assert snapshot_dict["http_post_requests_by_endpoint"] == {
            "/constrain": 1,
            "/run": 2,
        }
        assert snapshot_dict["success_rate"] == 0.5
        assert snapshot_dict["constrain_requests"] == 3
        assert snapshot_dict["valid_constrain_requests"] == 1
        assert snapshot_dict["timeout_constrain_requests"] == 1
        assert snapshot_dict["last_benchmark_validation"]["canonical_pass_rate"] == 1.0

        prom = snapshot.to_prometheus_text()
        assert "geno_http_post_requests_total 3" in prom
        assert 'geno_http_post_requests_by_endpoint_total{endpoint="/run"} 2' in prom
        assert 'geno_http_post_requests_by_status_total{status="401"} 1' in prom
        assert (
            'geno_http_post_requests_by_outcome_total{outcome="auth_failed"} 1' in prom
        )
        assert "geno_run_requests_total 2" in prom
        assert "geno_run_timeout_total 1" in prom
        assert "geno_constrain_requests_total 3" in prom
        assert "geno_constrain_invalid_total 1" in prom
        assert "geno_constrain_timeout_total 1" in prom
        assert "geno_benchmark_validation_total 1" in prom

        # Verify Prometheus exposition format compliance (# HELP and # TYPE)
        for line in prom.splitlines():
            if line.startswith("#"):
                assert line.startswith("# HELP ") or line.startswith("# TYPE "), (
                    f"Comment line must be # HELP or # TYPE: {line!r}"
                )

        health = collector.health_report(
            extra_checks=[
                HealthCheck(name="downstream_sink", status="warn", detail="lagging"),
            ]
        )
        assert health.status == "degraded"
        health_dict = health.to_dict()
        assert health_dict["build"]["service"] == "geno-api"
        assert health_dict["build"]["version"] == geno.__version__
        assert any(
            check["name"] == "metrics_collector"
            and "observed_http_post_requests=3" in check["detail"]
            and "observed_constrain_requests=3" in check["detail"]
            for check in health_dict["checks"]
        )
        assert any(
            check["name"] == "benchmark_validation" for check in health_dict["checks"]
        )

    def test_startup_errors_reported_in_health(self):
        """Startup failures should appear as a failed check in health_report."""
        collector = geno.RuntimeMetricsCollector(service="test")
        collector.record_startup_errors(["sandbox broke", "bad python"])
        report = collector.health_report()
        assert report.status == "failed"
        startup_check = next(c for c in report.checks if c.name == "startup_checks")
        assert startup_check.status == "fail"
        assert "sandbox broke" in startup_check.detail
        assert "bad python" in startup_check.detail

    def test_no_startup_errors_passes_health(self):
        """No startup errors means the startup_checks check passes."""
        collector = geno.RuntimeMetricsCollector(service="test")
        report = collector.health_report()
        startup_check = next(c for c in report.checks if c.name == "startup_checks")
        assert startup_check.status == "pass"
        assert report.status == "ok"
