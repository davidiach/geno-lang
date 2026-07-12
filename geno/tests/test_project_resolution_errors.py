"""Regression tests for project-resolution error handling."""

from pathlib import Path

import pytest

from geno.module_resolver import AmbiguousModuleError, ModuleResolutionError
from geno.project_resolution import ProjectResolutionError, resolve_project_context

_HELPER_SRC = "func helper(x: Int) -> Int\n    return x\nend func\n"


def _supports_case_distinct_paths(tmp_path: Path) -> bool:
    """Return True when the temp filesystem distinguishes names by case."""
    probe = tmp_path / "CaseProbe.geno"
    probe.write_text(_HELPER_SRC)
    try:
        return not (tmp_path / "caseprobe.geno").exists()
    finally:
        probe.unlink()


def test_ambiguous_module_error_is_a_resolution_error():
    assert issubclass(AmbiguousModuleError, ModuleResolutionError)


def test_ambiguous_import_is_wrapped_as_project_resolution_error(tmp_path):
    if not _supports_case_distinct_paths(tmp_path):
        pytest.skip("filesystem is case-insensitive")

    (tmp_path / "cli.geno").write_text(_HELPER_SRC)
    (tmp_path / "CLI.geno").write_text(_HELPER_SRC)
    app_file = tmp_path / "App.geno"
    app_file.write_text(
        "import Cli\nfunc main() -> Int\n    return helper(1)\nend func\n"
    )

    with pytest.raises(ProjectResolutionError, match="ambiguous"):
        resolve_project_context(app_file)
