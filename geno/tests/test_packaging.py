from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


REQUIRED_PACKAGE_DATA = {
    "py.typed",
    "_js_runtime_support.js",
    "packages.json",
    "std/*.geno",
}


def _publish_workflow_jobs():
    repo_root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (repo_root / ".github" / "workflows" / "publish.yml").read_text(
            encoding="utf-8"
        )
    )
    return workflow["jobs"]


def _step_by_name(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def _step_names(job: dict) -> list[str | None]:
    return [step.get("name") for step in job["steps"]]


def _pyproject() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return cast(
        dict[str, Any],
        tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8")),
    )


def test_distribution_name_avoids_the_unrelated_pypi_geno_project():
    assert _pyproject()["project"]["name"] == "geno-lang"


def test_package_discovery_excludes_test_suite():
    package_find = _pyproject()["tool"]["setuptools"]["packages"]["find"]

    assert "geno.tests*" in package_find["exclude"]
    assert _pyproject()["tool"]["setuptools"]["include-package-data"] is False


def test_publish_workflow_validates_pypi_artifact_metadata():
    jobs = _publish_workflow_jobs()

    job = jobs["build-and-publish"]
    install = _step_by_name(job, "Install build tools")["run"]
    validate = _step_by_name(job, "Validate artifact metadata")

    assert 'pip install build twine wheel "setuptools>=82.0.1"' in install
    assert validate["run"] == "python -m twine check --strict dist/*"


def test_publish_workflow_validates_metadata_before_smoke_and_publish():
    jobs = _publish_workflow_jobs()

    publish_steps = _step_names(jobs["build-and-publish"])
    assert publish_steps.index("Build package") < publish_steps.index(
        "Validate artifact metadata"
    )
    assert publish_steps.index("Validate artifact metadata") < publish_steps.index(
        "Smoke test wheel"
    )
    assert publish_steps.index("Validate artifact metadata") < publish_steps.index(
        "Smoke test sdist"
    )
    assert publish_steps.index("Smoke test wheel") < publish_steps.index(
        "Publish to PyPI"
    )
    assert publish_steps.index("Smoke test sdist") < publish_steps.index(
        "Publish to PyPI"
    )


def test_publish_workflow_requires_pypi_environment_approval():
    jobs = _publish_workflow_jobs()

    assert jobs["build-and-publish"]["environment"] == "pypi"


def test_publish_workflow_runs_release_check_before_build_and_publish():
    jobs = _publish_workflow_jobs()

    assert jobs["build-and-publish"]["needs"] == "release-check"
    assert (
        _step_by_name(jobs["release-check"], "Run release gate")["run"]
        == "make release-check PYTHON=python"
    )


def test_publish_workflow_checks_tag_before_release_gate():
    jobs = _publish_workflow_jobs()
    steps = jobs["release-check"]["steps"]
    names = [step.get("name") for step in steps]

    assert names.index("Verify release tag matches package version") < names.index(
        "Run release gate"
    )
    tag_check = _step_by_name(
        jobs["release-check"], "Verify release tag matches package version"
    )
    assert "--tag" in tag_check["run"]


def test_publish_workflow_scopes_oidc_to_publish_job():
    jobs = _publish_workflow_jobs()

    assert jobs["build-and-publish"]["permissions"]["id-token"] == "write"
    assert "permissions" not in jobs["release-check"]
    assert "permissions" not in jobs["verify"]


def test_publish_workflow_publishes_the_smoked_artifacts_without_rebuild():
    jobs = _publish_workflow_jobs()
    publish_steps = _step_names(jobs["build-and-publish"])

    assert publish_steps.count("Build package") == 1
    assert publish_steps.index("Build package") < publish_steps.index(
        "Smoke test wheel"
    )
    assert publish_steps.index("Build package") < publish_steps.index(
        "Smoke test sdist"
    )
    assert publish_steps.index("Build package") < publish_steps.index("Publish to PyPI")


def test_setuptools_package_data_includes_runtime_assets():
    pyproject = _pyproject()

    package_data = set(pyproject["tool"]["setuptools"]["package-data"]["geno"])

    assert package_data >= REQUIRED_PACKAGE_DATA


def test_declared_runtime_assets_exist_on_disk():
    package_root = Path(__file__).resolve().parents[1]

    for pattern in REQUIRED_PACKAGE_DATA:
        if "*" in pattern:
            if "/" in pattern:
                parent, glob_pat = pattern.rsplit("/", 1)
            else:
                parent, glob_pat = ".", pattern
            matches = list((package_root / parent).glob(glob_pat))
            assert matches, f"no files match '{pattern}' under {package_root}"
        else:
            assert (package_root / pattern).is_file(), (
                f"missing declared asset '{pattern}' under {package_root}"
            )
