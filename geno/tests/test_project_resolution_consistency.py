"""Consistency checks across project-resolution consumers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from geno.api import RunConfig, check_path, run_path
from geno.cli.watch import _resolve_watch_files
from geno.lsp_server import _load_project_module_index, create_server
from geno.project_graph import ProjectGraphError
from geno.project_resolution import (
    ProjectResolutionError,
    resolve_file_context,
    resolve_project_context,
)
from geno.test_runner import run_project_test_suite
from geno.tests.project_resolution_fixture_helpers import (
    write_dependency_collision_fixture,
)

try:
    from lsprotocol import types
    from pygls.workspace import Workspace

    HAS_PYGLS = True
except ImportError:
    HAS_PYGLS = False


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the Geno CLI and capture output."""
    return subprocess.run(
        [sys.executable, "-m", "geno", *args],
        capture_output=True,
        text=True,
        timeout=15,
    )


def _write_direct_file_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a direct-file project with transitive sibling imports."""
    utils_file = tmp_path / "Utils.geno"
    format_file = tmp_path / "Format.geno"
    app_file = tmp_path / "App.geno"

    utils_file.write_text(
        "func double(x: Int) -> Int\n  example 2 -> 4\n  return x * 2\nend func\n"
    )
    format_file.write_text(
        "import Utils\n"
        "func boost(x: Int) -> Int\n"
        "  example 3 -> 8\n"
        "  return double(x) + 2\n"
        "end func\n"
    )
    app_file.write_text(
        "import Format\n"
        "func compute(x: Int) -> Int\n"
        "  example 5 -> 12\n"
        "  return boost(x)\n"
        "end func\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return compute(5)\n"
        "end func\n"
    )
    return app_file, format_file, utils_file


def _write_manifest_error_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Create a manifest project with an imported module type error."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\nfiles = ["App", "Utils"]\n'
    )
    utils_file = tmp_path / "Utils.geno"
    app_file = tmp_path / "App.geno"

    utils_file.write_text(
        '@untested("consistency fixture")\n'
        "func helper(x: Int) -> Int\n"
        '  return "oops"\n'
        "end func\n"
    )
    app_file.write_text(
        "import Utils\nfunc main() -> Int\n  return helper(1)\nend func\n"
    )
    return app_file, utils_file


def _write_manifest_explicit_file_fixture(
    tmp_path: Path, *, bad_scratch: bool = False
) -> tuple[Path, Path, Path]:
    """Create a manifest project with an explicit file outside the project graph."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\nfiles = ["App", "Helper"]\n'
    )
    app_file = tmp_path / "App.geno"
    helper_file = tmp_path / "Helper.geno"
    scratch_file = tmp_path / "Scratch.geno"

    app_file.write_text(
        '@untested("entry point helper")\n'
        "func app_only() -> Int\n"
        "  return 99\n"
        "end func\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return app_only()\n"
        "end func\n"
    )
    helper_file.write_text(
        "func bump(x: Int) -> Int\n  example 1 -> 2\n  return x + 1\nend func\n"
    )
    if bad_scratch:
        scratch_file.write_text(
            "import Helper\n"
            '@untested("scratch entry point")\n'
            "func main() -> Int\n"
            '  return "oops"\n'
            "end func\n"
        )
    else:
        scratch_file.write_text(
            "import Helper\n"
            '@untested("scratch helper")\n'
            "func scratch_only() -> Int\n"
            "  return bump(1)\n"
            "end func\n"
            '@untested("scratch entry point")\n'
            "func main() -> Int\n"
            "  return scratch_only()\n"
            "end func\n"
        )

    return app_file, helper_file, scratch_file


def _write_manifest_explicit_included_file_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    """Create a manifest project where an explicitly requested file is listed."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\nfiles = ["App", "Helper", "Scratch"]\n'
    )
    app_file = tmp_path / "App.geno"
    helper_file = tmp_path / "Helper.geno"
    scratch_file = tmp_path / "Scratch.geno"

    app_file.write_text(
        '@untested("poisoned manifest entry point")\n'
        "func main() -> Int\n"
        '  return "oops"\n'
        "end func\n"
    )
    helper_file.write_text(
        "func bump(x: Int) -> Int\n  example 1 -> 2\n  return x + 1\nend func\n"
    )
    scratch_file.write_text(
        "import Helper\n"
        '@untested("scratch entry point")\n'
        "func main() -> Int\n"
        "  return bump(1)\n"
        "end func\n"
    )

    return app_file, helper_file, scratch_file


def _write_manifest_explicit_dependency_fixture(
    tmp_path: Path, *, nested: bool = False
) -> tuple[Path, Path, Path, Path]:
    """Create a manifest project with an explicit file importing a dependency."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\nfiles = ["App"]\n\n'
        '[dependencies.helper]\ngit = "https://example.com/helper.git"\n'
    )
    app_file = tmp_path / "App.geno"
    app_file.write_text(
        '@untested("manifest entry point")\nfunc main() -> Int\n  return 99\nend func\n'
    )

    dep_dir = tmp_path / "geno_modules" / "helper"
    dep_dir.mkdir(parents=True)
    dep_manifest = dep_dir / "geno.toml"
    dep_manifest.write_text('entrypoint = "Helper"\n')
    helper_file = dep_dir / "Helper.geno"
    helper_file.write_text(
        "export func bump(x: Int) -> Int\n  example 1 -> 2\n  return x + 1\nend func\n"
    )

    scratch_dir = tmp_path / "scratchpad" if nested else tmp_path
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scratch_file = scratch_dir / "Scratch.geno"
    scratch_file.write_text(
        "import Helper\n"
        '@untested("scratch entry point")\n'
        "func main() -> Int\n"
        "  return bump(1)\n"
        "end func\n"
    )

    return helper_file, dep_manifest, scratch_file, tmp_path / "geno.toml"


def _write_overlay_redirect_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, str, str]:
    """Create a direct-file project whose transitive import can be redirected."""
    app_file = tmp_path / "App.geno"
    utils_file = tmp_path / "Utils.geno"
    leaf_file = tmp_path / "Leaf.geno"
    alt_file = tmp_path / "Alt.geno"

    app_file.write_text(
        "import Utils\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return helper()\n"
        "end func\n"
    )
    utils_file.write_text(
        "import Leaf\n"
        "func helper() -> Int\n"
        "  example () -> 1\n"
        "  return selected()\n"
        "end func\n"
    )
    leaf_file.write_text(
        "export func selected() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )
    alt_file.write_text(
        "export func selected() -> Int\n  example () -> 10\n  return 10\nend func\n"
    )

    overlay_utils = (
        "import Alt\n"
        "func helper() -> Int\n"
        "  example () -> 99\n"
        "  return selected()\n"
        "end func\n"
    )
    overlay_alt = (
        "export func selected() -> Int\n  example () -> 99\n  return 99\nend func\n"
    )
    return app_file, utils_file, leaf_file, alt_file, overlay_utils, overlay_alt


def _write_dependency_overlay_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, str, str]:
    """Create a manifest project whose dependency can be overridden in memory."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\nfiles = ["App"]\n\n'
        '[dependencies.helper]\ngit = "https://example.com/helper.git"\n'
    )
    app_file = tmp_path / "App.geno"
    helper_dir = tmp_path / "geno_modules" / "helper"
    helper_dir.mkdir(parents=True)
    helper_file = helper_dir / "Helper.geno"
    leaf_file = helper_dir / "Leaf.geno"
    alt_file = helper_dir / "Alt.geno"

    app_file.write_text(
        "import Helper\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return helper()\n"
        "end func\n"
    )
    helper_file.write_text(
        "import Leaf\n"
        "func helper() -> Int\n"
        "  example () -> 1\n"
        "  return selected()\n"
        "end func\n"
    )
    leaf_file.write_text(
        "export func selected() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )
    alt_file.write_text(
        "export func selected() -> Int\n  example () -> 10\n  return 10\nend func\n"
    )

    overlay_helper = (
        "import Alt\n"
        "func helper() -> Int\n"
        "  example () -> 99\n"
        "  return selected()\n"
        "end func\n"
    )
    overlay_alt = (
        "export func selected() -> Int\n  example () -> 99\n  return 99\nend func\n"
    )
    return app_file, helper_file, leaf_file, alt_file, overlay_helper, overlay_alt


def _write_dependency_same_stem_private_chain_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    """Create an acyclic dependency chain with same-stem private modules."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\n'
        'files = ["App"]\n\n'
        "[dependencies.alpha]\n"
        'git = "https://example.com/alpha.git"\n\n'
        "[dependencies.beta]\n"
        'git = "https://example.com/beta.git"\n'
    )
    app_file = tmp_path / "App.geno"
    alpha_dir = tmp_path / "geno_modules" / "alpha"
    beta_dir = tmp_path / "geno_modules" / "beta"
    alpha_dir.mkdir(parents=True)
    beta_dir.mkdir(parents=True)

    app_file.write_text(
        "import Alpha\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return alpha_value()\n"
        "end func\n"
    )
    (alpha_dir / "Alpha.geno").write_text(
        "import Shared\n"
        "func alpha_value() -> Int\n"
        "  example () -> 2\n"
        "  return shared_value()\n"
        "end func\n"
    )
    alpha_shared = alpha_dir / "Shared.geno"
    alpha_shared.write_text(
        "import Beta\n"
        "func shared_value() -> Int\n"
        "  example () -> 2\n"
        "  return beta_value() + 1\n"
        "end func\n"
    )
    (beta_dir / "Beta.geno").write_text(
        "import Shared\n"
        "func beta_value() -> Int\n"
        "  example () -> 1\n"
        "  return beta_shared()\n"
        "end func\n"
    )
    beta_shared = beta_dir / "Shared.geno"
    beta_shared.write_text(
        "func beta_shared() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )
    return app_file, alpha_shared, beta_shared


def _write_dependency_overlay_collision_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, str, str]:
    """Create two dependencies with colliding private overlay candidates."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\n'
        'files = ["App"]\n\n'
        "[dependencies.alpha]\n"
        'git = "https://example.com/alpha.git"\n\n'
        "[dependencies.beta]\n"
        'git = "https://example.com/beta.git"\n'
    )
    app_file = tmp_path / "App.geno"
    alpha_dir = tmp_path / "geno_modules" / "alpha"
    beta_dir = tmp_path / "geno_modules" / "beta"
    alpha_dir.mkdir(parents=True)
    beta_dir.mkdir(parents=True)

    app_file.write_text(
        "import Alpha\n"
        "import Beta\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return alpha_value() + beta_value()\n"
        "end func\n"
    )
    (alpha_dir / "Alpha.geno").write_text(
        "import Helper\n"
        '@untested("overlay fixture")\n'
        "func alpha_value() -> Int\n"
        "  return alpha_helper()\n"
        "end func\n"
    )
    (alpha_dir / "Helper.geno").write_text(
        "import Leaf\n"
        "func alpha_helper() -> Int\n"
        "  example () -> 1\n"
        "  return alpha_selected()\n"
        "end func\n"
    )
    (alpha_dir / "Leaf.geno").write_text(
        "export func alpha_selected() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )
    alpha_alt = alpha_dir / "Alt.geno"
    alpha_alt.write_text(
        "export func alpha_selected() -> Int\n"
        "  example () -> 40\n"
        "  return 40\n"
        "end func\n"
    )
    unused_file = alpha_dir / "Unused.geno"
    unused_file.write_text(
        "func unused_value() -> Int\n  example () -> 100\n  return 100\nend func\n"
    )

    (beta_dir / "Beta.geno").write_text(
        "import Alt\n"
        "func beta_value() -> Int\n"
        "  example () -> 2\n"
        "  return beta_selected()\n"
        "end func\n"
    )
    beta_alt = beta_dir / "Alt.geno"
    beta_alt.write_text(
        "export func beta_selected() -> Int\n  example () -> 2\n  return 2\nend func\n"
    )

    overlay_helper = (
        "import Alt\n"
        "func alpha_helper() -> Int\n"
        "  example () -> 41\n"
        "  return alpha_selected()\n"
        "end func\n"
    )
    overlay_alt = (
        "export func alpha_selected() -> Int\n"
        "  example () -> 41\n"
        "  return 41\n"
        "end func\n"
    )
    return (
        app_file,
        alpha_alt,
        beta_alt,
        unused_file,
        overlay_helper,
        overlay_alt,
    )


def _write_nested_dependency_overlay_ambiguity_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, Path]:
    """Create unseen same-stem modules declared by nested dependency manifests."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\n'
        'files = ["App"]\n\n'
        "[dependencies.alpha]\n"
        'git = "https://example.com/alpha.git"\n\n'
        "[dependencies.beta]\n"
        'git = "https://example.com/beta.git"\n'
    )
    app_file = tmp_path / "App.geno"
    app_file.write_text(
        "import Alpha\n"
        "import Beta\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return alpha_value() + beta_value()\n"
        "end func\n"
    )

    alpha_dir = tmp_path / "geno_modules" / "alpha"
    alpha_src = alpha_dir / "src"
    alpha_src.mkdir(parents=True)
    (alpha_dir / "geno.toml").write_text(
        'entrypoint = "Core"\nfiles = ["Core", "src/Alt", "src/Shared"]\n'
    )
    alpha_entry = alpha_dir / "Core.geno"
    alpha_entry.write_text(
        "func alpha_value() -> Int\n  example () -> 1\n  return 1\nend func\n"
    )
    alpha_shared = alpha_src / "Shared.geno"
    alpha_shared.write_text(
        "func alpha_shared() -> Int\n  example () -> 10\n  return 10\nend func\n"
    )
    (alpha_src / "Alt.geno").write_text(
        "export func alpha_alt_value() -> Int\n"
        "  example () -> 10\n"
        "  return 10\n"
        "end func\n"
    )

    beta_dir = tmp_path / "geno_modules" / "beta"
    beta_lib = beta_dir / "lib"
    beta_lib.mkdir(parents=True)
    (beta_dir / "geno.toml").write_text(
        'entrypoint = "Start"\nfiles = ["Start", "lib/Shared"]\n'
    )
    beta_entry = beta_dir / "Start.geno"
    beta_entry.write_text(
        "func beta_value() -> Int\n  example () -> 2\n  return 2\nend func\n"
    )
    beta_shared = beta_lib / "Shared.geno"
    beta_shared.write_text(
        "func beta_shared() -> Int\n  example () -> 20\n  return 20\nend func\n"
    )
    return app_file, alpha_entry, beta_entry, alpha_shared, beta_shared


def _write_stdlib_overlay_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, str, str]:
    """Create a manifest project whose stdlib Math import can be overridden."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\nfiles = ["App", "Local"]\n'
    )
    app_file = tmp_path / "App.geno"
    local_file = tmp_path / "Local.geno"
    std_math_file = Path(__file__).resolve().parents[1] / "std" / "Math.geno"

    local_file.write_text(
        "func seed() -> Int\n  example () -> -5\n  return 0 - 5\nend func\n"
    )
    app_file.write_text(
        "import Local\n"
        "import Math\n"
        '@untested("entry point")\n'
        "func main() -> Int\n"
        "  return abs(seed())\n"
        "end func\n"
    )

    overlay_math = (
        "func abs(x: Int) -> Int\n  example -5 -> 42\n  return 42\nend func\n"
    )
    bad_overlay_math = (
        'func abs(x: Int) -> Int\n  example -5 -> 0\n  return "bad"\nend func\n'
    )
    return app_file, local_file, std_math_file, overlay_math, bad_overlay_math


class TestProjectResolutionConsistency:
    def test_direct_file_success_stays_consistent_across_surfaces(self, tmp_path):
        """Resolver-facing success surfaces agree on one direct-file fixture."""
        app_file, format_file, utils_file = _write_direct_file_fixture(tmp_path)

        project_context = resolve_project_context(app_file)
        file_context = resolve_file_context(app_file)

        expected_imports = {"Format", "Utils"}
        expected_modules = {"App", "Format", "Utils"}
        expected_paths = {
            str(app_file.resolve()),
            str(format_file.resolve()),
            str(utils_file.resolve()),
        }

        assert project_context.entrypoint == "App"
        assert file_context.module_name == "App"
        assert set(project_context.module_sources) == expected_imports
        assert set(file_context.module_sources) == expected_imports
        assert set(project_context.dependency_graph.sorted_modules) == expected_modules
        assert set(file_context.dependency_graph.sorted_modules) == expected_modules
        assert {
            rf.module_name for rf in project_context.project.files
        } == expected_modules

        project_modules, path_to_module = _load_project_module_index(app_file)
        assert set(project_modules) == expected_modules
        assert set(path_to_module) == expected_paths
        assert path_to_module[str(app_file.resolve())] == "App"
        assert path_to_module[str(format_file.resolve())] == "Format"
        assert path_to_module[str(utils_file.resolve())] == "Utils"

        check_result = check_path(str(app_file))
        assert check_result.ok is True

        run_result = run_path(str(app_file))
        assert run_result.ok is True
        assert run_result.value == 12

        cli_check = _run_cli("check", str(app_file))
        assert cli_check.returncode == 0
        assert "Type check passed" in cli_check.stdout
        assert "3 modules" in cli_check.stdout

        cli_run = _run_cli("run", str(app_file))
        assert cli_run.returncode == 12
        assert cli_run.stdout == ""

        suite = run_project_test_suite(app_file)
        assert suite.success is True
        assert suite.total == 3
        assert suite.passed == 3
        assert {Path(fr.path).resolve() for fr in suite.file_results} == {
            app_file.resolve(),
            format_file.resolve(),
            utils_file.resolve(),
        }

    def test_explicit_out_of_manifest_file_resolves_requested_module(self, tmp_path):
        """Explicit file paths should resolve the requested file, not the manifest entrypoint."""
        _app_file, helper_file, scratch_file = _write_manifest_explicit_file_fixture(
            tmp_path
        )

        project_context = resolve_project_context(scratch_file)
        file_context = resolve_file_context(scratch_file)

        assert project_context.entrypoint == "Scratch"
        assert project_context.entry_file.path.resolve() == scratch_file.resolve()
        assert project_context.source == scratch_file.read_text()
        assert file_context.module_name == "Scratch"
        assert set(project_context.module_sources) == {"Helper"}
        assert set(file_context.module_sources) == {"Helper"}
        assert set(project_context.dependency_graph.sorted_modules) == {
            "Helper",
            "Scratch",
        }
        assert set(file_context.dependency_graph.sorted_modules) == {
            "Helper",
            "Scratch",
        }
        assert {rf.module_name for rf in project_context.project.files} == {
            "Helper",
            "Scratch",
        }
        assert project_context.dependency_graph.file_map["Helper"].path.resolve() == (
            helper_file.resolve()
        )

    def test_explicit_out_of_manifest_file_stays_consistent_across_api_and_cli(
        self, tmp_path
    ):
        """API and CLI surfaces should keep targeting the explicitly requested file."""
        _app_file, _helper_file, scratch_file = _write_manifest_explicit_file_fixture(
            tmp_path
        )

        check_result = check_path(str(scratch_file))
        assert check_result.ok is True

        run_result = run_path(str(scratch_file))
        assert run_result.ok is True
        assert run_result.value == 2

        cli_check = _run_cli("check", str(scratch_file))
        assert cli_check.returncode == 0
        assert "Type check passed" in cli_check.stdout
        assert "Scratch.geno" in cli_check.stdout

        cli_run = _run_cli("run", str(scratch_file))
        assert cli_run.returncode == 2
        assert cli_run.stdout == ""

        output_file = tmp_path / "scratch.py"
        cli_compile = _run_cli("compile", str(scratch_file), "-o", str(output_file))
        assert cli_compile.returncode == 0
        compiled = output_file.read_text(encoding="utf-8")
        assert "def scratch_only" in compiled
        assert "def app_only" not in compiled

    def test_explicit_in_manifest_file_ignores_unrelated_manifest_siblings(
        self, tmp_path
    ):
        """Direct file requests should use the requested file's import closure."""
        app_file, _helper_file, scratch_file = (
            _write_manifest_explicit_included_file_fixture(tmp_path)
        )

        project_context = resolve_project_context(scratch_file)
        assert project_context.entrypoint == "Scratch"
        assert set(project_context.dependency_graph.sorted_modules) == {
            "Helper",
            "Scratch",
        }

        check_result = check_path(str(scratch_file))
        assert check_result.ok is True

        run_result = run_path(str(scratch_file))
        assert run_result.ok is True
        assert run_result.value == 2

        full_project_check = check_path(str(app_file))
        assert full_project_check.ok is False

    def test_explicit_out_of_manifest_file_type_errors_do_not_silently_check_app(
        self, tmp_path
    ):
        """Checking an explicit file should fail on that file's errors, not App.geno."""
        _app_file, _helper_file, scratch_file = _write_manifest_explicit_file_fixture(
            tmp_path,
            bad_scratch=True,
        )

        check_result = check_path(str(scratch_file))
        assert check_result.ok is False
        assert any(
            diag.location is not None
            and Path(diag.location.filename).resolve() == scratch_file.resolve()
            for diag in check_result.diagnostics
        )
        assert any(
            "String" in diag.message and "Int" in diag.message
            for diag in check_result.diagnostics
        )

        cli_check = _run_cli("check", str(scratch_file))
        assert cli_check.returncode != 0
        assert "Scratch.geno" in cli_check.stderr
        assert "String" in cli_check.stderr
        assert "Int" in cli_check.stderr

    def test_explicit_out_of_manifest_file_preserves_manifest_root_and_dependencies(
        self, tmp_path
    ):
        """Explicit files should keep manifest metadata needed by downstream tools."""
        helper_file, dep_manifest, scratch_file, project_manifest = (
            _write_manifest_explicit_dependency_fixture(tmp_path, nested=True)
        )

        project_context = resolve_project_context(scratch_file)

        assert project_context.entrypoint == "Scratch"
        assert project_context.project.root == tmp_path.resolve()
        assert project_context.project.dependencies == {
            "helper": dep_manifest.parent.resolve()
        }
        assert set(project_context.dependency_graph.sorted_modules) == {
            "Helper",
            "Scratch",
        }
        helper_path = project_context.dependency_graph.file_map["Helper"].path
        assert helper_path.name == "Helper.geno"
        assert helper_path.parent.name.lower() == "helper"
        assert helper_file.name == "Helper.geno"
        assert project_manifest.exists()

    def test_direct_file_dependency_private_same_stem_chain_is_not_a_cycle(
        self, tmp_path
    ):
        """Same-stem private modules in different packages should not collide."""
        app_file, alpha_shared, beta_shared = (
            _write_dependency_same_stem_private_chain_fixture(tmp_path)
        )

        project_context = resolve_project_context(app_file)

        shared_modules = [
            resolved
            for resolved in project_context.project.files
            if resolved.module_name == "Shared"
        ]
        assert {resolved.package_name for resolved in shared_modules} == {
            "alpha",
            "beta",
        }
        assert {resolved.path.resolve() for resolved in shared_modules} == {
            alpha_shared.resolve(),
            beta_shared.resolve(),
        }
        assert len({resolved.graph_key for resolved in shared_modules}) == 2

        run_result = run_path(str(app_file))
        assert run_result.ok is True
        assert run_result.value == 2

    def test_dependency_entry_overlay_reruns_amid_same_stem_collisions(self, tmp_path):
        """Name-keyed overlays keep working when private module names collide."""
        app_file, alpha_shared, _beta_shared = (
            _write_dependency_same_stem_private_chain_fixture(tmp_path)
        )
        overlay_alpha = (
            "import Shared\n"
            "func alpha_value() -> Int\n"
            "  example () -> 43\n"
            "  return shared_value() + 41\n"
            "end func\n"
        )

        overlay_run = run_path(
            str(app_file), RunConfig(modules={"Alpha": overlay_alpha})
        )
        assert overlay_run.ok is True
        assert overlay_run.value == 43

        overlay_check = check_path(str(app_file), modules={"Alpha": overlay_alpha})
        assert overlay_check.ok is True

        project_context = resolve_project_context(app_file)
        merged = project_context.merged_module_sources({"Alpha": overlay_alpha})
        assert merged is not None
        alpha_shared_key = next(
            graph_key
            for graph_key, resolved in (
                project_context.dependency_graph.file_map.items()
            )
            if resolved.path.resolve() == alpha_shared.resolve()
        )
        assert f"import {alpha_shared_key}" in merged["Alpha"]
        assert "import Shared\n" not in merged["Alpha"]

    def test_dependency_overlay_discovers_new_package_local_disk_import(self, tmp_path):
        """An overlay can redirect to an on-disk sibling outside the base closure."""
        (
            app_file,
            alpha_alt,
            _beta_alt,
            _unused_file,
            overlay_helper,
            _overlay_alt,
        ) = _write_dependency_overlay_collision_fixture(tmp_path)
        disk_redirect = overlay_helper.replace("41", "40")

        baseline = resolve_project_context(app_file)
        assert all(
            resolved.path.resolve() != alpha_alt.resolve()
            for resolved in baseline.dependency_graph.file_map.values()
        )

        result = run_path(
            str(app_file),
            RunConfig(modules={"Helper": disk_redirect}),
        )
        assert result.ok is True, result.diagnostics
        assert result.value == 42
        assert check_path(str(app_file), modules={"Helper": disk_redirect}).ok is True

        merged = baseline.merged_module_sources({"Helper": disk_redirect})
        assert merged is not None
        assert any(
            "alpha_selected" in source and "return 40" in source
            for source in merged.values()
        )
        assert not any("unused_value" in source for source in merged.values())

    def test_dependency_overlays_bind_imported_names_package_locally(self, tmp_path):
        """An unrelated dependency's same-stem module cannot steal an overlay."""
        (
            app_file,
            _alpha_alt,
            beta_alt,
            _unused_file,
            overlay_helper,
            overlay_alt,
        ) = _write_dependency_overlay_collision_fixture(tmp_path)
        overlays = {"Alt": overlay_alt, "Helper": overlay_helper}

        result = run_path(str(app_file), RunConfig(modules=overlays))
        assert result.ok is True, result.diagnostics
        assert result.value == 43
        assert check_path(str(app_file), modules=overlays).ok is True

        context = resolve_project_context(app_file)
        beta_alt_key = next(
            graph_key
            for graph_key, resolved in context.dependency_graph.file_map.items()
            if resolved.path.resolve() == beta_alt.resolve()
        )
        overlay_keys = context.overlay_graph_keys(overlays)
        alpha_alt_key = next(
            graph_key for graph_key, name in overlay_keys.items() if name == "Alt"
        )
        assert alpha_alt_key != beta_alt_key
        merged = context.merged_module_sources(overlays)
        assert merged is not None
        assert merged[alpha_alt_key] == overlay_alt
        assert "beta_selected" in merged[beta_alt_key]

    def test_package_local_overlay_beats_same_named_public_dependency(self, tmp_path):
        """Importer context outranks an exact public graph key with the same name."""
        app_file, *_ = _write_dependency_overlay_collision_fixture(tmp_path)
        manifest = tmp_path / "geno.toml"
        manifest.write_text(
            manifest.read_text()
            + "\n[dependencies.zeta]\n"
            + 'git = "https://example.com/zeta.git"\n'
        )

        zeta_dir = tmp_path / "geno_modules" / "zeta"
        zeta_dir.mkdir()
        public_zeta = zeta_dir / "Zeta.geno"
        public_zeta_source = (
            "func zeta_value() -> Int\n  example () -> 3\n  return 3\nend func\n"
        )
        public_zeta.write_text(public_zeta_source)
        (tmp_path / "geno_modules" / "alpha" / "Zeta.geno").write_text(
            "export func alpha_selected() -> Int\n"
            "  example () -> 40\n"
            "  return 40\n"
            "end func\n"
        )
        app_file.write_text(
            "import Alpha\n"
            "import Beta\n"
            "import Zeta\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "  return alpha_value() + beta_value() + zeta_value()\n"
            "end func\n"
        )
        overlay_helper = (
            "import Zeta\n"
            "func alpha_helper() -> Int\n"
            "  example () -> 41\n"
            "  return alpha_selected()\n"
            "end func\n"
        )
        overlay_zeta = (
            "export func alpha_selected() -> Int\n"
            "  example () -> 41\n"
            "  return 41\n"
            "end func\n"
        )
        overlays = {"Helper": overlay_helper, "Zeta": overlay_zeta}

        result = run_path(str(app_file), RunConfig(modules=overlays))
        assert result.ok is True, result.diagnostics
        assert result.value == 46

        context = resolve_project_context(app_file)
        assert context.dependency_graph.file_map["Zeta"].path == public_zeta
        overlay_keys = context.overlay_graph_keys(overlays)
        private_zeta_key = next(
            graph_key for graph_key, name in overlay_keys.items() if name == "Zeta"
        )
        assert private_zeta_key != "Zeta"
        merged = context.merged_module_sources(overlays)
        assert merged is not None
        assert merged[private_zeta_key] == overlay_zeta
        assert merged["Zeta"] == public_zeta_source

        public_overlay = (
            "func zeta_value() -> Int\n  example () -> 30\n  return 30\nend func\n"
        )
        public_result = run_path(
            str(app_file),
            RunConfig(modules={"Zeta": public_overlay}),
        )
        assert public_result.ok is True, public_result.diagnostics
        assert public_result.value == 33
        assert context.overlay_graph_keys({"Zeta": public_overlay}) == {"Zeta": "Zeta"}

    def test_dependency_overlay_discovers_nested_manifest_module(self, tmp_path):
        """File-path overlays resolve newly imported nested package modules."""
        app_file, _alpha_entry, _beta_entry, alpha_shared, _beta_shared = (
            _write_nested_dependency_overlay_ambiguity_fixture(tmp_path)
        )
        alpha_alt = alpha_shared.parent / "Alt.geno"
        overlay_alpha = (
            "import Alt\n"
            "func alpha_value() -> Int\n"
            "  example () -> 10\n"
            "  return alpha_alt_value()\n"
            "end func\n"
        )

        baseline = resolve_project_context(app_file)
        assert all(
            resolved.path.resolve() != alpha_alt.resolve()
            for resolved in baseline.dependency_graph.file_map.values()
        )

        result = run_path(
            str(app_file),
            RunConfig(modules={"Alpha": overlay_alpha}),
        )
        assert result.ok is True, result.diagnostics
        assert result.value == 12

        merged = baseline.merged_module_sources({"Alpha": overlay_alpha})
        assert merged is not None
        assert any("alpha_alt_value" in source for source in merged.values())
        assert not any("alpha_shared" in source for source in merged.values())

        alpha_alt.write_text(
            "export func alpha_alt_value() -> Int\n"
            "  example () -> 10\n"
            '  return "bad"\n'
            "end func\n"
        )
        bad_check = check_path(str(app_file), modules={"Alpha": overlay_alpha})
        assert bad_check.ok is False
        assert any(
            diagnostic.location is not None
            and Path(diagnostic.location.filename).resolve() == alpha_alt.resolve()
            for diagnostic in bad_check.diagnostics
        )
        assert not any(
            diagnostic.location is not None
            and "GenoDep" in diagnostic.location.filename
            for diagnostic in bad_check.diagnostics
        )

    def test_ambiguous_private_overlay_name_reports_package_candidates(self, tmp_path):
        """A context-free same-stem overlay fails clearly instead of doing nothing."""
        app_file, alpha_entry, beta_entry, alpha_shared, beta_shared = (
            _write_nested_dependency_overlay_ambiguity_fixture(tmp_path)
        )
        context = resolve_project_context(app_file)
        assert context.dependency_graph.file_map["Alpha"].path == alpha_entry
        assert context.dependency_graph.file_map["Beta"].path == beta_entry
        assert all(
            resolved.module_name != "Shared"
            for resolved in context.dependency_graph.file_map.values()
        )
        overlay_shared = (
            "func shared_value() -> Int\n  example () -> 9\n  return 9\nend func\n"
        )

        with pytest.raises(ProjectResolutionError) as exc_info:
            context.merged_module_sources({"Shared": overlay_shared})

        message = str(exc_info.value)
        assert "Overlay module 'Shared' is ambiguous" in message
        assert "dependency package 'alpha'" in message
        assert "dependency package 'beta'" in message
        assert str(alpha_shared) in message
        assert str(beta_shared) in message
        assert "package's local context" in message

    def test_dependency_private_overlay_diagnostics_use_user_facing_names(
        self, tmp_path
    ):
        """A bad overlay of a dependency-private module reports its plain name."""
        app_file, _helper, _leaf, _alt, _overlay_helper, _overlay_alt = (
            _write_dependency_overlay_fixture(tmp_path)
        )
        bad_overlay_leaf = (
            "export func selected() -> Int\n"
            "  example () -> 0\n"
            '  return "bad"\n'
            "end func\n"
        )

        overlay_check = check_path(str(app_file), modules={"Leaf": bad_overlay_leaf})
        assert overlay_check.ok is False
        assert any(
            diag.location is not None and diag.location.filename == "<module:Leaf>"
            for diag in overlay_check.diagnostics
        )
        assert not any(
            diag.location is not None and "GenoDep" in diag.location.filename
            for diag in overlay_check.diagnostics
        )

    def test_watch_includes_dependency_manifest_for_explicit_out_of_manifest_file(
        self, tmp_path
    ):
        """Watch mode should still observe dependency manifests for explicit files."""
        _helper_file, dep_manifest, scratch_file, project_manifest = (
            _write_manifest_explicit_dependency_fixture(tmp_path)
        )

        watched = {path.resolve() for path in _resolve_watch_files(scratch_file)}

        assert scratch_file.resolve() in watched
        assert project_manifest.resolve() in watched
        assert dep_manifest.resolve() in watched

    @pytest.mark.skipif(not HAS_PYGLS, reason="pygls not installed")
    def test_imported_type_error_stays_consistent_across_api_cli_and_lsp(
        self, tmp_path, monkeypatch
    ):
        """Imported-module type errors are surfaced consistently."""
        app_file, utils_file = _write_manifest_error_fixture(tmp_path)

        check_result = check_path(str(app_file))
        assert check_result.ok is False
        assert any(
            diag.location is not None
            and Path(diag.location.filename).resolve() == utils_file.resolve()
            for diag in check_result.diagnostics
        )
        assert any(
            "String" in diag.message and "Int" in diag.message
            for diag in check_result.diagnostics
        )

        cli_check = _run_cli("check", str(app_file))
        assert cli_check.returncode != 0
        assert "Utils.geno" in cli_check.stderr
        assert "String" in cli_check.stderr
        assert "Int" in cli_check.stderr

        server = create_server()
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=app_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=app_file.read_text(),
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[app_file.as_uri()] == []
        assert latest[utils_file.as_uri()]
        assert any(
            "String" in diag.message and "Int" in diag.message
            for diag in latest[utils_file.as_uri()]
        )

    @pytest.mark.skipif(not HAS_PYGLS, reason="pygls not installed")
    def test_overlay_redirects_transitive_imports_consistently_for_api_and_lsp(
        self, tmp_path, monkeypatch
    ):
        """In-memory overlays can redirect a transitive import on both surfaces."""
        (
            app_file,
            utils_file,
            _leaf_file,
            alt_file,
            overlay_utils,
            overlay_alt,
        ) = _write_overlay_redirect_fixture(tmp_path)

        baseline = resolve_project_context(app_file)
        assert set(baseline.dependency_graph.sorted_modules) == {"App", "Utils", "Leaf"}

        baseline_run = run_path(str(app_file))
        assert baseline_run.ok is True
        assert baseline_run.value == 1

        overlay_modules = {
            "Utils": overlay_utils,
            "Alt": overlay_alt,
        }
        overlay_run = run_path(str(app_file), RunConfig(modules=overlay_modules))
        assert overlay_run.ok is True
        assert overlay_run.value == 99

        overlay_check = check_path(str(app_file), modules=overlay_modules)
        assert overlay_check.ok is True

        modules, path_to_module = _load_project_module_index(
            app_file,
            source_overrides={
                utils_file: overlay_utils,
                alt_file: overlay_alt,
            },
        )
        assert set(modules) == {"App", "Utils", "Alt"}
        assert "Leaf" not in modules
        assert path_to_module[str(alt_file.resolve())] == "Alt"
        assert "selected" in modules["Alt"][2]

        server = create_server()
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=app_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=app_file.read_text(),
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=overlay_utils,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=alt_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=overlay_alt,
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[app_file.as_uri()] == []

    @pytest.mark.skipif(not HAS_PYGLS, reason="pygls not installed")
    def test_overlay_type_errors_stay_consistent_between_api_and_lsp(
        self, tmp_path, monkeypatch
    ):
        """Overlay-introduced transitive type errors surface on both API and LSP."""
        (
            app_file,
            utils_file,
            _leaf_file,
            alt_file,
            overlay_utils,
            _overlay_alt,
        ) = _write_overlay_redirect_fixture(tmp_path)
        bad_overlay_alt = (
            "export func selected() -> Int\n"
            "  example () -> 0\n"
            '  return "bad"\n'
            "end func\n"
        )

        overlay_modules = {
            "Utils": overlay_utils,
            "Alt": bad_overlay_alt,
        }
        overlay_check = check_path(str(app_file), modules=overlay_modules)
        assert overlay_check.ok is False
        assert any(
            diag.location is not None and diag.location.filename == "<module:Alt>"
            for diag in overlay_check.diagnostics
        )
        assert any(
            "String" in diag.message and "Int" in diag.message
            for diag in overlay_check.diagnostics
        )

        server = create_server()
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=app_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=app_file.read_text(),
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=utils_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=overlay_utils,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=alt_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=bad_overlay_alt,
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[app_file.as_uri()]
        assert any(
            "String" in diag.message and "Int" in diag.message
            for diag in latest[app_file.as_uri()]
        )

    @pytest.mark.skipif(not HAS_PYGLS, reason="pygls not installed")
    def test_local_dependency_name_collisions_fail_consistently_across_surfaces(
        self, tmp_path, monkeypatch
    ):
        """Local-vs-dependency name collisions surface uniformly."""
        app_file, _local_utils_file, _dep_utils_file = (
            write_dependency_collision_fixture(tmp_path)
        )

        with pytest.raises(ProjectGraphError, match=r"Module name collision.*Utils"):
            resolve_project_context(app_file)

        check_result = check_path(str(app_file))
        assert check_result.ok is False
        assert any(
            "Module name collision" in diag.message and "Utils" in diag.message
            for diag in check_result.diagnostics
        )

        cli_check = _run_cli("check", str(app_file))
        assert cli_check.returncode != 0
        assert "collision" in cli_check.stderr.lower()
        assert "Utils" in cli_check.stderr

        run_result = run_path(str(app_file))
        assert run_result.ok is False
        assert any(
            "Module name collision" in diag.message and "Utils" in diag.message
            for diag in run_result.diagnostics
        )

        suite = run_project_test_suite(tmp_path)
        assert suite.success is False
        assert suite.errors == 1
        assert suite.file_results[0].error is not None
        assert "Module name collision" in suite.file_results[0].error

        cli_test = _run_cli("test", str(tmp_path))
        assert cli_test.returncode != 0
        assert "Module name collision" in (cli_test.stdout + cli_test.stderr)

        server = create_server()
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )

        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=app_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=app_file.read_text(),
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[app_file.as_uri()]
        assert any(
            "Module name collision" in diag.message and "Utils" in diag.message
            for diag in latest[app_file.as_uri()]
        )

    @pytest.mark.skipif(not HAS_PYGLS, reason="pygls not installed")
    def test_dependency_overlays_redirect_transitive_imports_consistently(
        self, tmp_path, monkeypatch
    ):
        """Dependency overlays override on-disk dependency modules consistently."""
        (
            app_file,
            helper_file,
            leaf_file,
            alt_file,
            overlay_helper,
            overlay_alt,
        ) = _write_dependency_overlay_fixture(tmp_path)

        baseline_run = run_path(str(app_file))
        assert baseline_run.ok is True
        assert baseline_run.value == 1

        overlay_modules = {
            "Helper": overlay_helper,
            "Alt": overlay_alt,
        }
        overlay_run = run_path(str(app_file), RunConfig(modules=overlay_modules))
        assert overlay_run.ok is True
        assert overlay_run.value == 99

        overlay_check = check_path(str(app_file), modules=overlay_modules)
        assert overlay_check.ok is True

        overlay_context = resolve_project_context(
            app_file,
            source_overrides={
                helper_file: overlay_helper,
                alt_file: overlay_alt,
            },
        )
        assert overlay_context.module_sources["Helper"] == overlay_helper
        assert overlay_context.module_sources["Alt"] == overlay_alt
        assert overlay_context.module_sources["Leaf"] == leaf_file.read_text()

        root_modules, root_path_to_module = _load_project_module_index(
            app_file,
            source_overrides={
                helper_file: overlay_helper,
                alt_file: overlay_alt,
            },
        )
        assert root_path_to_module[str(helper_file.resolve())] == "Helper"
        assert "helper" in root_modules["Helper"][2]
        assert "Alt" not in root_modules

        dependency_modules, dependency_path_to_module = _load_project_module_index(
            helper_file,
            source_overrides={
                helper_file: overlay_helper,
                alt_file: overlay_alt,
            },
        )
        assert dependency_path_to_module[str(alt_file.resolve())] == "Alt"
        assert "selected" in dependency_modules["Alt"][2]

        server = create_server()
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=app_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=app_file.read_text(),
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=helper_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=overlay_helper,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=alt_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=overlay_alt,
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[app_file.as_uri()] == []

    @pytest.mark.skipif(not HAS_PYGLS, reason="pygls not installed")
    def test_dependency_overlay_type_errors_stay_consistent_between_api_and_lsp(
        self, tmp_path, monkeypatch
    ):
        """Dependency overlay type errors surface on both API and LSP."""
        (
            app_file,
            helper_file,
            _leaf_file,
            alt_file,
            overlay_helper,
            _overlay_alt,
        ) = _write_dependency_overlay_fixture(tmp_path)
        bad_overlay_alt = (
            "export func selected() -> Int\n"
            "  example () -> 0\n"
            '  return "bad"\n'
            "end func\n"
        )

        overlay_modules = {
            "Helper": overlay_helper,
            "Alt": bad_overlay_alt,
        }
        overlay_check = check_path(str(app_file), modules=overlay_modules)
        assert overlay_check.ok is False
        assert any(
            diag.location is not None and diag.location.filename == "<module:Alt>"
            for diag in overlay_check.diagnostics
        )
        assert any(
            "String" in diag.message and "Int" in diag.message
            for diag in overlay_check.diagnostics
        )

        server = create_server()
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=app_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=app_file.read_text(),
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=helper_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=overlay_helper,
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=alt_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=bad_overlay_alt,
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[app_file.as_uri()] == []
        assert latest[alt_file.as_uri()]
        assert any(
            "String" in diag.message and "Int" in diag.message
            for diag in latest[alt_file.as_uri()]
        )

    @pytest.mark.skipif(not HAS_PYGLS, reason="pygls not installed")
    def test_stdlib_overlays_redirect_imports_consistently_for_api_and_lsp(
        self, tmp_path, monkeypatch
    ):
        """Explicit overlays can replace stdlib modules consistently."""
        (
            app_file,
            _local_file,
            std_math_file,
            overlay_math,
            _bad_overlay_math,
        ) = _write_stdlib_overlay_fixture(tmp_path)

        baseline_run = run_path(str(app_file))
        assert baseline_run.ok is True
        assert baseline_run.value == 5

        overlay_modules = {"Math": overlay_math}
        overlay_run = run_path(str(app_file), RunConfig(modules=overlay_modules))
        assert overlay_run.ok is True
        assert overlay_run.value == 42

        overlay_check = check_path(str(app_file), modules=overlay_modules)
        assert overlay_check.ok is True

        overlay_context = resolve_project_context(
            app_file,
            source_overrides={std_math_file: overlay_math},
        )
        assert overlay_context.module_sources["Math"] == overlay_math

        modules, path_to_module = _load_project_module_index(
            app_file,
            source_overrides={std_math_file: overlay_math},
        )
        assert path_to_module[str(std_math_file.resolve())] == "Math"
        assert "abs" in modules["Math"][2]

        server = create_server()
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=app_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=app_file.read_text(),
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=std_math_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=overlay_math,
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[app_file.as_uri()] == []

    @pytest.mark.skipif(not HAS_PYGLS, reason="pygls not installed")
    def test_stdlib_overlay_type_errors_stay_consistent_between_api_and_lsp(
        self, tmp_path, monkeypatch
    ):
        """Stdlib overlay type errors surface on both API and LSP."""
        (
            app_file,
            _local_file,
            std_math_file,
            _overlay_math,
            bad_overlay_math,
        ) = _write_stdlib_overlay_fixture(tmp_path)

        overlay_check = check_path(str(app_file), modules={"Math": bad_overlay_math})
        assert overlay_check.ok is False
        assert any(
            diag.location is not None and diag.location.filename == "<module:Math>"
            for diag in overlay_check.diagnostics
        )
        assert any(
            "String" in diag.message and "Int" in diag.message
            for diag in overlay_check.diagnostics
        )

        server = create_server()
        server.lsp._workspace = Workspace(
            None,
            sync_kind=types.TextDocumentSyncKind.Full,
        )
        published: list[tuple[str, list[types.Diagnostic]]] = []
        monkeypatch.setattr(
            server,
            "publish_diagnostics",
            lambda uri, diags: published.append((uri, list(diags))),
        )
        did_open = server.lsp._get_handler(types.TEXT_DOCUMENT_DID_OPEN)

        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=app_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=app_file.read_text(),
                )
            )
        )
        did_open(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=std_math_file.as_uri(),
                    language_id="geno",
                    version=1,
                    text=bad_overlay_math,
                )
            )
        )

        latest = {uri: diags for uri, diags in published}
        assert latest[app_file.as_uri()] == []
        assert latest[std_math_file.as_uri()]
        assert any(
            "String" in diag.message and "Int" in diag.message
            for diag in latest[std_math_file.as_uri()]
        )
