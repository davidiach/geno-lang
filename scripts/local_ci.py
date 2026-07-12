#!/usr/bin/env python3
"""Local CI workflow that mirrors the most useful hosted GitHub Actions gates."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_CHECK_TARGETS = ("geno", "benchmark", "experiment", "analysis")
OPTIONAL_COLLECTION_TARGETS = (
    "geno/tests/test_backend_parity.py",
    "geno/tests/test_fuzzing.py",
    "geno/tests/test_property_based.py",
    "geno/tests/test_differential_fuzzing.py",
)
FUZZ_PROPERTY_TARGETS = (
    "geno/tests/test_property_based.py",
    "geno/tests/test_fuzzing.py",
    "geno/tests/test_differential_fuzzing.py",
)
PYTHON_BIN = sys.executable


@dataclass(frozen=True)
class Step:
    """One local CI step."""

    name: str
    command: tuple[str, ...] | None = None
    runner: Callable[[], int] | None = None
    env: Mapping[str, str] | None = None
    soft_fail_issue: str | None = None


@dataclass(frozen=True)
class StepResult:
    """Execution result for a step."""

    name: str
    status: str
    exit_code: int
    soft_fail_issue: str | None = None


def _repo_display(path: Path) -> str:
    """Return a stable display path relative to the repo when possible."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def _has_python_files(path: Path) -> bool:
    """Return whether a directory contains Python files."""
    return any(child.is_file() for child in path.rglob("*.py"))


def _normalize_input_paths(paths: Sequence[str]) -> list[Path]:
    """Resolve and validate CLI path arguments."""
    normalized: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (REPO_ROOT / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if not candidate.exists():
            raise ValueError(f"Path does not exist: {raw}")
        if candidate in seen:
            continue
        normalized.append(candidate)
        seen.add(candidate)
    return normalized


def _python_targets(paths: Sequence[Path]) -> list[str]:
    """Filter user-provided paths to Python-bearing targets."""
    targets: list[str] = []
    for path in paths:
        if (path.is_file() and path.suffix == ".py") or (
            path.is_dir() and _has_python_files(path)
        ):
            targets.append(_repo_display(path))
    return targets


def _security_targets(paths: Sequence[str]) -> list[str]:
    """Limit security linting to repo code, not docs or unrelated directories."""
    return [
        path
        for path in paths
        if path.replace("\\", "/") == "geno"
        or path.replace("\\", "/").startswith("geno/")
    ]


def _pytest_targets(targets: Sequence[str]) -> list[str]:
    """Resolve and validate pytest target arguments."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in targets:
        base = raw.split("::", 1)[0]
        resolved = _normalize_input_paths([base])[0]
        display = _repo_display(resolved)
        suffix = raw[len(base) :]
        target = f"{display}{suffix}"
        if target in seen:
            continue
        normalized.append(target)
        seen.add(target)
    return normalized


def _compileall_step(name: str, targets: Sequence[str]) -> Step:
    """Build a compileall step with an isolated pycache."""
    env = dict(os.environ)
    env["PYTHONPYCACHEPREFIX"] = os.path.join(
        tempfile.gettempdir(),
        "geno_local_ci_pycache",
    )
    return Step(
        name=name,
        command=(PYTHON_BIN, "-m", "compileall", "-q", *targets),
        env=env,
    )


def _check_examples() -> int:
    """Run `geno check` across top-level examples."""
    example_files = sorted((REPO_ROOT / "examples").glob("*.geno"))
    for example in example_files:
        cmd = [PYTHON_BIN, "-m", "geno", "check", _repo_display(example)]
        result = subprocess.run(cmd, cwd=REPO_ROOT, check=False)  # noqa: S603
        if result.returncode != 0:
            return result.returncode
    return 0


def build_targeted_steps(
    changed_paths: Sequence[str],
    tests: Sequence[str],
) -> list[Step]:
    """Build a scoped local validation plan for touched files."""
    normalized_paths = _normalize_input_paths(changed_paths)
    python_targets = _python_targets(normalized_paths)
    pytest_targets = _pytest_targets(tests)

    steps: list[Step] = []
    if python_targets:
        steps.extend(
            [
                Step(
                    name="ruff-check-targeted",
                    command=(PYTHON_BIN, "-m", "ruff", "check", *python_targets),
                ),
                Step(
                    name="ruff-format-targeted",
                    command=(
                        PYTHON_BIN,
                        "-m",
                        "ruff",
                        "format",
                        "--check",
                        *python_targets,
                    ),
                ),
                Step(
                    name="mypy-targeted",
                    command=(
                        PYTHON_BIN,
                        "-m",
                        "mypy",
                        *python_targets,
                        "--ignore-missing-imports",
                        "--no-error-summary",
                        "--follow-imports=skip",
                    ),
                ),
                _compileall_step("compileall-targeted", python_targets),
            ]
        )
        security_targets = _security_targets(python_targets)
        if security_targets:
            steps.append(
                Step(
                    name="ruff-security-targeted",
                    command=(
                        PYTHON_BIN,
                        "-m",
                        "ruff",
                        "check",
                        *security_targets,
                        "--select",
                        "S",
                        "--ignore",
                        "S101",
                    ),
                )
            )

    if pytest_targets:
        steps.append(
            Step(
                name="pytest-targeted",
                command=(PYTHON_BIN, "-m", "pytest", "-q", *pytest_targets),
            )
        )

    if not steps:
        raise ValueError(
            "No runnable local CI steps were selected. Provide Python paths and/or pytest targets."
        )
    return steps


def build_full_steps() -> list[Step]:
    """Build the repo-wide local CI plan."""
    repo_targets = tuple(REPO_CHECK_TARGETS)
    return [
        Step(
            name="ruff-check-full",
            command=(PYTHON_BIN, "-m", "ruff", "check", *repo_targets),
        ),
        Step(
            name="ruff-format-full",
            command=(PYTHON_BIN, "-m", "ruff", "format", "--check", *repo_targets),
        ),
        Step(
            name="mypy-full",
            command=(
                PYTHON_BIN,
                "-m",
                "mypy",
                "geno/",
                "--ignore-missing-imports",
                "--no-error-summary",
            ),
        ),
        Step(
            name="anytype-recovery-ratchet",
            command=(PYTHON_BIN, "scripts/check_anytype_recovery.py"),
        ),
        Step(
            name="ci-dx-ratchets",
            command=(PYTHON_BIN, "scripts/check_ci_dx_ratchets.py"),
        ),
        Step(
            name="ruff-security-full",
            command=(
                PYTHON_BIN,
                "-m",
                "ruff",
                "check",
                "geno/",
                "--select",
                "S",
                "--ignore",
                "S101",
            ),
        ),
        Step(
            name="pytest-full",
            command=(
                PYTHON_BIN,
                "-m",
                "pytest",
                "geno/tests/",
                "-v",
                "--tb=short",
                "--cov=geno",
                "--cov-report=term",
                "--cov-fail-under=80",
                "--timeout=60",
            ),
        ),
        Step(name="examples-check", runner=_check_examples),
    ]


def build_optional_steps() -> list[Step]:
    """Build the opt-in fuzz and property local CI plan."""
    return [
        Step(
            name="optional-test-collection",
            command=(
                PYTHON_BIN,
                "-m",
                "pytest",
                "--collect-only",
                *OPTIONAL_COLLECTION_TARGETS,
                "-q",
            ),
        ),
        Step(
            name="fuzz-property-tests",
            command=(
                PYTHON_BIN,
                "-m",
                "pytest",
                *FUZZ_PROPERTY_TARGETS,
                "-q",
                "--tb=short",
                "--timeout=60",
            ),
        ),
    ]


def build_release_steps() -> list[Step]:
    """Build the release-sensitive local CI plan."""
    full_steps = build_full_steps()
    quality_steps = [step for step in full_steps if step.name != "examples-check"]
    examples_step = next(step for step in full_steps if step.name == "examples-check")
    release_gate_env = {"PYTHON": PYTHON_BIN}

    return [
        Step(
            name="version-alignment",
            command=(PYTHON_BIN, "scripts/check_version_alignment.py"),
        ),
        Step(
            name="dependency-lock-gate",
            command=(
                PYTHON_BIN,
                "scripts/validate_dependencies.py",
                "--check-installs",
            ),
        ),
        Step(
            name="release-gate-templates",
            command=("bash", "scripts/release-gate-templates.sh"),
            env=release_gate_env,
        ),
        Step(
            name="release-gate-vscode",
            command=("bash", "scripts/release-gate-vscode.sh"),
            env=release_gate_env,
        ),
        Step(
            name="release-gate-apps",
            command=(PYTHON_BIN, "scripts/release_gate_apps.py"),
        ),
        Step(
            name="builtin-parity",
            command=(PYTHON_BIN, "scripts/validate_builtin_parity.py"),
        ),
        Step(
            name="validate-spec",
            command=(PYTHON_BIN, "scripts/validate_spec.py"),
        ),
        Step(
            name="validate-supported-targets",
            command=(PYTHON_BIN, "scripts/validate_supported_targets.py"),
        ),
        *quality_steps,
        examples_step,
        Step(
            name="selfhost-parity",
            command=(PYTHON_BIN, "scripts/check_selfhost_parity.py"),
        ),
        Step(
            name="validate-benchmark",
            command=(PYTHON_BIN, "scripts/validate_benchmark.py", "--strict-budgets"),
        ),
    ]


def _format_command(step: Step) -> str:
    """Render a human-readable command string for logs."""
    if step.command is not None:
        return " ".join(shlex.quote(part) for part in step.command)
    if step.runner is not None:
        return f"<python:{step.runner.__name__}>"
    raise ValueError(f"Step {step.name} has no command or runner")


def _execute_step(step: Step, dry_run: bool) -> StepResult:
    """Run one step and classify the result."""
    command_text = _format_command(step)
    print(f"\n==> {step.name}")
    print(command_text)
    if dry_run:
        return StepResult(
            name=step.name,
            status="DRY-RUN",
            exit_code=0,
            soft_fail_issue=step.soft_fail_issue,
        )

    if step.command is not None:
        result = subprocess.run(  # noqa: S603
            step.command,
            cwd=REPO_ROOT,
            env=dict(os.environ, **(step.env or {})),
            check=False,
        )
        exit_code = result.returncode
    elif step.runner is not None:
        exit_code = step.runner()
    else:
        raise ValueError(f"Step {step.name} has no command or runner")

    if exit_code == 0:
        return StepResult(step.name, "PASS", 0, step.soft_fail_issue)
    if step.soft_fail_issue is not None:
        return StepResult(step.name, "KNOWN-RED", exit_code, step.soft_fail_issue)
    return StepResult(step.name, "FAIL", exit_code, step.soft_fail_issue)


def _print_summary(results: Sequence[StepResult]) -> None:
    """Print a concise run summary."""
    print("\nSummary")
    for result in results:
        suffix = (
            f" ({result.soft_fail_issue})"
            if result.status == "KNOWN-RED" and result.soft_fail_issue
            else ""
        )
        print(f"- {result.status}: {result.name}{suffix}")


def run_steps(steps: Sequence[Step], dry_run: bool = False) -> int:
    """Run a sequence of steps and return the process exit code."""
    results = [_execute_step(step, dry_run=dry_run) for step in steps]
    _print_summary(results)
    failures = [result for result in results if result.status == "FAIL"]
    return 1 if failures else 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local CI workflows that mirror hosted release checks."
    )

    def add_dry_run_flag(
        target: argparse.ArgumentParser,
        *,
        default: bool | str = False,
    ) -> None:
        target.add_argument(
            "--dry-run",
            action="store_true",
            default=default,
            help="Print the planned commands without running them.",
        )

    add_dry_run_flag(parser)

    subparsers = parser.add_subparsers(dest="mode", required=True)

    targeted = subparsers.add_parser(
        "targeted",
        help="Run a scoped local gate for touched files and explicit pytest targets.",
    )
    add_dry_run_flag(targeted, default=argparse.SUPPRESS)
    targeted.add_argument(
        "--paths",
        nargs="*",
        default=[],
        help="Changed Python-bearing files or directories to lint/typecheck/compile.",
    )
    targeted.add_argument(
        "--tests",
        nargs="*",
        default=[],
        help="Explicit pytest targets for the changed subsystem.",
    )

    full = subparsers.add_parser(
        "full",
        help="Run the repo-wide local gate.",
    )
    add_dry_run_flag(full, default=argparse.SUPPRESS)
    optional = subparsers.add_parser(
        "optional",
        help="Run opt-in optional fuzz and property checks.",
    )
    add_dry_run_flag(optional, default=argparse.SUPPRESS)
    release = subparsers.add_parser(
        "release",
        help="Run the release-sensitive local gate, including benchmark and template checks.",
    )
    add_dry_run_flag(release, default=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    args = _parse_args(argv or sys.argv[1:])
    if args.mode == "targeted":
        steps = build_targeted_steps(args.paths, args.tests)
    elif args.mode == "full":
        steps = build_full_steps()
    elif args.mode == "optional":
        steps = build_optional_steps()
    else:
        steps = build_release_steps()
    return run_steps(steps, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
