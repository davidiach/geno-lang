"""Shared pytest fixtures for the Geno test suite.

Provides reusable fixtures for common test patterns found across multiple
test files, such as project directory setup, sandbox configuration, and
multi-module project typechecking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from geno.tests._script_runner import run_node_code


@pytest.fixture()
def geno_project_dir(tmp_path: Path) -> Path:
    """A tmp directory pre-seeded with a minimal geno.toml manifest.

    Creates a project directory containing a geno.toml with a single
    entrypoint module named "Main":

        entrypoint = "Main"
        files = ["Main"]

    Tests can add .geno source files and modify the manifest as needed.
    The returned path is the project root directory.

    Used by: test_exports, test_qualified_imports, test_multi_module_compile,
    test_stdlib, test_target_aware, test_lsp, test_cli, and others.
    """
    (tmp_path / "geno.toml").write_text('entrypoint = "Main"\nfiles = ["Main"]\n')
    return tmp_path


@pytest.fixture()
def geno_multi_module_dir(tmp_path: Path) -> Callable[[list[str]], Path]:
    """Factory fixture that creates a geno.toml with a custom file list.

    Returns a callable that accepts a list of module names and writes the
    corresponding geno.toml. The first module in the list is used as the
    entrypoint.

    Example usage::

        def test_something(geno_multi_module_dir):
            project = geno_multi_module_dir(["App", "Math", "Utils"])
            (project / "Math.geno").write_text("...")

    Used by: test_multi_module_compile, test_dependency_graph,
    test_exports, test_qualified_imports, test_target_aware.
    """

    def _create(module_names: list[str]) -> Path:
        entrypoint = module_names[0]
        files_str = ", ".join(f'"{name}"' for name in module_names)
        (tmp_path / "geno.toml").write_text(
            f'entrypoint = "{entrypoint}"\nfiles = [{files_str}]\n'
        )
        return tmp_path

    return _create


@pytest.fixture()
def sandbox_config_permissive():
    """A SandboxConfig with strict=False for tests that need relaxed sandboxing.

    Returns a fresh SandboxConfig(strict=False) instance. Many security and
    sandbox tests need a non-strict config to test specific behaviors without
    triggering the full strict-mode restrictions.

    Used by: test_security, test_security_attacks, test_sandbox_config.
    """
    from geno.sandbox import SandboxConfig

    return SandboxConfig(strict=False)


@pytest.fixture()
def typecheck_project(tmp_path: Path) -> Callable[[Path | None], None]:
    """Fixture that typechecks a multi-module project directory.

    Returns a callable that discovers the project graph from a directory,
    resolves the dependency graph, and runs the TypeChecker on it. Raises
    on type errors, so tests can use it to assert that a project typechecks
    cleanly or combine with pytest.raises to expect failures.

    The callable accepts an optional project path; if omitted, it uses
    the test's tmp_path.

    Example usage::

        def test_project_typechecks(typecheck_project, tmp_path):
            (tmp_path / "geno.toml").write_text('...')
            (tmp_path / "Main.geno").write_text('...')
            typecheck_project(tmp_path)  # raises on type error

    Used by: test_qualified_imports, test_exports, test_stdlib.
    """
    from geno.dependency_graph import DependencyGraph
    from geno.project_graph import ProjectGraph
    from geno.typechecker import TypeChecker

    def _check(project_dir: Path | None = None) -> None:
        target = project_dir if project_dir is not None else tmp_path
        pg = ProjectGraph.discover(target)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checker.check_project_graph(dg)

    return _check


@pytest.fixture()
def compile_and_run_js() -> Callable[[str], str]:
    """Compile a Geno source string to JavaScript and execute it via Node.

    Returns a callable that takes Geno source code, compiles it to JS,
    runs it with Node.js, and returns the stripped stdout. Raises
    RuntimeError if Node exits with a non-zero status.

    Note: requires Node.js to be available on PATH. Tests using this
    fixture should be marked with @pytest.mark.skipif if Node is absent.

    Used by: test_js_compiler (heavily), test_backend_parity.
    """
    from geno.js_compiler import compile_to_js

    def _run(source: str) -> str:
        js_out = compile_to_js(source)
        assert isinstance(js_out, str)
        result = run_node_code(js_out, args=("--cap", "print"), timeout=10)
        if result.returncode != 0:
            raise RuntimeError(f"JS execution failed: {result.stderr}")
        return result.stdout.strip()

    return _run


@pytest.fixture()
def compile_and_run_py() -> Callable[[str], object]:
    """Compile a Geno source string to Python and execute it.

    Returns a callable that takes Geno source code, compiles and executes
    it via compile_and_exec, then calls main() if it exists and returns
    the result (or None if no main function is defined).

    Used by: test_compiler, test_backend_parity.
    """
    from geno.compiler import compile_and_exec

    def _run(source: str) -> object:
        globals_dict = compile_and_exec(source, timeout=None)
        if "main" in globals_dict:
            return globals_dict["main"]()
        return None

    return _run
