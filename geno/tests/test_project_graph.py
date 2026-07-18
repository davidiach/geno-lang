"""
Tests for ProjectGraph — file discovery and manifest parsing (#123)
===================================================================
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.project_graph import ProjectGraph, ProjectGraphError
from geno.tests.project_resolution_fixture_helpers import (
    write_dependency_private_collision_fixture,
    write_dependency_private_package_name_collision_fixture,
)


def _symlink_or_skip(link: Path, target: Path) -> None:
    """Create a symlink or skip the test if unsupported by the platform."""
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks not supported here: {exc}")


class TestSingleFile:
    """Single .geno file without a geno.toml."""

    def test_single_file_returns_one_entry(self, tmp_path):
        geno_file = tmp_path / "Hello.geno"
        geno_file.write_text('func main() -> String\n  return "hi"\nend func\n')
        pg = ProjectGraph.discover(geno_file)
        assert len(pg.files) == 1
        assert pg.files[0].module_name == "Hello"
        assert pg.files[0].path == geno_file.resolve()

    def test_single_file_entrypoint_is_stem(self, tmp_path):
        geno_file = tmp_path / "App.geno"
        geno_file.write_text("func main() -> Int\n  return 0\nend func\n")
        pg = ProjectGraph.discover(geno_file)
        assert pg.entrypoint == "App"

    def test_single_file_no_root(self, tmp_path):
        geno_file = tmp_path / "Lone.geno"
        geno_file.write_text("func f() -> Int\n  return 1\nend func\n")
        pg = ProjectGraph.discover(geno_file)
        assert pg.root is None

    def test_single_file_no_dependencies(self, tmp_path):
        geno_file = tmp_path / "X.geno"
        geno_file.write_text("func x() -> Int\n  return 1\nend func\n")
        pg = ProjectGraph.discover(geno_file)
        assert pg.dependencies == {}

    def test_lowercase_single_file_module_remains_supported(self, tmp_path):
        geno_file = tmp_path / "fibonacci.geno"
        geno_file.write_text("func main() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(geno_file)

        assert pg.module_names == ["fibonacci"]

    def test_single_file_rejects_codegen_punctuation(self, tmp_path):
        geno_file = tmp_path / "Evil;Pwn.geno"
        geno_file.write_text("func main() -> Int\n  return 1\nend func\n")

        with pytest.raises(ProjectGraphError, match="Invalid module name"):
            ProjectGraph.discover(geno_file)


class TestMultiFileWithManifest:
    """Project with geno.toml declaring files."""

    def test_explicit_files_resolved(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno", "Utils.geno"]\n'
        )
        (tmp_path / "Main.geno").write_text(
            "func main() -> Int\n  return 0\nend func\n"
        )
        (tmp_path / "Utils.geno").write_text(
            "func helper() -> Int\n  return 1\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        assert pg.root == tmp_path.resolve()
        assert pg.entrypoint == "Main"
        names = pg.module_names
        assert "Main" in names
        assert "Utils" in names

    def test_files_without_extension_auto_appended(self, tmp_path):
        (tmp_path / "geno.toml").write_text('files = ["Foo"]\n')
        (tmp_path / "Foo.geno").write_text("func foo() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        assert pg.module_names == ["Foo"]

    def test_file_paths_are_absolute(self, tmp_path):
        (tmp_path / "geno.toml").write_text('files = ["A.geno"]\n')
        (tmp_path / "A.geno").write_text("func a() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        for fp in pg.file_paths:
            assert fp.is_absolute()

    def test_discover_from_subdirectory(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main.geno"]\n'
        )
        (tmp_path / "Main.geno").write_text(
            "func main() -> Int\n  return 0\nend func\n"
        )
        subdir = tmp_path / "src" / "nested"
        subdir.mkdir(parents=True)

        pg = ProjectGraph.discover(subdir)
        assert pg.root == tmp_path.resolve()

    def test_missing_declared_file_raises(self, tmp_path):
        (tmp_path / "geno.toml").write_text('files = ["Missing.geno"]\n')
        with pytest.raises(ProjectGraphError, match="not found"):
            ProjectGraph.discover(tmp_path)

    def test_manifest_file_rejects_codegen_punctuation(self, tmp_path):
        (tmp_path / "geno.toml").write_text('files = ["Evil;Pwn.geno"]\n')
        (tmp_path / "Evil;Pwn.geno").write_text(
            "func main() -> Int\n  return 1\nend func\n"
        )

        with pytest.raises(ProjectGraphError, match="Invalid module name"):
            ProjectGraph.discover(tmp_path)


class TestMissingManifestFallback:
    """Directory with .geno files but no geno.toml."""

    def test_discovers_geno_files_in_directory(self, tmp_path):
        (tmp_path / "Alpha.geno").write_text("func a() -> Int\n  return 1\nend func\n")
        (tmp_path / "Beta.geno").write_text("func b() -> Int\n  return 2\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        assert pg.root == tmp_path  # directory used as root
        assert pg.entrypoint is None
        names = sorted(pg.module_names)
        assert names == ["Alpha", "Beta"]

    def test_empty_directory_returns_empty(self, tmp_path):
        pg = ProjectGraph.discover(tmp_path)
        assert pg.files == []
        assert pg.root is None

    def test_no_manifest_no_dependencies(self, tmp_path):
        (tmp_path / "Solo.geno").write_text("func s() -> Int\n  return 0\nend func\n")
        pg = ProjectGraph.discover(tmp_path)
        assert pg.dependencies == {}

    def test_no_manifest_symlink_escape_rejected(self, tmp_path):
        """A symlinked .geno file outside the directory should be rejected."""
        outside = tmp_path.parent / "Outside.geno"
        outside.write_text("func x() -> Int\n  return 1\nend func\n")
        _symlink_or_skip(tmp_path / "App.geno", outside)

        with pytest.raises(ProjectGraphError, match="escapes the project root"):
            ProjectGraph.discover(tmp_path)

    def test_auto_discover_ignores_subdirectories(self, tmp_path):
        """Without manifest, only top-level .geno files are found."""
        (tmp_path / "geno.toml").write_text("")  # empty manifest, no files key
        (tmp_path / "Top.geno").write_text("func t() -> Int\n  return 1\nend func\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "Deep.geno").write_text("func d() -> Int\n  return 2\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        names = pg.module_names
        assert "Top" in names
        assert "Deep" not in names


class TestDependencyResolution:
    """Dependency resolution from geno_modules/."""

    def test_dependency_resolved(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            '[dependencies.utils]\ngit = "https://example.com/utils.git"\n'
        )
        dep_dir = tmp_path / "geno_modules" / "utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "Utils.geno").write_text("func u() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        assert "utils" in pg.dependencies
        dep_files = [f for f in pg.files if f.is_dependency]
        assert len(dep_files) == 1
        assert dep_files[0].package_name == "utils"

    def test_missing_dependency_raises(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            '[dependencies.missing-lib]\ngit = "https://example.com/x.git"\n'
        )
        with pytest.raises(ProjectGraphError, match="not found"):
            ProjectGraph.discover(tmp_path)

    def test_kebab_dependency_resolved_as_pascal(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            '[dependencies.http-utils]\ngit = "https://example.com/http.git"\n'
        )
        dep_dir = tmp_path / "geno_modules" / "http-utils"
        dep_dir.mkdir(parents=True)
        (dep_dir / "HttpUtils.geno").write_text(
            "func fetch() -> Int\n  return 200\nend func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dep_files = [f for f in pg.files if f.is_dependency]
        assert len(dep_files) == 1
        assert dep_files[0].module_name == "HttpUtils"

    def test_dependency_with_own_entrypoint(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            '[dependencies.my-lib]\ngit = "https://example.com/lib.git"\n'
        )
        dep_dir = tmp_path / "geno_modules" / "my-lib"
        dep_dir.mkdir(parents=True)
        (dep_dir / "geno.toml").write_text('entrypoint = "Lib"\n')
        (dep_dir / "Lib.geno").write_text("func help() -> Int\n  return 1\nend func\n")

        pg = ProjectGraph.discover(tmp_path)
        dep_files = [f for f in pg.files if f.is_dependency]
        assert len(dep_files) == 1
        assert dep_files[0].path == (dep_dir / "Lib.geno").resolve()

    def test_dependency_private_sibling_names_are_internalized_per_package(
        self, tmp_path
    ):
        write_dependency_private_collision_fixture(tmp_path)

        pg = ProjectGraph.discover(tmp_path)

        private_utils = [
            f
            for f in pg.files
            if f.is_dependency
            and f.package_name is not None
            and f.module_name == "Utils"
        ]
        assert len(private_utils) == 2
        assert {f.package_name for f in private_utils} == {"alpha", "beta"}
        assert all(f.graph_name is not None for f in private_utils)
        assert len({f.graph_name for f in private_utils}) == 2

    def test_dependency_private_graph_names_keep_distinct_package_spellings(
        self, tmp_path
    ):
        write_dependency_private_package_name_collision_fixture(tmp_path)

        pg = ProjectGraph.discover(tmp_path)

        private_utils = [
            f
            for f in pg.files
            if f.is_dependency
            and f.package_name is not None
            and f.module_name == "Utils"
        ]
        assert len(private_utils) == 2
        assert {f.package_name for f in private_utils} == {"foo-bar", "foo_bar"}
        assert all(f.graph_name is not None for f in private_utils)
        assert len({f.graph_name for f in private_utils}) == 2


class TestPathContainment:
    """Manifest file paths and dependency entrypoints must not escape their root."""

    def test_manifest_files_path_traversal_rejected(self, tmp_path):
        """files = ['../outside/Secret.geno'] should be rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "Secret.geno").write_text("func x() -> Int\n  return 1\nend func\n")

        project = tmp_path / "project"
        project.mkdir()
        manifest = project / "geno.toml"
        manifest.write_text('files = ["../outside/Secret"]\n')

        with pytest.raises(ProjectGraphError, match="escapes the project root"):
            ProjectGraph.discover(project)

    def test_dependency_entrypoint_traversal_rejected(self, tmp_path):
        """entrypoint = '../../Leaked' should be rejected."""
        from geno.project_graph import _resolve_dependency_entry

        dep_dir = tmp_path / "dep"
        dep_dir.mkdir()
        dep_manifest = dep_dir / "geno.toml"
        dep_manifest.write_text('entrypoint = "../../Leaked"\n')
        leaked = tmp_path.parent / "Leaked.geno"
        leaked.write_text("func x() -> Int\n  return 1\nend func\n")

        result = _resolve_dependency_entry(dep_dir, "evil", "Evil")
        assert result is None

    def test_manifest_files_normal_path_accepted(self, tmp_path):
        """A normal file path should be accepted."""
        manifest = tmp_path / "geno.toml"
        manifest.write_text('files = ["App"]\n')
        (tmp_path / "App.geno").write_text("func main() -> Int\n  return 0\nend func\n")
        pg = ProjectGraph.discover(tmp_path)
        assert len(pg.files) == 1

    def test_auto_discovered_symlink_escape_rejected(self, tmp_path):
        """Implicit root discovery should reject symlinks outside the project."""
        (tmp_path / "geno.toml").write_text("")
        outside = tmp_path.parent / "Outside.geno"
        outside.write_text("func x() -> Int\n  return 1\nend func\n")
        _symlink_or_skip(tmp_path / "App.geno", outside)

        with pytest.raises(ProjectGraphError, match="escapes the project root"):
            ProjectGraph.discover(tmp_path)

    def test_dependency_default_entrypoint_symlink_rejected(self, tmp_path):
        """Default dependency entrypoints must not resolve outside the package."""
        (tmp_path / "geno.toml").write_text(
            '[dependencies.evil]\ngit = "https://example.com/evil.git"\n'
        )
        dep_dir = tmp_path / "geno_modules" / "evil"
        dep_dir.mkdir(parents=True)
        outside = tmp_path.parent / "Leaked.geno"
        outside.write_text("func x() -> Int\n  return 1\nend func\n")
        _symlink_or_skip(dep_dir / "Evil.geno", outside)

        pg = ProjectGraph.discover(tmp_path)
        assert not any(rf.package_name == "evil" for rf in pg.files)
