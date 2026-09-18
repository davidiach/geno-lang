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


# =============================================================================
# Package-lock state directory setup failures (#96)
# =============================================================================


_MAIN_SRC = "func main() -> Int\n    return 0\nend func\n"


def _manifest_project(tmp_path: Path) -> Path:
    """A manifest-backed project: only these exercise the locking path."""
    (tmp_path / "geno.toml").write_text(
        '[project]\nname = "locked"\nversion = "0.1.0"\n'
    )
    main = tmp_path / "Main.geno"
    main.write_text(_MAIN_SRC)
    return main


def _chmod_denied(monkeypatch):
    """Deny chmod on the lock directory the way a restricted sandbox does."""
    import os

    import geno.package_manager as package_manager

    real_chmod = os.chmod

    def denied(path, mode, *args, **kwargs):
        if "geno-package-locks" in str(path):
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(package_manager.os, "chmod", denied)


def test_lock_directory_chmod_denial_is_a_resolution_error(tmp_path, monkeypatch):
    """A PermissionError preparing lock state must not escape as a traceback."""
    main = _manifest_project(tmp_path)
    _chmod_denied(monkeypatch)

    with pytest.raises(ProjectResolutionError) as exc_info:
        resolve_project_context(str(main))

    message = str(exc_info.value)
    assert "package lock directory" in message
    # The diagnostic must name the supported configuration, not just fail.
    assert "XDG_RUNTIME_DIR" in message


def test_check_reports_lock_setup_failure_without_a_traceback(
    tmp_path, monkeypatch, capsys
):
    """#96: `geno check` exits 1 with a diagnostic, never a raw traceback."""
    from geno.cli.check import check_file

    main = _manifest_project(tmp_path)
    _chmod_denied(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        check_file(str(main))

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "package lock directory" in err
    assert "XDG_RUNTIME_DIR" in err
    assert "Traceback" not in err
    assert "PermissionError" not in err


def test_test_suite_reports_lock_setup_failure_as_a_project_error(
    tmp_path, monkeypatch
):
    """The analogous setup path in `geno test` is covered too."""
    from geno.test_runner import run_project_test_suite

    main = _manifest_project(tmp_path)
    _chmod_denied(monkeypatch)

    suite = run_project_test_suite(main)

    assert not suite.success
    errors = " ".join(result.error or "" for result in suite.file_results)
    assert "package lock directory" in errors


def test_lock_setup_error_does_not_weaken_privacy_validation(tmp_path, monkeypatch):
    """A lock directory owned by another user is still refused outright.

    The clean diagnostic replaces the traceback; it must not become a way to
    proceed on state this process does not own.
    """
    import geno.package_manager as package_manager

    # Pretend to be a uid that owns nothing here, so the ownership check trips.
    monkeypatch.setattr(package_manager, "_current_uid", lambda: 999999)
    main = _manifest_project(tmp_path)

    with pytest.raises(ProjectResolutionError) as exc_info:
        resolve_project_context(str(main))

    assert "owned by another user" in str(exc_info.value)
