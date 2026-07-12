"""Regression tests for scripts/validate_dependencies.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import validate_dependencies
from scripts.validate_dependencies import validate_dependency_surfaces


def _write_valid_dependency_fixture(root: Path) -> None:
    (root / ".github").mkdir()
    (root / ".github" / "workflows").mkdir()
    (root / "vscode-geno").mkdir()
    (root / "pyproject.toml").write_text(
        """
[project]
dependencies = [
    "pyyaml>=6.0.3,<7",
    "tomli>=2.4.1,<3; python_version < '3.11'",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.4.2,<10",
    "packaging>=26.2,<27",
]
lsp = [
    "pygls>=1.0,<3",
]
llm = [
    "openai>=1.0",
    "anthropic>=0.97.0",
]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text(
        """
pyyaml>=6.0.3,<7
tomli>=2.4.1,<3; python_version < "3.11"

# Optional: LLM API clients (uncomment as needed)
# openai>=1.0
# anthropic>=0.97.0
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "requirements-dev.txt").write_text(
        """
-r requirements.txt
pytest>=8.4.2,<10
packaging>=26.2,<27
pygls>=1.0,<3
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "requirements.lock").write_text(
        """
pyyaml==6.0.3 \\
    --hash=sha256:abc
tomli==2.4.1 ; python_version < "3.11" \\
    --hash=sha256:def
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "requirements-dev.lock").write_text(
        """
packaging==26.2 \\
    --hash=sha256:abc
pygls==1.3.1 \\
    --hash=sha256:abc
pytest==8.4.2 \\
    --hash=sha256:abc
pyyaml==6.0.3 \\
    --hash=sha256:abc
tomli==2.4.1 ; python_version < "3.11" \\
    --hash=sha256:def
""".strip()
        + "\n",
        encoding="utf-8",
    )
    package_json = {
        "name": "geno",
        "version": "0.3.1",
        "engines": {"vscode": "^1.80.0", "node": ">=20 <25"},
        "dependencies": {"vscode-languageclient": "^9.0.0"},
        "devDependencies": {"typescript": "^5.0.0"},
    }
    package_lock = {
        "name": "geno",
        "version": "0.3.1",
        "lockfileVersion": 3,
        "packages": {
            "": {
                "name": "geno",
                "version": "0.3.1",
                "engines": {"vscode": "^1.80.0", "node": ">=20 <25"},
                "dependencies": {"vscode-languageclient": "^9.0.0"},
                "devDependencies": {"typescript": "^5.0.0"},
            },
            "node_modules/vscode-languageclient": {"version": "9.0.0"},
            "node_modules/typescript": {"version": "5.0.0"},
        },
    }
    (root / "vscode-geno" / "package.json").write_text(
        json.dumps(package_json, indent=2),
        encoding="utf-8",
    )
    (root / "vscode-geno" / "package-lock.json").write_text(
        json.dumps(package_lock, indent=2),
        encoding="utf-8",
    )
    (root / ".github" / "dependabot.yml").write_text(
        """
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
  - package-ecosystem: "npm"
    directory: "/vscode-geno"
    schedule:
      interval: "weekly"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / ".github" / "workflows" / "publish.yml").write_text(
        """
name: Publish to PyPI
jobs:
  build-and-publish:
    steps:
      - name: Validate artifact metadata
        run: python -m twine check --strict dist/*
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@ed0c53931b1dc9bd32cbe73a98c7f6766f8a527e
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_current_dependency_surfaces_pass_validation():
    assert validate_dependency_surfaces() == []


def test_valid_dependency_fixture_passes(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)

    assert validate_dependency_surfaces(tmp_path) == []


def test_python_requirement_drift_is_reported(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    (tmp_path / "requirements.txt").write_text(
        """
pyyaml>=5,<7
tomli>=2.4.1,<3; python_version < "3.11"

# Optional: LLM API clients (uncomment as needed)
# openai>=1.0
# anthropic>=0.97.0
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "requirements.txt" in error
        and "pyyaml" in error
        and "constraint drift" in error
        for error in errors
    )


def test_commented_llm_requirement_drift_is_reported(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    requirements = (tmp_path / "requirements.txt").read_text(encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(
        requirements.replace("# anthropic>=0.97.0", "# anthropic>=0.96.0"),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "requirements.txt" in error
        and "anthropic" in error
        and "constraint drift" in error
        for error in errors
    )


def test_python_lock_pin_drift_is_reported(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    (tmp_path / "requirements.lock").write_text(
        """
pyyaml==5.4.0 \\
    --hash=sha256:abc
tomli==2.4.1 ; python_version < "3.11" \\
    --hash=sha256:def
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "requirements.lock" in error
        and "pyyaml" in error
        and "does not satisfy" in error
        for error in errors
    )


def test_python_lock_missing_hash_is_reported(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    (tmp_path / "requirements.lock").write_text(
        """
pyyaml==6.0.3
tomli==2.4.1 ; python_version < "3.11" \\
    --hash=sha256:def
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "requirements.lock" in error and "pyyaml" in error and "--hash=sha256" in error
        for error in errors
    )


def test_python_lock_install_failure_reports_stale_hash_gate(
    tmp_path: Path, monkeypatch
):
    calls: list[tuple[str, ...]] = []

    def fake_run(command, root):
        calls.append(tuple(command))
        if any(str(part).endswith("requirements-dev.lock") for part in command):
            return 1
        return 0

    monkeypatch.setattr(validate_dependencies, "_run", fake_run)

    errors = validate_dependencies.check_python_lock_installs(tmp_path)

    assert any("requirements-dev.lock" in error for error in errors)
    assert any("pip" in command and "--require-hashes" in command for command in calls)


def test_vscode_package_lock_drift_is_reported(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    lock_path = tmp_path / "vscode-geno" / "package-lock.json"
    package_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    package_lock["packages"][""]["dependencies"]["vscode-languageclient"] = "^8.0.0"
    lock_path.write_text(json.dumps(package_lock), encoding="utf-8")

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "vscode-geno/package-lock.json" in error
        and "vscode-languageclient" in error
        and "drift" in error
        for error in errors
    )


def test_vscode_engine_lock_drift_is_reported(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    lock_path = tmp_path / "vscode-geno" / "package-lock.json"
    package_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    package_lock["packages"][""]["engines"]["node"] = ">=18"
    lock_path.write_text(json.dumps(package_lock), encoding="utf-8")

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "vscode-geno/package-lock.json" in error
        and "engines" in error
        and "node" in error
        and "drift" in error
        for error in errors
    )


def test_dependabot_npm_coverage_is_required(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    (tmp_path / ".github" / "dependabot.yml").write_text(
        """
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        ".github/dependabot.yml" in error and "/vscode-geno" in error
        for error in errors
    )


def test_publish_workflow_requires_strict_twine_check_before_publish(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    publish_path.write_text(
        """
name: Publish to PyPI
jobs:
  build-and-publish:
    steps:
      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@ed0c53931b1dc9bd32cbe73a98c7f6766f8a527e
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "publish.yml" in error and "twine check --strict" in error for error in errors
    )
