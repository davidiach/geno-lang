"""Regression tests for scripts/validate_dependencies.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    (root / "requirements-release.in").write_text(
        """
build==1.5.1
setuptools==83.0.0
twine==6.2.0
wheel==0.47.0
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "requirements-release.lock").write_text(
        """
build==1.5.1 \\
    --hash=sha256:abc
setuptools==83.0.0 \\
    --hash=sha256:abc
twine==6.2.0 \\
    --hash=sha256:abc
wheel==0.47.0 \\
    --hash=sha256:abc
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
  - package-ecosystem: "docker"
    directory: "/"
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
  release-check:
    steps:
      - name: Install dependencies
        run: |
          python -m pip install --require-hashes -r requirements-dev.lock
          python -m pip install --require-hashes -r requirements-release.lock
          python -m pip install --no-deps --no-build-isolation -e .
      - name: Run release gate
        run: make release-check PYTHON=python
  build:
    needs: release-check
    steps:
      - name: Install build tools
        run: python -m pip install --require-hashes -r requirements-release.lock
      - name: Build package
        run: python -m build --no-isolation
      - name: Validate artifact metadata
        run: python -m twine check --strict dist/*
      - name: Upload tested distributions
        uses: actions/upload-artifact@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
        with:
          name: python-distributions
          path: dist/
  publish:
    needs: build
    environment: pypi
    permissions:
      id-token: write
    steps:
      - name: Download tested distributions
        uses: actions/download-artifact@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
        with:
          name: python-distributions
          path: dist/
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


def test_publish_workflow_requires_hash_locked_release_gate_dependencies(
    tmp_path: Path,
):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace(
            "          python -m pip install --require-hashes -r requirements-dev.lock\n"
            "          python -m pip install --require-hashes -r requirements-release.lock\n"
            "          python -m pip install --no-deps --no-build-isolation -e .\n",
            "          python -m pip install --upgrade pip\n"
            '          pip install -e ".[dev,lsp]"\n',
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "release-check" in error
        and ("requirements-dev.lock" in error or "unhashed" in error)
        for error in errors
    )


@pytest.mark.parametrize(
    ("unsafe_install", "pip_invocation"),
    [
        (
            "python -m pip --disable-pip-version-check install attacker",
            "pip --disable-pip-version-check install attacker",
        ),
        ("pip3 install attacker", "pip3 install attacker"),
        (
            "pip3 install attacker # --require-hashes -r requirements-dev.lock",
            "pip3 install attacker",
        ),
        (
            "python -m pip \\\n          install attacker",
            "pip install attacker",
        ),
        (
            "pip3 install attacker # ignored\n          echo --require-hashes -r requirements-dev.lock",
            "pip3 install attacker",
        ),
        (
            "pip install --no-deps --no-build-isolation -e . attacker",
            "pip install --no-deps --no-build-isolation -e . attacker",
        ),
        (
            "pip install --no-deps --no-build-isolation -e . -- attacker",
            "pip install --no-deps --no-build-isolation -e . -- attacker",
        ),
        ("/usr/bin/pip3 install attacker", "/usr/bin/pip3 install attacker"),
        (
            "pip install --no-deps --no-build-isolation -e . pip attacker",
            "pip install --no-deps --no-build-isolation -e . pip attacker",
        ),
    ],
)
def test_publish_workflow_rejects_unhashed_install_chained_after_locked_install(
    tmp_path: Path,
    unsafe_install: str,
    pip_invocation: str,
):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace(
            "          python -m pip install --require-hashes -r requirements-dev.lock\n",
            "          python -m pip install --require-hashes -r requirements-dev.lock "
            f"&& {unsafe_install}\n",
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert pip_invocation
    assert any("unhashed" in error and pip_invocation in error for error in errors)


def test_release_gate_install_scan_rejects_backtick_command_substitution():
    unsafe = validate_dependencies._unsafe_release_gate_install_lines(
        {"run": "echo `pip install attacker >/dev/null`"}
    )

    assert any("backtick command substitution" in invocation for invocation in unsafe)


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


def test_python_lock_installs_use_fresh_venvs_and_skip_linux_lock_on_windows(
    tmp_path: Path, monkeypatch
):
    calls: list[tuple[str, ...]] = []

    def fake_run(command, root):
        calls.append(tuple(command))
        return 0

    monkeypatch.setattr(validate_dependencies, "_run", fake_run)
    monkeypatch.setattr(validate_dependencies.sys, "platform", "win32")

    assert validate_dependencies.check_python_lock_installs(tmp_path) == []

    create_calls = [call for call in calls if call[1:3] == ("-m", "venv")]
    install_calls = [call for call in calls if "install" in call]
    assert len(create_calls) == 2
    assert len({call[-1] for call in create_calls}) == 2
    assert len(install_calls) == 2
    assert not any(
        any(str(part).endswith("requirements-release.lock") for part in call)
        for call in calls
    )


def test_python_lock_installs_include_release_lock_on_linux(
    tmp_path: Path, monkeypatch
):
    calls: list[tuple[str, ...]] = []

    def fake_run(command, root):
        calls.append(tuple(command))
        return 0

    monkeypatch.setattr(validate_dependencies, "_run", fake_run)
    monkeypatch.setattr(validate_dependencies.sys, "platform", "linux")

    assert validate_dependencies.check_python_lock_installs(tmp_path) == []
    assert sum(call[1:3] == ("-m", "venv") for call in calls) == 3
    assert any(
        any(str(part).endswith("requirements-release.lock") for part in call)
        for call in calls
    )


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
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace(
            "      - name: Validate artifact metadata\n"
            "        run: python -m twine check --strict dist/*\n",
            "",
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "publish.yml" in error and "twine check --strict" in error for error in errors
    )


def test_publish_workflow_rejects_run_steps_in_oidc_job(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace(
            "  publish:\n    needs: build\n    environment: pypi\n"
            "    permissions:\n      id-token: write\n    steps:\n",
            "  publish:\n    needs: build\n    environment: pypi\n"
            "    permissions:\n      id-token: write\n    steps:\n"
            "      - name: Unsafe install\n        run: pip install attacker\n",
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any("OIDC publish job" in error and "run steps" in error for error in errors)


def test_publish_workflow_requires_pinned_artifact_actions(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace(
            "actions/download-artifact@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "actions/download-artifact@v4",
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "commit-pinned action" in error and "download tested artifacts" in error
        for error in errors
    )


def test_publish_workflow_rejects_oidc_on_build_job(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace(
            "  build:\n    needs: release-check\n    steps:\n",
            "  build:\n    needs: release-check\n    permissions:\n      id-token: write\n    steps:\n",
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "only the PyPI publish job" in error and "'build'" in error for error in errors
    )


def test_release_lock_requires_hashes(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    lock_path = tmp_path / "requirements-release.lock"
    lock_path.write_text(
        lock_path.read_text(encoding="utf-8").replace(
            "build==1.5.1 \\\n    --hash=sha256:abc",
            "build==1.5.1",
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "requirements-release.lock" in error
        and "build" in error
        and "--hash=sha256" in error
        for error in errors
    )


def test_dependabot_docker_coverage_is_required(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    dependabot_path = tmp_path / ".github" / "dependabot.yml"
    config = dependabot_path.read_text(encoding="utf-8")
    dependabot_path.write_text(
        config.replace(
            '  - package-ecosystem: "docker"\n'
            '    directory: "/"\n'
            "    schedule:\n"
            '      interval: "weekly"\n',
            "",
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        ".github/dependabot.yml" in error
        and "Docker" in error
        and "digest-pinned" in error
        for error in errors
    )


def test_release_lock_rejects_direct_input_pin_drift(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    input_path = tmp_path / "requirements-release.in"
    input_path.write_text(
        input_path.read_text(encoding="utf-8").replace("build==1.5.1", "build==1.5.0"),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "requirements-release.lock" in error
        and "build" in error
        and "direct pin drift" in error
        for error in errors
    )


def test_publish_workflow_rejects_workflow_level_oidc_grants(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")

    for permissions in (
        "permissions:\n  id-token: write\n",
        "permissions: write-all\n",
    ):
        publish_path.write_text(
            workflow.replace("jobs:\n", permissions + "jobs:\n"),
            encoding="utf-8",
        )

        errors = validate_dependency_surfaces(tmp_path)

        assert any(
            "workflow-level permissions" in error and "id-token" in error
            for error in errors
        )


def test_publish_release_gate_requires_release_lock(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace(
            "          python -m pip install --require-hashes -r requirements-release.lock\n",
            "",
            1,
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "release-check build dependencies" in error
        and "requirements-release.lock" in error
        for error in errors
    )


def test_publish_release_gate_requires_nonisolated_editable_install(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace(
            "python -m pip install --no-deps --no-build-isolation -e .",
            "python -m pip install --no-deps -e .",
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any("--no-build-isolation" in error for error in errors)
    assert any("unhashed dependency install" in error for error in errors)


def test_publish_workflow_requires_nonisolated_build(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace(
            "run: python -m build --no-isolation",
            "run: python -m build",
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any("builds with `--no-isolation`" in error for error in errors)


def test_publish_workflow_requires_protected_environment(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace("    environment: pypi\n", ""),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any(
        "publish job" in error and "protected 'pypi' environment" in error
        for error in errors
    )


def test_publish_workflow_rejects_extra_write_permissions(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    publish_path.write_text(
        workflow.replace(
            "      id-token: write\n",
            "      id-token: write\n      contents: write\n",
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any("additional write permissions" in error for error in errors)


def test_publish_workflow_rejects_cross_run_artifact_overrides(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    first_path = workflow.index("          path: dist/\n")
    second_path = workflow.index("          path: dist/\n", first_path + 1)

    for option in ("github-token", "repository", "run-id"):
        modified = workflow[:second_path] + workflow[second_path:].replace(
            "          path: dist/\n",
            f"          path: dist/\n          {option}: attacker\n",
            1,
        )
        publish_path.write_text(modified, encoding="utf-8")

        errors = validate_dependency_surfaces(tmp_path)

        assert any(
            "same-run provenance" in error and option in error for error in errors
        )


def test_publish_workflow_requires_matching_artifact_path(tmp_path: Path):
    _write_valid_dependency_fixture(tmp_path)
    publish_path = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow = publish_path.read_text(encoding="utf-8")
    first_path = workflow.index("          path: dist/\n")
    second_path = workflow.index("          path: dist/\n", first_path + 1)
    publish_path.write_text(
        workflow[:second_path]
        + workflow[second_path:].replace(
            "          path: dist/\n", "          path: fetched/\n", 1
        ),
        encoding="utf-8",
    )

    errors = validate_dependency_surfaces(tmp_path)

    assert any("uploads the same artifact" in error for error in errors)
