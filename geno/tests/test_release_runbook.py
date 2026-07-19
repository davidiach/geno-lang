"""Regression tests for release runbook gate coverage."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _make_target_body(makefile: str, target: str) -> str:
    lines = makefile.splitlines()
    start = next(index for index, line in enumerate(lines) if line == f"{target}:")
    end = next(
        (
            index
            for index, line in enumerate(lines[start + 1 :], start + 1)
            if line and not line.startswith(("\t", " "))
        ),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def _markdown_section(markdown: str, heading: str) -> str:
    lines = markdown.splitlines()
    start = next(index for index, line in enumerate(lines) if line == heading)
    end = next(
        (
            index
            for index, line in enumerate(lines[start + 1 :], start + 1)
            if line.startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def test_release_runbook_lists_key_release_check_gates():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "operations" / "release-runbook.md").read_text(
        encoding="utf-8"
    )
    release_check = _make_target_body(makefile, "release-check")
    mandatory_gate = _markdown_section(runbook, "## Mandatory Gate")

    expected = {
        "scripts/check_version_alignment.py": "version alignment",
        "scripts/validate_dependencies.py --check-installs": (
            "dependency lock and install validation"
        ),
        "scripts/release-gate-templates.sh": ("init template scaffolding/check/test"),
        "scripts/release-gate-vscode.sh": "VS Code extension packaging",
        "scripts/release_gate_apps.py": "example app validation",
        "scripts/validate_builtin_parity.py": (
            "builtin registry and runtime parity validation"
        ),
        "scripts/validate_spec.py": "language spec validation",
        "scripts/run_conformance.py --all-retained --target all --require-node": (
            "frozen all-backend language conformance"
        ),
        "scripts/validate_supported_targets.py": (
            "supported target documentation validation"
        ),
        "-m ruff check geno/ benchmark/ experiment/ analysis/": (
            "ruff lint and format checks"
        ),
        "-m ruff format --check geno/ benchmark/ experiment/ analysis/": (
            "ruff lint and format checks"
        ),
        "-m mypy geno/": "mypy over `geno/`",
        "-m ruff check geno/ --select S --ignore S101": "security linting",
        "-m pytest geno/tests/ -v --tb=short --cov=geno": (
            "pytest over `geno/tests/` with coverage and per-test timeouts"
        ),
        "scripts/check_selfhost_parity.py": "selfhost parity checks",
        "scripts/validate_benchmark.py": "benchmark corpus validation",
    }

    for command, phrase in expected.items():
        assert command in release_check
        assert f"- {phrase}" in mandatory_gate
