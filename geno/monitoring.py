"""Deploy-facing health and metrics contract for hosted Geno runtimes."""

from __future__ import annotations

import platform
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from ._version import __version__
from .diagnostics import ErrorCode


class RunOutcome(Enum):
    """Primary outcome classification for a single geno.run() call."""

    SUCCESS = "success"
    SYNTAX_ERROR = "syntax_error"
    TYPE_ERROR = "type_error"
    RUNTIME_ERROR = "runtime_error"
    TIMEOUT = "timeout"
    CAPABILITY_DENIED = "capability_denied"
    HOST_CALLBACK_MISSING = "host_callback_missing"
    SECURITY_VIOLATION = "security_violation"
    RESOURCE_LIMIT = "resource_limit"


@dataclass(frozen=True)
class RunMetrics:
    """Normalized metrics payload emitted for a single geno.run() result."""

    outcome: RunOutcome
    ok: bool
    wall_time_ms: float
    steps_used: int
    diagnostic_codes: tuple[str, ...] = ()

    @classmethod
    def from_run_result(cls, result) -> RunMetrics:
        codes = tuple(
            diag.code.value
            for diag in result.diagnostics
            if getattr(diag, "code", None) is not None
        )
        return cls(
            outcome=_classify_run_outcome(result.diagnostics, result.ok),
            ok=result.ok,
            wall_time_ms=result.timing.total_ms,
            steps_used=result.steps_used,
            diagnostic_codes=codes,
        )

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "ok": self.ok,
            "wall_time_ms": round(self.wall_time_ms, 2),
            "steps_used": self.steps_used,
            "diagnostic_codes": list(self.diagnostic_codes),
        }


MonitoringHook = Callable[[RunMetrics], None]


@dataclass(frozen=True)
class BuildInfo:
    """Build identity reported by deploy-facing health endpoints."""

    service: str = "geno-runtime"
    version: str = __version__
    revision: str | None = None
    python_version: str = field(default_factory=platform.python_version)

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "version": self.version,
            "revision": self.revision,
            "python_version": self.python_version,
        }


@dataclass(frozen=True)
class HealthCheck:
    """Single named health check in a health report."""

    name: str
    status: str
    detail: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class BenchmarkValidationState:
    """Latest benchmark validation result known to the process."""

    success: bool
    total_problems: int
    canonical_passed: int
    problems_with_issues: int

    @property
    def canonical_pass_rate(self) -> float:
        if self.total_problems == 0:
            return 0.0
        return self.canonical_passed / self.total_problems

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "total_problems": self.total_problems,
            "canonical_passed": self.canonical_passed,
            "canonical_pass_rate": round(self.canonical_pass_rate, 4),
            "problems_with_issues": self.problems_with_issues,
        }


@dataclass(frozen=True)
class MetricsSnapshot:
    """Process-local metrics snapshot suitable for JSON or Prometheus export."""

    http_post_requests: int = 0
    http_post_requests_by_endpoint: dict[str, int] = field(default_factory=dict)
    http_post_requests_by_status: dict[str, int] = field(default_factory=dict)
    http_post_requests_by_outcome: dict[str, int] = field(default_factory=dict)
    total_runs: int = 0
    successful_runs: int = 0
    syntax_error_runs: int = 0
    type_error_runs: int = 0
    runtime_error_runs: int = 0
    timeout_runs: int = 0
    capability_denied_runs: int = 0
    host_callback_missing_runs: int = 0
    security_violation_runs: int = 0
    resource_limit_runs: int = 0
    total_wall_time_ms: float = 0.0
    average_wall_time_ms: float = 0.0
    total_steps: int = 0
    average_steps: float = 0.0
    constrain_requests: int = 0
    valid_constrain_requests: int = 0
    invalid_constrain_requests: int = 0
    timeout_constrain_requests: int = 0
    total_constrain_wall_time_ms: float = 0.0
    average_constrain_wall_time_ms: float = 0.0
    benchmark_validations: int = 0
    benchmark_validation_failures: int = 0
    last_benchmark_validation: BenchmarkValidationState | None = None

    def to_dict(self) -> dict:
        return {
            "http_post_requests": self.http_post_requests,
            "http_post_requests_by_endpoint": dict(
                sorted(self.http_post_requests_by_endpoint.items())
            ),
            "http_post_requests_by_status": dict(
                sorted(self.http_post_requests_by_status.items())
            ),
            "http_post_requests_by_outcome": dict(
                sorted(self.http_post_requests_by_outcome.items())
            ),
            "total_runs": self.total_runs,
            "successful_runs": self.successful_runs,
            "failed_runs": self.total_runs - self.successful_runs,
            "success_rate": (
                round(self.successful_runs / self.total_runs, 4)
                if self.total_runs
                else 0.0
            ),
            "syntax_error_runs": self.syntax_error_runs,
            "type_error_runs": self.type_error_runs,
            "runtime_error_runs": self.runtime_error_runs,
            "timeout_runs": self.timeout_runs,
            "capability_denied_runs": self.capability_denied_runs,
            "host_callback_missing_runs": self.host_callback_missing_runs,
            "security_violation_runs": self.security_violation_runs,
            "resource_limit_runs": self.resource_limit_runs,
            "total_wall_time_ms": round(self.total_wall_time_ms, 2),
            "average_wall_time_ms": round(self.average_wall_time_ms, 2),
            "total_steps": self.total_steps,
            "average_steps": round(self.average_steps, 2),
            "constrain_requests": self.constrain_requests,
            "valid_constrain_requests": self.valid_constrain_requests,
            "invalid_constrain_requests": self.invalid_constrain_requests,
            "timeout_constrain_requests": self.timeout_constrain_requests,
            "total_constrain_wall_time_ms": round(self.total_constrain_wall_time_ms, 2),
            "average_constrain_wall_time_ms": round(
                self.average_constrain_wall_time_ms, 2
            ),
            "benchmark_validations": self.benchmark_validations,
            "benchmark_validation_failures": self.benchmark_validation_failures,
            "last_benchmark_validation": (
                self.last_benchmark_validation.to_dict()
                if self.last_benchmark_validation is not None
                else None
            ),
        }

    def to_prometheus_text(self) -> str:
        def _metric(name: str, mtype: str, helptext: str, value) -> list[str]:
            return [
                f"# HELP {name} {helptext}",
                f"# TYPE {name} {mtype}",
                f"{name} {value}",
            ]

        def _escape_label_value(value: str) -> str:
            return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

        def _labeled_metric(
            name: str,
            mtype: str,
            helptext: str,
            label_name: str,
            series: dict[str, int],
        ) -> list[str]:
            lines = [
                f"# HELP {name} {helptext}",
                f"# TYPE {name} {mtype}",
            ]
            for label_value, value in sorted(series.items()):
                lines.append(
                    f'{name}{{{label_name}="{_escape_label_value(label_value)}"}} {value}'
                )
            return lines

        lines: list[str] = []
        lines += _metric(
            "geno_http_post_requests_total",
            "counter",
            "Total handled POST requests for hosted execution endpoints.",
            self.http_post_requests,
        )
        if self.http_post_requests_by_endpoint:
            lines += _labeled_metric(
                "geno_http_post_requests_by_endpoint_total",
                "counter",
                "Handled POST requests grouped by endpoint.",
                "endpoint",
                self.http_post_requests_by_endpoint,
            )
        if self.http_post_requests_by_status:
            lines += _labeled_metric(
                "geno_http_post_requests_by_status_total",
                "counter",
                "Handled POST requests grouped by HTTP status code.",
                "status",
                self.http_post_requests_by_status,
            )
        if self.http_post_requests_by_outcome:
            lines += _labeled_metric(
                "geno_http_post_requests_by_outcome_total",
                "counter",
                "Handled POST requests grouped by server outcome classification.",
                "outcome",
                self.http_post_requests_by_outcome,
            )
        lines += _metric(
            "geno_run_requests_total",
            "counter",
            "Total geno.run() invocations.",
            self.total_runs,
        )
        lines += _metric(
            "geno_run_success_total",
            "counter",
            "Successful runs.",
            self.successful_runs,
        )
        lines += _metric(
            "geno_run_syntax_error_total",
            "counter",
            "Runs that failed with a syntax error.",
            self.syntax_error_runs,
        )
        lines += _metric(
            "geno_run_type_error_total",
            "counter",
            "Runs that failed with a type error.",
            self.type_error_runs,
        )
        lines += _metric(
            "geno_run_runtime_error_total",
            "counter",
            "Runs that failed with a runtime error.",
            self.runtime_error_runs,
        )
        lines += _metric(
            "geno_run_timeout_total",
            "counter",
            "Runs that timed out.",
            self.timeout_runs,
        )
        lines += _metric(
            "geno_run_capability_denied_total",
            "counter",
            "Runs denied by capability check.",
            self.capability_denied_runs,
        )
        lines += _metric(
            "geno_run_host_callback_missing_total",
            "counter",
            "Runs with missing host callback.",
            self.host_callback_missing_runs,
        )
        lines += _metric(
            "geno_run_security_violation_total",
            "counter",
            "Runs with security violation.",
            self.security_violation_runs,
        )
        lines += _metric(
            "geno_run_resource_limit_total",
            "counter",
            "Runs hitting resource limits.",
            self.resource_limit_runs,
        )
        lines += _metric(
            "geno_run_wall_time_ms_total",
            "counter",
            "Cumulative wall time in ms.",
            f"{self.total_wall_time_ms:.2f}",
        )
        lines += _metric(
            "geno_run_wall_time_ms_average",
            "gauge",
            "Average wall time per run in ms.",
            f"{self.average_wall_time_ms:.2f}",
        )
        lines += _metric(
            "geno_run_steps_total",
            "counter",
            "Cumulative interpreter steps.",
            self.total_steps,
        )
        lines += _metric(
            "geno_run_steps_average",
            "gauge",
            "Average interpreter steps per run.",
            f"{self.average_steps:.2f}",
        )
        lines += _metric(
            "geno_constrain_requests_total",
            "counter",
            "Total /constrain requests that reached constraint evaluation.",
            self.constrain_requests,
        )
        lines += _metric(
            "geno_constrain_valid_total",
            "counter",
            "Constrain requests with a valid Geno prefix.",
            self.valid_constrain_requests,
        )
        lines += _metric(
            "geno_constrain_invalid_total",
            "counter",
            "Constrain requests with an invalid Geno prefix.",
            self.invalid_constrain_requests,
        )
        lines += _metric(
            "geno_constrain_timeout_total",
            "counter",
            "Constrain requests that timed out before producing a result.",
            self.timeout_constrain_requests,
        )
        lines += _metric(
            "geno_constrain_wall_time_ms_total",
            "counter",
            "Cumulative constraint evaluation wall time in ms.",
            f"{self.total_constrain_wall_time_ms:.2f}",
        )
        lines += _metric(
            "geno_constrain_wall_time_ms_average",
            "gauge",
            "Average constraint evaluation wall time per request in ms.",
            f"{self.average_constrain_wall_time_ms:.2f}",
        )
        lines += _metric(
            "geno_benchmark_validation_total",
            "counter",
            "Total benchmark validations.",
            self.benchmark_validations,
        )
        lines += _metric(
            "geno_benchmark_validation_failures_total",
            "counter",
            "Failed benchmark validations.",
            self.benchmark_validation_failures,
        )
        if self.last_benchmark_validation is not None:
            lines += _metric(
                "geno_benchmark_last_canonical_pass_rate",
                "gauge",
                "Pass rate from the last benchmark validation.",
                f"{self.last_benchmark_validation.canonical_pass_rate:.4f}",
            )
            lines += _metric(
                "geno_benchmark_last_problems_with_issues",
                "gauge",
                "Problems with issues from the last benchmark validation.",
                f"{self.last_benchmark_validation.problems_with_issues}",
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class HealthReport:
    """Serialized health response for hosted Geno deployments."""

    status: str
    generated_at_unix: float
    uptime_seconds: float
    build: BuildInfo
    checks: tuple[HealthCheck, ...]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "generated_at_unix": round(self.generated_at_unix, 3),
            "uptime_seconds": round(self.uptime_seconds, 3),
            "build": self.build.to_dict(),
            "checks": [check.to_dict() for check in self.checks],
        }


class RuntimeMetricsCollector:
    """Process-local collector suitable for a health or metrics endpoint."""

    def __init__(self, service: str = "geno-runtime", revision: str | None = None):
        self._build = BuildInfo(service=service, revision=revision)
        self._started_at = time.monotonic()
        self._lock = threading.Lock()
        self._startup_errors: list[str] = []
        self._http_post_requests = 0
        self._http_post_requests_by_endpoint: dict[str, int] = {}
        self._http_post_requests_by_status: dict[str, int] = {}
        self._http_post_requests_by_outcome: dict[str, int] = {}
        self._total_runs = 0
        self._successful_runs = 0
        self._syntax_error_runs = 0
        self._type_error_runs = 0
        self._runtime_error_runs = 0
        self._timeout_runs = 0
        self._capability_denied_runs = 0
        self._host_callback_missing_runs = 0
        self._security_violation_runs = 0
        self._resource_limit_runs = 0
        self._total_wall_time_ms = 0.0
        self._total_steps = 0
        self._constrain_requests = 0
        self._valid_constrain_requests = 0
        self._invalid_constrain_requests = 0
        self._timeout_constrain_requests = 0
        self._total_constrain_wall_time_ms = 0.0
        self._benchmark_validations = 0
        self._benchmark_validation_failures = 0
        self._last_benchmark_validation: BenchmarkValidationState | None = None

    def record_startup_errors(self, errors: list[str]) -> None:
        """Record startup check failures for health reporting."""
        with self._lock:
            self._startup_errors = list(errors)

    def record(self, metrics: RunMetrics) -> None:
        with self._lock:
            self._total_runs += 1
            self._total_wall_time_ms += metrics.wall_time_ms
            self._total_steps += metrics.steps_used

            if metrics.ok:
                self._successful_runs += 1

            if metrics.outcome is RunOutcome.SYNTAX_ERROR:
                self._syntax_error_runs += 1
            elif metrics.outcome is RunOutcome.TYPE_ERROR:
                self._type_error_runs += 1
            elif metrics.outcome is RunOutcome.RUNTIME_ERROR:
                self._runtime_error_runs += 1
            elif metrics.outcome is RunOutcome.TIMEOUT:
                self._timeout_runs += 1
            elif metrics.outcome is RunOutcome.CAPABILITY_DENIED:
                self._capability_denied_runs += 1
            elif metrics.outcome is RunOutcome.HOST_CALLBACK_MISSING:
                self._host_callback_missing_runs += 1
            elif metrics.outcome is RunOutcome.SECURITY_VIOLATION:
                self._security_violation_runs += 1
            elif metrics.outcome is RunOutcome.RESOURCE_LIMIT:
                self._resource_limit_runs += 1

    def record_http_post_request(
        self, *, endpoint: str, status: int, outcome: str
    ) -> None:
        status_key = str(status)
        with self._lock:
            self._http_post_requests += 1
            self._http_post_requests_by_endpoint[endpoint] = (
                self._http_post_requests_by_endpoint.get(endpoint, 0) + 1
            )
            self._http_post_requests_by_status[status_key] = (
                self._http_post_requests_by_status.get(status_key, 0) + 1
            )
            self._http_post_requests_by_outcome[outcome] = (
                self._http_post_requests_by_outcome.get(outcome, 0) + 1
            )

    def record_run_result(self, result) -> None:
        self.record(RunMetrics.from_run_result(result))

    def record_constrain_result(
        self, *, valid: bool | None, wall_time_ms: float
    ) -> None:
        with self._lock:
            self._constrain_requests += 1
            self._total_constrain_wall_time_ms += wall_time_ms
            if valid is True:
                self._valid_constrain_requests += 1
            elif valid is False:
                self._invalid_constrain_requests += 1
            else:
                self._timeout_constrain_requests += 1

    def record_benchmark_validation(
        self,
        *,
        total_problems: int,
        canonical_passed: int,
        problems_with_issues: int,
    ) -> None:
        state = BenchmarkValidationState(
            success=(canonical_passed == total_problems and problems_with_issues == 0),
            total_problems=total_problems,
            canonical_passed=canonical_passed,
            problems_with_issues=problems_with_issues,
        )
        with self._lock:
            self._benchmark_validations += 1
            if not state.success:
                self._benchmark_validation_failures += 1
            self._last_benchmark_validation = state

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            http_post_requests = self._http_post_requests
            total_runs = self._total_runs
            total_wall_time_ms = self._total_wall_time_ms
            total_steps = self._total_steps
            constrain_requests = self._constrain_requests
            total_constrain_wall_time_ms = self._total_constrain_wall_time_ms
            last_benchmark_validation = self._last_benchmark_validation

            return MetricsSnapshot(
                http_post_requests=http_post_requests,
                http_post_requests_by_endpoint=dict(
                    self._http_post_requests_by_endpoint
                ),
                http_post_requests_by_status=dict(self._http_post_requests_by_status),
                http_post_requests_by_outcome=dict(self._http_post_requests_by_outcome),
                total_runs=total_runs,
                successful_runs=self._successful_runs,
                syntax_error_runs=self._syntax_error_runs,
                type_error_runs=self._type_error_runs,
                runtime_error_runs=self._runtime_error_runs,
                timeout_runs=self._timeout_runs,
                capability_denied_runs=self._capability_denied_runs,
                host_callback_missing_runs=self._host_callback_missing_runs,
                security_violation_runs=self._security_violation_runs,
                resource_limit_runs=self._resource_limit_runs,
                total_wall_time_ms=total_wall_time_ms,
                average_wall_time_ms=(
                    total_wall_time_ms / total_runs if total_runs else 0.0
                ),
                total_steps=total_steps,
                average_steps=(total_steps / total_runs if total_runs else 0.0),
                constrain_requests=constrain_requests,
                valid_constrain_requests=self._valid_constrain_requests,
                invalid_constrain_requests=self._invalid_constrain_requests,
                timeout_constrain_requests=self._timeout_constrain_requests,
                total_constrain_wall_time_ms=total_constrain_wall_time_ms,
                average_constrain_wall_time_ms=(
                    total_constrain_wall_time_ms / constrain_requests
                    if constrain_requests
                    else 0.0
                ),
                benchmark_validations=self._benchmark_validations,
                benchmark_validation_failures=self._benchmark_validation_failures,
                last_benchmark_validation=last_benchmark_validation,
            )

    def health_report(
        self, extra_checks: list[HealthCheck] | None = None
    ) -> HealthReport:
        snapshot = self.snapshot()
        checks: list[HealthCheck] = []
        with self._lock:
            startup_errors = list(self._startup_errors)
        if startup_errors:
            checks.append(
                HealthCheck(
                    name="startup_checks",
                    status="fail",
                    detail="; ".join(startup_errors),
                )
            )
        else:
            checks.append(
                HealthCheck(
                    name="startup_checks",
                    status="pass",
                    detail="all startup checks passed",
                )
            )
        checks.extend(
            [
                HealthCheck(
                    name="runtime_api",
                    status="pass",
                    detail="geno.run() remains the supported production entry point",
                ),
                HealthCheck(
                    name="metrics_collector",
                    status="pass",
                    detail=(
                        f"observed_http_post_requests={snapshot.http_post_requests}, "
                        f"observed_runs={snapshot.total_runs}, "
                        f"observed_constrain_requests={snapshot.constrain_requests}"
                    ),
                ),
            ]
        )
        if snapshot.last_benchmark_validation is not None:
            benchmark = snapshot.last_benchmark_validation
            checks.append(
                HealthCheck(
                    name="benchmark_validation",
                    status="pass" if benchmark.success else "fail",
                    detail=(
                        "canonical_passed="
                        f"{benchmark.canonical_passed}/{benchmark.total_problems}, "
                        f"problems_with_issues={benchmark.problems_with_issues}"
                    ),
                )
            )
        if extra_checks:
            checks.extend(extra_checks)

        overall = "ok"
        if any(check.status == "fail" for check in checks):
            overall = "failed"
        elif any(check.status == "warn" for check in checks):
            overall = "degraded"

        return HealthReport(
            status=overall,
            generated_at_unix=time.time(),
            uptime_seconds=time.monotonic() - self._started_at,
            build=self._build,
            checks=tuple(checks),
        )


def _classify_run_outcome(diagnostics, ok: bool) -> RunOutcome:
    if ok:
        return RunOutcome.SUCCESS

    codes = {
        diagnostic.code
        for diagnostic in diagnostics
        if getattr(diagnostic, "code", None) is not None
    }
    if ErrorCode.SANDBOX_TIMEOUT in codes:
        return RunOutcome.TIMEOUT
    if ErrorCode.RUNTIME_CAPABILITY_DENIED in codes:
        return RunOutcome.CAPABILITY_DENIED
    if ErrorCode.RUNTIME_HOST_CALLBACK_MISSING in codes:
        return RunOutcome.HOST_CALLBACK_MISSING
    if ErrorCode.SANDBOX_SECURITY_VIOLATION in codes:
        return RunOutcome.SECURITY_VIOLATION
    if (
        ErrorCode.SANDBOX_RESOURCE_LIMIT in codes
        or ErrorCode.SANDBOX_RECURSION_LIMIT in codes
        or ErrorCode.SANDBOX_STEP_LIMIT in codes
    ):
        return RunOutcome.RESOURCE_LIMIT
    if any(_is_syntax_error(code) for code in codes):
        return RunOutcome.SYNTAX_ERROR
    if any(_is_type_error(code) for code in codes):
        return RunOutcome.TYPE_ERROR
    return RunOutcome.RUNTIME_ERROR


def _is_syntax_error(code: ErrorCode) -> bool:
    return code.name.startswith("LEX_") or code.name.startswith("PARSE_")


def _is_type_error(code: ErrorCode) -> bool:
    return code.name.startswith("TYPE_")


__all__ = [
    "BenchmarkValidationState",
    "BuildInfo",
    "HealthCheck",
    "HealthReport",
    "MetricsSnapshot",
    "MonitoringHook",
    "RunMetrics",
    "RunOutcome",
    "RuntimeMetricsCollector",
]
