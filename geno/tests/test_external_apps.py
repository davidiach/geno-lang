"""Candidate identity and complete-coverage contracts for external application CI."""

from __future__ import annotations

import copy
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import check_external_apps as external


@pytest.fixture
def wheel(tmp_path: Path) -> Path:
    path = tmp_path / "geno_lang-0.5.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "geno_lang-0.5.0.dist-info/METADATA", "Name: geno-lang\nVersion: 0.5.0\n"
        )
    return path


def installed(candidate: dict[str, str], environment: Path) -> dict[str, Any]:
    return {
        "version": candidate["version"],
        "internal_version": "0.5.0",
        "prefix": str(environment),
        "package_path": str(environment / "lib/site-packages/geno/__init__.py"),
        "distribution_path": str(environment / "lib/site-packages"),
        "direct_url": {"archive_info": {"hashes": {"sha256": candidate["sha256"]}}},
    }


@pytest.mark.parametrize(
    "mismatch", ["version", "prefix", "package_path", "distribution_path", "hash"]
)
def test_rejects_wrong_candidate_or_source_import(wheel, tmp_path, mismatch):
    candidate = external.wheel_identity(wheel)
    environment = tmp_path / "fresh"
    identity = installed(candidate, environment)
    external.validate_install(identity, candidate, environment)
    if mismatch == "hash":
        identity["direct_url"]["archive_info"]["hashes"]["sha256"] = "another-wheel"
    elif mismatch == "version":
        identity[mismatch] = "0.4.3"
    else:
        identity[mismatch] = str(tmp_path / "source/geno/__init__.py")
    with pytest.raises(ValueError):
        external.validate_install(identity, candidate, environment)


def manifest() -> dict[str, Any]:
    return {
        "apps": [
            {"id": f"app-{i}", "tier": "corpus", "path": f"apps/app-{i}"}
            for i in range(101)
        ]
    }


@pytest.mark.parametrize(
    "damage", ["empty", "missing", "duplicate", "escape", "unsafe_id"]
)
def test_rejects_incomplete_or_unsafe_manifest(tmp_path, damage):
    data = manifest()
    assert len(external.validate_manifest(data, tmp_path)) == 101
    if damage == "empty":
        data["apps"] = []
    elif damage == "missing":
        data["apps"].pop()
    elif damage == "duplicate":
        data["apps"][-1]["id"] = data["apps"][0]["id"]
    elif damage == "escape":
        data["apps"][0]["path"] = "../outside"
    else:
        data["apps"][0]["id"] = "../outside"
    with pytest.raises(ValueError):
        external.validate_manifest(data, tmp_path)


def successful_results(executable: Path) -> dict[str, Any]:
    return {
        "tier": "all",
        "geno": str(executable),
        "geno_version": "geno 0.5.0",
        "total": 101,
        "passed": 101,
        "failed": 0,
        "apps": [
            dict(app, ok=True, test_ok=True, run_ok=True, compile_ok=True)
            for app in manifest()["apps"]
        ],
    }


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "duplicate",
        "substitution",
        "no_compile",
        "test_failed",
        "run_failed",
        "compile_failed",
        "false_ok",
        "numeric_true",
        "wrong_binary",
        "wrong_version",
        "wrong_tier",
        "wrong_counts",
    ],
)
def test_rejects_false_green_results(tmp_path, damage):
    executable = tmp_path / "venv/bin/geno"
    selected = external.validate_manifest(manifest(), tmp_path)
    data = successful_results(executable)
    external.validate_results(data, selected, executable, "geno 0.5.0")
    if damage == "missing":
        data["apps"].pop()
    elif damage == "duplicate":
        data["apps"][-1] = copy.deepcopy(data["apps"][0])
    elif damage == "substitution":
        data["apps"][-1]["id"] = "unselected"
    elif damage == "no_compile":
        data["apps"][0]["compile_ok"] = None
    elif damage in {"test_failed", "run_failed", "compile_failed"}:
        data["apps"][0][damage.replace("_failed", "_ok")] = False
    elif damage == "false_ok":
        data["apps"][0]["ok"] = False
    elif damage == "numeric_true":
        data["apps"][0]["run_ok"] = 1
    elif damage == "wrong_binary":
        data["geno"] = "/old/geno"
    elif damage == "wrong_version":
        data["geno_version"] = "geno 0.4.3"
    elif damage == "wrong_tier":
        data["tier"] = "flagship"
    else:
        data["passed"] = 100
    with pytest.raises(ValueError):
        external.validate_results(data, selected, executable, "geno 0.5.0")


def test_failed_applications_are_counted_not_hidden(tmp_path):
    executable = tmp_path / "venv/bin/geno"
    selected = external.validate_manifest(manifest(), tmp_path)
    data = successful_results(executable)
    data["apps"][3]["run_ok"] = False
    data["apps"][3]["ok"] = False
    data["passed"], data["failed"] = 100, 1
    assert external.failed_applications(data, selected, executable, "geno 0.5.0") == [
        "app-3"
    ]
    with pytest.raises(ValueError):
        external.validate_results(data, selected, executable, "geno 0.5.0")
    data["passed"], data["failed"] = 101, 0
    with pytest.raises(ValueError):
        external.failed_applications(data, selected, executable, "geno 0.5.0")


@pytest.mark.parametrize(
    "revision,status",
    [
        ("0" * 40, ""),
        (external.CORPUS_REVISION, " M harness/run_corpus.py"),
        (external.CORPUS_REVISION, "?? geno/__init__.py"),
    ],
)
def test_rejects_wrong_or_dirty_corpus(monkeypatch, tmp_path, revision, status):
    def run(self, name, args, cwd, timeout=180):
        return revision if name == "corpus-revision" else status

    monkeypatch.setattr(external.Commands, "run", run)
    with pytest.raises(ValueError):
        external.validate_corpus_checkout(external.Commands(tmp_path), tmp_path)


def test_subprocess_environment_cannot_shadow_candidate(monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", "/old/source")
    monkeypatch.setenv("PYTHONHOME", "/old/python")
    commands = external.Commands(tmp_path)
    assert "PYTHONPATH" not in commands.env
    assert "PYTHONHOME" not in commands.env
    assert commands.env["PYTHONNOUSERSITE"] == "1"


@pytest.mark.parametrize("outcome", ["success", "missing", "malformed", "nonzero"])
def test_driver_selects_candidate_and_retains_failure_evidence(
    monkeypatch, tmp_path, wheel, outcome
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps(manifest()))
    (corpus / "pin.json").write_text('{"pip": "geno-lang==0.4.3"}')
    output = tmp_path / "evidence"
    calls = []
    candidate = external.wheel_identity(wheel)
    identity = installed(candidate, output / "venv")
    real_run = external.Commands.run

    def run(self, name, args, cwd, timeout=180, *, check=True):
        calls.append((name, args, cwd))
        self.returncode = 0
        if name == "corpus-revision":
            return external.CORPUS_REVISION
        if name == "identity":
            return json.dumps(identity)
        if name == "candidate-version":
            return "geno 0.5.0"
        if name == "corpus":
            assert args[args.index("--tier") + 1] == "all"
            assert "--compile" in args
            executable = Path(args[args.index("--geno-bin") + 1])
            assert (
                executable.is_absolute() and executable.parent.parent == output / "venv"
            )
            if outcome == "nonzero":
                return real_run(
                    self,
                    name,
                    [
                        sys.executable,
                        "-c",
                        'print("corpus failure"); raise SystemExit(7)',
                    ],
                    cwd,
                    check=check,
                )
            if outcome != "missing":
                (output / "corpus.json").write_text(
                    "invalid json"
                    if outcome == "malformed"
                    else json.dumps(successful_results(executable))
                )
        return ""

    monkeypatch.setattr(external.Commands, "run", run)
    code = external.main(
        ["--wheel", str(wheel), "--corpus", str(corpus), "--output", str(output)]
    )
    assert code == (0 if outcome == "success" else 1)
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["passed"] is (outcome == "success")
    assert receipt["candidate"]["sha256"] == candidate["sha256"]
    install = next(args for name, args, cwd in calls if name == "install-candidate")
    assert "--no-deps" in install and install[-1] == str(wheel)
    if outcome == "nonzero":
        records = [
            json.loads(line)
            for line in (output / "commands.jsonl").read_text().splitlines()
        ]
        assert records[-1]["returncode"] == 7
        assert "corpus failure" in Path(records[-1]["log"]).read_text()
    if outcome == "success":
        assert receipt["results"] == {"total": 101, "passed": 101, "failed": 0}


@pytest.mark.parametrize("after", ["passes", "still_fails", "exit_disagrees"])
def test_an_application_failing_as_written_must_pass_after_migration(
    monkeypatch, tmp_path, wheel, after
):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps(manifest()))
    (corpus / "pin.json").write_text('{"pip": "geno-lang==0.4.3"}')
    output = tmp_path / "evidence"
    migrated = output / "migrated-corpus"
    calls = []
    candidate = external.wheel_identity(wheel)
    identity = installed(candidate, output / "venv")

    def run(self, name, args, cwd, timeout=180, *, check=True):
        calls.append((name, args, cwd))
        self.returncode = 0
        if name == "corpus-revision":
            return external.CORPUS_REVISION
        if name == "identity":
            return json.dumps(identity)
        if name == "candidate-version":
            return "geno 0.5.0"
        if name in {"corpus", "migrated-corpus"}:
            assert check is False
            broken = {"app-3", "app-7"} if name == "corpus" else set()
            if name == "migrated-corpus" and after == "still_fails":
                broken = {"app-7"}
            data = successful_results(Path(args[args.index("--geno-bin") + 1]))
            for app in data["apps"]:
                if app["id"] in broken:
                    app.update(ok=False, compile_ok=False)
            data["passed"], data["failed"] = 101 - len(broken), len(broken)
            self.returncode = 1 if broken and after != "exit_disagrees" else 0
            Path(args[args.index("--json-out") + 1]).write_text(json.dumps(data))
        return ""

    monkeypatch.setattr(external.Commands, "run", run)
    code = external.main(
        ["--wheel", str(wheel), "--corpus", str(corpus), "--output", str(output)]
    )
    receipt = json.loads((output / "receipt.json").read_text())
    assert code == (0 if after == "passes" else 1)
    assert receipt["passed"] is (after == "passes")
    if after == "exit_disagrees":
        assert not any(name.startswith("migrate") for name, _, _ in calls)
        return
    assert receipt["results"] == {"total": 101, "passed": 99, "failed": 2}
    entrypoint = "Scripts/geno.exe" if sys.platform == "win32" else "bin/geno"
    executable = str(output / "venv" / entrypoint)
    migrations = [(name, args, cwd) for name, args, cwd in calls if "migrate" in args]
    assert migrations == [
        (f"migrate-diff-{app}", [executable, "migrate", "--diff", "."], cwd)
        if diff
        else (f"migrate-{app}", [executable, "migrate", "."], cwd)
        for app in ("app-3", "app-7")
        for diff, cwd in (
            (True, migrated / f"apps/{app}"),
            (False, migrated / f"apps/{app}"),
        )
    ]
    rerun = next(args for name, args, _ in calls if name == "migrated-corpus")
    assert rerun[2] == str(migrated / "harness" / "run_corpus.py")
    if after == "passes":
        assert receipt["migrated"] == {
            "applications": ["app-3", "app-7"],
            "results": {"total": 101, "passed": 101, "failed": 0},
        }
    else:
        assert "app-7" in receipt["error"]


def test_driver_refuses_existing_results_without_overwriting(tmp_path, wheel):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    marker = evidence / "receipt.json"
    marker.write_text("existing evidence")
    with pytest.raises(FileExistsError):
        external.main(
            [
                "--wheel",
                str(wheel),
                "--corpus",
                str(tmp_path),
                "--output",
                str(evidence),
            ]
        )
    assert marker.read_text() == "existing evidence"


def test_workflow_has_unprivileged_pinned_inputs_and_failure_artifacts():
    workflow = yaml.safe_load(
        (external.ROOT / ".github/workflows/external-apps.yml").read_text()
    )
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"pull_request", "push", "schedule", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["external-apps"]
    assert job["timeout-minutes"] <= 20 and "permissions" not in job
    steps = job["steps"]
    assert all(
        re.search(r"@[a-f0-9]{40}$", step["uses"]) for step in steps if "uses" in step
    )
    checkouts = [
        step for step in steps if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert len(checkouts) == 2
    assert all(step["with"]["persist-credentials"] is False for step in checkouts)
    corpus = next(step for step in checkouts if "repository" in step["with"])
    assert corpus["with"]["repository"] == external.CORPUS_REPOSITORY
    assert corpus["with"]["ref"] == external.CORPUS_REVISION
    build = next(
        step["run"] for step in steps if "python -m build" in step.get("run", "")
    )
    assert "--require-hashes -r requirements-release.lock" in build
    assert "--wheel --no-isolation" in build
    run = next(
        step["run"] for step in steps if "check_external_apps.py" in step.get("run", "")
    )
    assert '--wheel "$RUNNER_TEMP"/candidate-wheel/*.whl' in run
    upload = next(
        step
        for step in steps
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    for filename in (
        "receipt.json",
        "commands.jsonl",
        "corpus.json",
        "corpus-migrated.json",
        "logs/*.log",
        "candidate-wheel/*.whl",
    ):
        assert filename in upload["with"]["path"]
    assert "venv" not in upload["with"]["path"]
